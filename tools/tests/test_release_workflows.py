"""Fail-closed contracts for repository validation and release workflows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import re
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
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
            "./.github/workflows/validate.yml",
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
        self.assertIn("native-host-lifecycle", hosts)
        self.assertIn("if: always()", validate)
        jobs = workflow_jobs(validate)
        self.assertEqual(jobs["changeset"]["if"], "github.event_name == 'pull_request'")
        self.assertEqual(jobs["check"]["needs"], [
            "changeset", "plan", "test-shards", "deterministic-check",
        ])
        self.assertEqual(jobs["changeset"]["needs"], ["plan"])
        self.assertEqual(jobs["deterministic-check"]["needs"], ["plan"])

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
            "release-hosts.yml:native-host-lifecycle", "release.yml:public-smoke", "validate.yml:plan"]})
        self.assertEqual(expressions, {
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

    def test_each_system_tests_the_policy_python_in_one_lane(self):
        text = self.text("validate.yml")
        policy = json.loads((REPO / "tools/data/ci-test-policy.json").read_text(encoding="utf-8"))
        self.assertEqual({lane["os"] for lane in policy["lanes"].values()},
                         {"ubuntu-latest", "macos-latest", "windows-latest"})
        self.assertEqual(len(policy["lanes"]), 3)
        shard_job = text.split("\n  test-shards:\n", 1)[1].split("\n  check:\n", 1)[0]
        setup = shard_job.split("uses: actions/setup-python@", 1)[1].split("\n      - ", 1)[0]
        self.assertEqual([line.strip() for line in setup.splitlines()[1:]],
                         ["with:", "python-version: ${{ matrix.python }}"])
        self.assertNotIn("continue-on-error", shard_job)
        self.assertIn("TEST_RESULT: ${{ needs.test-shards.result }}", text)
        self.assertIn('test "$TEST_RESULT" = success', text)

    def test_test_scratch_lives_on_the_runner_work_directory(self):
        shard_job = self.text("validate.yml").split("\n  test-shards:\n", 1)[1].split("\n  check:\n", 1)[0]
        run = shard_job.split("- name: Run the exact selected test partition\n", 1)[1]
        for name in ("TMPDIR", "TMP", "TEMP"):
            self.assertIn(f"{name}: ${{{{ runner.temp }}}}\n", run)

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
        self.assertIn('claude --version | grep -F "${claude_version}"', workflow)
        self.assertIn('codex --version | grep -F "${codex_version}"', workflow)
        self.assertIn("tools/smoke_plugin_installs.py --channel checkout", workflow)
        self.assertIn("runs-on: macos-latest", workflow)

        release_workflow = self.text("release.yml")
        self.assertNotIn("release-hosts.yml", release_workflow)
        self.assertIn('"@anthropic-ai/claude-code@${claude_version}"', release_workflow)
        self.assertIn('"@openai/codex@${codex_version}"', release_workflow)
        self.assertIn("EXPECTED_RELEASE_SHA", release_workflow)

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
            self.assertEqual([], workflow_action_findings(workflow.name, text))

    def test_codeql_scans_python_on_pr_main_and_schedule(self):
        text = self.text("codeql.yml")
        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("schedule:", text)
        self.assertIn("languages: python\n", text)
        self.assertIn("build-mode: none", text)
        self.assertIn("security-events: write", text)

    def test_node24_runtime_contract_rejects_each_stale_shape(self):
        self.assertTrue(workflow_action_findings(
            "unapproved-ref", "- uses: actions/setup-python@deadbeef\n"))

    def test_required_host_context_runs_the_lifecycle_unless_no_host_path_changed(self):
        text = self.text("release-hosts.yml")
        self.assertEqual(list(workflow_jobs(text)), ["native-host-lifecycle"])
        self.assertIn("name: Claude Code and Codex lifecycle", text)
        self.assertIn("run: python3 tools/smoke_plugin_installs.py --channel checkout", text)
        self.assertNotIn("continue-on-error", text)
        self.assertIn("permissions:\n  contents: read\n\nconcurrency:", text)
        steps = text.split("\n      - ")[2:]
        self.assertTrue(steps[0].startswith("name: Decide whether the change reaches a host"))
        for step in steps[1:]:
            with self.subTest(step=step.splitlines()[0]):
                self.assertIn("if: steps.scope.outputs.hosts == 'true'", step)

    @integration
    @unittest.skipUnless(shutil.which("bash"), "Bash workflow executor")
    def test_host_scope_runs_the_lifecycle_for_a_host_path_and_fails_closed(self):
        text = self.text("release-hosts.yml")
        block = re.search(r"        run: \|\n((?:          [^\n]*\n|\n)+)", text).group(1)
        script = "\n".join(line[10:] for line in block.splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake_git = directory / "git"
            fake_git.write_text('#!/bin/sh\n[ -n "$CHANGED" ] || exit 1\nprintf "%s\\n" "$CHANGED"\n',
                                encoding="utf-8")
            fake_git.chmod(0o755)
            output = directory / "output"

            def scope(event: str, changed: str) -> str | None:
                output.write_text("", encoding="utf-8")
                result = subprocess.run(["bash", "-c", script], capture_output=True, env=dict(
                    os.environ, PATH=f"{directory}{os.pathsep}{os.environ['PATH']}",
                    EVENT_NAME=event, CHANGED=changed, GITHUB_OUTPUT=str(output)))
                return output.read_text(encoding="utf-8").strip() if result.returncode == 0 else None

            self.assertEqual(scope("pull_request", "docs/ci.md\ntools/tests/test_x.py"), "hosts=false")
            self.assertEqual(scope("pull_request", "docs/ci.md\nplugins/team/flows/a.md"), "hosts=true")
            self.assertEqual(scope("schedule", ""), "hosts=true")
            self.assertIsNone(scope("pull_request", ""))

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
            with self.subTest(key=key):
                self.assertNotEqual(execute(dict(baseline, **{key: "skipped"})), 0)
        self.assertEqual(execute(dict(baseline, HAS_TESTS="false", TEST_RESULT="skipped")), 0)
        self.assertNotEqual(execute(dict(baseline, HAS_TESTS="", TEST_RESULT="skipped")), 0)
        push = dict(baseline, EVENT_NAME="push", CHANGESET_RESULT="skipped")
        self.assertEqual(execute(push), 0)
        self.assertNotEqual(execute(dict(push, CHANGESET_RESULT="success")), 0)

    def test_receipts_require_verified_reports_and_are_not_emitted_for_forks_or_schedule(self):
        text = self.text("validate.yml")
        self.assertLess(text.index("tools/ci_tests.py verify-reports"), text.index("tools/ci_evidence.py create"))
        self.assertIn("ci-evidence-${{ github.run_id }}-${{ github.run_attempt }}", text)
        self.assertIn("github.event_name != 'schedule'", text)
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
