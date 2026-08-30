"""Static contracts for manually invoked repository maintainer operations."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "docs" / "maintainer-operations-protocol.md"


class MaintainerProtocolTests(unittest.TestCase):
    def test_protocol_is_manual_discoverable_and_complete(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")

        for marker in (
            "NO_BACKGROUND_TRIGGER",
            "MANUAL_ISSUE_REQUEST",
            "ROOT_CAUSE",
            "SOLUTION_CHALLENGE",
            "IMPACT_ANALYSIS",
            "EXACT_SHA_REMOTE_GATES",
            "AWAIT_MERGE_APPROVAL",
            "RELEASE_REQUESTED",
            "MAIN_EXACT_SHA_GREEN",
            "PUBLISH_STABLE_RELEASE",
            "BOUNDED_BRANCH_CLEANUP",
            "CLEAN_MAIN",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, protocol)

        for host in ("Claude", "Codex", "OpenCode", "Linux", "macOS", "Windows", "ConPTY", "WSL2"):
            with self.subTest(host=host):
                self.assertIn(host, protocol)

        self.assertIn("Never scan, poll, or start work from a GitHub issue event", agents)
        self.assertIn("identifies either the issue or an unambiguous selection rule", agents)
        self.assertIn("docs/maintainer-operations-protocol.md", agents)
        self.assertIn("docs/maintainer-operations-protocol.md", contributing)

    def test_no_unattended_issue_agent_surface_exists(self):
        removed_paths = (
            ".github/workflows/issue-solution.yml",
            ".github/codex/prompts/solve-issue.md",
            ".github/codex/schemas/issue-solution.json",
            "tools/maintainer_automation.py",
        )
        for relative in removed_paths:
            with self.subTest(path=relative):
                self.assertFalse((REPO / relative).exists())

        workflow_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO / ".github" / "workflows").glob("*.yml"))
        )
        for forbidden in (
            "openai/codex-action",
            "CODEX_ISSUE_AUTOMATION_ENABLED",
            "ISSUE_AUTOMATION_APP_ID",
            "ISSUE_AUTOMATION_PRIVATE_KEY",
            "automation:solve",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow_text)

    def test_merge_and_release_authority_remain_explicit(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("Explicit user approval identifying that PR", protocol)
        self.assertIn("An explicit user instruction bound to an unambiguous PR set", protocol)
        self.assertIn("Statements such as “is it ready?”", protocol)
        self.assertIn("Do not merge the PR", protocol)
        self.assertIn("finalize-local", protocol)


if __name__ == "__main__":
    unittest.main()
