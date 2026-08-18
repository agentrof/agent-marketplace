"""Gate 4 naming and read-only Git preflight tests."""

from __future__ import annotations

import sys
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_git  # noqa: E402
import delivery_compile  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402


class DeliveryGitTests(unittest.TestCase):
    def author_execution_topology(self, docs: Path, delivery: str = "DLV-001") -> None:
        root = delivery_compile.find_delivery(docs, delivery)
        self.assertIsNotNone(root)
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

    def commit_item_product_change(self, worktree: str, content: str) -> str:
        path = Path(worktree) / "src" / "auth.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", worktree, "add", "src/auth.py"], check=True)
        subprocess.run(["git", "-C", worktree, "commit", "-qm", "Implement authentication"], check=True)
        return subprocess.run(
            ["git", "-C", worktree, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def approve_item_evidence(self, worktree: str, delivery: str = "DLV-001",
                              story: str = "AUTH-01") -> int:
        args = type("Args", (), {
            "docs": ".",
            "worktree": worktree,
            "delivery": delivery,
            "story": story,
        })
        return delivery_compile.approve_item_evidence(args)

    def make_project(self):
        temporary = tempfile.TemporaryDirectory()
        project = Path(temporary.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        (project / "workspace" / "docs").mkdir(parents=True)
        (project / "workspace" / "config.json").write_text(
            json.dumps({"team_id": "software-engineering-team", "doc_type_designations": {}}),
            encoding="utf-8",
        )
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
        remote = project / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
        subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        return temporary, project

    def prepare_pr_intent(self):
        """Build one real remote Delivery through its durable PR intent."""
        temporary, project = self.make_project()
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True)
        config_path = project / "workspace" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["max_parallel"] = 1
        config_path.write_text(json.dumps(config), encoding="utf-8")
        make_approved_backlog(docs)
        subprocess.run(["git", "-C", str(project), "add", "workspace"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "approved backlog"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)

        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {
            "docs": str(docs), "id": None, "slug": None,
            "goal": "SAML authentication", "outcome": None,
            "target_branch": "main", "story": ["AUTH-01"],
        })
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        delivery_git.reserve_delivery(project, "DLV-001")
        self.author_execution_topology(docs)
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "execution plan"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.refresh_target(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        product_tip = self.commit_item_product_change(
            active["worktree"], "def authenticate():\n    return 'v1'\n",
        )
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        review = type("Args", (), {
            "docs": str(docs), "delivery": "DLV-001",
            "reviewed_commit": integrated["integration"],
            "reviewed_integration_commit": integrated["integration"],
        })
        self.assertEqual(delivery_compile.approve_review(review), 0)
        delivery_git.publish_delivery_review(project, "DLV-001")
        intent = delivery_git.prepare_pr_creation(project, "DLV-001")
        return temporary, project, docs, product_tip, intent

    @staticmethod
    def fake_provider_type(state: dict):
        """Provider double that performs the final merge on the bare test remote."""
        class FakeProvider:
            def __init__(self, root: Path, remote: str = "origin"):
                self.root = root
                self.remote = remote
                self.repository = "agentrof/example"

            def _head(self) -> str:
                return delivery_git.remote_oid(
                    self.root, self.remote,
                    delivery_git.canonical_refs("DLV-001")["integration"],
                )

            def _record(self, head: str, base: str) -> dict:
                return {
                    "number": 17,
                    "url": "https://github.com/agentrof/example/pull/17",
                    "state": "MERGED" if state.get("merged") else "OPEN",
                    "isDraft": state.get("draft", True),
                    "headRefName": head,
                    "headRefOid": self._head(),
                    "baseRefName": base,
                    "mergeCommit": {"oid": state["merge"]} if state.get("merged") else None,
                    "statusCheckRollup": [{"name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"}],
                }

            def exact_unmerged(self, head: str, base: str) -> list[dict]:
                return [self._record(head, base)] if state.get("created") and not state.get("merged") else []

            def list_pull_requests(self, head: str, base: str) -> list[dict]:
                return [self._record(head, base)] if state.get("created") else []

            def create_draft(self, head: str, base: str, title: str, body: str) -> dict:
                state["created"] = True
                state["draft"] = True
                state["title"] = title
                state["body"] = body
                return {"url": "https://github.com/agentrof/example/pull/17"}

            def ensure_draft(self, url: str) -> dict:
                state["draft"] = True
                return {"url": url, "draft": True}

            def make_ready(self, url: str) -> dict:
                state["draft"] = False
                return {"url": url, "draft": False}

            def inspect_pull_request(self, url: str) -> dict:
                return self._record("agentrof/deliveries/dlv-001", "main")

            def require_green_checks(self, pull_request: dict) -> None:
                if not pull_request.get("statusCheckRollup"):
                    raise AssertionError("green checks are required")

            def merge_commit(self, url: str, head_oid: str) -> dict:
                target_ref = "refs/heads/main"
                target = delivery_git.remote_oid(self.root, self.remote, target_ref)
                merge = delivery_git.merge_candidate(
                    self.root, target, head_oid, "Merge Delivery PR", {},
                )
                delivery_git.atomic_push(self.root, self.remote, [(target_ref, target, merge)])
                state["merged"] = True
                state["merge"] = merge
                return {"url": url, "head": head_oid, "merge_commit": merge}

        return FakeProvider

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

    def test_target_impact_hash_is_order_invariant_and_closed(self):
        items = {
            "AUTH-02": {"action": "replan", "contracts": [], "descendants": [], "merge": "clean", "paths": [], "phase": "unintegrated"},
            "AUTH-01": {"action": "reopen", "contracts": ["api:v1"], "descendants": ["AUTH-02"], "merge": "textual_conflict", "paths": ["src/auth.py"], "phase": "integrated"},
        }
        reversed_items = {"AUTH-01": items["AUTH-01"], "AUTH-02": items["AUTH-02"]}
        first = delivery_git.target_impact_hash("DLV-001", "1" * 40, "2" * 40, items, "sha256:" + "a" * 64, "none")
        second = delivery_git.target_impact_hash("DLV-001", "1" * 40, "2" * 40, reversed_items, "sha256:" + "a" * 64, "none")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")

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

    def test_source_handoff_intent_is_durable_and_abort_after_intent_is_blocked(self):
        temporary, project = self.make_project()
        try:
            acquired = delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            self.assertEqual(acquired["mode"], "source_handoff")
            head = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "branch", "handoff-source", head], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            "handoff-source:refs/heads/handoff-source"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-source", "direct",
                head, head, "upstream",
            )
            self.assertEqual(authorized["target_update_intent"], "sha256:" + "b" * 64)
            self.assertEqual(authorized["receipt"]["state"], "prepared")
            self.assertEqual(
                delivery_git.mark_target_call_started(project, "source_handoff",
                                                      authorized["attempt"])["state"],
                "call_started",
            )
            with self.assertRaises(RuntimeError):
                delivery_git.abort_source_handoff(project)
            applied = delivery_git.apply_target_update(project, "source_handoff")
            self.assertEqual(applied["receipt"]["state"], "verified")
            finished = delivery_git.finish_source_handoff(project)
            self.assertEqual(finished["mode"], "open")
            _ref, fence_oid, values = delivery_git._fence_context(project, "origin")
            self.assertEqual(values["Mode"], "open")
            self.assertEqual(values["Target-Update-Intent"], "none")
        finally:
            temporary.cleanup()

    def test_target_reauthorization_is_fail_closed_without_zero_effect_proof(self):
        with self.assertRaises(RuntimeError):
            delivery_git.reauthorize_target_update(Path("/tmp"))

    def test_prepared_target_update_reauthorizes_atomically_after_target_drift(self):
        temporary, project = self.make_project()
        try:
            acquired = delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            head = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "branch", "handoff-carrier", head], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            "handoff-carrier:refs/heads/handoff-carrier"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-carrier", "direct",
                head, head, "upstream",
            )
            (project / "target-drift.txt").write_text("target moved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "target-drift.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "advance target"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin", "main"], check=True)
            reauthorized = delivery_git.reauthorize_target_update(project, "source_handoff", "none", "origin")
            self.assertNotEqual(reauthorized["attempt"], authorized["attempt"])
            self.assertEqual(reauthorized["receipt"]["state"], "prepared")
            applied = delivery_git.apply_target_update(project, "source_handoff")
            self.assertEqual(applied["receipt"]["state"], "verified")
            self.assertEqual(delivery_git.finish_source_handoff(project)["mode"], "open")
        finally:
            temporary.cleanup()

    def test_direct_target_response_loss_recovers_when_target_equals_candidate(self):
        temporary, project = self.make_project()
        try:
            delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            base = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "switch", "-q", "-c", "handoff-response-loss"], check=True)
            (project / "response-loss.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "response-loss.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "target candidate"], check=True)
            candidate = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            f"{candidate}:refs/heads/handoff-response-loss"], check=True)
            subprocess.run(["git", "-C", str(project), "switch", "-q", "main"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-response-loss", "direct",
                candidate, base, "upstream",
            )
            delivery_git.mark_target_call_started(
                project, "source_handoff", authorized["attempt"],
            )
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            f"{candidate}:refs/heads/main"], check=True)
            recovered = delivery_git.apply_target_update(project, "source_handoff")
            self.assertTrue(recovered["recovered"])
            self.assertEqual(recovered["target_oid"], candidate)
            self.assertEqual(recovered["receipt"]["state"], "verified")
            self.assertEqual(delivery_git.finish_source_handoff(project)["mode"], "open")
        finally:
            temporary.cleanup()

    def test_open_and_merge_pr_use_the_exact_reviewed_integration_head(self):
        temporary, project, _docs, product_tip, intent = self.prepare_pr_intent()
        try:
            state: dict = {}
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                opened = delivery_git.open_pr(project, "DLV-001")
                self.assertTrue(opened["provider_call"])
                self.assertEqual(opened["pull_request_url"], "https://github.com/agentrof/example/pull/17")
                self.assertEqual(state["title"], "SAML authentication")
                merged = delivery_git.merge_pr(project, "DLV-001")
            self.assertEqual(intent["provider"], "github")
            self.assertTrue(state["body"].strip())
            self.assertEqual(merged["status"], "merged")
            self.assertTrue(delivery_git.is_ancestor(project, product_tip, merged["target_after"]))
            parents = delivery_git.run_git(project, "show", "-s", "--format=%P", merged["merge_commit"]).split()
            self.assertEqual(parents[1], merged["reviewed_integration"])
        finally:
            temporary.cleanup()

    def test_scope_cancellation_projection_is_sorted_and_closed(self):
        stories = {
            "AUTH-02": {"disposition": "not_started", "tip": "none"},
            "AUTH-01": {"disposition": "not_started", "tip": "none"},
        }
        projection, digest = delivery_git.cancellation_projection(
            "DLV-001", "sha256:" + "a" * 64,
            "Request withdrawn before execution", stories, "1" * 40,
        )
        self.assertEqual(list(projection["stories"]), ["AUTH-01", "AUTH-02"])
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaises(ValueError):
            delivery_git.cancellation_projection(
                "DLV-001", "sha256:" + "a" * 64, "", stories, "1" * 40,
            )
        projection_hash = delivery_git.cancellation_projection_hash(
            "DLV-001", digest, stories, "1" * 40, delivery_git.epoch_token(),
        )
        self.assertRegex(projection_hash, r"^sha256:[0-9a-f]{64}$")
        executed = {"AUTH-01": {"disposition": "unintegrated_discarded", "tip": "2" * 40}}
        _, executed_hash = delivery_git.cancellation_projection(
            "DLV-001", "sha256:" + "a" * 64, "Stopped after activation", executed, "1" * 40,
        )
        self.assertNotEqual(digest, executed_hash)

    def test_active_delivery_cancellation_releases_slot_and_publishes_terminal_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"; (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"max_parallel": 1, "doc_type_designations": {}}), encoding="utf-8")
            make_approved_backlog(docs)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"; subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "Cancel active work", "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.reserve_delivery(project, "DLV-001")
            self.author_execution_topology(docs)
            delivery_compile.approve_execution(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "plan"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.publish_execution_plan(project, "DLV-001")
            delivery_git.refresh_target(project, "DLV-001")
            delivery_git.claim_items(project, "DLV-001")
            started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "User stopped the Delivery")
            self.assertTrue(cancelled["ok"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
            item_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
            item_oid = delivery_git.remote_oid(project, "origin", item_ref)
            item_message = delivery_git.commit_message(project, item_oid)
            self.assertEqual(delivery_git.trailer(item_message, "Record"), "item-cancelled-v1")
            self.assertEqual(delivery_git.trailer(item_message, "Disposition"), "unintegrated_discarded")
            self.assertEqual(delivery_git.trailer(item_message, "Previous-Tip"), started["item"])
            integration_ref = delivery_git.canonical_refs("DLV-001")["integration"]
            integration_message = delivery_git.commit_message(project, delivery_git.remote_oid(project, "origin", integration_ref))
            self.assertEqual(delivery_git.trailer(integration_message, "Record"), "delivery-review-published-v1")

    def test_ref_free_reservation_pushes_fence_and_integration_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"
            (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"max_parallel": 1, "doc_type_designations": {}}), encoding="utf-8")
            make_approved_backlog(docs)
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
                                      "story": ["AUTH-01"]})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
            delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            result = delivery_git.reserve_delivery(project, "DLV-001")
            self.assertTrue(result["ok"])
            self.assertEqual(
                delivery_git.trailer(
                    delivery_git.commit_message(project, result["fence"]), "Config-Hash"
                ),
                delivery_git.governed_config_hash(project),
            )
            (project / "README.md").write_text("target moved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "target advance"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            refreshed = delivery_git.refresh_target(project, "DLV-001")
            self.assertTrue(refreshed["changed"])
            self.assertFalse(refreshed["plan_invalidated"])
            integration_message = delivery_git.commit_message(project, refreshed["integration"])
            self.assertEqual(delivery_git.trailer(integration_message, "Record"), "target-refresh-v1")
            revised = delivery_git.revise_unclaimed_scope(project, "DLV-001")
            revised_message = delivery_git.commit_message(project, revised["integration"])
            self.assertEqual(delivery_git.trailer(revised_message, "Record"), "delivery-scope-revised-v1")
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
            make_approved_backlog(docs)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"; subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True); subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication", "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.reserve_delivery(project, "DLV-001")
            self.author_execution_topology(docs)
            delivery_compile.approve_execution(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "plan"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.publish_execution_plan(project, "DLV-001")
            delivery_git.refresh_target(project, "DLV-001")
            result = delivery_git.claim_items(project, "DLV-001")
            self.assertEqual(result["claims"], ["AUTH-01"])
            config_path = project / "workspace" / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["max_parallel"] = 2
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "governed Fence configuration baseline"):
                delivery_git.start_item(project, "DLV-001", "AUTH-01")
            config["max_parallel"] = 1
            config_path.write_text(json.dumps(config), encoding="utf-8")
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
            delivery_git.clear_verified_writer_receipt(project, "DLV-001", "AUTH-01")
            delivery_git.remove_item_worktree(project, "DLV-001", "AUTH-01")
            takeover = delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
            self.assertNotEqual(takeover["writer_epoch"], activation["writer_epoch"])
            self.assertEqual(takeover["receipt"]["state"], "verified")
            blocked = delivery_git.block_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(blocked["status"], "blocked")
            unblocked = delivery_git.unblock_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(unblocked["status"], "active")
            paused = delivery_git.pause_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(paused["status"], "paused")
            self.assertFalse(Path(activation["worktree"]).exists())
            self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
            resumed = delivery_git.resume_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(resumed["receipt"]["state"], "verified")
            self.assertNotEqual(resumed["writer_epoch"], activation["writer_epoch"])
            first_product = self.commit_item_product_change(resumed["worktree"], "def authenticate():\n    return 'v1'\n")
            self.assertEqual(self.approve_item_evidence(resumed["worktree"]), 0)
            pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(pushed["product_tip"], first_product)
            first_evidence = delivery_git.commit_message(project, pushed["item"])
            self.assertEqual(delivery_git.trailer(first_evidence, "Record"), "item-evidence-v1")
            self.assertEqual(delivery_git.trailer(first_evidence, "Product-Tip"), first_product)
            integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            self.assertTrue(integrated["ok"])
            self.assertEqual(
                subprocess.run(
                    ["git", "show", f"{integrated['integration']}:src/auth.py"],
                    cwd=project, check=True, capture_output=True, text=True,
                ).stdout,
                "def authenticate():\n    return 'v1'\n",
            )
            reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(reopened["status"], "active")
            self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, reopened["item"]), "Record"), "item-reopen-v1")
            second_product = self.commit_item_product_change(reopened["worktree"], "def authenticate():\n    return 'v2'\n")
            self.assertEqual(self.approve_item_evidence(reopened["worktree"]), 0)
            pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
            self.assertEqual(pushed["product_tip"], second_product)
            integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            integration_oid = delivery_git.remote_oid(
                project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]
            )
            review_args = type("Args", (), {
                "docs": str(docs), "delivery": "DLV-001",
                "reviewed_commit": integrated["integration"],
                "reviewed_integration_commit": integration_oid,
            })
            delivery_compile.approve_review(review_args)
            published = delivery_git.publish_delivery_review(project, "DLV-001")
            self.assertTrue(published["ok"])
            intent = delivery_git.prepare_pr_creation(project, "DLV-001")
            self.assertEqual(intent["provider"], "github")
            pr_url = "https://github.com/agentrof/example/pull/17"
            record_args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "url": pr_url})
            delivery_compile.record_pr(record_args)
            recorded = delivery_git.record_pr_remote(project, "DLV-001", pr_url)
            self.assertEqual(recorded["pull_request"], "17")
            refs = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=True, text=True, capture_output=True).stdout
            self.assertIn("refs/heads/agentrof/items/auth-01", refs)
            self.assertNotIn("refs/heads/agentrof/slots/001", refs)
            cancelled = delivery_git.cancel_delivery(
                project, "DLV-001", "Target no longer requires this Delivery"
            )
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertTrue(cancelled["reverts"])
            self.assertEqual(
                delivery_git.trailer(
                    delivery_git.commit_message(project, cancelled["finalization"]),
                    "Record",
                ),
                "cancellation-finalized-v1",
            )


if __name__ == "__main__":
    unittest.main()
