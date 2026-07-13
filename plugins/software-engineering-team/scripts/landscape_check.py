#!/usr/bin/env python3
"""Solution-tree integrity check: mechanical half of the SOLUTION gate.

Verifies over workspace/docs/solution-design/:
- decision-log.md: bare-id headings (## SD-###), unique ids, and a
  Status field per record.
- landscape.md Components rows: every decision link targets an existing
  record whose status is not superseded.
- landscape.md Target: every non-empty body line cites an SD id.
- engagements/*.md: the Summary carries a valid Status first body line.

Stdlib only. Exit 0 clean, 1 on findings, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SD_HEADING_RE = re.compile(r"^##\s+(SD-\d{3,})\s*$", re.MULTILINE)
SD_TITLED_HEADING_RE = re.compile(r"^##\s+SD-\d{3,}\s*:", re.MULTILINE)
STATUS_FIELD_RE = re.compile(r"\*\*Status:\*\*\s*(\w+)")
SD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:\.\./)?decision-log\.md#(sd-\d{3,})\)")
SD_CITE_RE = re.compile(r"SD-\d{3,}")
ENGAGEMENT_STATUS_RE = re.compile(
    r"^Status: (open|approved \d{4}-\d{2}-\d{2}"
    r"|parked \d{4}-\d{2}-\d{2}: .+|superseded by [a-z0-9-]+)$"
)


def section(text: str, name: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(name)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check solution-tree integrity.")
    parser.add_argument("--tree", required=True, help="solution-design directory")
    args = parser.parse_args(argv)
    tree = Path(args.tree)
    if not tree.is_dir():
        print(f"landscape_check: no tree at {tree}", file=sys.stderr)
        return 2

    findings: list[str] = []
    log_path = tree / "decision-log.md"
    records: dict[str, str] = {}
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8")
        if SD_TITLED_HEADING_RE.search(log_text):
            findings.append("decision-log.md: a record heading carries a title;"
                            " headings are bare ids (## SD-001)")
        ids = SD_HEADING_RE.findall(log_text)
        for rid in set(ids):
            if ids.count(rid) > 1:
                findings.append(f"decision-log.md: duplicate id {rid}")
        for match in SD_HEADING_RE.finditer(log_text):
            body = log_text[match.end():]
            nxt = SD_HEADING_RE.search(body)
            body = body[: nxt.start()] if nxt else body
            status = STATUS_FIELD_RE.search(body)
            if not status:
                findings.append(f"decision-log.md: {match.group(1)} has no Status field")
                continue
            records[match.group(1).lower()] = status.group(1).lower()
    else:
        findings.append("decision-log.md missing")

    land_path = tree / "landscape.md"
    if land_path.is_file():
        land_text = land_path.read_text(encoding="utf-8")
        components = section(land_text, "Components")
        for line in components.splitlines():
            if not line.strip().startswith("|") or set(line.strip()) <= {"|", "-", " "}:
                continue
            if "component" in line.lower() and "verdict" in line.lower():
                continue
            links = SD_LINK_RE.findall(line)
            if not links:
                findings.append(f"landscape.md Components row without a decision link: {line.strip()[:60]}")
                continue
            for _, anchor in links:
                status = records.get(anchor)
                if status is None:
                    findings.append(f"landscape.md Components cites missing record {anchor}")
                elif status == "superseded":
                    findings.append(f"landscape.md Components cites superseded record {anchor}")
        target = section(land_text, "Target")
        for line in target.splitlines():
            stripped = line.strip()
            if stripped and not SD_CITE_RE.search(stripped):
                findings.append(f"landscape.md Target delta cites no decision: {stripped[:60]}")
    else:
        findings.append("landscape.md missing")

    engagements = tree / "engagements"
    if engagements.is_dir():
        for doc in sorted(engagements.glob("*.md")):
            summary = section(doc.read_text(encoding="utf-8"), "Summary")
            first_line = next((l.strip() for l in summary.splitlines() if l.strip()), "")
            if not ENGAGEMENT_STATUS_RE.match(first_line):
                findings.append(f"engagements/{doc.name}: Summary's first line is not a valid Status line")

    if findings:
        print("landscape_check: FAIL", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"landscape_check: OK: {len(records)} records, tree consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
