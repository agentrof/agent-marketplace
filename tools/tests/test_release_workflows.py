"""Static fail-closed contracts for GitHub release automation."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PINNED_ACTIONS = {
    "actions/checkout": ("fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09", "v5"),
    "actions/setup-python": ("5fda3b95a4ea91299a34e894583c3862153e4b97", "v7.0.0"),
    "actions/setup-node": ("820762786026740c76f36085b0efc47a31fe5020", "v7.0.0"),
    "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7.0.1"),
    "github/codeql-action/init": ("ff2f1c621b7f889edc0d3c761ac2e6a3f8cdb0dd", "v4"),
    "github/codeql-action/analyze": ("ff2f1c621b7f889edc0d3c761ac2e6a3f8cdb0dd", "v4"),
}
ACTION_USE_RE = re.compile(
    r"uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)"
    r"@([^\s#]+)(?:\s+#\s*([^\s]+))?"
)


def workflow_action_findings(name: str, text: str) -> list[str]:
    findings: list[str] = []
    for action, commit, label in ACTION_USE_RE.findall(text):
        if action not in PINNED_ACTIONS:
            findings.append(f"{name}: unapproved workflow action {action}")
            continue
        expected_commit, expected_label = PINNED_ACTIONS[action]
        if commit != expected_commit or label != expected_label:
            findings.append(
                f"{name}: {action} must use {expected_commit} # {expected_label}, "
                f"found {commit} # {label or '(missing)'}"
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
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
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
        self.assertIn('"opencode-ai@${opencode_version}"', text)
        self.assertIn("OPENCODE_REAL_PROBE", text)

    def test_publish_refuses_fork_or_wrong_base_release_prs(self):
        text = self.text("publish-stable-release.yml")
        self.assertIn("github.event.pull_request.base.ref == 'main'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn('npm install --global "opencode-ai@${opencode_version}"', text)
        self.assertLess(
            text.index('test "$(git rev-parse HEAD)" = "$EXPECTED_MERGE_SHA"'),
            text.index('npm install --global "opencode-ai@${opencode_version}"'),
        )

    def test_main_validation_uploads_build_id_and_all_host_artifacts(self):
        text = self.text("validate.yml")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("tools/release.py build-info", text)
        self.assertEqual(
            text.count(
                "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0"
            ),
            4,
        )
        self.assertGreaterEqual(text.count('python-version: "3.9"'), 2)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1", text)
        self.assertIn("dist/claude", text)
        self.assertIn("dist/codex", text)
        self.assertIn("dist/opencode", text)

    def test_dependabot_tracks_github_actions(self):
        text = (REPO / ".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("package-ecosystem: github-actions", text)
        self.assertIn("interval: weekly", text)

    def test_security_policy_uses_private_vulnerability_reporting(self):
        text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("Do not report suspected vulnerabilities in a public issue", text)
        self.assertIn(
            "https://github.com/agentrof/agent-marketplace/security/advisories/new",
            text,
        )
        for path in (REPO / "README.md", REPO / "CONTRIBUTING.md"):
            with self.subTest(path=path.name):
                discoverability = path.read_text(encoding="utf-8")
                self.assertIn("SECURITY.md", discoverability)
                self.assertIn("security/advisories/new", discoverability)

    def test_community_governance_surface_is_complete(self):
        required = {
            "CODE_OF_CONDUCT.md": ("Expected behavior", "Enforcement"),
            "SUPPORT.md": ("issue forms", "SECURITY.md"),
            ".github/PULL_REQUEST_TEMPLATE.md": ("Verification", "release-impact changeset"),
            ".github/ISSUE_TEMPLATE/config.yml": ("blank_issues_enabled: false", "Security vulnerability"),
            ".github/ISSUE_TEMPLATE/bug_report.yml": ("Minimal reproduction", "Safe evidence"),
            ".github/ISSUE_TEMPLATE/feature_request.yml": ("Acceptance criteria", "non-goals"),
            ".github/ISSUE_TEMPLATE/workflow_question.yml": ("Workflow area", "Intended outcome"),
        }
        for relative, markers in required.items():
            with self.subTest(path=relative):
                text = (REPO / relative).read_text(encoding="utf-8")
                for marker in markers:
                    self.assertIn(marker, text)

    def test_real_host_smoke_uses_versions_from_the_tracked_policy(self):
        payload = json.loads((REPO / "tools/data/host-cli-versions.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        for key in ("claude_code", "codex", "opencode"):
            self.assertRegex(payload[key], r"^[0-9]+\.[0-9]+\.[0-9]+$")
        workflow = self.text("release-hosts.yml")
        self.assertIn("tools/data/host-cli-versions.json", workflow)
        self.assertIn('"@anthropic-ai/claude-code@${claude_version}"', workflow)
        self.assertIn('"@openai/codex@${codex_version}"', workflow)
        self.assertIn('"opencode-ai@${opencode_version}"', workflow)
        self.assertIn("platforms/opencode/host_check.py", workflow)
        self.assertIn("platforms/opencode/real_host_probe.py", workflow)
        self.assertIn("opencode-cli-real-host", workflow)
        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(runner, workflow)
        self.assertIn("workflow_call", workflow)
        self.assertIn("--tui", workflow)
        self.assertIn("candidate_sha:", workflow)
        self.assertIn("inputs.candidate_sha || github.sha", workflow)
        self.assertIn("opencode-windows-conpty", workflow)
        self.assertIn('"pywinpty==3.0.5"', workflow)
        self.assertIn("opencode-wsl2-real-host", workflow)
        wsl2 = workflow.split("  opencode-wsl2-real-host:", 1)[1]
        self.assertIn("runs-on: windows-latest", wsl2)
        self.assertIn("wsl.exe --install Ubuntu", wsl2)
        self.assertIn("wsl.exe --distribution Ubuntu", wsl2)
        self.assertIn("grep -q WSL2 /proc/sys/kernel/osrelease", workflow)
        for release_workflow in ("prepare-stable-release.yml", "publish-stable-release.yml"):
            text = self.text(release_workflow)
            with self.subTest(workflow=release_workflow):
                self.assertIn('"opencode-ai@${opencode_version}"', text)
                self.assertIn("OPENCODE_BIN", text)
                self.assertIn("OPENCODE_REAL_PROBE", text)
                self.assertIn("OPENCODE_TUI=1", text)
                self.assertIn("exact-sha-host-gates", text)
                self.assertIn("uses: ./.github/workflows/release-hosts.yml", text)
                self.assertIn("needs: exact-sha-host-gates", text)

        prepare = self.text("prepare-stable-release.yml")
        publish = self.text("publish-stable-release.yml")
        self.assertIn("candidate_sha: ${{ github.sha }}", prepare)
        self.assertIn(
            "candidate_sha: ${{ github.event.pull_request.merge_commit_sha }}",
            publish,
        )

    def test_release_check_requires_the_fail_closed_opencode_host_gate(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(
            makefile,
            r"(?m)^release-check: check host-check-opencode$",
        )
        self.assertIn("platforms/opencode/host_check.py --opencode", makefile)
        self.assertIn("OPENCODE_REAL_PROBE", makefile)
        self.assertIn("OPENCODE_TUI_ARG", makefile)

    def test_real_host_smoke_covers_its_complete_control_plane(self):
        workflow = self.text("release-hosts.yml")
        for path in (
            "'.agents/**'",
            "'.claude-plugin/**'",
            "'.github/workflows/release-hosts.yml'",
            "'Makefile'",
            "'platforms/**'",
            "'plugins/**'",
            "'product.json'",
            "'tools/**'",
            "'versions.json'",
        ):
            with self.subTest(path=path):
                self.assertIn(path, workflow)

    def test_validate_jobs_are_time_bounded(self):
        text = self.text("validate.yml")
        expectations = {
            "changeset": "10",
            "check": "20",
            "build-metadata": "10",
            "compatibility": "20",
        }
        for job, minutes in expectations.items():
            with self.subTest(job=job):
                self.assertRegex(
                    text,
                    rf"(?ms)^  {re.escape(job)}:\n.*?^    timeout-minutes: {minutes}$",
                )

    def test_validation_workflows_cancel_superseded_runs(self):
        expected = (
            "concurrency:\n"
            "  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
            "  cancel-in-progress: true"
        )
        for workflow in ("validate.yml", "codeql.yml", "release-hosts.yml"):
            with self.subTest(workflow=workflow):
                self.assertIn(expected, self.text(workflow))

    def test_workflow_actions_are_allowlisted_and_sha_pinned(self):
        workflow_root = REPO / ".github" / "workflows"
        workflows = sorted({
            *workflow_root.glob("*.yml"),
            *workflow_root.glob("*.yaml"),
        })
        self.assertTrue(workflows)
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            self.assertEqual([], workflow_action_findings(workflow.name, text))

    def test_codeql_scans_python_and_javascript_on_pr_main_and_schedule(self):
        text = self.text("codeql.yml")
        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("schedule:", text)
        self.assertIn("languages: python,javascript-typescript", text)
        self.assertIn("build-mode: none", text)
        self.assertIn("security-events: write", text)

    def test_node24_runtime_contract_rejects_each_stale_shape(self):
        cases = {
            "stale-major": "- uses: actions/checkout@v4 # v4\n",
            "unapproved-ref": "- uses: actions/setup-python@deadbeef\n",
            "job-node-20": (
                "- uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0\n"
                "  with:\n"
                "    node-version: \"20\"\n"
                "    package-manager-cache: false\n"
            ),
            "implicit-cache": (
                "- uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0\n"
                "  with:\n"
                "    node-version: \"24\"\n"
            ),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertTrue(workflow_action_findings(name, text))


if __name__ == "__main__":
    unittest.main()
