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



@integration
class ExternalEvidenceTests(unittest.TestCase):
    """Product bytes on the target, read from a plain repository with the Item's tips given."""

    def commit(self, root: Path, files: dict[str, str], message: str) -> str:
        for name, content in files.items():
            (root / name).write_text(content, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", message)
        return git(root, "rev-parse", "HEAD")

    def test_a_partial_hand_merge_names_the_paths_the_target_holds_and_never_proves_a_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            start = self.commit(root, {"a.py": "a = 0\n", "b.py": "b = 0\n", "c.py": "c = 1\n"}, "Start")
            product = self.commit(root, {"a.py": "a = 1\n", "b.py": "b = 1\n", "c.py": "c = 2\n"}, "Item")
            git(root, "checkout", "-q", start)
            # c.py already held the Item's bytes on the Fence target, so it proves nothing.
            fence = self.commit(root, {"c.py": "c = 2\n"}, "Same c")
            partial = self.commit(root, {"a.py": "a = 1\n"}, "Copy a by hand")
            full = self.commit(root, {"b.py": "b = 1\n"}, "Copy b by hand")
            with mock.patch.object(delivery_closure, "product_tips", return_value={product}), \
                    mock.patch.object(delivery_closure, "product_start", return_value=start):
                evidence, signs = delivery_closure.external_product_merge(root, "DLV-001", partial, "", [], fence)
                self.assertEqual(evidence, [])
                self.assertEqual(len(signs), 1)
                self.assertIn(f"product tip {product} at a.py but not at b.py, which a partial hand merge"
                              " leaves", signs[0])
                evidence, signs = delivery_closure.external_product_merge(root, "DLV-001", full, "", [], fence)
                self.assertEqual(evidence, [])
                self.assertIn("at a.py, b.py, which an independent change can also write", signs[0])


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
            "--head-ref", "x; rm -rf /", "--base", "main"])
        with mock.patch.object(vault_gate.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(arguments.func(arguments), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], [str(PACKAGE.resolve() / "scripts" / "delivery_git.py"), "closure-check"])
        self.assertEqual(command[command.index("--head-ref") + 1], "x; rm -rf /")
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


if __name__ == "__main__":
    unittest.main()
