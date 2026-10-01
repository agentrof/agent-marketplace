"""Exercise CI orchestration with real Git trees and trusted release replay."""

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
import release  # noqa: E402
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
        self.git("commit", "-qm", "stable")
        self.stable = self.git("rev-parse", "HEAD")
        fixtures.write(self.root / ".changes/ci-plan.json", json.dumps({
            "summary": "Exercise release CI orchestration.",
            "components": {fixtures.PLUGIN: "patch"},
        }))
        self.git("add", "--all")
        self.git("commit", "-qm", "main source")
        self.base = self.git("rev-parse", "HEAD")
        self.base_plan = ci_tests.make_plan(self.root, "full")
        release.prepare(
            self.root, self.stable, self.base,
            released_paths=release.changeset_paths_at_ref(self.root, self.stable),
        )
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "-qm", "chore: prepare stable v0.0.2")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)
        self.git("merge", "--no-ff", "-qm", "Merge release candidate", self.head)
        self.merge = self.git("rev-parse", "HEAD")
        self.event = {
            "number": 9,
            "pull_request": {
                "base": {"sha": self.base, "ref": "main"},
                "head": {
                    "sha": self.head, "ref": "release/stable",
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

    def api(self, *, pull_request=False, missing=False):
        plan = ci_tests.make_plan(self.root, "full") if pull_request else self.base_plan
        value = evidence_fixtures.receipt()
        value.update(
            plan=plan, plan_hash=plan["plan_hash"], tested_sha=plan["source_sha"],
            tested_tree=plan["source_tree"],
            contract_hash=ci_evidence.contract_hash(self.root, plan["source_sha"]),
            event="pull_request" if pull_request else "push",
            head_sha=self.head if pull_request else self.base,
            ref="refs/pull/9/merge" if pull_request else "refs/heads/main",
            pull_request=({
                "number": 9, "head_sha": self.head,
                "head_repository": evidence_fixtures.REPO, "base_ref": "main",
            } if pull_request else None),
            runtimes={name: {
                "python_version": lane["python"] + ".1",
                "python_implementation": "CPython", "os_release": "fixture",
                "machine": "fixture", "git_version": "git version fixture",
                "os": {"ubuntu-latest": "Linux", "macos-latest": "Darwin", "windows-latest": "Windows"}[lane["os"]],
            } for name, lane in plan["lanes"].items()},
        )
        api = evidence_fixtures.FakeGitHub(value)
        api.run.update(
            event=value["event"], head_sha=value["head_sha"],
            head_branch="release/stable" if pull_request else "main",
        )
        api.pull.update(merge_commit_sha=self.merge)
        api.pull["head"].update(sha=self.head, ref="release/stable")
        api.commit_changes = {
            "sha": value["tested_sha"], "tree": {"sha": value["tested_tree"]},
            "parents": ([{"sha": self.base}, {"sha": self.head}] if pull_request else [{"sha": self.stable}]),
        }
        if missing:
            api.runs = []
        return api

    def choose(self, *, api=None, event_name="pull_request", supplied="", event=None):
        original = ci_plan.run
        def run(*arguments):
            if len(arguments) > 2 and arguments[1:3] == ("tools/ci_evidence.py", "find"):
                self.assertIsNotNone(api, "unexpected evidence request")
                expected = arguments[arguments.index("--expected-sha") + 1]
                mode = arguments[arguments.index("--mode") + 1]
                output = Path(arguments[arguments.index("--output") + 1])
                self.queries.append((mode, expected))
                result = ci_evidence.find_evidence(
                    self.root, api, expected, mode, evidence_fixtures.NOW,
                )
                output.write_text(json.dumps(result), encoding="utf-8")
                return json.dumps(result)
            return original(*arguments)
        with mock.patch.object(ci_plan, "run", side_effect=run), mock.patch.dict(
            os.environ, {"GITHUB_REF": "refs/heads/main"},
        ):
            return ci_plan.choose_mode(
                event if event is not None else self.event,
                event_name, supplied, evidence_fixtures.REPO,
                self.git("rev-parse", "HEAD"), self.output,
            )

    def test_normal_pr_selects_impact_without_consulting_prior_runs(self):
        event = copy.deepcopy(self.event)
        event["pull_request"]["head"]["ref"] = "codex/feature"
        self.assertEqual(self.choose(event=event), ("impact", self.base))
        self.assertEqual(self.queries, [])

    def test_release_replay_and_main_evidence_work_across_different_trees(self):
        self.assertNotEqual(self.base_plan["source_tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.choose(api=self.api()), ("release", self.base))
        result = json.loads((self.output / "release-delta.json").read_text())
        self.assertTrue(result["release_only"], result)
        self.assertEqual(self.queries, [("prepare", self.base)])

    def test_release_without_main_evidence_runs_full_coverage(self):
        self.assertEqual(self.choose(api=self.api(missing=True)), ("full", self.base))

    def test_release_branch_label_cannot_skip_changed_runtime(self):
        self.git("checkout", "-q", "--detach", self.head)
        path = self.root / "plugins" / fixtures.PLUGIN / "scripts/atomic_file.py"
        path.write_bytes(path.read_bytes() + b"\n# altered runtime\n")
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "--amend", "--no-edit", "-q")
        self.event["pull_request"]["head"]["sha"] = self.git("rev-parse", "HEAD")
        self.assertEqual(self.choose(), ("full", self.base))
        self.assertEqual(self.queries, [])

    def test_malformed_release_metadata_falls_back_to_full(self):
        self.git("checkout", "-q", "--detach", self.head)
        (self.root / ".release/stable.json").write_text("not JSON\n", encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--amend", "--no-edit", "-q")
        self.event["pull_request"]["head"]["sha"] = self.git("rev-parse", "HEAD")
        self.assertEqual(self.choose(), ("full", self.base))

    def test_main_merge_reuses_only_a_successful_exact_pr_tree(self):
        self.assertEqual(self.choose(api=self.api(pull_request=True), event_name="push"), ("reuse", ""))
        self.assertEqual(self.queries, [("main", self.merge)])

    def test_missing_proof_runs_full_for_main_prepare_and_publish(self):
        for event_name, supplied, wanted in (
            ("push", "", "main"),
            ("workflow_dispatch", self.merge, "prepare"),
            ("pull_request_target", self.merge, "publish"),
        ):
            with self.subTest(event=event_name):
                api = self.api(pull_request=True, missing=True)
                self.assertEqual(self.choose(api=api, event_name=event_name, supplied=supplied), ("full", ""))
                self.assertEqual(self.queries[-1], (wanted, self.merge))

    def test_publish_cannot_reuse_push_evidence_with_the_same_tree(self):
        api = self.api(pull_request=True)
        api.run["event"] = "push"
        self.assertEqual(self.choose(api=api, event_name="pull_request_target", supplied=self.merge), ("full", ""))

    def test_fork_release_branch_is_an_ordinary_impact_pr(self):
        event = copy.deepcopy(self.event)
        event["pull_request"]["head"]["repo"]["full_name"] = "fork/project"
        self.assertEqual(self.choose(event=event), ("impact", self.base))

    def test_merge_queue_group_selects_impact_against_the_queue_base(self):
        event = {"merge_group": {
            "base_sha": self.base, "head_sha": self.merge,
            "base_ref": "refs/heads/main",
            "head_ref": f"refs/heads/gh-readonly-queue/main/pr-9-{self.base}",
        }}
        self.assertEqual(self.choose(event_name="merge_group", event=event), ("impact", self.base))
        self.assertEqual(self.queries, [])

    def test_manual_and_scheduled_refresh_always_run_full(self):
        for event in ("workflow_dispatch", "schedule"):
            with self.subTest(event=event):
                self.assertEqual(self.choose(event_name=event), ("full", ""))
        self.assertEqual(self.queries, [])


if __name__ == "__main__":
    unittest.main()
