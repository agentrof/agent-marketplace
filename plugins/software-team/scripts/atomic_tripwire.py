#!/usr/bin/env python3
"""Atomic-route tripwire: mechanical escape-hatch enforcement.

Scans the changed-file list of an atomic branch for model, contract or
schema touches. A hit means the work was never atomic: the flow must stop,
report "not atomic", and hand the request to the large route. The check is
structural so a mis-classification is caught by a script, not by the
orchestrating model's judgment.

Stdlib only. Exit 0 when clean, 1 on a tripwire hit, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TRIPWIRES = [
    (re.compile(r"(^|/)migrations?/", re.IGNORECASE), "database migration"),
    (re.compile(r"\.sql$", re.IGNORECASE), "raw schema file"),
    (re.compile(r"(^|/)models?/", re.IGNORECASE), "persistence model"),
    (re.compile(r"docs/system-architecture/", re.IGNORECASE), "living architecture document"),
    (re.compile(r"openapi|api-contract", re.IGNORECASE), "interface contract"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect model/contract/schema touches on an atomic branch.")
    parser.add_argument("--repo", default=".", help="git repository root")
    parser.add_argument("--range", default="", help="git diff range, e.g. main...HEAD")
    parser.add_argument("--files", nargs="*", default=[], help="explicit changed-file list (overrides git)")
    args = parser.parse_args(argv)

    files = list(args.files)
    if not files:
        if not args.range:
            print("atomic_tripwire: provide --range or --files", file=sys.stderr)
            return 2
        proc = subprocess.run(
            ["git", "-C", args.repo, "diff", "--name-only", args.range],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"atomic_tripwire: git diff failed: {proc.stderr.strip()}", file=sys.stderr)
            return 2
        files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    hits = []
    for path in files:
        for pattern, label in TRIPWIRES:
            if pattern.search(path):
                hits.append(f"{path} ({label})")
                break
    if hits:
        print("atomic_tripwire: NOT ATOMIC. The change touches:", file=sys.stderr)
        for hit in hits:
            print(f"  - {hit}", file=sys.stderr)
        print("atomic_tripwire: stop, report not atomic, hand over to the large route", file=sys.stderr)
        return 1
    print(f"atomic_tripwire: OK: {len(files)} changed files, no model/contract/schema touch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
