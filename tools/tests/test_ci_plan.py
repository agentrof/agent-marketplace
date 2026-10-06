"""Exercise CI orchestration with real Git trees and verified PR evidence."""

from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
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


@integration
class CIPlanIntegrationTests(unittest.TestCase):
    """One repository serves every test: the tests read its history and never change it."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "repository"
        cls.root.mkdir()
        try:
            fixtures.make_valid_root(cls.root)
            shutil.copytree(
                TESTS.parent, cls.root / "tools", dirs_exist_ok=True,
                ignore=build_distributions.ignore_python_cache,
            )
            fixtures.copy(".github/workflows", cls.root)
            fixtures.copy("Makefile", cls.root)
            git_fixture.init_repository(cls.root)
            cls.git("config", "user.name", "CI tests")
            cls.git("config", "user.email", "ci@example.invalid")
            cls.git("config", "core.autocrlf", "false")
            cls.git("add", "--all")
            for metadata in (cls.root / "dist").glob("*/*/.agent-marketplace-package.json"):
                value = json.loads(metadata.read_text(encoding="utf-8"))
                for executable in value["executables"]:
                    path = (metadata.parent / executable).relative_to(cls.root).as_posix()
                    cls.git("update-index", "--chmod=+x", path)
            cls.git("commit", "-qm", "main")
            cls.base = cls.git("rev-parse", "HEAD")
            cls.git("checkout", "-q", "-b", "feature")
            fixtures.write(cls.root / ".changes/ci-plan.json", json.dumps({
                "summary": "Exercise CI orchestration.",
                "components": {fixtures.PLUGIN: "patch"},
            }))
            cls.git("add", "--all")
            cls.git("commit", "-qm", "feature")
            cls.head = cls.git("rev-parse", "HEAD")
            cls.git("checkout", "-q", "--detach", cls.base)
            cls.git("merge", "--no-ff", "-qm", "Merge pull request #9", cls.head)
            cls.merge = cls.git("rev-parse", "HEAD")
        except BaseException:
            git_fixture.remove_temporary(cls.temporary)
            raise
        cls.full_plan = None

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.temporary)

    def setUp(self):
        output = tempfile.TemporaryDirectory()
        self.addCleanup(output.cleanup)
        self.output = Path(output.name)
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

    @classmethod
    def git(cls, *arguments):
        return subprocess.run(
            ["git", *arguments], cwd=cls.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def api(self, *, missing=False):
        value = evidence_fixtures.receipt()
        if not missing:
            # The full plan of the unchanging repository is built once.
            if type(self).full_plan is None:
                type(self).full_plan = ci_tests.make_plan(self.root, "full")
            plan = copy.deepcopy(self.full_plan)
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
                    "os": {"ubuntu-latest": "Linux", "macos-latest": "Darwin",
                           "windows-latest": "Windows"}[lane["os"]],
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

    def test_normal_pr_runs_the_full_suite_without_consulting_prior_runs(self):
        self.assertEqual(self.choose(), ("full", ""))
        self.assertEqual(self.queries, [])

    def test_main_merge_reuses_only_a_successful_exact_pr_tree(self):
        self.assertEqual(self.choose(api=self.api(), event_name="push"), ("reuse", ""))
        self.assertEqual(self.queries, [self.merge])

    def test_missing_proof_runs_full_on_main(self):
        self.assertEqual(
            self.choose(api=self.api(missing=True), event_name="push"), ("full", ""),
        )
        self.assertEqual(self.queries, [self.merge])

    def test_merge_queue_group_runs_the_full_suite(self):
        event = {"merge_group": {
            "base_sha": self.base, "head_sha": self.merge,
            "base_ref": "refs/heads/main",
            "head_ref": f"refs/heads/gh-readonly-queue/main/pr-9-{self.base}",
        }}
        self.assertEqual(self.choose(event_name="merge_group", event=event), ("full", ""))
        self.assertEqual(self.queries, [])

    def test_manual_scheduled_and_other_events_always_run_full(self):
        for event in ("workflow_dispatch", "schedule", "pull_request_target", "workflow_call"):
            with self.subTest(event=event):
                self.assertEqual(self.choose(event_name=event), ("full", ""))
        self.assertEqual(self.queries, [])

    def test_the_plan_job_outputs_the_exact_python_release_every_later_job_sets_up(self):
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
        # The release the plan job resolved for the policy's major.minor, never the major.minor alone (#443).
        release = platform.python_version()
        self.assertTrue(release.startswith(ci_tests.policy_at(self.root)["python"] + "."))
        self.assertEqual((sorted(values), values["python"], values["has_tests"], values["mode"]),
                         (["has_tests", "matrix", "mode", "python"], release, "true", "full"))
        self.assertEqual({row["python"] for row in json.loads(values["matrix"])["include"]}, {release})
        plan = json.loads((self.output / "plan" / "ci-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["python_release"], release)


if __name__ == "__main__":
    unittest.main()
