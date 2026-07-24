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
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pmo_cli

# Team plugins whose subagents are recorded as tasks. Maps the agent-name
# prefix to the team plugin name; future team plugins add a line here.
TEAM_AGENT_PREFIXES = {
    "software-engineering-team-": "software-engineering-team",
}

# Tool-name vocabulary -> the canonical names the guards reason in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "Bash": "Bash",
}

PLUGIN_ROOTS_NAME = "plugin_roots.json"


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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
    tool_input = payload.get("tool_input")
    tool_input = dict(tool_input) if isinstance(tool_input, dict) else {}
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    out["cwd"] = str(payload.get("cwd", "") or "")
    targets: list[dict] = []
    if tool in ("Write", "Edit"):
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
        manifest = root / ".claude-plugin" / "plugin.json"
        try:
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


def team_role(agent_type: str) -> str | None:
    """Map a spawned agent's type to a snake_case role, or None when the
    agent does not belong to a registered team plugin.

    Plugin-shipped agents arrive namespaced as '<plugin>:<agent-name>'
    (measured empirically); direct spawns may carry the bare agent name."""
    bare = agent_type.split(":", 1)[-1]
    for prefix in TEAM_AGENT_PREFIXES:
        if bare.startswith(prefix):
            return bare[len(prefix):].replace("-", "_")
    return None
