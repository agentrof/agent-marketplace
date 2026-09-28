"""Derived task inputs preserve full reads, scoped sources and role boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import task_inputs
from git_fixture import init_repository


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
        (root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        self.commit(root)

    def commit(self, root):
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"], check=True, capture_output=True)

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
                       "paths": [row[0] for row in rows], "review": {"path": rows[4][0]}}
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


if __name__ == "__main__":
    unittest.main()
