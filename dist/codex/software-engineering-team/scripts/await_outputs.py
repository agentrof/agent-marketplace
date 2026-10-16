#!/usr/bin/env python3
"""Wait in the caller's own turn until background work has written its results.

A role that starts background agents or commands cannot rely on the host to
wake it when they finish: a host may deliver the completion notice to the top
session instead of the role that started the work. The role names each
output file the work writes and calls this command, which returns as soon as
every file holds a line matching the final-result marker, and never blocks
longer than the Delivery runner's wait bound, so the next model call finds
the prompt cache warm. A call that returns pending is repeated at once.

Exit codes: 0 every output is final, 3 some output is still pending, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

POLICY_PATH = (Path(__file__).resolve().parents[1]
               / "skill-content/deliver/data/delivery-verification-policy.json")
DEFAULT_MARKER = r"^SELF-CHECK:"
POLL_SECONDS = 0.5
PENDING = 3


def wait_bound() -> float:
    return float(json.loads(POLICY_PATH.read_text(encoding="utf-8"))["wait_bound_seconds"])


def final(path: Path, marker: re.Pattern) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return marker.search(text) is not None


def await_outputs(files: list[Path], marker: re.Pattern, seconds: float) -> dict:
    started = time.monotonic()
    while True:
        pending = [path for path in files if not final(path, marker)]
        elapsed = time.monotonic() - started
        if not pending or elapsed >= seconds:
            break
        time.sleep(min(POLL_SECONDS, seconds - elapsed))
    value = {"done": [str(path) for path in files if path not in pending],
             "pending": [str(path) for path in pending],
             "missing": [str(path) for path in pending if not path.exists()],
             "waited_seconds": round(elapsed, 3), "bound_seconds": seconds}
    if pending:
        value["next"] = "call await_outputs.py again now, as a tool call of its own"
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", action="append", required=True, type=Path,
                        help="an output file the background work writes; repeat per output")
    parser.add_argument("--marker", default=DEFAULT_MARKER,
                        help=f"regular expression of a final-result line (default {DEFAULT_MARKER!r})")
    parser.add_argument("--seconds", type=float, help="the most this call blocks; at most the wait bound")
    args = parser.parse_args(argv)
    bound = wait_bound()
    seconds = bound if args.seconds is None else args.seconds
    try:
        marker = re.compile(args.marker, re.MULTILINE)
    except re.error as exc:
        parser.error(f"--marker is no regular expression: {exc}")
    if not 0 < seconds <= bound:
        parser.error(f"one call blocks for more than 0 and at most {bound:g} seconds;"
                     " call again to wait longer")
    value = await_outputs([path.resolve() for path in args.file], marker, seconds)
    print(json.dumps(value, indent=2))
    return PENDING if value["pending"] else 0


if __name__ == "__main__":
    sys.exit(main())
