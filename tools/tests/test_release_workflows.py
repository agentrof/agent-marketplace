"""Fail-closed contracts for repository validation and release workflows."""

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
    "github/codeql-action/init": ("b96794f015dfd88f77b49b1c93e0fa7110f94c63", "v4"),
    "github/codeql-action/analyze": ("b96794f015dfd88f77b49b1c93e0fa7110f94c63", "v4"),
}
ACTION_USE_RE = re.compile(
    r"uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)"
    r"@([^\s#]+)(?:\s+#\s*([^\s]+))?"
)
STATUS_CHECK_RE = re.compile(r"\b(?:always|cancelled|failure)\(\)")


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


def workflow_jobs(text: str) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    job: dict = {"if": "", "needs": []}
    key = ""
    for line in text.split("\njobs:\n", 1)[1].splitlines():
        if re.fullmatch(r"  [\w-]+:", line):
            job = jobs.setdefault(line.strip()[:-1], {"if": "", "needs": []})
            key = ""
        elif not line.startswith("    "):
            continue
        elif not line.startswith("     "):
            key, _, value = line.strip().partition(":")
            value = value.strip()
            if key == "if" and not value.startswith((">", "|")):
                job["if"] = value
            elif key == "needs":
                job["needs"] = [
                    need.strip() for need in value.strip("[]").split(",")
                    if need.strip()
                ]
        elif key == "if":
            job["if"] = f"{job['if']} {line.strip()}".strip()
        elif key == "needs" and line.strip().startswith("- "):
            job["needs"].append(line.strip()[2:].strip())
    return jobs


def workflow_skip_inheritance_findings(name: str, text: str) -> list[str]:
    # Without always(), cancelled() or failure() a job `if` gets GitHub's
    # implicit success(), which is false after a skipped or failed ancestor
    # anywhere in its chain. A need that calls one of them can succeed after
    # such an ancestor, and its dependent is then skipped anyway.
    jobs = workflow_jobs(text)
    findings: list[str] = []
    for job, spec in jobs.items():
        for need in spec["needs"]:
            if not STATUS_CHECK_RE.search(jobs.get(need, {}).get("if", "")):
                continue
            if not STATUS_CHECK_RE.search(spec["if"]):
                findings.append(
                    f"{name}: {job} inherits every skip {need} tolerates; gate "
                    f"it with !cancelled() && needs.{need}.result == 'success'"
                )
            elif f"needs.{need}.result == 'success'" not in spec["if"]:
                findings.append(
                    f"{name}: {job} runs past {need} without requiring "
                    f"needs.{need}.result == 'success'"
                )
    return findings


class ReleaseWorkflowContracts(unittest.TestCase):
    def text(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_prepare_is_manual_pat_free_and_never_merges(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn("workflow_dispatch", text)
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("permissions:\n      contents: write", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("gh pr create", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh workflow run validate.yml", text)
        self.assertIn("compare/main...release/stable?expand=1", text)
        self.assertIn("Maintainer release PR required", text)
        self.assertIn("pull_request validation event runs", text)
        self.assertIn("publish-release-branch", text)
        self.assertIn('--main-sha "$main_sha"', text)
        self.assertIn("git config core.autocrlf false", text)
        self.assertIn("git config core.eol lf", text)
        self.assertIn("git checkout-index --all --force", text)
        self.assertIn('test -z "$(git status --porcelain)"', text)
        self.assertLess(
            text.index("publish-release-branch"),
            text.index("manual_url="),
        )
        self.assertIn("bootstrap-public-smoke", text)
        self.assertIn("persist-credentials: false", text)

    def test_pull_requests_use_ordinary_checks_and_exact_host_gate(self):
        validate = self.text("validate.yml")
        codeql = self.text("codeql.yml")
        hosts = self.text("release-hosts.yml")
        self.assertIn("pull_request:", validate)
        self.assertIn("--base origin/${{ github.base_ref }}", validate)
        self.assertNotIn("--allow-bootstrap", validate)
        self.assertIn("pull_request:", codeql)
        self.assertIn("pull_request:\n", hosts)
        self.assertNotIn("pull_request:\n    paths:", hosts)
        self.assertIn("candidate_sha:", hosts)
        self.assertIn("inputs.candidate_sha || github.sha", hosts)
        self.assertIn("native-host-lifecycle", hosts)
        self.assertIn("release-pr-policy", validate)
        self.assertIn("if: always()", validate)
        for dependency in (
            "changeset", "release-pr-policy", "deterministic-check",
            "compatibility", "vault-hook-platforms",
        ):
            self.assertIn(f"      - {dependency}", validate)

    def test_bootstrap_requires_empty_tag_space_and_uses_atomic_refs(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn("verify-bootstrap", text)
        self.assertIn("verify-bootstrap-candidate", text)
        self.assertIn("refs/tags/v*", text)
        self.assertIn("tools/release_publish.py stage", text)
        self.assertIn("tools/release_publish.py rollback", text)
        self.assertIn("tools/release_publish.py finalize", text)
        self.assertIn("--bootstrap", text)
        self.assertIn("python3 trusted/tools/smoke_plugin_installs.py", text)
        self.assertNotIn("make -C candidate release-check", text)
        self.assertIn("needs.prepare.outputs.phase == 'published'", text)
        self.assertLess(
            text.index("tools/release_publish.py stage"),
            text.index("python3 trusted/tools/smoke_plugin_installs.py"),
        )
        self.assertLess(
            text.index("python3 trusted/tools/smoke_plugin_installs.py"),
            text.rindex("tools/release_publish.py finalize"),
        )

    def test_bootstrap_rerun_reconciles_the_exact_staged_candidate(self):
        text = self.text("prepare-stable-release.yml")
        self.assertIn('bootstrap_candidate="$stable_sha"', text)
        self.assertIn('--candidate-sha "$bootstrap_candidate"', text)
        self.assertIn("tools/release_publish.py rollback", text)
        self.assertIn("git tag -d v0.0.1", text)
        self.assertIn('bootstrap_candidate="$candidate_sha"', text)
        self.assertIn('json.load(sys.stdin)["has_release"]', text)
        self.assertIn(
            'echo "candidate_sha=$bootstrap_candidate"', text
        )
        rollback = text.split("\n  bootstrap-rollback:", 1)[1].split(
            "\n  bootstrap-finalize:", 1
        )[0]
        finalize = text.split("\n  bootstrap-finalize:", 1)[1]
        for write_job in (rollback, finalize):
            self.assertIn("ref: ${{ github.sha }}", write_job)
            self.assertNotIn(
                "ref: ${{ needs.prepare.outputs.candidate_sha }}", write_job
            )

    def test_publish_binds_exact_merge_and_recoverable_ref_transaction(self):
        text = self.text("publish-stable-release.yml")
        for required in (
            "merge_commit_sha", "verify-release-pr", "merge_parents=",
            "release_publish.py\" stage", "--prior-stable-sha",
            "rollback-publication", "finalize-publication",
            "--release-branch-sha",
        ):
            self.assertIn(required, text)
        self.assertLess(text.index("stage-publication:"),
                        text.index("public-stable-smoke:"))
        self.assertLess(text.index("public-stable-smoke:"),
                        text.index("finalize-publication:"))
        self.assertIn("EXPECTED_RELEASE_SHA", text)

    def test_publish_refuses_fork_or_wrong_base_release_prs(self):
        text = self.text("publish-stable-release.yml")
        self.assertIn("github.event.pull_request.base.ref == 'main'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn('test "$#" -eq 3', text)
        self.assertIn('test "$3" = "$RELEASE_HEAD_SHA"', text)
        self.assertIn('"$EXPECTED_MERGE_SHA^{tree}"', text)

    def test_post_merge_verification_survives_exact_branch_cleanup(self):
        text = self.text("publish-stable-release.yml")
        verify = text.split("\n  verify-release-candidate:", 1)[1].split(
            "\n  exact-sha-host-gates:", 1
        )[0]
        self.assertNotIn("refs/remotes/origin/release/stable", verify)
        self.assertIn('test "$3" = "$RELEASE_HEAD_SHA"', verify)
        self.assertIn(
            'git show "$RELEASE_HEAD_SHA:.release/stable.json"', verify
        )
        self.assertIn('--stable-sha "$stable_base"', verify)
        self.assertIn("verify-release-pr", verify)

    def test_publish_write_jobs_execute_only_attested_main_source_helper(self):
        text = self.text("publish-stable-release.yml")
        verify = text.split("\n  verify-release-candidate:", 1)[1].split(
            "\n  exact-sha-host-gates:", 1
        )[0]
        stage = text.split("\n  stage-publication:", 1)[1].split(
            "\n  public-stable-smoke:", 1
        )[0]
        public = text.split("\n  public-stable-smoke:", 1)[1].split(
            "\n  rollback-publication:", 1
        )[0]
        self.assertIn("contents: read", verify)
        self.assertIn("verify-release-pr", verify)
        self.assertIn("contents: write", stage)
        self.assertIn(
            'git show "$main_source:tools/release_publish.py"', stage
        )
        self.assertIn('git config user.name "github-actions[bot]"', stage)
        self.assertIn(
            'git config user.email '
            '"41898282+github-actions[bot]@users.noreply.github.com"',
            stage,
        )
        self.assertNotIn("make ", stage)
        self.assertNotIn("python3 tools/", stage)
        self.assertIn("contents: read", public)
        self.assertIn("persist-credentials: false", public)
        self.assertIn("make public-release-check", public)

    def test_validation_is_read_only_and_pins_setup_python(self):
        text = self.text("validate.yml")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertEqual(
            text.count(
                "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0"
            ),
            5,
        )
        self.assertGreaterEqual(text.count('python-version: "3.9"'), 2)

    def test_no_job_inherits_a_skip_its_need_tolerates(self):
        validate = workflow_jobs(self.text("validate.yml"))
        self.assertEqual(validate["check"]["if"], "always()")
        self.assertEqual(validate["check"]["needs"], [
            "changeset", "release-pr-policy", "deterministic-check",
            "compatibility", "vault-hook-platforms",
        ])
        self.assertIn("github.event_name == 'pull_request' &&",
                      validate["changeset"]["if"])
        prepare = workflow_jobs(self.text("prepare-stable-release.yml"))
        self.assertEqual(prepare["prepare"]["needs"], ["exact-sha-host-gates"])
        publish = workflow_jobs(self.text("publish-stable-release.yml"))
        self.assertEqual(publish["finalize-publication"]["needs"],
                         ["stage-publication", "public-stable-smoke"])
        self.assertRegex(publish["finalize-publication"]["if"], STATUS_CHECK_RE)
        workflow_root = REPO / ".github" / "workflows"
        for workflow in sorted({
            *workflow_root.glob("*.yml"),
            *workflow_root.glob("*.yaml"),
        }):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertTrue(workflow_jobs(text))
                self.assertEqual(
                    [], workflow_skip_inheritance_findings(workflow.name, text)
                )

    def test_skip_inheritance_rejects_each_stale_shape(self):
        template = (
            "on: push\n"
            "jobs:\n"
            "  changeset:\n"
            "    if: github.event_name == 'pull_request'\n"
            "  check:\n"
            "    if: always()\n"
            "    needs:\n"
            "      - changeset\n"
            "  build-metadata:\n"
            "    if: {condition}\n"
            "    needs: [check]\n"
        )
        cases = {
            "implicit-success": "github.event_name == 'push'",
            "explicit-success": "success() && github.event_name == 'push'",
            "ignores-verdict": "${{ !cancelled() && github.event_name == 'push' }}",
        }
        for name, condition in cases.items():
            with self.subTest(name=name):
                self.assertTrue(workflow_skip_inheritance_findings(
                    name, template.format(condition=condition),
                ))
        gated = template.format(condition=(
            "${{ !cancelled() && github.event_name == 'push' && "
            "needs.check.result == 'success' }}"
        ))
        self.assertEqual([], workflow_skip_inheritance_findings("gated", gated))

    def test_vault_hook_matrix_gates_platforms_and_apple_launcher(self):
        text = self.text("validate.yml")
        self.assertIn("VAULT_RESULT: ${{ needs.vault-hook-platforms.result }}", text)
        self.assertIn('test "$VAULT_RESULT" = success', text)
        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            with self.subTest(runner=runner):
                self.assertGreaterEqual(text.count(f"os: {runner}"), 2)
        self.assertIn('python: "3.9"', text)
        self.assertIn('python: "3.x"', text)
        self.assertIn("AGENT_MARKETPLACE_REQUIRE_APPLE_PYTHON3", text)
        for test_name in (
            "test_system_macos_python3_launcher_is_accepted",
            "test_issue_77_bare_python_cmd_preserves_attested_codex_result",
            "test_bare_python_candidate_with_invalid_result_is_restored",
            "test_bare_render_cannot_publish_a_different_valid_transition",
            "test_bare_render_with_forged_registry_is_restored",
            "test_issue_77_bare_init_has_an_exact_attested_delta",
            "test_issue_77_bare_init_preserves_real_codex_draft",
        ):
            with self.subTest(test_name=test_name):
                self.assertIn(test_name, text)
        self.assertIn("tools.tests.test_experience_compile", text)
        self.assertIn("tools.tests.test_vault_hook", text)

    def test_dependabot_is_not_asked_for_action_bumps_the_gates_refuse(self):
        # PINNED_ACTIONS and the changeset gate refuse every bump Dependabot can raise.
        self.assertFalse((REPO / ".github/dependabot.yml").exists())

    def test_security_policy_uses_private_vulnerability_reporting(self):
        text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(
            "Do not report suspected vulnerabilities in a public issue", text
        )
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
            ".github/PULL_REQUEST_TEMPLATE.md": (
                "Verification", "release-impact changeset",
            ),
            ".github/ISSUE_TEMPLATE/config.yml": (
                "blank_issues_enabled: false", "Security vulnerability",
            ),
            ".github/ISSUE_TEMPLATE/bug_report.yml": (
                "Minimal reproduction", "Safe evidence",
            ),
            ".github/ISSUE_TEMPLATE/feature_request.yml": (
                "Acceptance criteria", "non-goals",
            ),
            ".github/ISSUE_TEMPLATE/workflow_question.yml": (
                "Workflow area", "Intended outcome",
            ),
        }
        for relative, markers in required.items():
            with self.subTest(path=relative):
                text = (REPO / relative).read_text(encoding="utf-8")
                for marker in markers:
                    self.assertIn(marker, text)

    def test_real_host_smoke_uses_the_tracked_two_host_policy(self):
        payload = json.loads((REPO / "tools/data/host-cli-versions.json").read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"schema_version", "claude_code", "codex"})
        self.assertEqual(payload["schema_version"], 1)
        for key in ("claude_code", "codex"):
            self.assertRegex(payload[key], r"^[0-9]+\.[0-9]+\.[0-9]+$")
        workflow = self.text("release-hosts.yml")
        self.assertIn("tools/data/host-cli-versions.json", workflow)
        self.assertIn('"@anthropic-ai/claude-code@${claude_version}"', workflow)
        self.assertIn('"@openai/codex@${codex_version}"', workflow)
        self.assertIn("claude --version", workflow)
        self.assertIn("codex --version", workflow)
        self.assertIn("tools/smoke_plugin_installs.py --channel checkout", workflow)
        self.assertIn("make release-check", workflow)
        self.assertNotIn("make public-release-check", workflow)
        self.assertIn("runs-on: macos-latest", workflow)
        self.assertIn("workflow_call", workflow)

        for release_workflow in ("prepare-stable-release.yml", "publish-stable-release.yml"):
            text = self.text(release_workflow)
            with self.subTest(workflow=release_workflow):
                self.assertIn("exact-sha-host-gates", text)
                self.assertIn("uses: ./.github/workflows/release-hosts.yml", text)
                self.assertIn("exact-sha-host-gates", text)
        self.assertIn(
            "needs: [verify-release-candidate, exact-sha-host-gates]",
            self.text("publish-stable-release.yml"),
        )

        prepare = self.text("prepare-stable-release.yml")
        publish = self.text("publish-stable-release.yml")
        self.assertIn("candidate_sha: ${{ github.sha }}", prepare)
        self.assertIn(
            "candidate_sha: ${{ github.event.pull_request.merge_commit_sha }}",
            publish,
        )
        self.assertIn("python3 trusted/tools/smoke_plugin_installs.py", prepare)
        self.assertIn("path: trusted", prepare)
        self.assertIn("path: candidate", prepare)
        self.assertIn("make public-release-check", publish)
        for text in (prepare, publish):
            self.assertIn("EXPECTED_RELEASE_SHA", text)

    def test_release_check_requires_deterministic_gates(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^release-check: check$")
        self.assertRegex(makefile, r"(?m)^public-release-check: release-check$")
        self.assertRegex(
            makefile,
            r"(?m)^\s*PYTHONDONTWRITEBYTECODE=1 \$\(PY\) -m unittest",
        )
        self.assertIn(
            'tools/smoke_plugin_installs.py --channel public --expected-sha',
            makefile,
        )

    def test_required_host_smoke_has_no_event_level_path_filter(self):
        workflow = self.text("release-hosts.yml")
        self.assertIn("  pull_request:\n", workflow)
        self.assertNotIn("    paths:", workflow)
        self.assertNotIn("    paths-ignore:", workflow)

    def test_validate_jobs_are_time_bounded(self):
        text = self.text("validate.yml")
        expectations = {
            "changeset": "10",
            "release-pr-policy": "10",
            "deterministic-check": "20",
            "check": "5",
            "compatibility": "20",
            "vault-hook-platforms": "10",
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
