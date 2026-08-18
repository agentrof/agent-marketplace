"""Static contracts for transient, read-only Requirement review."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


class RequirementReviewContracts(unittest.TestCase):
    def test_backlog_reviewer_is_read_only_and_returns_structured_findings(self):
        reviewer = read("agents/backlog-reviewer.md")
        self.assertIn("tools: Read, Grep, Glob", reviewer)
        self.assertIn("Return findings to the invoking workflow", reviewer)
        self.assertIn("relation_audit", reviewer)
        self.assertIn("no writes performed", reviewer)
        self.assertNotIn("Write only the designated review note", reviewer)

    def test_backlog_flow_waits_before_single_writer_and_root_review(self):
        flow = " ".join(read("flows/backlog-planning.md").split())
        epic_wait = flow.index("Wait for every epic reviewer")
        epic_writer = flow.index("The Product Owner is the single writer")
        root_start = flow.index("Only after every epic package and review is green")
        root_wait = flow.index("Wait for its return", root_start)
        self.assertLess(epic_wait, epic_writer)
        self.assertLess(epic_writer, root_start)
        self.assertLess(root_start, root_wait)
        self.assertIn("no host-specific command is canonical", flow)

    def test_pre_backlog_challenge_has_no_durable_review_history(self):
        challenge = " ".join(read("skill-content/challenge-review/SKILL.md").split())
        triage = " ".join(
            read("skill-content/challenge-review/references/triage.md").split()
        )
        business_analysis = " ".join(
            read("skill-content/business-analysis/SKILL.md").split()
        )
        self.assertIn("not to a durable review history", challenge)
        self.assertIn("do not persist reviewer output as an audit log", challenge)
        self.assertIn("No reviewer-state field, transcript or audit", triage)
        self.assertIn("Do not create", business_analysis)
        self.assertIn("review-history documents", business_analysis)


if __name__ == "__main__":
    unittest.main()
