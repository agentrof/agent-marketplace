#!/usr/bin/env python3
"""Scaffolder for the Agent Marketplace.

Compliant components are born, not fixed: every stub this tool emits passes
`make check` with zero findings (a fixture test proves it).

Subcommands:
  new-plugin --name <kebab>
  new-agent  --plugin <plugin> --name <role>
  new-skill  --plugin <plugin> --name <skill> --kind entry|hidden

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import build_distributions

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PMO_PLUGIN = "project-management-office"
PMO_READY = "AGENTROF_PMO_READY: project-management-office"

CLAUDE_TEAM_CONTRACT = f"""# Host Contract

- The shared `team_guard.py` PreToolUse hook mechanically requires the PMO session-ready record before Write, Edit, or Bash. Keep the exact context check `{PMO_READY}` as the user-facing diagnostic. If it is absent, run `claude plugin list --json` as a read-only diagnostic and stop. If PMO is missing, ask the user to run `/plugin install project-management-office@agent-marketplace`; if it is disabled, ask for `/plugin enable project-management-office@agent-marketplace`; if it is installed and enabled, ask for a Claude Code restart and PMO hook-log inspection. State that no files or project state were changed.
- One delivery team owns a project. Stop without mutation when workspace/config.json or Agentrof-owned project agents name another team.
- Preserve every canonical workflow gate and artifact.
"""

CODEX_TEAM_CONTRACT = f"""# Host Contract

- The shared `team_guard.py` PreToolUse hook mechanically requires the PMO session-ready record before Write, Edit, apply_patch, or Bash. Keep the exact context check `{PMO_READY}` as the user-facing diagnostic. If it is absent, run `codex plugin list --json` as a read-only diagnostic and stop. If PMO is missing, show `codex plugin add project-management-office@agent-marketplace`; if it is disabled, ask the user to enable it in Plugins; if it is installed and enabled, ask the user to inspect and trust Project Management Office and this team plugin through `/hooks`, then start a new task. State that no files or project state were changed.
- One delivery team owns a project. Stop without mutation when workspace/config.json or Agentrof-owned project agents name another team.
- During setup, run the generated `scripts/generate_codex_project.py`; it owns only this team's marked AGENTS.md block and Agentrof-owned project agents.
- Preserve every canonical workflow gate and artifact.
"""

CLAUDE_TEAM_HOOKS = {
    "hooks": {
        "SessionStart": [{"hooks": [{
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/team_guard.py register",
        }]}],
        "PreToolUse": [{
            "matcher": "Write|Edit|Bash",
            "hooks": [{
                "type": "command",
                "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/team_guard.py pre",
            }],
        }],
    }
}

CODEX_TEAM_HOOKS = {
    "hooks": {
        "SessionStart": [{"hooks": [{
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}\"/scripts/team_guard.py register",
        }]}],
        "PreToolUse": [{
            "matcher": "Write|Edit|apply_patch|Bash",
            "hooks": [{
                "type": "command",
                "command": "python3 \"${PLUGIN_ROOT}\"/scripts/team_guard.py pre",
            }],
        }],
    }
}

AGENT_TEMPLATE = """---
name: {name}
description: {title} role for orchestrated team runs. Invoked by {plugin} flows with explicit inputs; not auto-triggered.
reasoning: medium
output_contract: prose
---

# {title}

One-sentence mission for this role, stated as what it produces and to what bar.

## Principles
- State the few non-negotiable judgments this role applies.
- Keep each principle testable against an output.

## Boundaries
- Does: the concrete territory this role owns.
- Does not: what it defers, and to which role.
- Never guesses silently; asks or escalates when inputs conflict.

## Approach
1. Read the constitution included in the spawn prompt; if absent, read the run folder copy.
2. Read the input files named in the spawn prompt, summaries first; trust files over memory.
3. Work in small verifiable increments toward the output contract.
4. Stop and report blocked with a specific question when inputs are missing or contradictory.

## Output Contract
- Exactly the artifacts named in the spawn prompt, at the given paths.
- End the reply with SELF-CHECK: each required element marked present or missing.
"""

SKILL_ENTRY_TEMPLATE = """---
name: {name}
description: Entry point for {title}. Invoked by the user as a slash skill; routes into the plugin flow.
exposure: entry
---

# {title}

One-line purpose of this entry.

## When to Use
- The user explicitly starts this flow.

## Procedure
1. Parse arguments and mode.
2. Pre-flight: check run state; offer resume when a run is in progress.
3. Delegate to the owning flow file and follow it exactly.
"""

SKILL_HIDDEN_TEMPLATE = """---
name: {name}
description: Knowledge skill for {title}. Loaded by {plugin} agents during runs; not user-facing.
exposure: internal
---

# {title}

One-line statement of the knowledge this skill carries.

## When to Use
- Loaded by the bound agent when its task touches this domain.

## Core Rules
- State prescriptive DO/DON'T rules; keep depth in references/.
"""

PLUGIN_JSON_TEMPLATE = {
    "name": "",
    "version": "0.1.0",
    "description": "",
    "author": {"name": "Agentrof", "url": "https://github.com/agentrof"},
    "license": "MIT",
}


def codex_manifest(name: str, description: str) -> dict:
    long_description = description
    if name != PMO_PLUGIN:
        long_description = f"Requires Project Management Office. {description}"
    return {
        "name": name,
        "version": "0.1.0",
        "description": description,
        "author": {"name": "Agentrof", "url": "https://github.com/agentrof"},
        "homepage": "https://github.com/agentrof/agent-marketplace",
        "repository": "https://github.com/agentrof/agent-marketplace",
        "license": "MIT",
        "skills": "./skills/",
        "interface": {
            "displayName": title_of(name),
            "shortDescription": description,
            "longDescription": long_description,
            "developerName": "Agentrof",
            "category": "Engineering",
            "capabilities": ["Read", "Write", "Interactive"],
            "websiteURL": "https://github.com/agentrof/agent-marketplace",
        },
    }


def sync_distributions(root: Path) -> None:
    output = root / "dist"
    try:
        build_distributions.replace_generated(root, output)
    except ValueError as exc:
        raise SystemExit(f"scaffold: {exc}") from exc


def rollback_created(root: Path, paths: list[Path]) -> None:
    """Remove only paths created by the active scaffold transaction."""
    resolved_root = root.resolve()
    for path in reversed(paths):
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise RuntimeError(f"refusing rollback outside repository: {path}") from exc
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


def title_of(name: str) -> str:
    display_tokens = {
        "api": "API", "cli": "CLI", "devops": "DevOps",
        "fastapi": "FastAPI", "nosql": "NoSQL", "pmo": "PMO",
        "qa": "QA", "sql": "SQL", "ui": "UI", "ux": "UX",
    }
    return " ".join(
        display_tokens.get(word, word.capitalize()) for word in name.split("-")
    )


def require_kebab(value: str, what: str) -> str:
    if not KEBAB_RE.match(value):
        raise SystemExit(f"scaffold: {what} '{value}' must be kebab-case")
    return value


def new_plugin(root: Path, name: str) -> None:
    require_kebab(name, "plugin name")
    plugin = root / "plugins" / name
    if plugin.exists():
        raise SystemExit(f"scaffold: plugin '{name}' already exists")
    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    codex_marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        codex_marketplace = json.loads(
            codex_marketplace_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"scaffold: marketplace registry is unreadable: {exc}") from exc
    marketplace_before = marketplace_path.read_bytes()
    codex_marketplace_before = codex_marketplace_path.read_bytes()
    claude_platform = root / "platforms" / "claude" / name
    codex_platform = root / "platforms" / "codex" / name
    created = [plugin, claude_platform, codex_platform]
    try:
        (plugin / "agents").mkdir(parents=True)
        (plugin / "skill-content").mkdir()
        claude_platform.mkdir(parents=True)
        codex_platform.mkdir(parents=True)
        manifest = dict(PLUGIN_JSON_TEMPLATE)
        manifest["name"] = name
        manifest["description"] = f"{title_of(name)} plugin."
        manifest["skills"] = "./skills/"
        if name != PMO_PLUGIN:
            manifest["dependencies"] = [PMO_PLUGIN]
        (claude_platform / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        claude_contract = (
            CLAUDE_TEAM_CONTRACT if name != PMO_PLUGIN
            else "# Host Contract\n\n- Preserve every canonical workflow gate and artifact.\n"
        )
        (claude_platform / "host-contract.md").write_text(
            claude_contract, encoding="utf-8")
        if name != PMO_PLUGIN:
            claude_hooks = claude_platform / "overlay" / "hooks" / "hooks.json"
            claude_hooks.parent.mkdir(parents=True)
            claude_hooks.write_text(
                json.dumps(CLAUDE_TEAM_HOOKS, indent=2) + "\n", encoding="utf-8"
            )
        marketplace.setdefault("plugins", []).append({
            "name": name,
            "source": f"./dist/claude/{name}",
            "description": manifest["description"],
            "version": manifest["version"],
            "license": "MIT",
        })
        marketplace_path.write_text(
            json.dumps(marketplace, indent=2) + "\n", encoding="utf-8")
        (codex_platform / "manifest.json").write_text(
            json.dumps(codex_manifest(name, manifest["description"]), indent=2) + "\n",
            encoding="utf-8",
        )
        codex_contract = (
            CODEX_TEAM_CONTRACT if name != PMO_PLUGIN
            else "# Host Contract\n\n- Preserve every canonical workflow gate and artifact.\n"
        )
        (codex_platform / "host-contract.md").write_text(
            codex_contract, encoding="utf-8")
        if name != PMO_PLUGIN:
            codex_hooks = codex_platform / "overlay" / "hooks" / "hooks.json"
            codex_hooks.parent.mkdir(parents=True)
            codex_hooks.write_text(
                json.dumps(CODEX_TEAM_HOOKS, indent=2) + "\n", encoding="utf-8"
            )
            agents_template = codex_platform / "overlay" / "templates" / "AGENTS.md"
            agents_template.parent.mkdir(parents=True)
            agents_template.write_text(
                f"# {title_of(name)}\n\n"
                "- Read {{workspace}}/memory/me.md before team work when it exists.\n"
                "- Use only this team's setup-generated project agents.\n",
                encoding="utf-8",
            )
        codex_marketplace.setdefault("plugins", []).append({
            "name": name,
            "source": {"source": "local", "path": f"./dist/codex/{name}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Engineering",
        })
        codex_marketplace_path.write_text(
            json.dumps(codex_marketplace, indent=2) + "\n", encoding="utf-8"
        )
        sync_distributions(root)
    except BaseException:
        marketplace_path.write_bytes(marketplace_before)
        codex_marketplace_path.write_bytes(codex_marketplace_before)
        rollback_created(root, created)
        raise
    print(f"scaffold: created plugins/{name}, both platform adapters,"
          " and both marketplace entries")


def new_agent(root: Path, plugin_name: str, name: str) -> None:
    require_kebab(name, "agent name")
    plugin = root / "plugins" / plugin_name
    if not plugin.is_dir():
        raise SystemExit(f"scaffold: plugin '{plugin_name}' does not exist")
    path = plugin / "agents" / f"{name}.md"
    if path.exists():
        raise SystemExit(f"scaffold: agent '{name}' already exists")
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            AGENT_TEMPLATE.format(plugin=plugin_name, name=name, title=title_of(name)),
            encoding="utf-8",
        )
        sync_distributions(root)
    except BaseException:
        rollback_created(root, [path])
        raise
    print(f"scaffold: created {path.relative_to(root)}")


def new_skill(root: Path, plugin_name: str, name: str, kind: str) -> None:
    require_kebab(name, "skill name")
    if kind not in {"entry", "hidden"}:
        raise SystemExit("scaffold: skill kind must be entry or hidden")
    plugin = root / "plugins" / plugin_name
    if not plugin.is_dir():
        raise SystemExit(f"scaffold: plugin '{plugin_name}' does not exist")
    skills_root = plugin / "skill-content"
    sdir = skills_root / name
    if sdir.exists():
        raise SystemExit(f"scaffold: skill '{name}' already exists")
    try:
        sdir.mkdir(parents=True)
        template = SKILL_ENTRY_TEMPLATE if kind == "entry" else SKILL_HIDDEN_TEMPLATE
        (sdir / "SKILL.md").write_text(
            template.format(name=name, title=title_of(name), plugin=plugin_name),
            encoding="utf-8",
        )
        if kind == "hidden":
            (sdir / "references").mkdir()
        sync_distributions(root)
    except BaseException:
        rollback_created(root, [sdir])
        raise
    print(f"scaffold: created {sdir.relative_to(root)} ({kind})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = parser.add_subparsers(dest="command", required=True)

    p_plugin = sub.add_parser("new-plugin")
    p_plugin.add_argument("--name", required=True)

    p_agent = sub.add_parser("new-agent")
    p_agent.add_argument("--plugin", required=True)
    p_agent.add_argument("--name", required=True)

    p_skill = sub.add_parser("new-skill")
    p_skill.add_argument("--plugin", required=True)
    p_skill.add_argument("--name", required=True)
    p_skill.add_argument("--kind", required=True, choices=["entry", "hidden"])

    args = parser.parse_args()
    root = args.root.resolve()

    if args.command == "new-plugin":
        new_plugin(root, args.name)
    elif args.command == "new-agent":
        new_agent(root, args.plugin, args.name)
    elif args.command == "new-skill":
        new_skill(root, args.plugin, args.name, args.kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
