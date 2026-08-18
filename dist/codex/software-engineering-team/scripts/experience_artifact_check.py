#!/usr/bin/env python3
"""Verify approved Experience Design HTML artifacts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ID_RE = re.compile(r"^(?:JRN|FLW|SCR|STA|TRN)-[0-9]{3,}$")


def frontmatter(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("manifest is missing frontmatter")
    result: dict = {}
    current = ""
    for line in lines[1:]:
        if line == "---":
            return result
        value = line.strip()
        if not value:
            continue
        if value.startswith("- ") and current:
            result[current].append(value[2:].strip().strip("\"'"))
            continue
        if ":" not in value:
            raise ValueError("manifest frontmatter is not parseable")
        key, scalar = value.split(":", 1)
        current = key.strip() if not scalar.strip() else ""
        result[key.strip()] = ([] if not scalar.strip()
                               else scalar.strip().strip("\"'"))
    raise ValueError("manifest frontmatter is unterminated")


class Scanner(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.targets: list[str] = []
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for key in ("id", "data-experience-id"):
            value = values.get(key, "")
            if ID_RE.fullmatch(value):
                self.ids.add(value)
        if tag in {"a", "img", "script", "link", "iframe", "source"}:
            target = values.get("href") or values.get("src")
            if target:
                self.targets.append(target)
        if tag == "meta" and values.get("name", "").startswith("experience-"):
            self.metadata[values["name"]] = values.get("content", "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--registry", default="")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--declared-id", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    artifact = Path(args.artifact).resolve()
    release = Path(args.release_root).resolve()
    owner = (release / args.owner).resolve()
    findings: list[str] = []
    try:
        relative = artifact.relative_to(owner / "artifacts")
        release_relative = artifact.relative_to(release).as_posix()
    except ValueError:
        findings.append("artifact is not under the declared owning node artifacts directory")
        relative = artifact.name
        release_relative = ""
    if not artifact.is_file() or artifact.suffix.lower() != ".html":
        findings.append("artifact must be an existing HTML file")
        content = ""
    else:
        content = artifact.read_text(encoding="utf-8")
    scanner = Scanner()
    try:
        scanner.feed(content)
    except Exception as exc:
        findings.append(f"HTML parse failed: {exc}")
    for target in scanner.targets:
        parsed = urlparse(target)
        if parsed.scheme in {"http", "https", "ftp"} or target.startswith("//"):
            findings.append(f"remote request or asset is forbidden: {target}")
        if target.startswith("#") and target[1:] and target[1:] not in scanner.ids:
            findings.append(f"navigation target does not exist: {target}")
    manifest = (Path(args.manifest).resolve() if args.manifest else
                artifact.with_name(
                    artifact.name.removesuffix("-preview.html")
                    + "-artifact.md"))
    manifest_fm: dict = {}
    try:
        manifest_fm = frontmatter(manifest)
    except (OSError, ValueError) as exc:
        findings.append(f"artifact manifest is unreadable: {exc}")
    if manifest.parent != artifact.parent:
        findings.append("artifact manifest is not adjacent to the preview")
    if manifest_fm.get("type") != "artifact-manifest":
        findings.append("artifact manifest has the wrong document type")
    declared = set(args.declared_id)
    if not declared:
        values = manifest_fm.get("declared_ids", [])
        if isinstance(values, list):
            declared = {str(value) for value in values}
    findings.extend(f"declared id is absent from HTML: {value}" for value in sorted(declared - scanner.ids))
    findings.extend(f"HTML contains undeclared experience id: {value}" for value in sorted(scanner.ids - declared))
    registry_path = Path(args.registry) if args.registry else release / "_generated" / "effective-registry.json"
    registry = {}
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(f"registry is unreadable: {exc}")
    expected = {
        "experience-program": str(registry.get("program_id", "")),
        "experience-release": str(registry.get("release_id", "")),
        "experience-registry-hash": str(registry.get("registry_hash", "")),
    }
    for key, value in expected.items():
        if not value or scanner.metadata.get(key) != value:
            findings.append(f"metadata {key} does not match the effective registry")
    owner_note = next(
        (owner / name for name in ("domain.md", "space.md", "release.md")
         if (owner / name).is_file()),
        None,
    )
    manifest_target = manifest.relative_to(release.parents[4]).with_suffix("").as_posix()
    if (owner_note is None
            or f"[[{manifest_target}|" not in owner_note.read_text(encoding="utf-8")):
        findings.append("owning node note does not reference the artifact manifest")
    digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    if manifest_fm.get("artifact_path") != release_relative:
        findings.append("artifact path does not match the manifest")
    if manifest_fm.get("artifact_sha256") != digest:
        findings.append("artifact SHA-256 does not match the manifest")
    if manifest_fm.get("registry_hash") != registry.get("registry_hash"):
        findings.append("artifact registry hash does not match the manifest")
    if manifest_fm.get("program_id") != registry.get("program_id"):
        findings.append("artifact program does not match the manifest")
    if manifest_fm.get("release_id") != registry.get("release_id"):
        findings.append("artifact release does not match the manifest")
    registered = {
        str(item.get("path", "")): str(item.get("sha256", ""))
        for item in registry.get("artifacts", []) if isinstance(item, dict)
    }
    if not release_relative or registered.get(release_relative) != digest:
        findings.append("artifact SHA-256 does not match the effective registry")
    result = {"ok": not findings, "path": str(relative), "sha256": digest,
              "ids": sorted(scanner.ids), "findings": sorted(set(findings))}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for value in result["findings"]:
            print(f"ERROR {artifact}:1 [experience_artifact] {value}")
        if not findings:
            print(digest)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
