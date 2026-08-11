#!/usr/bin/env python3
"""Fail-closed fresh setup preflight and closing contract verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
TEAM = "software-engineering-team"


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def local_roots() -> tuple[str, ...]:
    for path in (
        Path(__file__).resolve().parents[1] / "product.json",
        Path(__file__).resolve().parents[3] / "product.json",
    ):
        value = read(path)
        environment = value.get("project_environment", {}) \
            if isinstance(value, dict) else {}
        projections = environment.get("projection_roots", {}) \
            if isinstance(environment, dict) else {}
        roots = [str(environment.get("runtime_root", ""))]
        if isinstance(projections, dict):
            roots.extend(str(item) for item in projections.values())
        if roots and all(item.startswith(".") for item in roots):
            return tuple(dict.fromkeys(roots))
    raise ValueError("project-local root policy is missing")


def managed_block(workspace: str) -> str:
    return "\n".join((START, *(f"/{value}/" for value in local_roots()),
                      f"{workspace}/junit-*.xml",
                      f"{workspace}/docs/.obsidian/*",
                      f"!{workspace}/docs/.obsidian/app.json",
                      f"!{workspace}/docs/.obsidian/appearance.json",
                      f"!{workspace}/docs/.obsidian/core-plugins.json",
                      f"!{workspace}/docs/.obsidian/graph.json",
                      f"!{workspace}/docs/.obsidian/types.json",
                      f"!{workspace}/docs/.obsidian/snippets/",
                      f"!{workspace}/docs/.obsidian/snippets/**",
                      f"{workspace}/docs/.obsidian/workspace.json",
                      f"{workspace}/docs/.obsidian/workspace-mobile.json",
                      f"{workspace}/docs/.trash/", END))


def preflight(root: Path, workspace: str) -> list[str]:
    config = read(root / workspace / "config.json")
    state = read(root / ".agentrof" / "agent-marketplace" / "project.json")
    findings = []
    if isinstance(config, dict):
        contract = config.get("agent_marketplace", {})
        owner = contract.get("team_id", "") if isinstance(contract, dict) else ""
        owner = owner or config.get("team_id") \
            or str(config.get("managed_by", "")).split(" plugin", 1)[0]
        if owner and owner != TEAM:
            findings.append(f"foreign managed-team trace: {owner}")
        if config.get("project_key") and (
            not isinstance(contract, dict)
            or contract.get("contract_version") != 5
        ):
            findings.append("keyed config must use Agent Marketplace Upgrade")
        elif isinstance(contract, dict) and contract.get("contract_version") == 5:
            findings.append(
                "existing project contract must use environment reconciliation"
            )
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
    state = config.get("agent_marketplace", {}) if isinstance(config, dict) else {}
    owner = state.get("team_id", "") if isinstance(state, dict) else ""
    if owner != TEAM:
        findings.append("config team_id mismatch")
    if config.get("project_origin") not in {"greenfield", "existing"}:
        findings.append("project_origin is not classified")
    if not config.get("project_key"):
        findings.append("PMO registration has not stamped project_key")
    if not isinstance(state, dict) or state.get("contract_version") != 5:
        findings.append("project contract version is not 5")
    elif not state.get("contract_sha256"):
        findings.append("project contract hash is missing")
    required = (
        "apps", "environment", "demos", "sketches",
        "docs/business-analysis", "docs/solution-design",
        "docs/system-architecture", "docs/design-system/pages",
        "docs/experience-design",
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
    for relative in (f"{value}/probe" for value in local_roots()):
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", relative],
            cwd=root, check=False,
        )
        if ignored.returncode != 0:
            findings.append(f"local projection path is not ignored: {relative}")
    tracked_local = subprocess.run(
        ["git", "ls-files", "--", *local_roots()],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if tracked_local.returncode != 0:
        findings.append("tracked local projection check failed")
    elif tracked_local.stdout.strip():
        findings.append(
            "local runtime or projection files are force-added: "
            + ", ".join(tracked_local.stdout.splitlines())
        )
    for relative in (
        "docs/.obsidian/app.json", "docs/.obsidian/appearance.json",
        "docs/.obsidian/core-plugins.json", "docs/.obsidian/graph.json",
        "docs/.obsidian/types.json",
    ):
        if not (work / relative).is_file():
            findings.append(f"missing managed vault payload: {workspace}/{relative}")
    portable_gate = root / ".github" / "agentrof" / "vault-gate.pyz"
    if not portable_gate.is_file():
        findings.append("repository-portable vault gate is missing")
    if isinstance(state, dict):
        payload = dict(state)
        expected_hash = str(payload.pop("contract_sha256", ""))
        actual_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        if expected_hash != actual_hash:
            findings.append("project contract hash mismatch")
        components = state.get("components", {})
        if not isinstance(components, dict) or not all(
            isinstance(value, dict) and value.get("version") and value.get("build_id")
            for value in components.values()
        ):
            findings.append("component version and build baselines are missing")
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
