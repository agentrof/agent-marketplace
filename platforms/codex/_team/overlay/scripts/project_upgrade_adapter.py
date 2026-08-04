#!/usr/bin/env python3
"""Codex project-surface adapter for the shared upgrade protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import generate_codex_project


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def owned_surfaces(root: Path, team: str) -> dict[str, str]:
    result: dict[str, str] = {}
    instruction = root / "AGENTS.md"
    if instruction.is_file():
        text = instruction.read_text(encoding="utf-8")
        start, end = generate_codex_project.block_markers(team)
        left, right = text.find(start), text.find(end)
        if left >= 0 and right >= left:
            right += len(end)
            result["AGENTS.md#agentrof"] = digest(text[left:right])
    agents = root / ".codex" / "agents"
    if agents.is_dir():
        owner = generate_codex_project.owner(team)
        for path in sorted(agents.glob("*.toml")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if text.startswith(owner):
                result[path.relative_to(root).as_posix()] = digest(text)
    return result


def copy_if_present(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def preview(project: Path, plugin_root: Path, workspace: str) -> dict:
    for target in (project / "AGENTS.md", project / ".codex"):
        if target.is_symlink():
            raise ValueError(f"managed project target is a symbolic link: {target}")
    team = generate_codex_project.plugin_name(plugin_root)
    current = owned_surfaces(project, team)
    with tempfile.TemporaryDirectory(prefix="agentrof-project-adapter-") as tmp:
        candidate = Path(tmp)
        (candidate / ".git").mkdir()
        copy_if_present(project / "AGENTS.md", candidate / "AGENTS.md")
        copy_if_present(project / ".codex", candidate / ".codex")
        copy_if_present(
            project / workspace / "config.json",
            candidate / workspace / "config.json",
        )
        written = generate_codex_project.materialize(
            candidate, plugin_root, workspace
        )
        target = owned_surfaces(candidate, team)
        changes = sorted(path.relative_to(candidate).as_posix() for path in written)
    return {
        "changes": changes,
        "current_surfaces": current,
        "target_surfaces": target,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "check", "apply"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    try:
        result = preview(project, plugin_root, args.workspace)
    except ValueError as exc:
        raise SystemExit(f"project-upgrade-adapter: {exc}") from exc
    if args.action == "apply":
        generate_codex_project.materialize(project, plugin_root, args.workspace)
        team = generate_codex_project.plugin_name(plugin_root)
        result["current_surfaces"] = owned_surfaces(project, team)
        result["target_surfaces"] = result["current_surfaces"]
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
