#!/usr/bin/env python3
"""Mechanical post-step artifact check for document workflows.

The artifact must exist, be non-empty, and carry every required section
heading. This turns the flow's post-step check into a script with an exit
code instead of an orchestrator judgment.

Stdlib only. Exit 0 when the artifact passes, 1 when it fails, 2 on usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an artifact's existence and sections.")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument(
        "--require-sections", default="",
        help="comma-separated heading texts the artifact must contain",
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"artifact_check: FAIL: no artifact at {args.path}", file=sys.stderr)
        return 1
    text = args.path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        print(f"artifact_check: FAIL: artifact is empty: {args.path}", file=sys.stderr)
        return 1

    headings = [
        re.sub(r"^#{1,6}\s*", "", line).strip().lower()
        for line in text.splitlines()
        if line.lstrip().startswith("#")
    ]
    missing = []
    for wanted in [s.strip() for s in args.require_sections.split(",") if s.strip()]:
        if not any(wanted.lower() in h for h in headings):
            missing.append(wanted)
    if missing:
        print(
            f"artifact_check: FAIL: {args.path} is missing sections: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    print(f"artifact_check: OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
