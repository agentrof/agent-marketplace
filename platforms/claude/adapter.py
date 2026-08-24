"""Claude Code rendering policy for the distribution builder."""

from __future__ import annotations

from pathlib import Path


MODELS = {
    "high": "opus",
    "medium": "sonnet",
    "low": "haiku",
    "inherit": "inherit",
}


def skill_artifacts(context: dict, source_name: str, metadata: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    name, description, exposure, project_scope = metadata
    policy = "disable-model-invocation: true\n" if exposure == "entry" else "user-invocable: false\n"
    gate = (
        " Before changing a project, confirm the workspace config and local docs "
        "contract are present; setup is the only entry that may create them."
        if exposure == "entry" and project_scope == "project" else ""
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
        f"contract as its platform adapter.{gate}\n"
    )
    return [(f"skills/{name}/SKILL.md", text)]


def agent_artifacts(context: dict, source) -> list[tuple[str, str]]:
    fields, body = context["parse_frontmatter"](source)
    reasoning = fields.pop("reasoning", "")
    if reasoning not in MODELS:
        raise ValueError(f"{source}: invalid reasoning level {reasoning!r}")
    lines = ["---"]
    for key in ("name", "description"):
        if not fields.get(key):
            raise ValueError(f"{source}: missing {key}")
        lines.append(f"{key}: {fields.pop(key)}")
    lines.append(f"model: {MODELS[reasoning]}")
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
                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/team_guard.py register",
                }]}],
            },
        },
    }
