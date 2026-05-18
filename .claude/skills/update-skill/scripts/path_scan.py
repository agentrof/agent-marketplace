#!/usr/bin/env python3
"""Grep for stale references to an old path across marketplace text files.

Run when an update moves or renames a file inside the target. Greps for the
old repo-root-relative path string and reports any remaining references.

Usage:
    path_scan.py --old-path <repo-rel-old> [--new-path <repo-rel-new>] [--output PATH]

Output JSON:
{
  "old_path": "<repo-rel>",
  "new_path": "<repo-rel-or-null>",
  "references": [
    {"file": "<repo-rel>", "line": <int>, "text": "<line>"}
  ]
}
"""

import argparse
import json
import os
import sys
from pathlib import Path


SCAN_NAMES = {"SKILL.md", "README.md", "manifest.yaml"}
SCAN_EXTS = {".md", ".yaml", ".yml", ".json", ".py"}


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
        if path.name == ".gitkeep":
            continue
        if path.name in SCAN_NAMES or path.suffix in SCAN_EXTS:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-path", required=True)
    parser.add_argument("--new-path", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    root = repo_root()
    refs: list[dict] = []
    for path in iter_scan_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = str(path.relative_to(root))
        # Skip the file itself if it is the one being moved.
        if rel == args.old_path or rel == args.new_path:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if args.old_path in line:
                refs.append({
                    "file": rel,
                    "line": lineno,
                    "text": line.rstrip(),
                })

    result = {
        "old_path": args.old_path,
        "new_path": args.new_path,
        "references": refs,
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
