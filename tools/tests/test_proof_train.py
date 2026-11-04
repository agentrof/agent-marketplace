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


class IntegrationReportingTests(unittest.TestCase):
    def test_merged_integration_links_source_issues_and_counts_shared_proof_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = root / task_lane.TASKS
            tasks.mkdir(parents=True)
            paths = []
            members = []
            for number in (1, 2):
                path = tasks / f"tk-00{number}-sample.md"
                path.write_text(f"---\ntype: task\nid: TK-00{number}\ntitle: Sample {number}\nsize: s\n"
                                "rigor: normal\nrequested_at: 2026-01-01T10:00:00Z\n"
                                "approved_at: 2026-01-01T10:01:00Z\n"
                                f"source_issue: {number + 10}\n---\n", encoding="utf-8")
                paths.append(path)
                members.append({"task": f"TK-00{number}", "pr": str(number), "head": str(number) * 40,
                                "reviewed_head": str(number) * 40})
            batch = {"id": 1, "head": "a" * 40, "base_head": "b" * 40, "members": members,
                     "commands": [{"command": "check", "exit_code": 0, "seconds": 120,
                                   "finished_at": "2026-01-01T10:07:00Z"}]}
            body = proof_train.integration_body(batch, root, task_lane)
            self.assertIn("Closes #11.", body)
            self.assertIn("Closes #12.", body)
            record = task_lane.integration_record(body)
            self.assertEqual(record["members"][0]["head"], "1" * 40)
            self.assertEqual(task_lane.local_proof(body)["minutes"], 2.0)
            member_prs = [{"number": number, "headRefName": f"task/tk-00{number}-sample",
                           "headRefOid": str(number) * 40, "state": "CLOSED", "mergedAt": None,
                           "createdAt": "2026-01-01T10:03:00Z", "body": "", "comments": [],
                           "commits": [], "statusCheckRollup": []} for number in (1, 2)]
            aggregate = {"number": 3, "headRefName": "train/1", "headRefOid": "a" * 40,
                         "state": "MERGED", "createdAt": "2026-01-01T10:08:00Z",
                         "mergedAt": "2026-01-01T10:10:00Z", "body": body, "commits": [],
                         "statusCheckRollup": [], "comments": [{"body": task_lane.REVIEW_MARK,
                                                                  "createdAt": "2026-01-01T10:09:00Z"}]}
            listed = [aggregate, *member_prs]
            views = []

            def run(*args, cwd=None):
                if args[:3] == ("gh", "pr", "list"):
                    return json.dumps(listed)
                if args[:3] == ("gh", "pr", "view"):
                    views.append(args[3])
                    return json.dumps(next(pr for pr in listed if str(pr["number"]) == args[3]))
                raise AssertionError(args)

            args = SimpleNamespace(since=None, recent=20, no_sessions=True)
            with mock.patch.object(task_lane, "run", run), \
                    mock.patch.object(task_lane, "ci_runs", return_value=[]), \
                    mock.patch.object(task_lane, "task_records", return_value=paths), \
                    mock.patch.object(task_lane, "attach_readiness"), \
                    mock.patch.object(task_lane, "cost_method", return_value=None):
                for invalid in ({"state": "OPEN"}, {"headRefOid": "c" * 40},
                                {"headRefName": "train/2"}):
                    with self.subTest(invalid=invalid):
                        listed[0] = {**aggregate, **invalid}
                        self.assertNotIn("integration", task_lane.pull_requests(root)["TK-001"])
                listed[0] = aggregate
                data = task_lane.gather(root, args)
                self.assertEqual(views.count("3"), 1)
                self.assertEqual(data["merged"], 2)
                self.assertEqual(len(data["shared_integrations"]), 1)
                self.assertEqual(data["shared_integrations"][0]["proof_minutes"], 2.0)
                for number, row in enumerate(data["per_task"], 1):
                    self.assertEqual((row["pr"], row["member_pr"], row["member_head"]),
                                     (str(number), str(number), str(number) * 40))
                    self.assertEqual((row["integration_pr"], row["integration_head"]), ("3", "a" * 40))
                    self.assertEqual((row["merged"], row["total"]), (aggregate["mergedAt"], 10.0))
                    self.assertEqual(row["timing_scope"], "request_to_shared_integration")
                    self.assertEqual(row["checks"], {})
                    self.assertNotIn("proof_minutes", row)
                rendered = task_lane.render(data)
                self.assertEqual(rendered.count("local proof 2.0 min"), 1)
                self.assertEqual(rendered.count("Delivered by integration PR 3"), 2)
                self.assertIn("integration_pr,integration_head,timing_scope", task_lane.export(data, "csv"))
                self.assertFalse((root / proof_train.STATE).exists())


class TrainAddDryRunTests(unittest.TestCase):
    TASK = task_lane.next_id([])

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.directory))
        self.calls = []
        native = mock.patch.object(task_lane, "review_problems", return_value=[])
        native.start()
        self.addCleanup(native.stop)

    def add(self, **options):
        """``train add`` for one pull request against the train state kept in the temporary directory."""

        def run(*args, cwd=None):
            self.calls.append(args)
            armed = json.dumps({"state": "OPEN", "headRefOid": "abc",
                                "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"}})
            return armed if args[:3] == ("gh", "pr", "view") else "task/sample"

        lane = SimpleNamespace(Refused=task_lane.Refused, run=run, head_state=lambda root: ("abc", True),
                               task_path=lambda root, task: root / task, task_props=lambda path: {},
                               changed_files=lambda root, base: set(),
                               task_body=lambda path: "",
                               required_methods=lambda root, props, files, text: [],
                               missing_methods=lambda root, task, methods: [],
                               review_problems=task_lane.review_problems,
                               # Companion enforcement has dedicated receipt tests.
                               companion_evidence=lambda *args, **kwargs: [],
                               proof_problems=lambda root, task: ([], {"selection": "full"}),
                               proof_plan=lambda root, base: {"selection": "full"})
        args = SimpleNamespace(**{"task": self.TASK, "pr": "7", "base": "main", "critical": 0,
                                  "non_critical": 0, "filed": 0, "detail": None,
                                  "delegated": False, "style": "merge", **options})
        state = proof_train.State(self.directory)
        try:
            return proof_train.add(args, Path("."), lane, state)
        finally:
            state.close()

    def saved(self):
        path = self.directory / "state.json"
        batches = json.loads(path.read_text())["batches"] if path.is_file() else []
        return [(batch["id"], batch["status"], [member["task"] for member in batch["members"]]) for batch in batches]

    def test_a_dry_run_add_reports_the_batch_it_would_join_and_changes_nothing(self):
        planned = self.add(dry_run=True)
        self.assertEqual(self.saved(), [])
        self.assertEqual((planned.get("dry_run"), planned["batch"], planned["members"]), (True, 1, [self.TASK]))
        self.assertEqual([call for call in self.calls if call[:3] == ("gh", "pr", "merge")], [])
        self.assertEqual(self.add()["members"], [self.TASK])

    def test_a_dry_run_add_is_refused_where_the_real_add_is_refused(self):
        self.add()
        with self.assertRaisesRegex(task_lane.Refused, "already in a batch"):
            self.add(dry_run=True)

    def test_add_without_dry_run_still_joins_the_batch(self):
        self.add()
        self.assertEqual(self.saved(), [(1, proof_train.OPEN, [self.TASK])])

    def test_non_deferred_admission_requires_native_review(self):
        with mock.patch.object(task_lane, "review_problems", return_value=["native review missing"]), \
                self.assertRaisesRegex(task_lane.Refused, "native review missing"):
            self.add(dry_run=True)
        self.assertFalse(any(call[:3] == ("gh", "pr", "merge") for call in self.calls))


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
            return json.dumps({"state": "OPEN", "headRefOid": "abc", "autoMergeRequest": auto_merge}) if args[:3] == ("gh", "pr", "view") else "t7"

        lane = SimpleNamespace(Refused=task_lane.Refused, run=run, head_state=lambda root: ("abc", True),
                               task_path=lambda root, task: root / task, task_props=lambda path: {},
                               changed_files=lambda root, base: set(),
                               task_body=lambda path: "",
                               required_methods=lambda root, props, files, text: [],
                               missing_methods=lambda root, task, methods: [],
                               review_problems=lambda *args: [],
                               # Companion enforcement has dedicated receipt tests.
                               companion_evidence=lambda *args, **kwargs: [],
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


class TrainReviewTests(unittest.TestCase):
    def test_explicit_blocking_findings_cannot_join(self):
        state = SimpleNamespace(data={"batches": []})
        for count in (-1, 1):
            with self.subTest(critical=count), self.assertRaisesRegex(task_lane.Refused, "critical"):
                proof_train.add(SimpleNamespace(task="TK-001", pr="1", critical=count),
                                Path("."), task_lane, state)

    def test_a_legacy_batch_without_head_bound_review_is_rejected(self):
        member = {"task": "TK-001", "head": "abc", "merge": {"critical": 0}}
        with self.assertRaisesRegex(task_lane.Refused, "lacks a review bound"):
            proof_train.check_reviewed_heads({"members": [member]}, task_lane)


class BisectEvidenceTests(unittest.TestCase):
    def test_nonmonotone_prefixes_produce_observations_and_a_qualified_suspect(self):
        batch = {"id": 1, "status": proof_train.RED, "base": "main", "base_head": "base",
                 "head": "combined", "members": [{"task": str(n)} for n in range(1, 6)],
                 "commands": [{"command": "check", "exit_code": 1}]}
        state = SimpleNamespace(batch=lambda number, lane: batch)
        lane = SimpleNamespace(Refused=task_lane.Refused, run=lambda *args, **kwargs: "/tmp/git",
                               head_state=lambda tree: (str(tree), True))
        tested = []

        def integrate(root, lane, batch, members, base, suffix, base_head):
            tested.append(len(members))
            return Path(str(len(members))), base_head

        def checks(root, lane, tree, base):
            return [{"command": "check", "exit_code": 0 if int(str(tree)) % 2 == 0 else 1}]

        args = SimpleNamespace(batch=1, base="main", dry_run=False)
        with mock.patch.object(proof_train, "check_reviewed_heads"), \
                mock.patch.object(proof_train, "integrate", integrate), \
                mock.patch.object(proof_train, "run_commands", checks), \
                mock.patch.object(proof_train, "drop"):
            result = proof_train.bisect(args, Path("."), lane, state)
        self.assertEqual(tested, [3, 2])
        self.assertEqual(result["suspect"], "3")
        self.assertEqual([row["prefix_length"] for row in result["observations"]], [5, 3, 2])
        self.assertNotIn("culprit", result)
        self.assertIn("Earlier failures", result["note"])
        batch["status"] = proof_train.GREEN
        with self.assertRaisesRegex(task_lane.Refused, "only a red batch"):
            proof_train.bisect(args, Path("."), lane, state)


@integration
class MergeTrainTests(unittest.TestCase):
    def setUp(self):
        # test_companion_receipts covers add/candidate gates; this fixture isolates Git integration.
        companions = mock.patch.object(task_lane, "companion_evidence", return_value=[])
        companions.start()
        self.addCleanup(companions.stop)
        # Native review integrity is tested separately from Git integration.
        review = mock.patch.object(task_lane, "review_problems", return_value=[])
        review.start()
        self.addCleanup(review.stop)
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

    def member(self, number, bad=False, clash=False, issue=None):
        self.git("checkout", "-q", "-b", f"t{number}", "main")
        (self.root / f"f{number}").write_text("x")
        task = self.root / task_lane.TASKS / f"tk-00{number}-sample.md"
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text(f"---\ntype: task\nid: TK-00{number}\nrigor: normal\n"
                        + (f"source_issue: {issue}\n" if issue else "") + "---\n")
        if bad:
            (self.root / "bad").write_text("x")
        if clash:
            (self.root / "clash").write_text(str(number))
        self.git("add", "-A")
        self.git("commit", "-qm", f"t{number}")
        task_lane.cmd_prove(SimpleNamespace(task=f"TK-00{number}", base="main", deferred=True), self.root)
        real_run = task_lane.run

        def run(*args, cwd=None):
            return json.dumps({"state": "OPEN", "headRefOid": real_run("git", "rev-parse", "HEAD", cwd=cwd),
                               "autoMergeRequest": None}) if args[:3] == ("gh", "pr", "view") else real_run(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run):
            return proof_train.cmd_train(SimpleNamespace(verb="add", task=f"TK-00{number}", pr=str(number), base="main",
                                                         critical=0, non_critical=0, filed=0, detail=None,
                                                         delegated=False, style="merge"), self.root, task_lane)

    def train(self, verb, **options):
        args = SimpleNamespace(**{"verb": verb, "base": "main", "batch": None, "dry_run": False,
                                  "style": "merge", "delegated": False, "detail": None, **options})
        return proof_train.cmd_train(args, self.root, task_lane)

    def test_deferred_members_wait_for_one_aggregate_native_review(self):
        with mock.patch.object(task_lane, "review_problems", return_value=["aggregate review missing"]) as review:
            self.member(1)
            review.assert_not_called()
            self.assertTrue(self.train("prove")["ok"])
            with self.assertRaisesRegex(task_lane.Refused, "aggregate review missing"):
                self.train("publish")
            self.assertEqual(review.call_count, 1)
            self.assertEqual(review.call_args.args[1][0]["task"], "TK-001")

    def test_unchanged_train_reuses_proof_and_failed_retries_remain_invalid(self):
        identity = self.root.parent / "environment.txt"
        counter = self.root.parent / "proof-runs.txt"
        identity.write_text("one", encoding="utf-8")
        (self.root / "proof.py").write_text(
            "import pathlib, sys\n"
            "with pathlib.Path(sys.argv[1]).open('a') as stream:\n"
            "    stream.write('run\\n')\n"
            "sys.exit(1 if pathlib.Path(sys.argv[2]).read_text() == 'fail' else 0)\n",
            encoding="utf-8")
        command = f'"{sys.executable}" proof.py "{counter}" "{identity}"'
        self.config({"local_proof": [command], "proof_reuse": {"identity_command": [
            sys.executable, "-c", "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())", str(identity)]}})
        self.git("add", "proof.py", "workspace/config.json")
        self.git("commit", "-qm", "configure proof identity")
        self.git("push", "-q", "origin", "main")
        self.member(1)
        first = self.train("prove")
        self.assertTrue(first["ok"])
        self.assertFalse(first["reused"])
        directory = self.root / proof_train.STATE
        receipt = directory / "batch-1.proof.json"
        original = receipt.read_bytes()
        self.assertIsNotNone(json.loads(original)["proof_inputs"])

        # Environment-only changes must preserve the already reviewed Git candidate.
        with mock.patch.object(proof_train, "integrate", side_effect=AssertionError("candidate rebuilt")):
            hit = self.train("prove")
            self.assertTrue(hit["reused"])
            self.assertEqual(hit["integration_head"], first["integration_head"])
            self.assertEqual(hit["finished_at"], first["finished_at"])
            self.assertEqual(receipt.read_bytes(), original)
            self.assertEqual(counter.read_text().splitlines(), ["run"])

            identity.write_text("two", encoding="utf-8")
            with self.assertRaisesRegex(task_lane.Refused, "proof inputs changed"):
                self.train("publish")
            fresh = self.train("prove")
            self.assertFalse(fresh["reused"])
            self.assertEqual(fresh["integration_head"], first["integration_head"])
            self.assertEqual(len(counter.read_text().splitlines()), 2)

            identity.write_text("three", encoding="utf-8")
            with mock.patch.object(task_lane, "run_proof_commands", side_effect=KeyboardInterrupt), \
                    self.assertRaises(KeyboardInterrupt):
                self.train("prove")
            batch = json.loads((directory / "state.json").read_text())["batches"][0]
            self.assertEqual(batch["status"], proof_train.RED)
            self.assertEqual(json.loads(receipt.read_text())["proof_state"], "failed")
            identity.write_text("two", encoding="utf-8")
            self.assertFalse(self.train("prove")["reused"])
            self.assertEqual(len(counter.read_text().splitlines()), 3)

            identity.write_text("fail", encoding="utf-8")
            self.assertFalse(self.train("prove")["ok"])
            self.assertEqual(json.loads((directory / "state.json").read_text())["batches"][0]["status"], proof_train.RED)
            identity.write_text("two", encoding="utf-8")
            self.assertFalse(self.train("prove")["reused"])
            self.assertEqual(len(counter.read_text().splitlines()), 5)

            legacy = json.loads(receipt.read_text())
            legacy.pop("proof_inputs")
            receipt.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertFalse(self.train("prove")["reused"])
            self.assertEqual(len(counter.read_text().splitlines()), 6)

    def test_prove_rejects_a_new_commit_in_a_detached_member_worktree(self):
        self.member(1)
        tree = self.root.parent / "detached"
        self.git("worktree", "add", "-q", "--detach", str(tree), "t1")
        state_file = self.root / proof_train.STATE / "state.json"
        state = json.loads(state_file.read_text())
        member = state["batches"][0]["members"][0]
        original = member["head"]
        self.assertIsNone(member["reviewed_head"])
        self.assertEqual(member["review_status"], "pending_aggregate")
        member.update(root=str(tree), branch="HEAD")
        state_file.write_text(json.dumps(state))
        (tree / "extra").write_text("y")
        self.git("-C", str(tree), "add", "-A")
        self.git("-C", str(tree), "commit", "-qm", "more")
        with self.assertRaisesRegex(task_lane.Refused, "changed after review"):
            self.train("prove")
        member = json.loads(state_file.read_text())["batches"][0]["members"][0]
        self.assertEqual((member["head"], member["reviewed_head"]), (original, None))

    def test_a_green_combined_candidate_never_merges_its_unproved_prefix(self):
        self.config({"pr_proof": "local_record", "local_proof": ["test ! -e bad || test -e fix"]})
        self.git("add", "workspace/config.json")
        self.git("commit", "-qm", "select proof")
        self.git("push", "-q", "origin", "main")
        self.member(1, bad=True, issue=11)
        self.git("checkout", "-q", "main")
        (self.root / "fix").write_text("repair")
        self.member(2, issue=12)
        self.assertEqual(self.train("status")["batches"][0]["members"], ["TK-001", "TK-002"])
        result = self.train("prove")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["commands"]), 1)
        self.git("checkout", "-q", "t1")
        self.assertNotEqual(subprocess.run(["sh", "-c", "test ! -e bad || test -e fix"], cwd=self.root).returncode, 0)
        record = json.loads(task_lane.proof_path(self.root, "TK-001").read_text())
        self.assertEqual(record["selection"], "deferred")
        state_file = self.root / proof_train.STATE / "state.json"
        before = state_file.read_bytes()
        with mock.patch.object(task_lane, "cmd_merge") as merge:
            with self.assertRaisesRegex(task_lane.Refused, "requires --head"):
                self.train("merge")
        merge.assert_not_called()
        self.assertEqual(state_file.read_bytes(), before)

        real_run = task_lane.run
        created = False
        calls = []
        aggregate = {"number": 99, "url": "https://example.invalid/pull/99", "state": "OPEN",
                     "headRefOid": result["integration_head"], "headRefName": "train/1",
                     "baseRefName": "main", "body": "", "autoMergeRequest": None}

        batch_members = json.loads(state_file.read_text())["batches"][0]["members"]
        member_views = {str(member["pr"]): {"headRefOid": member["head"], "state": "OPEN", "comments": []}
                        for member in batch_members}
        close_attempts = {"1": 0, "2": 0}

        def run(*args, **kwargs):
            nonlocal created
            if args[0] != "gh":
                return real_run(*args, **kwargs)
            calls.append(args)
            if args[:3] == ("gh", "pr", "list"):
                return json.dumps([aggregate] if created else [])
            if args[:3] == ("gh", "pr", "create"):
                created = True
                aggregate["body"] = Path(args[args.index("--body-file") + 1]).read_text()
                raise task_lane.Refused("response lost after creation")
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps(aggregate if args[3] == "99" else member_views[args[3]])
            if args[:3] == ("gh", "pr", "comment") and args[3] in member_views:
                member_views[args[3]]["comments"].append({"body": args[-1]})
            if args[:3] == ("gh", "pr", "close"):
                member_pr = args[3]
                close_attempts[member_pr] += 1
                if member_pr == "2" and close_attempts[member_pr] == 1:
                    raise task_lane.Refused("closure failed before changing state")
                member_views[member_pr]["state"] = "CLOSED"
                raise task_lane.Refused("response lost after closure")
            if args[:3] == ("gh", "pr", "merge"):
                self.assertEqual(args[3], "99")
                self.assertIn("--match-head-commit", args)
                self.assertEqual(args[-1], result["integration_head"])
                aggregate.update(state="MERGED", mergedAt="2026-01-01T00:00:00Z")
                raise task_lane.Refused("response lost after merge")
            return ""

        with mock.patch.object(task_lane, "run", run):
            published = self.train("publish")
            self.assertEqual(published["integration_pr"], 99)
            self.assertIn("Closes #11.", aggregate["body"])
            self.assertIn("Closes #12.", aggregate["body"])
            record = task_lane.integration_record(aggregate["body"])
            self.assertEqual(record["head"], result["integration_head"])
            self.assertEqual([member["head"] for member in record["members"]],
                             [member["head"] for member in batch_members])
            self.assertEqual(task_lane.pull_requests(self.root), {})
            self.assertEqual(self.train("publish")["integration_pr"], 99)
            self.assertEqual(sum(call[:3] == ("gh", "pr", "create") for call in calls), 1)
            for options, message in (({"pr": "1", "head": result["integration_head"], "critical": 0}, "published integration PR"),
                                     ({"pr": "99", "head": "f" * 40, "critical": 0}, "requires --head"),
                                     ({"pr": "99", "head": result["integration_head"]}, "critical 0")):
                with self.subTest(options=options), self.assertRaisesRegex(task_lane.Refused, message):
                    self.train("merge", **options)
            dry = self.train("merge", pr="99", head=result["integration_head"], critical=0, dry_run=True)
            self.assertTrue(dry["ok"])
            self.assertEqual(dry["merged"], [])
            self.assertFalse(any(call[:3] == ("gh", "pr", "merge") for call in calls))
            merged = self.train("merge", pr="99", head=result["integration_head"], critical=0)
            self.assertEqual(merged["merged"], ["TK-001", "TK-002"])
            linked = task_lane.pull_requests(self.root)
            self.assertEqual(linked["TK-001"]["number"], "1")
            self.assertEqual(linked["TK-001"]["integration"]["number"], 99)
            self.assertEqual(linked["TK-002"]["member_head"], batch_members[1]["head"])
            self.assertEqual(merged["member_prs_pending_reconciliation"], ["2"])
            self.assertEqual(merged["member_prs_reconciled"], ["1"])
            self.assertFalse(merged["ok"])
            self.assertEqual(self.train("status")["batches"][0]["status"], proof_train.MERGED)
            original_head = member_views["2"]["headRefOid"]
            member_views["2"]["headRefOid"] = "f" * 40
            changed = self.train("merge", pr="99", head=result["integration_head"], critical=0)
            self.assertEqual(changed["member_prs_pending_reconciliation"], ["2"])
            self.assertEqual(close_attempts["2"], 1)
            member_views["2"]["headRefOid"] = original_head
            finished = self.train("merge", pr="99", head=result["integration_head"], critical=0)
            self.assertTrue(finished["ok"])
            self.assertEqual(finished["member_prs_pending_reconciliation"], [])
            self.assertEqual(finished["retro_job"], merged["retro_job"])
            self.assertEqual(close_attempts, {"1": 1, "2": 2})
            self.assertTrue(all(len(member["comments"]) == 1 for member in member_views.values()))
            self.assertTrue(all("https://example.invalid/pull/99" in member["comments"][0]["body"]
                                for member in member_views.values()))
            self.assertEqual(sum(call[:3] == ("gh", "pr", "merge") for call in calls), 1)

    def test_unknown_publication_is_held_until_a_pr_is_observed(self):
        self.member(1)
        self.train("prove")
        real_run = task_lane.run
        created = []

        def run(*args, **kwargs):
            if args[:3] == ("gh", "pr", "list"):
                return "[]"
            if args[:3] == ("gh", "pr", "create"):
                created.append(args)
                raise task_lane.Refused("creation outcome unknown")
            return real_run(*args, **kwargs)

        with mock.patch.object(task_lane, "run", run):
            self.assertTrue(self.train("publish")["publication_unknown"])
            self.assertTrue(self.train("publish")["publication_unknown"])
        self.assertEqual(len(created), 1)
        with self.assertRaisesRegex(task_lane.Refused, "pending integration publication"):
            self.train("prove")
        with self.assertRaisesRegex(task_lane.Refused, "pending integration publication"):
            self.train("remove", task="TK-001")

    def test_aggregate_merge_keeps_high_risk_and_ci_gates(self):
        self.member(1)
        proved = self.train("prove")
        state = proof_train.State(proof_train.home(self.root, task_lane))
        state.data["batches"][0]["integration_pr"] = 99
        state.save()
        state.close()
        real_run = task_lane.run
        remote = {"state": "OPEN", "headRefOid": proved["integration_head"],
                  "headRefName": "train/1", "baseRefName": "main"}
        mutations = []

        def run(*args, **kwargs):
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps(remote)
            if args[:3] == ("gh", "pr", "checks"):
                raise task_lane.Refused("required CI failed")
            if args[:3] in (("gh", "pr", "merge"), ("gh", "pr", "comment")):
                mutations.append(args)
                return ""
            return real_run(*args, **kwargs)

        with mock.patch.object(task_lane, "run", run):
            with mock.patch.object(task_lane, "task_props", return_value={"rigor": "high"}):
                with self.assertRaisesRegex(task_lane.Refused, "explicit delegation"):
                    self.train("merge", pr="99", head=proved["integration_head"], critical=0)
                with self.assertRaisesRegex(task_lane.Refused, "required CI failed"):
                    self.train("merge", pr="99", head=proved["integration_head"], critical=0, delegated=True)
        self.assertEqual(mutations, [])
        self.assertEqual(self.train("status")["batches"][0]["status"], proof_train.GREEN)


    def test_merge_refuses_a_changed_member_after_a_green_proof(self):
        self.member(1)
        self.train("prove")
        (self.root / "extra").write_text("new commit")
        self.git("add", "extra")
        self.git("commit", "-qm", "after review")
        with self.assertRaisesRegex(task_lane.Refused, "changed after review"):
            self.train("publish")

    def test_merge_refuses_a_changed_integration_candidate_or_target_base(self):
        self.member(1)
        self.train("prove")
        tree = self.root / proof_train.STATE / "wt-1"
        (tree / "f1").write_text("changed")
        with self.assertRaisesRegex(task_lane.Refused, "candidate changed after proof"):
            self.train("publish")
        self.train("prove")
        self.git("checkout", "-q", "main")
        (self.root / "base-change").write_text("new base")
        self.git("add", "base-change")
        self.git("commit", "-qm", "base moved")
        self.git("push", "-q", "origin", "main")
        with self.assertRaisesRegex(task_lane.Refused, "moved since the batch was proved"):
            self.train("publish")
        self.assertTrue(self.train("prove")["ok"])

    def test_a_dry_run_prove_does_not_change_files_refs_or_worktrees(self):
        self.member(1)
        state_file = self.root / proof_train.STATE / "state.json"
        before = state_file.read_bytes()
        worktrees = self.git("worktree", "list", "--porcelain")
        refs = self.git("show-ref")
        with mock.patch.object(proof_train, "integrate") as integrate:
            result = self.train("prove", dry_run=True)
        integrate.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["would_run"], ["test ! -e bad"])
        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(self.git("worktree", "list", "--porcelain"), worktrees)
        self.assertEqual(self.git("show-ref"), refs)

    def test_a_pull_request_added_after_the_proof_started_waits_for_the_next_batch(self):
        self.member(1)
        self.train("prove")
        joined = self.member(2)
        self.assertEqual(joined["batch"], 2)

    def test_a_red_batch_does_not_merge_and_bisect_reports_a_supported_suspect(self):
        for number, bad in ((1, False), (2, True), (3, False)):
            self.member(number, bad)
        self.assertFalse(self.train("prove")["ok"])
        with self.assertRaisesRegex(task_lane.Refused, "red"):
            self.train("merge", batch=1)
        diagnosis = self.train("bisect", batch=1)
        self.assertEqual(diagnosis["suspect"], "TK-002")
        self.assertNotIn("culprit", diagnosis)
        self.assertTrue(diagnosis["observations"])
        self.assertIn("failure need not be monotone", diagnosis["note"])

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
        task_file = tree / task_lane.TASKS / f"{task.lower()}-fix.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(f"---\ntype: task\nid: {task}\nrigor: normal\n---\n")
        self.git("-C", str(tree), "add", "-A")
        self.git("-C", str(tree), "commit", "-qm", "change")
        if proved:
            task_lane.cmd_prove(SimpleNamespace(task=task, base="main", deferred=True), tree)
        return tree

    def add(self, task):
        """``train add`` run from the main checkout, which is on main and holds no proof record."""
        real_run = task_lane.run

        def run(*args, cwd=None):
            return json.dumps({"state": "OPEN", "headRefOid": real_run("git", "rev-parse", "HEAD", cwd=cwd),
                               "autoMergeRequest": None}) if args[:3] == ("gh", "pr", "view") else real_run(*args, cwd=cwd)

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


class TrainStateDryRunTests(unittest.TestCase):
    def test_a_read_only_state_does_not_create_its_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "absent"
            state = proof_train.State(directory, read_only=True)
            self.assertEqual(state.data, {"batches": []})
            with self.assertRaisesRegex(RuntimeError, "dry run"):
                state.save()
            state.close()
            self.assertFalse(directory.exists())


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
        old_proof = {"head": "a1", "base_head": "b2", "commands": [{"command": "make test", "exit_code": 1}],
                     "proof_scope": {"batch": 1}, "finished_at": "2026-01-01T00:00:00Z"}
        for status, proof in ((proof_train.OPEN, {}), (proof_train.RED, old_proof), (proof_train.GREEN, old_proof)):
            with self.subTest(status):
                state = self.state((status, ["task-a", "task-b", "task-c"], proof), (proof_train.OPEN, ["task-d"], {}))
                self.assertEqual(self.remove(state, "task-b", batch=1), {"ok": True, "batch": 1, "members": ["task-a", "task-c"]})
                self.assertEqual(json.loads(state.file.read_text())["batches"], [
                    {"id": 1, "status": proof_train.OPEN, "members": [{"task": "task-a"}, {"task": "task-c"}]},
                    {"id": 2, "status": proof_train.OPEN, "members": [{"task": "task-d"}]}])

    def test_a_task_outside_the_batch_and_a_sealed_or_merged_batch_refuse_to_give_up_a_member(self):
        cases = ((proof_train.RED, "task-x", "task-x is not in batch 1"),
                 (proof_train.SEALED, "task-a", "batch 1 is sealed; only an open, red or green batch gives up a member"),
                 (proof_train.MERGED, "task-a", "batch 1 is merged; only an open, red or green batch gives up a member"),
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
