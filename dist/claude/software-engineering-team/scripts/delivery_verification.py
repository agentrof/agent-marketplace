#!/usr/bin/env python3
"""Coordinate independent, source-bound Item verification in disposable runtime."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import functools
import fnmatch
import json
import os
import platform
import re
import secrets
import stat
import time
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import uuid

import atomic_file
import backlog_compile
from backlog_compile import meaningful_text, scenario_blocks, scenario_fields
import delivery_compile as delivery
import file_lock
import operation_compile

POLICY_PATH = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/delivery-verification-policy.json"
ROLES = ("code_reviewer", "qa_engineer")
# How often one wait call checks the Item's locks; a local check costs no model call.
WAIT_POLL_SECONDS = 0.5
# At review_loop blocking_delta a fresh code reviewer registers its rulings on
# the claims of a code review in this mode, apart from the claiming result.
CALIBRATION_MODE = "calibration"
# At pre_handoff_regression touched_suites the coordinator runs the suites of the
# earlier stories a candidate touches before the freeze, which needs that run.
PRE_HANDOFF_SWITCH = "pre_handoff_regression"
TOUCHED_SUITES = "touched_suites"
PRE_HANDOFF_HOLDER = "delivery_coordinator"
PRE_HANDOFF_RUN = "regression-run"
PRE_HANDOFF_SELECTION = "scratch/pre-handoff-selection.json"
# What a pre-handoff run must share with the selection derived for the candidate.
PRE_HANDOFF_IDENTITY = ("candidate_tree", "kind", "command", "workdir", "affected_test_ids")
# What the sessions carry of every pre-handoff run, until approve-item-evidence records it.
PRE_HANDOFF_HISTORY_FIELDS = ("evidence_hash", "candidate_tree", "kind", "exit_code", "candidate_intact",
                              "duration_seconds", "earlier_stories")
# At own_target_reuse spot_run QA's final test run also takes the Item's own Test
# Plan targets from the accepted pre-handoff run, but for the spot-run targets QA
# names, which the approved test command runs with every target the run did not cover.
OWN_TARGET_SWITCH = "own_target_reuse"
SPOT_RUN = "spot_run"
# At test_group_report refuse_missing_groups a test run also reads the group
# report its approved command writes, where the Verification Contract declares one.
GROUP_REPORT_SWITCH = "test_group_report"
REFUSE_MISSING_GROUPS = "refuse_missing_groups"
GROUP_STATUSES = ("passed", "failed", "not_collected")
GROUP_COUNTS = ("passed", "failed", "skipped")
MISSING_GROUP = "missing"
# At level_change_map assertion_map an Item that rewrites the automation target of
# a scenario whose Test Plan level changed since its story integrated records,
# per scenario, the assertions that prove its Then before and after the change;
# freeze refuses an incomplete or unanchored map and the code reviewer reads the
# pairs the check flags (#440).
LEVEL_CHANGE_SWITCH = "level_change_map"
ASSERTION_MAP = "assertion_map"
ASSERTION_MAP_FILE = "assertion-map.json"
ASSERTION_KINDS_PATH = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/assertion-kinds.json"
ASSERTION_FIELDS = ("path", "code", "kind", "expected")
# At test_engines partitioned QA's final test run runs the partitions the
# Verification Contract declares in parallel, one private clone each, over its
# isolated test engines, longest first by the durations the Item's runtime keeps.
ENGINE_SWITCH = "test_engines"
PARTITIONED = "partitioned"
PARTITION_DURATIONS = "partition-durations.json"
# At process switch code_review_panel beside_official a lens panel reads the
# frozen candidate beside the official code reviewer, and merge-panel
# registers the one code review result from both.
CODE_REVIEW_PANEL = "code_review_panel"
BESIDE_OFFICIAL = "beside_official"
PANEL_LENS_MODE = "panel_lens"
PANEL_DATA_PATH = Path(__file__).resolve().parents[1] / "skill-content/code-review/data/code-review-panel.json"
PANEL_STEP = "code_review"
OFFICIAL = "official"
DUPLICATE = "duplicate"
PANEL_PASS_RE = re.compile(r"^P([1-9][0-9]*)-")
# The blocking severities of the code-review skill's Severity Definitions, highest first.
REVIEW_SEVERITY_ORDER = ("critical", "major")
# A run identity covers the variables that can change a command's result, never
# the shell that ran it: these, the ones the approved Verification Contract names
# in command_variables, and every variable of the prefixed namespaces that the
# command's environment holds. PWD, OLDPWD, SHLVL and session variables stay out,
# so evidence recorded in one reader's shell binds in every other one (#356). It
# keeps a hash of their values keyed with IDENTITY_KEY, which never leaves the
# Item's verification runtime, since QA's result copies that hash into a tracked
# record and a hash of a few guessable values would check a guess of a credential.
COMMAND_VARIABLES = ("HOME", "LANG", "PATH", "TZ")
COMMAND_VARIABLE_PREFIXES = ("AGENTROF_", "LC_")
# The runner sets these for every command it runs.
RUNNER_VARIABLES = ("AGENTROF_MUTATION_FILES", "AGENTROF_VERIFICATION_SCRATCH")
# The runner's per-run inputs: an identity binds the data each carries, so it
# names neither the variable nor its path. A partitioned test run hands each
# partition its own file and engine, which its record names, never the identity.
PARTITION_VARIABLES = ("AGENTROF_TEST_PARTITION", "AGENTROF_TEST_ENGINE")
SELECTION_VARIABLES = ("AGENTROF_DIAGNOSTIC_TESTS", "AGENTROF_REUSED_TESTS", *PARTITION_VARIABLES)
# At touched_suites QA's final test run names here the earlier-story targets the
# accepted pre-handoff run covered, for the approved test command to skip (#354).
REUSED_TESTS = "reused-tests.json"
# What every final run of one session shares, and what an identity records of its host.
ENVIRONMENT_FIELDS = ("environment_variables", "environment_hash", "python", "platform", "git")
IDENTITY_KEY = "identity.key"
IDENTITY_KEY_BYTES = 32
# A runner before #356 hashed the whole process environment and checked it against the checker's.
LEGACY_ENVIRONMENT = ("carries the whole-environment hash of an earlier runner, which binds the shell that ran"
                      " it; freeze the candidate again with freeze --fresh and rerun both readers")


def policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def keyed_digest(key: bytes, value) -> str:
    return "hmac-sha256:" + hmac.new(key, canonical(value), hashlib.sha256).hexdigest()


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


def command_owner_path(root: Path) -> Path:
    return safe_runtime_path(root, safe_runtime(root).with_name("command-owner.json"), file_only=True)


@contextlib.contextmanager
def command_lock(root: Path, role: str, command: str):
    """Hold the verification command lock while *role* runs *command*, with its owner record beside it.

    The record names the role, so a reader's registration waits only for that
    reader's own command (#355). As with the environment lock, the operating
    system ends the lock with its holder, and a record is read only while the
    lock is held.
    """
    path = safe_runtime_path(root, safe_runtime(root).with_name("commands.lock"), file_only=True)
    owner = command_owner_path(root)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not file_lock.try_lock(fd):
            raise RuntimeError("another verification command is still running; `wait` returns once it has exited")
        try:
            atomic_file.replace_text(owner, json.dumps(
                {"role": role, "command": command, "pid": os.getpid(),
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                indent=2, sort_keys=True) + "\n")
            yield
        finally:
            with contextlib.suppress(OSError):
                owner.unlink()
            file_lock.unlock(fd)
    finally:
        os.close(fd)


def command_holder(root: Path) -> dict | None:
    """The owner record of the running verification command, {} before it records one, or None when none runs."""
    if not command_active(root):
        return None
    return environment_owner(command_owner_path(root)) or {}


def recorded_command_owner(owner: dict) -> bool:
    return (all(isinstance(owner.get(key), str) for key in ("role", "command", "started_at"))
            and type(owner.get("pid")) is int)


def wait_for_own_command(root: Path, role: str, action: str) -> None:
    """Refuse *action* of a reader in *role* while its own verification command runs.

    A reader settles only once its own command has exited, so its evidence
    cannot change after its verdict. Another role's command holds no other
    reader: the code reviewer runs none and registers while QA's runs. A
    command that has not recorded its owner yet holds every reader.
    `wait --role` returns once the same check passes.
    """
    holder = wait_holder(root, role)
    if holder is None:
        return
    if not recorded_command_owner(holder):
        raise RuntimeError("wait for a verification command that has not recorded its owner yet to exit"
                           f" before {action}; `wait --role {role}` returns once it has")
    raise RuntimeError(f"wait for {holder['role']}'s verification command `{holder['command']}` in process"
                       f" {holder['pid']} since {holder['started_at']} to exit before {action};"
                       f" `wait --role {role}` returns once it has")


def wait_holder(root: Path, role: str | None) -> dict | None:
    """What a wait by *role* still waits for, or None once nothing does.

    For a reader role it is that reader's own running verification command, or
    a command that has not recorded its owner yet, which holds every reader:
    what wait_for_own_command refuses. Without a role it is any holder of the
    Item's environment lock or verification command lock, which every command,
    freeze and guarded write of the Item refuses.
    """
    if role is None:
        environment = environment_holder(root)
        if environment is not None:
            return {"lock": "environment", **environment}
    command = command_holder(root)
    if command is None or (role is not None and recorded_command_owner(command) and command["role"] != role):
        return None
    return {"lock": "verification_command", **command}


def wait_for_release(root: Path, role: str | None = None, seconds: float | None = None) -> dict:
    """Block until nothing the caller waits for holds the Item, and at most the policy's wait bound.

    The call returns as soon as the holder releases its lock, so a waiting role
    acts on the command's exit rather than on an interval of its own. It never
    blocks longer than wait_bound_seconds, which stays under the hosts' prompt
    cache lifetime: a role that calls wait again at once makes each model call
    of its wait while its cached context is warm, where one long sleep makes the
    host write that whole context into its cache again.
    """
    root = root.resolve()
    bound = policy()["wait_bound_seconds"]
    limit = bound if seconds is None else seconds
    if role is not None and role not in ROLES:
        raise RuntimeError("wait takes no role or a reader role: " + ", ".join(ROLES))
    if not 0 < limit <= bound:
        raise RuntimeError(f"one wait blocks for more than 0 and at most {bound} seconds, so the next model call"
                           " finds the prompt cache warm; call wait again to wait longer")
    started = time.monotonic()
    while True:
        holder = wait_holder(root, role)
        elapsed = time.monotonic() - started
        if holder is None or elapsed >= limit:
            break
        time.sleep(min(WAIT_POLL_SECONDS, limit - elapsed))
    value = {"role": role, "released": holder is None, "waited_seconds": round(elapsed, 3),
             "bound_seconds": bound}
    if holder is not None:
        value.update(holder=holder, next="call wait again now, as a tool call of its own")
    return value


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


def environment_busy(owner: dict) -> RuntimeError:
    """The refusal of a command that finds the Item's environment lock held, naming its holder."""
    return RuntimeError("DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by "
                        + describe_environment_holder(owner) + "; run this after it finishes")


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
            raise environment_busy(environment_owner(owner) or {})
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
    """Refuse writes while either reader or a verification command is active; only isolated scratch is writable.

    The refusal names what holds the Item: its running readers, or else the
    command that holds the verification command lock, which with no reader
    running is a pre-handoff regression run before the freeze. That run holds
    the lock only while it derives its selection and clones the candidate.
    """
    root = Path(worktree).resolve()
    if not (root / ".git").exists():
        return
    value = read_session(root, required=False)
    readers = bool(value) and any(worker["state"] == "running" for worker in value["workers"].values())
    if not readers and not command_active(root):
        return
    scratch = session_path(root).parent / "scratch"
    if paths and all(scratch in (path if path.is_absolute() else root / path).resolve().parents
                     for path in paths):
        return
    if not readers:
        # Each command holds the environment lock around the command lock, so
        # a command with no environment holder left has finished.
        holder = environment_holder(root)
        if holder is None and not command_active(root):
            return
        wait = ("it has derived its selection and cloned the candidate"
                if (holder or {}).get("command") == PRE_HANDOFF_RUN else "it finishes")
        raise RuntimeError("DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by "
                           + describe_environment_holder(holder or {})
                           + f"; writes to the Item worktree wait until {wait}")
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
    if delivery.delivery_switch_value(docs, delivery_id, LEVEL_CHANGE_SWITCH) == ASSERTION_MAP:
        value["assertion_map"] = assertion_map_check(root, value)
    value["candidate_hash"] = digest(value)
    return value


def require_published_claims(root: Path, current: dict, remote: str = "origin") -> None:
    """At provisional_claims during_plan_revision, refuse to freeze or start a reader on a candidate
    that changes a product path the published Item record does not claim, so no reader starts on
    work that push-item would refuse. The claims come from the Item record of the Integration
    commit the Item converged on, never the worktree's own record, which its writer can edit.
    That commit must be one the Item has taken, as push-item checks it against the remote Item
    ref and Integration. Other candidate reads, such as a regression run, still work on a
    provisional commit."""
    import delivery_git
    docs = delivery.docs_root(root)
    delivery_id, story = current["delivery"], current["story"]
    if delivery.delivery_switch_value(docs, delivery_id, delivery_git.PROVISIONAL_SWITCH) \
            != delivery_git.PROVISIONAL_VALUE:
        return
    item = delivery.find_delivery(docs, delivery_id) / "items" / delivery.id_slug(story) / "item.md"
    refs = delivery_git.canonical_refs(delivery_id, story)
    try:
        tips = delivery_git.remote_ref_oids(root, remote, [refs["item"], refs["integration"]])
        if not tips[refs["item"]] or not tips[refs["integration"]]:
            raise RuntimeError(f"{remote} holds no Item ref of {story} or no Integration of {delivery_id}")
        before = delivery_git.split_remote_note(
            root, delivery_git.require_commit(root, remote, refs["item"], tips[refs["item"]]),
            item.relative_to(root).as_posix(), delivery.split_note)[0]
        integration = delivery_git.require_commit(root, remote, refs["integration"], tips[refs["integration"]])
    except RuntimeError as exc:
        raise RuntimeError(f"DELIVERY_PUBLISHED_CLAIMS_UNREADABLE: the published Item record cannot be read"
                           f" from {remote}: {exc}") from exc
    delivery_git.converged_integration(root, before, {"integration_base_commit": current["integration_base_commit"]},
                                       current["product_commit"], integration)
    try:
        published = delivery_git.split_remote_note(root, current["integration_base_commit"],
                                                   item.relative_to(root).as_posix(), delivery.split_note)[0]
    except RuntimeError as exc:
        raise RuntimeError("the Item record that the Item's integration base publishes cannot be read: "
                           f"{exc}") from exc
    refuse_unpublished_paths(root, delivery_id, story, published.get("path_claims"), current["changed_files"],
                             remote)


def refuse_unpublished_paths(root: Path, delivery_id: str, story: str, path_claims, changed: list[str],
                             remote: str = "origin") -> None:
    """Refuse changed product paths that *path_claims* does not cover, naming a provisional claim's
    paths as pending or orphaned."""
    import delivery_git
    outside = delivery_git.paths_outside_claims(
        {path for path in changed if not path.startswith("workspace/docs/")}, path_claims)
    if outside:
        raise RuntimeError(delivery_git.provisional_path_refusal(root, remote, delivery_id, story, outside)
                           or "DELIVERY_PATH_CLAIM_EXCEEDED: the Item's product change lies outside its path"
                              " claims: " + ", ".join(outside))


def freeze(root: Path, delivery_id: str, story: str, *, fresh: bool = False, remote: str = "origin") -> dict:
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
        if current.get("assertion_map", {}).get("problems"):
            raise RuntimeError("assertion map: " + "; ".join(current["assertion_map"]["problems"]))
        require_published_claims(root, current, remote)
        # At touched_suites the readers start only on a candidate whose touched
        # earlier suites and own Test Plan targets passed before the freeze.
        pre_handoff = None
        if pre_handoff_regression(root, delivery_id) == TOUCHED_SUITES:
            # The latest run decides, and a run holds the environment lock until it is recorded.
            holder = environment_holder(root)
            if holder is not None:
                raise environment_busy(holder)
            pre_handoff = accepted_pre_handoff(root, delivery_id, story, current)
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
        if pre_handoff is not None:
            value["pre_handoff"] = pre_handoff
            value["pre_handoff_history"] = pre_handoff_history(root, previous, delivery_id, story)
            value["metrics"]["pre_handoff_seconds"] = pre_handoff["duration_seconds"]
        history = panel_history(previous)
        if history:
            value["panel_history"] = history
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
    for name in SELECTION_VARIABLES:
        environment.pop(name, None)
    if diagnostic:
        environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(session_path(root).parent / "diagnostic-tests.json")
    environment["AGENTROF_VERIFICATION_SCRATCH"] = str(session_path(root).parent / "scratch")
    return environment


def verification_contract(root: Path) -> dict:
    return delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")[0]


def contract_variables(contract: dict) -> list[str]:
    """The environment variables the approved Verification Contract declares for its commands.

    The contract check refuses the same lists, so a hand-edited contract
    refuses here before any command runs.
    """
    names = contract.get("command_variables", [])
    problem = operation_compile.command_variable_problem(names)
    if problem:
        raise RuntimeError(problem)
    return names


def variable_value(environment: dict, name: str) -> str | None:
    """A variable's value, or None while it is unset; Windows matches its names without case."""
    if name in environment:
        return environment[name]
    if os.name == "nt":
        return next((value for key, value in environment.items() if key.upper() == name.upper()), None)
    return None


def identity_key(root: Path) -> bytes:
    """The random key of the Item's verification runtime that keys every run identity's environment hash.

    The key never leaves the runtime, where every identity is compared, so a
    hash copied into tracked evidence checks no guess of a value. The caller
    holds the session lock, so one key is created once. A runtime that lost its
    key starts a new one, and no identity hashed with the old key matches a new
    one, so nothing recorded before is reused.
    """
    path = safe_runtime_path(root, session_path(root).parent / IDENTITY_KEY, file_only=True)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = b""
    if len(key) != IDENTITY_KEY_BYTES:
        key = secrets.token_bytes(IDENTITY_KEY_BYTES)
        atomic_file.replace_bytes(path, key, mode=0o600)
    return key


def environment_identity(root: Path, environment: dict, contract: dict | None = None) -> dict:
    """The identity of a command's environment: the declared variables it covers and the host.

    It names every variable it covers, an unset one too, and keeps only a hash
    of their values keyed with the runtime's identity key, so no value is
    written and no guess of one can be checked against the hash. The caller
    holds the session lock.
    """
    names = set(COMMAND_VARIABLES) | set(contract_variables(verification_contract(root) if contract is None
                                                             else contract))
    names |= {name for name in environment if name.startswith(COMMAND_VARIABLE_PREFIXES)}
    values = {name: variable_value(environment, name) for name in sorted(names - set(SELECTION_VARIABLES))}
    return {"environment_variables": sorted(values), "environment_hash": keyed_digest(identity_key(root), values),
            "python": sys.version, "platform": platform.platform(), "git": git(root, "--version")}


def environment_problem(identity: object, contract: dict) -> str | None:
    """Why a recorded environment identity is incomplete or inconsistent with the approved contract, or None.

    It is checked as recorded, never against the environment of the process that checks it.
    """
    if not isinstance(identity, dict):
        return "records no environment identity"
    names = identity.get("environment_variables")
    if names is None and "environment_hash" in identity:
        return LEGACY_ENVIRONMENT
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or names != sorted(set(names))):
        return "must name each variable it covers once, in order"
    declared = {*COMMAND_VARIABLES, *contract_variables(contract), *RUNNER_VARIABLES}
    missing = sorted(declared - set(names))
    if missing:
        return "does not cover " + ", ".join(missing)
    stray = [name for name in names if name not in declared
             and (name in SELECTION_VARIABLES or not name.startswith(COMMAND_VARIABLE_PREFIXES))]
    if stray:
        return "covers " + ", ".join(stray) + ", which no declaration names"
    if not isinstance(identity.get("environment_hash"), str) \
            or not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", identity["environment_hash"]):
        return "records no environment hash"
    host = [key for key in ("python", "platform", "git")
            if not isinstance(identity.get(key), str) or not identity[key].strip()]
    if host:
        return "records no " + ", ".join(host)
    return None


def literal_test_id(identifier) -> bool:
    """Whether a test id passes as selection data: nonempty, unpadded, no option prefix or control character."""
    return (isinstance(identifier, str) and bool(identifier) and identifier == identifier.strip()
            and not identifier.startswith("-")
            and not any(ord(character) < 32 or ord(character) == 127 for character in identifier))


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
        if (not isinstance(identifiers, list) or any(not literal_test_id(identifier) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise RuntimeError("diagnostic test IDs must be unique nonempty literal identifiers, without option prefixes or control characters")
        value[name] = sorted(identifiers)
    value["selected_test_ids"] = sorted(set(value["failed_test_ids"]) | set(value["affected_test_ids"]))
    if not value["selected_test_ids"]:
        raise RuntimeError("diagnostic selection must include at least one failed or affected test ID")
    return value


def clone_private_checkout(root: Path, execution_root: Path, commit: str) -> None:
    """Check *commit* out in a new clone of *root* with its own objects, index and files.

    Git for Windows creates no file whose absolute path reaches MAX_PATH, 260
    characters, unless core.longpaths is set, yet its checkout exits 0. A
    clone deep in the verification scratch would then lack a tracked file the
    project holds and never count as intact, so the clone sets core.longpaths
    in its own config. Git on other hosts ignores the key.
    """
    git(root, "clone", "--config", "core.longpaths=true", "--no-local", "--no-hardlinks", "--no-checkout",
        "--", str(root), str(execution_root))
    git(execution_root, "checkout", "--detach", commit)


# How many paths a checkout difference names before it counts the rest.
CHECKOUT_DIFFERENCE_LIMIT = 5


def checkout_difference(execution_root: Path) -> str:
    """Name the tracked paths where a private checkout differs from its HEAD, or return "" if none does.

    A path the checkout lacks is named missing, any other changed: Git can
    leave a tracked file out of a checkout that exits 0, and the clone then
    differs from its commit before any command ran. At most
    CHECKOUT_DIFFERENCE_LIMIT paths are named, then how many more differ.
    """
    fields = git(execution_root, "diff", "--name-status", "--no-renames", "-z", "HEAD").split("\0")
    named = [("missing " if status == "D" else "changed ") + path for status, path in zip(fields[::2], fields[1::2])]
    more = len(named) - CHECKOUT_DIFFERENCE_LIMIT
    return ", ".join(named[:CHECKOUT_DIFFERENCE_LIMIT]) + (f" and {more} more" if more > 0 else "")


def private_checkout_run(root: Path, scratch: Path, commit: str, workdir: str, command: str, environment: dict,
                         *, isolate_search_paths: bool = False,
                         cloned=None) -> tuple[subprocess.CompletedProcess, bool, str, dict, dict]:
    """Run an approved command verbatim in a private clone of one exact commit.

    Returns the completed command, whether the clone still holds that commit
    unchanged, the checkout difference that names where it does not, with
    *isolate_search_paths* the interpreter search path entries dropped because
    they resolve outside the clone, and the environment the command ran in.
    *cloned*, when given, is called once the clone holds the commit, before
    the command runs.
    """
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=scratch) as temporary:
        execution_root = Path(temporary) / "checkout"
        # A private repository gives mutation tools their own index, objects and
        # files; no transient mutant can enter the independent reviewer's view.
        clone_private_checkout(root, execution_root, commit)
        execution_directory = (execution_root / workdir).resolve()
        if execution_directory != execution_root.resolve() and execution_root.resolve() not in execution_directory.parents:
            raise RuntimeError("verification workdir escapes its isolated checkout")
        if cloned is not None:
            cloned()
        dropped: dict[str, list[str]] = {}
        if isolate_search_paths:
            environment, dropped = lane_command_environment(execution_root.resolve(), execution_directory, environment)
        completed = subprocess.run(command, cwd=execution_directory, env=environment, shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        from delivery_git import require_visible_item_index
        difference = ""
        try:
            require_visible_item_index(execution_root)
            intact = (git(execution_root, "rev-parse", "HEAD") == commit
                      and not git(execution_root, "diff", "--name-only", "HEAD"))
            difference = "" if intact else checkout_difference(execution_root)
        except RuntimeError:
            intact = False
    return completed, intact, difference, dropped, environment


def run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None,
              spot_file: Path | None = None) -> dict:
    root = root.resolve()
    read_session(root)
    with environment_lock(root, "qa_engineer", "run --kind " + kind), \
            command_lock(root, "qa_engineer", "run --kind " + kind):
        return _run_check(root, kind, fresh=fresh, selection_file=selection_file, spot_file=spot_file)


def _run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None,
               spot_file: Path | None = None) -> dict:
    """Run an approved command verbatim with a file-based mutation scope binding."""
    root = root.resolve()
    if kind not in {"test", "mutation", "dependency_audit", "diagnostic_test"}:
        raise RuntimeError("unsupported verification command kind")
    if (kind == "diagnostic_test") != (selection_file is not None):
        raise RuntimeError("diagnostic_test requires --selection-file; final commands do not accept a focused selection")
    if spot_file is not None and kind != "test":
        raise RuntimeError("only run --kind test takes --spot-run-file")
    if spot_file is not None and fresh:
        raise RuntimeError("run --fresh reuses nothing, so it takes no --spot-run-file")
    if selection_file is not None:
        selection_file = Path(selection_file)
        if not selection_file.is_absolute():
            selection_file = root / selection_file
    with locked(root):
        session = read_session(root)
        current = require_current(root, session, allow_evidence=True)
        if session["workers"]["qa_engineer"]["state"] != "running":
            raise RuntimeError("verification commands require the active QA reader")
        spot = None
        if spot_file is not None:
            value = own_target_reuse(root, current["delivery"])
            if value != SPOT_RUN:
                raise RuntimeError(f"run --spot-run-file serves only process switch {OWN_TARGET_SWITCH} {SPOT_RUN};"
                                   f" {current['delivery']} runs it at {value}")
            spot = spot_run_selection(root, Path(spot_file), current)
        contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
        command = contract.get(kind + "_command")
        if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
            raise RuntimeError("approved verification command is missing or contains unresolved parameters")
        partitions = (partition_declaration(root, current["delivery"], contract, contract_body)
                      if kind == "test" else None)
        if partitions is not None:
            command = partitions["command"]
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
        declared = environment_identity(root, environment, contract)
        reuse, refusal = (pre_handoff_reuse(root, session, current, declared, fresh=fresh, spot=spot)
                          if kind == "test" else (None, None))
        own_note = None
        # Without a spot-run file the run says so even when it reuses nothing else; with one that
        # reuses nothing, its pre_handoff_reuse refusal says why.
        if (kind == "test" and (reuse is None and spot is None or reuse is not None and "own_targets" not in reuse)
                and own_target_reuse(root, current["delivery"]) == SPOT_RUN):
            own_note = ("no --spot-run-file names the own targets QA runs itself, so the run reuses none of the"
                        " Item's own targets" if spot is None else
                        "the spot-run targets, with every own target that is, prefixes or lies under one of them,"
                        " cover every own target, so the run reuses none of them")
        identity = {"candidate_hash": current["candidate_hash"], "kind": kind, "command": command,
                    "workdir": workdir, **declared, "execution_isolation": "private_clone_v1"}
        if selection is not None:
            identity["diagnostic_selection_hash"] = digest(selection)
        if reuse is not None:
            identity["reused_pre_handoff"] = reuse
        groups = (group_report_declaration(root, current["delivery"], contract)
                  if kind in {"test", "diagnostic_test"} else None)
        if groups is not None:
            identity["test_group_report"] = groups
        if partitions is not None:
            identity["test_partitions"] = {key: partitions[key] for key in
                                           ("partitions", "test_engines", "shared_profiles")}
        key = digest(identity)
        old = session["raw_evidence"].get(kind)
        if (not fresh and old and old.get("identity") == identity and old.get("exit_code") == 0 and old.get("candidate_intact") is True
                and fresh_record(old)):
            output = raw_output_path(root, old["output_file"])
            if output.is_file() and not output.is_symlink() and hashlib.sha256(output.read_bytes()).hexdigest() == old["output_sha256"]:
                session["metrics"]["command_cache_hits"] += 1
                write_session(root, session)
                return {**old, "reused": True, **({"own_target_reuse": own_note} if own_note else {})}
        if reuse is not None:
            reused = safe_runtime_path(root, session_path(root).parent / REUSED_TESTS, file_only=True)
            reused_bytes = (json.dumps(
                {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                 "pre_handoff_evidence_hash": reuse["evidence_hash"], "reused_test_ids": reuse["test_ids"]},
                indent=2, sort_keys=True) + "\n").encode("utf-8")
            atomic_file.replace_bytes(reused, reused_bytes)
            reused_generation = source_file_generation(reused)
            environment["AGENTROF_REUSED_TESTS"] = str(reused)
        session["raw_evidence"].pop(kind, None)
        write_session(root, session)
        session_id = session["session_id"]
        if groups is not None and partitions is None:
            clear_group_report(scratch, groups)
    started = time.monotonic()
    partition_records = None
    if partitions is not None:
        partition_records = run_partitions(root, scratch, current, workdir, partitions, environment,
                                           reuse["test_ids"] if reuse is not None else [], groups)
        intact, difference = all(record["candidate_intact"] is True for record in partition_records), ""
        completed = subprocess.CompletedProcess(command, 0 if all(record["passed"] for record in partition_records)
                                                else 1, b"".join(
            f"== partition {record['partition']} on engine {record['engine']}: exit {record['exit_code']}"
            f" ==\n".encode("utf-8") + record["output"] for record in partition_records))
        group_report = None if groups is None else {
            "test_groups": {group: entry for record in partition_records
                            for group, entry in record.get("test_groups", {}).items()},
            "missing_test_groups": sorted(group for record in partition_records
                                          for group in record.get("missing_test_groups", []))}
    else:
        completed, intact, difference, _dropped, _ran = private_checkout_run(
            root, scratch, current["product_commit"], workdir, command, environment)
        group_report = read_group_report(scratch, groups) if groups is not None else None
    if group_report is not None and group_report["missing_test_groups"]:
        intact = False
    selection_intact = None
    if selection is not None:
        try:
            selector = safe_runtime_path(root, Path(environment["AGENTROF_DIAGNOSTIC_TESTS"]), file_only=True)
            selection_intact = (diagnostic_selection(root, selection_file, current) == selection
                                and selector.read_bytes() == selection_bytes
                                and source_file_generation(selection_file) == input_generation
                                and source_file_generation(selector) == selector_generation)
        except (RuntimeError, ValueError, OSError):
            selection_intact = False
    elif reuse is not None:
        # A command that changed the reuse list it received skipped suites the reused run never covered.
        try:
            selection_intact = (safe_runtime_path(root, reused, file_only=True).read_bytes() == reused_bytes
                                and source_file_generation(reused) == reused_generation)
        except (RuntimeError, ValueError, OSError):
            selection_intact = False
    if selection_intact is not None:
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
        if difference:
            record["checkout_difference"] = difference
        if selection is not None:
            record["diagnostic_selection"] = selection
        if selection_intact is not None:
            record["selection_intact"] = selection_intact
        if group_report is not None:
            record.update(group_report)
        if partition_records is not None:
            record["partitions"] = []
            for index, entry in enumerate(partition_records):
                name = f"scratch/{kind}-{key.removeprefix('sha256:')}-partition-{index}.log"
                atomic_file.replace_bytes(raw_output_path(root, name), entry["output"])
                record["partitions"].append({**{field: value for field, value in entry.items()
                                                if field not in {"output", "test_groups", "missing_test_groups"}},
                                             "output_file": name,
                                             "output_sha256": hashlib.sha256(entry["output"]).hexdigest()})
        record["evidence_hash"] = digest(record)
        session["raw_evidence"][kind] = record
        session["metrics"]["command_seconds"] += record["duration_seconds"]
        write_session(root, session)
        return {**record, "reused": False, **({"pre_handoff_reuse": refusal} if refusal else {}),
                **({"own_target_reuse": own_note} if own_note else {})}


def own_target_reuse(root: Path, delivery_id: str) -> str:
    """The own_target_reuse value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, OWN_TARGET_SWITCH)


def group_report_declaration(root: Path, delivery_id: str, contract: dict) -> dict | None:
    """The test groups and the group report path a test run checks, or None.

    Only a Verification Contract that declares test_groups and
    test_group_report under a Delivery that runs test_group_report at
    refuse_missing_groups yields them; a contract without the fields never
    reads the Process Policy, so every run without them is as released.
    """
    if "test_groups" not in contract and "test_group_report" not in contract:
        return None
    if delivery.delivery_switch_value(delivery.docs_root(root), delivery_id,
                                      GROUP_REPORT_SWITCH) != REFUSE_MISSING_GROUPS:
        return None
    problems = operation_compile.test_group_problems(contract)
    if problems:
        raise RuntimeError("; ".join(problems))
    return {"test_groups": list(contract["test_groups"]), "test_group_report": contract["test_group_report"]}


def group_report_path(scratch: Path, declaration: dict) -> Path:
    path = scratch / declaration["test_group_report"]
    if scratch.resolve() not in path.resolve().parents:
        raise RuntimeError("test_group_report escapes the verification scratch")
    return path


def clear_group_report(scratch: Path, declaration: dict) -> None:
    """Remove an earlier run's group report, so only the command about to run can write one."""
    path = group_report_path(scratch, declaration)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise RuntimeError(f"test_group_report {declaration['test_group_report']} is no regular file")


def read_group_report(scratch: Path, declaration: dict) -> dict:
    """Each declared group's status and case counts, as the command's group report names them.

    A group the report lacks, or names without a declared status and
    non-negative case counts, is missing; so is every group when the report is
    absent or no report of the declared shape.
    """
    path = group_report_path(scratch, declaration)
    groups, problem = {}, None
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("the command wrote no group report")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"schema_version", "groups"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["groups"], dict)):
            raise ValueError("the group report holds no schema_version 1 and groups")
        groups = value["groups"]
    except (OSError, ValueError) as exc:
        problem = str(exc)
    statuses = {}
    for group in declaration["test_groups"]:
        entry = groups.get(group)
        if (isinstance(entry, dict) and set(entry) == {"status", *GROUP_COUNTS}
                and entry["status"] in GROUP_STATUSES
                and all(type(entry[count]) is int and entry[count] >= 0 for count in GROUP_COUNTS)):
            statuses[group] = {key: entry[key] for key in ("status", *GROUP_COUNTS)}
        else:
            statuses[group] = {"status": MISSING_GROUP}
    report = {"test_groups": statuses,
              "missing_test_groups": [group for group, entry in statuses.items() if entry["status"] == MISSING_GROUP]}
    if problem:
        report["test_group_report_problem"] = problem
    return report


def group_report_problem(raw: dict, declaration: dict) -> str | None:
    """Why a final test run's recorded group report does not show every declared group passed, or None."""
    if raw.get("identity", {}).get("test_group_report") != declaration:
        return "does not check the test groups and group report the approved Verification Contract declares"
    recorded = raw.get("test_groups")
    if not isinstance(recorded, dict) or set(recorded) != set(declaration["test_groups"]):
        return "does not record every declared test group"
    unpassed = {group: entry.get("status") if isinstance(entry, dict) else None
                for group, entry in sorted(recorded.items())
                if not isinstance(entry, dict) or entry.get("status") != "passed"}
    if unpassed:
        return "records test groups that did not pass: " + ", ".join(
            f"{group} {status}" for group, status in unpassed.items())
    return None


def partition_declaration(root: Path, delivery_id: str, contract: dict, body: str) -> dict | None:
    """The partition plan, command and engines QA's final test run runs under, or None.

    Only a Verification Contract that declares test_partition_command under a
    Delivery that runs test_engines at partitioned yields them; a contract
    without it never reads the Process Policy. The plan must place every
    declared group in exactly one partition, and the Environment Contract must
    provision every engine.
    """
    if "test_partition_command" not in contract:
        return None
    docs = delivery.docs_root(root)
    if delivery.delivery_switch_value(docs, delivery_id, ENGINE_SWITCH) != PARTITIONED:
        return None
    plan, problems = operation_compile.test_partition_plan(contract, body)
    if problems or plan is None:
        raise RuntimeError("; ".join(problems) or "the Verification Contract declares no test partition plan")
    path = docs / "operation/environment-contract.md"
    provisioned = delivery.split_note(path)[0].get("test_engines") if path.is_file() else None
    missing = [engine for engine in contract["test_engines"]
               if not isinstance(provisioned, list) or engine not in provisioned]
    if missing:
        raise RuntimeError("the Environment Contract provisions no test engine " + ", ".join(missing))
    command = contract["test_partition_command"]
    if "{{" in command or "}}" in command:
        raise RuntimeError("approved test partition command contains unresolved parameters")
    return {"command": command, "partitions": plan, "test_engines": list(contract["test_engines"]),
            "shared_profiles": sorted(contract.get("shared_profiles", []))}


def partition_durations_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / PARTITION_DURATIONS, file_only=True)


def partition_durations(root: Path) -> dict:
    try:
        value = json.loads(partition_durations_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: seconds for name, seconds in value.items()
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)} if isinstance(value, dict) else {}


def partition_order(root: Path, plan: list[dict]) -> list[dict]:
    """The partitions longest first by their last recorded duration; one never recorded comes first."""
    durations = partition_durations(root)
    return sorted(plan, key=lambda entry: -durations.get(entry["partition"], float("inf")))


def run_partitions(root: Path, scratch: Path, current: dict, workdir: str, partitions: dict, environment: dict,
                   reused: list[str], groups: dict | None) -> list[dict]:
    """Run every partition in its own private clone, in parallel over the declared test engines.

    A partition of an exclusive profile takes an engine alone; partitions of a
    shared profile may share one with each other. Never more partitions run at
    once than there are engines. A partition that fails, cannot start or
    misses a group never stops another. Each record names its partition,
    engine, exit code, intactness, duration and output, in plan order.
    """
    import threading

    engines, shared = partitions["test_engines"], set(partitions["shared_profiles"])
    load = {engine: {"exclusive": False, "count": 0} for engine in engines}
    pending, results, condition = partition_order(root, partitions["partitions"]), {}, threading.Condition()
    directory = safe_runtime_path(root, session_path(root).parent / "partitions")
    directory.mkdir(exist_ok=True)

    def engine_for(entry: dict) -> str | None:
        if sum(state["count"] for state in load.values()) >= len(engines):
            return None
        if entry["profile"] in shared:
            free = [engine for engine in engines if not load[engine]["exclusive"]]
            return min(free, key=lambda engine: load[engine]["count"]) if free else None
        return next((engine for engine in engines if load[engine]["count"] == 0), None)

    def run(entry: dict, engine: str, index: int) -> None:
        record = {"partition": entry["partition"], "engine": engine, "groups": entry["groups"],
                  "profile": entry["profile"]}
        started = time.monotonic()
        try:
            own = safe_runtime_path(root, scratch / "partitions" / entry["partition"])
            own.mkdir(parents=True, exist_ok=True)
            selection = safe_runtime_path(root, directory / f"{index}.json", file_only=True)
            data = (json.dumps({"schema_version": 1, "candidate_hash": current["candidate_hash"],
                                "partition": entry["partition"], "groups": entry["groups"], "engine": engine,
                                "reused_test_ids": reused}, indent=2, sort_keys=True) + "\n").encode("utf-8")
            atomic_file.replace_bytes(selection, data)
            generation = source_file_generation(selection)
            own_groups = (None if groups is None else
                          {**groups, "test_groups": [group for group in groups["test_groups"]
                                                     if group in entry["groups"]]})
            if own_groups is not None:
                clear_group_report(own, own_groups)
            completed, intact, difference, _dropped, _ran = private_checkout_run(
                root, own, current["product_commit"], workdir, partitions["command"],
                {**environment, "AGENTROF_TEST_PARTITION": str(selection), "AGENTROF_TEST_ENGINE": engine,
                 "AGENTROF_VERIFICATION_SCRATCH": str(own)})
            try:
                intact = intact and selection.read_bytes() == data and source_file_generation(selection) == generation
            except OSError:
                intact = False
            record.update(exit_code=completed.returncode, output=completed.stdout)
            if difference:
                record["checkout_difference"] = difference
            if own_groups is not None:
                report = read_group_report(own, own_groups)
                record.update(report)
                intact = intact and not report["missing_test_groups"]
            record["candidate_intact"] = intact
        except Exception as exc:  # noqa: BLE001 - any failure is this partition's result, never another's
            record.update(exit_code=None, candidate_intact=False, output=f"{exc}\n".encode("utf-8"),
                          error=f"the partition did not start or finish: {exc}")
        record["duration_seconds"] = time.monotonic() - started
        record["passed"] = (record["exit_code"] == 0 and record["candidate_intact"] is True
                            and all(group.get("status") == "passed"
                                    for group in record.get("test_groups", {}).values()))
        with condition:
            results[entry["partition"]] = record
            load[engine]["count"] -= 1
            load[engine]["exclusive"] = False
            condition.notify_all()

    threads = []
    with condition:
        while pending:
            chosen = next(((entry, engine) for entry in pending
                           for engine in [engine_for(entry)] if engine is not None), None)
            if chosen is None:
                condition.wait()
                continue
            entry, engine = chosen
            pending.remove(entry)
            load[engine]["count"] += 1
            load[engine]["exclusive"] = entry["profile"] not in shared
            thread = threading.Thread(target=run, args=(entry, engine, partitions["partitions"].index(entry)),
                                      daemon=True)
            threads.append(thread)
            thread.start()
    for thread in threads:
        thread.join()
    durations = partition_durations(root)
    durations.update({partition: record["duration_seconds"] for partition, record in results.items()})
    atomic_file.replace_text(partition_durations_path(root), json.dumps(durations, indent=2, sort_keys=True) + "\n")
    return [results[entry["partition"]] for entry in partitions["partitions"]]


def partition_problem(root: Path, raw: dict, partitions: dict) -> str | None:
    """Why a final test run's record is no complete, passing run of the declared partition plan, or None."""
    if raw.get("identity", {}).get("test_partitions") != {key: partitions[key] for key in (
            "partitions", "test_engines", "shared_profiles")}:
        return "does not run the test partition plan the approved Verification Contract declares"
    records = raw.get("partitions")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        return "records no partition results"
    names = [record.get("partition") for record in records]
    twice = sorted({str(name) for name in names if names.count(name) > 1})
    if twice:
        return "holds partition " + ", ".join(twice) + " more than once"
    declared = [entry["partition"] for entry in partitions["partitions"]]
    missing = [name for name in declared if name not in names]
    if missing:
        return "lacks partition " + ", ".join(missing)
    stray = sorted(str(name) for name in names if name not in declared)
    if stray:
        return "holds partition " + ", ".join(stray) + ", which the plan does not declare"
    failed = [record["partition"] for record in records
              if record.get("passed") is not True or record.get("exit_code") != 0
              or record.get("candidate_intact") is not True]
    if failed:
        return "holds failed or not intact partition " + ", ".join(failed)
    for record in records:
        try:
            output = raw_output_path(root, str(record.get("output_file")))
        except RuntimeError:
            return f"names no raw output of partition {record['partition']}"
        if (not output.is_file() or output.is_symlink()
                or hashlib.sha256(output.read_bytes()).hexdigest() != record.get("output_sha256")):
            return f"partition {record['partition']} raw command output is missing or changed"
    return None


def own_plan_targets(root: Path, current: dict) -> list[str]:
    """The automation targets of the Item's own Test Plan, in plan order."""
    plan = str(item_record(root, current["delivery"], current["story"]).get("test_plan_path") or "")
    return plan_automation_targets(delivery.docs_root(root), plan, f"{current['story']} of {current['delivery']}")


def spot_run_selection(root: Path, path: Path, current: dict) -> list[str]:
    """The own Test Plan targets QA's final test run runs itself at own_target_reuse spot_run, read as data.

    The file lies in the verification scratch, binds the frozen candidate and
    names at least one automation target of the Item's own Test Plan, each
    once; no command receives it, so its ids are bound by value in the run's
    identity.
    """
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(session_path(root).parent).as_posix()
    except ValueError as exc:
        raise RuntimeError("spot-run selection must be a regular file inside verification scratch") from exc
    if not path.is_file():
        raise RuntimeError("spot-run selection must be a regular file inside verification scratch")
    value = json.loads(raw_output_path(root, relative).read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "candidate_hash", "spot_test_ids"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("candidate_hash") != current["candidate_hash"]):
        raise RuntimeError("spot-run selection must bind this candidate and declare only schema_version,"
                           " candidate_hash and spot_test_ids")
    identifiers = value["spot_test_ids"]
    if (not isinstance(identifiers, list) or not identifiers
            or any(not literal_test_id(identifier) for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise RuntimeError("spot_test_ids must name at least one unique literal test id, without option prefixes"
                           " or control characters")
    stray = sorted(set(identifiers) - set(own_plan_targets(root, current)))
    if stray:
        raise RuntimeError("spot_test_ids must be automation targets of the Item's own Test Plan; "
                           + ", ".join(stray) + (" is" if len(stray) == 1 else " are") + " not")
    return sorted(identifiers)


def kept_own_targets(own: list[str], spot: list[str]) -> list[str]:
    """The own targets the approved test command must run when QA spot-runs *spot*.

    They are the spot-run targets and every own target that is, prefixes or
    lies under one of them or under such a target in turn: a command that skips
    a reused id by node id prefix would otherwise skip part of a target it
    must run.
    """
    kept = set(spot)
    grown = True
    while grown:
        grown = False
        for target in own:
            if target not in kept and overlaps(target, sorted(kept)):
                kept.add(target)
                grown = True
    return sorted(kept)


def pre_handoff_reuse(root: Path, session: dict, current: dict, environment: dict,
                      *, fresh: bool = False, spot: list[str] | None = None) -> tuple[dict | None, str | None]:
    """The targets QA's final test run takes from the accepted pre-handoff run, or why it takes none.

    At pre_handoff_regression touched_suites the run the freeze accepted covers
    the targets of the earlier stories when it passed intact on the frozen
    tree, with the selection and the approved command derived for the frozen
    candidate, in the declared environment of QA's run, and is as fresh as
    final evidence must be. The Item's own Test Plan targets stay QA's to run,
    with every earlier target that overlaps one, and a *fresh* run reuses
    nothing. With *spot*, the own targets QA names at own_target_reuse
    spot_run, the run also covers every other own target, and only those
    *spot* keeps with kept_own_targets stay QA's, with every target that
    overlaps one. At any other value it returns (None, None), or with *spot*
    why it reuses nothing.
    """
    value = pre_handoff_regression(root, current["delivery"])
    if value != TOUCHED_SUITES:
        return None, (f"own targets are reused only from an accepted pre-handoff run, which"
                      f" {PRE_HANDOFF_SWITCH} {value} never runs" if spot is not None else None)
    if fresh:
        return None, "run --fresh runs every suite itself"
    receipt = session.get("pre_handoff")
    if not isinstance(receipt, dict):
        return None, "the frozen session holds no accepted pre-handoff run"
    if receipt.get("exit_code") != 0 or receipt.get("candidate_intact") is not True:
        return None, "the accepted pre-handoff run did not pass intact"
    if not fresh_record(receipt):
        return None, "the accepted pre-handoff run expired"
    try:
        derived = derive_regression(root, current["delivery"], current["story"], current)
    except RuntimeError as exc:
        return None, f"the frozen candidate's selection cannot be derived again: {exc}"
    for label, keys in (("candidate tree", ("candidate_tree",)), ("selection", ("affected_test_ids",)),
                        ("approved command", ("kind", "command", "workdir"))):
        if any(receipt.get(key) != derived[key] for key in keys):
            return None, f"the pre-handoff run's {label} differs from the frozen candidate's"
    if {key: receipt.get(key) for key in ENVIRONMENT_FIELDS} != environment:
        return None, "the pre-handoff run's declared environment differs from this run's"
    own = derived["own_targets"]
    kept = own if spot is None else kept_own_targets(own, spot)
    stories = [{"delivery": entry["delivery"], "story": entry["story"], "test_ids": test_ids}
               for entry in derived["earlier_stories"]
               for test_ids in [sorted(test for test in set(entry["automation_targets"]) if not overlaps(test, kept))]
               if test_ids]
    reused_own = sorted(set(own) - set(kept))
    test_ids = sorted({test for entry in stories for test in entry["test_ids"]} | set(reused_own))
    if not test_ids:
        return None, ("the pre-handoff run covered no earlier story's target beyond the Item's own" if spot is None
                      else "the pre-handoff run covered no target beyond the own targets QA's run keeps")
    reuse = {"evidence_hash": receipt["evidence_hash"], "earlier_stories": stories, "test_ids": test_ids}
    if reused_own:
        reuse["own_targets"] = {"test_ids": reused_own, "spot_test_ids": sorted(spot)}
    return reuse, None


def overlaps(identifier: str, targets: list[str]) -> bool:
    """Whether a test id is, prefixes or lies under one of *targets*, compared as text.

    A command that skips the reused ids by node id prefix, as pytest's
    --deselect does with no boundary, skips every target the id prefixes, so
    an id that overlaps an own target the command must run is never reused.
    """
    return any(target.startswith(identifier) or identifier.startswith(target) for target in targets)


def reuse_problem(root: Path, session: dict, identity: dict, contract: dict) -> str | None:
    """Why the pre-handoff reuse a final test run records does not bind its frozen session, or None.

    It is checked as recorded: the reused run is the frozen session's accepted
    run, passed intact on the frozen tree, as fresh as final evidence must be
    now, of the approved command and in the declared environment of the final
    run, and the reused test ids are earlier story targets that run selected,
    none that is, prefixes or lies under one of the Item's own. A reuse of own
    targets is valid only at own_target_reuse spot_run, with its spot-run
    targets and its reused own targets disjoint automation targets of the
    Item's own Test Plan, and no reused id overlaps a target kept_own_targets
    keeps for the command instead.
    """
    reuse, receipt = identity["reused_pre_handoff"], session.get("pre_handoff")
    if (not isinstance(reuse, dict) or not isinstance(receipt, dict)
            or reuse.get("evidence_hash") != receipt.get("evidence_hash")):
        return "reuses a pre-handoff run the frozen session does not hold"
    stories, test_ids = reuse.get("earlier_stories"), reuse.get("test_ids")
    own_reuse = reuse.get("own_targets")
    if own_reuse is not None and (
            not isinstance(own_reuse, dict) or set(own_reuse) != {"test_ids", "spot_test_ids"}
            or any(not isinstance(own_reuse[key], list) or not own_reuse[key]
                   or any(not isinstance(test, str) for test in own_reuse[key])
                   or own_reuse[key] != sorted(set(own_reuse[key])) for key in own_reuse)):
        return "names its reused own targets apart from its spot-run targets"
    reused_own = own_reuse["test_ids"] if own_reuse is not None else []
    if (not isinstance(stories, list) or (not stories and own_reuse is None)
            or not isinstance(test_ids, list) or not test_ids
            or any(not isinstance(entry, dict) or not isinstance(entry.get("test_ids"), list) or not entry["test_ids"]
                   for entry in stories)
            or test_ids != sorted({test for entry in stories for test in entry["test_ids"]} | set(reused_own))):
        return "names its reused test ids apart from their earlier stories"
    current = session["candidate"]
    if (receipt.get("exit_code") != 0 or receipt.get("candidate_intact") is not True
            or receipt.get("candidate_tree") != git(root, "rev-parse", current["product_commit"] + "^{tree}")):
        return "reuses a pre-handoff run that did not pass intact on the frozen tree"
    if not fresh_record(receipt):
        return "reuses a pre-handoff run that expired"
    kind = str(receipt.get("kind"))
    if (kind not in {"test", "diagnostic_test"} or receipt.get("command") != contract.get(kind + "_command")
            or receipt.get("workdir") != str(contract.get(kind + "_workdir", "."))):
        return "reuses a pre-handoff run of another command than the approved one"
    if not set(test_ids) <= set(receipt.get("affected_test_ids") or []):
        return "reuses test ids the pre-handoff run did not select"
    own = own_plan_targets(root, current)
    if own_reuse is not None:
        value = own_target_reuse(root, current["delivery"])
        if value != SPOT_RUN:
            return (f"reuses the Item's own Test Plan targets, which only process switch {OWN_TARGET_SWITCH}"
                    f" {SPOT_RUN} allows; {current['delivery']} runs it at {value}")
        spot = own_reuse["spot_test_ids"]
        if not set(spot) | set(reused_own) <= set(own) or set(spot) & set(reused_own):
            return ("names spot-run targets or reused own targets that are no automation targets of the Item's own"
                    " Test Plan, or one target as both")
        if any(overlaps(test, kept_own_targets(own, spot)) for test in test_ids):
            return ("reuses a test id that is, prefixes or lies under one of the Item's own Test Plan targets that"
                    " QA's final test run spot-runs or keeps with them")
    elif any(overlaps(test, own) for test in test_ids):
        return ("reuses a test id that is, prefixes or lies under one of the Item's own Test Plan targets,"
                " which QA runs itself")
    if {key: receipt.get(key) for key in ENVIRONMENT_FIELDS} != {key: identity[key] for key in ENVIRONMENT_FIELDS}:
        return "reuses a pre-handoff run of another declared environment"
    return None


def fresh_record(record: dict) -> bool:
    """Whether a recorded command completed no longer ago than raw evidence stays fresh."""
    completed = record.get("completed_at", 0)
    return (isinstance(completed, (int, float))
            and 0 <= time.time() - completed <= policy()["raw_evidence_max_age_seconds"])


def require_raw_evidence(root: Path, session: dict, checks: dict) -> dict | None:
    """Check QA's final command evidence as recorded and return the environment it shares.

    Each record must be complete, run the approved command and stay fresh, and
    every final record of the session shares the environment identity of its
    full_test_suite evidence, the first check QA runs. No record is compared
    with the environment of the process that checks it, so evidence recorded
    in a reader's shell is approved from any other.
    """
    kinds = {"full_test_suite": "test", "mutation_whole_changed_files": "mutation", "dependency_audit": "dependency_audit"}
    contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    shared: tuple[str, dict] | None = None
    for check in required_checks(root, session["candidate"], "qa_engineer"):
        if check not in kinds:
            continue
        raw = session["raw_evidence"].get(kinds[check], {})
        if (raw.get("exit_code") != 0 or raw.get("candidate_intact") is not True or checks[check].get("raw_evidence_hash") != raw.get("evidence_hash")
                or raw.get("evidence_hash") != digest({key: item for key, item in raw.items() if key != "evidence_hash"})
                or raw.get("identity", {}).get("candidate_hash") != session["candidate"]["candidate_hash"]):
            raise RuntimeError(f"{check} requires successful same-candidate command evidence from run")
        identity = raw["identity"]
        problem = environment_problem(identity, contract)
        if problem:
            raise RuntimeError(f"{check} evidence {problem}")
        partitions = (partition_declaration(root, session["candidate"]["delivery"], contract, contract_body)
                      if check == "full_test_suite" else None)
        approved = partitions["command"] if partitions is not None else contract.get(kinds[check] + "_command")
        if (identity.get("kind") != kinds[check] or identity.get("command") != approved
                or identity.get("workdir") != contract.get(kinds[check] + "_workdir", ".")
                or identity.get("execution_isolation") != "private_clone_v1"):
            raise RuntimeError(f"{check} evidence does not run the approved command in its approved workdir")
        if not fresh_record(raw):
            raise RuntimeError(f"{check} evidence expired")
        groups = (group_report_declaration(root, session["candidate"]["delivery"], contract)
                  if check == "full_test_suite" else None)
        if groups is not None:
            problem = group_report_problem(raw, groups)
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        if partitions is not None:
            problem = partition_problem(root, raw, partitions)
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        if "reused_pre_handoff" in identity:
            problem = reuse_problem(root, session, identity, contract) if check == "full_test_suite" \
                else "reuses a pre-handoff run, which only the full test suite does"
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        environment = {key: identity[key] for key in ENVIRONMENT_FIELDS}
        if shared is None:
            shared = (check, environment)
        elif environment != shared[1]:
            raise RuntimeError(f"{check} evidence ran in another environment than {shared[0]} evidence")
        if check == "full_test_suite" and checks[check].get("environment") != identity["environment_hash"]:
            raise RuntimeError("full suite environment must bind the recorded execution environment")
        output = raw_output_path(root, raw["output_file"])
        if (not output.is_file() or output.is_symlink()
                or hashlib.sha256(output.read_bytes()).hexdigest() != raw.get("output_sha256")):
            raise RuntimeError(f"{check} raw command output is missing or changed")
    return shared[1] if shared else None


def review_loop(root: Path, delivery_id: str) -> str:
    """The review_loop value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, delivery.REVIEW_LOOP)


def code_review_panel(root: Path, delivery_id: str) -> str:
    """The code_review_panel value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, CODE_REVIEW_PANEL)


def panel_assignments() -> list[dict]:
    """The code review panel's lens assignments, each with its key, its lens ids and their focus."""
    step = json.loads(PANEL_DATA_PATH.read_text(encoding="utf-8"))["review_steps"][PANEL_STEP]
    focus = {lens["id"]: lens["focus"] for lens in step["lenses"]}
    return [{"key": assignment[0], "lens": list(assignment), "focus": {lens: focus[lens] for lens in assignment}}
            for assignment in step["default_panel"]]


def panel_prefix(panel: dict, assignment: dict) -> str:
    """The id prefix of a lens assignment's findings in one panel pass."""
    return f"P{panel['pass']}-{assignment['key']}-"


def code_review_panel_state(root: Path, value: dict) -> dict | None:
    """The session's code review panel at code_review_panel beside_official, else None.

    A session that registered no panel result yet gets the panel it would
    start: its pass follows every earlier pass and every panel id the session
    carries, so a lens finding id never repeats an earlier one. The Process
    Policy is part of the frozen candidate, so the value holds for the session.
    """
    if code_review_panel(root, value["candidate"]["delivery"]) != BESIDE_OFFICIAL:
        return None
    if isinstance(value.get("panel"), dict):
        return value["panel"]
    passes = [record.get("pass", 0) for record in value.get("panel_history", [])]
    passes += [int(match.group(1)) for finding in value.get("unresolved_findings", [])
               for match in [PANEL_PASS_RE.match(str(finding.get("id", "")))] if match]
    return {"pass": max(passes, default=0) + 1, "assignments": panel_assignments(), "members": {}}


def panel_history(session: dict | None) -> list[dict]:
    """The panel records of every code review panel pass of the Item that settled so far."""
    if not session:
        return []
    history = list(session.get("panel_history", []))
    worker = session["workers"]["code_reviewer"]
    record = worker.get("result", {}).get("panel")
    if worker["state"] == "settled" and isinstance(record, dict):
        history.append(record)
    return history


def complete_members(panel: dict) -> dict:
    """The panel's registered results, once the official result and every lens result are in."""
    members = panel["members"]
    missing = [key for key in [OFFICIAL, *(assignment["key"] for assignment in panel["assignments"])]
               if key not in members]
    if missing:
        raise RuntimeError("register the official result and every lens result with panel-result first;"
                           " missing: " + ", ".join(missing))
    return members


def panel_claims(panel: dict, value: dict, loop: str) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """The claims a code review panel's calibration rules, as returned, and the
    findings each lens claim may be ruled a duplicate of, with their severity.

    Every open critical or major finding of a lens result is a claim and, at
    review_loop blocking_delta, every open critical or major claim of the
    official result that no earlier calibration ruled. A lens claim duplicates
    an open critical or major official finding or another lens claim.
    """
    members = complete_members(panel)
    official = members[OFFICIAL]["result"]["findings"]
    lens_claims = [finding for assignment in panel["assignments"]
                   for finding in members[assignment["key"]]["result"]["findings"]
                   if finding["status"] == "open" and blocking(finding)]
    claims = list(lens_claims)
    if loop == "blocking_delta":
        ruled = {finding["id"] for finding in value.get("unresolved_findings", [])
                 if finding["role"] == "code_reviewer" and "calibrated_severity" in finding}
        claims += open_claims(official, ruled)
    targets = {finding["id"]: finding["severity"] for finding in [*official, *lens_claims]
               if finding["status"] == "open" and blocking(finding)}
    return (sorted(claims, key=lambda claim: claim["id"]),
            {claim["id"]: {identifier: severity for identifier, severity in targets.items()
                           if identifier != claim["id"]} for claim in lens_claims})


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
    A merged panel finding already records the severity it was claimed at.
    """
    rows = {row["finding"]: row for row in result.get("calibration", [])}
    findings = []
    for finding in result.get("findings", []):
        row = rows.get(finding["id"])
        if row is None:
            findings.append(finding)
            continue
        ruling = row["calibrated_severity"].casefold()
        ruled = {**finding, "claimed_severity": finding.get("claimed_severity", finding["severity"]),
                 "calibrated_severity": ruling}
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


def at_least_as_severe(severity: str, claimed: str) -> bool:
    """Whether a blocking severity is the claimed one or above it on the code review scale.

    Only the code review scale orders severities; any other declared severity
    is at least as severe as itself alone.
    """
    severity, claimed = severity.casefold(), claimed.casefold()
    return severity == claimed or (severity in REVIEW_SEVERITY_ORDER and claimed in REVIEW_SEVERITY_ORDER
                                   and REVIEW_SEVERITY_ORDER.index(severity) < REVIEW_SEVERITY_ORDER.index(claimed))


def calibration_problems(root: Path, current: dict, result: dict, ruled: set[str],
                         roles: list[str], duplicates: dict[str, dict[str, str]] | None = None) -> list[str]:
    """One row per open critical or major claim that no earlier calibration ruled.

    *duplicates* maps each lens claim of a code review panel to the findings it
    may be ruled a duplicate of, with their severity; only those claims take
    that ruling, and only toward a finding at least as severe, since no ruling
    lowers a claim to another blocking severity.
    """
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
    by_claim = {row["finding"]: row for row in rows}
    problems = []
    for row in rows:
        claim = claims[row["finding"]]
        label, severity = row["finding"], claim["severity"].casefold()
        claimed, ruling = row.get("claimed_severity"), row.get("calibrated_severity")
        targets = (duplicates or {}).get(label)
        if not isinstance(claimed, str) or claimed.casefold() != severity:
            problems.append(f"{label} calibration must record the claimed severity {claim['severity']}")
        if targets is None and (not isinstance(ruling, str) or ruling.casefold() not in {severity, "minor", "invalid"}):
            problems.append(f"{label} calibrated_severity must confirm {claim['severity']} or be minor or invalid")
        elif targets is not None and (not isinstance(ruling, str)
                                      or ruling.casefold() not in {severity, "minor", "invalid", DUPLICATE}):
            problems.append(f"{label} calibrated_severity must confirm {claim['severity']} or be minor, invalid"
                            " or duplicate")
        if targets is not None and isinstance(ruling, str) and ruling.casefold() == DUPLICATE:
            target = row.get("duplicate_of")
            target_row = by_claim.get(target, {}) if isinstance(target, str) else {}
            chained = str(target_row.get("calibrated_severity", "")).casefold() == DUPLICATE
            if not isinstance(target, str) or target not in targets or chained:
                problems.append(f"{label} duplicate_of must name an open critical or major official finding or"
                                " another lens claim not ruled duplicate")
            elif not at_least_as_severe(targets[target], claim["severity"]):
                problems.append(f"{label} duplicate_of must name a finding at least as severe as the claim,"
                                f" {claim['severity']}; {target} is {targets[target]}")
        elif "duplicate_of" in row:
            problems.append(f"{label} names duplicate_of without a duplicate ruling")
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


def check_calibration(root: Path, value: dict, current: dict, loop: str, panel: dict | None, result: dict) -> None:
    """Refuse a calibration reader's result that register_calibration may not register."""
    if loop != "blocking_delta" and panel is None:
        raise RuntimeError("severity calibration runs only at review_loop blocking_delta or"
                           " code_review_panel beside_official")
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
    duplicates = None
    if panel is not None:
        expected, duplicates = panel_claims(panel, value, loop)
        if sorted(claims, key=lambda claim: claim["id"]) != expected:
            raise RuntimeError("calibration claims must be exactly the open critical or major claims to rule,"
                               " as returned: " + (", ".join(claim["id"] for claim in expected) or "none"))
    item = item_record(root, current["delivery"], current["story"])
    problems = calibration_problems(root, current, {"findings": claims, "calibration": result.get("calibration")},
                                    set(), follow_up_roles(item), duplicates)
    if problems:
        raise RuntimeError("severity calibration is incomplete: " + "; ".join(problems))


def register_calibration(root: Path, result: dict) -> dict:
    """Register a calibration reader's rulings as a result of their own.

    At review_loop blocking_delta a fresh code reviewer, never the reviewer that
    returned the claims, rules each open critical or major claim before the
    claiming result is registered, so the implementation writer stays idle.
    Its rows bind the exact claims it ruled, and they reach the claiming
    result only through this registration: register_result refuses a claiming
    result that carries rows of its own. At code_review_panel beside_official
    it rules, once every panel result is registered, exactly the claims
    panel_claims names, also at review_loop current.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "registering a calibration")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        loop = review_loop(root, current["delivery"])
        panel = code_review_panel_state(root, value)
        check_calibration(root, value, current, loop, panel, result)
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


def check_findings(findings: object) -> None:
    """Every finding has a unique stable id, a declared severity, a verification and a status."""
    if (not isinstance(findings, list) or any(not isinstance(finding, dict)
            or not all(isinstance(finding.get(key), str) and finding[key].strip()
                       for key in ("id", "severity", "verification"))
            or finding.get("status") not in {"open", "resolved"} for finding in findings)
            or len({finding["id"] for finding in findings}) != len(findings)):
        raise RuntimeError("findings require unique stable IDs, severity, verification and open/resolved status")
    allowed_severities = {value.casefold() for value in policy()["blocking_severities"] + policy()["nonblocking_severities"]}
    if any(finding["severity"].casefold() not in allowed_severities for finding in findings):
        raise RuntimeError("finding severity is not declared in the verification policy")


def check_result(root: Path, value: dict, result: dict) -> tuple[dict, dict, dict]:
    """Check a reader's result against its session before its reader settles.

    Returns the candidate the result binds, the role's inherited findings and
    the result's findings by id. settle_result adds the review_loop checks.
    """
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
            contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
            partitions = partition_declaration(root, current["delivery"], contract, contract_body)
            approved = partitions["command"] if partitions is not None else contract.get("test_command")
            if test.get("command") != approved or test.get("exit_code") != 0:
                raise RuntimeError("full suite evidence must identify the approved test command and successful exit")
            if not isinstance(test.get("environment"), str) or not test["environment"].strip():
                raise RuntimeError("full suite evidence requires the verification environment identity")
            environment = require_raw_evidence(root, value, checks)
            if current["runtime_required"]:
                require_runtime_evidence(root, value, checks["fresh_runtime"], environment)
    findings = result.get("findings", [])
    check_findings(findings)
    blocking_severities = {severity.casefold() for severity in policy()["blocking_severities"]}
    if verdict == "passed" and any(finding["severity"].casefold() in blocking_severities
                                   and finding["status"] == "open" for finding in findings):
        raise RuntimeError("passing verification cannot retain an open blocking finding")
    inherited = {finding["id"]: finding for finding in value.get("unresolved_findings", []) if finding["role"] == role}
    dispositions = {finding["id"]: finding for finding in findings}
    if any(finding["severity"].casefold() != inherited[identifier]["severity"].casefold()
           for identifier, finding in dispositions.items() if identifier in inherited):
        raise RuntimeError("inherited finding severity must be preserved")
    if verdict == "passed" and mode != "qa_diagnostic" and (
            not inherited.keys() <= dispositions.keys()
            or any(finding["severity"].casefold() in blocking_severities
                   and dispositions[identifier]["status"] != "resolved"
                   for identifier, finding in inherited.items())):
        raise RuntimeError("final result must explicitly disposition every inherited finding and resolve blocking findings")
    return current, inherited, dispositions


def register_result(root: Path, result: dict) -> dict:
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, str(result.get("role")), "settling its reader")
        return settle_result(root, read_session(root), result)


def settle_result(root: Path, value: dict, result: dict, *, merged: bool = False) -> dict:
    """Check a reader's result and settle its reader with it.

    At code_review_panel beside_official a code review settles only with the
    result merge_panel builds, *merged*, whose calibration merge_panel matched
    to the claims as returned; a confirmed cancellation settles as before.
    """
    current, inherited, dispositions = check_result(root, value, result)
    role, verdict = result["role"], result["verdict"]
    findings = result.get("findings", [])
    if (role == "code_reviewer" and verdict != "cancelled" and not merged
            and code_review_panel(root, current["delivery"]) == BESIDE_OFFICIAL):
        raise RuntimeError("at code_review_panel beside_official the code review settles through merge-panel:"
                           " register the official result and every lens result with panel-result")
    if (role == "code_reviewer" and verdict != "cancelled"
            and review_loop(root, current["delivery"]) == "blocking_delta"):
        item = item_record(root, current["delivery"], current["story"])
        if not merged:
            if "calibration" in result:
                raise RuntimeError("calibration rows come only from the calibration reader's own result,"
                                   " registered with calibrate; the claiming result carries none")
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


def check_panel_result(root: Path, value: dict, current: dict, panel: dict, result: dict) -> str:
    """Refuse a code review panel result that register_panel_result may not register; return its member key."""
    if result.get("role") != "code_reviewer":
        raise RuntimeError("a code review panel result names role code_reviewer")
    if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
        raise RuntimeError("result does not bind this candidate and reader session")
    if value["workers"]["code_reviewer"]["state"] != "running":
        raise RuntimeError("the code review already settled; freeze a new session to replace it")
    mode = result.get("mode")
    if mode != PANEL_LENS_MODE and mode not in policy()["role_modes"]["code_reviewer"]:
        raise RuntimeError("a code review panel result names mode review_initial or review_repair for the"
                           " official reviewer, or panel_lens for a lens reader")
    if result.get("verdict") not in {"passed", "failed"}:
        raise RuntimeError("a code review panel result's verdict is passed or failed")
    members = panel["members"]
    inherited = {finding["id"] for finding in value.get("unresolved_findings", [])
                 if finding["role"] == "code_reviewer"}
    if mode == PANEL_LENS_MODE:
        assignment = next((assignment for assignment in panel["assignments"]
                           if assignment["lens"] == result.get("lens")), None)
        if assignment is None:
            raise RuntimeError("lens must name one assignment of the code review panel: "
                               + "; ".join(", ".join(item["lens"]) for item in panel["assignments"]))
        key = assignment["key"]
        if key in members:
            raise RuntimeError(f"lens assignment {key} is already registered")
        if not isinstance(result.get("report"), str) or not result["report"].strip():
            raise RuntimeError("verification result requires its independent report")
        findings = result.get("findings", [])
        check_findings(findings)
        if result["verdict"] == "passed" and any(blocking(finding) for finding in findings):
            raise RuntimeError("passing verification cannot retain an open blocking finding")
        prefix = panel_prefix(panel, assignment)
        for finding in findings:
            identifier = finding["id"]
            if finding["status"] != "open":
                raise RuntimeError(f"{identifier} must be a new open finding")
            if not identifier.startswith(prefix):
                raise RuntimeError(f"{identifier} must start with its assignment's id_prefix {prefix}")
            if not all(isinstance(finding.get(field), str) and finding[field].strip()
                       for field in ("file", "description")):
                raise RuntimeError(f"{identifier} needs its file and description")
        held = inherited | {finding["id"] for member in members.values()
                            for finding in member["result"].get("findings", [])}
    else:
        if OFFICIAL in members:
            raise RuntimeError("the official code review result is already registered")
        check_result(root, value, result)
        if "calibration" in result:
            raise RuntimeError("calibration rows come only from the calibration reader's own result,"
                               " registered with calibrate; the claiming result carries none")
        if review_loop(root, current["delivery"]) == "blocking_delta":
            problems = follow_up_problems(result.get("findings", []), follow_up_roles(
                item_record(root, current["delivery"], current["story"])))
            if problems:
                raise RuntimeError("code review follow-ups are incomplete: " + "; ".join(problems))
        key, findings = OFFICIAL, result.get("findings", [])
        held = {finding["id"] for member in members.values() for finding in member["result"].get("findings", [])}
    for finding in findings:
        if finding["id"] in held:
            raise RuntimeError(f"{finding['id']} is already held by another result of this pass or an"
                               " earlier cycle")
    return key


def register_panel_result(root: Path, result: dict) -> dict:
    """Register one result of a code review panel pass at code_review_panel beside_official.

    The official reviewer's result is checked as register_result checks it. A
    lens reader's result names its assignment and holds only new, open
    findings under the assignment's id prefix. Each records when it returned;
    neither settles the code review, so the implementation writer stays idle
    until merge_panel does.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "registering a panel result")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        panel = code_review_panel_state(root, value)
        if panel is None:
            raise RuntimeError("panel-result registers a code review panel result only at code_review_panel"
                               " beside_official")
        key = check_panel_result(root, value, current, panel, result)
        members = panel["members"]
        stored = dict(result)
        stored["result_hash"] = digest(result)
        completed = time.time()
        started = value["workers"]["code_reviewer"].get("started_at", completed)
        value["panel"] = {**panel, "members": {**members, key: {
            "result": stored, "completed_at": completed, "elapsed_seconds": max(0.0, completed - started)}}}
        write_session(root, value)
        return value


def merged_panel_result(root: Path, value: dict, current: dict, panel: dict) -> dict:
    """The one code review result merge_panel settles, or the refusal of an incomplete panel pass."""
    worker = value["workers"]["code_reviewer"]
    if worker["state"] != "running":
        raise RuntimeError("the code review already settled; freeze a new session to replace it")
    claims, _targets = panel_claims(panel, value, review_loop(root, current["delivery"]))
    registered = (value.get("calibration") or {}).get("result")
    if (claims or registered) and (registered is None or sorted(
            registered["claims"], key=lambda claim: claim["id"]) != claims):
        raise RuntimeError("register the calibration reader's result with calibrate for exactly the open"
                           " critical or major claims to rule, as returned: "
                           + (", ".join(claim["id"] for claim in claims) or "none"))
    rows = {row["finding"]: row for row in registered["calibration"]} if registered else {}
    members = panel["members"]
    official = members[OFFICIAL]["result"]
    carried = {finding["id"]: finding for finding in value.get("unresolved_findings", [])
               if finding["role"] == "code_reviewer" and finding.get("source") == "panel"}
    findings = [{**finding, "source": "panel", "lens": carried[finding["id"]]["lens"]}
                if finding["id"] in carried else {**finding, "source": OFFICIAL}
                for finding in official["findings"]]
    gating = {source: sorted(finding["id"] for finding in findings if finding["source"] == source
                             and finding["status"] == "open" and blocking(finding))
              for source in (OFFICIAL, "panel")}
    rulings: dict = {"confirmed": [], "minor": [], "invalid": [], DUPLICATE: {}}
    claims = []
    for assignment in panel["assignments"]:
        for finding in members[assignment["key"]]["result"]["findings"]:
            if not blocking(finding):
                continue
            row = rows[finding["id"]]
            ruling = row["calibrated_severity"].casefold()
            claim = {"finding": finding["id"], "lens": assignment["lens"], "severity": finding["severity"],
                     "ruling": ruling if ruling in {DUPLICATE, "invalid", "minor"} else "confirmed",
                     "file": finding["file"], "description": finding["description"], "reason": row["reason"]}
            claims.append(claim)
            if ruling == DUPLICATE:
                claim["duplicate_of"] = row["duplicate_of"]
                rulings[DUPLICATE][finding["id"]] = row["duplicate_of"]
                continue
            if ruling == "invalid":
                rulings["invalid"].append(finding["id"])
                continue
            entry = {**finding, "source": "panel", "lens": assignment["lens"],
                     "claimed_severity": finding["severity"], "calibrated_severity": ruling}
            if ruling == "minor":
                follow_up = {"owner_role": row.get("owner_role"), "revisit_trigger": row.get("revisit_trigger")}
                entry.update(severity="minor", **follow_up)
                claim.update(follow_up)
                rulings["minor"].append(finding["id"])
            else:
                rulings["confirmed"].append(finding["id"])
            findings.append(entry)
    official_seconds = members[OFFICIAL]["elapsed_seconds"]
    combined = max(0.0, time.time() - worker.get("started_at", time.time()))
    record = {"pass": panel["pass"], "mode": official["mode"],
              "official_result_hash": official["result_hash"],
              "lens_result_hashes": {assignment["key"]: members[assignment["key"]]["result"]["result_hash"]
                                     for assignment in panel["assignments"]},
              "official_seconds": round(official_seconds, 1),
              "panel_seconds": round(max(members[assignment["key"]]["elapsed_seconds"]
                                         for assignment in panel["assignments"]), 1),
              "combined_seconds": round(combined, 1),
              "combined_ratio": round(combined / official_seconds, 2) if official_seconds > 0 else None,
              "official_blocking": gating[OFFICIAL], "carried_panel_blocking": gating["panel"],
              "confirmed": sorted(rulings["confirmed"]), "minor": sorted(rulings["minor"]),
              "invalid": sorted(rulings["invalid"]), DUPLICATE: dict(sorted(rulings[DUPLICATE].items())),
              "claims": sorted(claims, key=lambda claim: claim["finding"])}
    result = {key: item for key, item in official.items() if key not in {"result_hash", "findings", "verdict"}}
    result.update(verdict="failed" if official["verdict"] == "failed" or rulings["confirmed"] else "passed",
                  findings=findings, panel=record)
    if registered:
        result.update(calibration=registered["calibration"], calibration_result_hash=registered["result_hash"])
    return result


def merge_panel(root: Path) -> dict:
    """Register the one code review result of a code review panel pass and settle the code review.

    Its findings are the official result's, with source official, except that
    a panel finding of an earlier pass the official result re-lists keeps its
    source and lens, and each lens claim that calibration confirmed or lowered
    to minor, with source panel, its lens and its claimed and calibrated
    severity. A claim ruled invalid or duplicate and a lens finding that does
    not block stay out; the calibration rows on the result keep every ruling.
    The result keeps the official report, mode and checks, fails when the
    official result fails or a lens claim is confirmed, and settles through
    every check register_result applies. Its panel record keeps the combined
    step's wall clock from the freeze to this merge against the official
    reviewer's, the open blocking findings of the official result by source,
    and every lens claim with its text and ruling, which no later session
    holds otherwise.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "merging the code review panel")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        panel = code_review_panel_state(root, value)
        if panel is None:
            raise RuntimeError("merge-panel settles a code review only at code_review_panel beside_official")
        result = merged_panel_result(root, value, current, panel)
        return settle_result(root, value, result, merged=True)


def resume_qa(root: Path) -> dict:
    """Continue QA on unchanged sources without repeating the independent review."""
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the verification command to exit before resuming QA;"
                               " `wait` returns once it has")
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
            environment = require_raw_evidence(root, value, result["checks"])
            if current["runtime_required"]:
                require_runtime_evidence(root, value, result["checks"]["fresh_runtime"], environment)
    return value


def runtime_needs_cleanup(session: dict) -> bool:
    state = session.get("runtime", {})
    return bool(state.get("active") or state.get("cleanup_required") or state.get("pending"))


def runtime_environment_identity(root: Path) -> dict:
    return environment_identity(root, command_environment(root))


def run_environment(root: Path, verb: str, value: str | None = None) -> dict:
    """Invoke only the approved environment contract in a persistent private clone."""
    root = root.resolve()
    if verb not in {"down", "up", "seed", "logs", "url"}:
        raise RuntimeError("unsupported environment verb")
    with environment_lock(root, "qa_engineer", "environment --verb " + verb), \
            command_lock(root, "qa_engineer", "environment --verb " + verb):
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
                clone_private_checkout(root, execution_root, current["product_commit"])
            if execution_root.is_symlink() or (verb != "down" and git(execution_root, "rev-parse", "HEAD") != current["product_commit"]):
                raise RuntimeError("runtime checkout no longer binds the frozen candidate")
            workdir = (execution_root / str(contract.get("env_workdir", "."))).resolve()
            if workdir != execution_root.resolve() and execution_root.resolve() not in workdir.parents:
                raise RuntimeError("environment workdir escapes its isolated candidate")
            from delivery_git import require_visible_item_index
            difference = ""
            try:
                require_visible_item_index(execution_root)
                before_intact = not git(execution_root, "diff", "--name-only", "HEAD")
                difference = "" if before_intact else checkout_difference(execution_root)
            except RuntimeError:
                before_intact = False
            if not before_intact and verb != "down":
                raise RuntimeError("runtime checkout changed" + (f" ({difference})" if difference else "")
                                   + "; only teardown is allowed before a new verification session")
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
            difference = ""
            try:
                require_visible_item_index(execution_root)
                intact = (before_intact and git(execution_root, "rev-parse", "HEAD") == current["product_commit"]
                          and not git(execution_root, "diff", "--name-only", "HEAD"))
                difference = "" if intact else checkout_difference(execution_root)
            except RuntimeError:
                intact = False
            event = {"candidate_intact": intact, "verb": verb, "value": value, "command": command, "exit_code": completed.returncode,
                     "output_file": output_name, "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                     "duration_seconds": time.monotonic() - started, "candidate_hash": current["candidate_hash"],
                     "environment_identity": environment_identity, "completed_at": time.time(),
                     "attempt_id": state.get("attempt_id")}
            if difference:
                event["checkout_difference"] = difference
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


def lane_command_environment(root: Path, directory: Path,
                             base: dict | None = None) -> tuple[dict, dict[str, list[str]]]:
    """Return the inherited environment, or *base*, without search path entries outside *root*.

    An entry is kept when it resolves inside the worktree, a relative entry
    against the command's working directory and a link through its target. A
    variable left with no entry is unset. The second value lists the dropped
    entries of each variable.
    """
    environment = dict(os.environ if base is None else base)
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
    # Only the run that writes a selection file names it; an inherited one would skip suites unseen.
    for name in SELECTION_VARIABLES:
        environment.pop(name, None)
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


def pre_handoff_regression(root: Path, delivery_id: str) -> str:
    """The pre_handoff_regression value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, PRE_HANDOFF_SWITCH)


def require_touched_suites(root: Path, delivery_id: str) -> None:
    value = pre_handoff_regression(root, delivery_id)
    if value != TOUCHED_SUITES:
        raise RuntimeError(f"pre-handoff regression runs only at process switch {PRE_HANDOFF_SWITCH}"
                           f" {TOUCHED_SUITES}; {delivery_id} runs it at {value}")


def automation_targets(body: str) -> list[str]:
    """The automation targets of a Test Plan body's automation-required scenarios, in plan order."""
    targets = []
    for _scenario, block in scenario_blocks(body):
        fields, _duplicates = scenario_fields(block)
        target = fields.get("automation_target", "").strip()
        if fields.get("automation", "").strip().casefold() == "required" and target:
            targets.append(target)
    return targets


def scenario_records(body: str) -> dict[str, dict]:
    """Each scenario of a Test Plan body by id, with its automation, target, level and Then."""
    records = {}
    for identifier, block in scenario_blocks(body):
        fields, _duplicates = scenario_fields(block)
        records[identifier] = {key: fields.get(field, "").strip() for key, field in (
            ("automation", "automation"), ("automation_target", "automation_target"), ("level", "level"),
            ("then", "Then"))}
    return records


def plan_automation_targets(docs: Path, relative: str, label: str) -> list[str]:
    """The automation targets of a Test Plan's automation-required scenarios, in plan order."""
    if not relative:
        raise RuntimeError(f"{label} records no Test Plan; its suite cannot be selected")
    path = docs / relative
    if not delivery._is_normalized_claim(relative) or path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} records Test Plan {relative}, which the candidate does not hold;"
                           " its suite cannot be selected")
    return automation_targets(delivery.split_note(path)[1])


def integrated_plan(root: Path, commit: str, plans: list[str], hashes: set[str]) -> dict | None:
    """The newest revision of a story's Test Plans whose backlog digest is one of *hashes*, or None.

    An Item records the digest of the Test Plan revision it integrated as
    test_plan_source_hash. The candidate's own file is the newest revision;
    after it come the revisions the candidate's history wrote, newest first.
    The result names the plan, its digest, the commit that holds it and its
    automation targets.
    """
    docs = delivery.docs_root(root)

    def integrated(relative: str, text: str, holder: str) -> dict | None:
        value = backlog_compile.digest_text(text)
        if value not in hashes:
            return None
        body = backlog_compile.parse_front_matter_text(text)[1]
        return {"test_plan": relative, "source_hash": value, "commit": holder,
                "targets": automation_targets(body), "scenarios": scenario_records(body)}

    for relative in plans:
        path = docs / relative
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() and not path.is_symlink() else None
        except (OSError, ValueError):
            text = None
        revision = integrated(relative, text, commit) if text is not None else None
        if revision is not None:
            return revision
    names = {(docs / relative).relative_to(root).as_posix(): relative for relative in plans}
    history = git(root, "--literal-pathspecs", "log", "--full-history", "--topo-order", "--format=%H",
                  commit, "--", *names)
    for holder in history.split():
        for name, relative in names.items():
            shown = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob",
                                    f"{holder}:{name}"], capture_output=True, check=False)
            if shown.returncode:
                continue
            try:
                revision = integrated(relative, shown.stdout.decode("utf-8"), holder)
            except UnicodeDecodeError:
                continue
            if revision is not None:
                return revision
    return None


def integrated_items(root: Path, delivery_id: str):
    """Yield each integrated Item of a merged Delivery or of *delivery_id* as (delivery, current, path, record).

    The current Delivery's integrated Items are no earlier stories, but a story
    one of them integrated again takes the revision it integrated. A record
    that cannot be read and a merge state Git cannot decide refuse, so no
    integrated Item is dropped silently.
    """
    docs = delivery.docs_root(root)
    for directory in delivery.delivery_dirs(docs):
        record = directory / "delivery.md"
        if not record.is_file():
            continue
        try:
            props, _ = delivery.split_note(record)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{record.relative_to(root).as_posix()} cannot be read, so the suites it"
                               f" merged cannot be selected: {exc}") from exc
        identifier = str(props.get("id", ""))
        current_delivery = identifier == delivery_id
        if not current_delivery:
            # An unreadable Delivery Review record is an unknown merge state as well.
            status, unknown = delivery.delivery_state(directory, props)
            if unknown is not None:
                raise RuntimeError(f"whether {identifier} merged decides which earlier suites the candidate"
                                   f" touches: {unknown}")
            if status != "merged":
                continue
        for item_path in sorted(directory.glob("items/*/item.md")):
            try:
                item, _ = delivery.split_note(item_path)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"{item_path.relative_to(root).as_posix()} cannot be read, so its suite"
                                   f" cannot be selected: {exc}") from exc
            if item.get("status") == "integrated":
                yield identifier, current_delivery, item_path, item


def add_integration(integrations: dict[str, dict], item: dict) -> None:
    """Add the Test Plan path and revision digest an integrated Item recorded to its story's integrations."""
    plan = str(item.get("test_plan_path") or "")
    recorded = item.get("test_plan_source_hash")
    if plan and delivery._is_normalized_claim(plan) and isinstance(recorded, str) and recorded:
        integration = integrations.setdefault(str(item.get("story_id", "")), {"plans": [], "hashes": set()})
        if plan not in integration["plans"]:
            integration["plans"].append(plan)
        integration["hashes"].add(recorded)


def converted_scenarios(root: Path, delivery_id: str, current: dict) -> list[dict]:
    """The scenarios the Item converts: automation-required, their target in a file the Item changes, and
    their Test Plan level changed since the newest revision an integrated Item of their story bound.

    A scenario no integration bound yet, or one whose level is unchanged, is
    no conversion. A story whose integrated revision neither the candidate nor
    its history holds refuses, so no conversion is dropped silently.
    """
    docs = delivery.docs_root(root)
    changed = set(current["changed_files"])
    integrations: dict[str, dict] = {}
    for _identifier, _current, _path, item in integrated_items(root, delivery_id):
        add_integration(integrations, item)
    converted = []
    for story, integration in sorted(integrations.items()):
        rewritten = {}
        for relative in integration["plans"]:
            path = docs / relative
            if path.is_symlink() or not path.is_file():
                continue
            for identifier, record in scenario_records(delivery.split_note(path)[1]).items():
                target = record["automation_target"]
                if (record["automation"].casefold() == "required" and target
                        and target.partition("::")[0] in changed):
                    rewritten.setdefault(identifier, (relative, record))
        if not rewritten:
            continue
        revision = integrated_plan(root, current["product_commit"], integration["plans"], integration["hashes"])
        if revision is None:
            raise RuntimeError(f"{story} integrated a Test Plan revision neither the candidate nor its history"
                               f" holds, so whether {', '.join(sorted(rewritten))} changed level cannot be decided")
        for identifier, (relative, record) in sorted(rewritten.items()):
            before = revision["scenarios"].get(identifier)
            if before is None or before["level"] == record["level"]:
                continue
            converted.append({"story": story, "scenario": identifier,
                              "test_plan": (docs / relative).relative_to(root).as_posix(), "then": record["then"],
                              "before": {"level": before["level"] or None,
                                         "automation_target": before["automation_target"]},
                              "after": {"level": record["level"] or None,
                                        "automation_target": record["automation_target"]}})
    return converted


def assertion_kinds() -> dict:
    return json.loads(ASSERTION_KINDS_PATH.read_text(encoding="utf-8"))["kinds"]


def assertion_map_path(root: Path) -> Path:
    return session_path(root).parent / ASSERTION_MAP_FILE


def anchored(root: Path, commit: str, path: str, code: str) -> bool:
    """Whether a line of *path* at *commit*, stripped, is *code*, stripped."""
    shown = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"],
                           capture_output=True, check=False)
    if shown.returncode:
        return False
    try:
        text = shown.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return code.strip() in {line.strip() for line in text.splitlines()}


def assertion_problems(root: Path, commit: str, side: str, identifier: str, entries: object,
                       kinds: dict) -> list[str]:
    """Why the *side* assertions of one map entry are no anchored, typed assertions, or nothing."""
    where = "integration base" if side == "before" else "candidate"
    if not isinstance(entries, list) or not entries:
        return [f"{identifier} names no {side} assertion"]
    problems = []
    for number, entry in enumerate(entries, 1):
        label = f"{identifier} {side} assertion {number}"
        if not isinstance(entry, dict) or set(entry) != set(ASSERTION_FIELDS):
            problems.append(f"{label} holds other fields than {', '.join(ASSERTION_FIELDS)}")
            continue
        path, code, kind, expected = (entry[field] for field in ASSERTION_FIELDS)
        if not isinstance(path, str) or not delivery._is_normalized_claim(path):
            problems.append(f"{label} names no normalized repository path")
        elif not isinstance(code, str) or not code.strip() or "\n" in code or "\r" in code:
            problems.append(f"{label} names no single line of code")
        elif kind not in kinds:
            problems.append(f"{label} names kind {kind!r}, none of {', '.join(kinds)}")
        elif expected is not None and (not isinstance(expected, str) or not expected.strip()
                                       or expected not in code):
            problems.append(f"{label} names an expected value its code does not hold; name null for none")
        elif not anchored(root, commit, path, code):
            problems.append(f"{label} is no line of {path} in the {where}")
    return problems


def assertion_map_check(root: Path, current: dict) -> dict:
    """Check the Item's assertion map against the scenarios it converts.

    Every converted scenario needs one entry whose then is its Then, with at
    least one before assertion that is a line of the integration base and one
    after assertion that is a line of the candidate; anything else is a
    problem freeze refuses. A complete entry is flagged when every after
    assertion is of a weak kind while a before one was not, or when an
    expected value of a before assertion is no after assertion's.
    """
    converted = converted_scenarios(root, current["delivery"], current)
    path = safe_runtime_path(root, assertion_map_path(root))
    result = {"converted_scenarios": converted, "map_hash": None, "problems": [], "flagged": []}
    if not path.exists():
        if converted:
            result["problems"].append("no assertion map names the assertions of the converted scenarios "
                                      + ", ".join(entry["scenario"] for entry in converted))
        return result
    raw = safe_runtime_path(root, path, file_only=True).read_bytes()
    result["map_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scenarios"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["scenarios"], dict)):
        result["problems"].append("the assertion map holds no schema_version 1 and scenarios")
        return result
    kinds = assertion_kinds()
    scenarios = {entry["scenario"]: entry for entry in converted}
    for identifier in sorted(set(value["scenarios"]) - set(scenarios)):
        result["problems"].append(f"{identifier} is no scenario the Item converts")
    for identifier, scenario in sorted(scenarios.items()):
        entry = value["scenarios"].get(identifier)
        if not isinstance(entry, dict) or set(entry) != {"then", "before", "after"}:
            result["problems"].append(f"{identifier} has no map entry of then, before and after")
            continue
        problems = [] if entry["then"] == scenario["then"] else [
            f"{identifier} maps another then than its Test Plan's Then: {scenario['then']}"]
        problems += assertion_problems(root, current["integration_base_commit"], "before", identifier,
                                       entry["before"], kinds)
        problems += assertion_problems(root, current["product_commit"], "after", identifier, entry["after"], kinds)
        if problems:
            result["problems"].extend(problems)
            continue
        flags = []
        before, after = entry["before"], entry["after"]
        if (any(not kinds[item["kind"]]["weak"] for item in before)
                and all(kinds[item["kind"]]["weak"] for item in after)):
            flags.append("weakened_kind")
        lost = sorted({item["expected"] for item in before if item["expected"] is not None}
                      - {item["expected"] for item in after if item["expected"] is not None})
        if lost:
            flags.append("expected_value_lost")
        if flags:
            result["flagged"].append({"story": scenario["story"], "scenario": identifier, "then": scenario["then"],
                                      "flags": flags, "lost_expected_values": lost, "before": before,
                                      "after": after})
    return result


def assertion_map_status(root: Path, delivery_id: str, story: str) -> dict:
    """The converted scenarios of the committed candidate, its assertion map's check and a template to fill."""
    root = root.resolve()
    value = delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, LEVEL_CHANGE_SWITCH)
    if value != ASSERTION_MAP:
        raise RuntimeError(f"the assertion map is checked only at process switch {LEVEL_CHANGE_SWITCH}"
                           f" {ASSERTION_MAP}; {delivery_id} runs it at {value}")
    check = candidate(root, delivery_id, story, allow_evidence=True)["assertion_map"]
    return {**check, "assertion_map_file": str(assertion_map_path(root)),
            "kinds": {kind: spec["summary"] for kind, spec in assertion_kinds().items()},
            "template": {"schema_version": 1, "scenarios": {
                entry["scenario"]: {"then": entry["then"], "before": [], "after": []}
                for entry in check["converted_scenarios"]}}}


def derive_regression(root: Path, delivery_id: str, story: str, current: dict) -> dict:
    """Derive the pre-handoff regression selection of an Item's exact candidate.

    An earlier story is one whose Item is integrated in a Delivery that the
    candidate holds as merged and whose path claims hold or lie under a path
    the candidate changes against its integration base. The selection is the
    automation targets of the automation-required scenarios in those stories'
    Test Plans and in the Item's own, as affected_test_ids of the approved
    diagnostic adapter; without that adapter, or with no target, the full
    approved test command runs.

    An earlier story's targets come from the newest Test Plan revision that an
    integrated Item the candidate holds, of a merged Delivery or of the current
    one, records as its test_plan_source_hash: the candidate's own Test Plan
    when it is that revision, else that revision from the candidate's history.
    A backlog revision no integration bound yet changes no selection. A record
    that cannot be read, a merge state Git cannot decide and an integrated
    revision neither the candidate nor its history holds refuse, so no suite
    is dropped silently.
    """
    docs = delivery.docs_root(root)
    changed = current["changed_files"]
    touched_items = []
    integrations: dict[str, dict] = {}
    for identifier, current_delivery, item_path, item in integrated_items(root, delivery_id):
        earlier_story = str(item.get("story_id", ""))
        plan = str(item.get("test_plan_path") or "")
        recorded = item.get("test_plan_source_hash")
        add_integration(integrations, item)
        claims = item.get("path_claims")
        if current_delivery or not isinstance(claims, list):
            continue
        touched = sorted({claim for claim in claims if isinstance(claim, str)
                          and any(delivery._claims_overlap(claim, path) for path in changed)})
        if not touched:
            continue
        label = f"{earlier_story} of {identifier}"
        if not plan:
            raise RuntimeError(f"{label} records no Test Plan; its suite cannot be selected")
        if not delivery._is_normalized_claim(plan):
            raise RuntimeError(f"{label} records Test Plan {plan}, which is no normalized docs path; its suite"
                               " cannot be selected")
        if not isinstance(recorded, str) or not recorded:
            raise RuntimeError(f"{label} records no test_plan_source_hash, so the Test Plan revision it"
                               " integrated cannot be selected")
        touched_items.append({"delivery": identifier, "story": earlier_story, "label": label,
                              "item": item_path.relative_to(root).as_posix(), "plan": plan,
                              "recorded": recorded, "touched": touched})
    revisions: dict[str, dict | None] = {}
    earlier = []
    for entry in touched_items:
        if entry["story"] not in revisions:
            integration = integrations[entry["story"]]
            revisions[entry["story"]] = integrated_plan(root, current["product_commit"], integration["plans"],
                                                        integration["hashes"])
        revision = revisions[entry["story"]]
        if revision is None:
            raise RuntimeError(f"{entry['label']} integrated Test Plan {entry['plan']} at {entry['recorded']},"
                               " a revision neither the candidate nor its history holds; its suite cannot be"
                               " selected")
        earlier.append({"delivery": entry["delivery"], "story": entry["story"], "item": entry["item"],
                        "test_plan": (docs / revision["test_plan"]).relative_to(root).as_posix(),
                        "test_plan_source_hash": revision["source_hash"], "test_plan_commit": revision["commit"],
                        "touched_claims": entry["touched"],
                        "touched_paths": sorted({path for path in changed for claim in entry["touched"]
                                                 if delivery._claims_overlap(claim, path)}),
                        "automation_targets": sorted(set(revision["targets"]))})
    earlier.sort(key=lambda entry: (entry["delivery"], entry["story"]))
    plan = str(item_record(root, delivery_id, story).get("test_plan_path") or "")
    own = sorted(set(plan_automation_targets(docs, plan, f"{story} of {delivery_id}")))
    for label, targets in [*((entry["story"], entry["automation_targets"]) for entry in earlier), (story, own)]:
        for target in targets:
            if not literal_test_id(target):
                raise RuntimeError(f"automation target {target} of {label} is no literal test id: it is padded,"
                                   " starts with '-' or holds a control character")
    selected = sorted(set(own).union(*(entry["automation_targets"] for entry in earlier)))
    contract, _ = delivery.split_note(docs / "operation/verification-contract.md")

    def approved(kind: str) -> str | None:
        command = contract.get(kind + "_command")
        resolved = isinstance(command, str) and command.strip() and "{{" not in command and "}}" not in command
        return command if resolved else None

    kind = "diagnostic_test" if selected and approved("diagnostic_test") else "test"
    command = approved(kind)
    if command is None:
        raise RuntimeError("approved verification command is missing or contains unresolved parameters")
    return {"delivery": delivery_id, "story": story, "candidate_hash": current["candidate_hash"],
            "product_commit": current["product_commit"],
            "candidate_tree": git(root, "rev-parse", current["product_commit"] + "^{tree}"),
            "earlier_stories": earlier, "own_targets": own, "affected_test_ids": selected,
            "kind": kind, "command": command, "workdir": str(contract.get(kind + "_workdir", "."))}


def write_pre_handoff_selection(root: Path, derived: dict) -> Path | None:
    """Write the derived selection in the diagnostic adapter's input schema, which QA's run reads too.

    Unchanged bytes are left in place, so deriving the selection again never
    invalidates a QA diagnostic that reads the same file.
    """
    if not derived["affected_test_ids"]:
        return None
    path = raw_output_path(root, PRE_HANDOFF_SELECTION)
    text = json.dumps({"schema_version": 1, "candidate_hash": derived["candidate_hash"], "failed_test_ids": [],
                       "affected_test_ids": derived["affected_test_ids"]}, indent=2, sort_keys=True) + "\n"
    if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.replace_text(path, text)
    return path


def regression_selection(root: Path, delivery_id: str, story: str) -> dict:
    """Derive and write the pre-handoff regression selection of the Item's committed candidate.

    It holds the Item's environment lock meanwhile, which a pre-handoff run
    and QA's diagnostic hold while their command runs, so it never rewrites the
    selection a running command reads.
    """
    root = root.resolve()
    require_touched_suites(root, delivery_id)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "regression-selection") as interrupted:
        derived = derive_regression(root, delivery_id, story, candidate(root, delivery_id, story, allow_evidence=True))
        path = write_pre_handoff_selection(root, derived)
    return {**derived, "selection_file": str(path) if path else None, "interrupted_holder": interrupted}


def pre_handoff_record_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / "pre-handoff.json", file_only=True)


def pre_handoff_runs(root: Path) -> list[dict]:
    """The pre-handoff regression runs recorded in this Item worktree's runtime, oldest first."""
    path = pre_handoff_record_path(root)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        runs = value["runs"]
        if (value.get("schema_version") != 1 or not isinstance(runs, list)
                or any(not isinstance(run, dict) or run.get("evidence_hash") != digest(
                    {key: item for key, item in run.items() if key != "evidence_hash"}) for run in runs)):
            raise ValueError("invalid schema or evidence hash")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"pre-handoff run record is invalid: {exc}; move {path} aside and run"
                           " regression-run again") from exc
    return runs


def regression_run(root: Path, delivery_id: str, story: str) -> dict:
    """Run the pre-handoff regression selection on the Item's exact committed candidate.

    The coordinator runs it before the freeze, under the Item's environment
    lock for the whole run, in a private clone of the candidate commit without
    interpreter search paths that resolve outside that clone: the approved
    diagnostic adapter with the selection in AGENTROF_DIAGNOSTIC_TESTS, or the
    full approved test command without one. Only deriving the selection and
    cloning the candidate read the Item worktree, so the verification command
    lock, which guarded writes to it wait for, is held for those alone. The run
    and its result are recorded against the tree it cloned, so a commit made
    while the command runs is a new candidate that this run does not bind.
    """
    root = root.resolve()
    require_touched_suites(root, delivery_id)
    guard_write(root)
    pre_handoff_runs(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, PRE_HANDOFF_RUN) as interrupted, \
            contextlib.ExitStack() as reading:
        reading.enter_context(command_lock(root, PRE_HANDOFF_HOLDER, PRE_HANDOFF_RUN))
        current = candidate(root, delivery_id, story, allow_evidence=True)
        derived = derive_regression(root, delivery_id, story, current)
        # Read while the run still holds the worktree, which may change once its command runs,
        # and checked now, so a malformed contract costs no run of the selection.
        contract = verification_contract(root)
        contract_variables(contract)
        shared = write_pre_handoff_selection(root, derived)
        environment = command_environment(root, diagnostic=derived["kind"] == "diagnostic_test")
        selections: dict[Path, tuple[bytes, list[int]]] = {}
        if derived["kind"] == "diagnostic_test":
            selector = safe_runtime_path(root, session_path(root).parent / "pre-handoff-tests.json", file_only=True)
            atomic_file.replace_text(selector, json.dumps(
                {"schema_version": 1, "candidate_hash": current["candidate_hash"], "failed_test_ids": [],
                 "affected_test_ids": derived["affected_test_ids"],
                 "selected_test_ids": derived["affected_test_ids"]}, indent=2, sort_keys=True) + "\n")
            environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(selector)
            # As in QA's diagnostic run, neither the selection the command
            # receives nor the one QA's diagnostic reads may change while it runs.
            selections = {path: (path.read_bytes(), source_file_generation(path)) for path in (selector, shared)}
        scratch = safe_runtime_path(root, Path(environment["AGENTROF_VERIFICATION_SCRATCH"]))
        scratch.mkdir(parents=True, exist_ok=True)
        groups = group_report_declaration(root, delivery_id, contract)
        if groups is not None:
            clear_group_report(scratch, groups)
        started = time.monotonic()
        completed, intact, difference, dropped, ran = private_checkout_run(
            root, scratch, current["product_commit"], derived["workdir"], derived["command"], environment,
            isolate_search_paths=True, cloned=reading.close)
        duration = time.monotonic() - started
        group_report = read_group_report(scratch, groups) if groups is not None else None
        if group_report is not None and group_report["missing_test_groups"]:
            intact = False
        selection_intact = None
        if selections:
            try:
                selection_intact = all(
                    safe_runtime_path(root, path, file_only=True).read_bytes() == data
                    and source_file_generation(path) == generation for path, (data, generation) in selections.items())
            except (RuntimeError, ValueError, OSError):
                selection_intact = False
            intact = intact and selection_intact
        with locked(root):
            runs = pre_handoff_runs(root)
            output_name = "scratch/pre-handoff-" + uuid.uuid4().hex + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
            record = {**{key: derived[key] for key in ("delivery", "story", "product_commit", *PRE_HANDOFF_IDENTITY)},
                      "earlier_stories": [{"delivery": entry["delivery"], "story": entry["story"]}
                                          for entry in derived["earlier_stories"]],
                      "exit_code": completed.returncode, "candidate_intact": intact, "output_file": output_name,
                      "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                      "duration_seconds": duration, "completed_at": time.time(), "dropped_search_paths": dropped,
                      **environment_identity(root, ran, contract)}
            if difference:
                record["checkout_difference"] = difference
            if selection_intact is not None:
                record["selection_intact"] = selection_intact
            if group_report is not None:
                record.update(group_report)
            record["evidence_hash"] = digest(record)
            atomic_file.replace_text(pre_handoff_record_path(root), json.dumps(
                {"schema_version": 1, "runs": [*runs, record]}, indent=2, sort_keys=True) + "\n")
    return {**record, "output_path": str(raw_output_path(root, output_name)), "interrupted_holder": interrupted}


def accepted_pre_handoff(root: Path, delivery_id: str, story: str, current: dict) -> dict:
    """The passing pre-handoff run that lets the exact candidate freeze, or the refusal that names why none does.

    The latest run on the candidate's tree with the selection and command
    derived for it decides: a new commit, a changed selection or a changed
    approved command needs a new run. The session keeps it with the bindings
    and the declared environment QA's final test run compares to reuse it.
    """
    derived = derive_regression(root, delivery_id, story, current)
    identity = {key: derived[key] for key in PRE_HANDOFF_IDENTITY}
    runs = [run for run in pre_handoff_runs(root) if (run.get("delivery"), run.get("story")) == (delivery_id, story)
            and {key: run.get(key) for key in PRE_HANDOFF_IDENTITY} == identity]
    tree = derived["candidate_tree"]
    if not runs:
        raise RuntimeError(f"DELIVERY_PRE_HANDOFF_MISSING: no pre-handoff regression run binds candidate tree {tree}"
                           f" with the selection and command derived for it; run regression-run --delivery"
                           f" {delivery_id} --story {story}, repair what it finds and commit, then freeze")
    latest = runs[-1]
    if latest.get("exit_code") != 0 or latest.get("candidate_intact") is not True:
        changed = ("" if latest.get("candidate_intact") is True
                   else " and changed the selection it ran" if latest.get("selection_intact") is False
                   else " and its group report lacks " + ", ".join(latest["missing_test_groups"])
                   if latest.get("missing_test_groups")
                   else f" and changed its checkout ({latest['checkout_difference']})" if latest.get("checkout_difference")
                   else " and changed its checkout")
        raise RuntimeError(f"DELIVERY_PRE_HANDOFF_MISSING: the latest pre-handoff regression run on candidate tree"
                           f" {tree} exited {latest.get('exit_code')}{changed}; repair what it found and commit,"
                           " then run regression-run on the new candidate, or run it again once a cause outside"
                           " the candidate is fixed")
    receipt = {key: latest[key] for key in ("evidence_hash", *PRE_HANDOFF_IDENTITY, "earlier_stories", "exit_code",
                                            "candidate_intact", "duration_seconds", "completed_at")}
    # A run recorded before runs named their declared environment has none, so QA reuses no such run.
    receipt.update({key: latest[key] for key in ENVIRONMENT_FIELDS if key in latest})
    return receipt


def pre_handoff_history(root: Path, session: dict | None, delivery_id: str, story: str) -> list[dict]:
    """Every pre-handoff regression run of the Item so far, oldest first.

    It is the history the session carries, then each run of the Item's runtime
    record that the history does not hold yet, by evidence hash. Each freeze
    carries it into the next session and approve-item-evidence records it, so
    a freeze of the same tree never counts a run twice, and a runtime record
    moved aside loses no run a freeze already carried.
    """
    history = list(session.get("pre_handoff_history", [])) if session else []
    held = {run.get("evidence_hash") for run in history}
    for run in pre_handoff_runs(root):
        if (run.get("delivery"), run.get("story")) == (delivery_id, story) and run["evidence_hash"] not in held:
            history.append({key: run.get(key) for key in PRE_HANDOFF_HISTORY_FIELDS})
            held.add(run["evidence_hash"])
    return history


def require_runtime_evidence(root: Path, session: dict, evidence: dict, environment: dict | None = None) -> None:
    """Check the fresh runtime events as recorded: every event of the attempt shares one complete
    environment identity, the one *environment* names when QA's final command evidence gives it, and
    none is compared with the environment of the process that checks it."""
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
    contract = verification_contract(root)
    shared = None
    for event in events:
        path = raw_output_path(root, event["output_file"])
        if (event.get("exit_code") != 0 or event.get("candidate_intact") is not True or event.get("candidate_hash") != session["candidate"]["candidate_hash"]
                or event.get("attempt_id") != attempt_id
                or not fresh_record(event)
                or event.get("evidence_hash") != digest({key: value for key, value in event.items() if key != "evidence_hash"})
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != event["output_sha256"]):
            raise RuntimeError("runtime command evidence is failed, stale or missing")
        identity = event.get("environment_identity")
        problem = environment_problem(identity, contract)
        if problem:
            raise RuntimeError(f"runtime command evidence {problem}")
        if shared is None:
            shared = {key: identity[key] for key in ENVIRONMENT_FIELDS}
        elif {key: identity[key] for key in ENVIRONMENT_FIELDS} != shared:
            raise RuntimeError("runtime command evidence ran in more than one environment")
    if environment is not None and shared is not None and shared != environment:
        raise RuntimeError("runtime command evidence ran in another environment than full_test_suite evidence")


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


def inspect_context(root: Path, payload: dict, *, reason: str | None = None,
                    refs: list[str] | None = None) -> dict:
    """Batch source units from the frozen Git candidate, never live text."""
    import context_catalog
    import context_history
    import project_context
    current = require_current(root, read_session(root), allow_evidence=True)
    if payload.get("candidate_hash") != current["candidate_hash"]:
        raise RuntimeError("context manifest belongs to another verification candidate")
    if payload.get("product_commit", current["product_commit"]) != current["product_commit"]:
        raise RuntimeError("context manifest names another candidate commit")
    plan = payload.get("project_reading")
    if not isinstance(plan, dict) or "request" not in plan:
        raise RuntimeError("resolve context or use frozen inspect reads before continuing")
    def addressed(index):
        project_context.validate_plan(root, index, plan)
        if reason is not None:
            return project_context.expand_context(root, index, plan, reason=reason, refs=refs, persist_state=False)
        if refs:
            raise RuntimeError("expanding frozen context requires a reason")
        return {row["unit_id"]: dict(index["catalog"]["units"].get(row["unit_id"], row)) for row in plan["must_read"]}
    addresses = project_context.with_index(root, addressed, no_cache=True)
    if reason is not None:
        return {"candidate_hash": current["candidate_hash"], "product_commit": current["product_commit"],
                "project_reading": addresses}
    request = project_context.request_data(root, plan)
    manual = []
    for obligation in plan.get("manual_reads", []):
        relative = obligation["path"]
        if not delivery._is_normalized_claim(relative):
            raise RuntimeError("manual frozen context path must be normalized and repository-relative")
        raw = context_history.git_source(root, relative, current["product_commit"])
        if context_catalog.digest(raw) != obligation["source_hash"] or len(raw) != obligation["source_bytes"]:
            raise RuntimeError("manual context source differs from the frozen candidate")
        manual.append({**obligation, "candidate_hash": current["candidate_hash"],
            "product_commit": current["product_commit"], "disposition": "requires_frozen_inspect",
            "next_action": "Use delivery_verification.py inspect --path <path> to read this source from the bound candidate before completing its obligation."})
    units = {}
    for row in plan["must_read"]:
        unit = addresses[row["unit_id"]]
        unit.setdefault("git_revision", current["product_commit"])
        units[unit["unit_id"]] = unit
    result = context_catalog.read_units(root / "workspace/docs", {"units": units},
        list(units), request["budget"]["max_source_bytes"])
    result.update(candidate_hash=current["candidate_hash"], plan_status=plan["status"],
                  coverage=plan["coverage"])
    if "continuation" in plan:
        result["continuation"] = plan["continuation"]
    if manual:
        result["manual_reads"] = manual
        result["status"] = "needs_manual_read"
    if not units and not manual and plan["status"] != "ready":
        result["status"] = plan["status"]
    return result


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


CLOSURE_SWITCH, CLOSURE_VALUE = "review_scope", "impact_closure"
# The Item notes its closure starts from; every other bound record stays a full read.
ITEM_RECORD_KEYS = ("story_path", "test_plan_path")
CONTRACT_KEYS = ("verification_contract_ref", "environment_contract_ref")
# The bound inputs that grow with the package rather than with the Item.
PACKAGE_SCOPED = "system-architecture/"


def review_scope(root: Path, delivery_id: str) -> str:
    """The review_scope value the Delivery runs under.

    ``full`` while no registry declares it, and where the policy cannot be
    read, so a reader whose policy is unreadable reads everything as released.
    """
    try:
        return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, CLOSURE_SWITCH)
    except (KeyError, ValueError):
        return "full"


def closure_read(root: Path, current: dict) -> tuple[list[str], dict]:
    """Narrow a reader's full read to the impact closure of the Item change.

    The closure starts from the Item's Story, Test Plan and contracts and every
    vault note the change touched. An architecture note outside the closure is
    listed with the hash the candidate already binds instead of being read;
    the Item's own records, the project root files and every product file the
    change touched stay full reads.
    """
    import task_inputs

    docs = delivery.docs_root(root)
    prefix = docs.relative_to(root).as_posix() + "/"
    item = next(path for path in current["inputs"] if path.endswith("/item.md"))
    props, _ = delivery.split_note(root / item)
    seeds = {prefix + props[key] for key in ITEM_RECORD_KEYS}
    seeds |= {prefix + props[key] + ".md" for key in CONTRACT_KEYS if props.get(key)}
    # Every vault file the Item changed since its integration base starts the
    # closure, notes and data alike; a deleted note seeds it with what it named.
    changed_vault = {path for path in current["changed_files"]
                     if path.startswith(prefix) and task_inputs.canonical_source(path)}
    seeds |= changed_vault
    deleted = task_inputs.deleted_texts(root, current.get("integration_base_commit"),
                                        changed_vault)
    try:
        scope = task_inputs.impact_closure(docs, seeds, prefix, deleted)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    reach = set(scope["closure"]) | set(scope["graph_gaps"]) | seeds
    reads = {path for path in reach if (root / path).is_file()}
    # Only a note the closure proves unchanged since its approval stays unread.
    proven = {row["path"] for row in scope["proven_unchanged"]}
    unread = sorted(path for path in current["inputs"]
                    if path.startswith(prefix + PACKAGE_SCOPED) and path not in reads
                    and path in proven)
    scope["proven_unchanged"] = [row for row in scope["proven_unchanged"] if row["path"] not in reads]
    scope.update(read="closure", seeds=sorted(seeds),
                 unread_inputs=[{"path": path, "sha256": current["inputs"][path]} for path in unread])
    full = (set(current["inputs"]) - set(unread)) | set(current["changed_files"]) | reads
    return sorted(full), {"scope": scope, "views": task_inputs.vault_views(docs)}


def manifest(root: Path, delivery_id: str, story: str, role: str, mode: str, remote: str = "origin") -> dict:
    if role not in ROLES or mode not in policy()["role_modes"][role]:
        raise RuntimeError("unsupported verification role or mode")
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if (current["delivery"], current["story"]) != (delivery_id, story):
        raise RuntimeError("verification session belongs to another Item")
    require_published_claims(root, current, remote)
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
    result = {"schema_version": 1, "role": role, "mode": mode, "session_id": value["session_id"],
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
    if review_scope(root, delivery_id) == CLOSURE_VALUE:
        result["full_read"], closure = closure_read(root, current)
        result.update({CLOSURE_SWITCH: CLOSURE_VALUE, "vault_views": closure["views"],
                       CLOSURE_VALUE: closure["scope"]})
    import project_context
    seeds = {path for path in current["inputs"] if path.endswith("/item.md")}
    result["project_reading"] = project_context.task_context(root, entry="deliver",
        role=role.replace("_", "-"), mode="review", paths=seeds, no_cache=True)
    result["read_interface"]["context"] = "inspect-context --plan <saved verification manifest>"
    result["read_interface"]["expand_context"] = "expand-context --plan <saved context manifest> --reason <reason> [--ref <reference>]"
    result["context_guidance"] = ("Start with project_reading and batch-read with inspect-context; "
        "full_read and all verification gates remain mandatory. Expand incomplete plans. "
        "If context is insufficient, use frozen inspect/diff on your initiative or parent direction "
        "and report context_findings with sources, impact, recovery and proposed fix to the parent. "
        "Only the parent offers an anonymous issue through issue-report after exact-payload user approval.")
    panel = code_review_panel_state(root, value) if role == "code_reviewer" else None
    if panel is not None:
        # Every reader of the panel pass receives this same manifest and one assignment.
        result["code_review_panel"] = {
            "pass": panel["pass"],
            "assignments": [{"lens": assignment["lens"], "focus": assignment["focus"],
                             "id_prefix": panel_prefix(panel, assignment)} for assignment in panel["assignments"]],
            "lens_result_interface": {"candidate_hash": current["candidate_hash"], "session_id": value["session_id"],
                                      "role": role, "mode": PANEL_LENS_MODE, "lens": "The assignment's lens ids",
                                      "verdict": "passed|failed", "report": "Independent findings through the lens",
                                      "findings": []},
            "registration": "Every reader registers its own result with panel-result --file <result.json>;"
                            " merge-panel then registers the one code review result"}
    return result


RUN_SUMMARY_FIELDS = ("exit_code", "candidate_intact", "selection_intact", "reused_pre_handoff", "duration_seconds",
                      "completed_at", "evidence_hash", "environment_hash", "checkout_difference", "earlier_stories",
                      "output_file")


def run_summary(root: Path, record: dict) -> dict:
    """One run's outcome without the identity and bindings that make the whole session large."""
    fields = {**record.get("identity", {}), **record}
    value = {key: fields[key] for key in RUN_SUMMARY_FIELDS if key in fields}
    if "reused_pre_handoff" in value:
        value["reused_pre_handoff"] = value["reused_pre_handoff"].get("evidence_hash")
    if "output_file" in value:
        value["output_path"] = str(raw_output_path(root, value["output_file"]))
    return value


def status_summary(root: Path, run: str | None = None) -> dict:
    """The session's identity, readers and run outcomes; `run` narrows the runs to one kind."""
    session = read_session(root)
    runs = dict(session["raw_evidence"])
    if "pre_handoff" in session:
        runs["pre_handoff"] = session["pre_handoff"]
    if run is not None:
        if run not in runs:
            raise RuntimeError(f"no {run} run is recorded in verification session {session['session_id']}")
        runs = {run: runs[run]}
    current = session["candidate"]
    return {"session_id": session["session_id"], "delivery": current["delivery"], "story": current["story"],
            "candidate_hash": current["candidate_hash"], "product_commit": current["product_commit"],
            "workers": {role: worker["state"] for role, worker in session["workers"].items()},
            "unresolved_findings": len(session.get("unresolved_findings", [])),
            "metrics": session["metrics"], "runs": {kind: run_summary(root, record) for kind, record in runs.items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "manifest", "validate", "regression-selection", "regression-run", "assertion-map"):
        cmd = subs.add_parser(name)
        cmd.add_argument("--delivery", required=True)
        cmd.add_argument("--story", required=True)
    subs.choices["freeze"].add_argument("--fresh", action="store_true")
    for name in ("freeze", "manifest"):
        subs.choices[name].add_argument("--remote", default="origin",
                                        help="the Delivery's Git remote, whose Integration holds its provisional claims")
    for name in ("manifest",):
        subs.choices[name].add_argument("--role", choices=ROLES, required=True)
        subs.choices[name].add_argument("--mode", required=True)
    result = subs.add_parser("result")
    result.add_argument("--file", required=True)
    calibrate = subs.add_parser("calibrate")
    calibrate.add_argument("--file", required=True)
    panel_result = subs.add_parser("panel-result")
    panel_result.add_argument("--file", required=True)
    subs.add_parser("merge-panel")
    run = subs.add_parser("run")
    run.add_argument("--kind", choices=("test", "mutation", "dependency_audit", "diagnostic_test"), required=True)
    run.add_argument("--selection-file", type=Path)
    run.add_argument("--spot-run-file", type=Path)
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
    for verb in ("inspect-context", "expand-context"):
        context = subs.add_parser(verb)
        context.add_argument("--plan", required=True)
        if verb == "expand-context":
            context.add_argument("--reason", required=True)
            context.add_argument("--ref", action="append")
    subs.add_parser("resume-qa")
    status = subs.add_parser("status")
    status.add_argument("--summary", action="store_true")
    status.add_argument("--run", choices=("test", "mutation", "dependency_audit", "diagnostic_test", "pre_handoff"))
    wait = subs.add_parser("wait")
    wait.add_argument("--role", choices=ROLES)
    wait.add_argument("--seconds", type=float)
    args = parser.parse_args(argv)
    root = Path(args.worktree).resolve()
    try:
        if args.command == "freeze":
            value = freeze(root, args.delivery, args.story, fresh=args.fresh, remote=args.remote)
        elif args.command == "inspect-context":
            value = inspect_context(root, json.loads(Path(args.plan).read_text(encoding="utf-8")))
        elif args.command == "expand-context":
            value = inspect_context(root, json.loads(Path(args.plan).read_text(encoding="utf-8")),
                                    reason=args.reason, refs=args.ref)
        elif args.command == "result":
            value = register_result(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "calibrate":
            value = register_calibration(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "panel-result":
            value = register_panel_result(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "merge-panel":
            value = merge_panel(root)
        elif args.command == "validate":
            value = validate(root, args.delivery, args.story)
        elif args.command == "environment":
            value = run_environment(root, args.verb, args.value)
        elif args.command == "lane-run":
            value = lane_run(root, args.delivery, args.story, args.role, args.kind, args.verb, args.value)
        elif args.command == "regression-selection":
            value = regression_selection(root, args.delivery, args.story)
        elif args.command == "regression-run":
            value = regression_run(root, args.delivery, args.story)
        elif args.command == "assertion-map":
            value = assertion_map_status(root, args.delivery, args.story)
        elif args.command == "inspect":
            value = inspect_instruction(root, args.instruction) if args.instruction else inspect_candidate(root, args.path, base=args.base)
        elif args.command == "diff":
            value = candidate_diff(root, args.path)
        elif args.command == "resume-qa":
            value = resume_qa(root)
        elif args.command == "wait":
            value = wait_for_release(root, args.role, args.seconds)
        elif args.command == "run":
            value = run_check(root, args.kind, fresh=args.fresh, selection_file=args.selection_file,
                              spot_file=args.spot_run_file)
        elif args.command == "manifest":
            value = manifest(root, args.delivery, args.story, args.role, args.mode, args.remote)
        elif args.summary or args.run:
            value = status_summary(root, args.run)
        else:
            value = read_session(root)
        print(json.dumps({"ok": True, **value}, indent=2))
        return 0 if args.command not in {"run", "environment", "lane-run", "regression-run"} or (value["exit_code"] == 0 and value.get("candidate_intact", True)) else 1
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
