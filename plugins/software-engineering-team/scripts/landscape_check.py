#!/usr/bin/env python3
"""Solution-tree integrity check: mechanical half of the SOLUTION gate.

Verifies over workspace/docs/solution-design/:
- decision-log.md: bare-id headings (## SD-###), unique ids, and a
  Status field per record.
- landscape.md Components rows: every decision link targets an existing
  record whose status is not superseded.
- landscape.md Target: every non-empty body line cites an SD id.
- engagements/*.md: the Summary carries a valid Status first body line,
  never dated in the future (stamps come from the clock).

Stamp mode (--stamp-engagement <slug> --status ... [--reason ...])
rewrites an engagement's Status line with the current UTC date, then
falls through to the full check; when the check fails, the stamp is
rolled back, so a stamp can never leave an inconsistent tree. It only
replaces a valid open/parked Status line: closed engagements (approved,
superseded) are append-only, and a Summary without a Status line is a
doc defect to fix first. The model never types the date.

Stdlib only. Exit 0 clean, 1 on findings, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
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


def utc_today():
    """This script's single clock read."""
    return datetime.now(timezone.utc).date()


def stamp_engagement(tree: Path, slug: str, status: str,
                     reason: str) -> tuple[int, Path | None, str | None]:
    """Rewrite the engagement's Status line with the current UTC date.
    Returns (exit code, stamped doc, original text) so the caller can
    roll the stamp back when the fall-through check fails."""
    doc = tree / "engagements" / f"{slug}.md"
    if not doc.is_file():
        print(f"landscape_check: no engagement at {doc}", file=sys.stderr)
        return 2, None, None
    today = utc_today().isoformat()
    if status == "approved":
        line = f"Status: approved {today}"
    elif status == "parked":
        line = f"Status: parked {today}: {reason}"
    else:
        line = "Status: open"
    original = doc.read_text(encoding="utf-8")
    lines = original.splitlines()
    heading = next((i for i, l in enumerate(lines)
                    if re.match(r"^##\s+Summary\s*$", l)), None)
    target_idx = None
    if heading is not None:
        for i in range(heading + 1, len(lines)):
            if lines[i].startswith("## "):
                break
            if lines[i].strip():
                target_idx = i
                break
    if target_idx is None or not ENGAGEMENT_STATUS_RE.match(
            lines[target_idx].strip()):
        print(f"landscape_check: engagements/{doc.name}: Summary's first"
              " body line is not a valid Status line; fix the doc before"
              " stamping", file=sys.stderr)
        return 2, None, None
    current = lines[target_idx].strip()
    if current.startswith("Status: approved") \
            or current.startswith("Status: superseded"):
        print(f"landscape_check: engagements/{doc.name} is closed"
              f" ({current}); closed engagements are append-only, a"
              " reopened topic gets a new engagement (-2 suffix)",
              file=sys.stderr)
        return 1, None, None
    lines[target_idx] = line
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"landscape_check: stamped engagements/{doc.name}: {line}")
    return 0, doc, original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check solution-tree integrity.")
    parser.add_argument("--tree", required=True, help="solution-design directory")
    parser.add_argument("--stamp-engagement", default="", metavar="SLUG")
    parser.add_argument("--status", choices=["approved", "parked", "open"],
                        default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    tree = Path(args.tree)
    if not tree.is_dir():
        print(f"landscape_check: no tree at {tree}", file=sys.stderr)
        return 2

    stamped_doc: Path | None = None
    stamped_original: str | None = None
    if args.stamp_engagement:
        if not args.status:
            print("landscape_check: --stamp-engagement requires --status",
                  file=sys.stderr)
            return 2
        if args.status == "parked" and not args.reason.strip():
            print("landscape_check: --status parked requires --reason",
                  file=sys.stderr)
            return 2
        code, stamped_doc, stamped_original = stamp_engagement(
            tree, args.stamp_engagement, args.status, args.reason.strip())
        if code:
            return code

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
        today = utc_today()
        for doc in sorted(engagements.glob("*.md")):
            summary = section(doc.read_text(encoding="utf-8"), "Summary")
            first_line = next((l.strip() for l in summary.splitlines() if l.strip()), "")
            if not ENGAGEMENT_STATUS_RE.match(first_line):
                findings.append(f"engagements/{doc.name}: Summary's first line is not a valid Status line")
                continue
            dated = re.search(r"(?:approved|parked) (\d{4}-\d{2}-\d{2})",
                              first_line)
            if dated:
                try:
                    stamped = date.fromisoformat(dated.group(1))
                except ValueError:
                    stamped = None
                if stamped is None or stamped > today:
                    findings.append(
                        f"engagements/{doc.name}: Status date"
                        f" {dated.group(1)} is not a past-or-today calendar"
                        " date; stamps come from the clock"
                        " (landscape_check.py --stamp-engagement)")

    if findings:
        if stamped_doc is not None and stamped_original is not None:
            stamped_doc.write_text(stamped_original, encoding="utf-8")
            print(f"landscape_check: stamp on engagements/{stamped_doc.name}"
                  " rolled back (the tree has findings)", file=sys.stderr)
        print("landscape_check: FAIL", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"landscape_check: OK: {len(records)} records, tree consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
