"""Small single-team repository fixtures for release tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import build_distributions


REAL_REPOSITORY = Path(__file__).resolve().parents[2]
PLUGIN = "software-engineering-team"
REFRESH_N_VERSION = "0.0.1"
REFRESH_NEXT_VERSION = "0.0.2"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def copy(relative: str, root: Path) -> None:
    source = REAL_REPOSITORY / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def make_valid_root(
    root: Path, version: str = "0.0.1", *, build: bool = True
) -> None:
    copy("product.json", root)
    copy("tools/data/limits.json", root)
    copy("tools/data/models.json", root)
    copy("AGENTS.md", root)
    copy("CLAUDE.md", root)
    copy(f"plugins/{PLUGIN}", root)
    for relative in (
        "platforms/shared/_team",
        f"platforms/shared/{PLUGIN}",
        "platforms/claude/_team",
        f"platforms/claude/{PLUGIN}",
        "platforms/codex/_team",
        f"platforms/codex/{PLUGIN}",
    ):
        source = REAL_REPOSITORY / relative
        if source.exists():
            copy(relative, root)

    for host in ("claude", "codex"):
        manifest_path = root / "platforms" / host / PLUGIN / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        write(manifest_path, json.dumps(manifest, indent=2) + "\n")

    write(root / "versions.json", json.dumps({
        "schema_version": 1,
        "marketplace": version,
        "plugins": {PLUGIN: version},
    }, indent=2) + "\n")
    write(root / ".claude-plugin" / "marketplace.json", json.dumps({
        "name": "agent-marketplace",
        "owner": {"name": "Agentrof"},
        "metadata": {"description": "fixture", "version": version},
        "plugins": [{
            "name": PLUGIN,
            "source": f"./dist/claude/{PLUGIN}",
            "description": "fixture",
            "version": version,
            "license": "MIT",
        }],
    }, indent=2) + "\n")
    write(root / ".agents" / "plugins" / "marketplace.json", json.dumps({
        "name": "agent-marketplace",
        "interface": {"displayName": "Agent Marketplace"},
        "plugins": [{
            "name": PLUGIN,
            "source": {"source": "local", "path": f"./dist/codex/{PLUGIN}"},
            "policy": {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"},
            "category": "Engineering",
        }],
    }, indent=2) + "\n")
    write(root / ".changes" / "fixture.json", json.dumps({
        "summary": "Fixture baseline.",
        "components": {},
    }, indent=2) + "\n")
    write(root / "CHANGELOG.md", "# Changelog\n")
    if build:
        build_distributions.replace_generated(root, root / "dist")


def make_refresh_pair(n_root: Path, next_root: Path) -> None:
    """Build deterministic packages with one real N to N+1 contract delta."""
    make_valid_root(n_root, REFRESH_N_VERSION, build=False)
    plugin = n_root / "plugins" / PLUGIN

    policy_path = (
        plugin / "skill-content" / "obsidian-vault" / "data"
        / "vault-policy.json"
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["extra_doc_types"].remove("issue-report")
    policy["default_designations"].pop("issue_report")
    policy["type_path_patterns"].pop("issue_report")
    policy["status_values"].pop("issue_report")
    policy["fragment_graph_groups"]["backlog"].remove("issue-report")
    policy["graph_color_groups"] = [
        group for group in policy["graph_color_groups"]
        if group["id"] != "issue-report"
    ]
    write(policy_path, json.dumps(policy, indent=2) + "\n")

    graph_path = plugin / "templates" / "vault" / ".obsidian" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["colorGroups"] = [
        group for group in graph["colorGroups"]
        if group["query"] != "tag:#doc/issue-report"
    ]
    write(graph_path, json.dumps(graph, indent=2) + "\n")
    write(
        plugin / "templates/vault/.obsidian/snippets/brand.css",
        "/* N-only brand payload */\n:root { --agentrof-accent: #000001; }\n",
    )

    projected_plugin = (
        plugin / "templates" / "vault" / ".obsidian" / "plugins"
        / "obsidian-front-matter-title-plugin"
    )
    license_path = projected_plugin / "LICENSE"
    license_path.unlink()
    license_path.mkdir()
    write(license_path / "retired-license-fragment.txt", "N-only directory\n")
    write(projected_plugin / "retired-after-n.js", "N-only package asset\n")

    write(plugin / "agents" / "refresh-retired-probe.md", (
        "---\n"
        "name: refresh-retired-probe\n"
        "description: N-only agent used to prove package refresh cleanup.\n"
        "reasoning: low\n"
        "output_contract: prose\n"
        "---\n\n"
        "# Refresh Retired Probe\n\n"
        "## Principles\n- Preserve the fixture boundary.\n\n"
        "## Boundaries\n- Does only the N refresh probe.\n\n"
        "## Approach\n1. Return the probe result.\n\n"
        "## Output Contract\n- Probe result.\n"
    ))
    team_instructions = plugin / "templates" / "project-instructions" / "team.md"
    write(
        team_instructions,
        team_instructions.read_text(encoding="utf-8")
        + "\nN-only managed project instruction.\n",
    )
    build_distributions.replace_generated(n_root, n_root / "dist")

    make_valid_root(next_root, REFRESH_NEXT_VERSION)


def install_fixture_package(root: Path, host: str, install_root: Path) -> Path:
    """Replace one fixture install from its generated host distribution."""
    if host not in build_distributions.HOSTS:
        raise ValueError(f"unsupported fixture host: {host}")
    source = root / "dist" / host / PLUGIN
    provenance = json.loads(
        (source / build_distributions.PROVENANCE).read_text(encoding="utf-8")
    )
    if provenance.get("component") != PLUGIN or provenance.get("host") != host:
        raise ValueError("fixture distribution provenance does not match install")
    target = install_root / PLUGIN
    if target.exists():
        marker, _ = build_distributions.packaging_names(root)
        if not (target / marker).is_file():
            raise ValueError("refusing to replace an unmanaged fixture install")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target
