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
import release as release_tool

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

CLAUDE_TEAM_CONTRACT = """# Host Contract

- `team_guard.py` is an informational session marker and never stores state.
- One team owns one project and no cross-project state is consulted.
- Resolve packaged scripts from this plugin root and invoke them directly.
- During setup, preview and then run the generated project instruction
  generator. Preserve user instructions in CLAUDE.user.md.
- Present canonical choice gates through `AskUserQuestion`.
"""

CODEX_TEAM_CONTRACT = """# Host Contract

- `team_guard.py` is an informational session marker and never stores state.
- One team owns one project and no cross-project state is consulted.
- Resolve packaged scripts from this plugin root and invoke them directly.
- During setup, preview and then run the generated project instruction
  generator. Preserve user instructions in AGENTS.user.md.
- Present canonical choice gates through `request_user_input`.
"""

CLAUDE_TEAM_HOOKS = {
    "hooks": {
        "SessionStart": [{"hooks": [{
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/team_guard.py register",
        }]}],
    }
}

CODEX_TEAM_HOOKS = {
    "hooks": {
        "SessionStart": [{"hooks": [{
            "type": "command",
            "command": "python3 \"${PLUGIN_ROOT}\"/scripts/team_guard.py register",
        }]}],
    }
}

AGENT_TEMPLATE = """---
name: {name}
description: {title} role invoked by {plugin} flows with explicit project-local inputs; not auto-triggered.
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
1. Read the constitution included in the invocation; if absent, read the canonical team constitution from the installed package.
2. Read every project-local input file named in the invocation; trust files over memory.
3. Work in small verifiable increments toward the output contract.
4. Stop and report blocked with a specific question when inputs are missing or contradictory.

## Output Contract
- Return or write only the explicitly requested results and artifact paths.
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
2. Pre-flight: validate project-local prerequisites and tracked artifacts, then identify the next incomplete stage from those files.
3. Delegate to the owning flow file and follow it exactly.
"""

SKILL_HIDDEN_TEMPLATE = """---
name: {name}
description: Knowledge skill for {title}. Loaded by {plugin} agents for scoped work; not user-facing.
exposure: internal
---

# {title}

One-line statement of the knowledge this skill carries.

## When to Use
- Loaded by the bound agent when its task touches this domain.

## Core Rules
- State prescriptive DO/DON'T rules; keep depth in references/.
"""

def codex_manifest(name: str, description: str, product_contract: dict) -> dict:
    vendor = product_contract["vendor"]
    product = product_contract["product"]
    repository = f"https://github.com/{vendor['id']}/{product['id']}"
    long_description = description
    return {
        "name": name,
        "version": "0.0.1",
        "description": description,
        "author": {
            "name": vendor["display_name"],
            "url": f"https://github.com/{vendor['id']}",
        },
        "homepage": repository,
        "repository": repository,
        "license": "MIT",
        "skills": "./skills/",
        "interface": {
            "displayName": title_of(name),
            "shortDescription": description,
            "longDescription": long_description,
            "developerName": vendor["display_name"],
            "category": "Engineering",
            "capabilities": ["Read", "Write", "Interactive"],
            "websiteURL": repository,
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
        "fastapi": "FastAPI", "nosql": "NoSQL",
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
    product_contract = build_distributions.load_product_contract(root)
    plugin = root / "plugins" / name
    if plugin.exists():
        raise SystemExit(f"scaffold: plugin '{name}' already exists")
    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    codex_marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    versions_path = root / "versions.json"
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        codex_marketplace = json.loads(
            codex_marketplace_path.read_text(encoding="utf-8")
        )
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"scaffold: marketplace registry is unreadable: {exc}") from exc
    marketplace_before = marketplace_path.read_bytes()
    codex_marketplace_before = codex_marketplace_path.read_bytes()
    versions_before = versions_path.read_bytes()
    claude_platform = root / "platforms" / "claude" / name
    codex_platform = root / "platforms" / "codex" / name
    created = [plugin, claude_platform, codex_platform]
    try:
        (plugin / "agents").mkdir(parents=True)
        (plugin / "skill-content").mkdir()
        project_instructions = plugin / "templates" / "project-instructions"
        project_instructions.mkdir(parents=True)
        (project_instructions / "common.md").write_text(
            "# Project contract\n\nUse tracked project files as durable truth.\n",
            encoding="utf-8",
        )
        (project_instructions / "team.md").write_text(
            f"# {title_of(name)}\n\n"
            "Start managed work through this team's entry skills.\n",
            encoding="utf-8",
        )
        memory = plugin / "templates" / "memory"
        memory.mkdir()
        (memory / "agent-marketplace.md").write_text(
            "# Agent Marketplace\n\nThis project is managed by the installed team.\n",
            encoding="utf-8",
        )
        (memory / "me.md").write_text(
            "# Rules\n\nKeep durable decisions in tracked files.\n",
            encoding="utf-8",
        )
        (memory / "profile.md").write_text(
            "# User profile\n",
            encoding="utf-8",
        )
        (plugin / "scripts").mkdir()
        (plugin / "scripts" / "marketplace_paths.py").write_text(
            build_distributions.marketplace_paths_source(product_contract),
            encoding="utf-8",
        )
        claude_platform.mkdir(parents=True)
        codex_platform.mkdir(parents=True)
        vendor = product_contract["vendor"]
        manifest = {
            "name": name,
            "version": "0.0.1",
            "description": f"{title_of(name)} plugin.",
            "author": {
                "name": vendor["display_name"],
                "url": f"https://github.com/{vendor['id']}",
            },
            "license": "MIT",
            "skills": "./skills/",
        }
        (claude_platform / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        claude_contract = CLAUDE_TEAM_CONTRACT
        (claude_platform / "host-contract.md").write_text(
            claude_contract, encoding="utf-8")
        if True:
            claude_hooks = claude_platform / "overlay" / "hooks" / "hooks.json"
            claude_hooks.parent.mkdir(parents=True)
            claude_hooks.write_text(
                json.dumps(CLAUDE_TEAM_HOOKS, indent=2) + "\n", encoding="utf-8"
            )
        marketplace.setdefault("plugins", []).append({
            "name": name,
            "source": release_tool.channel_source("claude", name),
            "description": manifest["description"],
            "version": manifest["version"],
            "license": "MIT",
        })
        marketplace_path.write_text(
            json.dumps(marketplace, indent=2) + "\n", encoding="utf-8")
        (codex_platform / "manifest.json").write_text(
            json.dumps(
                codex_manifest(name, manifest["description"], product_contract),
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        codex_contract = CODEX_TEAM_CONTRACT
        (codex_platform / "host-contract.md").write_text(
            codex_contract, encoding="utf-8")
        if True:
            codex_hooks = codex_platform / "overlay" / "hooks" / "hooks.json"
            codex_hooks.parent.mkdir(parents=True)
            codex_hooks.write_text(
                json.dumps(CODEX_TEAM_HOOKS, indent=2) + "\n", encoding="utf-8"
            )
        codex_marketplace.setdefault("plugins", []).append({
            "name": name,
            "source": release_tool.channel_source("codex", name),
            "policy": {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"},
            "category": "Engineering",
        })
        codex_marketplace_path.write_text(
            json.dumps(codex_marketplace, indent=2) + "\n", encoding="utf-8"
        )
        plugin_versions = versions.setdefault("plugins", {})
        if name in plugin_versions:
            raise RuntimeError(f"versions.json already registers {name}")
        plugin_versions[name] = manifest["version"]
        versions_path.write_text(
            json.dumps(versions, indent=2) + "\n", encoding="utf-8"
        )
        sync_distributions(root)
    except BaseException:
        marketplace_path.write_bytes(marketplace_before)
        codex_marketplace_path.write_bytes(codex_marketplace_before)
        versions_path.write_bytes(versions_before)
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
    try:
        build_distributions.load_product_contract(root)
    except ValueError as exc:
        raise SystemExit(f"scaffold: {exc}") from exc

    if args.command == "new-plugin":
        new_plugin(root, args.name)
    elif args.command == "new-agent":
        new_agent(root, args.plugin, args.name)
    elif args.command == "new-skill":
        new_skill(root, args.plugin, args.name, args.kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
