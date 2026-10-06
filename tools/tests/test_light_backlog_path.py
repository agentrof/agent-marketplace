"""Process switch backlog_path (#441): at the default every revision keeps its
epic and root reviews; at light_when_eligible a small technical or defect
revision is reviewed by one reader of its story delta and the compiler writes
the root review round. stub-story --from-requirement writes the story and
test plan from the approved Requirement's acceptance items.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.tests import test_backlog_requirement_bindings as fixtures
from tools.tests.backlog_fixture import _author_story, _complete_review_body
from tools.tests.test_review_manifest_scope import choose

compiler = fixtures.compiler
requirement_compile = fixtures.requirement_compile

EPIC = "delivery-fixture"
EVIDENCE = "[[solution-design/decisions/fixture-api|Fixture API]]"
ITEMS = ["The report page offers one CSV download.",
         "The CSV keeps the column order of the page."]
ROOT_REVIEW = "backlog/reviews/round-2-backlog-review.md"


class LightBacklogPathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RequirementBindingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.docs = self.fixture.docs
        with contextlib.redirect_stdout(io.StringIO()):
            fixtures.backlog_fixture.make_approved_backlog(self.docs, "ST-001")
        self.requirement()
        fixtures.init_repository(self.fixture.project)
        for args in (["add", "-A"], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", "Approved fixture"]):
            subprocess.run(["git", *args], cwd=self.fixture.project, check=True, capture_output=True)
        mocks = contextlib.ExitStack()
        self.addCleanup(mocks.close)
        mocks.enter_context(self.fixture.upstreams())
        # The fixture's first story cites its Experience screen in the legacy form.
        mocks.enter_context(mock.patch.object(compiler, "validate_experience_ref"))

    def requirement(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            path = requirement_compile.create_requirement(
                self.docs, "report-export", "Report export", "technical", "high", None, [])
        props, body = requirement_compile.split_note(path)
        for placeholder, text in {
            "TODO: state the requested change and who needs it.": "Export the monthly report.",
            "TODO: state the observable outcome and acceptance boundary.":
                "\n".join(f"- {item}" for item in ITEMS),
            "TODO: define included and excluded behavior.": "Include CSV export. Exclude PDF layout.",
            "TODO: record evidence, constraints and urgency rationale.":
                f"Users copy the report by hand; {EVIDENCE} fixes the API.",
        }.items():
            body = body.replace(placeholder, text)
        for stage in requirement_compile.STAGES:
            body = body.replace(f"| {stage} | required |  | TODO: explain why this stage must change. |",
                                f"| {stage} | not_applicable |  | No {stage} surface changes. |")
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            requirement_compile.approve_requirement(path)

    def call(self, function, **arguments) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = function(SimpleNamespace(docs=str(self.docs), **arguments))
        return code, output.getvalue()

    def revise(self) -> None:
        code, output = self.call(
            compiler.begin_revision, delivery_snapshot="", planning_mode="requirement",
            requirement_ref="REQ-001", absent_input=[],
            input_ref=[fixtures.BA, fixtures.SOLUTION, fixtures.DESIGN,
                       fixtures.APPLICATION, fixtures.PROCESS])
        self.assertEqual(code, 0, output)

    def stub(self, slug: str = "st-002", story_id: str = "ST-002") -> tuple[int, str]:
        return self.call(compiler.stub_story, epic=EPIC, slug=slug, id=story_id, title=None,
                         scope=None, work_kind="feature", criterion_ref=[], experience_ref=[],
                         evidence_ref=[], uses_design=[], constrained_by=[], implements=[],
                         from_requirement=True)

    def author(self, slug: str = "st-002", story_id: str = "ST-002") -> None:
        folder = self.docs / "backlog/epics" / EPIC / "stories" / slug
        _author_story(folder / "story.md", folder / "test-plan.md", story_id)
        plan = folder / "test-plan.md"
        text = plan.read_text(encoding="utf-8").replace(
            f"| empty | covered | {story_id}-TS-001 |",
            f"| empty | covered | {story_id}-TS-001, {story_id}-TS-002 |")
        plan.write_text(text, encoding="utf-8")

    def review_epic(self) -> None:
        code, output = self.call(compiler.stub_epic, slug=EPIC, id=None, title=None, goal=None,
                                 new_review=True)
        self.assertEqual(code, 0, output)
        path = self.docs / "backlog/epics" / EPIC / "reviews/round-2-epic-review.md"
        props, body = compiler.parse_front_matter(path)
        props["verdict"] = "approved"
        complete = _complete_review_body(
            str(props["title"]), compiler.backlog_contract()["required_epic_review_sections"])
        path.write_text(compiler.front_matter(
            props, complete + "\n" + compiler.NAV_MARKER + body.split(compiler.NAV_MARKER, 1)[1]),
            encoding="utf-8")

    def status(self) -> dict:
        code, output = self.call(compiler.light_path_status_command)
        self.assertEqual(code, 0, output)
        return json.loads(output)

    def light_revision(self) -> None:
        choose(self.docs, "light_when_eligible", switch="backlog_path")
        self.revise()
        code, output = self.stub()
        self.assertEqual(code, 0, output)
        self.author()
        self.review_epic()

    def test_the_default_keeps_the_standard_path_and_refuses_a_light_root_review(self):
        self.revise()
        code, output = self.stub()
        self.assertEqual(code, 0, output)
        self.author()
        self.review_epic()
        status = self.status()
        self.assertEqual((status["backlog_path"], status["eligible"]), ("standard", False))
        self.assertEqual(status["reasons"], ["backlog_path is standard"])
        before = (self.docs / ROOT_REVIEW).read_bytes()
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 1, output)
        self.assertIn("takes the standard path", output)
        self.assertEqual((self.docs / ROOT_REVIEW).read_bytes(), before)
        record, _errors = compiler.collect(self.docs)
        self.assertEqual(compiler.light_root_review_findings(record, self.docs), [])

    def test_the_stub_writes_the_requirement_items_into_story_and_test_plan(self):
        self.revise()
        code, output = self.stub()
        self.assertEqual(code, 0, output)
        folder = self.docs / "backlog/epics" / EPIC / "stories/st-002"
        props, body = compiler.parse_front_matter(folder / "story.md")
        self.assertEqual(props["work_kind"], "technical")
        self.assertEqual(props["implements"], ["[[requirements/req-001-report-export|REQ-001]]"])
        self.assertEqual(props["related_to"], [EVIDENCE])
        self.assertEqual(compiler.section(body, "Acceptance").splitlines(),
                         [f"- [ ] {item}" for item in ITEMS])
        plan = (folder / "test-plan.md").read_text(encoding="utf-8")
        blocks = compiler.scenario_blocks(plan)
        self.assertEqual([scenario for scenario, _block in blocks], ["ST-002-TS-001", "ST-002-TS-002"])
        for (scenario, block), item in zip(blocks, ITEMS):
            fields, _duplicates = compiler.scenario_fields(block)
            self.assertEqual(fields["Then"], item)
            self.assertEqual(compiler.source_ref_values(fields["source_refs"])[0], [EVIDENCE])
        rows = compiler.structured_table(compiler.section(plan, "Coverage Classes"),
                                         ("class", "disposition", "scenario_refs", "reason"),
                                         "", "Coverage Classes")[0]
        self.assertEqual([row["class"] for row in rows], list(compiler.SCENARIO_COVERAGE_CLASSES))
        self.assertEqual({row["reason"] for row in rows}, {compiler.COVERAGE_REASON_STUB})

    def test_the_stub_needs_a_requirement_mode_backlog(self):
        code, output = self.stub()
        self.assertEqual(code, 2)
        self.assertIn("--from-requirement needs a Requirement-mode backlog", output)

    def test_an_eligible_revision_is_approved_on_one_story_delta_review(self):
        self.light_revision()
        status = self.status()
        self.assertTrue(status["eligible"], status)
        self.assertEqual((status["changed"], status["epic"]), (["ST-002"], "EP-001"))
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 0, output)
        props, body = compiler.parse_front_matter(self.docs / ROOT_REVIEW)
        self.assertEqual(props["verdict"], "approved")
        self.assertIn("Compiler [Light Path]: changed ST-002",
                      compiler.section(body, compiler.LIGHT_PATH_SECTION))
        self.assertIn("| REQ-001 | ST-002 | covered |",
                      compiler.section(body, compiler.REQUIREMENT_COVERAGE))
        code, output = self.call(compiler.approve)
        self.assertEqual(code, 0, output)

    def test_an_ineligible_revision_falls_back_to_the_standard_path(self):
        self.light_revision()
        story = self.docs / "backlog/epics" / EPIC / "stories/st-001/story.md"
        story.write_text(story.read_text(encoding="utf-8").replace(
            "Administrative bulk operations remain outside this slice.",
            "Administrative bulk operations and exports remain outside this slice."),
            encoding="utf-8")
        status = self.status()
        self.assertFalse(status["eligible"])
        self.assertEqual(status["changed"], ["ST-001", "ST-002"])
        self.assertIn("ST-001 work_kind is feature, not defect or technical", status["reasons"])
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 1, output)
        self.assertIn("takes the standard path", output)

    def test_approval_refuses_a_light_root_review_the_revision_has_outgrown(self):
        self.light_revision()
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 0, output)
        for number in (3, 4, 5):
            code, output = self.stub(f"st-00{number}", f"ST-00{number}")
            self.assertEqual(code, 0, output)
        record, _errors = compiler.collect(self.docs)
        findings = compiler.light_root_review_findings(record, self.docs)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("no longer takes the light path", findings[0])
        self.assertIn("more than max_changed_stories 3", findings[0])

    def test_a_light_revision_needs_its_own_epic_review_round(self):
        # A light revision approved, then a next one changes a story with no
        # new epic round: the earlier, stamped round never saw the change.
        self.light_revision()
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 0, output)
        code, output = self.call(compiler.approve)
        self.assertEqual(code, 0, output)
        for args in (["add", "-A"], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", "Light revision"]):
            subprocess.run(["git", *args], cwd=self.fixture.project, check=True, capture_output=True)
        self.revise()
        story = self.docs / "backlog/epics" / EPIC / "stories/st-002/story.md"
        story.write_text(story.read_text(encoding="utf-8") + "\nA changed line.\n", encoding="utf-8")
        self.assertTrue(self.status()["eligible"], self.status())
        code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 1, output)
        self.assertIn("stamped by an earlier revision", output)
        # Approval refuses it too, even when the round was written without the check.
        with mock.patch.object(compiler, "light_epic_review_findings", return_value=[]):
            code, output = self.call(compiler.record_light_root_review)
        self.assertEqual(code, 0, output)
        code, output = self.call(compiler.approve)
        self.assertNotEqual(code, 0, output)
        self.assertIn("stamped by an earlier revision", output)

if __name__ == "__main__":
    unittest.main()
