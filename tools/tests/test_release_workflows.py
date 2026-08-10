"""Static fail-closed contracts for GitHub release automation."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


class ReleaseWorkflowContracts(unittest.TestCase):
    def text(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_prepare_is_manual_pat_free_and_never_merges(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn("workflow_dispatch", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("gh pr merge", text)
        self.assertIn("gh pr create", text)
        self.assertNotIn("gh workflow run validate.yml", text)
        self.assertIn(
            "GitHub Actions is not permitted to create or approve pull requests",
            text,
        )
        self.assertIn(
            "compare/main...release/stable?expand=1", text
        )
        self.assertIn("Manual release PR required", text)
        self.assertLess(text.index("git push origin HEAD:refs/heads/release/stable"),
                        text.index("gh pr create"))

    def test_bootstrap_requires_empty_tag_space_and_uses_atomic_refs(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn("verify-bootstrap", text)
        self.assertIn("refs/tags/v*", text)
        self.assertIn("git push --atomic origin HEAD:refs/heads/stable", text)
        self.assertIn("make public-release-check", text)
        self.assertIn(":refs/heads/stable :refs/tags/v0.0.1", text)

    def test_publish_binds_exact_merge_stable_base_and_tag_collision(self):
        text = self.text("publish-stable-release.yml")
        for required in (
            "merge_commit_sha", "test \"$remote_stable\" = \"$stable_base\"",
            "merge-base --is-ancestor", "refs/tags/v${version}",
            "gh release view", "make release-check", "verify-release",
            "git push --atomic origin HEAD:refs/heads/stable",
        ):
            self.assertIn(required, text)
        self.assertLess(text.index("make release-check"),
                        text.index("git push --atomic origin HEAD:refs/heads/stable"))
        self.assertLess(text.index("gh release view"),
                        text.index("git push --atomic origin HEAD:refs/heads/stable"))

    def test_main_validation_uploads_build_id_and_both_hosts(self):
        text = self.text("validate.yml")
        self.assertIn("tools/release.py build-info", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("dist/claude", text)
        self.assertIn("dist/codex", text)


if __name__ == "__main__":
    unittest.main()
