"""Derived task inputs preserve full reads, scoped sources and role boundaries."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import backlog_compile
import process_policy
import task_inputs
from backlog_fixture import CONSTRAINT, CRITERION, DESIGN, EXPERIENCE, _author_story, make_approved_backlog
from git_fixture import init_repository, remove_temporary
from test_default_equivalence import SWITCH_DATA, SWITCH_FILES, build_task_package, build_task_project


def commit_all(root):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"], check=True, capture_output=True)


class TaskInputTests(unittest.TestCase):
    def test_setup_supports_unborn_repository_but_binds_initial_files_and_commit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            init_repository(root)
            (root / "README.md").write_text("New project\n", encoding="utf-8")
            kwargs = dict(entry="setup", role="delivery-coordinator", mode="create", project=root)
            result = task_inputs.manifest(**kwargs)
            self.assertIsNone(result["head"])
            self.assertEqual(result["changed_paths"], ["README.md"])
            with self.assertRaisesRegex(ValueError, "committed Git HEAD"):
                task_inputs.manifest(entry="deliver", role="code-reviewer", mode="review", project=root)
            with self.assertRaisesRegex(ValueError, "committed Git HEAD"):
                task_inputs.manifest(**kwargs, base="HEAD")
            (root / "README.md").write_text("Changed project\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            self.commit(root)
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def make_project(self, root):
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True, capture_output=True)
        (root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        self.commit(root)

    def commit(self, root):
        commit_all(root)

    def note(self, root, relative, kind, owner, extra=""):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: {kind}\nowner_role: {owner}\n{extra}---\n\n# Selected source\n", encoding="utf-8")
        return relative

    def test_ba_write_scope_is_exact_owned_selected_space_and_not_read_dependencies(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            own = self.note(root, "workspace/docs/business-analysis/orders/space.md", "space", "business_analyst")
            other = self.note(root, "workspace/docs/business-analysis/payments/space.md", "space", "business_analyst")
            dependency = self.note(root, "workspace/docs/solution-design/landscape.md", "landscape", "solution_architect")
            generated = self.note(root, "workspace/docs/business-analysis/orders/_generated/catalog.md", "space", "business_analyst")
            self.commit(root)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="repair", project=root,
                          inputs=[own, dependency, generated])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["write_scope"]["allowed_write_area"],
                             [{"path": own, "coverage": "exact_file", "source": own}])
            self.assertFalse(result["write_scope"]["writer_authority"])
            self.assertIn("ba_compile.py", next(row["detail"] for row in result["next_transition_conditions"]
                                               if row["condition"] == "entry_gate"))
            unresolved = task_inputs.manifest(**{**kwargs, "inputs": [own, other]})
            self.assertEqual(unresolved["write_scope"]["status"], "unresolved")
            self.assertEqual(unresolved["write_scope"]["allowed_write_area"], [])
            review = task_inputs.manifest(**{**kwargs, "role": "analysis-challenger"})
            self.assertEqual(review["write_scope"]["allowed_write_area"], [])

    def test_epic_owner_write_area_excludes_dependency_context_and_keeps_po_as_writer(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            rows = [
                ("backlog/backlog.md", "backlog", "product_owner"),
                ("backlog/epics/one/epic.md", "epic", "product_owner"),
                ("backlog/epics/one/stories/one/story.md", "story", "backend_developer"),
                ("backlog/epics/one/stories/one/test-plan.md", "test-plan", "qa_engineer"),
                ("backlog/epics/one/reviews/round-1-epic-review.md", "epic-review", "product_owner"),
                ("backlog/epics/two/stories/two/story.md", "story", "backend_developer"),
            ]
            for path, kind, owner in rows:
                self.note(root, "workspace/docs/" + path, kind, owner)
            self.commit(root)
            closure = {"scope": rows[1][0], "primary_paths": [row[0] for row in rows[:4]],
                       "paths": [row[0] for row in rows], "review": {"path": rows[4][0]},
                       "check": {}}
            unscoped = task_inputs.manifest(entry="backlog-plan", mode="revise", project=root,
                                            role="product-owner", inputs=["workspace/docs/" + row[0] for row in rows])
            self.assertEqual(unscoped["write_scope"]["status"], "unresolved")
            self.assertEqual(unscoped["write_scope"]["allowed_write_area"], [])
            kwargs = dict(entry="backlog-plan", mode="revise", project=root, epic="one")
            with mock.patch("backlog_review_inputs.manifest", return_value=closure):
                result = task_inputs.manifest(**kwargs, role="product-owner")
                self.assertEqual([row["path"] for row in result["write_scope"]["allowed_write_area"]],
                                 sorted("workspace/docs/" + row[0] for row in rows[1:5]))
                for role in ("business-analyst", "qa-engineer", "backlog-reviewer"):
                    reader = task_inputs.manifest(**kwargs, role=role)
                    self.assertEqual(reader["write_scope"]["status"], "read_only")
                    self.assertEqual(reader["write_scope"]["allowed_write_area"], [])

    def test_product_owner_scope_covers_a_story_right_after_stub_story(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "maps").mkdir(parents=True)
            (root / "workspace/config.json").write_text(json.dumps({
                "schema_version": 2, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
            }), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                make_approved_backlog(docs)
                self.commit(root)
                self.assertEqual(backlog_compile.stub_story(SimpleNamespace(
                    docs=str(docs), epic="delivery-fixture", slug="job-worker", id="AUTH-02",
                    title="Job worker", scope=None, work_kind="technical", criterion_ref=[],
                    experience_ref=[], evidence_ref=["[[solution-design/decisions/fixture-api|Fixture API]]"],
                    uses_design=[], constrained_by=[], implements=[])), 0)
            folder = "workspace/docs/backlog/epics/delivery-fixture/stories/job-worker/"
            kwargs = dict(entry="backlog-plan", project=root, epic="EP-001")
            writer = task_inputs.manifest(**kwargs, role="product-owner", mode="revise")
            self.assertEqual(writer["write_scope"]["status"], "resolved")
            area = [row["path"] for row in writer["write_scope"]["allowed_write_area"]]
            self.assertIn(folder + "story.md", area)
            self.assertIn(folder + "test-plan.md", area)
            self.assertTrue(writer["backlog_scope"]["check"]["scaffold_findings"])
            for role, mode in (("backlog-reviewer", "review"), ("product-owner", "review")):
                with self.assertRaisesRegex(ValueError, "untouched"):
                    task_inputs.manifest(**kwargs, role=role, mode=mode)

    def test_item_claims_bind_one_item_and_role_without_granting_runtime_or_vault_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            item = self.note(root, "workspace/docs/delivery/deliveries/one/items/st-001/item.md",
                             "delivery-item", "backend_developer", "role_sequence:\n  - backend_developer\n  - code_reviewer\n  - qa_engineer\npath_claims:\n  - src\n  - tests\n  - workspace/docs\n  - .git\n")
            self.commit(root)
            kwargs = dict(entry="deliver", role="backend-developer", mode="repair", project=root, inputs=[item])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["write_scope"]["allowed_write_area"], [
                {"path": path, "coverage": "path_and_descendants", "source": item} for path in ("src", "tests")])
            self.assertFalse(result["write_scope"]["writer_authority"])
            self.assertIn("workspace/docs", result["write_scope"]["excluded_subtrees"])
            other_role = task_inputs.manifest(**{**kwargs, "role": "frontend-developer"})
            self.assertEqual(other_role["write_scope"]["status"], "unresolved")
            self.assertEqual(other_role["write_scope"]["allowed_write_area"], [])
            content = (root / item).read_text(encoding="utf-8")
            (root / item).write_text(content.replace("  - src", "  - ../outside"), encoding="utf-8")
            self.assertEqual(task_inputs.manifest(**kwargs)["write_scope"]["status"], "unresolved")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def test_lane_roles_bind_only_their_lane_scope_while_the_architect_keeps_every_claim(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            lanes = ("implementation_schedule: parallel_lanes_v1\nrole_sequence:\n  - software_architect\n"
                     "  - backend_developer\n  - devops_engineer\n  - code_reviewer\n  - qa_engineer\n"
                     "path_claims:\n  - deploy\n  - src/api\n  - workspace/docs\n"
                     "lane_scopes:\n  - backend_developer:src/api\n  - devops_engineer:deploy\n"
                     "lane_seams:\n")
            item = self.note(root, "workspace/docs/delivery/deliveries/one/items/st-001/item.md",
                             "delivery-item", "backend_developer", lanes)
            self.commit(root)
            kwargs = dict(entry="deliver", mode="create", project=root, inputs=[item])

            def scope(role):
                return task_inputs.manifest(role=role, **kwargs)["write_scope"]

            lane_note = [constraint for constraint in scope("backend-developer")["constraints"]
                         if constraint.startswith("parallel lane:")]
            self.assertEqual(len(lane_note), 1)
            for role, paths in (("backend-developer", ["src/api"]), ("devops-engineer", ["deploy"])):
                with self.subTest(role=role):
                    self.assertEqual(scope(role)["allowed_write_area"], [
                        {"path": path, "coverage": "path_and_descendants", "source": item} for path in paths])
                    self.assertIn(lane_note[0], scope(role)["constraints"])
            # The architect runs alone before the lanes, so its area is the Item's claims.
            architect = scope("software-architect")
            self.assertEqual([area["path"] for area in architect["allowed_write_area"]], ["deploy", "src/api"])
            self.assertNotIn(lane_note[0], architect["constraints"])
            content = (root / item).read_text(encoding="utf-8")
            for broken in (content.replace("backend_developer:src/api", "backend_developer:src\\api"),
                           content.replace("backend_developer:src/api", "backend_developer:elsewhere"),
                           content.replace("parallel_lanes_v1", "parallel_lanes_v2")):
                with self.subTest(broken=broken):
                    (root / item).write_text(broken, encoding="utf-8")
                    self.assertEqual(scope("backend-developer")["status"], "unresolved")
            # Without the schedule every implementation role holds every claim, as before.
            (root / item).write_text(content.replace("implementation_schedule: parallel_lanes_v1\n", ""),
                                     encoding="utf-8")
            self.assertEqual([area["path"] for area in scope("backend-developer")["allowed_write_area"]],
                             ["deploy", "src/api"])

    def test_parallel_lane_instructions_are_bound_only_at_parallel_lanes(self):
        planning = "skill-content/execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md"
        execution = "skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            tasks = (("execution-plan", "software-architect", planning),
                     ("deliver", "backend-developer", execution),
                     ("deliver", "code-reviewer", execution),
                     ("deliver", None, execution))

            def bound(entry, role):
                result = task_inputs.manifest(entry=entry, role=role, mode="review", project=root)
                return set(result["required_reads"]) | {item["path"] for item in result["instructions"]}

            for entry, role, reference in tasks:
                with self.subTest(entry=entry, role=role, policy=False):
                    self.assertFalse({planning, execution} & bound(entry, role))
            docs = root / "workspace/docs"
            def policy(*argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]), 0)
            policy("init")
            policy("set", "--switch", "implementation_schedule", "--value", "parallel_lanes_v1")
            policy("approve")
            for entry, role, reference in tasks:
                with self.subTest(entry=entry, role=role, policy=True):
                    self.assertEqual({planning, execution} & bound(entry, role), {reference})

    def test_unknown_write_scope_remains_empty_with_unresolved_transition_condition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            result = task_inputs.manifest(entry="setup", role="delivery-coordinator", mode="create",
                                          project=root, inputs=["brief.md"])
            self.assertEqual(result["write_scope"]["status"], "unresolved")
            self.assertEqual(result["write_scope"]["allowed_write_area"], [])
            self.assertEqual(next(row["status"] for row in result["next_transition_conditions"]
                                  if row["condition"] == "write_scope"), "unresolved")

    def test_regular_input_rejects_lexical_and_filesystem_aliases(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "note.md").write_text("Note", encoding="utf-8")
            for name in ("./note.md", "folder/../note.md", "folder//note.md", "note.md/", "C:note.md", "note.md."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    task_inputs.regular(root, name)
            try:
                (root / "alias.md").symlink_to(root / "note.md")
            except OSError as exc:
                self.skipTest(str(exc))
            with self.assertRaisesRegex(ValueError, "symlink"):
                task_inputs.regular(root, "alias.md")

    def test_new_incoming_canonical_source_invalidates_but_opaque_interior_does_not(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "solution-design").mkdir(parents=True)
            (docs / "solution-design/landscape.md").write_text("Landscape", encoding="utf-8")
            self.commit(root)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="review", project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            opaque = docs / "experience-design/artifacts/deep/arbitrary.json"
            opaque.parent.mkdir(parents=True)
            opaque.write_bytes(b"\xffopaque unparsed")
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"]), result)
            (docs / "solution-design/incoming.md").write_text("[[business-analysis/new-edge|Incoming]]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def test_base_inventory_includes_dirty_and_untracked_product_sources(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            (root / "brief.md").write_text("Uncommitted change", encoding="utf-8")
            (root / "new.py").write_text("new = True\n", encoding="utf-8")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root, base="HEAD")
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["changed_paths"], ["brief.md", "new.py"])
            (root / "new.py").write_text("new = False\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            subprocess.run(["git", "-C", str(root), "update-index", "--assume-unchanged", "brief.md"], check=True)
            with self.assertRaisesRegex(ValueError, "hidden index"):
                task_inputs.manifest(**kwargs)

    def test_head_drift_during_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            original = subprocess.run
            observations = []
            def moved(argv, **kwargs):
                result = original(argv, **kwargs)
                if argv[-3:] == ["rev-parse", "--verify", "HEAD"]:
                    observations.append(True)
                    if len(observations) > 1:
                        result.stdout = b"0" * 40 + b"\n"
                return result
            with mock.patch.object(task_inputs.subprocess, "run", side_effect=moved):
                with self.assertRaisesRegex(ValueError, "HEAD changed"):
                    task_inputs.manifest(entry="business-analysis", role="business-analyst", mode="review", project=root)

    def test_technology_methods_require_selected_committed_accepted_decisions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            path = root / "workspace/docs/solution-design/decisions/api-decision.md"
            path.parent.mkdir(parents=True)
            text = "---\ntype: decision\nstatus: proposed\nmethod_skills:\n  - python-fastapi\n---\n\n# API\n"
            path.write_text(text, encoding="utf-8")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root,
                          skills=["python-fastapi"], inputs=[path.relative_to(root).as_posix()])
            for value in (text, text.replace("proposed", "accepted")):
                path.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "committed accepted"):
                    task_inputs.manifest(**kwargs)
            self.commit(root)
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["method_bindings"]["python-fastapi"], [path.relative_to(root).as_posix()])
            with self.assertRaisesRegex(ValueError, "internal skills"):
                task_inputs.manifest(entry="deliver", role="code-reviewer", mode="review", skills=["setup"])

    def test_technology_binding_rejects_git_clean_crlf_bytes_that_differ_from_head(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "true"], check=True, capture_output=True)
            relative = "workspace/docs/solution-design/decisions/api-decision.md"
            path = root / relative
            path.parent.mkdir(parents=True)
            committed = b"---\ntype: decision\nstatus: accepted\nmethod_skills:\n  - python-fastapi\n---\n\n# API\n"
            path.write_bytes(committed.replace(b"\n", b"\r\n"))
            self.commit(root)
            self.assertEqual(subprocess.check_output(["git", "-C", str(root), "show", "HEAD:" + relative]), committed)
            self.assertEqual(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", relative]), b"")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root,
                          skills=["python-fastapi"], inputs=[relative])
            with self.assertRaisesRegex(ValueError, "committed accepted"):
                task_inputs.manifest(**kwargs)
            path.write_bytes(committed)
            self.assertEqual(task_inputs.manifest(**kwargs)["method_bindings"], {"python-fastapi": [relative]})

    def test_catalog_covers_current_roles_skills_and_flows(self):
        policy = task_inputs.catalog()
        self.assertEqual(set(policy["role_skills"]), {p.stem for p in (task_inputs.PACKAGE / "agents").glob("*.md")})
        for entry, route in policy["entries"].items():
            for role in route["roles"] or [None]:
                result = task_inputs.manifest(entry=entry, role=role, mode="review")
                self.assertIn("constitution.md", result["required_reads"])
                self.assertFalse(result["approval_authority"])

    def test_method_skills_are_explicit_and_required_checklists_are_full_reads(self):
        result = task_inputs.manifest(entry="deliver", role="code-reviewer", mode="repair", skills=["python-fastapi"])
        self.assertIn("skill-content/code-review/references/passes.md", result["required_reads"])
        self.assertIn("skill-content/python-fastapi/references/review-checklist.md", result["required_reads"])
        backend = task_inputs.manifest(entry="deliver", role="backend-developer", mode="create")
        self.assertNotIn("skill-content/sql-database-design/SKILL.md", backend["required_reads"])
        self.assertNotIn("skill-content/nosql-database-design/SKILL.md", backend["required_reads"])
        qa = task_inputs.manifest(entry="deliver", role="qa-engineer", mode="create")
        self.assertEqual(qa["write_boundary"], "read_only")

    def test_review_panel_protocol_is_bound_only_at_lens_panel(self):
        protocol = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            tasks = (("configure", "devops-engineer", ["challenge-review"]),
                     ("configure", "qa-engineer", ["challenge-review"]),
                     ("backlog-plan", "backlog-reviewer", []),
                     ("backlog-plan", "product-owner", ["challenge-review"]))
            for entry, role, skills in tasks:
                with self.subTest(entry=entry, role=role, policy=False):
                    result = task_inputs.manifest(entry=entry, role=role, mode="review",
                                                  project=root, skills=skills)
                    self.assertIn("skill-content/challenge-review/SKILL.md", result["required_reads"])
                    self.assertIn("skill-content/challenge-review/data/review-panels.json",
                                  [item["path"] for item in result["instructions"]])
                    self.assertNotIn(protocol, [item["path"] for item in result["instructions"]])
            plain = task_inputs.manifest(entry="configure", role="devops-engineer", mode="review")
            self.assertNotIn("skill-content/challenge-review/SKILL.md", plain["required_reads"])
            docs = root / "workspace/docs"
            def policy(*argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]), 0)
            policy("init")
            policy("set", "--switch", "review_panels", "--value", "lens_panel")
            policy("approve")
            for entry, role, skills in tasks:
                with self.subTest(entry=entry, role=role, policy=True):
                    result = task_inputs.manifest(entry=entry, role=role, mode="review",
                                                  project=root, skills=skills)
                    self.assertIn(protocol, result["required_reads"])
                    self.assertEqual(result["write_boundary"], "read_only")

    def test_process_policy_binds_only_the_chosen_switch_references(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            package, project = base / "package", base / "project"
            build_task_package(package, switch_files=True)
            build_task_project(project)
            docs = project / "workspace/docs"
            reader = dict(entry="fixture-entry", role="fixture-reader", mode="review",
                          project=project, package=package)
            plain = task_inputs.manifest(**reader)

            def policy(*argv):
                output = io.StringIO()
                with mock.patch.object(process_policy, "PACKAGE", package), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]),
                                     0, output.getvalue())

            policy("init")
            policy("set", "--switch", "fixture_mode", "--value", "fast")
            with self.assertRaisesRegex(ValueError, "Process Policy revision 1 is a draft"):
                task_inputs.manifest(**reader)
            policy("approve")
            chosen = task_inputs.manifest(**reader)
            # The value's data travels with its references as a required read.
            self.assertEqual(sorted(set(chosen["required_reads"]) - set(plain["required_reads"])),
                             sorted([*SWITCH_FILES, SWITCH_DATA]))
            self.assertEqual(chosen["conditional_reads"], plain["conditional_reads"])
            self.assertIn("workspace/docs/delivery/process-policy.md",
                          [record["path"] for record in chosen["project_inputs"]])
            self.assertTrue({*SWITCH_FILES, SWITCH_DATA}
                            <= {record["path"] for record in chosen["instructions"]})
            self.assertNotIn(SWITCH_DATA, [record["path"] for record in plain["instructions"]])

            policy("begin-revision")
            policy("set", "--switch", "fixture_mode", "--default")
            policy("approve")
            default = task_inputs.manifest(**reader)
            self.assertEqual(default["required_reads"], plain["required_reads"])
            self.assertEqual(default["instructions"], plain["instructions"])
            self.assertEqual([record["path"] for record in default["project_inputs"]],
                             ["workspace/docs/delivery/process-policy.md"])
            (docs / "delivery/process-policy.md").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "process policy cannot bind switch instructions"):
                task_inputs.manifest(**reader, expected_hash=default["source_hash"])

    def test_a_task_inside_a_pinned_delivery_refuses_a_policy_changed_since_the_pin(self):
        import delivery_compile

        lanes = "skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            docs = root / "workspace/docs"
            (docs / "maps").mkdir(parents=True)
            make_approved_backlog(docs)
            self.make_project(root)

            def run(call, *args):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(call(*args), 0)

            def policy(*argv):
                run(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])

            dod = SimpleNamespace(docs=str(docs), title="Project", file=None)
            run(delivery_compile.init_dod, dod)
            run(delivery_compile.approve_dod, dod)
            policy("init")
            policy("approve")
            run(delivery_compile.init_delivery, SimpleNamespace(
                docs=str(docs), id=None, slug="auth", goal="Authenticate", outcome=None,
                target_branch="main", story=["AUTH-01"]))
            run(delivery_compile.approve_scope, SimpleNamespace(docs=str(docs), delivery="DLV-001"))
            self.commit(root)
            item = "workspace/docs/delivery/deliveries/dlv-001-auth/items/auth-01/item.md"
            pinned = process_policy.path_for(docs).read_bytes()

            def bound(**kwargs):
                result = task_inputs.manifest(entry="deliver", role="backend-developer",
                                              mode="create", project=root, **kwargs)
                return set(result["required_reads"])

            inside = ({"inputs": [item]}, {"delivery": "DLV-001"})
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            policy("begin-revision")
            policy("set", "--switch", "implementation_schedule", "--value", "parallel_lanes_v1")
            policy("approve")
            # The Delivery runs under the values it pinned; its tasks never bind a later one.
            drift = ("process policy cannot bind switch instructions: DLV-001: Delivery runs switch"
                     " implementation_schedule at sequential_v1 under its pinned Process Policy"
                     " revision 1, but the approved revision 2 sets parallel_lanes_v1")
            for kwargs in inside:
                with self.subTest(context=kwargs), self.assertRaisesRegex(ValueError, drift):
                    bound(**kwargs)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = task_inputs.main(["--entry", "deliver", "--role", "backend-developer",
                                         "--mode", "create", "--project-root", str(root),
                                         "--delivery", "DLV-001"])
            self.assertEqual(code, 1)
            self.assertIn("Delivery runs switch implementation_schedule at sequential_v1",
                          output.getvalue())
            # A task outside any Delivery follows the project's current policy.
            self.assertIn(lanes, bound())
            # Restoring the pinned policy, or re-pinning through a new execution
            # approval, lets the Delivery's tasks bind again.
            current = process_policy.path_for(docs).read_bytes()
            process_policy.path_for(docs).write_bytes(pinned)
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            # Without a policy every switch is at its default, as the pinned revision sets it.
            process_policy.path_for(docs).unlink()
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            # From the Delivery Review on the Delivery reads the revision it pinned,
            # so a policy set for the next Delivery never changes what its tasks bind.
            process_policy.path_for(docs).write_bytes(current)
            path = docs / "delivery/deliveries/dlv-001-auth/delivery.md"
            props, body = delivery_compile.split_note(path)
            props["status"] = "review"
            delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))
            self.assertNotIn(lanes, bound(inputs=[item]))
            with self.assertRaisesRegex(ValueError, "Delivery not found: DLV-404"):
                bound(delivery="DLV-404")

    def test_no_shipped_manifest_binds_a_switch_reference_without_a_policy(self):
        policy = task_inputs.catalog()
        for entry, route in policy["entries"].items():
            for role in route["roles"] or [None]:
                with self.subTest(entry=entry, role=role):
                    result = task_inputs.manifest(entry=entry, role=role, mode="review")
                    paths = [*result["required_reads"],
                             *(item["path"] for item in result["conditional_reads"]),
                             *(item["path"] for item in result["instructions"])]
                    self.assertFalse([path for path in paths if "/references/switch-" in path])

    def test_changed_project_input_invalidates_manifest_without_runtime_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_repository(root)
            (root / "brief.md").write_text("Original accepted intent.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "brief.md"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "brief"], check=True)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="revise", project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"]), result)
            (root / "brief.md").write_text("Changed intent.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            self.assertFalse((root / ".agentrof").exists())

    def test_catalog_rejects_orphan_role_and_missing_mandatory_reference(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "package"
            shutil.copytree(task_inputs.PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
            (package / "agents/new-role.md").write_text("# Unregistered role\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "every current role"):
                task_inputs.catalog(package)
            (package / "agents/new-role.md").unlink()
            (package / "skill-content/code-review/references/passes.md").unlink()
            with self.assertRaisesRegex(ValueError, "required input is missing"):
                task_inputs.catalog(package)

    def test_invoked_tools_and_selected_method_data_invalidate_without_becoming_reads(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "package"
            shutil.copytree(task_inputs.PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
            kwargs = dict(entry="design-system", role="ux-designer", mode="create", package=package,
                          skills=["ui-ux-design"])
            result = task_inputs.manifest(**kwargs)
            data = "skill-content/ui-ux-design/data/styles.csv"
            self.assertNotIn(data, result["required_reads"])
            self.assertIn(data, {record["path"] for record in result["instructions"]})
            with (package / data).open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            with (package / "scripts/ba_compile.py").open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def test_external_issue_flow_refuses_project_state_and_wrong_role(self):
        result = task_inputs.manifest(entry="issue-report", role=None, mode="create")
        self.assertEqual(result["project_inputs"], [])
        with self.assertRaisesRegex(ValueError, "conversation inputs only"):
            task_inputs.manifest(entry="issue-report", role=None, mode="create", project=ROOT)
        with self.assertRaisesRegex(ValueError, "role does not belong"):
            task_inputs.manifest(entry="business-analysis", role="frontend-developer", mode="create")


class EpicTaskScopeTests(unittest.TestCase):
    """EP-001 holds ST-001; EP-002's writer still has to finish the stubs of ST-002."""

    ROLES = (("backlog-reviewer", "review"), ("product-owner", "revise"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        self.docs = self.root / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        }), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            make_approved_backlog(self.docs, "ST-001")
            backlog_compile.stub_epic(SimpleNamespace(
                docs=str(self.docs), slug="second", id="EP-002", title="Second",
                goal="Deliver a separate customer outcome."))
            backlog_compile.stub_story(SimpleNamespace(
                docs=str(self.docs), epic="second", slug="st-002", id="ST-002", title=None,
                scope=None, work_kind="feature", criterion_ref=[CRITERION],
                experience_ref=[EXPERIENCE], evidence_ref=[], uses_design=[DESIGN],
                constrained_by=[CONSTRAINT]))
        init_repository(self.root)
        subprocess.run(["git", "-C", str(self.root), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        commit_all(self.root)

    def task(self, role, mode, epic="EP-001", **extra):
        return task_inputs.manifest(entry="backlog-plan", role=role, mode=mode,
                                    project=self.root, epic=epic, **extra)

    def story(self, slug):
        epic = "delivery-fixture" if slug == "st-001" else "second"
        return self.docs / f"backlog/epics/{epic}/stories/{slug}"

    def finish_second_story(self):
        """EP-002's writer replaces every placeholder stub-story wrote."""
        folder = self.story("st-002")
        _author_story(folder / "story.md", folder / "test-plan.md", "ST-002")

    def test_another_epics_writer_leaves_an_epic_task_fresh(self):
        tasks = {role: self.task(role, mode) for role, mode in self.ROLES}
        self.assertTrue(tasks["backlog-reviewer"]["backlog_scope"]["check"]["scaffold_findings"])
        # The root package, bare --epic, and an unscoped writer keep every source.
        whole = {"root": dict(role="product-owner", mode="revise", epic=""),
                 "unscoped": dict(role="product-owner", mode="revise", epic=None)}
        previous = {name: self.task(**kwargs) for name, kwargs in whole.items()}
        self.finish_second_story()
        aside = self.docs / "solution-design/aside.md"
        aside.write_text("---\ntype: note\n---\n\n# A source no closure reads\n", encoding="utf-8")
        outside = {self.story("st-002").relative_to(self.root).as_posix() + name
                   for name in ("/story.md", "/test-plan.md")} | {"workspace/docs/solution-design/aside.md"}
        for role, mode in self.ROLES:
            with self.subTest(role=role):
                fresh = self.task(role, mode, expected_hash=tasks[role]["source_hash"])
                closure = {"workspace/docs/" + path for path in fresh["backlog_scope"]["paths"]}
                self.assertLessEqual({record["path"] for record in fresh["canonical_source_inventory"]},
                                     closure)
                self.assertFalse(outside & {record["path"] for record in fresh["working_inputs"]})
                self.assertFalse(outside & set(fresh["changed_paths"]))
                self.assertFalse(fresh["backlog_scope"]["check"].get("scaffold_findings"))
        for name, kwargs in whole.items():
            with self.subTest(scope=name):
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(**kwargs, expected_hash=previous[name]["source_hash"])
                self.assertIn("workspace/docs/solution-design/aside.md", {
                    record["path"] for record in self.task(**kwargs)["canonical_source_inventory"]})

    def test_a_change_inside_an_epic_tasks_closure_still_makes_it_stale(self):
        self.finish_second_story()
        commit_all(self.root)
        first = self.story("st-001") / "story.md"
        for role, mode in self.ROLES:
            with self.subTest(role=role, change="inside"):
                previous = self.task(role, mode)
                first.write_text(first.read_text(encoding="utf-8").replace(
                    "Administrative bulk operations", "Administrative batch operations"),
                    encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(role, mode, expected_hash=previous["source_hash"])
                subprocess.run(["git", "-C", str(self.root), "checkout", "--", "."],
                               check=True, capture_output=True)
        # ST-002 of EP-002 gains a dependency on ST-001: a new incoming edge
        # brings ST-002 into EP-001's closure, so every EP-001 task goes stale.
        previous = {role: self.task(role, mode) for role, mode in self.ROLES}
        path = self.story("st-002") / "story.md"
        props, body = backlog_compile.parse_front_matter(path)
        link = "[[backlog/epics/delivery-fixture/stories/st-001/story|ST-001]]"
        props["depends_on"] = [link]
        body = body.replace("## Dependencies\n\nNone.",
                            "## Dependencies\n\n- " + link + ": Supplies the account boundary.")
        path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        for role, mode in self.ROLES:
            with self.subTest(role=role, change="incoming edge"):
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(role, mode, expected_hash=previous[role]["source_hash"])
                current = self.task(role, mode)
                self.assertIn(path.relative_to(self.root).as_posix(),
                              {record["path"] for record in current["canonical_source_inventory"]})


class BuiltPackageTaskInputTests(unittest.TestCase):
    """A built host package derives tasks with its own scripts and agents."""

    def test_every_built_package_checks_its_catalog_and_derives_a_role_task(self):
        # The catalog reads the switch registry without importing its compiler.
        self.assertEqual(task_inputs.SWITCH_REGISTRY, process_policy.REGISTRY)
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import build_distributions as builder
        finally:
            sys.path.remove(str(ROOT / "tools"))
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "dist"
            builder.build(ROOT, output)
            for host in builder.HOSTS:
                package = output / host / "software-engineering-team"
                # The build writes each switch's agent variants beside their base agents.
                self.assertTrue((package / "agents/product-owner-mechanical.md").is_file())
                self.assertTrue((package / "agents/backlog-reviewer-lens.md").is_file())
                for argv in (["--check-catalog"],
                             ["--entry", "backlog-plan", "--role", "backlog-reviewer",
                              "--mode", "review"]):
                    with self.subTest(host=host, argv=argv):
                        result = subprocess.run(
                            [sys.executable, str(package / "scripts/task_inputs.py"), *argv],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotEqual(json.loads(result.stdout).get("ok"), False)
            # A variant is never a task role: its task is derived as its base role.
            script = output / "claude/software-engineering-team/scripts/task_inputs.py"
            refused = subprocess.run(
                [sys.executable, str(script), "--entry", "backlog-plan",
                 "--role", "product-owner-mechanical", "--mode", "revise"],
                capture_output=True, text=True, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(refused.returncode, 1)
            self.assertIn("derive its task as product-owner", json.loads(refused.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
