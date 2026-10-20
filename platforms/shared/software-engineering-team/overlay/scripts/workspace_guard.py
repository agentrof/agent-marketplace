#!/usr/bin/env python3
"""Keep the workspace layout fixed: no new top-level folder outside the config.

pre, a file tool (Write, Edit, MultiEdit, apply_patch): a write that would
create a new folder directly under ``workspace/`` or ``workspace/docs/`` is
denied unless ``workspace_layout`` in ``workspace/config.json`` allows it.
Folders that already exist stay writable, so a project with older folders keeps
working until it moves them.

shortcut: shell commands are not parsed, so a `mkdir` through Bash passes;
extend to shell commands if agents are seen routing around the guard.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import project_config
from autopilot_guard import TOOL_NAME_CANON, read_payload, written_paths


def deny(message: str) -> int:
    print(f"workspace guard: {message}", file=sys.stderr)
    return 2


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
            # A guard that cannot read the config must not block all work: allow and log.
            print(f"workspace guard: cannot read {workspace / 'config.json'} ({exc});"
                  " write allowed", file=sys.stderr)
            return None
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
    if TOOL_NAME_CANON.get(raw_tool, raw_tool) not in ("Write", "Edit"):
        return 0
    cwd = str(payload.get("cwd") or "")
    for file_path in written_paths(raw_tool, payload.get("tool_input")):
        message = refusal(Path(os.path.abspath(os.path.join(cwd, file_path))))
        if message:
            return deny(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
