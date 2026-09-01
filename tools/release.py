#!/usr/bin/env python3
"""Stable release and cross-host version tooling for Agent Marketplace.

SemVer belongs only to stable releases. Normal main commits use a build identity
derived from first-parent history and the commit SHA.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import build_distributions


MARKETPLACE_COMPONENT = "agent-marketplace"
IMPACTS = {"patch": 1, "minor": 2, "major": 3}
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHANGESET_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
BOOTSTRAP_VERSION = "0.0.1"


class ReleaseError(RuntimeError):
    """A release contract is invalid or unsafe to continue."""


@dataclass(frozen=True)
class Changeset:
    path: Path
    summary: str
    components: dict[str, str]


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseError(f"missing release file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode("utf-8"))


def parse_semver(value: str, label: str = "version") -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ReleaseError(f"{label} must be strict SemVer X.Y.Z, got {value!r}")
    return tuple(int(part) for part in match.groups())


def require_sha(value: str, label: str) -> str:
    if SHA_RE.fullmatch(value) is None:
        raise ReleaseError(f"{label} must be an exact lowercase 40-hex commit SHA")
    return value


def bump(value: str, impact: str) -> str:
    major, minor, patch = parse_semver(value)
    if impact == "patch":
        patch += 1
    elif impact == "minor":
        minor += 1
        patch = 0
    elif impact == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ReleaseError(f"unknown release impact: {impact!r}")
    return f"{major}.{minor}.{patch}"


def load_versions(root: Path) -> dict:
    path = root / "versions.json"
    data = read_json(path)
    if set(data) != {"schema_version", "marketplace", "plugins"}:
        raise ReleaseError("versions.json has unknown or missing top-level keys")
    if data.get("schema_version") != 1:
        raise ReleaseError("versions.json schema_version must be 1")
    parse_semver(data.get("marketplace", ""), "marketplace version")
    plugins = data.get("plugins")
    if not isinstance(plugins, dict) or not plugins:
        raise ReleaseError("versions.json plugins must be a non-empty object")
    expected = {
        path.name for path in (root / "plugins").iterdir() if path.is_dir()
    }
    if set(plugins) != expected:
        raise ReleaseError(
            "versions.json plugin registry differs from plugins/: "
            f"registered={sorted(plugins)}, actual={sorted(expected)}"
        )
    for name, version in plugins.items():
        if not isinstance(name, str) or not isinstance(version, str):
            raise ReleaseError("versions.json plugin entries must be strings")
        parse_semver(version, f"{name} version")
    return data


def load_changesets(root: Path, versions: dict | None = None) -> list[Changeset]:
    versions = versions or load_versions(root)
    allowed = {MARKETPLACE_COMPONENT, *versions["plugins"]}
    changes_dir = root / ".changes"
    if not changes_dir.is_dir():
        raise ReleaseError(".changes directory is missing")
    result: list[Changeset] = []
    for path in sorted(changes_dir.glob("*.json")):
        if not CHANGESET_NAME_RE.fullmatch(path.name):
            raise ReleaseError(f"changeset filename must be kebab-case: {path.name}")
        data = read_json(path)
        if set(data) != {"summary", "components"}:
            raise ReleaseError(f"{path} must contain only summary and components")
        summary = data.get("summary")
        components = data.get("components")
        if not isinstance(summary, str) or not summary.strip():
            raise ReleaseError(f"{path} summary must be a non-empty string")
        if not isinstance(components, dict):
            raise ReleaseError(f"{path} components must be an object")
        for component, impact in components.items():
            if component not in allowed:
                raise ReleaseError(f"{path} has unknown component {component!r}")
            if impact not in IMPACTS:
                raise ReleaseError(f"{path} has invalid impact {impact!r}")
        result.append(Changeset(path, summary.strip(), dict(components)))
    return result


def release_plan(versions: dict, changesets: list[Changeset]) -> dict:
    impacts: dict[str, str] = {}
    for changeset in changesets:
        for component, impact in changeset.components.items():
            current = impacts.get(component)
            if current is None or IMPACTS[impact] > IMPACTS[current]:
                impacts[component] = impact
    if not impacts:
        return {
            "has_release": False,
            "marketplace": versions["marketplace"],
            "plugins": dict(versions["plugins"]),
            "impacts": {},
            "summaries": [item.summary for item in changesets],
        }
    highest = max(impacts.values(), key=IMPACTS.__getitem__)
    plugins = dict(versions["plugins"])
    for plugin, version in list(plugins.items()):
        if plugin in impacts:
            plugins[plugin] = bump(version, impacts[plugin])
    return {
        "has_release": True,
        "marketplace": bump(versions["marketplace"], highest),
        "plugins": plugins,
        "impacts": impacts,
        "summaries": [item.summary for item in changesets],
    }


def channel_source(host: str, plugin: str) -> str | dict:
    """Resolve a plugin inside the selected marketplace checkout.

    The marketplace ref is the release-channel boundary. Relative sources keep
    catalog and package content on that same ref for main, stable, and tags.
    """
    try:
        adapter = build_distributions.load_adapters(
            Path(__file__).resolve().parent.parent
        )[host]
    except (KeyError, ValueError) as exc:
        raise ReleaseError(f"unknown marketplace host: {host!r}") from exc
    resolver = getattr(adapter.module, "channel_source", None)
    if not callable(resolver):
        raise ReleaseError(f"{host} does not provide a marketplace channel source")
    return resolver(plugin)


def catalog_adapters(
    root: Path,
    adapters: dict[str, build_distributions.HostAdapter] | None = None,
) -> dict[str, build_distributions.HostAdapter]:
    adapters = adapters or build_distributions.load_adapters(root)
    result = {}
    for host, adapter in adapters.items():
        if not adapter.metadata.get("marketplace_catalog"):
            continue
        required = (
            "marketplace_catalog_path", "sync_catalog_entry", "sync_catalog_metadata",
            "catalog_component_version", "channel_source",
        )
        missing = [name for name in required if not callable(getattr(adapter.module, name, None))]
        if missing:
            raise ReleaseError(f"{host} catalog adapter lacks: {', '.join(missing)}")
        result[host] = adapter
    return result


def sync_version_surfaces(root: Path, versions: dict) -> None:
    adapters = build_distributions.load_adapters(root)
    for host, adapter in catalog_adapters(root).items():
        marketplace_path = adapter.module.marketplace_catalog_path(root)
        marketplace = read_json(marketplace_path)
        entries = {entry.get("name"): entry for entry in marketplace.get("plugins", [])}
        if set(entries) != set(versions["plugins"]):
            raise ReleaseError(
                f"{host} marketplace plugin registry differs from versions.json"
            )
        adapter.module.sync_catalog_metadata(marketplace, versions["marketplace"])
        for plugin, version in versions["plugins"].items():
            adapter.module.sync_catalog_entry(entries[plugin], plugin, version)
        write_json(marketplace_path, marketplace)
    for plugin, version in versions["plugins"].items():
        for host, adapter in adapters.items():
            if adapter.metadata.get("artifact_kind") != "native_marketplace":
                continue
            manifest_path = root / "platforms" / host / plugin / "manifest.json"
            manifest = read_json(manifest_path)
            if manifest.get("name") != plugin:
                raise ReleaseError(f"manifest identity mismatch: {manifest_path}")
            manifest["version"] = version
            write_json(manifest_path, manifest)



def validate_version_surfaces(
    root: Path,
    adapters: dict[str, build_distributions.HostAdapter] | None = None,
) -> list[str]:
    problems: list[str] = []
    try:
        versions = load_versions(root)
        changesets = load_changesets(root, versions)
        release_plan(versions, changesets)
        adapters = adapters or build_distributions.load_adapters(root)
    except ReleaseError as exc:
        return [str(exc)]
    try:
        catalogs = {
            host: read_json(adapter.module.marketplace_catalog_path(root))
            for host, adapter in catalog_adapters(root, adapters).items()
        }
    except ReleaseError as exc:
        return [str(exc)]
    catalog_entries = {
        host: {entry.get("name"): entry for entry in catalog.get("plugins", [])}
        for host, catalog in catalogs.items()
    }
    for plugin, expected in versions["plugins"].items():
        surfaces: list[tuple[str, str]] = []
        for host, adapter in catalog_adapters(root, adapters).items():
            catalog_entry = catalog_entries[host].get(plugin, {})
            value = adapter.module.catalog_component_version(catalog_entry)
            if value is not None:
                surfaces.append((f"{host} marketplace", value))
        for host in adapters:
            manifests = (
                root / "platforms" / host / plugin / "manifest.json",
                root / "dist" / host / plugin / f".{host}-plugin" / "plugin.json",
            )
            for manifest in manifests:
                try:
                    surfaces.append((str(manifest.relative_to(root)), read_json(manifest).get("version", "")))
                except ReleaseError as exc:
                    problems.append(str(exc))
        for label, actual in surfaces:
            if actual != expected:
                problems.append(
                    f"{plugin} version drift at {label}: expected {expected}, got {actual or '<missing>'}"
                )
        for host, catalog in catalog_entries.items():
            source = catalog.get(plugin, {}).get("source")
            expected_source = adapters[host].module.channel_source(plugin)
            if source != expected_source:
                problems.append(
                    f"{plugin} {host} marketplace source must stay inside the selected channel"
                )
    return problems


def git(
    root: Path, *args: str, environment: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise ReleaseError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def git_ok(
    root: Path, *args: str, environment: dict[str, str] | None = None,
) -> bool:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False,
        env=environment,
    )
    return completed.returncode == 0


def hermetic_git_environment() -> dict[str, str]:
    """Isolate provenance checks from caller Git policy and replacement refs."""
    environment = os.environ.copy()
    repository_overrides = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_SOURCE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_TEMPLATE_DIR",
        "GIT_WORK_TREE",
    }
    for name in tuple(environment):
        if name in repository_overrides \
                or name == "GIT_CONFIG_PARAMETERS" \
                or name.startswith("GIT_CONFIG_KEY_") \
                or name.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(name)
    environment.update({
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
    })
    return environment


def reject_graph_overlays(root: Path, environment: dict[str, str]) -> None:
    """Reject legacy graph overlays that can rewrite exact commit ancestry."""
    shallow = git(
        root, "rev-parse", "--is-shallow-repository", environment=environment,
    )
    if shallow != "false":
        raise ReleaseError("release PR verification requires complete Git history")
    graft_value = git(
        root, "rev-parse", "--git-path", "info/grafts", environment=environment,
    )
    graft_path = Path(graft_value)
    if not graft_path.is_absolute():
        graft_path = root / graft_path
    try:
        grafts = graft_path.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ReleaseError("release PR verification cannot inspect Git grafts") from exc
    if grafts.strip():
        raise ReleaseError("release PR verification rejects Git graft overlays")


FINALIZE_BRANCH_RE = re.compile(r"^codex/[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_FINALIZE_BRANCH_CHARS = 120


def validate_finalize_branch(branch: str) -> None:
    if branch == "release/stable":
        return
    if len(branch) > MAX_FINALIZE_BRANCH_CHARS \
            or FINALIZE_BRANCH_RE.fullmatch(branch) is None:
        raise ReleaseError(
            "release cleanup branch must be release/stable or a bounded "
            f"codex/<kebab-name> branch, got {branch!r}"
        )


def worktree_branch_locations(root: Path) -> dict[str, Path]:
    locations: dict[str, Path] = {}
    worktree = None
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree ")).resolve()
        elif line.startswith("branch refs/heads/") and worktree is not None:
            branch = line.removeprefix("branch refs/heads/")
            locations[branch] = worktree
    return locations


def remote_release_branch_refs(root: Path) -> dict[str, str | None]:
    refs = {
        "refs/heads/main": None,
        "refs/heads/release/stable": None,
    }
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", *refs],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReleaseError(
            completed.stderr.strip() or "remote release branch observation failed"
        )
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in refs:
            raise ReleaseError("remote release branch observation was invalid")
        require_sha(fields[0], f"remote object ID for {fields[1]}")
        if refs[fields[1]] is not None:
            raise ReleaseError("remote release branch observation was ambiguous")
        refs[fields[1]] = fields[0]
    if refs["refs/heads/main"] is None:
        raise ReleaseError("remote main is missing")
    return {
        "main": refs["refs/heads/main"],
        "release_stable": refs["refs/heads/release/stable"],
    }


def publish_release_branch(
    root: Path,
    main_sha: str,
    release_sha: str,
    *,
    after_push: Callable[[], None] | None = None,
) -> dict:
    """Create release/stable only for an exact observed main candidate.

    Git omits a no-op main refspec from the receive-pack transaction, so a
    lease on that ref is not a server-side compare-and-swap. The operation
    therefore re-observes both refs after the exact-absence branch push and
    exact-lease deletes only the branch it just created if main raced.
    """
    main_sha = require_sha(main_sha, "release branch main SHA")
    release_sha = require_sha(release_sha, "release branch commit SHA")
    parents = git(root, "rev-list", "--parents", "-n", "1", release_sha).split()
    if parents != [release_sha, main_sha]:
        raise ReleaseError(
            "release branch commit must be exactly one commit on the main candidate"
        )
    before = remote_release_branch_refs(root)
    if before["main"] != main_sha:
        raise ReleaseError(
            "remote main advanced before release branch publication"
        )
    if before["release_stable"] is not None:
        raise ReleaseError("remote release/stable already exists")

    pushed = subprocess.run(
        [
            "git", "push",
            "--force-with-lease=refs/heads/release/stable:",
            "origin", f"{release_sha}:refs/heads/release/stable",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if after_push is not None:
        after_push()
    observed = remote_release_branch_refs(root)
    if observed["release_stable"] != release_sha:
        detail = pushed.stderr.strip() or pushed.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise ReleaseError(
            "release/stable was not created at the exact release commit" + suffix
        )
    if observed["main"] != main_sha:
        deleted = subprocess.run(
            [
                "git", "push",
                f"--force-with-lease=refs/heads/release/stable:{release_sha}",
                "origin", ":refs/heads/release/stable",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        final = remote_release_branch_refs(root)
        if final["release_stable"] is not None:
            detail = deleted.stderr.strip() or deleted.stdout.strip()
            suffix = f": {detail}" if detail else ""
            raise ReleaseError(
                "main advanced and exact release branch rollback failed" + suffix
            )
        raise ReleaseError(
            "remote main advanced during release branch publication; "
            "the exact stale release branch was rolled back"
        )
    return {
        "schema_version": 1,
        "action": "reconciled" if pushed.returncode else "created",
        "main": main_sha,
        "release_stable": release_sha,
    }


def release_ref_audit(root: Path, version: str) -> dict:
    parse_semver(version, "release version")
    main_sha = git(root, "rev-parse", "refs/remotes/origin/main")
    stable_sha = git(root, "rev-parse", "refs/remotes/origin/stable")
    if git(root, "cat-file", "-t", f"refs/tags/v{version}") != "tag":
        raise ReleaseError(f"v{version} must be an annotated tag")
    tag_sha = git(root, "rev-list", "-n", "1", f"refs/tags/v{version}")
    if stable_sha != tag_sha:
        raise ReleaseError(
            "release refs differ: "
            f"origin/main={main_sha}, origin/stable={stable_sha}, v{version}={tag_sha}"
        )
    if not git_ok(
        root, "merge-base", "--is-ancestor", stable_sha,
        "refs/remotes/origin/main",
    ):
        raise ReleaseError(
            "published stable release is not an ancestor of origin/main"
        )
    metadata = json_at_ref(root, stable_sha, ".release/stable.json")
    metadata_present = git_ok(
        root, "cat-file", "-e", f"{stable_sha}:.release/stable.json"
    )
    if version == BOOTSTRAP_VERSION:
        if metadata_present:
            raise ReleaseError(
                "published bootstrap must not carry release metadata"
            )
        bootstrap_versions = json_at_ref(root, stable_sha, "versions.json")
        plugins = (
            bootstrap_versions.get("plugins")
            if isinstance(bootstrap_versions, dict) else None
        )
        if (
            not isinstance(bootstrap_versions, dict)
            or set(bootstrap_versions) != {
                "schema_version", "marketplace", "plugins"
            }
            or bootstrap_versions.get("schema_version") != 1
            or bootstrap_versions.get("marketplace") != BOOTSTRAP_VERSION
            or not isinstance(plugins, dict)
            or not plugins
            or any(value != BOOTSTRAP_VERSION for value in plugins.values())
        ):
            raise ReleaseError(
                "published bootstrap versions do not attest v0.0.1"
            )
    elif not metadata_present or not isinstance(metadata, dict) \
            or metadata.get("version") != version:
        raise ReleaseError(
            f"published stable release metadata does not attest v{version}"
        )
    if git_ok(root, "show-ref", "--verify", "--quiet", "refs/remotes/origin/release/stable"):
        raise ReleaseError(
            "origin/release/stable still exists; the publish workflow is incomplete"
        )
    return {
        "version": version,
        "commit": stable_sha,
        "main": main_sha,
        "stable": stable_sha,
        "tag": tag_sha,
    }


def finalize_local_release(
    root: Path, version: str, branches: list[str], apply: bool = False,
) -> dict:
    repository_root = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if repository_root != root.resolve():
        raise ReleaseError(
            f"release cleanup root must be the Git toplevel: {repository_root}"
        )
    if git(root, "status", "--porcelain"):
        raise ReleaseError("release cleanup requires a clean worktree")
    if len(branches) != len(set(branches)):
        raise ReleaseError("release cleanup branches must be unique")
    for branch in branches:
        validate_finalize_branch(branch)

    if apply:
        git(root, "fetch", "origin", "--prune", "--tags")
    audit = release_ref_audit(root, version)

    worktrees = worktree_branch_locations(root)
    for branch in {"main", "stable", *branches}:
        location = worktrees.get(branch)
        if location is not None and location != root.resolve():
            raise ReleaseError(
                f"release cleanup branch {branch!r} is checked out at {location}"
            )
    if not git_ok(root, "show-ref", "--verify", "--quiet", "refs/heads/main"):
        raise ReleaseError("local main branch is missing")
    if not git_ok(
        root,
        "merge-base",
        "--is-ancestor",
        "refs/heads/main",
        "refs/remotes/origin/main",
    ):
        raise ReleaseError("local main cannot fast-forward to the published release")
    if git_ok(root, "show-ref", "--verify", "--quiet", "refs/heads/stable") \
            and not git_ok(
                root,
                "merge-base",
                "--is-ancestor",
                "refs/heads/stable",
                "refs/remotes/origin/stable",
            ):
        raise ReleaseError("local stable has commits outside the published release")

    selected: list[dict[str, object]] = []
    for branch in branches:
        local_ref = f"refs/heads/{branch}"
        remote_ref = f"refs/remotes/origin/{branch}"
        local_exists = git_ok(root, "show-ref", "--verify", "--quiet", local_ref)
        remote_exists = git_ok(root, "show-ref", "--verify", "--quiet", remote_ref)
        for label, ref, exists in (
            ("local", local_ref, local_exists),
            ("remote", remote_ref, remote_exists),
        ):
            if exists and not git_ok(
                root, "merge-base", "--is-ancestor", ref, "refs/remotes/origin/main"
            ):
                raise ReleaseError(
                    f"refusing to delete unmerged {label} branch {branch!r}"
                )
        if branch == "release/stable" and remote_exists:
            raise ReleaseError(
                "origin/release/stable still exists; publish must delete it"
            )
        selected.append({
            "branch": branch,
            "local": local_exists,
            "remote": remote_exists,
        })

    result = {
        "schema_version": 1,
        "apply": apply,
        "release": audit,
        "branches": selected,
        "final_branch": "main",
        "worktree": "clean",
    }
    if not apply:
        return result

    current = git(root, "branch", "--show-current")
    if current != "main":
        git(root, "switch", "main")
    git(root, "merge", "--ff-only", "refs/remotes/origin/main")

    for item in selected:
        branch = str(item["branch"])
        if item["remote"]:
            git(root, "push", "origin", "--delete", branch)
    git(root, "fetch", "origin", "--prune", "--tags")
    for item in selected:
        branch = str(item["branch"])
        if item["local"]:
            git(root, "branch", "-d", branch)
    git(root, "branch", "-f", "stable", "refs/remotes/origin/stable")

    final_audit = release_ref_audit(root, version)
    if git(root, "branch", "--show-current") != "main":
        raise ReleaseError("release cleanup did not finish on main")
    if git(root, "rev-parse", "refs/heads/main") != final_audit["main"]:
        raise ReleaseError("local main does not match origin/main")
    if git(root, "rev-parse", "refs/heads/stable") != final_audit["commit"]:
        raise ReleaseError("local stable does not match the published release")
    if git(root, "status", "--porcelain"):
        raise ReleaseError("release cleanup left a dirty worktree")
    for item in selected:
        branch = str(item["branch"])
        if git_ok(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            raise ReleaseError(f"local cleanup branch remains: {branch}")
        if git_ok(
            root,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{branch}",
        ):
            raise ReleaseError(f"remote cleanup branch remains: {branch}")
    result["release"] = final_audit
    return result


def build_identity(root: Path, sha: str = "HEAD") -> dict:
    full_sha = git(root, "rev-parse", sha)
    count = git(root, "rev-list", "--count", "--first-parent", full_sha)
    return {
        "schema_version": 1,
        "build_id": f"main.{count}.g{full_sha[:7]}",
        "commit": full_sha,
        "first_parent_count": int(count),
        "stable_versions": load_versions(root),
    }


def changed_paths(root: Path, base: str) -> list[tuple[str, str]]:
    output = git(root, "diff", "--name-status", f"{base}...HEAD")
    result: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            result.append((fields[0], fields[-1]))
    return result


def json_at_ref(root: Path, ref: str, path: str) -> dict | None:
    """Read a JSON object from Git, returning None when the ref/path is absent."""
    try:
        raw = git(root, "show", f"{ref}:{path}")
        value = json.loads(raw)
    except (ReleaseError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def retirement_registry_delta(
    base_versions: dict | None, current_versions: dict,
) -> set[str]:
    """Return safely retired plugins, or an empty set for any version edit."""
    if not isinstance(base_versions, dict):
        return set()
    base_plugins = base_versions.get("plugins")
    current_plugins = current_versions.get("plugins")
    if not isinstance(base_plugins, dict) or not isinstance(current_plugins, dict):
        return set()
    retired = set(base_plugins) - set(current_plugins)
    if not retired or set(current_plugins) - set(base_plugins):
        return set()
    if base_versions.get("schema_version") != current_versions.get("schema_version"):
        return set()
    if base_versions.get("marketplace") != current_versions.get("marketplace"):
        return set()
    if any(base_plugins[name] != version for name, version in current_plugins.items()):
        return set()
    return retired


def stable_retirement_cleanup(
    base_metadata: dict | None, current_metadata: dict | None,
    retired: set[str],
) -> bool:
    """Allow only deletion of retired plugin keys from historical impacts."""
    if not retired or not isinstance(base_metadata, dict) \
            or not isinstance(current_metadata, dict):
        return False
    expected = json.loads(json.dumps(base_metadata))
    impacts = expected.get("impacts")
    if not isinstance(impacts, dict):
        return False
    for plugin in retired:
        impacts.pop(plugin, None)
    return expected == current_metadata


def check_pr_changeset(root: Path, base: str) -> None:
    changed = changed_paths(root, base)
    added = [path for status, path in changed if status == "A" and path.startswith(".changes/") and path.endswith(".json")]
    versions = load_versions(root)
    selected = [item for item in load_changesets(root, versions) if item.path.relative_to(root).as_posix() in added]
    declared = {component for item in selected for component in item.components}
    required: set[str] = set()
    adapters = build_distributions.load_adapters(root)
    _, provenance_name = build_distributions.packaging_names(root)
    for _status, path in changed:
        parts = Path(path).parts
        if len(parts) >= 2 and parts[0] == "plugins" and parts[1] in versions["plugins"]:
            required.add(parts[1])
        if len(parts) >= 3 and parts[0] == "platforms" and parts[1] in adapters and parts[2] in versions["plugins"]:
            required.add(parts[2])
        if len(parts) >= 3 and parts[0] == "dist" and parts[1] in adapters and parts[2] in versions["plugins"]:
            derived_provenance = len(parts) == 4 and parts[3] == provenance_name
            if not derived_provenance:
                required.add(parts[2])
        if path in {".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"}:
            required.add(MARKETPLACE_COMPONENT)
    if not added:
        raise ReleaseError("every normal pull request must add a .changes/*.json file")
    retired: set[str] = set()
    registry_retirement = False
    stable_retirement = False
    if any(path == "versions.json" for _status, path in changed):
        retired = retirement_registry_delta(
            json_at_ref(root, base, "versions.json"), versions
        )
        registry_retirement = bool(retired)
        if any(path == ".release/stable.json" for _status, path in changed):
            stable_retirement = stable_retirement_cleanup(
                json_at_ref(root, base, ".release/stable.json"),
                read_json(root / ".release" / "stable.json")
                if (root / ".release" / "stable.json").is_file() else None,
                retired,
            )
    protected = {
        path for status, path in changed
        if path in {"versions.json", "CHANGELOG.md", ".release/stable.json"}
        and not (path == "versions.json" and registry_retirement)
        and not (path == ".release/stable.json" and stable_retirement)
    }
    changed_existing_changesets = {
        path for status, path in changed
        if path.startswith(".changes/") and path.endswith(".json")
        and status != "A"
    }
    if protected:
        raise ReleaseError(
            "normal pull requests cannot edit release-owned files: "
            + ", ".join(sorted(protected))
        )
    if changed_existing_changesets:
        raise ReleaseError(
            "normal pull requests cannot modify or delete existing changesets: "
            + ", ".join(sorted(changed_existing_changesets))
        )
    missing = required - declared
    if missing:
        raise ReleaseError(
            "changeset omits changed release components: " + ", ".join(sorted(missing))
        )


def append_changelog(root: Path, plan: dict) -> None:
    path = root / "CHANGELOG.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Changelog\n"
    lines = ["", f"## {plan['marketplace']}", ""]
    for summary in plan["summaries"]:
        lines.append(f"- {summary}")
    path.write_bytes(
        (existing.rstrip() + "\n" + "\n".join(lines) + "\n").encode("utf-8")
    )


def changeset_paths_at_ref(
    root: Path, ref: str, *, environment: dict[str, str] | None = None,
) -> set[str]:
    output = git(
        root, "ls-tree", "-r", "--name-only", ref, "--", ".changes",
        environment=environment,
    )
    return {line for line in output.splitlines() if line.endswith(".json")}


def prepare(
    root: Path, stable_sha: str, main_sha: str,
    released_paths: set[str] | None = None,
) -> dict:
    versions = load_versions(root)
    changesets = load_changesets(root, versions)
    released_paths = released_paths or set()
    pending = [
        item for item in changesets
        if item.path.relative_to(root).as_posix() not in released_paths
    ]
    plan = release_plan(versions, pending)
    if not plan["has_release"]:
        raise ReleaseError("no pending stable release impact")
    next_versions = {
        "schema_version": 1,
        "marketplace": plan["marketplace"],
        "plugins": plan["plugins"],
    }
    write_json(root / "versions.json", next_versions)
    sync_version_surfaces(root, next_versions)
    append_changelog(root, plan)
    for changeset in changesets:
        changeset.path.unlink()
    metadata = {
        "schema_version": 1,
        "version": plan["marketplace"],
        "stable_base": stable_sha,
        "main_source": main_sha,
        "impacts": plan["impacts"],
        "summaries": plan["summaries"],
    }
    write_json(root / ".release" / "stable.json", metadata)
    return metadata


def verify_release(root: Path, version: str | None = None) -> dict:
    problems = validate_version_surfaces(root)
    if problems:
        raise ReleaseError("; ".join(problems))
    metadata = read_json(root / ".release" / "stable.json")
    versions = load_versions(root)
    expected = version or versions["marketplace"]
    if metadata.get("version") != expected or versions["marketplace"] != expected:
        raise ReleaseError("release metadata, requested tag, and marketplace version differ")
    parse_semver(expected, "release version")
    return metadata


def verify_release_pr(
    root: Path, *, base_sha: str, head_sha: str, stable_sha: str,
) -> dict:
    """Prove that a release PR is the deterministic output of trusted main.

    The repository must be checked out at ``base_sha``. Candidate Git data is
    inspected by object ID but never checked out or executed. Release tooling
    is replayed from the trusted base in a disposable clone and the resulting
    tree is compared byte-for-byte with the candidate commit.
    """
    git_environment = hermetic_git_environment()
    base_sha = require_sha(base_sha, "release PR base")
    head_sha = require_sha(head_sha, "release PR head")
    stable_sha = require_sha(stable_sha, "stable base")
    reject_graph_overlays(root, git_environment)
    if git(root, "rev-parse", "HEAD", environment=git_environment) != base_sha:
        raise ReleaseError("release PR verification must run from the exact base SHA")
    if git(root, "status", "--porcelain", environment=git_environment):
        raise ReleaseError("release PR verification requires a clean base worktree")
    parents = git(
        root, "rev-list", "--parents", "-n", "1", head_sha,
        environment=git_environment,
    ).split()
    if parents != [head_sha, base_sha]:
        raise ReleaseError(
            "release PR head must be exactly one non-merge commit on the current base"
        )
    if not git_ok(
        root, "merge-base", "--is-ancestor", stable_sha, base_sha,
        environment=git_environment,
    ):
        raise ReleaseError("stable base must be an ancestor of the release PR base")

    raw_metadata = git(
        root, "show", f"{head_sha}:.release/stable.json",
        environment=git_environment,
    )
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise ReleaseError("release PR metadata is invalid JSON") from exc
    expected_keys = {
        "schema_version", "version", "stable_base", "main_source",
        "impacts", "summaries",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_keys:
        raise ReleaseError("release PR metadata has unknown or missing keys")
    if metadata.get("schema_version") != 1:
        raise ReleaseError("release PR metadata schema_version must be 1")
    version = metadata.get("version")
    if not isinstance(version, str):
        raise ReleaseError("release PR metadata version must be a string")
    parse_semver(version, "release PR version")
    if metadata.get("main_source") != base_sha:
        raise ReleaseError("release PR main_source differs from the current base")
    if metadata.get("stable_base") != stable_sha:
        raise ReleaseError("release PR stable_base differs from the stable ref")
    impacts = metadata.get("impacts")
    summaries = metadata.get("summaries")
    if not isinstance(impacts, dict) or any(
        not isinstance(component, str) or impact not in IMPACTS
        for component, impact in impacts.items()
    ):
        raise ReleaseError("release PR impacts are invalid")
    if not isinstance(summaries, list) or any(
        not isinstance(summary, str) or not summary.strip()
        for summary in summaries
    ):
        raise ReleaseError("release PR summaries are invalid")

    expected_message = f"chore: prepare stable v{version}"
    if git(
        root, "show", "-s", "--format=%B", head_sha,
        environment=git_environment,
    ) != expected_message:
        raise ReleaseError("release PR commit message differs from the release contract")

    with tempfile.TemporaryDirectory(prefix="release-pr-verify.") as temporary:
        expected_root = Path(temporary) / "expected"
        completed = subprocess.run(
            ["git", "clone", "--shared", "--no-checkout", str(root), str(expected_root)],
            capture_output=True,
            text=True,
            check=False,
            env=git_environment,
        )
        if completed.returncode != 0:
            raise ReleaseError(completed.stderr.strip() or "release replay clone failed")
        git(
            expected_root, "config", "core.autocrlf", "false",
            environment=git_environment,
        )
        git(
            expected_root, "config", "core.eol", "lf",
            environment=git_environment,
        )
        git(
            expected_root, "config", "core.filemode", "false",
            environment=git_environment,
        )
        git(
            expected_root, "checkout", "--detach", base_sha,
            environment=git_environment,
        )
        released_paths = changeset_paths_at_ref(
            root, stable_sha, environment=git_environment,
        )
        replay = prepare(
            expected_root,
            stable_sha,
            base_sha,
            released_paths=released_paths,
        )
        build_distributions.replace_generated(expected_root, expected_root / "dist")
        verify_release(expected_root, replay["version"])
        if replay != metadata:
            raise ReleaseError("release PR metadata differs from deterministic replay")
        git(expected_root, "add", "--all", environment=git_environment)
        expected_tree = git(
            expected_root, "write-tree", environment=git_environment,
        )
    head_tree = git(
        root, "rev-parse", f"{head_sha}^{{tree}}",
        environment=git_environment,
    )
    if head_tree != expected_tree:
        raise ReleaseError("release PR tree differs from deterministic replay")
    return {
        "schema_version": 1,
        "base": base_sha,
        "head": head_sha,
        "stable": stable_sha,
        "version": version,
        "expected_tree": expected_tree,
        "head_tree": head_tree,
    }


def verify_bootstrap(
    root: Path,
    adapters: dict[str, build_distributions.HostAdapter] | None = None,
) -> dict:
    problems = validate_version_surfaces(root, adapters)
    if problems:
        raise ReleaseError("; ".join(problems))
    versions = load_versions(root)
    if versions["marketplace"] != BOOTSTRAP_VERSION or any(
        value != BOOTSTRAP_VERSION
        for value in versions["plugins"].values()
    ):
        raise ReleaseError(
            f"the first stable release must use {BOOTSTRAP_VERSION} everywhere"
        )
    if (root / ".release" / "stable.json").exists():
        raise ReleaseError(
            "the first stable release cannot carry prior stable provenance"
        )
    impactful = [
        item.path.name for item in load_changesets(root, versions)
        if item.components
    ]
    if impactful:
        raise ReleaseError(
            "the first stable release cannot strand release-impact changesets: "
            + ", ".join(impactful)
        )
    return versions


def verify_bootstrap_candidate(
    root: Path, candidate_sha: str, main_sha: str,
) -> dict:
    """Validate a staged bootstrap ancestor using only trusted current code."""
    environment = hermetic_git_environment()
    candidate_sha = require_sha(candidate_sha, "bootstrap candidate")
    main_sha = require_sha(main_sha, "bootstrap main")
    reject_graph_overlays(root, environment)
    if git(root, "rev-parse", "HEAD", environment=environment) != main_sha:
        raise ReleaseError(
            "bootstrap candidate verification must run from the exact main SHA"
        )
    if git(root, "status", "--porcelain", environment=environment):
        raise ReleaseError("bootstrap candidate verification requires a clean worktree")
    if not git_ok(
        root, "merge-base", "--is-ancestor", candidate_sha, main_sha,
        environment=environment,
    ):
        raise ReleaseError("bootstrap candidate must be an ancestor of current main")
    trusted_adapters = build_distributions.load_adapters(root)
    with tempfile.TemporaryDirectory(prefix="bootstrap-candidate-verify.") as temporary:
        temporary_root = Path(temporary)
        candidate_root = temporary_root / "candidate"
        expected_dist = temporary_root / "expected-dist"
        completed = subprocess.run(
            [
                "git", "clone", "--no-checkout", "--local", "--no-hardlinks",
                str(root), str(candidate_root),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            raise ReleaseError(
                completed.stderr.strip() or "bootstrap candidate clone failed"
            )
        git(
            candidate_root, "config", "core.autocrlf", "false",
            environment=environment,
        )
        git(
            candidate_root, "config", "core.eol", "lf",
            environment=environment,
        )
        git(
            candidate_root, "config", "core.filemode", "false",
            environment=environment,
        )
        git(
            candidate_root, "checkout", "--detach", candidate_sha,
            environment=environment,
        )
        if git(
            candidate_root, "status", "--porcelain", environment=environment,
        ):
            raise ReleaseError("bootstrap candidate checkout is not canonical")
        try:
            candidate_versions = verify_bootstrap(
                candidate_root, adapters=trusted_adapters,
            )
            build_distributions.build(
                candidate_root, expected_dist, adapters=trusted_adapters,
            )
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"bootstrap candidate is invalid: {exc}") from exc
        problems = build_distributions.compare_dirs(
            expected_dist, candidate_root / "dist",
        )
        if problems:
            raise ReleaseError(
                "bootstrap candidate distribution differs from trusted replay: "
                + "; ".join(problems)
            )
        snapshot = build_distributions.marketplace_snapshot(candidate_root)
    return {
        "schema_version": 1,
        "candidate": candidate_sha,
        "main": main_sha,
        "version": candidate_versions["marketplace"],
        "build_id": snapshot["build_id"],
    }


def release_notes(root: Path, version: str) -> str:
    metadata_path = root / ".release" / "stable.json"
    if metadata_path.is_file():
        metadata = read_json(metadata_path)
        if metadata.get("version") == version:
            return "\n".join(f"- {item}" for item in metadata.get("summaries", [])) + "\n"
    if version == BOOTSTRAP_VERSION:
        return "- Establish the first stable Agent Marketplace baseline for all supported hosts.\n"
    raise ReleaseError(f"release notes unavailable for {version}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("plan")
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--write", action="store_true", required=True)
    build_parser = sub.add_parser("build-info")
    build_parser.add_argument("--sha", default="HEAD")
    build_parser.add_argument("--output", type=Path)
    pr_parser = sub.add_parser("check-pr")
    pr_parser.add_argument("--base", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--stable-sha", required=True)
    prepare_parser.add_argument("--main-sha", required=True)
    verify_parser = sub.add_parser("verify-release")
    verify_parser.add_argument("--version")
    release_pr_parser = sub.add_parser("verify-release-pr")
    release_pr_parser.add_argument("--base-sha", required=True)
    release_pr_parser.add_argument("--head-sha", required=True)
    release_pr_parser.add_argument("--stable-sha", required=True)
    branch_parser = sub.add_parser("publish-release-branch")
    branch_parser.add_argument("--main-sha", required=True)
    branch_parser.add_argument("--release-sha", required=True)
    sub.add_parser("verify-bootstrap")
    bootstrap_candidate_parser = sub.add_parser("verify-bootstrap-candidate")
    bootstrap_candidate_parser.add_argument("--candidate-sha", required=True)
    bootstrap_candidate_parser.add_argument("--main-sha", required=True)
    notes_parser = sub.add_parser("release-notes")
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--output", type=Path)
    finalize_parser = sub.add_parser("finalize-local")
    finalize_parser.add_argument("--version", required=True)
    finalize_parser.add_argument("--branch", action="append", default=[])
    finalize_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "validate":
        problems = validate_version_surfaces(root)
        if problems:
            raise ReleaseError("\n".join(problems))
        print("release: version and changeset contracts valid")
    elif args.command == "plan":
        print(json.dumps(release_plan(load_versions(root), load_changesets(root)), indent=2))
    elif args.command == "sync":
        sync_version_surfaces(root, load_versions(root))
    elif args.command == "build-info":
        result = build_identity(root, args.sha)
        payload = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_bytes(payload.encode("utf-8"))
        else:
            print(payload, end="")
    elif args.command == "check-pr":
        check_pr_changeset(root, args.base)
        print("release: pull request changeset valid")
    elif args.command == "prepare":
        released_paths = changeset_paths_at_ref(root, args.stable_sha)
        result = prepare(
            root, args.stable_sha, args.main_sha, released_paths=released_paths
        )
        print(json.dumps(result, indent=2))
    elif args.command == "verify-release":
        print(json.dumps(verify_release(root, args.version), indent=2))
    elif args.command == "verify-release-pr":
        print(json.dumps(verify_release_pr(
            root,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            stable_sha=args.stable_sha,
        ), indent=2))
    elif args.command == "publish-release-branch":
        print(json.dumps(publish_release_branch(
            root, args.main_sha, args.release_sha
        ), indent=2))
    elif args.command == "verify-bootstrap":
        print(json.dumps(verify_bootstrap(root), indent=2))
    elif args.command == "verify-bootstrap-candidate":
        print(json.dumps(verify_bootstrap_candidate(
            root, args.candidate_sha, args.main_sha,
        ), indent=2))
    elif args.command == "release-notes":
        notes = release_notes(root, args.version)
        if args.output:
            args.output.write_bytes(notes.encode("utf-8"))
        else:
            print(notes, end="")
    elif args.command == "finalize-local":
        print(json.dumps(finalize_local_release(
            root, args.version, args.branch, apply=args.apply
        ), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        raise SystemExit(f"release: {exc}") from exc
