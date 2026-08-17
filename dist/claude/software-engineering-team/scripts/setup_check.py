#!/usr/bin/env python3
"""Fail-closed project refresh preflight and closing contract verifier."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import marketplace_paths
import project_config
import vault_check

START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
TEAM = "software-engineering-team"
WORKSPACE = "workspace"
RUNTIME_PARTS = ("agent-marketplace", ".runtime")
PRIOR_OWNER_SUFFIX = " plugin; change only through the configure entry"
FORBIDDEN_RUNTIME_NAMES = {"project.json", "backlog.json"}
FORBIDDEN_RUNTIME_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def setup_owner(config: dict) -> str:
    """Recognize current ownership plus the two retired setup inputs."""
    owner = marketplace_paths.team_from_config(config)
    if owner:
        return owner
    contract = config.get("agent_marketplace")
    if isinstance(contract, dict):
        return str(contract.get("team_id", "")).strip()
    prior = str(config.get("managed_by", "")).strip()
    if prior.endswith(PRIOR_OWNER_SUFFIX):
        return prior[:-len(PRIOR_OWNER_SUFFIX)]
    return ""


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


def runtime_root(root: Path) -> Path:
    return root / local_roots()[0] / Path(*RUNTIME_PARTS)


def runtime_findings(root: Path) -> list[str]:
    """The owned local tree contains only one disposable runtime directory."""
    findings: list[str] = []
    runtime = runtime_root(root)
    owned_root = runtime.parent
    runtime_chain = (owned_root.parent, owned_root, runtime)
    symlink = next((path for path in runtime_chain if path.is_symlink()), None)
    if symlink is not None:
        return [
            "project-local runtime path is symlinked: "
            + symlink.relative_to(root).as_posix()
        ]
    if not runtime.is_dir():
        findings.append(
            "missing project-local runtime: .agentrof/agent-marketplace/.runtime"
        )
    if owned_root.is_dir():
        for path in sorted(owned_root.iterdir()):
            if path.name != ".runtime":
                findings.append(
                    "only .runtime may exist in the owned local tree: "
                    + path.relative_to(root).as_posix()
                )
    if runtime.is_dir():
        for path in sorted(runtime.rglob("*")):
            if not path.is_file():
                continue
            if path.name in FORBIDDEN_RUNTIME_NAMES \
                    or path.suffix.casefold() in FORBIDDEN_RUNTIME_SUFFIXES:
                findings.append(
                    "database or canonical state is forbidden in runtime: "
                    + path.relative_to(root).as_posix()
                )
    return findings


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
    config_path = root / workspace / "config.json"
    config = read(config_path)
    findings = []
    for candidate in sorted(root.glob("*/config.json")):
        if candidate.parent.name == WORKSPACE:
            continue
        value = read(candidate)
        if isinstance(value, dict) and setup_owner(value) == TEAM:
            findings.append(
                "non-canonical managed workspace: "
                + candidate.parent.relative_to(root).as_posix()
            )
    if not config_path.exists():
        return findings
    if isinstance(config, dict):
        owner = setup_owner(config)
        if owner and owner != TEAM:
            findings.append(f"foreign managed-team trace: {owner}")
    if not isinstance(config, dict):
        findings.append("workspace config is missing or invalid")
    return findings


def designation_findings(work: Path) -> list[str]:
    checker = Path(__file__).with_name("vault_check.py")
    if not checker.is_file():
        return ["designation contract checker is missing"]
    result = subprocess.run(
        [sys.executable, "-B", str(checker), "check-designations",
         "--vault", str(work / "docs"), "--json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return []
    if result.returncode == 1:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
        issues = payload.get("issues") if isinstance(payload, dict) else None
        if isinstance(issues, list):
            messages = [
                str(issue.get("message", "designation contract is invalid"))
                for issue in issues if isinstance(issue, dict)
            ]
            if messages:
                return [f"designation contract: {message}"
                        for message in messages]
    detail = (result.stderr or result.stdout).strip()
    return ["designation contract check failed"
            + (f": {detail}" if detail else "")]


def payload_findings(work: Path) -> list[str]:
    """Validate only the Obsidian payload, never active authored notes."""
    docs = work / "docs"
    if not docs.is_dir():
        return []
    try:
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        policy = vault_check.effective_policy(policy, docs)
        vault = vault_check.build_vault(docs, policy)
        findings = []
        vault_check.check_obsidian_payload(
            vault, findings, vault_check.DEFAULT_PAYLOAD,
            require_local_projection=True,
        )
        stale = vault_check.payload_reconcile_updates(
            docs, policy, vault_check.DEFAULT_PAYLOAD
        )
        stale_deletions = vault_check.payload_reconcile_deletions(
            docs, policy, vault_check.DEFAULT_PAYLOAD
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"managed vault payload check failed: {exc}"]
    messages = [
        f"managed vault payload: {finding.path}: {finding.message}"
        for finding in findings if finding.severity == "error"
    ]
    messages.extend(
        "managed vault payload is stale: "
        + path.relative_to(work.parent).as_posix()
        for path in sorted(stale, key=lambda value: str(value))
    )
    messages.extend(
        "managed vault payload has stale package-local content: "
        + path.relative_to(work.parent).as_posix()
        for path in stale_deletions
    )
    return list(dict.fromkeys(messages))


def closing(root: Path, workspace: str) -> list[str]:
    work = root / workspace
    config = read(work / "config.json")
    findings = []
    if not isinstance(config, dict):
        findings.append("workspace config is missing or invalid")
        config = {}
    owner = marketplace_paths.team_from_config(config)
    if owner != TEAM:
        findings.append("config team_id mismatch")
    if config.get("project_origin") not in {"greenfield", "existing"}:
        findings.append("project_origin is not classified")
    findings.extend(f"config contract: {value}" for value in project_config.check(config))
    for candidate in sorted(root.glob("*/config.json")):
        if candidate.parent.name == WORKSPACE:
            continue
        value = read(candidate)
        if isinstance(value, dict) and marketplace_paths.team_from_config(value) == TEAM:
            findings.append(
                "non-canonical managed workspace: "
                + candidate.parent.relative_to(root).as_posix()
            )
    required = (
        "apps", "environment", "demos", "sketches",
        "docs/business-analysis", "docs/solution-design",
        "docs/system-architecture", "docs/design-system/pages",
        "docs/experience-design", "docs/requirements", "docs/delivery", "docs/backlog",
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
    tracked_plugins = subprocess.run(
        [
            "git", "ls-files", "--",
            f"{workspace}/docs/.obsidian/community-plugins.json",
            f"{workspace}/docs/.obsidian/plugins",
        ],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if tracked_plugins.returncode != 0:
        findings.append("local Obsidian plugin projection tracking check failed")
    elif tracked_plugins.stdout.strip():
        findings.append(
            "package-projected local Obsidian plugin files are tracked: "
            + ", ".join(tracked_plugins.stdout.splitlines())
        )
    for relative in (
        "docs/.obsidian/app.json", "docs/.obsidian/appearance.json",
        "docs/.obsidian/core-plugins.json", "docs/.obsidian/graph.json",
        "docs/.obsidian/types.json",
    ):
        if not (work / relative).is_file():
            findings.append(f"missing managed vault payload: {workspace}/{relative}")
    findings.extend(runtime_findings(root))
    findings.extend(payload_findings(work))
    findings.extend(designation_findings(work))
    portable_gate = root / ".github" / "agentrof" / "vault-gate.pyz"
    if not portable_gate.is_file():
        findings.append("repository-portable vault gate is missing")
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "check"])
    parser.add_argument("--project-root", required=True)
    parser.add_argument(
        "--workspace", default=WORKSPACE, choices=(WORKSPACE,),
        help="compatibility option; the project workspace is always 'workspace'",
    )
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
