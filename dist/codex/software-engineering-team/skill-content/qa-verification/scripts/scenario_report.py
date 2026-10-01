#!/usr/bin/env python3
"""Deterministic coverage audit: planned identities vs JUnit test results.

Planned identities come from one of two inputs. ``--plan`` reads approved
story Test Plans: each scenario a plan defines under a ``<story-id>-TS-###``
heading, followed by the qualified BA identities its ``source_refs`` cite. An
identity a plan only mentions, such as another story's scenario named in a
Given clause, is not planned. The first ``--plan`` is the Item's own Test
Plan and any further one a dependency's, so ``--superseded`` names only
dependency scenarios. ``--brief`` reads an explicit id list and takes
every canonical qualified or unqualified BA identity and story-scenario
identity in its text, so a Test Plan never goes through it.

Planned identities map to test cases in JUnit XML files (identity present in
the test name, class name, or property values, case-insensitive). The script
prints a coverage matrix with PASS/FAIL/NO-TEST per identity plus a
machine-readable summary line.

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


# The Test Plan grammar of the backlog compiler (scripts/backlog_compile.py:
# parse_front_matter_text, scenario_blocks, scenario_fields, split_wikilink,
# BA_ID_RE). A skill script imports only the standard library, so the grammar
# is mirrored here and a parity test pins it to the compiler.
SCENARIO_HEADING_RE = re.compile(
    r"^##\s+([A-Z][A-Z0-9]*-[0-9]{2,}-TS-[0-9]{3})\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^-\s+([A-Za-z_]+):\s*(.*)$")
NESTED_ITEM_RE = re.compile(r"^\s{2,}-\s+(.+?)\s*$")
SOURCE_LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
BA_IDENTITY_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*:(?:AC|BR)-[A-Z]{2,4}-[0-9]{3,}$")


def extract_ids(brief_paths: list[Path]) -> list[str]:
    """Return planned identities in first-seen order, normalized to upper case."""
    seen: dict[str, None] = {}
    for path in brief_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ID_RE.finditer(text):
            seen.setdefault(match.group(0).upper(), None)
    return list(seen)


def plan_body(text: str) -> str:
    """Return the Markdown after a Test Plan's front matter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next((index for index, line in enumerate(lines[1:], 1)
                if line.strip() == "---"), -1)
    return text if end < 0 else "\n".join(lines[end + 1:])


def source_refs(block: str) -> str:
    """Return one scenario's source_refs value, nested items comma-joined."""
    fields: dict[str, str] = {}
    active = ""
    for raw in block.splitlines():
        match = FIELD_RE.match(raw)
        if match:
            fields[match.group(1)] = match.group(2).strip()
            active = match.group(1)
            continue
        nested = NESTED_ITEM_RE.match(raw)
        if nested and active == "source_refs":
            fields[active] = (fields[active] + ", " + nested.group(1)).strip(", ")
    return fields.get("source_refs", "")


def link_alias(inner: str) -> str:
    for separator in ("\\|", "|"):
        if separator in inner:
            return inner.split(separator, 1)[1].strip()
    return ""


def plan_scenarios(text: str) -> list[tuple[str, list[str]]]:
    """Return each defined scenario with the BA identities it traces, in order."""
    body = plan_body(text)
    headings = list(SCENARIO_HEADING_RE.finditer(body))
    scenarios = []
    for index, heading in enumerate(headings):
        end = (headings[index + 1].start() if index + 1 < len(headings)
               else len(body))
        aliases = [link_alias(inner) for inner in
                   SOURCE_LINK_RE.findall(source_refs(body[heading.end():end]))]
        scenarios.append((heading.group(1), [
            alias for alias in aliases if BA_IDENTITY_RE.fullmatch(alias)]))
    return scenarios


def extract_plan_ids(plans: list[list[tuple[str, list[str]]]],
                     superseded: set[str]) -> list[str]:
    """Return the identities the plans define in first-seen order.

    A superseded scenario leaves the audit together with each BA identity
    that no remaining scenario traces.
    """
    seen: dict[str, None] = {}
    for scenarios in plans:
        for scenario_id, traced in scenarios:
            if scenario_id in superseded:
                continue
            seen.setdefault(scenario_id, None)
            for identity in traced:
                seen.setdefault(identity.upper(), None)
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


def plan_ids(paths: list[Path], superseded: list[str]) -> tuple[list[str], str]:
    """Return the planned identities of ``--plan``, or an input error.

    The first plan is the Item's own. Only a dependency's scenario can be
    superseded, so an id the own plan defines is refused rather than dropped
    with the BA identities only it traces.
    """
    plans = []
    for path in paths:
        scenarios = plan_scenarios(
            path.read_text(encoding="utf-8", errors="replace"))
        if not scenarios:
            return [], f"{path} defines no story scenario"
        plans.append(scenarios)
    defined = {scenario_id for scenarios in plans for scenario_id, _ in scenarios}
    dropped = {value.upper() for value in superseded}
    unknown = sorted(dropped - defined)
    if unknown:
        return [], ("superseded ids are not scenarios the plans define: "
                    + ", ".join(unknown))
    own = sorted(dropped & {scenario_id for scenario_id, _ in plans[0]})
    if own:
        return [], ("superseded ids are scenarios of the Item's own Test Plan, the first"
                    " --plan: " + ", ".join(own))
    return extract_plan_ids(plans, dropped), ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coverage matrix: planned requirement ids vs JUnit results.",
    )
    planned = parser.add_mutually_exclusive_group(required=True)
    planned.add_argument(
        "--plan", nargs="+", type=Path, metavar="MD",
        help="approved story Test Plan(s), the Item's own first: the scenarios each"
             " defines and the qualified AC/BR ids their source_refs cite",
    )
    planned.add_argument(
        "--brief", nargs="+", type=Path, metavar="MD",
        help="explicit id list(s): every qualified AC/BR or story scenario id"
             " in the text, so never a Test Plan",
    )
    parser.add_argument(
        "--superseded", nargs="+", default=[], metavar="ID",
        help="with --plan: dependency scenario ids the Item's own Test Plan, the"
             " first --plan, supersedes; they and the BA ids only they cite leave"
             " the audit",
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
    if args.superseded and not args.plan:
        parser.error("--superseded requires --plan")

    for path in list(args.plan or args.brief) + list(args.junit):
        if not path.is_file():
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2

    if args.plan:
        ids, problem = plan_ids(args.plan, args.superseded)
    else:
        ids = extract_ids(args.brief)
        problem = "" if ids else "no AC/BR or story scenario ids found in the brief(s)"
    if problem:
        print(f"error: {problem}", file=sys.stderr)
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
        args.json_out.write_bytes(
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
