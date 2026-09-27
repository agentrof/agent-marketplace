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
ATTRIBUTES_START = "# agent-marketplace:software-engineering-team:gitattributes:start"
ATTRIBUTES_END = "# agent-marketplace:software-engineering-team:gitattributes:end"
TEAM = "software-engineering-team"
WORKSPACE = "workspace"
RUNTIME_PARTS = ("agent-marketplace", ".runtime")
# Canonical project and backlog state belongs in tracked workspace files, in any
# format. Disposable tool output in ignored scratch, such as a scanner cache or a
# mutation session, is not project truth and its storage format does not make it so.
FORBIDDEN_RUNTIME_NAMES = {
    f"{name}{suffix}"
    for name in ("project", "backlog")
    for suffix in (".json", ".db", ".sqlite", ".sqlite3")
}


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def setup_owner(config: dict) -> str:
    """Resolve ownership from the canonical project configuration."""
    return marketplace_paths.team_from_config(config)


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
            if path.name.casefold() in FORBIDDEN_RUNTIME_NAMES:
                findings.append(
                    "canonical state is forbidden in runtime: "
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


def managed_attributes_block(workspace: str) -> str:
    """Keep every checkout of the governed vault byte-identical to its commits.

    Backlog, Experience and stage-package checks compare working bytes with
    committed blobs, so the vault opts out of end-of-line conversion such as
    Git for Windows' default ``core.autocrlf=true``.
    """
    return "\n".join((ATTRIBUTES_START, f"{workspace}/docs/** -text",
                      ATTRIBUTES_END))


def attribute_findings(root: Path, workspace: str) -> list[str]:
    """Read the effective rule back through Git for every governed file.

    A later root line, a nested ``.gitattributes`` or ``.git/info/attributes``
    outranks the managed block, so its text alone does not prove the effect.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
         "--", f"{workspace}/docs"],
        cwd=root, capture_output=True, check=False,
    )
    if listed.returncode != 0:
        return ["governed checkout attribute check failed"]
    paths = list(dict.fromkeys(
        item for item in listed.stdout.split(b"\0") if item
    ))
    if not paths:
        return []
    resolved = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "text"],
        cwd=root, input=b"".join(item + b"\0" for item in paths),
        capture_output=True, check=False,
    )
    fields = resolved.stdout.split(b"\0")
    if resolved.returncode != 0 or len(fields) != 3 * len(paths) + 1:
        return ["governed checkout attribute check failed"]
    converted = [
        f"{path.decode('utf-8', 'backslashreplace')} (text: "
        f"{value.decode('utf-8', 'backslashreplace')})"
        for path, _name, value in (
            fields[index:index + 3] for index in range(0, len(fields) - 1, 3)
        )
        if value != b"unset"
    ]
    if not converted:
        return []
    more = len(converted) - 5
    return [
        "managed .gitattributes rule is overridden; governed files still"
        " convert line endings: " + ", ".join(converted[:5])
        + (f" and {more} more" if more > 0 else "")
    ]


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


def legacy_experience_findings(docs: Path) -> list[str]:
    experience = docs / "experience-design"
    if not experience.exists() and not experience.is_symlink():
        return []
    findings: list[str] = []
    if experience.is_symlink():
        return [
            "Experience subtree symlink exists at experience-design; remove"
            " it before setup"
        ]
    if not experience.is_dir():
        return []
    if (experience / "baselines").exists():
        findings.append(
            "legacy Experience baseline tree exists at docs/experience-design/baselines; no migration is available"
        )
    for name in ("artifact-registry.json", "artifact-manifest.md"):
        path = experience / name
        if path.exists():
            findings.append(
                f"legacy Experience artifact index exists at {path.relative_to(docs)}"
            )
    entries = sorted(experience.rglob("*"))
    for path in entries:
        relative = path.relative_to(docs)
        if path.is_symlink():
            findings.append(
                "Experience subtree symlink exists at "
                f"{relative.as_posix()}; remove it before setup"
            )
        elif path.is_file() and path.stat().st_nlink != 1:
            findings.append(
                "Experience Design file has a hard-link alias at "
                f"{relative.as_posix()}; remove every alias before setup"
            )
        if (path.name == "artifact-registry.json"
                and "_generated" in relative.parts):
            findings.append(
                f"legacy Experience artifact index exists at {relative.as_posix()}"
            )
    experiences = experience / "experiences"
    for path in experiences.glob("exp-*") \
            if experiences.is_dir() and not experiences.is_symlink() \
            else []:
        if path.is_dir():
            findings.append(
                f"retired exp- Experience slug exists at {path.relative_to(docs)}"
            )
    for path in experience.rglob("*.md"):
        if path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in (
            "baseline_id:", "program_id:", "release_id:",
            "type: experience-baseline", "type: experience-space",
            "type: experience-domain", "type: artifact-manifest",
        )):
            findings.append(
                f"legacy Experience metadata exists at {path.relative_to(docs)}"
            )
    try:
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        for path in entries:
            if not (path.is_file() or path.is_symlink()):
                continue
            violation = vault_check.artifact_hard_cut_violation(
                policy, path.relative_to(docs).as_posix()
            )
            if violation:
                findings.append(violation)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings.append(f"Experience artifact policy cannot be checked: {exc}")
    return sorted(set(findings))


def listed_paths(listing: bytes) -> list[str]:
    """Name each path of a NUL-separated Git listing as Git holds it.

    Without -z Git quotes a name holding a control character, a double quote
    or a backslash, and under its default core.quotePath any byte outside
    ASCII, so a finding would name the quoted form instead of the file.
    """
    return [item.decode("utf-8", "backslashreplace")
            for item in listing.split(b"\0") if item]


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
        "apps", "demos", "sketches",
        "docs/business-analysis", "docs/solution-design",
        "docs/system-architecture", "docs/design-system/pages",
        "docs/experience-design", "docs/requirements", "docs/operation",
        "docs/delivery", "docs/delivery/governance", "docs/backlog",
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
    attributes_path = root / ".gitattributes"
    attributes = attributes_path.read_text(encoding="utf-8") \
        if attributes_path.is_file() else ""
    if attributes.count(ATTRIBUTES_START) != 1 \
            or attributes.count(ATTRIBUTES_END) != 1:
        findings.append("managed .gitattributes marker is missing or duplicated")
    elif managed_attributes_block(workspace) not in attributes:
        findings.append("managed .gitattributes block is stale")
    else:
        findings.extend(attribute_findings(root, workspace))
    for relative in (f"{value}/probe" for value in local_roots()):
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", relative],
            cwd=root, check=False,
        )
        if ignored.returncode != 0:
            findings.append(f"local projection path is not ignored: {relative}")
    tracked_local = subprocess.run(
        ["git", "ls-files", "-z", "--", *local_roots()],
        cwd=root, capture_output=True, check=False,
    )
    if tracked_local.returncode != 0:
        findings.append("tracked local projection check failed")
    elif tracked_local.stdout:
        findings.append(
            "local runtime or projection files are force-added: "
            + ", ".join(listed_paths(tracked_local.stdout))
        )
    tracked_plugins = subprocess.run(
        [
            "git", "ls-files", "-z", "--",
            f"{workspace}/docs/.obsidian/community-plugins.json",
            f"{workspace}/docs/.obsidian/plugins",
        ],
        cwd=root, capture_output=True, check=False,
    )
    if tracked_plugins.returncode != 0:
        findings.append("local Obsidian plugin projection tracking check failed")
    elif tracked_plugins.stdout:
        findings.append(
            "package-projected local Obsidian plugin files are tracked: "
            + ", ".join(listed_paths(tracked_plugins.stdout))
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
    findings.extend(legacy_experience_findings(work / "docs"))
    portable_gate = root / ".github" / "agentrof" / "vault-gate.pyz"
    if not portable_gate.is_file():
        findings.append("repository-portable vault gate is missing")
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "check"])
    parser.add_argument("--project-root", required=True)
    parser.set_defaults(workspace=WORKSPACE)
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
