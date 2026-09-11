import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
sys.path.insert(0, str(COMPILER.parent))

import backlog_compile
import landscape_check
import stage_package


class BacklogCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_new_backlog_requires_an_explicit_mode(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs")
            self.assertNotEqual(result.returncode, 0)

    def test_manual_mode_requires_all_four_input_refs(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs", "--planning-mode", "manual",
                                  "--input-ref", "[[business-analysis/a/space|A]]")
            self.assertNotEqual(result.returncode, 0)

    def test_requirement_mode_requires_requirement_ref(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs", "--planning-mode", "requirement")
            self.assertNotEqual(result.returncode, 0)

    def test_changes_requested_status_tag_uses_kebab_case(self):
        props = {
            "status": "changes_requested",
            "tags": ["doc/backlog-review", "status/changes-requested"],
        }
        contract = backlog_compile.backlog_contract()

        self.assertEqual(
            backlog_compile.status_findings(
                props, "backlog-review", "review.md", contract,
            ),
            [],
        )

        backlog_compile.status_tag(props, "changes_requested")

        self.assertEqual(
            props["tags"], ["doc/backlog-review", "status/changes-requested"],
        )
        self.assertEqual(
            backlog_compile.status_findings(
                props, "backlog-review", "review.md", contract,
            ),
            [],
        )

    def test_candidate_session_reuses_each_stage_only_within_preflight(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            docs.mkdir(parents=True)
            calls = []
            expected = [{"result_ref": "business-analysis/foundation/space"}]

            def compile_candidates(_docs):
                calls.append(_docs)
                return expected

            with mock.patch.object(
                stage_package, "ba_candidates", side_effect=compile_candidates,
            ):
                with stage_package.candidate_session():
                    self.assertIs(stage_package.candidates(docs, "business-analysis"), expected)
                    self.assertIs(stage_package.candidates(docs, "business-analysis"), expected)
                self.assertIs(stage_package.candidates(docs, "business-analysis"), expected)

            self.assertEqual(len(calls), 2)

    def test_experience_validation_session_reuses_application_and_package_compiles(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            package = docs / "experience-design" / "experiences" / "checkout"
            package.mkdir(parents=True)
            application = mock.Mock()
            application.compile_application.return_value = ({}, [])
            experience = mock.Mock()
            experience.resolve_package.return_value = package
            experience.compile_package.return_value = ({
                "registry_hash": "registry-hash",
                "records": [{"id": "SCR-001", "revision": 1,
                             "record_state": "active"}],
            }, [])
            errors: list[str] = []
            with (
                mock.patch.dict(sys.modules, {
                    "experience_application_check": application,
                    "experience_compile": experience,
                }),
                mock.patch.object(
                    backlog_compile, "parse_front_matter",
                    return_value=({"status": "approved",
                                   "registry_hash": "registry-hash"}, ""),
                ),
                backlog_compile.experience_validation_session(),
            ):
                backlog_compile.validate_experience_ref(
                    docs, "checkout:SCR-001@r1", "first", errors,
                )
                backlog_compile.validate_experience_ref(
                    docs, "checkout:SCR-001@r1", "second", errors,
                )

            self.assertFalse(errors, errors)
            self.assertEqual(application.compile_application.call_count, 1)
            self.assertEqual(experience.compile_package.call_count, 1)

    def test_manual_init_candidate_session_covers_all_preflight_reads(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            docs.mkdir(parents=True)
            events = []

            @contextmanager
            def session():
                events.append("enter")
                try:
                    yield
                finally:
                    events.append("exit")

            def bindings(*_args):
                events.append("bindings")
                return ["business-analysis|business-analysis/foundation|sha256:x"], []

            def findings(*_args):
                events.append("findings")
                return "manual", [], ["preflight rejection"]

            args = SimpleNamespace(
                docs=docs, planning_mode="manual", requirement_ref="",
                input_ref=["one", "two", "three", "four"],
            )
            with (
                mock.patch.object(
                    stage_package, "candidate_session", side_effect=session,
                ),
                mock.patch.object(
                    backlog_compile, "resolve_manual_input_bindings",
                    side_effect=bindings,
                ),
                mock.patch.object(
                    backlog_compile, "planning_package_findings",
                    side_effect=findings,
                ),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(backlog_compile.init(args), 1)

            self.assertEqual(events, ["enter", "bindings", "findings", "exit"])
            self.assertFalse((docs / "backlog").exists())

    def test_requirement_init_never_opens_a_candidate_session(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            docs.mkdir(parents=True)
            args = SimpleNamespace(
                docs=docs, planning_mode="requirement", requirement_ref="REQ-001",
                input_ref=[],
            )
            with (
                mock.patch.object(
                    stage_package, "candidate_session",
                    side_effect=AssertionError("Requirement init must not cache candidates"),
                ),
                mock.patch.object(
                    backlog_compile, "planning_package_findings",
                    return_value=("requirement", [], ["preflight rejection"]),
                ),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(backlog_compile.init(args), 1)

    def test_manual_begin_revision_candidate_session_ends_before_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            docs.mkdir(parents=True)
            events = []

            @contextmanager
            def session():
                events.append("enter")
                try:
                    yield
                finally:
                    events.append("exit")

            def bindings(*_args):
                events.append("bindings")
                return [], ["preflight rejection"]

            args = SimpleNamespace(
                docs=docs, delivery_snapshot="", planning_mode="manual",
                requirement_ref="", input_ref=["one", "two", "three", "four"],
            )
            record = {"backlog": {"path": "backlog/backlog.md"}}
            with (
                mock.patch.object(backlog_compile, "collect", return_value=(record, [])),
                mock.patch.object(backlog_compile, "approval_findings", return_value=[]),
                mock.patch.object(
                    backlog_compile, "parse_front_matter", return_value=({"revision": 1}, ""),
                ),
                mock.patch.object(
                    stage_package, "candidate_session", side_effect=session,
                ),
                mock.patch.object(
                    backlog_compile, "resolve_manual_input_bindings",
                    side_effect=bindings,
                ),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(backlog_compile.begin_revision(args), 1)

            self.assertEqual(events, ["enter", "bindings", "exit"])
            self.assertFalse((docs / "backlog").exists())


class BacklogUpstreamApprovalTests(unittest.TestCase):
    LINK = "[[solution-design/landscape|Solution Landscape]]"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name) / "project with spaces/workspace/docs"
        self.tree = self.docs / "solution-design"
        self.landscape = self.tree / "landscape.md"

    def write_note(self, relative, props, body="# Note\n"):
        path = self.docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        return path

    def approved_solution(self, *, status=None, commit=True):
        props = {"type": "landscape", "package_status": "draft", "topology_selected": True}
        if status is not None:
            props["status"] = status
        self.write_note("solution-design/landscape.md", props,
                        "# Landscape\n\n## Target\n\nSD-001 supplies the external service.\n"
                        "\n## Transition\n\nConnect the service boundary.\n\n## Components\n\n"
                        "| component | verdict | decision |\n|---|---|---|\n"
                        "| external-service | third-party | "
                        "[[solution-design/decisions/service-decision\\|SD-001]] |\n")
        self.write_note("solution-design/components/external-service/component.md", {
            "type": "solution-component", "component_id": "external-service",
            "component_class": "external-service", "sourcing": "third-party",
            "owned_ba_refs": [],
            "technology_bindings": ["solution-design/decisions/service-decision"],
        })
        self.write_note("solution-design/decisions/service-decision.md", {
            "type": "decision", "status": "accepted", "aliases": ["SD-001"],
            "decision_kind": "integration", "applies_to": ["external-service"],
            "selected_technology": "https", "method_skills": [],
        })
        (self.tree / "decision-log.md").write_text("<!-- generated by test -->\n", encoding="utf-8")
        for command in ("confirm-topology", "approve"):
            output, error = StringIO(), StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                code = landscape_check.main([command, "--tree", str(self.tree)])
            self.assertEqual(code, 0, output.getvalue() + error.getvalue())
        receipt, errors = stage_package.verify(
            self.docs, "solution-design", "solution-design/landscape", require_strict_current=True)
        self.assertFalse(errors, errors)
        self.assertEqual(receipt["verification_profile"], "strict-current")
        if commit:
            self.commit_solution()

    def commit_solution(self):
        project = self.docs.parents[1]
        commands = [
            ["init", "--quiet"],
            ["add", "--", "workspace/docs/solution-design"],
            ["-c", "user.name=Test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Approve solution fixture"],
        ]
        for args in commands:
            result = subprocess.run(["git", "-c", "core.autocrlf=false", *args],
                                    cwd=project, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def findings(self, *, evidence=False, link=None, roots=("solution-design/",)):
        errors = []
        if evidence:
            backlog_compile.validate_evidence_ref(self.docs, link or self.LINK, "story evidence", errors)
        else:
            backlog_compile.validate_upstream_ref(
                self.docs, link or self.LINK, "story constraint", roots, set(), errors)
        return errors

    def assert_rejected_by_both_consumers(self, expected="not an approved/current solution-design package"):
        for evidence in (False, True):
            with self.subTest(evidence=evidence):
                errors = self.findings(evidence=evidence)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_compiler_approved_landscape_without_note_status_is_accepted_readonly(self):
        self.approved_solution()
        props, _ = backlog_compile.parse_front_matter(self.landscape)
        self.assertNotIn("status", props)
        before = {path.relative_to(self.docs): path.read_bytes()
                  for path in self.docs.rglob("*") if path.is_file()}
        self.assertFalse(self.findings())
        self.assertFalse(self.findings(evidence=True))
        after = {path.relative_to(self.docs): path.read_bytes()
                 for path in self.docs.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_draft_package_cannot_be_approved_by_note_status(self):
        self.approved_solution(status="approved")
        landscape_check.rewrite_frontmatter(self.landscape, {"package_status": "draft"})
        self.commit_solution()
        self.assert_rejected_by_both_consumers()

    def test_missing_or_invalid_receipt_cannot_be_approved_by_note_status(self):
        self.approved_solution(status="approved")
        original = self.landscape.read_text(encoding="utf-8")
        for value in (None, "sha256:" + "0" * 64):
            with self.subTest(package_hash=value):
                self.landscape.write_text(original, encoding="utf-8")
                if value is None:
                    landscape_check.rewrite_frontmatter(self.landscape, {}, {"package_hash"})
                else:
                    landscape_check.rewrite_frontmatter(self.landscape, {"package_hash": value})
                self.commit_solution()
                self.assert_rejected_by_both_consumers()

    def test_changed_package_child_invalidates_landscape_reference(self):
        self.approved_solution(status="approved")
        child = self.tree / "decisions/service-decision.md"
        child.write_text(child.read_text(encoding="utf-8") + "\nChanged boundary.\n", encoding="utf-8")
        self.commit_solution()
        self.assert_rejected_by_both_consumers()

    def test_matching_hash_does_not_override_failed_solution_compiler(self):
        self.approved_solution(status="approved")
        landscape_check.rewrite_frontmatter(
            self.tree / "decisions/service-decision.md", {"status": "draft"})
        landscape_check.rewrite_frontmatter(
            self.landscape, {"package_hash": landscape_check.package_hash(self.tree)})
        self.commit_solution()
        self.assert_rejected_by_both_consumers()

    def test_canonical_landscape_requires_landscape_type(self):
        self.approved_solution(status="approved")
        landscape_check.rewrite_frontmatter(self.landscape, {"type": "decision"})
        landscape_check.rewrite_frontmatter(
            self.landscape, {"package_hash": landscape_check.package_hash(self.tree)})
        self.commit_solution()
        self.assert_rejected_by_both_consumers("must have type: landscape")

    def test_malformed_topology_version_returns_a_finding(self):
        self.approved_solution(status="approved")
        for value in ("invalid", "true", "false", "[]", "", "3.5", "{}"):
            with self.subTest(version=value):
                landscape_check.rewrite_frontmatter(self.landscape, {"topology_contract_version": value})
                self.assert_rejected_by_both_consumers("topology_contract_version must be an integer")

    def test_landscape_still_cannot_cross_allowed_subtree(self):
        self.approved_solution()
        self.assertTrue(any("wrong vault subtree" in error
                            for error in self.findings(roots=("design-system/",))))

    def test_other_notes_keep_document_status_contract(self):
        path = self.write_note("system-architecture/api.md", {
            "type": "api-contract", "status": "approved", "package_status": "draft",
        })
        link = "[[system-architecture/api|API contract]]"
        self.assertFalse(self.findings(link=link, roots=("system-architecture/",)))
        self.assertFalse(self.findings(link=link, evidence=True))
        landscape_check.rewrite_frontmatter(path, {"status": "draft", "package_status": "approved"})
        self.assertTrue(self.findings(link=link, roots=("system-architecture/",)))
        self.assertTrue(self.findings(link=link, evidence=True))

    def test_accepted_decision_is_evidence_not_an_approved_constraint(self):
        self.write_note("solution-design/decisions/example-decision.md", {
            "type": "decision", "status": "accepted",
        })
        link = "[[solution-design/decisions/example-decision|SD-001]]"
        self.assertFalse(self.findings(link=link, evidence=True))
        self.assertTrue(self.findings(link=link))

    def manual_binding_findings(self, digest):
        _mode, _refs, errors = backlog_compile.planning_package_findings(
            self.docs, {
                "planning_mode": "manual",
                "input_bindings": [f"solution-design|solution-design/landscape|{digest}"],
            }, "backlog/backlog.md")
        return errors

    def test_manual_handoff_rejects_uncommitted_solution(self):
        self.approved_solution(commit=False)
        props, _ = backlog_compile.parse_front_matter(self.landscape)
        errors = self.manual_binding_findings(props["package_hash"])
        self.assertIn("backlog/backlog.md input binding: solution-design/landscape has uncommitted package changes", errors)

    def test_manual_handoff_rejects_a_stale_bound_receipt(self):
        self.approved_solution()
        errors = self.manual_binding_findings("sha256:" + "0" * 64)
        self.assertIn("backlog/backlog.md input binding: solution-design/landscape package hash is stale or does not match expected hash", errors)

    def test_legacy_note_approval_behavior_is_unchanged(self):
        self.write_note("solution-design/landscape.md", {
            "type": "landscape", "status": "approved", "package_status": "approved",
            "topology_contract_version": 2,
        })
        landscape_check.rewrite_frontmatter(
            self.landscape, {"package_hash": landscape_check.package_hash(self.tree)})
        self.assertFalse(self.findings())
        self.assertFalse(self.findings(evidence=True))
        _receipt, errors = stage_package.verify(
            self.docs, "solution-design", "solution-design/landscape", require_strict_current=True)
        self.assertTrue(any("legacy-readonly" in error for error in errors))
        landscape_check.rewrite_frontmatter(self.landscape, {}, {"topology_contract_version"})
        self.assertFalse(self.findings())
        self.assertFalse(self.findings(evidence=True))

    def test_current_but_uncommitted_modern_solution_is_rejected(self):
        self.approved_solution(commit=False)
        for evidence in (False, True):
            with self.subTest(evidence=evidence):
                self.assertTrue(any("uncommitted package changes" in error
                                    for error in self.findings(evidence=evidence)))

    def test_manual_handoff_rejects_legacy_readonly_solution(self):
        self.write_note("solution-design/landscape.md", {
            "type": "landscape", "package_status": "approved", "topology_contract_version": 2,
        })
        digest = landscape_check.package_hash(self.tree)
        landscape_check.rewrite_frontmatter(self.landscape, {"package_hash": digest})
        errors = self.manual_binding_findings(digest)
        self.assertIn("backlog/backlog.md input binding: solution-design/landscape is legacy-readonly; begin a revision before using it as a new solution-design handoff", errors)


if __name__ == "__main__":
    unittest.main()
