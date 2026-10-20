#!/usr/bin/env python3
"""Keep autopilot authority in the user's hands: only the user arms a grant.

The grant and the arming record under
``.agentrof/agent-marketplace/.runtime/autopilot/`` are written only by the
packaged autopilot.py and its user-prompt hook.

- pre, a file tool (Write, Edit, MultiEdit, apply_patch): a write into that
  directory is denied.
- pre and post, a shell command: pre snapshots both files into a private
  recovery capsule outside the project; post puts back what the command added
  to them. A command may leave them as they were, delete them or end the grant
  it held. The packaged autopilot.py is the one shell writer that may change
  them, and bytes the package marked as its latest write (autopilot_provenance)
  landed beside the command, not through it, and stay. A command whose capsule
  is lost keeps only the files whose bytes are the package's latest write.

Stdlib only, plus the packaged autopilot_provenance module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import autopilot_provenance

TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "apply_patch": "Edit",
    "PowerShell": "Bash",
}
PATCH_TARGET_RE = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.MULTILINE)
SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
PYTHON_COMMAND_RE = re.compile(r"^python(?:3(?:[.][0-9]+)?)?$")
WINDOWS_CMD_ALWAYS_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f^%!`$]")
SANCTIONED_SHELL_ASSIGNMENTS = {"PYTHONDONTWRITEBYTECODE": "1"}
RECOVERY_TTL_SECONDS = 24 * 60 * 60
AUTOPILOT_RUNTIME = (".agentrof", "agent-marketplace", ".runtime", "autopilot")
AUTOPILOT_AUTHORITY = ("grant.json", "arming.json")
AUTOPILOT_SCRIPT = "skill-content/autopilot/scripts/autopilot.py"
AUTOPILOT_ENDED_STATES = ("expired", "completed", "revoked", "replaced")
AUTOPILOT_GUARD_MESSAGE = (
    "autopilot runtime state is written only by the packaged autopilot.py and its"
    " hooks; only the user arms a grant")


def read_payload() -> dict:
    try:
        value = json.load(sys.stdin)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def deny(message: str) -> int:
    print(f"autopilot guard: {message}", file=sys.stderr)
    return 2


def canonical_shell_command(tool_input: dict) -> tuple[str, str]:
    """Resolve the documented command field and one defensive host alias."""
    if not isinstance(tool_input, dict):
        return "", "tool_input must be an object"
    command_present = "command" in tool_input
    cmd_present = "cmd" in tool_input
    command = tool_input.get("command")
    cmd = tool_input.get("cmd")
    if command_present and not isinstance(command, str):
        return "", "tool_input.command must be a string"
    if cmd_present and not isinstance(cmd, str):
        return "", "tool_input.cmd must be a string"
    if command_present and cmd_present and command != cmd:
        return "", "tool_input.command and tool_input.cmd disagree"
    return str(command if command_present else cmd if cmd_present else ""), ""


def written_paths(raw_tool: str, tool_input) -> list[str]:
    """The paths a file tool or an apply_patch call writes."""
    if raw_tool != "apply_patch":
        path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        return [path] if isinstance(path, str) and path else []
    patch = tool_input if isinstance(tool_input, str) else ""
    for key in ("command", "patch", "patchText", "input", "text"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if not patch and isinstance(value, str) and value.strip():
            patch = value
    return [target.strip() for target in PATCH_TARGET_RE.findall(patch) if target.strip()]


def in_autopilot_runtime(file_path: str) -> bool:
    """Whether a path lies in an autopilot runtime directory, as written or once resolved."""
    size = len(AUTOPILOT_RUNTIME)
    for path in (Path(os.path.abspath(file_path)), Path(file_path).resolve()):
        parts = path.parts
        if any(parts[index:index + size] == AUTOPILOT_RUNTIME
               for index in range(len(parts) - size + 1)):
            return True
    return False


def path_is_alias(path: Path) -> bool:
    """Recognize symlinks and Windows reparse-point directory aliases."""
    try:
        mode = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(mode, "st_file_attributes", 0) & 0x400)


def shell_project(payload: dict) -> Path:
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    for parent in (cwd, *cwd.parents):
        workspace = parent / "workspace"
        if (workspace / "docs").is_dir() or (workspace / "config.json").is_file():
            return parent
        if (parent / ".git").exists():
            return parent
    return cwd


# ---------------------------------------------------------------------------
# The packaged writer: one direct, unchanged invocation of autopilot.py.
# ---------------------------------------------------------------------------


def _installed_script_path(value: str, cwd: Path, name: str, relative: str) -> Path | None:
    expanded = os.path.expandvars(value)
    candidate = _lexical_executable_path(expanded, cwd)
    if candidate is None or candidate.name != name:
        return None
    # In a built host package this hook and autopilot_provenance are siblings.
    # Source-tree tests run the uncomposed overlay, so fall back to the
    # canonical directory that supplied the imported provenance module.
    hook_directory = Path(__file__).resolve().parent
    imported_directory = Path(autopilot_provenance.__file__).resolve().parent
    packaged = hook_directory == imported_directory
    expected = (hook_directory if packaged else imported_directory).parent / relative
    try:
        if path_is_alias(expected) or not expected.is_file() or expected.stat().st_nlink != 1:
            return None
        lexical_expected = expected.parent.resolve() / expected.name
        if candidate != lexical_expected or candidate.resolve() != expected.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    if packaged:
        manifest = hook_directory.parent / ".agent-marketplace-package.json"
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
            expected_hash = package["files"][relative]
            actual_hash = hashlib.sha256(expected.read_bytes()).hexdigest()
        except (KeyError, OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            return None
    return candidate


def _lexical_executable_path(value: str, cwd: Path) -> Path | None:
    if not value or "$" in value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute() or "/" in value or "\\" in value:
        path = Path(os.path.abspath(path if path.is_absolute() else cwd / path))
        try:
            return path.parent.resolve() / path.name
        except (OSError, RuntimeError):
            return None
    resolved = shutil.which(value)
    if not resolved or not Path(resolved).is_absolute():
        return None
    try:
        return Path(resolved).parent.resolve() / Path(resolved).name
    except (OSError, RuntimeError):
        return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _runtime_values() -> list[str]:
    return [value for value in (sys.executable, getattr(sys, "_base_executable", "")) if value]


def _trusted_runtime_targets() -> set[Path]:
    targets = set()
    for value in _runtime_values():
        try:
            targets.add(Path(value).resolve())
        except (OSError, RuntimeError):
            continue
    return targets


def _trusted_runtime_origins() -> set[Path]:
    origins = set()
    for value in _runtime_values():
        path = Path(value)
        if not path.is_absolute():
            continue
        try:
            origins.add(path.parent.resolve() / path.name)
        except (OSError, RuntimeError):
            continue
    return origins


# Homebrew links python3 into <prefix>/bin, outside the keg directory that
# sys.executable reports (<prefix>/opt/python@3.X/bin).
HOMEBREW_PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"), Path("/home/linuxbrew/.linuxbrew"))


def _homebrew_python_launcher_matches(candidate: Path) -> bool:
    """Bind Homebrew's prefix launcher to the Homebrew interpreter running this hook."""
    minor = f"3.{sys.version_info[1]}"
    running = {Path(value) for value in _runtime_values() if Path(value).is_absolute()}
    for prefix in HOMEBREW_PREFIXES:
        if candidate not in (prefix / "bin" / "python3", prefix / "bin" / f"python{minor}"):
            continue
        keg = prefix / "opt" / f"python@{minor}"
        if not any(_path_within(value, keg) for value in running):
            return False
        try:
            link = candidate.lstat()
            directory = candidate.parent.stat()
        except OSError:
            return False
        owners = {0, os.getuid()} if hasattr(os, "getuid") else {0}
        # Homebrew keeps its prefix group-writable for macOS's admin group (gid 80).
        writable_groups = {0, 80} if sys.platform == "darwin" else {0}
        if link.st_uid not in owners or directory.st_uid not in owners \
                or directory.st_mode & 0o002 \
                or (directory.st_mode & 0o020 and directory.st_gid not in writable_groups):
            return False
        return candidate.resolve() in _trusted_runtime_targets()
    return False


def trusted_python_command(value: str, cwd: Path) -> bool:
    name = re.split(r"[/\\]", value)[-1]
    if sys.platform == "win32":
        name = name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
    # A bare name can be intercepted by a shell function or alias before PATH resolution.
    if not PYTHON_COMMAND_RE.fullmatch(name) or "$" in value or not Path(value).is_absolute():
        return False
    candidate = _lexical_executable_path(value, cwd)
    if candidate is None:
        return False
    project = shell_project({"cwd": str(cwd)}).resolve()
    try:
        if candidate in _trusted_runtime_origins() and candidate.resolve() in _trusted_runtime_targets():
            return True
        if _path_within(candidate, project) or _path_within(candidate.resolve(), project):
            return False
        # A managed runtime's python3 symlink beside the interpreter running this hook.
        if candidate.resolve() in _trusted_runtime_targets():
            directories = {Path(value).parent.resolve() for value in _runtime_values()
                           if Path(value).is_absolute()}
            if candidate.parent.resolve() in directories:
                return True
        return _homebrew_python_launcher_matches(candidate)
    except (OSError, RuntimeError):
        return False


def has_shell_composition(command: str) -> bool:
    """Detect shell composition outside quotes; quoted values are data."""
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif quote == "'":
            if char == "'":
                quote = ""
        elif quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = ""
            elif char == "`" or command[index:index + 2] == "$(":
                return True
        elif char == "\\":
            escaped = True
        elif char in ("'", '"'):
            quote = char
        elif char in "\n\r;|&<>`" or command[index:index + 2] == "$(":
            return True
        index += 1
    return bool(quote or escaped)


def windows_cmd_has_composition(command: str) -> bool:
    """Reject cmd.exe operators outside canonical double-quoted argv."""
    quoted = False
    backslashes = 0
    for char in command:
        if char == '"' and backslashes % 2 == 0:
            quoted = not quoted
        elif not quoted and char in "&|<>()":
            return True
        backslashes = backslashes + 1 if char == "\\" else 0
    return quoted


def direct_shell_tokens(payload: dict) -> tuple[list[str], Path] | None:
    """Parse one direct command with only the closed safe assignment set."""
    command, error = canonical_shell_command(payload.get("tool_input", {}))
    command = command.strip()
    if error or not command:
        return None
    if sys.platform == "win32":
        # PowerShell carries no writer authorization until a native launcher contract exists.
        if payload.get("shell_family") != "cmd" or payload.get("raw_tool_name") == "PowerShell" \
                or WINDOWS_CMD_ALWAYS_UNSAFE_RE.search(command) is not None \
                or windows_cmd_has_composition(command) \
                or any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in command):
            return None
        try:
            raw_tokens = shlex.split(command, posix=False)
        except ValueError:
            return None
        tokens = []
        for token in raw_tokens:
            if token[:1] == '"':
                if len(token) < 2 or token[-1] != token[0]:
                    return None
                token = token[1:-1]
            if not token or "'" in token or '"' in token:
                return None
            tokens.append(token)
        if not tokens or subprocess.list2cmdline(tokens) != command \
                or SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
            return None
    else:
        # An explicit custom or unknown shell identity never becomes a grant.
        if "shell_family" in payload and payload.get("shell_family") != "posix":
            return None
        if has_shell_composition(command):
            return None
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return None
        while tokens and SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
            name, value = tokens.pop(0).split("=", 1)
            if SANCTIONED_SHELL_ASSIGNMENTS.get(name) != value:
                return None
    return tokens, Path(str(payload.get("cwd") or ".")).resolve()


def sanctioned_autopilot_writer(payload: dict) -> bool:
    """Recognize one direct invocation of the packaged autopilot.py, any verb."""
    parsed = direct_shell_tokens(payload)
    if parsed is None:
        return False
    tokens, cwd = parsed
    if len(tokens) < 2 or not trusted_python_command(tokens[0], cwd):
        return False
    # ``-B`` only disables bytecode emission; it is the one inert flag accepted.
    script_index = 2 if tokens[1] == "-B" else 1
    if len(tokens) <= script_index:
        return False
    return _installed_script_path(tokens[script_index], cwd, "autopilot.py", AUTOPILOT_SCRIPT) is not None


# ---------------------------------------------------------------------------
# Snapshot and restore of the two authority files.
# ---------------------------------------------------------------------------


def autopilot_main_checkout(project: Path) -> Path:
    """The main worktree of the checkout holding project, read from its .git link.

    A linked worktree keeps the grant of the main worktree; its .git file
    names the common directory inside it.
    """
    marker = project / ".git"
    try:
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text[len("gitdir:"):].strip())
                gitdir = gitdir if gitdir.is_absolute() else project / gitdir
                common = gitdir / "commondir"
                if common.is_file():
                    gitdir = gitdir / common.read_text(encoding="utf-8").strip()
                gitdir = Path(os.path.abspath(gitdir))
                if gitdir.name == ".git":
                    return gitdir.parent
    except (OSError, UnicodeError):
        pass
    return project


def read_autopilot_files(directory: Path) -> dict:
    files = {}
    for name in AUTOPILOT_AUTHORITY:
        path = directory / name
        try:
            files[name] = base64.b64encode(path.read_bytes()).decode("ascii") \
                if path.is_file() and not path_is_alias(path) else None
        except OSError:
            files[name] = None
    return files


def autopilot_snapshot(project: Path) -> dict:
    directory = autopilot_main_checkout(project).joinpath(*AUTOPILOT_RUNTIME)
    return {"directory": str(directory), "files": read_autopilot_files(directory)}


def valid_autopilot_snapshot(snapshot: object) -> bool:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("directory"), str):
        return False
    files = snapshot.get("files")
    return isinstance(files, dict) and set(files) == set(AUTOPILOT_AUTHORITY) \
        and all(value is None or isinstance(value, str) for value in files.values())


def ends_same_grant(before: bytes, after: bytes) -> bool:
    """Whether a grant change only ends the grant it held, which adds no authority."""
    try:
        old = json.loads(before.decode("utf-8"))
        new = json.loads(after.decode("utf-8"))
    except (UnicodeError, ValueError):
        return False
    if not isinstance(old, dict) or not isinstance(new, dict) \
            or old.get("id") != new.get("id") or new.get("state") not in AUTOPILOT_ENDED_STATES:
        return False
    ending = {"state", "ended_at", "completion", "replaced_by"}
    return all(old.get(key) == new.get(key) for key in set(old) | set(new) if key not in ending)


def restore_autopilot_file(path: Path, encoded: str | None) -> str | None:
    runtime = path.parent
    for part in (runtime, *list(runtime.parents)[:3]):
        if path_is_alias(part):
            return f"{part} is a link, which autopilot.py refuses"
    try:
        if path_is_alias(path) or path.is_file():
            if encoded is None:
                path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        if encoded is None:
            return None
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore-", dir=str(runtime))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(base64.b64decode(encoded))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def violation_message(restored: list[str]) -> str:
    return ("Bash changed autopilot runtime state outside the packaged autopilot.py; "
            + ", ".join(restored) + " put back as the command found it. "
            + AUTOPILOT_GUARD_MESSAGE)


def autopilot_violation(before: object, writer_allowed: bool) -> str:
    """Restore what a shell command added to autopilot authority, and name it.

    A command may leave the grant and the arming record as they were, delete
    them or end the grant it held; anything else, a new or widened grant or
    an arming record, is put back as the command found it. A change whose
    bytes autopilot.py or its user-prompt hook marked as their latest write
    landed while the command ran, not through it, and stays.
    """
    if writer_allowed or not valid_autopilot_snapshot(before):
        return ""
    directory = Path(before["directory"])
    after = read_autopilot_files(directory)
    restored = []
    for name in AUTOPILOT_AUTHORITY:
        old, new = before["files"][name], after[name]
        if new == old or new is None:
            continue
        if autopilot_provenance.written_by_package(directory, name, base64.b64decode(new)):
            continue
        if name == "grant.json" and old is not None \
                and ends_same_grant(base64.b64decode(old), base64.b64decode(new)):
            continue
        error = restore_autopilot_file(directory / name, old)
        restored.append(name if error is None else f"{name} (restore failed: {error})")
    # A grant put back above was made by consuming the arming record, so put that back too:
    # the state is then what the command found, and a sanctioned `on` can still complete it.
    if any(entry.startswith("grant.json") for entry in restored) \
            and before["files"]["arming.json"] is not None and after["arming.json"] is None:
        error = restore_autopilot_file(directory / "arming.json", before["files"]["arming.json"])
        restored.append("arming.json" if error is None else f"arming.json (restore failed: {error})")
    return violation_message(restored) if restored else ""


def unattested_violation(project: Path) -> str:
    """Without a pre-command snapshot, keep only the bytes the package wrote last."""
    directory = autopilot_main_checkout(project).joinpath(*AUTOPILOT_RUNTIME)
    restored = []
    for name, value in read_autopilot_files(directory).items():
        if value is None or autopilot_provenance.written_by_package(
                directory, name, base64.b64decode(value)):
            continue
        error = restore_autopilot_file(directory / name, None)
        restored.append(f"{name} removed" if error is None else f"{name} (removal failed: {error})")
    return violation_message(restored) if restored else ""


# ---------------------------------------------------------------------------
# The private recovery capsule that carries one command's snapshot.
# ---------------------------------------------------------------------------


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def guard_event_id(payload: dict) -> str:
    for key in ("tool_use_id", "tool_call_id", "event_id"):
        if payload.get(key):
            return str(payload[key])
    return ""


def guard_cwd(payload: dict) -> str:
    try:
        return str(Path(str(payload.get("cwd") or ".")).resolve())
    except (OSError, RuntimeError):
        return str(payload.get("cwd") or ".")


def locator_digest(session_id: str, event: str, cwd: str) -> str:
    return sha256_text(canonical_json({"session_id": session_id, "event": event, "cwd": cwd}))


def guard_locator(payload: dict) -> str:
    """Separate equal host event IDs that occur in different directories."""
    return locator_digest(str(payload.get("session_id") or "unknown"),
                          guard_event_id(payload), guard_cwd(payload))


def guard_command_digest(payload: dict) -> str:
    command, error = canonical_shell_command(payload.get("tool_input", {}))
    if error:
        tool_input = payload.get("tool_input")
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        command = canonical_json({"command": tool_input.get("command"), "cmd": tool_input.get("cmd")})
    return sha256_text(command)


def binding_digest(locator: str, command_sha256: str, cwd: str) -> str:
    return sha256_text(canonical_json({"locator": locator, "command_sha256": command_sha256, "cwd": cwd}))


def recovery_binding_matches(state: dict, payload: dict) -> bool:
    """Accept one command whose two events report different directories.

    Rebuild the binding from the identity the capsule recorded, keeping the
    post event's command digest: a genuinely different command still fails.
    """
    command = guard_command_digest(payload)
    if state.get("binding") == binding_digest(guard_locator(payload), command, guard_cwd(payload)):
        return True
    cwd = str(state.get("cwd") or "")
    locator = locator_digest(str(state.get("session_id") or "unknown"),
                             str(state.get("event_id") or ""), cwd)
    return state.get("binding") == binding_digest(locator, command, cwd)


def recovery_root() -> Path:
    user = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"agentrof-autopilot-guard-{user}"


def recovery_path(payload: dict) -> Path:
    return recovery_root() / f"{guard_locator(payload)}.json"


def load_json_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def recovery_path_for_post(payload: dict) -> Path:
    """Recover a host-drifted event only when correlation is unambiguous."""
    exact = recovery_path(payload)
    if exact.is_file():
        return exact
    session = str(payload.get("session_id") or "unknown")
    event = guard_event_id(payload)
    try:
        paths = list(recovery_root().glob("*.json"))
    except OSError:
        return exact
    matching = []
    for path in paths:
        envelope = load_json_file(path)
        state = envelope.get("state") if envelope else None
        if not isinstance(state, dict) or state.get("session_id") != session:
            continue
        if (state.get("event_id") == event) if event else (
                state.get("command_sha256") == guard_command_digest(payload)
                and state.get("cwd") == guard_cwd(payload)):
            matching.append(path)
    return matching[0] if len(matching) == 1 else exact


def prepare_recovery_root() -> Path:
    root = recovery_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path_is_alias(root) or not root.is_dir():
        raise OSError("autopilot guard recovery root is not a private directory")
    os.chmod(root, 0o700)
    # A capsule whose post never ran (host timeout, interrupted command) expires.
    cutoff = time.time() - RECOVERY_TTL_SECONDS
    for candidate in root.glob("*.json"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue
    return root


def atomic_create_text(path: Path, text: str) -> None:
    """Publish a fully-written capsule only when this event has no owner."""
    fd, temporary = tempfile.mkstemp(prefix=".recovery-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, path, follow_symlinks=False)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_recovery(payload: dict, path: Path) -> tuple[dict | None, str]:
    envelope = load_json_file(path)
    if envelope is None:
        return None, "recovery capsule is missing or unreadable"
    state = envelope.get("state")
    if not isinstance(state, dict) or envelope.get("state_sha256") != sha256_text(canonical_json(state)):
        return None, "recovery capsule checksum does not match"
    if state.get("session_id") != str(payload.get("session_id") or "unknown") \
            or not valid_autopilot_snapshot(state.get("autopilot")) \
            or not isinstance(state.get("autopilot_writer_allowed"), bool) \
            or not isinstance(state.get("project"), str):
        return None, "recovery capsule is invalid"
    if not recovery_binding_matches(state, payload):
        state = dict(state, autopilot_writer_allowed=False)
    return state, ""


def shell_snapshot(payload: dict) -> int:
    if not guard_event_id(payload):
        return deny("the shell hook payload has no stable tool-call id; refusing a"
                    " command whose recovery state could not be correlated")
    project = shell_project(payload).resolve()
    state = {
        "binding": binding_digest(guard_locator(payload), guard_command_digest(payload), guard_cwd(payload)),
        "command_sha256": guard_command_digest(payload),
        "cwd": guard_cwd(payload),
        "event_id": guard_event_id(payload),
        "project": str(project),
        "session_id": str(payload.get("session_id") or "unknown"),
        "autopilot": autopilot_snapshot(project),
        "autopilot_writer_allowed": sanctioned_autopilot_writer(payload),
    }
    capsule = canonical_json({"state": state, "state_sha256": sha256_text(canonical_json(state))})
    try:
        prepare_recovery_root()
        atomic_create_text(recovery_path(payload), capsule)
    except FileExistsError:
        return deny("a Bash guard already owns this session/event identity;"
                    " refusing to overwrite its recovery state")
    except OSError as exc:
        return deny(f"autopilot recovery capsule could not be created: {exc}")
    return 0


def shell_verify(payload: dict) -> int:
    path = recovery_path_for_post(payload)
    state, error = load_recovery(payload, path)
    try:
        if state is None:
            message = unattested_violation(shell_project(payload).resolve())
            return deny(error + ("; " + message if message else ""))
        message = autopilot_violation(state["autopilot"], state["autopilot_writer_allowed"])
        return deny(message) if message else 0
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    payload = read_payload()
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    payload["raw_tool_name"] = raw_tool
    if tool == "Bash":
        if mode == "post":
            return shell_verify(payload)
        _command, error = canonical_shell_command(payload.get("tool_input", {}))
        if error:
            return deny(f"the shell payload is ambiguous ({error}); the autopilot guard fails closed.")
        return shell_snapshot(payload)
    if tool not in ("Write", "Edit") or mode != "pre":
        return 0
    cwd = str(payload.get("cwd") or "")
    for file_path in written_paths(raw_tool, payload.get("tool_input")):
        if in_autopilot_runtime(os.path.join(cwd, file_path) if cwd else file_path):
            return deny(AUTOPILOT_GUARD_MESSAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
