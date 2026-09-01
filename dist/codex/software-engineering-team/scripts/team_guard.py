#!/usr/bin/env python3
"""Small host-neutral team hook.

The team has no shared state service.  This hook only announces the active
team at session start and leaves mutation policy to the owning document
compiler and the project-local vault hook.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def plugin_name() -> str:
    root = Path(__file__).resolve().parents[1]
    for manifest in (root / ".codex-plugin" / "plugin.json", root / ".claude-plugin" / "plugin.json"):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8")).get("name", "")
        except (OSError, json.JSONDecodeError):
            continue
        if value:
            return str(value)
    return "software-engineering-team"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    if mode == "register":
        python_path = Path(os.path.abspath(sys.executable))
        scripts_path = Path(__file__).resolve().parent
        context = "\n".join((
            f"AGENT_MARKETPLACE_HOOKS_ACTIVE: {plugin_name()}",
            f"AGENT_MARKETPLACE_PYTHON: {python_path}",
            f"AGENT_MARKETPLACE_SCRIPTS: {scripts_path}",
        ))
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            },
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
