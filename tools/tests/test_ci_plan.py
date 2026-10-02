"""Exercise CI orchestration with real Git trees and verified PR evidence."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import build_distributions  # noqa: E402
import ci_evidence  # noqa: E402
import ci_plan  # noqa: E402
import ci_tests  # noqa: E402
import fixtures  # noqa: E402
import git_fixture  # noqa: E402
import test_ci_evidence as evidence_fixtures  # noqa: E402


class CIPlanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(git_fixture.remove_temporary, self.temporary)
        self.root = Path(self.temporary.name) / "repository"
        self.root.mkdir()
        self.output = Path(self.temporary.name) / "output"
        self.output.mkdir()
        fixtures.make_valid_root(self.root)
        shutil.copytree(
            TESTS.parent, self.root / "tools", dirs_exist_ok=True,
            ignore=build_distributions.ignore_python_cache,
        )
        fixtures.copy(".github/workflows", self.root)
        fixtures.copy("Makefile", self.root)
        git_fixture.init_repository(self.root)
        self.git("config", "user.name", "CI tests")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "--all")
        for metadata in (self.root / "dist").glob("*/*/.agent-marketplace-package.json"):
            value = json.loads(metadata.read_text(encoding="utf-8"))
            for executable in value["executables"]:
                path = (metadata.parent / executable).relative_to(self.root).as_posix()
                self.git("update-index", "--chmod=+x", path)
        self.git("commit", "-qm", "main")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "feature")
        fixtures.write(self.root / ".changes/ci-plan.json", json.dumps({
            "summary": "Exercise CI orchestration.",
            "components": {fixtures.PLUGIN: "patch"},
        }))
        self.git("add", "--all")
        self.git("commit", "-qm", "feature")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)
        self.git("merge", "--no-ff", "-qm", "Merge pull request #9", self.head)
        self.merge = self.git("rev-parse", "HEAD")
        self.event = {
            "number": 9,
            "pull_request": {
                "base": {"sha": self.base, "ref": "main"},
                "head": {
                    "sha": self.head, "ref": "codex/feature",
                    "repo": {"full_name": evidence_fixtures.REPO},
                },
            },
        }
        self.root_patch = mock.patch.object(ci_plan, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.queries = []

    def git(self, *arguments):
        return subprocess.run(
            ["git", *arguments], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def api(self, *, missing=False):
        plan = ci_tests.make_plan(self.root, "full")
        value = evidence_fixtures.receipt()
        value.update(
            plan=plan, plan_hash=plan["plan_hash"], tested_sha=plan["source_sha"],
            tested_tree=plan["source_tree"],
            contract_hash=ci_evidence.contract_hash(self.root, plan["source_sha"]),
            event="pull_request", head_sha=self.head, ref="refs/pull/9/merge",
            pull_request={
                "number": 9, "head_sha": self.head,
                "head_repository": evidence_fixtures.REPO, "base_ref": "main",
            },
            runtimes={name: {
                "python_version": lane["python"] + ".1",
                "python_implementation": "CPython", "os_release": "fixture",
                "machine": "fixture", "git_version": "git version fixture",
                "os": {"ubuntu-latest": "Linux", "macos-latest": "Darwin", "windows-latest": "Windows"}[lane["os"]],
            } for name, lane in plan["lanes"].items()},
        )
        api = evidence_fixtures.FakeGitHub(value)
        api.run.update(event="pull_request", head_sha=self.head, head_branch="codex/feature")
        api.pull.update(merge_commit_sha=self.merge)
        api.pull["head"].update(sha=self.head, ref="codex/feature")
        api.commit_changes = {
            "sha": value["tested_sha"], "tree": {"sha": value["tested_tree"]},
            "parents": [{"sha": self.base}, {"sha": self.head}],
        }
        if missing:
            api.runs = []
        return api

    def choose(self, *, api=None, event_name="pull_request", event=None):
        original = ci_plan.run

        def run(*arguments):
            if len(arguments) > 2 and arguments[1:3] == ("tools/ci_evidence.py", "find"):
                self.assertIsNotNone(api, "unexpected evidence request")
                self.assertNotIn("--mode", arguments)
                expected = arguments[arguments.index("--expected-sha") + 1]
                output = Path(arguments[arguments.index("--output") + 1])
                self.queries.append(expected)
                result = ci_evidence.find_evidence(
                    self.root, api, expected, evidence_fixtures.NOW,
                )
                output.write_text(json.dumps(result), encoding="utf-8")
                return json.dumps(result)
            return original(*arguments)

        with mock.patch.object(ci_plan, "run", side_effect=run), mock.patch.dict(
            os.environ, {"GITHUB_REF": "refs/heads/main"},
        ):
            return ci_plan.choose_mode(
                event if event is not None else self.event,
                event_name, self.git("rev-parse", "HEAD"), self.output,
            )

    def test_normal_pr_selects_impact_without_consulting_prior_runs(self):
        self.assertEqual(self.choose(), ("impact", self.base))
        self.assertEqual(self.queries, [])

    def test_a_release_named_branch_is_an_ordinary_impact_pr(self):
        # A release commit rides in an ordinary pull request; no branch name
        # selects a narrower test profile.
        for repository in (evidence_fixtures.REPO, "fork/project"):
            with self.subTest(repository=repository):
                event = copy.deepcopy(self.event)
                event["pull_request"]["head"].update(
                    ref="release/stable", repo={"full_name": repository},
                )
                self.assertEqual(self.choose(event=event), ("impact", self.base))
        self.assertEqual(self.queries, [])

    def test_main_merge_reuses_only_a_successful_exact_pr_tree(self):
        self.assertEqual(self.choose(api=self.api(), event_name="push"), ("reuse", ""))
        self.assertEqual(self.queries, [self.merge])

    def test_missing_proof_runs_full_on_main(self):
        self.assertEqual(
            self.choose(api=self.api(missing=True), event_name="push"), ("full", ""),
        )
        self.assertEqual(self.queries, [self.merge])

    def test_merge_queue_group_selects_impact_against_the_queue_base(self):
        event = {"merge_group": {
            "base_sha": self.base, "head_sha": self.merge,
            "base_ref": "refs/heads/main",
            "head_ref": f"refs/heads/gh-readonly-queue/main/pr-9-{self.base}",
        }}
        self.assertEqual(self.choose(event_name="merge_group", event=event), ("impact", self.base))
        self.assertEqual(self.queries, [])

    def test_manual_scheduled_and_other_events_always_run_full(self):
        for event in ("workflow_dispatch", "schedule", "pull_request_target", "workflow_call"):
            with self.subTest(event=event):
                self.assertEqual(self.choose(event_name=event), ("full", ""))
        self.assertEqual(self.queries, [])

    def test_the_plan_job_outputs_the_policy_python_every_later_job_sets_up(self):
        original = ci_plan.run

        def run(*arguments):
            if arguments[1:3] == ("tools/ci_evidence.py", "timings"):
                output = Path(arguments[arguments.index("--output") + 1])
                output.write_text(json.dumps({"schema_version": 1, "durations": {}}), encoding="utf-8")
                return ""
            return original(*arguments)

        outputs = self.output / "github-output"
        with mock.patch.object(ci_plan, "run", side_effect=run), mock.patch.dict(
                os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}), \
                mock.patch.object(sys, "argv", ["ci_plan.py", "--output-directory", str(self.output / "plan"),
                                                "--github-output", str(outputs)]):
            os.environ.pop("GITHUB_EVENT_PATH", None)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            self.assertEqual(ci_plan.main(), 0)
        values = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
        version = ci_tests.policy_at(self.root)["python"]
        self.assertEqual((sorted(values), values["python"], values["has_tests"], values["mode"]),
                         (["has_tests", "matrix", "mode", "python"], version, "true", "full"))
        self.assertEqual({row["python"] for row in json.loads(values["matrix"])["include"]}, {version})


if __name__ == "__main__":
    unittest.main()
