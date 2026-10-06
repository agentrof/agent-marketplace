"""Fail-closed contracts for repository validation and release workflows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import re
import sys
import unittest
from tools.tests.levels import integration
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import release  # noqa: E402
PINNED_ACTIONS = {
    "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7.0.1"),
    "actions/download-artifact": ("3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", "v8.0.1"),
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


def workflow_action_findings(name: str, text: str, host_policy: dict | None = None) -> list[str]:
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
    host_policy_output = name == "release-hosts.yml" and host_policy == {
        "schema_version": 1, "runner_os": "macos-latest", "python": "3.14", "node": "24",
    } and all(marker in text for marker in (
        "node: ${{ steps.policy.outputs.node }}", "python3 tools/ci_host_policy.py",
        '--github-output "$GITHUB_OUTPUT"',
    ))
    for index, line in enumerate(lines):
        if "uses: actions/setup-node@" not in line:
            continue
        inputs = "\n".join(lines[index + 1:index + 5])
        if 'node-version: "24"' not in inputs and not (
                host_policy_output and 'node-version: ${{ needs.host-plan.outputs.node }}' in inputs):
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
            elif not any(f"needs.{need}.result {operator} 'success'" in spec["if"]
                         for operator in ("==", "!=")):
                block = re.split(r"(?m)^  [\w-]+:", text.split(f"\n  {job}:\n", 1)[1], maxsplit=1)[0]
                result = re.search(r"(?m)^          (\w+): \$\{\{ needs\." + re.escape(need) + r"\.result \}\}$", block)
                if spec["if"] == "always()" and result and f'test "${result.group(1)}" = success' in block:
                    continue
                findings.append(
                    f"{name}: {job} runs past {need} without requiring "
                    f"needs.{need}.result == 'success'"
                )
    return findings


class ReleaseWorkflowContracts(unittest.TestCase):
    def text(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def release_jobs(self) -> dict[str, str]:
        text = self.text("release.yml")
        return {
            name: block for name, block in re.findall(
                r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)",
                text.split("\njobs:\n", 1)[1],
            )
        }

    def test_release_is_one_manual_main_workflow_that_never_tests_again(self):
        workflows = {path.name for path in (REPO / ".github" / "workflows").glob("*.y*ml")}
        self.assertNotIn("prepare-stable-release.yml", workflows)
        self.assertNotIn("publish-stable-release.yml", workflows)
        text = self.text("release.yml")
        events = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", events), ["workflow_dispatch"])
        self.assertIn("      version:\n        description:", events)
        self.assertIn("        required: true", events.split("      sha:", 1)[0])
        self.assertIn("      sha:\n", events)
        self.assertIn('        default: ""', events.split("      sha:", 1)[1])
        self.assertIn("permissions:\n  contents: read\n\nconcurrency:", text)
        jobs = workflow_jobs(text)
        self.assertEqual(list(jobs), ["verify", "stage", "public-smoke", "rollback", "finalize"])
        self.assertEqual(jobs["verify"]["if"], "github.ref == 'refs/heads/main'")
        for forbidden in (
            "pull_request_target", "gh pr merge", "gh pr create", "pull-requests: write",
            "release/stable", "./.github/workflows/validate.yml",
            "./.github/workflows/release-hosts.yml", "ci_tests.py", "unittest",
            "make check", "make release-check", "build_distributions.py", "release.py bump",
            "git push", "secrets.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_each_release_job_holds_only_the_permission_it_needs(self):
        jobs = self.release_jobs()
        expected = {
            "verify": "    permissions:\n      contents: read\n      actions: read\n",
            "stage": "    permissions:\n      contents: write\n    runs-on",
            "public-smoke": "    permissions:\n      contents: read\n    runs-on",
            "rollback": "    permissions:\n      contents: write\n    runs-on",
            "finalize": "    permissions:\n      contents: write\n    runs-on",
        }
        for job, permissions in expected.items():
            with self.subTest(job=job):
                self.assertIn(permissions, jobs[job])
        self.assertIn("GH_TOKEN: ${{ github.token }}", jobs["verify"])

    def test_inputs_reach_scripts_only_through_the_environment(self):
        text = self.text("release.yml")
        for block in re.findall(r"(?ms)^        run: [|>]-?\n(.*?)(?=^      - |^  [\w-]+:\n|\Z)", text):
            with self.subTest(block=block[:60]):
                self.assertNotIn("${{", block)
        self.assertIn("VERSION: ${{ inputs.version }}", text)
        self.assertIn("CANDIDATE_SHA: ${{ inputs.sha || github.sha }}", text)

    def test_release_verifies_then_stages_then_smokes_then_finalizes(self):
        jobs = self.release_jobs()
        verify = jobs["verify"]
        self.assertIn("fetch-depth: 0", verify)
        self.assertIn("python3 tools/release.py verify-candidate", verify)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', verify)
        stage = jobs["stage"]
        self.assertIn("needs: verify", stage)
        self.assertIn("python3 tools/release_publish.py stage", stage)
        self.assertIn('prior=(--prior-stable-sha "$PRIOR_STABLE_SHA")', stage)
        self.assertIn("prior=(--bootstrap)", stage)
        self.assertIn('git config user.name "github-actions[bot]"', stage)
        smoke = jobs["public-smoke"]
        self.assertEqual(workflow_jobs(self.text("release.yml"))["public-smoke"]["if"],
                         "needs.stage.outputs.phase == 'staged'")
        self.assertIn("runs-on: macos-latest", smoke)
        self.assertIn("python3 trusted/tools/smoke_plugin_installs.py", smoke)
        self.assertIn("--root candidate", smoke)
        self.assertIn("--channel public", smoke)
        self.assertIn('--expected-sha "$EXPECTED_RELEASE_SHA"', smoke)
        self.assertIn("trusted/tools/data/host-cli-versions.json", smoke)
        rollback = jobs["rollback"]
        self.assertIn("python3 tools/release_publish.py rollback", rollback)
        self.assertIn("needs.public-smoke.result != 'success'", rollback)
        finalize = jobs["finalize"]
        self.assertIn("python3 tools/release.py release-notes", finalize)
        self.assertIn('--ref "$CANDIDATE_SHA"', finalize)
        self.assertIn("python3 tools/release_publish.py finalize", finalize)
        self.assertLess(finalize.index("release-notes"), finalize.index("release_publish.py finalize"))
        self.assertIn("needs.stage.outputs.phase == 'published'", finalize)
        self.assertIn("needs.public-smoke.result == 'success'", finalize)

    def test_write_jobs_run_trusted_main_code_never_candidate_code(self):
        jobs = self.release_jobs()
        for job in ("verify", "stage", "rollback", "finalize"):
            with self.subTest(job=job):
                self.assertNotIn("ref:", jobs[job])
                self.assertNotIn("candidate/", jobs[job])
        smoke = jobs["public-smoke"]
        self.assertEqual(smoke.count("persist-credentials: false"), 2)
        self.assertIn("ref: ${{ needs.verify.outputs.candidate_sha }}", smoke)
        self.assertIn("path: candidate", smoke)
        self.assertIn("path: trusted", smoke)
        self.assertNotIn("python3 candidate/", smoke)

    def test_ship_dispatches_this_workflow_with_its_inputs(self):
        self.assertEqual(release.RELEASE_WORKFLOW, "release.yml")
        self.assertTrue((REPO / ".github" / "workflows" / release.RELEASE_WORKFLOW).is_file())
        events = self.text("release.yml").split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^      ([a-z_]+):$", events), ["version", "sha"])

    def test_a_main_push_dispatches_the_release_only_through_its_decision(self):
        text = self.text("auto-release.yml")
        events = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(events, "  push:\n    branches: [main]\n")
        self.assertIn("permissions:\n  contents: read\n\njobs:", text)
        jobs = workflow_jobs(text)
        self.assertEqual(list(jobs), ["dispatch"])
        self.assertEqual((jobs["dispatch"]["if"], jobs["dispatch"]["needs"]), ("", []))
        block = text.split("\n  dispatch:\n", 1)[1]
        self.assertIn(
            "    permissions:\n      contents: read\n      actions: write\n    runs-on:", block,
        )
        self.assertIn("    timeout-minutes: 5\n", block)
        self.assertIn("persist-credentials: false", block)
        self.assertNotIn("ref:", block)
        self.assertIn("python3 tools/release.py auto-release", block)
        self.assertIn("BEFORE_SHA: ${{ github.event.before }}", block)
        self.assertIn("AFTER_SHA: ${{ github.sha }}", block)
        for run in re.findall(r"(?ms)^        run: [|>]-?\n(.*?)(?=^      - |^  [\w-]+:\n|\Z)", text):
            self.assertNotIn("${{", run)
        for forbidden in ("contents: write", "secrets.", "git push", "concurrency:",
                          "unittest", "release_publish.py"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_stable_and_tag_pushes_start_no_workflow(self):
        workflow_root = REPO / ".github" / "workflows"
        for workflow in sorted({*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml")}):
            text = workflow.read_text(encoding="utf-8")
            events = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
            with self.subTest(workflow=workflow.name):
                for event in ("create", "delete", "release", "registry_package", "workflow_run"):
                    self.assertNotRegex(events, rf"(?m)^  {event}:")
                self.assertNotIn("tags", events)
                if re.search(r"(?m)^  push:", events):
                    push = events.split("  push:\n", 1)[1]
                    push = re.split(r"(?m)^  \S", push, maxsplit=1)[0]
                    self.assertEqual(push, "    branches: [main]\n")

    def test_pull_requests_use_ordinary_checks_and_exact_host_gate(self):
        validate = self.text("validate.yml")
        codeql = self.text("codeql.yml")
        hosts = self.text("release-hosts.yml")
        self.assertIn("pull_request:", validate)
        self.assertIn('BASE_SHA: ${{ github.event.pull_request.base.sha }}', validate)
        self.assertIn('HEAD_SHA: ${{ github.event.pull_request.head.sha }}', validate)
        self.assertIn('check-pr --base "$BASE_SHA" --head "$HEAD_SHA"', validate)
        self.assertNotIn("--allow-bootstrap", validate)
        self.assertIn("pull_request:", codeql)
        self.assertIn("pull_request:\n", hosts)
        self.assertNotIn("pull_request:\n    paths:", hosts)
        for workflow, text in (("validate.yml", validate), ("release-hosts.yml", hosts)):
            with self.subTest(workflow=workflow):
                self.assertNotIn("workflow_call", text)
                self.assertNotIn("candidate_sha", text)
                self.assertNotIn("release/stable", text)
        self.assertIn("native-host-lifecycle", hosts)
        self.assertIn("if: always()", validate)
        jobs = workflow_jobs(validate)
        self.assertEqual(jobs["changeset"]["if"], "github.event_name == 'pull_request'")
        self.assertEqual(jobs["check"]["needs"], [
            "changeset", "plan", "test-shards", "deterministic-check",
        ])
        self.assertNotIn("release-pr-policy", jobs)
        self.assertNotIn("release-queue-policy", jobs)
        self.assertNotIn("compatibility", jobs)
        self.assertEqual(jobs["changeset"]["needs"], ["plan"])
        self.assertEqual(jobs["deterministic-check"]["needs"], ["plan"])

    def test_required_contexts_also_report_on_merge_queue_groups(self):
        trigger = "  merge_group:\n    types: [checks_requested]\n"
        for workflow, contexts in (
            ("validate.yml", ("name: check\n",)),
            ("codeql.yml", ("  analyze-python:\n",)),
            ("release-hosts.yml", ("name: Claude Code and Codex lifecycle\n",)),
        ):
            text = self.text(workflow)
            events = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
            with self.subTest(workflow=workflow):
                self.assertIn(trigger, events)
                self.assertIn("  pull_request:\n", events)
                self.assertNotIn("    branches:", events.split("  merge_group:\n", 1)[1])
                for context in contexts:
                    self.assertIn(context, text)
        self.assertNotIn("merge_group", self.text("release.yml"))

    def test_finalize_requires_a_release_github_reports_immutable(self):
        text = self.text("release.yml")
        block = self.release_jobs()["finalize"]
        self.assertEqual(text.count("release_publish.py finalize"), 1)
        self.assertIn("--require-immutable", block)
        self.assertEqual(text.count("--require-immutable"), 1)
        workflow_root = REPO / ".github" / "workflows"
        for workflow in sorted(workflow_root.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                for mutation in ("gh release edit", "gh release delete", "gh release upload"):
                    self.assertNotIn(mutation, text)

    def test_validation_is_read_only_and_pins_setup_python(self):
        text = self.text("validate.yml")
        self.assertIn("permissions:\n  contents: read\n  actions: read\n  pull-requests: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn('python-version: ${{ matrix.python }}', text)
        self.assertIn('persist-credentials: false', text)

    def test_every_python_a_workflow_sets_up_is_the_policy_version(self):
        policy = json.loads((REPO / "tools/data/ci-test-policy.json").read_text(encoding="utf-8"))
        hosts = json.loads((REPO / "tools/data/ci-host-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(hosts["python"], policy["python"])
        literals, expressions = {}, {}
        for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for job, block in re.findall(r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)",
                                         text.split("\njobs:\n", 1)[1]):
                for value in re.findall(r"(?m)^ +python-version: (.+)$", block):
                    target = expressions if value.startswith("${{") else literals
                    target.setdefault(value, []).append(f"{workflow.name}:{job}")
        # A job that runs before any plan sets up the policy's version itself.
        self.assertEqual(literals, {f'"{policy["python"]}"': [
            "release-hosts.yml:host-plan", "release.yml:public-smoke", "validate.yml:plan"]})
        self.assertEqual(expressions, {
            "${{ needs.host-plan.outputs.python }}": ["release-hosts.yml:fresh-host-lifecycle"],
            "${{ needs.plan.outputs.python }}": [
                "validate.yml:changeset", "validate.yml:deterministic-check", "validate.yml:check"],
            "${{ matrix.python }}": ["validate.yml:test-shards"]})

    def test_no_job_inherits_a_skip_its_need_tolerates(self):
        validate = workflow_jobs(self.text("validate.yml"))
        self.assertEqual(validate["check"]["if"], "always()")
        self.assertEqual(validate["check"]["needs"], [
            "changeset", "plan", "test-shards", "deterministic-check",
        ])
        release_jobs = workflow_jobs(self.text("release.yml"))
        self.assertEqual(release_jobs["stage"]["needs"], ["verify"])
        for job in ("rollback", "finalize"):
            with self.subTest(job=job):
                self.assertEqual(release_jobs[job]["needs"], ["verify", "stage", "public-smoke"])
                self.assertRegex(release_jobs[job]["if"], STATUS_CHECK_RE)
                self.assertIn("needs.stage.result == 'success'", release_jobs[job]["if"])
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

    def test_each_system_tests_the_policy_python_in_one_lane(self):
        text = self.text("validate.yml")
        policy = json.loads((REPO / "tools/data/ci-test-policy.json").read_text(encoding="utf-8"))
        self.assertEqual({lane["os"] for lane in policy["lanes"].values()},
                         {"ubuntu-latest", "macos-latest", "windows-latest"})
        self.assertEqual(len(policy["lanes"]), 3)
        self.assertTrue(all("python" not in lane and "interpreter" not in lane
                            for lane in policy["lanes"].values()))
        self.assertNotIn("apple_launcher_lane", policy)
        self.assertNotIn("vault-hook-platforms:", text)
        shard_job = text.split("\n  test-shards:\n", 1)[1].split("\n  check:\n", 1)[0]
        setup = shard_job.split("uses: actions/setup-python@", 1)[1].split("\n      - ", 1)[0]
        self.assertEqual([line.strip() for line in setup.splitlines()[1:]],
                         ["with:", "python-version: ${{ matrix.python }}"])
        for retired in ("apple_launcher", "Apple system Python", "/usr/bin/python3",
                        "AGENT_MARKETPLACE_REQUIRE_APPLE_PYTHON3", "interpreter_", "nuget", "pwsh"):
            self.assertNotIn(retired, text)
        self.assertNotIn("continue-on-error", shard_job)
        self.assertIn("TEST_RESULT: ${{ needs.test-shards.result }}", text)
        self.assertIn('test "$TEST_RESULT" = success', text)
        # The hook and the Delivery code keep native tests where their systems differ.
        native = {lane: [selector for group in ("platform", lane)
                         for selector in policy["groups"][group]["tests"]]
                  for lane in ("macos", "windows")}
        for lane, module in (("macos", "test_vault_hook"), ("windows", "test_vault_hook"),
                             ("windows", "test_delivery_compile"), ("windows", "test_delivery_git")):
            with self.subTest(lane=lane, module=module):
                self.assertTrue(any(selector.startswith(f"tools.tests.{module}.")
                                    for selector in native[lane]))

    def test_test_scratch_lives_on_the_runner_work_directory(self):
        shard_job = self.text("validate.yml").split("\n  test-shards:\n", 1)[1].split("\n  check:\n", 1)[0]
        run = shard_job.split("- name: Run the exact selected test partition\n", 1)[1]
        for name in ("TMPDIR", "TMP", "TEMP"):
            self.assertIn(f"{name}: ${{{{ runner.temp }}}}\n", run)

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
        self.assertNotIn("make release-check", workflow)
        self.assertNotIn("make public-release-check", workflow)
        self.assertIn("runs-on: ${{ needs.host-plan.outputs.runner_os }}", workflow)
        policy = json.loads((REPO / "tools/data/ci-host-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy, {"schema_version": 1, "runner_os": "macos-latest", "python": "3.14", "node": "24"})

        release_workflow = self.text("release.yml")
        self.assertNotIn("release-hosts.yml", release_workflow)
        self.assertIn('"@anthropic-ai/claude-code@${claude_version}"', release_workflow)
        self.assertIn('"@openai/codex@${codex_version}"', release_workflow)
        self.assertIn("EXPECTED_RELEASE_SHA", release_workflow)

    def test_release_check_requires_deterministic_gates(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^check: static-check test$")
        self.assertRegex(makefile, r"(?m)^static-check: validate release-validate counts-check dist-check$")
        self.assertRegex(makefile, r"(?m)^release-check: check$")
        self.assertRegex(makefile, r"(?m)^public-release-check: release-check public-release-smoke$")
        self.assertRegex(makefile, r"(?m)^public-release-smoke:$")
        self.assertRegex(makefile, r"(?m)^\s*PYTHONDONTWRITEBYTECODE=1 \$\(PY\) -m unittest")
        self.assertIn("tools/smoke_plugin_installs.py --channel public --expected-sha", makefile)

    def test_required_host_smoke_has_no_event_level_path_filter(self):
        workflow = self.text("release-hosts.yml")
        self.assertIn("  pull_request:\n", workflow)
        self.assertNotIn("    paths:", workflow)
        self.assertNotIn("    paths-ignore:", workflow)

    def test_validate_jobs_are_time_bounded(self):
        text = self.text("validate.yml")
        for name, block in re.findall(r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)", text.split("\njobs:\n", 1)[1]):
            with self.subTest(job=name):
                match = re.search(r"(?m)^    timeout-minutes: (\d+)$", block)
                self.assertIsNotNone(match)
                self.assertLessEqual(int(match.group(1)), 25)

    def test_validation_workflows_cancel_superseded_runs(self):
        for workflow in ("validate.yml", "codeql.yml", "release-hosts.yml"):
            with self.subTest(workflow=workflow):
                text = self.text(workflow)
                self.assertIn("cancel-in-progress: true", text)
                self.assertIn("github.event.pull_request.number || github.ref", text)
        self.assertIn("}}-validation", self.text("validate.yml"))
        self.assertIn("group: stable-release", self.text("release.yml"))
        self.assertIn("cancel-in-progress: false", self.text("release.yml"))

    def test_workflow_actions_are_allowlisted_and_sha_pinned(self):
        workflow_root = REPO / ".github" / "workflows"
        workflows = sorted({
            *workflow_root.glob("*.yml"),
            *workflow_root.glob("*.yaml"),
        })
        self.assertTrue(workflows)
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            policy = json.loads((REPO / "tools/data/ci-host-policy.json").read_text(encoding="utf-8")) \
                if workflow.name == "release-hosts.yml" else None
            self.assertEqual([], workflow_action_findings(workflow.name, text, policy))

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

    def test_host_node_policy_output_is_not_a_general_expression_exemption(self):
        text = self.text("release-hosts.yml")
        policy = json.loads((REPO / "tools/data/ci-host-policy.json").read_text(encoding="utf-8"))
        self.assertEqual([], workflow_action_findings("release-hosts.yml", text, policy))
        for name, content, contract in (
            ("other.yml", text, policy),
            ("release-hosts.yml", text, dict(policy, node="20")),
            ("release-hosts.yml", text.replace("needs.host-plan.outputs.node", "github.event.inputs.node"), policy),
            ("release-hosts.yml", text.replace("steps.policy.outputs.node", "steps.other.outputs.node"), policy),
            ("release-hosts.yml", text.replace("tools/ci_host_policy.py", "tools/other.py"), policy),
        ):
            with self.subTest(name=name, content=content, contract=contract):
                self.assertTrue(workflow_action_findings(name, content, contract))

    def test_required_host_context_accepts_only_a_fresh_lifecycle_success(self):
        text = self.text("release-hosts.yml")
        jobs = workflow_jobs(text)
        self.assertEqual(jobs["native-host-lifecycle"]["if"], "always()")
        self.assertEqual(jobs["native-host-lifecycle"]["needs"], ["host-plan", "fresh-host-lifecycle"])
        self.assertEqual(jobs["fresh-host-lifecycle"]["if"], "")
        fresh = text.split("\n  fresh-host-lifecycle:\n", 1)[1].split("\n  native-host-lifecycle:\n", 1)[0]
        required = text.split("\n  native-host-lifecycle:\n", 1)[1]
        self.assertIn("name: Claude Code and Codex lifecycle", required)
        self.assertIn("run: python3 tools/smoke_plugin_installs.py --channel checkout", fresh)
        self.assertNotIn("continue-on-error", fresh + required)
        self.assertIn("permissions:\n  contents: read\n\nconcurrency:", text)
        for retired in ("ci_host_evidence", "reused", "upload-artifact", "actions: read"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)

    @integration
    @unittest.skipUnless(shutil.which("bash"), "Bash workflow executor")
    def test_host_aggregate_executes_fail_closed_for_every_path(self):
        text = self.text("release-hosts.yml").split("\n  native-host-lifecycle:\n", 1)[1]
        block = re.search(r"        run: \|\n((?:          [^\n]*\n|\n)+)", text).group(1)
        script = "\n".join(line[10:] for line in block.splitlines())
        for plan in ("success", "failure", "cancelled", "skipped", ""):
            for fresh in ("success", "failure", "cancelled", "skipped", ""):
                with self.subTest(plan=plan, fresh=fresh):
                    result = subprocess.run(["bash", "-c", script], capture_output=True,
                                            env=dict(os.environ, PLAN_RESULT=plan,
                                                     FRESH_RESULT=fresh))
                    self.assertEqual(result.returncode == 0,
                                     plan == "success" and fresh == "success")

    @integration
    @unittest.skipUnless(shutil.which("bash"), "Bash workflow executor")
    def test_aggregate_executes_fail_closed_for_missing_failed_and_cancelled_work(self):
        text = self.text("validate.yml").split("\n  check:\n", 1)[1]
        block = re.search(r"        run: \|\n((?:          [^\n]*\n|\n)+)", text).group(1)
        script = "\n".join(line[10:] for line in block.splitlines())
        baseline = dict(os.environ, EVENT_NAME="pull_request", CHANGESET_RESULT="success",
                        PLAN_RESULT="success", DETERMINISTIC_RESULT="success",
                        HAS_TESTS="true", TEST_RESULT="success")
        def execute(values):
            return subprocess.run(["bash", "-c", script], env=values, capture_output=True).returncode
        self.assertEqual(execute(baseline), 0)
        for key in ("PLAN_RESULT", "DETERMINISTIC_RESULT", "TEST_RESULT", "CHANGESET_RESULT"):
            for status in ("failure", "cancelled", "skipped", ""):
                with self.subTest(key=key, status=status):
                    self.assertNotEqual(execute(dict(baseline, **{key: status})), 0)
        self.assertEqual(execute(dict(baseline, HAS_TESTS="false", TEST_RESULT="skipped")), 0)
        self.assertNotEqual(execute(dict(baseline, HAS_TESTS="", TEST_RESULT="skipped")), 0)
        for event in ("push", "merge_group", "schedule", "workflow_dispatch"):
            other = dict(baseline, EVENT_NAME=event, CHANGESET_RESULT="skipped")
            with self.subTest(event=event):
                self.assertEqual(execute(other), 0)
                self.assertNotEqual(execute(dict(other, CHANGESET_RESULT="success")), 0)
                for key in ("PLAN_RESULT", "DETERMINISTIC_RESULT", "TEST_RESULT"):
                    for status in ("failure", "cancelled", "skipped", ""):
                        self.assertNotEqual(execute(dict(other, **{key: status})), 0)

    def test_receipts_require_verified_reports_and_are_not_emitted_for_forks_or_schedule(self):
        text = self.text("validate.yml")
        self.assertLess(text.index("tools/ci_tests.py verify-reports"), text.index("tools/ci_evidence.py create"))
        self.assertIn("ci-evidence-${{ github.run_id }}-${{ github.run_attempt }}", text)
        self.assertIn("github.event_name != 'schedule'", text)
        # Receipt creation accepts no merge_group event; a queue run must skip it, not fail.
        receipt_guard = "github.event_name != 'schedule' && github.event_name != 'merge_group' &&"
        self.assertEqual(text.count(receipt_guard), 2)
        self.assertEqual(text.count("github.event_name != 'schedule'"), 2)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn('args+=(--inherited "$RUNNER_TEMP/ci-plan/ci-reuse.json")', text)
        self.assertIn('if [ "$CI_MODE" = reuse ]; then', text)
        self.assertNotIn("--candidate-sha", text)
        self.assertIn("if-no-files-found: error", text)

    def test_failed_job_retries_keep_plan_and_report_identity_but_replace_evidence_attempt(self):
        text = self.text("validate.yml")
        self.assertEqual(text.count("name: ci-plan-${{ github.run_id }}\n"), 3)
        self.assertIn("name: ci-report-${{ github.run_id }}-${{ matrix.lane }}-${{ matrix.shard }}", text)
        self.assertIn("pattern: ci-report-${{ github.run_id }}-*", text)
        self.assertEqual(text.count("overwrite: true"), 2)
        evidence = text.split("name: ci-evidence-", 1)[1]
        self.assertTrue(evidence.startswith("${{ github.run_id }}-${{ github.run_attempt }}\n"))
        self.assertNotIn("overwrite: true", evidence)


if __name__ == "__main__":
    unittest.main()
