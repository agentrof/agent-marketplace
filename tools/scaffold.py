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
import sys
from pathlib import Path

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

AGENT_TEMPLATE = """---
name: {plugin}-{name}
description: {title} role for orchestrated team runs. Invoked by {plugin} flows with explicit inputs; not auto-triggered.
model: sonnet
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
disable-model-invocation: true
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
user-invocable: false
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


def title_of(name: str) -> str:
    return " ".join(word.capitalize() for word in name.split("-"))


def require_kebab(value: str, what: str) -> str:
    if not KEBAB_RE.match(value):
        raise SystemExit(f"scaffold: {what} '{value}' must be kebab-case")
    return value


def new_plugin(root: Path, name: str) -> None:
    require_kebab(name, "plugin name")
    plugin = root / "plugins" / name
    if plugin.exists():
        raise SystemExit(f"scaffold: plugin '{name}' already exists")
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "agents").mkdir()
    (plugin / "skills").mkdir()
    manifest = dict(PLUGIN_JSON_TEMPLATE)
    manifest["name"] = name
    manifest["description"] = f"{title_of(name)} plugin."
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    # Registration is part of birth: a scaffolded-but-unregistered plugin
    # would fail the registration rule the moment someone registered it by
    # hand. One writer, one moment.
    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    marketplace.setdefault("plugins", []).append({
        "name": name,
        "source": f"./plugins/{name}",
        "description": manifest["description"],
        "version": manifest["version"],
        "license": "MIT",
    })
    marketplace_path.write_text(
        json.dumps(marketplace, indent=2) + "\n", encoding="utf-8")
    print(f"scaffold: created plugins/{name} and registered it in"
          " .claude-plugin/marketplace.json")


def new_agent(root: Path, plugin_name: str, name: str) -> None:
    require_kebab(name, "agent name")
    plugin = root / "plugins" / plugin_name
    if not plugin.is_dir():
        raise SystemExit(f"scaffold: plugin '{plugin_name}' does not exist")
    path = plugin / "agents" / f"{name}.md"
    if path.exists():
        raise SystemExit(f"scaffold: agent '{name}' already exists")
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        AGENT_TEMPLATE.format(plugin=plugin_name, name=name, title=title_of(name)),
        encoding="utf-8",
    )
    print(f"scaffold: created {path.relative_to(root)}")


def new_skill(root: Path, plugin_name: str, name: str, kind: str) -> None:
    require_kebab(name, "skill name")
    plugin = root / "plugins" / plugin_name
    if not plugin.is_dir():
        raise SystemExit(f"scaffold: plugin '{plugin_name}' does not exist")
    sdir = plugin / "skills" / name
    if sdir.exists():
        raise SystemExit(f"scaffold: skill '{name}' already exists")
    sdir.mkdir(parents=True)
    template = SKILL_ENTRY_TEMPLATE if kind == "entry" else SKILL_HIDDEN_TEMPLATE
    (sdir / "SKILL.md").write_text(
        template.format(name=name, title=title_of(name), plugin=plugin_name),
        encoding="utf-8",
    )
    if kind == "hidden":
        (sdir / "references").mkdir()
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
