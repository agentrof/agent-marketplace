#!/usr/bin/env python3
"""Small host-neutral team hook.

The team has no shared state service.  This hook only announces the active
team at session start and leaves mutation policy to the owning document
compiler and the project-local vault hook.  It also reports the project's
rendered role files whose stamp does not match the installed package and the
project's settings: in the Claude Code package the session then spawns the
plugin's roles until a refresh, and in the Codex package, which has no other
identity of a role, the user is told that those roles run what they were
rendered with until setup.  It never blocks: anything it cannot read leaves
the announcement as it is.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# A compaction continues a session whose user was told at its start; Claude
# gets the context again, the user no second message.
REPEATED_SOURCES = {"compact"}


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


def read_event() -> dict:
    """Return the hook's JSON input, or {} when a person runs the hook by hand."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        value = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def codex_root(directory: Path) -> Path:
    """The project a Codex session in ``directory`` renders its roles for: the
    nearest directory up to the repository root that holds `.codex/agents/`."""
    for parent in (directory, *directory.parents):
        if (parent / ".codex" / "agents").is_dir() or (parent / ".git").exists():
            return parent
    return directory


def stale_render(event: dict) -> dict | None:
    """Return the warning for rendered role files that another package or
    other project settings rendered."""
    try:
        import generate_claude_project as generator  # each package ships its host's
    except ImportError:
        try:
            import generate_codex_project as generator
        except ImportError:
            return None
    root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
    if not isinstance(root, str) or not root:
        return None
    plugin_root = Path(__file__).resolve().parents[1]
    if generator.HOST == "codex":
        return stale_codex_roles(generator, codex_root(Path(root)), plugin_root)
    stale, held = generator.stale_agents(Path(root), plugin_root)
    if not stale:
        return None
    team = plugin_name()
    version = generator.package_version(plugin_root)
    return {
        "message": (
            f"{team}: {len(stale)} of the {held} role agents in .claude/agents/ do not"
            f" match the installed {team} {version} or this project's"
            " workspace/config.json. Until setup or a package refresh renders them"
            f" again, every role runs as the plugin's {team}:<role> agent with the"
            " package's models and efforts, not this project's model settings."
            f" Run /{team}:setup to refresh."
        ),
        "context": (
            "AGENT_MARKETPLACE_RENDERED_AGENTS: stale\n"
            f"{len(stale)} of the {held} rendered role agents in .claude/agents/ were"
            f" rendered by another package than the installed {team} {version} or from"
            " other model settings than this project's workspace/config.json, and still"
            " run what they were rendered with. Until setup or a package refresh"
            " renders them again, spawn every role as the plugin's"
            f" `{team}:<agent-id>` identity, never as `{team}-<agent-id>`. The user was"
            " told at session start to run setup."
        ),
    }


def stale_codex_roles(generator, root: Path, plugin_root: Path) -> dict | None:
    stale, held = generator.stale_agents(root, plugin_root)
    if not stale:
        return None
    team = plugin_name()
    version = generator.role_settings.package_version(plugin_root)
    return {
        "message": (
            f"{team}: {len(stale)} of the {held} role files in .codex/agents/ do not match"
            f" the installed {team} {version} or this project's workspace/config.json, so"
            " those roles still run the role definition, model and effort they were"
            f" rendered with. Run ${team}:setup to render them again."
        ),
        "context": (
            "AGENT_MARKETPLACE_RENDERED_AGENTS: stale\n"
            f"{len(stale)} of the {held} role files in .codex/agents/ were rendered by another"
            f" package than the installed {team} {version} or from other model settings than"
            " this project's workspace/config.json. Until setup or a package refresh renders"
            " them again, every start of such a role still runs the role definition, model"
            " and effort it was rendered with. The user was told at session start to run"
            " setup."
        ),
    }


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
        event = read_event()
        try:
            stale = stale_render(event)
        except Exception:
            stale = None
        output: dict = {}
        if stale is not None:
            context += "\n" + stale["context"]
            if event.get("source") not in REPEATED_SOURCES:
                output["systemMessage"] = stale["message"]
        output["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
