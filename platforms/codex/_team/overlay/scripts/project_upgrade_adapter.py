#!/usr/bin/env python3
"""Codex project-surface adapter for the shared upgrade protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import generate_codex_project
import project_instructions


def result_payload(project: Path, plugin_root: Path, workspace: str, action: str,
                   choices: dict[str, str]) -> dict:
    team = generate_codex_project.plugin_name(plugin_root)
    if action == "inspect":
        surfaces = project_instructions.owned_surfaces(
            project, team, "codex", workspace
        )
        return {
            "changes": [],
            "choice_requests": [],
            "current_surfaces": surfaces,
            "target_surfaces": surfaces,
        }
    result = generate_codex_project.preview(
        project, plugin_root, workspace, choices=choices, seed_user_files=False
    )
    payload = {
        "changes": sorted(
            path.relative_to(project).as_posix() for path in result["changes"]
        ),
        "choice_requests": result["choice_requests"],
        "current_surfaces": result["current_surfaces"],
        "target_surfaces": result["target_surfaces"],
    }
    if action == "apply" and not payload["choice_requests"]:
        generate_codex_project.materialize(
            project,
            plugin_root,
            workspace,
            choices=choices,
            seed_user_files=False,
        )
        payload["current_surfaces"] = project_instructions.owned_surfaces(
            project, team, "codex", workspace
        )
        payload["target_surfaces"] = payload["current_surfaces"]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "check", "apply"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--choice", action="append", default=[])
    args = parser.parse_args()
    project = args.project_root.resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    try:
        choices = project_instructions.parse_choices(args.choice)
        result = result_payload(
            project, plugin_root, args.workspace, args.action, choices
        )
    except ValueError as exc:
        raise SystemExit(f"project-upgrade-adapter: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
