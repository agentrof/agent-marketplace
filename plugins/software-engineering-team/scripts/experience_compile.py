#!/usr/bin/env python3
"""Compile living, process-owned Experience Design packages.

Experience Design uses ``experiences/<slug>/experience.md``.  It never owns
deployment releases, numbered baselines or program trees.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import stage_package
from ba_compile import without_generated_relations


GENERATED = "_generated"
LEDGER = "_ledger"
OPEN_REVISION = "open-revision.json"
OPEN_APPLICATION_REVISION = "open-application-revision.json"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXACT = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*):(JRN|FLW|SCR|STA|TRN)-[0-9]{3,}@r([1-9][0-9]*)$")
PACKAGE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)@r([1-9][0-9]*)$")
APPLICATION = re.compile(r"^application@r([1-9][0-9]*)$")
KIND = {
    "journey": ("journeys", "JRN", "-journey.md"),
    "flow-set": ("flows", "FLW", "-flow-set.md"),
    "screen": ("screens", "SCR", "-screen.md"),
    "state": ("states", "STA", "-state.md"),
    "transition": ("transitions", "TRN", "-transition.md"),
}
REFERENCE_FIELDS = (
    "journey_refs", "flow_refs", "screen_refs", "state_refs",
    "transition_refs", "related_to",
)
STATE_CLASSES = {
    "ordinary", "loading", "empty", "validation", "permission", "stale",
    "conflict", "failure", "retry", "recovery",
}
PROCESS_REGISTRY_FIELDS = {
    "schema_version", "experience_id", "package_revision", "origin_mode",
    "implements", "primary_process_ref", "related_process_refs",
    "input_bindings", "source_hash", "records", "application_map",
    "registry_hash", "package_hash",
}
MUTATING_COMMANDS = {
    "init", "begin-revision", "enter-review", "stub", "render",
    "render-application", "begin-application-revision",
    "enter-application-review", "approve-set", "rename", "retire",
}
TRANSACTION_SCHEMA_VERSION = 1
TRANSACTION_TIMEOUT_SECONDS = 30.0


def valid_experience_slug(value: str) -> bool:
    return (
        bool(SLUG.fullmatch(value))
        and not value.startswith("exp-")
        and value != "application"
    )


def fail(message: str, code: int = 1) -> int:
    print(f"experience_compile: {message}", file=sys.stderr)
    return code


def canonical(value: object) -> bytes:
    import experience_application_check
    if experience_application_check._json_contains_non_scalar(value):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n").encode()
    except RecursionError as exc:
        raise ValueError("JSON exceeds the canonical nesting depth") from exc


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON member: {key}")
        value[key] = item
    return value


def strict_json_loads(value: str) -> object:
    import experience_application_check
    experience_application_check._bounded_json_text(value)
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_json_object)
    except RecursionError as exc:
        raise ValueError("JSON exceeds the canonical nesting depth") from exc
    if experience_application_check._json_contains_non_scalar(decoded):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    return decoded


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def fsync_directory(path: Path) -> None:
    """Persist a directory entry update where the host supports it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_bytes(path, canonical(value))


def fsync_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            directories.append(path)
            continue
        if not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        except OSError:
            pass
    for directory in reversed(directories):
        fsync_directory(directory)


def transaction_project(root: Path) -> Path:
    if root.name != "experience-design":
        raise ValueError("Experience transaction root must end in experience-design")
    if root.parent.name == "docs" and root.parents[1].name == "workspace":
        return root.parents[2]
    return root.parent


def transaction_runtime(root: Path) -> Path:
    return (
        transaction_project(root)
        / ".agentrof" / "agent-marketplace" / ".runtime"
        / "experience-transactions"
    )


def transaction_map(root: Path) -> Path:
    return root.parent / "maps" / "experience-design.md"


def transaction_journal(root: Path) -> Path:
    return transaction_runtime(root) / "active.json"


def validate_mutation_surface(root: Path) -> None:
    """Reject links and special/shared inodes before any Experience write."""
    def raise_walk_error(error: OSError) -> None:
        raise error

    if root.exists() or root.is_symlink():
        root_stat = os.lstat(root)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError(
                "Experience root must be one regular, non-symlink directory"
            )
        for current, directory_names, file_names in os.walk(
            root, topdown=True, onerror=raise_walk_error, followlinks=False,
        ):
            current_path = Path(current)
            for name in [*directory_names, *file_names]:
                path = current_path / name
                path_stat = os.lstat(path)
                mode = path_stat.st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError(
                        f"Experience mutation surface contains a symlink: {path}"
                    )
                if name in directory_names:
                    if not stat.S_ISDIR(mode):
                        raise ValueError(
                            f"Experience mutation surface contains a non-directory: {path}"
                        )
                    continue
                if not stat.S_ISREG(mode) or path_stat.st_nlink != 1:
                    raise ValueError(
                        "Experience mutation files must be regular, non-hard-linked files: "
                        + str(path)
                    )

    map_path = transaction_map(root)
    if map_path.exists() or map_path.is_symlink():
        _regular_directory(
            map_path.parent, "Experience navigation owner",
        )
        map_stat = os.lstat(map_path)
        if (
            stat.S_ISLNK(map_stat.st_mode)
            or not stat.S_ISREG(map_stat.st_mode)
            or map_stat.st_nlink != 1
        ):
            raise ValueError(
                "Experience navigation map must be a regular, non-hard-linked file"
            )


def _reserved_path_alias(name: str, expected: str) -> bool:
    return unicodedata.normalize("NFC", name).casefold() == expected.casefold()


def _regular_directory(
    path: Path,
    label: str,
    *,
    reserved_name: str | None = None,
    allow_missing: bool = False,
) -> None:
    if reserved_name is not None:
        if (
            path.name != reserved_name
            or unicodedata.normalize("NFC", path.name) != reserved_name
        ):
            raise ValueError(
                f"{label} must use exact NFC spelling and case: {reserved_name}"
            )
        if path.parent.is_dir() and not path.parent.is_symlink():
            aliases = [
                child.name for child in path.parent.iterdir()
                if _reserved_path_alias(child.name, reserved_name)
            ]
            missing_without_alias = (
                allow_missing
                and not path.exists()
                and not path.is_symlink()
                and not aliases
            )
            if aliases != [reserved_name] and not missing_without_alias:
                raise ValueError(
                    f"{label} must resolve from one exact {reserved_name} directory"
                )
    if path.is_symlink():
        raise ValueError(f"{label} must be one regular, non-symlink directory")
    if allow_missing and not path.exists():
        return
    if not path.is_dir():
        raise ValueError(f"{label} must be one regular, non-symlink directory")


def _lexical_root(value: str | Path, *, allow_missing_root: bool) -> Path:
    """Resolve only after validating the lexical canonical-root identity."""
    raw = Path(value).expanduser()
    lexical = Path(os.path.abspath(os.fspath(raw)))
    name = unicodedata.normalize("NFC", lexical.name).casefold()
    chain: list[tuple[Path, str, str | None]]
    if name == "experience-design":
        candidate = lexical
        chain = [(candidate, "Experience root", "experience-design")]
        docs = candidate.parent
        if _reserved_path_alias(docs.name, "docs"):
            chain.insert(0, (docs, "Experience owner", "docs"))
            workspace = docs.parent
            if _reserved_path_alias(workspace.name, "workspace"):
                chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "docs":
        candidate = lexical / "experience-design"
        chain = [
            (lexical, "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
        workspace = lexical.parent
        if _reserved_path_alias(workspace.name, "workspace"):
            chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "workspace":
        candidate = lexical / "docs" / "experience-design"
        chain = [
            (lexical, "Experience workspace", "workspace"),
            (lexical / "docs", "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
    else:
        _regular_directory(lexical, "project root selector")
        workspace = lexical / "workspace"
        direct = lexical / "experience-design"
        if workspace.exists() or workspace.is_symlink():
            candidate = workspace / "docs" / "experience-design"
            chain = [
                (workspace, "Experience workspace", "workspace"),
                (workspace / "docs", "Experience owner", "docs"),
                (candidate, "Experience root", "experience-design"),
            ]
        elif direct.exists() or direct.is_symlink():
            candidate = direct
            chain = [(candidate, "Experience root", "experience-design")]
        elif allow_missing_root and (workspace / "docs").is_dir():
            candidate = workspace / "docs" / "experience-design"
            chain = [
                (workspace, "Experience workspace", "workspace"),
                (workspace / "docs", "Experience owner", "docs"),
                (candidate, "Experience root", "experience-design"),
            ]
        elif allow_missing_root:
            candidate = direct
            chain = [(candidate, "Experience root", "experience-design")]
        else:
            raise ValueError("--root must identify workspace/docs/experience-design")
    for path, label, reserved_name in chain:
        _regular_directory(
            path,
            label,
            reserved_name=reserved_name,
            allow_missing=allow_missing_root and path == candidate,
        )
    return candidate.resolve()


def command_experience_root(args) -> Path:
    package_value = getattr(args, "experience_root", "")
    if package_value:
        path = Path(os.path.abspath(os.fspath(Path(package_value).expanduser())))
        if path.name == "experience.md":
            path = path.parent
        packages_root = path.parent
        _regular_directory(
            packages_root,
            "Experience packages",
            reserved_name="experiences",
            allow_missing=True,
        )
        if path.is_symlink():
            raise ValueError(
                "Experience package must be one regular, non-symlink directory"
            )
        return _lexical_root(
            packages_root.parent, allow_missing_root=True,
        )
    return _lexical_root(
        getattr(args, "root", ""), allow_missing_root=True,
    )


def _lock_file(handle) -> None:
    deadline = time.monotonic() + TRANSACTION_TIMEOUT_SECONDS
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "timed out waiting for the project Experience transaction lock"
                    )
                time.sleep(0.05)
    else:
        import fcntl
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "timed out waiting for the project Experience transaction lock"
                    )
                time.sleep(0.05)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def project_transaction_lock(root: Path):
    runtime = transaction_runtime(root)
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / "project.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _transaction_payload(root: Path, transaction_id: str, command: str,
                         root_existed: bool, map_existed: bool) -> dict:
    return {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "command": command,
        "root": str(root),
        "root_existed": root_existed,
        "map_path": str(transaction_map(root)),
        "map_existed": map_existed,
        "phase": "prepared",
    }


def read_transaction_journal(root: Path) -> dict | None:
    journal = transaction_journal(root)
    if not journal.exists():
        return None
    if not journal.is_file() or journal.is_symlink():
        raise ValueError("Experience transaction journal must be a regular file")
    try:
        value = strict_json_loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Experience transaction journal is unreadable: {exc}") from exc
    expected_keys = {
        "schema_version", "transaction_id", "command", "root",
        "root_existed", "map_path", "map_existed", "phase",
    }
    transaction_id = value.get("transaction_id") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or not isinstance(transaction_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", transaction_id)
        or value.get("command") not in MUTATING_COMMANDS
        or value.get("root") != str(root)
        or type(value.get("root_existed")) is not bool
        or value.get("map_path") != str(transaction_map(root))
        or type(value.get("map_existed")) is not bool
        or value.get("phase") != "prepared"
    ):
        raise ValueError("Experience transaction journal has an invalid exact schema")
    return value


def transaction_backup(root: Path, transaction_id: str) -> Path:
    return transaction_runtime(root) / "backups" / transaction_id


def _remove_exact_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def restore_transaction(root: Path, journal: dict) -> None:
    transaction_id = str(journal["transaction_id"])
    backup = transaction_backup(root, transaction_id)
    root_backup = backup / "experience-design"
    map_backup = backup / "experience-design.md"
    if not backup.is_dir() or backup.is_symlink():
        raise ValueError("Experience transaction backup is missing")
    if journal["root_existed"] and (
        not root_backup.is_dir() or root_backup.is_symlink()
    ):
        raise ValueError("Experience transaction root backup is missing")
    if journal["map_existed"] and (
        not map_backup.is_file() or map_backup.is_symlink()
    ):
        raise ValueError("Experience transaction map backup is missing")

    _remove_exact_path(root)
    if journal["root_existed"]:
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root_backup, root, symlinks=True)
        fsync_tree(root)
        fsync_directory(root.parent)

    map_path = transaction_map(root)
    if journal["map_existed"]:
        atomic_write_bytes(map_path, map_backup.read_bytes())
    else:
        _remove_exact_path(map_path)
        fsync_directory(map_path.parent)


def cleanup_transaction(root: Path, transaction_id: str) -> None:
    journal = transaction_journal(root)
    journal.unlink(missing_ok=True)
    fsync_directory(journal.parent)
    backup = transaction_backup(root, transaction_id)
    if backup.is_dir() and not backup.is_symlink():
        shutil.rmtree(backup)
    backups = backup.parent
    if backups.is_dir() and not any(backups.iterdir()):
        backups.rmdir()


def recover_transaction(root: Path) -> bool:
    journal = read_transaction_journal(root)
    if journal is None:
        return False
    restore_transaction(root, journal)
    cleanup_transaction(root, str(journal["transaction_id"]))
    return True


def begin_transaction(root: Path, command: str) -> str:
    if read_transaction_journal(root) is not None:
        raise ValueError("an unrecovered Experience transaction already exists")
    transaction_id = uuid.uuid4().hex
    backup = transaction_backup(root, transaction_id)
    backup.mkdir(parents=True, exist_ok=False)
    try:
        root_existed = root.exists()
        if root_existed:
            if not root.is_dir() or root.is_symlink():
                raise ValueError("Experience transaction root must be a regular directory")
            shutil.copytree(root, backup / "experience-design", symlinks=True)
        map_path = transaction_map(root)
        map_existed = map_path.exists()
        if map_existed:
            if not map_path.is_file() or map_path.is_symlink():
                raise ValueError("Experience navigation map must be a regular file")
            shutil.copy2(map_path, backup / "experience-design.md")
        fsync_tree(backup)
        atomic_write_json(
            transaction_journal(root),
            _transaction_payload(
                root, transaction_id, command, root_existed, map_existed,
            ),
        )
    except BaseException:
        if transaction_journal(root).exists():
            raise
        shutil.rmtree(backup, ignore_errors=True)
        raise
    return transaction_id


def rollback_transaction(root: Path, transaction_id: str) -> None:
    journal = read_transaction_journal(root)
    if journal is None or journal.get("transaction_id") != transaction_id:
        raise ValueError("Experience transaction ownership changed before rollback")
    restore_transaction(root, journal)
    cleanup_transaction(root, transaction_id)


def commit_transaction(root: Path, transaction_id: str) -> None:
    journal = read_transaction_journal(root)
    if journal is None or journal.get("transaction_id") != transaction_id:
        raise ValueError("Experience transaction ownership changed before commit")
    cleanup_transaction(root, transaction_id)


def fm(path: Path) -> tuple[dict, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing frontmatter")
    data, current, end = {}, "", -1
    for number, raw in enumerate(lines[1:], 1):
        line = raw.strip()
        if line == "---":
            end = number
            break
        if not line:
            continue
        if line.startswith("- ") and current:
            data.setdefault(current, []).append(line[2:].strip().strip("\"'"))
            continue
        if ":" not in line:
            raise ValueError(f"unparseable frontmatter line {number + 1}")
        key, value = (part.strip() for part in line.split(":", 1))
        if not value:
            data[key], current = [], key
        else:
            current = ""
            if value.startswith(("[", "{")):
                try:
                    data[key] = strict_json_loads(value)
                except (json.JSONDecodeError, ValueError):
                    data[key] = value.strip("\"'")
            elif value.isdigit():
                data[key] = int(value)
            else:
                data[key] = value.strip("\"'")
    if end < 0:
        raise ValueError("unterminated frontmatter")
    return data, "\n".join(lines[end + 1:]).strip() + "\n"


def render_fm(data: dict, body: str) -> str:
    rows = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {json.dumps(item, ensure_ascii=False)}" for item in value)
        elif isinstance(value, dict):
            rows.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            rows.append(f"{key}: {value}")
    return "\n".join(rows + ["---", "", body.rstrip(), ""])


def status_tags(data: dict) -> None:
    tags = [f"doc/{data['type']}"]
    if data.get("status"):
        tags.append("status/" + str(data["status"]).replace("_", "-"))
    data["tags"] = tags


def write(path: Path, data: dict, title: str, content: str, nav: str) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data.setdefault("title", title)
    status_tags(data)
    body = (f"# {title}\n\n{content.strip()}\n\n## Navigation <!-- sec: nav -->\n\n"
            f"[[{nav}|{nav.rsplit('/', 1)[-1].replace('-', ' ').title()}]]\n")
    atomic_write_bytes(path, render_fm(data, body).encode())


def rewrite(path: Path, data: dict, body: str) -> None:
    atomic_write_bytes(path, render_fm(data, body).encode())


def root_for(value: str | Path) -> Path:
    return _lexical_root(value, allow_missing_root=False)


def package_for(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    path = Path(os.path.abspath(os.fspath(raw)))
    if path.name == "experience.md":
        path = path.parent
    packages_root = path.parent
    _regular_directory(
        packages_root,
        "Experience packages",
        reserved_name="experiences",
    )
    root = root_for(packages_root.parent)
    _regular_directory(path, "Experience package")
    note = path / "experience.md"
    if note.is_symlink() or not note.is_file():
        raise ValueError("--experience-root must identify experiences/<slug>")
    resolved = path.resolve()
    if resolved.parent != root / "experiences":
        raise ValueError("--experience-root must identify experiences/<slug>")
    return resolved


def docs_for(package: Path) -> Path:
    if package.parents[2].name != "docs":
        raise ValueError("Experience package is not below workspace/docs")
    return package.parents[2]


def packages(root: Path) -> list[Path]:
    parent = root / "experiences"
    _regular_directory(
        parent,
        "Experience packages",
        reserved_name="experiences",
        allow_missing=True,
    )
    if not parent.exists():
        return []
    result = []
    for item in parent.iterdir():
        if item.is_symlink():
            raise ValueError(
                "Experience package entries must be regular, non-symlink directories"
            )
        if item.is_dir():
            result.append(item)
    return sorted(result, key=lambda item: item.name)


def fields(package: Path) -> dict:
    try:
        return fm(package / "experience.md")[0]
    except (OSError, ValueError):
        return {}


def list_value(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    return [str(item) for item in value] if isinstance(value, list) else ([str(value)] if value else [])


def ref_value(value: str) -> str:
    value = value.strip()
    if value.startswith("[[") and value.endswith("]]" ):
        return value[2:-2].split("|", 1)[0].split("#", 1)[0]
    return value.removesuffix(".md")


def bindings(data: dict) -> dict[str, tuple[str, str]]:
    result = {}
    for entry in list_value(data, "input_bindings"):
        stage, marker, rest = entry.partition("|")
        reference, marker2, digest = rest.partition("|")
        if marker and marker2:
            result[stage] = (reference, digest)
    return result


def binding_rows(receipts: list[dict]) -> list[str]:
    return [f"{item['stage']}|{item['result_ref']}|{item['package_hash']}" for item in receipts]


def requirement_upstream_hash(results: dict[str, list[tuple[str, str]]]) -> str:
    """Hash only the receipts that form an Experience package input.

    The Experience receipt is written after approval.  Including that receipt
    in its own input hash creates a self-invalidating package.
    """
    upstream = {
        stage: sorted(results.get(stage, []))
        for stage in ("business-analysis", "solution-design", "design-system")
    }
    return sha(canonical(upstream))


def proposal_digest(plan: dict) -> str:
    stable = {key: value for key, value in plan.items() if key != "proposal_hash"}
    return sha(canonical(stable))


def input_rows(plan: dict) -> list[tuple[str, str, str]]:
    rows = []
    for value in plan.get("input_bindings", []):
        stage, marker, remainder = str(value).partition("|")
        reference, marker2, digest = remainder.partition("|")
        if not marker or not marker2:
            raise ValueError("scope plan contains an invalid input binding")
        rows.append((stage, reference, digest))
    return rows


def exact_package_preimage(value: object, *, create: bool) -> bool:
    if create:
        return value == {}
    return (
        type(value) is dict
        and set(value) == {"status", "revision", "source_hash"}
        and value.get("status") in {
            "draft", "in_review", "approved", "retirement_pending",
        }
        and type(value.get("revision")) is int
        and value["revision"] > 0
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(value.get("source_hash", "")),
        ) is not None
    )


def exact_application_preimage(value: object) -> bool:
    if value == {"exists": False}:
        return type(value.get("exists")) is bool
    expected_keys = {
        "exists", "status", "revision", "source_hash", "package_set_hash",
        "coverage_hash", "application_hash", "runtime_sha256",
        "design_system_package_hash",
    }
    return (
        type(value) is dict
        and set(value) == expected_keys
        and value.get("exists") is True
        and value.get("status") == "approved"
        and type(value.get("revision")) is int
        and value["revision"] > 0
        and all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get(key, "")))
            is not None
            for key in expected_keys - {"exists", "status", "revision"}
        )
    )


def exact_scope_plan(plan: object) -> bool:
    if type(plan) is not dict:
        return False
    base_keys = {
        "schema_version", "origin_mode", "input_bindings", "actions",
        "application_action", "expected_application", "proposal_hash",
    }
    origin_mode = plan.get("origin_mode")
    expected_keys = set(base_keys)
    if origin_mode == "requirement":
        expected_keys.update({
            "requirement", "requirement_semantic_hash",
            "upstream_stage_receipts_hash",
        })
    if (
        set(plan) != expected_keys
        or type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != 2
        or origin_mode not in {"manual", "requirement"}
        or type(plan.get("input_bindings")) is not list
        or any(type(value) is not str for value in plan["input_bindings"])
        or type(plan.get("actions")) is not list
        or plan.get("application_action") not in {"create", "update", "reuse"}
        or not exact_application_preimage(plan.get("expected_application"))
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(plan.get("proposal_hash", "")),
        ) is None
    ):
        return False
    if (
        plan["application_action"] == "create"
        and plan["expected_application"] != {"exists": False}
    ) or (
        plan["application_action"] in {"update", "reuse"}
        and plan["expected_application"].get("exists") is not True
    ):
        return False
    if origin_mode == "requirement" and (
        re.fullmatch(r"REQ-[0-9]{3,}", str(plan.get("requirement", ""))) is None
        or any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(plan.get(key, "")))
            is None
            for key in (
                "requirement_semantic_hash", "upstream_stage_receipts_hash",
            )
        )
    ):
        return False
    action_keys = {
        "primary_process_ref", "experience", "target_experience", "action",
        "affected_records", "expected_package", "reason",
    }
    for action in plan["actions"]:
        if (
            type(action) is not dict
            or set(action) != action_keys
            or action.get("action") not in {
                "create", "update", "reuse", "rename", "retire",
            }
            or type(action.get("experience")) is not str
            or not valid_experience_slug(action["experience"])
            or type(action.get("target_experience")) is not str
            or (
                action.get("target_experience")
                and not valid_experience_slug(action["target_experience"])
            )
            or type(action.get("primary_process_ref")) is not str
            or not action["primary_process_ref"]
            or type(action.get("affected_records")) is not list
            or any(type(value) is not str for value in action["affected_records"])
            or len(action["affected_records"])
            != len(set(action["affected_records"]))
            or type(action.get("reason")) is not str
            or not exact_package_preimage(
                action.get("expected_package"),
                create=action.get("action") == "create",
            )
        ):
            return False
    return True


def load_scope_plan(path_value: str, provided_hash: str) -> dict:
    if not path_value:
        raise ValueError("mutation requires --scope-plan produced by propose")
    try:
        plan = strict_json_loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"scope plan is unreadable: {exc}") from exc
    if not exact_scope_plan(plan):
        raise ValueError("scope plan has an unsupported schema")
    actual = proposal_digest(plan)
    if not provided_hash or provided_hash != actual or plan.get("proposal_hash") != actual:
        raise ValueError("scope plan hash does not match the approved proposal")
    actions = plan.get("actions")
    application_only = (
        isinstance(actions, list)
        and not actions
        and plan.get("application_action") == "update"
        and isinstance(plan.get("expected_application"), dict)
        and plan["expected_application"].get("exists") is True
    )
    if not isinstance(actions, list) or (not actions and not application_only):
        raise ValueError("scope plan needs package actions or an application-only update")
    return plan


def verify_scope_inputs(root: Path, plan: dict, *, require_committed: bool) -> list[str]:
    errors = []
    expected = {"business-analysis", "solution-design", "design-system"}
    try:
        rows = input_rows(plan)
    except ValueError as exc:
        return [str(exc)]
    present = {stage for stage, _reference, _digest in rows}
    if present != expected or len(rows) != len(expected):
        return ["scope plan must pin exactly one Business Analysis, Solution and Design input"]
    for stage, reference, digest in rows:
        _receipt, findings = stage_package.verify(
            root.parent, stage, reference, digest, require_committed,
            require_strict_current=True,
        )
        errors.extend(findings)
    if plan.get("origin_mode") == "requirement":
        requirement = str(plan.get("requirement", ""))
        try:
            import requirement_compile, requirement_route
            matches = [path for path in requirement_compile.requirement_paths(root.parent)
                       if requirement_compile.split_note(path)[0].get("id") == requirement]
            if len(matches) != 1:
                errors.append("scope plan Requirement is not uniquely resolvable")
            else:
                props, body = requirement_compile.split_note(matches[0])
                semantic = requirement_compile.semantic_hash(props, body)
                upstream = requirement_upstream_hash(requirement_compile.stage_results(body))
                if props.get("status") != "approved" or semantic != plan.get("requirement_semantic_hash"):
                    errors.append("scope plan Requirement semantic state is stale")
                if upstream != plan.get("upstream_stage_receipts_hash"):
                    errors.append("scope plan Requirement upstream receipt set is stale")
                route = requirement_route.route(root.parent, requirement)
                if route.get("stage") != "experience-design" or route.get("action") != "author":
                    errors.append("scope plan Requirement is no longer ready to author Experience Design")
        except (ImportError, OSError, ValueError):
            errors.append("scope plan Requirement cannot be verified")
    return errors


def action_for_plan(root: Path, plan: dict, *, action: str, experience: str,
                    process: str, target: str = "", validate_current: bool = True) -> dict:
    matches = [row for row in plan["actions"] if isinstance(row, dict)
               and row.get("action") == action
               and row.get("experience") == experience
               and row.get("primary_process_ref") == process
               and (not target or row.get("target_experience", "") == target)]
    if len(matches) != 1:
        raise ValueError("mutation is not one of the approved scope-plan actions")
    selected = matches[0]
    expected = selected.get("expected_package", {})
    if action == "create":
        if expected != {}:
            raise ValueError("scope-plan create action has an invalid package preimage")
    elif (
        not isinstance(expected, dict)
        or set(expected) != {"status", "revision", "source_hash"}
        or type(expected.get("revision")) is not int
        or expected["revision"] < 1
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(expected.get("source_hash", "")))
    ):
        raise ValueError("scope-plan package preimage is incomplete")
    if action in {"update", "retire"} and expected.get("status") != "approved":
        raise ValueError(f"scope-plan {action} action must start from an approved package")
    if action == "rename" and expected.get("status") not in {
        "approved", "draft", "in_review",
    }:
        raise ValueError("scope-plan rename action has an invalid lifecycle phase")
    if validate_current:
        package = root / "experiences" / experience
        if action == "create":
            if package.exists():
                raise ValueError("scope-plan create target is no longer absent")
        elif not isinstance(expected, dict) or not package.is_dir():
            raise ValueError("scope-plan target package is missing")
        elif (expected.get("revision") != fields(package).get("revision")
              or expected.get("status") != fields(package).get("status")
              or expected.get("source_hash") != source_digest(package)):
            raise ValueError("scope-plan target package changed after proposal")
    return selected


def action_target(action: dict) -> str:
    return str(action.get("target_experience") or action.get("experience") or "")


def opened_package_revision(action: dict) -> int:
    kind = str(action.get("action", ""))
    if kind == "create":
        return 1
    expected = action.get("expected_package", {})
    revision = expected.get("revision") if isinstance(expected, dict) else None
    if type(revision) is not int or revision < 1:
        raise ValueError("scope-plan package preimage has an invalid revision")
    if kind in {"update", "retire"} or expected.get("status") == "approved":
        return revision + 1
    return revision


def open_revision_payload(plan: dict, action: dict, proposal_hash: str) -> dict:
    return {
        "schema_version": 1,
        "proposal_hash": proposal_hash,
        "action": str(action.get("action", "")),
        "source_experience": str(action.get("experience", "")),
        "target_experience": action_target(action),
        "primary_process_ref": str(action.get("primary_process_ref", "")),
        "origin_mode": str(plan.get("origin_mode", "")),
        "expected_package": action.get("expected_package", {}),
        "opened_revision": opened_package_revision(action),
    }


def write_open_revision(
    package: Path, plan: dict, action: dict, proposal_hash: str
) -> None:
    target = package / GENERATED / OPEN_REVISION
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        target, canonical(open_revision_payload(plan, action, proposal_hash)),
    )


def read_open_revision(package: Path) -> dict:
    target = package / GENERATED / OPEN_REVISION
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"{package.name} is missing compiler-owned open revision state")
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{package.name} open revision state is unreadable: {exc}") from exc
    expected_keys = {
        "schema_version", "proposal_hash", "action", "source_experience",
        "target_experience", "primary_process_ref", "origin_mode",
        "expected_package", "opened_revision",
    }
    action = value.get("action") if isinstance(value, dict) else None
    if (
        type(value) is not dict
        or set(value) != expected_keys
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(value.get("proposal_hash", "")),
        ) is None
        or action not in {"create", "update", "rename", "retire"}
        or not valid_experience_slug(str(value.get("source_experience", "")))
        or not valid_experience_slug(str(value.get("target_experience", "")))
        or type(value.get("primary_process_ref")) is not str
        or not value["primary_process_ref"]
        or value.get("origin_mode") not in {"manual", "requirement"}
        or not exact_package_preimage(
            value.get("expected_package"), create=action == "create",
        )
        or type(value.get("opened_revision")) is not int
        or value["opened_revision"] < 1
    ):
        raise ValueError(
            f"{package.name} open revision state has an invalid exact schema"
        )
    return value


def validate_open_revision(
    package: Path,
    plan: dict,
    action: dict,
    proposal_hash: str,
    *,
    expected_status: str,
) -> None:
    expected = open_revision_payload(plan, action, proposal_hash)
    if read_open_revision(package) != expected:
        raise ValueError(
            f"{package.name} open revision is not bound to the approved scope-plan action"
        )
    data = fields(package)
    if (
        package.name != expected["target_experience"]
        or data.get("experience_id") != expected["target_experience"]
        or data.get("primary_process_ref") != expected["primary_process_ref"]
        or data.get("origin_mode") != expected["origin_mode"]
        or data.get("status") != expected_status
        or data.get("revision") != expected["opened_revision"]
    ):
        raise ValueError(
            f"{package.name} lifecycle identity, phase or successor revision drifted after opening"
        )


def validate_open_revision_identity(package: Path, *, expected_status: str) -> dict:
    state = read_open_revision(package)
    expected_keys = {
        "schema_version", "proposal_hash", "action", "source_experience",
        "target_experience", "primary_process_ref", "origin_mode",
        "expected_package", "opened_revision",
    }
    data = fields(package)
    if (
        set(state) != expected_keys
        or type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(state.get("proposal_hash", "")))
        or package.name != state.get("target_experience")
        or data.get("experience_id") != state.get("target_experience")
        or data.get("primary_process_ref") != state.get("primary_process_ref")
        or data.get("origin_mode") != state.get("origin_mode")
        or data.get("status") != expected_status
        or data.get("revision") != state.get("opened_revision")
    ):
        raise ValueError(
            f"{package.name} compiler-owned open revision state is stale or tampered"
        )
    return state


def opened_application_revision(plan: dict) -> int:
    action = str(plan.get("application_action", ""))
    expected = plan.get("expected_application")
    if not isinstance(expected, dict):
        raise ValueError("scope plan is missing the application preimage")
    if action == "create":
        if expected != {"exists": False}:
            raise ValueError("application create has an invalid absent preimage")
        return 1
    if action != "update" or expected.get("exists") is not True:
        raise ValueError("application update has an invalid approved preimage")
    revision = expected.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("application preimage revision must be a positive integer")
    return revision + 1


def open_application_payload(
    plan: dict, proposal_hash: str, *, phase: str,
) -> dict:
    if phase not in {"draft", "in_review"}:
        raise ValueError("open application phase must be draft or in_review")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise ValueError("scope plan application action set is invalid")
    return {
        "schema_version": 1,
        "proposal_hash": proposal_hash,
        "application_action": str(plan.get("application_action", "")),
        "package_actions_hash": sha(canonical(actions)),
        "expected_application": plan.get("expected_application"),
        "opened_revision": opened_application_revision(plan),
        "phase": phase,
    }


def open_application_state_path(root: Path) -> Path:
    return root / GENERATED / OPEN_APPLICATION_REVISION


def write_open_application_state(
    root: Path, plan: dict, proposal_hash: str, *, phase: str,
) -> None:
    atomic_write_json(
        open_application_state_path(root),
        open_application_payload(plan, proposal_hash, phase=phase),
    )


def read_open_application_state(root: Path) -> dict:
    target = open_application_state_path(root)
    if not target.is_file() or target.is_symlink():
        raise ValueError("application is missing compiler-owned open revision state")
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"application open revision state is unreadable: {exc}") from exc
    expected_keys = {
        "schema_version", "proposal_hash", "application_action",
        "package_actions_hash", "expected_application", "opened_revision",
        "phase",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(value.get("proposal_hash", "")),
        )
        or value.get("application_action") not in {"create", "update"}
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(value.get("package_actions_hash", "")),
        )
        or not exact_application_preimage(value.get("expected_application"))
        or type(value.get("opened_revision")) is not int
        or int(value.get("opened_revision", 0)) < 1
        or value.get("phase") not in {"draft", "in_review"}
    ):
        raise ValueError("application open revision state has an invalid exact schema")
    return value


def validate_application_predecessor(root: Path, expected: dict) -> None:
    import experience_application_check
    if expected == {"exists": False}:
        if (root / experience_application_check.LEDGER_RELATIVE).exists():
            raise ValueError("application create has stale compiler-owned receipt state")
        return
    if expected.get("exists") is not True or expected.get("status") != "approved":
        raise ValueError("application predecessor is not an approved receipt")
    rows, findings = experience_application_check.verified_application_ledger(root)
    if findings:
        raise ValueError("application predecessor ledger is invalid: " + "; ".join(findings))
    revision = expected.get("revision")
    if type(revision) is not int or len(rows) != revision:
        raise ValueError("application predecessor ledger revision is stale")
    predecessor = rows[-1] if rows else None
    if not isinstance(predecessor, dict):
        raise ValueError("application predecessor receipt is missing")
    actual = {
        "exists": True,
        "status": "approved",
        "revision": predecessor.get("application_revision"),
        "source_hash": predecessor.get("source_hash"),
        "package_set_hash": predecessor.get("package_set_hash"),
        "coverage_hash": predecessor.get("coverage_hash"),
        "application_hash": predecessor.get("application_hash"),
        "runtime_sha256": predecessor.get("runtime_sha256"),
        "design_system_package_hash": predecessor.get(
            "design_system", {},
        ).get("package_hash"),
    }
    if actual != expected:
        raise ValueError("application predecessor receipt differs from the scope plan")


def validate_open_application_state(
    root: Path,
    *,
    plan: dict | None = None,
    proposal_hash: str = "",
    expected_phase: str,
) -> dict:
    state = read_open_application_state(root)
    if plan is not None:
        expected = open_application_payload(
            plan, proposal_hash, phase=expected_phase,
        )
        if state != expected:
            raise ValueError(
                "application open revision is not bound to the approved scope-plan action"
            )
    elif state.get("phase") != expected_phase:
        raise ValueError("application compiler-owned lifecycle phase is stale")
    validate_application_predecessor(root, state["expected_application"])
    meta = application_metadata(root)
    if (
        meta.get("experience-application-status") != expected_phase
        or meta.get("experience-application-proposal-hash")
        != state.get("proposal_hash")
        or meta.get("experience-application-revision")
        != str(state.get("opened_revision"))
    ):
        raise ValueError(
            "application lifecycle identity, phase or successor revision drifted after opening"
        )
    return state


def package_record_ids(package: Path) -> list[str]:
    return sorted(str(row.get("id", "")) for row in records(package, [])
                  if str(row.get("id", "")))


def external_active_record(root: Path, source: Path, reference: str,
                           related_processes: set[str], *, gate: bool) -> bool:
    """Resolve a cross-Experience ref directly from its owning record.

    Direct record inspection avoids a generated-registry ordering dependency,
    so two packages in one approved action set may reference each other. The
    aggregate application gate still compiles every package before approval.
    """
    match = EXACT.fullmatch(reference)
    if match is None:
        return False
    target = resolve_package(root, match.group(1))
    if target is None or target == source:
        return False
    if match.group(1) != target.name:
        return False
    target_data = fields(target)
    status = target_data.get("status")
    if (status not in {"approved", "draft", "in_review"}
            or (gate and status != "approved")
            or str(target_data.get("primary_process_ref", "")) not in related_processes
            or (status == "approved"
                and target_data.get("source_hash") != source_digest(target))):
        return False
    record_findings: list[str] = []
    target_records = records(target, record_findings)
    if record_findings:
        return False
    ident, revision = reference.split(":", 1)[1].split("@", 1)
    return any(row.get("id") == ident and row.get("revision") == int(revision[1:])
               and row.get("record_state") == "active"
               for row in target_records)


def authored(package: Path) -> list[Path]:
    return sorted(path for path in package.rglob("*.md") if GENERATED not in path.parts and LEDGER not in path.parts)


def source_digest(package: Path, *, historical_revision: int | None = None) -> str:
    ignored = {"status", "approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc", "tags"}
    digest = hashlib.sha256()
    for path in authored(package):
        data, body = fm(path)
        stable = {key: value for key, value in data.items() if key not in ignored}
        if historical_revision is not None and path == package / "experience.md":
            stable["revision"] = historical_revision
            stable.pop("retired_at_utc", None)
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(render_fm(stable, without_generated_relations(body)).encode())
        digest.update(b"\0")
    application_map = package / "artifacts" / "application-map.json"
    if application_map.is_file():
        digest.update(b"artifacts/application-map.json\0")
        try:
            digest.update(canonical(strict_json_loads(
                application_map.read_text(encoding="utf-8")
            )))
        except (json.JSONDecodeError, ValueError):
            digest.update(application_map.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def read_process_ledger(package: Path) -> tuple[list[dict], list[str]]:
    target = package / LEDGER / "package-revisions.json"
    if not target.exists():
        return [], []
    if not target.is_file() or target.is_symlink():
        return [], ["_ledger/package-revisions.json must be a regular file"]
    try:
        data = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [], [f"_ledger/package-revisions.json is unreadable: {exc}"]
    if not isinstance(data, dict) or set(data) != {"revisions"}:
        return [], ["_ledger/package-revisions.json must contain only revisions"]
    revisions = data.get("revisions")
    if not isinstance(revisions, list) or any(
        not isinstance(row, dict) for row in revisions
    ):
        return [], ["_ledger/package-revisions.json revisions must be objects"]
    return revisions, []


def ledger(package: Path) -> list[dict]:
    return read_process_ledger(package)[0]


def validate_process_ledger(
    package: Path, current_revision: int
) -> tuple[list[dict], list[str]]:
    rows, findings = read_process_ledger(package)
    if type(current_revision) is not int or current_revision < 1:
        return rows, sorted(set([
            *findings, "experience.md: revision must be a positive integer",
        ]))
    try:
        revisions = [row.get("package_revision") for row in rows]
    except AttributeError:
        revisions = []
    if revisions != list(range(1, current_revision)):
        findings.append(
            "_ledger/package-revisions.json must contain one ordered, contiguous approved registry before the current revision"
        )
    for row in rows:
        if (
            set(row) != PROCESS_REGISTRY_FIELDS
            or type(row.get("schema_version")) is not int
            or row.get("schema_version") != 5
            or type(row.get("package_revision")) is not int
            or int(row.get("package_revision", 0)) < 1
            or not valid_experience_slug(str(row.get("experience_id", "")))
            or row.get("origin_mode") not in {"manual", "requirement"}
            or not isinstance(row.get("implements"), list)
            or not isinstance(row.get("related_process_refs"), list)
            or not isinstance(row.get("input_bindings"), dict)
            or not isinstance(row.get("records"), list)
            or not isinstance(row.get("application_map"), dict)
        ):
            findings.append(
                "_ledger/package-revisions.json contains an invalid process registry schema"
            )
            continue
        unsigned = {
            key: value for key, value in row.items()
            if key not in {"source_hash", "registry_hash", "package_hash"}
        }
        registry_hash = sha(canonical(unsigned))
        package_hash = sha(canonical({
            "source_hash": row.get("source_hash"),
            "registry_hash": registry_hash,
        }))
        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("source_hash", "")))
            or row.get("registry_hash") != registry_hash
            or row.get("package_hash") != package_hash
        ):
            findings.append(
                "_ledger/package-revisions.json contains a tampered process registry hash"
            )
        record_rows = row.get("records")
        if not isinstance(record_rows, list):
            findings.append(
                "_ledger/package-revisions.json contains an invalid record snapshot set"
            )
            continue
        for record in record_rows:
            if not isinstance(record, dict):
                findings.append(
                    "_ledger/package-revisions.json contains an invalid record snapshot"
                )
                continue
            ident, revision = record.get("id"), record.get("revision")
            if (
                not isinstance(ident, str)
                or not re.fullmatch(r"(?:JRN|FLW|SCR|STA|TRN)-[0-9]{3,}", ident)
                or type(revision) is not int
                or revision < 1
            ):
                findings.append(
                    "_ledger/package-revisions.json contains an invalid record snapshot identity"
                )
                continue
            snapshot = snapshots(package, ident, revision)
            if snapshot != record:
                findings.append(
                    f"_ledger/records/{ident}/r{revision}.json is missing or stale"
                )
    if rows:
        import experience_application_check
        application_rows, application_findings = (
            experience_application_check.verified_application_ledger(
                package.parent.parent
            )
        )
        if application_findings:
            findings.append(
                "_ledger/package-revisions.json cannot verify its application-ledger anchors: "
                + "; ".join(application_findings)
            )
        else:
            published = {
                (
                    str(receipt.get("result_ref", "")),
                    str(receipt.get("package_hash", "")),
                )
                for application_row in application_rows
                for receipt in application_row.get("packages", [])
                if isinstance(receipt, dict)
            }
            for row in rows:
                identity = (
                    f"{row.get('experience_id')}@r{row.get('package_revision')}",
                    str(row.get("package_hash", "")),
                )
                if identity not in published:
                    findings.append(
                        "_ledger/package-revisions.json contains a process receipt not anchored by the immutable application ledger"
                    )
    return rows, sorted(set(findings))


def write_ledger(package: Path, rows: list[dict]) -> None:
    target = package / LEDGER / "package-revisions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        target,
        canonical({"revisions": sorted(
            rows, key=lambda row: int(row.get("package_revision", 0) or 0),
        )}),
    )


def archive_process_registry(package: Path, registry: dict) -> None:
    current_revision = int(registry.get("package_revision", 0) or 0)
    history, problems = validate_process_ledger(package, current_revision)
    if problems:
        raise ValueError("; ".join(problems))
    write_ledger(package, [*history, registry])
    for row in registry.get("records", []):
        path = (
            package / LEDGER / "records" / str(row.get("id", ""))
            / f"r{row.get('revision')}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                current = strict_json_loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"existing record snapshot is unreadable: {path}: {exc}") from exc
            if current != row:
                raise ValueError(f"existing record snapshot is stale or tampered: {path}")
        else:
            atomic_write_bytes(path, canonical(row))


def package_aliases(package: Path) -> set[str]:
    result = {package.name, str(fields(package).get("experience_id", ""))}
    result.update(list_value(fields(package), "aliases"))
    target = package / LEDGER / "aliases.json"
    if target.exists():
        if not target.is_file() or target.is_symlink():
            raise ValueError("_ledger/aliases.json must be a regular file")
        try:
            value = strict_json_loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"_ledger/aliases.json is unreadable: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("aliases"), dict):
            raise ValueError("_ledger/aliases.json has an invalid schema")
        result.update(value["aliases"].keys())
    return {item for item in result if item}


def resolve_package(root: Path, identifier: str) -> Path | None:
    candidates = [package for package in packages(root) if identifier in package_aliases(package)]
    return candidates[0] if len(candidates) == 1 else None


def snapshots(package: Path, ident: str, revision: int) -> dict | None:
    path = package / LEDGER / "records" / ident / f"r{revision}.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def records(package: Path, findings: list[str]) -> list[dict]:
    result, ids = [], set()
    for kind, (directory, prefix, suffix) in KIND.items():
        directory_path = package / directory
        for path in sorted(directory_path.glob(f"*{suffix}")) if directory_path.is_dir() else []:
            rel = path.relative_to(package).as_posix()
            try:
                data, _body = fm(path)
            except (OSError, ValueError) as exc:
                findings.append(f"{rel}: {exc}"); continue
            ident, revision = str(data.get("id", "")), data.get("revision")
            if data.get("type") != kind or not re.fullmatch(prefix + r"-[0-9]{3,}", ident):
                findings.append(f"{rel}: invalid {kind} identity")
            if type(revision) is not int or revision < 1:
                findings.append(f"{rel}: revision must be a positive integer")
            if ident in ids:
                findings.append(f"{rel}: duplicate package record id {ident}")
            ids.add(ident)
            if data.get("record_state") not in {"active", "retired"}:
                findings.append(f"{rel}: record_state must be active or retired")
            if any(key in data for key in ("status", "approved_at_utc", "approval_hash")):
                findings.append(f"{rel}: child records cannot carry approval state")
            if data.get("record_state") == "active" and not list_value(data, "derives_from"):
                findings.append(f"{rel}: active record needs derives_from")
            if kind == "screen" and data.get("record_state") == "active" and not list_value(data, "uses_design"):
                findings.append(f"{rel}: active screen needs uses_design")
            if (
                kind == "state"
                and data.get("record_state") == "active"
                and data.get("state_class") not in STATE_CLASSES
            ):
                findings.append(
                    f"{rel}: active state needs a canonical state_class"
                )
            elif (
                kind == "state"
                and data.get("state_class") is not None
                and data.get("state_class") not in STATE_CLASSES
            ):
                findings.append(f"{rel}: state_class is not canonical")
            result.append({**data, "path": rel})
    return sorted(result, key=lambda row: (str(row.get("id", "")), int(row.get("revision", 0) or 0)))


def reverse_reference_packages(root: Path, target: Path) -> list[Path]:
    target_aliases = package_aliases(target)
    result: list[Path] = []
    for package in packages(root):
        if package == target or fields(package).get("status") == "retired":
            continue
        record_findings: list[str] = []
        rows = records(package, record_findings)
        if record_findings:
            raise ValueError(
                f"cannot inspect reverse Experience references for {package.name}: "
                + "; ".join(record_findings)
            )
        if any(
            (match := EXACT.fullmatch(reference)) is not None
            and match.group(1) in target_aliases
            for row in rows
            for field in REFERENCE_FIELDS
            for reference in list_value(row, field)
        ):
            result.append(package)
    return sorted(result, key=lambda value: value.name)


def rewrite_live_reference_prefix(package: Path, old: str, new: str) -> None:
    for path in authored(package):
        data, body = fm(path)
        changed = False
        for field in REFERENCE_FIELDS:
            values = list_value(data, field)
            rewritten = [
                value.replace(old + ":", new + ":", 1)
                if value.startswith(old + ":") else value
                for value in values
            ]
            if rewritten != values:
                data[field] = rewritten
                changed = True
        if changed:
            rewrite(path, data, body)


def require_lifecycle_dependents_open(
    root: Path, plan: dict, target: Path
) -> list[Path]:
    planned_names = {
        str(row.get("experience", ""))
        for row in plan.get("actions", []) if isinstance(row, dict)
        and row.get("action") == "update"
    }
    current_names = {
        package.name for package in reverse_reference_packages(root, target)
    }
    unplanned = current_names - planned_names
    if unplanned:
        raise ValueError(
            "scope plan omits reverse-reference dependents: "
            + ", ".join(sorted(unplanned))
        )
    result: list[Path] = []
    for name in sorted(planned_names):
        package = resolve_package(root, name)
        if package is None or fields(package).get("status") not in {"draft", "in_review"}:
            raise ValueError(
                f"reverse-reference dependent {name} must open its planned revision before the lifecycle mutation"
            )
        result.append(package)
    return result


def compile_package(package: Path, gate: bool = False, *,
                    allow_stale_inputs: bool = False,
                    defer_lifecycle_record_revision: bool = False) -> tuple[dict, list[str]]:
    problems = []
    try:
        data, _body = fm(package / "experience.md")
    except (OSError, ValueError) as exc:
        return {}, [f"experience.md: {exc}"]
    if data.get("type") != "experience": problems.append("experience.md: root type must be experience")
    if (not valid_experience_slug(str(data.get("experience_id", "")))
            or data.get("experience_id") != package.name):
        problems.append("experience.md: experience_id must match a non-exp lower-kebab folder")
    aliases = list_value(data, "aliases")
    if len(aliases) != len(set(aliases)) or any(
            not valid_experience_slug(alias) for alias in aliases):
        problems.append(
            "experience.md: aliases must be unique non-exp process slugs and cannot reserve application"
        )
    status = data.get("status")
    historical = status == "retired"
    if status not in {"draft", "in_review", "approved", "retirement_pending", "retired"}: problems.append("experience.md: invalid status")
    if type(data.get("revision")) is not int or data["revision"] < 1:
        problems.append("experience.md: revision must be a positive integer")
    if not str(data.get("primary_process_ref", "")).strip(): problems.append("experience.md: primary_process_ref is required")
    if data.get("origin_mode") not in {"manual", "requirement"}: problems.append("experience.md: origin_mode must be manual or requirement")
    implemented_requirements = list_value(data, "implements")
    if data.get("origin_mode") == "requirement" and (len(implemented_requirements) != 1 or not re.fullmatch(r"REQ-[0-9]{3,}", implemented_requirements[0])): problems.append("experience.md: requirement mode needs exactly one implements: REQ-###")
    if data.get("origin_mode") == "requirement" and not str(data.get("upstream_stage_receipts_hash", "")).startswith("sha256:"):
        problems.append("experience.md: requirement mode needs upstream_stage_receipts_hash")
    if data.get("origin_mode") == "manual" and data.get("implements"): problems.append("experience.md: manual package cannot implement Requirement")
    for key in ("baseline_id", "inherits", "program_id", "release_id", "baseline", "program", "release"):
        if key in data: problems.append(f"experience.md: legacy field {key} is forbidden")
    docs = docs_for(package)
    validate_current_inputs = not allow_stale_inputs and not historical
    if (validate_current_inputs and data.get("origin_mode") == "requirement"
            and len(implemented_requirements) == 1
            and re.fullmatch(r"REQ-[0-9]{3,}", implemented_requirements[0])):
        try:
            import requirement_compile
            matches = [path for path in requirement_compile.requirement_paths(docs)
                       if requirement_compile.split_note(path)[0].get("id") == implemented_requirements[0]]
            if len(matches) != 1:
                problems.append("experience.md: implements Requirement is not uniquely resolvable")
            else:
                requirement_props, requirement_body = requirement_compile.split_note(matches[0])
                actual_results_hash = requirement_upstream_hash(
                    requirement_compile.stage_results(requirement_body))
                if (requirement_props.get("status") != "approved"
                        or data.get("upstream_stage_receipts_hash") != actual_results_hash):
                    problems.append("experience.md: Requirement receipt set is stale")
        except (ImportError, OSError, ValueError):
            problems.append("experience.md: Requirement receipt set cannot be verified")
    if validate_current_inputs:
        for stage in ("business-analysis", "solution-design", "design-system"):
            item = bindings(data).get(stage)
            if item is None:
                problems.append(f"experience.md: missing {stage} input binding")
            else:
                _receipt, errors = stage_package.verify(docs, stage, item[0], item[1])
                problems.extend(f"experience.md: {error}" for error in errors)
        ba_binding = bindings(data).get("business-analysis")
        if ba_binding is not None:
            canonical_process, process_errors = stage_package.resolve_ba_process(
                docs, str(data.get("primary_process_ref", "")),
                expected_ba_ref=ba_binding[0], expected_ba_hash=ba_binding[1],
                require_strict_current=gate,
            )
            problems.extend(f"experience.md: {error}" for error in process_errors)
            if canonical_process and canonical_process != data.get("primary_process_ref"):
                problems.append("experience.md: primary_process_ref is not canonical")
            related = list_value(data, "related_process_refs")
            for index, raw in enumerate(related, start=1):
                canonical_related, related_errors = stage_package.resolve_ba_process(
                    docs, raw, expected_ba_ref=ba_binding[0], expected_ba_hash=ba_binding[1],
                    require_strict_current=gate,
                )
                problems.extend(
                    f"experience.md: related_process_refs[{index}]: {error}"
                    for error in related_errors
                )
                if canonical_related and canonical_related != raw:
                    problems.append(
                        f"experience.md: related_process_refs[{index}] is not canonical")
    rows = records(package, problems)
    live = {f"{package.name}:{row['id']}@r{row['revision']}" for row in rows
            if row.get("record_state") == "active"}
    related_processes = set(list_value(data, "related_process_refs"))
    experience_root = package.parent.parent
    for row in rows:
        for field in REFERENCE_FIELDS:
            for reference in list_value(row, field):
                if not EXACT.fullmatch(reference):
                    problems.append(f"{row['path']}: {field} needs exact Experience refs")
                elif (not historical and reference not in live and not external_active_record(
                        experience_root, package, reference, related_processes,
                        gate=gate and not allow_stale_inputs)):
                    problems.append(f"{row['path']}: {field} targets a missing, retired, or stale Experience record: {reference}")
    import experience_application_check
    application_map, map_problems = experience_application_check.load_application_map(package)
    problems.extend(map_problems)
    registry = {"schema_version": 5, "experience_id": package.name, "package_revision": data.get("revision", 1), "origin_mode": data.get("origin_mode"), "implements": implemented_requirements, "primary_process_ref": data.get("primary_process_ref", ""), "related_process_refs": list_value(data, "related_process_refs"), "input_bindings": {key: list(value) for key, value in bindings(data).items()}, "source_hash": source_digest(package), "records": rows, "application_map": application_map}
    registry["registry_hash"] = sha(canonical({key: value for key, value in registry.items() if key not in {"source_hash", "registry_hash", "package_hash"}}))
    registry["package_hash"] = sha(canonical({"source_hash": registry["source_hash"], "registry_hash": registry["registry_hash"]}))
    current_revision = data.get("revision") if type(data.get("revision")) is int else 0
    previous, ledger_problems = validate_process_ledger(package, current_revision)
    problems.extend(ledger_problems)
    generated = package / GENERATED / "registry.json"
    expected_generated = previous[-1] if historical and previous else registry
    if generated.is_file() and not generated.is_symlink():
        try:
            current_registry = strict_json_loads(generated.read_text(encoding="utf-8"))
            if current_registry != expected_generated:
                problems.append("_generated/registry.json: registry is stale or tampered")
        except (OSError, json.JSONDecodeError, ValueError): problems.append("_generated/registry.json: registry is unreadable")
    elif historical:
        problems.append("_generated/registry.json: retired package history is missing")
    root = package.parent.parent
    for other in packages(root):
        if (not historical and other != package
                and fields(other).get("status") != "retired"
                and fields(other).get("primary_process_ref") == data.get("primary_process_ref")):
            problems.append(f"experience.md: primary process is already owned by {other.name}")
        if other != package and package_aliases(package) & package_aliases(other):
            problems.append(f"experience.md: package identity or alias collides with {other.name}")
    if previous:
        old = previous[-1]
        old_owner = str(old.get("experience_id", package.name))
        old_rows = {str(row.get("id")): row for row in old.get("records", []) if isinstance(row, dict)}
        for row in rows:
            prior = old_rows.get(str(row.get("id", "")))
            if (not defer_lifecycle_record_revision and prior
                    and {k: v for k, v in prior.items() if k != "path"}
                    != {k: v for k, v in row.items() if k != "path"}):
                old_revision = int(prior.get("revision", 0) or 0)
                expected_supersedes = f"{old_owner}:{row.get('id')}@r{old_revision}"
                if (int(row.get("revision", 0) or 0) <= old_revision
                        or row.get("supersedes") != expected_supersedes):
                    problems.append(
                        f"{row['path']}: changed record must increment revision and supersede {expected_supersedes}"
                    )
    if gate:
        if status == "approved":
            if not rows: problems.append("experience.md: empty Experience package cannot be approved")
            if data.get("registry_hash") != registry["registry_hash"] or data.get("package_hash") != registry["package_hash"]: problems.append("experience.md: approved hashes are stale")
        elif status == "retired":
            if not previous:
                problems.append("experience.md: retired package needs approved process history")
            else:
                prior = previous[-1]
                if current_revision != int(prior.get("package_revision", 0) or 0) + 1:
                    problems.append("experience.md: retired package revision must follow its final approved receipt")
                if source_digest(
                    package,
                    historical_revision=int(prior.get("package_revision", 0) or 0),
                ) != prior.get("source_hash"):
                    problems.append("experience.md: retired package source changed after its final approval")
                try:
                    retired_at = datetime.fromisoformat(
                        str(data.get("retired_at_utc", "")).replace("Z", "+00:00")
                    )
                    if retired_at.tzinfo is None or retired_at > datetime.now(timezone.utc):
                        raise ValueError
                except ValueError:
                    problems.append("experience.md: retired_at_utc is invalid")
        else:
            problems.append("experience.md: package is not approved or historically retired")
    return registry, sorted(set(problems))


def print_problems(problems: list[str], as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": not problems, "findings": [{"message": item} for item in problems]}, indent=2))
    else:
        for item in problems: print(f"ERROR {item}")
    return 1 if problems else 0


def package_receipt(package: Path, registry: dict) -> dict:
    if fields(package).get("status") != "approved":
        raise ValueError("only an approved package has a current process receipt")
    return {"stage": "experience-design", "result_ref": f"{package.name}@r{registry['package_revision']}", "result_type": "experience-package", "package_hash": registry["package_hash"], "status": "approved", "current": True}


def historical_package_receipt(registry: dict) -> dict:
    return {
        "stage": "experience-design",
        "result_ref": (
            f"{registry.get('experience_id')}@r{registry.get('package_revision')}"
        ),
        "result_type": "experience-package",
        "package_hash": str(registry.get("package_hash", "")),
        "status": "approved",
        "current": False,
    }


def manual_inputs(root: Path, args) -> tuple[list[dict], list[str]]:
    receipts, errors = [], []
    for stage, refs in (("business-analysis", args.ba_ref), ("solution-design", args.solution_ref), ("design-system", args.design_ref)):
        if len(refs) != 1:
            errors.append(f"manual mode needs exactly one {stage} reference"); continue
        receipt, invalid = stage_package.verify(root.parent, stage, ref_value(refs[0]),
                                                require_committed=True,
                                                require_strict_current=True)
        if receipt is None or invalid: errors.extend(invalid or [f"invalid {stage} input"])
        else: receipts.append(receipt)
    return receipts, errors


def requirement_inputs(root: Path, requirement: str) -> tuple[list[dict], list[str], dict]:
    if not re.fullmatch(r"REQ-[0-9]{3,}", requirement):
        return [], ["requirement mode needs --requirement REQ-###"], {}
    import requirement_compile, requirement_route
    candidates = [path for path in requirement_compile.requirement_paths(root.parent)
                  if requirement_compile.split_note(path)[0].get("id") == requirement]
    route = requirement_route.route(root.parent, requirement)
    if len(candidates) != 1 or route.get("stage") != "experience-design" or route.get("action") != "author":
        return [], ["Requirement router is not ready to author Experience Design"], {}
    props, body = requirement_compile.split_note(candidates[0])
    result = requirement_compile.stage_results(body)
    receipts, errors = [], []
    for stage in ("business-analysis", "solution-design", "design-system"):
        rows = result.get(stage, [])
        if len(rows) != 1:
            errors.append(f"Requirement needs one {stage} receipt")
            continue
        receipt, invalid = stage_package.verify(root.parent, stage, rows[0][0], rows[0][1],
                                                require_committed=True,
                                                require_strict_current=True)
        if receipt is None or invalid:
            errors.extend(invalid or [f"invalid {stage} input"])
        else:
            receipts.append(receipt)
    context = {
        "requirement": requirement,
        "requirement_semantic_hash": requirement_compile.semantic_hash(props, body),
        "upstream_stage_receipts_hash": requirement_upstream_hash(result),
    }
    return receipts, errors, context


def selected_inputs(root: Path, args) -> tuple[list[dict], list[str], dict]:
    if args.origin_mode == "manual":
        if args.requirement:
            return [], ["manual mode cannot receive --requirement"], {}
        receipts, errors = manual_inputs(root, args)
        return receipts, errors, {}
    return requirement_inputs(root, args.requirement)


def process_from_inputs(root: Path, value: str, receipts: list[dict], *, require_committed: bool) -> tuple[str | None, list[str]]:
    ba = next((row for row in receipts if row.get("stage") == "business-analysis"), None)
    if ba is None:
        return None, ["Business Analysis input is required before selecting a primary process"]
    return stage_package.resolve_ba_process(
        root.parent, value, expected_ba_ref=str(ba["result_ref"]),
        expected_ba_hash=str(ba["package_hash"]), require_committed=require_committed,
        require_strict_current=True,
    )


def application_metadata(root: Path) -> dict[str, str]:
    application = root / "artifacts" / "application.html"
    if not application.is_file():
        return {}
    import experience_application_check
    return experience_application_check.metadata(
        application.read_text(encoding="utf-8")
    )


def validate_reviewer_attestation(
    path_value: str,
    proposal_hash: str,
    application_registry: dict,
    application_status: str,
) -> None:
    if not path_value:
        raise ValueError("approval requires --review-attestation from experience-reviewer")
    path = Path(path_value)
    if not path.is_file() or path.is_symlink():
        raise ValueError("review attestation must be a regular JSON file")
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"review attestation is unreadable: {exc}") from exc
    expected_keys = {
        "schema_version", "proposal_hash", "application_source_hash",
        "application_package_set_hash", "application_coverage_hash",
        "application_hash", "application_revision", "application_status",
        "reviewed_at_utc", "reviewer_role", "blockers",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError("review attestation has an unsupported schema")
    if value.get("schema_version") != 2:
        raise ValueError("review attestation has an unsupported schema version")
    if value.get("reviewer_role") != "experience-reviewer":
        raise ValueError("review attestation must come from experience-reviewer")
    blockers = value.get("blockers")
    if not isinstance(blockers, list) or blockers:
        raise ValueError("review attestation must report zero blockers")
    if value.get("proposal_hash") != proposal_hash:
        raise ValueError("review attestation is bound to another scope proposal")
    revision = value.get("application_revision")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision != application_registry.get("application_revision")
    ):
        raise ValueError("review attestation is stale for the current application revision")
    if (
        value.get("application_status") != "in_review"
        or application_status != "in_review"
    ):
        raise ValueError("review attestation application must be in_review")
    reviewed_at = value.get("reviewed_at_utc")
    if not isinstance(reviewed_at, str):
        raise ValueError("review attestation reviewed_at_utc must be timezone-aware")
    try:
        reviewed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "review attestation reviewed_at_utc must be timezone-aware"
        ) from exc
    if reviewed.tzinfo is None or reviewed.utcoffset() is None:
        raise ValueError("review attestation reviewed_at_utc must be timezone-aware")
    reviewed = reviewed.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if reviewed > now:
        raise ValueError("review attestation reviewed_at_utc cannot be in the future")
    if now - reviewed > timedelta(hours=24):
        raise ValueError("review attestation is older than 24 hours")
    if (
        value.get("application_source_hash")
        != application_registry.get("source_hash")
        or value.get("application_package_set_hash")
        != application_registry.get("package_set_hash")
        or value.get("application_coverage_hash")
        != application_registry.get("coverage_hash")
        or value.get("application_hash")
        != application_registry.get("application_hash")
    ):
        raise ValueError("review attestation is stale for the current application")


def require_committed_application(root: Path) -> None:
    import experience_application_check
    paths = [
        root / experience_application_check.APPLICATION_RELATIVE,
        root / experience_application_check.REGISTRY_RELATIVE,
        root / experience_application_check.LEDGER_RELATIVE,
    ]
    if not stage_package.paths_are_committed(paths):
        raise ValueError(
            "the approved application, generated registry and revision ledger must be committed before opening a successor revision"
        )


def approved_application_registry(root: Path) -> tuple[dict, list[str]]:
    import experience_application_check
    registry, problems = experience_application_check.compile_application(root, True)
    if not problems:
        return registry, []
    snapshot, snapshot_problems = experience_application_check.approved_snapshot(root)
    return snapshot, snapshot_problems


def expected_application(root: Path) -> dict:
    application = root / "artifacts" / "application.html"
    if not application.is_file():
        return {"exists": False}
    meta = application_metadata(root)
    registry, problems = approved_application_registry(root)
    if problems:
        raise ValueError(
            "current Experience application is not proposal-ready: "
            + "; ".join(problems)
        )
    return {
        "exists": True,
        "status": meta.get("experience-application-status"),
        "revision": registry["application_revision"],
        "source_hash": registry["source_hash"],
        "package_set_hash": registry["package_set_hash"],
        "coverage_hash": registry["coverage_hash"],
        "application_hash": registry["application_hash"],
        "runtime_sha256": registry["runtime_sha256"],
        "design_system_package_hash": registry["design_system"]["package_hash"],
    }


def verify_application_preimage(
    root: Path, plan: dict, proposal_hash: str, *, allow_open: bool = True
) -> None:
    expected = plan.get("expected_application")
    if not isinstance(expected, dict):
        raise ValueError("scope plan is missing the application preimage")
    application = root / "artifacts" / "application.html"
    meta = application_metadata(root)
    phase = meta.get("experience-application-status", "")
    if allow_open and application.is_file() and phase in {"draft", "in_review"}:
        if meta.get("experience-application-proposal-hash") != proposal_hash:
            raise ValueError("application is open for another scope proposal")
        validate_open_application_state(
            root, plan=plan, proposal_hash=proposal_hash,
            expected_phase=phase,
        )
        return
    if not expected.get("exists"):
        if application.exists():
            raise ValueError("application changed after the scope proposal")
        return
    if not application.is_file():
        raise ValueError("application disappeared after the scope proposal")
    registry, problems = approved_application_registry(root)
    if problems:
        raise ValueError("application changed after the scope proposal: " + "; ".join(problems))
    actual = {
        "exists": True,
        "status": meta.get("experience-application-status"),
        "revision": registry.get("application_revision"),
        "source_hash": registry.get("source_hash"),
        "package_set_hash": registry.get("package_set_hash"),
        "coverage_hash": registry.get("coverage_hash"),
        "application_hash": registry.get("application_hash"),
        "runtime_sha256": registry.get("runtime_sha256"),
        "design_system_package_hash": registry.get("design_system", {}).get("package_hash"),
    }
    if actual != expected:
        raise ValueError("application changed after the scope proposal")


def sync_application_dependencies(root: Path, text: str) -> str:
    import experience_application_check
    design, _receipt, problems = experience_application_check.design_binding(root)
    if problems:
        raise ValueError("; ".join(problems))
    values = {
        "experience-application-runtime-sha256": experience_application_check.runtime_sha256(),
        "design-system-package-hash": str(design["package_hash"]),
        "design-system-master-revision": str(design["revision"]),
        "design-system-master-source-hash": str(design["master_source_hash"]),
    }
    for name, value in values.items():
        text = experience_application_check.replace_meta(text, name, value)
    text = experience_application_check.replace_tokens(text, str(design["tokens"]))
    runtime_pattern = re.compile(
        r'(<script\b[^>]*\bid=["\']experience-application-runtime["\'][^>]*>)'
        r'.*?(</script(?:[\t\n\f\r />][^<>]*)?>)',
        re.I | re.S,
    )
    template_runtime = experience_application_check.template_runtime()
    if not runtime_pattern.search(text):
        raise ValueError("application fixed runtime is missing")
    text = runtime_pattern.sub(
        lambda match: match.group(1) + template_runtime + match.group(2),
        text,
        count=1,
    )
    checksum_pattern = re.compile(
        r'(<script\b[^>]*\bid=["\']experience-application-runtime["\'][^>]*'
        r'\bdata-runtime-sha256=["\'])[^"\']*(["\'])',
        re.I,
    )
    if not checksum_pattern.search(text):
        raise ValueError("application runtime checksum attribute is missing")
    text = checksum_pattern.sub(
        rf"\g<1>{experience_application_check.runtime_sha256()}\g<2>",
        text,
        count=1,
    )
    csp_pattern = re.compile(
        r'(<meta\b(?=[^>]*\bhttp-equiv=["\']Content-Security-Policy["\'])'
        r'(?=[^>]*\bcontent=")[^>]*\bcontent=")[^"]*(")',
        re.I,
    )
    if not csp_pattern.search(text):
        raise ValueError("application Content-Security-Policy metadata is missing")
    runtime_csp = experience_application_check.runtime_csp_sha256()
    expected_csp = experience_application_check.expected_csp()
    if runtime_csp not in expected_csp:
        raise ValueError("application runtime CSP digest cannot be synchronized")
    return csp_pattern.sub(
        lambda match: match.group(1) + expected_csp + match.group(2),
        text,
        count=1,
    )


def rename_application_refs(text: str, old: str, new: str) -> str:
    """Rewrite live exact refs and the declarative route owner canonically."""
    text = text.replace(old + ":", new + ":")
    pattern = re.compile(
        r'(<script\b(?=[^>]*\btype=["\']application/json["\'])'
        r'(?=[^>]*\bid=["\']experience-application-contract["\'])[^>]*>)'
        r'(.*?)(</script(?:[\t\n\f\r />][^<>]*)?>)',
        re.I | re.S,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError("application contract script is missing during rename")
    try:
        contract = strict_json_loads(match.group(2))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"application contract cannot be renamed: {exc}") from exc
    if not isinstance(contract, dict) or not isinstance(contract.get("routes"), list):
        raise ValueError("application contract routes are invalid during rename")
    for route in contract["routes"]:
        if isinstance(route, dict) and route.get("experience_id") == old:
            route["experience_id"] = new
    body = "\n" + json.dumps(
        contract, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n  "
    return text[:match.start()] + match.group(1) + body + match.group(3) + text[match.end():]


def open_application(root: Path, plan: dict, proposal_hash: str) -> None:
    import experience_application_check
    action = str(plan.get("application_action", ""))
    if action == "reuse":
        if open_application_state_path(root).exists():
            raise ValueError("a reused application cannot have open revision state")
        verify_application_preimage(root, plan, proposal_hash, allow_open=False)
        return
    if action not in {"create", "update"}:
        raise ValueError("scope plan has an invalid application_action")
    application = root / experience_application_check.APPLICATION_RELATIVE
    current_meta = application_metadata(root)
    if (
        application.is_file()
        and current_meta.get("experience-application-status") in {"draft", "in_review"}
    ):
        validate_open_application_state(
            root, plan=plan, proposal_hash=proposal_hash,
            expected_phase="draft",
        )
        return
    if open_application_state_path(root).exists():
        raise ValueError("application has stale compiler-owned open revision state")
    verify_application_preimage(root, plan, proposal_hash, allow_open=False)
    expected = plan["expected_application"]
    if action == "create":
        if expected.get("exists"):
            raise ValueError("application_action create requires an absent application")
        text = experience_application_check.render_template(root, proposal_hash, 1)
    else:
        if not expected.get("exists"):
            raise ValueError("application_action update requires an approved application")
        require_committed_application(root)
        text = application.read_text(encoding="utf-8")
        text = sync_application_dependencies(root, text)
        revision = opened_application_revision(plan)
        for name, value in (
            ("experience-application-status", "draft"),
            ("experience-application-revision", str(revision)),
            ("experience-application-proposal-hash", proposal_hash),
            ("experience-application-source-hash", ""),
            ("experience-application-package-set-hash", ""),
            ("experience-application-coverage-hash", ""),
            ("experience-application-hash", ""),
            ("experience-application-approved-at-utc", ""),
        ):
            text = experience_application_check.replace_meta(text, name, value)
    atomic_write_bytes(application, text.encode())
    write_open_application_state(
        root, plan, proposal_hash, phase="draft",
    )


def write_application_map(package: Path) -> None:
    target = package / "artifacts" / "application-map.json"
    if target.exists():
        raise ValueError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, canonical({
        "schema_version": 2,
        "application_path": "experience-design/artifacts/application.html",
        "experience_id": package.name,
        "bindings": [],
    }))


def init(args) -> int:
    root = root_for(args.root)
    if not valid_experience_slug(args.experience): return fail("experience must be lower kebab-case and must not use exp-", 2)
    if resolve_package(root, args.experience) or (root / "experiences" / args.experience).exists(): return fail("Experience slug or retired alias is reserved", 2)
    receipts, problems, context = selected_inputs(root, args)
    if problems: return fail("; ".join(problems), 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        primary, process_errors = process_from_inputs(root, args.primary_process_ref, receipts,
                                                       require_committed=True)
        if process_errors or primary is None:
            raise ValueError("; ".join(process_errors))
        related = []
        for raw_related in args.related_process_ref:
            related_process, related_errors = process_from_inputs(
                root, raw_related, receipts, require_committed=True)
            if related_errors or related_process is None:
                raise ValueError("; ".join(related_errors))
            related.append(related_process)
        if plan.get("origin_mode") != args.origin_mode:
            raise ValueError("scope plan origin mode does not match the new Experience package")
        selected_action = action_for_plan(
            root, plan, action="create", experience=args.experience,
            process=primary,
        )
    except ValueError as exc:
        return fail(str(exc), 2)
    if any(fields(item).get("primary_process_ref") == primary and fields(item).get("status") != "retired" for item in packages(root)): return fail("an active Experience already owns this primary process", 2)
    package = root / "experiences" / args.experience
    application = root / "artifacts" / "application.html"
    original_application = application.read_bytes() if application.is_file() else None
    try:
        open_application(root, plan, args.proposal_hash)
    except ValueError as exc:
        return fail(str(exc), 2)
    data = {"type": "experience", "experience_id": args.experience, "origin_mode": args.origin_mode, "status": "draft", "revision": 1, "primary_process_ref": primary, "input_bindings": binding_rows(receipts)}
    if related: data["related_process_refs"] = related
    if args.origin_mode == "requirement":
        data["implements"] = [args.requirement]
        data["upstream_stage_receipts_hash"] = context["upstream_stage_receipts_hash"]
    try:
        write(package / "experience.md", data, args.title or f"{args.experience.replace('-', ' ').title()} Experience", "Living process-owned Experience package.", "maps/experience-design")
        for name in [entry[0] for entry in KIND.values()] + ["artifacts", GENERATED, LEDGER]: (package / name).mkdir(parents=True, exist_ok=True)
        write_application_map(package)
        write_open_revision(package, plan, selected_action, args.proposal_hash)
    except (OSError, ValueError) as exc:
        if package.exists():
            shutil.rmtree(package)
        if original_application is None:
            application.unlink(missing_ok=True)
        else:
            atomic_write_bytes(application, original_application)
        return fail(str(exc), 2)
    print(json.dumps({"experience": args.experience, "path": str(package), "origin_mode": args.origin_mode}, indent=2)); return 0


def propose(args) -> int:
    root = root_for(args.root)
    receipts, problems, context = selected_inputs(root, args)
    if problems:
        return fail("; ".join(problems), 2)
    import experience_application_check
    _design, _receipt, design_problems = experience_application_check.design_binding(root)
    if design_problems:
        return fail("; ".join(design_problems), 2)
    if len(args.process_ref) > 1 and args.experience:
        return fail("a multi-process create derives each Experience slug from its process; omit --experience", 2)
    actions = []
    if not args.process_ref:
        if args.application_action != "update":
            return fail("an application-only proposal requires --application-action update", 2)
        current_packages = [
            package for package in packages(root)
            if fields(package).get("status") == "approved"
        ]
        if not current_packages and not (root / "artifacts" / "application.html").is_file():
            return fail("an application-only proposal needs an existing application", 2)
        actions.extend({
            "primary_process_ref": str(fields(package).get("primary_process_ref", "")),
            "experience": package.name,
            "target_experience": "",
            "action": "reuse",
            "affected_records": package_record_ids(package),
            "expected_package": {
                "status": fields(package).get("status"),
                "revision": fields(package).get("revision"),
                "source_hash": source_digest(package),
            },
            "reason": args.reason or "Application-only acceptance correction.",
        } for package in current_packages)
    for raw_process in args.process_ref:
        process, process_errors = process_from_inputs(root, raw_process, receipts,
                                                      require_committed=True)
        if process_errors or process is None:
            return fail("; ".join(process_errors), 2)
        current = [item for item in packages(root)
                   if fields(item).get("primary_process_ref") == process
                   and fields(item).get("status") != "retired"]
        package = current[0] if current else None
        action = args.action or ("update" if package else "create")
        if action == "create":
            experience = args.experience or process.rsplit("/", 1)[-1].removesuffix("-process")
        elif package is not None:
            experience = package.name
        else:
            return fail(f"{action} needs an active Experience for {process}", 2)
        if action == "rename" and not valid_experience_slug(args.to):
            return fail("rename scope proposal needs --to with a non-exp lower-kebab slug", 2)
        expected = ({} if action == "create" or package is None else {
            "status": fields(package).get("status"),
            "revision": fields(package).get("revision"),
            "source_hash": source_digest(package),
        })
        actions.append({
            "primary_process_ref": process,
            "experience": experience,
            "target_experience": args.to if action == "rename" else "",
            "action": action,
            "affected_records": (
                package_record_ids(package)
                if package is not None and action != "create"
                else []
            ),
            "expected_package": expected,
            "reason": args.reason or "Owner confirmation required.",
        })
    lifecycle_actions = [
        row for row in actions if row.get("action") in {"rename", "retire"}
    ]
    for lifecycle in lifecycle_actions:
        target = resolve_package(root, str(lifecycle.get("experience", "")))
        if target is None:
            return fail("lifecycle scope target is no longer resolvable", 2)
        try:
            dependents = reverse_reference_packages(root, target)
        except ValueError as exc:
            return fail(str(exc), 2)
        for dependent in dependents:
            if any(row.get("experience") == dependent.name for row in actions):
                continue
            dependent_data = fields(dependent)
            if dependent_data.get("status") != "approved":
                return fail(
                    f"reverse-reference dependent {dependent.name} must be approved before lifecycle planning",
                    2,
                )
            if dependent_data.get("origin_mode") != args.origin_mode:
                return fail(
                    f"reverse-reference dependent {dependent.name} uses another origin mode; remove its reference in a prior revision",
                    2,
                )
            dependent_process, dependent_errors = process_from_inputs(
                root, str(dependent_data.get("primary_process_ref", "")), receipts,
                require_committed=True,
            )
            if dependent_errors or dependent_process is None:
                return fail("; ".join(dependent_errors), 2)
            actions.append({
                "primary_process_ref": dependent_process,
                "experience": dependent.name,
                "target_experience": "",
                "action": "update",
                "affected_records": package_record_ids(dependent),
                "expected_package": {
                    "status": dependent_data.get("status"),
                    "revision": dependent_data.get("revision"),
                    "source_hash": source_digest(dependent),
                },
                "reason": (
                    f"Repair exact refs affected by {lifecycle['action']} of "
                    f"{target.name}."
                ),
            })
    actions.sort(
        key=lambda row: (
            str(row.get("experience", "")), str(row.get("action", "")),
            str(row.get("target_experience", "")),
        )
    )
    current_design_hash = str(_design["package_hash"])
    selected_updates = {
        str(row.get("experience"))
        for row in actions
        if row.get("action") in {"update", "rename", "retire"}
    }
    stale_design_packages = [
        package.name for package in packages(root)
        if fields(package).get("status") == "approved"
        and bindings(fields(package)).get("design-system", ("", ""))[1]
        != current_design_hash
        and package.name not in selected_updates
    ]
    if stale_design_packages:
        return fail(
            "scope proposal must update every Experience package with a stale Design System binding: "
            + ", ".join(sorted(stale_design_packages)),
            2,
        )
    mutating = any(row["action"] in {"create", "update", "rename", "retire"} for row in actions)
    application_exists = (root / "artifacts" / "application.html").is_file()
    application_action = args.application_action or (
        ("update" if application_exists else "create") if mutating else "reuse"
    )
    if mutating and application_action not in {"create", "update"}:
        return fail("a package-set mutation must create or update the application", 2)
    if application_action == "create" and application_exists:
        return fail("application_action create requires an absent application", 2)
    if application_action in {"update", "reuse"} and not application_exists:
        return fail(f"application_action {application_action} requires an approved application", 2)
    try:
        application_preimage = expected_application(root)
    except ValueError as exc:
        return fail(str(exc), 2)
    plan = {
        "schema_version": 2,
        "origin_mode": args.origin_mode,
        "input_bindings": binding_rows(receipts),
        "actions": actions,
        "application_action": application_action,
        "expected_application": application_preimage,
        **context,
    }
    plan["proposal_hash"] = proposal_digest(plan)
    print(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True)); return 0


def begin_revision(args) -> int:
    package = package_for(args.experience_root); root = package.parent.parent
    data, body = fm(package / "experience.md")
    if data.get("status") != "approved":
        return fail("only an approved Experience may begin a successor revision", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        if plan.get("origin_mode") != data.get("origin_mode"):
            raise ValueError("scope plan origin mode does not match the Experience package")
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        selected_action = action_for_plan(
            root, plan, action="update", experience=package.name,
            process=str(data.get("primary_process_ref", "")),
        )
        replacement_bindings = [f"{stage}|{reference}|{digest}"
                                for stage, reference, digest in input_rows(plan)]
    except ValueError as exc:
        return fail(str(exc), 2)
    # The plan has already verified the successor receipts. Preserve the last
    # approved registry while allowing those predecessor receipts to be stale.
    registry, problems = compile_package(package, True, allow_stale_inputs=True)
    if problems: return print_problems(problems, False)
    if not stage_package.is_committed(package):
        return fail("the approved Experience package must be committed before opening a successor revision", 2)
    with tempfile.TemporaryDirectory(prefix="experience-revision-") as raw:
        backup = Path(raw) / "experience-design"
        shutil.copytree(root, backup)
        try:
            open_application(root, plan, args.proposal_hash)
            archive_process_registry(package, registry)
            data["status"] = "draft"
            data["revision"] = int(data.get("revision", 1) or 1) + 1
            data["input_bindings"] = replacement_bindings
            if data.get("origin_mode") == "requirement":
                data["upstream_stage_receipts_hash"] = plan["upstream_stage_receipts_hash"]
            for key in (
                "approval_revision", "registry_hash", "package_hash",
                "source_hash", "approved_at_utc",
            ):
                data.pop(key, None)
            status_tags(data)
            rewrite(package / "experience.md", data, body)
            write_open_revision(
                package, plan, selected_action, args.proposal_hash,
            )
        except Exception as exc:
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(backup, root)
            return fail(f"begin-revision rolled back: {exc}", 2)
    return 0


def enter_review(args) -> int:
    package = package_for(args.experience_root); data, body = fm(package / "experience.md")
    if data.get("status") != "draft": return fail("only draft Experience may enter review", 2)
    try:
        validate_open_revision_identity(package, expected_status="draft")
    except ValueError as exc:
        return fail(str(exc), 2)
    registry, problems = compile_package(package)
    if [item for item in problems if "registry is stale" not in item]: return print_problems(problems, False)
    data["status"] = "in_review"; status_tags(data); rewrite(package / "experience.md", data, body)
    return render(argparse.Namespace(experience_root=str(package)))


def stub(args) -> int:
    package = package_for(args.experience_root)
    if fields(package).get("status") not in {"draft", "in_review"}: return fail("approved Experience is immutable; begin revision first", 2)
    directory, prefix, suffix = KIND[args.kind]
    if not SLUG.fullmatch(args.slug) or not re.fullmatch(prefix + r"-[0-9]{3,}", args.id): return fail("invalid record slug or ID", 2)
    if args.kind == "state" and args.state_class not in STATE_CLASSES:
        return fail("state stub requires --state-class from the canonical taxonomy", 2)
    if args.kind != "state" and args.state_class:
        return fail("--state-class is valid only for state records", 2)
    data = {"type": args.kind, "id": args.id, "revision": args.revision, "record_state": args.record_state, "derives_from": args.derives_from or [str(fields(package).get("primary_process_ref", ""))]}
    if args.kind == "state":
        data["state_class"] = args.state_class
    if args.criterion_ref: data["criterion_refs"] = args.criterion_ref; data["satisfies"] = args.criterion_ref
    for key in ("uses_design", "constrained_by", "journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"):
        value = getattr(args, key)
        if value: data[key] = value
    if args.supersedes: data["supersedes"] = args.supersedes
    write(package / directory / f"{args.slug}{suffix}", data, args.title or args.id, "Define the bounded experience behavior and exact references.", f"experience-design/experiences/{package.name}/experience")
    return 0


def render(args) -> int:
    package = package_for(args.experience_root); registry, problems = compile_package(package)
    if [item for item in problems if "registry is stale" not in item]: return print_problems(problems, False)
    generated = package / GENERATED; generated.mkdir(exist_ok=True)
    atomic_write_bytes(generated / "registry.json", canonical(registry))
    atomic_write_bytes(
        generated / "coverage.json",
        canonical({"experience_id": package.name, "active_records": [
            item["id"] for item in registry["records"]
            if item.get("record_state") == "active"
        ]}),
    )
    render_experience_navigation(package.parent.parent)
    return 0


def check(args) -> int:
    registry, problems = compile_package(package_for(args.experience_root), args.gate)
    if args.json and not problems: print(json.dumps({"ok": True, "experience_id": registry.get("experience_id"), "registry_hash": registry.get("registry_hash"), "findings": []}, indent=2)); return 0
    return print_problems(problems, args.json)


def render_application(args) -> int:
    root = root_for(args.root)
    import experience_application_check
    registry, problems = experience_application_check.compile_application(root)
    if problems:
        return print_problems(problems, getattr(args, "json", False))
    application = root / experience_application_check.APPLICATION_RELATIVE
    text = application.read_text(encoding="utf-8")
    meta = experience_application_check.metadata(text)
    status = meta.get("experience-application-status", "draft")
    if status not in {"draft", "in_review"}:
        return fail("approved application is immutable; begin an application revision first", 2)
    try:
        validate_open_application_state(root, expected_phase=status)
    except ValueError as exc:
        return fail(str(exc), 2)
    proposal_hash = meta.get("experience-application-proposal-hash", "")
    atomic_write_bytes(
        application,
        experience_application_check.stamp_application(
            text, registry, status, proposal_hash,
        ).encode(),
    )
    target = root / experience_application_check.REGISTRY_RELATIVE
    atomic_write_json(target, registry)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "application": registry, "findings": []}, indent=2))
    return 0


def begin_application_revision(args) -> int:
    root = root_for(args.root)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        if plan.get("application_action") not in {"create", "update"}:
            raise ValueError("scope plan does not create or update the application")
        open_application(root, plan, args.proposal_hash)
    except ValueError as exc:
        return fail(str(exc), 2)
    print(json.dumps({
        "path": str(root / "artifacts" / "application.html"),
        "status": application_metadata(root).get("experience-application-status"),
        "revision": application_metadata(root).get("experience-application-revision"),
    }, indent=2))
    return 0


def enter_application_review(args) -> int:
    root = root_for(args.root)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        if plan.get("application_action") not in {"create", "update"}:
            raise ValueError("only a created or updated application may enter review")
        verify_application_preimage(root, plan, args.proposal_hash)
    except ValueError as exc:
        return fail(str(exc), 2)
    meta = application_metadata(root)
    if (
        meta.get("experience-application-status") != "draft"
        or meta.get("experience-application-proposal-hash") != args.proposal_hash
    ):
        return fail("only the proposal-bound draft application may enter review", 2)
    retiring = {
        str(row.get("experience"))
        for row in plan["actions"] if isinstance(row, dict)
        and row.get("action") == "retire"
    }
    effective = []
    for package in packages(root):
        if fields(package).get("status") != "retired" and package.name not in retiring:
            rendered = render(argparse.Namespace(experience_root=str(package)))
            if rendered:
                return rendered
            effective.append(package)
    import experience_application_check
    registry, problems = experience_application_check.compile_application(
        root, package_paths=effective
    )
    if problems:
        return print_problems(problems, False)
    application = root / experience_application_check.APPLICATION_RELATIVE
    text = application.read_text(encoding="utf-8")
    text = experience_application_check.stamp_application(
        text, registry, "in_review", args.proposal_hash
    )
    atomic_write_bytes(application, text.encode())
    target = root / experience_application_check.REGISTRY_RELATIVE
    atomic_write_json(target, registry)
    write_open_application_state(
        root, plan, args.proposal_hash, phase="in_review",
    )
    return 0


def check_application(args) -> int:
    import experience_application_check
    registry, problems = experience_application_check.compile_application(
        root_for(args.root), args.gate
    )
    if args.json:
        print(json.dumps({
            "ok": not problems,
            "application": registry,
            "findings": [{"message": item} for item in problems],
        }, indent=2))
        return 1 if problems else 0
    return print_problems(problems, False)


def application_status(args) -> int:
    import experience_application_check
    root = root_for(args.root)
    registry, problems = experience_application_check.compile_application(root, True)
    if problems:
        return print_problems(problems, True)
    print(json.dumps(
        experience_application_check.application_receipt(root, registry),
        indent=2,
    ))
    return 0


def approve_set(args) -> int:
    root = root_for(args.root)
    selected = [resolve_package(root, item) for item in args.experience]
    if any(item is None for item in selected) or len(set(selected)) != len(selected):
        return fail("approve-set needs unique resolvable Experience packages", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        changed_actions = [
            row for row in plan["actions"] if isinstance(row, dict)
            and row.get("action") in {"create", "update", "rename", "retire"}
        ]
        changed_targets = [action_target(row) for row in changed_actions]
        if len(changed_targets) != len(set(changed_targets)):
            raise ValueError("scope plan contains duplicate package mutation targets")
        action_by_target = dict(zip(changed_targets, changed_actions))
        expected = set(changed_targets)
        actual = {item.name for item in selected if item is not None}
        if actual != expected:
            raise ValueError("approve-set packages must exactly match the approved scope-plan action set")
        for row in plan["actions"]:
            if not isinstance(row, dict):
                raise ValueError("scope plan contains an invalid package action")
            action = str(row.get("action", ""))
            if action == "reuse":
                action_for_plan(
                    root, plan, action="reuse",
                    experience=str(row.get("experience", "")),
                    process=str(row.get("primary_process_ref", "")),
                )
        verify_application_preimage(root, plan, args.proposal_hash)
        application_action = str(plan.get("application_action", ""))
        if application_action not in {"create", "update", "reuse"}:
            raise ValueError("scope plan has an invalid application_action")
    except ValueError as exc:
        return fail(str(exc), 2)
    import experience_application_check
    if application_action == "reuse":
        if selected:
            return fail("a reused application cannot approve package mutations", 2)
        if open_application_state_path(root).exists():
            return fail("a reused application cannot have open revision state", 2)
        registry, problems = experience_application_check.compile_application(root, True)
        if problems:
            return print_problems(problems, False)
        package_receipts = []
        for package in packages(root):
            if fields(package).get("status") == "approved":
                compiled, package_problems = compile_package(package, True)
                if package_problems:
                    return print_problems(package_problems, False)
                package_receipts.append(package_receipt(package, compiled))
        print(json.dumps({"receipts": [
            experience_application_check.application_receipt(root, registry),
            *package_receipts,
        ]}, indent=2))
        return 0
    meta = application_metadata(root)
    if (
        meta.get("experience-application-status") != "in_review"
        or meta.get("experience-application-proposal-hash") != args.proposal_hash
    ):
        return fail("proposal-bound application must be in_review before approval", 2)
    try:
        validate_open_application_state(
            root, plan=plan, proposal_hash=args.proposal_hash,
            expected_phase="in_review",
        )
    except ValueError as exc:
        return fail(str(exc), 2)
    attested_registry, _attestation_findings = (
        experience_application_check.compile_application(root)
    )
    try:
        if not {
            "source_hash", "package_set_hash", "coverage_hash",
            "application_hash",
        }.issubset(attested_registry):
            raise ValueError("current application cannot be attested")
        validate_reviewer_attestation(
            args.review_attestation, args.proposal_hash, attested_registry,
            application_metadata(root).get("experience-application-status", ""),
        )
    except ValueError as exc:
        return fail(str(exc), 2)
    map_path = root.parent / "maps" / "experience-design.md"
    original_map = map_path.read_bytes() if map_path.is_file() else None
    with tempfile.TemporaryDirectory(prefix="experience-approve-") as raw:
        backup_root = Path(raw) / "experience-design"
        shutil.copytree(root, backup_root)
        prepared = []
        retiring = []
        try:
            for package in selected:
                data, body = fm(package / "experience.md")
                action = action_by_target[package.name]
                if action.get("action") == "retire":
                    validate_open_revision(
                        package, plan, action, args.proposal_hash,
                        expected_status="retirement_pending",
                    )
                    retiring.append((package, data, body))
                    continue
                validate_open_revision(
                    package, plan, action, args.proposal_hash,
                    expected_status="in_review",
                )
                if render(argparse.Namespace(experience_root=str(package))):
                    raise ValueError(f"cannot render {package.name}")
                registry, problems = compile_package(package)
                hard = [item for item in problems if "registry is stale" not in item]
                if hard:
                    raise ValueError("; ".join(hard))
                previous, previous_problems = validate_process_ledger(
                    package, int(data.get("revision", 0) or 0),
                )
                if previous_problems:
                    raise ValueError("; ".join(previous_problems))
                if (previous and previous[-1].get("source_hash")
                        == registry["source_hash"]):
                    raise ValueError(f"{package.name} has no record or input delta")
                prepared.append((package, data, body, registry))
            for package, data, body, registry in prepared:
                data.update({
                    "status": "approved",
                    "approval_revision": int(data.get("approval_revision", 0) or 0) + 1,
                    "registry_hash": registry["registry_hash"],
                    "package_hash": registry["package_hash"],
                    "source_hash": registry["source_hash"],
                    "approved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                })
                status_tags(data)
                rewrite(package / "experience.md", data, body)
                (package / GENERATED / OPEN_REVISION).unlink(missing_ok=True)
            # Publish all package statuses before any strict cross-package
            # validation so a coordinated cycle never observes a peer still
            # in_review merely because of iteration order.
            for package, _data, _body, _registry in prepared:
                if render(argparse.Namespace(experience_root=str(package))):
                    raise ValueError("cannot render approved Experience package")
            for package, _data, _body, _registry in prepared:
                _registry, problems = compile_package(package, True)
                if problems:
                    raise ValueError("; ".join(problems))
            for package, data, body in retiring:
                data["status"] = "retired"
                data["retired_at_utc"] = datetime.now(timezone.utc).replace(
                    microsecond=0
                ).isoformat()
                status_tags(data)
                rewrite(package / "experience.md", data, body)
                (package / GENERATED / OPEN_REVISION).unlink(missing_ok=True)
                _retired_registry, retired_problems = compile_package(package, True)
                if retired_problems:
                    raise ValueError("; ".join(retired_problems))
            application_registry, problems = experience_application_check.compile_application(root)
            if problems:
                raise ValueError("; ".join(problems))
            attested_fields = (
                "application_revision", "source_hash", "package_set_hash",
                "coverage_hash", "application_hash",
            )
            if any(
                application_registry.get(field) != attested_registry.get(field)
                for field in attested_fields
            ):
                raise ValueError(
                    "review attestation is stale for the final application"
                )
            previous_application = plan.get("expected_application", {})
            no_semantic_delta = bool(previous_application.get("exists")) and all((
                application_registry["source_hash"] == previous_application.get("source_hash"),
                application_registry["package_set_hash"] == previous_application.get("package_set_hash"),
                application_registry["coverage_hash"] == previous_application.get("coverage_hash"),
                application_registry["design_system"]["package_hash"]
                == previous_application.get("design_system_package_hash"),
                application_registry["runtime_sha256"]
                == previous_application.get("runtime_sha256"),
            ))
            if no_semantic_delta:
                raise ValueError("application update has no application or package-set delta")
            application = root / experience_application_check.APPLICATION_RELATIVE
            atomic_write_bytes(
                application,
                experience_application_check.stamp_application(
                    application.read_text(encoding="utf-8"),
                    application_registry,
                    "approved",
                    args.proposal_hash,
                ).encode(),
            )
            experience_application_check.write_registry_and_ledger(
                root, application_registry
            )
            checked, problems = experience_application_check.compile_application(root, True)
            if problems:
                raise ValueError("; ".join(problems))
            application_registry = checked
            open_state = open_application_state_path(root)
            open_state.unlink(missing_ok=True)
            fsync_directory(open_state.parent)
            render_experience_navigation(root)
        except Exception as exc:
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(backup_root, root)
            if original_map is None:
                map_path.unlink(missing_ok=True)
            else:
                map_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(map_path, original_map)
            return fail(f"approve-set rolled back: {exc}")
    package_receipts = []
    for package in packages(root):
        if fields(package).get("status") == "approved":
            compiled, problems = compile_package(package, True)
            if problems:
                return print_problems(problems, False)
            package_receipts.append(package_receipt(package, compiled))
    print(json.dumps({"receipts": [
        experience_application_check.application_receipt(root, application_registry),
        *package_receipts,
    ]}, indent=2))
    return 0


def rename(args) -> int:
    package = package_for(args.experience_root); root = package.parent.parent
    if not valid_experience_slug(args.to) or resolve_package(root, args.to) or (package.parent / args.to).exists(): return fail("new Experience slug is invalid or reserved", 2)
    data, body = fm(package / "experience.md")
    if data.get("status") not in {"approved", "draft", "in_review"}: return fail("rename requires an active Experience revision", 2)
    if data.get("status") == "approved" and not stage_package.is_committed(package):
        return fail("the approved Experience package must be committed before rename", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        selected_action = action_for_plan(
            root, plan, action="rename", experience=package.name,
            target=args.to, process=str(data.get("primary_process_ref", "")),
        )
        require_lifecycle_dependents_open(root, plan, package)
    except ValueError as exc:
        return fail(str(exc), 2)
    old, destination = package.name, package.parent / args.to
    map_path = root.parent / "maps" / "experience-design.md"
    original_map = map_path.read_bytes() if map_path.is_file() else None
    with tempfile.TemporaryDirectory(prefix="experience-rename-") as raw:
        backup = Path(raw) / "experience-design"
        shutil.copytree(root, backup)
        try:
            open_application(root, plan, args.proposal_hash)
            if data.get("status") == "approved":
                registry, problems = compile_package(
                    package, True, allow_stale_inputs=True,
                )
                if problems:
                    raise ValueError("; ".join(problems))
                archive_process_registry(package, registry)
                data["status"] = "draft"
                data["revision"] = int(data.get("revision", 1) or 1) + 1
                for key in ("approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc"):
                    data.pop(key, None)
                status_tags(data)
                rewrite(package / "experience.md", data, body)
            os.replace(package, destination)
            # Rewrite only live graph fields.  ``supersedes`` and ledger
            # snapshots identify history and must keep the old exact ref.
            rewrite_live_reference_prefix(destination, old, args.to)
            data["experience_id"] = args.to
            data["aliases"] = sorted(set(list_value(data, "aliases") + [old]))
            rewrite(destination / "experience.md", data, body)
            target = destination / LEDGER / "aliases.json"
            aliases = {}
            if target.is_file():
                try:
                    value = strict_json_loads(target.read_text(encoding="utf-8"))
                    if not isinstance(value, dict) or not isinstance(
                        value.get("aliases"), dict
                    ):
                        raise ValueError("alias ledger has an invalid schema")
                    aliases = value["aliases"]
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"alias ledger is unreadable: {exc}") from exc
            aliases[old] = args.to
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, canonical({"aliases": aliases}))
            write_open_revision(
                destination, plan, selected_action, args.proposal_hash,
            )
            application_map = destination / "artifacts" / "application-map.json"
            map_data = strict_json_loads(application_map.read_text(encoding="utf-8"))
            if not isinstance(map_data, dict):
                raise ValueError("application map must be a JSON object")
            map_data["experience_id"] = args.to
            for binding in map_data.get("bindings", []):
                if str(binding.get("record_ref", "")).startswith(old + ":"):
                    binding["record_ref"] = str(binding["record_ref"]).replace(
                        old + ":", args.to + ":", 1
                    )
            atomic_write_bytes(application_map, canonical(map_data))
            import experience_application_check
            application = root / experience_application_check.APPLICATION_RELATIVE
            application_text = rename_application_refs(
                application.read_text(encoding="utf-8"), old, args.to,
            )
            atomic_write_bytes(application, application_text.encode())
            render_experience_navigation(root)
            _registry, problems = compile_package(
                destination, defer_lifecycle_record_revision=True,
            )
            if [item for item in problems if "registry is stale" not in item]:
                raise ValueError("; ".join(problems))
        except Exception:
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(backup, root)
            if original_map is None:
                map_path.unlink(missing_ok=True)
            else:
                map_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(map_path, original_map)
            raise
    print(json.dumps({"from": old, "to": args.to}, indent=2)); return 0


def retire(args) -> int:
    package = package_for(args.experience_root)
    data, body = fm(package / "experience.md")
    if data.get("status") != "approved":
        return fail("only an approved Experience may retire", 2)
    if not stage_package.is_committed(package):
        return fail("the approved Experience package must be committed before retirement", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(package.parent.parent, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        selected_action = action_for_plan(
            package.parent.parent, plan, action="retire",
            experience=package.name,
            process=str(data.get("primary_process_ref", "")),
        )
        require_lifecycle_dependents_open(package.parent.parent, plan, package)
    except ValueError as exc:
        return fail(str(exc), 2)
    registry, problems = compile_package(package, True, allow_stale_inputs=True)
    if problems:
        return print_problems(problems, False)
    root = package.parent.parent
    with tempfile.TemporaryDirectory(prefix="experience-retire-") as raw:
        backup = Path(raw) / "experience-design"
        shutil.copytree(root, backup)
        try:
            open_application(root, plan, args.proposal_hash)
            archive_process_registry(package, registry)
            data["status"] = "retirement_pending"
            data["revision"] = int(data.get("revision", 1) or 1) + 1
            for key in ("approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc"):
                data.pop(key, None)
            status_tags(data)
            rewrite(package / "experience.md", data, body)
            write_open_revision(
                package, plan, selected_action, args.proposal_hash,
            )
            render_experience_navigation(root)
        except Exception as exc:
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(backup, root)
            return fail(f"retire preparation rolled back: {exc}")
    print(json.dumps({"experience": package.name, "status": "retirement_pending"}, indent=2))
    return 0


def resolve(args) -> int:
    root = root_for(args.root); match = EXACT.fullmatch(args.ref)
    if match:
        package = resolve_package(root, match.group(1)); ident, revision = args.ref.split(":", 1)[1].split("@", 1)[0], int(match.group(3))
        if package:
            data = fields(package)
            if data.get("status") == "approved" and match.group(1) == package.name:
                registry, problems = compile_package(package, True)
                current = next(
                    (
                        row for row in registry.get("records", [])
                        if row.get("id") == ident and row.get("revision") == revision
                    ),
                    None,
                ) if not problems else None
                if current:
                    print(json.dumps(current, indent=2)); return 0
            history, history_problems = validate_process_ledger(
                package, int(data.get("revision", 0) or 0),
            )
            if not history_problems:
                historic = next(
                    (
                        row for registry in reversed(history)
                        if registry.get("experience_id") == match.group(1)
                        for row in registry.get("records", [])
                        if row.get("id") == ident and row.get("revision") == revision
                    ),
                    None,
                )
                if historic:
                    print(json.dumps(historic, indent=2)); return 0
    application_match = APPLICATION.fullmatch(args.ref)
    if application_match:
        import experience_application_check
        requested_revision = int(application_match.group(1))
        registry, problems = experience_application_check.compile_application(root, True)
        if not problems and int(registry.get("application_revision", 0)) == requested_revision:
            print(json.dumps(
                experience_application_check.application_receipt(root, registry),
                indent=2,
            ))
            return 0
        history, history_findings = (
            experience_application_check.verified_application_ledger(root)
        )
        historic = None if history_findings else next(
            (
                row for row in history
                if int(row.get("application_revision", 0) or 0)
                == requested_revision
            ),
            None,
        )
        if historic is not None:
            print(json.dumps({
                "stage": "experience-design",
                "result_ref": args.ref,
                "result_type": "experience-application",
                "package_hash": historic.get("application_hash", ""),
                "status": "approved",
                "current": False,
            }, indent=2))
            return 0
    package_match = PACKAGE.fullmatch(args.ref)
    if package_match:
        package = resolve_package(root, package_match.group(1))
        if package:
            data = fields(package)
            requested_revision = int(package_match.group(2))
            if data.get("status") == "approved" and package_match.group(1) == package.name:
                registry, problems = compile_package(package, True)
                if not problems and int(registry["package_revision"]) == requested_revision:
                    print(json.dumps(package_receipt(package, registry), indent=2)); return 0
            history, history_problems = validate_process_ledger(
                package, int(data.get("revision", 0) or 0),
            )
            if not history_problems:
                historic = next(
                    (
                        row for row in history
                        if row.get("experience_id") == package_match.group(1)
                        and row.get("package_revision") == requested_revision
                    ),
                    None,
                )
                if historic:
                    print(json.dumps(historical_package_receipt(historic), indent=2)); return 0
    return fail("reference is not resolvable", 1)


def status(args) -> int:
    package = package_for(args.experience_root); registry, problems = compile_package(package, True)
    if problems: return print_problems(problems, True)
    if fields(package).get("status") != "approved":
        return fail("retired Experience has no current process receipt", 1)
    print(json.dumps(package_receipt(package, registry), indent=2)); return 0


def render_experience_navigation(root: Path) -> None:
    experience_root = root_for(root)
    experience_root.mkdir(parents=True, exist_ok=True)
    map_path = experience_root.parent / "maps" / "experience-design.md"
    if not map_path.is_file():
        return
    marker = "<!-- experience_compile.py: generated packages -->"
    retained = map_path.read_text(encoding="utf-8").split(marker, 1)[0].rstrip()
    rows = ["", marker, ""]
    for package in packages(experience_root):
        rows.append(f"- [[experience-design/experiences/{package.name}/experience|{package.name}]] — `{fields(package).get('status', 'draft')}`")
    atomic_write_bytes(
        map_path, ("\n".join([retained, *rows]).rstrip() + "\n").encode(),
    )


def reconcile_vault_navigation(root: Path) -> None:
    render_experience_navigation(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose"); p.add_argument("--root", required=True); p.add_argument("--process-ref", action="append", default=[]); p.add_argument("--experience", default=""); p.add_argument("--to", default=""); p.add_argument("--action", choices=("create", "update", "reuse", "rename", "retire"), default=""); p.add_argument("--application-action", choices=("create", "update", "reuse"), default=""); p.add_argument("--reason", default=""); p.add_argument("--origin-mode", choices=("manual", "requirement"), required=True); p.add_argument("--requirement", default=""); p.add_argument("--ba-ref", action="append", default=[]); p.add_argument("--solution-ref", action="append", default=[]); p.add_argument("--design-ref", action="append", default=[]); p.set_defaults(func=propose)
    p = sub.add_parser("init"); p.add_argument("--root", required=True); p.add_argument("--experience", required=True); p.add_argument("--origin-mode", choices=("manual", "requirement"), required=True); p.add_argument("--primary-process-ref", required=True); p.add_argument("--related-process-ref", action="append", default=[]); p.add_argument("--requirement", default=""); p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True); p.add_argument("--title", default=""); p.add_argument("--ba-ref", action="append", default=[]); p.add_argument("--solution-ref", action="append", default=[]); p.add_argument("--design-ref", action="append", default=[]); p.set_defaults(func=init)
    for name, handler in (("begin-revision", begin_revision), ("enter-review", enter_review), ("render", render), ("check", check), ("status", status), ("rename", rename), ("retire", retire)):
        p = sub.add_parser(name); p.add_argument("--experience-root", required=True)
        if name == "check": p.add_argument("--gate", action="store_true"); p.add_argument("--json", action="store_true")
        if name == "rename": p.add_argument("--to", required=True)
        if name in {"begin-revision", "rename", "retire"}:
            p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True)
        p.set_defaults(func=handler)
    p = sub.add_parser("stub"); p.add_argument("--experience-root", required=True); p.add_argument("--kind", choices=sorted(KIND), required=True); p.add_argument("--id", required=True); p.add_argument("--slug", required=True); p.add_argument("--title", default=""); p.add_argument("--revision", type=int, default=1); p.add_argument("--record-state", choices=("active", "retired"), default="active"); p.add_argument("--state-class", choices=sorted(STATE_CLASSES), default=""); p.add_argument("--derives-from", action="append", default=[]); p.add_argument("--criterion-ref", action="append", default=[]); p.add_argument("--supersedes", default="")
    for key in ("uses_design", "constrained_by", "journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"): p.add_argument("--" + key.replace("_", "-"), action="append", default=[])
    p.set_defaults(func=stub)
    for name, handler in (
        ("begin-application-revision", begin_application_revision),
        ("enter-application-review", enter_application_review),
    ):
        p = sub.add_parser(name); p.add_argument("--root", required=True); p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True); p.set_defaults(func=handler)
    p = sub.add_parser("render-application"); p.add_argument("--root", required=True); p.add_argument("--json", action="store_true"); p.set_defaults(func=render_application)
    p = sub.add_parser("check-application"); p.add_argument("--root", required=True); p.add_argument("--gate", action="store_true"); p.add_argument("--json", action="store_true"); p.set_defaults(func=check_application)
    p = sub.add_parser("application-status"); p.add_argument("--root", required=True); p.set_defaults(func=application_status)
    p = sub.add_parser("approve-set"); p.add_argument("--root", required=True); p.add_argument("--experience", action="append", default=[]); p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True); p.add_argument("--review-attestation", default=""); p.set_defaults(func=approve_set)
    p = sub.add_parser("resolve"); p.add_argument("--root", required=True); p.add_argument("--ref", required=True); p.set_defaults(func=resolve)
    args = parser.parse_args(argv)
    try:
        transaction_root = command_experience_root(args)
        with project_transaction_lock(transaction_root):
            recover_transaction(transaction_root)
            if args.command in MUTATING_COMMANDS:
                validate_mutation_surface(transaction_root)
            transaction_id = (
                begin_transaction(transaction_root, args.command)
                if args.command in MUTATING_COMMANDS else None
            )
            try:
                result = args.func(args)
            except BaseException:
                if transaction_id is not None:
                    rollback_transaction(transaction_root, transaction_id)
                raise
            if transaction_id is not None:
                if result == 0:
                    commit_transaction(transaction_root, transaction_id)
                else:
                    rollback_transaction(transaction_root, transaction_id)
            return result
    except (OSError, ValueError) as exc:
        return fail(str(exc), 2)


if __name__ == "__main__":
    raise SystemExit(main())
