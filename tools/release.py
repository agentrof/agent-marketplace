#!/usr/bin/env python3
"""Stable release and cross-host version tooling for Agent Marketplace.

A stable release is named YYYY.M.N: the UTC year and month its release commit
was made in and its number within that month, a strict SemVer X.Y.Z. A
release commit, the last commit of an ordinary pull request, consumes every
pending changeset and sets every version surface to that one version; a
release then tags that approved main commit and moves stable.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Container

import build_distributions


MARKETPLACE_COMPONENT = "agent-marketplace"
IMPACTS = {"patch": 1, "minor": 2, "major": 3}
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHANGESET_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
BOOTSTRAP_VERSION = "0.0.1"
BOOTSTRAP_NOTE = (
    "Establish the first stable Agent Marketplace baseline for all supported hosts."
)
RESET_MARKER = ".release/reset.json"
RESET_MARKER_KEYS = ("date", "reason", "retired_versions", "schema_version")
CHANGELOG_RELEASE_RE = re.compile(r"^## (\S+)[ \t\r]*$", re.MULTILINE)
ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STABLE_METADATA = ".release/stable.json"
METADATA_SCHEMA = 2
METADATA_KEYS = ("build_id", "impacts", "schema_version", "summaries", "version")
RELEASE_OWNED = ("CHANGELOG.md", STABLE_METADATA, "versions.json")
RELEASE_WORKFLOW = "release.yml"
VALIDATION_WORKFLOW = ".github/workflows/validate.yml"
# Main's push validation reuses its pull request's evidence in about a minute
# and runs the full suite in about fifteen. A release waits for it instead of
# testing the same tree again.
VALIDATION_WAIT_SECONDS = 1200
VALIDATION_APPEAR_SECONDS = 120
VALIDATION_POLL_SECONDS = 15
RUN_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/actions/runs/([0-9]+)")


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


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def release_instant(when: datetime.datetime) -> datetime.datetime:
    """``when`` in UTC and whole seconds, the precision of a commit date."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ReleaseError("a release date must carry its timezone")
    return when.astimezone(datetime.timezone.utc).replace(microsecond=0)


def next_version(latest: str, when: datetime.datetime) -> str:
    """Name the release that follows ``latest`` when bump runs at ``when``.

    The name is YYYY.M.N for the UTC year and month of ``when``. N counts on
    from ``latest`` when it names that same month and starts at 1 otherwise,
    a version of an earlier numbering such as 0.0.3 included. A ``latest``
    of a later month can only come from a wrong clock.
    """
    year, month, number = parse_semver(latest, "latest release")
    current = release_instant(when)
    if (year, month) == (current.year, current.month):
        return f"{year}.{month}.{number + 1}"
    if (year, month) > (current.year, current.month):
        raise ReleaseError(
            f"the latest release {latest} is newer than {current:%Y-%m}, the UTC"
            " month of the release clock; check the clock"
        )
    return f"{current.year}.{current.month}.1"


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


def release_plan(
    versions: dict, changesets: list[Changeset], when: datetime.datetime,
) -> dict:
    """Plan the release commit bump makes at ``when``.

    Impacts no longer choose the number: the marketplace and every plugin
    take the one calendar version next_version names. A release still needs
    a changeset that declares an impact; the highest impact of each
    component is recorded, and every summary is kept.
    """
    impacts: dict[str, str] = {}
    for changeset in changesets:
        for component, impact in changeset.components.items():
            current = impacts.get(component)
            if current is None or IMPACTS[impact] > IMPACTS[current]:
                impacts[component] = impact
    summaries = [item.summary for item in changesets]
    if not impacts:
        return {
            "has_release": False,
            "marketplace": versions["marketplace"],
            "plugins": dict(versions["plugins"]),
            "impacts": {},
            "summaries": summaries,
        }
    version = next_version(versions["marketplace"], when)
    return {
        "has_release": True,
        "marketplace": version,
        "plugins": {plugin: version for plugin in versions["plugins"]},
        "impacts": impacts,
        "summaries": summaries,
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
        load_changesets(root, versions)
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


def git_text(
    root: Path, *args: str, environment: dict[str, str] | None = None,
) -> str:
    """What git prints, decoded without failing on a byte that is not UTF-8."""
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ReleaseError(detail or "git command failed")
    return completed.stdout.decode("utf-8", "replace")


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
        raise ReleaseError("release verification requires complete Git history")
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
        raise ReleaseError("release verification cannot inspect Git grafts") from exc
    if grafts.strip():
        raise ReleaseError("release verification rejects Git graft overlays")


FINALIZE_BRANCH_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_FINALIZE_BRANCH_CHARS = 120


def finalize_branch_prefixes() -> tuple[str, ...]:
    """Return the feature-branch prefix each registered host adapter declares."""
    adapters = build_distributions.load_adapters(
        Path(__file__).resolve().parent.parent
    )
    return tuple(
        adapter.metadata["feature_branch_prefix"] for adapter in adapters.values()
    )


def validate_finalize_branch(branch: str) -> None:
    prefixes = finalize_branch_prefixes()
    bounded = len(branch) <= MAX_FINALIZE_BRANCH_CHARS and any(
        branch.startswith(prefix)
        and FINALIZE_BRANCH_NAME_RE.fullmatch(branch[len(prefix):]) is not None
        for prefix in prefixes
    )
    if not bounded:
        forms = " or ".join(f"{prefix}<kebab-name>" for prefix in prefixes)
        raise ReleaseError(
            f"release cleanup branch must be a bounded {forms} branch, "
            f"got {branch!r}"
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
    versions = json_at_ref(root, stable_sha, "versions.json")
    if not isinstance(versions, dict) or versions.get("marketplace") != version:
        raise ReleaseError(
            f"published stable release versions.json does not name v{version}"
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


def blob_at_ref(root: Path, ref: str, path: str) -> bytes | None:
    """Return a file's exact bytes at a Git ref, or None when it is absent."""
    completed = subprocess.run(
        ["git", "cat-file", "blob", f"{ref}:{path}"],
        cwd=root, capture_output=True, check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def bootstrap_changelog() -> str:
    """Return the CHANGELOG.md of the first stable baseline: its note alone."""
    return f"# Changelog\n\n## {BOOTSTRAP_VERSION}\n\n- {BOOTSTRAP_NOTE}\n"


def changelog_releases(text: str) -> list[str]:
    """Return the versions a CHANGELOG.md released, in file order."""
    return [
        version for version in CHANGELOG_RELEASE_RE.findall(text)
        if SEMVER_RE.fullmatch(version)
    ]


def read_reset_marker(root: Path) -> dict:
    marker = read_json(root / RESET_MARKER)
    if set(marker) != set(RESET_MARKER_KEYS):
        raise ReleaseError(
            f"{RESET_MARKER} must contain only "
            + ", ".join(RESET_MARKER_KEYS[:-1]) + f" and {RESET_MARKER_KEYS[-1]}"
        )
    schema_version = marker["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ReleaseError(f"{RESET_MARKER} schema_version must be 1")
    reason = marker["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ReleaseError(f"{RESET_MARKER} reason must be a non-empty string")
    date = marker["date"]
    try:
        valid_date = isinstance(date, str) and ISO_DATE_RE.fullmatch(date) \
            and datetime.date.fromisoformat(date)
    except ValueError:
        valid_date = False
    if not valid_date:
        raise ReleaseError(f"{RESET_MARKER} date must be a calendar date YYYY-MM-DD")
    retired = marker["retired_versions"]
    if not isinstance(retired, list) or not retired \
            or not all(isinstance(version, str) for version in retired):
        raise ReleaseError(
            f"{RESET_MARKER} retired_versions must be a non-empty list of versions"
        )
    parsed = [parse_semver(version, "retired version") for version in retired]
    if parsed != sorted(set(parsed)):
        raise ReleaseError(
            f"{RESET_MARKER} retired_versions must be unique and ascending"
        )
    return marker


def pull_request_fork(root: Path, base: str) -> str:
    """Return where the pull request left ``base``, where its diff starts.

    The three-dot diff already needs this merge base, so ``base`` stands in
    only where no repository answers.
    """
    completed = subprocess.run(
        ["git", "merge-base", base, "HEAD"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    fork = completed.stdout.strip()
    return fork if completed.returncode == 0 and fork else base


def reset_marker_changed(root: Path, fork: str) -> bool:
    """Tell whether a pull request adds, edits, renames or deletes the marker.

    ``fork`` is where the pull request left its base, the commit its diff
    starts from, so a branch behind a base that gained the marker is not
    taken for one that deletes it.
    """
    marker = root / RESET_MARKER
    current = marker.read_bytes() if marker.is_file() else None
    return blob_at_ref(root, fork, RESET_MARKER) != current


def changelog_history_kept(root: Path, base: str, entries: set[str]) -> list[str]:
    """Return the files whose added lines repeat a retired CHANGELOG.md entry."""
    completed = subprocess.run(
        [
            "git", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
            "--src-prefix=a/", "--dst-prefix=b/", "--unified=0",
            f"{base}...HEAD", "--", ".", ":(exclude)CHANGELOG.md",
        ],
        cwd=root, capture_output=True, check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseError(detail or "git diff failed")
    kept: list[str] = []
    current = ""
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("diff --git "):
            current = line.rsplit(" b/", 1)[-1]
        elif line.startswith("+") and line[1:].strip() in entries \
                and current not in kept:
            kept.append(current)
    return kept


def require_reset_marker_added(base_marker: bytes | None, present: bool) -> None:
    """Refuse a pull request that edits, renames or deletes the reset marker."""
    if base_marker is not None or not present:
        raise ReleaseError(
            f"{RESET_MARKER} records a one-time release reset: a pull request "
            "may only add it, never edit, rename or delete it"
        )


def check_release_reset(root: Path, base: str, fork: str) -> dict:
    """Accept only the complete one-time restart of stable numbering.

    The owner retires every published release once and publishes
    BOOTSTRAP_VERSION again through the first-stable-baseline path. The pull
    request adds RESET_MARKER, which lists every release of the base
    CHANGELOG.md, and leaves exactly the state that path accepts: every
    version at BOOTSTRAP_VERSION on every surface, no stable release metadata,
    no changeset and a CHANGELOG.md holding only the bootstrap note. No
    retired entry is kept anywhere else. Any other mix is refused. ``fork``
    is where the pull request left ``base``.
    """
    require_reset_marker_added(
        blob_at_ref(root, fork, RESET_MARKER), (root / RESET_MARKER).is_file(),
    )
    marker = read_reset_marker(root)
    retired = marker["retired_versions"]
    base_stable = json_at_ref(root, base, STABLE_METADATA)
    if base_stable is None:
        raise ReleaseError(
            "a release reset retires a published stable line, but the base has "
            "no .release/stable.json"
        )
    base_changelog = (
        blob_at_ref(root, base, "CHANGELOG.md") or b""
    ).decode("utf-8", errors="replace")
    released = changelog_releases(base_changelog)
    if retired != released:
        raise ReleaseError(
            f"{RESET_MARKER} retired_versions must list every release of the "
            "base CHANGELOG.md, in order: " + (", ".join(released) or "none")
        )
    if base_stable.get("version") != retired[-1]:
        raise ReleaseError(
            f"the base stable release {base_stable.get('version')} must be the "
            f"last retired version, {retired[-1]}"
        )
    versions = load_versions(root)
    if versions["marketplace"] != BOOTSTRAP_VERSION or any(
        version != BOOTSTRAP_VERSION for version in versions["plugins"].values()
    ):
        raise ReleaseError(
            "a release reset sets the marketplace and every plugin to "
            f"{BOOTSTRAP_VERSION}"
        )
    base_versions = json_at_ref(root, base, "versions.json")
    base_plugins = (
        base_versions.get("plugins") if isinstance(base_versions, dict) else None
    )
    if not isinstance(base_plugins, dict) \
            or set(base_plugins) != set(versions["plugins"]):
        raise ReleaseError("a release reset cannot change the plugin registry")
    if (root / STABLE_METADATA).exists():
        raise ReleaseError(f"a release reset deletes {STABLE_METADATA}")
    pending = sorted(path.name for path in (root / ".changes").glob("*.json"))
    if pending:
        raise ReleaseError(
            "a release reset deletes every pending changeset: " + ", ".join(pending)
        )
    verify_bootstrap(root)
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file() \
            or changelog.read_text(encoding="utf-8") != bootstrap_changelog():
        raise ReleaseError(
            "a release reset starts CHANGELOG.md over with the bootstrap note "
            f"alone: {bootstrap_changelog()!r}"
        )
    entries = {
        line.strip() for line in base_changelog.splitlines()
        if line.startswith("- ")
    }
    kept = changelog_history_kept(root, base, entries)
    if kept:
        raise ReleaseError(
            "a release reset keeps no changelog history: " + ", ".join(kept)
            + (" repeats" if len(kept) == 1 else " repeat")
            + " retired CHANGELOG.md entries"
        )
    return {
        "mode": "reset",
        "version": BOOTSTRAP_VERSION,
        "retired_versions": retired,
    }


def release_owned_changes(
    root: Path, base: str, changed: list[tuple[str, str]], versions: dict,
) -> list[str]:
    """Name the changes only a release commit may make; none for a normal PR."""
    retired: set[str] = set()
    registry_retirement = False
    stable_retirement = False
    if any(path == "versions.json" for _status, path in changed):
        retired = retirement_registry_delta(
            json_at_ref(root, base, "versions.json"), versions
        )
        registry_retirement = bool(retired)
        if any(path == STABLE_METADATA for _status, path in changed):
            stable_retirement = stable_retirement_cleanup(
                json_at_ref(root, base, STABLE_METADATA),
                read_json(root / STABLE_METADATA)
                if (root / STABLE_METADATA).is_file() else None,
                retired,
            )
    protected = {
        path for _status, path in changed
        if path in RELEASE_OWNED
        and not (path == "versions.json" and registry_retirement)
        and not (path == STABLE_METADATA and stable_retirement)
    }
    changed_existing_changesets = {
        path for status, path in changed
        if path.startswith(".changes/") and path.endswith(".json")
        and status != "A"
    }
    problems: list[str] = []
    if protected:
        problems.append(
            "normal pull requests cannot edit release-owned files: "
            + ", ".join(sorted(protected))
        )
    if changed_existing_changesets:
        problems.append(
            "normal pull requests cannot modify or delete existing changesets: "
            + ", ".join(sorted(changed_existing_changesets))
        )
    return problems


def check_pr_changeset(
    root: Path, base: str, head: str = "HEAD", *,
    now: Callable[[], datetime.datetime] = utc_now,
) -> dict:
    """Check the release-impact declaration of the pull request ``base...HEAD``.

    ``head`` names the pull request's last commit. It differs from HEAD where
    CI checks out the merge of the pull request into ``base``. ``now`` is the
    clock a release commit's date is checked against.
    """
    changed = changed_paths(root, base)
    fork = pull_request_fork(root, base)
    if reset_marker_changed(root, fork):
        return check_release_reset(root, base, fork)
    versions = load_versions(root)
    problems = release_owned_changes(root, base, changed, versions)
    if problems:
        try:
            version = verify_release_commit(root, base, head, now=now)
        except ReleaseError as exc:
            raise ReleaseError(
                "; ".join(problems) + ". Only a release commit may make these"
                " changes: the last commit of a pull request, made by"
                f" `python3 tools/release.py bump`. This one is not: {exc}"
            ) from exc
        return {"mode": "release", "version": version}
    added = [path for status, path in changed if status == "A" and path.startswith(".changes/") and path.endswith(".json")]
    selected = [item for item in load_changesets(root, versions) if item.path.relative_to(root).as_posix() in added]
    declared = {component for item in selected for component in item.components}
    adapters = build_distributions.load_adapters(root)
    return changeset_components_rule(
        changed, versions["plugins"], adapters, added, declared,
    )


def changeset_components_rule(
    changed: list[tuple[str, str]], plugins: Container[str], hosts: Container[str],
    added: list[str], declared: set[str],
) -> dict:
    """Require a normal pull request's changesets to declare each changed component."""
    required: set[str] = set()
    for _status, path in changed:
        parts = Path(path).parts
        if len(parts) >= 2 and parts[0] == "plugins" and parts[1] in plugins:
            required.add(parts[1])
        if len(parts) >= 3 and parts[0] == "platforms" and parts[1] in hosts and parts[2] in plugins:
            required.add(parts[2])
        if len(parts) >= 3 and parts[0] == "dist" and parts[1] in hosts and parts[2] in plugins:
            required.add(parts[2])
        if path in {".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"}:
            required.add(MARKETPLACE_COMPONENT)
    if not added:
        raise ReleaseError("every normal pull request must add a .changes/*.json file")
    missing = required - declared
    if missing:
        raise ReleaseError(
            "changeset omits changed release components: " + ", ".join(sorted(missing))
        )
    return {"mode": "changeset"}


# The owner's local file of private terms, one per line, such as consumer
# project names a generic check cannot know. It stays outside the
# repository; check-pr never prints its path or a term.
PRIVATE_TERMS_VARIABLE = "AGENT_MARKETPLACE_PRIVATE_TERMS_FILE"
HUNK_RE = re.compile(r"^(@+) (?:-[0-9]+(?:,[0-9]+)? )+\+([0-9]+)(?:,[0-9]+)? @+")


def owner_terms(root: Path) -> list[str]:
    """The terms of the file PRIVATE_TERMS_VARIABLE names; none when unset."""
    name = os.environ.get(PRIVATE_TERMS_VARIABLE, "").strip()
    if not name:
        return []
    path = Path(name).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(
            f"{PRIVATE_TERMS_VARIABLE} names a file that cannot be read"
        ) from exc
    resolved, top = path.resolve(), root.resolve()
    if (resolved == top or top in resolved.parents) \
            and not git_ok(root, "check-ignore", "-q", str(resolved)):
        raise ReleaseError(
            f"{PRIVATE_TERMS_VARIABLE} names a file inside this checkout that Git"
            " does not ignore; keep it outside the repository"
        )
    return sorted({line.strip() for line in text.splitlines() if line.strip()})


def text_position(text: str, offset: int) -> str:
    """The 1-based line and column of offset in text."""
    line = text.count("\n", 0, offset) + 1
    column = offset - text.rfind("\n", 0, offset)
    return f"line {line}, column {column}"


def check_pr_publishable(
    root: Path, base: str, pr_text: Path | None = None,
) -> dict:
    """Refuse a home-directory path, and a private term when the owner names
    a terms file, in every commit message and added line of base..HEAD and
    in the PR text. The merge method keeps every commit of a pull request, so
    a later commit that removes a hit does not unpublish it. A merge is read
    for the lines it adds itself. A refusal names kind and position only."""
    import validate  # validate imports this module, so it loads on use

    terms = owner_terms(root)
    term_re = re.compile("|".join(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])" for term in terms
    ), re.IGNORECASE) if terms else None
    hits: list[str] = []

    def scan(text: str, place: Callable[[int], str], relative: str = "") -> bool:
        found = [(match.start(), "home-directory path")
                 for match in validate.refused_home_paths(text, relative)]
        if term_re is not None:
            found += [(match.start(), "private term") for match in term_re.finditer(text)]
        hits.extend(f"{kind} at {place(offset)}" for offset, kind in sorted(found))
        return bool(found)

    environment = hermetic_git_environment()
    commits = int(git(root, "rev-list", "--count", f"{base}..HEAD", environment=environment))
    log = git_text(root, "log", "-z", "--format=%H%n%B", f"{base}..HEAD",
                   environment=environment)
    for entry in filter(None, log.split("\0")):
        commit, _, message = entry.partition("\n")
        scan(message, lambda offset: f"commit {commit[:12]} message "
             + text_position(message, offset))
    patch = git_text(
        root, "-c", "core.quotePath=false", "log", "-p", "--cc", "-M",
        "--no-color", "--no-ext-diff", "--no-textconv", "--no-show-signature",
        "--src-prefix=a/", "--dst-prefix=b/", "--format=%x00%H", f"{base}..HEAD",
        environment=environment,
    )
    commit, path, shown, index, parents, line = "", "", "", 0, 0, 0
    for raw in patch.split("\n"):
        if raw.startswith("\0"):
            commit, index, parents = raw[1:13], 0, 0
            continue
        if raw.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            path, shown, index, parents = "", "", index + 1, 0
            continue
        hunk = HUNK_RE.match(raw)
        if hunk:
            parents, line = len(hunk.group(1)) - 1, int(hunk.group(2))
            continue
        if not parents:
            target = raw[6:] if raw.startswith("+++ b/") \
                else raw[10:] if raw.startswith("rename to ") else None
            if target is not None:
                path = target
                hidden = scan(path, lambda _offset: f"commit {commit} file {index} path")
                shown = f"file {index}" if hidden else path
            continue
        prefix = raw[:parents]
        if raw.startswith("\\") or "-" in prefix:
            continue
        if prefix == "+" * parents:
            content = raw[parents:]
            scan(content, lambda offset: f"commit {commit} {shown} line {line},"
                 f" column {offset + 1}", path)
        line += 1
    if pr_text is not None:
        try:
            text = pr_text.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReleaseError("the --pr-text file cannot be read") from exc
        scan(text, lambda offset: "PR text " + text_position(text, offset))
    if hits:
        raise ReleaseError(
            "the pull request carries text a public repository must not publish: "
            + "; ".join(dict.fromkeys(hits))
            + ". Reword each one, rewriting the commit that holds it, then run"
            " check-pr again"
        )
    return {"commits": commits, "terms_checked": len(terms)}


def publishable_summary(scanned: dict, pr_text: bool) -> str:
    commits = scanned["commits"]
    subject = f"{commits} commit and its" if commits == 1 else f"{commits} commits and their"
    count = scanned["terms_checked"]
    checked = "no private terms set" if not count else (
        f"{count} private term{'' if count == 1 else 's'} checked")
    text = " and the PR text" if pr_text else ""
    return (f"release: {subject} added lines{text} hold no home-directory path or"
            f" private term ({checked})")


def append_changelog(root: Path, plan: dict) -> None:
    path = root / "CHANGELOG.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Changelog\n"
    lines = ["", f"## {plan['marketplace']}", ""]
    for summary in plan["summaries"]:
        lines.append(f"- {summary}")
    path.write_bytes(
        (existing.rstrip() + "\n" + "\n".join(lines) + "\n").encode("utf-8")
    )


def prepare_release(root: Path, when: datetime.datetime) -> dict:
    """Consume every pending changeset into the release commit's tree.

    It names the release for the UTC month of ``when``, sets every version
    surface to that one version, appends the CHANGELOG.md section, records
    the release metadata and regenerates every host distribution. The result
    depends on the tree and that month alone, so check-pr can replay it byte
    for byte at the release commit's own date.
    """
    versions = load_versions(root)
    changesets = load_changesets(root, versions)
    plan = release_plan(versions, changesets, when)
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
        "schema_version": METADATA_SCHEMA,
        "version": plan["marketplace"],
        "build_id": build_distributions.marketplace_snapshot(root)["build_id"],
        "impacts": plan["impacts"],
        "summaries": plan["summaries"],
    }
    write_json(root / STABLE_METADATA, metadata)
    try:
        build_distributions.replace_generated(root, root / "dist")
    except ValueError as exc:
        raise ReleaseError(f"distribution build failed: {exc}") from exc
    return metadata


def commit_release(
    root: Path, now: Callable[[], datetime.datetime] = utc_now,
) -> dict:
    """Make the release commit on a clean checkout of the pull request.

    The commit's author and committer date is the instant its version was
    named, so check-pr replays it in the same month even when bump runs in a
    month's last second.
    """
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError(
            "bump needs a clean worktree; commit or stash every change first"
        )
    when = release_instant(now())
    metadata = prepare_release(root, when)
    message = f"chore: release v{metadata['version']}"
    git(root, "add", "--all")
    stamp = when.isoformat()
    git(
        root, "commit", "--quiet", "--message", message,
        environment={
            **os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
        },
    )
    return {
        "version": metadata["version"],
        "commit": git(root, "rev-parse", "HEAD"),
        "message": message,
    }


def verify_release(root: Path, version: str | None = None) -> dict:
    problems = validate_version_surfaces(root)
    if problems:
        raise ReleaseError("; ".join(problems))
    metadata = read_json(root / STABLE_METADATA)
    if set(metadata) != set(METADATA_KEYS) \
            or metadata.get("schema_version") != METADATA_SCHEMA:
        raise ReleaseError(
            f"{STABLE_METADATA} must hold schema_version {METADATA_SCHEMA} and only "
            + ", ".join(METADATA_KEYS)
        )
    versions = load_versions(root)
    expected = version or versions["marketplace"]
    if metadata.get("version") != expected or versions["marketplace"] != expected:
        raise ReleaseError("release metadata, requested tag, and marketplace version differ")
    parse_semver(expected, "release version")
    others = sorted(
        plugin for plugin, value in versions["plugins"].items() if value != expected
    )
    if others:
        raise ReleaseError(
            f"every plugin carries the release version {expected}; "
            + ", ".join(others) + (" does" if len(others) == 1 else " do") + " not"
        )
    if metadata.get("build_id") != build_distributions.marketplace_snapshot(root)["build_id"]:
        raise ReleaseError("release metadata build identity differs from the release sources")
    return metadata


def replay_checkout(
    root: Path, target: Path, revision: str, environment: dict[str, str],
) -> None:
    """Check ``revision`` out in a disposable clone with a fixed text policy."""
    completed = subprocess.run(
        [
            "git", "clone", "--quiet", "--shared", "--no-checkout",
            str(root), str(target),
        ],
        capture_output=True, text=True, check=False, env=environment,
    )
    if completed.returncode != 0:
        raise ReleaseError(completed.stderr.strip() or "release replay clone failed")
    for key, value in (
        ("core.autocrlf", "false"), ("core.eol", "lf"), ("core.filemode", "false"),
        ("gc.auto", "0"), ("maintenance.auto", "false"),
    ):
        git(target, "config", key, value, environment=environment)
    git(target, "checkout", "--quiet", "--detach", revision, environment=environment)


def apply_package_index_modes(root: Path, environment: dict[str, str]) -> None:
    """Stage each generated package file with the mode its provenance declares.

    The replay ignores filesystem execute bits, so a package file it adds
    would otherwise be staged without the mode the release commit carries.
    """
    _marker, provenance_name = build_distributions.packaging_names(root)
    declared: set[str] = set()
    for provenance in sorted((root / "dist").glob(f"*/*/{provenance_name}")):
        package = provenance.parent.relative_to(root).as_posix()
        executables = read_json(provenance).get("executables", [])
        declared.update(f"{package}/{path}" for path in executables)
    to_executable: list[str] = []
    to_regular: list[str] = []
    staged = git_text(root, "ls-files", "-s", "-z", "--", "dist", environment=environment)
    for record in filter(None, staged.split("\0")):
        metadata, path = record.split("\t", 1)
        mode = metadata.split()[0]
        if path in declared and mode != "100755":
            to_executable.append(path)
        elif path not in declared and mode == "100755":
            to_regular.append(path)
    if to_executable:
        git(root, "update-index", "--chmod=+x", "--", *to_executable, environment=environment)
    if to_regular:
        git(root, "update-index", "--chmod=-x", "--", *to_regular, environment=environment)


def release_replay_instant(
    made: datetime.datetime, now: datetime.datetime,
) -> datetime.datetime:
    """The instant a release commit made at ``made`` is replayed at: its own.

    A date in a month that has not begun at ``now`` comes from a wrong clock.
    """
    today = release_instant(now)
    if (made.year, made.month) > (today.year, today.month):
        raise ReleaseError(
            f"it is dated {made:%Y-%m-%d}, in a month that has not begun; fix"
            " the clock, drop it and run bump again"
        )
    return made


def release_commit_parent(
    parents: list[str], base_is_ancestor: Callable[[str], bool],
) -> str:
    """The one parent of a release commit, which must descend from the base."""
    if len(parents) != 1:
        raise ReleaseError(f"its last commit has {len(parents)} parents, not one")
    parent = parents[0]
    if not base_is_ancestor(parent):
        raise ReleaseError(
            "the base advanced after the release commit was made; rebase the"
            " pull request onto the base and run bump again"
        )
    return parent


def require_changeset_commits(check: Callable[[], dict]) -> None:
    """Require the commits before a release commit to pass as a normal pull request."""
    try:
        earlier = check()
    except ReleaseError as exc:
        raise ReleaseError(
            f"the commits before it break the changeset rules: {exc}"
        ) from exc
    if earlier["mode"] != "changeset":
        raise ReleaseError(
            "the commits before it already make a release or a reset"
        )


def bump_parent(root: Path, when: datetime.datetime) -> dict:
    """Bump a release commit's parent as bump did at ``when``."""
    try:
        return prepare_release(root, when)
    except ReleaseError as exc:
        raise ReleaseError(f"its parent cannot be bumped: {exc}") from exc


def require_bumped_tree(head_tree: str, expected_tree: str) -> None:
    """Refuse a release commit whose tree is not the replayed bump's."""
    if head_tree != expected_tree:
        raise ReleaseError(
            "its tree differs from the deterministic bump of its parent; drop"
            " it and run bump again"
        )


def verify_release_commit(
    root: Path, base: str, head: str = "HEAD", *,
    now: Callable[[], datetime.datetime] = utc_now,
) -> str:
    """Prove that ``head`` is the deterministic release commit of its parent.

    ``base`` must be an ancestor of the parent, and the commits between them
    keep the normal changeset rules. The parent is bumped again in a
    disposable clone that ignores ambient Git configuration, attributes,
    excludes, replacement refs and graph overlays; its complete tree must
    equal the release commit's. The replay runs at the release commit's own
    committer date, so a release commit stays valid for the month it was
    made in; a date in a month that has not begun comes from a wrong clock
    and is refused.
    """
    environment = hermetic_git_environment()
    reject_graph_overlays(root, environment)
    head_sha = git(
        root, "rev-parse", "--verify", f"{head}^{{commit}}", environment=environment,
    )
    made = datetime.datetime.fromtimestamp(int(git(
        root, "log", "-1", "--no-show-signature", "--format=%ct", head_sha,
        environment=environment,
    )), datetime.timezone.utc)
    when = release_replay_instant(made, now())
    base_sha = git(
        root, "rev-parse", "--verify", f"{base}^{{commit}}", environment=environment,
    )
    parents = git(
        root, "rev-list", "--parents", "-n", "1", head_sha, environment=environment,
    ).split()[1:]
    parent = release_commit_parent(parents, lambda parent: git_ok(
        root, "merge-base", "--is-ancestor", base_sha, parent,
        environment=environment,
    ))
    with tempfile.TemporaryDirectory(prefix="release-commit.") as temporary:
        replay = Path(temporary) / "replay"
        replay_checkout(root, replay, parent, environment)
        if parent != base_sha:
            require_changeset_commits(
                lambda: check_pr_changeset(replay, base_sha, now=now),
            )
        metadata = bump_parent(replay, when)
        git(replay, "add", "--all", environment=environment)
        apply_package_index_modes(replay, environment)
        expected_tree = git(replay, "write-tree", environment=environment)
    head_tree = git(
        root, "rev-parse", f"{head_sha}^{{tree}}", environment=environment,
    )
    require_bumped_tree(head_tree, expected_tree)
    return metadata["version"]


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
    if (root / STABLE_METADATA).exists():
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


def changelog_section(text: str, version: str) -> str:
    """Return the body of the one ``## version`` section of CHANGELOG.md."""
    lines = text.splitlines()
    heading = f"## {version}"
    starts = [index for index, line in enumerate(lines) if line.rstrip() == heading]
    if len(starts) != 1:
        raise ReleaseError(
            f"CHANGELOG.md must hold exactly one {heading} section, found {len(starts)}"
        )
    body: list[str] = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    notes = "\n".join(body).strip()
    if not notes:
        raise ReleaseError(f"CHANGELOG.md section {version} is empty")
    return notes + "\n"


def release_notes(root: Path, version: str, ref: str | None = None) -> str:
    """The CHANGELOG.md section of a release, from the worktree or a commit."""
    parse_semver(version, "release version")
    if ref is None:
        path = root / "CHANGELOG.md"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ReleaseError(f"missing release file: {path}") from exc
    else:
        raw = blob_at_ref(root, ref, "CHANGELOG.md")
        if raw is None:
            raise ReleaseError(f"CHANGELOG.md is missing at {ref}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("CHANGELOG.md is not UTF-8") from exc
    return changelog_section(text, version)


def observe_release_refs(root: Path) -> dict:
    """Read remote main, stable and every SemVer release tag in one call."""
    output = git(
        root, "ls-remote", "origin", "refs/heads/main", "refs/heads/stable",
        "refs/tags/v*",
    )
    heads: dict[str, str] = {}
    objects: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ReleaseError(f"remote ref observation returned {line!r}")
        oid, ref = fields
        require_sha(oid, f"remote object ID for {ref}")
        if ref in ("refs/heads/main", "refs/heads/stable"):
            heads[ref.rsplit("/", 1)[1]] = oid
            continue
        if not ref.startswith("refs/tags/v"):
            continue
        name = ref[len("refs/tags/v"):]
        target = peeled if name.endswith("^{}") else objects
        version = name[:-3] if name.endswith("^{}") else name
        if SEMVER_RE.fullmatch(version):
            target[version] = oid
    if "main" not in heads:
        raise ReleaseError("remote main is missing")
    lightweight = sorted(set(objects) - set(peeled), key=parse_semver)
    if lightweight:
        raise ReleaseError(
            "release tags must be annotated: "
            + ", ".join(f"v{version}" for version in lightweight)
        )
    return {
        "main": heads["main"],
        "stable": heads.get("stable"),
        "tags": {version: peeled[version] for version in objects},
    }


def github_api(endpoint: str) -> object:
    """One read-only GitHub REST call through the gh CLI."""
    try:
        completed = subprocess.run(
            ["gh", "api", endpoint], capture_output=True, text=True,
            check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"GitHub API request failed: {exc}") from exc
    if completed.returncode != 0:
        raise ReleaseError(
            "GitHub API request failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or "no detail")
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError("GitHub API returned invalid JSON") from exc


def main_validation(
    api: Callable[[str], object],
    repository: str,
    sha: str,
    *,
    wait_seconds: float = VALIDATION_WAIT_SECONDS,
    appear_seconds: float = VALIDATION_APPEAR_SECONDS,
    poll_seconds: float = VALIDATION_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Wait, bounded, for main's push validation of ``sha`` and require success.

    The release never runs the tests again: the validate run that main's push
    started for this exact commit decides. A running one is awaited; a failed
    or cancelled one, or none at all, refuses the release.
    """
    endpoint = (
        f"repos/{repository}/actions/workflows/validate.yml/runs"
        f"?event=push&branch=main&head_sha={sha}&per_page=100"
    )
    started = clock()
    failures = 0
    while True:
        try:
            listing = api(endpoint)
            failures = 0
        except ReleaseError as exc:
            failures += 1
            if failures >= 3:
                raise ReleaseError(f"cannot read main's validation runs: {exc}") from exc
            listing = None
        elapsed = clock() - started
        runs = [
            run for run in (
                listing.get("workflow_runs", []) if isinstance(listing, dict) else []
            )
            if isinstance(run, dict)
            and run.get("path") == VALIDATION_WORKFLOW
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == sha
            and (run.get("repository") or {}).get("full_name") == repository
            and (run.get("head_repository") or {}).get("full_name") == repository
        ]
        if runs:
            latest = max(runs, key=lambda run: (str(run.get("created_at", "")), run.get("id", 0)))
            status = latest.get("status")
            location = latest.get("html_url", f"run {latest.get('id')}")
            if status == "completed":
                if latest.get("conclusion") == "success":
                    return latest
                raise ReleaseError(
                    f"main's validation of {sha} concluded {latest.get('conclusion')}:"
                    f" {location}; rerun it and release again"
                )
            if elapsed >= wait_seconds:
                raise ReleaseError(
                    f"main's validation of {sha} is still {status} after"
                    f" {int(wait_seconds)} seconds: {location}; release again"
                    " when it finishes"
                )
        elif listing is not None and elapsed >= min(appear_seconds, wait_seconds):
            raise ReleaseError(
                f"main has no push validation run for {sha}; release a commit"
                " main moved to, whose validation passed"
            )
        elif listing is None and elapsed >= wait_seconds:
            raise ReleaseError(f"cannot read main's validation runs for {sha}")
        sleep(poll_seconds)


def untagged_stable_version(root: Path, stable: str, version: str) -> str:
    """Return the version the commit stable points to names, older than ``version``.

    When every release tag was deleted, stable still points at the last
    released commit, so that commit is the release before ``version``.
    """
    if not git_ok(root, "cat-file", "-e", f"{stable}^{{commit}}"):
        raise ReleaseError(
            f"remote stable is at {stable}, which this checkout lacks; fetch origin"
        )
    named = (json_at_ref(root, stable, "versions.json") or {}).get("marketplace")
    if not isinstance(named, str) or SEMVER_RE.fullmatch(named) is None:
        raise ReleaseError(
            f"versions.json at remote stable {stable} names no release version"
        )
    if parse_semver(named) >= parse_semver(version):
        raise ReleaseError(
            f"remote stable at {stable} names v{named}; a release never goes"
            f" back to v{version}"
        )
    return named


def verify_candidate(
    root: Path,
    version: str,
    sha: str,
    *,
    repository: str,
    api: Callable[[str], object] = github_api,
    wait_seconds: float = VALIDATION_WAIT_SECONDS,
    appear_seconds: float = VALIDATION_APPEAR_SECONDS,
    poll_seconds: float = VALIDATION_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Decide whether the main commit ``sha`` may be released as ``version``.

    Read-only. The commit must be on main and be a release commit for exactly
    this version: every version surface names it, no changeset is pending,
    its release metadata matches its sources and CHANGELOG.md has its
    section. The version must be newer than every release tag, and its tag
    may exist only on this commit, which resumes an interrupted release.
    Stable must sit on the previous release or on this commit. The previous
    release is the newest older tag; with no release tag at all it is the
    commit stable points to, which must name an older version, and only a
    repository without stable takes the first-release path. Last, main's
    push validation of this commit must have passed.
    """
    target = parse_semver(version, "release version")
    sha = require_sha(sha, "release commit")
    environment = hermetic_git_environment()
    reject_graph_overlays(root, environment)
    if not git_ok(root, "cat-file", "-e", f"{sha}^{{commit}}") or not git_ok(
        root, "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main",
    ):
        raise ReleaseError(f"{sha} is not a commit on main")
    refs = observe_release_refs(root)
    tags = refs["tags"]
    newer = sorted(
        (tag for tag in tags if parse_semver(tag) > target), key=parse_semver,
    )
    if newer:
        raise ReleaseError(
            f"v{newer[-1]} is already released; a release never goes back to v{version}"
        )
    if tags.get(version, sha) != sha:
        raise ReleaseError(f"v{version} already tags {tags[version]}, not {sha}")
    earlier = [tag for tag in tags if parse_semver(tag) < target]
    prior = max(earlier, key=parse_semver) if earlier else None
    prior_sha = tags[prior] if prior else None
    prior_name = f"v{prior}"
    if not tags and refs["stable"] is not None:
        prior_sha = refs["stable"]
        if prior_sha == sha:
            raise ReleaseError(f"{sha} is already the stable release")
        prior = untagged_stable_version(root, prior_sha, version)
        prior_name = f"the untagged stable release v{prior}"
    if prior_sha is not None:
        if prior_sha == sha:
            raise ReleaseError(f"{sha} is already released as v{prior}")
        if not git_ok(root, "merge-base", "--is-ancestor", prior_sha, sha):
            raise ReleaseError(
                f"{prior_name} at {prior_sha} is not an ancestor of {sha}; stable"
                " only moves forward"
            )
    if refs["stable"] not in (prior_sha, sha):
        expected = f"v{prior} at {prior_sha}" if prior else "absent"
        raise ReleaseError(
            f"remote stable is at {refs['stable'] or 'nothing'}; it must be"
            f" {expected} or the release commit"
        )
    if prior_sha is None and refs["stable"] == sha and version != BOOTSTRAP_VERSION:
        raise ReleaseError(
            f"v{version} and stable already sit on {sha}, and no older release"
            " tag records the commit stable moved from, which a failed public"
            " smoke must roll back to; re-run the failed jobs of the Release run"
            " that staged them, `gh run rerun <run-id> --failed`, whose verify"
            " output names that commit"
        )
    with tempfile.TemporaryDirectory(prefix="release-candidate.") as temporary:
        candidate = Path(temporary) / "candidate"
        replay_checkout(root, candidate, sha, environment)
        versions = load_versions(candidate)
        if versions["marketplace"] != version:
            raise ReleaseError(
                f"versions.json at {sha} names {versions['marketplace']}, not"
                f" {version}; merge the release commit that"
                " `python3 tools/release.py bump` makes first"
            )
        changes = candidate / ".changes"
        pending = sorted(path.name for path in changes.glob("*.json")) \
            if changes.is_dir() else []
        if pending:
            raise ReleaseError(
                f"{sha} holds changesets no release commit consumed: "
                + ", ".join(pending)
                + "; pass --sha with the main commit that merged the release"
                " commit, or make a new release commit"
            )
        if prior_sha is None:
            verify_bootstrap(candidate)
        else:
            verify_release(candidate, version)
        notes = release_notes(candidate, version)
    run = main_validation(
        api, repository, sha, wait_seconds=wait_seconds,
        appear_seconds=appear_seconds, poll_seconds=poll_seconds,
        clock=clock, sleep=sleep,
    )
    return {
        "schema_version": 1,
        "version": version,
        "candidate_sha": sha,
        "prior_version": prior,
        "prior_stable_sha": prior_sha,
        "validation_run": run.get("html_url", run.get("id")),
        "notes": notes,
    }


def release_intent(root: Path, before: str, after: str) -> dict:
    """Decide whether a push to main carries a release commit.

    It does when the push changes the version that versions.json names and no
    release tag holds that version yet. Every other push starts nothing, a
    repeated run for an already released push included. ``before`` is the
    main head the push replaced; when it is absent or unreadable, the first
    parent of ``after`` stands in for it.
    """
    after = require_sha(after, "pushed main commit")
    current = json_at_ref(root, after, "versions.json")
    version = current.get("marketplace") if current else None
    if not isinstance(version, str):
        raise ReleaseError(f"versions.json at {after} names no marketplace version")
    parse_semver(version, "marketplace version")
    previous = None
    if before.strip("0"):
        previous = json_at_ref(
            root, require_sha(before, "previous main head"), "versions.json",
        )
    if previous is None:
        previous = json_at_ref(root, f"{after}^1", "versions.json")
    if previous is not None and previous.get("marketplace") == version:
        return {
            "release": False, "version": version,
            "reason": f"this push keeps v{version}; nothing to release",
        }
    tagged = observe_release_refs(root)["tags"].get(version)
    if tagged:
        return {
            "release": False, "version": version,
            "reason": f"v{version} already tags {tagged}",
        }
    return {
        "release": True, "version": version,
        "reason": f"this push moves the marketplace to v{version}",
    }


class Commands:
    """The gh calls ship makes; tests replace them."""

    def capture(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    def stream(self, argv: list[str]) -> int:
        return subprocess.run(argv, check=False).returncode


def dispatched_run(
    commands: Commands, since: datetime.datetime, *,
    attempts: int = 12, sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Find the Release run a dispatch started when gh did not print it."""
    for attempt in range(attempts):
        listed = commands.capture([
            "gh", "run", "list", "--workflow", RELEASE_WORKFLOW,
            "--event", "workflow_dispatch", "--branch", "main",
            "--limit", "10", "--json", "databaseId,createdAt",
        ])
        try:
            runs = json.loads(listed.stdout) if listed.returncode == 0 else []
        except json.JSONDecodeError:
            runs = []
        fresh = []
        for run in runs if isinstance(runs, list) else []:
            try:
                created = datetime.datetime.fromisoformat(
                    str(run["createdAt"]).replace("Z", "+00:00")
                )
                identity = int(run["databaseId"])
            except (KeyError, TypeError, ValueError):
                continue
            if created >= since:
                fresh.append((created, identity))
        if fresh:
            return str(max(fresh)[1])
        if attempt + 1 < attempts:
            sleep(5)
    raise ReleaseError(
        "the dispatched Release run did not appear; find it with"
        f" `gh run list --workflow {RELEASE_WORKFLOW}`"
    )


def ship(
    root: Path,
    version: str,
    sha: str | None = None,
    *,
    commands: Commands | None = None,
    watch: bool = True,
    now: Callable[[], datetime.datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Dispatch the Release workflow on main and follow it to the Release."""
    parse_semver(version, "release version")
    commands = commands or Commands()
    inputs = ["-f", f"version={version}"]
    resolved = None
    if sha:
        try:
            resolved = git(root, "rev-parse", "--verify", f"{sha}^{{commit}}")
        except ReleaseError as exc:
            raise ReleaseError(f"unknown commit {sha}; fetch origin first") from exc
        inputs += ["-f", f"sha={require_sha(resolved, 'release commit')}"]
    since = now() - datetime.timedelta(seconds=30)
    dispatched = commands.capture([
        "gh", "workflow", "run", RELEASE_WORKFLOW, "--ref", "main", *inputs,
    ])
    if dispatched.returncode != 0:
        raise ReleaseError(
            "release dispatch failed: "
            + ((dispatched.stderr or "").strip() or (dispatched.stdout or "").strip()
               or "no detail")
        )
    match = RUN_URL_RE.search(dispatched.stdout or "") \
        or RUN_URL_RE.search(dispatched.stderr or "")
    run_id = match.group(1) if match else dispatched_run(commands, since, sleep=sleep)
    result: dict = {"version": version, "sha": resolved, "run_id": run_id}
    if not watch:
        return result
    if commands.stream([
        "gh", "run", "watch", run_id, "--exit-status", "--compact",
    ]) != 0:
        raise ReleaseError(
            f"Release run {run_id} failed; read `gh run view {run_id}"
            " --log-failed`, then ship again, which resumes from the state the"
            " run left"
        )
    viewed = commands.capture([
        "gh", "release", "view", f"v{version}", "--json", "url,isImmutable",
    ])
    try:
        release = json.loads(viewed.stdout) if viewed.returncode == 0 else None
    except json.JSONDecodeError:
        release = None
    if not isinstance(release, dict) or release.get("isImmutable") is not True:
        raise ReleaseError(
            f"Release run {run_id} passed, yet v{version} is not an immutable Release"
        )
    result.update(release_url=release.get("url"), immutable=True)
    return result


def auto_release(
    root: Path,
    before: str,
    after: str,
    *,
    commands: Commands | None = None,
    now: Callable[[], datetime.datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Dispatch the Release for a push to main that carries a release commit."""
    intent = release_intent(root, before, after)
    if not intent["release"]:
        return intent
    started = ship(
        root, intent["version"], after, commands=commands, watch=False,
        now=now, sleep=sleep,
    )
    return {**intent, "run_id": started["run_id"]}


def write_github_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("plan")
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--write", action="store_true", required=True)
    pr_parser = sub.add_parser("check-pr")
    pr_parser.add_argument("--base", required=True)
    pr_parser.add_argument(
        "--head", default="HEAD",
        help="the pull request's last commit, when HEAD is CI's merge commit",
    )
    pr_parser.add_argument(
        "--pr-text", type=Path,
        help="a file with the pull request title and body to scan as well",
    )
    sub.add_parser(
        "bump", help="make the release commit: the pull request's last commit",
    )
    verify_parser = sub.add_parser("verify-release")
    verify_parser.add_argument("--version")
    candidate_parser = sub.add_parser("verify-candidate")
    candidate_parser.add_argument("--version", required=True)
    candidate_parser.add_argument("--sha", required=True)
    candidate_parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    candidate_parser.add_argument("--github-output", type=Path)
    candidate_parser.add_argument(
        "--wait-seconds", type=float, default=VALIDATION_WAIT_SECONDS,
    )
    notes_parser = sub.add_parser("release-notes")
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--ref")
    notes_parser.add_argument("--output", type=Path)
    ship_parser = sub.add_parser(
        "ship", help="release a main commit: dispatch the Release workflow and follow it",
    )
    ship_parser.add_argument("--version", required=True)
    ship_parser.add_argument("--sha")
    ship_parser.add_argument("--no-watch", action="store_true")
    auto_parser = sub.add_parser(
        "auto-release",
        help="dispatch the Release when a push to main carries a release commit",
    )
    auto_parser.add_argument("--before", default="")
    auto_parser.add_argument("--after", required=True)
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
        print(json.dumps(release_plan(
            load_versions(root), load_changesets(root), utc_now(),
        ), indent=2))
    elif args.command == "sync":
        sync_version_surfaces(root, load_versions(root))
    elif args.command == "check-pr":
        result = check_pr_changeset(root, args.base, args.head)
        if result["mode"] == "reset":
            retired = result["retired_versions"]
            plural = "" if len(retired) == 1 else "s"
            print(
                f"release: release reset valid; it retires {len(retired)} "
                f"release{plural}, {retired[0]} to {retired[-1]}, and restarts "
                f"stable numbering at {result['version']}"
            )
        elif result["mode"] == "release":
            print(
                "release: release commit valid; it is the deterministic bump"
                f" of its parent to v{result['version']}"
            )
        else:
            print("release: pull request changeset valid")
        scanned = check_pr_publishable(root, args.base, args.pr_text)
        print(publishable_summary(scanned, args.pr_text is not None))
    elif args.command == "bump":
        print(json.dumps(commit_release(root), indent=2))
    elif args.command == "verify-release":
        print(json.dumps(verify_release(root, args.version), indent=2))
    elif args.command == "verify-candidate":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise ReleaseError("--repository must name owner/repository")
        result = verify_candidate(
            root, args.version, args.sha, repository=args.repository,
            wait_seconds=args.wait_seconds,
        )
        if args.github_output:
            write_github_output(args.github_output, {
                "candidate_sha": result["candidate_sha"],
                "prior_stable_sha": result["prior_stable_sha"] or "",
                "version": result["version"],
            })
        print(json.dumps(result, indent=2))
    elif args.command == "release-notes":
        notes = release_notes(root, args.version, args.ref)
        if args.output:
            args.output.write_bytes(notes.encode("utf-8"))
        else:
            print(notes, end="")
    elif args.command == "ship":
        print(json.dumps(ship(
            root, args.version, args.sha, watch=not args.no_watch,
        ), indent=2))
    elif args.command == "auto-release":
        print(json.dumps(auto_release(root, args.before, args.after), indent=2))
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
