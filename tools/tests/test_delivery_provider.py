"""Provider boundary tests that never require network credentials."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
import delivery_provider  # noqa: E402
import delivery_result  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402


@integration
class DeliveryProviderTests(unittest.TestCase):
    def test_repository_normalizes_https_and_scp_github_remotes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "git@github.com:agentrof/example.git"], check=True)
            self.assertEqual(delivery_provider.repository_from_remote(root), "agentrof/example")

    def test_canonical_pr_url_rejects_query_fragment_and_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://gitlab.com/a/b.git"], check=True)
            with self.assertRaises(delivery_provider.ProviderError):
                delivery_provider.repository_from_remote(root)
        with self.assertRaises(ValueError):
            # Use the same closed grammar exposed by the Git coordinator.
            import delivery_git
            delivery_git.canonical_github_pr("https://github.com/a/b/pull/0")
        import delivery_git
        self.assertEqual(
            delivery_git.canonical_github_pr("https://github.com/a/b/pull/17"),
            ("https://github.com/a/b/pull/17", "17"),
        )
        with self.assertRaises(ValueError):
            delivery_git.canonical_github_pr("https://github.com/a/b/pull/17?x=1")

    def test_merge_commit_requires_provider_confirmed_merge_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/agentrof/example.git"], check=True)
            response = {
                "url": "https://github.com/agentrof/example/pull/17",
                "state": "MERGED",
                "headRefOid": "a" * 40,
                "mergeCommit": {"oid": "b" * 40},
            }
            with patch.object(delivery_provider, "run_gh", side_effect=["", json.dumps(response)]) as gh:
                result = delivery_provider.GitHubProvider(root).merge_commit(
                    response["url"], "a" * 40
                )
            self.assertEqual(result["merge_commit"], "b" * 40)
            self.assertEqual(gh.call_count, 2)

    def test_update_body_sends_only_the_body_to_the_exact_pr(self):
        """The body reaches GitHub's pull request update as JSON on standard input, not through
        gh pr edit, and GitHub's answer must name the same PR."""
        provider = self.github_provider()
        url = "https://github.com/agentrof/example/pull/17"
        body = "## Verdict\n\nCancellation approved and finalized with exact Item dispositions.\n"
        answer = subprocess.CompletedProcess([], 0, json.dumps({"html_url": url, "body": body}).encode("utf-8"), b"")
        with patch.object(delivery_provider.shutil, "which", return_value="/usr/bin/gh"), \
                patch.object(delivery_provider.subprocess, "run", return_value=answer) as run:
            self.assertEqual(provider.update_body(url, body), {"url": url})
        self.assertEqual(run.call_args.args, (["gh", "api", "--hostname", "github.com", "--method", "PATCH",
                                               "repos/agentrof/example/pulls/17", "--input", "-"],))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"body": body})

    def github_provider(self) -> delivery_provider.GitHubProvider:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/agentrof/example.git"], check=True)
        return delivery_provider.GitHubProvider(root)

    @staticmethod
    def check_run(name: str, status: str = "COMPLETED", conclusion: str | None = "SUCCESS") -> dict:
        """One CheckRun entry in the shape `gh pr view --json statusCheckRollup` prints."""
        return {"__typename": "CheckRun", "name": name, "workflowName": "validate",
                "status": status, "conclusion": conclusion}

    @staticmethod
    def commit_status(context: str, state: str | None) -> dict:
        """One StatusContext entry: a commit status has a state and no conclusion."""
        return {"__typename": "StatusContext", "context": context, "state": state,
                "targetUrl": "https://ci.example/builds/1"}

    def test_green_commit_status_passes_without_a_conclusion(self):
        provider = self.github_provider()
        provider.require_green_checks({"statusCheckRollup": [self.commit_status("ci/build", "SUCCESS")]})
        provider.require_green_checks({"statusCheckRollup": [
            self.check_run("tests"), self.commit_status("ci/build", "SUCCESS"),
        ]})
        # gh before 2.14 printed empty check-run fields on every entry.
        legacy = {**self.commit_status("ci/build", "SUCCESS"), "name": "", "status": "", "conclusion": ""}
        provider.require_green_checks({"statusCheckRollup": [legacy]})

    def test_pending_or_failed_commit_status_blocks(self):
        provider = self.github_provider()
        for state in ("PENDING", "EXPECTED", "FAILURE", "ERROR", None):
            with self.subTest(state=state):
                with self.assertRaisesRegex(delivery_provider.ProviderError,
                                            rf"not green: ci/build \({state or 'no state'}\)"):
                    provider.require_green_checks({"statusCheckRollup": [
                        self.check_run("tests"), self.commit_status("ci/build", state),
                    ]})

    def test_skipped_or_neutral_check_run_does_not_block_a_green_rollup(self):
        provider = self.github_provider()
        for conclusion in ("SKIPPED", "NEUTRAL"):
            with self.subTest(conclusion=conclusion):
                provider.require_green_checks({"statusCheckRollup": [
                    self.check_run("check"), self.check_run("release-pr-policy", conclusion=conclusion),
                ]})

    def test_skipped_or_neutral_checks_alone_cannot_authorize_the_merge(self):
        provider = self.github_provider()
        skipped = self.check_run("release-pr-policy", conclusion="SKIPPED")
        neutral = self.check_run("advisory", conclusion="NEUTRAL")
        for rollup in ([skipped], [neutral], [skipped, neutral]):
            with self.subTest(rollup=rollup):
                with self.assertRaisesRegex(delivery_provider.ProviderError, "no successful check"):
                    provider.require_green_checks({"statusCheckRollup": rollup})

    def test_completed_check_run_without_conclusion_blocks(self):
        provider = self.github_provider()
        absent = self.check_run("tests")
        del absent["conclusion"]
        for check in (self.check_run("tests", conclusion=None), self.check_run("tests", conclusion=""), absent):
            with self.subTest(check=check):
                with self.assertRaisesRegex(delivery_provider.ProviderError,
                                            r"not green: tests \(no conclusion\)"):
                    provider.require_green_checks({"statusCheckRollup": [self.check_run("check"), check]})

    def test_unfinished_or_unsuccessful_check_run_blocks_a_green_rollup(self):
        provider = self.github_provider()
        for status, conclusion in (
            ("QUEUED", ""), ("IN_PROGRESS", ""), ("PENDING", ""), ("REQUESTED", ""), ("WAITING", ""),
            ("COMPLETED", "FAILURE"), ("COMPLETED", "CANCELLED"), ("COMPLETED", "TIMED_OUT"),
            ("COMPLETED", "ACTION_REQUIRED"), ("COMPLETED", "STALE"), ("COMPLETED", "STARTUP_FAILURE"),
        ):
            reported = conclusion if status == "COMPLETED" else status
            with self.subTest(status=status, conclusion=conclusion):
                with self.assertRaisesRegex(delivery_provider.ProviderError,
                                            rf"not green: tests \({reported}\)"):
                    provider.require_green_checks({"statusCheckRollup": [
                        self.check_run("check"), self.check_run("tests", status, conclusion),
                    ]})

    def test_unknown_rollup_entry_type_blocks(self):
        provider = self.github_provider()
        unknown = {"__typename": "Deployment", "name": "preview", "status": "COMPLETED", "conclusion": "SUCCESS"}
        with self.assertRaisesRegex(delivery_provider.ProviderError,
                                    r"not green: preview \(unsupported type Deployment\)"):
            provider.require_green_checks({"statusCheckRollup": [self.check_run("check"), unknown]})

    def test_every_check_refusal_reports_the_required_check_finding(self):
        provider = self.github_provider()
        for rollup in (None, [], ["not an entry"], [self.check_run("tests", "IN_PROGRESS", "")],
                       [self.check_run("release-pr-policy", conclusion="SKIPPED")]):
            with self.subTest(rollup=rollup):
                with self.assertRaises(delivery_provider.ProviderError) as refused:
                    provider.require_green_checks({"statusCheckRollup": rollup})
                result = delivery_result.from_raw("merge-pr", {"ok": False, "errors": [str(refused.exception)]})
                self.assertEqual([finding["code"] for finding in result["findings"]],
                                 ["DELIVERY_REQUIRED_CHECK_FAILED"])
                self.assertTrue(result["findings"][0]["message"].startswith("GitHub required check"))

    def test_provider_refusals_report_their_finding_codes(self):
        """A provider refusal reaches the result envelope under its own code, with its words intact."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://gitlab.com/a/b.git"], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "owner", "https://github.com/agentrof"], check=True)
            provider = self.github_provider()
            url = "https://github.com/agentrof/example/pull/17"

            def without_gh():
                with patch.object(delivery_provider.shutil, "which", return_value=None):
                    delivery_provider.run_gh(root, "pr", "list")

            def when_gh_prints(output, call):
                with patch.object(delivery_provider, "run_gh", return_value=output):
                    call()

            def merge_then_read(response):
                with patch.object(delivery_provider, "run_gh", side_effect=["", json.dumps(response)]):
                    provider.merge_commit(url, "a" * 40)

            for code, message, refusal in (
                ("DELIVERY_PROVIDER_UNSUPPORTED", "Delivery PR provider requires a GitHub remote",
                 lambda: delivery_provider.repository_from_remote(root)),
                ("DELIVERY_PROVIDER_UNSUPPORTED", "GitHub remote must identify exactly owner/repository",
                 lambda: delivery_provider.repository_from_remote(root, "owner")),
                ("DELIVERY_PROVIDER_UNSUPPORTED", "GitHub provider requires the authenticated gh CLI", without_gh),
                ("DELIVERY_PR_UNCERTAIN", "GitHub returned invalid PR JSON",
                 lambda: when_gh_prints("not json", lambda: provider.list_pull_requests("head", "main"))),
                ("DELIVERY_PR_UNCERTAIN", "GitHub did not return a canonical PR URL",
                 lambda: when_gh_prints("", lambda: provider.create_draft("head", "main", "Title", "Body"))),
                ("DELIVERY_PR_UNCERTAIN", "GitHub returned invalid PR JSON",
                 lambda: when_gh_prints("not json", lambda: provider.update_body(url, "Body"))),
                ("DELIVERY_PR_UNCERTAIN", "GitHub did not confirm the PR body update",
                 lambda: when_gh_prints(json.dumps({"html_url": "https://github.com/agentrof/example/pull/18"}),
                                        lambda: provider.update_body(url, "Body"))),
                ("DELIVERY_MERGE_PROOF_INVALID", "GitHub PR merge call returned before the PR was merged",
                 lambda: merge_then_read({"url": url, "state": "OPEN", "headRefOid": "a" * 40})),
                ("DELIVERY_MERGE_PROOF_INVALID", "GitHub PR has no provider-confirmed merge commit",
                 lambda: merge_then_read({"url": url, "state": "MERGED", "headRefOid": "a" * 40})),
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "GitHub PR head changed during merge",
                 lambda: merge_then_read({"url": url, "state": "MERGED", "headRefOid": "c" * 40,
                                          "mergeCommit": {"oid": "b" * 40}})),
            ):
                with self.subTest(message=message):
                    with self.assertRaises(delivery_provider.ProviderError) as refused:
                        refusal()
                    result = delivery_result.from_raw("merge-pr", {"ok": False, "errors": [str(refused.exception)]})
                    self.assertEqual([(finding["code"], finding["message"]) for finding in result["findings"]],
                                     [(code, message)])


class ProtectionStatusTests(unittest.TestCase):
    """The read-only repository protection report over GitHub's rules, rulesets and classic protection."""

    RULES = "repos/agentrof/example/rules/branches/main"
    RULESET = "repos/agentrof/example/rulesets/7"
    CLASSIC = "repos/agentrof/example/branches/main/protection"
    CONTEXT_RULE = {"type": "required_status_checks", "ruleset_id": 7,
                    "parameters": {"required_status_checks": [{"context": "delivery-closure",
                                                               "integration_id": 15368}]}}
    NAME_RULE = {"type": "required_status_checks", "ruleset_id": 7,
                 "parameters": {"required_status_checks": [{"context": "delivery-closure"}]}}
    PULL_REQUEST_RULE = {"type": "pull_request", "ruleset_id": 7}
    NOT_PROTECTED = delivery_provider.ProviderError("gh: Branch not protected (HTTP 404)")
    FORBIDDEN = delivery_provider.ProviderError("gh: Resource not accessible by integration (HTTP 403)")

    @staticmethod
    def provider() -> delivery_provider.GitHubProvider:
        provider = object.__new__(delivery_provider.GitHubProvider)
        provider.root, provider.remote, provider.repository = Path("."), "origin", "agentrof/example"
        return provider

    def report(self, answers: dict) -> dict:
        """Run protection_status with each gh api path answered from *answers*, a 404 otherwise.

        An answer that is an exception is raised as gh's refusal for that path.
        """
        import delivery_closure

        def gh(_root, *args, input_text=None):
            self.assertEqual(args[0], "api")
            value = answers.get(args[1])
            if value is None:
                raise delivery_provider.ProviderError("gh: Not Found (HTTP 404)")
            if isinstance(value, Exception):
                raise value
            return json.dumps(value)

        with patch.object(delivery_provider, "run_gh", side_effect=gh):
            report = delivery_closure.protection_status(Path("."), "main", provider=self.provider())
        for value in (report["status"], *report["properties"].values()):
            self.assertIn(value, delivery_closure.PROTECTION_STATES)
        return report

    def test_a_ruleset_requiring_the_actions_check_without_bypass_actors_is_configured(self):
        report = self.report({self.RULES: [self.CONTEXT_RULE, self.PULL_REQUEST_RULE],
                              self.RULESET: {"id": 7, "bypass_actors": []}, self.CLASSIC: self.NOT_PROTECTED})
        self.assertEqual((report["ok"], report["status"]), (True, "configured"))
        self.assertEqual(set(report["properties"].values()), {"configured"})
        finding = delivery_result.from_raw("protection-status", report)["findings"][0]
        self.assertEqual((finding["code"], finding["severity"]), ("DELIVERY_PROTECTION_STATUS", "info"))
        self.assertIn("no command of this package can", finding["message"])

    def test_a_check_required_by_name_only_is_not_configured_with_its_reason(self):
        """A commit status or a same-name job meets a check that names no app."""
        for rules in ([self.NAME_RULE, self.PULL_REQUEST_RULE],
                      [{"type": "required_status_checks", "ruleset_id": 7, "parameters": {
                          "required_status_checks": [{"context": "delivery-closure", "integration_id": 99}]}},
                       self.PULL_REQUEST_RULE]):
            with self.subTest(rules=rules[0]["parameters"]):
                report = self.report({self.RULES: rules, self.RULESET: {"id": 7, "bypass_actors": []},
                                      self.CLASSIC: self.NOT_PROTECTED})
                self.assertEqual((report["status"], report["properties"]["closure_context_required"]),
                                 ("not_configured", "not_configured"))
                self.assertIn("a commit status or a same-name job", report["reasons"]["closure_context_required"])
                self.assertIn("required by name only",
                              delivery_result.from_raw("protection-status", report)["findings"][0]["message"])
        classic = self.report({self.RULES: [], self.CLASSIC: {
            "required_status_checks": {"contexts": ["delivery-closure"]},
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "enforce_admins": {"enabled": True}}})
        self.assertEqual(classic["properties"]["closure_context_required"], "not_configured")

    def test_a_workflows_rule_counts_only_for_this_repositorys_closure_workflow(self):
        def workflows(repository_id: int) -> dict:
            return {"type": "workflows", "ruleset_id": 7, "parameters": {"workflows": [
                {"path": ".github/workflows/delivery-closure.yml", "repository_id": repository_id, "ref": "main"}]}}

        for repository_id, expected in ((41, "configured"), (42, "not_configured")):
            with self.subTest(repository_id=repository_id):
                report = self.report({self.RULES: [workflows(repository_id), self.PULL_REQUEST_RULE],
                                      self.RULESET: {"id": 7, "bypass_actors": []},
                                      "repos/agentrof/example": {"id": 41}, self.CLASSIC: self.NOT_PROTECTED})
                self.assertEqual(report["properties"]["closure_context_required"], expected)

    def test_bypass_actors_or_a_missing_rule_are_not_configured(self):
        bypassed = self.report({self.RULES: [self.CONTEXT_RULE, self.PULL_REQUEST_RULE],
                                self.RULESET: {"id": 7, "bypass_actors": [{"actor_type": "RepositoryRole"}]}})
        self.assertEqual((bypassed["status"], bypassed["properties"]["no_bypass"]), ("not_configured", "not_configured"))
        for classic in (self.NOT_PROTECTED, self.FORBIDDEN):
            with self.subTest(classic=str(classic)):
                unprotected = self.report({self.RULES: [], self.CLASSIC: classic})
                self.assertEqual(unprotected["status"], "not_configured")
                self.assertEqual(unprotected["properties"]["closure_context_required"], "not_configured")
                self.assertEqual(delivery_result.from_raw("protection-status", unprotected)["findings"][0]["severity"],
                                 "warning")

    def test_unreadable_branch_rules_or_bypass_actors_report_unknown(self):
        for answers in ({}, {self.RULES: self.FORBIDDEN, self.CLASSIC: self.NOT_PROTECTED}):
            with self.subTest(answers=sorted(answers)):
                report = self.report(answers)
                self.assertEqual((report["ok"], report["status"]), (True, "unknown"))
        hidden = self.report({self.RULES: [self.CONTEXT_RULE], self.RULESET: {"id": 7}})
        self.assertEqual((hidden["properties"]["closure_context_required"], hidden["properties"]["no_bypass"]),
                         ("configured", "unknown"))

    def test_classic_protection_counts_when_pinned_to_actions_with_admin_enforcement(self):
        report = self.report({self.RULES: [], self.CLASSIC: {
            "required_status_checks": {"checks": [{"context": "delivery-closure", "app_id": 15368}]},
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "enforce_admins": {"enabled": True}}})
        self.assertEqual(report["status"], "configured")

    def test_only_branch_not_protected_reads_as_absent_classic_protection(self):
        """GitHub answers 404 both for a readable unprotected branch and for a hidden protection."""
        for refusal, expected in ((self.NOT_PROTECTED, {}), (self.FORBIDDEN, None),
                                  (delivery_provider.ProviderError("gh: Not Found (HTTP 404)"), None)):
            with self.subTest(refusal=str(refusal)):
                with patch.object(delivery_provider, "run_gh", side_effect=refusal):
                    self.assertEqual(self.provider().branch_protection("main"), expected)

    def test_branch_names_are_one_encoded_path_segment(self):
        with patch.object(delivery_provider, "run_gh", return_value="[]") as gh:
            self.assertEqual(self.provider().branch_rules("release/2026"), [])
        self.assertEqual(gh.call_args.args[1:], ("api", "repos/agentrof/example/rules/branches/release%2F2026"))


if __name__ == "__main__":
    unittest.main()
