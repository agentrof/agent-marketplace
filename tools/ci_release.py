#!/usr/bin/env python3
"""Conservatively prove that a replayed release changed metadata only.

This is a scope classifier, not release authorization. Callers must first run
release.py verify-release-pr from the trusted base. Any unrecognized delta
selects the full test suite.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

import build_distributions
import release


class ClassificationError(ValueError):
    pass


def git_bytes(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=root, env=release.hermetic_git_environment(),
        input=data, capture_output=True, check=False,
    )
    if result.returncode:
        raise ClassificationError("cannot read release Git objects")
    return result.stdout


def tree_entries(root: Path, ref: str) -> dict[str, tuple[str, str]]:
    result = {}
    for record in git_bytes(root, "ls-tree", "-rz", ref).split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ClassificationError("tree contains unsupported object types or modes")
        result[path.decode("utf-8")] = (mode, object_id)
    return result


def read_blobs(root: Path, entries: dict[str, tuple[str, str]]) -> dict[str, bytes]:
    object_ids = sorted({entry[1] for entry in entries.values()})
    raw = git_bytes(
        root, "cat-file", "--batch",
        data=("\n".join(object_ids) + "\n").encode("ascii"),
    )
    objects = {}
    offset = 0
    for expected in object_ids:
        end = raw.index(b"\n", offset)
        actual, kind, size = raw[offset:end].decode("ascii").split()
        if actual != expected or kind != "blob":
            raise ClassificationError("invalid release blob response")
        offset = end + 1
        objects[actual] = raw[offset:offset + int(size)]
        offset += int(size) + 1
    if offset != len(raw):
        raise ClassificationError("unexpected release blob response")
    return {path: objects[entry[1]] for path, entry in entries.items()}


def json_object(blobs: dict[str, bytes], path: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ClassificationError(f"duplicate JSON key in {path}")
            result[key] = value
        return result

    value = json.loads(blobs[path], object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ClassificationError(f"expected JSON object in {path}")
    return value


def versions(blobs: dict[str, bytes]) -> dict:
    value = json_object(blobs, "versions.json")
    if set(value) != {"schema_version", "marketplace", "plugins"} or value["schema_version"] != 1:
        raise ClassificationError("unsupported version registry")
    if not isinstance(value["plugins"], dict):
        raise ClassificationError("invalid plugin registry")
    for version in [value["marketplace"], *value["plugins"].values()]:
        release.parse_semver(version)
    return value


def manifest_equal(before: dict, after: dict, old: str, new: str) -> bool:
    if before.get("version") != old or after.get("version") != new:
        return False
    return {key: value for key, value in before.items() if key != "version"} == {
        key: value for key, value in after.items() if key != "version"
    }


def catalog_equal(before: dict, after: dict, adapter, old: dict, new: dict) -> bool:
    expected = copy.deepcopy(before)
    adapter.module.sync_catalog_metadata(expected, new["marketplace"])
    entries = expected.get("plugins")
    if not isinstance(entries, list):
        return False
    if {entry.get("name") for entry in entries} != set(new["plugins"]):
        return False
    for entry in entries:
        plugin = entry["name"]
        adapter.module.sync_catalog_entry(entry, plugin, new["plugins"][plugin])
    if expected != after:
        return False

    # Adapter catalog writers may set sources too. Only the established
    # catalog version fields can be ignored by a runtime-equivalence proof.
    def strip(value):
        value = copy.deepcopy(value)
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and "version" in metadata:
            metadata["version"] = "VERSION"
        for entry in value["plugins"]:
            if "version" in entry:
                entry["version"] = "VERSION"
        return value

    return strip(before) == strip(after)


def provenance_valid(
    blobs: dict[str, bytes], entries: dict[str, tuple[str, str]],
    package: str, filename: str, component: str, host: str, registry: dict,
) -> dict:
    path = f"{package}/{filename}"
    value = json_object(blobs, path)
    keys = {
        "schema_version", "component", "host", "version", "build_id",
        "marketplace_release", "source_channel", "source_ref", "source_commit",
        "files", "executables", "runtime_contracts", "delivery_protocol",
    }
    if set(value) != keys or value["schema_version"] != 3:
        raise ClassificationError(f"unsupported package provenance: {path}")
    if (
        value["component"] != component or value["host"] != host
        or value["version"] != registry["plugins"][component]
        or value["marketplace_release"] != registry["marketplace"]
        or value["source_channel"] != "snapshot" or value["source_commit"] != ""
        or not isinstance(value["build_id"], str)
        or not re.fullmatch(r"snapshot\.[0-9a-f]{64}", value["build_id"])
        or value["source_ref"] != value["build_id"]
    ):
        raise ClassificationError(f"invalid package provenance: {path}")
    prefix = package + "/"
    payload = {
        name[len(prefix):]: content for name, content in blobs.items()
        if name.startswith(prefix) and name != path
    }
    expected = {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()}
    executable = sorted(
        name for name in payload if entries[prefix + name][0] == "100755"
    )
    if value["files"] != expected or value["executables"] != executable:
        raise ClassificationError(f"package inventory or modes differ: {path}")
    return {
        key: value[key] for key in (
            "schema_version", "component", "host", "runtime_contracts",
            "delivery_protocol", "executables",
        )
    }


def _classify(root: Path, base: str, head: str) -> None:
    release.require_sha(base, "release base")
    release.require_sha(head, "release head")
    release.reject_graph_overlays(root, release.hermetic_git_environment())
    if git_bytes(root, "rev-parse", "HEAD").decode().strip() != base:
        raise ClassificationError("classifier must execute from the trusted base")
    before_entries = tree_entries(root, base)
    after_entries = tree_entries(root, head)
    changed = {
        path for path in set(before_entries) | set(after_entries)
        if before_entries.get(path) != after_entries.get(path)
    }
    if not changed:
        raise ClassificationError("candidate contains no release transformation")
    before = read_blobs(root, before_entries)
    after = read_blobs(root, after_entries)
    old, new = versions(before), versions(after)
    if set(old["plugins"]) != set(new["plugins"]):
        raise ClassificationError("release changes the plugin registry")
    adapters = build_distributions.load_adapters(root)
    _, provenance_name = build_distributions.packaging_names(root)
    manifests = {}
    catalogs = {}
    provenance = {}
    for host, adapter in adapters.items():
        if adapter.metadata.get("marketplace_catalog"):
            path = adapter.module.marketplace_catalog_path(root).relative_to(root).as_posix()
            catalogs[path] = adapter
        for plugin in old["plugins"]:
            package = f"dist/{host}/{plugin}"
            provenance[f"{package}/{provenance_name}"] = (package, plugin, host)
            if adapter.metadata["artifact_kind"] == "native_marketplace":
                manifests[f"platforms/{host}/{plugin}/manifest.json"] = plugin
                directory = adapter.module.native_manifest_directory(host)
                manifests[f"{package}/{directory}/plugin.json"] = plugin

    for path in sorted(changed):
        old_entry, new_entry = before_entries.get(path), after_entries.get(path)
        if old_entry and new_entry and old_entry[0] != new_entry[0]:
            raise ClassificationError(f"release changes an executable mode: {path}")
        if path.startswith(".changes/") and Path(path).parent.as_posix() == ".changes" and path.endswith(".json"):
            if old_entry and new_entry is None:
                continue
            raise ClassificationError(f"release adds or modifies a changeset: {path}")
        if path == ".release/stable.json":
            if new_entry and new_entry[0] == "100644":
                continue
            raise ClassificationError("release metadata is missing or executable")
        if not old_entry or not new_entry:
            raise ClassificationError(f"release changes the runtime file inventory: {path}")
        if path in {"versions.json", "CHANGELOG.md"}:
            continue
        if path in manifests:
            plugin = manifests[path]
            if manifest_equal(json_object(before, path), json_object(after, path), old["plugins"][plugin], new["plugins"][plugin]):
                continue
            raise ClassificationError(f"release changes manifest content beyond version: {path}")
        if path in catalogs:
            if catalog_equal(json_object(before, path), json_object(after, path), catalogs[path], old, new):
                continue
            raise ClassificationError(f"release changes catalog content beyond version: {path}")
        if path in provenance:
            continue
        raise ClassificationError(f"release changes runtime or an unknown path: {path}")

    for package, plugin, host in provenance.values():
        left = provenance_valid(before, before_entries, package, provenance_name, plugin, host, old)
        right = provenance_valid(after, after_entries, package, provenance_name, plugin, host, new)
        if left != right:
            raise ClassificationError(f"release changes package runtime contracts: {package}")


def classify(root: Path, base: str, head: str) -> dict:
    result = {
        "schema_version": 1, "base_sha": base, "head_sha": head,
        "release_only": False,
    }
    try:
        _classify(root, base, head)
    except (ClassificationError, release.ReleaseError, ValueError, TypeError, KeyError, OSError) as exc:
        result["reason"] = str(exc)
    else:
        result["release_only"] = True
        result["reason"] = "only release metadata and version fields changed; runtime bytes and modes are identical"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("classify")
    command.add_argument("--base", required=True)
    command.add_argument("--head", required=True)
    command.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = classify(Path(__file__).resolve().parents[1], arguments.base, arguments.head)
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
