"""Codex rendering policy for the distribution builder."""

from __future__ import annotations

from pathlib import Path


def skill_artifacts(context: dict, source_name: str, metadata: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    name, description, exposure, project_scope = metadata
    if exposure != "entry":
        return []
    gate = (
        " Before any workflow step inside a Git repository, confirm the "
        "project-local workspace config and docs contract."
        if project_scope == "project" else ""
    )
    wrapper = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{context['wrapper_marker']}\n\n"
        f"# {context['title_of'](name)}\n\n"
        "Read `../../host-contract.md` and "
        f"`../../skill-content/{name}/SKILL.md` completely, resolving both paths "
        "relative to this file. Follow the canonical skill as the authoritative "
        f"workflow and the host contract as its platform adapter.{gate}\n"
    )
    visible = context["title_of"](name)
    short = f"Start the {visible} guided workflow"
    if len(short) > 64:
        short = f"Run {visible}"
    metadata_text = (
        "interface:\n"
        f"  display_name: \"{visible}\"\n"
        f"  short_description: \"{short}\"\n"
        f"  default_prompt: \"Use ${source_name}:{name} to start this workflow.\"\n"
        "policy:\n"
        "  allow_implicit_invocation: false\n"
    )
    return [
        (f"skills/{name}/SKILL.md", wrapper),
        (f"skills/{name}/agents/openai.yaml", metadata_text),
    ]


def agent_artifacts(context: dict, source) -> list[tuple[str, str]]:
    return [(f"agents/{source.name}", source.read_text(encoding="utf-8"))]


def native_manifest_directory(host_id: str) -> str:
    return f".{host_id}-plugin"


def instruction_surface() -> dict:
    return {
        "filename": "AGENTS.override.md",
        "owner_host": "codex",
        "user_companion": "AGENTS.user.md",
        "migrates_from_owners": [],
    }


def runtime_contracts() -> list[str]:
    return []


def scaffold_contract() -> str:
    return """# Host Contract

- `team_guard.py` is an informational session marker and never stores state.
- One team owns one project and no cross-project state is consulted.
- Resolve packaged scripts from this plugin root and invoke them directly.
- During setup, preview and then run the generated project instruction
  generator. Preserve user instructions in AGENTS.user.md.
- Present canonical choice gates through `request_user_input`.
"""


def scaffold_manifest(name: str, description: str, product_contract: dict) -> dict:
    vendor = product_contract["vendor"]
    product = product_contract["product"]
    repository = f"https://github.com/{vendor['id']}/{product['id']}"
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
            "displayName": _title(name),
            "shortDescription": description,
            "longDescription": description,
            "developerName": vendor["display_name"],
            "category": "Engineering",
            "capabilities": ["Read", "Write", "Interactive"],
            "websiteURL": repository,
        },
    }


def marketplace_catalog_path(root: Path) -> Path:
    return root / ".agents" / "plugins" / "marketplace.json"


def scaffold_catalog_entry(name: str, manifest: dict, product_contract: dict) -> dict:
    del manifest, product_contract
    return {
        "name": name,
        "source": {"source": "local", "path": f"./dist/codex/{name}"},
        "policy": {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_INSTALL",
        },
        "category": "Engineering",
    }


def channel_source(plugin: str) -> dict:
    return {"source": "local", "path": f"./dist/codex/{plugin}"}


def sync_catalog_entry(entry: dict, plugin: str, version: str) -> None:
    del version
    entry["source"] = channel_source(plugin)


def sync_catalog_metadata(catalog: dict, marketplace_version: str) -> None:
    del catalog, marketplace_version


def catalog_component_version(entry: dict) -> str | None:
    del entry
    return None


def _title(name: str) -> str:
    display_tokens = {"api": "API", "cli": "CLI", "devops": "DevOps", "qa": "QA"}
    return " ".join(display_tokens.get(token, token.capitalize()) for token in name.split("-"))


def scaffold_overlay_files() -> dict[str, dict]:
    return {
        "overlay/hooks/hooks.json": {
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": "python3 \"${PLUGIN_ROOT}\"/scripts/team_guard.py register",
                }]}],
            },
        },
    }
