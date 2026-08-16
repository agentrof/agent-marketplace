"""Small single-team repository fixtures for release tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import build_distributions


REAL_REPOSITORY = Path(__file__).resolve().parents[2]
PLUGIN = "software-engineering-team"


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


def make_valid_root(root: Path) -> None:
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

    version = "0.0.1"
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
    build_distributions.replace_generated(root, root / "dist")
