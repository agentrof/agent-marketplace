#!/usr/bin/env python3
"""Stable release and cross-host version tooling for Agent Marketplace.

SemVer belongs only to stable releases. Normal main commits use a build identity
derived from first-parent history and the commit SHA.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import build_distributions


MARKETPLACE_COMPONENT = "agent-marketplace"
IMPACTS = {"patch": 1, "minor": 2, "major": 3}
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
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
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def parse_semver(value: str, label: str = "version") -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ReleaseError(f"{label} must be strict SemVer X.Y.Z, got {value!r}")
    return tuple(int(part) for part in match.groups())


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


def catalog_adapters(root: Path) -> dict[str, build_distributions.HostAdapter]:
    adapters = build_distributions.load_adapters(root)
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



def validate_version_surfaces(root: Path) -> list[str]:
    problems: list[str] = []
    try:
        versions = load_versions(root)
        changesets = load_changesets(root, versions)
        release_plan(versions, changesets)
        adapters = build_distributions.load_adapters(root)
    except ReleaseError as exc:
        return [str(exc)]
    try:
        catalogs = {
            host: read_json(adapter.module.marketplace_catalog_path(root))
            for host, adapter in catalog_adapters(root).items()
        }
    except ReleaseError as exc:
        return [str(exc)]
    catalog_entries = {
        host: {entry.get("name"): entry for entry in catalog.get("plugins", [])}
        for host, catalog in catalogs.items()
    }
    for plugin, expected in versions["plugins"].items():
        surfaces: list[tuple[str, str]] = []
        for host, adapter in catalog_adapters(root).items():
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
            expected_source = build_distributions.load_adapters(root)[host].module.channel_source(plugin)
            if source != expected_source:
                problems.append(
                    f"{plugin} {host} marketplace source must stay inside the selected channel"
                )
    return problems


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise ReleaseError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def git_ok(root: Path, *args: str) -> bool:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    return completed.returncode == 0


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


def release_ref_audit(root: Path, version: str) -> dict:
    parse_semver(version, "release version")
    main_sha = git(root, "rev-parse", "refs/remotes/origin/main")
    stable_sha = git(root, "rev-parse", "refs/remotes/origin/stable")
    tag_sha = git(root, "rev-list", "-n", "1", f"refs/tags/v{version}")
    if len({main_sha, stable_sha, tag_sha}) != 1:
        raise ReleaseError(
            "release refs differ: "
            f"origin/main={main_sha}, origin/stable={stable_sha}, v{version}={tag_sha}"
        )
    metadata = json_at_ref(root, "refs/remotes/origin/main", ".release/stable.json")
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise ReleaseError(
            f"origin/main release metadata does not attest v{version}"
        )
    if git_ok(root, "show-ref", "--verify", "--quiet", "refs/remotes/origin/release/stable"):
        raise ReleaseError(
            "origin/release/stable still exists; the publish workflow is incomplete"
        )
    return {
        "version": version,
        "commit": main_sha,
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
    if git(root, "rev-parse", "refs/heads/main") != final_audit["commit"]:
        raise ReleaseError("local main does not match the published release")
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


def check_pr_changeset(root: Path, base: str, allow_bootstrap: bool = False) -> None:
    changed = changed_paths(root, base)
    added = [path for status, path in changed if status == "A" and path.startswith(".changes/") and path.endswith(".json")]
    versions = load_versions(root)
    selected = [item for item in load_changesets(root, versions) if item.path.relative_to(root).as_posix() in added]
    declared = {component for item in selected for component in item.components}
    required: set[str] = set()
    for _status, path in changed:
        parts = Path(path).parts
        if len(parts) >= 2 and parts[0] == "plugins" and parts[1] in versions["plugins"]:
            required.add(parts[1])
        adapters = build_distributions.load_adapters(root)
        if len(parts) >= 3 and parts[0] == "platforms" and parts[1] in adapters and parts[2] in versions["plugins"]:
            required.add(parts[2])
        if len(parts) >= 3 and parts[0] == "dist" and parts[1] in adapters and parts[2] in versions["plugins"]:
            required.add(parts[2])
        if path in {".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"}:
            required.add(MARKETPLACE_COMPONENT)
    version_is_bootstrap = (
        versions["marketplace"] == BOOTSTRAP_VERSION
        and all(
            value == BOOTSTRAP_VERSION
            for value in versions["plugins"].values()
        )
    )
    version_registry_is_bootstrapped = any(
        status in {"A", "M"} and path == "versions.json"
        for status, path in changed
    )
    bootstrap = (
        allow_bootstrap
        and version_registry_is_bootstrapped
        and version_is_bootstrap
    )
    if not added and not bootstrap:
        raise ReleaseError("every normal pull request must add a .changes/*.json file")
    retired: set[str] = set()
    registry_retirement = False
    stable_retirement = False
    if version_registry_is_bootstrapped and not bootstrap:
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
        and not (
            bootstrap
            and path in {
                "versions.json", "CHANGELOG.md", ".release/stable.json"
            }
        )
        and not (path == "versions.json" and registry_retirement)
        and not (path == ".release/stable.json" and stable_retirement)
    }
    changed_existing_changesets = {
        path for status, path in changed
        if path.startswith(".changes/") and path.endswith(".json")
        and status != "A"
        and not (bootstrap and status == "D")
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
    if missing and not bootstrap:
        raise ReleaseError(
            "changeset omits changed release components: " + ", ".join(sorted(missing))
        )


def append_changelog(root: Path, plan: dict) -> None:
    path = root / "CHANGELOG.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Changelog\n"
    lines = ["", f"## {plan['marketplace']}", ""]
    for summary in plan["summaries"]:
        lines.append(f"- {summary}")
    path.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def changeset_paths_at_ref(root: Path, ref: str) -> set[str]:
    output = git(root, "ls-tree", "-r", "--name-only", ref, "--", ".changes")
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


def verify_bootstrap(root: Path) -> dict:
    problems = validate_version_surfaces(root)
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
    return versions


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
    pr_parser.add_argument("--allow-bootstrap", action="store_true")
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--stable-sha", required=True)
    prepare_parser.add_argument("--main-sha", required=True)
    verify_parser = sub.add_parser("verify-release")
    verify_parser.add_argument("--version")
    sub.add_parser("verify-bootstrap")
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
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
    elif args.command == "check-pr":
        check_pr_changeset(root, args.base, args.allow_bootstrap)
        print("release: pull request changeset valid")
    elif args.command == "prepare":
        released_paths = changeset_paths_at_ref(root, args.stable_sha)
        result = prepare(
            root, args.stable_sha, args.main_sha, released_paths=released_paths
        )
        print(json.dumps(result, indent=2))
    elif args.command == "verify-release":
        print(json.dumps(verify_release(root, args.version), indent=2))
    elif args.command == "verify-bootstrap":
        print(json.dumps(verify_bootstrap(root), indent=2))
    elif args.command == "release-notes":
        notes = release_notes(root, args.version)
        if args.output:
            args.output.write_text(notes, encoding="utf-8")
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
