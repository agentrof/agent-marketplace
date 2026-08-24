#!/usr/bin/env python3
"""Inspect and project one OpenCode Agent Marketplace package into a project."""

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
from pathlib import Path


TEAM = "software-engineering-team"
SCHEMA_VERSION = 1
START = "# agent-marketplace:software-engineering-team:gitignore:start"
END = "# agent-marketplace:software-engineering-team:gitignore:end"
PACKAGE_TOKEN = "{{AGENT_MARKETPLACE_OPENCODE_PACKAGE_ROOT}}"
PUBLIC_DIRS = ("agents", "commands", "skills", "plugins")
TRUSTED_ORIGINS = {
    "https://github.com/agentrof/agent-marketplace.git",
    "git@github.com:agentrof/agent-marketplace.git",
    "ssh://git@github.com/agentrof/agent-marketplace.git",
}


class ProjectionError(RuntimeError):
    """A project projection cannot safely continue."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def emit(action: str, *, ok: bool, code: str = "ok", **extra: object) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "action": action,
        "code": code,
        "changed": [],
        "preserved": [],
        "conflicts": [],
        "next_actions": [],
    }
    payload.update(extra)
    print(json.dumps(payload, indent=2, sort_keys=True))


def source_package() -> Path:
    return Path(__file__).resolve().parents[1]


def repository_root(source: Path) -> Path | None:
    for candidate in (source, *source.parents):
        if (candidate / "product.json").is_file() and (candidate / ".git").exists():
            return candidate
    return None


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError("manifest_hash_mismatch", f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ProjectionError("manifest_hash_mismatch", f"JSON object required: {path}")
    return value


def package_manifest(source: Path) -> dict:
    manifest = source / ".agent-marketplace-package.json"
    data = read_json(manifest)
    if data.get("schema_version") != 2 or data.get("component") != TEAM \
            or data.get("host") != "opencode":
        raise ProjectionError("manifest_hash_mismatch", "OpenCode package provenance is invalid")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise ProjectionError("manifest_hash_mismatch", "package provenance has no file inventory")
    for relative, expected in files.items():
        path = source / relative
        if not isinstance(relative, str) or not isinstance(expected, str) \
                or not path.is_file() or sha256(path) != expected:
            raise ProjectionError("manifest_hash_mismatch", f"package file hash differs: {relative}")
    return data


def git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True,
                          text=True, check=False)


def is_git_target(project: Path) -> bool:
    return git(project, "rev-parse", "--show-toplevel").returncode == 0


def validate_source(source: Path, development_source: bool) -> tuple[dict, dict]:
    manifest = package_manifest(source)
    if development_source:
        return manifest, {"trust_mode": "development"}
    root = repository_root(source)
    if root is None:
        raise ProjectionError("invalid_source_ref", "stable projection requires an annotated-tag source checkout")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        raise ProjectionError("source_dirty", "stable source checkout must be clean")
    head = git(root, "rev-parse", "HEAD")
    tag = git(root, "describe", "--exact-match", "--tags", "HEAD")
    if head.returncode or tag.returncode:
        raise ProjectionError("non_annotated_tag", "source HEAD must have an exact annotated tag")
    object_type = git(root, "cat-file", "-t", tag.stdout.strip())
    if object_type.returncode or object_type.stdout.strip() != "tag":
        raise ProjectionError("non_annotated_tag", "source tag must be annotated")
    tag_name = tag.stdout.strip()
    if tag_name != f"v{manifest.get('version', '')}":
        raise ProjectionError("version_mismatch", "source tag and package version differ")
    peeled = git(root, "rev-parse", f"{tag_name}^{{}}")
    if peeled.returncode or peeled.stdout.strip() != head.stdout.strip():
        raise ProjectionError("tag_head_mismatch", "annotated tag does not peel to source HEAD")
    origin = git(root, "remote", "get-url", "origin")
    if origin.returncode or origin.stdout.strip() not in TRUSTED_ORIGINS:
        raise ProjectionError("source_origin_mismatch", "source origin is not the stable Agent Marketplace origin")
    return manifest, {
        "tag": tag_name,
        "peeled_commit": peeled.stdout.strip(),
        "origin": origin.stdout.strip(),
        "trust_mode": "annotated_tag",
    }


def ensure_regular_path(root: Path, target: Path, label: str) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ProjectionError("unsafe_path", f"{label} escapes project root") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            attributes = getattr(os.lstat(current), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0
        reparse = bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        if current.is_symlink() or reparse:
            raise ProjectionError("symlink_or_reparse", f"{label} is symlinked: {current}")


def managed_block(source: Path) -> str:
    template = (source / "templates" / "gitignore").read_text(encoding="utf-8")
    try:
        left = template.index(START)
        right = template.index(END, left) + len(END)
    except ValueError as exc:
        raise ProjectionError("projection_drift", "package gitignore template is invalid") from exc
    return template[left:right]


def proposed_gitignore(project: Path, source: Path) -> tuple[bytes | None, str]:
    path = project / ".gitignore"
    ensure_regular_path(project, path, ".gitignore")
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = managed_block(source)
    starts, ends = current.count(START), current.count(END)
    if starts != ends or starts > 1:
        raise ProjectionError("projection_drift", "managed .gitignore markers are malformed")
    if starts:
        left = current.index(START)
        right = current.index(END, left) + len(END)
        updated = current[:left] + block + current[right:]
    else:
        updated = (current.rstrip() + "\n\n" if current.strip() else "") + block + "\n"
    return path.read_bytes() if path.is_file() else None, updated


def source_public_files(source: Path, package_root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory in PUBLIC_DIRS:
        root = source / directory
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            content = path.read_bytes()
            if path.suffix in {".md", ".js"}:
                content = content.replace(PACKAGE_TOKEN.encode(), str(package_root).encode())
            files[relative] = content
    return files


def build_key(manifest: dict) -> str:
    build_id = str(manifest.get("build_id", ""))
    digest = build_id.removeprefix("snapshot.")
    if len(digest) < 32 or any(char not in "0123456789abcdef" for char in digest):
        raise ProjectionError("manifest_hash_mismatch", "package build ID is invalid")
    return f"{manifest['version']}-{digest[:32]}"


def installation_path(project: Path) -> Path:
    return project / ".opencode" / "agentrof" / "agent-marketplace" / "installation.json"


def load_installation(project: Path) -> dict | None:
    path = installation_path(project)
    if not path.exists():
        return None
    data = read_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ProjectionError("unsupported_installation_schema", "installation schema is unsupported")
    return data


def tracked_opencode(project: Path) -> list[str]:
    if not is_git_target(project):
        return []
    result = git(project, "ls-files", "--", ".opencode")
    if result.returncode:
        raise ProjectionError("tracked_projection_conflict", "cannot inspect tracked OpenCode files")
    return [line for line in result.stdout.splitlines() if line]


def inspect(project: Path, source: Path, development_source: bool) -> dict:
    manifest, provenance = validate_source(source, development_source)
    if not project.is_dir():
        raise ProjectionError("unsafe_path", "project root must be an existing directory")
    try:
        project.relative_to(source)
    except ValueError:
        try:
            source.relative_to(project)
        except ValueError:
            pass
        else:
            raise ProjectionError("source_target_overlap", "target may not contain the source package")
    else:
        raise ProjectionError("source_target_overlap", "target may not be inside the source package")
    opencode = project / ".opencode"
    ensure_regular_path(project, opencode, ".opencode")
    tracked = tracked_opencode(project)
    if tracked:
        raise ProjectionError("tracked_projection_conflict", "tracked .opencode files: " + ", ".join(tracked))
    current = load_installation(project)
    key = build_key(manifest)
    package_root = project / ".opencode" / "agentrof" / "agent-marketplace" / "packages" / key / TEAM
    public = source_public_files(source, package_root)
    current_owned = (current or {}).get("public_owned_files", {})
    conflicts = []
    owned_modified = []
    for relative, content in public.items():
        target = project / ".opencode" / relative
        ensure_regular_path(project, target, relative)
        if not target.exists():
            continue
        known = current_owned.get(relative, {}) if isinstance(current_owned, dict) else {}
        if not isinstance(known, dict) or "sha256" not in known:
            conflicts.append(relative)
        elif known.get("sha256") != sha256(target):
            owned_modified.append(relative)
    if isinstance(current_owned, dict):
        for relative, known in current_owned.items():
            if relative in public or not isinstance(known, dict):
                continue
            target = project / ".opencode" / relative
            ensure_regular_path(project, target, relative)
            if target.exists() and (not target.is_file() or known.get("sha256") != sha256(target)):
                owned_modified.append(relative)
    existing = []
    if opencode.is_dir() and current is None:
        existing = [path.relative_to(opencode).as_posix() for path in opencode.rglob("*") if path.is_file()]
    return {
        "manifest": manifest,
        "source": provenance,
        "build_key": key,
        "package_root": package_root,
        "public": public,
        "current": current,
        "conflicts": sorted(conflicts),
        "owned_modified": sorted(set(owned_modified)),
        "untracked_existing": sorted(existing),
    }


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def maintenance_guard(project: Path, timeout_seconds: float = 3.0):
    """Acquire the shared setup/projector maintenance guard with a bound."""
    runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
    ensure_regular_path(project, runtime, "maintenance runtime")
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "setup-apply.guard"
    ensure_regular_path(project, path, "maintenance guard")
    handle = path.open("a+b")
    acquired = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    msvcrt = __import__("msvcrt")
                    if path.stat().st_size == 0:
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
            raise ProjectionError("maintenance_busy", "project maintenance lock is busy")
        yield
    finally:
        if acquired:
            if os.name == "nt":
                msvcrt = __import__("msvcrt")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = __import__("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _apply_locked(project: Path, source: Path, development_source: bool,
                  acknowledge_untracked: bool, allow_downgrade: bool) -> None:
    plan = inspect(project, source, development_source)
    if plan["conflicts"]:
        raise ProjectionError("unmanaged_collision", "public OpenCode collisions: " + ", ".join(plan["conflicts"]))
    if plan["owned_modified"]:
        raise ProjectionError("owned_file_modified", "modified owned OpenCode files: " + ", ".join(plan["owned_modified"]))
    if plan["untracked_existing"] and not acknowledge_untracked:
        raise ProjectionError("untracked_projection_ack_required", "unrelated .opencode files require --acknowledge-untracked-opencode")
    current = plan["current"]
    if current and current.get("active_build_key") != plan["build_key"] and not allow_downgrade:
        current_version = str(current.get("source", {}).get("version", ""))
        if current_version and current_version > str(plan["manifest"].get("version", "")):
            raise ProjectionError("downgrade_requires_flag", "downgrade requires --allow-downgrade")
    private = installation_path(project).parent
    package_root = plan["package_root"]
    stage = private / "runtime" / "transactions" / f"{plan['build_key']}.staging"
    journal = private / "runtime" / "maintenance.json"
    if stage.exists() or journal.exists():
        raise ProjectionError("maintenance_busy", "an unfinished projector staging directory exists")
    stage.parent.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(journal, b'{"state":"applying"}\n')
    previous: dict[Path, bytes | None] = {}
    written: dict[Path, bytes | None] = {}
    published_package = False

    def image(path: Path) -> bytes | None:
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not path.is_file():
            raise ProjectionError("unmanaged_collision", f"managed target is not a regular file: {path}")
        return path.read_bytes()

    def remember(path: Path) -> None:
        if path not in previous:
            previous[path] = image(path)

    def revalidate(path: Path) -> None:
        if image(path) != previous[path]:
            raise ProjectionError("rollback_conflict", f"target changed during projection: {path}")

    def replace(path: Path, content: bytes) -> None:
        revalidate(path)
        atomic_write(path, content)
        written[path] = content

    def remove(path: Path) -> None:
        revalidate(path)
        path.unlink(missing_ok=True)
        written[path] = None

    old_ignore, new_ignore = proposed_gitignore(project, source)
    del old_ignore
    previous_owned = current.get("public_owned_files", {}) if current else {}
    tracked_targets = [private / "manage.py", project / ".gitignore", installation_path(project)]
    tracked_targets.extend(project / ".opencode" / relative for relative in plan["public"])
    if isinstance(previous_owned, dict):
        tracked_targets.extend(
            project / ".opencode" / relative
            for relative in previous_owned if relative not in plan["public"]
        )
    for target in tracked_targets:
        ensure_regular_path(project, target, str(target))
        remember(target)

    try:
        private_stage = stage / TEAM
        shutil.copytree(source, private_stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        package_manifest(private_stage)
        package_root.parent.mkdir(parents=True, exist_ok=True)
        if package_root.exists():
            existing_manifest = package_manifest(package_root)
            if existing_manifest.get("build_id") != plan["manifest"].get("build_id"):
                raise ProjectionError("build_key_collision", "bounded build key belongs to another package")
            shutil.rmtree(private_stage)
        else:
            os.replace(private_stage, package_root)
            published_package = True
        manage_source = package_root / "scripts" / "opencode_manage.py"
        if not manage_source.is_file():
            raise ProjectionError("projection_drift", "package lifecycle script is missing")
        replace(private / "manage.py", manage_source.read_bytes())
        replace(project / ".gitignore", new_ignore.encode())
        if isinstance(previous_owned, dict):
            for relative, item in previous_owned.items():
                if relative in plan["public"] or not isinstance(item, dict):
                    continue
                target = project / ".opencode" / relative
                if not target.exists():
                    continue
                if not target.is_file() or item.get("sha256") != sha256(target):
                    raise ProjectionError("owned_file_modified", f"modified owned OpenCode file: {relative}")
                remove(target)
                for parent in target.parents:
                    if parent == project / ".opencode":
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
        owned = {}
        for relative, content in plan["public"].items():
            target = project / ".opencode" / relative
            replace(target, content)
            owned[relative] = {"sha256": sha256(target), "kind": "public"}
        retained = []
        if current and current.get("active_build_key"):
            retained.append(current["active_build_key"])
        installation = {
            "schema_version": SCHEMA_VERSION,
            "component": TEAM,
            "active_build_key": plan["build_key"],
            "active_full_build_id": plan["manifest"]["build_id"],
            "retained_builds": list(dict.fromkeys(retained)),
            "source": {
                "version": plan["manifest"]["version"],
                "build_id": plan["manifest"]["build_id"],
                **plan["source"],
            },
            "package_manifest_path": f"packages/{plan['build_key']}/{TEAM}/.agent-marketplace-package.json",
            "package_manifest_sha256": sha256(package_root / ".agent-marketplace-package.json"),
            "public_owned_files": owned,
            "runtime_bindings": [],
            "supported_surfaces": ["terminal.tui", "terminal.run.choice_free"],
            "tested_opencode_versions": ["1.18.17"],
            "mutator_contract": ["write", "edit", "apply_patch", "bash"],
            "generation": int((current or {}).get("generation", 0)) + 1,
            "transaction_state": "ready",
        }
        replace(installation_path(project), json.dumps(installation, indent=2, sort_keys=True).encode() + b"\n")
        journal.unlink(missing_ok=True)
        stage.rmdir()
        try:
            stage.parent.rmdir()
        except OSError:
            pass
        emit("apply", ok=True, changed=[".gitignore", ".opencode"], build_key=plan["build_key"])
    except Exception as exc:
        rollback_conflicts = []
        for target in reversed(list(written)):
            try:
                if image(target) != written[target]:
                    rollback_conflicts.append(str(target))
                    continue
                content = previous[target]
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, content)
            except (OSError, ProjectionError):
                rollback_conflicts.append(str(target))
        if published_package:
            try:
                existing_manifest = package_manifest(package_root)
                if existing_manifest.get("build_id") != plan["manifest"].get("build_id"):
                    rollback_conflicts.append(str(package_root))
                else:
                    shutil.rmtree(package_root)
                    package_root.parent.rmdir()
            except (OSError, ProjectionError):
                rollback_conflicts.append(str(package_root))
        if rollback_conflicts:
            raise ProjectionError(
                "rollback_conflict",
                "projection rollback preserved concurrent paths: " + ", ".join(rollback_conflicts),
            ) from exc
        shutil.rmtree(stage, ignore_errors=True)
        journal.unlink(missing_ok=True)
        if written or published_package:
            raise ProjectionError("rollback_complete", f"projection failed and was restored: {exc}") from exc
        raise


def apply(project: Path, source: Path, development_source: bool,
          clients_stopped: bool, acknowledge_untracked: bool, allow_downgrade: bool) -> None:
    if not clients_stopped:
        raise ProjectionError("clients_stopped_required", "apply requires --clients-stopped")
    with maintenance_guard(project):
        _apply_locked(project, source, development_source, acknowledge_untracked,
                      allow_downgrade)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "apply", "recover"), nargs="?", default="apply")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--clients-stopped", action="store_true")
    parser.add_argument("--acknowledge-untracked-opencode", action="store_true")
    parser.add_argument("--allow-downgrade", action="store_true")
    parser.add_argument("--development-source", action="store_true")
    args = parser.parse_args()
    action = args.command
    try:
        project = args.project_root.resolve()
        source = source_package()
        if action == "inspect":
            plan = inspect(project, source, args.development_source)
            emit(action, ok=True, build_key=plan["build_key"],
                 source=plan["source"], conflicts=plan["conflicts"],
                 untracked_existing=plan["untracked_existing"])
        elif action == "recover":
            transaction = installation_path(project).parent / "runtime" / "maintenance.json"
            if transaction.exists():
                raise ProjectionError(
                    "rollback_conflict",
                    "an interrupted transaction requires explicit journal-guided recovery; refusing to discard evidence",
                )
            emit(action, ok=True)
        else:
            apply(project, source, args.development_source, args.clients_stopped,
                  args.acknowledge_untracked_opencode, args.allow_downgrade)
        return 0
    except ProjectionError as exc:
        emit(action, ok=False, code=exc.code, next_actions=[str(exc)])
        return 4 if exc.code in {"unsupported_opencode_version", "hook_contract_incompatible"} else 1


if __name__ == "__main__":
    sys.exit(main())
