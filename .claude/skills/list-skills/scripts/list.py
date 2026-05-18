#!/usr/bin/env python3
"""Enumerate skills under .claude/skills/ and emit JSON + Markdown listings.

Usage:
    list.py                                 # print Markdown table to stdout
    list.py --output-dir <dir>              # write listing.json and listing.md into <dir>
    list.py --format json                   # print JSON to stdout
"""

import argparse
import json
import os
import sys
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("MARKETPLACE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def parse_manifest(path: Path) -> dict:
    """Lightweight manifest.yaml parser. Avoids requiring PyYAML."""
    out: dict = {"id": None, "kind": None, "version": None, "description": None, "tags": [], "depends_on": []}
    raw = path.read_text(encoding="utf-8")
    current_list_key: str | None = None
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_list_key:
            out[current_list_key].append(line[4:].strip())
            continue
        if line.startswith("- ") and current_list_key:
            out[current_list_key].append(line[2:].strip())
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in ("tags", "depends_on"):
            current_list_key = key
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                out[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
                current_list_key = None
            else:
                out[key] = []
        elif key in out:
            current_list_key = None
            out[key] = value
    return out


def collect(root: Path) -> list[dict]:
    skills_dir = root / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []
    entries: list[dict] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest = child / "manifest.yaml"
        record: dict = {"folder": child.name, "manifest_present": manifest.is_file()}
        if not manifest.is_file():
            record["error"] = "manifest.yaml missing"
            entries.append(record)
            continue
        try:
            parsed = parse_manifest(manifest)
            record.update(parsed)
        except Exception as exc:
            record["error"] = f"parse failure: {exc}"
        entries.append(record)
    return entries


def to_markdown(entries: list[dict]) -> str:
    lines = ["# Skill Catalog", "", f"Total: {len(entries)}", ""]
    if not entries:
        lines.append("_Empty._")
        return "\n".join(lines) + "\n"
    lines.append("| id | version | description | tags | depends_on |")
    lines.append("|---|---|---|---|---|")
    for e in entries:
        if "error" in e:
            lines.append(f"| {e.get('folder')} | - | ERROR: {e['error']} | - | - |")
            continue
        tags = ", ".join(e.get("tags", []) or []) or "-"
        deps = ", ".join(e.get("depends_on", []) or []) or "-"
        lines.append(
            f"| {e.get('id') or '-'} | {e.get('version') or '-'} | "
            f"{e.get('description') or '-'} | {tags} | {deps} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=None, help="Write listing.json and listing.md here.")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    args = parser.parse_args()

    root = repo_root()
    entries = collect(root)

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "listing.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        (out_dir / "listing.md").write_text(to_markdown(entries), encoding="utf-8")
        print(f"wrote {out_dir / 'listing.json'}")
        print(f"wrote {out_dir / 'listing.md'}")
        return 0

    if args.format == "json":
        print(json.dumps(entries, indent=2))
    else:
        print(to_markdown(entries), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
