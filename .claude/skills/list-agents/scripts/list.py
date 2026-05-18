#!/usr/bin/env python3
"""Enumerate subagents under .claude/agents/ and emit JSON + Markdown listings.

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


def parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a Claude Code subagent file."""
    out: dict = {"name": None, "description": None, "tools": None, "model": None}
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening --- frontmatter marker")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("missing closing --- frontmatter marker")
    for raw_line in lines[1:end]:
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in out:
            out[key] = value
    return out


def collect(root: Path) -> list[dict]:
    agents_dir = root / ".claude" / "agents"
    if not agents_dir.is_dir():
        return []
    entries: list[dict] = []
    for child in sorted(agents_dir.iterdir()):
        if not child.is_file() or child.suffix != ".md" or child.name.startswith("."):
            continue
        record: dict = {"file": child.name, "id_from_filename": child.stem}
        try:
            parsed = parse_frontmatter(child)
            record.update(parsed)
        except Exception as exc:
            record["error"] = f"frontmatter parse failure: {exc}"
        entries.append(record)
    return entries


def to_markdown(entries: list[dict]) -> str:
    lines = ["# Agent Catalog", "", f"Total: {len(entries)}", ""]
    if not entries:
        lines.append("_Empty._")
        return "\n".join(lines) + "\n"
    lines.append("| name | description | tools | model |")
    lines.append("|---|---|---|---|")
    for e in entries:
        if "error" in e:
            lines.append(f"| {e.get('id_from_filename')} | ERROR: {e['error']} | - | - |")
            continue
        lines.append(
            f"| {e.get('name') or '-'} | {e.get('description') or '-'} | "
            f"{e.get('tools') or '-'} | {e.get('model') or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=None)
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
