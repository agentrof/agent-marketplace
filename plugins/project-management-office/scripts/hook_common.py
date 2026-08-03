"""Shared plumbing for the PMO hook scripts.

Hooks must never break a session: every entry point reads stdin defensively,
logs failures to hooks.log in the data directory, and exits 0 unless the
hook's whole purpose is to block (the database write guard).

normalize_payload gives every hook one canonical payload shape (canonical
tool name, guaranteed tool_input/cwd fields, derived file_targets), so the
guard logic exists exactly once.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pmo_cli

# Team plugins whose subagents are recorded as tasks. Claude supplies the
# plugin namespace; Codex supplies a bare name backed by an Agentrof-owned
# project agent file. The prefix map is compatibility for pre-9.2 identities.
TEAM_AGENT_NAMESPACES = {"software-engineering-team"}
LEGACY_TEAM_AGENT_PREFIXES = {
    "software-engineering-team-": "software-engineering-team",
}
AGENT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Tool-name vocabulary -> the canonical names the guards reason in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "apply_patch": "Edit",
    "Bash": "Bash", "exec_command": "Bash", "shell": "Bash",
}

PLUGIN_ROOTS_NAME = "plugin_roots.json"


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
                move_to = lines[index][len("*** Move to: "):].strip()
            else:
                body.append(lines[index])
            index += 1
        added = "\n".join(line[1:] for line in body if line.startswith("+"))
        removed = "\n".join(line[1:] for line in body if line.startswith("-"))
        target = {"file_path": file_path, "operation": operation.lower()}
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


def plugin_manifest(root: Path) -> Path | None:
    """Return the active host manifest, preferring the native Codex one."""
    for relative in (
        Path(".codex-plugin") / "plugin.json",
        Path(".claude-plugin") / "plugin.json",
    ):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def register_plugin_root(plugin_name: str, root: Path) -> None:
    """Record a plugin's install root in the shared registry the
    agentrof_run dispatcher resolves from. Atomic write (temp file +
    replace): two sessions may race."""
    try:
        registry_path = plugin_roots_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            registry = {}
        if not isinstance(registry, dict):
            registry = {}
        registry.setdefault("schema_version", 1)
        version = ""
        manifest = plugin_manifest(root)
        try:
            if manifest is not None:
                version = json.loads(
                    manifest.read_text(encoding="utf-8")).get("version", "")
        except Exception:
            pass
        plugins = registry.setdefault("plugins", {})
        plugins[plugin_name] = {
            "root": str(root),
            "version": version,
            "registered_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        }
        fd, tmp = tempfile.mkstemp(dir=str(registry_path.parent),
                                   prefix=".plugin_roots.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, registry_path)
    except Exception as exc:
        log(f"register_plugin_root failed for {plugin_name}: {exc}")


def log(message: str) -> None:
    try:
        target = pmo_cli.data_dir() / "hooks.log"
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


def resolve_project(cwd: str) -> tuple[str, str] | None:
    """Walk up from cwd to a directory holding workspace/config.json with a
    project_key. Returns (project_key, project_root) or None."""
    try:
        current = Path(cwd).resolve()
    except Exception:
        return None
    for candidate in [current, *current.parents][:6]:
        config = candidate / "workspace" / "config.json"
        if config.is_file():
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
    and count only when the matching project TOML carries the Agentrof owner
    marker. Legacy prefixed identities remain readable during migration."""
    if ":" in agent_type:
        namespace, bare = agent_type.split(":", 1)
        if namespace not in TEAM_AGENT_NAMESPACES:
            return None
        legacy = f"{namespace}-"
        if bare.startswith(legacy):
            bare = bare[len(legacy):]
        if not AGENT_NAME_RE.fullmatch(bare):
            return None
        return bare, bare.replace("-", "_")

    for prefix in LEGACY_TEAM_AGENT_PREFIXES:
        if agent_type.startswith(prefix):
            bare = agent_type[len(prefix):]
            if AGENT_NAME_RE.fullmatch(bare):
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
    for namespace in TEAM_AGENT_NAMESPACES:
        if first_line == (
            f"# Generated by Agentrof {namespace}; do not edit by hand."
        ):
            return agent_type, agent_type.replace("-", "_")
    return None


def team_role(agent_type: str, project_root: str = "") -> str | None:
    identity = team_agent(agent_type, project_root)
    return identity[1] if identity is not None else None
