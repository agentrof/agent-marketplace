#!/usr/bin/env python3
"""Ownership-map overlap check: the model gate's mechanical half.

Reads the ownership map from state.json and fails when any two roles own
overlapping paths (equal, or one a path-prefix of the other). Overlap is
machine-decidable; it must never pass a gate on a reading of prose.

Stdlib only. Exit 0 when disjoint, 1 on overlap, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path


def parts(path: str) -> tuple[str, ...]:
    return tuple(p for p in path.strip().strip("/").split("/") if p)


def overlaps(a: str, b: str) -> bool:
    pa, pb = parts(a), parts(b)
    shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return longer[: len(shorter)] == shorter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on overlapping ownership paths.")
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ownership_check: cannot read state: {exc}", file=sys.stderr)
        return 2
    ownership = state.get("ownership", {})
    if not ownership:
        print("ownership_check: FAIL: ownership map is empty", file=sys.stderr)
        return 1

    entries = [(role, path) for role, paths in ownership.items() for path in paths]
    conflicts = [
        f"{ra}:{a} overlaps {rb}:{b}"
        for (ra, a), (rb, b) in combinations(entries, 2)
        if ra != rb and overlaps(a, b)
    ]
    if conflicts:
        for line in conflicts:
            print(f"ownership_check: FAIL: {line}", file=sys.stderr)
        return 1
    print(f"ownership_check: OK: {len(entries)} paths across {len(ownership)} roles, disjoint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
