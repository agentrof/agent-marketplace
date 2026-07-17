#!/usr/bin/env python3
"""Per-harness artifact generator for the Agent Marketplace.

Renders every committed harness artifact (Cursor and Codex manifests, agent
trees, hooks manifests, skill policy files, templates/AGENTS.md, the runtime
data file and the docs/harnesses.md matrix block) from the source tree and
tools/data/harnesses.json. `--check` fails (exit 1) when any committed
artifact is missing, stale or orphaned, which is the CI drift gate.

Stdlib only. Deterministic output: running twice is byte-identical.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--check", action="store_true",
                        help="fail if committed artifacts drift from the renderer")
    args = parser.parse_args()
    root = args.root.resolve()

    try:
        config = harness.load_config(root)
    except harness.HarnessConfigError as exc:
        print(f"generate: {exc}", file=sys.stderr)
        return 1

    if args.check:
        try:
            problems = harness.diff(root, config)
        except harness.HarnessConfigError as exc:
            print(f"generate: {exc}", file=sys.stderr)
            return 1
        if problems:
            for relpath, reason in problems:
                print(f"generate: {reason}: {relpath}", file=sys.stderr)
            print("generate: harness artifacts drifted; run `make generate`",
                  file=sys.stderr)
            return 1
        print("generate: harness artifacts are current")
        return 0

    try:
        changed = harness.write_all(root, config)
    except harness.HarnessConfigError as exc:
        print(f"generate: {exc}", file=sys.stderr)
        return 1
    if changed:
        for relpath in changed:
            print(f"generate: wrote {relpath}")
    else:
        print("generate: everything already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
