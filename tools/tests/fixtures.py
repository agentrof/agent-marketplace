"""Fixture builders for the validator test suite.

Each builder takes a repository root that `make_valid_root` prepared and
plants EXACTLY ONE rule violation. The meta-test asserts that the builder
registry and the validator check registry stay in lockstep: a check without
a fixture cannot merge.
"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN = "sample-team"

VALID_AGENT = """---
name: sample-team-planner
description: Planner role for orchestrated team runs. Invoked by sample-team flows with explicit inputs.
model: sonnet
---

# Planner

Turns an approved brief into an ordered plan of small packages.

## Principles
- Every criterion is mapped or consciously deferred with a reason.

## Boundaries
- Does: planning and ordering.
- Does not: implementation decisions.

## Approach
1. Read the inputs named in the spawn prompt.
2. Produce the plan in small verifiable increments.

## Output Contract
- Exactly one plan artifact at the given path, ending with SELF-CHECK.
"""

VALID_SKILL = """---
name: notes
description: Knowledge skill for planning notes. Loaded by sample-team agents during runs.
user-invocable: false
---

# Notes

Planning notes knowledge.

## When to Use
- Loaded by the bound agent when planning.

## Core Rules
- Keep notes terse and decision-oriented.
"""

VALID_FLOW = """# Develop Flow

You MUST follow these rules exactly.

1. Execute steps in order.
2. Read prior steps from files, not conversation memory.

Spawn template:

Paste {{constitution}} here, then the inputs.
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_valid_root(root: Path) -> None:
    write(root / ".claude-plugin" / "marketplace.json", json.dumps({
        "name": "fixture-marketplace",
        "owner": {"name": "fixture"},
        "metadata": {"description": "fixture", "version": "0.0.1"},
        "plugins": [{
            "name": PLUGIN,
            "source": f"./plugins/{PLUGIN}",
            "description": "fixture plugin",
            "version": "0.0.1",
            "license": "MIT",
        }],
    }, indent=2))
    write(root / "plugins" / PLUGIN / ".claude-plugin" / "plugin.json", json.dumps({
        "name": PLUGIN,
        "version": "0.0.1",
        "description": "fixture plugin",
        "license": "MIT",
    }, indent=2))
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", VALID_AGENT)
    write(root / "plugins" / PLUGIN / "skills" / "notes" / "SKILL.md", VALID_SKILL)
    # Hook event names are the host platform's PascalCase schema and are
    # exempt from the snake_case law at exactly $.hooks in hooks/hooks.json.
    write(root / "plugins" / PLUGIN / "hooks" / "hooks.json", json.dumps({
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}],
        },
    }, indent=2))


# --- one builder per validator check -------------------------------------


def break_frontmatter_shape(root: Path) -> None:
    text = VALID_AGENT.replace("model: sonnet", "model: sonnet\ncolor: blue")
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_agent_name(root: Path) -> None:
    text = VALID_AGENT.replace("name: sample-team-planner", "name: planner")
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_skill_name(root: Path) -> None:
    text = VALID_SKILL.replace("name: notes", "name: memo")
    write(root / "plugins" / PLUGIN / "skills" / "notes" / "SKILL.md", text)


def break_trigger_policy(root: Path) -> None:
    text = VALID_AGENT.replace(
        "description: Planner role for orchestrated team runs. Invoked by sample-team flows with explicit inputs.",
        "description: Planner role. Use PROACTIVELY when planning work.",
    )
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_size_caps(root: Path) -> None:
    padding = "\n".join(f"- padding line {i}" for i in range(1, 70))
    text = VALID_AGENT.rstrip() + "\n" + padding + "\n"
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_section_contract(root: Path) -> None:
    text = VALID_AGENT.replace("## Boundaries", "## Limits")
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_agent_tech_nouns(root: Path) -> None:
    text = VALID_AGENT.replace(
        "- Does: planning and ordering.",
        "- Does: planning and ordering with python scripts.",
    )
    write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)


def break_content_bans(root: Path) -> None:
    write(root / "docs" / "note.md", "A sentence with an em dash — right here.\n")


def break_handwritten_counts(root: Path) -> None:
    write(root / "docs" / "note.md", "This marketplace ships 3 agents today.\n")


def break_dead_links(root: Path) -> None:
    write(root / "docs" / "note.md", "See [the guide](missing/guide.md) for detail.\n")


def break_reference_triggers(root: Path) -> None:
    base = root / "plugins" / PLUGIN / "skills" / "field-notes"
    write(base / "SKILL.md", """---
name: field-notes
description: Knowledge skill for field notes. Loaded by sample-team agents during runs.
user-invocable: false
---

# Field Notes

Field notes knowledge.

## When to Use
- Loaded by the bound agent when noting.

## References
- [depth](references/depth.md): deeper notes without a trigger clause.
""")
    write(base / "references" / "depth.md", "# Depth\n\nDeeper notes.\n")


def break_registration(root: Path) -> None:
    write(root / "plugins" / "ghost-team" / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "ghost-team",
        "version": "0.0.1",
        "description": "unregistered plugin",
        "license": "MIT",
    }, indent=2))


def break_json_hygiene(root: Path) -> None:
    write(root / "plugins" / PLUGIN / ".claude-plugin" / "plugin.json", json.dumps({
        "name": PLUGIN,
        "version": "0.0.1",
        "description": "fixture plugin",
        "license": "MIT",
        "displayName": "Sample Team",
    }, indent=2))


def break_orchestrator_integrity(root: Path) -> None:
    text = VALID_FLOW.replace("{{constitution}}", "the constitution body")
    write(root / "plugins" / PLUGIN / "flows" / "develop.md", text)


def break_stdlib_only(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "skills" / "notes" / "scripts" / "tool.py",
        "import requests\n\nprint(requests.__name__)\n",
    )


def break_naive_clock(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "skills" / "notes" / "scripts" / "clock.py",
        "from datetime import date\n\nprint(date.today())\n",
    )


def break_script_references(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "flows" / "check.md",
        VALID_FLOW + "\nRun ${CLAUDE_PLUGIN_ROOT}/scripts/missing_tool.py here.\n",
    )


def break_template_placeholders(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "templates" / "ci.yml",
        "jobs:\n  ghost:\n    run: {{ghost_command}}\n",
    )


def break_spawn_shape_constitution(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "skills" / "notes" / "spawning.md",
        "# Spawning\n\n## Spawn Shape\n\n1. Identity line.\n2. Inputs.\n",
    )


def break_ba_schema_shape(root: Path) -> None:
    write(
        root / "plugins" / PLUGIN / "skills" / "notes" / "data" / "space-schema.json",
        json.dumps({
            "schema_version": 1,
            "statuses": ["draft", "in_review", "approved"],
            "id_format": "^(BR)-([A-Z]{2,4})-([0-9]{3,})$",
            "doc_types": {"space": {"required_sections": [], "mints": ["BR"],
                                    "gate_blocking": True}},
            "row_schemas": {},
        }, indent=2),
    )


BUILDERS = {
    "frontmatter_shape": break_frontmatter_shape,
    "agent_name": break_agent_name,
    "skill_name": break_skill_name,
    "trigger_policy": break_trigger_policy,
    "size_caps": break_size_caps,
    "section_contract": break_section_contract,
    "content_bans": break_content_bans,
    "agent_tech_nouns": break_agent_tech_nouns,
    "handwritten_counts": break_handwritten_counts,
    "dead_links": break_dead_links,
    "reference_triggers": break_reference_triggers,
    "registration": break_registration,
    "json_hygiene": break_json_hygiene,
    "orchestrator_integrity": break_orchestrator_integrity,
    "stdlib_only": break_stdlib_only,
    "naive_clock": break_naive_clock,
    "script_references": break_script_references,
    "template_placeholders": break_template_placeholders,
    "spawn_shape_constitution": break_spawn_shape_constitution,
    "ba_schema_shape": break_ba_schema_shape,
}


def plant_out_of_scope_violations(root: Path) -> None:
    """Violations that must NOT be caught: assets/ and memory/ are excluded."""
    write(root / "assets" / "bad.md", "Em dash — and 5 agents and /Users/nobody paths.\n")
    write(root / "memory" / "bad.md", "Another em dash — with 9 skills.\n")
