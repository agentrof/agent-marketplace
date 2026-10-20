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
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import process_policy
import task_inputs
from git_fixture import init_repository, remove_temporary


def commit_all(root):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"], check=True, capture_output=True)


def instruction_paths(project, entry, role, skills=None, catalog=None):
    """The required reads and hashed instructions of a task, as task_inputs.manifest derives them
    from the project's Process Policy, without the Git reads of a full manifest."""
    catalog = catalog or task_inputs.catalog()
    route = catalog["entries"][entry]
    chosen, _policy_inputs = task_inputs.switch_choices(project, route, task_inputs.PACKAGE)
    _registry, _chosen, required, _conditional, hashed = task_inputs.instruction_reads(
        catalog, task_inputs.PACKAGE, route, role,
        task_inputs.task_skills(catalog, entry, role, skills, route), chosen)
    return required, hashed


def task_scope(project, entry, role, mode, inputs=(), closure=None, catalog=None, chosen=frozenset(),
               remote="origin"):
    """The write scope task_inputs.manifest derives for a task, without its Git reads."""
    catalog = catalog or task_inputs.catalog()
    paths = set(inputs) | ({"workspace/docs/" + path for path in closure["paths"]} if closure else set())
    return task_inputs.write_scope(project, paths, role, catalog["entries"][entry],
                                   task_inputs.read_only_task(catalog, entry, role, mode), closure,
                                   task_inputs.PACKAGE, chosen=chosen, remote=remote)


class TaskInputTests(unittest.TestCase):
    @integration
    def test_setup_supports_unborn_repository_but_binds_initial_files_and_commit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            init_repository(root)
            (root / "README.md").write_text("New project\n", encoding="utf-8")
            kwargs = dict(entry="setup", role=None, mode="create", project=root)
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

    @integration
    def test_unknown_write_scope_remains_empty_with_unresolved_transition_condition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            result = task_inputs.manifest(entry="setup", role=None, mode="create",
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

    @integration
    def test_new_incoming_canonical_source_invalidates_but_opaque_interior_does_not(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "solution-design").mkdir(parents=True)
            (docs / "solution-design/landscape.md").write_text("Landscape", encoding="utf-8")
            self.commit(root)
            kwargs = dict(entry="deliver", role="business-analyst", mode="review", project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            opaque = docs / "experience-design/artifacts/deep/arbitrary.json"
            opaque.parent.mkdir(parents=True)
            opaque.write_bytes(b"\xffopaque unparsed")
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"]), result)
            (docs / "solution-design/incoming.md").write_text("[[business-analysis/new-edge|Incoming]]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    @integration
    def test_an_input_task_binds_its_inputs_and_cited_notes_and_only_membership_of_the_rest(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "operation").mkdir(parents=True)
            (docs / "operation/cited.md").write_text("Cited", encoding="utf-8")
            (docs / "operation/unrelated.md").write_text("Unrelated", encoding="utf-8")
            (root / "brief.md").write_text("Reads [[operation/cited|Cited]].\n", encoding="utf-8")
            self.commit(root)
            kwargs = dict(entry="deliver", role="business-analyst", mode="review",
                          project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["context_membership"], {"count": 2, "source_hash":
                             task_inputs.digest(task_inputs.source_paths(root))})
            self.assertNotIn("canonical_source_paths", result)
            self.assertEqual({record["path"] for record in result["canonical_source_inventory"]},
                             {"workspace/docs/operation/cited.md"})
            # Another writer's note and a commit leave the task fresh.
            (docs / "operation/unrelated.md").write_text("Changed elsewhere", encoding="utf-8")
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])["source_hash"],
                             result["source_hash"])
            self.commit(root)
            task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            # A cited note and a new source still invalidate it.
            (docs / "operation/cited.md").write_text("Cited, changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            (docs / "operation/new.md").write_text("New", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    @integration
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

    @integration
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
                    task_inputs.manifest(entry="deliver", role="business-analyst", mode="review", project=root)

    @integration
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

    @integration
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
        security = task_inputs.manifest(entry="deliver", role="security-reviewer", mode="create")
        self.assertEqual(security["write_boundary"], "read_only")

    @integration
    def test_changed_project_input_invalidates_manifest_without_runtime_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_repository(root)
            (root / "brief.md").write_text("Original accepted intent.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "brief.md"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "brief"], check=True)
            kwargs = dict(entry="deliver", role="business-analyst", mode="revise", project=root, inputs=["brief.md"])
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
            kwargs = dict(entry="deliver", role="frontend-developer", mode="create", package=package,
                          skills=["ui-ux-design"])
            result = task_inputs.manifest(**kwargs)
            data = "skill-content/ui-ux-design/references/recipes.md"
            self.assertNotIn(data, result["required_reads"])
            self.assertIn(data, {record["path"] for record in result["instructions"]})
            with (package / data).open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            with (package / "scripts/task_lane.py").open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def test_external_issue_flow_refuses_project_state_and_wrong_role(self):
        result = task_inputs.manifest(entry="issue-report", role=None, mode="create")
        self.assertEqual(result["project_inputs"], [])
        with self.assertRaisesRegex(ValueError, "conversation inputs only"):
            task_inputs.manifest(entry="issue-report", role=None, mode="create", project=ROOT)
        with self.assertRaisesRegex(ValueError, "role does not belong"):
            task_inputs.manifest(entry="configure", role="frontend-developer", mode="create")


@integration
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
                for argv in (["--check-catalog"],
                             ["--entry", "deliver", "--role", "code-reviewer", "--mode", "review"]):
                    with self.subTest(host=host, argv=argv):
                        result = subprocess.run(
                            [sys.executable, str(package / "scripts/task_inputs.py"), *argv],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotEqual(json.loads(result.stdout).get("ok"), False)


if __name__ == "__main__":
    unittest.main()
