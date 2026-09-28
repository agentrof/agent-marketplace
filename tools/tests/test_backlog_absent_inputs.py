"""An explicit headless intake may omit only genuinely absent visual packages."""

import contextlib
import io
import subprocess
import unittest
from types import SimpleNamespace

from tools.tests.git_fixture import init_repository
from tools.tests import test_backlog_requirement_bindings as binding_fixture

compiler = binding_fixture.compiler
CARRIED = binding_fixture.CARRIED
BA = binding_fixture.BA
SOLUTION = binding_fixture.SOLUTION


class AbsentInputTests(unittest.TestCase):
    setUp = binding_fixture.RequirementBindingTests.setUp
    requirement = binding_fixture.RequirementBindingTests.requirement
    upstreams = binding_fixture.RequirementBindingTests.upstreams

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.project, check=True,
                              capture_output=True, text=True).stdout.strip()

    def prepare(self):
        self.req = self.requirement({})
        self.component = self.docs / "solution-design/components/job-worker/component.md"
        self.component.parent.mkdir(parents=True)
        self.component.write_text(compiler.front_matter({
            "type": "component", "title": "Job worker", "component_id": "job-worker",
            "component_class": "application", "sourcing": "build", "app_kind": "worker",
            "code_path": "workspace/apps/job-worker",
        }, "# Job worker\n"))
        init_repository(self.project)
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "Headless baseline")
        self.props = {
            "planning_mode": "requirement", "requirement_ref": "REQ-001",
            "input_contract": "headless-v1",
            "absent_input_stages": ["design-system", "experience-design"],
            "input_bindings": [CARRIED[0], CARRIED[-1]],
        }

    def findings(self):
        with self.upstreams():
            return compiler.planning_package_findings(
                self.docs, self.props, "backlog/backlog.md")[2]

    def test_explicit_headless_intake_is_allowed_and_renders_absence(self):
        self.prepare()
        self.assertEqual(self.findings(), [])
        args = SimpleNamespace(docs=str(self.docs), planning_mode="requirement",
                               requirement_ref="REQ-001", input_ref=[BA, SOLUTION],
                               absent_input=["design-system", "experience-design"])
        with self.upstreams(), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.init(args), 0)
        props, _body = compiler.parse_front_matter(self.docs / "backlog/backlog.md")
        self.assertEqual(props["input_contract"], "headless-v1")
        self.assertEqual(props["absent_input_stages"], self.props["absent_input_stages"])
        self.assertEqual(len(props["input_bindings"]), 2)

    def test_absence_is_never_inferred_from_na(self):
        self.prepare()
        self.props.pop("input_contract")
        self.props.pop("absent_input_stages")
        errors = self.findings()
        self.assertTrue(any("design-system" in error for error in errors), errors)
        self.assertTrue(any("experience-design" in error for error in errors), errors)

    def test_existing_or_deleted_visual_content_cannot_be_discarded(self):
        self.prepare()
        visual = self.docs / "design-system/MASTER.md"
        visual.parent.mkdir()
        visual.write_text("# Existing visual package\n")
        self.assertTrue(any("existing package content" in error for error in self.findings()))
        self.git("add", ".")
        self.git("commit", "-qm", "Visual package")
        visual.unlink()
        self.git("add", "-u")
        self.git("commit", "-qm", "Delete package")
        self.assertTrue(any("previous or unverifiable" in error for error in self.findings()))

    def test_bound_receipts_cannot_turn_into_absence(self):
        self.prepare()
        self.props["input_bindings"] = CARRIED
        self.assertTrue(any("both absent and bound" in error for error in self.findings()))
        with self.upstreams():
            _bindings, errors = compiler.requirement_input_bindings(
                self.docs, "REQ-001", CARRIED, [], "backlog/backlog.md",
                absent_stages=["design-system", "experience-design"])
        self.assertTrue(any("existing binding" in error for error in errors))

    def test_web_unknown_and_missing_topology_are_rejected(self):
        self.prepare()
        original = self.component.read_text()
        for kind in ("frontend-web", "backend-api", "other"):
            with self.subTest(kind=kind):
                self.component.write_text(original.replace("app_kind: worker", f"app_kind: {kind}"))
                self.assertTrue(any("topology" in error for error in self.findings()))
        self.component.unlink()
        self.assertTrue(any("topology" in error for error in self.findings()))

    def test_unapproved_feature_or_non_na_requirement_is_rejected(self):
        self.prepare()
        original = self.req.read_text()
        for text in (original.replace("status: approved", "status: draft"),
                     original.replace("request_kind: technical", "request_kind: feature"),
                     original.replace("| design-system | not_applicable |", "| design-system | required |")):
            with self.subTest(text=text):
                self.req.write_text(text)
                self.assertTrue(self.findings())

    def test_contract_stage_and_dependency_checks_fail_closed(self):
        self.prepare()
        for field, value in (("input_contract", "unknown"),
                             ("absent_input_stages", ["business-analysis"]),
                             ("absent_input_stages", "design-system"),
                             ("absent_input_stages", ["design-system", "design-system"]),
                             ("input_bindings", [])):
            with self.subTest(field=field, value=value):
                previous = self.props[field]
                self.props[field] = value
                self.assertTrue(self.findings())
                self.props[field] = previous
        note = self.docs / "backlog/epics/job/epic.md"
        note.parent.mkdir(parents=True)
        note.write_text("# Job\n\nRequires [[design-system/MASTER|Design master]].\n")
        self.assertTrue(any("references absent" in error for error in self.findings()))

    def test_failed_init_leaves_no_backlog(self):
        self.prepare()
        self.component.unlink()
        args = SimpleNamespace(docs=str(self.docs), planning_mode="requirement",
                               requirement_ref="REQ-001", input_ref=[BA, SOLUTION],
                               absent_input=["design-system", "experience-design"])
        with self.upstreams(), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compiler.init(args), 1)
        self.assertFalse((self.docs / "backlog").exists())


if __name__ == "__main__":
    unittest.main()
