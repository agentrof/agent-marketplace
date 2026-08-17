"""Gate 3 tests for the offline Delivery knowledge model."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import delivery_compile  # noqa: E402


class DeliveryCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace" / "config.json").write_text(
            json.dumps({"doc_type_designations": {}}), encoding="utf-8"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_dod_bootstrap_approval_and_revision_keep_one_path(self):
        args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        self.assertEqual(delivery_compile.check_dod_cmd(args), 0)
        self.assertEqual(delivery_compile.approve_dod(args), 0)
        path = self.docs / "delivery" / "definition-of-done.md"
        before = delivery_compile.split_note(path)[0]["source_hash"]
        self.assertEqual(delivery_compile.begin_dod_revision(args), 0)
        props, _ = delivery_compile.split_note(path)
        self.assertEqual(props["status"], "draft")
        self.assertEqual(props["revision"], 2)
        self.assertNotEqual(props.get("source_hash"), before)

    def test_scope_then_execution_creates_exact_item_evidence_files(self):
        dod_args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod_args)
        delivery_compile.approve_dod(dod_args)
        init_args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": None,
                                       "goal": "SAML authentication", "outcome": "Users sign in",
                                       "target_branch": "main", "story": ["AUTH-01"],
                                       "story_source_hash": "sha256:story", "test_plan_source_hash": "sha256:test"})
        self.assertEqual(delivery_compile.init_delivery(init_args), 0)
        plan_args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(plan_args), 0)
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-saml-authentication"
        self.assertTrue((root / "execution-plan.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "code-review.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "verification.md").exists())
        self.assertEqual(delivery_compile.check_delivery(plan_args), 0)

    def test_no_timebox_or_runtime_coordination_fields_are_generated(self):
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "small-change",
                                  "goal": "Small change", "outcome": None, "target_branch": "main",
                                  "story": ["ST-001"], "story_source_hash": None,
                                  "test_plan_source_hash": None})
        delivery_compile.init_delivery(args)
        props, _ = delivery_compile.split_note(
            self.docs / "delivery" / "deliveries" / "dlv-001-small-change" / "delivery.md"
        )
        for forbidden in ("duration", "estimate", "velocity", "slot", "worktree", "assignee"):
            self.assertNotIn(forbidden, props)


if __name__ == "__main__":
    unittest.main()
