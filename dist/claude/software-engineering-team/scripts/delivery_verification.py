#!/usr/bin/env python3
"""Coordinate independent, source-bound Item verification in disposable runtime."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import functools
import fnmatch
import json
import os
import platform
import re
import stat
import time
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import uuid

import atomic_file
from backlog_compile import meaningful_text
import delivery_compile as delivery
import file_lock

POLICY_PATH = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/delivery-verification-policy.json"
ROLES = ("code_reviewer", "qa_engineer")
# At review_loop blocking_delta a fresh code reviewer registers its rulings on
# the claims of a code review in this mode, apart from the claiming result.
CALIBRATION_MODE = "calibration"


def policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                                 separators=(",", ":")).encode()).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip()
                           or "cannot inspect verification candidate")
    # NUL-separated Git paths preserve carriage returns, newlines and leading
    # spaces. Text-mode pipes and strip() both change valid POSIX file names.
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\n")


@functools.lru_cache(maxsize=128)
def runtime_anchor(root: Path) -> Path:
    if (root / ".git").is_file():
        from delivery_git import main_worktree
        return main_worktree(root)
    return root


def session_path(root: Path) -> Path:
    anchor = runtime_anchor(root.resolve())
    name = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
    return anchor / ".agentrof/agent-marketplace/.runtime/verification" / name / "session.json"


def safe_runtime_path(root: Path, path: Path, *, file_only: bool = False) -> Path:
    """Reject aliases before accessing runtime, including native Windows junctions."""
    anchor = runtime_anchor(root.resolve()).resolve()
    if path != anchor and anchor not in path.parents:
        raise RuntimeError("verification runtime must remain inside its main worktree")
    for parent in (path, *path.parents):
        if parent == anchor:
            break
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("verification runtime must not contain symlinks or junctions")
        if parent == path and file_only and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise RuntimeError("verification runtime files must be regular and unshared")
    return path


def safe_runtime(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root), file_only=True)


@contextlib.contextmanager
def locked(root: Path):
    path = safe_runtime(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = safe_runtime_path(root, path.with_suffix(".lock"), file_only=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.lock(fd)
        yield
    finally:
        file_lock.unlock(fd)
        os.close(fd)


def command_active(root: Path) -> bool:
    path = safe_runtime_path(root, safe_runtime(root).with_name("commands.lock"), file_only=True)
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if file_lock.try_lock(fd):
            file_lock.unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


@contextlib.contextmanager
def command_lock(root: Path):
    path = safe_runtime_path(root, safe_runtime(root).with_name("commands.lock"), file_only=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not file_lock.try_lock(fd):
            raise RuntimeError("another verification command is still running")
        try:
            yield
        finally:
            file_lock.unlock(fd)
    finally:
        os.close(fd)


def environment_lock_paths(root: Path) -> tuple[Path, Path]:
    """The Item's environment lock and the owner record beside it, in the Item's runtime state."""
    directory = session_path(root).parent
    return (safe_runtime_path(root, directory / "environment.lock", file_only=True),
            safe_runtime_path(root, directory / "environment-owner.json", file_only=True))


def environment_owner(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def environment_holder(root: Path) -> dict | None:
    """The owner record of the process holding the Item's environment lock, or None when none does."""
    lock, owner = environment_lock_paths(root)
    if not lock.exists():
        return None
    descriptor = os.open(lock, os.O_RDWR)
    try:
        if file_lock.try_lock(descriptor):
            file_lock.unlock(descriptor)
            return None
    finally:
        os.close(descriptor)
    return environment_owner(owner) or {}


def describe_environment_holder(owner: dict) -> str:
    if not all(isinstance(owner.get(key), str) for key in ("holder", "command", "started_at")) \
            or type(owner.get("pid")) is not int:
        return "a process that has not recorded its owner yet"
    return (f"{owner['holder']}, running `{owner['command']}` in process {owner['pid']}"
            f" since {owner['started_at']}")


@contextlib.contextmanager
def environment_lock(root: Path, holder: str, command: str):
    """Hold the Item's environment lock while one environment verb or verification command runs.

    The lock refuses at once while another process holds it and names that
    holder from its owner record. As with every package lock, the operating
    system ends the lock with the process holding it, so a holder that died
    leaves only its owner record: the next holder replaces that record and
    yields it as the interrupted holder. Neither a record's age nor its process
    id frees a lock.
    """
    lock, owner = environment_lock_paths(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not file_lock.try_lock(descriptor):
            raise RuntimeError("DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by "
                               + describe_environment_holder(environment_owner(owner) or {})
                               + "; run this after it finishes")
        try:
            interrupted = environment_owner(owner)
            atomic_file.replace_text(owner, json.dumps(
                {"holder": holder, "command": command, "pid": os.getpid(),
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                indent=2, sort_keys=True) + "\n")
            yield interrupted
        finally:
            with contextlib.suppress(OSError):
                owner.unlink()
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def read_session(root: Path, *, required: bool = True) -> dict | None:
    path = safe_runtime(root)
    if not path.exists():
        if required:
            raise RuntimeError("verification session is missing; freeze and rerun both roles")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("schema_version") != 1 or value.get("digest") != digest(
                {key: item for key, item in value.items() if key != "digest"})
                or set(value["workers"]) != set(ROLES)
                or any(worker.get("state") not in {"running", "settled", "cancelled"}
                       for worker in value["workers"].values())):
            raise ValueError("invalid schema, workers or digest")
        return value
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"verification session is invalid: {exc}") from exc


def write_session(root: Path, value: dict) -> None:
    value = {key: item for key, item in value.items() if key != "digest"}
    value["digest"] = digest(value)
    atomic_file.replace_text(safe_runtime(root), json.dumps(value, indent=2, sort_keys=True) + "\n")


def guard_write(worktree: Path, paths: list[Path] | None = None) -> None:
    """Refuse writes while either reader is active; only isolated scratch is writable."""
    root = Path(worktree).resolve()
    if not (root / ".git").exists():
        return
    value = read_session(root, required=False)
    if not command_active(root) and (not value or not any(worker["state"] == "running" for worker in value["workers"].values())):
        return
    scratch = session_path(root).parent / "scratch"
    if paths and all(scratch in (path if path.is_absolute() else root / path).resolve().parents
                     for path in paths):
        return
    raise RuntimeError("DELIVERY_VERIFICATION_READERS_ACTIVE: settle or confirm cancellation of both readers before writing")


def instruction_identity() -> str:
    base = Path(__file__).resolve().parents[1]
    files = [["constitution.md", hashlib.sha256((base / "constitution.md").read_bytes()).hexdigest()]]
    for folder in ("agents", "skill-content", "flows", "scripts", "templates"):
        for path in sorted((base / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append([path.relative_to(base).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return digest(files)


def mutation_scope(root: Path, changed: list[str]) -> list[str]:
    rules = policy()["mutation_scope"]
    contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    include = contract.get(rules["additional_include_property"], [])
    if not isinstance(include, list) or any(not isinstance(path, str) or not delivery._is_normalized_claim(path) for path in include):
        raise RuntimeError("mutation_include_paths must name normalized approved repository paths")
    files = []
    for name in changed:
        path = PurePosixPath(name)
        if any(name.startswith(prefix) for prefix in rules["excluded_prefixes"]):
            continue
        if not (root / name).is_file() or (root / name).is_symlink():
            continue
        explicit = any(path == PurePosixPath(value) or PurePosixPath(value) in path.parents for value in include)
        non_code = (path.suffix.lower() in rules["non_code_suffixes"]
                    or any(part in rules["test_path_segments"] for part in path.parts[:-1])
                    or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in rules["test_name_patterns"]))
        if explicit or not non_code:
            files.append(name)
    return files


def source_file_generation(path: Path) -> list[int]:
    """Observe reverted writes with inode change time, not Windows creation time."""
    metadata = path.lstat()
    changed = metadata.st_ctime_ns
    if os.name == "nt" and stat.S_ISREG(metadata.st_mode):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]

        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        with path.open("rb") as stream:
            info = BasicInfo()
            if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.change <= 0:
                raise RuntimeError("filesystem does not expose a trustworthy source change timestamp")
            changed = info.change
    return [metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mode, metadata.st_mtime_ns, changed]


def candidate(root: Path, delivery_id: str, story: str, *, allow_evidence: bool = False) -> dict:
    from delivery_git import require_visible_item_index, worktree_pending_paths
    root = root.resolve()
    head, dirty = delivery.item_worktree_head(root)
    require_visible_item_index(root)
    docs = delivery.docs_root(root)
    directory = delivery.find_delivery(docs, delivery_id)
    item = directory / "items" / delivery.id_slug(story) / "item.md" if directory else None
    if item is None or not item.is_file():
        raise RuntimeError("Delivery Item not found")
    props, _ = delivery.split_note(item)
    if props.get("status") != "active" or not props.get("item_plan_hash"):
        raise RuntimeError("verification requires an active, approved Item")
    if delivery.verification_schedule(props) != "parallel_snapshot_v1":
        raise RuntimeError("verification sessions require parallel_snapshot_v1 in the approved Item plan")
    report_paths = (item.parent / "code-review.md", item.parent / "verification.md")
    report_errors = delivery.item_evidence_file_findings(root, head, report_paths)
    if report_errors:
        raise RuntimeError("; ".join(report_errors))
    reports = {path.relative_to(root).as_posix() for path in report_paths}
    if dirty and (not allow_evidence or not worktree_pending_paths(root, root).issubset(reports)):
        raise RuntimeError("commit or remove non-evidence changes before verifying the Item candidate")
    paths = [item, directory / "execution-plan.md", directory / "delivery.md"]
    for key in ("story_path", "test_plan_path"):
        paths.append(docs / props[key])
    delivery_props, _ = delivery.split_note(directory / "delivery.md")
    paths.append(docs / delivery_props["definition_of_done_path"])
    for key in ("verification_contract_ref", "environment_contract_ref"):
        if props.get(key):
            paths.append(docs / (props[key] + ".md"))
    paths.extend(sorted((docs / "system-architecture").rglob("*.md")))
    paths.extend(sorted(root.glob("*.md")))
    if (root / "workspace/config.json").is_file():
        paths.append(root / "workspace/config.json")
    identities = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"verification input is not a regular file: {path}")
        identities[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    base = props.get("integration_base_commit")
    if not isinstance(base, str) or not delivery.GIT_OID_RE.fullmatch(base):
        raise RuntimeError("Item has no exact integration base for verification")
    # Both sides of a rename belong to the Item change. Deleted files remain in
    # the manifest, while mutation tools receive only surviving regular files.
    changed = sorted(set(part for part in git(root, "diff", "--name-only", "--no-renames", "-z", base, head).split("\0") if part))
    code = mutation_scope(root, changed)
    observations = {}
    for name in git(root, "ls-files", "-z").split("\0"):
        if name and name not in reports:
            observations[name] = source_file_generation(root / name)
    value = {"schema_version": 1, "delivery": delivery_id, "story": story,
             "source_observations": observations,
             "product_commit": head, "integration_base_commit": base,
             "item_plan_hash": props["item_plan_hash"], "inputs": identities,
             "instruction_identity": instruction_identity(), "runtime_required": props.get("runtime_required", False),
             "changed_files": changed, "mutation_files": code, "report_paths": sorted(reports)}
    value["candidate_hash"] = digest(value)
    return value


def freeze(root: Path, delivery_id: str, story: str, *, fresh: bool = False) -> dict:
    started = time.monotonic()
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("verification command is still running")
        previous = read_session(root, required=False)
        if previous and runtime_needs_cleanup(previous):
            raise RuntimeError("tear the environment down before replacing its verification session")
        if previous and any(worker["state"] == "running" for worker in previous["workers"].values()):
            raise RuntimeError("settle or cancel the existing readers before freezing a new candidate")
        current = candidate(root, delivery_id, story, allow_evidence=True)
        if previous and not fresh and previous.get("candidate") == current:
            try:
                validate(root, delivery_id, story)
            except RuntimeError:
                pass
            else:
                return {**previous, "reused": True}
        previous_results = previous.get("workers", {}) if previous else {}
        retained = {(finding["role"], finding["id"]): finding
                    for finding in (previous.get("unresolved_findings", []) if previous else [])}
        for role, worker in previous_results.items():
            result = worker.get("result", {})
            findings = result.get("findings", [])
            # Calibration rulings carry into the next session, so no claim is ruled twice.
            if (role == "code_reviewer" and result.get("calibration")
                    and review_loop(root, delivery_id) == "blocking_delta"):
                findings = calibrated_findings(result)
            for finding in findings:
                key = (role, finding["id"])
                if finding.get("status") == "resolved":
                    retained.pop(key, None)
                else:
                    retained[key] = {**finding, "role": role}
        findings = [retained[key] for key in sorted(retained)]
        value = {"schema_version": 1, "session_id": uuid.uuid4().hex, "candidate": current,
                 "previous_candidate": previous.get("candidate") if previous else None,
                 "unresolved_findings": findings,
                 "workers": {role: {"state": "running", "started_at": time.time()} for role in ROLES},
                 "metrics": {"freeze_seconds": time.monotonic() - started, "command_cache_hits": 0,
                             "command_seconds": 0.0, "model_seconds": None, "model_tokens": None}, "raw_evidence": {}}
        write_session(root, value)
        scope = session_path(root).parent / "mutation-files.json"
        atomic_file.replace_text(scope, json.dumps({"candidate_hash": current["candidate_hash"],
                                                   "scope": "whole_changed_files", "files": current["mutation_files"]}, indent=2) + "\n")
        return value


def require_current(root: Path, value: dict, *, allow_evidence: bool = False) -> dict:
    bound = value["candidate"]
    current = candidate(root, bound["delivery"], bound["story"], allow_evidence=allow_evidence)
    if current != bound:
        raise RuntimeError("verification candidate or source bindings changed; freeze and rerun both roles")
    return current


def required_checks(root: Path, current: dict, role: str) -> list[str]:
    if role == "code_reviewer":
        return policy()["review_checks"]
    checks = list(policy()["qa_checks"])
    docs = delivery.docs_root(root)
    props, _ = delivery.split_note(docs / "operation/verification-contract.md")
    if props.get("mutation_disposition") == "required":
        checks.append("mutation_whole_changed_files")
    if props.get("dependency_audit_disposition") == "required":
        checks.append("dependency_audit")
    if current["runtime_required"]:
        checks.append("fresh_runtime")
    return checks


def raw_output_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (not delivery._is_normalized_claim(relative) or ":" in relative
            or any(part.rstrip(". ") != part for part in parts)
            or not parts or parts[0] != "scratch" or len(parts) != 2
            or PurePosixPath(relative).is_absolute() or any(part in {".", ".."} for part in parts)):
        raise RuntimeError("raw verification output must be an isolated scratch file")
    path = session_path(root).parent / relative
    return safe_runtime_path(root, path, file_only=True)


def command_environment(root: Path, *, diagnostic: bool = False) -> dict:
    environment = dict(os.environ)
    environment["AGENTROF_MUTATION_FILES"] = str(session_path(root).parent / "mutation-files.json")
    environment.pop("AGENTROF_DIAGNOSTIC_TESTS", None)
    if diagnostic:
        environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(session_path(root).parent / "diagnostic-tests.json")
    environment["AGENTROF_VERIFICATION_SCRATCH"] = str(session_path(root).parent / "scratch")
    return environment


def diagnostic_selection(root: Path, path: Path, current: dict) -> dict:
    """Treat focused test identifiers as data, never command arguments or code."""
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(session_path(root).parent).as_posix()
    except ValueError as exc:
        raise RuntimeError("diagnostic selection must be a regular file inside verification scratch") from exc
    if not path.is_file():
        raise RuntimeError("diagnostic selection must be a regular file inside verification scratch")
    path = raw_output_path(root, relative)
    value = json.loads(path.read_text(encoding="utf-8"))
    fields = {"schema_version", "candidate_hash", "failed_test_ids", "affected_test_ids"}
    if (not isinstance(value, dict) or set(value) != fields or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("candidate_hash") != current["candidate_hash"]):
        raise RuntimeError("diagnostic selection must bind this candidate and declare only schema_version, candidate_hash, failed_test_ids and affected_test_ids")
    for name in ("failed_test_ids", "affected_test_ids"):
        identifiers = value[name]
        if (not isinstance(identifiers, list) or any(not isinstance(identifier, str)
                or not identifier or identifier != identifier.strip() or identifier.startswith("-")
                or any(ord(character) < 32 or ord(character) == 127 for character in identifier)
                for identifier in identifiers) or len(set(identifiers)) != len(identifiers)):
            raise RuntimeError("diagnostic test IDs must be unique nonempty literal identifiers, without option prefixes or control characters")
        value[name] = sorted(identifiers)
    value["selected_test_ids"] = sorted(set(value["failed_test_ids"]) | set(value["affected_test_ids"]))
    if not value["selected_test_ids"]:
        raise RuntimeError("diagnostic selection must include at least one failed or affected test ID")
    return value


def run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None) -> dict:
    root = root.resolve()
    read_session(root)
    with environment_lock(root, "qa_engineer", "run --kind " + kind), command_lock(root):
        return _run_check(root, kind, fresh=fresh, selection_file=selection_file)


def _run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None) -> dict:
    """Run an approved command verbatim with a file-based mutation scope binding."""
    root = root.resolve()
    if kind not in {"test", "mutation", "dependency_audit", "diagnostic_test"}:
        raise RuntimeError("unsupported verification command kind")
    if (kind == "diagnostic_test") != (selection_file is not None):
        raise RuntimeError("diagnostic_test requires --selection-file; final commands do not accept a focused selection")
    if selection_file is not None:
        selection_file = Path(selection_file)
        if not selection_file.is_absolute():
            selection_file = root / selection_file
    with locked(root):
        session = read_session(root)
        current = require_current(root, session, allow_evidence=True)
        if session["workers"]["qa_engineer"]["state"] != "running":
            raise RuntimeError("verification commands require the active QA reader")
        contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
        command = contract.get(kind + "_command")
        if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
            raise RuntimeError("approved verification command is missing or contains unresolved parameters")
        workdir = str(contract.get(kind + "_workdir", "."))
        directory = (root / workdir).resolve()
        if directory != root and root not in directory.parents:
            raise RuntimeError("verification command workdir must remain inside the Item worktree")
        environment = command_environment(root, diagnostic=kind == "diagnostic_test")
        selection = diagnostic_selection(root, selection_file, current) if selection_file is not None else None
        selection_bytes = None
        if selection is not None:
            input_generation = source_file_generation(selection_file)
            selection_bytes = (json.dumps(selection, indent=2, sort_keys=True) + "\n").encode("utf-8")
            selector = safe_runtime_path(root, Path(environment["AGENTROF_DIAGNOSTIC_TESTS"]), file_only=True)
            atomic_file.replace_bytes(selector, selection_bytes)
            selector_generation = source_file_generation(selector)
        scope = {"candidate_hash": current["candidate_hash"], "scope": "whole_changed_files", "files": current["mutation_files"]}
        atomic_file.replace_text(Path(environment["AGENTROF_MUTATION_FILES"]), json.dumps(scope, indent=2) + "\n")
        scratch = safe_runtime_path(root, Path(environment["AGENTROF_VERIFICATION_SCRATCH"]))
        scratch.mkdir(exist_ok=True)
        identity = {"candidate_hash": current["candidate_hash"], "kind": kind, "command": command,
                    "workdir": workdir, "environment_hash": digest(environment),
                    "python": sys.version, "platform": platform.platform(), "git": git(root, "--version"),
                    "execution_isolation": "private_clone_v1"}
        if selection is not None:
            identity["diagnostic_selection_hash"] = digest(selection)
        key = digest(identity)
        old = session["raw_evidence"].get(kind)
        if (not fresh and old and old.get("identity") == identity and old.get("exit_code") == 0 and old.get("candidate_intact") is True
                and 0 <= time.time() - old.get("completed_at", 0) <= policy()["raw_evidence_max_age_seconds"]):
            output = raw_output_path(root, old["output_file"])
            if output.is_file() and not output.is_symlink() and hashlib.sha256(output.read_bytes()).hexdigest() == old["output_sha256"]:
                session["metrics"]["command_cache_hits"] += 1
                write_session(root, session)
                return {**old, "reused": True}
        session["raw_evidence"].pop(kind, None)
        write_session(root, session)
        session_id = session["session_id"]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=scratch) as temporary:
        execution_root = Path(temporary) / "checkout"
        # A private repository gives mutation tools their own index, objects and
        # files; no transient mutant can enter the independent reviewer's view.
        git(root, "clone", "--no-local", "--no-hardlinks", "--no-checkout", "--", str(root), str(execution_root))
        git(execution_root, "checkout", "--detach", current["product_commit"])
        execution_directory = (execution_root / workdir).resolve()
        if execution_directory != execution_root.resolve() and execution_root.resolve() not in execution_directory.parents:
            raise RuntimeError("verification workdir escapes its isolated checkout")
        completed = subprocess.run(command, cwd=execution_directory, env=environment, shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        from delivery_git import require_visible_item_index
        try:
            require_visible_item_index(execution_root)
            intact = (git(execution_root, "rev-parse", "HEAD") == current["product_commit"]
                      and not git(execution_root, "diff", "--name-only", "HEAD"))
        except RuntimeError:
            intact = False
        if selection is not None:
            try:
                selector = safe_runtime_path(root, Path(environment["AGENTROF_DIAGNOSTIC_TESTS"]), file_only=True)
                selection_intact = (diagnostic_selection(root, selection_file, current) == selection
                                    and selector.read_bytes() == selection_bytes
                                    and source_file_generation(selection_file) == input_generation
                                    and source_file_generation(selector) == selector_generation)
            except (RuntimeError, ValueError, OSError):
                selection_intact = False
            intact = intact and selection_intact
    with locked(root):
        session = read_session(root)
        if session["session_id"] != session_id:
            raise RuntimeError("verification session changed while the command ran")
        require_current(root, session, allow_evidence=True)
        output_name = "scratch/" + kind + "-" + key.removeprefix("sha256:") + ".log"
        output = raw_output_path(root, output_name)
        atomic_file.replace_bytes(output, completed.stdout)
        record = {"identity": identity, "exit_code": completed.returncode, "candidate_intact": intact,
                  "output_file": output_name, "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                  "duration_seconds": time.monotonic() - started, "completed_at": time.time()}
        if selection is not None:
            record["diagnostic_selection"] = selection
            record["selection_intact"] = selection_intact
        record["evidence_hash"] = digest(record)
        session["raw_evidence"][kind] = record
        session["metrics"]["command_seconds"] += record["duration_seconds"]
        write_session(root, session)
        return {**record, "reused": False}


def require_raw_evidence(root: Path, session: dict, checks: dict) -> None:
    kinds = {"full_test_suite": "test", "mutation_whole_changed_files": "mutation", "dependency_audit": "dependency_audit"}
    for check in required_checks(root, session["candidate"], "qa_engineer"):
        if check not in kinds:
            continue
        raw = session["raw_evidence"].get(kinds[check], {})
        if (raw.get("exit_code") != 0 or raw.get("candidate_intact") is not True or checks[check].get("raw_evidence_hash") != raw.get("evidence_hash")
                or raw.get("evidence_hash") != digest({key: item for key, item in raw.items() if key != "evidence_hash"})
                or raw.get("identity", {}).get("candidate_hash") != session["candidate"]["candidate_hash"]):
            raise RuntimeError(f"{check} requires successful same-candidate command evidence from run")
        identity = raw["identity"]
        contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
        if (identity.get("command") != contract.get(kinds[check] + "_command")
                or identity.get("workdir") != contract.get(kinds[check] + "_workdir", ".")
                or identity.get("environment_hash") != digest(command_environment(root))
                or identity.get("python") != sys.version or identity.get("platform") != platform.platform()
                or identity.get("git") != git(root, "--version")
                or not 0 <= time.time() - raw.get("completed_at", 0) <= policy()["raw_evidence_max_age_seconds"]):
            raise RuntimeError(f"{check} command environment changed or evidence expired")
        if check == "full_test_suite" and checks[check].get("environment") != identity["environment_hash"]:
            raise RuntimeError("full suite environment must bind the recorded execution environment")
        output = raw_output_path(root, raw["output_file"])
        if (not output.is_file() or output.is_symlink()
                or hashlib.sha256(output.read_bytes()).hexdigest() != raw.get("output_sha256")):
            raise RuntimeError(f"{check} raw command output is missing or changed")


def review_loop(root: Path, delivery_id: str) -> str:
    """The review_loop value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, delivery.REVIEW_LOOP)


def item_record(root: Path, delivery_id: str, story: str) -> dict:
    directory = delivery.find_delivery(delivery.docs_root(root), delivery_id)
    if directory is None:
        raise RuntimeError("Delivery Item not found")
    return delivery.split_note(directory / "items" / delivery.id_slug(story) / "item.md")[0]


def nonblocking(finding: dict) -> bool:
    return finding["severity"].casefold() in {value.casefold() for value in policy()["nonblocking_severities"]}


def blocking(finding: dict) -> bool:
    return finding["severity"].casefold() in {value.casefold() for value in policy()["blocking_severities"]}


def calibrated_findings(result: dict) -> list[dict]:
    """A code review result's findings with its calibration rows applied.

    A confirmed claim keeps its severity, a claim calibrated minor becomes a
    follow-up with the row's owner role and trigger, and a claim calibrated
    invalid is closed by the row's cited evidence. Each keeps the severity it
    was claimed at and records its ruling, so no later session rules it again.
    """
    rows = {row["finding"]: row for row in result.get("calibration", [])}
    findings = []
    for finding in result.get("findings", []):
        row = rows.get(finding["id"])
        if row is None:
            findings.append(finding)
            continue
        ruling = row["calibrated_severity"].casefold()
        ruled = {**finding, "claimed_severity": finding["severity"], "calibrated_severity": ruling}
        if ruling == "invalid":
            ruled["status"] = "resolved"
        elif ruling == "minor":
            ruled.update(severity="minor", owner_role=row.get("owner_role"),
                         revisit_trigger=row.get("revisit_trigger"))
        findings.append(ruled)
    return findings


def calibrated_verdict(result: dict) -> str:
    """A failed code review whose every calibrated claim is minor or invalid passes."""
    if result.get("verdict") != "failed" or not result.get("calibration"):
        return str(result.get("verdict"))
    return "failed" if any(finding["status"] == "open" and blocking(finding)
                           for finding in calibrated_findings(result)) else "passed"


def candidate_line_count(root: Path, commit: str, path: str) -> int | None:
    """The number of lines ``path`` holds in the frozen candidate, or None without that file."""
    completed = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob",
                                f"{commit}:{path}"], capture_output=True, check=False)
    if completed.returncode:
        return None
    data = completed.stdout
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def cites_candidate(root: Path, current: dict, reason: str) -> bool:
    """Whether a calibration reason cites a line of the frozen candidate as path:line."""
    for path, line in re.findall(r"(?<![\w./-])([\w.-]+(?:/[\w.-]+)*):([1-9][0-9]*)\b", reason):
        if delivery._is_normalized_claim(path):
            count = candidate_line_count(root, current["product_commit"], path)
            if count is not None and int(line) <= count:
                return True
    return False


def calibration_problems(root: Path, current: dict, result: dict, ruled: set[str],
                         roles: list[str]) -> list[str]:
    """One row per open critical or major claim that no earlier calibration ruled."""
    rows = result.get("calibration", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("finding"), str)
                                         for row in rows):
        return ["calibration must list one row object per claim"]
    claims = {finding["id"]: finding for finding in result.get("findings", [])
              if finding["status"] == "open" and blocking(finding) and finding["id"] not in ruled}
    listed = [row["finding"] for row in rows]
    if sorted(listed) != sorted(claims):
        return ["calibration must hold exactly one row for each open critical or major claim no earlier"
                f" calibration ruled: {', '.join(sorted(claims)) or 'none'}"]
    problems = []
    for row in rows:
        claim = claims[row["finding"]]
        label, severity = row["finding"], claim["severity"].casefold()
        claimed, ruling = row.get("claimed_severity"), row.get("calibrated_severity")
        if not isinstance(claimed, str) or claimed.casefold() != severity:
            problems.append(f"{label} calibration must record the claimed severity {claim['severity']}")
        if not isinstance(ruling, str) or ruling.casefold() not in {severity, "minor", "invalid"}:
            problems.append(f"{label} calibrated_severity must confirm {claim['severity']} or be minor or invalid")
        reason = row.get("reason")
        if not isinstance(reason, str) or not meaningful_text(reason) or not cites_candidate(root, current, reason):
            problems.append(f"{label} calibration reason must cite the candidate text as path:line,"
                            " a line the frozen candidate holds")
        if isinstance(ruling, str) and ruling.casefold() == "minor" and (
                row.get("owner_role") not in roles or not isinstance(row.get("revisit_trigger"), str)
                or not meaningful_text(row["revisit_trigger"])):
            problems.append(f"{label} calibrated minor needs an owner_role of {', '.join(roles)}"
                            " and a concrete revisit_trigger")
    return problems


def open_claims(findings: list[dict], ruled: set[str]) -> list[dict]:
    """The open critical or major claims that no earlier calibration ruled, by id."""
    return sorted((finding for finding in findings
                   if finding["status"] == "open" and blocking(finding) and finding["id"] not in ruled),
                  key=lambda finding: finding["id"])


def register_calibration(root: Path, result: dict) -> dict:
    """Register a calibration reader's rulings as a result of their own.

    At review_loop blocking_delta a fresh code reviewer, never the reviewer that
    returned the claims, rules each open critical or major claim before the
    claiming result is registered, so the implementation writer stays idle.
    Its rows bind the exact claims it ruled, and they reach the claiming
    result only through this registration: register_result refuses a claiming
    result that carries rows of its own.
    """
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the verification command to exit before registering a calibration")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        if review_loop(root, current["delivery"]) != "blocking_delta":
            raise RuntimeError("severity calibration runs only at review_loop blocking_delta")
        if result.get("role") != "code_reviewer" or result.get("mode") != CALIBRATION_MODE:
            raise RuntimeError(f"a calibration result names role code_reviewer and mode {CALIBRATION_MODE}")
        if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
            raise RuntimeError("calibration does not bind this candidate and reader session")
        if value["workers"]["code_reviewer"]["state"] != "running":
            raise RuntimeError("calibrate the claims before the claiming code review result is registered")
        if value.get("calibration"):
            raise RuntimeError("this session's claims are already calibrated; each claim is ruled once")
        if not isinstance(result.get("report"), str) or not result["report"].strip():
            raise RuntimeError("calibration requires its independent report")
        claims = result.get("claims")
        if (not isinstance(claims, list) or not claims
                or any(not isinstance(claim, dict) or not isinstance(claim.get("id"), str)
                       or not isinstance(claim.get("severity"), str) or claim.get("status") != "open"
                       or not blocking(claim) for claim in claims)
                or len({claim["id"] for claim in claims}) != len(claims)):
            raise RuntimeError("calibration claims must list each open critical or major claim once, as returned")
        ruled = sorted({finding["id"] for finding in value.get("unresolved_findings", [])
                        if finding["role"] == "code_reviewer" and "calibrated_severity" in finding}
                       & {claim["id"] for claim in claims})
        if ruled:
            raise RuntimeError(f"an earlier calibration already ruled {', '.join(ruled)}")
        item = item_record(root, current["delivery"], current["story"])
        problems = calibration_problems(root, current, {"findings": claims, "calibration": result.get("calibration")},
                                        set(), follow_up_roles(item))
        if problems:
            raise RuntimeError("severity calibration is incomplete: " + "; ".join(problems))
        stored = dict(result)
        stored["result_hash"] = digest(result)
        value["calibration"] = {"state": "settled", "result": stored, "completed_at": time.time()}
        write_session(root, value)
        return value


def follow_up_roles(item: dict) -> list[str]:
    """The Item's implementation roles, which own its code review follow-ups."""
    roles = item.get("role_sequence")
    return [role for role in roles if role not in ROLES] if isinstance(roles, list) else []


def follow_up_problems(findings: list, roles: list[str]) -> list[str]:
    """Every open non-blocking code review finding is a tracked follow-up."""
    problems = []
    for finding in findings:
        if finding.get("status") != "open" or not nonblocking(finding):
            continue
        identifier = finding["id"]
        for key in ("file", "description"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                problems.append(f"{identifier} needs its {key}")
        if finding.get("owner_role") not in roles:
            problems.append(f"{identifier} owner_role must be one of: {', '.join(roles)}")
        trigger = finding.get("revisit_trigger")
        if not isinstance(trigger, str) or not meaningful_text(trigger):
            problems.append(f"{identifier} needs a concrete revisit_trigger")
    return problems


def open_follow_ups(result: dict, item: dict) -> list[dict]:
    """The open non-blocking findings of a code review result, each a complete follow-up."""
    findings = calibrated_findings(result)
    problems = follow_up_problems(findings, follow_up_roles(item))
    if problems:
        raise RuntimeError("code review follow-ups are incomplete: " + "; ".join(problems))
    return sorted((finding for finding in findings
                   if finding.get("status") == "open" and nonblocking(finding)),
                  key=lambda finding: finding["id"])


def register_result(root: Path, result: dict) -> dict:
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the verification command to exit before settling its reader")
        value = read_session(root)
        if result.get("role") == "qa_engineer" and runtime_needs_cleanup(value):
            raise RuntimeError("tear the environment down before settling the QA reader")
        current = value["candidate"] if result.get("verdict") == "cancelled" else require_current(root, value, allow_evidence=True)
        role, mode, verdict = (result.get(key) for key in ("role", "mode", "verdict"))
        if role not in ROLES or mode not in policy()["role_modes"][role]:
            raise RuntimeError("unsupported verification role or mode")
        if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
            raise RuntimeError("result does not bind this candidate and reader session")
        if value["workers"][role]["state"] != "running":
            raise RuntimeError("reader already settled; freeze a new session to replace its result")
        if verdict not in {"passed", "failed", "cancelled"}:
            raise RuntimeError("verification verdict must be passed, failed or cancelled")
        if not isinstance(result.get("report"), str) or not result["report"].strip():
            raise RuntimeError("verification result requires its independent report")
        if verdict == "cancelled":
            if result.get("cancellation_confirmed") is not True:
                raise RuntimeError("reader cancellation must be confirmed before reopening writes")
        elif verdict == "passed" and mode != "qa_diagnostic":
            checks = result.get("checks", {})
            for check in required_checks(root, current, role):
                evidence = checks.get(check, {}) if isinstance(checks, dict) else {}
                if (not isinstance(evidence, dict) or evidence.get("passed") is not True
                        or not isinstance(evidence.get("evidence"), str) or not evidence["evidence"].strip()):
                    raise RuntimeError(f"final {role} result requires passing {check} evidence")
                if check == "mutation_whole_changed_files" and evidence.get("files") != current["mutation_files"]:
                    raise RuntimeError("mutation evidence must cover every compiler-selected changed file")
            if role == "qa_engineer":
                test = checks["full_test_suite"]
                contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
                if test.get("command") != contract.get("test_command") or test.get("exit_code") != 0:
                    raise RuntimeError("full suite evidence must identify the approved test command and successful exit")
                if not isinstance(test.get("environment"), str) or not test["environment"].strip():
                    raise RuntimeError("full suite evidence requires the verification environment identity")
                require_raw_evidence(root, value, checks)
                if current["runtime_required"]:
                    require_runtime_evidence(root, value, checks["fresh_runtime"])
        findings = result.get("findings", [])
        if (not isinstance(findings, list) or any(not isinstance(finding, dict)
                or not all(isinstance(finding.get(key), str) and finding[key].strip()
                           for key in ("id", "severity", "verification"))
                or finding.get("status") not in {"open", "resolved"} for finding in findings)
                or len({finding["id"] for finding in findings}) != len(findings)):
            raise RuntimeError("findings require unique stable IDs, severity, verification and open/resolved status")
        allowed_severities = {value.casefold() for value in policy()["blocking_severities"] + policy()["nonblocking_severities"]}
        if any(finding["severity"].casefold() not in allowed_severities for finding in findings):
            raise RuntimeError("finding severity is not declared in the verification policy")
        if verdict == "passed" and any(finding["severity"].casefold() in {value.casefold() for value in policy()["blocking_severities"]}
                                       and finding["status"] == "open" for finding in findings):
            raise RuntimeError("passing verification cannot retain an open blocking finding")
        inherited = {finding["id"]: finding for finding in value.get("unresolved_findings", []) if finding["role"] == role}
        dispositions = {finding["id"]: finding for finding in findings}
        if any(finding["severity"].casefold() != inherited[identifier]["severity"].casefold()
               for identifier, finding in dispositions.items() if identifier in inherited):
            raise RuntimeError("inherited finding severity must be preserved")
        blocking = {severity.casefold() for severity in policy()["blocking_severities"]}
        if verdict == "passed" and mode != "qa_diagnostic" and (
                not inherited.keys() <= dispositions.keys()
                or any(finding["severity"].casefold() in blocking and dispositions[identifier]["status"] != "resolved"
                       for identifier, finding in inherited.items())):
            raise RuntimeError("final result must explicitly disposition every inherited finding and resolve blocking findings")
        if (role == "code_reviewer" and verdict != "cancelled"
                and review_loop(root, current["delivery"]) == "blocking_delta"):
            if "calibration" in result:
                raise RuntimeError("calibration rows come only from the calibration reader's own result,"
                                   " registered with calibrate; the claiming result carries none")
            item = item_record(root, current["delivery"], current["story"])
            ruled = {identifier for identifier, finding in inherited.items() if "calibrated_severity" in finding}
            claims = open_claims(findings, ruled)
            registered = (value.get("calibration") or {}).get("result")
            if claims or registered:
                if registered is None or sorted(registered["claims"], key=lambda claim: claim["id"]) != claims:
                    raise RuntimeError("register the calibration reader's result with calibrate for exactly the"
                                       " open critical or major claims no earlier calibration ruled, as returned: "
                                       + (", ".join(claim["id"] for claim in claims) or "none"))
                result = {**result, "calibration": registered["calibration"],
                          "calibration_result_hash": registered["result_hash"]}
            problems = calibration_problems(root, current, result, ruled, follow_up_roles(item))
            if problems:
                raise RuntimeError("severity calibration is incomplete: " + "; ".join(problems))
            if calibrated_verdict(result) != verdict:
                checks = result.get("checks", {})
                for check in required_checks(root, current, role):
                    evidence = checks.get(check, {}) if isinstance(checks, dict) else {}
                    if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence"), str) \
                            or not evidence["evidence"].strip():
                        raise RuntimeError(f"a calibrated {role} pass requires {check} evidence")
                if not inherited.keys() <= dispositions.keys():
                    raise RuntimeError("final result must explicitly disposition every inherited finding"
                                       " and resolve blocking findings")
            # Refuses an open minor finding that is not a complete follow-up.
            open_follow_ups(result, item)
        stored = dict(result)
        stored["result_hash"] = digest(result)
        value["workers"][role] = {"state": "cancelled" if verdict == "cancelled" else "settled", "result": stored,
                                  "completed_at": time.time(),
                                  "reader_elapsed_seconds": max(0.0, time.time() - value["workers"][role].get("started_at", time.time()))}
        write_session(root, value)
        return value


def resume_qa(root: Path) -> dict:
    """Continue QA on unchanged sources without repeating the independent review."""
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the verification command to exit before resuming QA")
        value = read_session(root)
        require_current(root, value, allow_evidence=True)
        worker = value["workers"]["qa_engineer"]
        result = worker.get("result", {})
        failed_final = result.get("mode") == "qa_final" and result.get("verdict") == "failed"
        if worker["state"] != "settled" or not (result.get("mode") == "qa_diagnostic" or failed_final):
            raise RuntimeError("only a settled QA diagnostic or failed final QA can resume on the same candidate")
        if runtime_needs_cleanup(value):
            raise RuntimeError("tear the environment down before resuming QA")
        retained = {(finding["role"], finding["id"]): finding for finding in value.get("unresolved_findings", [])}
        for finding in result.get("findings", []):
            if finding["status"] == "open":
                retained[("qa_engineer", finding["id"])] = {**finding, "role": "qa_engineer"}
        value["unresolved_findings"] = [retained[key] for key in sorted(retained)]
        value.setdefault("qa_attempts", []).append(worker)
        runtime = value.get("runtime")
        failed_runtime = runtime and (runtime.get("interrupted_commands")
                                      or any(event.get("exit_code") != 0 or event.get("candidate_intact") is not True
                                             for event in runtime.get("events", [])))
        if runtime and (failed_final or failed_runtime):
            # Preserve failure evidence and its files. A new attempt receives a
            # different checkout and output namespace and cannot inherit events.
            value.setdefault("runtime_attempts", []).append(runtime)
            value.pop("runtime")
        value["workers"]["qa_engineer"] = {"state": "running", "started_at": time.time(), "resumed_from": result}
        write_session(root, value)
        return value


def validate(root: Path, delivery_id: str, story: str) -> dict:
    root = root.resolve()
    if command_active(root):
        raise RuntimeError("verification command is still running")
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if current["delivery"] != delivery_id or current["story"] != story:
        raise RuntimeError("verification session belongs to another Item")
    for role in ROLES:
        worker = value["workers"][role]
        result = worker.get("result", {})
        verdict = result.get("verdict")
        if (role == "code_reviewer" and verdict == "failed" and result.get("calibration")
                and review_loop(root, delivery_id) == "blocking_delta"):
            verdict = calibrated_verdict(result)
        if (worker["state"] != "settled" or verdict != "passed"
                or result.get("mode") not in policy()["final_modes"][role]
                or result.get("candidate_hash") != current["candidate_hash"]
                or result.get("session_id") != value["session_id"]
                or result.get("result_hash") != digest({key: item for key, item in result.items() if key != "result_hash"})):
            raise RuntimeError(f"same-candidate final passed {role} result is required")
        if role == "qa_engineer":
            require_raw_evidence(root, value, result["checks"])
            if current["runtime_required"]:
                require_runtime_evidence(root, value, result["checks"]["fresh_runtime"])
    return value


def runtime_needs_cleanup(session: dict) -> bool:
    state = session.get("runtime", {})
    return bool(state.get("active") or state.get("cleanup_required") or state.get("pending"))


def runtime_environment_identity(root: Path) -> dict:
    return {"environment_hash": digest(command_environment(root)), "python": sys.version,
            "platform": platform.platform(), "git": git(root, "--version")}


def run_environment(root: Path, verb: str, value: str | None = None) -> dict:
    """Invoke only the approved environment contract in a persistent private clone."""
    root = root.resolve()
    if verb not in {"down", "up", "seed", "logs", "url"}:
        raise RuntimeError("unsupported environment verb")
    with environment_lock(root, "qa_engineer", "environment --verb " + verb), command_lock(root):
        with locked(root):
            session = read_session(root)
            current = session["candidate"] if verb == "down" else require_current(root, session, allow_evidence=True)
            if not current["runtime_required"] or session["workers"]["qa_engineer"]["state"] != "running":
                raise RuntimeError("environment commands require an active runtime QA reader")
            contract, _, error = delivery.parse_frontmatter(git(root, "show", current["product_commit"] + ":workspace/docs/operation/environment-contract.md"))
            if error:
                raise RuntimeError("frozen environment contract cannot be parsed")
            command = contract.get("env_command", "")
            if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
                raise RuntimeError("approved environment command is missing or unresolved")
            if verb in {"seed", "url"}:
                allowed = contract.get("scenarios" if verb == "seed" else "service_catalog", [])
                if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value)
                        or value not in allowed):
                    raise RuntimeError("environment argument must be an exact approved scenario or service identifier")
            elif value is not None:
                raise RuntimeError("this environment verb does not accept a value")
            state = session.get("runtime", {"attempt_id": uuid.uuid4().hex, "events": [], "active": False})
            if state.get("pending") and verb != "down":
                raise RuntimeError("interrupted runtime command requires teardown before another command")
            if state.get("attempt_id") and not re.fullmatch(r"[0-9a-f]{32}", str(state["attempt_id"])):
                raise RuntimeError("runtime attempt identity is invalid")
            successful = [event["verb"] for event in state["events"] if event["exit_code"] == 0]
            if verb == "up" and (not successful or successful[-1] != "down"):
                raise RuntimeError("fresh runtime requires a successful down before up")
            if verb in {"seed", "logs", "url"} and not state["active"]:
                raise RuntimeError("runtime must be up before seed, logs or url")
            # Legacy disposable sessions retain their old path for teardown.
            # New attempts always have independent checkouts and raw output.
            namespace = state.get("attempt_id", session["session_id"])
            execution_root = safe_runtime_path(root, session_path(root).parent / "scratch" / ("runtime-" + namespace))
            if not execution_root.exists():
                execution_root.parent.mkdir(parents=True, exist_ok=True)
                git(root, "clone", "--no-local", "--no-hardlinks", "--no-checkout", "--", str(root), str(execution_root))
                git(execution_root, "checkout", "--detach", current["product_commit"])
            if execution_root.is_symlink() or (verb != "down" and git(execution_root, "rev-parse", "HEAD") != current["product_commit"]):
                raise RuntimeError("runtime checkout no longer binds the frozen candidate")
            workdir = (execution_root / str(contract.get("env_workdir", "."))).resolve()
            if workdir != execution_root.resolve() and execution_root.resolve() not in workdir.parents:
                raise RuntimeError("environment workdir escapes its isolated candidate")
            from delivery_git import require_visible_item_index
            try:
                require_visible_item_index(execution_root)
                before_intact = not git(execution_root, "diff", "--name-only", "HEAD")
            except RuntimeError:
                before_intact = False
            if not before_intact and verb != "down":
                raise RuntimeError("runtime checkout changed; only teardown is allowed before a new verification session")
            bound_session = session["session_id"]
            environment_identity = runtime_environment_identity(root)
            # Persist before launching: a failed/interrupted up may still leave
            # real services behind, so only a successful down permits settlement.
            if state.get("pending"):
                state.setdefault("interrupted_commands", []).append(state["pending"])
            state.update(cleanup_required=True, pending={"verb": verb, "value": value, "started_at": time.time(),
                                                        "candidate_hash": current["candidate_hash"],
                                                        "environment_identity": environment_identity})
            session["runtime"] = state
            write_session(root, session)
        # Only fixed verbs and declared identifier tokens enter this argument
        # suffix; project command bytes remain the approved contract's bytes.
        command = command + " " + verb + (" " + value if value else "")
        started = time.monotonic()
        completed = subprocess.run(command, cwd=workdir, env=command_environment(root), shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        with locked(root):
            session = read_session(root)
            if session["session_id"] != bound_session:
                raise RuntimeError("runtime reader session changed while command ran")
            if verb != "down":
                require_current(root, session, allow_evidence=True)
            state = session["runtime"]
            output_name = "scratch/runtime-" + state.get("attempt_id", session["session_id"]) + "-" + str(len(state["events"])) + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
            try:
                require_visible_item_index(execution_root)
                intact = (before_intact and git(execution_root, "rev-parse", "HEAD") == current["product_commit"]
                          and not git(execution_root, "diff", "--name-only", "HEAD"))
            except RuntimeError:
                intact = False
            event = {"candidate_intact": intact, "verb": verb, "value": value, "command": command, "exit_code": completed.returncode,
                     "output_file": output_name, "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                     "duration_seconds": time.monotonic() - started, "candidate_hash": current["candidate_hash"],
                     "environment_identity": environment_identity, "completed_at": time.time(),
                     "attempt_id": state.get("attempt_id")}
            event["evidence_hash"] = digest(event)
            state["events"].append(event)
            state.pop("pending", None)
            if completed.returncode == 0 and verb in {"up", "down"}:
                state["active"] = verb == "up"
                state["cleanup_required"] = verb != "down"
            session["runtime"] = state
            session["metrics"]["command_seconds"] += event["duration_seconds"]
            write_session(root, session)
            return {**event, "output": completed.stdout.decode("utf-8", errors="replace")}


# The interpreter search paths a lane command never inherits from outside the Item worktree.
LANE_SEARCH_PATH_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "NODE_PATH")


def lane_command_environment(root: Path, directory: Path) -> tuple[dict, dict[str, list[str]]]:
    """Return the inherited environment without search path entries outside the Item worktree.

    An entry is kept when it resolves inside the worktree, a relative entry
    against the command's working directory and a link through its target. A
    variable left with no entry is unset. The second value lists the dropped
    entries of each variable.
    """
    environment = dict(os.environ)
    dropped: dict[str, list[str]] = {}
    for name in LANE_SEARCH_PATH_VARIABLES:
        if name not in environment:
            continue
        kept: list[str] = []
        for entry in environment[name].split(os.pathsep):
            resolved = (directory / entry).resolve()
            if resolved == root or root in resolved.parents:
                kept.append(entry)
            else:
                dropped.setdefault(name, []).append(entry)
        if kept:
            environment[name] = os.pathsep.join(kept)
        else:
            del environment[name]
    return environment, dropped


def lane_run(root: Path, delivery_id: str, story: str, role: str, kind: str,
             verb: str | None = None, value: str | None = None) -> dict:
    """Run the approved full test command or an approved environment verb for one lane.

    Parallel lanes share the Item worktree and its one environment, so the
    coordinator runs these commands here, in that worktree and before the
    candidate freeze, and each takes the Item's environment lock.
    """
    root = root.resolve()
    item = item_record(root, delivery_id, story)
    if item.get("status") != "active" or not item.get("item_plan_hash"):
        raise RuntimeError("lane commands require an active, approved Item")
    if delivery.implementation_schedule(item) != "parallel_lanes_v1":
        raise RuntimeError("lane commands serve only an Item whose approved plan runs parallel_lanes_v1")
    if role not in delivery.lane_roles(item):
        raise RuntimeError(f"{role} is not a lane of {story}")
    guard_write(root)
    kinds = {"test": ("verification", "test_command", "test_workdir"),
             "environment": ("environment", "env_command", "env_workdir")}
    if kind not in kinds:
        raise RuntimeError("unsupported lane command kind")
    contract_kind, command_key, workdir_key = kinds[kind]
    relative = f"workspace/docs/operation/{contract_kind}-contract.md"
    try:
        contract, _, error = delivery.parse_frontmatter(git(root, "show", "HEAD:" + relative))
    except RuntimeError as exc:
        raise RuntimeError(f"the Item worktree's HEAD holds no {relative}") from exc
    if error:
        raise RuntimeError(f"{relative} cannot be parsed")
    command = contract.get(command_key)
    if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
        raise RuntimeError("approved command is missing or contains unresolved parameters")
    if kind == "test":
        if verb is not None or value is not None:
            raise RuntimeError("the test command takes no verb or value")
        label = "test"
    else:
        if verb not in {"down", "up", "seed", "logs", "url"}:
            raise RuntimeError("unsupported environment verb")
        if verb in {"seed", "url"}:
            allowed = contract.get("scenarios" if verb == "seed" else "service_catalog", [])
            if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value)
                    or value not in allowed):
                raise RuntimeError("environment argument must be an exact approved scenario or service identifier")
        elif value is not None:
            raise RuntimeError("this environment verb does not accept a value")
        label = "environment --verb " + verb + (" " + value if value else "")
        # Only fixed verbs and approved identifiers join the approved command bytes.
        command = command + " " + verb + (" " + value if value else "")
    directory = (root / str(contract.get(workdir_key, "."))).resolve()
    if directory != root and root not in directory.parents:
        raise RuntimeError("lane command workdir must remain inside the Item worktree")
    output = safe_runtime_path(root, session_path(root).parent / "lanes" / f"{role}-{uuid.uuid4().hex}.log",
                               file_only=True)
    environment, dropped = lane_command_environment(root, directory)
    with environment_lock(root, role, label) as interrupted:
        output.parent.mkdir(exist_ok=True)
        started = time.monotonic()
        completed = subprocess.run(command, cwd=directory, env=environment, shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        duration = time.monotonic() - started
        atomic_file.replace_bytes(output, completed.stdout)
    return {"delivery": delivery_id, "story": story, "holder": role, "kind": kind, "command": command,
            "exit_code": completed.returncode, "output_file": str(output), "duration_seconds": duration,
            "interrupted_holder": interrupted, "dropped_search_paths": dropped}


def require_runtime_evidence(root: Path, session: dict, evidence: dict) -> None:
    state = session.get("runtime", {})
    events = state.get("events", [])
    attempt_id = state.get("attempt_id")
    verbs = [event["verb"] for event in events if event.get("exit_code") == 0]
    if (not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
            or runtime_needs_cleanup(session) or state.get("interrupted_commands")
            or state.get("active") is not False or not verbs or verbs[0] != "down" or verbs[-1] != "down"
            or any(verb not in verbs for verb in ("up", "seed", "logs"))
            or evidence.get("event_hashes") != [event["evidence_hash"] for event in events]):
        raise RuntimeError("fresh runtime evidence requires recorded down/up/seed/logs/down and exact event hashes")
    environment_identity = runtime_environment_identity(root)
    for event in events:
        path = raw_output_path(root, event["output_file"])
        if (event.get("exit_code") != 0 or event.get("candidate_intact") is not True or event.get("candidate_hash") != session["candidate"]["candidate_hash"]
                or event.get("attempt_id") != attempt_id
                or event.get("environment_identity") != environment_identity
                or not 0 <= time.time() - event.get("completed_at", 0) <= policy()["raw_evidence_max_age_seconds"]
                or event.get("evidence_hash") != digest({key: value for key, value in event.items() if key != "evidence_hash"})
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != event["output_sha256"]):
            raise RuntimeError("runtime command evidence is failed, stale or missing")


def inspect_instruction(root: Path, relative: str) -> dict:
    current = require_current(root, read_session(root), allow_evidence=True)
    if not delivery._is_normalized_claim(relative):
        raise RuntimeError("instruction path must be normalized and package-relative")
    path = PurePosixPath(relative)
    if relative != "constitution.md" and path.parts[0] not in {"agents", "skill-content", "flows", "scripts", "templates"}:
        raise RuntimeError("instruction path is outside the bound package instruction roots")
    base = Path(__file__).resolve().parents[1]
    file = base / relative
    if any(parent.is_symlink() for parent in (file, *file.parents) if parent != base and base in parent.parents):
        raise RuntimeError("instruction reads cannot follow symlinks")
    if not file.is_file():
        raise RuntimeError("instruction file is missing")
    return {"candidate_hash": current["candidate_hash"], "instruction_identity": current["instruction_identity"],
            "path": relative, "content": file.read_text(encoding="utf-8")}


def inspect_candidate(root: Path, relative: str, *, base: bool = False) -> dict:
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if not delivery._is_normalized_claim(relative):
        raise RuntimeError("inspect path must be normalized and repository-relative")
    commit = current["integration_base_commit"] if base else current["product_commit"]
    completed = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "show", f"{commit}:{relative}"],
                               capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError("path is not present in the selected candidate tree")
    import base64
    try:
        content, encoding = completed.stdout.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(completed.stdout).decode("ascii"), "base64"
    return {"candidate_hash": current["candidate_hash"], "commit": commit, "path": relative,
            "encoding": encoding, "content": content}


def candidate_diff(root: Path, paths: list[str]) -> dict:
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if any(not delivery._is_normalized_claim(path) for path in paths):
        raise RuntimeError("diff paths must be normalized and repository-relative")
    return {"candidate_hash": current["candidate_hash"], "diff": git(root, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv",
            current["integration_base_commit"], current["product_commit"], "--", *paths)}


def validate_evidence(item: dict, review: dict, verification: dict) -> None:
    """Check durable final-mode bindings after the product becomes evidence's parent."""
    if delivery.verification_schedule(item) == "sequential_v1":
        return
    candidate_hash = review.get("verification_candidate_hash")
    if (not isinstance(candidate_hash, str) or not candidate_hash.startswith("sha256:")
            or verification.get("verification_candidate_hash") != candidate_hash):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: independent reports must bind the same verification candidate")
    for role, record in (("code_reviewer", review), ("qa_engineer", verification)):
        if (record.get("verification_mode") not in policy()["final_modes"][role]
                or not str(record.get("verification_result_hash", "")).startswith("sha256:")):
            raise RuntimeError(f"DELIVERY_ITEM_NOT_READY: final {role} verification result is missing")


def manifest(root: Path, delivery_id: str, story: str, role: str, mode: str) -> dict:
    if role not in ROLES or mode not in policy()["role_modes"][role]:
        raise RuntimeError("unsupported verification role or mode")
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if (current["delivery"], current["story"]) != (delivery_id, story):
        raise RuntimeError("verification session belongs to another Item")
    previous = value.get("previous_candidate")
    delta = list(current["changed_files"])
    scope_expanded = bool(previous and (previous.get("inputs") != current["inputs"]
                                       or previous.get("instruction_identity") != current["instruction_identity"]))
    if previous and not scope_expanded and mode in {"review_repair", "qa_diagnostic"}:
        old = previous.get("product_commit", "")
        try:
            delta = sorted(set(part for part in git(root, "diff", "--name-only", "--no-renames", "-z", old, current["product_commit"]).split("\0") if part))
        except RuntimeError:
            scope_expanded = True
    contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    checks = {name: {"passed": False, "evidence": "Independent assessment of this gate"}
              for name in required_checks(root, current, role)} if mode != "qa_diagnostic" else {}
    for name, command_kind in (("full_test_suite", "test"), ("mutation_whole_changed_files", "mutation"),
                               ("dependency_audit", "dependency_audit")):
        if name in checks:
            checks[name]["raw_evidence_hash"] = "Copy evidence_hash from run --kind " + command_kind
    if "full_test_suite" in checks:
        checks["full_test_suite"].update(command=contract["test_command"], exit_code=0,
                                         environment="Copy identity.environment_hash from run --kind test")
    if "mutation_whole_changed_files" in checks:
        checks["mutation_whole_changed_files"]["files"] = current["mutation_files"]
    if "fresh_runtime" in checks:
        checks["fresh_runtime"]["event_hashes"] = []
    return {"schema_version": 1, "role": role, "mode": mode, "session_id": value["session_id"],
            **current, "required_checks": list(checks),
            "full_read": sorted(set(current["inputs"]) | set(current["changed_files"])),
            "repair_delta": delta, "scope_expanded": scope_expanded, "unresolved_findings": value.get("unresolved_findings", []),
            "read_interface": {"inspect": "inspect --path <repository-relative-path> [--base]", "diff": "diff [--path <repository-relative-path>]"},
            "review_passes": policy()["review_checks"], "allowed_writes": [str(session_path(root).parent / "scratch")],
            "mutation_scope_file": str(session_path(root).parent / "mutation-files.json"),
            "result_interface": {"candidate_hash": current["candidate_hash"], "session_id": value["session_id"],
                                 "role": role, "mode": mode, "verdict": "passed|failed|cancelled",
                                 "report": "Independent findings and conclusion", "checks": checks, "findings": []},
            "diagnostic_interface": {"available": bool(contract.get("diagnostic_test_command")),
                                     "command": "run --kind diagnostic_test --selection-file <scratch-selection.json>",
                                     "selection": {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                                                   "failed_test_ids": [], "affected_test_ids": []},
                                     "environment_variable": "AGENTROF_DIAGNOSTIC_TESTS",
                                     "terminal_evidence": False},
            "execution_note": "Commands run in private clones containing tracked files only. Approved commands must provision dependencies or use a fixed external environment; ignored dependencies are never copied. The clone is not an operating-system sandbox for trusted commands with absolute paths.",
            "next_transition": "Register independent result; owner writes reports only after both readers settle"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "manifest", "validate"):
        cmd = subs.add_parser(name)
        cmd.add_argument("--delivery", required=True)
        cmd.add_argument("--story", required=True)
    subs.choices["freeze"].add_argument("--fresh", action="store_true")
    for name in ("manifest",):
        subs.choices[name].add_argument("--role", choices=ROLES, required=True)
        subs.choices[name].add_argument("--mode", required=True)
    result = subs.add_parser("result")
    result.add_argument("--file", required=True)
    calibrate = subs.add_parser("calibrate")
    calibrate.add_argument("--file", required=True)
    run = subs.add_parser("run")
    run.add_argument("--kind", choices=("test", "mutation", "dependency_audit", "diagnostic_test"), required=True)
    run.add_argument("--selection-file", type=Path)
    run.add_argument("--fresh", action="store_true")
    environment = subs.add_parser("environment")
    environment.add_argument("--verb", required=True, choices=("down", "up", "seed", "logs", "url"))
    environment.add_argument("--value")
    lane = subs.add_parser("lane-run")
    lane.add_argument("--delivery", required=True)
    lane.add_argument("--story", required=True)
    lane.add_argument("--role", required=True)
    lane.add_argument("--kind", choices=("test", "environment"), required=True)
    lane.add_argument("--verb", choices=("down", "up", "seed", "logs", "url"))
    lane.add_argument("--value")
    inspect = subs.add_parser("inspect")
    selection = inspect.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path")
    selection.add_argument("--instruction")
    inspect.add_argument("--base", action="store_true")
    diff = subs.add_parser("diff")
    diff.add_argument("--path", action="append", default=[])
    subs.add_parser("resume-qa")
    subs.add_parser("status")
    args = parser.parse_args(argv)
    root = Path(args.worktree).resolve()
    try:
        if args.command == "freeze":
            value = freeze(root, args.delivery, args.story, fresh=args.fresh)
        elif args.command == "result":
            value = register_result(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "calibrate":
            value = register_calibration(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "validate":
            value = validate(root, args.delivery, args.story)
        elif args.command == "environment":
            value = run_environment(root, args.verb, args.value)
        elif args.command == "lane-run":
            value = lane_run(root, args.delivery, args.story, args.role, args.kind, args.verb, args.value)
        elif args.command == "inspect":
            value = inspect_instruction(root, args.instruction) if args.instruction else inspect_candidate(root, args.path, base=args.base)
        elif args.command == "diff":
            value = candidate_diff(root, args.path)
        elif args.command == "resume-qa":
            value = resume_qa(root)
        elif args.command == "run":
            value = run_check(root, args.kind, fresh=args.fresh, selection_file=args.selection_file)
        elif args.command == "manifest":
            value = manifest(root, args.delivery, args.story, args.role, args.mode)
        else:
            value = read_session(root)
        print(json.dumps({"ok": True, **value}, indent=2))
        return 0 if args.command not in {"run", "environment", "lane-run"} or (value["exit_code"] == 0 and value.get("candidate_intact", True)) else 1
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
