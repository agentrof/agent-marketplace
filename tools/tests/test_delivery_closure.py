"""Delivery closure: managed pull requests, their readiness, the closure audit and the opt-in CI check (#461)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_closure  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_result  # noqa: E402
import operation_compile  # noqa: E402
import vault_gate  # noqa: E402
try:
    from tools.tests import test_delivery_git as delivery_tests
    from tools.tests.git_fixture import init_repository, remove_temporary
except ModuleNotFoundError:  # run as a script from tools/tests
    import test_delivery_git as delivery_tests
    from git_fixture import init_repository, remove_temporary

URL = "https://github.com/agentrof/example/pull/17"
INTEGRATION = "agentrof/deliveries/dlv-001"
TEMPLATE = PACKAGE / "templates" / "delivery-closure.yml"
# The audit of one closed Delivery: 33 Git processes for the remote reads and its whole proof, with
# headroom; one process per object read spawned 70.
AUDIT_GIT_PROCESS_BUDGET = 36


def git(project: Path, *args: str) -> str:
    return delivery_git.run_git(project, *args)


@integration
class DeliveryClosureTests(unittest.TestCase):
    """Real Delivery repositories with a bare remote; the provider is the coordinator tests' double."""

    @classmethod
    def tearDownClass(cls):
        delivery_tests.DeliveryGitTests.tearDownClass()

    def setUp(self):
        self.case = delivery_tests.DeliveryGitTests()

    def pr_intent(self) -> tuple[Path, Path, str]:
        temporary, project, docs, product_tip, _intent = self.case.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        return project, docs, product_tip

    def pre_start(self) -> tuple[Path, Path]:
        if self.case.fixture_cache_context_unchanged():
            temporary, project, docs = delivery_tests._PR_FIXTURE_CACHE.copy(self.case.build_pre_start_fixture)
        else:
            temporary, project, docs = self.case.build_pre_start_fixture()
        self.addCleanup(remove_temporary, temporary)
        return project, docs

    def recorded(self) -> tuple[Path, Path, str, str, type]:
        """A Delivery whose PR open-pr recorded: the PR head is the Integration tip."""
        project, docs, product_tip = self.pr_intent()
        provider = self.case.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.open_pr(project, "DLV-001")
        head = delivery_git.remote_oid(project, "origin", "refs/heads/" + INTEGRATION)
        return project, docs, product_tip, head, provider

    def check(self, project: Path, head: str, *, base: str = "main", head_ref: str = INTEGRATION,
              url: str = URL) -> dict:
        return delivery_closure.check_pull_request(project, head=head, base=base, url=url, head_ref=head_ref)

    def codes(self, result: dict) -> list[str]:
        return [delivery_result.from_raw("closure-check", result)["findings"][index]["code"]
                for index in range(len(result.get("errors", [])))]

    def on_target(self, project: Path, change, message: str = "Change the target by hand") -> str:
        """Commit *change* on a detached copy of the remote target and push it as the new target tip."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        view = Path(temporary.name) / "target"
        git(project, "fetch", "-q", "origin", "main")
        git(project, "worktree", "add", "-q", "--detach", str(view), "FETCH_HEAD")
        try:
            change(view)
            if git(view, "status", "--porcelain"):
                git(view, "add", "-A")
                git(view, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", message)
            git(view, "push", "-q", "origin", "HEAD:refs/heads/main")
            return git(view, "rev-parse", "HEAD")
        finally:
            git(project, "worktree", "remove", "--force", str(view))

    def push_branch(self, project: Path, name: str, oid: str) -> None:
        git(project, "push", "-q", "origin", f"{oid}:refs/heads/{name}")

    def refs(self, project: Path) -> str:
        return git(project / "remote.git", "for-each-ref")

    def outcome(self, project: Path, delivery: str = "DLV-001") -> dict:
        audited = delivery_closure.audit(project, delivery)
        for item in audited["deliveries"]:
            self.assertIn(item["outcome"], delivery_closure.OUTCOMES)
        return audited["deliveries"][0] if audited["deliveries"] else {"errors": audited["errors"]}

    def forge(self, project: Path, commit: str, change, *, amend: bool = True,
              message: str = "Forge the Delivery work") -> str:
        """Commit *change* on a detached copy of *commit*, amending it unless told otherwise."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        view = Path(temporary.name) / "forge"
        git(project, "worktree", "add", "-q", "--detach", str(view), commit)
        try:
            change(view)
            git(view, "add", "-A")
            arguments = ["--amend", "--no-edit"] if amend else ["-m", message]
            git(view, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-q", *arguments)
            return git(view, "rev-parse", "HEAD")
        finally:
            git(project, "worktree", "remove", "--force", str(view))

    def test_the_canonical_flow_passes_closure_proves_its_merge_and_leaves_no_slot(self):
        project, _docs, _product, head, provider = self.recorded()
        checked = self.check(project, head)
        self.assertEqual((checked["ok"], checked["managed"], checked.get("errors")), (True, True, []), checked)
        before = self.refs(project)
        self.assertEqual(self.outcome(project)["outcome"], "awaiting_merge")
        self.assertEqual(self.refs(project), before, "the audit changes no ref")
        with mock.patch("delivery_provider.GitHubProvider", provider):
            merged = delivery_git.merge_pr(project, "DLV-001")
        audited = delivery_closure.audit(project, "DLV-001")
        self.assertEqual((audited["ok"], audited["deliveries"][0]["outcome"]), (True, "closed"))
        self.assertEqual(audited["deliveries"][0]["record"], merged["reviewed_integration"])
        self.assertNotIn("agentrof/slots/", self.refs(project))
        envelope = delivery_result.from_raw("closure-audit", audited)
        self.assertIn({"kind": "ref", "target": "closure/DLV-001", "value": "closed"}, envelope["observations"])

    def test_a_direct_pr_of_delivery_work_fails_closure_whatever_its_branch_or_product_ci(self):
        """A PR whose head is the Item's product commit is green on product CI, but it is not the
        recorded Integration head, so closure fails; renaming its branch changes nothing."""
        project, _docs, product_tip = self.pr_intent()
        for branch in ("feature/auth", "hotfix/renamed"):
            with self.subTest(branch=branch):
                self.push_branch(project, branch, product_tip)
                checked = self.check(project, product_tip, head_ref=branch)
                self.assertTrue(checked["managed"])
                self.assertFalse(checked["ok"])
                self.assertIn("DELIVERY_PR_HEAD_BASE_MISMATCH", self.codes(checked))
                self.assertIn("recovery: close this PR and continue through /deliver DLV-001",
                              " ".join(checked["errors"]))

    def test_a_pr_that_changes_a_claimed_path_is_managed_and_any_other_pr_passes(self):
        project, _docs = self.pre_start()

        def claimed(view: Path) -> None:
            (view / "src").mkdir(exist_ok=True)
            (view / "src" / "auth.py").write_text("def authenticate():\n    return 'direct'\n", encoding="utf-8")

        def unrelated(view: Path) -> None:
            (view / "README.md").write_text("fixture\nmore\n", encoding="utf-8")

        base = git(project, "rev-parse", "origin/main")
        for change, managed in ((claimed, True), (unrelated, False)):
            head = self.on_target(project, change)
            git(project, "push", "-q", "--force", "origin", f"{base}:refs/heads/main")
            with self.subTest(managed=managed):
                checked = self.check(project, head, head_ref="feature/direct")
                self.assertEqual((checked["managed"], checked["ok"]), (managed, not managed), checked)
                if managed:
                    self.assertIn("under a path claim of AUTH-01 of DLV-001", " ".join(checked["reasons"]))

    def test_a_changed_pr_head_invalidates_readiness_and_the_earlier_head_cannot_be_reused(self):
        project, _docs, _product, head, _provider = self.recorded()
        self.assertTrue(self.check(project, head)["ok"])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        view = Path(temporary.name) / "edit"
        git(project, "worktree", "add", "-q", "--detach", str(view), head)
        (view / "src" / "auth.py").write_text("def authenticate():\n    return 'late'\n", encoding="utf-8")
        git(view, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qam", "Late change")
        changed = git(view, "rev-parse", "HEAD")
        git(view, "push", "-q", "origin", f"HEAD:refs/heads/{INTEGRATION}")
        git(project, "worktree", "remove", "--force", str(view))
        later = self.check(project, changed)
        self.assertFalse(later["ok"])
        self.assertIn("DELIVERY_CLOSURE_INCOMPLETE", self.codes(later))
        earlier = self.check(project, head)
        self.assertFalse(earlier["ok"])
        self.assertIn("is not the recorded Integration head of DLV-001", " ".join(earlier["errors"]))

    def test_a_base_other_than_the_delivery_target_fails(self):
        project, _docs, _product, head, _provider = self.recorded()
        self.push_branch(project, "release", git(project, "rev-parse", "origin/main"))
        checked = self.check(project, head, base="release")
        self.assertFalse(checked["ok"])
        self.assertIn("the PR targets release, but DLV-001 targets main", " ".join(checked["errors"]))

    def test_a_pr_record_that_binds_another_pr_fails(self):
        project, _docs, _product, head, _provider = self.recorded()
        checked = self.check(project, head, url="https://github.com/agentrof/example/pull/18")
        self.assertFalse(checked["ok"])
        self.assertIn("binds another PR", " ".join(checked["errors"]))

    def test_a_cancelled_delivery_closes_through_its_recorded_cancellation_pr(self):
        project, _docs, _product = self.pr_intent()
        provider = self.case.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.open_pr(project, "DLV-001")
            delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
            head = delivery_git.open_pr(project, "DLV-001")["integration"]
            checked = self.check(project, head)
            self.assertEqual((checked["ok"], checked.get("errors")), (True, []), checked)
            delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(self.outcome(project)["outcome"], "closed")

    def test_a_leftover_slot_blocks_the_recorded_head_and_the_audit_names_its_recovery(self):
        project, _docs, _product, head, _provider = self.recorded()
        self.push_branch(project, "agentrof/slots/009", head)
        checked = self.check(project, head)
        self.assertFalse(checked["ok"])
        self.assertIn("agentrof/slots/009 still holds", " ".join(checked["errors"]))
        audited = delivery_closure.audit(project, "DLV-001")
        outcome = audited["deliveries"][0]
        self.assertEqual((audited["ok"], outcome["outcome"]), (False, "awaiting_merge"))
        self.assertIn("each releases its Slot atomically", " ".join(outcome["readiness"]))
        finding = delivery_result.from_raw("closure-audit", audited)["findings"][0]
        self.assertEqual((finding["code"], finding["next_entry"]), ("DELIVERY_CLOSURE_INCOMPLETE", "/deliver DLV-001"))

    def test_a_proven_merge_whose_refs_stay_is_merged_cleanup_pending_until_verify_merge(self):
        project, _docs, _product, head, provider = self.recorded()
        provider(project).merge_commit(URL, head)
        pending = delivery_closure.audit(project, "DLV-001")
        outcome = pending["deliveries"][0]
        self.assertEqual((pending["ok"], outcome["outcome"], outcome["next_entry"]),
                         (False, "merged_cleanup_pending", "verify-merge"))
        self.assertIn(f"Integration ref {INTEGRATION}", outcome["leftovers"])
        self.assertEqual(delivery_result.from_raw("closure-audit", pending)["findings"][0]["code"],
                         "DELIVERY_CLOSURE_INCOMPLETE")
        base = git(project, "rev-parse", "origin/main")
        follow_up = self.forge(project, base, self.auth("def authenticate():\n    return 'v2'\n"), amend=False,
                               message="Follow up on authentication")
        self.push_branch(project, "feature/next", follow_up)
        # The merged Delivery's claims hold until verify-merge drops its refs, and the check says so.
        before = self.check(project, follow_up, head_ref="feature/next")
        self.assertFalse(before["ok"])
        self.assertIn("recovery: verify-merge DLV-001 drops its Integration", " ".join(before["errors"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.merge_pr(project, "DLV-001", verify_only=True)
        self.assertEqual(self.outcome(project)["outcome"], "closed")
        after = self.check(project, follow_up, head_ref="feature/next")
        self.assertEqual((after["managed"], after["ok"]), (False, True), after)

    def test_a_squash_of_the_recorded_head_is_an_external_product_merge_never_completion(self):
        project, docs, _product, head, _provider = self.recorded()

        def squash(view: Path) -> None:
            git(view, "fetch", "-q", "origin", INTEGRATION)
            git(view, "merge", "--squash", "-q", "FETCH_HEAD")

        self.on_target(project, squash, "Squash the Delivery PR")
        outcome = self.outcome(project)
        self.assertEqual(outcome["outcome"], "external_product_merge")
        self.assertNotEqual(outcome["outcome"], "closed")
        self.assertIn("/deliver DLV-001", outcome["recovery"])
        target = git(project, "rev-parse", "origin/main")
        self.assertIsNone(delivery_compile.merged_pr_record(project, "DLV-001", target))

    def test_a_stale_slot_after_an_external_merge_reports_external_product_merge(self):
        """An active Item pushed its evidence, and its product reached the target by hand while its
        Slot stays: the audit reports the external merge with the Slot, and never closes it."""
        project, _docs = self.pre_start()
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        product = self.case.commit_item_product_change(active["worktree"], "def authenticate():\n    return 'v1'\n")
        self.assertEqual(self.case.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertIn("agentrof/slots/", self.refs(project))
        before = git(project, "rev-parse", "origin/main")
        for name, change in (
                ("merge", lambda view: git(view, "-c", "user.email=test@example.com", "-c", "user.name=Test",
                                           "merge", "-q", "--no-ff", "-m", "Merge the Item by hand", product)),
                ("squash", lambda view: git(view, "checkout", product, "--", "src/auth.py"))):
            with self.subTest(name=name):
                self.on_target(project, change)
                audited = delivery_closure.audit(project, "DLV-001")
                outcome = audited["deliveries"][0]
                finding = delivery_result.from_raw("closure-audit", audited)["findings"][0]
                if name == "merge":
                    self.assertEqual((audited["ok"], outcome["outcome"]), (False, "external_product_merge"))
                    self.assertEqual(outcome["leftovers"], ["Slot agentrof/slots/001"])
                    self.assertEqual((finding["code"], finding["severity"]), ("DELIVERY_EXTERNAL_MERGE", "blocker"))
                else:
                    # The Item's bytes alone may be an independent change: a warning, never an external merge.
                    self.assertEqual((audited["ok"], outcome["outcome"]), (True, "open"))
                    self.assertEqual((finding["code"], finding["severity"]), ("DELIVERY_EXTERNAL_MERGE", "warning"))
                    self.assertIn("which an independent change can also write", finding["message"])
                git(project, "push", "-q", "--force", "origin", f"{before}:refs/heads/main")
        self.assertEqual(self.outcome(project)["outcome"], "open")

    def test_closure_commands_print_one_result_envelope(self):
        project, _docs, _product, head, _provider = self.recorded()
        for argv, operation in (
                (["closure-audit", "--project-root", str(project), "--all"], "closure-audit"),
                (["closure-check", "--project-root", str(project), "--pr-url", URL, "--head", head,
                  "--head-ref", INTEGRATION, "--base", "main"], "closure-check")):
            with self.subTest(operation=operation):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = delivery_git.main(argv)
                envelope = json.loads(output.getvalue())
                self.assertEqual((code, envelope["ok"], envelope["operation"]), (0, True, operation))

    @staticmethod
    def auth(content: str):
        def write(view: Path) -> None:
            (view / "src").mkdir(exist_ok=True)
            (view / "src" / "auth.py").write_text(content, encoding="utf-8")
        return write

    def rebuild(self, project: Path, commit: str, parent: str, blob: str, replace: dict[str, str]) -> str:
        """A copy of *commit* on *parent* whose src/auth.py is *blob* and whose trailers *replace* names."""
        index = Path(tempfile.mkdtemp()) / "index"
        self.addCleanup(lambda: index.unlink(missing_ok=True))
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        subprocess.run(["git", "read-tree", commit], cwd=project, env=env, check=True)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},src/auth.py"],
                       cwd=project, env=env, check=True)
        tree = subprocess.run(["git", "write-tree"], cwd=project, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
        message = delivery_git.commit_message(project, commit)
        for key, value in replace.items():
            message = re.sub(rf"(?m)^Agentrof-{key}: .*$", f"Agentrof-{key}: {value}", message)
        return subprocess.run(["git", "commit-tree", tree, "-p", parent], cwd=project, input=message, check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_an_amended_pr_record_with_a_product_change_fails_closure_merge_pr_and_never_closes(self):
        """The forged record keeps every trailer and its parent; only its tree carries product bytes."""
        project, _docs, _product, head, provider = self.recorded()
        forged = self.forge(project, head, self.auth("def authenticate():\n    return 'forged'\n"))
        self.assertEqual(delivery_git.commit_message(project, forged), delivery_git.commit_message(project, head))
        git(project, "push", "-q", "--force", "origin", f"{forged}:refs/heads/{INTEGRATION}")
        checked = self.check(project, forged)
        self.assertFalse(checked["ok"])
        self.assertIn("is not the tree open-pr writes on its intent; it differs at src/auth.py",
                      " ".join(checked["errors"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, "merge-pr refuses a PR head the coordinator did not write"):
                delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(self.outcome(project)["outcome"], "awaiting_merge")
        provider(project).merge_commit(URL, forged)
        audited = delivery_closure.audit(project, "DLV-001")
        outcome = audited["deliveries"][0]
        self.assertEqual((audited["ok"], outcome["outcome"], outcome["next_entry"]),
                         (False, "unproven_record_merge", "/deliver DLV-001"))
        self.assertIn("differs at src/auth.py", " ".join(outcome["evidence"]))
        self.assertEqual(delivery_result.from_raw("closure-audit", audited)["findings"][0]["code"],
                         "DELIVERY_EXTERNAL_MERGE")
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, "merge-pr refuses a PR head the coordinator did not write"):
                delivery_git.merge_pr(project, "DLV-001", verify_only=True)

    def test_the_closure_check_recomputes_the_record_without_a_git_identity(self):
        """A CI runner has no Git identity, so the check writes no commit."""
        project, _docs, _product, head, _provider = self.recorded()
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_NAME": "", "GIT_AUTHOR_NAME": ""}):
            checked = self.check(project, head)
        self.assertEqual((checked["ok"], checked.get("errors")), (True, []), checked)

    def test_a_product_commit_slipped_under_the_reviewed_integration_fails_closure(self):
        """A rebuilt record, intent and Review over an extra commit that claims a control record."""
        project, _docs, _product, head, _provider = self.recorded()
        chain = {"intent": delivery_git.trailer(delivery_git.commit_message(project, head), "Intent")}
        chain["review"] = delivery_git.trailer(delivery_git.commit_message(project, chain["intent"]), "Review-Head")
        chain["reviewed_integration"] = delivery_git.trailer(
            delivery_git.commit_message(project, chain["review"]), "Reviewed-Integration")
        smuggled = self.forge(project, chain["reviewed_integration"], self.auth("def authenticate():\n    return 'x'\n"),
                              amend=False, message="Claim\n\nAgentrof-Record: claims-established-v1\n"
                                                   "Agentrof-Delivery: DLV-001\n")
        blob = git(project, "rev-parse", f"{smuggled}:src/auth.py")
        review = self.rebuild(project, chain["review"], smuggled, blob, {"Reviewed-Integration": smuggled})
        intent = self.rebuild(project, chain["intent"], review, blob, {"Review-Head": review})
        record = self.rebuild(project, head, intent, blob, {"Intent": intent})
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        errors = " ".join(self.check(project, record)["errors"])
        self.assertIn(f"the claims_established_v1 record {smuggled} of DLV-001 changes the product paths src/auth.py",
                      errors)
        self.assertIn(f"not the Integration {smuggled} its record names", errors)

    def test_promotions_and_prs_into_another_branch_are_not_managed_by_target_commits(self):
        project, _docs = self.pre_start()
        main = git(project, "rev-parse", "origin/main")
        self.push_branch(project, "release", git(project, "rev-list", "--max-parents=0", main).split()[0])
        develop = self.forge(project, main, lambda view: (view / "README.md").write_text("fixture\nother\n",
                                                                                          encoding="utf-8"),
                             amend=False, message="Document the release")
        self.push_branch(project, "develop", develop)
        for head, base, head_ref in ((main, "release", "main"), (develop, "release", "develop"),
                                     (develop, "main", "develop")):
            with self.subTest(head_ref=head_ref, base=base):
                checked = self.check(project, head, base=base, head_ref=head_ref)
                self.assertEqual((checked["managed"], checked["ok"]), (False, True), checked)
        claimed = self.forge(project, main, self.auth("def authenticate():\n    return 'direct'\n"),
                             amend=False, message="Change a claimed path")
        self.push_branch(project, "feature/claimed", claimed)
        checked = self.check(project, claimed, base="release", head_ref="feature/claimed")
        self.assertEqual((checked["managed"], checked["ok"]), (True, False))
        self.assertIn("under a path claim of AUTH-01 of DLV-001", " ".join(checked["reasons"]))

    def test_an_integrated_items_product_cherry_picked_onto_a_fresh_branch_is_managed_and_fails(self):
        project, _docs, product, _head, _provider = self.recorded()
        main = git(project, "rev-parse", "origin/main")
        fresh = self.forge(project, main, lambda view: git(view, "cherry-pick", "-n", product), amend=False,
                           message="Authenticate")
        self.push_branch(project, "feature/fresh", fresh)
        checked = self.check(project, fresh, head_ref="feature/fresh")
        self.assertEqual((checked["managed"], checked["ok"]), (True, False))
        reasons = " ".join(checked["reasons"])
        self.assertIn("it changes src/auth.py under a path claim of AUTH-01 of DLV-001", reasons)
        self.assertIn(f"it carries the exact product bytes of the Item product tip {product} of DLV-001", reasons)

    def test_an_older_record_merged_after_the_integration_moved_is_not_merged_and_needs_the_owner(self):
        project, _docs, _product, head, _provider = self.recorded()
        later = self.forge(project, head, self.auth("def authenticate():\n    return 'late'\n"), amend=False,
                           message="Late change")
        git(project, "push", "-q", "origin", f"{later}:refs/heads/{INTEGRATION}")
        self.on_target(project, lambda view: git(view, "-c", "user.email=test@example.com", "-c", "user.name=Test",
                                                 "merge", "-q", "--no-ff", "-m", "Merge the old head", head))
        audited = delivery_closure.audit(project, "DLV-001")
        outcome = audited["deliveries"][0]
        self.assertEqual((audited["ok"], outcome["outcome"], outcome["next_entry"]),
                         (False, "unproven_record_merge", "/deliver DLV-001"))
        self.assertIn(f"has since moved to {later}", " ".join(outcome["evidence"]))
        self.assertNotIn("verify-merge", outcome["recovery"].replace("verify-merge does not apply", ""))

    def test_a_cancelled_storys_writer_receipt_does_not_keep_the_delivery_from_closed(self):
        """Cancelling a Delivery with an active Item leaves its writer receipt, which binds no live writer."""
        project, _docs = self.pre_start()
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.case.commit_item_product_change(active["worktree"], "def authenticate():\n    return 'v1'\n")
        root = delivery_git.main_worktree(project)
        receipt = ["item-dlv-001-auth-01.json"]
        provider = self.case.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
            self.assertEqual(delivery_closure.writer_receipts(root, "DLV-001"), receipt)
            delivery_git.prepare_pr_creation(project, "DLV-001")
            head = delivery_git.open_pr(project, "DLV-001")["integration"]
            self.assertEqual(self.check(project, head).get("errors"), [])
            delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(self.outcome(project)["outcome"], "closed")

    def test_a_live_items_writer_receipt_still_counts(self):
        project, _docs = self.pre_start()
        delivery_git.start_item(project, "DLV-001", "AUTH-01")
        root = delivery_git.main_worktree(project)
        self.assertEqual(delivery_closure.writer_receipts(
            root, "DLV-001", delivery_closure.coordination_state(root, "origin")), ["item-dlv-001-auth-01.json"])

    def test_identical_product_bytes_on_the_target_alone_are_a_warning_not_an_external_merge(self):
        project, _docs, _product, _head, _provider = self.recorded()
        self.on_target(project, self.auth("def authenticate():\n    return 'v1'\n"), "Write the same fix")
        audited = delivery_closure.audit(project, "DLV-001")
        self.assertEqual((audited["ok"], audited["deliveries"][0]["outcome"]), (True, "awaiting_merge"))
        finding = delivery_result.from_raw("closure-audit", audited)["findings"][0]
        self.assertEqual((finding["code"], finding["severity"]), ("DELIVERY_EXTERNAL_MERGE", "warning"))
        self.assertIn("this alone proves no external merge", finding["message"])

    def test_audit_all_reports_an_unreadable_delivery_and_audits_the_rest(self):
        project, _docs = self.pre_start()
        audit_delivery = delivery_closure.audit_delivery

        def unreadable(root, identifier, *args, **kwargs):
            if identifier == "DLV-002":
                raise RuntimeError("DELIVERY_PROTOCOL_UNSUPPORTED: control record provisional-claim-v1 is unknown")
            return audit_delivery(root, identifier, *args, **kwargs)

        with mock.patch.object(delivery_closure, "known_deliveries", return_value=["DLV-001", "DLV-002"]), \
                mock.patch.object(delivery_closure, "audit_delivery", side_effect=unreadable):
            audited = delivery_closure.audit(project)
            self.assertEqual([item["delivery"] for item in audited["deliveries"]], ["DLV-001"])
            self.assertFalse(audited["ok"])
            self.assertEqual(len(audited["errors"]), 1)
            self.assertIn("DLV-002: DELIVERY_PROTOCOL_UNSUPPORTED", audited["errors"][0])
            with self.assertRaisesRegex(RuntimeError, "DELIVERY_PROTOCOL_UNSUPPORTED"):
                delivery_closure.audit(project, "DLV-002")

    def test_a_pr_that_changes_the_closure_gate_gets_an_owner_warning(self):
        project, _docs = self.pre_start()
        main = git(project, "rev-parse", "origin/main")

        def gate(view: Path) -> None:
            workflow = view / ".github" / "workflows" / "delivery-closure.yml"
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text("on: push\n", encoding="utf-8")

        head = self.forge(project, main, gate, amend=False, message="Change the closure workflow")
        self.push_branch(project, "feature/gate", head)
        checked = self.check(project, head, head_ref="feature/gate")
        self.assertEqual((checked["ok"], checked["managed"]), (True, False))
        findings = delivery_result.from_raw("closure-check", checked)["findings"]
        self.assertEqual([(finding["code"], finding["severity"], finding["paths"]) for finding in findings],
                         [("DELIVERY_CLOSURE_GATE_CHANGED", "warning", [".github/workflows/delivery-closure.yml"])])


    # Round-2 forgeries: a consistent Review, intent and record rebuilt over a forged reviewed Integration.

    def blob(self, project: Path, content: str) -> tuple[str, str]:
        oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=project, input=content, check=True,
                             capture_output=True, text=True).stdout.strip()
        return "100644", oid

    def tree_with(self, project: Path, commit: str, changes: dict) -> str:
        """The tree of *commit* with each path set to its (mode, blob), or removed for None."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        index = Path(temporary.name) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        subprocess.run(["git", "read-tree", commit], cwd=project, env=env, check=True)
        for path, entry in changes.items():
            arguments = (["--force-remove", "--", path] if entry is None
                         else ["--add", "--cacheinfo", f"{entry[0]},{entry[1]},{path}"])
            subprocess.run(["git", "update-index", *arguments], cwd=project, env=env, check=True)
        return subprocess.run(["git", "write-tree"], cwd=project, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

    @staticmethod
    def commit_tree(project: Path, tree: str, parents: list[str], message: str) -> str:
        arguments = [argument for parent in parents for argument in ("-p", parent)]
        return subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit-tree",
                               tree, *arguments], cwd=project, input=message, check=True, capture_output=True,
                              text=True).stdout.strip()

    @staticmethod
    def retrailer(message: str, **values: str) -> str:
        for key, value in values.items():
            message = re.sub(rf"(?m)^Agentrof-{key.replace('_', '-')}: .*$", f"Agentrof-{key.replace('_', '-')}: {value}",
                             message)
        return message

    def chain(self, project: Path, record: str) -> dict:
        message = delivery_git.commit_message
        intent = delivery_git.trailer(message(project, record), "Intent")
        review = delivery_git.trailer(message(project, intent), "Review-Head")
        return {"record": record, "intent": intent, "review": review,
                "reviewed": delivery_git.trailer(message(project, review), "Reviewed-Integration")}

    def review_on(self, project: Path, chain: dict, reviewed: str) -> str:
        """A Review, intent and PR record over *reviewed* that bind each other exactly as the verbs would."""
        package = delivery_closure.package_directory(project, chain["record"], "DLV-001")
        review_path = f"{package}/delivery-review.md"
        changes = {path: self.entry(project, chain["review"], path) for path in delivery_git.git_paths(
            project, "diff", "--no-renames", "--name-only", "-z", chain["reviewed"], chain["review"])}
        props, body = delivery_git.split_remote_note(project, chain["review"], review_path, delivery_compile.split_note)
        props["reviewed_commit"] = props["reviewed_integration_commit"] = reviewed
        props["approval_hash"] = delivery_compile.content_hash(props, body,
                                                               exclude=delivery_compile.MUTABLE | {"approval_hash"})
        props["source_hash"] = delivery_compile.content_hash(
            props, body, exclude={"status", "approved_at_utc", "source_hash", "approval_hash"})
        changes[review_path] = self.blob(project, delivery_compile.frontmatter(props, body))
        tree = self.tree_with(project, reviewed, changes)
        message = delivery_git.commit_message
        review = self.commit_tree(project, tree, [reviewed], self.retrailer(
            message(project, chain["review"]), Reviewed_Integration=reviewed, Approval_Hash=props["approval_hash"]))
        intent = self.commit_tree(project, tree, [review],
                                  self.retrailer(message(project, chain["intent"]), Review_Head=review))
        return delivery_git.pr_record_candidate(project, intent, package, "DLV-001", URL)

    def entry(self, project: Path, commit: str, path: str) -> tuple[str, str] | None:
        listed = git(project, "ls-tree", commit, "--", path)
        if not listed:
            return None
        mode, _kind, oid = listed.split("\t", 1)[0].split()
        return mode, oid

    def item_merge(self, project: Path, first: str, story: str, files: dict) -> str:
        """A product, evidence commit, seal and Item integration of *story* on *first*, with no evidence notes."""
        tree = self.tree_with(project, first, files)
        product = self.commit_tree(project, tree, [first], "Implement\n\nAgentrof-Delivery: DLV-001\n")
        evidence = self.commit_tree(project, tree, [product], f"Update Item {story}\n")
        trailers = (f"Agentrof-Record: item-integration-v1\nAgentrof-Protocol: 1\nAgentrof-Delivery: DLV-001\n"
                    f"Agentrof-Story: {story}\nAgentrof-Item-Plan-Hash: none\n")
        seal = self.commit_tree(project, tree, [evidence], f"Seal Item {story} for DLV-001\n\n{trailers}"
                                f"Agentrof-Reviewed-Tip: {evidence}\nAgentrof-Product-Tip: {product}\n"
                                f"Agentrof-Integration-Parent: {first}\n")
        return self.commit_tree(project, tree, [first, seal], f"Integrate Item {story} for DLV-001\n\n{trailers}"
                                f"Agentrof-Reviewed-Tip: {seal}\nAgentrof-Integration-Parent: {first}\n")

    def refused_everywhere(self, project: Path, record: str, provider, expected: str) -> None:
        """The forged record fails the check with *expected*, merge-pr refuses it and the audit never closes."""
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        checked = self.check(project, record)
        self.assertFalse(checked["ok"])
        self.assertIn(expected, " ".join(checked["errors"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, "merge-pr refuses a PR head the coordinator did not write"):
                delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(self.outcome(project)["outcome"], "awaiting_merge")
        self.assertNotIn("src/evil.py", git(project, "ls-tree", "-r", "--name-only", "origin/main"))

    def test_an_item_integration_of_a_story_the_package_lacks_fails_closure(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        merge = self.item_merge(project, chain["reviewed"], "GHOST-01", {"src/evil.py": self.blob(project, "EVIL\n")})
        self.refused_everywhere(project, self.review_on(project, chain, merge), provider,
                                f"the Item integration {merge} of DLV-001 merges GHOST-01, which the reviewed package"
                                " of DLV-001 does not hold")

    def test_an_earlier_unevidenced_integration_of_a_reviewed_story_fails_closure(self):
        """Only the latest integration of a Story was evidence-checked; each one is now."""
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        first, seal = git(project, "show", "-s", "--format=%P", chain["reviewed"]).split()
        evil = self.blob(project, "EVIL\n")
        forged = self.item_merge(project, first, "AUTH-01", {"src/evil.py": evil})
        remade = self.commit_tree(project, self.tree_with(project, chain["reviewed"], {"src/evil.py": evil}),
                                  [forged, seal], self.retrailer(delivery_git.commit_message(project, chain["reviewed"]),
                                                                 Integration_Parent=forged))
        self.refused_everywhere(project, self.review_on(project, chain, remade), provider,
                                f"the Item integration {forged} of DLV-001 holds AUTH-01 as")

    def test_an_integration_of_a_story_cancelled_without_its_revert_fails_closure(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        package = delivery_closure.package_directory(project, head, "DLV-001")
        item_path = f"{package}/items/auth-01/item.md"
        props, body = delivery_git.split_remote_note(project, chain["reviewed"], item_path, delivery_compile.split_note)
        props["status"] = "cancelled"
        cancelled = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"],
                                    {item_path: self.blob(project, delivery_compile.frontmatter(props, body))}),
            [chain["reviewed"]], "Cancel AUTH-01\n\nAgentrof-Record: item-cancelled-v1\nAgentrof-Protocol: 1\n"
                                 "Agentrof-Delivery: DLV-001\nAgentrof-Story: AUTH-01\n")
        record = self.review_on(project, chain, cancelled)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        errors = " ".join(self.check(project, record)["errors"])
        self.assertIn(f"the Item integration {chain['reviewed']} of DLV-001 merges AUTH-01, which the reviewed"
                      " package cancelled, and no cancellation revert names it", errors)
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, "merge-pr refuses a PR head the coordinator did not write"):
                delivery_git.merge_pr(project, "DLV-001")

    def test_a_provider_merged_forged_line_is_proven_against_the_target_before_the_merge(self):
        """The line walk stops at the target as it was before the merge, never at the target that holds it."""
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        slipped = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"], {"src/evil.py": self.blob(project, "EVIL\n")}),
            [chain["reviewed"]], "Claim\n\nAgentrof-Record: claims-established-v1\nAgentrof-Protocol: 1\n"
                                 "Agentrof-Delivery: DLV-001\n")
        record = self.review_on(project, chain, slipped)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        provider(project).merge_commit(URL, record)
        expected = f"the claims_established_v1 record {slipped} of DLV-001 changes the product paths src/evil.py"
        audited = delivery_closure.audit(project, "DLV-001")
        outcome = audited["deliveries"][0]
        self.assertEqual((audited["ok"], outcome["outcome"]), (False, "unproven_record_merge"))
        self.assertIn(expected, " ".join(outcome["evidence"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, re.escape(expected)):
                delivery_git.merge_pr(project, "DLV-001", verify_only=True)
        self.assertEqual(self.outcome(project)["outcome"], "unproven_record_merge")

    def test_a_fence_target_the_target_lacks_is_drift_and_hides_nothing(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        plain = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"], {"src/evil.py": self.blob(project, "EVIL\n")}),
            [chain["reviewed"]], "Plain product change\n")
        fence = git(project, "ls-remote", "origin", "refs/heads/agentrof/fence").split()[0]
        forged_fence = self.commit_tree(project, fence + "^{tree}", [fence],
                                        self.retrailer(delivery_git.commit_message(project, fence), Target=plain))
        git(project, "push", "-q", "--force", "origin", f"{forged_fence}:refs/heads/agentrof/fence")
        record = self.review_on(project, chain, plain)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        self.assertIn(f"the Integration commit {plain} is not a control record of DLV-001",
                      " ".join(self.check(project, record)["errors"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, f"DELIVERY_TARGET_DRIFT: the Fence target {plain} of DLV-001"):
                delivery_git.merge_pr(project, "DLV-001")
        self.assertNotIn("src/evil.py", git(project, "ls-tree", "-r", "--name-only", "origin/main"))

    def test_the_audit_of_a_closed_delivery_spawns_a_bounded_number_of_git_processes(self):
        """Every proof still runs; objects come from one batched reader per replace mode, read once per audit."""
        project, _docs, _product, _head, provider = self.recorded()
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.merge_pr(project, "DLV-001")
        spawned: list = []
        popen = subprocess.Popen

        class Counted(popen):
            def __init__(self, args, *rest, **options):
                spawned.append(args)
                super().__init__(args, *rest, **options)

        with mock.patch.object(subprocess, "Popen", Counted):
            audited = delivery_closure.audit(project)
        self.assertEqual([item["outcome"] for item in audited["deliveries"]], ["closed"])
        self.assertEqual(sum("cat-file" in args and "--batch" in args for args in spawned), 2)
        self.assertLessEqual(len(spawned), AUDIT_GIT_PROCESS_BUDGET, [" ".join(args[1:4]) for args in spawned])

    def test_a_later_projection_rendering_neither_corrupts_the_record_nor_reopens_a_closed_delivery(self):
        """A later package release may render the vault projections differently than the one that wrote them."""
        project, _docs, _product, head, provider = self.recorded()
        render_map = delivery_compile.render_map

        def later_release(docs: Path, *args, **kwargs):
            result = render_map(docs, *args, **kwargs)
            for path in (Path(docs) / "maps").rglob("*.md"):
                path.write_text(path.read_text(encoding="utf-8") + "\n<!-- a later rendering -->\n", encoding="utf-8")
            return result

        with mock.patch.object(delivery_compile, "render_map", later_release):
            checked = self.check(project, head)
            self.assertEqual((checked["ok"], checked.get("errors")), (True, []), checked)
            with mock.patch("delivery_provider.GitHubProvider", provider):
                delivery_git.merge_pr(project, "DLV-001")
            self.assertEqual(self.outcome(project)["outcome"], "closed")
        # A historical closed Delivery is proven again without recomputing the notes open-pr authored.
        with mock.patch.object(delivery_git, "pr_record_replacements", side_effect=AssertionError("recomputed")):
            audited = delivery_closure.audit(project)
        self.assertEqual((audited["ok"], [item["outcome"] for item in audited["deliveries"]]), (True, ["closed"]))

    def test_the_line_changes_read_in_one_call_match_each_commits_own_diff(self):
        project, _docs, _product, head, _provider = self.recorded()
        main = git(project, "rev-parse", "origin/main")
        singles = [oid for oid, lineage, _message in delivery_closure.line_commits(project, head, [main])
                   if len(lineage) == 1]
        self.assertGreater(len(singles), 3)
        self.assertEqual(delivery_closure.single_parent_deltas(project, singles),
                         {oid: delivery_closure.tree_delta(project, oid + "^", oid) for oid in singles})

    def test_the_closure_check_runs_in_a_detached_ci_checkout_without_a_remote_head(self):
        """actions/checkout fetches every branch and checks out one commit: no origin/HEAD, no current branch."""
        project, _docs, _product, head, _provider = self.recorded()
        main = git(project, "rev-parse", "origin/main")
        feature = self.forge(project, main, lambda view: (view / "README.md").write_text("fixture\nmore\n",
                                                                                         encoding="utf-8"),
                             amend=False, message="Document more")
        self.push_branch(project, "feature/docs", feature)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        ci = Path(temporary.name) / "ci"
        init_repository(ci, initial_branch="main")
        git(ci, "remote", "add", "origin", str(project / "remote.git"))
        git(ci, "fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
        git(ci, "checkout", "-q", "--force", main)
        with self.assertRaises(RuntimeError):
            git(ci, "symbolic-ref", "refs/remotes/origin/HEAD")
        self.assertEqual(git(ci, "branch", "--show-current"), "")
        # Without the workflow's target the base stands in; with it, the default branch it names.
        for target in ({}, {"target": "main"}):
            with self.subTest(**target):
                unmanaged = delivery_closure.check_pull_request(ci, head=feature, base="main", url=URL,
                                                                head_ref="feature/docs", **target)
                self.assertEqual((unmanaged["ok"], unmanaged["managed"]), (True, False), unmanaged)
                managed = delivery_closure.check_pull_request(ci, head=head, base="main", url=URL,
                                                              head_ref=INTEGRATION, **target)
                self.assertEqual((managed["ok"], managed["managed"], managed.get("errors")), (True, True, []), managed)

    def test_a_pr_into_a_base_without_shared_history_is_classified_on_its_own_changes(self):
        """An orphan branch such as a pages site shares no merge base with the Delivery target."""
        project, _docs = self.pre_start()
        page = self.blob(project, "<html>\n")
        empty = subprocess.run(["git", "mktree"], cwd=project, input="", check=True, capture_output=True,
                               text=True).stdout.strip()
        pages = self.commit_tree(project, self.tree_with(project, empty, {"index.html": page}), [], "Pages\n")
        edit = self.commit_tree(project, self.tree_with(project, pages, {"index.html": self.blob(project, "<p>\n")}),
                                [pages], "Edit the page\n")
        self.push_branch(project, "pages", pages)
        self.push_branch(project, "pages-edit", edit)
        checked = self.check(project, edit, base="pages", head_ref="pages-edit")
        self.assertEqual((checked["ok"], checked["managed"]), (True, False), checked)

    def test_a_partial_hand_merge_is_named_by_the_audit_and_never_proves_a_merge(self):
        """The audit names the Item paths the target holds and the ones it lacks, as a warning only."""
        project, _docs = self.pre_start()
        start = git(project, "rev-parse", "origin/main")

        def item(view: Path) -> None:
            (view / "lib").mkdir(exist_ok=True)
            (view / "lib" / "a.py").write_text("a = 1\n", encoding="utf-8")
            (view / "lib" / "b.py").write_text("b = 1\n", encoding="utf-8")

        product = self.forge(project, start, item, amend=False, message="Item product")

        def copy(*names: str):
            def write(view: Path) -> None:
                (view / "lib").mkdir(exist_ok=True)
                for name in names:
                    (view / "lib" / name).write_text(f"{name[0]} = 1\n", encoding="utf-8")
            return write

        with mock.patch.object(delivery_closure, "product_tips", return_value={product}), \
                mock.patch.object(delivery_closure, "product_start", return_value=start):
            for names, expected in ((("a.py",), f"product tip {product} at lib/a.py but not at lib/b.py, which a"
                                                " partial hand merge leaves"),
                                    (("a.py", "b.py"), "at lib/a.py, lib/b.py, which an independent change can"
                                                       " also write")):
                with self.subTest(names=names):
                    self.on_target(project, copy(*names), "Copy the Item by hand")
                    audited = delivery_closure.audit(project, "DLV-001")
                    self.assertEqual((audited["ok"], audited["deliveries"][0]["outcome"]), (True, "open"))
                    findings = delivery_result.from_raw("closure-audit", audited)["findings"]
                    self.assertEqual([(finding["code"], finding["severity"]) for finding in findings],
                                     [("DELIVERY_EXTERNAL_MERGE", "warning")])
                    self.assertIn(expected, findings[0]["message"])
                    git(project, "push", "-q", "--force", "origin", f"{start}:refs/heads/main")

    # Round 3: post-merge Fence state, per-Delivery claims, deleted refs, cancellation reverts and own-line stops.

    def source_handoff(self, project: Path, _docs: Path) -> str:
        """A source handoff that leaves the target tip as it is and moves the Fence target to it."""
        delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
        tip = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        git(project, "push", "-q", "origin", f"{tip}:refs/heads/handoff-source")
        authorized = delivery_git.authorize_target_update(project, "source_handoff", "sha256:" + "b" * 64, "origin",
                                                          "direct_target", "refs/heads/handoff-source", "direct",
                                                          tip, tip, "upstream")
        delivery_git.mark_target_call_started(project, "source_handoff", authorized["attempt"])
        delivery_git.apply_target_update(project, "source_handoff")
        return delivery_git.finish_source_handoff(project)["target"]

    def test_a_handoff_after_the_provider_merge_is_post_merge_state_and_verify_merge_closes(self):
        """A Fence target that holds the merged record is neither a stop nor drift."""
        handoffs = {"governance": lambda project, docs: self.case.governance_target_handoff(project, docs)[0],
                    "source": self.source_handoff}
        for name, handoff in handoffs.items():
            with self.subTest(handoff=name):
                project, docs, _product, head, provider = self.recorded()
                provider(project).merge_commit(URL, head)
                git(project, "fetch", "-q", "origin", "main")
                git(project, "reset", "-q", "--hard", "origin/main")
                moved = handoff(project, docs)
                self.assertEqual(delivery_closure.fence_target(project, "origin"), moved)
                self.assertTrue(delivery_git.is_ancestor(project, head, moved))
                self.assertEqual(self.outcome(project)["outcome"], "merged_cleanup_pending")
                with mock.patch("delivery_provider.GitHubProvider", provider):
                    self.assertEqual(delivery_git.merge_pr(project, "DLV-001", verify_only=True)["status"], "merged")
                audited = delivery_closure.audit(project, "DLV-001")
                self.assertEqual((audited["ok"], audited["deliveries"][0]["outcome"]), (True, "closed"))

    def check_into(self, project: Path, head: str, base: str, head_ref: str) -> dict:
        """The check as CI runs it: the repository default branch, main, is the --target."""
        return delivery_closure.check_pull_request(project, head=head, base=base, url=URL.replace("17", "18"),
                                                   head_ref=head_ref, target="main")

    def test_claims_are_measured_from_each_deliverys_own_target_and_only_at_paths_the_pr_changes(self):
        """A prior develop change at a claimed path never makes an unrelated PR into develop managed."""
        project, _docs = self.pre_start()
        main = git(project, "rev-parse", "origin/main")
        develop = self.forge(project, main, self.auth("legacy = 1\n"), amend=False, message="Develop work")
        self.push_branch(project, "develop", develop)
        unrelated = self.forge(project, develop, lambda view: (view / "README.md").write_text(
            "fixture\nunrelated\n", encoding="utf-8"), amend=False, message="Document")
        self.push_branch(project, "feature/docs", unrelated)
        claimed = self.forge(project, develop, self.auth("def authenticate():\n    return 'direct'\n"),
                             amend=False, message="Change a claimed path")
        self.push_branch(project, "feature/auth", claimed)
        integration = delivery_git.remote_oid(project, "origin", "refs/heads/" + INTEGRATION)
        package = delivery_closure.package_directory(project, integration, "DLV-001")
        props, body = delivery_git.split_remote_note(project, integration, f"{package}/delivery.md",
                                                     delivery_compile.split_note)
        targeting_develop = self.commit_tree(project, self.tree_with(project, integration, {
            f"{package}/delivery.md": self.blob(project, delivery_compile.frontmatter(
                {**props, "target_branch": "develop"}, body))}), [integration], "Target develop\n")
        for target_branch, tip in (("main", integration), ("develop", targeting_develop)):
            git(project, "push", "-q", "--force", "origin", f"{tip}:refs/heads/{INTEGRATION}")
            with self.subTest(delivery_target=target_branch):
                checked = self.check_into(project, unrelated, "develop", "feature/docs")
                self.assertEqual((checked["managed"], checked["ok"]), (False, True), checked)
                checked = self.check_into(project, claimed, "develop", "feature/auth")
                self.assertEqual((checked["managed"], checked["ok"]), (True, False), checked)
                self.assertIn("under a path claim of AUTH-01 of DLV-001", " ".join(checked["reasons"]))
        git(project, "push", "-q", "--force", "origin", f"{integration}:refs/heads/{INTEGRATION}")
        promotion = self.check_into(project, develop, "main", "develop")
        self.assertEqual((promotion["managed"], promotion["ok"]), (True, False), promotion)

    def test_deleting_the_delivery_refs_after_a_provider_merge_never_skips_the_merged_proof(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        slipped = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"], {"src/evil.py": self.blob(project, "EVIL\n")}),
            [chain["reviewed"]], "Claim\n\nAgentrof-Record: claims-established-v1\nAgentrof-Protocol: 1\n"
                                 "Agentrof-Delivery: DLV-001\n")
        record = self.review_on(project, chain, slipped)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        provider(project).merge_commit(URL, record)
        for ref in git(project / "remote.git", "for-each-ref", "--format=%(refname)",
                       "refs/heads/agentrof/deliveries/", "refs/heads/agentrof/items/").split():
            git(project, "push", "-q", "origin", f":{ref}")
        outcome = self.outcome(project)
        self.assertEqual(outcome["outcome"], "unproven_record_merge")
        self.assertIn(f"the claims_established_v1 record {slipped} of DLV-001 changes the product paths src/evil.py",
                      " ".join(outcome["evidence"]))

    def two_path_cancellation(self) -> tuple[Path, str, str, type]:
        """A Delivery whose AUTH-01 merge changed two product paths, cancelled through the real flow.

        Returns the project, the recorded cancellation PR head, the reverted Item merge and the provider.
        """
        project, docs = self.pre_start()
        delivery_git.begin_plan_revision(project, "DLV-001")
        item = delivery_compile.find_delivery(docs, "DLV-001") / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery_compile.approve_execution(
                type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.finish_plan_revision(project, "DLV-001")
        worktree = Path(delivery_git.start_item(project, "DLV-001", "AUTH-01")["worktree"])
        (worktree / "src").mkdir(exist_ok=True)
        (worktree / "src" / "auth.py").write_text("def authenticate():\n    return 1\n", encoding="utf-8")
        (worktree / "src" / "session.py").write_text("SESSION = 1\n", encoding="utf-8")
        git(worktree, "add", "src")
        git(worktree, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "Implement")
        self.assertEqual(self.case.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        merge = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")["integration"]
        provider = self.case.fake_provider_type({})
        delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        delivery_git.prepare_pr_creation(project, "DLV-001")
        with mock.patch("delivery_provider.GitHubProvider", provider):
            head = delivery_git.open_pr(project, "DLV-001")["integration"]
        return project, head, merge, provider

    def test_a_cancellation_revert_must_restore_every_product_path_its_item_merge_changed(self):
        project, head, merge, provider = self.two_path_cancellation()
        first = git(project, "rev-parse", merge + "^1")
        self.assertEqual(sorted(path for path in delivery_closure.tree_delta(project, first, merge)
                                if delivery_closure.is_product_path(path)), ["src/auth.py", "src/session.py"])
        honest = self.check(project, head)
        self.assertEqual((honest["ok"], honest.get("errors")), (True, []), honest)
        chain = self.chain(project, head)
        line = delivery_closure.line_commits(project, chain["reviewed"], [merge])
        revert = next(oid for oid, _lineage, message in line
                      if delivery_git.trailer(message, "Record") == "cancellation-revert-v1")
        kept_paths = {"no-op revert": ("src/auth.py", "src/session.py"), "partial revert": ("src/session.py",)}
        for label, kept in kept_paths.items():
            with self.subTest(revert=label):
                changes = {path: self.entry(project, merge, path) for path in kept}
                rebuilt = git(project, "rev-parse", revert + "^1")
                for oid, lineage, message in line[[entry[0] for entry in line].index(revert):]:
                    self.assertEqual(len(lineage), 1)
                    rebuilt = self.commit_tree(project, self.tree_with(project, oid, changes), [rebuilt], message)
                    if oid == revert:
                        forged_revert = rebuilt
                record = self.review_on(project, chain, rebuilt)
                expected = (f"the cancellation revert {forged_revert} of DLV-001 leaves {', '.join(kept)} as the"
                            " reverted Item merge wrote them")
                git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
                self.assertIn(expected, " ".join(self.check(project, record)["errors"]))
                with mock.patch("delivery_provider.GitHubProvider", provider):
                    with self.assertRaisesRegex(RuntimeError, "merge-pr refuses a PR head the coordinator did not"):
                        delivery_git.merge_pr(project, "DLV-001")
                self.assertEqual(self.outcome(project)["outcome"], "awaiting_merge")
        git(project, "push", "-q", "--force", "origin", f"{head}:refs/heads/{INTEGRATION}")
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(self.outcome(project)["outcome"], "closed")

    def test_a_target_merge_whose_first_parent_is_the_records_own_line_is_no_proof_stop(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        slipped = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"], {"src/evil.py": self.blob(project, "EVIL\n")}),
            [chain["reviewed"]], "Claim\n\nAgentrof-Record: claims-established-v1\nAgentrof-Protocol: 1\n"
                                 "Agentrof-Delivery: DLV-001\n")
        record = self.review_on(project, chain, slipped)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        merge = self.commit_tree(project, record + "^{tree}", [slipped, record], "Merge pull request #17\n")
        git(project, "push", "-q", "--force", "origin", f"{merge}:refs/heads/main")
        expected = f"the target {slipped} the proof of DLV-001 would stop at lies on its recorded PR head's own line"
        outcome = self.outcome(project)
        self.assertEqual(outcome["outcome"], "unproven_record_merge")
        self.assertIn(expected, " ".join(outcome["evidence"]))
        with mock.patch("delivery_provider.GitHubProvider", provider):
            with self.assertRaisesRegex(RuntimeError, re.escape(expected)):
                delivery_git.merge_pr(project, "DLV-001", verify_only=True)

    def test_a_fast_forwarded_record_never_stops_at_a_fence_target_on_its_own_line(self):
        project, _docs, _product, head, provider = self.recorded()
        chain = self.chain(project, head)
        plain = self.commit_tree(
            project, self.tree_with(project, chain["reviewed"], {"src/evil.py": self.blob(project, "EVIL\n")}),
            [chain["reviewed"]], "Plain product change\n")
        fence = git(project, "ls-remote", "origin", "refs/heads/agentrof/fence").split()[0]
        forged_fence = self.commit_tree(project, fence + "^{tree}", [fence],
                                        self.retrailer(delivery_git.commit_message(project, fence), Target=plain))
        git(project, "push", "-q", "--force", "origin", f"{forged_fence}:refs/heads/agentrof/fence")
        record = self.review_on(project, chain, plain)
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/{INTEGRATION}")
        git(project, "push", "-q", "--force", "origin", f"{record}:refs/heads/main")
        checked = self.check(project, record)
        self.assertFalse(checked["ok"])
        self.assertIn(f"the Integration commit {plain} is not a control record of DLV-001", " ".join(checked["errors"]))
        self.assertNotEqual(self.outcome(project)["outcome"], "closed")
        self.assertIn(f"the Integration commit {plain} is not a control record of DLV-001", " ".join(
            delivery_closure.recorded_head_findings(project, "origin", "DLV-001", record, URL)))


@integration
class ExactBytesClassificationTests(unittest.TestCase):
    """The exact-bytes reason, read from a plain repository with one Item product tip given."""

    def commit(self, root: Path, files: dict, message: str) -> str:
        for name, content in files.items():
            if content is None:
                (root / name).unlink()
            else:
                (root / name).write_text(content, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", message)
        return git(root, "rev-parse", "HEAD")

    def test_an_item_deletion_counts_only_for_a_pr_that_deletes_the_path_itself(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            old = self.commit(root, {"x.py": "x = 0\n"}, "Before the file")
            start = self.commit(root, {"f.py": "f = 0\n"}, "Add the file")
            product = self.commit(root, {"f.py": None}, "The Item deletes the file")
            git(root, "checkout", "-q", "-b", "feature/old", old)
            unrelated = self.commit(root, {"x.py": "x = 1\n"}, "Unrelated work from before the file")
            git(root, "checkout", "-q", "-b", "feature/delete", start)
            deleting = self.commit(root, {"f.py": None}, "Delete the file")
            state = {"integrations": {"DLV-001": product}, "items": {}, "slots": {}, "fence_target": ""}
            reason = f"it carries the exact product bytes of the Item product tip {product} of DLV-001"
            with mock.patch.object(delivery_closure, "product_tips", return_value={product}), \
                    mock.patch.object(delivery_closure, "product_start", return_value=start):
                for head, managed in ((unrelated, False), (deleting, True)):
                    with self.subTest(managed=managed):
                        paths = delivery_closure.changed_paths(root, start, head)
                        classified = delivery_closure.classify(root, "origin", head, start, paths, "", state)
                        self.assertEqual((classified["managed"], reason in classified["reasons"]),
                                         (managed, managed), classified)



@integration
class AuditReaderTests(unittest.TestCase):
    """The audit's batched object reads and tree deltas answer exactly as the Git commands they stand in for."""

    def test_the_audit_reader_answers_as_the_git_commands_it_replaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            environment = {**os.environ, "GIT_INDEX_FILE": str(root / ".git" / "fixture-index"),
                           "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}

            def run(*args: str, data: bytes = b"") -> str:
                return subprocess.run(["git", *args], cwd=root, env=environment, input=data, capture_output=True,
                                      check=True).stdout.decode("utf-8").strip()

            def commit(entries: dict, removed: tuple = (), message: str = "State\n") -> str:
                for path in removed:
                    run("update-index", "--force-remove", "--", path)
                for path, (mode, content) in entries.items():
                    oid = content if mode == "160000" else run("hash-object", "-w", "--stdin", data=content)
                    run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
                parents = [arg for oid in commits[-1:] for arg in ("-p", oid)]
                return run("commit-tree", run("write-tree"), *parents, data=message.encode("utf-8"))

            commits: list[str] = []
            note = b"---\r\nstatus: approved\r\n---\r\nBody\r\n"
            commits.append(commit({"a": ("100644", b"1\n"), "a-b": ("100644", b"1\n"), "d/e/f": ("100644", b"x\n"),
                                   "sp ace": ("100644", b"y\n"), "\u00fcn\u00ef": ("100644", b"z\n"),
                                   "n.md": ("100644", note), "x[1]/y": ("100644", b"1\n")},
                                  message="First\r\n\r\nAgentrof-Delivery: DLV-001\r\n"))
            # A file becomes a directory, a mode changes, a symbolic link and a submodule appear.
            commits.append(commit({"a/x": ("100644", b"2\n"), "a-b": ("100755", b"1\n"), "link": ("120000", b"t"),
                                   "sub": ("160000", commits[0])}, removed=("a",)))
            # A directory becomes a file, and a submodule becomes a file.
            commits.append(commit({"d": ("100644", b"d\n"), "sub": ("100644", b"s\n"), "a": ("100644", b"3\n")},
                                  removed=("d/e/f", "sub", "a/x")))
            paths = ["a", "a-b", "a/x", "d", "d/e", "d/e/f", "sp ace", "\u00fcn\u00ef", "n.md", "x[1]/y", "link",
                     "sub", "missing", "a-b/c"]
            prefixes = ["a", "d", "d/e", "x[1]", "missing", "a-b"]

            def answers() -> dict:
                found = {}
                for oid in commits:
                    found[oid] = (delivery_closure.commit_message(root, oid), delivery_closure.parents(root, oid),
                                  delivery_closure.tree_of(root, oid), delivery_closure.blobs(root, oid, paths),
                                  [delivery_closure.tree_paths(root, oid, prefix) for prefix in prefixes],
                                  delivery_closure.tree_note(root, oid, "n.md"),
                                  delivery_closure.has_object(root, oid))
                    for other in commits:
                        found[oid, other] = delivery_closure.tree_delta(root, oid, other)
                        found["ancestor", oid, other] = delivery_closure.is_ancestor(root, oid, other)
                return found

            expected = answers()
            spawned: list = []
            popen = subprocess.Popen

            class Counted(popen):
                def __init__(self, args, *rest, **options):
                    spawned.append(args)
                    super().__init__(args, *rest, **options)

            with delivery_closure.reading_session(root), mock.patch.object(subprocess, "Popen", Counted):
                delivery_closure.prefetch_deltas(root, [(first, second) for first in commits for second in commits])
                self.assertEqual(answers(), expected)
            # One diff-tree reads every delta; only ancestry the parents cannot prove and a wildcard prefix,
            # which Git matches, ask Git again.
            self.assertEqual(sum("diff-tree" in args for args in spawned), 1, spawned)
            self.assertTrue(all("merge-base" in args or "diff-tree" in args or args[-1] == "x[1]/"
                                for args in spawned), spawned)
            self.assertIn("a/x", expected[commits[0], commits[1]])
            self.assertIn("d/e/f", expected[commits[1], commits[2]])


class ClosureTextTests(unittest.TestCase):
    """The instruction and template surfaces of the closure check, read as shipped."""

    @staticmethod
    def text(path: Path) -> str:
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_the_template_checks_out_the_base_and_passes_event_values_only_through_env(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(template,
                         r"(?m)^on:\n  pull_request_target:\n    types: \[opened, synchronize, reopened, edited\]$")
        self.assertRegex(template, r"(?m)^permissions:\n  contents: read\n\n")
        # The base branch tip that holds this workflow, not the PR's possibly older base.
        self.assertIn("ref: ${{ github.sha }}", template)
        self.assertNotIn("pull_request.base.sha", template)
        # A skipped job reports success to a required check, so no event may skip it.
        self.assertNotRegex(template, r"(?m)^\s+if:")
        self.assertIn("fetch-depth: 0", template)
        self.assertIn(f"    name: {delivery_closure.CLOSURE_CONTEXT}\n", template)
        self.assertIn('test "$(git rev-parse FETCH_HEAD)" = "$PR_HEAD_SHA"', template)
        self.assertIn("python3 .github/agentrof/vault-gate.pyz delivery-closure", template)
        # A detached checkout has no remote HEAD, so the workflow names the default branch as the target.
        self.assertIn("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}", template)
        self.assertIn('--target "$DEFAULT_BRANCH"', template)
        self.assertNotIn("pull_request:\n", template)
        lines = template.splitlines()
        for number, line in enumerate(lines):
            if line.strip().startswith("run:"):
                indent = len(line) - len(line.lstrip())
                block = [line]
                for following in lines[number + 1:]:
                    if following.strip() and len(following) - len(following.lstrip()) <= indent:
                        break
                    block.append(following)
                self.assertNotIn("${{", "\n".join(block))
                self.assertNotIn("${", "\n".join(block))

    def test_render_closure_ci_writes_the_template_as_it_ships(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vault_gate.main(["install", "--project-root", str(project)]), 0)
            output = project / ".github" / "workflows" / "delivery-closure.yml"
            output.parent.mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(operation_compile.main(["render-closure-ci", "--project-root", str(project),
                                                         "--output", str(output)]), 0)
            self.assertEqual(output.read_bytes(), TEMPLATE.read_bytes())

    def test_render_closure_ci_refuses_a_tracked_gate_without_the_closure_subcommand(self):
        """The workflow runs the base branch's archive, so one without the subcommand fails every PR."""
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            output = project / "delivery-closure.yml"
            archive = project / ".github" / "agentrof" / "vault-gate.pyz"
            archive.parent.mkdir(parents=True)
            for label, build in (("missing", None),
                                 ("older", lambda z: z.writestr("__main__.py", "sub.add_parser('check')\n"))):
                with self.subTest(archive=label):
                    if build is not None:
                        with zipfile.ZipFile(archive, "w") as written:
                            build(written)
                    printed = io.StringIO()
                    # The project root defaults to the working directory, where the owner renders it.
                    previous = Path.cwd()
                    os.chdir(project)
                    try:
                        with contextlib.redirect_stdout(printed):
                            code = operation_compile.main(["render-closure-ci", "--output", str(output)])
                    finally:
                        os.chdir(previous)
                    self.assertEqual(code, 2)
                    self.assertIn("vault_gate.py install", printed.getvalue())
                    self.assertFalse(output.exists())

    def test_the_portable_gate_carries_the_closure_check_and_runs_it_with_argument_values(self):
        scripts, data = vault_gate.packaged_closure(PACKAGE)
        self.assertTrue({"delivery_closure.py", "delivery_git.py", "delivery_provider.py"} <= set(scripts))
        self.assertIn("skill-content/deliver/data/delivery-control-record-contract.json", data)
        arguments = vault_gate.build_parser().parse_args([
            "delivery-closure", "--project-root", ".", "--pr-url", URL, "--head", "a" * 40,
            "--head-ref", "x; rm -rf /", "--base", "main", "--target", "main"])
        with mock.patch.object(vault_gate.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(arguments.func(arguments), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], [str(PACKAGE.resolve() / "scripts" / "delivery_git.py"), "closure-check"])
        self.assertEqual(command[command.index("--head-ref") + 1], "x; rm -rf /")
        self.assertEqual(command[command.index("--target") + 1], "main")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_closure_finding_codes_are_declared_by_the_result_contract(self):
        contract = json.loads((PACKAGE / "skill-content/deliver/data/delivery-result-contract.json")
                              .read_text(encoding="utf-8"))["finding_codes"]
        for code in ("DELIVERY_CLOSURE_INCOMPLETE", "DELIVERY_EXTERNAL_MERGE", "DELIVERY_PROTECTION_STATUS",
                     "DELIVERY_CLOSURE_GATE_CHANGED"):
            self.assertIn(code, contract)
            self.assertIn(code, delivery_result.FINDING_CODES)

    def test_an_unknown_control_record_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "DELIVERY_PROTOCOL_UNSUPPORTED"):
            delivery_closure.record_kind("Subject\n\nAgentrof-Record: provisional-claim-v9\n")
        self.assertEqual(delivery_closure.record_kind("S\n\nAgentrof-Record: pr-url-recorded-v1\n"),
                         "pr_url_recorded_v1")
        self.assertIsNone(delivery_closure.record_kind("A product commit\n"))

    def test_path_claims_compare_case_folded(self):
        self.assertTrue(delivery_closure.claims_cover("SRC/Auth.py", ["src/auth.py"]))
        self.assertTrue(delivery_closure.claims_cover("src/auth/login.py", ["src/auth"]))
        self.assertFalse(delivery_closure.claims_cover("src/authz.py", ["src/auth"]))
        self.assertFalse(delivery_closure.claims_cover("src/auth.py", ["../src"]))

    def test_the_fence_takeover_refusal_names_each_holders_recovery(self):
        with self.assertRaises(RuntimeError) as refused:
            delivery_git.require_fence_takeover(
                {"Mode": "open", "Barrier-Kind": "none", "Source-Intent": "none", "Target-Update-Intent": "none",
                 "Governance-Hash": "none"},
                lambda: "a" * 40 + "\trefs/heads/agentrof/deliveries/dlv-002\n"
                        + "b" * 40 + "\trefs/heads/agentrof/slots/001", lambda: "none")
        message = str(refused.exception)
        self.assertIn("agentrof/deliveries/dlv-002 (recovery: closure-audit --delivery DLV-002", message)
        self.assertIn("agentrof/slots/001 (recovery: the Delivery whose Item it holds", message)

    def test_the_deliver_entry_reports_completion_only_from_a_closed_audit(self):
        skill = self.text(PACKAGE / "skill-content/deliver/SKILL.md")
        flow = self.text(PACKAGE / "flows/delivery-execution.md")
        for document in (skill, flow):
            self.assertIn("Report a Delivery complete only when `closure-audit` returns `closed`", document)
            self.assertIn("merge only through `merge-pr` on the recorded pr head", document.lower())
            self.assertIn("never waive", document)
        self.assertIn("`/deliver DLV-### status` also runs `closure-audit --delivery DLV-###`", skill)

    def test_planning_reveals_an_incomplete_prior_closure_before_the_handoff(self):
        for path in (PACKAGE / "flows/delivery-planning.md", PACKAGE / "skill-content/delivery-plan/SKILL.md"):
            with self.subTest(path=path.name):
                self.assertIn("closure-audit --all", self.text(path))

    def test_protection_is_described_as_a_report_that_guarantees_nothing(self):
        for path in (PACKAGE / "skill-content/setup/references/ci-bootstrap.md",
                     ROOT / "docs/requirement-delivery-protocol.md"):
            with self.subTest(path=path.name):
                text = self.text(path)
                self.assertIn("protection-status", text)
                self.assertIn("ruleset", text)
                self.assertIn("no bypass actors", text)
                self.assertIn("GitHub Actions app", text)
                self.assertIn("same-name job", text)
        self.assertIn("no command of this package can", delivery_closure.PROTECTION_LIMIT)
        self.assertIn("integration_id 15368", delivery_closure.PROTECTION_LIMIT)
        bootstrap = self.text(PACKAGE / "skill-content/setup/references/ci-bootstrap.md")
        for text in ("`reopened` and `edited`", "red on `opened`", "re-run the failed check",
                     "vault_gate.py install", "CODEOWNERS", "DELIVERY_CLOSURE_GATE_CHANGED"):
            self.assertIn(text, bootstrap)
        self.assertIn("re-run the failed check", self.text(PACKAGE / "skill-content/deliver/SKILL.md"))

    def test_the_trust_boundary_and_the_required_agentrof_ref_protection_are_stated(self):
        """Closure proves record shape and binding, never authorship, so the agentrof refs need protection."""
        for path in (PACKAGE / "skill-content/setup/references/ci-bootstrap.md",
                     ROOT / "docs/requirement-delivery-protocol.md"):
            with self.subTest(path=path.name):
                text = self.text(path)
                for phrase in ("does not prove who wrote a Review", "can write consistent notes that pass",
                               "`refs/heads/agentrof/**`", "part of the required setup",
                               "only required to be non-product paths"):
                    self.assertIn(phrase, text)
                self.assertNotIn("may also protect", text)
                # Deletion and force-push rules alone let a writer create refs or fast-forward forged notes.
                for phrase in ("restricts creations", "restricts updates", "restricts deletions",
                               "blocks non-fast-forward pushes",
                               "only the accounts that run the coordinator as bypass actors",
                               "`merge-pr` and `verify-merge` delete the Integration and Item refs",
                               "`integrate-item`, `pause-item` and `cancel-delivery` delete Slot refs",
                               "`refresh-target` re-issues Item claims", "non-fast-forward updates",
                               "`--force-with-lease`"):
                    self.assertIn(phrase, text)
        bootstrap = self.text(PACKAGE / "skill-content/setup/references/ci-bootstrap.md")
        self.assertIn("A property reads `not_configured` only when both the branch rules and the classic branch"
                      " protection are readable", bootstrap)


if __name__ == "__main__":
    unittest.main()
