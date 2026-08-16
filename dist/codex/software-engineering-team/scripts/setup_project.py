#!/usr/bin/env python3
"""Bootstrap one project-local Software Engineering Team workspace.

The command is deliberately small and file-first: it creates only missing
project directories, writes the team-owned workspace declaration, materializes
the Obsidian payload, installs the repository-portable gate and maintains the
managed root ``.gitignore`` block. Existing authored files and user ignore
rules are preserved.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import marketplace_paths
import project_config
import vault_check


TEAM = "software-engineering-team"
WORKSPACE = "workspace"
START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
REQUIRED_DIRS = (
    "apps", "environment", "demos", "sketches",
    "docs/business-analysis", "docs/solution-design",
    "docs/system-architecture", "docs/design-system/pages",
    "docs/experience-design", "docs/backlog",
)
RUNTIME_PARTS = ("agent-marketplace", ".runtime")
PRIOR_OWNER_SUFFIX = " plugin; change only through the configure entry"


def atomic_text(path: Path, text: str) -> None:
    """Replace one file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"setup-project: invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"setup-project: {path} must contain an object")
    return value


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


def git_root(project: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SystemExit("setup-project: project-root must be a Git checkout")
    return Path(result.stdout.strip()).resolve()


def local_roots() -> tuple[str, ...]:
    script = Path(__file__).resolve()
    product = {}
    for candidate in (script.parents[1] / "product.json",
                      script.parents[3] / "product.json"):
        if candidate.is_file():
            product = load_json(candidate)
            break
    environment = product.get("project_environment", {})
    projections = environment.get("projection_roots", {})
    roots = [str(environment.get("runtime_root", ""))]
    if isinstance(projections, dict):
        roots.extend(str(value) for value in projections.values())
    if not roots or not all(value.startswith(".") for value in roots):
        raise SystemExit("setup-project: project-local root policy is invalid")
    return tuple(dict.fromkeys(roots))


def runtime_root(root: Path) -> Path:
    return root / local_roots()[0] / Path(*RUNTIME_PARTS)


def create_runtime(root: Path) -> Path:
    runtime = runtime_root(root)
    relative = runtime.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SystemExit(
                f"setup-project: runtime path is symlinked: {current}"
            )
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def managed_block(workspace: str) -> str:
    roots = local_roots()
    return "\n".join((
        START,
        *(f"/{value}/" for value in roots),
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
        f"{workspace}/docs/.trash/",
        END,
    ))


def update_gitignore(root: Path, workspace: str) -> bool:
    path = root / ".gitignore"
    if path.is_symlink():
        raise SystemExit("setup-project: .gitignore must not be a symbolic link")
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = managed_block(workspace)
    start_count, end_count = current.count(START), current.count(END)
    if start_count > 1 or end_count > 1 or start_count != end_count:
        raise SystemExit("setup-project: managed .gitignore markers are duplicated or incomplete")
    if start_count == 1:
        left = current.index(START)
        right = current.index(END, left) + len(END)
        updated = current[:left] + block + current[right:]
    else:
        prefix = current.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + block + "\n"
    if updated != current:
        atomic_text(path, updated)
        return True
    return False


def run_checked(argv: list[str], label: str) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"setup-project: {label} failed" + (f": {detail}" if detail else ""))


def bootstrap(args) -> dict:
    root = git_root(Path(args.project_root).resolve())
    workspace = args.workspace
    alternate = []
    for candidate in sorted(root.glob("*/config.json")):
        if candidate.parent.name == WORKSPACE:
            continue
        candidate_config = load_json(candidate)
        if setup_owner(candidate_config) == TEAM:
            alternate.append(candidate.parent.relative_to(root).as_posix())
    if alternate:
        raise SystemExit(
            "setup-project: non-canonical managed workspace detected: "
            + ", ".join(alternate)
            + f"; move its durable content under {WORKSPACE}/"
        )
    workspace_root = root / workspace
    config_path = workspace_root / "config.json"
    config = load_json(config_path)
    if config:
        owner = setup_owner(config)
        if owner and owner != TEAM:
            raise SystemExit(f"setup-project: workspace is owned by {owner}")
    else:
        config = {
            "team_id": TEAM,
            "project_origin": "unclassified",
            "scale": args.scale,
            "output_language": args.output_language,
            "terminology_language": args.terminology_language,
        }
    # Setup is the compatibility boundary. Collapse retired team-owned config
    # envelopes here so upgrades need neither a migration runner nor a second
    # durable state store. Unknown project-owned fields remain untouched.
    config.pop("agent_marketplace", None)
    config.pop("model_overrides", None)
    prior_owner = str(config.get("managed_by", "")).strip()
    if prior_owner == TEAM or prior_owner == TEAM + PRIOR_OWNER_SUFFIX:
        config.pop("managed_by", None)
    config["team_id"] = TEAM
    config.setdefault("scale", args.scale)
    config.setdefault("output_language", args.output_language)
    config.setdefault("terminology_language", args.terminology_language)
    current_origin = str(config.get("project_origin", "unclassified"))
    if current_origin not in {"unclassified", args.origin}:
        raise SystemExit(
            "setup-project: project_origin is already classified as "
            f"{current_origin}; use configure before changing it"
        )
    config["project_origin"] = args.origin
    config_errors = project_config.check(config)
    if config_errors:
        raise SystemExit("setup-project: invalid workspace config: "
                         + "; ".join(config_errors))
    atomic_text(config_path, json.dumps(config, indent=2) + "\n")

    for relative in REQUIRED_DIRS:
        target = workspace_root / relative
        if target.is_symlink():
            raise SystemExit(f"setup-project: managed target is symlinked: {target}")
        target.mkdir(parents=True, exist_ok=True)

    runtime = create_runtime(root)

    package_root = Path(__file__).resolve().parents[1]
    vault_root = workspace_root / "docs"
    policy_path = package_root / "skill-content" / "obsidian-vault" / "data" / "vault-policy.json"
    payload_root = package_root / "templates" / "vault" / ".obsidian"
    policy = vault_check.load_policy(policy_path)
    copied = vault_check.materialize_payload(vault_root, policy, payload_root)
    run_checked([
        sys.executable, str(Path(__file__).with_name("vault_check.py")),
        "reconcile-designations", "--vault", str(vault_root), "--defaults",
    ], "designation reconciliation")
    ignore_changed = update_gitignore(root, workspace)
    run_checked([
        sys.executable, str(Path(__file__).with_name("vault_gate.py")),
        "install", "--project-root", str(root),
    ], "portable gate installation")
    return {
        "ok": True,
        "project_root": str(root),
        "workspace": workspace,
        "project_origin": args.origin,
        "payload_files_copied": copied,
        "gitignore_changed": ignore_changed,
        "runtime_root": str(runtime),
        "next_entry": "business-analysis" if args.origin == "greenfield" else "deliver",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument(
        "--workspace", default=WORKSPACE, choices=(WORKSPACE,),
        help="compatibility option; the project workspace is always 'workspace'",
    )
    parser.add_argument("--origin", choices=("greenfield", "existing"), default="greenfield")
    parser.add_argument("--scale", choices=("small", "medium", "large", "x-large", "xx-large", "enterprise"), default="small")
    parser.add_argument("--output-language", default="English")
    parser.add_argument("--terminology-language", default="English")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = bootstrap(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"setup-project: ready ({result['next_entry']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
