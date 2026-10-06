"""The owner is offered only a backlog that atomic approval accepts."""

from __future__ import annotations

import contextlib
import io
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration

from tools.tests import backlog_fixture
from tools.tests import test_backlog_pending_review_policy as pending

compiler = pending.compiler
process_policy = pending.process_policy


@integration
class PreApprovalCheckTests(unittest.TestCase):
    def setUp(self):
        self.base = pending.PendingReviewPolicyTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.docs = self.base.docs
        self.epic_review, self.root_review = self.base.completed_current_rounds()

    def edit(self, path, **fields):
        props, body = compiler.parse_front_matter(path)
        for key, value in fields.items():
            if key == "status":
                compiler.status_tag(props, value)
            elif value is None:
                props.pop(key, None)
            else:
                props[key] = value
        path.write_text(compiler.front_matter(props, body), encoding="utf-8")

    def rel(self, path):
        return path.relative_to(self.docs).as_posix()

    def assert_refused_like_approval(self, *expected):
        code, check = self.base.cli("check", "--json")
        self.assertEqual(code, 1, check)
        for message in expected:
            self.assertIn(message, check["errors"])
        code, pre = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 1, pre)
        before = self.base.fixture.files()
        code, approval = self.base.cli("approve")
        self.assertEqual(code, 1, approval)
        self.assertEqual(self.base.fixture.files(), before)
        # The pre-approval check reports exactly what approval refuses with.
        self.assertEqual(pre["errors"], approval["errors"])
        return pre["errors"]

    def test_pre_approval_check_writes_nothing_and_passes_when_approval_does(self):
        before = self.base.fixture.files()
        code, result = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.base.fixture.files(), before)
        code, result = self.base.cli("approve")
        self.assertEqual(code, 0, result)

    def test_empty_verdict_under_a_concluded_verdict_section_refuses_before_the_owner_gate(self):
        self.edit(self.epic_review, verdict=None)
        errors = self.assert_refused_like_approval(
            f"{self.rel(self.epic_review)} concludes its Verdict section but its verdict field is"
            " empty; record the reader's verdict")
        self.assertIn("EP-001 latest epic review verdict is not approved", errors)

    def test_approved_verdict_under_changes_requested_status_refuses_before_the_owner_gate(self):
        self.edit(self.root_review, status="changes_requested")
        errors = self.assert_refused_like_approval(
            f"{self.rel(self.root_review)} verdict approved contradicts status changes_requested;"
            " set status draft, approval stamps approved")
        self.assertIn("latest cross-epic backlog review still requests changes", errors)

    def test_verdict_outside_the_closed_set_is_a_check_finding(self):
        self.edit(self.epic_review, verdict="approve")
        self.assert_refused_like_approval(
            f"{self.rel(self.epic_review)} verdict must be approved or changes_requested")

    def test_pending_review_without_a_verdict_keeps_only_its_completion_findings(self):
        props, _body = compiler.parse_front_matter(self.root_review)
        props.pop("verdict")
        sections = compiler.backlog_contract()["required_backlog_review_sections"]
        self.root_review.write_text(compiler.front_matter(props, compiler.review_body(props["title"], sections)),
                                    encoding="utf-8")
        code, result = self.base.cli("check", "--json")
        self.assertEqual(code, 1, result)
        rel = self.rel(self.root_review)
        self.assertIn(f"{rel} review section needs section-specific Conclusion [Verdict]", result["errors"])
        self.assertFalse([error for error in result["errors"] if "verdict field" in error], result["errors"])

    def test_changes_requested_round_checks_clean_and_only_the_pre_approval_check_refuses(self):
        self.edit(self.epic_review, verdict="changes_requested", status="changes_requested")
        code, result = self.base.cli("check", "--json")
        self.assertEqual(code, 0, result)
        code, pre = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 1, pre)
        code, approval = self.base.cli("approve")
        self.assertEqual(code, 1, approval)
        self.assertEqual(pre["errors"], approval["errors"])
        self.assertEqual(pre["errors"], ["EP-001 latest epic review verdict is not approved"])

    def test_a_round_the_pre_approval_check_pins_is_judged_as_approval_reads_it(self):
        props, body = compiler.parse_front_matter(self.epic_review)
        unpinned = {key: value for key, value in props.items() if key not in process_policy.PIN_FIELDS}
        self.epic_review.write_text(compiler.front_matter(unpinned, body), encoding="utf-8")
        code, result = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["pinned_reviews"], [self.rel(self.epic_review)])
        self.assertEqual(self.base.pin(self.epic_review), self.base.pin(self.root_review))
        code, result = self.base.cli("approve")
        self.assertEqual(code, 0, result)

    def implement_requirement(self):
        """Add ST-002, which implements the revision's REQ-001, to the reviewed epic."""
        epic = "delivery-fixture"
        with contextlib.redirect_stdout(io.StringIO()):
            code = compiler.main([
                "stub-story", epic, "export", "--docs", str(self.docs), "--id", "ST-002",
                "--criterion-ref", backlog_fixture.CRITERION, "--experience-ref", backlog_fixture.EXPERIENCE,
                "--uses-design", backlog_fixture.DESIGN, "--constrained-by", backlog_fixture.CONSTRAINT])
        self.assertEqual(code, 0)
        story = self.docs / f"backlog/epics/{epic}/stories/export"
        backlog_fixture._author_story(story / "story.md", story / "test-plan.md", "ST-002")
        props, body = compiler.parse_front_matter(self.epic_review)
        props["verifies"] = [*props["verifies"], f"[[backlog/epics/{epic}/stories/export/story|ST-002]]",
                             f"[[backlog/epics/{epic}/stories/export/test-plan|ST-002-TP]]"]
        props["scenario_refs"] = [*props["scenario_refs"], "ST-002-TS-001"]
        self.epic_review.write_text(compiler.front_matter(props, body), encoding="utf-8")

    def write_coverage(self, *rows):
        props, _body = compiler.parse_front_matter(self.root_review)
        self.root_review.write_text(compiler.front_matter(props, backlog_fixture._complete_review_body(
            props["title"], compiler.backlog_review_sections("requirement"), rows)), encoding="utf-8")

    def assert_only_approval_refuses(self, *expected):
        code, result = self.base.cli("check", "--json")
        self.assertEqual(code, 0, result)
        code, pre = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 1, pre)
        before = self.base.fixture.files()
        code, approval = self.base.cli("approve")
        self.assertEqual(code, 1, approval)
        self.assertEqual(self.base.fixture.files(), before)
        self.assertEqual(pre["errors"], approval["errors"])
        self.assertEqual(pre["errors"], sorted(expected))

    def test_requirement_mode_approval_needs_the_coverage_row_of_its_implementing_stories(self):
        self.implement_requirement()
        rel = self.rel(self.root_review)
        needs = (f"{rel} Requirement Coverage needs one REQ-001 row whose story_ids are exactly"
                 " the Stories that implement it: ST-002")
        self.assert_only_approval_refuses(f"{rel} is missing required section: Requirement Coverage", needs)
        self.write_coverage("| REQ-001 | AUTH-01 | covered |")
        self.assert_only_approval_refuses(needs)
        self.write_coverage("| REQ-001 | ST-002 | covered |")
        code, result = self.base.cli("check", "--pre-approval", "--json")
        self.assertEqual(code, 0, result)
        self.assertFalse(compiler.requirement_compile.requirement_incorporated(self.docs, "REQ-001"))
        code, result = self.base.cli("approve")
        self.assertEqual(code, 0, result)
        self.assertTrue(compiler.requirement_compile.requirement_incorporated(self.docs, "REQ-001"))

    def test_pre_approval_and_approved_are_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            compiler.main(["check", "--docs", str(self.docs), "--approved", "--pre-approval"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
