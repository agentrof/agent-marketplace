"""Impact-based local proof and the merge train: what a change must prove, and one proof for a batch of pull requests."""

from __future__ import annotations

import contextlib
import io
import json
import os
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

    def test_a_glob_matches_whatever_the_case_of_the_path(self):
        self.assertEqual(self.plan({"apps/a/AuthCard.tsx"})["commands"], ["echo full"])

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


class TrainAddAutoMergeTests(unittest.TestCase):
    def add(self, auto_merge):
        calls = []

        def run(*args, cwd=None):
            calls.append(args)
            return json.dumps({"autoMergeRequest": auto_merge}) if args[:3] == ("gh", "pr", "view") else "t7"

        lane = SimpleNamespace(Refused=task_lane.Refused, run=run, head_state=lambda root: ("abc", True),
                               proof_problems=lambda root, task: ([], {"selection": "full"}),
                               proof_plan=lambda root, base: {"selection": "full"})
        state = SimpleNamespace(data={"batches": []}, save=lambda: None)
        args = SimpleNamespace(task="TK-007", pr="7", base="main", critical=0, non_critical=0, filed=0, detail=None,
                               delegated=False, style="merge")
        self.assertTrue(proof_train.add(args, Path("."), lane, state)["ok"])
        return calls

    def test_joining_a_batch_turns_off_an_auto_merge_armed_earlier(self):
        self.assertIn(("gh", "pr", "merge", "7", "--disable-auto"), self.add({"enabledAt": "2026-01-01T00:00:00Z"}))

    def test_joining_a_batch_leaves_a_pull_request_without_auto_merge_alone(self):
        self.assertFalse([call for call in self.add(None) if "--disable-auto" in call])


class StaleGreenBatchTests(unittest.TestCase):
    def prove(self, origin_head):
        batch = {"id": 1, "status": proof_train.GREEN, "base_head": "proved-base",
                 "members": [{"task": "a", "branch": "a", "root": "."}]}
        state = SimpleNamespace(data={"batches": [batch]}, batch=lambda number, lane: batch,
                                file=Path("state.json"), close=lambda: None)
        lane = SimpleNamespace(Refused=task_lane.Refused, run=lambda *args, **options: origin_head)
        args = SimpleNamespace(batch=1, base="main", dry_run=True)
        with mock.patch.object(proof_train, "integrate", side_effect=RuntimeError("integration reached")):
            proof_train.prove(args, Path("."), lane, state)

    def test_a_green_batch_is_proved_again_only_when_its_base_moved(self):
        # merge refuses a green batch whose base moved, so prove must take it or the batch is stuck
        with self.assertRaisesRegex(RuntimeError, "integration reached"):
            self.prove("moved-base")
        with self.assertRaisesRegex(task_lane.Refused, "green; nothing to prove"):
            self.prove("proved-base")


@integration
class MergeTrainTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        identity = {f"GIT_{who}_{key}": value for who in ("AUTHOR", "COMMITTER") for key, value in (("NAME", "t"), ("EMAIL", "t@example.com"))}
        patcher = mock.patch.dict(os.environ, identity)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def member(self, number, bad=False, clash=False):
        self.git("checkout", "-q", "-b", f"t{number}", "main")
        (self.root / f"f{number}").write_text("x")
        if bad:
            (self.root / "bad").write_text("x")
        if clash:
            (self.root / "clash").write_text(str(number))
        self.git("add", "-A")
        self.git("commit", "-qm", f"t{number}")
        task_lane.cmd_prove(SimpleNamespace(task=f"TK-00{number}", base="main", deferred=True), self.root)
        real_run = task_lane.run

        def run(*args, cwd=None):
            return '{"autoMergeRequest": null}' if args[:3] == ("gh", "pr", "view") else real_run(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run):
            return proof_train.cmd_train(SimpleNamespace(verb="add", task=f"TK-00{number}", pr=str(number), base="main",
                                                         critical=0, non_critical=0, filed=0, detail=None,
                                                         delegated=False, style="merge"), self.root, task_lane)

    def train(self, verb, **options):
        args = SimpleNamespace(**{"verb": verb, "base": "main", "batch": None, "dry_run": False, **options})
        return proof_train.cmd_train(args, self.root, task_lane)

    def test_prove_reads_the_head_of_a_member_recorded_from_a_detached_worktree(self):
        self.member(1)
        tree = self.root.parent / "detached"
        self.git("worktree", "add", "-q", "--detach", str(tree), "t1")
        state_file = self.root / proof_train.STATE / "state.json"
        state = json.loads(state_file.read_text())
        state["batches"][0]["members"][0].update(root=str(tree), branch="HEAD")
        state_file.write_text(json.dumps(state))
        (tree / "extra").write_text("y")
        for args in (("add", "-A"), ("commit", "-qm", "more")):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=tree, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()
        self.git("checkout", "-q", "main")
        self.assertTrue(self.train("prove")["ok"])
        self.assertEqual(json.loads(state_file.read_text())["batches"][0]["members"][0]["head"], head)

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

    def test_a_member_that_does_not_merge_onto_the_batch_leaves_it_and_the_rest_is_proved(self):
        self.member(1, clash=True)
        self.member(2, clash=True)
        first, clashing, last = self.member(3)["members"]
        with self.assertRaisesRegex(task_lane.Refused, f"{clashing} does not merge onto the batch"):
            self.train("prove")
        with mock.patch.object(task_lane, "project_root", return_value=self.root), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(task_lane.main(["train", "remove", "--task", clashing]), 0)
        self.assertEqual(json.loads(out.getvalue())["members"], [first, last])
        self.assertTrue(self.train("prove")["ok"])

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

    def task_tree(self, task, proved):
        """A worktree on the task's branch, a commit ahead of main; ``proved`` records the deferred proof in it."""
        tree = self.root.parent / f"tree-{task.lower()}"
        self.git("worktree", "add", "-q", "-b", f"task/{task.lower()}-fix", str(tree), "main")
        (tree / "change").write_text("x")
        self.git("-C", str(tree), "add", "-A")
        self.git("-C", str(tree), "commit", "-qm", "change")
        if proved:
            task_lane.cmd_prove(SimpleNamespace(task=task, base="main", deferred=True), tree)
        return tree

    def add(self, task):
        """``train add`` run from the main checkout, which is on main and holds no proof record."""
        real_run = task_lane.run

        def run(*args, cwd=None):
            return '{"autoMergeRequest": null}' if args[:3] == ("gh", "pr", "view") else real_run(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run):
            return proof_train.cmd_train(SimpleNamespace(verb="add", task=task, pr="7", base="main", critical=0,
                                                         non_critical=0, filed=0, detail=None, delegated=False,
                                                         style="merge"), self.root, task_lane)

    def test_add_finds_the_proof_of_the_pull_requests_own_worktree_from_any_directory(self):
        proved = task_lane.next_id([])
        unproved = task_lane.next_id([f"task/{proved.lower()}-fix"])
        tree = self.task_tree(proved, proved=True)
        self.task_tree(unproved, proved=False)
        with self.subTest("the proof record sits in the pull request's worktree"):
            self.assertEqual(self.add(proved)["members"], [proved])
            member = json.loads((self.root / proof_train.STATE / "state.json").read_text())["batches"][0]["members"][0]
            self.assertEqual((Path(member["root"]).resolve(), member["branch"], member["head"]),
                             (tree.resolve(), f"task/{proved.lower()}-fix", self.git("-C", str(tree), "rev-parse", "HEAD")))
        with self.subTest("no proof record anywhere"):
            with self.assertRaisesRegex(task_lane.Refused, "no local proof record"):
                self.add(unproved)


class MergeDryRunTests(unittest.TestCase):
    def test_a_dry_run_merge_checks_the_batch_and_merges_nothing(self):
        batch = {"id": 1, "status": proof_train.GREEN, "base_head": "proved-base", "head": "h",
                 "members": [{"task": "a"}, {"task": "b"}]}
        state = SimpleNamespace(batch=lambda number, lane: batch, save=mock.Mock())
        lane = SimpleNamespace(Refused=task_lane.Refused, run=lambda *args, **options: "proved-base", cmd_merge=mock.Mock())
        result = proof_train.merge(SimpleNamespace(batch=1, base="main", dry_run=True), Path("."), lane, state)
        self.assertEqual(result["would_merge"], ["a", "b"])
        lane.cmd_merge.assert_not_called()
        state.save.assert_not_called()
        self.assertEqual(batch["status"], proof_train.GREEN)


class TrainRemoveTests(unittest.TestCase):
    def state(self, *batches):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory))
        state = proof_train.State(directory)
        self.addCleanup(state.close)
        state.data["batches"] = [{"id": number, "status": status, "members": [{"task": task} for task in tasks], **proof}
                                 for number, (status, tasks, proof) in enumerate(batches, 1)]
        return state

    def remove(self, state, task, batch=None):
        return proof_train.remove(SimpleNamespace(task=task, batch=batch), None, task_lane, state)

    def test_a_member_leaves_an_open_or_red_batch_and_the_batch_reopens_without_the_old_proof(self):
        old_proof = {"head": "a1", "base_head": "b2", "commands": [{"command": "make test", "exit_code": 1}]}
        for status, proof in ((proof_train.OPEN, {}), (proof_train.RED, old_proof)):
            with self.subTest(status):
                state = self.state((status, ["task-a", "task-b", "task-c"], proof), (proof_train.OPEN, ["task-d"], {}))
                self.assertEqual(self.remove(state, "task-b", batch=1), {"ok": True, "batch": 1, "members": ["task-a", "task-c"]})
                self.assertEqual(json.loads(state.file.read_text())["batches"], [
                    {"id": 1, "status": proof_train.OPEN, "members": [{"task": "task-a"}, {"task": "task-c"}]},
                    {"id": 2, "status": proof_train.OPEN, "members": [{"task": "task-d"}]}])

    def test_a_task_outside_the_batch_and_a_sealed_green_or_merged_batch_refuse_to_give_up_a_member(self):
        cases = ((proof_train.RED, "task-x", "task-x is not in batch 1"),
                 (proof_train.SEALED, "task-a", "batch 1 is sealed; only an open or red batch gives up a member"),
                 (proof_train.GREEN, "task-a", "batch 1 is green; only an open or red batch gives up a member"),
                 (proof_train.MERGED, "task-a", "batch 1 is merged; only an open or red batch gives up a member"),
                 (proof_train.OPEN, None, "train remove needs --task"))
        for status, task, message in cases:
            with self.subTest(status=status, task=task):
                state = self.state((status, ["task-a", "task-b"], {}))
                before = json.dumps(state.data)
                with self.assertRaisesRegex(task_lane.Refused, message):
                    self.remove(state, task)
                self.assertEqual(json.dumps(state.data), before)


if __name__ == "__main__":
    unittest.main()
