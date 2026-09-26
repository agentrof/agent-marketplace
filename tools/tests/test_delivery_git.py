"""Gate 4 naming and read-only Git preflight tests."""

from __future__ import annotations

import contextlib
import io
import sys
import json
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import delivery_provider  # noqa: E402
import delivery_result  # noqa: E402
import operation_compile  # noqa: E402
import architecture_compile  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary, temporary_directory  # noqa: E402


def write_pull_request_workflow(project: Path) -> None:
    """Give a fixture repository the pull request workflow that execution approval requires."""
    workflow = project / ".github" / "workflows" / "tests.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on:\n  pull_request:\n", encoding="utf-8")


class WindowsVaultPath(type(Path())):
    """A local path whose path relative to another path of this type renders as on native Windows."""

    def relative_to(self, *other):
        relative = super().relative_to(*other)
        if other and isinstance(other[0], WindowsVaultPath):
            return PureWindowsPath(*relative.parts)
        return relative


def windows_vault_paths():
    """Render paths inside the Delivery vault as native Windows renders them.

    The files stay real. A path relative to the Git checkout keeps its
    separator: its Windows handling is #236.
    """
    docs_root = delivery_compile.docs_root
    return mock.patch.object(delivery_compile, "docs_root", lambda value: WindowsVaultPath(docs_root(value)))


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
        init_repository(project, initial_branch="main")
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
        write_pull_request_workflow(project)
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
        remote = project / "remote.git"
        init_repository(remote, bare=True)
        subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
        subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        return temporary, project

    def test_project_fixture_disables_automatic_git_maintenance(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
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

    def prepare_pr_intent(self, author_review=None):
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
        if author_review is not None:
            author_review(docs)
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

    def test_coordinator_refusals_report_their_finding_codes(self):
        """A coordinator refusal reaches the result envelope under its own code, with its words intact."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        root, remote = Path(temporary.name) / "project", Path(temporary.name) / "remote.git"
        init_repository(root)
        init_repository(remote, bare=True)
        delivery_git.run_git(root, "config", "user.email", "test@example.com")
        delivery_git.run_git(root, "config", "user.name", "Test")
        for name in ("base", "target"):
            (root / f"{name}.txt").write_text(name + "\n", encoding="utf-8")
            delivery_git.run_git(root, "add", f"{name}.txt")
            delivery_git.run_git(root, "commit", "-qm", name)
        base, target = delivery_git.run_git(root, "rev-parse", "HEAD^", "HEAD").split()
        delivery_git.run_git(root, "remote", "add", "origin", str(remote))
        delivery_git.run_git(root, "push", "-q", "origin", "main")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01", 1)
        missing = Path(temporary.name) / "missing-worktree"

        def fence(mode: str, fence_target: str) -> str:
            return ("Fence project\n\nAgentrof-Record: project-fence-v2\n"
                    f"Agentrof-Mode: {mode}\nAgentrof-Target: {fence_target}\n")

        for code, message, refusal in (
            ("DELIVERY_SLOT_INVALID", "slot must be a positive number rendered with at least three digits",
             lambda: delivery_git.slot_key(0)),
            ("DELIVERY_CANCELLATION_INVALID", "unsupported cancellation disposition",
             lambda: delivery_git.cancellation_projection(
                 "DLV-001", "none", "Stop", {"AUTH-01": {"disposition": "paused", "tip": "none"}}, target)),
            ("DELIVERY_TARGET_IMPACT_INVALID", "target impact requires exact previous and current target OIDs",
             lambda: delivery_git.target_impact_hash("DLV-001", "none", target, {})),
            ("DELIVERY_FENCE_MISSING", "remote ref is absent: refs/heads/agentrof/fence",
             lambda: delivery_git.remote_oid(root, "origin", refs["fence"])),
            ("DELIVERY_ITEM_REF_MISSING", "remote ref is absent: refs/heads/agentrof/items/auth-01",
             lambda: delivery_git.remote_oid(root, "origin", refs["item"])),
            ("DELIVERY_ITEM_SLOT_MISSING", "remote ref is absent: refs/heads/agentrof/slots/001",
             lambda: delivery_git.remote_oid(root, "origin", refs["slot"])),
            ("DELIVERY_FENCE_MODE", "writer readiness requires an open Fence",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("upgrade", target), target)),
            ("DELIVERY_TARGET_DRIFT", "target advanced; refresh the Delivery before Item activation",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("open", base), target)),
            ("DELIVERY_TARGET_CONVERGENCE_REQUIRED", "Integration does not contain the current target",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("open", target), base)),
            ("DELIVERY_WORKTREE_UNSAFE", f"Item worktree is missing: {missing}",
             lambda: delivery_git.worktree_is_clean_and_at(root, missing, target)),
            ("DELIVERY_LOCAL_REF_DIVERGED", "Item worktree HEAD differs from the remote Item tip",
             lambda: delivery_git.worktree_is_clean_and_at(root, root, base)),
            ("DELIVERY_WRITER_RECEIPT_MISSING", "push-item requires this machine's verified Item writer receipt",
             lambda: delivery_git.active_writer_receipt(root, "DLV-001", "AUTH-01", target, refs["slot"])),
        ):
            with self.subTest(code=code):
                with self.assertRaises((RuntimeError, ValueError)) as refused:
                    refusal()
                result = delivery_result.from_raw("refusal", {"ok": False, "errors": [str(refused.exception)]})
                self.assertEqual([(finding["code"], finding["message"]) for finding in result["findings"]],
                                 [(code, message)])

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
            remove_temporary(temporary)
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
            remove_temporary(temporary)
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
            remove_temporary(temporary)
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
            remove_temporary(temporary)
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
            remove_temporary(temporary)

    def test_merge_pr_reports_a_red_check_as_a_required_check_failure(self):
        """The provider's own green-check rule refuses before the merge call, under its finding code."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            state: dict = {}

            class RedCheckProvider(self.fake_provider_type(state), delivery_provider.GitHubProvider):
                require_green_checks = delivery_provider.GitHubProvider.require_green_checks

                def _record(self, head: str, base: str) -> dict:
                    record = super()._record(head, base)
                    record["statusCheckRollup"] = [
                        {"__typename": "CheckRun", "name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"__typename": "StatusContext", "context": "ci/deploy", "state": "PENDING"},
                    ]
                    return record

            with mock.patch("delivery_provider.GitHubProvider", RedCheckProvider):
                delivery_git.open_pr(project, "DLV-001")
                target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = delivery_git.main(["merge-pr", "--project-root", str(project), "--delivery", "DLV-001"])
            findings = json.loads(output.getvalue())["findings"]
            self.assertEqual(exit_code, 1)
            self.assertEqual([(finding["code"], finding["message"]) for finding in findings], [
                ("DELIVERY_REQUIRED_CHECK_FAILED", "GitHub required check is not green: ci/deploy (PENDING)"),
            ])
            self.assertNotIn("merged", state)
            self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
        finally:
            remove_temporary(temporary)

    def merge_pr_findings(self, project: Path, provider_type) -> list[tuple[str, str]]:
        """Run merge-pr through its command boundary and return the refusal's findings."""
        output = io.StringIO()
        with mock.patch("delivery_provider.GitHubProvider", provider_type), contextlib.redirect_stdout(output):
            exit_code = delivery_git.main(["merge-pr", "--project-root", str(project), "--delivery", "DLV-001"])
        envelope = json.loads(output.getvalue())
        self.assertEqual((exit_code, envelope["ok"]), (1, False))
        return [(finding["code"], finding["message"]) for finding in envelope["findings"]]

    def test_merge_pr_refusals_report_their_finding_codes(self):
        """A PR that cannot close the Delivery is refused under the code that says why, before any merge."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
                delivery_git.open_pr(project, "DLV-001")
            integration = delivery_git.remote_oid(
                project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
            fake_provider_type = self.fake_provider_type

            def provider(state, listed=None, viewed=None):
                """The provider double whose listed PR, and its view before the merge call, differ as given."""
                class Variant(fake_provider_type(state)):
                    def _record(self, head: str, base: str) -> dict:
                        return {**super()._record(head, base), **(listed or {})}

                    def inspect_pull_request(self, url: str) -> dict:
                        return {**super().inspect_pull_request(url), **(viewed or {})}
                return Variant

            merged = {"created": True, "merged": True}
            for code, message, provider_type in (
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "exactly one lifecycle PR is required", provider({})),
                ("DELIVERY_PR_STATE_INVALID", "Delivery PR head/base/state is not mergeable",
                 provider({"created": True}, listed={"state": "CLOSED"})),
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "Delivery PR head/base/state is not mergeable",
                 provider({"created": True}, listed={"baseRefName": "release"})),
                ("DELIVERY_PR_STATE_INVALID", "Delivery PR changed before the merge call",
                 provider({"created": True}, viewed={"isDraft": True})),
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "Delivery PR changed before the merge call",
                 provider({"created": True}, viewed={"headRefOid": "0" * 40})),
                ("DELIVERY_MERGE_PROOF_INVALID", "provider did not return an exact merge commit",
                 provider({**merged, "merge": None})),
                ("DELIVERY_MERGE_PROOF_INVALID", "provider merge is not present in the exact target ancestry",
                 provider({**merged, "merge": integration})),
            ):
                with self.subTest(code=code, message=message):
                    self.assertEqual(self.merge_pr_findings(project, provider_type), [(code, message)])
                    self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
            # A fast-forward puts the reviewed head itself on the target: no merge commit binds it.
            delivery_git.atomic_push(project, "origin", [("refs/heads/main", target, integration)])
            self.assertEqual(self.merge_pr_findings(project, provider({**merged, "merge": integration})), [
                ("DELIVERY_MERGE_POLICY_INVALID",
                 "provider merge is not an exact two-parent merge of the reviewed Integration"),
            ])
        finally:
            remove_temporary(temporary)

    def test_review_publication_regenerates_the_delivery_projections(self):
        """The published Review is a new note: the Integration's map and relation
        projections are derived from the published tree, never taken from a local
        map that lags the Integration."""
        marker = "<!-- delivery_compile.py: generated deliveries -->"

        def lagging_map(docs):
            path = docs / "maps" / "delivery.md"
            path.write_text(path.read_text().split(marker)[0] + marker + "\n", encoding="utf-8")

        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent(author_review=lagging_map)
        try:
            integration = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            published = delivery_git.run_git(project, "show", f"{integration}:workspace/docs/maps/delivery.md")
            self.assertIn("|DLV-001]] — `review`", published)
            tree = delivery_git.run_git(project, "rev-parse", integration + "^{tree}")
            self.assertEqual(delivery_git.delivery_projection_changes(project, tree), {})
        finally:
            remove_temporary(temporary)

    def test_invalidated_review_mirrors_its_status_in_its_tags(self):
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
                delivery_git.open_pr(project, "DLV-001")
            delivery_git.invalidate_delivery_review(project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)
            review_path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            invalidated = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            props, body = delivery_git.split_remote_note(
                project, invalidated, review_path.relative_to(project).as_posix(), delivery_compile.split_note)
            self.assertEqual(props["status"], "changes_requested")
            self.assertEqual(set(props["tags"]), {"doc/delivery-review", "status/changes-requested"})
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        finally:
            remove_temporary(temporary)

    def test_published_review_and_pr_carry_the_authored_delivery_review(self):
        authored = {"Scope Disposition": "AUTH-01 delivered as planned.",
                    "Deviations": "The owner added session expiry on 2026-01-01.",
                    "Lessons and Follow-up": "Rotate the fixture keys."}

        def author(docs):
            path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            path.write_text(delivery_compile.frontmatter(
                {"type": "delivery-review", "status": "draft"},
                delivery_compile.body_for("delivery-review", "Draft review", authored)), encoding="utf-8")

        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent(author_review=author)
        try:
            review_path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            published = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            props, body = delivery_git.split_remote_note(
                project, published, review_path.relative_to(project).as_posix(), delivery_compile.split_note)
            for title, text in authored.items():
                self.assertEqual(delivery_compile.section_bodies(body)[title], text)
            self.assertEqual(props["approval_hash"], delivery_compile.content_hash(
                props, body, exclude=delivery_compile.MUTABLE | {"approval_hash"}))
            state: dict = {}
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                delivery_git.open_pr(project, "DLV-001")
            for text in authored.values():
                self.assertIn(text, state["body"])
        finally:
            remove_temporary(temporary)

    @staticmethod
    def reported(docs: Path) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery_compile.status(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}))
        return code, json.loads(output.getvalue())

    def reported_status(self, docs: Path) -> str:
        code, reported = self.reported(docs)
        self.assertEqual((code, reported["ok"]), (0, True), reported)
        return reported["status"]

    def merge_and_integration_checkouts(self, project: Path) -> tuple[Path, Path]:
        """Clone the remote, merge the PR head into its main with --no-ff and keep the Integration beside it."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        merged = Path(temporary.name) / "main"
        integration = Path(temporary.name) / "integration"
        head = "origin/" + delivery_git.short_refs("DLV-001")["integration"]
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(merged)], check=True)
        subprocess.run(["git", "-C", str(merged), "worktree", "add", "-q", "--detach", str(integration), head], check=True)
        subprocess.run(["git", "-C", str(merged), "-c", "user.email=test@example.com", "-c", "user.name=Test",
                        "merge", "-q", "--no-ff", "-m", "Merge pull request #17", head], check=True)
        return merged, integration

    def revise_selected_story(self, docs: Path) -> None:
        """Approve a later backlog revision that changes the selected Story's bytes."""
        story = docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        props, body = backlog_compile.parse_front_matter(story)
        revised = body.replace(
            "Preserve the approved API boundary and avoid delivery-state metadata.",
            "Preserve the approved API boundary, cover the session scenario and avoid delivery-state metadata.")
        self.assertNotEqual(revised, body)
        story.write_text(backlog_compile.front_matter(props, revised), encoding="utf-8")
        props["source_hash"] = backlog_compile.digest(story)
        story.write_text(backlog_compile.front_matter(props, revised), encoding="utf-8")
        record, errors = backlog_compile.collect(docs)
        self.assertEqual(errors, [])
        backlog = docs / "backlog" / "backlog.md"
        backlog_props, backlog_body = backlog_compile.parse_front_matter(backlog)
        backlog_props["package_hash"] = backlog_compile.package_digest(
            docs, backlog_compile.package_paths(record, docs))
        backlog.write_text(backlog_compile.front_matter(backlog_props, backlog_body), encoding="utf-8")

    def test_recorded_pr_moves_the_delivery_to_awaiting_merge_in_the_pr_head(self):
        temporary, project, docs, _product_tip, intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        package = delivery_compile.find_delivery(docs, "DLV-001")
        relative = package.relative_to(project).as_posix()
        provider = self.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            opened = delivery_git.open_pr(project, "DLV-001")
        head = opened["integration"]
        local = delivery_compile.split_note(package / "delivery.md")
        published = delivery_git.split_remote_note(project, head, relative + "/delivery.md", delivery_compile.split_note)
        for props, body in (local, published):
            self.assertEqual(props["status"], "awaiting_merge")
            self.assertEqual(set(props["tags"]), {"doc/delivery", "status/awaiting-merge"})
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        review, review_body = delivery_git.split_remote_note(
            project, head, relative + "/delivery-review.md", delivery_compile.split_note)
        self.assertEqual(review["pull_request_url"], opened["pull_request_url"])
        self.assertEqual(review["approval_hash"], delivery_compile.content_hash(
            review, review_body, exclude=delivery_compile.MUTABLE | {"approval_hash"}))
        # The PR head changes only the Review, the Delivery status and the map that mirrors it.
        self.assertEqual(set(delivery_git.run_git(project, "diff", "--name-only", intent["intent"], head).splitlines()),
                         {relative + "/delivery-review.md", relative + "/delivery.md", "workspace/docs/maps/delivery.md"})
        self.assertIn("|DLV-001]] — `awaiting_merge`",
                      delivery_git.run_git(project, "show", head + ":workspace/docs/maps/delivery.md"))
        self.assertEqual(delivery_git.delivery_projection_changes(project, head), {})
        self.assertEqual(self.reported_status(docs), "awaiting_merge")
        recorded = (package / "delivery.md").read_bytes()
        with mock.patch("delivery_provider.GitHubProvider", provider):
            again = delivery_git.open_pr(project, "DLV-001")
        self.assertTrue(again["reused"])
        self.assertFalse(again["provider_call"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]), head)
        self.assertEqual((package / "delivery.md").read_bytes(), recorded)

    def test_merging_the_pr_head_reports_merged_and_keeps_the_generated_map(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            head = delivery_git.open_pr(project, "DLV-001")["integration"]
        merged, integration = self.merge_and_integration_checkouts(project)
        self.assertEqual(delivery_git.run_git(merged, "rev-parse", "HEAD^2"), head)
        self.assertEqual(self.reported_status(merged / "workspace/docs"), "merged")
        self.assertEqual(self.reported_status(integration / "workspace/docs"), "awaiting_merge")
        # The map renders tracked bytes only, so the target branch keeps the
        # Integration's map and a fresh render there changes nothing.
        map_path = merged / "workspace/docs/maps/delivery.md"
        self.assertIn("|DLV-001]] — `awaiting_merge`", map_path.read_text(encoding="utf-8"))
        self.assertEqual(delivery_git.delivery_projection_changes(merged, "HEAD"), {})
        delivery_compile.render_map(merged / "workspace/docs")
        self.assertEqual(delivery_git.run_git(merged, "status", "--porcelain"), "")

    def test_merged_delivery_keeps_its_pinned_baseline_after_a_story_revision(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        merged, integration = (checkout / "workspace/docs" for checkout in self.merge_and_integration_checkouts(project))
        for docs in (merged, integration):
            self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])
            self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(merged, "DLV-001")[1], [])
        _root, stale = delivery_compile.delivery_findings(integration, "DLV-001")
        self.assertTrue(any("story_source_hash is stale" in finding for finding in stale), stale)
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog", stale)

    def test_merge_reached_through_a_second_parent_proves_the_delivery_merged(self):
        """A branch that later merges the target, as refresh-target does for the next
        Delivery's Integration, reaches the PR merge only through a second parent."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        merged, _integration = self.merge_and_integration_checkouts(project)
        refreshed = merged.parent / "refreshed"
        identity = ["-c", "user.email=test@example.com", "-c", "user.name=Test"]
        subprocess.run(["git", "-C", str(merged), "worktree", "add", "-q", "-b", "next", str(refreshed), "HEAD^1"], check=True)
        (refreshed / "next.txt").write_text("next Delivery work\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(refreshed), "add", "next.txt"], check=True)
        subprocess.run(["git", "-C", str(refreshed), *identity, "commit", "-qm", "Next Delivery work"], check=True)
        subprocess.run(["git", "-C", str(refreshed), *identity, "merge", "-q", "--no-ff", "-m", "Refresh target", "main"], check=True)
        proof = delivery_git.run_git(merged, "rev-parse", "HEAD")
        self.assertNotIn(proof, delivery_git.run_git(refreshed, "rev-list", "--first-parent", "HEAD").split())
        self.assertIn(proof, delivery_git.run_git(refreshed, "rev-list", "HEAD").split())
        docs = refreshed / "workspace/docs"
        self.assertEqual(self.reported_status(docs), "merged")
        self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])

    def test_delivery_recorded_while_in_review_is_merged_by_its_pr_merge(self):
        """A PR recorded before the record moved the Delivery to awaiting_merge left
        it in review; a merge of that recorded head closes it all the same."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})), \
                mock.patch("delivery_compile.pr_recorded_props", return_value=None):
            delivery_git.open_pr(project, "DLV-001")
        merged, integration = (checkout / "workspace/docs" for checkout in self.merge_and_integration_checkouts(project))
        package = delivery_compile.find_delivery(merged, "DLV-001")
        self.assertEqual(delivery_compile.split_note(package / "delivery.md")[0]["status"], "review")
        self.assertEqual(self.reported_status(merged), "merged")
        self.assertEqual(self.reported_status(integration), "review")
        for docs in (merged, integration):
            self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(merged, "DLV-001")[1], [])
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog",
                      delivery_compile.delivery_findings(integration, "DLV-001")[1])

    def test_reopen_after_the_pr_record_does_not_prove_a_merge(self):
        """reopen-item writes a two-parent control commit whose second parent is the
        recorded PR head. Only the provider's merge of the re-recorded head closes
        the Delivery."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            record = delivery_git.open_pr(project, "DLV-001")["integration"]
        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.run_git(project, "rev-parse", reopened["item"] + "^2"), record)
        worktree = Path(reopened["worktree"])
        self.assertEqual(self.reported_status(worktree / "workspace/docs"), "awaiting_merge")
        self.commit_item_product_change(str(worktree), "def authenticate():\n    return 'v2'\n")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")["integration"]
        view = Path(temporary.name) / "integration-view"
        subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", "--detach", str(view), integrated], check=True)
        self.assertEqual(self.reported_status(view / "workspace/docs"), "awaiting_merge")
        review = type("Args", (), {"docs": str(docs), "delivery": "DLV-001",
                                   "reviewed_commit": integrated, "reviewed_integration_commit": integrated})
        self.assertEqual(delivery_compile.approve_review(review), 0)
        delivery_git.publish_delivery_review(project, "DLV-001")
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            rerecorded = delivery_git.open_pr(project, "DLV-001")
            delivery_git.merge_pr(project, "DLV-001")
        self.assertTrue(rerecorded["adopted"])
        checkout = Path(temporary.name) / "main-after-merge"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(checkout)], check=True)
        self.assertEqual(delivery_git.run_git(checkout, "rev-parse", "HEAD^2"), rerecorded["integration"])
        self.assertEqual(self.reported_status(checkout / "workspace/docs"), "merged")
        self.revise_selected_story(view / "workspace/docs")
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog",
                      delivery_compile.delivery_findings(view / "workspace/docs", "DLV-001")[1])

    def test_merge_state_git_cannot_evaluate_is_reported(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, outside)
        unrecorded = Path(outside.name) / "unrecorded"
        shutil.copytree(project / "workspace", unrecorded / "workspace")
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        # A Review that records no PR leaves nothing to prove, so no history is needed.
        with mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": outside.name}):
            self.assertEqual(self.reported_status(unrecorded / "workspace/docs"), "review")
        merged, _integration = self.merge_and_integration_checkouts(project)
        shallow = merged.parent / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "-c", "gc.auto=0", merged.as_uri(), str(shallow)], check=True)
        exported = merged.parent / "exported"
        shutil.copytree(merged / "workspace", exported / "workspace")
        run = subprocess.run

        def failing_walk(command, *args, **kwargs):
            if "--merges" in command:
                return subprocess.CompletedProcess(command, 128, "", "fatal: simulated walk failure\n")
            return run(command, *args, **kwargs)

        cases = (
            (shallow, contextlib.nullcontext(),
             "Delivery merge state cannot be evaluated in a shallow clone; fetch the full history,"
             " for example with git fetch --unshallow"),
            (exported, mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": str(merged.parent)}),
             "Delivery merge state cannot be evaluated: "),
            (merged, mock.patch("delivery_compile.subprocess.run", side_effect=failing_walk),
             "Delivery merge state cannot be evaluated: fatal: simulated walk failure"),
        )
        for checkout, context, finding in cases:
            with self.subTest(checkout=checkout.name), context:
                docs = checkout / "workspace/docs"
                code, reported = self.reported(docs)
                self.assertEqual((code, reported["ok"], reported["status"]), (1, False, "awaiting_merge"))
                self.assertEqual(len(reported["errors"]), 1, reported)
                self.assertTrue(reported["errors"][0].startswith(finding), reported)
                self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], reported["errors"])
        self.assertEqual(self.reported_status(merged / "workspace/docs"), "merged")

    def test_cancelled_delivery_stays_cancelled_through_its_pr_record_and_merge(self):
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        relative = delivery_compile.find_delivery(docs, "DLV-001").relative_to(project).as_posix() + "/delivery.md"

        def published_status() -> str:
            head = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            return delivery_git.split_remote_note(project, head, relative, delivery_compile.split_note)[0]["status"]

        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            delivery_git.open_pr(project, "DLV-001")
            self.assertEqual(published_status(), "awaiting_merge")
            delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
            self.assertEqual(published_status(), "cancelled")
            rerecorded = delivery_git.open_pr(project, "DLV-001")
            self.assertTrue(rerecorded["adopted"])
            self.assertEqual(published_status(), "cancelled")
            delivery_git.merge_pr(project, "DLV-001")
        checkout = Path(temporary.name) / "main-after-merge"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(checkout)], check=True)
        self.assertEqual(delivery_git.run_git(checkout, "rev-parse", "HEAD^2"), rerecorded["integration"])
        self.assertEqual(self.reported_status(checkout / "workspace/docs"), "cancelled")

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
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project, initial_branch="main")
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"; (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
            self.approve_governance(docs)
            make_approved_backlog(docs)
            write_pull_request_workflow(project)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"; init_repository(remote, bare=True)
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
            integration = delivery_git.remote_oid(project, "origin", integration_ref)
            integration_message = delivery_git.commit_message(project, integration)
            self.assertEqual(delivery_git.trailer(integration_message, "Record"), "delivery-review-published-v1")
            # The cancellation Review is published like any other: the map and the
            # relation projections come from the published tree.
            published_map = delivery_git.run_git(project, "show", f"{integration}:workspace/docs/maps/delivery.md")
            self.assertIn("|DLV-001]] — `cancelled`", published_map)
            tree = delivery_git.run_git(project, "rev-parse", integration + "^{tree}")
            self.assertEqual(delivery_git.delivery_projection_changes(project, tree), {})

    def test_cancellation_review_links_stay_posix_on_a_host_with_backslash_separators(self):
        """The cancellation Review links its Delivery with forward slashes on every host (#228)."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(
            type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve scope")
        delivery_git.run_git(project, "push", "-q")
        delivery_git.reserve_delivery(project, "DLV-001")
        with windows_vault_paths():
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "Request withdrawn")
        review = delivery_git.run_git(project, "show", cancelled["review"]
                                      + ":workspace/docs/delivery/deliveries/dlv-001-auth/delivery-review.md")
        self.assertNotIn("\\", review)
        # derives_from and the Navigation section
        self.assertEqual(review.count("[[delivery/deliveries/dlv-001-auth/delivery|DLV-001]]"), 2, review)

    def test_ref_free_reservation_pushes_fence_and_integration_atomically(self):
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project, initial_branch="main")
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
            init_repository(remote, bare=True)
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
        self.addCleanup(remove_temporary, temporary)
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

    def test_integration_publication_projects_the_direct_map_render(self):
        """The Integration carries the map its own tree renders, ending with one newline."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": None,
                                 "goal": "SAML authentication", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(
            type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve scope")
        delivery_git.run_git(project, "push", "-q")
        integration = delivery_git.reserve_delivery(project, "DLV-001")["integration"]
        with tempfile.TemporaryDirectory() as clone_root:
            clone = Path(clone_root) / "checkout"
            delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
            delivery_git.run_git(clone, "checkout", "-q", "--detach", integration)
            map_path = clone / "workspace/docs/maps/delivery.md"
            # Text mode folds native CRLF from checkout or render, so the ending check holds on every OS.
            published = map_path.read_text(encoding="utf-8")
            delivery_compile.render_map(clone / "workspace/docs")
            self.assertEqual(map_path.read_text(encoding="utf-8"), published)
        self.assertIn("|DLV-001]]", published)
        self.assertTrue(published.endswith("\n") and not published.endswith("\n\n"), published[-60:])

    def test_publications_render_exact_candidate_without_local_sibling_or_dirty_note(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
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
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        if architecture:
            catalog = docs / "solution-design/_generated/component-catalog.json"
            catalog.parent.mkdir(parents=True, exist_ok=True)
            catalog.write_text(json.dumps({"components": [
                {"component_id": "api", "sourcing": "build", "code_path": "src/auth.py"},
                {"component_id": "other", "sourcing": "build", "code_path": "src/other.py"},
            ]}), encoding="utf-8")
            # The architecture stub derives from these components by canonical alias;
            # the relation contract can only resolve an alias that a solution-component
            # note owns, and integration now regenerates the projections that check it.
            for component_id in ("api", "other"):
                note = docs / "solution-design/components" / component_id / "component.md"
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(delivery_compile.frontmatter(
                    {"type": "solution-component", "title": component_id.title() + " component",
                     "component_id": component_id, "component_class": "application", "sourcing": "build",
                     "derives_from": ["[[solution-design/landscape|Solution Landscape]]"],
                     "tags": ["doc/solution-component"]},
                    f"# {component_id.title()} component\n\nFixture component.\n"), encoding="utf-8")
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

    def prepare_stamped_architecture_item(self, before_publish=None):
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            runtime=False, architecture=True)
        if before_publish is not None:
            before_publish(project)
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

    def test_integration_merges_item_deletions_and_republished_evidence_drafts(self):
        integration_ref = "refs/heads/agentrof/deliveries/dlv-001"

        def carry_legacy_file(project):
            (project / "notes").mkdir()
            (project / "notes/legacy.txt").write_text("carried from the target\n", encoding="utf-8")
            base = delivery_git.remote_oid(project, "origin", integration_ref)
            candidate = delivery_git.commit_tree(project, base, ["notes/legacy.txt"], "Carry a legacy file", {})
            delivery_git.atomic_push(project, "origin", [(integration_ref, base, candidate)])

        project, worktree, item, active = self.prepare_stamped_architecture_item(before_publish=carry_legacy_file)
        # The Item removes a file the base carried: a trivial resolution any merge
        # makes, which a plain three-way read left unmerged.
        self.assertTrue((worktree / "notes/legacy.txt").is_file())
        delivery_git.run_git(worktree, "rm", "-q", "notes/legacy.txt")
        delivery_git.run_git(worktree, "commit", "-qm", "Retire the legacy file")
        product = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        # Meanwhile the Integration re-projected the Item's evidence drafts, as a plan
        # publication does; the sealed Item's own records must win that conflict.
        relative_reports = [(item.parent / name).relative_to(worktree).as_posix()
                            for name in ("code-review.md", "verification.md")]
        for relative in relative_reports:
            path = project / relative
            props, body = delivery_compile.split_note(path)
            path.write_text(delivery_compile.frontmatter(props, body + "\nRe-projected draft.\n"), encoding="utf-8")
        base = delivery_git.remote_oid(project, "origin", integration_ref)
        republished = delivery_git.commit_tree(project, base, relative_reports, "Republish evidence drafts", {},
                                               delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(integration_ref, base, republished)])
        authored = {}
        for name, report in (("code-review.md", "Reviewed the retirement of the legacy file."),
                             ("verification.md", "Executed the suite without the legacy file; it passed.")):
            path = item.parent / name
            props, body = delivery_compile.split_note(path)
            body += "\n\n" + report + "\n"
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            authored[name] = body.rstrip()
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        tree = delivery_git.run_git(project, "ls-tree", "-r", "--name-only", integrated["integration"]).splitlines()
        self.assertNotIn("notes/legacy.txt", tree)
        self.assertIn("src/auth.py", tree)
        for name, body in authored.items():
            relative = (item.parent / name).relative_to(worktree).as_posix()
            props, actual = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            self.assertEqual(actual, body)
            self.assertEqual(props["reviewed_commit" if name == "code-review.md" else "verified_commit"], product)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], item.relative_to(worktree).as_posix(), delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})

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

    def republish_integration_plan(self, project, worktree, item):
        """Republish the active Item's plan on the Integration, as a plan revision does."""
        integration_ref = "refs/heads/agentrof/deliveries/dlv-001"
        package = item.parents[2]
        relative = {name: path.relative_to(worktree).as_posix() for name, path in (
            ("plan", package / "execution-plan.md"), ("scope", package / "delivery.md"), ("item", item))}
        base = delivery_git.remote_oid(project, "origin", integration_ref)
        for name, path in relative.items():
            props, body = delivery_git.split_remote_note(project, base, path, delivery_compile.split_note)
            body += "\n\nRepublished for the revised plan.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            (project / path).write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        republished = delivery_git.commit_tree(project, base, list(relative.values()), "Publish execution plan", {},
                                               delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(integration_ref, base, republished)])
        return republished, relative

    def converge_on_integration(self, worktree, item, integration, relative, *, merge=True, base=None):
        """Take the Integration into the Item as its writer does: its controls, its plan, a new base."""
        mine, _ = delivery_compile.split_note(item)
        if merge:
            subprocess.run(["git", "-C", str(worktree), "merge", "-q", "--no-ff", "--no-commit", integration],
                           capture_output=True, check=False)
        for name in ("plan", "scope"):
            (worktree / relative[name]).write_bytes(subprocess.run(
                ["git", "-C", str(worktree), "show", f"{integration}:{relative[name]}"],
                check=True, capture_output=True).stdout)
        published, body = delivery_git.split_remote_note(worktree, integration, relative["item"], delivery_compile.split_note)
        converged = dict(published)
        for key in ("status", "tags", "architecture_delta_hash"):
            converged[key] = mine[key]
        converged["integration_base_commit"] = base or integration
        converged["source_hash"] = delivery_compile.content_hash(converged, body)
        item.write_text(delivery_compile.frontmatter(converged, body), encoding="utf-8")
        delivery_git.run_git(worktree, "add", "-A", "workspace/docs")
        delivery_git.run_git(worktree, "commit", "-qm", "Take the republished Integration")
        return delivery_git.run_git(worktree, "rev-parse", "HEAD")

    def test_push_accepts_an_item_converged_on_its_republished_integration(self):
        """A target refresh leaves an Item that carries work to its writer. The
        writer takes the Integration, its republished plan and the Item's new base;
        publication accepts exactly that and integration seals it."""
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        integration, relative = self.republish_integration_plan(project, worktree, item)
        product = self.converge_on_integration(worktree, item, integration, relative)
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        for name in ("plan", "scope"):
            _props, body = delivery_git.split_remote_note(project, integrated["integration"], relative[name],
                                                          delivery_compile.split_note)
            self.assertIn("Republished for the revised plan.", body)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], relative["item"],
                                                     delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertIn("Republished for the revised plan.", body)
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))

    def test_push_refuses_what_a_converged_item_does_not_carry(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        integration, relative = self.republish_integration_plan(project, worktree, item)
        baseline = delivery_git.run_git(project, "ls-remote", "origin")

        def edit_note(path, key=None, value=None):
            props, body = delivery_compile.split_note(path)
            if key:
                props[key] = value
            else:
                body += "\nUnapproved change.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            delivery_git.run_git(worktree, "commit", "-qam", "Change a control after converging")

        beyond = "beyond its Architecture stamp and its converged Integration"
        base_rule = "integration base only forward"
        variants = {
            "plan_beyond_integration": (lambda: edit_note(worktree / relative["plan"]), "may not edit Delivery control"),
            "item_field_beyond_integration": (lambda: edit_note(item, "owner_role", "frontend_developer"), beyond),
            "item_body_beyond_integration": (lambda: edit_note(item), beyond),
            "lifecycle": (lambda: edit_note(item, "status", "paused"), beyond),
            "base_not_taken": (None, base_rule),
            "base_off_integration": (None, base_rule),
            "base_kept": (None, "may not edit Delivery control"),
        }
        for label, (tamper, refusal) in variants.items():
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                if label == "base_not_taken":
                    self.converge_on_integration(worktree, item, integration, relative, merge=False)
                elif label == "base_off_integration":
                    self.converge_on_integration(worktree, item, integration, relative, base=clean)
                elif label == "base_kept":
                    previous, _ = delivery_compile.split_note(item)
                    self.converge_on_integration(worktree, item, integration, relative,
                                                 base=previous["integration_base_commit"])
                else:
                    self.converge_on_integration(worktree, item, integration, relative)
                    tamper()
                if label != "lifecycle":
                    self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                with self.assertRaisesRegex(RuntimeError, refusal):
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
        self.addCleanup(remove_temporary, temporary)
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
        self.addCleanup(remove_temporary, temporary)
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

    def test_target_refresh_recovers_a_claim_stranded_by_an_earlier_refresh(self):
        """Convergence must not depend on the target having moved this time."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        stranded = delivery_git.remote_oid(project, "origin", refs["item"])
        self.governance_target_handoff(project, docs)
        delivery_git.refresh_target(project, "DLV-001")
        refreshed = delivery_git.remote_oid(project, "origin", refs["item"])

        # Put the claim back where a pre-repair refresh would have left it.
        delivery_git.atomic_push(project, "origin", [(refs["item"], refreshed, stranded)])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), stranded)
        again = delivery_git.refresh_target(project, "DLV-001")
        self.assertTrue(again["changed"])
        self.assertEqual(again["claims_refreshed"], [refs["item"]])
        recovered = delivery_git.remote_oid(project, "origin", refs["item"])
        self.assertNotEqual(recovered, stranded)
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.assertTrue(Path(started["worktree"]).is_dir())

        # A Delivery with nothing left behind reports no change.
        settled = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(settled["changed"])

    def test_activation_carries_the_currently_published_plan_into_the_item(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        relative_item = str(item.relative_to(project))
        contract = docs / "operation/verification-contract.md"
        relative_contract = str(contract.relative_to(project))
        first, _body = delivery_compile.split_note(Path(started["worktree"]) / relative_item)
        self.assertEqual(first["path_claims"], ["src/auth.py"])
        self.assertEqual(operation_compile.parse(Path(started["worktree"]) / relative_contract)[0]["revision"], 1)
        delivery_git.pause_item(project, "DLV-001", "AUTH-01")

        kind = type("Args", (), {"docs": str(docs), "kind": "verification"})
        self.assertEqual(operation_compile.revise(kind), 0)
        props, body = operation_compile.parse(contract)
        operation_compile.atomic_text(contract, operation_compile.render(props, body + "\n\nA later approved revision.\n"))
        self.assertEqual(operation_compile.approve(kind), 0)
        revised = operation_compile.parse(contract)[0]
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py", "src/session.py"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_execution(args), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")

        worktree = Path(delivery_git.resume_item(project, "DLV-001", "AUTH-01")["worktree"])
        current, _body = delivery_compile.split_note(worktree / relative_item)
        self.assertEqual(current["path_claims"], ["src/auth.py", "src/session.py"])
        self.assertEqual(current["verification_contract_hash"], revised["source_hash"])
        self.assertEqual(current["status"], "active")
        published = operation_compile.parse(worktree / relative_contract)[0]
        self.assertEqual(published["revision"], 2)
        self.assertEqual(published["source_hash"], revised["source_hash"])

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
        with self.assertRaisesRegex(RuntimeError, r"^DELIVERY_TARGET_SOURCE_VIOLATION: target changed claimed paths src/new\.py$"):
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

    def test_stale_integrated_item_reopens_on_the_refreshed_integration(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        target, _fence = self.governance_target_handoff(project, docs)
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), integrated["item"])
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        delivery_git.refresh_target(project, "DLV-001")
        integration_oid = delivery_git.remote_oid(project, "origin", refs["integration"])
        self.assertFalse(delivery_git.is_ancestor(project, target, integrated["item"]))
        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(reopened["status"], "active")
        self.assertTrue(delivery_git.is_ancestor(project, target, reopened["item"]))
        self.assertEqual(
            delivery_git.run_git(project, "rev-list", "--parents", "-n", "1", reopened["item"]).split()[1:],
            [integrated["item"], integration_oid],
        )
        message = delivery_git.commit_message(project, reopened["item"])
        self.assertEqual(delivery_git.trailer(message, "Record"), "item-reopen-v1")
        self.assertEqual(delivery_git.trailer(message, "Previous-Tip"), integrated["item"])
        self.assertEqual(delivery_git.trailer(message, "Integration-Base"), integration_oid)
        self.assertEqual(
            delivery_git.run_git(project, "diff", "--name-only", integration_oid, reopened["item"]).splitlines(),
            [Path(item).relative_to(Path(project)).as_posix()],
        )
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), reopened["item"])
        self.assertEqual(set(delivery_git.remote_slot_oids(project, "origin").values()), {reopened["item"]})
        self.assertTrue(Path(reopened["worktree"]).is_dir())
        second_product = self.commit_item_product_change(reopened["worktree"], "def authenticate():\n    return 'v2'\n")
        self.assertEqual(self.approve_item_evidence(reopened["worktree"]), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], second_product)
        integrated_again = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(
            subprocess.run(
                ["git", "show", f"{integrated_again['integration']}:src/auth.py"],
                cwd=project, check=True, capture_output=True, text=True,
            ).stdout,
            "def authenticate():\n    return 'v2'\n",
        )

    def test_fence_writers_carry_an_active_plan_revision_barrier(self):
        project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        fence_ref = delivery_git.canonical_refs("DLV-001")["fence"]

        begun = delivery_git.begin_plan_revision(project, "DLV-001")
        self.assertEqual(begun["barrier_kind"], "plan-revision")

        for writer in (
            lambda: delivery_git.publish_execution_plan(project, "DLV-001"),
            lambda: delivery_git.claim_items(project, "DLV-001"),
        ):
            writer()
            carried = delivery_git.commit_message(
                project, delivery_git.remote_oid(project, "origin", fence_ref))
            self.assertEqual(delivery_git.trailer(carried, "Barrier-Kind"), "plan-revision")
            self.assertEqual(delivery_git.trailer(carried, "Barrier-Epoch"), begun["barrier_epoch"])

        delivery_git.finish_plan_revision(project, "DLV-001")
        released = delivery_git.commit_message(
            project, delivery_git.remote_oid(project, "origin", fence_ref))
        self.assertEqual(delivery_git.trailer(released, "Barrier-Kind"), "none")
        self.assertEqual(delivery_git.trailer(released, "Barrier-Epoch"), "none")

    def test_sealed_item_reopens_after_reapproval_names_it_for_rebinding(self):
        project, docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        # The plan revision compiles the tracked package as the Integration sealed it.
        for name in ("item.md", "code-review.md", "verification.md"):
            relative = (directory / "items/auth-01" / name).relative_to(project).as_posix()
            props, body = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            delivery_compile.atomic_text(project / relative, delivery_compile.frontmatter(props, body))
        sealed, _body = delivery_compile.split_note(item)
        self.assertEqual(sealed["status"], "integrated")
        first = sealed["verification_contract_hash"]

        kind = type("Args", (), {"docs": str(docs), "kind": "verification"})
        self.assertEqual(operation_compile.revise(kind), 0)
        contract = docs / "operation/verification-contract.md"
        props, body = operation_compile.parse(contract)
        operation_compile.atomic_text(contract, operation_compile.render(props, body + "\n\nA later approved revision.\n"))
        self.assertEqual(operation_compile.approve(kind), 0)
        second = operation_compile.parse(contract)[0]["source_hash"]
        self.assertNotEqual(second, first)
        with self.assertRaisesRegex(RuntimeError, "Operation Contract bindings are invalid"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")

        # Re-approval alone keeps the sealed binding, so the drift still blocks reopen.
        args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_execution(args), 0)
        self.assertEqual(delivery_compile.split_note(item)[0]["verification_contract_hash"], first)
        delivery_git.publish_execution_plan(project, "DLV-001")
        with self.assertRaisesRegex(RuntimeError, "Operation Contract bindings are invalid"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")

        named = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "reopen": ["AUTH-01"]})
        self.assertEqual(delivery_compile.approve_execution(named), 0)
        rebound, _body = delivery_compile.split_note(item)
        self.assertEqual(rebound["status"], "integrated")
        self.assertEqual(rebound["verification_contract_hash"], second)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        relative_item = item.relative_to(project).as_posix()
        remote, _body = delivery_git.split_remote_note(project, published["integration"], relative_item, delivery_compile.split_note)
        self.assertEqual(remote["status"], "integrated")
        self.assertEqual(remote["verification_contract_hash"], second)

        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(reopened["status"], "active")
        current, _body = delivery_compile.split_note(Path(reopened["worktree"]) / relative_item)
        self.assertEqual(current["verification_contract_hash"], second)
        self.assertEqual(current["item_plan_hash"], rebound["item_plan_hash"])

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
        self.addCleanup(remove_temporary, temporary)
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
        self.addCleanup(remove_temporary, temporary)
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
