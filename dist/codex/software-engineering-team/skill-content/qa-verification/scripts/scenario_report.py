#!/usr/bin/env python3
"""Deterministic coverage audit: planned identities vs JUnit test results.

Extracts canonical qualified or unqualified BA identities and story-scenario
identities from planned Markdown files, maps them to test cases in JUnit XML
files (identity present in the test name, class name, or property values,
case-insensitive), and prints a coverage matrix with PASS/FAIL/NO-TEST per
identity plus a machine-readable summary line.

Exit code 0 when every id has at least one passing, non-skipped test and no
mapped test failed; exit code 1 when any NO-TEST or FAIL row exists; exit
code 2 on usage or input errors.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ID_RE = re.compile(
    r"(?<![A-Za-z0-9_:-])(?:"
    r"(?:[a-z0-9]+(?:-[a-z0-9]+)*:)?(?:BR|AC)-(?:[A-Z0-9]+-)*[0-9]+"
    r"|[A-Z][A-Z0-9]*-[0-9]{2,}-TS-[0-9]+"
    r")(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


def extract_ids(brief_paths: list[Path]) -> list[str]:
    """Return planned identities in first-seen order, normalized to upper case."""
    seen: dict[str, None] = {}
    for path in brief_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ID_RE.finditer(text):
            seen.setdefault(match.group(0).upper(), None)
    return list(seen)


def iter_testcases(root: ET.Element):
    """Yield every <testcase> under a <testsuite> or <testsuites> root."""
    if root.tag == "testcase":
        yield root
    for case in root.iter("testcase"):
        yield case


def case_identity(case: ET.Element) -> str:
    """Concatenate every string a tag could have been rendered into."""
    parts = [case.get("name", ""), case.get("classname", "")]
    for prop in case.iter("property"):
        parts.append(prop.get("name", ""))
        parts.append(prop.get("value", ""))
        if prop.text:
            parts.append(prop.text)
    return " ".join(parts)


def case_status(case: ET.Element) -> str:
    """Return 'failed', 'skipped', or 'passed' for one test case."""
    for child in case:
        tag = child.tag.lower()
        if tag in ("failure", "error"):
            return "failed"
        if tag == "skipped":
            return "skipped"
    return "passed"


def collect_tests(junit_paths: list[Path]) -> list[tuple[str, str, str]]:
    """Return (display_name, identity, status) for every test case."""
    tests: list[tuple[str, str, str]] = []
    for path in junit_paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise SystemExit(f"error: cannot parse {path}: {exc}")
        for case in iter_testcases(root):
            name = case.get("name", "(unnamed)")
            tests.append((name, case_identity(case), case_status(case)))
    return tests


def build_matrix(ids: list[str], tests: list[tuple[str, str, str]]):
    """Return rows of (id, [test names], result).

    Ids match on token boundaries, never as substrings: AC-1 must not map
    a test tagged AC-10, or an untested id scores a false PASS.
    """
    rows = []
    for req_id in ids:
        needle = re.compile(
            rf"(?<![A-Za-z0-9_:-]){re.escape(req_id)}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        mapped = [(n, s) for n, ident, s in tests if needle.search(ident)]
        live = [(n, s) for n, s in mapped if s != "skipped"]
        if not live:
            result = "NO-TEST"
        elif any(s == "failed" for _, s in live):
            result = "FAIL"
        else:
            result = "PASS"
        rows.append((req_id, [n for n, _ in live] or [n for n, _ in mapped], result))
    return rows


def print_matrix(rows) -> None:
    id_w = max([len("Id")] + [len(r[0]) for r in rows])
    res_w = len("NO-TEST")
    header = f"| {'Id'.ljust(id_w)} | {'Result'.ljust(res_w)} | Mapped tests"
    print(header)
    print(f"|{'-' * (id_w + 2)}|{'-' * (res_w + 2)}|{'-' * 14}")
    for req_id, names, result in rows:
        shown = "; ".join(names) if names else "(none)"
        print(f"| {req_id.ljust(id_w)} | {result.ljust(res_w)} | {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coverage matrix: brief requirement ids vs JUnit results.",
    )
    parser.add_argument(
        "--brief", nargs="+", required=True, type=Path, metavar="MD",
        help="markdown file(s) containing qualified AC/BR or story scenario ids",
    )
    parser.add_argument(
        "--junit", nargs="+", required=True, type=Path, metavar="XML",
        help="JUnit XML result file(s)",
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, metavar="JSON",
        help="also write the matrix rows and summary as JSON (the shape"
             " the backlog coverage compiler consumes)",
    )
    args = parser.parse_args(argv)

    for path in list(args.brief) + list(args.junit):
        if not path.is_file():
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2

    ids = extract_ids(args.brief)
    if not ids:
        print("error: no AC/BR or story scenario ids found in the brief(s)",
              file=sys.stderr)
        return 2

    tests = collect_tests(args.junit)
    rows = build_matrix(ids, tests)
    print_matrix(rows)

    counts = {"pass": 0, "fail": 0, "no_test": 0}
    for _, _, result in rows:
        counts[result.lower().replace("-", "_")] += 1
    verdict = "PASS" if counts["fail"] == 0 and counts["no_test"] == 0 else "FAIL"
    summary = {
        "total_ids": len(rows),
        "pass": counts["pass"],
        "fail": counts["fail"],
        "no_test": counts["no_test"],
        "verdict": verdict,
    }
    print("scenario_report_summary " + json.dumps(summary, sort_keys=True))
    if args.json_out is not None:
        document = {
            "rows": [
                {"id": req_id, "result": result, "tests": names}
                for req_id, names, result in rows
            ],
            "summary": summary,
        }
        args.json_out.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
