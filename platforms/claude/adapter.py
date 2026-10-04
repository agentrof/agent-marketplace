"""Claude Code rendering policy for the distribution builder."""

from __future__ import annotations

import re
from pathlib import Path


# Documented subagent effort values and model ID format; the pinned models
# are data in model-catalog.json beside this module.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MODEL_ID_RE = re.compile(
    r"claude-(?P<family>[a-z]+)-(?P<major>[0-9]+)(?:-(?P<minor>[0-9]{1,2}))?"
    r"(?:-(?P<snapshot>[0-9]{8}))?"
)
# IDs are dateless snapshots from the 4.6 generation on and dated before it,
# where the dateless form is an alias that is not pinned.
DATELESS_SINCE = (4, 6)
# The shape of a model ID a project may set for a tier: the documented ID
# format, which the host's own model list judges further.
MODEL_ID_SHAPE = MODEL_ID_RE.pattern
# This host's CLI in tools/data/host-cli-versions.json, the exact version CI
# installs; no catalog model's min_cli_version may be newer.
HOST_CLI_KEY = "claude_code"
# How a frozen-task A/B runs every role on one candidate model.
MODEL_TRIAL = (
    "Claude Code: set `CLAUDE_CODE_SUBAGENT_MODEL=<model ID>` and"
    " `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` for the session; a role's `effort`"
    " still applies."
)


def model_version(model_id: str) -> tuple[str, tuple[int, ...]] | None:
    """Return the family and comparable version of a pinned model ID."""
    match = MODEL_ID_RE.fullmatch(model_id)
    if match is None:
        return None
    release = (int(match["major"]), int(match["minor"] or 0))
    if (match["snapshot"] is None) != (release >= DATELESS_SINCE):
        return None
    return match["family"], (*release, int(match["snapshot"] or 0))


def skill_artifacts(context: dict, source_name: str, metadata: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    name, description, exposure, project_scope = metadata
    policy = "disable-model-invocation: true\n" if exposure == "entry" else "user-invocable: false\n"
    gate = (
        " Before changing a project, confirm the workspace config and local docs "
        "contract are present; setup is the only entry that may create them."
        if exposure == "entry" and project_scope == "project" else ""
    )
    floor = (
        " When the session context reports `AGENT_MARKETPLACE_PYTHON: unsupported`,"
        " stop and tell the user the reason and the fix it gives."
        if exposure == "entry" else ""
    )
    text = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{policy}"
        "---\n\n"
        f"{context['wrapper_marker']}\n\n"
        f"# {context['title_of'](name)}\n\n"
        "Read `${CLAUDE_PLUGIN_ROOT}/host-contract.md` and "
        f"`${{CLAUDE_PLUGIN_ROOT}}/skill-content/{name}/SKILL.md` completely. "
        "Follow the canonical skill as the authoritative workflow and the host "
        f"contract as its platform adapter.{gate}{floor}\n"
    )
    return [(f"skills/{name}/SKILL.md", text)]


def agent_artifacts(context: dict, source) -> list[tuple[str, str]]:
    fields, body = context["parse_frontmatter"](source)
    reasoning = fields.pop("reasoning", "")
    setting = context["execution_profile"].get(reasoning)
    if setting is None:
        raise ValueError(f"{source}: invalid reasoning level {reasoning!r}")
    lines = ["---"]
    for key in ("name", "description"):
        if not fields.get(key):
            raise ValueError(f"{source}: missing {key}")
        lines.append(f"{key}: {fields.pop(key)}")
    lines.append(f"model: {setting.get('model', 'inherit')}")
    if "effort" in setting:
        lines.append(f"effort: {setting['effort']}")
    lines.extend(f"{key}: {value}" for key, value in fields.items())
    lines.extend(("---", "", body.lstrip("\n")))
    return [(f"agents/{source.name}", "\n".join(lines))]


def native_manifest_directory(host_id: str) -> str:
    return f".{host_id}-plugin"


def instruction_surface() -> dict:
    return {
        "filename": "CLAUDE.md",
        "owner_host": "claude",
        "user_companion": "CLAUDE.user.md",
        "migrates_from_owners": [],
    }


def runtime_contracts() -> list[str]:
    return ["in_use_pid_marker_v1"]


def scaffold_contract() -> str:
    return """# Host Contract

- `team_guard.py` is an informational session marker and never stores state.
- One team owns one project and no cross-project state is consulted.
- Resolve packaged scripts from this plugin root and invoke them directly.
- During setup, preview and then run the generated project instruction
  generator. Preserve user instructions in CLAUDE.user.md.
- Present canonical choice gates through `AskUserQuestion`.
"""


def scaffold_manifest(name: str, description: str, product_contract: dict) -> dict:
    vendor = product_contract["vendor"]
    return {
        "name": name,
        "version": "0.0.1",
        "description": description,
        "author": {
            "name": vendor["display_name"],
            "url": f"https://github.com/{vendor['id']}",
        },
        "license": "MIT",
        "skills": "./skills/",
    }


def marketplace_catalog_path(root: Path) -> Path:
    return root / ".claude-plugin" / "marketplace.json"


def scaffold_catalog_entry(name: str, manifest: dict, product_contract: dict) -> dict:
    del product_contract
    return {
        "name": name,
        "source": f"./dist/claude/{name}",
        "description": manifest["description"],
        "version": manifest["version"],
        "license": "MIT",
    }


def channel_source(plugin: str) -> str:
    return f"./dist/claude/{plugin}"


def sync_catalog_entry(entry: dict, plugin: str, version: str) -> None:
    entry["version"] = version
    entry["source"] = channel_source(plugin)


def sync_catalog_metadata(catalog: dict, marketplace_version: str) -> None:
    catalog.setdefault("metadata", {})["version"] = marketplace_version


def catalog_component_version(entry: dict) -> str | None:
    return str(entry.get("version", ""))


def scaffold_overlay_files() -> dict[str, dict]:
    return {
        "overlay/hooks/hooks.json": {
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook_launcher.py scripts/team_guard.py register",
                }]}],
            },
        },
    }
