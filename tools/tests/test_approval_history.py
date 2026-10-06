"""Approval events, not later rendering commits, define the change baseline."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import task_inputs  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402


@integration
class ApprovalHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        init_repository(self.project)
        for scope in ("alpha", "beta"):
            self.write(scope, 1)
        self.initial = self.commit()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
        return self.git("rev-parse", "HEAD")

    def write(self, scope, revision):
        path = self.project / f"workspace/docs/business-analysis/{scope}/space.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: space\npackage_status: approved\nrevision: {revision}\n"
                        f"package_hash: receipt-{revision}\n---\n# Example\n")
        return path

    def base(self, *scopes):
        return task_inputs.approval_base(self.project, "business-analysis", inputs=[
            f"workspace/docs/business-analysis/{scope}/domains/example/entity.md" for scope in scopes])

    def test_rendered_navigation_keeps_the_original_approval_event(self):
        path = self.project / "workspace/docs/business-analysis/alpha/space.md"
        path.write_text(path.read_text() + "\nRendered navigation changed.\n")
        self.commit()
        self.assertEqual(self.base("alpha"), self.initial)

    def test_scopes_use_their_own_approval_and_union_uses_the_older_baseline(self):
        self.write("alpha", 2)
        newer = self.commit()
        self.assertEqual(self.base("alpha"), newer)
        self.assertEqual(self.base("beta"), self.initial)
        self.assertEqual(self.base("alpha", "beta"), self.initial)

    def test_unknown_or_unapproved_scope_does_not_fall_back_to_head(self):
        self.assertIsNone(self.base("missing"))
        files = {"workspace/docs/business-analysis/alpha/space.md"}
        actual, scope = task_inputs.closure_reads(self.project, files, files, None, None, [])
        self.assertEqual(actual, files)
        self.assertEqual(scope["read"], "full")
        self.assertIn("no proven approval", scope["reason"])


class ApprovalRuleTests(unittest.TestCase):
    def test_every_document_workflow_has_an_anchor_policy(self):
        policy = json.loads((task_inputs.PACKAGE / task_inputs.POLICY).read_text())
        self.assertTrue({"requirement", "business_analysis", "solution_design", "design_system",
                         "experience_design", "backlog", "delivery", "operation"}
                        <= set(policy["approval_anchors"]))

    def test_only_approved_changed_receipts_are_events(self):
        anchor = {"field": "status", "value": "approved"}
        approved = {"status": "approved", "revision": 1, "source_hash": "same"}
        self.assertTrue(task_inputs.approval_event(approved, {}, anchor))
        self.assertFalse(task_inputs.approval_event(approved, dict(approved, title="old"), anchor))
        for field in ("revision", "source_hash", "package_hash", "approved_at_utc",
                      "package_approved_at_utc", "approval_revision", "scope_hash", "plan_hash"):
            with self.subTest(field=field):
                self.assertTrue(task_inputs.approval_event(dict(approved, **{field: "new"}), approved, anchor))
        self.assertFalse(task_inputs.approval_event(dict(approved, status="draft"), approved, anchor))
        wildcard = {"field": "scope_hash", "value": "*"}
        self.assertFalse(task_inputs.approval_event({"scope_hash": ""}, {}, wildcard))
        self.assertTrue(task_inputs.approval_event({"scope_hash": "receipt"}, {}, wildcard))

    def test_missing_history_preserves_all_inputs_without_git(self):
        with tempfile.TemporaryDirectory() as raw:
            paths = {"workspace/docs/backlog/story.md"}
            actual, scope = task_inputs.closure_reads(Path(raw), paths, paths, None, None, [])
            self.assertEqual(actual, paths)
            self.assertEqual(scope["read"], "full")
            self.assertEqual(scope["reason"], "no proven approval baseline")



if __name__ == "__main__":
    unittest.main()
