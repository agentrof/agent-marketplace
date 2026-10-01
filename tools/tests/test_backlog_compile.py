import json
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
import backlog_review_inputs
import landscape_check
import stage_package
from tools.tests.backlog_fixture import make_approved_backlog
from tools.tests.git_fixture import init_repository, remove_temporary


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

    def test_implements_findings_require_one_typed_link_to_the_current_requirement(self):
        link = "[[requirements/req-002-tiered-verification-gates|REQ-002]]"
        self.assertEqual(backlog_compile.implements_findings({"implements": [link]}, "s", "REQ-002"), [])
        self.assertIn("quoted vault-absolute wikilink",
                      backlog_compile.implements_findings({"implements": ["REQ-002"]}, "s", "REQ-002")[0])
        self.assertIn("exact current Requirement REQ-002",
                      backlog_compile.implements_findings(
                          {"implements": ["[[requirements/req-003-other|REQ-003]]"]}, "s", "REQ-002")[0])
        self.assertIn("exact current Requirement REQ-002",
                      backlog_compile.implements_findings(
                          {"implements": ["[[backlog/backlog|REQ-002]]"]}, "s", "REQ-002")[0])
        self.assertIn("exactly one Requirement",
                      backlog_compile.implements_findings({"implements": [link, link]}, "s", "REQ-002")[0])
        self.assertIn("exactly one Requirement",
                      backlog_compile.implements_findings({}, "s", "REQ-002")[0])

    def test_stub_story_writes_the_requirement_wikilink_in_requirement_mode(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            (docs / "backlog" / "epics" / "platform").mkdir(parents=True)
            (docs / "requirements").mkdir()
            (docs / "backlog" / "backlog.md").write_text(backlog_compile.front_matter({
                "type": "backlog", "title": "Product Backlog", "status": "draft",
                "planning_mode": "requirement", "requirement_ref": "REQ-002", "revision": 7,
            }, "# Product Backlog\n"), encoding="utf-8")
            args = SimpleNamespace(
                docs=docs, epic="platform", slug="gate", id="ST-104", title="Gate",
                scope="Scope.", work_kind="technical", criterion_ref=[], experience_ref=[],
                evidence_ref=[], uses_design=[], constrained_by=[], implements=[],
            )
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(backlog_compile.stub_story(args), 2)
            self.assertFalse((docs / "backlog" / "epics" / "platform" / "stories" / "gate" / "story.md").exists())
            (docs / "requirements" / "req-002-tiered-verification-gates.md").write_text(
                backlog_compile.front_matter({
                    "type": "requirement", "id": "REQ-002", "title": "Tiered verification gates",
                    "status": "approved", "aliases": ["REQ-002"],
                }, "# Tiered verification gates\n"), encoding="utf-8")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(backlog_compile.stub_story(args), 0)
            props, _body = backlog_compile.parse_front_matter(
                docs / "backlog" / "epics" / "platform" / "stories" / "gate" / "story.md")
            self.assertEqual(props["implements"], ["[[requirements/req-002-tiered-verification-gates|REQ-002]]"])
            self.assertEqual(backlog_compile.implements_findings(props, "story.md", "REQ-002"), [])

    def test_stub_story_passes_the_per_write_vault_check(self):
        import vault_check
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            (docs / "maps").mkdir(parents=True)
            (docs.parent / "config.json").write_text(json.dumps({
                "schema_version": 2, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
            }), encoding="utf-8")
            make_approved_backlog(docs)
            # A planning mode makes the stub carry origin_mode; the legacy shape has its own test.
            backlog = docs / "backlog" / "backlog.md"
            props, body = backlog_compile.parse_front_matter(backlog)
            props["planning_mode"] = "manual"
            backlog.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            constraint = "[[solution-design/landscape|Solution Landscape]]"
            for slug, identity, constrained_by in (("job-worker", "AUTH-02", []),
                                                   ("job-api", "AUTH-03", [constraint])):
                args = SimpleNamespace(
                    docs=docs, epic="delivery-fixture", slug=slug, id=identity, title=slug,
                    scope="Run the job.", work_kind="technical", criterion_ref=[], experience_ref=[],
                    evidence_ref=[], uses_design=[], constrained_by=constrained_by, implements=[],
                )
                with redirect_stdout(StringIO()):
                    self.assertEqual(backlog_compile.stub_story(args), 0)
                story = f"backlog/epics/delivery-fixture/stories/{slug}/story.md"
                props, _body = backlog_compile.parse_front_matter(docs / story)
                self.assertNotIn("uses_design", props)
                self.assertEqual(props.get("constrained_by"), constrained_by or None)
                vault = vault_check.build_vault(docs, vault_check.load_policy(vault_check.DEFAULT_POLICY))
                self.assertEqual(vault_check.changed_findings(vault, [story])[story], [])

    def test_stub_story_in_a_legacy_backlog_writes_no_origin_mode(self):
        import vault_check
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            (docs / "maps").mkdir(parents=True)
            (docs.parent / "config.json").write_text(json.dumps({
                "schema_version": 2, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
            }), encoding="utf-8")
            make_approved_backlog(docs)
            backlog = docs / "backlog" / "backlog.md"
            props, body = backlog_compile.parse_front_matter(backlog)
            self.assertNotIn("planning_mode", props)
            self.assertTrue(props["legacy_contract"])
            args = SimpleNamespace(
                docs=docs, epic="delivery-fixture", slug="job-worker", id="AUTH-02", title="job-worker",
                scope="Run the job.", work_kind="technical", criterion_ref=[], experience_ref=[],
                evidence_ref=[], uses_design=[], constrained_by=[], implements=[],
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(backlog_compile.stub_story(args), 0)
            story = "backlog/epics/delivery-fixture/stories/job-worker/story.md"
            story_props, _body = backlog_compile.parse_front_matter(docs / story)
            self.assertNotIn("origin_mode", story_props)
            revision = int(props.get("revision", 1) or 1)
            self.assertEqual(story_props["introduced_in_revision"], revision)
            vault = vault_check.build_vault(docs, vault_check.load_policy(vault_check.DEFAULT_POLICY))
            self.assertEqual(vault_check.changed_findings(vault, [story])[story], [])

            def origin_findings():
                _record, errors = backlog_compile.collect(docs)
                return [error for error in errors if "origin_mode" in error]

            self.assertEqual(origin_findings(), [])
            # A later revision picks a planning mode. The legacy story stays
            # readable only while the backlog carries its legacy contract.
            props.update(planning_mode="manual", revision=revision + 1)
            backlog.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            self.assertEqual(origin_findings(), [])
            del props["legacy_contract"]
            backlog.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            self.assertIn(f"{story} needs origin_mode and introduced_in_revision", origin_findings())

    def test_an_untouched_delivery_notes_stub_is_reported_above_the_navigation(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            make_approved_backlog(docs)
            args = SimpleNamespace(
                docs=docs, epic="delivery-fixture", slug="job-worker", id="AUTH-02", title="job-worker",
                scope="Run the job.", work_kind="technical", criterion_ref=[], experience_ref=[],
                evidence_ref=[], uses_design=[], constrained_by=[], implements=[],
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(backlog_compile.stub_story(args), 0)
            story = docs / "backlog/epics/delivery-fixture/stories/job-worker/story.md"
            stub = backlog_compile.STORY_STUBS["Delivery Notes"]
            text = story.read_text(encoding="utf-8")
            # The stub is the last section, so navigation lands right under it.
            self.assertLess(text.index(stub), text.index(backlog_compile.NAV_MARKER))
            prefix = "backlog/epics/delivery-fixture/stories/job-worker/story.md has an untouched"
            delivery_notes = f"{prefix} Delivery Notes stub"
            record, errors = backlog_compile.collect(docs)
            self.assertIn(delivery_notes, record["scaffold_findings"])
            self.assertIn(delivery_notes, errors)
            story.write_text(text.replace(stub, "Run the worker beside the approved API only."),
                             encoding="utf-8")
            record, errors = backlog_compile.collect(docs)
            self.assertNotIn(delivery_notes, errors)
            self.assertIn(f"{prefix} Non-Goals stub", record["scaffold_findings"])

    def test_an_empty_last_section_of_a_new_story_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            make_approved_backlog(docs)
            args = SimpleNamespace(
                docs=docs, epic="delivery-fixture", slug="job-worker", id="AUTH-02", title="job-worker",
                scope="Run the job.", work_kind="technical", criterion_ref=[], experience_ref=[],
                evidence_ref=[], uses_design=[], constrained_by=[], implements=[],
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(backlog_compile.stub_story(args), 0)
            story = docs / "backlog/epics/delivery-fixture/stories/job-worker/story.md"
            text = story.read_text(encoding="utf-8")
            prefix = "backlog/epics/delivery-fixture/stories/job-worker/story.md"
            # The writer clears the Delivery Notes stub and writes nothing in its place.
            story.write_text(text.replace(backlog_compile.STORY_STUBS["Delivery Notes"] + "\n", ""),
                             encoding="utf-8")
            record, errors = backlog_compile.collect(docs)
            self.assertIn(f"{prefix} required section is empty: Delivery Notes", errors)
            self.assertNotIn(f"{prefix} required section is empty: Delivery Notes",
                             record["scaffold_findings"])
            self.assertEqual(record["advisory_findings"], [])
            # A middle section was always read up to the next heading.
            story.write_text(text.replace(backlog_compile.STORY_STUBS["Non-Goals"], ""),
                             encoding="utf-8")
            _record, errors = backlog_compile.collect(docs)
            self.assertIn(f"{prefix} required section is empty: Non-Goals", errors)
            self.assertNotIn(f"{prefix} required section is empty: Delivery Notes", errors)

    def test_an_approved_story_with_an_empty_last_section_is_advisory_until_revised(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            make_approved_backlog(docs)
            story = docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
            notes = "Preserve the approved API boundary and avoid delivery-state metadata.\n"
            # An approval from before the compiler read the last section above
            # the navigation: the stamp is intact over an empty Delivery Notes.
            props, body = backlog_compile.parse_front_matter(story)
            story.write_text(backlog_compile.front_matter(props, body.replace(notes, "")),
                             encoding="utf-8")
            props, body = backlog_compile.parse_front_matter(story)
            props["source_hash"] = backlog_compile.digest(story)
            story.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            root = docs / "backlog/backlog.md"
            record, _errors = backlog_compile.collect(docs)
            props, body = backlog_compile.parse_front_matter(root)
            props["package_hash"] = backlog_compile.package_digest(
                docs, backlog_compile.package_paths(record, docs))
            root.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            finding = ("backlog/epics/delivery-fixture/stories/auth-01/story.md required section"
                       " is empty: Delivery Notes")
            advisory = f"{finding}; advisory until the approved story is revised"
            record, errors = backlog_compile.collect(docs)
            self.assertEqual((errors, record["advisory_findings"]), ([], [advisory]))
            output = StringIO()
            with redirect_stdout(output):
                code = backlog_compile.main(["check", "--docs", str(docs), "--approved", "--json"])
            result = json.loads(output.getvalue())
            self.assertEqual((code, result["ok"], result["advisories"]), (0, True, [advisory]))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(backlog_compile.main(["check", "--docs", str(docs)]), 0)
            self.assertEqual(output.getvalue(), f"ADVISORY [backlog] {advisory}\nbacklog ok\n")
            # Revising the story ends the grace: its stamp no longer matches.
            story.write_text(story.read_text(encoding="utf-8").replace(
                "Administrative bulk operations", "Administrative bulk imports"), encoding="utf-8")
            record, errors = backlog_compile.collect(docs)
            self.assertEqual((errors, record["advisory_findings"]), ([finding], []))

    def test_an_approved_story_with_an_untouched_delivery_notes_stub_is_advisory_until_revised(self):
        import delivery_compile

        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            make_approved_backlog(docs)
            story = docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
            notes = "Preserve the approved API boundary and avoid delivery-state metadata."
            # An approval from before the compiler read the stub above the
            # navigation: the stamp is intact over the untouched stub.
            props, body = backlog_compile.parse_front_matter(story)
            story.write_text(backlog_compile.front_matter(
                props, body.replace(notes, backlog_compile.STORY_STUBS["Delivery Notes"])),
                encoding="utf-8")
            props, body = backlog_compile.parse_front_matter(story)
            props["source_hash"] = backlog_compile.digest(story)
            story.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            root = docs / "backlog/backlog.md"
            record, _errors = backlog_compile.collect(docs)
            props, body = backlog_compile.parse_front_matter(root)
            props["package_hash"] = backlog_compile.package_digest(
                docs, backlog_compile.package_paths(record, docs))
            root.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
            finding = ("backlog/epics/delivery-fixture/stories/auth-01/story.md has an untouched"
                       " Delivery Notes stub")
            advisory = f"{finding}; advisory until the approved story is revised"
            for historical in (False, True):
                with self.subTest(historical_inputs=historical):
                    record, errors = backlog_compile.collect(docs, historical_inputs=historical)
                    self.assertEqual((errors, record["advisory_findings"]), ([], [advisory]))
            # Delivery reads the approved backlog as it did before.
            _selected, _sources, errors = delivery_compile.approved_backlog_sources(
                docs, ["AUTH-01"])
            self.assertEqual(errors, [])
            output = StringIO()
            with redirect_stdout(output):
                code = backlog_compile.main(["check", "--docs", str(docs), "--approved", "--json"])
            result = json.loads(output.getvalue())
            self.assertEqual((code, result["ok"], result["advisories"]), (0, True, [advisory]))
            # Revising the story ends the grace: the stub is writer work again.
            story.write_text(story.read_text(encoding="utf-8").replace(
                "Administrative bulk operations", "Administrative bulk imports"), encoding="utf-8")
            record, errors = backlog_compile.collect(docs)
            self.assertEqual((errors, record["advisory_findings"]), ([finding], []))
            self.assertEqual(record["scaffold_findings"], [finding])

    def test_a_backlog_without_an_empty_last_section_checks_as_released(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace" / "docs"
            make_approved_backlog(docs)
            output = StringIO()
            with redirect_stdout(output):
                code = backlog_compile.main(["check", "--docs", str(docs), "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn("advisories", json.loads(output.getvalue()))

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

    def test_approve_preflight_reuses_candidate_and_experience_sessions(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw)
            args = SimpleNamespace(docs=docs)
            events = []

            @contextmanager
            def candidate_session():
                events.append("candidate-enter")
                try:
                    yield
                finally:
                    events.append("candidate-exit")

            @contextmanager
            def experience_session():
                events.append("experience-enter")
                try:
                    yield
                finally:
                    events.append("experience-exit")

            with (
                mock.patch.object(
                    backlog_compile, "collect",
                    return_value=({"backlog_reviews": [], "epics": []}, ["reject"]),
                ),
                mock.patch.object(
                    stage_package, "candidate_session", side_effect=candidate_session,
                ),
                mock.patch.object(
                    backlog_compile, "experience_validation_session",
                    side_effect=experience_session,
                ),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(backlog_compile.approve(args), 1)

            self.assertEqual(events, [
                "candidate-enter", "experience-enter", "experience-exit", "candidate-exit",
            ])


class BacklogUpstreamApprovalTests(unittest.TestCase):
    LINK = "[[solution-design/landscape|Solution Landscape]]"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
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
        init_repository(project)
        commands = [
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

    def revise_solution(self):
        """Open, change and re-approve the committed solution package; return (old, new) hashes."""
        old = backlog_compile.parse_front_matter(self.landscape)[0]["package_hash"]
        for command in ("begin-revision",):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(landscape_check.main([command, "--tree", str(self.tree)]), 0)
        decision = self.tree / "decisions" / "service-decision.md"
        decision.write_text(decision.read_text(encoding="utf-8") + "\nRevised rationale.\n", encoding="utf-8")
        for command in ("confirm-topology", "approve"):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(landscape_check.main([command, "--tree", str(self.tree)]), 0)
        project = self.docs.parents[1]
        for args in (["add", "--", "workspace/docs/solution-design"],
                     ["-c", "user.name=Test", "-c", "user.email=test@example.com",
                      "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Approve solution revision"]):
            result = subprocess.run(["git", "-c", "core.autocrlf=false", *args],
                                    cwd=project, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = backlog_compile.parse_front_matter(self.landscape)[0]["package_hash"]
        self.assertNotEqual(old, new)
        return old, new

    def test_historical_binding_resolves_to_the_earlier_approved_receipt(self):
        self.approved_solution()
        old, new = self.revise_solution()
        receipt, errors = stage_package.verify(
            self.docs, "solution-design", "solution-design/landscape", old,
            require_committed=True, allow_historical=True)
        self.assertEqual(errors, [])
        self.assertEqual((receipt["package_hash"], receipt["status"], receipt["current"],
                          receipt["committed"], receipt["verification_profile"]),
                         (old, "approved", False, True, "historical"))
        current, errors = stage_package.verify(
            self.docs, "solution-design", "solution-design/landscape", new,
            require_committed=True, allow_historical=True)
        self.assertEqual(errors, [])
        self.assertTrue(current["current"])

    def test_historical_binding_still_rejects_a_hash_that_was_never_approved(self):
        self.approved_solution()
        self.revise_solution()
        for digest in ("sha256:" + "0" * 64, "sha256:" + "f" * 64):
            _receipt, errors = stage_package.verify(
                self.docs, "solution-design", "solution-design/landscape", digest,
                require_committed=True, allow_historical=True)
            self.assertIn("solution-design/landscape package hash is stale or does not match expected hash", errors)

    def test_strict_binding_rejects_the_earlier_receipt_after_a_revision(self):
        self.approved_solution()
        old, _new = self.revise_solution()
        self.assertIn(
            "backlog/backlog.md input binding: solution-design/landscape package hash is stale or does not match expected hash",
            self.manual_binding_findings(old))

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


class AcceptedMinorFindingsTests(unittest.TestCase):
    """Minor review findings left unfixed need a complete, traceable record."""

    STORY = "[[backlog/epics/delivery-fixture/stories/auth-01/story\\|AUTH-01]]"
    HEADER = "| finding | owner_role | reason | revisit_trigger |"
    VALID = (f"| {STORY} Scope states the lockout rule twice in different words. "
             "| product_owner | Both sentences state one rule, so behavior and "
             "verification are unchanged. | Revisit at the next revision of this story. |")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name) / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs.parent / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        }), encoding="utf-8")
        make_approved_backlog(self.docs)
        self.epic_review = (self.docs / "backlog/epics/delivery-fixture/reviews"
                            / "round-1-epic-review.md")
        self.root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.originals = {path: path.read_text(encoding="utf-8")
                          for path in (self.epic_review, self.root_review)}

    def accept(self, review: Path, *rows: str, header: str = HEADER) -> None:
        review.write_text(self.originals[review], encoding="utf-8")
        props, body = backlog_compile.parse_front_matter(review)
        separator = "|" + "---|" * (header.count("|") - 1)
        section = "\n".join(["## Accepted Minor Findings", "", header, separator,
                             *rows, "", ""])
        review.write_text(backlog_compile.front_matter(
            props, body.replace("## Verdict", section + "## Verdict", 1)),
            encoding="utf-8")

    def errors(self) -> list[str]:
        _record, errors = backlog_compile.collect(self.docs)
        return errors

    def test_reviews_without_the_section_stay_valid(self):
        self.assertEqual(self.errors(), [])

    def test_complete_rows_pass_in_epic_and_root_reviews(self):
        other = self.VALID.replace("product_owner", "qa_engineer").replace(
            "Scope states", "Delivery Notes state")
        self.accept(self.epic_review, self.VALID, other)
        self.accept(self.root_review, self.VALID)
        self.assertEqual(self.errors(), [])

    def test_incomplete_rows_fail_with_the_missing_follow_up(self):
        cases = {
            "must cite the affected vault note": self.VALID.replace(self.STORY + " ", ""),
            "targets missing note": self.VALID.replace("auth-01/story", "missing/story"),
            "needs a concrete finding": self.VALID.replace(
                "Scope states the lockout rule twice in different words.", "typo"),
            "owner_role must be one of: product_owner, qa_engineer, business_analyst":
                self.VALID.replace("product_owner", "backend_developer"),
            "needs a concrete reason": self.VALID.replace(
                "Both sentences state one rule, so behavior and verification are unchanged.",
                "TODO"),
            "needs a concrete revisit_trigger": self.VALID.replace(
                "Revisit at the next revision of this story.", "later"),
        }
        for review in (self.epic_review, self.root_review):
            label = review.relative_to(self.docs).as_posix()
            for expected, row in cases.items():
                with self.subTest(review=label, expected=expected):
                    self.accept(review, row)
                    errors = self.errors()
                    self.assertTrue(any(error.startswith(label) and expected in error
                                        for error in errors), errors)
                    self.assertTrue(all("accepted minor finding" in error
                                        for error in errors), errors)
            review.write_text(self.originals[review], encoding="utf-8")

    def test_duplicate_rows_and_other_columns_fail(self):
        self.accept(self.epic_review, self.VALID, self.VALID)
        self.assertIn("backlog/epics/delivery-fixture/reviews/round-1-epic-review.md "
                      "repeats accepted minor finding 2", self.errors())
        self.accept(self.epic_review, self.VALID.replace("| product_owner |",
                                                         "| minor | product_owner |"),
                    header="| finding | severity | owner_role | reason | revisit_trigger |")
        self.assertIn("backlog/epics/delivery-fixture/reviews/round-1-epic-review.md "
                      "Accepted Minor Findings columns must be: finding, owner_role, "
                      "reason, revisit_trigger", self.errors())

    def test_code_spans_in_review_citations_are_not_links(self):
        code = "`pages/api/v1/health-checks/[[...resource]].ts`"
        props, body = backlog_compile.parse_front_matter(self.root_review)
        evidence = next(line for line in body.splitlines() if line.startswith("Evidence ["))
        self.root_review.write_text(backlog_compile.front_matter(
            props, body.replace(evidence, f"{evidence} It routes {code}.", 1)), encoding="utf-8")
        self.accept(self.epic_review, self.VALID.replace("twice", f"for {code} twice"))
        self.assertEqual(self.errors(), [])
        self.accept(self.epic_review, self.VALID.replace(self.STORY, code))
        self.assertTrue(any("must cite the affected vault note" in error for error in self.errors()))
        self.root_review.write_text(backlog_compile.front_matter(
            props, body.replace(evidence, evidence.split("[[", 1)[0] + f"{code} records the inputs.", 1)),
            encoding="utf-8")
        self.assertTrue(any("review Evidence must cite a vault note" in error for error in self.errors()))

    def test_review_input_discovery_leaves_the_record_to_the_final_gate(self):
        self.accept(self.epic_review, self.VALID.replace("product_owner", "backend_developer"))
        self.assertTrue(backlog_review_inputs.manifest(self.docs, epic="EP-001")["ok"])
        self.assertTrue(any("owner_role must be one of" in error for error in self.errors()))


class ReviewRecordTests(unittest.TestCase):
    """Switch `review_loop` at `blocking_delta` keeps a review note's record: the
    findings the review returned with their ids and severities, the calibration
    rulings and the finding id of each accepted minor finding."""

    STORY = "[[backlog/epics/delivery-fixture/stories/auth-01/story\\|AUTH-01]]"
    RETURNED = (
        f"| F-1 | major | {STORY} Scope states the lockout rule twice, with five and with six attempts. |",
        f"| F-2 | minor | {STORY} Scope states the lockout rule twice in different words. |",
    )
    LOWERED = (f"| F-1 | major | minor | {STORY} Scope and Acceptance both name five attempts,"
               " so behavior and tests stay the same. |")
    ACCEPTED = (
        f"| F-1 {STORY} Scope names the attempts in two sentences. | product_owner | Both name five"
        " attempts, so behavior and tests stay the same. | Revisit at the next revision of AUTH-01. |",
        f"| F-2 {STORY} Scope states the lockout rule twice in different words. | qa_engineer | Both"
        " sentences state one rule, so verification is unchanged. | Revisit at the next revision of AUTH-01. |",
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name) / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs.parent / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        }), encoding="utf-8")
        make_approved_backlog(self.docs)
        self.epic_review = (self.docs / "backlog/epics/delivery-fixture/reviews"
                            / "round-1-epic-review.md")
        self.root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.originals = {path: path.read_text(encoding="utf-8")
                          for path in (self.epic_review, self.root_review)}
        self.loop("blocking_delta")

    def loop(self, value: str) -> None:
        import process_policy

        first = "begin-revision" if process_policy.path_for(self.docs).exists() else "init"
        for step in ((first,), ("set", "--switch", "review_loop", "--value", value), ("approve",)):
            output = StringIO()
            with redirect_stdout(output):
                code = process_policy.main([step[0], "--docs", str(self.docs), *step[1:]])
            self.assertEqual(code, 0, output.getvalue())

    def record(self, review: Path, *, returned=RETURNED, calibration=(LOWERED,), accepted=ACCEPTED,
               status: str = "draft") -> str:
        review.write_text(self.originals[review], encoding="utf-8")
        props, body = backlog_compile.parse_front_matter(review)
        sections = []
        for title, header, rows in (
                ("Returned Findings", "| finding | severity | description |", returned),
                ("Severity Calibration", "| finding | claimed_severity | calibrated_severity | reason |",
                 calibration),
                ("Accepted Minor Findings", "| finding | owner_role | reason | revisit_trigger |",
                 accepted)):
            if rows is not None:
                separator = "|" + "---|" * (header.count("|") - 1)
                sections.append("\n".join([f"## {title}", "", header, separator, *rows, "", ""]))
        backlog_compile.status_tag(props, status)
        review.write_text(backlog_compile.front_matter(
            props, body.replace("## Verdict", "".join(sections) + "## Verdict", 1)), encoding="utf-8")
        return review.relative_to(self.docs).as_posix()

    def errors(self) -> list[str]:
        return backlog_compile.collect(self.docs)[1]

    def test_a_complete_record_passes_in_epic_and_root_reviews(self):
        for review in (self.epic_review, self.root_review):
            with self.subTest(review=review.name):
                self.record(review)
                self.assertEqual(self.errors(), [])
                review.write_text(self.originals[review], encoding="utf-8")

    def test_a_finding_the_review_returned_as_blocking_never_enters_accepted_minor_findings(self):
        path = self.epic_review.relative_to(self.docs).as_posix()
        label = f"{path} accepted minor finding 1 names F-1, which"
        confirmed = self.LOWERED.replace("| minor |", "| major |")
        cases = (
            ((confirmed,), [f"{label} calibration ruled major; only a minor finding is accepted"]),
            ((self.LOWERED.replace("| minor |", "| invalid |"),),
             [f"{label} calibration ruled invalid; only a minor finding is accepted"]),
            ((), [f"{path} returned major finding F-1 has no Severity Calibration row",
                  f"{label} the review returned as major; only a minor finding is accepted"]),
        )
        for calibration, expected in cases:
            with self.subTest(calibration=calibration):
                self.record(self.epic_review, calibration=calibration)
                self.assertEqual(sorted(self.errors()), sorted(expected))
        self.record(self.epic_review, accepted=(self.ACCEPTED[1].replace("| F-2 ", "| "),))
        self.assertEqual(self.errors(), [
            f"{path} accepted minor finding 1 must start with the id of the finding it accepts"])
        self.record(self.epic_review, returned=None, calibration=None, accepted=(self.ACCEPTED[1],))
        self.assertEqual(self.errors(), [
            f"{path} accepted minor finding 1 names F-2, which Returned Findings does not list"])

    def test_severity_calibration_rows_are_validated(self):
        path = self.root_review.relative_to(self.docs).as_posix()
        label = f"{path} severity calibration 1"
        reason = "Scope and Acceptance both name five attempts, so behavior and tests stay the same."
        cases = {
            # The finding's own example: a lowering with no citation and no returned claim.
            "| F-3 | major | minor | not a real problem |": [
                f"{label} reason must cite a vault note", f"{label} needs a concrete reason",
                f"{label} rules F-3, which Returned Findings does not list",
                f"{path} returned major finding F-1 has no Severity Calibration row"],
            self.LOWERED.replace("| major | minor |", "| minor | minor |"): [
                f"{label} claimed_severity must be critical or major",
                f"{label} claimed_severity must be major, the severity F-1 was returned at"],
            self.LOWERED.replace("| major | minor |", "| major | critical |"): [
                f"{label} calibrated_severity must confirm major or be minor or invalid"],
            self.LOWERED.replace(self.STORY, "[[backlog/epics/missing/story\\|ST-9]]"): [
                f"{label} targets missing note: backlog/epics/missing/story"],
            self.LOWERED.replace(reason, "Fine."): [f"{label} needs a concrete reason"],
            self.LOWERED.replace("| F-1 |", "| one |"): [
                f"{label} finding must be an id such as F-3: one",
                f"{path} returned major finding F-1 has no Severity Calibration row"],
        }
        for row, expected in cases.items():
            with self.subTest(row=row):
                self.record(self.root_review, calibration=(row,), accepted=(self.ACCEPTED[1],))
                self.assertEqual(sorted(self.errors()), sorted(expected))
        self.record(self.root_review, calibration=(self.LOWERED, self.LOWERED), accepted=None)
        self.assertEqual(self.errors(), [f"{path} calibrates finding F-1 twice"])
        props, body = backlog_compile.parse_front_matter(self.root_review)
        self.root_review.write_text(backlog_compile.front_matter(props, body.replace(
            "| finding | claimed_severity | calibrated_severity | reason |",
            "| finding | severity | reason | revisit |")), encoding="utf-8")
        self.assertIn(f"{path} Severity Calibration columns must be: finding, claimed_severity,"
                      " calibrated_severity, reason", self.errors())

    def test_returned_findings_carry_an_id_a_severity_and_a_citation(self):
        path = self.epic_review.relative_to(self.docs).as_posix()
        label = f"{path} returned finding 1"
        cases = {
            f"{label} finding must be an id such as F-3: two": ("| F-2 |", "| two |"),
            f"{label} severity must be critical, major or minor": ("| minor |", "| low |"),
            f"{label} description must cite a vault note": (self.STORY + " ", ""),
        }
        for expected, (old, new) in cases.items():
            with self.subTest(expected=expected):
                self.record(self.epic_review, returned=(self.RETURNED[1].replace(old, new),),
                            calibration=None, accepted=None)
                self.assertEqual(self.errors(), [expected])
        self.record(self.epic_review, returned=(self.RETURNED[1], self.RETURNED[1]),
                    calibration=None, accepted=None)
        self.assertEqual(self.errors(), [f"{path} returns finding F-2 twice"])

    def test_a_note_approved_before_its_review_kept_a_record_stays_as_it_was(self):
        old_style = (self.ACCEPTED[1].replace("| F-2 ", "| "),)
        self.record(self.epic_review, returned=None, calibration=None, accepted=old_style,
                    status="approved")
        self.assertEqual(self.errors(), [])
        self.record(self.epic_review, returned=None, calibration=None, accepted=old_style)
        path = self.epic_review.relative_to(self.docs).as_posix()
        self.assertEqual(self.errors(), [
            f"{path} accepted minor finding 1 must start with the id of the finding it accepts"])
        # An approved note that carries a record keeps being read as one.
        self.record(self.epic_review, calibration=(), status="approved")
        self.assertIn(f"{path} returned major finding F-1 has no Severity Calibration row", self.errors())

    def test_the_record_is_authored_text_unless_the_loop_is_blocking_delta(self):
        garbage = {"returned": ("| not an id | fatal | no citation |",),
                   "calibration": ("| F-3 | major | minor | not a real problem |",)}
        for value in ("current", None):
            with self.subTest(policy=value):
                if value is None:
                    (self.docs / "delivery/process-policy.md").unlink()
                else:
                    self.loop(value)
                self.record(self.epic_review, **garbage, accepted=None)
                self.assertEqual(self.errors(), [])
        self.loop("blocking_delta")
        import process_policy
        with redirect_stdout(StringIO()):
            self.assertEqual(process_policy.main(["begin-revision", "--docs", str(self.docs)]), 0)
        errors = self.errors()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("needs the review_loop value of the Process Policy", errors[0])
        self.assertIn("is a draft", errors[0])


if __name__ == "__main__":
    unittest.main()
