#!/usr/bin/env python3
"""Emit the validated runtime policy of the real host lifecycle job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
POLICY = "tools/data/ci-host-policy.json"
RUNNERS = {"macos-latest", "ubuntu-latest", "windows-latest"}


class PolicyError(ValueError):
    pass


def host_policy(root: Path) -> dict:
    try:
        policy = json.loads((root / POLICY).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read {POLICY}") from exc
    if not isinstance(policy, dict) \
            or set(policy) != {"schema_version", "runner_os", "python", "node"} \
            or policy.get("schema_version") != 1:
        raise PolicyError("unsupported host runtime policy")
    if policy["runner_os"] not in RUNNERS:
        raise PolicyError("unsupported host operating system")
    if not isinstance(policy["python"], str) \
            or re.fullmatch(r"[0-9]+\.[0-9]+", policy["python"]) is None:
        raise PolicyError("host Python policy must pin major.minor")
    if not isinstance(policy["node"], str) \
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", policy["node"]) is None:
        raise PolicyError("host Node policy is invalid")
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        policy = host_policy(args.root)
    except PolicyError as exc:
        print(f"ci-host-policy: {exc}", file=sys.stderr)
        return 1
    with args.github_output.open("a", encoding="utf-8") as output:
        for key in ("runner_os", "python", "node"):
            output.write(f"{key}={policy[key]}\n")
    print(json.dumps(policy, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
