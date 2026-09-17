"""Gate 4 naming and read-only Git preflight tests."""

from __future__ import annotations

import sys
import json
import hashlib
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
import delivery_governance  # noqa: E402
import operation_compile  # noqa: E402
import architecture_compile  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402


class DeliveryGitTests(unittest.TestCase):
    def approve_governance(self, docs: Path, max_parallel: int = 1) -> None:
        initialized = type("Args", (), {"docs": str(docs), "max_parallel": max_parallel})
        self.assertEqual(delivery_governance.init(initialized), 0)
        approved = type("Args", (), {"docs": str(docs)})
        self.assertEqual(delivery_governance.approve(approved), 0)

    def author_execution_topology(self, docs: Path, delivery: str = "DLV-001") -> None:
        self.approve_verification_contract(docs)
        root = delivery_compile.find_delivery(docs, delivery)
        self.assertIsNotNone(root)
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

    def approve_verification_contract(self, docs: Path) -> None:
        path = docs / "operation" / "verification-contract.md"
        if path.exists():
            return
        args = type("Args", (), {
            "docs": str(docs), "kind": "verification",
            "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"],
        })
        self.assertEqual(operation_compile.init(args), 0)
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(operation_compile.approve(args), 0)

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
        subprocess.run(["git", "-C", str(project), "config", "gc.auto", "0"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        (project / "workspace" / "docs").mkdir(parents=True)
        (project / "workspace" / "config.json").write_text(
            json.dumps({"schema_version": 2, "team_id": "software-engineering-team",
                        "output_language": "English", "terminology_language": "English"}),
            encoding="utf-8",
        )
        self.approve_governance(project / "workspace" / "docs")
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
        remote = project / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "--git-dir", str(remote), "config", "gc.auto", "0"], check=True)
        subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
        subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        return temporary, project

    def test_project_fixture_disables_automatic_git_maintenance(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        remote = project / "remote.git"
        local_auto_gc = subprocess.run(
            ["git", "-C", str(project), "config", "--get", "gc.auto"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        remote_auto_gc = subprocess.run(
            ["git", "--git-dir", str(remote), "config", "--get", "gc.auto"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(local_auto_gc, "0")
        self.assertEqual(remote_auto_gc, "0")

    def prepare_pr_intent(self):
        """Build one real remote Delivery through its durable PR intent."""
        temporary, project = self.make_project()
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
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

    def test_quiescent_v1_fence_upgrades_to_governed_v2(self):
        temporary, project = self.make_project()
        try:
            target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
            v1 = delivery_git.commit_tree(
                project, target, [], "Open legacy Agentrof Fence", {
                    "Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
                    "Epoch": delivery_git.epoch_token(), "Target": target,
                    "Config-Hash": "none", "Source-Kind": "none", "Source-Intent": "none",
                    "Target-Update-Intent": "none", "Target-Update-Attempt": "none",
                    "Target-Repository": "none", "Target-Carrier-Kind": "none",
                    "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
                    "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
                    "Upgrade-Phase": "none", "Upgrade-Contract": "none", "Handoff-Target": "none",
                    "Barrier-Kind": "none", "Barrier-Epoch": "none",
                },
            )
            fence = delivery_git.canonical_refs("DLV-000")["fence"]
            delivery_git.atomic_push(project, "origin", [(fence, "", v1)])
            preview = delivery_git.upgrade_fence_v1(project, dry_run=True)
            self.assertTrue(preview["changed"])
            result = delivery_git.upgrade_fence_v1(project)
            message = delivery_git.commit_message(project, result["fence"])
            self.assertEqual(delivery_git.trailer(message, "Record"), "project-fence-v2")
            self.assertEqual(delivery_git.trailer(message, "Governance-Hash"), delivery_git.governed_governance_hash(project))
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
            (project / "workspace" / "config.json").write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
            self.approve_governance(docs)
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
            (project / "workspace" / "config.json").write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
            self.approve_governance(docs)
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
                    delivery_git.commit_message(project, result["fence"]), "Governance-Hash"
                ),
                delivery_git.governed_governance_hash(project),
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

    def test_candidate_map_excludes_unpublished_local_governance(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        docs = project / "workspace/docs"
        governance = delivery_governance.path_for(docs)
        relative_governance = governance.relative_to(project).as_posix()
        original = governance.read_bytes()
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        index = (project / ".git/index").read_bytes()
        governance.unlink()
        base = delivery_git.commit_tree(project, head, [relative_governance], "Candidate before Governance", {})
        governance.write_bytes(original)
        args = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        dod = docs / "delivery/definition-of-done.md"
        delivery_compile.render_map(docs)
        self.assertIn("delivery/governance/governance|Governance", (docs / "maps/delivery.md").read_text(encoding="utf-8"))
        candidate = delivery_git.commit_tree(project, base, [dod.relative_to(project).as_posix()],
            "Publish candidate Definition of Done", {}, delivery_projections=True)
        candidate_map = delivery_git.run_git(project, "show", candidate + ":workspace/docs/maps/delivery.md")
        self.assertIn("[[delivery/definition-of-done|Definition of Done]]", candidate_map)
        self.assertNotIn("delivery/governance/governance", candidate_map)
        self.assertEqual(delivery_git.run_git(project, "ls-tree", candidate, "--", relative_governance), "")
        self.assertEqual(delivery_git.delivery_projection_changes(project, candidate), {})
        self.assertEqual(governance.read_bytes(), original)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_publications_render_exact_candidate_without_local_sibling_or_dirty_note(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        self.approve_verification_contract(docs)
        dirty = docs / "research/notes/local-note.md"
        dirty.parent.mkdir(parents=True)
        dirty.write_text("# Local note\n\nCommitted content.\n", encoding="utf-8")
        stale_catalog = docs / "maps/_relations/obsolete/relations-001.md"
        stale_catalog.parent.mkdir(parents=True)
        stale_catalog.write_text(vault_check.RELATION_CATALOG_MARKER + "\nOld catalog.\n", encoding="utf-8")
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve source inputs")
        delivery_git.run_git(project, "push", "-q")
        target = delivery_git.run_git(project, "rev-parse", "HEAD")
        story = next(docs.glob("backlog/epics/*/stories/*/story.md"))
        original_story = story.read_text(encoding="utf-8")
        init = type("Args", (), {
            "docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication",
            "outcome": None, "target_branch": "main", "story": ["AUTH-01"],
        })
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        directory = delivery_compile.find_delivery(docs, "DLV-001")
        approved_props, _ = delivery_compile.split_note(directory / "delivery.md")
        (directory / ".DS_Store").write_bytes(b"local operating system metadata")
        sibling = docs / "delivery/deliveries/dlv-002-unpublished/delivery.md"
        sibling.parent.mkdir(parents=True)
        sibling.write_text(delivery_compile.frontmatter({
            "type": "delivery", "id": "DLV-002", "title": "Unpublished delivery",
            "status": "scope_proposed", "derives_from": [delivery_compile.link(
                story.relative_to(docs).as_posix(), "AUTH-01")],
        }, "# Unpublished delivery\n"), encoding="utf-8")
        dirty.write_text("# Local note\n\nUnpublished edit.\n", encoding="utf-8")
        delivery_git.run_git(project, "add", dirty.relative_to(project).as_posix())
        delivery_compile.render_map(docs)
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.assertEqual(vault_check.cmd_render_relations(
            type("Args", (), {"vault": docs}), policy), 0)

        def verify_publication(result):
            oid = result["integration"]
            self.assertEqual(delivery_git.remote_oid(
                project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]), oid)
            with tempfile.TemporaryDirectory() as clone_root:
                clone = Path(clone_root) / "checkout"
                delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                delivery_git.run_git(clone, "checkout", "-q", "--detach", oid)
                published = clone / "workspace/docs"
                findings = []
                vault_check.check_relation_projections(vault_check.build_vault(published, policy), findings)
                self.assertEqual(findings, [])
                self.assertFalse((published / sibling.relative_to(docs)).exists())
                self.assertFalse((published / stale_catalog.relative_to(docs)).exists())
                self.assertFalse((published / directory.relative_to(docs) / ".DS_Store").exists())
                self.assertNotIn("DLV-002", (published / "maps/delivery.md").read_text(encoding="utf-8"))
                self.assertNotIn("Unpublished delivery", (published / "maps/_generated/cross-subtree-matrix.md").read_text(encoding="utf-8"))
                self.assertEqual((published / dirty.relative_to(docs)).read_text(encoding="utf-8"),
                                 "# Local note\n\nCommitted content.\n")
                published_story = (published / story.relative_to(docs)).read_text(encoding="utf-8")
                self.assertIn(directory.name, vault_check.relation_block(published_story))
                self.assertEqual(delivery_compile.without_generated_relations(published_story),
                                 delivery_compile.without_generated_relations(original_story))
                props, _ = delivery_compile.split_note(published / directory.relative_to(docs) / "delivery.md")
                self.assertEqual(props["scope_hash"], approved_props["scope_hash"])
                for local_note in directory.rglob("*.md"):
                    published_note = published / local_note.relative_to(docs)
                    self.assertEqual(
                        delivery_compile.without_generated_relations(published_note.read_text(encoding="utf-8")),
                        delivery_compile.without_generated_relations(local_note.read_text(encoding="utf-8")),
                    )
                self.assertEqual(delivery_git.delivery_projection_changes(clone, oid), {})

        for verb in (delivery_git.reserve_delivery, delivery_git.revise_unclaimed_scope,
                     delivery_git.publish_execution_plan):
            with self.subTest(verb=verb.__name__):
                if verb is delivery_git.publish_execution_plan:
                    self.author_execution_topology(docs)
                    self.assertEqual(delivery_compile.approve_execution(scope), 0)
                local_before = {path: path.read_bytes() for path in docs.rglob("*") if path.is_file()}
                index_before = (project / ".git/index").read_bytes()
                result = verb(project, "DLV-001")
                verify_publication(result)
                self.assertEqual({path: path.read_bytes() for path in docs.rglob("*") if path.is_file()}, local_before)
                self.assertEqual((project / ".git/index").read_bytes(), index_before)
                self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), target)

    def prepare_execution_with_draft_reserved_contracts(self, runtime=True, path_claim="src/auth.py", architecture=False, legacy_operation_receipts=False):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        if architecture:
            catalog = docs / "solution-design/_generated/component-catalog.json"
            catalog.parent.mkdir(parents=True, exist_ok=True)
            catalog.write_text(json.dumps({"components": [
                {"component_id": "api", "sourcing": "build", "code_path": "src/auth.py"},
                {"component_id": "other", "sourcing": "build", "code_path": "src/other.py"},
            ]}), encoding="utf-8")
            import landscape_check
            landscape = docs / "solution-design/landscape.md"
            props, body = delivery_compile.split_note(landscape)
            landscape.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            props["package_hash"] = landscape_check.package_hash(landscape.parent)
            landscape.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        for kind in ("verification", "environment"):
            args = type("Args", (), {"docs": str(docs), "kind": kind,
                                    "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"]})
            self.assertEqual(operation_compile.init(args), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve sources with draft Operation contracts")
        delivery_git.run_git(project, "push", "-q")
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth", "goal": "Authenticate",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(args), 0)
        reserved = delivery_git.reserve_delivery(project, "DLV-001")
        for kind, command in (("verification", "test_command"), ("environment", "env_command")):
            path = operation_compile.contract_path(docs, kind)
            props, body = operation_compile.parse(path)
            props[command] = "make test" if kind == "verification" else "make env"
            operation_compile.atomic_text(path, operation_compile.render(props, body))
            self.assertEqual(operation_compile.approve(type("Args", (), {"docs": str(docs), "kind": kind})), 0)
            if legacy_operation_receipts:
                props, authored = operation_compile.parse(path)
                view = {key: value for key, value in props.items()
                        if key not in {"source_hash", "approved_at_utc"}}
                props["source_hash"] = "sha256:" + hashlib.sha256(json.dumps(
                    {"frontmatter": view, "body": authored + "\n"}, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                block = vault_check.RELATION_START + "\n\nHistorical generated inverse\n\n" + vault_check.RELATION_END
                path.write_text(vault_check.replace_relation_block(operation_compile.render(props, authored), block), encoding="utf-8")
        self.author_execution_topology(docs)
        directory = delivery_compile.find_delivery(docs, "DLV-001")
        item = directory / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(item)
        props["runtime_required"] = runtime
        props["path_claims"] = [path_claim]
        if architecture:
            props.update({"architecture_impact": "required", "architecture_components": ["api"],
                          "architecture_record_kinds": ["system-architecture", "architecture-component", "interface-contract"],
                          "architecture_reason": "Define the authentication interface."})
            sources, _snapshot, errors = delivery_compile.approved_backlog_sources(docs, ["AUTH-01"])
            self.assertEqual(errors, [])
            props["role_sequence"] = delivery_compile.execution_roles(sources["AUTH-01"], True)
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(args), 0)
        return project, docs, directory, item, reserved

    def prepare_stamped_architecture_item(self):
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            runtime=False, architecture=True)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        worktree = Path(active["worktree"])
        item = worktree / directory.relative_to(project) / "items/auth-01/item.md"
        original = item.read_bytes()
        before, before_body = delivery_compile.split_note(item)
        active_docs = worktree / "workspace/docs"
        self.assertEqual(architecture_compile.init_root(active_docs, "AUTH-01"), 0)
        self.assertEqual(architecture_compile.init_component(active_docs, "api", "AUTH-01"), 0)
        self.assertEqual(architecture_compile.stub(active_docs, "interface", "api", "IFC-001", "auth", "AUTH-01"), 0)
        self.assertEqual(architecture_compile.stamp_item(active_docs, "AUTH-01"), 0)
        props, body = delivery_compile.split_note(item)
        self.assertEqual(props["item_plan_hash"], before["item_plan_hash"])
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        self.assertEqual(body, before_body)
        self.assertEqual(item.read_bytes().split(b"\n---\n", 1)[1], original.split(b"\n---\n", 1)[1])
        self.assertEqual({key: value for key, value in props.items() if key not in {"source_hash", "architecture_delta_hash"}},
                         {key: value for key, value in before.items() if key not in {"source_hash", "architecture_delta_hash"}})
        (worktree / "src").mkdir()
        (worktree / "src/auth.py").write_text("def authenticate():\n    return 'approved'\n", encoding="utf-8")
        delivery_git.run_git(worktree, "add", "workspace/docs", "src")
        delivery_git.run_git(worktree, "commit", "-qm", "Implement authentication with sealed Architecture")
        return project, worktree, item, active

    def test_architecture_stamp_authored_evidence_push_and_integration(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        product = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        authored = {}
        for name, report in (("code-review.md", "Reviewed authentication interface and rejected unauthorized input."),
                             ("verification.md", "Executed authentication success and missing-credential tests; both passed.")):
            path = item.parent / name
            props, body = delivery_compile.split_note(path)
            body += "\n\n" + report + "\n"
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            authored[name] = body.rstrip()
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], product)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", pushed["item"] + "^"), product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        for name, body in authored.items():
            relative = (item.parent / name).relative_to(worktree).as_posix()
            props, actual = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            self.assertEqual(actual, body)
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, actual))
            self.assertEqual(props["reviewed_commit" if name == "code-review.md" else "verified_commit"], product)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], item.relative_to(worktree).as_posix(), delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertTrue(props["architecture_delta_hash"].startswith("sha256:"))
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertFalse(worktree.exists())

    def test_architecture_push_rejects_control_and_receipt_tampering(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        baseline = delivery_git.run_git(project, "ls-remote", "origin")
        package = item.parents[2]
        def mutate_note(path, key=None, value=None):
            props, body = delivery_compile.split_note(path)
            if key:
                props[key] = value
            else:
                body += "\nUnapproved change.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        mutations = {
            "body": lambda: mutate_note(item),
            "status": lambda: mutate_note(item, "status", "paused"),
            "owner": lambda: mutate_note(item, "owner_role", "frontend_developer"),
            "path_claim": lambda: mutate_note(item, "path_claims", ["src/other.py"]),
            "item_plan": lambda: mutate_note(item, "item_plan_hash", "sha256:" + "0" * 64),
            "story_pin": lambda: mutate_note(item, "story_source_hash", "sha256:" + "0" * 64),
            "operation_pin": lambda: mutate_note(item, "verification_contract_hash", "sha256:" + "0" * 64),
            "scope": lambda: mutate_note(package / "delivery.md"),
            "plan": lambda: mutate_note(package / "execution-plan.md"),
            "committed_evidence": lambda: mutate_note(item.parent / "code-review.md"),
            "new_item": lambda: (package / "items/extra").mkdir(),
            "deleted_evidence": lambda: (item.parent / "verification.md").unlink(),
            "non_markdown": lambda: (package / "extra\ncontrol.json").write_text("{}"),
            "mode": lambda: item.chmod(0o755),
            "symlink": lambda: (item.unlink(), item.symlink_to("code-review.md")),
            "missing_hash": lambda: mutate_note(item, "architecture_delta_hash", "none"),
            "wrong_hash": lambda: mutate_note(item, "architecture_delta_hash", "sha256:" + "0" * 64),
            "source_hash": lambda: item.write_text(item.read_text().replace("source_hash: sha256:", "source_hash: broken:")),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                mutation()
                if label == "new_item":
                    (package / "items/extra/item.md").write_bytes(item.read_bytes())
                delivery_git.run_git(worktree, "add", "workspace/docs")
                delivery_git.run_git(worktree, "commit", "-qm", "Tamper with control")
                # Independent report writers can produce receipts, but publication
                # must still reject changes to the authoritative Item controls.
                if label not in {"status", "deleted_evidence", "symlink"}:
                    self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                with self.assertRaises(RuntimeError):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

    def test_evidence_authoring_rejects_product_index_drift_and_unsafe_reports(self):
        import os
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        review = item.parent / "code-review.md"
        for label in ("product", "staged_product", "sibling", "leading_space", "symlink", "hardlink", "mode"):
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                review.write_text(review.read_text() + "\nReviewed the authenticated entrypoint.\n")
                if label in {"product", "staged_product"}:
                    product = worktree / "src/auth.py"
                    content = product.read_bytes()
                    product.write_text("unreviewed change\n")
                    if label == "staged_product":
                        delivery_git.run_git(worktree, "add", "src/auth.py")
                        product.write_bytes(content)
                elif label == "sibling":
                    (item.parents[2] / "execution-plan.md").write_text("unapproved plan\n")
                elif label == "leading_space":
                    lookalike = worktree / (" " + review.relative_to(worktree).as_posix())
                    lookalike.parent.mkdir(parents=True)
                    lookalike.write_text("untracked report lookalike\n")
                elif label == "symlink":
                    review.unlink(); review.symlink_to(worktree / "src/auth.py")
                elif label == "hardlink":
                    os.link(review, project / "report-hardlink.md")
                else:
                    review.chmod(0o755)
                before = review.read_bytes()
                self.assertNotEqual(self.approve_item_evidence(str(worktree)), 0)
                self.assertEqual(review.read_bytes(), before)
        delivery_git.run_git(worktree, "reset", "--hard", clean)
        delivery_git.run_git(worktree, "clean", "-fd")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        review.write_text(review.read_text() + "\nChanged after approval.\n")
        with self.assertRaisesRegex(RuntimeError, "source_hash is stale"):
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
        for flag, target in ((flag, target) for flag in ("assume-unchanged", "skip-worktree")
                             for target in ("item", "product")):
            with self.subTest(index_flag=flag, target=target):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                path = item if target == "item" else worktree / "src/auth.py"
                relative = path.relative_to(worktree).as_posix()
                delivery_git.run_git(worktree, "update-index", "--" + flag, relative)
                if target == "item":
                    props, body = delivery_compile.split_note(item)
                    props["architecture_impact"] = "not_applicable"
                    props["owner_role"] = "frontend_developer"
                    item.write_text(delivery_compile.frontmatter(props, body))
                else:
                    path.write_text("def authenticate():\n    return 'untested'\n")
                review.write_text(review.read_text() + "\nReviewed the authentication interface.\n")
                before_reports = {name: (item.parent / name).read_bytes()
                                  for name in ("code-review.md", "verification.md")}
                before_index = delivery_git.run_git(worktree, "ls-files", "-v", "-z")
                self.assertNotEqual(self.approve_item_evidence(str(worktree)), 0)
                baseline = delivery_git.run_git(project, "ls-remote", "origin")
                with self.assertRaisesRegex(RuntimeError, "index flags hide tracked paths"):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)
                self.assertEqual(delivery_git.run_git(worktree, "ls-files", "-v", "-z"), before_index)
                self.assertEqual({name: (item.parent / name).read_bytes() for name in before_reports}, before_reports)
                delivery_git.run_git(worktree, "update-index", "--no-" + flag, relative)

    def test_architecture_push_rejects_unsealed_stale_and_outside_claim_delta(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        baseline = delivery_git.run_git(project, "ls-remote", "origin")
        architecture = worktree / "workspace/docs/system-architecture"
        delta_path = architecture / "_ledger/item-deltas/AUTH-01.json"
        record = architecture / "components/api/interfaces/auth/interface.md"
        for label in ("missing_delta", "stale_record", "missing_seal", "unsealed_forgery", "unclaimed_component", "unclaimed_kind", "symlink_record"):
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                if label == "missing_delta":
                    delta_path.unlink()
                elif label == "missing_seal":
                    (architecture / "_ledger/records/IFC-001/r1.json").unlink()
                elif label == "symlink_record":
                    record.unlink(); record.symlink_to("../../../component.md")
                elif label == "stale_record":
                    record.write_text(record.read_text() + "\nChanged after seal.\n")
                else:
                    if label == "unclaimed_component":
                        self.assertEqual(architecture_compile.init_component(worktree / "workspace/docs", "other", "AUTH-01"), 0)
                        architecture_compile.seal_record(architecture, architecture / "components/other/component.md", "AUTH-01")
                    elif label == "unclaimed_kind":
                        self.assertEqual(architecture_compile.stub(worktree / "workspace/docs", "runtime", "api", "RUN-001", "runtime", "AUTH-01"), 0)
                        architecture_compile.seal_record(architecture, architecture / "components/api/runtime/runtime/runtime.md", "AUTH-01")
                    else:
                        record.write_text(record.read_text().replace("revision_state: sealed", "revision_state: draft"))
                    delta = architecture_compile.item_delta(architecture, "AUTH-01")
                    digest = architecture_compile.item_delta_hash(delta)
                    delta_path.write_text(json.dumps({**delta, "architecture_delta_hash": digest}))
                    props, body = delivery_compile.split_note(item)
                    props["architecture_delta_hash"] = digest
                    props["source_hash"] = delivery_compile.content_hash(props, body)
                    item.write_text(delivery_compile.frontmatter(props, body))
                delivery_git.run_git(worktree, "add", "workspace/docs")
                delivery_git.run_git(worktree, "commit", "-qm", "Tamper with Architecture receipt")
                self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                with self.assertRaisesRegex(RuntimeError, "[Aa]rchitecture"):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

    def governance_target_handoff(self, project, docs, extra_paths=()):
        args = type("Args", (), {"docs": str(docs)})
        self.assertEqual(delivery_governance.begin_revision(args), 0)
        governance = delivery_governance.path_for(docs)
        props, body = delivery_governance.read(governance)
        props["max_parallel"] += 1
        governance.write_text(delivery_governance.render(props, body), encoding="utf-8")
        self.assertEqual(delivery_governance.approve(args), 0)
        desired = delivery_governance.status(docs)[0]["governance_hash"]
        delivery_git.begin_applying_governance(project, desired)
        _branch, baseline = delivery_git.resolve_target(project, "origin")
        candidate = delivery_git.commit_tree(project, baseline,
            [governance.relative_to(project).as_posix(), *extra_paths], "Publish Governance", {},
            delivery_projections=True)
        carrier = "refs/heads/governance-input"
        delivery_git.atomic_push(project, "origin", [(carrier, "", candidate)])
        delivery_git.authorize_target_update(project, "governance", desired, "origin",
            "direct_target", carrier, "direct", candidate, baseline, "upstream")
        delivery_git.apply_target_update(project, "governance")
        finished = delivery_git.finish_source_handoff(project)
        self.assertEqual(finished["target"], candidate)
        return candidate, finished["fence"]

    def test_governance_handoff_refreshes_each_integration_and_preserves_published_contracts(self):
        project, docs, directory, _item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        first = delivery_git.publish_execution_plan(project, "DLV-001")
        init = type("Args", (), {"docs": str(docs), "id": "DLV-002", "slug": "second", "goal": "Second delivery",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-002"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        second_dir = delivery_compile.find_delivery(docs, "DLV-002")
        second_ref = delivery_git.canonical_refs("DLV-002")["integration"]
        second_base = delivery_git.commit_tree(project, reserved["target"],
            delivery_git.package_paths(project, second_dir, docs, include_map=False),
            "Reserve second Delivery", {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": "DLV-002"},
            delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(second_ref, "", second_base)])
        self.author_execution_topology(docs, "DLV-002")
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        second = delivery_git.publish_execution_plan(project, "DLV-002")
        target, fence = self.governance_target_handoff(project, docs)
        self.assertFalse(delivery_git.is_ancestor(project, target, first["integration"]))
        self.assertFalse(delivery_git.is_ancestor(project, target, second["integration"]))
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.claim_items(project, "DLV-001")
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        self.assertFalse(delivery_git.remote_has_ref(project, "origin", delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]))
        for ident, before, package in (("DLV-001", first["integration"], directory),
                                        ("DLV-002", second["integration"], second_dir)):
            refreshed = delivery_git.refresh_target(project, ident)
            self.assertTrue(refreshed["changed"])
            self.assertFalse(refreshed["plan_invalidated"])
            self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
            self.assertEqual(refreshed["previous_target"], reserved["target"])
            for relative in ((package / "delivery.md").relative_to(project).as_posix(),
                             "workspace/docs/operation/verification-contract.md"):
                self.assertEqual(delivery_git.run_git(project, "show", before + ":" + relative),
                                 delivery_git.run_git(project, "show", refreshed["integration"] + ":" + relative))
            with tempfile.TemporaryDirectory() as temporary:
                clone = Path(temporary) / "checkout"
                delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                delivery_git.run_git(clone, "checkout", "-q", "--detach", refreshed["integration"])
                candidate_docs = clone / "workspace/docs"
                candidate_package = candidate_docs / package.relative_to(docs)
                self.assertEqual(delivery_compile.delivery_findings(candidate_docs, ident)[1], [])
                for path in candidate_package.rglob("*.md"):
                    relative = path.relative_to(clone).as_posix()
                    previous = delivery_git.run_git(project, "show", before + ":" + relative) + "\n"
                    self.assertEqual(delivery_compile.without_generated_relations(path.read_text(encoding="utf-8")),
                                     delivery_compile.without_generated_relations(previous))
                map_text = (candidate_docs / "maps/delivery.md").read_text(encoding="utf-8")
                self.assertIn("[[delivery/governance/governance|Governance]]", map_text)
                self.assertIn(ident, map_text)
                self.assertEqual(delivery_git.delivery_projection_changes(clone, refreshed["integration"]), {})
                vault = vault_check.build_vault(candidate_docs, vault_check.load_policy(vault_check.DEFAULT_POLICY))
                findings = []
                for check in (vault_check.check_frontmatter_props, vault_check.check_nav_footer,
                              vault_check.check_wikilink_resolution, vault_check.check_orphans,
                              vault_check.check_map_coverage, vault_check.check_relation_contract,
                              vault_check.check_relation_projections):
                    check(vault, findings)
                self.assertEqual([finding for finding in findings if finding.path.startswith("delivery/")
                                  or finding.path == "maps/delivery.md" or finding.check == "generated_views"], [])
                self.assertEqual(delivery_git.run_git(clone, "status", "--porcelain"), "")
            again = delivery_git.refresh_target(project, ident)
            self.assertFalse(again["changed"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)

    def test_target_refresh_rejects_conflicting_authored_delivery_content(self):
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        delivery = directory / "delivery.md"
        props, body = delivery_compile.split_note(delivery)
        body += "\nUnapproved target scope change.\n"
        props["source_hash"] = delivery_compile.content_hash(props, body)
        delivery.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        _target, fence = self.governance_target_handoff(project, docs,
            delivery_git.package_paths(project, directory, docs, include_map=False))
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_refresh_preserves_approved_control_content_and_path_set(self):
        for control in ("delivery.md", "execution-plan.md", "items/auth-01/item.md", "injected_item", "deleted_item"):
            with self.subTest(control=control):
                project, _docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                base = published["integration"]
                # Both branches descend from the same already-approved package.
                previous = delivery_git.remote_oid(project, "origin", "refs/heads/main")
                delivery_git.atomic_push(project, "origin", [("refs/heads/main", previous, base)])
                delivery_git.refresh_target(project, "DLV-001")
                refs = delivery_git.canonical_refs("DLV-001")
                integration = delivery_git.remote_oid(project, "origin", refs["integration"])
                path = directory / control if control.endswith(".md") else item
                relative = path.relative_to(project).as_posix()
                original = delivery_git.run_git(project, "show", base + ":" + relative) + "\n"
                ours = vault_check.replace_relation_block(original,
                    vault_check.RELATION_START + "\n\nIntegration view\n\n" + vault_check.RELATION_END)
                integration = delivery_git.commit_replacements(project, integration, {relative: ours}, "Integration projection", {})
                delivery_git.atomic_push(project, "origin", [(refs["integration"],
                    delivery_git.remote_oid(project, "origin", refs["integration"]), integration)])
                if control == "injected_item":
                    path = directory / "items/auth-02/item.md"
                    path.parent.mkdir()
                    path.write_text("# Injected Item\n", encoding="utf-8")
                elif control == "deleted_item":
                    path.unlink()
                else:
                    theirs = vault_check.replace_relation_block(original,
                        vault_check.RELATION_START + "\n\nTarget view\n\n" + vault_check.RELATION_END)
                    path.write_text(theirs + "\nTarget-only authored control change.\n", encoding="utf-8")
                target = delivery_git.commit_tree(project, base, [path.relative_to(project).as_posix()], "Target control drift", {})
                delivery_git.atomic_push(project, "origin", [("refs/heads/main", base, target)])
                before = delivery_git.run_git(project, "ls-remote", "origin")
                with self.assertRaisesRegex(RuntimeError, "selected Delivery control content or path set"):
                    delivery_git.refresh_target(project, "DLV-001")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
                self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
                self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_projection_merge_rejects_structural_conflicts_at_owned_generated_paths(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        for relative in ("workspace/docs/maps/delivery.md", "workspace/docs/maps/_relations/test/relations-001.md"):
            with self.subTest(path=relative):
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Base generated view\n", encoding="utf-8")
                base = delivery_git.commit_tree(project, head, [relative], "Base projection", {})
                path.write_text("Integration generated view\n", encoding="utf-8")
                ours = delivery_git.commit_tree(project, base, [relative], "Integration projection", {})
                path.unlink()
                path.symlink_to("unrelated-target")
                theirs = delivery_git.commit_tree(project, base, [relative], "Target symlink", {})
                with self.assertRaisesRegex(RuntimeError, "unmerged"):
                    delivery_git.merge_candidate(project, ours, theirs, "Reject structural conflict", {}, delivery_projections=True)
                self.assertTrue(path.is_symlink())
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_projection_merge_resolves_only_owned_inverse_blocks(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        index = (project / ".git/index").read_bytes()
        note = project / "workspace/docs/research/notes/merge.md"
        note.parent.mkdir(parents=True)
        authored = "# Authored note\n\nStable content.\n"

        def content(projection, body=authored):
            return body + "\n" + vault_check.RELATION_START + "\n\n" + projection + "\n\n" + vault_check.RELATION_END + "\n"

        relative = note.relative_to(project).as_posix()
        note.write_text(content("Base view"), encoding="utf-8")
        base = delivery_git.commit_tree(project, head, [relative], "Base note", {})
        note.write_text(content("Integration view"), encoding="utf-8")
        ours = delivery_git.commit_tree(project, base, [relative], "Integration view", {})
        note.write_text(content("Target view"), encoding="utf-8")
        theirs = delivery_git.commit_tree(project, base, [relative], "Target view", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, ours, theirs, "Ordinary merge", {})
        merged = delivery_git.merge_candidate(project, ours, theirs, "Projection merge", {}, delivery_projections=True)
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":" + relative) + "\n", authored)
        self.assertEqual(delivery_git.delivery_projection_changes(project, merged), {})
        note.write_text(content("Integration view", "# Authored note\n\nIntegration edit.\n"), encoding="utf-8")
        authored_ours = delivery_git.commit_tree(project, base, [relative], "Integration authored edit", {})
        note.write_text(content("Target view", "# Authored note\n\nTarget edit.\n"), encoding="utf-8")
        authored_theirs = delivery_git.commit_tree(project, base, [relative], "Target authored edit", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, authored_ours, authored_theirs, "Reject authored conflict", {}, delivery_projections=True)
        for malformed in (content("Target view").replace(vault_check.RELATION_END, ""),
                          content("Target view") + vault_check.RELATION_START + "\n"):
            note.write_text(malformed, encoding="utf-8")
            bad = delivery_git.commit_tree(project, base, [relative], "Malformed projection markers", {})
            with self.assertRaisesRegex(RuntimeError, "unmerged"):
                delivery_git.merge_candidate(project, ours, bad, "Reject malformed markers", {}, delivery_projections=True)
        foreign = lambda text: text.replace("relations:generated", "structural:generated")
        note.write_text(foreign(content("Base contents")), encoding="utf-8")
        foreign_base = delivery_git.commit_tree(project, head, [relative], "Base structural contents", {})
        note.write_text(foreign(content("Integration contents")), encoding="utf-8")
        foreign_ours = delivery_git.commit_tree(project, foreign_base, [relative], "Integration structural contents", {})
        note.write_text(foreign(content("Target contents")), encoding="utf-8")
        foreign_theirs = delivery_git.commit_tree(project, foreign_base, [relative], "Target structural contents", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, foreign_ours, foreign_theirs, "Reject unowned markers", {}, delivery_projections=True)
        artifact = project / "workspace/docs/experience-design/artifacts/prototype.md"
        artifact.parent.mkdir(parents=True)
        relative_artifact = artifact.relative_to(project).as_posix()
        artifact.write_text(content("Base prototype content"), encoding="utf-8")
        artifact_base = delivery_git.commit_tree(project, head, [relative_artifact], "Base prototype", {})
        artifact.write_text(content("Integration prototype content"), encoding="utf-8")
        artifact_ours = delivery_git.commit_tree(project, artifact_base, [relative_artifact], "Integration prototype", {})
        artifact.write_text(content("Target prototype content"), encoding="utf-8")
        artifact_theirs = delivery_git.commit_tree(project, artifact_base, [relative_artifact], "Target prototype", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, artifact_ours, artifact_theirs, "Reject prototype conflict", {}, delivery_projections=True)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_target_refresh_reissues_an_untouched_claim_against_the_new_integration(self):
        """A claim carrying no work must not strand its Item behind the target."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        before = delivery_git.remote_oid(project, "origin", refs["item"])
        self.governance_target_handoff(project, docs)
        delivery_git.refresh_target(project, "DLV-001")
        after = delivery_git.remote_oid(project, "origin", refs["item"])
        self.assertNotEqual(after, before)
        self.assertEqual(
            delivery_git.trailer(delivery_git.commit_message(project, after), "Record"),
            "item-claim-v1",
        )
        integration = delivery_git.remote_oid(
            project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
        self.assertTrue(delivery_git.is_ancestor(project, integration, after))
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.assertTrue(Path(started["worktree"]).is_dir())

    def test_stale_paused_item_cannot_activate_after_integration_refresh(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        delivery_git.start_item(project, "DLV-001", "AUTH-01")
        paused = delivery_git.pause_item(project, "DLV-001", "AUTH-01")
        self.governance_target_handoff(project, docs)
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.resume_item(project, "DLV-001", "AUTH-01")
        delivery_git.refresh_target(project, "DLV-001")
        for action in (delivery_git.start_item, delivery_git.resume_item):
            with self.assertRaisesRegex(RuntimeError, "Item does not contain"):
                action(project, "DLV-001", "AUTH-01")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), paused["item"])
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_stale_active_item_takeover_preserves_existing_slot_and_receipt(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        receipt = delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01")
        slots = delivery_git.remote_slot_oids(project, "origin")
        self.governance_target_handoff(project, docs)
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
        delivery_git.refresh_target(project, "DLV-001")
        with self.assertRaisesRegex(RuntimeError, "Item does not contain"):
            delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
        self.assertEqual(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"), receipt)
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), slots)
        self.assertTrue(Path(active["worktree"]).is_dir())

    def test_target_refresh_rejects_changed_descendant_of_claimed_directory(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False, "src")
        delivery_git.publish_execution_plan(project, "DLV-001")
        claimed = delivery_git.claim_items(project, "DLV-001")
        source = project / "src/new.py"
        source.parent.mkdir()
        source.write_text("new_target_code = True\n", encoding="utf-8")
        _target, fence = self.governance_target_handoff(project, docs, ["src/new.py"])
        item.unlink()  # Local cache loss must not hide the authoritative remote claim.
        with self.assertRaisesRegex(RuntimeError, "claimed_source_violation"):
            delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), claimed["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_accepts_planned_story_generated_only_changes(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        item_props, _body = delivery_compile.split_note(item)
        story = docs / item_props["story_path"]
        relative = story.relative_to(project).as_posix()
        published_story = delivery_git.run_git(project, "show", published["integration"] + ":" + relative) + "\n"
        self.assertIn("status: planned", published_story)
        self.assertNotEqual(story.read_text(encoding="utf-8"), published_story)
        story.write_text(published_story, encoding="utf-8")
        target, _fence = self.governance_target_handoff(project, docs, [relative])
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(refreshed["plan_invalidated"])
        self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
        self.assertEqual(delivery_git.run_git(project, "show", refreshed["integration"] + ":" + relative) + "\n", published_story)

    def test_target_refresh_rejects_changed_pinned_source_and_operation_receipts(self):
        for kind in ("story", "operation", "dod", "missing_dod"):
            with self.subTest(kind=kind):
                project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                props, _body = delivery_compile.split_note(item)
                path = (docs / props["story_path"] if kind == "story" else
                        docs / "delivery/definition-of-done.md" if kind in {"dod", "missing_dod"} else
                        operation_compile.contract_path(docs, "verification"))
                if kind == "missing_dod":
                    path.unlink()
                else:
                    path.write_text(path.read_text(encoding="utf-8") + "\nChanged approved input.\n", encoding="utf-8")
                _target, fence = self.governance_target_handoff(project, docs, [path.relative_to(project).as_posix()])
                item.unlink()  # The remote pin must remain enforced without local Item files.
                with self.assertRaises(RuntimeError) as failure:
                    delivery_git.refresh_target(project, "DLV-001")
                if kind != "missing_dod":
                    self.assertIn("changed a pinned source or Operation receipt", str(failure.exception))
                refs = delivery_git.canonical_refs("DLV-001")
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_preserves_legacy_operation_pins_after_relation_rendering(self):
        project, docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            runtime=True, legacy_operation_receipts=True)
        approved = {kind: operation_compile.parse(operation_compile.contract_path(docs, kind))[0]
                    for kind in ("verification", "environment")}
        pinned_item, _body = delivery_compile.split_note(item)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        paths = [operation_compile.contract_path(docs, kind).relative_to(project).as_posix()
                 for kind in approved]
        # Owning candidate rendering removes the old generated block and regenerates
        # projections; the target consumer must still verify each original pin.
        target, _fence = self.governance_target_handoff(project, docs, paths)
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(refreshed["plan_invalidated"])
        self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "checkout"
            delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
            delivery_git.run_git(clone, "checkout", "-q", "--detach", refreshed["integration"])
            candidate_docs = clone / "workspace/docs"
            self.assertEqual(delivery_compile.item_operation_findings(candidate_docs, pinned_item), [])
            for kind, expected in approved.items():
                receipt, errors = operation_compile.check_contract(candidate_docs, kind)
                self.assertEqual(errors, [])
                self.assertTrue(receipt["current"])
                self.assertEqual(receipt["source_hash"], expected["source_hash"])
                actual, _body = operation_compile.parse(operation_compile.contract_path(candidate_docs, kind))
                self.assertEqual((actual["revision"], actual["approved_at_utc"]),
                                 (expected["revision"], expected["approved_at_utc"]))
                self.assertEqual(pinned_item[kind + "_contract_hash"], expected["source_hash"])
        for kind in approved:
            path = operation_compile.contract_path(docs, kind)
            path.write_text(path.read_text() + "\nChanged authored operation behavior.\n", encoding="utf-8")
        carrier = "refs/heads/governance-input"
        delivery_git.atomic_push(project, "origin", [(carrier, delivery_git.remote_oid(project, "origin", carrier), "")])
        self.governance_target_handoff(project, docs, paths)
        refs_before = delivery_git.run_git(project, "ls-remote", "origin")
        with self.assertRaisesRegex(RuntimeError, "changed a pinned source or Operation receipt"):
            delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), refs_before)

    def test_stale_integrated_item_cannot_reopen_after_target_refresh(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.governance_target_handoff(project, docs)
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        delivery_git.refresh_target(project, "DLV-001")
        with self.assertRaisesRegex(RuntimeError, "Item does not contain"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), integrated["item"])
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_activation_target_race_quiesces_before_writer_receipt_or_worktree(self):
        for action in ("start", "resume", "reopen", "takeover"):
            with self.subTest(action=action):
                project, _docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                delivery_git.publish_execution_plan(project, "DLV-001")
                delivery_git.claim_items(project, "DLV-001")
                if action != "start":
                    active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
                    if action == "resume":
                        delivery_git.pause_item(project, "DLV-001", "AUTH-01")
                    elif action == "reopen":
                        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
                        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
                        delivery_git.push_item(project, "DLV-001", "AUTH-01")
                        delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
                original_push = delivery_git.atomic_push
                advanced = []

                def race_target(root, remote, updates):
                    if not advanced:
                        previous = delivery_git.remote_oid(root, remote, "refs/heads/main")
                        (root / "README.md").write_text("Concurrent target movement\n", encoding="utf-8")
                        target = delivery_git.commit_tree(root, previous, ["README.md"], "Advance target during activation", {})
                        original_push(root, remote, [("refs/heads/main", previous, target)])
                        advanced.append(target)
                    return original_push(root, remote, updates)

                verb = getattr(delivery_git, action + "_item")
                with mock.patch.object(delivery_git, "atomic_push", side_effect=race_target):
                    with self.assertRaisesRegex(RuntimeError, "target advanced after Item activation"):
                        verb(project, "DLV-001", "AUTH-01", **({"confirm": True} if action == "takeover" else {}))
                self.assertEqual(len(advanced), 1)
                refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
                current = delivery_git.remote_oid(project, "origin", refs["item"])
                props, _body = delivery_git.split_remote_note(project, current,
                    item.relative_to(project).as_posix(), delivery_compile.split_note)
                self.assertEqual(props["status"], "paused")
                self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
                self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
                self.assertFalse(delivery_git.worktree_paths(project, "DLV-001", "AUTH-01")["item"].exists())

    def test_merge_candidate_preserves_disjoint_additions_and_rejects_conflicts(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        base = delivery_git.run_git(project, "rev-parse", "HEAD")
        (project / "left.txt").write_text("Integration-only content\n", encoding="utf-8")
        left = delivery_git.commit_tree(project, base, ["left.txt"], "Left addition", {})
        (project / "right.txt").write_text("Target-only content\n", encoding="utf-8")
        right = delivery_git.commit_tree(project, base, ["right.txt"], "Right addition", {})
        index = (project / ".git/index").read_bytes()
        merged = delivery_git.merge_candidate(project, left, right, "Merge disjoint additions", {})
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":left.txt"), "Integration-only content")
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":right.txt"), "Target-only content")
        self.assertEqual(delivery_git.run_git(project, "show", "-s", "--format=%P", merged), left + " " + right)
        (project / "README.md").write_text("Left edit\n", encoding="utf-8")
        left_conflict = delivery_git.commit_tree(project, base, ["README.md"], "Left edit", {})
        (project / "README.md").write_text("Right edit\n", encoding="utf-8")
        right_conflict = delivery_git.commit_tree(project, base, ["README.md"], "Right edit", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, left_conflict, right_conflict, "Reject conflict", {})
        tree = delivery_git.run_git(project, "rev-parse", base + "^{tree}")
        orphan = delivery_git.run_git(project, "commit-tree", tree, "-m", "Unrelated history")
        with self.assertRaisesRegex(RuntimeError, "one unambiguous common base"):
            delivery_git.merge_candidate(project, left, orphan, "Reject unrelated history", {})
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), base)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), base)

    def test_target_refresh_rejects_ambiguous_merge_bases_before_ref_updates(self):
        project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        base = published["integration"]
        left = delivery_git.commit_tree(project, base, [], "Left history", {})
        right = delivery_git.commit_tree(project, base, [], "Right history", {})
        integration = delivery_git.merge_candidate(project, left, right, "Integration history", {})
        target = delivery_git.merge_candidate(project, right, left, "Target history", {})
        refs = delivery_git.canonical_refs("DLV-001")
        old_target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        delivery_git.atomic_push(project, "origin", [(refs["integration"], base, integration),
                                                    ("refs/heads/main", old_target, target)])
        before = delivery_git.run_git(project, "ls-remote", "origin")
        with self.assertRaisesRegex(RuntimeError, "one unambiguous"):
            delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

    def test_governance_refresh_keeps_integration_on_exact_fence_lease_failure(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        self.governance_target_handoff(project, docs)
        original_push = delivery_git.atomic_push
        advanced = {}

        def race_fence(root, remote, updates):
            ref, fence, values = delivery_git._fence_context(root, remote)
            child = delivery_git._fence_child(root, fence, values, "Concurrent coordinator update")
            original_push(root, remote, [(ref, fence, child)])
            advanced["fence"] = child
            return original_push(root, remote, updates)

        with mock.patch.object(delivery_git, "atomic_push", side_effect=race_fence):
            with self.assertRaises(RuntimeError):
                delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), advanced["fence"])

    def test_execution_publication_includes_only_exact_bound_operation_contracts(self):
        for runtime in (True, False):
            with self.subTest(runtime=runtime):
                project, docs, directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts(runtime)
                target = delivery_git.run_git(project, "rev-parse", "HEAD")
                scope_hash = delivery_compile.split_note(directory / "delivery.md")[0]["scope_hash"]
                unrelated = docs / "operation/local-notes.md"
                unrelated.write_text("# Unpublished local notes\n", encoding="utf-8")
                draft_environment = delivery_git.run_git(project, "show", reserved["integration"] + ":workspace/docs/operation/environment-contract.md")
                self.assertIn("status: draft", draft_environment)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                with tempfile.TemporaryDirectory() as temporary:
                    clone = Path(temporary) / "checkout"
                    delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                    delivery_git.run_git(clone, "checkout", "-q", "--detach", published["integration"])
                    candidate_docs = clone / "workspace/docs"
                    candidate_item = candidate_docs / item.relative_to(docs)
                    item_props, _body = delivery_compile.split_note(candidate_item)
                    self.assertEqual(delivery_compile.item_operation_findings(candidate_docs, item_props), [])
                    self.assertEqual(delivery_compile.delivery_findings(candidate_docs, "DLV-001")[1], [])
                    for kind in ("verification", "environment") if runtime else ("verification",):
                        receipt, errors = operation_compile.check_contract(candidate_docs, kind)
                        self.assertEqual(errors, [])
                        self.assertTrue(receipt["current"])
                        self.assertEqual(item_props[kind + "_contract_hash"], receipt["source_hash"])
                        actual = operation_compile.contract_path(candidate_docs, kind).read_text(encoding="utf-8")
                        expected = operation_compile.contract_path(docs, kind).read_text(encoding="utf-8")
                        self.assertEqual(delivery_compile.without_generated_relations(actual),
                                         delivery_compile.without_generated_relations(expected))
                    if not runtime:
                        self.assertNotIn("environment_contract_ref", item_props)
                        self.assertEqual(operation_compile.contract_path(candidate_docs, "environment").read_text(encoding="utf-8").strip(),
                                         draft_environment)
                    self.assertFalse((candidate_docs / unrelated.relative_to(docs)).exists())
                    self.assertEqual(delivery_compile.split_note(candidate_docs / directory.relative_to(docs) / "delivery.md")[0]["scope_hash"], scope_hash)
                    findings = []
                    vault_check.check_relation_projections(vault_check.build_vault(candidate_docs, vault_check.load_policy(vault_check.DEFAULT_POLICY)), findings)
                    self.assertEqual(findings, [])
                    self.assertEqual(delivery_git.run_git(clone, "status", "--porcelain"), "")
                self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), target)
                self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
                self.assertEqual(unrelated.read_text(encoding="utf-8"), "# Unpublished local notes\n")

    def test_execution_publication_keeps_a_terminal_item_on_its_verified_revision(self):
        """A closed Item's binding names history, not a stale current receipt."""
        project, _docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts()
        props, body = delivery_compile.split_note(item)
        superseded = dict(props)
        superseded["verification_contract_hash"] = "sha256:" + "0" * 64

        def write(status):
            value = dict(superseded, status=status)
            value["tags"] = [tag for tag in props.get("tags", [])
                             if not str(tag).startswith("status/")] + [f"status/{status}"]
            delivery_compile.atomic_text(item, delivery_compile.frontmatter(value, body))

        write("integrated")
        with mock.patch.object(delivery_git, "atomic_push") as push:
            delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_called()

        # The same binding on an open Item is genuine staleness and still fails.
        write("active")
        with mock.patch.object(delivery_git, "atomic_push") as push:
            with self.assertRaisesRegex(RuntimeError, "binding is stale or missing"):
                delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_not_called()

    def test_execution_publication_rejects_invalid_operation_bindings_before_ref_changes(self):
        project, docs, _directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        original = item.read_bytes()
        props, body = delivery_compile.split_note(item)
        refs = delivery_git.canonical_refs("DLV-001")
        for field, value in (("verification_contract_ref", "../../README"),
                             ("verification_contract_hash", "sha256:" + "0" * 64),
                             ("verification_contract_ref", None),
                             ("environment_contract_hash", None)):
            with self.subTest(field=field, value=value):
                invalid = dict(props)
                if value is None:
                    invalid.pop(field)
                else:
                    invalid[field] = value
                delivery_compile.atomic_text(item, delivery_compile.frontmatter(invalid, body))
                with mock.patch.object(delivery_git, "atomic_push") as push:
                    with self.assertRaisesRegex(RuntimeError, "binding is stale or missing"):
                        delivery_git.publish_execution_plan(project, "DLV-001")
                    push.assert_not_called()
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])
        item.write_bytes(original)
        contract = operation_compile.contract_path(docs, "verification")
        saved_contract = contract.read_bytes()
        for missing in (True, False):
            with self.subTest(missing_contract=missing):
                if missing:
                    contract.unlink()
                else:
                    contract.write_bytes(saved_contract + b"\nChanged approved contract.\n")
                with mock.patch.object(delivery_git, "atomic_push") as push:
                    with self.assertRaisesRegex(RuntimeError, "approved current verification contract"):
                        delivery_git.publish_execution_plan(project, "DLV-001")
                    push.assert_not_called()
                contract.write_bytes(saved_contract)
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_validates_staged_contract_after_local_check(self):
        project, docs, _directory, _item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        contract = operation_compile.contract_path(docs, "verification")
        original = contract.read_bytes()
        original_commit = delivery_git.commit_tree

        def change_contract_before_staging(*args, **kwargs):
            if kwargs.get("operation_bindings"):
                contract.write_bytes(original.replace(b"test_command: make test", b"test_command: make changed"))
            return original_commit(*args, **kwargs)

        with mock.patch.object(delivery_git, "commit_tree", side_effect=change_contract_before_staging), \
                mock.patch.object(delivery_git, "atomic_push") as push:
            with self.assertRaisesRegex(RuntimeError, "Delivery candidate Operation bindings are invalid"):
                delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_not_called()
        contract.write_bytes(original)
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_rejects_candidate_solution_drift_with_current_local_receipts(self):
        project, docs, _directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        decision = "workspace/docs/solution-design/decisions/fixture-api.md"
        previous = delivery_git.run_git(project, "show", reserved["integration"] + ":" + decision)
        drifted = delivery_git.commit_replacements(project, reserved["integration"],
            {decision: previous + "\n\nChanged remote Solution input.\n"}, "Change candidate Solution input", {})
        refs = delivery_git.canonical_refs("DLV-001")
        delivery_git.atomic_push(project, "origin", [(refs["integration"], reserved["integration"], drifted)])
        self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])
        self.assertEqual(delivery_compile.item_operation_findings(docs, delivery_compile.split_note(item)[0]), [])
        with self.assertRaisesRegex(RuntimeError, "Delivery candidate Operation bindings are invalid"):
            delivery_git.publish_execution_plan(project, "DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), drifted)
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_claim_and_start_use_global_slot(self):
        temporary, project = self.make_project()
        self.addCleanup(temporary.cleanup)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs)
        subprocess.run(["git", "-C", str(project), "add", "workspace"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "approved backlog"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        remote = project / "remote.git"
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication", "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        delivery_compile.init_delivery(init)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        delivery_git.reserve_delivery(project, "DLV-001")
        self.author_execution_topology(docs)
        delivery_compile.approve_execution(scope)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.refresh_target(project, "DLV-001")
        result = delivery_git.claim_items(project, "DLV-001")
        self.assertEqual(result["claims"], ["AUTH-01"])
        governance = docs / "delivery" / "governance" / "governance.md"
        before_governance = governance.read_text(encoding="utf-8")
        governance.write_text(before_governance.replace("max_parallel: 1", "max_parallel: 2"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "(?i)governance"):
            delivery_git.start_item(project, "DLV-001", "AUTH-01")
        governance.write_text(before_governance, encoding="utf-8")
        receipt, errors = delivery_governance.status(docs)
        self.assertEqual(errors, [])
        self.assertTrue(receipt.get("current"), receipt)
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
