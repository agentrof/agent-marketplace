"""Impact-based local proof and the merge train: what a change must prove, and one proof for a batch of pull requests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
import project_config  # noqa: E402
import proof_train  # noqa: E402
import task_lane  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402

try:
    from tools.tests.levels import integration
except ImportError:
    from levels import integration

MAP = [{"paths": ["docs/*"], "none": True, "reason": "documents"},
       {"paths": ["*auth*"], "commands": ["echo full"], "reason": "security"},
       {"paths": ["apps/a/*"], "commands": ["echo a"], "reason": "app a"}]
BASE_CONFIG = {"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English",
               "terminology_language": "English", "local_proof": ["echo full"]}


class ProofPlanTests(unittest.TestCase):
    def plan(self, files, rules=MAP):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root))
        (root / "workspace").mkdir()
        (root / "workspace/config.json").write_text(json.dumps({"local_proof": ["echo full"], "local_proof_map": rules}))
        return task_lane.proof_plan(root, "main", set(files))

    def test_a_change_the_map_covers_selects_only_its_commands(self):
        self.assertEqual(self.plan({"apps/a/x.py"})["commands"], ["echo a"])
        self.assertEqual(self.plan({"docs/x.md"}), {"selection": "impact", "commands": [], "why": ["documents"]})

    def test_one_uncovered_path_or_no_map_selects_the_full_proof(self):
        self.assertEqual(self.plan({"apps/a/x.py", "other.txt"})["selection"], "full")
        self.assertEqual(self.plan({"apps/a/x.py"}, rules=[])["commands"], ["echo full"])

    def test_the_first_matching_rule_wins_and_commands_are_not_repeated(self):
        plan = self.plan({"apps/a/auth.py", "apps/a/y.py"})
        self.assertEqual(plan["commands"], ["echo full", "echo a"])

    def test_the_config_check_refuses_a_rule_without_commands_or_none(self):
        self.assertEqual(project_config.base_errors({**BASE_CONFIG, "local_proof_map": MAP}), [])
        errors = project_config.base_errors({**BASE_CONFIG, "local_proof_map": [{"paths": ["a"]}]})
        self.assertTrue(any("local_proof_map" in item for item in errors))

    def test_a_proof_runner_wraps_each_command(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root))
        (root / "workspace").mkdir()
        (root / "workspace/config.json").write_text(json.dumps({"proof_runner": "/q/run.sh"}))
        self.assertEqual(task_lane.proof_runner(root, "make test"), "/q/run.sh sh -c 'make test'")


@integration
class MergeTrainTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        init_repository(base / "origin.git", bare=True, initial_branch="main")
        self.root = base / "work"
        subprocess.run(["git", "clone", "-q", str(base / "origin.git"), str(self.root)], check=True)
        self.git("checkout", "-q", "-b", "main")
        (self.root / "workspace").mkdir()
        self.config({"local_proof": ["test ! -e bad"]})
        (self.root / ".gitignore").write_text(".agentrof\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.git("push", "-q", "origin", "main")

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=self.root,
                              check=True, capture_output=True, text=True).stdout.strip()

    def config(self, value):
        (self.root / "workspace/config.json").write_text(json.dumps(value))

    def member(self, number, bad=False):
        self.git("checkout", "-q", "-b", f"t{number}", "main")
        (self.root / f"f{number}").write_text("x")
        if bad:
            (self.root / "bad").write_text("x")
        self.git("add", "-A")
        self.git("commit", "-qm", f"t{number}")
        task_lane.cmd_prove(SimpleNamespace(task=f"TK-00{number}", base="main", deferred=True), self.root)
        return proof_train.cmd_train(SimpleNamespace(verb="add", task=f"TK-00{number}", pr=str(number), base="main",
                                                     critical=0, non_critical=0, filed=0, detail=None,
                                                     delegated=False, style="merge"), self.root, task_lane)

    def train(self, verb, **options):
        args = SimpleNamespace(**{"verb": verb, "base": "main", "batch": None, "dry_run": False, **options})
        return proof_train.cmd_train(args, self.root, task_lane)

    def test_a_green_batch_is_proved_once_satisfies_each_member_and_merges_through_the_review_path(self):
        self.member(1)
        self.member(2)
        self.assertEqual(self.train("status")["batches"][0]["members"], ["TK-001", "TK-002"])
        result = self.train("prove")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["commands"]), 1)
        self.git("checkout", "-q", "t1")
        problems, record = task_lane.proof_problems(self.root, "TK-001")
        self.assertEqual((problems, record["selection"]), ([], "train"))
        with mock.patch.object(task_lane, "cmd_merge") as merge:
            self.assertEqual(self.train("merge", batch=1)["merged"], ["TK-001", "TK-002"])
        self.assertEqual(merge.call_count, 2)
        self.assertIn("merge train batch 1, integration HEAD", merge.call_args.args[0].detail)

    def test_a_pull_request_added_after_the_proof_started_waits_for_the_next_batch(self):
        self.member(1)
        self.train("prove")
        joined = self.member(2)
        self.assertEqual(joined["batch"], 2)

    def test_a_red_batch_does_not_merge_and_bisect_names_the_member_that_broke_it(self):
        for number, bad in ((1, False), (2, True), (3, False)):
            self.member(number, bad)
        self.assertFalse(self.train("prove")["ok"])
        with self.assertRaisesRegex(task_lane.Refused, "red"):
            self.train("merge", batch=1)
        self.assertEqual(self.train("bisect", batch=1)["culprit"], "TK-002")

    def test_one_batch_proof_runs_at_a_time(self):
        self.member(1)
        state = proof_train.State(proof_train.home(self.root, task_lane))
        state.data["batches"].append({"id": 2, "status": proof_train.SEALED, "pid": __import__("os").getpid(),
                                      "members": [{"task": "TK-009"}]})
        state.save()
        state.close()
        with self.assertRaisesRegex(task_lane.Refused, "one batch proof runs at a time"):
            self.train("prove", batch=1)

    def test_a_review_with_critical_findings_cannot_join(self):
        self.git("checkout", "-q", "-b", "t5", "main")
        task_lane.cmd_prove(SimpleNamespace(task="TK-005", base="main", deferred=True), self.root)
        args = SimpleNamespace(verb="add", task="TK-005", pr="5", base="main", critical=1, non_critical=0, filed=0,
                               detail=None, delegated=False, style="merge", batch=None, dry_run=False)
        with self.assertRaisesRegex(task_lane.Refused, "critical"):
            proof_train.cmd_train(args, self.root, task_lane)


if __name__ == "__main__":
    unittest.main()
