#!/usr/bin/env python3
"""Grep target id (and optional rename string) across marketplace text files.

Scans every SKILL.md, AGENT subagent .md (under .claude/agents/), README.md,
manifest.yaml under the marketplace and returns structured findings.

Usage:
    content_scan.py --target <id> [--rename-from STR] [--rename-to STR] [--output PATH]

Output JSON:
{
  "target": "<id>",
  "rename_from": "<str-or-null>",
  "rename_to": "<str-or-null>",
  "matches": [
    {"file": "<repo-rel-path>", "line": <int>, "match_type": "id"|"rename", "text": "<line>"}
  ]
}
"""

import argparse
import json
import os
import sys
from pathlib import Path


SCAN_NAMES = {"SKILL.md", "README.md", "manifest.yaml"}


def repo_root() -> Path:
    env = os.environ.get("MARKETPLACE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def iter_scan_files(root: Path):
    base = root / ".claude"
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") and part not in {".claude", ".gitkeep"} for part in path.parts):
            continue
        if path.name in SCAN_NAMES:
            yield path
            continue
        # Subagent files: any .md directly under .claude/agents/
        if path.parent == base / "agents" and path.suffix == ".md":
            yield path


def scan_file(path: Path, needles: list[tuple[str, str]], root: Path) -> list[dict]:
    """needles: list of (label, string) pairs to look for."""
    findings: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings
    rel = str(path.relative_to(root))
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, needle in needles:
            if needle and needle in line:
                findings.append({
                    "file": rel,
                    "line": lineno,
                    "match_type": label,
                    "text": line.rstrip(),
                })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--rename-from", default=None)
    parser.add_argument("--rename-to", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    root = repo_root()
    needles: list[tuple[str, str]] = [("id", args.target)]
    if args.rename_from:
        needles.append(("rename", args.rename_from))

    matches: list[dict] = []
    for path in iter_scan_files(root):
        matches.extend(scan_file(path, needles, root))

    result = {
        "target": args.target,
        "rename_from": args.rename_from,
        "rename_to": args.rename_to,
        "matches": matches,
    }
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
