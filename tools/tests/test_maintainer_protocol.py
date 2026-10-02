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
            "RELEASE_COMMIT",
            "MAIN_EXACT_SHA_GREEN",
            "PUBLISH_STABLE_RELEASE",
            "BOUNDED_BRANCH_CLEANUP",
            "CLEAN_MAIN",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, protocol)

        for host in ("Claude", "Codex", "Linux", "macOS", "Windows"):
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

    def test_model_catalog_captures_are_bundled_versioned_and_checked(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        section = protocol.split("## Model catalog bump", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        # A signed-in capture falls back to the bundled catalog and still exits 0.
        self.assertIn("codex debug models --bundled > codex-models.json &&", section)
        self.assertIn('--cli-version "codex=$(codex --version)"', section)
        self.assertIn("curl -fsSL https://platform.claude.com/docs/en/about-claude/models/"
                      "overview.md \\\n", section)
        self.assertNotIn("curl -sL", section)
        self.assertIn("Never use the signed-in `codex debug models`", flat)
        self.assertNotIn("A signed-in `codex debug models` and", flat)

    def test_a_kept_mutable_release_completes_with_the_publication_tooling(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        section = protocol.split("### Release immutability", 1)[1].split("\n## ", 1)[0]
        command = section.split("python3 tools/release_publish.py finalize", 1)[1]
        command = command.split("```", 1)[0]
        # The workflow hard-codes --require-immutable, so keeping the Release
        # needs a manual finalize that reconciles it.
        for argument in ('--candidate-sha "$CANDIDATE_SHA"',
                         '--prior-stable-sha "$PRIOR_STABLE_SHA"', "--notes-file"):
            self.assertIn(argument, command)
        self.assertNotIn("--require-immutable", command)
        self.assertNotIn("--release-branch-sha", command)
        flat = " ".join(section.split())
        self.assertIn("delete that Release (never its tag) and re-run the failed finalize job",
                      flat)
        self.assertIn("To keep it, the maintainer completes the publication", flat)
        self.assertIn("The finalize job of `Release` passes `--require-immutable`", flat)

    def test_a_release_is_one_command_after_the_release_commit(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        section = protocol.split("\n## Flow B", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        steps = (
            "python3 tools/release.py bump",
            "python3 tools/release.py check-pr --base origin/main",
            "python3 tools/release.py ship --version X.Y.Z",
            "gh release view vX.Y.Z",
            "python3 tools/release.py finalize-local --version X.Y.Z",
        )
        positions = []
        for step in steps:
            with self.subTest(step=step):
                self.assertIn(step, section)
                positions.append(section.index(step))
        self.assertEqual(positions, sorted(positions))
        for term in ("A release is a tag.", "it never runs the tests again",
                     "a push to `stable` or a tag starts no workflow",
                     "A release commit is never edited by hand",
                     "Merge the PR when green; the merge starts the release.",
                     "Every other push to `main` starts nothing.",
                     "about three minutes from the merge to the immutable Release"):
            with self.subTest(term=term):
                self.assertIn(term, flat)
        # .release/stable.json stays; the release/stable branch is gone.
        self.assertNotRegex(protocol, r"(?<![.])release/stable(?![.]json)")
        for retired in ("Prepare stable release", "Publish stable release",
                        "release PR", "main_source", "verify-merge-group"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, protocol)

    def test_the_release_audit_requires_the_version_only_title(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        flat = " ".join(protocol.split())
        self.assertIn(
            "`gh release view vX.Y.Z --json name,isDraft,isPrerelease,isImmutable`"
            " must report the `name` `vX.Y.Z`", flat,
        )
        self.assertNotIn("Agent Marketplace v", protocol)

    def test_the_one_time_release_reset_is_recorded_and_cannot_recur(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("\n## One-time release reset\n", protocol)
        section = protocol.split("\n## One-time release reset\n", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        for term in ("`.release/reset.json` records the decision", "It cannot happen again",
                     "refuses every partial or mixed variant",
                     "the old history is not archived", "A pull request may only add the marker",
                     "python3 tools/release.py ship --version 0.0.1"):
            with self.subTest(term=term):
                self.assertIn(term, flat)
        # The reset ran once through owner commands that no longer apply.
        self.assertNotIn("gh release delete", section)
        self.assertNotIn("prepare-stable-release.yml", section)

    def test_upstream_text_never_identifies_a_consumer_project(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("\n## Confidentiality\n", protocol)
        section = " ".join(protocol.split("\n## Confidentiality\n", 1)[1].split("\n## ", 1)[0].split())
        for term in ("Issue intake, PR bodies, commit messages, changesets and every tracked file"
                     " never identify a consumer project or its data",
                     "never copy them into a branch, commit, changeset, PR or comment",
                     '"in one measured project"',
                     "Before each commit and PR, scan the message, body and diff",
                     "The merge method keeps every commit of a PR",
                     "`tools/release.py check-pr` reads every commit message and added line of"
                     " the PR and refuses a home-directory path",
                     "`AGENT_MARKETPLACE_PRIVATE_TERMS_FILE`",
                     "in the PR text passed with `--pr-text` as well, and prints only each"
                     " hit's kind and position"):
            with self.subTest(term=term):
                self.assertIn(term, section)
        self.assertLess(protocol.index("\n## Confidentiality\n"), protocol.index("\n## Flow A"))
        # Every commit made here reads the rule, not only issue and release work.
        agents = " ".join((REPO / "AGENTS.md").read_text(encoding="utf-8").split())
        working = agents.split("## Working in this repository", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Text and files committed here never identify a consumer project or its"
                      " data; follow the Confidentiality section of"
                      " `docs/maintainer-operations-protocol.md`", working)

    def test_merge_and_release_authority_remain_explicit(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("Explicit user approval identifying that PR", protocol)
        self.assertIn("An explicit user instruction bound to an unambiguous PR set", protocol)
        self.assertIn("Statements such as “is it ready?”", protocol)
        self.assertIn("Do not merge the PR", protocol)
        self.assertIn("finalize-local", protocol)


if __name__ == "__main__":
    unittest.main()
