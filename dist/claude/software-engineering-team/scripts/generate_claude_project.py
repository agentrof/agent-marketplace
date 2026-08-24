#!/usr/bin/env python3
"""Preview or materialize one team's whole-file Claude project contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import project_instructions


def preview(
    project_root: Path,
    plugin_root: Path,
    workspace: str,
    *,
    choices: dict[str, str] | None = None,
    seed_user_files: bool = False,
    scope: str = "all",
) -> dict:
    project_instructions.validate_workspace(workspace)
    if scope not in {"all", "tracked", "local"}:
        raise project_instructions.ProjectInstructionError(
            f"unsupported project generation scope: {scope}"
        )
    if scope == "local":
        return {
            "changes": {}, "choice_requests": [],
            "current_surfaces": {}, "target_surfaces": {},
        }
    return project_instructions.plan_portable_project_files(
        project_root,
        plugin_root,
        workspace,
        choices=choices,
        seed_user_files=seed_user_files,
    )


def materialize(
    project_root: Path,
    plugin_root: Path,
    workspace: str,
    *,
    choices: dict[str, str] | None = None,
    seed_user_files: bool = True,
    scope: str = "all",
) -> list[Path]:
    result = preview(
        project_root,
        plugin_root,
        workspace,
        choices=choices,
        seed_user_files=seed_user_files, scope=scope,
    )
    if result["choice_requests"]:
        raise project_instructions.ProjectInstructionError(
            "project instruction reconciliation choice required: "
            + json.dumps(result["choice_requests"], sort_keys=True)
        )
    return project_instructions.apply_changes(result["changes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "check", "apply"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.set_defaults(workspace=project_instructions.CANONICAL_WORKSPACE)
    parser.add_argument("--choice", action="append", default=[])
    parser.add_argument("--seed-user-files", action="store_true")
    parser.add_argument("--scope", choices=("all", "tracked", "local"), default="all")
    args = parser.parse_args()
    project = args.project_root.resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    try:
        choices = project_instructions.parse_choices(args.choice)
        if args.action == "inspect":
            team = project_instructions.plugin_name(plugin_root)
            surfaces = project_instructions.owned_portable_surfaces(
                project, plugin_root, team, args.workspace
            )
            result = {
                "changes": [],
                "choice_requests": [],
                "current_surfaces": surfaces,
                "target_surfaces": surfaces,
            }
            written: list[Path] = []
        else:
            result = preview(
                project,
                plugin_root,
                args.workspace,
                choices=choices,
                seed_user_files=args.seed_user_files,
                scope=args.scope,
            )
            written = []
            if args.action == "apply" and not result["choice_requests"]:
                written = materialize(
                    project,
                    plugin_root,
                    args.workspace,
                    choices=choices,
                    seed_user_files=args.seed_user_files,
                    scope=args.scope,
                )
    except ValueError as exc:
        raise SystemExit(f"claude-project: {exc}") from exc
    print(json.dumps({
        "status": "choice_required" if result["choice_requests"] else "ok",
        "changes": sorted(
            path.relative_to(project).as_posix()
            for path in result["changes"]
        ) if isinstance(result.get("changes"), dict) else result["changes"],
        "choice_requests": result["choice_requests"],
        "current_surfaces": result["current_surfaces"],
        "target_surfaces": result["target_surfaces"],
        "written": [str(path) for path in written],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
