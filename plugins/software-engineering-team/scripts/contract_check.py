#!/usr/bin/env python3
"""Structural contract check: every endpoint carries an error-cases block.

Endpoints are headings of the form '## VERB /path'. Each endpoint block
(until the next heading of the same or higher level) must mention its
error cases. Semantic completeness stays a human judgment; presence is a
machine fact and gates mechanically.

Stdlib only. Exit 0 when all endpoints comply, 1 on violations, 2 when no
endpoint is recognized (a contract with zero endpoints cannot pass a gate
that promises error-case completeness).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ENDPOINT_RE = re.compile(r"^(#{2,4})\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)", re.IGNORECASE)
ERROR_RE = re.compile(r"error\s+case", re.IGNORECASE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check error-case blocks per endpoint.")
    parser.add_argument("--contract", required=True, type=Path)
    args = parser.parse_args(argv)

    if not args.contract.is_file():
        print(f"contract_check: no contract at {args.contract}", file=sys.stderr)
        return 2
    lines = args.contract.read_text(encoding="utf-8", errors="replace").splitlines()

    endpoints: list[tuple[str, int, int]] = []  # (label, start, heading_level)
    for i, line in enumerate(lines):
        m = ENDPOINT_RE.match(line.strip())
        if m:
            endpoints.append((f"{m.group(2).upper()} {m.group(3)}", i, len(m.group(1))))
    if not endpoints:
        print("contract_check: no endpoints recognized (## VERB /path headings)", file=sys.stderr)
        return 2

    violations = []
    for idx, (label, start, level) in enumerate(endpoints):
        end = len(lines)
        for j in range(start + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped.startswith("#") and len(stripped) - len(stripped.lstrip("#")) >= 0:
                hashes = len(stripped) - len(stripped.lstrip("#"))
                if 0 < hashes <= level:
                    end = j
                    break
        block = "\n".join(lines[start:end])
        if not ERROR_RE.search(block):
            violations.append(label)

    if violations:
        for label in violations:
            print(f"contract_check: FAIL: {label} declares no error cases", file=sys.stderr)
        return 1
    print(f"contract_check: OK: {len(endpoints)} endpoints, all carry error cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
