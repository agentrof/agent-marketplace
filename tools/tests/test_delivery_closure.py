"""Delivery closure: managed pull requests, their readiness, the closure audit and the opt-in CI check (#461)."""

from __future__ import annotations

import contextlib
import io
import json
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
    from tools.tests.git_fixture import remove_temporary
except ModuleNotFoundError:  # run as a script from tools/tests
    import test_delivery_git as delivery_tests
    from git_fixture import remove_temporary

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
        return audited["deliveries"][0] if audited["deliveries"] else {"errors": audited["errors"]}

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
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.merge_pr(project, "DLV-001", verify_only=True)
        self.assertEqual(self.outcome(project)["outcome"], "closed")

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
                self.assertEqual((audited["ok"], outcome["outcome"]), (False, "external_product_merge"))
                self.assertEqual(outcome["leftovers"], ["Slot agentrof/slots/001"])
                self.assertEqual(delivery_result.from_raw("closure-audit", audited)["findings"][0]["code"],
                                 "DELIVERY_EXTERNAL_MERGE")
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


class ClosureTextTests(unittest.TestCase):
    """The instruction and template surfaces of the closure check, read as shipped."""

    @staticmethod
    def text(path: Path) -> str:
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_the_template_checks_out_the_base_and_passes_event_values_only_through_env(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(template, r"(?m)^on:\n  pull_request_target:\n    types: \[opened, synchronize, reopened\]$")
        self.assertRegex(template, r"(?m)^permissions:\n  contents: read\n\n")
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", template)
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
            output = Path(temporary) / "delivery-closure.yml"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(operation_compile.main(["render-closure-ci", "--output", str(output)]), 0)
            self.assertEqual(output.read_bytes(), TEMPLATE.read_bytes())

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
        for code in ("DELIVERY_CLOSURE_INCOMPLETE", "DELIVERY_EXTERNAL_MERGE", "DELIVERY_PROTECTION_STATUS"):
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
        self.assertIn("no command of this package can", delivery_closure.PROTECTION_LIMIT)


if __name__ == "__main__":
    unittest.main()
