"""Shared plumbing for the PMO hook scripts.

Hooks must never break a session: every entry point reads stdin defensively,
logs failures to hooks.log in the data directory, and exits 0 unless the
hook's whole purpose is to block (the database write guard).

The same scripts serve every supported harness. Payload differences (event
name casing, tool-name vocabulary, top-level file_path/command fields,
apply_patch envelopes) are absorbed by normalize_payload, so the guard
logic exists exactly once.
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

# Team plugins whose subagents are recorded as tasks. Maps the agent-name
# prefix to the team plugin name; future team plugins add a line here.
TEAM_AGENT_PREFIXES = {
    "software-engineering-team-": "software-engineering-team",
}

# Harness tool-name vocabulary -> the canonical names the guards reason in.
TOOL_NAME_CANON = {
    "Write": "Write", "write": "Write", "create_file": "Write",
    "search_replace": "Write",
    "Edit": "Edit", "edit": "Edit", "edit_file": "Edit", "MultiEdit": "Edit",
    "Bash": "Bash", "bash": "Bash", "run_shell_command": "Bash",
    "apply_patch": "apply_patch",
}

# Events whose payload implies the tool when no tool_name arrives.
EVENT_IMPLIED_TOOL = {
    "BeforeShellExecution": "Bash",
    "AfterShellExecution": "Bash",
    "AfterFileEdit": "Edit",
}

APPLY_PATCH_FILE_RE = re.compile(
    r"^\*\*\* (?:Update|Add) File: (.+)$", re.MULTILINE)

PLUGIN_ROOTS_NAME = "plugin_roots.json"


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pascal(event: str) -> str:
    return event[:1].upper() + event[1:] if event else ""


def apply_patch_targets(patch_text: str) -> list[dict]:
    """File targets of an apply_patch envelope: path plus the added lines
    (the content a write-guard judges). Deleted files carry no content and
    are not write targets."""
    targets: list[dict] = []
    sections = APPLY_PATCH_FILE_RE.split(patch_text)
    # split yields [prefix, path1, body1, path2, body2, ...]
    for i in range(1, len(sections) - 1, 2):
        path = sections[i].strip()
        body = sections[i + 1]
        added = "\n".join(line[1:] for line in body.splitlines()
                          if line.startswith("+"))
        targets.append({"file_path": path, "content": added})
    return targets


def normalize_payload(payload: dict) -> dict:
    """One canonical shape for every harness's hook stdin JSON.

    Returns the payload extended with:
      hook_event_name  PascalCase
      tool_name        canonical (Write|Edit|Bash|apply_patch|original)
      tool_input       dict; file_path/content/command lifted from
                       top-level fields when the harness puts them there
      cwd              falls back to workspace_roots[0]
      file_targets     list of {file_path, content, new_string, old_string}
                       write targets (one for Write/Edit, one per file for
                       apply_patch, empty for shell/lifecycle events)
    """
    out = dict(payload)
    event = _pascal(str(payload.get("hook_event_name", "")))
    out["hook_event_name"] = event
    tool_input = payload.get("tool_input")
    tool_input = dict(tool_input) if isinstance(tool_input, dict) else {}
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    if not tool and event in EVENT_IMPLIED_TOOL:
        tool = EVENT_IMPLIED_TOOL[event]
    if "command" not in tool_input and isinstance(payload.get("command"), str):
        tool_input["command"] = payload["command"]
    if "file_path" not in tool_input and isinstance(payload.get("file_path"), str):
        tool_input["file_path"] = payload["file_path"]
    cwd = str(payload.get("cwd", "") or "")
    if not cwd:
        roots = payload.get("workspace_roots")
        if isinstance(roots, list) and roots:
            cwd = str(roots[0])
    out["cwd"] = cwd
    targets: list[dict] = []
    if tool == "apply_patch":
        patch_text = str(tool_input.get("patch")
                         or tool_input.get("input")
                         or tool_input.get("command") or "")
        targets = apply_patch_targets(patch_text)
        if targets:
            tool = "Write"
            tool_input.setdefault("file_path", targets[0]["file_path"])
            tool_input.setdefault("content", targets[0]["content"])
    elif tool in ("Write", "Edit"):
        file_path = str(tool_input.get("file_path", "")
                        or tool_input.get("path", ""))
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


def runtime_data() -> dict:
    """The generated harness_runtime.json next to this script (signals,
    install roots, sandbox stanzas); empty when absent."""
    try:
        path = Path(__file__).resolve().parent / "harness_runtime.json"
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_harness(payload: dict) -> str:
    """Deterministic harness detection from hook-time signals: the Cursor
    stdin carries cursor_version, the Codex hook env carries PLUGIN_ROOT
    alongside CLAUDE_PLUGIN_ROOT, Claude Code carries CLAUDE_PLUGIN_ROOT
    alone. The signal table ships in harness_runtime.json; this fallback
    mirrors it so detection works even before the first sync."""
    signals = runtime_data().get("harness_signals") or {}
    for harness_id in ("cursor", "codex", "claude_code"):
        sig = signals.get(harness_id) or {}
        field = sig.get("stdin_field") or ""
        if field and field in payload:
            return harness_id
    if "cursor_version" in payload or os.environ.get("CURSOR_PLUGIN_ROOT"):
        return "cursor"
    if os.environ.get("PLUGIN_ROOT"):
        return "codex"
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude_code"
    return "unknown"


def plugin_roots_path() -> Path:
    return pmo_cli.data_dir() / PLUGIN_ROOTS_NAME


def register_plugin_root(plugin_name: str, root: Path,
                         harness: str = "unknown") -> None:
    """Record a plugin's install root (and the detected harness) in the
    shared registry the agentrof_run dispatcher resolves from. Atomic
    write (temp file + replace): two sessions may race."""
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
        if harness and harness != "unknown":
            registry["harness"] = harness
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
