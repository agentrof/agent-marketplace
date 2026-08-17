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


class DeliveryProviderTests(unittest.TestCase):
    def test_repository_normalizes_https_and_scp_github_remotes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "git@github.com:agentrof/example.git"], check=True)
            self.assertEqual(delivery_provider.repository_from_remote(root), "agentrof/example")

    def test_canonical_pr_url_rejects_query_fragment_and_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
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
            subprocess.run(["git", "init", "-q", str(root)], check=True)
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
            subprocess.run(["git", "init", "-q", str(root)], check=True)
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
            subprocess.run(["git", "init", "-q", str(root)], check=True)
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


if __name__ == "__main__":
    unittest.main()
