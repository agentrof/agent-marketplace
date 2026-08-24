#!/usr/bin/env python3
"""Inspect, check or apply one convergent project-local team refresh.

``inspect`` produces the exact managed-surface plan without changing the
project, ``check`` verifies the installed project contract, and ``apply``
executes the inspected plan with rollback when the closing check does not pass.
Omitting the command runs ``apply``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import marketplace_paths
import delivery_governance
import operation_compile
import project_config
import setup_check
import vault_check


TEAM = "software-engineering-team"
WORKSPACE = "workspace"
START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
REQUIRED_DIRS = (
    "apps", "demos", "sketches",
    "docs/business-analysis", "docs/solution-design",
    "docs/system-architecture", "docs/design-system/pages",
    "docs/experience-design", "docs/requirements", "docs/operation",
    "docs/delivery", "docs/delivery/governance", "docs/backlog",
)
RUNTIME_PARTS = ("agent-marketplace", ".runtime")
JSON_PAYLOAD_FILES = (
    "app.json", "appearance.json", "core-plugins.json", "graph.json",
    "types.json", "community-plugins.json",
)


class SetupError(RuntimeError):
    """A refresh cannot be planned or safely applied."""


def atomic_text(path: Path, text: str, before_replace=None) -> None:
    """Replace one file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_bytes(path: Path, content: bytes, mode: int = 0o644,
                 before_replace=None) -> None:
    """Replace one binary file atomically with deterministic permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def legacy_topology_blockers(vault_root: Path) -> list[str]:
    """Hard-cut models must be removed deliberately, never rewritten by setup."""
    blockers: list[str] = []
    experience = vault_root / "experience-design"
    legacy_experience = experience / "baselines"
    if legacy_experience.exists():
        blockers.append("legacy Experience baseline tree exists at docs/experience-design/baselines; no migration is available")
    for path in (experience / "experiences").glob("exp-*") if (experience / "experiences").is_dir() else []:
        if path.is_dir():
            blockers.append(f"retired exp- Experience slug exists at {path.relative_to(vault_root)}; rename it deliberately before setup")
    for path in experience.rglob("*.md") if experience.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in ("baseline_id:", "program_id:", "release_id:", "type: experience-baseline", "type: experience-space", "type: experience-domain")):
            blockers.append(f"legacy Experience metadata exists at {path.relative_to(vault_root)}; no migration is available")
    architecture = vault_root / "system-architecture"
    for name in ("api-contract.md", "data-model.md", "threat-model.md", "environment.md"):
        if (architecture / name).exists():
            blockers.append(f"legacy root-level System Architecture record exists at system-architecture/{name}; no migration is available")
    return sorted(set(blockers))


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SetupError(f"{path} must contain an object")
    return value


def setup_owner(config: dict) -> str:
    """Resolve ownership from the canonical project configuration."""
    return marketplace_paths.team_from_config(config)


def git_root(project: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SetupError("project-root must be a Git checkout")
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
    projections = environment.get("projection_roots", {}) \
        if isinstance(environment, dict) else {}
    roots = [str(environment.get("runtime_root", ""))] \
        if isinstance(environment, dict) else []
    if isinstance(projections, dict):
        roots.extend(str(value) for value in projections.values())
    if not roots or not all(value.startswith(".") for value in roots):
        raise SetupError("project-local root policy is invalid")
    return tuple(dict.fromkeys(roots))


def runtime_root(root: Path) -> Path:
    return root / local_roots()[0] / Path(*RUNTIME_PARTS)


def assert_not_symlinked(root: Path, target: Path, label: str) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise SetupError(f"{label} escapes the project root: {target}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SetupError(f"{label} is symlinked: {current}")


def create_runtime(root: Path) -> Path:
    runtime = runtime_root(root)
    assert_not_symlinked(root, runtime, "runtime path")
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


@contextlib.contextmanager
def refresh_guard(root: Path, timeout_seconds: float = 3.0):
    """Serialize setup and host projection maintenance with a bounded wait."""
    runtime = create_runtime(root)
    guard_path = runtime / "setup-apply.guard"
    handle = guard_path.open("a+b")
    windows = os.name == "nt"
    acquired = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if windows:
                    msvcrt = __import__("msvcrt")
                    if guard_path.stat().st_size == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = __import__("fcntl")
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.05)
        if not acquired:
            raise SetupError("maintenance_busy: setup/projector maintenance lock is busy")
        yield
    finally:
        if acquired and windows:
            msvcrt = __import__("msvcrt")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif acquired:
            fcntl = __import__("fcntl")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


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


def proposed_gitignore(root: Path, workspace: str) -> tuple[str, str]:
    path = root / ".gitignore"
    assert_not_symlinked(root, path, ".gitignore")
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = managed_block(workspace)
    start_count, end_count = current.count(START), current.count(END)
    if start_count > 1 or end_count > 1 or start_count != end_count:
        raise SetupError(
            "managed .gitignore markers are duplicated or incomplete"
        )
    if start_count == 1:
        left = current.index(START)
        right = current.index(END, left) + len(END)
        updated = current[:left] + block + current[right:]
    else:
        prefix = current.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + block + "\n"
    return current, updated


def run_checked(argv: list[str], label: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SetupError(label + " failed" + (f": {detail}" if detail else ""))
    return result.stdout


def package_surfaces() -> tuple[Path, Path, dict]:
    package_root = Path(__file__).resolve().parents[1]
    policy_path = (
        package_root / "skill-content" / "obsidian-vault" / "data"
        / "vault-policy.json"
    )
    payload_root = package_root / "templates" / "vault" / ".obsidian"
    return payload_root, policy_path, vault_check.load_policy(policy_path)


def payload_sources(policy: dict, payload_root: Path,
                    vault_root: Path) -> list[tuple[Path, Path]]:
    values: list[tuple[Path, Path]] = []
    home_rel = Path(str(policy.get("home_file", "home.md")))
    home_source = payload_root.parent / home_rel
    if home_source.is_file():
        values.append((home_source, vault_root / home_rel))
    for source in sorted(path for path in payload_root.rglob("*")
                         if path.is_file()):
        values.append((source, vault_root / ".obsidian"
                       / source.relative_to(payload_root)))
    return values


def relation_report_updates(vault_root: Path, policy: dict) -> dict[Path, str]:
    """Plan only compiler-owned relation reports, never authored note blocks."""
    effective = vault_check.effective_policy(policy, vault_root)
    vault = vault_check.build_vault(vault_root, effective)
    updates: dict[Path, str] = {}
    for relative, content in vault_check.relation_reports(vault).items():
        target = vault_root / relative
        current = target.read_text(encoding="utf-8") \
            if target.is_file() else ""
        if current != content:
            updates[target] = content
    return updates


def projection_ownership(target: Path, vault_root: Path) -> str:
    relative = target.relative_to(vault_root).as_posix()
    if relative == ".obsidian/community-plugins.json" \
            or relative.startswith(".obsidian/plugins/"):
        return "package_local"
    return "tracked_managed"


def expected_gate_bytes() -> bytes:
    installer = Path(__file__).with_name("vault_gate.py")
    with tempfile.TemporaryDirectory(prefix="setup-gate-plan.") as temporary:
        root = Path(temporary)
        run_checked([
            sys.executable, str(installer), "install", "--project-root",
            str(root),
        ], "portable gate planning")
        return (root / ".github" / "agentrof" / "vault-gate.pyz").read_bytes()


def desired_config(args, config_path: Path) -> tuple[dict, list[str]]:
    current = load_json(config_path)
    if current:
        owner = setup_owner(current)
        if owner and owner != TEAM:
            raise SetupError(f"workspace is owned by {owner}")
        schema = current.get("schema_version")
        if isinstance(schema, int) and schema > project_config.SCHEMA_VERSION:
            raise SetupError(
                "workspace config uses a future schema_version and cannot be downgraded"
            )
        config = {
            "schema_version": project_config.SCHEMA_VERSION,
            "team_id": TEAM,
            "output_language": current.get("output_language", args.output_language),
            "terminology_language": current.get(
                "terminology_language", args.terminology_language
            ),
        }
    else:
        config = {
            "schema_version": project_config.SCHEMA_VERSION,
            "team_id": TEAM,
            "output_language": args.output_language,
            "terminology_language": args.terminology_language,
        }

    errors = project_config.check(config)
    if errors:
        raise SetupError("invalid workspace config: " + "; ".join(errors))
    changed_fields = sorted(
        key for key in set(current) | set(config)
        if current.get(key) != config.get(key) or (key in current) != (key in config)
    )
    return config, changed_fields


def legacy_operation_updates(workspace_root: Path, config: dict) -> tuple[dict[Path, str], list[Path], list[str]]:
    """Translate retired command fields into draft vault contracts.

    No stack value becomes a Solution decision. A legacy command becomes a
    transparent, still-draft operational proposal until a user binds it to an
    accepted Solution decision and approves the contract.
    """
    docs = workspace_root / "docs"
    updates: dict[Path, str] = {}
    deletions: list[Path] = []
    blockers: list[str] = []
    verification = docs / "operation" / "verification-contract.md"
    environment = docs / "operation" / "environment-contract.md"
    legacy_test = config.get("test_command")
    legacy_mutation = config.get("mutation_command")
    legacy_env = config.get("env_command")
    if not verification.exists() and (isinstance(legacy_test, str) or isinstance(legacy_mutation, str)):
        props = operation_compile.initial_props("verification", [])
        props["test_command"] = legacy_test if isinstance(legacy_test, str) else ""
        if isinstance(legacy_mutation, str) and legacy_mutation.strip():
            props["mutation_disposition"] = "required"
            props["mutation_command"] = legacy_mutation
            props["mutation_rationale"] = ""
        body = "# Verification Contract\n\n## Contract\n\nMigrated from retired workspace config. Bind this draft to accepted Solution decisions before approval.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/operation|Operation]]"
        updates[verification] = operation_compile.render(props, body)
    legacy_environment = workspace_root / "environment" / "contract.md"
    if not environment.exists() and legacy_environment.exists():
        text = legacy_environment.read_text(encoding="utf-8")
        try:
            props, _body_line, error = operation_compile.parse_frontmatter(text)
        except ValueError:
            props, error = {}, "invalid frontmatter"
        if error or props.get("type") not in {"environment-contract", "environment-reference"}:
            blockers.append(
                "legacy workspace/environment/contract.md is not a recognized environment contract; preserve it and migrate manually"
            )
        else:
            updates[environment] = text
            # A recognized legacy document has a lossless canonical successor.
            # Keep this removal in the same refresh transaction as the create,
            # so a failed closing gate restores the original file.
            deletions.append(legacy_environment)
    elif not environment.exists() and isinstance(legacy_env, str):
        props = operation_compile.initial_props("environment", [])
        props["env_command"] = legacy_env
        body = "# Environment Contract\n\n## Contract\n\nMigrated from retired workspace config. Bind this draft to accepted Solution decisions before approval.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/operation|Operation]]"
        updates[environment] = operation_compile.render(props, body)
    return updates, deletions, blockers


def legacy_governance_update(workspace_root: Path, config: dict) -> dict[Path, str]:
    value = config.get("max_parallel")
    target = workspace_root / "docs" / "delivery" / "governance" / "governance.md"
    if target.exists() or not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return {}
    props = {
        "type": "delivery-governance", "title": "Delivery Governance",
        "status": "approved", "revision": 1, "max_parallel": value,
        "governance_hash": "", "source_hash": "",
        "approved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tags": ["doc/delivery-governance", "status/approved"],
    }
    body = "# Delivery Governance\n\n## Coordination\n\nMigrated from retired workspace config. This is the hard maximum number of active Delivery slots.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/delivery|Delivery]]"
    digest = delivery_governance.governance_hash(props, body)
    props["governance_hash"] = digest
    props["source_hash"] = digest
    return {target: delivery_governance.render(props, body)}


def alternate_workspaces(root: Path) -> list[str]:
    alternate = []
    for candidate in sorted(root.glob("*/config.json")):
        if candidate.parent.name == WORKSPACE:
            continue
        candidate_config = load_json(candidate)
        if setup_owner(candidate_config) == TEAM:
            alternate.append(candidate.parent.relative_to(root).as_posix())
    return alternate


def public_plan(plan: dict) -> dict:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def value_deltas(before, after, prefix: str = "") -> list[dict]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before:
                changes.append({"key": path, "before": None,
                                "after": after[key]})
            elif key not in after:
                changes.append({"key": path, "before": before[key],
                                "after": None})
            else:
                changes.extend(value_deltas(before[key], after[key], path))
        return changes
    if before != after:
        return [{"key": prefix, "before": before, "after": after}]
    return []


def bytes_hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def build_plan(args) -> dict:
    root = git_root(Path(args.project_root).resolve())
    workspace = args.workspace
    workspace_root = root / workspace
    config_path = workspace_root / "config.json"
    vault_root = workspace_root / "docs"
    for target, label in (
        (workspace_root, "workspace"), (config_path, "workspace config"),
        (vault_root, "vault root"), (vault_root / ".obsidian", "Obsidian payload"),
    ):
        assert_not_symlinked(root, target, label)
    alternate = alternate_workspaces(root)
    if alternate:
        raise SetupError(
            "non-canonical managed workspace detected: "
            + ", ".join(alternate)
            + f"; move its durable content under {WORKSPACE}/"
        )

    legacy_config = load_json(config_path)
    config, changed_fields = desired_config(args, config_path)
    payload_root, policy_path, policy = package_surfaces()
    operations: list[dict] = []
    blockers: list[str] = legacy_topology_blockers(vault_root)
    target_config = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    current_config = config_path.read_text(encoding="utf-8") \
        if config_path.is_file() else ""
    if target_config != current_config:
        current_value = load_json(config_path) if config_path.is_file() else {}
        operations.append({
            "action": "create" if not config_path.exists() else "replace",
            "surface": "workspace_config",
            "path": f"{workspace}/config.json",
            "schema_from": current_value.get("schema_version", "unversioned"),
            "schema_to": project_config.SCHEMA_VERSION,
            "preserved_fields": sorted(
                key for key in project_config.CONFIG_FIELDS
                if key in current_value and current_value.get(key) == config.get(key)
            ),
            "removed_fields": sorted(
                key for key in current_value if key not in project_config.CONFIG_FIELDS
            ),
            "migrated_fields": sorted(
                key for key in ("test_command", "mutation_command", "env_command", "max_parallel")
                if key in current_value
            ),
            "fields": changed_fields,
            "changes": value_deltas(current_value, config),
            "ownership": "tracked_managed",
        })

    operation_updates, operation_deletions, migration_blockers = legacy_operation_updates(
        workspace_root, legacy_config
    )
    governance_updates = legacy_governance_update(workspace_root, legacy_config)
    blockers.extend(migration_blockers)
    if operation_updates:
        operation_map = vault_root / "maps" / "operation.md"
        if not operation_map.exists():
            source = payload_root.parent / "maps" / "operation.md"
            operation_updates[operation_map] = source.read_text(encoding="utf-8")
    if governance_updates:
        delivery_map = vault_root / "maps" / "delivery.md"
        if not delivery_map.exists():
            source = payload_root.parent / "maps" / "delivery.md"
            governance_updates[delivery_map] = source.read_text(encoding="utf-8")
    for target, content in sorted(
        {**operation_updates, **governance_updates}.items(), key=lambda item: str(item[0])
    ):
        assert_not_symlinked(root, target, "legacy contract migration")
        operations.append({
            "action": "create", "surface": "legacy_contract_migration",
            "path": target.relative_to(root).as_posix(),
            "ownership": "tracked_migration",
            "after_hash": bytes_hash(content.encode("utf-8")),
        })
    for target in operation_deletions:
        assert_not_symlinked(root, target, "legacy contract migration")
        operations.append({
            "action": "delete", "surface": "legacy_contract_migration",
            "path": target.relative_to(root).as_posix(),
            "ownership": "tracked_migration",
            "before_hash": bytes_hash(target.read_bytes()),
        })

    for relative in REQUIRED_DIRS:
        target = workspace_root / relative
        assert_not_symlinked(root, target, f"managed target {workspace}/{relative}")
        if not target.is_dir():
            operations.append({
                "action": "create", "surface": "directory",
                "path": f"{workspace}/{relative}",
                "ownership": "tracked_container",
            })

    runtime = runtime_root(root)
    assert_not_symlinked(root, runtime, "runtime path")
    if not runtime.is_dir():
        operations.append({
            "action": "create", "surface": "runtime",
            "path": runtime.relative_to(root).as_posix(),
            "ownership": "disposable_local",
        })
    blockers.extend(
        "project-local state blocks refresh: " + finding
        for finding in setup_check.runtime_findings(root)
        if not finding.startswith("missing project-local runtime:")
    )

    seen_payload: set[Path] = set()
    materializations: dict[Path, tuple[bytes, int]] = {}
    for source, target in payload_sources(policy, payload_root, vault_root):
        assert_not_symlinked(root, target, "vault payload target")
        if not target.exists():
            seen_payload.add(target)
            content = source.read_bytes()
            materializations[target] = (
                content, source.stat().st_mode & 0o777,
            )
            operation = {
                "action": "create", "surface": "vault_payload",
                "path": target.relative_to(root).as_posix(),
                "ownership": projection_ownership(target, vault_root),
            }
            if target.suffix == ".json":
                try:
                    after_value = json.loads(content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    after_value = None
                if isinstance(after_value, dict):
                    operation["changes"] = value_deltas({}, after_value)
                elif after_value is not None:
                    operation["changes"] = [{
                        "key": "$", "before": None, "after": after_value,
                    }]
            if "changes" not in operation:
                operation["after_hash"] = bytes_hash(content)
            operations.append(operation)
    obsidian = vault_root / ".obsidian"
    for name in JSON_PAYLOAD_FILES:
        target = obsidian / name
        if not target.is_file():
            continue
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            blockers.append(
                f"{target.relative_to(root).as_posix()} is not valid JSON: {exc}"
            )
    planned_updates = vault_check.payload_reconcile_updates(
        vault_root, policy, payload_root
    )
    for target in sorted(planned_updates, key=lambda value: str(value)):
        if target in seen_payload:
            continue
        assert_not_symlinked(root, target, "vault payload target")
        operation = {
            "action": "update", "surface": "vault_payload",
            "path": target.relative_to(root).as_posix(),
            "ownership": projection_ownership(target, vault_root),
        }
        content = planned_updates[target]
        if target.suffix == ".json":
            try:
                before_value = json.loads(target.read_text(encoding="utf-8"))
                after_value = json.loads(content.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                before_value, after_value = None, None
            if before_value is not None and after_value is not None:
                operation["changes"] = value_deltas(
                    before_value, after_value
                )
        if "changes" not in operation:
            operation["before_hash"] = (
                bytes_hash(target.read_bytes()) if target.is_file() else None
            )
            operation["after_hash"] = bytes_hash(content)
        operations.append(operation)
    planned_deletions = vault_check.payload_reconcile_deletions(
        vault_root, policy, payload_root
    )
    for target in planned_deletions:
        if not target.is_symlink():
            assert_not_symlinked(root, target, "vault payload target")
        operation = {
            "action": "delete", "surface": "vault_payload",
            "path": target.relative_to(root).as_posix(),
            "ownership": "package_local",
            "before_kind": (
                "symlink" if target.is_symlink() else
                "directory" if target.is_dir() else
                "file" if target.is_file() else "missing"
            ),
        }
        if target.is_file() and not target.is_symlink():
            operation["before_hash"] = bytes_hash(target.read_bytes())
        operations.append(operation)

    relation_updates = relation_report_updates(vault_root, policy)
    for target, content in sorted(
        relation_updates.items(), key=lambda item: str(item[0])
    ):
        assert_not_symlinked(root, target, "generated relation report")
        operations.append({
            "action": "create" if not target.exists() else "update",
            "surface": "generated_relation_report",
            "path": target.relative_to(root).as_posix(),
            "ownership": "tracked_generated",
            "before_hash": (
                bytes_hash(target.read_bytes()) if target.is_file() else None
            ),
            "after_hash": bytes_hash(content.encode("utf-8")),
        })

    current_ignore, target_ignore = proposed_gitignore(root, workspace)
    if current_ignore != target_ignore:
        operations.append({
            "action": "create" if not (root / ".gitignore").exists() else "update",
            "surface": "gitignore", "path": ".gitignore",
            "ownership": "tracked_managed_block",
            "before_hash": bytes_hash(current_ignore.encode("utf-8")),
            "after_hash": bytes_hash(target_ignore.encode("utf-8")),
        })

    gate_path = root / ".github" / "agentrof" / "vault-gate.pyz"
    assert_not_symlinked(root, gate_path, "portable gate")
    gate_bytes = expected_gate_bytes()
    if not gate_path.is_file() or gate_path.read_bytes() != gate_bytes:
        operations.append({
            "action": "create" if not gate_path.exists() else "update",
            "surface": "portable_gate",
            "path": gate_path.relative_to(root).as_posix(),
            "ownership": "tracked_generated",
            "before_hash": (
                bytes_hash(gate_path.read_bytes())
                if gate_path.is_file() else None
            ),
            "after_hash": bytes_hash(gate_bytes),
        })

    operations.sort(key=lambda item: (item["path"], item["surface"]))
    return {
        "ok": not blockers,
        "command": "inspect",
        "project_root": str(root),
        "workspace": workspace,
        "changes_required": bool(operations),
        "operations": operations,
        "blockers": blockers,
        "_config": config,
        "_policy": policy,
        "_policy_path": policy_path,
        "_payload_root": payload_root,
        "_payload_materializations": materializations,
        "_payload_updates": planned_updates,
        "_payload_deletions": planned_deletions,
        "_gate_bytes": gate_bytes,
        "_relation_reports": relation_updates,
        "_legacy_contract_updates": {**operation_updates, **governance_updates},
        "_legacy_contract_deletions": operation_deletions,
        "_gitignore_content": target_ignore,
    }


def path_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if path.is_file():
        return "file:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        return "missing"
    digest.update(b"directory\0")
    for child in sorted(path.rglob("*"), key=lambda value: str(value)):
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        if child.is_symlink():
            digest.update(b"symlink\0" + os.readlink(child).encode("utf-8"))
        elif child.is_file():
            digest.update(b"file\0" + hashlib.sha256(
                child.read_bytes()
            ).digest())
        elif child.is_dir():
            digest.update(b"directory\0")
    return "directory:" + digest.hexdigest()


def rollback_targets(root: Path, plan: dict) -> list[Path]:
    """Return only paths this exact plan can mutate.

    Container/runtime directory creation is rolled back with empty-directory
    cleanup. Including an unchanged managed file here would let a failed setup
    erase an unrelated concurrent edit, so the transaction roots come only
    exclusively from planned file and payload operations.
    """
    mutable_surfaces = {
        "workspace_config", "vault_payload", "generated_relation_report",
        "legacy_contract_migration",
        "gitignore", "portable_gate",
    }
    targets = {
        root / item["path"] for item in plan["operations"]
        if item["surface"] in mutable_surfaces
    }
    ordered = sorted(targets, key=lambda value: (len(value.parts), str(value)))
    minimal: list[Path] = []
    for target in ordered:
        if any(parent == target or parent in target.parents for parent in minimal):
            continue
        minimal.append(target)
    return minimal


class RefreshSnapshot:
    """Optimistic transaction over the exact targets in one refresh plan.

    A private expected tree advances with every setup write. Before and after
    each mutation, the real target root must match that tree. Rollback restores
    only roots still equal to the setup postimage; concurrent edits observed at
    mutation boundaries are kept and reported instead of being overwritten.
    """

    def __init__(self, root: Path, workspace: str, plan: dict):
        self.root = root
        self.workspace = workspace
        self.temporary = tempfile.TemporaryDirectory(prefix="setup-rollback.")
        self.backup_root = Path(self.temporary.name)
        self.paths = tuple(rollback_targets(root, plan))
        self.existed: dict[Path, str] = {}
        self.expected: dict[Path, Path] = {}
        self.backups: dict[Path, Path] = {}
        self.written: set[Path] = set()
        self.conflicts: set[Path] = set()
        for index, path in enumerate(self.paths):
            backup = self.backup_root / "original" / str(index)
            expected = self.backup_root / "expected" / str(index)
            self.backups[path] = backup
            self.expected[path] = expected
            self.existed[path] = self.copy(path, backup)
            self.copy(path, expected)
        candidates = [root / workspace]
        candidates.extend(root / workspace / relative for relative in REQUIRED_DIRS)
        runtime = runtime_root(root)
        current = runtime
        while current != root:
            candidates.append(current)
            current = current.parent
        candidates.extend((root / ".github", root / ".github" / "agentrof"))
        for path in self.paths:
            parent = path.parent
            while parent != root:
                candidates.append(parent)
                parent = parent.parent
        self.preexisting_dirs = {path for path in candidates if path.is_dir()}
        self.directory_candidates = set(candidates)

    @staticmethod
    def copy(source: Path, target: Path) -> str:
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(os.readlink(source))
            return "symlink"
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
            return "directory"
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            return "file"
        return "missing"

    @staticmethod
    def remove(path: Path, *, recursive: bool = True) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            if recursive:
                shutil.rmtree(path)
            else:
                path.rmdir()

    def owner(self, path: Path) -> Path:
        matches = [candidate for candidate in self.paths
                   if candidate == path or candidate in path.parents]
        if not matches:
            raise SetupError(
                "refresh attempted a path absent from its inspect plan: "
                + path.relative_to(self.root).as_posix()
            )
        return max(matches, key=lambda value: len(value.parts))

    def expected_path(self, path: Path, owner: Path) -> Path:
        relative = path.relative_to(owner)
        return self.expected[owner] / relative if relative.parts \
            else self.expected[owner]

    def assert_current(self, owner: Path) -> None:
        if path_fingerprint(owner) != path_fingerprint(self.expected[owner]):
            self.conflicts.add(owner)
            raise SetupError(
                "concurrent edit changed refresh target: "
                + owner.relative_to(self.root).as_posix()
            )

    def write_bytes(self, path: Path, content: bytes,
                    mode: int = 0o644) -> None:
        owner = self.owner(path)
        self.assert_current(owner)
        expected = self.expected_path(path, owner)
        self.written.add(owner)
        atomic_bytes(
            path, content, mode,
            before_replace=lambda: self.assert_current(owner),
        )
        atomic_bytes(expected, content, mode)
        self.assert_current(owner)

    def write_text(self, path: Path, content: str) -> None:
        owner = self.owner(path)
        self.assert_current(owner)
        expected = self.expected_path(path, owner)
        self.written.add(owner)
        atomic_text(
            path, content,
            before_replace=lambda: self.assert_current(owner),
        )
        atomic_text(expected, content)
        self.assert_current(owner)

    def delete(self, path: Path) -> None:
        owner = self.owner(path)
        self.assert_current(owner)
        expected = self.expected_path(path, owner)
        self.written.add(owner)
        self.remove(path, recursive=False)
        self.remove(expected, recursive=False)
        self.assert_current(owner)

    def restore(self) -> list[str]:
        conflicts = set(self.conflicts)
        for path in self.paths:
            if path not in self.written:
                continue
            if path_fingerprint(path) != path_fingerprint(self.expected[path]):
                conflicts.add(path)
                continue
            self.remove(path)
            kind = self.existed[path]
            backup = self.backups[path]
            if kind == "directory":
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup, path, symlinks=True)
            elif kind == "file":
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, path)
            elif kind == "symlink":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(os.readlink(backup))
        for directory in sorted(
            self.directory_candidates - self.preexisting_dirs,
            key=lambda value: len(value.parts), reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        return sorted(path.relative_to(self.root).as_posix()
                      for path in conflicts)

    def close(self) -> None:
        self.temporary.cleanup()


def next_entry(root: Path) -> str:
    result = subprocess.run([
        sys.executable, str(Path(__file__).with_name("requirement_route.py")),
        "--project-root", str(root), "--json",
    ], capture_output=True, text=True, check=False)
    try:
        routed = json.loads(result.stdout)
    except json.JSONDecodeError:
        routed = {}
    entry = routed.get("next_entry") if isinstance(routed, dict) else None
    if isinstance(entry, str) and entry:
        return entry
    return "requirement"


def _apply_plan_locked(args, plan: dict) -> tuple[int, dict]:
    root = Path(plan["project_root"])
    workspace_root = root / args.workspace
    snapshot = RefreshSnapshot(root, args.workspace, plan)
    copied = 0
    reconciled = 0
    ignore_changed = False
    rollback_conflicts: list[str] = []
    try:
        config_path = workspace_root / "config.json"
        desired = json.dumps(plan["_config"], ensure_ascii=False, indent=2) + "\n"
        if not config_path.is_file() \
                or config_path.read_text(encoding="utf-8") != desired:
            snapshot.write_text(config_path, desired)
        for relative in REQUIRED_DIRS:
            target = workspace_root / relative
            assert_not_symlinked(root, target, "managed target")
            target.mkdir(parents=True, exist_ok=True)
        runtime = create_runtime(root)
        # Execute the exact inspected payload plan one path at a time. Each
        # mutation is mirrored into the private expected tree, so rollback can
        # distinguish setup's postimage from a concurrent user edit.
        for target in plan["_payload_deletions"]:
            snapshot.delete(target)
        for target, (content, mode) in sorted(
            plan["_payload_materializations"].items(),
            key=lambda item: str(item[0]),
        ):
            snapshot.write_bytes(target, content, mode)
        for target, content in sorted(
            plan["_payload_updates"].items(), key=lambda item: str(item[0])
        ):
            snapshot.write_bytes(target, content)
        copied = len(plan["_payload_materializations"])
        reconciled = (
            len(plan["_payload_deletions"])
            + len(plan["_payload_updates"])
        )
        for target, content in plan["_relation_reports"].items():
            snapshot.write_text(target, content)
        for target, content in sorted(
            plan["_legacy_contract_updates"].items(), key=lambda item: str(item[0])
        ):
            snapshot.write_text(target, content)
        for target in plan["_legacy_contract_deletions"]:
            snapshot.delete(target)
        current_ignore, _target_ignore = proposed_gitignore(
            root, args.workspace
        )
        if current_ignore != plan["_gitignore_content"]:
            snapshot.write_text(
                root / ".gitignore", plan["_gitignore_content"]
            )
            ignore_changed = True
        if any(item["surface"] == "portable_gate"
               for item in plan["operations"]):
            snapshot.write_bytes(
                root / ".github" / "agentrof" / "vault-gate.pyz",
                plan["_gate_bytes"], 0o755,
            )

        findings = setup_check.closing(root, args.workspace)
        converged = build_plan(args)
        if converged["blockers"]:
            findings.extend(
                "refresh convergence: " + value
                for value in converged["blockers"]
            )
        if converged["operations"]:
            findings.append(
                "refresh did not converge; remaining operations: "
                + ", ".join(item["path"] for item in converged["operations"])
            )
        if findings:
            rollback_conflicts = snapshot.restore()
            snapshot.close()
            return 1, {
                "ok": False,
                "command": "apply",
                "project_root": str(root),
                "workspace": args.workspace,
                "plan": public_plan(plan),
                "findings": findings,
                "rolled_back": True,
                "rollback_conflicts": rollback_conflicts,
            }
        snapshot.close()
        return 0, {
            "ok": True,
            "command": "apply",
            "project_root": str(root),
            "workspace": args.workspace,
            "plan": public_plan(plan),
            "applied_operations": plan["operations"],
            "payload_files_copied": copied,
            "payload_files_reconciled": reconciled,
            "gitignore_changed": ignore_changed,
            "runtime_root": str(runtime),
            "next_entry": next_entry(root),
            "rolled_back": False,
        }
    except Exception as exc:
        try:
            rollback_conflicts = snapshot.restore()
        finally:
            snapshot.close()
        return 1, {
            "ok": False,
            "command": "apply",
            "project_root": str(root),
            "workspace": args.workspace,
            "plan": public_plan(plan),
            "error": str(exc),
            "rolled_back": True,
            "rollback_conflicts": rollback_conflicts,
        }


def apply_plan(args, _inspected_plan: dict | None = None) -> tuple[int, dict]:
    """Replan and apply while one disposable OS guard serializes refreshes."""
    root = git_root(Path(args.project_root).resolve())
    with refresh_guard(root):
        plan = build_plan(args)
        if plan["blockers"]:
            return 1, {
                "ok": False,
                "command": "apply",
                "project_root": str(root),
                "workspace": args.workspace,
                "plan": public_plan(plan),
                "findings": [
                    "refresh plan blocker: " + value
                    for value in plan["blockers"]
                ],
                "rolled_back": False,
                "rollback_conflicts": [],
            }
        return _apply_plan_locked(args, plan)


def emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if result.get("ok"):
        command = result.get("command")
        if command == "inspect":
            print(
                f"setup-project: {len(result['operations'])} managed change(s)"
                " planned"
            )
            for item in result["operations"]:
                print(
                    f"{item['action'].upper():9} {item['path']}"
                    f" [{item['ownership']}]"
                )
        elif command == "check":
            print("setup-project: project refresh contract is current")
        else:
            print(f"setup-project: ready ({result['next_entry']})")
        return
    for value in result.get("blockers", []) + result.get("findings", []):
        print(f"ERROR {result.get('project_root', '.')}:1 [setup_contract] {value}")
    if result.get("error"):
        print(f"setup-project: {result['error']}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("inspect", "check", "apply"),
        default="apply",
    )
    parser.add_argument("--project-root", required=True)
    parser.set_defaults(workspace=WORKSPACE)
    parser.add_argument("--output-language", default="English")
    parser.add_argument("--terminology-language", default="English")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            plan = build_plan(args)
            root = Path(plan["project_root"])
            findings = setup_check.closing(root, args.workspace)
            findings.extend(
                "refresh plan blocker: " + value
                for value in plan["blockers"]
            )
            findings.extend(
                "managed refresh drift: " + item["path"]
                for item in plan["operations"]
            )
            result = {
                "ok": not findings, "command": "check",
                "project_root": str(root), "workspace": args.workspace,
                "findings": list(dict.fromkeys(findings)),
                "plan": public_plan(plan),
            }
            emit(result, args.json)
            return 1 if findings else 0
        if args.command == "apply":
            code, result = apply_plan(args)
            emit(result, args.json)
            return code
        plan = build_plan(args)
        if args.command == "inspect":
            result = public_plan(plan)
            emit(result, args.json)
            return 1 if plan["blockers"] else 0
        raise SetupError(f"unsupported setup command: {args.command}")
    except (SetupError, OSError, ValueError) as exc:
        result = {
            "ok": False, "command": args.command,
            "project_root": str(Path(args.project_root).resolve()),
            "workspace": args.workspace, "error": str(exc),
        }
        emit(result, args.json)
        if args.json:
            print(f"setup-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
