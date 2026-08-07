"""Shared plumbing for the PMO hook scripts.

Hooks must never break a session: every entry point reads stdin defensively,
logs failures to logs/hooks.log in the data directory, and exits 0 unless the
hook's whole purpose is to block (the database write guard).

normalize_payload gives every hook one canonical payload shape (canonical
tool name, guaranteed tool_input/cwd fields, derived file_targets), so the
guard logic exists exactly once.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pmo_cli

AGENT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Tool-name vocabulary -> the canonical names the guards reason in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "apply_patch": "Edit",
    "Bash": "Bash", "exec_command": "Bash", "shell": "Bash",
}

PLUGIN_ROOTS_NAME = "plugin_roots.json"
PLUGIN_ROOTS_LOCK = ".plugin_roots.lock"
SESSION_STATE_DIR = "sessions"


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


PATCH_HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")


def parse_apply_patch(patch: str) -> list[dict]:
    """Parse Codex apply_patch input into the guard's write-target shape."""
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" \
            or lines[-1].strip() != "*** End Patch":
        raise ValueError("missing apply_patch boundary")
    targets: list[dict] = []
    index = 1
    while index < len(lines) - 1:
        if not lines[index].strip():
            index += 1
            continue
        match = PATCH_HEADER_RE.match(lines[index])
        if match is None:
            raise ValueError(f"unexpected apply_patch line {index + 1}")
        operation, file_path = match.groups()
        file_path = file_path.strip()
        if not file_path:
            raise ValueError("empty apply_patch path")
        index += 1
        body: list[str] = []
        move_to = ""
        while index < len(lines) - 1 and PATCH_HEADER_RE.match(lines[index]) is None:
            if lines[index].startswith("*** Move to: "):
                candidate = lines[index][len("*** Move to: "):].strip()
                if operation != "Update":
                    raise ValueError("apply_patch move is valid only for Update File")
                if not candidate:
                    raise ValueError("empty apply_patch move target")
                if move_to:
                    raise ValueError("duplicate apply_patch move target")
                move_to = candidate
            else:
                body.append(lines[index])
            index += 1
        added = "\n".join(line[1:] for line in body if line.startswith("+"))
        removed = "\n".join(line[1:] for line in body if line.startswith("-"))
        target = {"file_path": file_path, "operation": operation.lower()}
        target["patch_body"] = body
        if move_to:
            target["move_to"] = move_to
        if operation == "Add":
            target["content"] = added
        elif operation == "Delete":
            target["old_string"] = removed
        else:
            target["new_string"] = added
            target["old_string"] = removed
        targets.append(target)
        if move_to:
            targets.append({
                "file_path": move_to,
                "operation": "move-target",
                "new_string": added,
                "old_string": removed,
            })
    if not targets:
        raise ValueError("apply_patch contains no file operations")
    return targets


def normalize_payload(payload: dict) -> dict:
    """One canonical shape for hook stdin JSON.

    Returns the payload extended with:
      hook_event_name  string, always present
      tool_name        canonical (Write|Edit|Bash|original)
      tool_input       dict, always present
      cwd              string, always present
      file_targets     list of {file_path, content, new_string, old_string}
                       write targets (one for Write/Edit, empty for
                       shell/lifecycle events)
    """
    out = dict(payload)
    out["hook_event_name"] = str(payload.get("hook_event_name", ""))
    raw_input = payload.get("tool_input")
    tool_input = dict(raw_input) if isinstance(raw_input, dict) else {}
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    out["cwd"] = str(payload.get("cwd", "") or "")
    targets: list[dict] = []
    out["raw_tool_name"] = raw_tool
    if raw_tool == "apply_patch":
        patch = ""
        if isinstance(raw_input, str):
            patch = raw_input
        else:
            for key in ("patch", "input", "text"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    patch = value
                    break
        try:
            targets = parse_apply_patch(patch)
        except ValueError as exc:
            out["patch_parse_error"] = str(exc)
    elif tool in ("Write", "Edit"):
        file_path = str(tool_input.get("file_path", ""))
        if file_path:
            target = {"file_path": file_path}
            for key in ("content", "new_string", "old_string"):
                if key in tool_input:
                    target[key] = str(tool_input.get(key) or "")
            targets = [target]
    out["tool_name"] = tool
    out["tool_input"] = tool_input
    out["file_targets"] = targets
    return out


def plugin_roots_path() -> Path:
    return pmo_cli.data_dir() / PLUGIN_ROOTS_NAME


def team_agent_namespaces() -> set[str]:
    """Team namespaces generated into the PMO distribution at build time."""
    try:
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "team_plugins.json")
            .read_text(encoding="utf-8")
        )
        plugins = data.get("plugins", [])
        return {
            value for value in plugins
            if isinstance(value, str) and AGENT_NAME_RE.fullmatch(value)
        }
    except Exception:
        return set()


def session_state_path(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return pmo_cli.data_dir() / SESSION_STATE_DIR / f"{digest}.json"


def write_session_readiness(
    session_id: str, ready: bool, upgrade_status: str = ""
) -> None:
    if not session_id:
        return
    path = session_state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "pmo_ready": bool(ready),
        "upgrade_status": upgrade_status,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(tmp, path)


def clear_session_readiness(session_id: str) -> None:
    if not session_id:
        return
    try:
        session_state_path(session_id).unlink(missing_ok=True)
    except Exception as exc:
        log(f"clear_session_readiness failed: {exc}")


class RegistryLock:
    """Portable inter-process lock for the registry read-modify-write."""

    def __init__(self, parent: Path, timeout: float = 5.0):
        self.path = parent / PLUGIN_ROOTS_LOCK
        self.timeout = timeout

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.rmdir()
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for plugin registry lock")
                time.sleep(0.01)

    def __exit__(self, exc_type, exc, tb):
        try:
            self.path.rmdir()
        except OSError:
            pass
        return False


def plugin_manifest(root: Path) -> tuple[str, Path] | None:
    """Return the one native host manifest carried by an install root."""
    candidates = []
    for candidate in sorted(root.glob(".*-plugin/plugin.json")):
        name = candidate.parent.name
        if name.startswith(".") and name.endswith("-plugin"):
            candidates.append((name[1:-7], candidate))
    return candidates[0] if len(candidates) == 1 else None


def register_plugin_root(plugin_name: str, root: Path) -> None:
    """Record a plugin's install root in the shared registry the
    marketplace_run dispatcher resolves from."""
    try:
        registry_path = plugin_roots_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        with RegistryLock(registry_path.parent):
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
            except Exception:
                registry = {}
            if not isinstance(registry, dict):
                registry = {}
            registry["schema_version"] = 2
            version = ""
            manifest_info = plugin_manifest(root)
            host = ""
            try:
                if manifest_info is not None:
                    host, manifest = manifest_info
                    version = json.loads(
                        manifest.read_text(encoding="utf-8")).get("version", "")
            except Exception:
                pass
            if not host:
                raise ValueError(f"unambiguous host manifest missing at {root}")
            plugins = registry.setdefault("plugins", {})
            existing = plugins.get(plugin_name, {})
            if not isinstance(existing, dict):
                existing = {}
            hosts = existing.get("hosts", {})
            if not isinstance(hosts, dict):
                hosts = {}
            v1_root = Path(str(existing.get("root", "")))
            v1_info = plugin_manifest(v1_root) if v1_root.is_dir() else None
            if v1_info is not None:
                v1_host, v1_manifest = v1_info
                try:
                    v1_version = json.loads(
                        v1_manifest.read_text(encoding="utf-8")
                    ).get("version", "")
                except Exception:
                    v1_version = ""
                hosts.setdefault(v1_host, {
                    "root": str(v1_root.resolve()),
                    "version": v1_version,
                    "manifest_sha256": hashlib.sha256(
                        v1_manifest.read_bytes()
                    ).hexdigest(),
                    "registered_at": str(existing.get("registered_at", "")),
                })
            hosts[host] = {
                "root": str(root),
                "version": version,
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "registered_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
            }
            plugins[plugin_name] = {"hosts": hosts}
            fd, tmp = tempfile.mkstemp(dir=str(registry_path.parent),
                                       prefix=".plugin_roots.")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, registry_path)
    except Exception as exc:
        log(f"register_plugin_root failed for {plugin_name}: {exc}")


def log(message: str) -> None:
    try:
        target = pmo_cli.data_dir() / "logs" / "hooks.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
    except Exception:
        pass  # logging must never take a session down


def run_cli(argv: list[str]) -> int:
    """Invoke the CLI in-process, discarding stdout; failures are logged."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = pmo_cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # a hook must not crash on CLI errors
            log(f"cli-error {argv[0] if argv else '?'}: {exc}")
            return 1
    if code != 0:
        log(f"cli-nonzero {' '.join(argv[:3])}: {err.getvalue().strip()}")
    return code


def project_config(root: Path) -> Path | None:
    state = root / ".agentrof" / "agent-marketplace" / "project.json"
    try:
        workspace = str(json.loads(state.read_text(encoding="utf-8")).get("workspace", ""))
        configured = root / workspace / "config.json"
        if workspace and configured.is_file():
            return configured
    except Exception:
        pass
    conventional = root / "workspace" / "config.json"
    if conventional.is_file():
        return conventional
    candidates = sorted(root.glob("*/config.json"))
    recognized = []
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict) and value.get("project_key"):
            recognized.append(path)
    return recognized[0] if len(recognized) == 1 else None


def project_workspace(root: Path) -> str:
    config = project_config(root)
    return config.parent.name if config is not None else "workspace"


def resolve_project(cwd: str) -> tuple[str, str] | None:
    """Walk up to one unambiguous managed config with a project key."""
    try:
        current = Path(cwd).resolve()
    except Exception:
        return None
    for candidate in [current, *current.parents][:6]:
        config = project_config(candidate)
        if config is not None:
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
            except Exception:
                return None
            key = data.get("project_key", "")
            if key:
                return key, str(candidate)
            return None
    return None


def team_agent(agent_type: str, project_root: str) -> tuple[str, str] | None:
    """Return (canonical agent name, snake_case PMO role) for team agents.

    Claude identities are '<plugin>:<bare-agent>'. Codex identities are bare
    and count only when the matching project TOML carries the Agent Marketplace owner
    marker."""
    namespaces = team_agent_namespaces()
    if ":" in agent_type:
        namespace, bare = agent_type.split(":", 1)
        if namespace not in namespaces:
            return None
        if not AGENT_NAME_RE.fullmatch(bare):
            return None
        return bare, bare.replace("-", "_")

    if not AGENT_NAME_RE.fullmatch(agent_type):
        return None
    agent_file = Path(project_root) / ".codex" / "agents" / f"{agent_type}.toml"
    try:
        first_line = agent_file.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[0]
    except (OSError, IndexError):
        return None
    for namespace in namespaces:
        if first_line == (
            f"# Generated by Agent Marketplace {namespace}; do not edit by hand."
        ):
            return agent_type, agent_type.replace("-", "_")
    return None
