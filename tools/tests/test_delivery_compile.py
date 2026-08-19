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
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import delivery_compile  # noqa: E402
import operation_compile  # noqa: E402
import stage_package  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402


class DeliveryCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace" / "config.json").write_text(
            json.dumps({"doc_type_designations": {}}), encoding="utf-8"
        )
        make_approved_backlog(self.docs)

    def tearDown(self):
        self.temporary.cleanup()

    def approve_dod(self):
        args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        self.assertEqual(delivery_compile.approve_dod(args), 0)

    def approve_verification_contract(self):
        """Create the smallest current Solution and Operation handoff chain."""
        decisions = self.docs / "solution-design" / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        decision = decisions / "fixture-api.md"
        ref = "solution-design/decisions/fixture-api"
        if not decision.exists():
            decision = decisions / "api.md"
            ref = "solution-design/decisions/api"
            decision.write_text(
                "---\n"
                "type: decision\nstatus: accepted\n"
                "decision_kind: technology-selection\n"
                "applies_to:\n  - api\n"
                "selected_technology: python-fastapi\n"
                "method_skills:\n  - python-fastapi\n"
                "---\n\n# API\n",
                encoding="utf-8",
            )
            landscape = self.docs / "solution-design" / "landscape.md"
            landscape.write_text("---\ntype: landscape\nstatus: approved\npackage_status: draft\n---\n\n# Landscape\n", encoding="utf-8")
            digest = stage_package.tree_hash(
                self.docs / "solution-design",
                {"package_hash", "package_status", "package_approved_at_utc"},
            )
            landscape.write_text(
                f"---\ntype: landscape\nstatus: approved\npackage_status: approved\npackage_hash: {digest}\n---\n\n# Landscape\n",
                encoding="utf-8",
            )
        args = type("Args", (), {
            "docs": str(self.docs), "kind": "verification", "constrained_by": [ref],
        })
        self.assertEqual(operation_compile.init(args), 0)
        path = self.docs / "operation" / "verification-contract.md"
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(operation_compile.approve(args), 0)

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
        self.approve_verification_contract()
        dod_args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod_args)
        delivery_compile.approve_dod(dod_args)
        init_args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": None,
                                       "goal": "SAML authentication", "outcome": "Users sign in",
                                       "target_branch": "main", "story": ["AUTH-01"],
                                       })
        self.assertEqual(delivery_compile.init_delivery(init_args), 0)
        plan_args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(plan_args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-saml-authentication"
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        self.assertTrue((root / "execution-plan.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "code-review.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "verification.md").exists())
        self.assertEqual(delivery_compile.check_delivery(plan_args), 0)

    def test_no_timebox_or_runtime_coordination_fields_are_generated(self):
        self.approve_dod()
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "small-change",
                                  "goal": "Small change", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        delivery_compile.init_delivery(args)
        props, _ = delivery_compile.split_note(
            self.docs / "delivery" / "deliveries" / "dlv-001-small-change" / "delivery.md"
        )
        for forbidden in ("duration", "estimate", "velocity", "slot", "worktree", "assignee"):
            self.assertNotIn(forbidden, props)

    def test_scope_resolves_exact_approved_story_and_test_plan_hashes(self):
        self.approve_dod()
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-auth"
        item_props, _ = delivery_compile.split_note(root / "items" / "auth-01" / "item.md")
        source_story = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        source_test = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/test-plan.md"
        import backlog_compile
        self.assertEqual(item_props["story_source_hash"], backlog_compile.digest(source_story))
        self.assertEqual(item_props["test_plan_source_hash"], backlog_compile.digest(source_test))
        self.assertNotEqual(item_props["story_source_hash"], "pending")
        source_story.write_text(source_story.read_text(encoding="utf-8") + "\nChanged after scope proposal.\n", encoding="utf-8")
        scope = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 1)

    def test_review_rejects_non_git_or_missing_review_baselines(self):
        self.approve_dod()
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                   "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                   "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        review = type("Args", (), {
            "docs": str(self.docs), "delivery": "DLV-001",
            "reviewed_commit": "not-a-git-oid", "reviewed_integration_commit": "none",
        })
        self.assertEqual(delivery_compile.approve_review(review), 2)

    def test_scope_rejects_unknown_story_and_execution_rejects_unclaimed_topology(self):
        self.approve_dod()
        unknown = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "unknown",
                                     "goal": "Unknown", "outcome": None, "target_branch": "main",
                                     "story": ["UNKNOWN-01"]})
        self.assertEqual(delivery_compile.init_delivery(unknown), 2)
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        scope = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        self.assertEqual(delivery_compile.approve_execution(scope), 1)


if __name__ == "__main__":
    unittest.main()
