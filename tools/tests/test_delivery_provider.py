"""Provider boundary tests that never require network credentials."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
import delivery_provider  # noqa: E402
import delivery_result  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402


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

    def test_merge_commit_rejects_provider_without_merge_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/agentrof/example.git"], check=True)
            response = {"url": "https://github.com/agentrof/example/pull/17", "state": "MERGED"}
            with patch.object(delivery_provider, "run_gh", side_effect=["", json.dumps(response)]):
                with self.assertRaises(delivery_provider.ProviderError):
                    delivery_provider.GitHubProvider(root).merge_commit(
                        response["url"], "a" * 40
                    )

    def test_merge_commit_rejects_changed_provider_head(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/agentrof/example.git"], check=True)
            response = {
                "url": "https://github.com/agentrof/example/pull/17",
                "state": "MERGED",
                "headRefOid": "c" * 40,
                "mergeCommit": {"oid": "b" * 40},
            }
            with patch.object(delivery_provider, "run_gh", side_effect=["", json.dumps(response)]):
                with self.assertRaises(delivery_provider.ProviderError):
                    delivery_provider.GitHubProvider(root).merge_commit(
                        response["url"], "a" * 40
                    )

    def test_required_checks_must_be_complete_and_successful(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/agentrof/example.git"], check=True)
            provider = delivery_provider.GitHubProvider(root)
            provider.require_green_checks({
                "statusCheckRollup": [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
            })
            for check in (
                {"name": "tests", "status": "IN_PROGRESS", "conclusion": None},
                {"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"},
            ):
                with self.assertRaises(delivery_provider.ProviderError):
                    provider.require_green_checks({"statusCheckRollup": [check]})
            with self.assertRaises(delivery_provider.ProviderError):
                provider.require_green_checks({"statusCheckRollup": []})

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


if __name__ == "__main__":
    unittest.main()
