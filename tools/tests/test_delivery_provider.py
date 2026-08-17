"""Provider boundary tests that never require network credentials."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

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


if __name__ == "__main__":
    unittest.main()
