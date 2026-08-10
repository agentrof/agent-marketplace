"""Static fail-closed contracts for GitHub release automation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
NODE24_ACTIONS = {
    "checkout": "v5",
    "setup-python": "v6",
    "setup-node": "v5",
    "upload-artifact": "v6",
}
OFFICIAL_ACTION_RE = re.compile(
    r"uses:\s+actions/"
    r"(checkout|setup-python|setup-node|upload-artifact)@([^\s#]+)"
)


def node24_runtime_findings(name: str, text: str) -> list[str]:
    findings: list[str] = []
    for action, version in OFFICIAL_ACTION_RE.findall(text):
        expected = NODE24_ACTIONS[action]
        if version != expected:
            findings.append(
                f"{name}: actions/{action} must use {expected}, found {version}"
            )
    if 'node-version: "20"' in text:
        findings.append(f"{name}: job Node.js must not use 20")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "uses: actions/setup-node@" not in line:
            continue
        inputs = "\n".join(lines[index + 1:index + 5])
        if 'node-version: "24"' not in inputs:
            findings.append(f"{name}: setup-node must select Node.js 24")
        if "package-manager-cache: false" not in inputs:
            findings.append(f"{name}: setup-node must disable package caching")
    return findings


class ReleaseWorkflowContracts(unittest.TestCase):
    def text(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_prepare_is_manual_pat_free_and_never_merges(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn("workflow_dispatch", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("gh pr create", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh workflow run validate.yml", text)
        self.assertIn(
            "compare/main...release/stable?expand=1", text
        )
        self.assertIn("Maintainer release PR required", text)
        self.assertIn("pull_request validation event runs", text)
        self.assertLess(text.index("git push origin HEAD:refs/heads/release/stable"),
                        text.index("manual_url="))

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
        self.assertIn("actions/upload-artifact@v6", text)
        self.assertIn("dist/claude", text)
        self.assertIn("dist/codex", text)

    def test_official_actions_use_the_node24_runtime_contract(self):
        workflow_root = REPO / ".github" / "workflows"
        workflows = sorted({
            *workflow_root.glob("*.yml"),
            *workflow_root.glob("*.yaml"),
        })
        self.assertTrue(workflows)
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            self.assertEqual([], node24_runtime_findings(workflow.name, text))

    def test_node24_runtime_contract_rejects_each_stale_shape(self):
        cases = {
            "stale-major": "- uses: actions/checkout@v4\n",
            "unapproved-ref": "- uses: actions/setup-python@deadbeef\n",
            "job-node-20": (
                "- uses: actions/setup-node@v5\n"
                "  with:\n"
                "    node-version: \"20\"\n"
                "    package-manager-cache: false\n"
            ),
            "implicit-cache": (
                "- uses: actions/setup-node@v5\n"
                "  with:\n"
                "    node-version: \"24\"\n"
            ),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertTrue(node24_runtime_findings(name, text))


if __name__ == "__main__":
    unittest.main()
