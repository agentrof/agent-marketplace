#!/usr/bin/env python3
"""Fail-closed fresh setup preflight and closing contract verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
TEAM = "software-engineering-team"


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def managed_block(workspace: str) -> str:
    return "\n".join((START, f"{workspace}/work-orders/",
                      f"{workspace}/planning/",
                      f"{workspace}/experience-design-work/",
                      f"{workspace}/junit-*.xml", END))


def preflight(root: Path, workspace: str) -> list[str]:
    config = read(root / workspace / "config.json")
    state = read(root / ".agentrof" / "agent-marketplace" / "project.json")
    findings = []
    if isinstance(config, dict):
        owner = config.get("team_id") or str(config.get("managed_by", "")).split(" plugin", 1)[0]
        if owner and owner != TEAM:
            findings.append(f"foreign managed-team trace: {owner}")
        if config.get("project_key"):
            findings.append("keyed config must use Agent Marketplace Upgrade")
    if state is not None:
        findings.append("existing project contract must use Agent Marketplace Upgrade")
    return findings


def closing(root: Path, workspace: str) -> list[str]:
    work = root / workspace
    config = read(work / "config.json")
    findings = []
    if not isinstance(config, dict):
        findings.append("workspace config is missing or invalid")
        config = {}
    if config.get("team_id") != TEAM:
        findings.append("config team_id mismatch")
    if config.get("project_origin") not in {"greenfield", "existing"}:
        findings.append("project_origin is not classified")
    if not config.get("project_key"):
        findings.append("PMO registration has not stamped project_key")
    state = read(root / ".agentrof" / "agent-marketplace" / "project.json")
    if not isinstance(state, dict) or state.get("contract_version") != 2:
        findings.append("project contract version is not 2")
    required = (
        "docs/experience-design", "experience-design-work", "planning",
        "work-orders",
    )
    for relative in required:
        path = work / relative
        if path.is_symlink():
            findings.append(f"managed target is symlinked: {workspace}/{relative}")
        elif not path.is_dir():
            findings.append(f"missing managed directory: {workspace}/{relative}")
    ignore_path = root / ".gitignore"
    text = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    if text.count(START) != 1 or text.count(END) != 1:
        findings.append("managed .gitignore marker is missing or duplicated")
    elif managed_block(workspace) not in text:
        findings.append("managed .gitignore block is stale")
    if isinstance(state, dict):
        if not state.get("hosts"):
            findings.append("host project contract is missing")
        surfaces = state.get("managed_surfaces", {})
        if not isinstance(surfaces, dict) or not any(":" in key for key in surfaces):
            findings.append("host-managed project surfaces are missing")
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "check"])
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--workspace", default="workspace")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    values = preflight(root, args.workspace) if args.command == "preflight" else closing(root, args.workspace)
    result = {"ok": not values, "command": args.command, "findings": values}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for value in values:
            print(f"ERROR {root}:1 [setup_contract] {value}")
    return 1 if values else 0


if __name__ == "__main__":
    raise SystemExit(main())
