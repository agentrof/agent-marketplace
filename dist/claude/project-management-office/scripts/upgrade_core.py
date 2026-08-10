#!/usr/bin/env python3
"""Host-neutral Agent Marketplace upgrade engine.

The engine derives compatibility from installed package manifests, the PMO
database, and the consuming repository's tracked project contract.  A status
cache is never authoritative: every plan and apply recomputes the complete
fingerprint.  Project-specific rendering is delegated to package adapters
that implement one small JSON protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_STATE_SCHEMA = 1
PROJECT_CONTRACT_VERSION = 4
REGISTRY_SCHEMA = 2
WRITER_EPOCH = 1
STATE_RELATIVE = Path(".agentrof") / "agent-marketplace" / "project.json"
RUNTIME_RELATIVE = (Path(".agentrof") / "agent-marketplace" / ".runtime")
MAINTENANCE_NAME = "maintenance.json"
UPGRADES_DIR = "upgrades"
LOCKS_DIR = "locks"
PRIOR_OWNER_SUFFIX = " plugin; change only through the configure entry"
GITIGNORE_START = "# agent-marketplace:software-engineering-team:gitignore:start"
GITIGNORE_END = "# agent-marketplace:software-engineering-team:gitignore:end"
ACTIVE_ORDER_STATUSES = ("running", "waiting_gate")
STATUS_CURRENT = "AGENT_MARKETPLACE_CURRENT"
STATUS_READY = "AGENT_MARKETPLACE_UPGRADE_REQUIRED_READY"
STATUS_BLOCKED = "AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED"
STATUS_APPLY_READY = "AGENT_MARKETPLACE_UPGRADE_APPLY_READY"
STATUS_UPGRADING = "AGENT_MARKETPLACE_UPGRADING"
STATUS_RECOVERY = "AGENT_MARKETPLACE_UPGRADE_RECOVERY_REQUIRED"
STATUS_RESTART = "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED"
STATUS_PROJECT_PR = "AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING"
UPGRADE_BRANCH_RE = re.compile(
    r"^agent-marketplace/upgrade-[a-z0-9][a-z0-9._-]*$"
)
IN_USE_MARKER_RE = re.compile(
    r"^(?P<pid>[1-9][0-9]*)(?:\.tmp\.[0-9a-f]{8})?$"
)
KNOWN_RUNTIME_CONTRACTS = {"in_use_pid_marker_v1"}


class UpgradeError(Exception):
    """A fail-closed upgrade rule violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def atomic_bytes(path: Path, value: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        try:
            os.fchmod(fd, mode)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_json(path: Path, value: dict, mode: int = 0o600) -> None:
    atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(), mode)


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise UpgradeError(f"unreadable JSON file: {path}: {exc}") from exc


def guidance_for(status_code: str) -> dict:
    catalog = load_json(Path(__file__).with_name("upgrade_guidance.json"), {})
    statuses = catalog.get("statuses", []) if isinstance(catalog, dict) else []
    guidance = next(
        (value for value in statuses
         if isinstance(value, dict) and value.get("code") == status_code),
        {},
    ) if isinstance(statuses, list) else {}
    return {
        "display_name": str(catalog.get("display_name", "Agent Marketplace Upgrade"))
        if isinstance(catalog, dict) else "Agent Marketplace Upgrade",
        "summary": str(guidance.get("summary", ""))
        if isinstance(guidance, dict) else "",
        "actions": guidance.get("actions", []) if isinstance(guidance, dict) else [],
    }


def safe_relative(root: Path, path: Path) -> str:
    root_resolved = root.resolve()
    try:
        resolved = path.resolve(strict=False)
        relative = resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise UpgradeError(f"upgrade target escapes the project root: {path}") from exc
    current = root_resolved
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise UpgradeError(f"upgrade target crosses a symbolic link: {current}")
    return relative.as_posix()


def project_root(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if not (candidate / ".git").exists():
        raise UpgradeError(f"project root is not a git checkout: {candidate}")
    return candidate


def run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise UpgradeError(
            f"git {' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def run_git_optional(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=30,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""


def git_preflight(root: Path) -> list[str]:
    blockers: list[str] = []
    try:
        if run_git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
            blockers.append("DIRTY_WORKTREE")
        if run_git_optional(root, "symbolic-ref", "-q", "HEAD").strip() == "":
            blockers.append("DETACHED_HEAD")
        sparse = run_git_optional(
            root, "config", "--bool", "core.sparseCheckout"
        ).strip()
        if sparse == "true":
            blockers.append("SPARSE_CHECKOUT")
        if run_git(root, "ls-files", "--stage").find(" 160000 ") >= 0:
            blockers.append("SUBMODULE_PRESENT")
    except UpgradeError as exc:
        blockers.append(f"GIT_PREFLIGHT_FAILED:{exc}")
    return blockers


def normalized_path_collisions(root: Path) -> list[str]:
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        key = unicodedata.normalize("NFC", relative).casefold()
        previous = seen.get(key)
        if previous is not None and previous != relative:
            collisions.append(f"{previous} <> {relative}")
        else:
            seen[key] = relative
    return collisions


def database_version(db_file: Path) -> int:
    if not db_file.is_file() or db_file.stat().st_size == 0:
        return 0
    con = sqlite3.connect(db_file)
    try:
        return int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()


def database_health(db_file: Path) -> list[str]:
    if not db_file.is_file() or database_version(db_file) == 0:
        return []
    con = sqlite3.connect(db_file)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return [f"DATABASE_INTEGRITY_FAILED:{integrity}"]
        foreign = con.execute("PRAGMA foreign_key_check").fetchone()
        if foreign is not None:
            return [f"DATABASE_FOREIGN_KEY_FAILED:{tuple(foreign)}"]
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "meta" in tables:
            stamp = con.execute(
                "SELECT value FROM meta WHERE key='fingerprint'"
            ).fetchone()
            if stamp is not None and stamp[0] != database_content_fingerprint(con):
                return ["DATABASE_INTEGRITY_STAMP_MISMATCH"]
        return []
    except sqlite3.DatabaseError as exc:
        return [f"DATABASE_HEALTH_CHECK_FAILED:{exc}"]
    finally:
        con.close()


def database_file_fingerprint(db_file: Path) -> str:
    if not db_file.is_file() or database_version(db_file) == 0:
        return ""
    con = sqlite3.connect(db_file)
    try:
        return database_content_fingerprint(con)
    except sqlite3.DatabaseError as exc:
        raise UpgradeError(f"database fingerprint failed: {exc}") from exc
    finally:
        con.close()


def active_database_work(db_file: Path, project_key: str = "") -> list[str]:
    if not db_file.is_file() or database_version(db_file) == 0:
        return []
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"projects", "work_orders", "task_attempts"} <= tables:
            return []
        args: tuple = ()
        project_filter = ""
        if project_key:
            project_filter = " AND p.project_key = ?"
            args = (project_key,)
        orders = con.execute(
            "SELECT w.work_order_key FROM work_orders w"
            " JOIN projects p ON p.id = w.project_id"
            " WHERE w.status IN ('running','waiting_gate')" + project_filter,
            args,
        ).fetchall()
        attempts = con.execute(
            "SELECT COUNT(*) FROM task_attempts a"
            " JOIN work_items i ON i.id = a.task_id"
            " JOIN projects p ON p.id = i.project_id"
            " WHERE a.outcome = 'running'" + project_filter,
            args,
        ).fetchone()[0]
        result = [f"ACTIVE_WORK_ORDER:{row['work_order_key']}" for row in orders]
        if attempts:
            result.append(f"RUNNING_TASK_ATTEMPTS:{attempts}")
        if "experience_runs" in tables:
            count = con.execute(
                "SELECT COUNT(*) FROM experience_runs r JOIN projects p"
                " ON p.id = r.project_id WHERE r.status = 'active'" + project_filter,
                args,
            ).fetchone()[0]
            if count:
                result.append(f"ACTIVE_EXPERIENCE_RUNS:{count}")
        if "backlog_plans" in tables:
            count = con.execute(
                "SELECT COUNT(*) FROM backlog_plans b JOIN projects p"
                " ON p.id = b.project_id WHERE b.status IN ('draft','verified')"
                + project_filter,
                args,
            ).fetchone()[0]
            if count:
                result.append(f"ACTIVE_BACKLOG_PLANS:{count}")
        return result
    except sqlite3.DatabaseError as exc:
        return [f"DATABASE_ACTIVITY_CHECK_FAILED:{exc}"]
    finally:
        con.close()


def active_freezes(db_file: Path, work_orders: Path) -> list[str]:
    if not work_orders.is_dir() or not db_file.is_file():
        return []
    manifests = sorted(work_orders.glob("*/freeze.json"))
    if not manifests:
        return []
    con = sqlite3.connect(db_file)
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "work_orders" not in tables:
            return []
        active = {
            row[0] for row in con.execute(
                "SELECT work_order_key FROM work_orders"
                " WHERE status IN ('running','waiting_gate')"
            )
        }
        return [
            f"ACTIVE_FREEZE_MANIFEST:{path.parent.name}"
            for path in manifests if path.parent.name in active
        ]
    finally:
        con.close()


def project_runtime_paths(root: Path, workspace: str) -> dict[str, Path]:
    runtime = root / RUNTIME_RELATIVE
    work = root / workspace
    return {
        "legacy_plan": work / "planning",
        "legacy_work_orders": work / "work-orders",
        "legacy_design": work / "design-system-work",
        "legacy_experience": work / "experience-design-work",
        "plan": runtime / "plan",
        "work_orders": runtime / "work-orders",
    }


def runtime_migration_blockers(root: Path, workspace: str) -> list[str]:
    paths = project_runtime_paths(root, workspace)
    blockers: list[str] = []
    for name, path in paths.items():
        if path.is_symlink():
            blockers.append(
                "SYMLINKED_RUNTIME_TARGET:"
                + path.relative_to(root).as_posix()
            )
        elif path.exists() and not path.is_dir():
            blockers.append(
                "RUNTIME_TARGET_NOT_DIRECTORY:" + safe_relative(root, path)
            )
    for source_name, target_name in (
        ("legacy_plan", "plan"),
        ("legacy_work_orders", "work_orders"),
    ):
        source, target = paths[source_name], paths[target_name]
        if source.exists() and target.exists() \
                and not source.is_symlink() and not target.is_symlink():
            blockers.append(
                "RUNTIME_MIGRATION_COLLISION:"
                + safe_relative(root, source) + "->" + safe_relative(root, target)
            )
    for name in ("legacy_design", "legacy_experience"):
        path = paths[name]
        if path.is_dir() and not path.is_symlink() and any(path.iterdir()):
            blockers.append(
                "LEGACY_TRANSIENT_CONTENT:" + safe_relative(root, path)
            )
    return blockers


def directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "absent"
    digest.update(b"directory\0")
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path).as_posix()
        digest.update(relative.encode())
        if candidate.is_symlink():
            digest.update(b"\0symlink\0")
            digest.update(os.readlink(candidate).encode())
        elif candidate.is_file():
            digest.update(b"\0file\0")
            digest.update(candidate.read_bytes())
        elif candidate.is_dir():
            digest.update(b"\0directory\0")
    return digest.hexdigest()


def runtime_migration_fingerprint(root: Path, workspace: str) -> str:
    digest = hashlib.sha256()
    for name, path in sorted(project_runtime_paths(root, workspace).items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(directory_digest(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def live_session_records(data_root: Path) -> list[dict]:
    result: list[dict] = []
    sessions = data_root / "sessions"
    if not sessions.is_dir():
        return result
    for path in sorted(sessions.glob("*.json")):
        value = load_json(path, {})
        if isinstance(value, dict) and value.get("pmo_ready") is True:
            session_id = str(value.get("session_id", ""))
            if session_id:
                result.append({
                    "session_id": session_id,
                    "recorded_at": str(value.get("recorded_at", "")),
                })
    return result


def release_session(data_root: Path, session_id: str, confirm_closed: bool) -> dict:
    if not confirm_closed:
        raise UpgradeError(
            "session release requires --confirm-closed after the owner verifies"
            " that the named host session is no longer running"
        )
    if not session_id:
        raise UpgradeError("session release requires a session id")
    path = data_root / "sessions" / f"{sha256_bytes(session_id.encode())}.json"
    value = load_json(path, None)
    if not isinstance(value, dict) or value.get("session_id") != session_id:
        raise UpgradeError(f"unknown marketplace session: {session_id}")
    if value.get("pmo_ready") is not True:
        raise UpgradeError(
            f"session is not a blocking ready session: {session_id}"
        )
    path.unlink()
    return {
        "status": "AGENT_MARKETPLACE_SESSION_RELEASED",
        "session_id": session_id,
        "mutation_performed": True,
    }


def disk_space_blockers(data_root: Path, db_file: Path, root: Path | None) -> list[str]:
    database_bytes = db_file.stat().st_size if db_file.is_file() else 0
    required = max(16 * 1024 * 1024, database_bytes * 4)
    targets = [data_root if data_root.exists() else data_root.parent]
    if root is not None:
        targets.append(root)
    blockers = []
    for target in targets:
        try:
            free = shutil.disk_usage(target).free
        except OSError as exc:
            blockers.append(f"DISK_SPACE_CHECK_FAILED:{target}:{exc}")
            continue
        if free < required:
            blockers.append(f"INSUFFICIENT_DISK_SPACE:{target}:{required}:{free}")
    return blockers


def detect_package_host(root: Path) -> str:
    candidates: list[str] = []
    for manifest in sorted(root.glob(".*-plugin/plugin.json")):
        name = manifest.parent.name
        if name.startswith(".") and name.endswith("-plugin"):
            candidates.append(name[1:-7])
    if len(candidates) != 1:
        raise UpgradeError(f"package root has no unambiguous host manifest: {root}")
    return candidates[0]


def package_manifest(root: Path) -> tuple[Path, dict]:
    host = detect_package_host(root)
    path = root / f".{host}-plugin" / "plugin.json"
    data = load_json(path, {})
    if not isinstance(data, dict) or not data.get("name") or not data.get("version"):
        raise UpgradeError(f"invalid package manifest: {path}")
    return path, data


def is_host_runtime_marker(
    candidate: Path, relative: Path, runtime_contracts: set[str],
) -> bool:
    if "in_use_pid_marker_v1" not in runtime_contracts \
            or len(relative.parts) != 2 \
            or relative.parts[0] != ".in_use" or candidate.is_symlink():
        return False
    matched = IN_USE_MARKER_RE.fullmatch(relative.name)
    if matched is None:
        return False
    try:
        raw = candidate.read_bytes()
    except OSError:
        return False
    if not raw:
        return True
    if len(raw) > 512:
        return False
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(marker, dict)
        and set(marker) == {"pid", "procStart"}
        and marker.get("pid") == int(matched.group("pid"))
        and isinstance(marker.get("procStart"), str)
        and bool(marker["procStart"].strip())
    )


def verify_package_provenance(root: Path, host: str, manifest: dict) -> str:
    path = root / ".agent-marketplace-package.json"
    data = load_json(path, None)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise UpgradeError(f"package provenance missing or invalid: {root}")
    if data.get("component") != manifest.get("name") \
            or data.get("host") != host \
            or data.get("version") != manifest.get("version"):
        raise UpgradeError(f"package provenance identity mismatch: {root}")
    files = data.get("files")
    if not isinstance(files, dict):
        raise UpgradeError(f"package provenance file map invalid: {root}")
    declared_contracts = data.get("runtime_contracts", [])
    if not isinstance(declared_contracts, list) \
            or any(not isinstance(value, str) for value in declared_contracts) \
            or len(declared_contracts) != len(set(declared_contracts)) \
            or not set(declared_contracts) <= KNOWN_RUNTIME_CONTRACTS:
        raise UpgradeError(f"package provenance runtime contract invalid: {root}")
    runtime_contracts = set(declared_contracts)
    actual: dict[str, str] = {}
    for candidate in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = candidate.relative_to(root)
        if candidate == path or "__pycache__" in relative.parts \
                or candidate.suffix in {".pyc", ".pyo"} \
                or is_host_runtime_marker(
                    candidate, relative, runtime_contracts
                ):
            continue
        actual[relative.as_posix()] = sha256_file(candidate)
    if actual != files:
        raise UpgradeError(f"package provenance verification failed: {root}")
    return sha256_file(path)


def normalize_registry(data_root: Path) -> dict:
    path = data_root / "plugin_roots.json"
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        raise UpgradeError(f"plugin root registry must be an object: {path}")
    plugins = raw.get("plugins", {})
    if not isinstance(plugins, dict):
        raise UpgradeError(f"plugin root registry plugins must be an object: {path}")
    normalized = {"schema_version": REGISTRY_SCHEMA, "plugins": {}}
    for plugin, entry in sorted(plugins.items()):
        if not isinstance(entry, dict):
            raise UpgradeError(f"invalid registry entry for {plugin}")
        hosts = entry.get("hosts")
        if isinstance(hosts, dict):
            normalized_hosts = hosts
        else:
            root = Path(str(entry.get("root", "")))
            if not root.is_dir():
                raise UpgradeError(f"stale package root for {plugin}: {root}")
            host = detect_package_host(root)
            normalized_hosts = {host: entry}
        clean_hosts: dict[str, dict] = {}
        for host, host_entry in sorted(normalized_hosts.items()):
            if not isinstance(host_entry, dict):
                raise UpgradeError(f"invalid host registry entry for {plugin}/{host}")
            root = Path(str(host_entry.get("root", ""))).resolve()
            manifest_path, manifest = package_manifest(root)
            if manifest.get("name") != plugin:
                raise UpgradeError(f"registry manifest mismatch for {plugin}/{host}")
            if detect_package_host(root) != str(host):
                raise UpgradeError(f"registry host mismatch for {plugin}/{host}")
            clean_hosts[str(host)] = {
                "root": str(root),
                "version": str(manifest["version"]),
                "manifest_sha256": sha256_file(manifest_path),
                "provenance_sha256": verify_package_provenance(
                    root, str(host), manifest
                ),
                "registered_at": str(host_entry.get("registered_at", "")),
            }
        normalized["plugins"][plugin] = {"hosts": clean_hosts}
    return normalized


def inventory(data_root: Path) -> tuple[dict, list[str]]:
    registry = normalize_registry(data_root)
    blockers: list[str] = []
    components: dict[str, dict] = {}
    for plugin, entry in sorted(registry["plugins"].items()):
        hosts = entry["hosts"]
        versions = {host: value["version"] for host, value in hosts.items()}
        if len(set(versions.values())) > 1:
            blockers.append(f"DUAL_HOST_VERSION_MISMATCH:{plugin}")
        components[plugin] = {
            "version": next(iter(versions.values()), ""),
            "hosts": sorted(hosts),
            "manifests": {
                host: value["manifest_sha256"] for host, value in sorted(hosts.items())
            },
            "provenance": {
                host: value["provenance_sha256"] for host, value in sorted(hosts.items())
            },
        }
    if "project-management-office" not in components:
        blockers.append("REQUIRED_COMPONENT_MISSING:project-management-office")
    return {"registry": registry, "components": components}, blockers


def find_workspace(root: Path) -> tuple[str, Path | None]:
    state = load_json(root / STATE_RELATIVE, {})
    if isinstance(state, dict) and isinstance(state.get("workspace"), str):
        workspace = state["workspace"]
        candidate = root / workspace / "config.json"
        if candidate.is_file():
            safe_relative(root, candidate)
            return workspace, candidate
    candidates = sorted(
        path for path in root.glob("*/config.json")
        if path.parent.name not in {".git", ".agentrof"}
    )
    recognized: list[Path] = []
    for path in candidates:
        data = load_json(path, {})
        if isinstance(data, dict) and (
            data.get("team_id") or data.get("managed_by") or data.get("project_key")
        ):
            recognized.append(path)
    if len(recognized) > 1:
        raise UpgradeError("multiple managed workspace config candidates")
    if recognized:
        safe_relative(root, recognized[0])
        return recognized[0].parent.name, recognized[0]
    return "workspace", None


def team_from_config(config: dict) -> str:
    team = str(config.get("team_id", "")).strip()
    if team:
        return team
    prior_owner = str(config.get("managed_by", "")).strip()
    if prior_owner.endswith(PRIOR_OWNER_SUFFIX):
        return prior_owner[:-len(PRIOR_OWNER_SUFFIX)]
    if prior_owner and " " not in prior_owner:
        return prior_owner
    return ""


def config_owned_digest(config: dict) -> str:
    owned = {
        key: config[key] for key in (
            "team_id", "managed_by", "project_key", "project_origin",
        ) if key in config
    }
    return sha256_bytes(canonical_json(owned).encode())


def managed_gitignore_block(workspace: str) -> str:
    return "\n".join((
        GITIGNORE_START,
        ".agentrof/agent-marketplace/.runtime/",
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
        GITIGNORE_END,
    ))


def vault_contract_state(root: Path, workspace: str,
                         package_roots: list[Path]) -> dict:
    vault = root / workspace / "docs"
    base = {"root": f"{workspace}/docs", "policy_version": 5,
            "status": "active", "adoption_plan_hash": ""}
    if not vault.is_dir() or not any(vault.rglob("*.md")):
        return base
    checker = next((path / "scripts" / "vault_check.py"
                    for path in package_roots
                    if (path / "scripts" / "vault_check.py").is_file()), None)
    if checker is None:
        return {**base, "status": "pending",
                "reason": "vault checker is unavailable"}
    completed = subprocess.run(
        [sys.executable, str(checker), "adoption-plan", "--vault", str(vault)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    try:
        plan = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {**base, "status": "pending",
                "reason": "vault adoption plan is unreadable"}
    return {
        **base,
        "status": "active" if completed.returncode == 0 else "pending",
        "adoption_plan_hash": str(plan.get("plan_hash", "")),
        "finding_count": len(plan.get("findings", []))
        if isinstance(plan.get("findings"), list) else 0,
    }


def reconcile_gitignore(content: str, workspace: str) -> str:
    starts, ends = content.count(GITIGNORE_START), content.count(GITIGNORE_END)
    if starts != ends or starts > 1:
        raise UpgradeError("managed .gitignore marker is missing or duplicated")
    legacy_rules = {
        f"{workspace}/planning/",
        f"{workspace}/work-orders/",
        f"{workspace}/design-system-work/",
        f"{workspace}/experience-design-work/",
    }
    content = "\n".join(
        line for line in content.splitlines() if line.strip() not in legacy_rules
    )
    block = managed_gitignore_block(workspace)
    if starts == 1:
        begin = content.index(GITIGNORE_START)
        finish = content.index(GITIGNORE_END, begin) + len(GITIGNORE_END)
        result = content[:begin] + block + content[finish:]
    else:
        result = content.rstrip() + ("\n\n" if content.strip() else "") + block + "\n"
    return result if result.endswith("\n") else result + "\n"


def gitignore_owned_digest(content: str) -> str:
    if content.count(GITIGNORE_START) != 1 or content.count(GITIGNORE_END) != 1:
        return ""
    begin = content.index(GITIGNORE_START)
    finish = content.index(GITIGNORE_END, begin) + len(GITIGNORE_END)
    return sha256_bytes(content[begin:finish].encode())


def managed_surface_hashes(root: Path, config_path: Path | None) -> dict[str, str]:
    surfaces: dict[str, str] = {}
    if config_path is not None and config_path.is_file():
        config = load_json(config_path, {})
        if isinstance(config, dict):
            surfaces[safe_relative(root, config_path) + "#agent-marketplace"] = (
                config_owned_digest(config)
            )
    ignore = root / ".gitignore"
    if ignore.is_file():
        digest = gitignore_owned_digest(ignore.read_text(encoding="utf-8"))
        if digest:
            surfaces[".gitignore#agent-marketplace:software-engineering-team"] = digest
    return surfaces


def adapter_surfaces(
    data_root: Path,
    team_id: str,
    root: Path,
    workspace: str,
    action: str = "inspect",
) -> tuple[dict[str, str], list[str]]:
    """Collect host-owned project surfaces through the package protocol."""
    if not team_id:
        return {}, []
    registry = normalize_registry(data_root)
    team = registry["plugins"].get(team_id, {})
    hosts = team.get("hosts", {}) if isinstance(team, dict) else {}
    surfaces: dict[str, str] = {}
    changes: set[str] = set()
    for host, entry in sorted(hosts.items()):
        result = run_adapter(Path(entry["root"]), action, root, workspace)
        current = result.get("current_surfaces", {})
        if not isinstance(current, dict):
            raise UpgradeError(f"project adapter surfaces are invalid for {team_id}/{host}")
        for key, value in sorted(current.items()):
            namespaced = f"{host}:{key}"
            surfaces[namespaced] = str(value)
        changes.update(str(value) for value in result["changes"])
    return surfaces, sorted(changes)


def relevant_components(installed: dict, team_id: str) -> dict:
    selected: dict = {}
    for plugin in ("project-management-office", team_id):
        if plugin and plugin in installed:
            selected[plugin] = installed[plugin]
    return selected


def project_fingerprint(
    root: Path,
    config_path: Path | None,
    surfaces: dict[str, str] | None = None,
) -> dict:
    state_path = root / STATE_RELATIVE
    state_hash = sha256_file(state_path) if state_path.is_file() else ""
    workspace = config_path.parent.name if config_path is not None else "workspace"
    return {
        "state_sha256": state_hash,
        "managed_surfaces": surfaces or managed_surface_hashes(root, config_path),
        "head": run_git(root, "rev-parse", "HEAD").strip(),
        "branch": run_git(root, "symbolic-ref", "--short", "-q", "HEAD").strip(),
        "worktree_sha256": sha256_bytes(
            run_git(root, "status", "--porcelain=v1", "--untracked-files=all").encode()
        ),
        "runtime_sha256": runtime_migration_fingerprint(root, workspace),
    }


def repository_fingerprint(root: Path) -> str:
    remote = run_git_optional(root, "config", "--get", "remote.origin.url").strip()
    identity = remote or run_git(root, "rev-parse", "--git-common-dir").strip()
    return sha256_bytes(identity.encode())


def repository_delivery(root: Path) -> dict:
    branch = run_git(root, "symbolic-ref", "--short", "-q", "HEAD").strip()
    remote = run_git_optional(root, "config", "--get", "remote.origin.url").strip()
    if not remote:
        return {
            "requires_pull_request": False,
            "target_branch": branch,
        }
    remote_head = run_git_optional(
        root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
    ).strip()
    if remote_head.startswith("origin/"):
        target = remote_head.split("/", 1)[1]
    else:
        target = next((
            candidate for candidate in ("main", "master")
            if run_git_optional(
                root, "show-ref", "--verify", f"refs/remotes/origin/{candidate}"
            ).strip()
        ), "")
    if not target:
        raise UpgradeError(
            "origin default branch is unresolved; set refs/remotes/origin/HEAD"
            " before preparing an upgrade"
        )
    return {
        "requires_pull_request": True,
        "target_branch": target,
    }


def target_contains_project_upgrade(root: Path, target: str, upgrade_id: str) -> bool:
    if not target or not upgrade_id:
        return False
    content = run_git_optional(
        root, "show", f"refs/heads/{target}:{STATE_RELATIVE.as_posix()}"
    )
    if not content:
        return False
    try:
        state = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(state, dict) and state.get("upgrade_id") == upgrade_id


def status(
    data_root: Path,
    db_file: Path,
    target_schema: int,
    project_path: str | Path | None = None,
    include_git: bool = True,
    exclude_session_id: str = "",
) -> dict:
    blockers: list[str] = []
    reasons: list[str] = []
    try:
        installed, inventory_blockers = inventory(data_root)
        blockers.extend(inventory_blockers)
    except UpgradeError as exc:
        installed = {"registry": {"schema_version": REGISTRY_SCHEMA, "plugins": {}},
                     "components": {}}
        blockers.append(f"PLUGIN_INVENTORY_INVALID:{exc}")
    db_version = database_version(db_file)
    blockers.extend(database_health(db_file))
    try:
        db_fingerprint = database_file_fingerprint(db_file)
    except UpgradeError as exc:
        db_fingerprint = "unavailable"
        blockers.append(f"DATABASE_FINGERPRINT_FAILED:{exc}")
    if db_version > target_schema:
        blockers.append(f"DOWNGRADE_REFUSED:{db_version}>{target_schema}")
    elif db_version not in (0, target_schema):
        reasons.append(f"DATABASE_SCHEMA:{db_version}->{target_schema}")

    project_info: dict = {}
    root: Path | None = None
    config_path: Path | None = None
    if project_path:
        try:
            root = project_root(project_path)
            workspace, config_path = find_workspace(root)
            state_path = root / STATE_RELATIVE
            safe_relative(root, state_path)
            for managed_target in (state_path, root / ".gitignore",
                                   config_path if config_path is not None else root / workspace / "config.json"):
                if managed_target.is_symlink():
                    blockers.append(
                        "SYMLINKED_MANAGED_TARGET:" + safe_relative(root, managed_target)
                    )
            state = load_json(state_path, None)
            config = load_json(config_path, {}) if config_path is not None else {}
            team_id = team_from_config(config) if isinstance(config, dict) else ""
            project_key = str(config.get("project_key", "")) \
                if isinstance(config, dict) else ""
            current_surfaces = managed_surface_hashes(root, config_path)
            if team_id:
                host_surfaces, _ = adapter_surfaces(
                    data_root, team_id, root, workspace, "inspect"
                )
                current_surfaces.update(host_surfaces)
            footprint = bool(config_path)
            bootstrap_project = bool(
                state is None and footprint and not project_key
                and team_id in installed["components"]
                and team_id != "project-management-office"
            )
            managed_project = (footprint or state is not None) \
                and not bootstrap_project
            if state is None and footprint and not bootstrap_project:
                reasons.append(
                    f"PROJECT_CONTRACT:unversioned->{PROJECT_CONTRACT_VERSION}"
                )
                state = {}
            elif state is not None:
                if not isinstance(state, dict):
                    blockers.append("PROJECT_STATE_INVALID")
                    state = {}
                version = int(state.get("contract_version", 0))
                if version > PROJECT_CONTRACT_VERSION:
                    blockers.append(
                        f"PROJECT_DOWNGRADE_REFUSED:{version}>{PROJECT_CONTRACT_VERSION}"
                    )
                elif version < PROJECT_CONTRACT_VERSION:
                    reasons.append(
                        f"PROJECT_CONTRACT:{version}->{PROJECT_CONTRACT_VERSION}"
                    )
                applied = state.get("components", {})
                scoped = relevant_components(installed["components"], team_id)
                if isinstance(applied, dict):
                    for plugin, value in sorted(scoped.items()):
                        if applied.get(plugin) != value["version"]:
                            reasons.append(
                                f"PLUGIN_COMPONENT:{plugin}:{applied.get(plugin, 'missing')}"
                                f"->{value['version']}"
                            )
                installed_hosts = set(scoped.get(team_id, {}).get("hosts", []))
                state_hosts = {
                    str(value) for value in state.get("hosts", [])
                } if isinstance(state.get("hosts", []), list) else set()
                if installed_hosts != state_hosts:
                    reasons.append(
                        "HOST_SURFACES:"
                        + ",".join(sorted(state_hosts))
                        + "->" + ",".join(sorted(installed_hosts))
                    )
                expected = state.get("managed_surfaces", {})
                actual = current_surfaces
                if isinstance(expected, dict):
                    drift = sorted(
                        key for key, value in expected.items()
                        if not (
                            ":" in key and key.split(":", 1)[0] not in installed_hosts
                        ) and actual.get(key) != value
                    )
                    if drift:
                        blockers.append("PROJECT_CONTRACT_DRIFT:" + ",".join(drift))
            database_upgrade = any(
                value.startswith("DATABASE_SCHEMA:") for value in reasons
            )
            delivery = state.get("delivery", {}) \
                if isinstance(state, dict) else {}
            if not isinstance(delivery, dict):
                delivery = {}
            if managed_project and reasons:
                delivery = repository_delivery(root)
                if not database_upgrade:
                    blockers.extend(active_database_work(db_file, project_key))
                runtime_paths = project_runtime_paths(root, workspace)
                blockers.extend(active_freezes(
                    db_file, runtime_paths["legacy_work_orders"]
                ))
                blockers.extend(active_freezes(
                    db_file, runtime_paths["work_orders"]
                ))
                blockers.extend(runtime_migration_blockers(root, workspace))
                if include_git:
                    blockers.extend(git_preflight(root))
                    branch = run_git(
                        root, "symbolic-ref", "--short", "-q", "HEAD"
                    ).strip()
                    if delivery["requires_pull_request"]:
                        target = str(delivery["target_branch"])
                        if branch == target:
                            blockers.append("UPGRADE_BRANCH_REQUIRED:" + target)
                        elif not UPGRADE_BRANCH_RE.fullmatch(branch):
                            blockers.append("UPGRADE_TARGET_REQUIRED:" + target)
                    collisions = normalized_path_collisions(root)
                    if collisions:
                        blockers.append(
                            "NORMALIZED_PATH_COLLISION:" + ";".join(collisions)
                        )
            if managed_project:
                project_info = {
                    "root": str(root),
                    "workspace": workspace,
                    "project_key": project_key,
                    "team_id": team_id,
                    "repository_fingerprint": repository_fingerprint(root),
                    "delivery": delivery,
                    "state": state or {},
                    "managed_surfaces": current_surfaces,
                    "fingerprint": project_fingerprint(
                        root, config_path, current_surfaces
                    ),
                }
        except UpgradeError as exc:
            blockers.append(f"PROJECT_PREFLIGHT_FAILED:{exc}")

    if any(value.startswith("DATABASE_SCHEMA:") for value in reasons):
        blockers.extend(active_database_work(db_file))
    if reasons:
        blockers.extend(disk_space_blockers(data_root, db_file, root))

    sessions = [
        value for value in live_session_records(data_root)
        if value["session_id"] != exclude_session_id
    ]
    # The dedicated upgrade session is recorded with pmo_ready=false. Every
    # ready session found here is therefore a competing pre-upgrade session.
    if reasons and sessions:
        blockers.append(f"CLOSE_OTHER_SESSIONS_REQUIRED:{len(sessions)}")
    recovery = sorted((data_root / UPGRADES_DIR).glob("*/journal.json")) \
        if (data_root / UPGRADES_DIR).is_dir() else []
    incomplete = []
    for path in recovery:
        journal = load_json(path, {})
        if isinstance(journal, dict) and journal.get("phase") not in {
            "complete", "rolled_back"
        }:
            incomplete.append(path)
    if incomplete:
        blockers.append(f"RECOVERY_REQUIRED:{incomplete[-1].parent.name}")

    maintenance = load_json(data_root / MAINTENANCE_NAME, {})
    fingerprint_payload = {
        "db_schema": db_version,
        "db_content": db_fingerprint,
        "installed": installed["components"],
        "project": project_info.get("fingerprint", {}),
        "maintenance": maintenance,
    }
    fingerprint = sha256_bytes(canonical_json(fingerprint_payload).encode())
    pending_project_pr = False
    if project_info:
        state = project_info.get("state", {})
        pending_project_pr = bool(
            isinstance(state, dict)
            and state.get("upgrade_base_head")
        )
        if pending_project_pr:
            base_head = str(state.get("upgrade_base_head", ""))
            project_revision = project_info.get("fingerprint", {})
            head = str(project_revision.get("head", ""))
            delivery = state.get("delivery", {})
            requires_pr = bool(
                isinstance(delivery, dict)
                and delivery.get("requires_pull_request")
            )
            target = str(delivery.get("target_branch", "")) \
                if isinstance(delivery, dict) else ""
            if requires_pr:
                pending_project_pr = not (
                    root is not None and target_contains_project_upgrade(
                        root, target, str(state.get("upgrade_id", ""))
                    )
                )
            else:
                pending_project_pr = base_head == head
    if incomplete:
        code = STATUS_RECOVERY
    elif isinstance(maintenance, dict) and maintenance.get("run_id"):
        code = STATUS_UPGRADING
    elif blockers:
        code = STATUS_BLOCKED
    elif reasons:
        code = STATUS_READY
    elif pending_project_pr:
        code = STATUS_PROJECT_PR
    else:
        code = STATUS_CURRENT
    return {
        "status": code,
        "fingerprint": fingerprint,
        "database_schema": db_version,
        "database_fingerprint": db_fingerprint,
        "target_database_schema": target_schema,
        "installed": installed["components"],
        "reasons": sorted(set(reasons)),
        "blockers": sorted(set(blockers)),
        "blocking_sessions": sessions,
        "project": project_info,
        "mutation_performed": False,
        "guidance": guidance_for(code),
    }


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


class DirectoryLock:
    """Portable fail-closed lock. Upgrade locks are never stolen by age."""

    def __init__(self, data_root: Path, name: str, *, reclaim_dead: bool = False):
        self.path = data_root / LOCKS_DIR / name
        self.reclaim_dead = reclaim_dead

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_exists = self.path.exists() or self.path.is_symlink()
        if lock_exists and self.reclaim_dead:
            owner = load_json(self.path / "owner.json", None)
            host = str(owner.get("host", "")) if isinstance(owner, dict) else ""
            pid = int(owner.get("pid", 0)) if isinstance(owner, dict) else 0
            if host == socket.gethostname() and pid > 0 and not process_alive(pid):
                (self.path / "owner.json").unlink(missing_ok=True)
                try:
                    self.path.rmdir()
                except OSError as exc:
                    raise UpgradeError(
                        f"dead upgrade lock could not be reclaimed: {self.path}"
                    ) from exc
        if self.path.exists() or self.path.is_symlink():
            raise UpgradeError(f"upgrade lock is already held: {self.path}")
        candidate = self.path.parent / f".{self.path.name}.{uuid.uuid4().hex}"
        candidate.mkdir()
        try:
            atomic_json(candidate / "owner.json", {
                "pid": os.getpid(), "host": socket.gethostname(),
                "started_at": utc_now(),
            })
        except Exception:
            try:
                candidate.rmdir()
            except OSError:
                pass
            raise
        try:
            candidate.rename(self.path)
        except OSError as exc:
            (candidate / "owner.json").unlink(missing_ok=True)
            candidate.rmdir()
            raise UpgradeError(f"upgrade lock is already held: {self.path}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            (self.path / "owner.json").unlink(missing_ok=True)
            self.path.rmdir()
        except OSError:
            pass
        return False


def plan(
    data_root: Path,
    db_file: Path,
    target_schema: int,
    project_path: str | Path | None,
) -> dict:
    current = status(data_root, db_file, target_schema, project_path)
    if current["status"] == STATUS_CURRENT:
        return {**current, "plan_id": "", "changes": []}
    if current["status"] != STATUS_READY:
        raise UpgradeError("upgrade preflight is blocked: " + ", ".join(current["blockers"]))
    plan_payload = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_fingerprint": current["fingerprint"],
        "database": {
            "from": current["database_schema"],
            "to": target_schema,
        },
        "project": current["project"],
        "installed": current["installed"],
        "changes": current["reasons"],
        "project_id": str(
            current["project"].get("state", {}).get("project_id") or uuid.uuid4()
        ) if current["project"] else "",
    }
    plan_payload["database"]["fingerprint"] = current["database_fingerprint"]
    plan_payload["upgrade_id"] = sha256_bytes(
        canonical_json(plan_payload).encode()
    )[:24]
    preview_payload = dict(plan_payload)
    preview_payload["data_root"] = str(data_root)
    preview, _prepared = project_changes(preview_payload)
    preview_root = Path(current["project"]["root"]) if current["project"] else None
    plan_payload["project_files"] = sorted(
        safe_relative(preview_root, path) for path in preview
    ) if preview_root is not None else []
    plan_payload["backup_policy"] = {
        "database": (
            "online backup and candidate verification under a database writer"
            " lock before transactional migration commit"
        ),
        "project": "durable before-image for every managed file",
        "retention": "recovery evidence is retained until explicit maintenance",
    }
    plan_id = sha256_bytes(canonical_json(plan_payload).encode())[:24]
    plan_payload["plan_id"] = plan_id
    destination = data_root / UPGRADES_DIR / "plans" / f"{plan_id}.json"
    atomic_json(destination, plan_payload)
    return {
        **current,
        "plan_id": plan_id,
        "changes": plan_payload["changes"],
        "project_files": plan_payload["project_files"],
        "backup_policy": plan_payload["backup_policy"],
        "plan_path": str(destination),
        "status": STATUS_APPLY_READY,
        "guidance": guidance_for(STATUS_APPLY_READY),
    }


def install_writer_guards(con: sqlite3.Connection) -> None:
    """Reject stale writers that do not register the current writer epoch."""
    tables = [
        str(row[0]) for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' AND name != 'meta' ORDER BY name"
        )
    ]
    for table in tables:
        if not table.replace("_", "").isalnum():
            raise UpgradeError(f"unsafe table name in writer guard: {table}")
        for operation in ("INSERT", "UPDATE", "DELETE"):
            trigger = f"agent_marketplace_writer_{table}_{operation.lower()}"
            con.execute(
                f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {table} "
                "BEGIN SELECT CASE WHEN agent_marketplace_writer_epoch() != "
                "CAST((SELECT value FROM meta WHERE key='writer_epoch') AS INTEGER) "
                "THEN RAISE(ABORT, 'AGENT_MARKETPLACE_STALE_WRITER') END; END"
            )


def database_content_fingerprint(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    tables = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name != 'meta' ORDER BY name"
    )]
    for table in tables:
        digest.update(str(table).encode())
        for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def stamp_database_integrity(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
    generation = int(row[0]) + 1 if row else 1
    for key, value in (
        ("generation", str(generation)),
        ("fingerprint", database_content_fingerprint(con)),
        ("stamped_at", utc_now()),
    ):
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def migration_chain(
    plan_payload: dict,
    component: str,
    surface: str,
    from_version: int,
    to_version: int,
) -> list[dict]:
    registry = normalize_registry(Path(plan_payload["data_root"]))
    hosts = registry["plugins"].get(component, {}).get("hosts", {})
    catalogs: list[dict] = []
    package_roots: list[Path] = []
    for host, entry in sorted(hosts.items()):
        path = Path(entry["root"]) / "migrations" / "manifest.json"
        catalog = load_json(path, None)
        if not isinstance(catalog, dict) or catalog.get("component") != component:
            raise UpgradeError(f"invalid migration catalog for {component}/{host}")
        catalogs.append(catalog)
        package_roots.append(Path(entry["root"]))
    if not catalogs:
        raise UpgradeError(f"missing migration catalog for {component}")
    if len({canonical_json(value) for value in catalogs}) != 1:
        raise UpgradeError(f"host migration catalog mismatch for {component}")
    contract = catalogs[0].get(surface)
    if not isinstance(contract, dict) or contract.get("current") != to_version:
        raise UpgradeError(f"migration target mismatch for {component}/{surface}")
    steps = {step.get("from"): step for step in contract.get("steps", [])}
    chain: list[dict] = []
    current = from_version
    while current < to_version:
        step = steps.get(current)
        if not isinstance(step, dict) or step.get("to") != current + 1:
            raise UpgradeError(
                f"no ordered migration chain for {component}/{surface}:"
                f" {from_version}->{to_version}"
            )
        runner = Path(str(step.get("runner", "")))
        if runner.is_absolute() or not runner.parts:
            raise UpgradeError(f"invalid migration runner: {step.get('id', '')}")
        for package_root in package_roots:
            path = package_root / runner
            try:
                path.resolve().relative_to(package_root.resolve())
            except (OSError, ValueError) as exc:
                raise UpgradeError(
                    f"migration runner escapes package: {step.get('id', '')}"
                ) from exc
            expected = "sha256:" + sha256_file(path)
            if step.get("checksum") != expected:
                raise UpgradeError(
                    f"migration checksum mismatch: {step.get('id', '')}"
                )
        chain.append(step)
        current += 1
    return chain


def execute_migration_step(
    con: sqlite3.Connection, plan_payload: dict, step: dict
) -> dict:
    registry = normalize_registry(Path(plan_payload["data_root"]))
    hosts = registry["plugins"]["project-management-office"]["hosts"]
    package_root = Path(hosts[sorted(hosts)[0]]["root"])
    runner = package_root / str(step["runner"])
    namespace: dict = {}
    try:
        exec(compile(runner.read_text(encoding="utf-8"), str(runner), "exec"), namespace)
        migrate = namespace.get("migrate")
        if not callable(migrate):
            raise UpgradeError(f"migration runner has no migrate function: {runner}")
        result = migrate(con, plan_payload)
    except UpgradeError:
        raise
    except Exception as exc:
        raise UpgradeError(f"migration runner failed: {step.get('id', '')}: {exc}") from exc
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise UpgradeError(f"migration runner result must be an object: {runner}")
    return result


def migrate_database_connection(
    con: sqlite3.Connection,
    target_schema: int,
    plan_payload: dict,
    *,
    begin_transaction: bool,
) -> None:
    transaction_started = False
    try:
        con.execute("PRAGMA foreign_keys=ON")
        try:
            epoch_row = con.execute(
                "SELECT value FROM meta WHERE key='writer_epoch'"
            ).fetchone()
            source_epoch = int(epoch_row[0]) if epoch_row else WRITER_EPOCH
        except sqlite3.DatabaseError:
            source_epoch = WRITER_EPOCH
        migration_epoch = {"value": source_epoch}
        con.create_function(
            "agent_marketplace_writer_epoch", 0, lambda: migration_epoch["value"],
            deterministic=True,
        )
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        chain = migration_chain(
            plan_payload, "project-management-office", "database",
            version, target_schema,
        )
        if chain:
            if begin_transaction:
                con.execute("BEGIN EXCLUSIVE")
                transaction_started = True
            events = []
            for step in chain:
                stamp = utc_now()
                result = execute_migration_step(con, plan_payload, step)
                con.execute(
                    "INSERT INTO schema_migrations"
                    " (migration_id, from_version, to_version, checksum, plugin_version,"
                    " started_at, finished_at, source_fingerprint, result_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (step["id"], step["from"], step["to"], step["checksum"],
                     plan_payload.get("installed", {}).get(
                         "project-management-office", {}).get("version", ""),
                     stamp, utc_now(), plan_payload["source_fingerprint"],
                     canonical_json(result)),
                )
                con.execute(f"PRAGMA user_version={int(step['to'])}")
                events.append((stamp, step))
            migration_epoch["value"] = WRITER_EPOCH
            con.execute(
                "INSERT INTO meta(key, value) VALUES ('writer_epoch', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(WRITER_EPOCH),),
            )
            for stamp, step in events:
                con.execute(
                    "INSERT INTO events"
                    " (ts, project_id, work_order_id, actor, action, payload_json)"
                    " VALUES (?, NULL, NULL, 'upgrade', 'schema_migrated', ?)",
                    (stamp, canonical_json({
                        "migration_id": step["id"], "from": step["from"],
                        "to": step["to"],
                    })),
                )
            stamp_database_integrity(con)
            install_writer_guards(con)
            version = int(con.execute("PRAGMA user_version").fetchone()[0])
        if version != target_schema:
            raise UpgradeError(
                f"no migration chain from database schema {version} to {target_schema}"
            )
        foreign = con.execute("PRAGMA foreign_key_check").fetchone()
        if foreign is not None:
            raise UpgradeError(f"migration foreign key failure: {tuple(foreign)}")
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise UpgradeError(f"migration integrity failure: {integrity}")
        if transaction_started:
            con.execute("COMMIT")
    except Exception:
        if transaction_started:
            try:
                con.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
        raise


def migrate_database_candidate(
    candidate: Path,
    target_schema: int,
    plan_payload: dict,
) -> None:
    con = sqlite3.connect(candidate, isolation_level=None)
    try:
        migrate_database_connection(
            con, target_schema, plan_payload, begin_transaction=True
        )
    finally:
        con.close()


def lock_database_for_upgrade(db_file: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_file, timeout=5, isolation_level=None)
    con.create_function(
        "agent_marketplace_writer_epoch", 0, lambda: WRITER_EPOCH, deterministic=True
    )
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.DatabaseError as exc:
        con.close()
        raise UpgradeError(f"database writer lock unavailable: {exc}") from exc
    return con


def online_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass


def adapter_roots(plan_payload: dict, team_id: str) -> list[Path]:
    installed = plan_payload.get("installed", {})
    component = installed.get(team_id, {}) if isinstance(installed, dict) else {}
    hosts = component.get("hosts", []) if isinstance(component, dict) else []
    registry = normalize_registry(Path(plan_payload["data_root"]))
    entry = registry["plugins"].get(team_id, {})
    values = entry.get("hosts", {}) if isinstance(entry, dict) else {}
    roots = [Path(values[host]["root"]) for host in hosts if host in values]
    if hosts and len(roots) != len(hosts):
        raise UpgradeError("project adapter roots are incomplete")
    return roots


def run_adapter(root: Path, action: str, project: Path, workspace: str) -> dict:
    script = root / "scripts" / "project_upgrade_adapter.py"
    if not script.is_file():
        raise UpgradeError(f"package lacks project upgrade adapter: {root}")
    completed = subprocess.run(
        [sys.executable, str(script), action, "--project-root", str(project),
         "--workspace", workspace],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if completed.returncode != 0:
        raise UpgradeError(
            f"project adapter {action} failed at {root}: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UpgradeError(f"project adapter returned invalid JSON: {root}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("changes"), list):
        raise UpgradeError(f"project adapter returned invalid shape: {root}")
    return result


def project_changes(plan_payload: dict) -> tuple[dict[Path, bytes | None], dict]:
    project_data = plan_payload.get("project", {})
    if not project_data:
        return {}, {}
    root = project_root(project_data["root"])
    workspace = project_data["workspace"]
    config_path = root / workspace / "config.json"
    config = load_json(config_path, {})
    if not isinstance(config, dict):
        raise UpgradeError(f"project config must be an object: {config_path}")
    team_id = team_from_config(config)
    if not team_id:
        raise UpgradeError("project config has no recognized team owner")
    prior_owner = str(config.get("managed_by", ""))
    if prior_owner and prior_owner not in {team_id, team_id + PRIOR_OWNER_SUFFIX}:
        raise UpgradeError(f"project config has an unknown owner: {prior_owner}")
    config["team_id"] = team_id
    config.pop("managed_by", None)
    config.setdefault("project_origin", "unclassified")
    config_bytes = (json.dumps(config, indent=2) + "\n").encode()
    changes: dict[Path, bytes | None] = {}
    if config_path.read_bytes() != config_bytes:
        changes[config_path] = config_path.read_bytes()
    gitignore_path = root / ".gitignore"
    gitignore_current = gitignore_path.read_text(encoding="utf-8") \
        if gitignore_path.is_file() else ""
    gitignore_text = reconcile_gitignore(gitignore_current, workspace)
    gitignore_bytes = gitignore_text.encode()
    if not gitignore_path.is_file() or gitignore_path.read_bytes() != gitignore_bytes:
        changes[gitignore_path] = gitignore_path.read_bytes() \
            if gitignore_path.is_file() else None
    adapter_previews: list[tuple[Path, dict]] = []
    plan_payload["data_root"] = str(plan_payload["data_root"])
    package_roots = adapter_roots(plan_payload, team_id)
    for package_root in package_roots:
        preview = run_adapter(package_root, "check", root, workspace)
        adapter_previews.append((package_root, preview))
        for relative in preview["changes"]:
            path = root / str(relative)
            safe_relative(root, path)
            changes.setdefault(path, path.read_bytes() if path.is_file() else None)
    target_surfaces = {
        safe_relative(root, config_path) + "#agent-marketplace": config_owned_digest(config),
        ".gitignore#agent-marketplace:software-engineering-team":
            gitignore_owned_digest(gitignore_text),
    }
    for package_root, preview in adapter_previews:
        host = detect_package_host(package_root)
        for relative, value in preview.get("target_surfaces", {}).items():
            target_surfaces[f"{host}:{relative}"] = str(value)
    state = {
        "schema_version": PROJECT_STATE_SCHEMA,
        "project_id": plan_payload["project_id"],
        "project_key": project_data.get("project_key", ""),
        "repository_fingerprint": project_data.get("repository_fingerprint", ""),
        "delivery": project_data.get("delivery", {}),
        "team_id": team_id,
        "workspace": workspace,
        "contract_version": PROJECT_CONTRACT_VERSION,
        "hosts": sorted(plan_payload.get("installed", {}).get(team_id, {}).get("hosts", [])),
        "components": {
            plugin: value["version"]
            for plugin, value in sorted(relevant_components(
                plan_payload.get("installed", {}), team_id
            ).items())
        },
        "managed_surfaces": target_surfaces,
        "identities": {
            "preparation": "preparation_check.py",
            "experience": "experience-design",
            "backlog": "backlog-plan",
        },
        "vault": vault_contract_state(root, workspace, package_roots),
        "applied_at": plan_payload["created_at"],
        "upgrade_id": plan_payload["upgrade_id"],
        "upgrade_base_head": project_data.get("fingerprint", {}).get("head", ""),
    }
    state_path = root / STATE_RELATIVE
    changes.setdefault(state_path, state_path.read_bytes() if state_path.is_file() else None)
    return changes, {"state": state, "adapters": adapter_previews,
                     "config": config_bytes, "gitignore": gitignore_bytes}


def snapshot_content(snapshot: dict) -> bytes | None:
    return bytes.fromhex(snapshot["content"]) if snapshot.get("exists") else None


def validate_recovery_surfaces(
    plan_payload: dict, prepared: dict, durable: dict[str, dict], root: Path,
) -> None:
    project_data = plan_payload["project"]
    workspace = project_data["workspace"]
    config_path = root / workspace / "config.json"
    current_config = load_json(config_path, {})
    target_config = json.loads(prepared["config"])
    if not isinstance(current_config, dict) or not isinstance(target_config, dict):
        raise UpgradeError("project config is invalid during recovery")
    config_key = safe_relative(root, config_path) + "#agent-marketplace"
    original_config = project_data.get("managed_surfaces", {}).get(config_key, "")
    if config_owned_digest(current_config) not in {
        original_config, config_owned_digest(target_config),
    }:
        raise UpgradeError("managed project config drifted during recovery")
    ignore_path = root / ".gitignore"
    current_ignore = ignore_path.read_bytes() if ignore_path.is_file() else None
    original_ignore = snapshot_content(durable.get(".gitignore", {}))
    if current_ignore not in {original_ignore, prepared["gitignore"]}:
        raise UpgradeError("managed .gitignore drifted during recovery")

    state_path = root / STATE_RELATIVE
    state_relative = safe_relative(root, state_path)
    original_state = snapshot_content(durable.get(state_relative, {}))
    current_state = state_path.read_bytes() if state_path.is_file() else None
    target_state = (
        json.dumps(prepared["state"], indent=2, sort_keys=True) + "\n"
    ).encode()
    if current_state not in {original_state, target_state}:
        raise UpgradeError("managed project state drifted during recovery")

    original_surfaces = project_data.get("managed_surfaces", {})
    for package_root, preview in prepared["adapters"]:
        host = detect_package_host(package_root)
        current = preview.get("current_surfaces", {})
        target = preview.get("target_surfaces", {})
        if not isinstance(current, dict) or not isinstance(target, dict):
            raise UpgradeError(f"project adapter recovery shape is invalid: {host}")
        for relative in set(current) | set(target):
            value = str(current.get(relative, ""))
            allowed = {
                str(original_surfaces.get(f"{host}:{relative}", "")),
                str(target.get(relative, "")),
            }
            if value not in allowed:
                raise UpgradeError(
                    f"managed {host} project surface drifted during recovery:"
                    f" {relative}"
                )


def migrate_project_runtime(
    root: Path, workspace: str, plan_payload: dict,
    journal: dict, journal_path: Path,
) -> None:
    blockers = runtime_migration_blockers(root, workspace)
    if blockers:
        raise UpgradeError("runtime migration blocked: " + ", ".join(blockers))
    paths = project_runtime_paths(root, workspace)
    migration = journal.get("runtime_migration")
    if migration is None:
        moves = []
        for source_name, target_name in (
            ("legacy_plan", "plan"),
            ("legacy_work_orders", "work_orders"),
        ):
            source, target = paths[source_name], paths[target_name]
            moves.append({
                "source": safe_relative(root, source),
                "target": safe_relative(root, target),
                "staging": safe_relative(
                    root, target.parent /
                    f".{target.name}.migration-{plan_payload['plan_id']}"
                ),
                "source_existed": source.is_dir(),
                "digest": directory_digest(source),
            })
        removed_empty = [
            safe_relative(root, paths[name])
            for name in ("legacy_design", "legacy_experience")
            if paths[name].is_dir()
        ]
        migration = {"moves": moves, "removed_empty": removed_empty}
        journal["runtime_migration"] = migration
        atomic_json(journal_path, journal)
    if not isinstance(migration, dict) \
            or not isinstance(migration.get("moves"), list):
        raise UpgradeError("runtime migration journal is invalid")

    for entry in migration["moves"]:
        source = root / str(entry["source"])
        target = root / str(entry["target"])
        expected = str(entry["digest"])
        if not entry.get("source_existed"):
            continue
        staging = root / str(entry["staging"])
        if source.is_dir() and target.is_dir():
            if directory_digest(source) != expected \
                    or directory_digest(target) != expected:
                raise UpgradeError(
                    f"runtime migration collision changed: {entry['source']}"
                )
            shutil.rmtree(source)
            continue
        if not source.exists() and target.is_dir():
            if directory_digest(target) != expected:
                raise UpgradeError(
                    f"migrated runtime digest changed: {entry['target']}"
                )
            continue
        if not source.is_dir() or target.exists():
            raise UpgradeError(
                f"runtime migration state is inconsistent: {entry['source']}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            if not staging.is_dir() or directory_digest(staging) != expected:
                raise UpgradeError(
                    f"runtime migration staging changed: {entry['target']}"
                )
        else:
            shutil.copytree(source, staging)
        if directory_digest(staging) != expected:
            raise UpgradeError(
                f"runtime migration copy verification failed: {entry['source']}"
            )
        staging.rename(target)
        if directory_digest(target) != expected:
            raise UpgradeError(
                f"runtime migration rename verification failed: {entry['target']}"
            )
        shutil.rmtree(source)
        atomic_json(journal_path, journal)

    for relative in migration.get("removed_empty", []):
        path = root / str(relative)
        if path.is_dir():
            try:
                path.rmdir()
            except OSError as exc:
                raise UpgradeError(
                    f"legacy transient directory is no longer empty: {relative}"
                ) from exc


def rollback_project_runtime(root: Path, journal: dict) -> None:
    migration = journal.get("runtime_migration")
    if not isinstance(migration, dict):
        return
    for entry in reversed(migration.get("moves", [])):
        if not isinstance(entry, dict) or not entry.get("source_existed"):
            continue
        source = root / str(entry["source"])
        target = root / str(entry["target"])
        staging = root / str(entry.get("staging", ""))
        expected = str(entry.get("digest", ""))
        if source.is_dir() and target.is_dir():
            if directory_digest(source) == expected \
                    and directory_digest(target) == expected:
                shutil.rmtree(target)
        elif not source.exists() and target.is_dir() \
                and directory_digest(target) == expected:
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
        if staging.is_dir() and directory_digest(staging) == expected:
            shutil.rmtree(staging)
    for relative in migration.get("removed_empty", []):
        (root / str(relative)).mkdir(parents=True, exist_ok=True)
    runtime = root / RUNTIME_RELATIVE
    for path in (runtime / "plan", runtime / "work-orders", runtime):
        try:
            path.rmdir()
        except OSError:
            pass


def apply_project(plan_payload: dict, journal: dict, journal_path: Path) -> None:
    project_data = plan_payload.get("project", {})
    if not project_data:
        journal["phase"] = "project_complete"
        atomic_json(journal_path, journal)
        return
    root = project_root(project_data["root"])
    changes, prepared = project_changes(plan_payload)
    durable = journal.get("project_snapshots")
    if durable is not None and not isinstance(durable, dict):
        raise UpgradeError("project recovery snapshots are invalid")
    snapshots: dict[str, dict] = {}
    for path, expected in changes.items():
        relative = safe_relative(root, path)
        current = path.read_bytes() if path.is_file() else None
        if durable is None and current != expected:
            raise UpgradeError(f"project file changed after planning: {relative}")
        snapshots[relative] = {
            "exists": current is not None,
            "content": current.hex() if current is not None else "",
            "mode": path.stat().st_mode & 0o777 if path.exists() else None,
        }
    if durable is None:
        journal["project_snapshots"] = snapshots
        durable = snapshots
    else:
        validate_recovery_surfaces(plan_payload, prepared, durable, root)
    journal["phase"] = "project_applying"
    atomic_json(journal_path, journal)
    try:
        migrate_project_runtime(
            root, project_data["workspace"], plan_payload,
            journal, journal_path,
        )
        config_path = root / project_data["workspace"] / "config.json"
        if config_path.read_bytes() != prepared["config"]:
            atomic_bytes(config_path, prepared["config"],
                         config_path.stat().st_mode & 0o777)
        gitignore_path = root / ".gitignore"
        if not gitignore_path.is_file() or gitignore_path.read_bytes() != prepared["gitignore"]:
            atomic_bytes(gitignore_path, prepared["gitignore"],
                         gitignore_path.stat().st_mode & 0o777
                         if gitignore_path.exists() else 0o644)
        for package_root, _preview in prepared["adapters"]:
            run_adapter(package_root, "apply", root, project_data["workspace"])
        state = prepared["state"]
        surfaces = managed_surface_hashes(root, config_path)
        host_surfaces, _ = adapter_surfaces(
            Path(plan_payload["data_root"]), state["team_id"], root,
            project_data["workspace"], "inspect",
        )
        surfaces.update(host_surfaces)
        state["managed_surfaces"] = surfaces
        atomic_json(root / STATE_RELATIVE, state, 0o644)
        journal["phase"] = "project_complete"
        atomic_json(journal_path, journal)
    except Exception:
        for relative, snapshot in snapshots.items():
            path = root / relative
            if snapshot["exists"]:
                atomic_bytes(path, snapshot_content(snapshot) or b"",
                             snapshot["mode"] or 0o644)
            else:
                path.unlink(missing_ok=True)
        rollback_project_runtime(root, journal)
        raise


def sync_project_identity(db_file: Path, plan_payload: dict) -> None:
    project = plan_payload.get("project", {})
    key = str(project.get("project_key", ""))
    if not key:
        return
    con = sqlite3.connect(db_file, isolation_level=None)
    con.create_function(
        "agent_marketplace_writer_epoch", 0, lambda: WRITER_EPOCH, deterministic=True
    )
    try:
        con.execute("PRAGMA foreign_keys=ON")
        row = con.execute(
            "SELECT id, project_uuid, repository_fingerprint FROM projects"
            " WHERE project_key = ?", (key,),
        ).fetchone()
        if row is None:
            raise UpgradeError(f"project identity is absent from PMO: {key}")
        wanted = (
            str(plan_payload.get("project_id", "")),
            str(project.get("repository_fingerprint", "")),
        )
        if (str(row[1]), str(row[2])) == wanted:
            return
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE projects SET project_uuid = ?, repository_fingerprint = ?"
            " WHERE id = ?", (*wanted, row[0]),
        )
        con.execute(
            "INSERT INTO events"
            " (ts, project_id, work_order_id, actor, action, payload_json)"
            " VALUES (?, ?, NULL, 'upgrade', 'project_contract_upgraded', ?)",
            (utc_now(), row[0], canonical_json({
                "project_uuid": wanted[0],
                "contract_version": PROJECT_CONTRACT_VERSION,
            })),
        )
        stamp_database_integrity(con)
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise
    finally:
        con.close()


def apply(
    data_root: Path,
    db_file: Path,
    target_schema: int,
    plan_id: str,
) -> dict:
    plan_path = data_root / UPGRADES_DIR / "plans" / f"{plan_id}.json"
    plan_payload = load_json(plan_path, None)
    if not isinstance(plan_payload, dict) or plan_payload.get("plan_id") != plan_id:
        raise UpgradeError(f"unknown upgrade plan: {plan_id}")
    plan_payload["data_root"] = str(data_root)
    current = status(
        data_root, db_file, target_schema,
        plan_payload.get("project", {}).get("root") or None,
    )
    if current["status"] != STATUS_READY:
        raise UpgradeError(
            "upgrade is no longer apply-ready: "
            + ", ".join(current.get("blockers", []))
        )
    if current["fingerprint"] != plan_payload["source_fingerprint"]:
        raise UpgradeError("upgrade source fingerprint changed; prepare a new plan")
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{plan_id[:8]}"
    run_root = data_root / UPGRADES_DIR / run_id
    journal_path = run_root / "journal.json"
    backup = run_root / "backup.db"
    candidate = run_root / "candidate.db"
    journal = {
        "schema_version": 1,
        "run_id": run_id,
        "plan_id": plan_id,
        "phase": "starting",
        "started_at": utc_now(),
        "database_backup": str(backup),
        "database_candidate": str(candidate),
    }
    run_root.mkdir(parents=True, exist_ok=False)
    atomic_json(journal_path, journal)
    db_committed = False
    project_started = False
    with DirectoryLock(data_root, "upgrade.lock"):
        atomic_json(data_root / MAINTENANCE_NAME, {
            "run_id": run_id, "plan_id": plan_id, "started_at": utc_now(),
        })
        try:
            if plan_payload["database"]["from"] != target_schema:
                source = lock_database_for_upgrade(db_file)
                try:
                    source_version = int(
                        source.execute("PRAGMA user_version").fetchone()[0]
                    )
                    source_fingerprint = database_content_fingerprint(source)
                    if source_version != plan_payload["database"]["from"] or \
                            source_fingerprint != \
                            plan_payload["database"].get("fingerprint"):
                        raise UpgradeError(
                            "database changed before the writer lock was acquired;"
                            " prepare a new plan"
                        )
                    online_backup(db_file, backup)
                    online_backup(db_file, candidate)
                    journal["phase"] = "database_candidate"
                    atomic_json(journal_path, journal)
                    migrate_database_candidate(
                        candidate, target_schema, plan_payload
                    )
                    migrate_database_connection(
                        source, target_schema, plan_payload,
                        begin_transaction=False,
                    )
                    source.execute("COMMIT")
                    db_committed = True
                except BaseException:
                    if not db_committed:
                        try:
                            source.execute("ROLLBACK")
                        except sqlite3.DatabaseError:
                            pass
                    raise
                finally:
                    source.close()
                journal["phase"] = "database_complete"
                atomic_json(journal_path, journal)
            else:
                journal["phase"] = "database_complete"
                atomic_json(journal_path, journal)
            project_started = bool(plan_payload.get("project"))
            apply_project(plan_payload, journal, journal_path)
            sync_project_identity(db_file, plan_payload)
            journal["phase"] = "complete"
            journal["finished_at"] = utc_now()
            atomic_json(journal_path, journal)
            (data_root / MAINTENANCE_NAME).unlink(missing_ok=True)
            return {
                "status": STATUS_RESTART,
                "run_id": run_id,
                "plan_id": plan_id,
                "journal": str(journal_path),
                "backup": str(backup) if backup.exists() else "",
                "mutation_performed": True,
                "guidance": guidance_for(STATUS_RESTART),
                "project_status": STATUS_PROJECT_PR \
                if plan_payload.get("project") else "",
            }
        except BaseException:
            journal["phase"] = (
                "recovery_required"
                if db_committed or project_started else "rolled_back"
            )
            journal["failed_at"] = utc_now()
            atomic_json(journal_path, journal)
            if not db_committed and not project_started:
                (data_root / MAINTENANCE_NAME).unlink(missing_ok=True)
            raise


def recover(data_root: Path, db_file: Path, target_schema: int, run_id: str) -> dict:
    journal_path = data_root / UPGRADES_DIR / run_id / "journal.json"
    journal = load_json(journal_path, None)
    if not isinstance(journal, dict):
        raise UpgradeError(f"unknown recovery run: {run_id}")
    if journal.get("phase") == "complete":
        return {"status": STATUS_RESTART, "run_id": run_id,
                "mutation_performed": False}
    plan_id = str(journal.get("plan_id", ""))
    plan_payload = load_json(
        data_root / UPGRADES_DIR / "plans" / f"{plan_id}.json", None
    )
    if not isinstance(plan_payload, dict):
        raise UpgradeError(f"recovery plan is missing for run: {run_id}")
    plan_payload["data_root"] = str(data_root)
    with DirectoryLock(data_root, "upgrade.lock", reclaim_dead=True):
        if database_version(db_file) != target_schema:
            journal["phase"] = "rolled_back"
            atomic_json(journal_path, journal)
            (data_root / MAINTENANCE_NAME).unlink(missing_ok=True)
            return {"status": STATUS_READY, "run_id": run_id,
                    "mutation_performed": False}
        apply_project(plan_payload, journal, journal_path)
        sync_project_identity(db_file, plan_payload)
        journal["phase"] = "complete"
        journal["finished_at"] = utc_now()
        atomic_json(journal_path, journal)
        (data_root / MAINTENANCE_NAME).unlink(missing_ok=True)
    return {"status": STATUS_RESTART, "run_id": run_id,
            "mutation_performed": True}


def initialize_project_contract(
    data_root: Path,
    project_path: str | Path,
    team_id: str,
    workspace: str,
) -> dict:
    root = project_root(project_path)
    state_path = root / STATE_RELATIVE
    if state_path.exists():
        state = load_json(state_path, {})
        if state.get("team_id") != team_id:
            raise UpgradeError("existing project contract belongs to another team")
        return state
    installed, blockers = inventory(data_root)
    if blockers:
        raise UpgradeError(", ".join(blockers))
    config_path = root / workspace / "config.json"
    surfaces = managed_surface_hashes(
        root, config_path if config_path.is_file() else None
    )
    host_surfaces, _ = adapter_surfaces(
        data_root, team_id, root, workspace, "inspect"
    )
    surfaces.update(host_surfaces)
    state = {
        "schema_version": PROJECT_STATE_SCHEMA,
        "project_id": str(uuid.uuid4()),
        "project_key": "",
        "repository_fingerprint": repository_fingerprint(root),
        "delivery": repository_delivery(root),
        "team_id": team_id,
        "workspace": workspace,
        "contract_version": PROJECT_CONTRACT_VERSION,
        "hosts": sorted(installed["components"].get(team_id, {}).get("hosts", [])),
        "components": {
            plugin: value["version"]
            for plugin, value in sorted(relevant_components(
                installed["components"], team_id
            ).items())
        },
        "managed_surfaces": surfaces,
        "identities": {
            "preparation": "preparation_check.py",
            "experience": "experience-design",
            "backlog": "backlog-plan",
        },
        "vault": {
            "root": f"{workspace}/docs", "policy_version": 5,
            "status": "active", "adoption_plan_hash": "",
        },
        "applied_at": utc_now(),
    }
    atomic_json(state_path, state, 0o644)
    return state
