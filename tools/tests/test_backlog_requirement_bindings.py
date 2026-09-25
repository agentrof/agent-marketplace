"""A Requirement-mode backlog pins every input family, not only what its Requirement changes."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.tests import backlog_fixture

compiler = backlog_fixture.backlog_compile
stage_package = backlog_fixture.stage_package
requirement_compile = compiler.requirement_compile
requirement_route = compiler.requirement_route

LABEL = "backlog/backlog.md"
BA = "business-analysis/sales/space"
SOLUTION = "solution-design/landscape"
DESIGN = "design-system/MASTER"
APPLICATION = "application@r6"
PROCESS = "workspace@r5"


def digest(fill: str) -> str:
    return "sha256:" + fill * 64


def binding(stage: str, reference: str, fill: str) -> str:
    return f"{stage}|{reference}|{digest(fill)}"


CARRIED = [
    binding("business-analysis", BA, "1"),
    binding("design-system", DESIGN, "2"),
    binding("experience-design", APPLICATION, "3"),
    binding("experience-design", PROCESS, "4"),
    binding("solution-design", SOLUTION, "5"),
]


class RequirementBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.docs = self.project / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs / "home.md").write_text("# Home\n", encoding="utf-8")
        (self.project / "workspace/config.json").write_text(
            '{"schema_version": 2, "team_id": "software-engineering-team", '
            '"output_language": "English", "terminology_language": "English"}',
            encoding="utf-8")

    def requirement(self, dispositions: dict[str, str], results=None) -> Path:
        """Write an approved REQ-001 with the given impact matrix and Stage Results."""
        with contextlib.redirect_stdout(io.StringIO()):
            path = requirement_compile.create_requirement(
                self.docs, "pin-acquisition", "Pin acquisition", "technical", "high", None, [])
        props, body = requirement_compile.split_note(path)
        for placeholder, text in {
            "TODO: state the requested change and who needs it.": "Acquire pins by digest.",
            "TODO: state the observable outcome and acceptance boundary.": "Bring-up asks for each pinned digest.",
            "TODO: define included and excluded behavior.": "Include acquisition. Exclude image builds.",
            "TODO: record evidence, constraints and urgency rationale.": "A moved tag broke bring-up.",
        }.items():
            body = body.replace(placeholder, text)
        for stage in requirement_compile.STAGES:
            disposition = dispositions.get(stage, "not_applicable")
            rationale = (f"The {stage} output constrains this change."
                         if disposition != "not_applicable"
                         else f"No {stage} surface changes.")
            body = body.replace(
                f"| {stage} | required |  | TODO: explain why this stage must change. |",
                f"| {stage} | {disposition} |  | {rationale} |")
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            requirement_compile.approve_requirement(path)
        if results:
            props, body = requirement_compile.split_note(path)
            body = requirement_compile.stage_results_body(body, results)
            path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        return path

    @contextlib.contextmanager
    def upstreams(self, stale=frozenset(), receipts=None):
        """Resolve packages without building them; *stale* references fail strict currency."""
        receipts = receipts or {}

        def verify(docs, stage, reference, expected_hash="", **options):
            if reference in stale:
                return None, [f"{reference} is not the current {stage} receipt"]
            return {"result_ref": reference,
                    "package_hash": receipts.get(reference, expected_hash or digest("9"))}, []

        with mock.patch.object(stage_package, "verify", side_effect=verify), \
                mock.patch.object(requirement_route, "route", return_value={"action": "backlog"}), \
                mock.patch.object(requirement_compile, "valid_experience_receipt_refs", return_value=True):
            yield

    def findings(self, props):
        return compiler.planning_package_findings(self.docs, props, LABEL)[2]

    def test_untouched_stages_carry_the_previous_bindings(self):
        self.requirement({"business-analysis": "required"},
                         {"business-analysis": [(BA, digest("a"))]})
        with self.upstreams():
            bindings, errors = compiler.requirement_input_bindings(
                self.docs, "REQ-001", CARRIED, [], LABEL)
        self.assertEqual(errors, [])
        self.assertIn(binding("business-analysis", BA, "a"), bindings)
        self.assertNotIn(binding("business-analysis", BA, "1"), bindings)
        for carried in CARRIED[1:]:
            self.assertIn(carried, bindings)

    def test_an_untouched_stage_without_a_binding_is_refused(self):
        self.requirement({})
        with self.upstreams():
            _bindings, errors = compiler.requirement_input_bindings(
                self.docs, "REQ-001", [], [], LABEL)
        for stage in requirement_compile.STAGES:
            self.assertIn(f"{LABEL} requirement planning needs an input binding for {stage}, "
                          "which REQ-001 marks not_applicable; pass --input-ref", errors)

    def test_input_ref_pins_only_untouched_stages(self):
        self.requirement({"business-analysis": "required"},
                         {"business-analysis": [(BA, digest("a"))]})
        with self.upstreams():
            _bindings, errors = compiler.requirement_input_bindings(
                self.docs, "REQ-001", CARRIED, [BA], LABEL)
        self.assertIn(f"{LABEL} --input-ref may pin only a stage REQ-001 marks not_applicable; "
                      "business-analysis binds its Stage Results", errors)

    def test_a_declared_input_ref_replaces_the_carried_binding(self):
        self.requirement({})
        with self.upstreams(receipts={SOLUTION: digest("b")}):
            bindings, errors = compiler.requirement_input_bindings(
                self.docs, "REQ-001", CARRIED, [f"[[{SOLUTION}|Landscape]]"], LABEL)
        self.assertEqual(errors, [])
        self.assertIn(binding("solution-design", SOLUTION, "b"), bindings)
        self.assertNotIn(binding("solution-design", SOLUTION, "5"), bindings)

    def test_a_package_the_requirement_does_not_touch_still_goes_stale(self):
        # The observed failure: the Requirement marks Experience not_applicable,
        # the application advances, and the backlog check stayed green.
        self.requirement({})
        with self.upstreams(stale={APPLICATION}):
            errors = self.findings({"planning_mode": "requirement", "requirement_ref": "REQ-001",
                                    "status": "approved", "input_bindings": CARRIED})
        self.assertIn(f"{LABEL} input binding: {APPLICATION} is not the current experience-design receipt",
                      errors)

    def test_a_changed_stage_binds_exactly_its_stage_results(self):
        self.requirement({"business-analysis": "required"},
                         {"business-analysis": [(BA, digest("a"))]})
        with self.upstreams():
            errors = self.findings({"planning_mode": "requirement", "requirement_ref": "REQ-001",
                                    "status": "draft", "input_bindings": CARRIED})
        self.assertIn(f"{LABEL} business-analysis input binding must equal REQ-001 Stage Results", errors)

    def test_a_backlog_approved_before_bindings_stays_readable(self):
        self.requirement({})
        missing = f"{LABEL} requirement planning needs compiler-owned input_bindings"
        with self.upstreams():
            approved = self.findings({"planning_mode": "requirement", "requirement_ref": "REQ-001",
                                      "status": "approved"})
            draft = self.findings({"planning_mode": "requirement", "requirement_ref": "REQ-001",
                                   "status": "draft"})
        self.assertNotIn(missing, approved)
        self.assertIn(missing, draft)

    def test_begin_revision_writes_the_complete_binding_set(self):
        with contextlib.redirect_stdout(io.StringIO()):
            backlog_fixture.make_approved_backlog(self.docs)
        self.requirement({})
        for args in (["init", "--quiet"], ["add", "-A"],
                     ["-c", "user.name=Jane Doe", "-c", "user.email=jane@example.invalid",
                      "commit", "--quiet", "-m", "Approved backlog fixture"]):
            subprocess.run(["git", *args], cwd=self.project, check=True, capture_output=True)
        declared = [BA, SOLUTION, DESIGN, APPLICATION, PROCESS]
        output = io.StringIO()
        # The legacy fixture story cites its Experience screen as a wikilink,
        # which a planning mode rejects; this test covers only the root bindings.
        with self.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = compiler.begin_revision(SimpleNamespace(
                docs=str(self.docs), delivery_snapshot="", planning_mode="requirement",
                requirement_ref="REQ-001", input_ref=declared))
        self.assertEqual(result, 0, output.getvalue())
        props, _body = compiler.parse_front_matter(self.docs / "backlog/backlog.md")
        self.assertEqual(props["requirement_ref"], "REQ-001")
        self.assertEqual(sorted(binding.split("|")[1] for binding in props["input_bindings"]),
                         sorted(declared))


if __name__ == "__main__":
    unittest.main()
