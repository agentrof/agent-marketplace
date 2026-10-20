#!/usr/bin/env python3
"""Keep the workspace layout fixed: no new top-level folder outside the config.

pre, a file tool (Write, Edit, MultiEdit, apply_patch): a write that would
create a new folder directly under ``workspace/`` or ``workspace/docs/`` is
denied unless ``workspace_layout`` in ``workspace/config.json`` allows it.
Folders that already exist stay writable, so a project with older folders keeps
working until it moves them. A shell command (Bash, PowerShell) is checked for
``mkdir`` and ``>``/``>>`` redirect targets. Without ``workspace/config.json``
the project is not set up yet and everything passes; a config that exists but
cannot be read or is invalid denies the write and logs it, since a guard whose
data source fails must not pass.

shortcut: only plain `mkdir` and `>`/`>>` targets are parsed; cp, mv, tee,
touch, New-Item, interpreter writes and variables in paths pass. Parse more
when agents are seen routing around the guard.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

import project_config
from autopilot_guard import TOOL_NAME_CANON, canonical_shell_command, read_payload, written_paths

MKDIR_RE = re.compile(r"(?<![\w./-])mkdir\s+([^;&|<>()]+)")
REDIRECT_RE = re.compile(r">>?\s*([^\s;&|<>()]+)")


def deny(message: str) -> int:
    print(f"workspace guard: {message}", file=sys.stderr)
    return 2


def log(root: Path, message: str) -> None:
    try:
        runtime = root / ".agentrof" / "agent-marketplace" / ".runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "workspace-guard.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")
    except OSError:
        pass


def shell_targets(command: str) -> list[str]:
    """Paths a plain ``mkdir`` creates as folders (with a ``/_`` child) and redirect targets."""
    targets = []
    for match in MKDIR_RE.finditer(command):
        try:
            words = shlex.split(match.group(1))
        except ValueError:
            words = match.group(1).split()
        targets += [f"{word.rstrip('/')}/_" for word in words if not word.startswith("-")]
    for match in REDIRECT_RE.finditer(command):
        if not match.group(1).startswith("&"):  # 2>&1 duplicates a stream, writes no file
            targets.append(match.group(1).strip("'\""))
    return targets


def refusal(path: Path) -> str | None:
    """Why writing ``path`` would create a folder the workspace layout refuses."""
    for workspace in path.parents:
        if workspace.name != "workspace" or not (workspace / "config.json").is_file():
            continue
        parts = path.relative_to(workspace).parts
        try:
            config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
            layout = project_config.setting(config, "workspace_layout")
            levels = (("workspace", workspace, parts[:-1]),
                      ("docs", workspace / "docs", parts[1:-1] if parts[0] == "docs" else ()))
            allowed = {key: set(layout[key]) for key, _base, _rest in levels}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if parts == ("config.json",):
                return None  # the config itself stays writable so it can be repaired
            message = (f"cannot read {workspace / 'config.json'} ({exc}); write denied until"
                       " the config is valid again")
            log(workspace.parent, message)
            return message
        for key, base, rest in levels:
            if rest and rest[0] not in allowed[key] and not (base / rest[0]).is_dir():
                where = "workspace/" + ("docs/" if key == "docs" else "")
                return (f"{where}{rest[0]}/ is not a folder workspace_layout.{key} in"
                        " workspace/config.json allows. Put project-specific data under"
                        " workspace/project/<name>/, or add the folder through the"
                        " configure entry.")
        return None
    return None


def main() -> int:
    payload = read_payload()
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    if tool in ("Bash", "PowerShell"):
        paths = shell_targets(canonical_shell_command(payload.get("tool_input"))[0])
    elif tool in ("Write", "Edit"):
        paths = written_paths(raw_tool, payload.get("tool_input"))
    else:
        return 0
    cwd = str(payload.get("cwd") or "")
    for file_path in paths:
        message = refusal(Path(os.path.abspath(os.path.join(cwd, file_path))))
        if message:
            return deny(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
