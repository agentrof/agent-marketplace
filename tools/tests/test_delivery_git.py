"""Gate 4 naming and read-only Git preflight tests."""

from __future__ import annotations

import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
import delivery_git  # noqa: E402
import delivery_compile  # noqa: E402


class DeliveryGitTests(unittest.TestCase):
    def test_refs_are_deterministic_and_slug_free(self):
        refs = delivery_git.short_refs("DLV-001", "AUTH-01", 1)
        self.assertEqual(refs, {
            "fence": "agentrof/fence",
            "integration": "agentrof/deliveries/dlv-001",
            "item": "agentrof/items/auth-01",
            "slot": "agentrof/slots/001",
        })

    def test_no_delivery_slug_or_story_title_enters_ref(self):
        self.assertEqual(
            delivery_git.short_refs("DLV-1042", "PAYMENT-204")["integration"],
            "agentrof/deliveries/dlv-1042",
        )
        self.assertEqual(
            delivery_git.short_refs("DLV-1042", "PAYMENT-204")["item"],
            "agentrof/items/payment-204",
        )

    def test_invalid_zero_slot_and_noninjective_story_are_rejected(self):
        with self.assertRaises(ValueError):
            delivery_git.short_refs("DLV-001", "AUTH-01", 0)
        with self.assertRaises(ValueError):
            delivery_git.short_refs("DLV-001", "AUTH/01")

    def test_worktree_paths_have_no_branch_or_worktree_for_fence_slot(self):
        paths = delivery_git.worktree_paths(Path("/project"), "DLV-001", "AUTH-01")
        self.assertEqual(str(paths["integration"]), "/project/.agentrof/agent-marketplace/.runtime/worktrees/dlv-001/integration")
        self.assertEqual(str(paths["item"]), "/project/.agentrof/agent-marketplace/.runtime/worktrees/dlv-001/items/auth-01")

    def test_writer_receipt_is_exact_and_same_candidate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = "a" * 40
            epoch = delivery_git.epoch_token()
            first = delivery_git.create_writer_receipt(
                root, "DLV-001", "AUTH-01", "001", epoch,
                "refs/heads/agentrof/items/auth-01",
                "refs/heads/agentrof/slots/001", candidate,
            )
            second = delivery_git.create_writer_receipt(
                root, "DLV-001", "AUTH-01", "001", epoch,
                "refs/heads/agentrof/items/auth-01",
                "refs/heads/agentrof/slots/001", candidate,
            )
            self.assertEqual(first, second)
            self.assertEqual(first["state"], "pending")
            promoted = delivery_git.promote_writer_receipt(root, "DLV-001", "AUTH-01", candidate)
            self.assertEqual(promoted["state"], "verified")
            self.assertEqual(delivery_git.read_writer_receipt(root, "DLV-001", "AUTH-01"), promoted)
            with self.assertRaises(RuntimeError):
                delivery_git.create_writer_receipt(
                    root, "DLV-001", "AUTH-01", "001", delivery_git.epoch_token(),
                    "refs/heads/agentrof/items/auth-01",
                    "refs/heads/agentrof/slots/001", "b" * 40,
                )

    def test_ref_free_reservation_pushes_fence_and_integration_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"
            (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"max_parallel": 1, "doc_type_designations": {}}), encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            args = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(args); delivery_compile.approve_dod(args)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None,
                                      "goal": "SAML authentication", "outcome": None, "target_branch": "main",
                                      "story": ["AUTH-01"], "story_source_hash": "sha256:story", "test_plan_source_hash": "sha256:test"})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
            delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            result = delivery_git.reserve_delivery(project, "DLV-001")
            self.assertTrue(result["ok"])
            refs = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=True, text=True, capture_output=True).stdout
            self.assertIn("refs/heads/agentrof/fence", refs)
            self.assertIn("refs/heads/agentrof/deliveries/dlv-001", refs)
            with self.assertRaises(RuntimeError):
                delivery_git.reserve_delivery(project, "DLV-001")

    def test_execution_publication_claim_and_start_use_global_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"; (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"max_parallel": 1, "doc_type_designations": {}}), encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "."], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"; subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True); subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication", "outcome": None, "target_branch": "main", "story": ["AUTH-01"], "story_source_hash": "sha256:story", "test_plan_source_hash": "sha256:test"})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.reserve_delivery(project, "DLV-001")
            delivery_compile.approve_execution(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "plan"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.publish_execution_plan(project, "DLV-001")
            result = delivery_git.claim_items(project, "DLV-001")
            self.assertEqual(result["claims"], ["AUTH-01"])
            activation = delivery_git.start_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(activation["slot"], "001")
            self.assertEqual(activation["receipt"]["state"], "verified")
            self.assertTrue(Path(activation["worktree"]).is_dir())
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", activation["worktree"], "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip(),
                activation["item"],
            )
            transition = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "story": "AUTH-01", "to": "active"})
            delivery_compile.prepare_item_transition(transition)
            evidence = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "story": "AUTH-01", "reviewed_commit": "r1", "verified_commit": "r1"})
            delivery_compile.approve_item_evidence(evidence)
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
            integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            self.assertTrue(integrated["ok"])
            refs = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=True, text=True, capture_output=True).stdout
            self.assertIn("refs/heads/agentrof/items/auth-01", refs)
            self.assertNotIn("refs/heads/agentrof/slots/001", refs)


if __name__ == "__main__":
    unittest.main()
