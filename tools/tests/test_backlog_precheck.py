"""The backlog pre-review check fails, before any reader is spawned, on the
mechanical inconsistencies a review round would otherwise be spent on."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import backlog_compile as backlog  # noqa: E402

A = "backlog/epics/e/stories/a/story"
B = "backlog/epics/e/stories/b/story"


def story(sid: str, path: str, *, acceptance: int = 2, dependencies: str = "None.",
          notes: str = "Plain notes.", depends: tuple[str, ...] = (), scenarios: str = "") -> dict:
    items = "\n".join(f"- [ ] Criterion {number} holds." for number in range(1, acceptance + 1))
    body = (f"## Acceptance\n\n{items}\n\n## Dependencies\n\n{dependencies}\n\n"
            f"## Delivery Notes\n\n{notes}\n")
    return {"id": sid, "path": path + ".md", "test_plan": path.replace("story", "test-plan.md"),
            "body": body, "test_body": scenarios, "dependency_targets": list(depends)}


def scenario(sid: str, number: int, **fields: str) -> str:
    lines = [f"## {sid}-TS-{number:03d}", ""]
    lines += [f"- {key}: {value}" for key, value in fields.items()]
    return "\n".join(lines) + "\n\n"


def record(*stories: dict, root: str = "", epics: int = 1) -> dict:
    return {"stories": list(stories), "epics": [{}] * epics,
            "backlog": {"path": "backlog/backlog.md", "body": root}}


class PrecheckTests(unittest.TestCase):
    def test_a_consistent_package_has_no_finding(self):
        plan = (scenario("ST-001", 1, story_criteria="1", rows="2", Given="rows: empty; full",
                         Then="each row passes")
                + scenario("ST-001", 2, story_criteria="2", Given="a precondition"))
        first = story("ST-001", A, scenarios=plan, notes="Supplies: `ledger-api`.")
        second = story("ST-002", B, dependencies=f"- [[{A}|ST-001]]: needs the ledger.",
                       depends=(A,), notes="Supplies: `report-view`.")
        root = "The draft retains 1 epics and contains 2 stories with 2 sibling test plans."
        self.assertEqual(backlog.precheck_findings(record(first, second, root=root)), [])

    def test_criterion_trace_findings(self):
        plan = (scenario("ST-001", 1, story_criteria="1, 4")
                + scenario("ST-001", 2, story_criteria="one"))
        findings = backlog.precheck_findings(record(story("ST-001", A, acceptance=3, scenarios=plan)))
        self.assertEqual(findings, [
            "backlog/epics/e/stories/a/test-plan.md scenario ST-001-TS-001 cites story criterion 4,"
            " but ST-001 Acceptance holds 3",
            "backlog/epics/e/stories/a/test-plan.md scenario ST-001-TS-002 story_criteria must list"
            " Acceptance item numbers such as 1, 3-5: one",
            f"{A}.md Acceptance item 2 has no scenario whose story_criteria traces to it",
            f"{A}.md Acceptance item 3 has no scenario whose story_criteria traces to it"])
        # A Test Plan that declares no story_criteria is never traced.
        self.assertEqual(backlog.precheck_findings(record(story("ST-001", A, scenarios=scenario(
            "ST-001", 1, Given="a precondition")))), [])
        self.assertEqual(backlog.story_criterion_numbers("1, 3-5", 5), {1, 3, 4, 5})
        self.assertIsNone(backlog.story_criterion_numbers("5-3", 5))
        self.assertEqual(backlog.story_criterion_numbers("1-1000000000000", 3), {1, 2, 3, 1000000000000})

    def test_rows_must_match_the_row_list(self):
        plan = (scenario("ST-001", 1, rows="3", Given="three groups in rows: equal; one over")
                + scenario("ST-001", 2, rows="2", Given="two control rows: a and b"))
        findings = backlog.precheck_findings(record(story("ST-001", A, scenarios=plan)))
        self.assertEqual(findings, ["backlog/epics/e/stories/a/test-plan.md scenario ST-001-TS-001"
                                    " declares rows: 3, but its Given lists 2 rows"])

    def test_dependencies_prose_and_depends_on_agree(self):
        first = story("ST-001", A)
        linked = story("ST-002", B, dependencies=f"Builds on [[{A}|ST-001]] and ST-009 by id only.")
        self.assertEqual(backlog.precheck_findings(record(first, linked)), [
            f"{B}.md Dependencies links ST-001, which depends_on does not declare"])
        declared = story("ST-002", B, dependencies=f"Builds on [[{A}|ST-001]].", depends=(A,))
        self.assertEqual(backlog.precheck_findings(record(first, declared)), [])

    def test_a_deliverable_supplied_twice_and_stale_root_counts(self):
        first = story("ST-001", A, notes="Supplies: `ledger-api`, `seed`.")
        second = story("ST-002", B, notes="Supplies: `ledger-api`.")
        root = "It retains 2 epics and contains 3 stories with 3 sibling test plans."
        self.assertEqual(backlog.precheck_findings(record(first, second, root=root)), [
            "deliverable `ledger-api` is supplied by more than one story: ST-001, ST-002",
            "backlog/backlog.md states 3 stories with 3 test plans; the package holds 2 of each",
            "backlog/backlog.md states 2 epics; the package holds 1"])


if __name__ == "__main__":
    unittest.main()
