"""Pinned headless Backlog evidence survives later UI work, never source drift."""

import contextlib
import io
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from types import SimpleNamespace

from tools.tests import backlog_fixture
from tools.tests import test_backlog_absent_inputs as strict_fixture

compiler = strict_fixture.compiler


@integration
class BacklogAbsenceHistoryTests(unittest.TestCase):
    setUp = strict_fixture.AbsentInputTests.setUp
    requirement = strict_fixture.AbsentInputTests.requirement
    upstreams = strict_fixture.AbsentInputTests.upstreams
    git = strict_fixture.AbsentInputTests.git
    prepare = strict_fixture.AbsentInputTests.prepare

    def approved_headless_backlog(self):
        self.prepare()
        source = "[[solution-design/decisions/run-job|Run the headless job]]"
        backlog_fixture._write_note(
            self.docs / "solution-design/decisions/run-job.md",
            {"type": "decision", "title": "Run the headless job", "status": "accepted",
             "tags": ["doc/decision", "status/accepted"]},
            "# Run the headless job\n\nRun the existing headless job without a UI.\n",
        )
        output = io.StringIO()
        # Only BA/Solution receipt lookup is replaced by the shared narrow
        # fixture. Requirement approval, topology policy, Backlog authoring,
        # review/coverage gates, hashes, Git and Delivery source reads are real.
        with self.upstreams(), contextlib.redirect_stdout(output):
            self.assertEqual(compiler.init(SimpleNamespace(
                docs=str(self.docs), planning_mode="requirement",
                requirement_ref="REQ-001", input_ref=[strict_fixture.BA, strict_fixture.SOLUTION],
                absent_input=["design-system", "experience-design"],
            )), 0, output.getvalue())
            self.assertEqual(compiler.stub_epic(SimpleNamespace(
                docs=str(self.docs), slug="jobs", id="EP-001", title="Run jobs",
                goal="Run the approved headless job reliably.",
            )), 0)
            self.assertEqual(compiler.stub_story(SimpleNamespace(
                docs=str(self.docs), epic="jobs", slug="run-job", id="JOB-01",
                title="Run the job", scope="Run the approved job.",
                work_kind="technical", criterion_ref=[], experience_ref=[],
                evidence_ref=[source], uses_design=[], constrained_by=[],
            )), 0)
        self.story = self.docs / "backlog/epics/jobs/stories/run-job/story.md"
        plan = self.story.with_name("test-plan.md")
        backlog_fixture._author_story(self.story, plan, "JOB-01")
        root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        props, _ = compiler.parse_front_matter(root_review)
        props.update(verdict="approved", related_to=["[[backlog/epics/jobs/epic|EP-001]]"],
                     dependency_refs=[])
        root_review.write_text(compiler.front_matter(props, backlog_fixture._complete_review_body(
            props["title"], compiler.backlog_review_sections("requirement"),
            ("| REQ-001 | JOB-01 | covered |",))))
        epic_review = self.docs / "backlog/epics/jobs/reviews/round-1-epic-review.md"
        props, _ = compiler.parse_front_matter(epic_review)
        props.update(verdict="approved", verifies=[
            "[[backlog/epics/jobs/stories/run-job/story|JOB-01]]",
            "[[backlog/epics/jobs/stories/run-job/test-plan|JOB-01-TP]]"],
            scenario_refs=["JOB-01-TS-001"], dependency_refs=[])
        epic_review.write_text(compiler.front_matter(props, backlog_fixture._complete_review_body(
            props["title"], compiler.backlog_contract()["required_epic_review_sections"])))
        with self.upstreams(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(compiler.approve(SimpleNamespace(docs=str(self.docs))), 0, output.getvalue())
        self.root_note = self.docs / "backlog/backlog.md"
        self.git("add", ".")
        self.git("commit", "-qm", "Approved headless backlog")
        self.pinned = compiler.parse_front_matter(self.root_note)[0]["package_hash"]

    def collect(self, historical=True):
        with self.upstreams():
            return compiler.collect(self.docs, historical_inputs=historical)

    def test_later_visual_work_preserves_pinned_delivery_but_not_new_handoff(self):
        self.approved_headless_backlog()
        before = self.root_note.read_bytes()
        for name in ("design-system/MASTER.md", "experience-design/artifacts/app.html"):
            path = self.docs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Later visual work outside the pinned Backlog.\n")
        self.component.write_text(self.component.read_text().replace("app_kind: worker", "app_kind: frontend-web"))
        self.git("add", ".")
        self.git("commit", "-qm", "Add a later UI")
        record, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual(record["backlog"]["props"]["package_hash"], self.pinned)
        self.assertEqual(self.root_note.read_bytes(), before)
        import delivery_compile
        with self.upstreams():
            sources, snapshot, errors = delivery_compile.approved_backlog_sources(
                self.docs, ["JOB-01"], historical_inputs=True)
        self.assertEqual(errors, [])
        self.assertEqual(set(sources), {"JOB-01"})
        self.assertEqual(snapshot["backlog_package_hash"], self.pinned)
        _record, strict_errors = self.collect(historical=False)
        self.assertTrue(any("headless Solution topology" in error for error in strict_errors), strict_errors)
        self.assertTrue(any("existing package content" in error for error in strict_errors), strict_errors)

    def test_unapproved_backlog_cannot_claim_historical_absence(self):
        self.approved_headless_backlog()
        props, body = compiler.parse_front_matter(self.root_note)
        compiler.status_tag(props, "draft")
        self.root_note.write_text(compiler.front_matter(props, body))
        _record, errors = self.collect()
        self.assertTrue(any("approved backlog" in error or "status is not approved" in error
                            for error in errors), errors)

    def test_changed_story_is_rejected_by_original_approval_hash(self):
        self.approved_headless_backlog()
        self.story.write_text(self.story.read_text().replace("Run the approved job.", "Run a different job."))
        self.git("add", ".")
        self.git("commit", "-qm", "Change source without approval")
        _record, errors = self.collect()
        self.assertTrue(any("source_hash" in error or "package_hash is stale" in error for error in errors), errors)

    def test_semantically_equal_uncommitted_bytes_are_rejected(self):
        self.approved_headless_backlog()
        self.root_note.write_bytes(self.root_note.read_bytes() + b"\n")
        _record, errors = self.collect()
        self.assertTrue(any("byte-exact committed backlog sources" in error for error in errors), errors)

    def test_changed_absence_contract_cannot_borrow_old_approval(self):
        self.approved_headless_backlog()
        props, body = compiler.parse_front_matter(self.root_note)
        props["input_contract"] = "unknown"
        self.root_note.write_text(compiler.front_matter(props, body))
        _record, errors = self.collect()
        self.assertTrue(any("supported input_contract" in error for error in errors), errors)

    def test_empty_declared_fields_do_not_disable_contract_validation(self):
        self.approved_headless_backlog()
        original = self.root_note.read_text()
        for declared in ([], None, "", "design-system"):
            with self.subTest(declared=declared):
                props, body = compiler.parse_front_matter_text(original)
                props["input_contract"] = ""
                props["absent_input_stages"] = declared
                self.root_note.write_text(compiler.front_matter(props, body))
                _record, errors = self.collect()
                self.assertTrue(any("supported input_contract" in error for error in errors), errors)

    def test_authored_reference_after_navigation_cannot_hide_absent_dependency(self):
        self.prepare()
        note = self.docs / "backlog/epics/job/epic.md"
        note.parent.mkdir(parents=True)
        for body in (
            "# Job\n\n<!-- sec: nav -->\n- [[maps/backlog|Backlog]]\n\n## Contract\nRequires [[design-system/MASTER|Design]].\n",
            "---\nuses_design: [[design-system/MASTER|Design]]\n---\n# Job\n\n<!-- sec: nav -->\n- [[maps/backlog|Backlog]]\n",
        ):
            with self.subTest(body=body):
                note.write_text(body)
                with self.upstreams():
                    errors = compiler.planning_package_findings(
                        self.docs, self.props, "backlog/backlog.md")[2]
                self.assertTrue(any("references absent design-system" in error for error in errors), errors)
        note.write_text("# Job\n\n<!-- sec: nav -->\n- [[design-system/MASTER|Navigation only]]\n\n## Contract\nNo visual dependency.\n")
        with self.upstreams():
            errors = compiler.planning_package_findings(
                self.docs, self.props, "backlog/backlog.md")[2]
        self.assertFalse(any("references absent" in error for error in errors), errors)

    def test_requirement_no_longer_na_still_blocks_historical_read(self):
        self.approved_headless_backlog()
        self.req.write_text(self.req.read_text().replace(
            "| design-system | not_applicable |", "| design-system | required |"))
        _record, errors = self.collect()
        self.assertTrue(any("may be absent only" in error for error in errors), errors)

    def test_unapproved_requirement_body_change_blocks_historical_read(self):
        self.approved_headless_backlog()
        self.req.write_text(self.req.read_text().replace(
            "Export the monthly report.", "Export a different deliverable."))
        _record, errors = self.collect()
        self.assertTrue(any("approved source_hash is stale" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
