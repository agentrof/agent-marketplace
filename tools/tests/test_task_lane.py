"""Fast lane: one task, one branch and one
pull request carry a change; the task holds the request and approval times,
GitHub the rest, and the retrospective derives steps and proposals from them."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "platforms/shared/software-engineering-team/overlay/scripts"))
sys.path.insert(0, str(ROOT / "tools"))
import task_lane  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402

try:
    from tools.tests.levels import integration
except ImportError:
    from levels import integration

TASK = """---
type: task
title: "Export invoices"
status: active
id: TK-004
requested_at: 2026-10-16T10:00:00+00:00
approved_at: 2026-10-16T10:03:00+00:00
size: s
rigor: normal
tags:
  - doc/task
  - status/active
aliases:
  - TK-004
---
# Export invoices

## Request

Export the month's invoices as CSV.

## Acceptance Criteria

1. The export lists every invoice of the month.
2. Each row carries the invoice number and total.
3. An empty month yields a header-only file.
4. Live wiring: the export button downloads the file from the running app.
5. Docs delta: none.

## Tests

- 1, 2, 3: tests/test_export.py
- 4, 5: manual check

<!-- sec: nav -->
## Navigation

- [[maps/backlog|Backlog]]
"""


def companion_fallbacks(root: Path) -> None:
    """Give task/proof tests an explicit synthetic-host fallback receipt."""
    path = root / task_lane.REVIEWS / "fixture-evidence.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Synthetic host. Core review and verification are exercised by this test.\n", encoding="utf-8")
    record = {"unavailable_reason": "Companions are not installed in this synthetic test host.",
              "availability_source": "worker_report", "completion_source": "worker_report",
              "recorded_at": "2026-11-04T10:00:00Z", "evidence_head": "a" * 40,
              "host_context": {"host": "codex", "source": "worker_report", "enabled": None},
              "evidence": path.relative_to(root).as_posix(),
              "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    task_lane.methods_path(root, "TK-004").write_text(json.dumps({
        f"companion/{name}": record for name in ("ponytail", "graphify", "impeccable", "security-audit")
    }), encoding="utf-8")


class TaskTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        path = directory / "tk-004-export-invoices.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_complete_task_passes(self):
        self.assertEqual(task_lane.lint(self.write(TASK)), [])

    def test_criteria_size_rigor_and_times_are_checked(self):
        bad = (re.sub(r"(?m)^[1-5]\. .*\n", "", TASK)
               .replace("size: s", "size: xl").replace("rigor: normal", "rigor: extreme")
               .replace("requested_at: 2026-10-16T10:00:00+00:00\n", ""))
        errors = task_lane.lint(self.write(bad))
        self.assertIn("Acceptance Criteria needs at least one numbered, verifiable criterion", errors)
        self.assertTrue(any(error.startswith("size") for error in errors))
        self.assertTrue(any(error.startswith("rigor") for error in errors))
        self.assertIn("requested_at is required", errors)

    def test_one_criterion_and_uncapped_criteria_need_no_live_or_docs_boilerplate(self):
        compact = re.sub(r"(?s)## Acceptance Criteria.*?(?=<!-- sec: nav -->)",
                         "## Acceptance Criteria\n\n1. The exported total is exact.\n\n"
                         "## Tests\n\n- 1: tests/test_export.py\n\n", TASK)
        self.assertNotIn("Live wiring", compact)
        self.assertNotIn("Docs delta", compact)
        self.assertEqual(task_lane.lint(self.write(compact)), [])
        many = compact.replace("1. The exported total is exact.\n",
                               "".join(f"{n}. Property {n} holds.\n" for n in range(1, 31)))
        many = many.replace("- 1: tests/test_export.py", "- " + ", ".join(map(str, range(1, 31)))
                            + ": tests/test_export.py")
        self.assertEqual(task_lane.lint(self.write(many)), [])
        duplicate = compact.replace("1. The exported total is exact.",
                                    "1. The exported total is exact.\n1. The header appears once.")
        self.assertIn("Acceptance Criteria numbers must be unique", task_lane.lint(self.write(duplicate)))
        unmapped = compact.replace("- 1: tests/test_export.py", "- 2: tests/test_export.py")
        self.assertTrue(any("for criteria 1" in item for item in task_lane.lint(self.write(unmapped))))

    def test_the_skeleton_is_valid_once_filled(self):
        text = task_lane.task_text("TK-007", "Rotate keys", "Rotate the signing keys.",
                                    "2026-10-16T10:00:00+00:00", "high")
        text = text.replace("1. \n", "1. One.\n").replace("- 1: \n", "- 1: tests/test_keys.py\n")
        path = self.write(text)
        self.assertEqual(task_lane.lint(path), [])
        props = task_lane.task_props(path)
        self.assertEqual(props["rigor"], "high")

    def test_set_field_adds_and_replaces_only_its_scalar(self):
        path = self.write(TASK.replace("approved_at: 2026-10-16T10:03:00+00:00\n", ""))
        task_lane.set_field(path, "approved_at", "2026-10-16T10:05:00+00:00")
        task_lane.set_field(path, "status", "delivered")
        props = task_lane.task_props(path)
        self.assertEqual(props["approved_at"], "2026-10-16T10:05:00+00:00")
        self.assertEqual(props["status"], "delivered")
        self.assertIn("status/delivered", props["tags"])
        self.assertEqual(task_lane.lint(path), [])

    def test_risky_requests_propose_high_rigor(self):
        cases = {"Delete stale tenant data": "high", "Rotate the API key secrets": "high",
                 "Add authentication to the export": "high", "Add a CSV export button": "normal",
                 "Show the approving authority": "normal"}
        for request, rigor in cases.items():
            self.assertEqual(task_lane.proposed_rigor(request)[0], rigor, request)

    def test_ids_follow_tasks_and_remote_task_branches(self):
        names = ["workspace/docs/backlog/tasks/tk-003-a.md", "origin/task/tk-011-b"]
        self.assertEqual(task_lane.next_id(names), "TK-012")
        self.assertEqual(task_lane.next_id([]), "TK-001")


def pr(opened, ci, review, merged, checks=None):
    return {"pr": "9", "pr_opened": opened, "ci_green": ci, "review": review,
            "merged": merged, "checks": checks or {}}


def tree_records(root, _snapshot):
    """Stand-in for task_lane.task_records in a unit test: the working tree's records, no Git."""
    return sorted((root / task_lane.TASKS).glob("tk-*.md"))


class RetrospectiveTests(unittest.TestCase):
    task = {"id": "TK-004", "title": "Export", "size": "s", "rigor": "normal",
            "requested_at": "2026-10-16T10:00:00+00:00",
            "approved_at": "2026-10-16T10:03:00+00:00"}

    def test_steps_split_the_request_to_merge_time(self):
        row = task_lane.task_row(self.task, pr("2026-10-16T10:10:00Z", "2026-10-16T10:15:00Z",
                                                 "2026-10-16T10:13:00Z", "2026-10-16T10:16:00Z"))
        self.assertEqual(row["steps"], {"define": 3.0, "write": 7.0, "ci": 5.0, "review": 3.0,
                                        "merge_wait": 1.0})
        self.assertEqual((row["total"], row["largest_step"]), (16.0, "write"))

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_an_open_task_has_no_total_and_a_proposal_names_it(self):
        merged = task_lane.task_row({**self.task, "id": "TK-005"}, pr("2026-10-16T10:10:00Z", None, None,
                                                                      "2026-10-16T10:20:00Z"))
        data = task_lane.report([task_lane.task_row(self.task, None), merged], "latest 20")
        self.assertIsNone(data["per_task"][0]["total"])
        self.assertEqual(data["request_to_merge_minutes"]["count"], 1)
        self.assertIn("RETRO-OPEN-TASKS", [item["id"] for item in data["proposals"]])

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_report_ranks_proposals_by_measured_minutes(self):
        rows = [task_lane.task_row({**self.task, "id": f"TK-00{n}"},
                                     pr("2026-10-16T10:05:00Z", f"2026-10-16T10:{20 + n}:00Z",
                                        "2026-10-16T10:08:00Z", f"2026-10-16T10:{21 + n}:00Z",
                                        {"tests": 14.0 + n, "vault gate": 1.5}))
                for n in range(1, 4)]
        data = task_lane.report(rows, "latest 20")
        self.assertEqual(data["request_to_merge_minutes"],
                         {"count": 3, "average": 23.0, "median": 23.0, "max": 24.0})
        ids = [item["id"] for item in data["proposals"]]
        self.assertEqual(ids[0], "RETRO-CI")
        self.assertIn("RETRO-CHECK-TESTS", ids)
        self.assertLessEqual(len(ids), 5)
        self.assertEqual(data["slowest"][0]["task_id"], "TK-003")
        self.assertEqual(task_lane.export(data, "csv").splitlines()[0].split(",")[:3],
                         ["task_id", "title", "size"])

    def test_scope_selects_a_window_or_the_latest(self):
        tasks = [{"id": f"TK-00{n}", "requested_at": f"2026-10-{10 + n}T10:00:00+00:00"}
                 for n in range(1, 6)]
        at = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.assertEqual([task["id"] for task in task_lane.select(tasks, since="3d", at=at)],
                         ["TK-004", "TK-005"])
        self.assertEqual([task["id"] for task in task_lane.select(tasks, recent=2)],
                         ["TK-004", "TK-005"])
        with self.assertRaises(task_lane.Refused):
            task_lane.select(tasks, since="week")

    def test_github_times_come_from_checks_and_the_review_comment(self):
        checks = [{"name": "tests", "conclusion": "SUCCESS", "startedAt": "2026-10-16T10:05:00Z",
                   "completedAt": "2026-10-16T10:11:00Z"},
                  {"name": "gate", "conclusion": "SUCCESS", "startedAt": "2026-10-16T10:05:00Z",
                   "completedAt": "2026-10-16T10:07:00Z"}]
        self.assertEqual(task_lane.ci_green_at(checks), "2026-10-16T10:11:00Z")
        self.assertIsNone(task_lane.ci_green_at(checks + [{"name": "x", "conclusion": "FAILURE"}]))
        self.assertEqual(task_lane.check_minutes(checks), {"tests": 6.0, "gate": 2.0})
        comments = [{"body": "looks fine", "createdAt": "2026-10-16T10:06:00Z"},
                    {"body": task_lane.REVIEW_MARK, "createdAt": "2026-10-16T10:09:00Z"}]
        self.assertEqual(task_lane.review_at(comments), "2026-10-16T10:09:00Z")


class GitHubCommandTests(unittest.TestCase):
    def setUp(self):
        # Native execution and receipt integrity have their own tests.
        review = mock.patch.object(task_lane, "review_problems", return_value=[])
        review.start()
        self.addCleanup(review.stop)

    def project(self, rigor: str) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        tasks = root / task_lane.TASKS
        tasks.mkdir(parents=True)
        (tasks / "tk-004-export-invoices.md").write_text(
            TASK.replace("rigor: normal", f"rigor: {rigor}"), encoding="utf-8")
        companion_fallbacks(root)
        return root

    head = "a" * 40

    def merge_args(self, **values):
        return SimpleNamespace(**{"task": "TK-004", "pr": "9", "style": "merge",
                                  "detail": None, "head": self.head, "critical": 0, **values})

    def remote(self, calls, **values):
        def run(*args, **kwargs):
            calls.append(args)
            if args == ("git", "rev-parse", "HEAD"):
                return self.head
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps({"headRefOid": self.head, "baseRefName": "main", "state": "OPEN",
                                   **values,
                                   **({"state": "MERGED", "mergedAt": "2026-11-04T10:00:00Z"}
                                      if "headRefOid,state,mergedAt" in args else {})})
            return ""
        return run

    def test_merge_requires_zero_blockers_and_the_reviewed_head(self):
        root = self.project("normal")
        with mock.patch.object(task_lane, "run") as run:
            for values in ({"critical": 1}, {"critical": None}, {"head": "short"}):
                with self.subTest(values=values), self.assertRaises(task_lane.Refused):
                    task_lane.cmd_merge(self.merge_args(**values), root)
            run.assert_not_called()
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls, headRefOid="b" * 40)):
            with self.assertRaisesRegex(task_lane.Refused, "changed after review"):
                task_lane.cmd_merge(self.merge_args(), root)
        self.assertFalse(any(call[:3] == ("gh", "pr", "merge") for call in calls))
        for candidate in (("b" * 40, True), (self.head, False)):
            calls = []
            with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                    mock.patch.object(task_lane, "head_state", return_value=candidate), \
                    self.assertRaisesRegex(task_lane.Refused, "clean reviewed candidate"):
                task_lane.cmd_merge(self.merge_args(), root)
            self.assertFalse(any(call[:3] in (("gh", "pr", "merge"), ("gh", "pr", "comment"))
                                 for call in calls))
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                mock.patch.object(task_lane, "changed_files", return_value={"db/migrations/0001_total.sql"}), \
                self.assertRaisesRegex(task_lane.Refused, "method evidence is missing.*data-design"):
            task_lane.cmd_merge(self.merge_args(), root)
        self.assertFalse(any(call[:3] in (("gh", "pr", "merge"), ("gh", "pr", "comment"))
                             for call in calls))

    def test_green_checks_merge_only_the_reviewed_head(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                mock.patch.object(task_lane, "refresh_graph"), \
                mock.patch.object(task_lane, "queue_retrospective", return_value={"retro_job": "queued"}):
            result = task_lane.cmd_merge(self.merge_args(), self.project("normal"))
        self.assertTrue(result["merged"])
        self.assertEqual(result["retro_job"], "queued")
        self.assertFalse(result["auto_merge"])
        self.assertIn(("gh", "pr", "checks", "9", "--required"), calls)
        self.assertIn(("gh", "pr", "merge", "9", "--merge", "--match-head-commit", self.head), calls)
        self.assertFalse(any("--auto" in call for call in calls))

    def test_a_caller_zero_cannot_replace_native_review(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                mock.patch.object(task_lane, "review_problems", return_value=["native review missing"]), \
                self.assertRaisesRegex(task_lane.Refused, "native review missing"):
            task_lane.cmd_merge(self.merge_args(critical=0), self.project("normal"))
        self.assertFalse(any(call[:3] in (("gh", "pr", "merge"), ("gh", "pr", "comment")) for call in calls))

    def test_failed_checks_never_publish_a_pass_or_merge(self):
        calls = []
        remote = self.remote(calls)
        def run(*args, **kwargs):
            if args[:3] == ("gh", "pr", "checks"):
                raise task_lane.Refused("required check failed")
            return remote(*args, **kwargs)
        with mock.patch.object(task_lane, "run", side_effect=run):
            with self.assertRaisesRegex(task_lane.Refused, "check failed"):
                task_lane.cmd_merge(self.merge_args(), self.project("normal"))
        self.assertFalse(any(call[:3] in (("gh", "pr", "merge"), ("gh", "pr", "comment")) for call in calls))

    def test_high_rigor_leaves_the_merge_to_the_owner(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)):
            result = task_lane.cmd_merge(self.merge_args(), self.project("high"))
        self.assertFalse(result["auto_merge"])
        self.assertFalse(any(call[:3] == ("gh", "pr", "merge") for call in calls))

    def test_high_rigor_merges_only_under_delegation(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                mock.patch.object(task_lane, "refresh_graph"), \
                mock.patch.object(task_lane, "queue_retrospective", return_value={"retro_job": "queued"}):
            result = task_lane.cmd_merge(self.merge_args(delegated=True), self.project("high"))
        self.assertTrue(result["merged"])

    def test_confirmed_risk_requires_delegation_even_with_normal_rigor(self):
        root = self.project("normal")
        path = task_lane.task_path(root, "TK-004")
        text = path.read_text(encoding="utf-8").replace("rigor: normal", "rigor: normal\nrisks: auth")
        path.write_text(text, encoding="utf-8")
        for delegated in (False, True):
            calls = []
            with self.subTest(delegated=delegated), \
                    mock.patch.object(task_lane, "run", side_effect=self.remote(calls)), \
                    mock.patch.object(task_lane, "refresh_graph"), \
                    mock.patch.object(task_lane, "queue_retrospective", return_value={}):
                result = task_lane.cmd_merge(self.merge_args(delegated=delegated), root)
                self.assertEqual(result.get("merged", False), delegated)
                self.assertFalse(result["auto_merge"])
                self.assertEqual(any(call[:3] == ("gh", "pr", "merge") for call in calls), delegated)
                self.assertEqual(any(call[:3] == ("gh", "pr", "comment") for call in calls), delegated)
                self.assertEqual(any(call[:3] == ("gh", "pr", "checks") for call in calls), delegated)
                self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_graph_refresh_gets_the_project_root_and_merged_range(self):
        import graphify_refresh
        script = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/graphify_refresh.py"
        view = json.dumps({"baseRefName": "main", "headRefOid": self.head})
        with mock.patch.object(task_lane, "GRAPH_REFRESH", script), \
                mock.patch.object(task_lane.shutil, "which", return_value="/bin/graphify"), \
                mock.patch.object(task_lane, "run", return_value=view), \
                mock.patch.object(task_lane.subprocess, "Popen") as popen:
            task_lane.refresh_graph(self.project("normal"), "9")
        argv = popen.call_args.args[0][2:]
        self.assertEqual(argv[argv.index("--range") + 1], "origin/main..." + self.head)
        with mock.patch.object(graphify_refresh.subprocess, "Popen") as child:
            self.assertEqual(graphify_refresh.main(argv), 0)
        self.assertIn("--wait", child.call_args.args[0])


class MethodRoutingTests(unittest.TestCase):
    def test_invalid_issue_refuses_before_task_or_worktree_mutation(self):
        with mock.patch.object(task_lane, "run") as run:
            with self.assertRaisesRegex(task_lane.Refused, "positive project issue"):
                task_lane._start_task(SimpleNamespace(issue=0), Path("unused"))
            run.assert_not_called()

    def test_retro_git_failure_does_not_undo_delivery(self):
        import retrospective
        with mock.patch.object(retrospective, "emit", side_effect=subprocess.CalledProcessError(1, "git")):
            result = task_lane.queue_retrospective(Path("unused"))
        self.assertIn("retro_queue_error", result)

    def test_failed_commands_emit_local_observations_without_changing_exit_status(self):
        root = Path("/synthetic-project")
        for failure in (task_lane.Refused("private command detail"),
                        {"ok": False, "head": "a" * 40, "record": "proof.json"}):
            options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            output = io.StringIO()
            with mock.patch.object(task_lane, "project_root", return_value=root), \
                    mock.patch.object(task_lane, "cmd_prove", **options), \
                    mock.patch.object(task_lane, "queue_retrospective", return_value={"retro_job": "observed"}) as queued, \
                    contextlib.redirect_stdout(output):
                code = task_lane.main(["prove", "--task", "TK-004"])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output.getvalue())["ok"])
            queued.assert_called_once()
            project, event = queued.call_args.args
            self.assertEqual(project, root)
            self.assertEqual((event["kind"], event["operation"], event["task"]),
                             ("failure", "prove", "TK-004"))
            self.assertNotIn("private command detail", json.dumps(event))
            self.assertGreaterEqual(event["seconds"], 0)
            if not isinstance(failure, Exception):
                self.assertEqual(event["receipt_path"], str(root / "proof.json"))

    def project(self, task: str = TASK) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(task, encoding="utf-8")
        companion_fallbacks(root)
        return root

    def test_task_lookup_ignores_evidence_sidecars_and_preserves_task_lint(self):
        root = self.project()
        record = task_lane.task_path(root, "TK-004")
        (record.parent / "tk-004-evidence.md").write_text("# Proof\nObserved the expected result.\n", encoding="utf-8")
        (record.parent / "tk-004-review.md").write_text(
            "---\ntype: evidence\nid: EV-004\n---\n# Review\n", encoding="utf-8")
        self.assertEqual(task_lane.task_path(root, "TK-004"), record)
        self.assertEqual(task_lane.task_path(root, "tk-004"), record)
        loaded = task_lane.cmd_method(SimpleNamespace(task="TK-004", name="data-design", evidence=None), root)
        self.assertEqual(loaded["task_id"], "TK-004")
        self.assertEqual(task_lane.cmd_approve(SimpleNamespace(task="TK-004"), root)["task_id"], "TK-004")
        record.write_text(TASK.replace('title: "Export invoices"\n', ''), encoding="utf-8")
        self.assertEqual(task_lane.task_path(root, "TK-004"), record)
        self.assertIn("title is required", task_lane.cmd_lint(SimpleNamespace(file=None, task="TK-004"), root)["errors"])

    def test_task_lookup_refuses_duplicate_malformed_or_contradictory_task_identity(self):
        for text in (TASK, TASK.replace("id: TK-004", "id: TK-005"),
                     TASK.replace("type: task\n", ""), TASK.replace("id: TK-004\n", ""),
                     TASK.replace("type: task", "type: evidence"),
                     "---\ntype: task\nid: TK-004\n", "---\ntype: task\nbroken line\n---\n"):
            with self.subTest(metadata=text.split("---", 2)[1]):
                root = self.project()
                (root / task_lane.TASKS / "tk-004-other.md").write_text(text, encoding="utf-8")
                with self.assertRaises(task_lane.Refused):
                    task_lane.task_path(root, "TK-004")

    def test_evidence_without_a_task_record_cannot_become_the_task(self):
        root = self.project()
        record = task_lane.task_path(root, "TK-004")
        record.unlink()
        (record.parent / "tk-004-evidence.md").write_text("# Proof\n", encoding="utf-8")
        with self.assertRaisesRegex(task_lane.Refused, "no single task"):
            task_lane.cmd_method(SimpleNamespace(task="TK-004", name="data-design", evidence=None), root)

    def test_server_rendered_ui_effects_require_experience_design_and_impeccable(self):
        root = self.project()
        rules = task_lane.task_triggers(root)["method_skills"]
        props = task_lane.task_props(task_lane.task_path(root, "TK-004"))
        for request in ("Correct the UI button label in app.py",
                        "Preserve semantic markup in app.py", "Fix the visual design in app.py"):
            with self.subTest(request=request), mock.patch.object(task_lane, "read_methods", return_value={}):
                methods = task_lane.triggered(rules, "skill", request, {"app.py"})
                self.assertIn("experience-design", methods)
                companions = task_lane.companion_evidence(root, "TK-004", methods, props, {"app.py"})
                required = next(row for row in companions if row["companion"] == "impeccable")
                self.assertFalse(required["ok"])
        methods = task_lane.triggered(rules, "skill", "Fix an integer calculation in app.py", {"app.py"})
        self.assertNotIn("experience-design", methods)
        with mock.patch.object(task_lane, "read_methods", return_value={}):
            companions = task_lane.companion_evidence(root, "TK-004", methods, props, {"app.py"})
        self.assertNotIn("impeccable", [row["companion"] for row in companions])

    def test_routine_integration_proof_does_not_require_solution_design(self):
        root = self.project()
        for text, expected in (("Record integration readiness", False), ("Run an integration test", False),
                               ("Change an external integration", True), ("Change an integration contract", True),
                               ("Change an integration boundary", True)):
            with self.subTest(text=text):
                props = {"rigor": "normal", "required_methods": ["experience-design"]}
                methods = task_lane.required_methods(root, props, {"app.py"}, text)
                self.assertEqual("solution-design" in methods, expected)
                self.assertIn("experience-design", methods)
                companions = task_lane.companion_evidence(root, "TK-004", methods, props, {"app.py"}, head="a" * 40)
                self.assertEqual("security-audit" in [row["companion"] for row in companions], expected)
        self.assertIn("solution-design", task_lane.required_methods(
            root, {"required_methods": ["solution-design"]}, {"app.py"}, "Record integration readiness"))
        self.assertIn("solution-design", task_lane.required_methods(root, {}, {"api/contracts/export.py"}))
        for risk in ("migration", "data_placement"):
            self.assertIn("data-design", task_lane.required_methods(
                root, {"risks": [risk]}, {"app.py"}, "Run an integration test"))

    def test_method_discovery_uses_task_contract_and_preserves_legacy_scope(self):
        task = TASK.replace("rigor: normal", "rigor: normal\nrisks: migration")
        task = task.replace("<!-- sec: nav -->", "## Design and scope\n\n"
                            "No UI or external integration change is needed. Existing page.tsx stays intact.\n\n"
                            "<!-- sec: nav -->")
        root = self.project(task)
        path = task_lane.task_path(root, "TK-004")
        props = task_lane.task_props(path)
        body = task_lane.task_body(path)
        self.assertNotIn("No UI", body)
        self.assertNotIn("page.tsx", task_lane.named_files(body))
        self.assertEqual(task_lane.required_methods(root, props, {"app.py"}, body), ["data-design"])
        self.assertIn("experience-design", task_lane.required_methods(root, props, {"page.tsx"}, body))
        with self.fake_run("app.py"):
            inputs = task_lane.cmd_review_inputs(SimpleNamespace(task="TK-004", base="main"), root)
        self.assertEqual(inputs["triggered_skills"], ["data-design"])
        self.assertNotIn("impeccable", [row["companion"] for row in inputs["companion_evidence"]])
        for body in ("## Request\n\nFix the UI label.\n\n## Acceptance Criteria\n\n1. Correct text.\n",
                     "## Request\n\nFix the label.\n\n### Do\n\n- Preserve semantic markup.\n",
                     "## Request\n\nFix the label.\n\n## Acceptance Criteria\n\n1. Preserve semantic markup.\n",
                     "Fix the UI label in this legacy unstructured task.\n"):
            with self.subTest(body=body):
                path.write_text(body, encoding="utf-8")
                methods = task_lane.required_methods(root, {}, {"app.py"}, task_lane.task_body(path))
                self.assertIn("experience-design", methods)

    def fake_run(self, changed: str = ""):
        def run(*args, **_kwargs):
            if args == ("git", "rev-parse", "HEAD"):
                return "a" * 40
            if args == ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"):
                return str(_kwargs["cwd"] / ".git")
            if args[:2] == ("git", "diff") and "--name-only" in args and "--cached" not in args:
                return changed
            if args[:3] == ("gh", "pr", "list"):
                return "[]"
            if args[:3] == ("gh", "pr", "create"):
                return "https://github.com/acme/shop/pull/9"
            return ""
        return mock.patch.object(task_lane, "run", side_effect=run)

    def open_pr(self, root: Path) -> dict:
        return task_lane.cmd_open_pr(SimpleNamespace(task="TK-004", base="main"), root)

    def method(self, root: Path, evidence: str | None = None) -> dict:
        return task_lane.cmd_method(SimpleNamespace(task="TK-004", name="data-design", evidence=evidence), root)

    def test_small_data_changes_require_the_same_method_as_large_ones(self):
        for size in ("s", "m", "l"):
            root = self.project()
            args = SimpleNamespace(request="Add db/migrations/0009_totals.sql", request_file=None,
                                   title="Persist totals", epic=None, source_story=None, covers=None,
                                   fixes=None, knowledge=False, size=size, base="main", risk=None, switch=True)
            with self.fake_run():
                result = task_lane.cmd_start(args, root)
            self.assertEqual(result["required_methods"], ["data-design"])
            self.assertEqual(result["suggested_risks"], ["migration"])
            self.assertEqual((result["proposed_rigor"], result["risks"]), ("normal", []))
            props = task_lane.task_props(root / result["task"])
            self.assertEqual(props["size"], size)
            self.assertEqual(task_lane.field_items(props, "required_methods"), ["data-design"])
            self.assertNotIn("applied_skills", props)
            self.assertNotIn("roles", props)
            self.assertNotIn("spawn", result)
            args.risk = ["migration"]
            with self.fake_run():
                result = task_lane.cmd_start(args, root)
            self.assertEqual((result["proposed_rigor"], result["risks"]), ("high", ["migration"]))

    def test_a_knowledge_task_naming_risky_words_starts_normal_with_only_explicit_risks(self):
        root = self.project()
        args = SimpleNamespace(delivery=None, at=None, request="Document where the credential and token live",
                               request_file=None, title="Secrets notes", epic=None, source_story=None,
                               covers=None, fixes=None, knowledge=True, size="s", base="main", risk=None, switch=True)
        with self.fake_run():
            result = task_lane.cmd_start(args, root)
        self.assertEqual((result["proposed_rigor"], result["risks"]), ("normal", []))
        args.risk = ["secrets"]
        with self.fake_run():
            self.assertEqual(task_lane.cmd_start(args, root)["risks"], ["secrets"])

    def test_confirmed_data_risks_require_design_without_discovery_matches(self):
        for risk in ("migration", "data_placement"):
            with self.subTest(risk=risk):
                root = self.project(TASK.replace("rigor: normal", f"rigor: high\nrisks: {risk}"))
                (root / "workspace/config.json").write_text(json.dumps({"task_triggers": {
                    "method_skills": [{"skill": "data-design", "keywords": [], "path_globs": []}]}}), encoding="utf-8")
                props = task_lane.task_props(task_lane.task_path(root, "TK-004"))
                self.assertNotIn("required_methods", props)
                self.assertEqual(task_lane.required_methods(root, props, {"opaque.py"}), ["data-design"])
                self.assertEqual(task_lane.required_methods(root, {"risks": "auth"}, {"opaque.py"}), [])
                args = SimpleNamespace(request="Adjust opaque.py", request_file=None, title="Adjust behavior",
                                       epic=None, source_story=None, covers=None, fixes=None, knowledge=False,
                                       size="s", base="main", risk=[risk], switch=True)
                with self.fake_run():
                    started = task_lane.cmd_start(args, root)
                    inputs = task_lane.cmd_review_inputs(SimpleNamespace(task="TK-004", base="main"), root)
                self.assertEqual(started["required_methods"], ["data-design"])
                self.assertEqual(inputs["missing_skills"], ["data-design"])
        self.assertEqual(task_lane.required_methods(None, {"title": "Correct visual design"}, {"opaque.py"}),
                         ["experience-design"])

    def test_missing_or_stale_discovered_or_confirmed_methods_block_all_publication_and_merge_paths(self):
        import proof_train

        head, base = "a" * 40, "b" * 40
        cases = [("data-design", TASK.replace("rigor: normal", f"rigor: high\nrisks: {risk}"))
                 for risk in ("migration", "data_placement")]
        cases.append(("experience-design", TASK.replace("Export the month's invoices as CSV.",
                                                       "Preserve semantic markup in opaque.py.")))
        for method, text in cases:
            for state in ("missing", "stale"):
                with self.subTest(method=method, task=text, evidence=state):
                    root = self.project(text)
                    record = task_lane.task_path(root, "TK-004")
                    self.assertNotIn("required_methods", task_lane.task_props(record))
                    integration_task = root / "wt-1" / record.relative_to(root)
                    integration_task.parent.mkdir(parents=True)
                    integration_task.write_text(text, encoding="utf-8")
                    evidence = root / "design.md"
                    evidence.write_text("Reviewed existing rows and tenant ownership.\n", encoding="utf-8")
                    if state == "stale":
                        task_lane.cmd_method(SimpleNamespace(task="TK-004", name=method, evidence=None), root)
                        task_lane.cmd_method(SimpleNamespace(task="TK-004", name=method, evidence="design.md"), root)
                        self.assertEqual(task_lane.missing_methods(root, "TK-004", [method]), [])
                        evidence.write_text("Changed design after its receipt.\n", encoding="utf-8")
                    self.assertEqual(task_lane.missing_methods(root, "TK-004", [method]), [method])
                    batch = {"id": 1, "status": proof_train.GREEN, "base": "main", "head": head,
                             "base_head": base, "integration_pr": "99",
                             "commands": [{"command": "check", "exit_code": 0}],
                             "members": [{"task": "TK-004", "head": head, "root": str(root)}]}
                    train_state = SimpleNamespace(batch=lambda *args: batch)
                    merge = SimpleNamespace(task="TK-004", pr="99", head=head, critical=0,
                                            non_critical=0, filed=0, detail=None, delegated=True,
                                            base="main", batch=1, dry_run=False)

                    def run(*args, cwd=None):
                        if args[:3] == ("gh", "pr", "view"):
                            return json.dumps({"headRefOid": head, "headRefName": "train/1",
                                               "baseRefName": "main", "state": "OPEN"})
                        if args[:2] == ("git", "ls-remote"):
                            return f"{base}\trefs/heads/main"
                        if args[:2] == ("git", "diff") and "--name-only" in args:
                            return "opaque.py"
                        return head if args == ("git", "rev-parse", "HEAD") else ""

                    with mock.patch.object(task_lane, "run", side_effect=run) as commands, \
                            mock.patch.object(task_lane, "changed_files", return_value={"opaque.py"}), \
                            mock.patch.object(task_lane, "head_state", return_value=(head, True)), \
                            mock.patch.object(task_lane, "proof_commands", return_value=["check"]), \
                            mock.patch.object(proof_train, "home", return_value=root), \
                            mock.patch.object(proof_train, "check_reviewed_heads"):
                        for operation in (lambda: self.open_pr(root),
                                          lambda: task_lane.cmd_merge(merge, root),
                                          lambda: proof_train.add(merge, root, task_lane, train_state),
                                          lambda: proof_train.publish(merge, root, task_lane, train_state),
                                          lambda: proof_train.merge(merge, root, task_lane, train_state)):
                            with self.assertRaisesRegex(task_lane.Refused, f"method evidence is missing.*{method}"):
                                operation()
                        self.assertFalse(any(call.args[:3] in (("gh", "pr", "create"), ("gh", "pr", "merge"),
                                                              ("gh", "pr", "comment"))
                                             or call.args[:2] == ("git", "push") for call in commands.call_args_list))

    def test_required_method_needs_current_load_and_completion_evidence(self):
        task = TASK.replace("size: s", "size: s\nrequired_methods: data-design\napplied_skills: data-design")
        root = self.project(task)
        skills = root / "methods"
        for name in ("data-design", "solution-design", "experience-design", "verify"):
            source = skills / name / "SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_text(f"# {name}\nExamine the affected contract.\n", encoding="utf-8")
        source = skills / "data-design" / "SKILL.md"
        evidence = root / "data-review.md"
        evidence.write_text("Reviewed nullability and existing rows.\n", encoding="utf-8")
        with mock.patch.object(task_lane, "SKILLS", skills), self.fake_run() as run:
            self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
            with self.assertRaisesRegex(task_lane.Refused, "method evidence is missing"):
                self.open_pr(root)
            self.assertFalse(any(call.args[:2] == ("git", "push") for call in run.call_args_list))
            with self.assertRaisesRegex(task_lane.Refused, "load this version"):
                self.method(root, "data-review.md")
            loaded = self.method(root)
            self.assertEqual(loaded["content"], source.read_text(encoding="utf-8"))
            self.assertEqual(loaded["skill_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(loaded["load_source"], "method_tool")
            self.assertNotIn("completion_source", loaded)
            self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
            completed = self.method(root, "data-review.md")
            self.assertEqual(completed["completion_source"], "worker_report")
            self.assertEqual(completed["evidence_sha256"], hashlib.sha256(evidence.read_bytes()).hexdigest())
            self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), [])
            self.assertEqual(self.open_pr(root)["pr"], "9")
            evidence.write_text("Changed review.\n", encoding="utf-8")
            with self.assertRaisesRegex(task_lane.Refused, "method evidence is missing"):
                self.open_pr(root)
            self.method(root, "data-review.md")
            source.write_text("# data-design\nNew method requirements.\n", encoding="utf-8")
            self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
            with self.assertRaisesRegex(task_lane.Refused, "load this version"):
                self.method(root, "data-review.md")
            self.method(root)
            self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
            self.method(root, "data-review.md")
            self.assertEqual(self.open_pr(root)["pr"], "9")

    def test_a_changed_migration_requires_evidence_even_when_the_task_omits_the_method(self):
        root = self.project()
        with self.fake_run("db/migrations/0008_add_index.sql\n"), \
                self.assertRaisesRegex(task_lane.Refused, "data-design"):
            self.open_pr(root)

    def test_the_retrospective_counts_role_checks_and_their_minutes(self):
        root = self.project()
        path = task_lane.task_path(root, "TK-004")
        task_lane.add_line(path, "Role Checks", "- 2026-10-16T10:05:00+00:00 software-architect go: fits the records")
        task_lane.add_line(path, "Role Checks", "- 2026-10-16T10:06:30+00:00 qa-engineer go: checks passed")
        task = {**task_lane.task_props(path), "role_checks": task_lane.role_checks(path)}
        row = task_lane.task_row(task, pr("2026-10-16T10:12:00Z", "2026-10-16T10:15:00Z",
                                          "2026-10-16T10:14:00Z", "2026-10-16T10:16:00Z"))
        self.assertEqual(row["role_checks"], {"checks": 2, "minutes": 3.5})
        data = task_lane.report([row], "latest 20")
        self.assertEqual(data["targets"]["build"]["role_checks"], {"tasks": 1, "checks": 2, "minutes": 3.5})

    def test_a_date_only_role_check_counts_and_adds_no_minutes(self):
        task = {"approved_at": "2026-10-27T10:00:00+00:00",
                "role_checks": [{"at": "2026-10-27"}, {"at": "2026-10-27T11:30:00+00:00"}]}
        self.assertEqual(task_lane.role_minutes(task), {"checks": 2, "minutes": 90.0})

    def test_date_only_role_checks_alone_count_and_add_no_minutes(self):
        task = {"approved_at": "2026-10-27T10:00:00+00:00",
                "role_checks": [{"at": "2026-10-27"}, {"at": "2026-10-28"}]}
        self.assertEqual(task_lane.role_minutes(task), {"checks": 2, "minutes": 0.0})

    def test_a_stamp_with_no_time_or_no_offset_does_not_abort_the_minutes(self):
        check = {"at": "2026-10-27T11:30:00+00:00"}
        self.assertEqual(task_lane.role_minutes({"approved_at": "2026-10-27", "role_checks": [check]}),
                         {"checks": 1, "minutes": 0.0})
        self.assertEqual(task_lane.role_minutes({"requested_at": "2026-10-27", "role_checks": [check]}),
                         {"checks": 1, "minutes": 0.0})
        self.assertEqual(task_lane.role_minutes({"approved_at": "2026-10-27T10:00:00+00:00",
                                                 "role_checks": [{"at": "2026-10-27T11:30:00"}]}),
                         {"checks": 1, "minutes": 90.0})


class LocalProofTests(unittest.TestCase):
    """Config `pr_proof`: at `local_record`, open-pr needs a green local proof of the clean HEAD."""

    HEAD = "a" * 40

    def project(self, value: str | None = "local_record") -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(TASK, encoding="utf-8")
        companion_fallbacks(root)
        if value:
            (root / "workspace" / "config.json").write_text(json.dumps({"pr_proof": value}),
                                                            encoding="utf-8")
        return root

    def record(self, root: Path, head: str = HEAD, clean: bool = True, *commands: dict) -> None:
        commands = commands or ({"command": "make test", "exit_code": 0, "seconds": 1.0,
                                 "finished_at": "2026-10-16T10:00:00+00:00"},)
        path = root / task_lane.PROOFS / "TK-004.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"head": head, "tree_clean": clean, "commands": list(commands)}),
                        encoding="utf-8")

    def open_pr(self, root: Path, clean: bool = True) -> tuple[dict, str]:
        calls = []

        def run(*args, **_kwargs):
            calls.append(args)
            if args == ("git", "rev-parse", "HEAD"):
                return self.HEAD
            if args[:3] == ("gh", "pr", "list"):
                return "[]"
            if args[:3] == ("gh", "pr", "create"):
                return "https://github.com/acme/shop/pull/9"
            return ""
        with mock.patch.object(task_lane, "run", side_effect=run), \
                mock.patch.object(task_lane, "head_state", return_value=(self.HEAD, clean)):
            result = task_lane.cmd_open_pr(SimpleNamespace(task="TK-004", base="main"), root)
        create = next(call for call in calls if call[:3] == ("gh", "pr", "create"))
        return result, create[create.index("--body") + 1]

    def test_the_default_opens_the_pull_request_without_a_record(self):
        for value in (None, "remote_ci"):
            result, body = self.open_pr(self.project(value))
            self.assertEqual(result["pr"], "9")
            self.assertNotIn("Local proof", body)

    def test_a_knowledge_task_changing_only_markdown_needs_no_proof(self):
        root = self.project()
        task = task_lane.task_path(root, "TK-004")
        task.write_text(task.read_text(encoding="utf-8").replace("type: task", "type: task\ntask_kind: knowledge", 1),
                        encoding="utf-8")
        for changed, waived in (("docs/a.md\nREADME.md", True), ("docs/a.md\nsrc/app.py", False), ("", False)):
            with self.subTest(changed), mock.patch.object(task_lane, "changed_files",
                                                          return_value=set(filter(None, changed.splitlines()))):
                self.assertEqual(task_lane.proof_waived(root, task, "main"), waived)

    def test_a_missing_stale_red_or_dirty_proof_refuses_before_pushing(self):
        red = {"command": "echo boom; exit 3", "exit_code": 3, "failed_tests": ["tests/test_a.py::test_one"]}
        cases = (("no local proof record; run prove --task TK-004", None, True),
                 ("the proof ran on bbbbbbbbbbbb, HEAD is aaaaaaaaaaaa", {"head": "b" * 40}, True),
                 ("`echo boom; exit 3` exited 3; failed: tests/test_a.py::test_one", {"commands": (red,)}, True),
                 ("the proof ran on a tree with uncommitted changes", {"clean": False}, True),
                 ("tracked files changed since HEAD", {}, False))
        for message, record, clean in cases:
            with self.subTest(message):
                root = self.project()
                if record is not None:
                    self.record(root, record.get("head", self.HEAD), record.get("clean", True),
                                *record.get("commands", ()))
                with self.assertRaisesRegex(task_lane.Refused, re.escape(message)):
                    self.open_pr(root, clean=clean)
                self.assertEqual(task_lane.task_props(task_lane.task_path(root, "TK-004"))["status"], "active")

    def test_a_green_record_of_head_opens_the_pull_request_with_its_proof(self):
        root = self.project()
        self.record(root)
        result, body = self.open_pr(root)
        self.assertEqual(result["pr"], "9")
        self.assertIn(f"Every check passed on HEAD `{self.HEAD}` with a clean tree", body)
        self.assertIn("| `make test` | pass |", body)

    def test_a_role_check_after_the_proof_keeps_it_but_any_other_change_does_not(self):
        root = self.project()
        self.record(root, "b" * 40)
        own = task_lane.task_path(root, "TK-004").relative_to(root).as_posix()
        for changed, stale in ((own, False), (own + "\nsrc/other.txt", True)):
            with mock.patch.object(task_lane, "run", return_value=changed), \
                    mock.patch.object(task_lane, "head_state", return_value=(self.HEAD, True)):
                problems = task_lane.proof_problems(root, "TK-004")[0]
            self.assertEqual(any("run prove again" in item for item in problems), stale, changed)

    def test_failing_test_ids_are_read_from_pytest_and_unittest_output(self):
        output = ("FAILED tests/test_a.py::test_one - AssertionError\nFAIL: test_two (pkg.test_b.Case)\n"
                  "FAILED tests/test_a.py::test_one - again\n")
        self.assertEqual(task_lane.failed_test_ids(output), ["tests/test_a.py::test_one", "pkg.test_b.Case.test_two"])

    def test_multi_suite_output_names_each_suite_with_its_status_and_first_failure(self):
        output = ("===== orders ok 3.1s\norders: ran 214\n"
                  "===== billing FAILED 12.4s\n"
                  "FAIL: test_refund_limit (billing.tests.test_refunds.RefundTest.test_refund_limit)\n"
                  "ERROR: test_refund_fee (billing.tests.test_refunds.RefundTest.test_refund_fee)\n"
                  "FAILED (failures=1, errors=1)\nbilling: ran 96\n")
        self.assertEqual(task_lane.suite_results(output), [
            {"name": "orders", "status": "ok", "failures": 0, "first_failure": ""},
            {"name": "billing", "status": "FAILED", "failures": 2,
             "first_failure": "billing.tests.test_refunds.RefundTest.test_refund_limit"}])

    def test_pytest_collection_errors_and_node_test_failures_are_read_too(self):
        output = ("ERROR tests/test_c.py - ImportError: no module\n✖ failing tests:\n\n"
                  "test at file:///work/web/tests/d.test.ts:4:1\n✖ d broken\n")
        self.assertEqual(task_lane.failed_test_ids(output), ["tests/test_c.py", "/work/web/tests/d.test.ts"])

    def test_a_failed_proof_keeps_the_end_of_its_output_where_the_summary_is(self):
        output = "0.50s call tests/test_x.py::test_slow\n" * 60 + "1 failed in 3.0s"
        self.assertEqual(task_lane.proof_tail(output)[-16:], "1 failed in 3.0s")
        self.assertEqual(len(task_lane.proof_tail(output)), 400)


@integration
class LocalProofGitTests(unittest.TestCase):
    def test_prove_records_each_command_on_the_real_head_and_a_new_commit_makes_it_stale(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        init_repository(root)
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(TASK, encoding="utf-8")
        (root / "workspace").mkdir(exist_ok=True)
        (root / "workspace/config.json").write_text(json.dumps({"local_proof": ["true", "echo {base}; exit 3"]}),
                                                    encoding="utf-8")

        def git(*args: str) -> str:
            return task_lane.run("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args, cwd=root)
        git("add", "-A")
        git("commit", "-qm", "start")
        record = task_lane.cmd_prove(SimpleNamespace(task="TK-004", base="main"), root)
        self.assertEqual((record["head"], record["tree_clean"], record["ok"]), (git("rev-parse", "HEAD"), True, False))
        self.assertEqual([(item["command"], item["exit_code"]) for item in record["commands"]],
                         [("true", 0), ("echo main; exit 3", 3)])
        self.assertIn("main", record["commands"][1]["tail"])
        (root / "src.txt").write_text("change", encoding="utf-8")
        git("add", "src.txt")
        git("commit", "-qm", "change")
        self.assertTrue(any("run prove again" in item for item in task_lane.proof_problems(root, "TK-004")[0]))

    def test_risk_flags_preserve_explicit_impact_based_proof_selection(self):
        def selection(task: str) -> tuple[str, list[str]]:
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(root))
            init_repository(root)
            (root / task_lane.TASKS).mkdir(parents=True)
            (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(task, encoding="utf-8")
            (root / "workspace").mkdir(exist_ok=True)
            (root / "workspace/config.json").write_text(json.dumps({
                "local_proof": ["echo full"],
                "local_proof_map": [{"paths": ["src/*"], "commands": ["echo narrow"], "reason": "src only"}]}),
                encoding="utf-8")
            (root / "src").mkdir()
            (root / "src/app.py").write_text("x = 0\n", encoding="utf-8")

            def git(*args: str) -> str:
                return task_lane.run("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args, cwd=root)
            git("add", "-A")
            git("commit", "-qm", "start")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            (root / "src/app.py").write_text("x = 1\n", encoding="utf-8")
            git("commit", "-qam", "change")
            record = task_lane.cmd_prove(SimpleNamespace(task="TK-004", base="main"), root)
            return record["selection"], [item["command"] for item in record["commands"]]

        self.assertEqual(selection(TASK.replace("---\n", "---\nrisks: auth\n", 1)), ("impact", ["echo narrow"]))
        self.assertEqual(selection(TASK), ("impact", ["echo narrow"]))


@integration
class TaskStartWorktreeTests(unittest.TestCase):
    """start gives the task its own worktree; a shared checkout stays on its branch with its dirty files."""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        self.origin, self.root, self.tree = base / "origin.git", base / "shared", base / "task-tree"
        init_repository(self.origin, bare=True, initial_branch="main")
        init_repository(self.root, initial_branch="main")
        self.git("remote", "add", "origin", str(self.origin))
        (self.root / "app.txt").write_text("one", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "first")
        self.git("push", "-q", "-u", "origin", "main")

    def git(self, *args: str) -> str:
        return task_lane.run("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args, cwd=self.root)

    def start(self, **extra) -> dict:
        args = SimpleNamespace(delivery=None, at=None, request="Fix the export", request_file=None,
                               title="Fix export", epic=None, source_story=None, covers=None, fixes=None,
                               knowledge=True, size="s", base="main", risk=None, **extra)
        return task_lane.cmd_start(args, self.root)

    def test_start_leaves_a_dirty_shared_checkout_on_its_branch_and_works_in_a_worktree(self):
        (self.root / "app.txt").write_text("edited", encoding="utf-8")
        with mock.patch("companion_routing.apply", return_value=[".native/local-settings"]) as refresh:
            result = self.start(worktree_dir=str(self.tree))
        refresh.assert_called_once_with(self.tree)
        self.assertEqual(result["companion_routing"], [".native/local-settings"])
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual((self.root / "app.txt").read_text(encoding="utf-8"), "edited")
        self.assertEqual(result["worktree"], str(self.tree))
        self.assertNotIn("push_reconciliation", result)
        self.assertTrue((self.tree / result["task"]).is_file())
        self.assertFalse((self.root / result["task"]).exists())
        self.assertEqual(task_lane.run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=self.tree), result["branch"])

    def test_local_routing_failure_preserves_reserved_task_for_resume(self):
        with mock.patch("companion_routing.apply", side_effect=ValueError("invalid local settings")), \
                self.assertRaisesRegex(task_lane.Refused, "Resume existing TK-001"):
            self.start(worktree_dir=str(self.tree))
        self.assertTrue(list((self.tree / task_lane.TASKS).glob("tk-001-*.md")))
        branch = task_lane.run("git", "branch", "--show-current", cwd=self.tree)
        head = task_lane.run("git", "rev-parse", "HEAD", cwd=self.tree)
        self.assertEqual(self.git("ls-remote", "--heads", "origin", f"refs/heads/{branch}"),
                         f"{head}\trefs/heads/{branch}")

    def test_switch_keeps_the_old_behaviour_on_request(self):
        result = self.start(switch=True)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), result["branch"])
        self.assertEqual(result["worktree"], str(self.root))

    def test_local_unpushed_task_branches_reserve_their_ids(self):
        self.git("branch", "task/tk-009-pending")
        result = self.start(worktree_dir=str(self.tree))
        self.assertEqual(result["task_id"], "TK-010")

    def test_denied_worktree_add_removes_only_its_unchanged_unattached_branch(self):
        original = task_lane.run
        base = self.git("rev-parse", "origin/main")
        calls = []

        def run(*args, cwd=None):
            calls.append(args)
            if args[:3] == ("git", "worktree", "add"):
                self.tree.mkdir()
                (self.tree / "owner.txt").write_text("keep", encoding="utf-8")
                raise task_lane.Refused("config.lock denied")
            return original(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run), self.assertRaisesRegex(task_lane.Refused, "config.lock denied.*destination"):
            self.start(worktree_dir=str(self.tree))
        ref = "refs/heads/task/tk-001-fix-export"
        self.assertIn(("git", "update-ref", ref, base, "0" * len(base)), calls)
        self.assertIn(("git", "update-ref", "-d", ref, base), calls)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", ref), "")
        self.assertEqual((self.tree / "owner.txt").read_text(encoding="utf-8"), "keep")
        retried = self.start(worktree_dir=str(self.tree.parent / "retry"))
        self.assertEqual(retried["task_id"], "TK-001")

    def test_failed_add_preserves_a_branch_that_moved_from_the_starting_base(self):
        self.git("commit", "--allow-empty", "-qm", "local change")
        changed = self.git("rev-parse", "HEAD")
        original = task_lane.run

        def run(*args, cwd=None):
            if args[:3] == ("git", "worktree", "add"):
                original("git", "update-ref", f"refs/heads/{args[4]}", changed, cwd=cwd)
                raise task_lane.Refused("interrupted add")
            return original(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run), self.assertRaisesRegex(task_lane.Refused, "branch preserved"):
            self.start(worktree_dir=str(self.tree))
        self.assertEqual(self.git("rev-parse", "task/tk-001-fix-export"), changed)

    def test_failed_add_preserves_a_branch_that_is_attached_to_a_worktree(self):
        original = task_lane.run

        def run(*args, cwd=None):
            result = original(*args, cwd=cwd)
            if args[:3] == ("git", "worktree", "add"):
                raise task_lane.Refused("lost add response")
            return result

        with mock.patch.object(task_lane, "run", run), self.assertRaisesRegex(task_lane.Refused, "branch preserved"):
            self.start(worktree_dir=str(self.tree))
        self.assertIn("branch refs/heads/task/tk-001-fix-export", self.git("worktree", "list", "--porcelain"))
        self.assertTrue((self.tree / "app.txt").is_file())

    def test_failed_add_preserves_a_branch_that_existed_before_the_attempt(self):
        branch = "task/tk-001-existing"
        self.git("branch", branch)
        before = self.git("rev-parse", branch)
        with self.assertRaisesRegex(task_lane.Refused, "Branch preserved"):
            task_lane.create_task_worktree(self.root, self.tree, branch, "origin/main")
        self.assertEqual(self.git("rev-parse", branch), before)

    def test_reservation_preserves_an_external_same_ref_created_at_the_same_base(self):
        original = task_lane.run
        ref = "refs/heads/task/tk-001-fix-export"
        base = self.git("rev-parse", "origin/main")
        calls = []

        def run(*args, cwd=None):
            calls.append(args)
            if args[:3] == ("git", "update-ref", ref):
                original("git", "update-ref", ref, base, cwd=cwd)
            return original(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run), self.assertRaisesRegex(task_lane.Refused, "Branch preserved"):
            self.start(worktree_dir=str(self.tree))
        self.assertEqual(self.git("rev-parse", ref), base)
        self.assertFalse(any(args[:3] == ("git", "worktree", "add") for args in calls))
        self.assertFalse(any(args[:3] == ("git", "update-ref", "-d") for args in calls))

    def test_cleanup_compare_and_swap_preserves_a_concurrently_changed_branch(self):
        self.git("commit", "--allow-empty", "-qm", "local change")
        changed = self.git("rev-parse", "HEAD")
        original = task_lane.run

        def run(*args, cwd=None):
            if args[:3] == ("git", "worktree", "add"):
                raise task_lane.Refused("denied add")
            if args[:3] == ("git", "update-ref", "-d"):
                original("git", "update-ref", args[3], changed, cwd=cwd)
            return original(*args, cwd=cwd)

        with mock.patch.object(task_lane, "run", run), self.assertRaisesRegex(task_lane.Refused, "cleanup could not be verified"):
            self.start(worktree_dir=str(self.tree))
        self.assertEqual(self.git("rev-parse", "task/tk-001-fix-export"), changed)

    def test_a_push_accepted_before_a_lost_response_is_reconciled_by_exact_remote_head(self):
        original = task_lane.run

        def run(*args, cwd=None):
            result = original(*args, cwd=cwd)
            if args[:3] == ("git", "push", "-u"):
                raise task_lane.Refused("lost push response")
            return result

        with mock.patch.object(task_lane, "run", run):
            result = self.start(worktree_dir=str(self.tree))
        head = original("git", "rev-parse", "HEAD", cwd=self.tree)
        self.assertEqual(result["push_reconciliation"], {
            "status": "remote_confirmed", "remote_head": head,
            "upstream_tracking": "unverified", "error": "lost push response"})
        self.assertEqual(self.git("ls-remote", "--heads", "origin", f"refs/heads/{result['branch']}"),
                         f"{head}\trefs/heads/{result['branch']}")
        self.assertTrue((self.tree / result["task"]).is_file())

    def test_an_unconfirmed_push_preserves_the_task_and_points_to_its_existing_worktree(self):
        original = task_lane.run
        for state in ("missing", "unavailable", "different"):
            with self.subTest(state=state):
                tree = self.tree.parent / state

                def run(*args, cwd=None):
                    if args[:3] == ("git", "push", "-u"):
                        raise task_lane.Refused("push failed")
                    if args[:3] == ("git", "ls-remote", "--heads"):
                        if state == "unavailable":
                            raise task_lane.Refused("remote unavailable")
                        return "" if state == "missing" else f"{'b' * 40}\t{args[4]}"
                    return original(*args, cwd=cwd)

                with mock.patch.object(task_lane, "run", run), self.assertRaises(task_lane.Refused) as raised:
                    self.start(worktree_dir=str(tree))
                self.assertIn("Resume existing TK-", str(raised.exception))
                self.assertIn(str(tree), str(raised.exception))
                self.assertEqual(len(list((tree / task_lane.TASKS).glob("tk-*-*.md"))), 1)
                self.assertTrue(original("git", "rev-parse", "HEAD", cwd=tree))


@integration
class ReportRecordsTests(unittest.TestCase):
    """The report reads the task records of origin/main, so a checkout that has not pulled still counts every merged task."""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        self.origin, self.root = base / "origin.git", base / "checkout"
        init_repository(self.origin, bare=True, initial_branch="main")
        init_repository(self.root, initial_branch="main")
        self.record = self.root / task_lane.TASKS / "tk-004-export-invoices.md"

    def git(self, *args: str) -> str:
        return task_lane.run("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args, cwd=self.root)

    def titles(self) -> list[str]:
        args = SimpleNamespace(delivery=None, since=None, recent=20, deep=False, no_sessions=True)
        with mock.patch.object(task_lane, "pull_requests", return_value={}):
            return [row["title"] for row in task_lane.gather(self.root, args)["per_task"]]

    def test_a_task_merged_after_the_last_pull_is_counted(self):
        self.git("remote", "add", "origin", str(self.origin))
        (self.root / "app.txt").write_text("one", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "first")
        self.record.parent.mkdir(parents=True)
        self.record.write_text(TASK, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "merge a task")
        self.git("push", "-q", "origin", "main")
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.assertFalse(self.record.exists())
        self.assertEqual(self.titles(), ["Export invoices"])

    def test_without_an_origin_main_the_records_of_the_working_tree_are_counted(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(TASK, encoding="utf-8")
        self.assertEqual(self.titles(), ["Export invoices"])


STORY = """---
type: story
title: "Invoice export"
id: ST-012
---
# Invoice export

## Acceptance

- [ ] The export lists every invoice of the month.
- [ ] Each row carries the number and total.
- [ ] An empty month yields a header-only file.

## Dependencies

- none
"""


class TaskLinkTests(unittest.TestCase):
    def project(self) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        epic = root / task_lane.STORIES / "billing"
        (epic / "stories" / "invoice-export").mkdir(parents=True)
        (epic / "epic.md").write_text("---\ntype: epic\nid: EP-003\n---\n# Billing\n", encoding="utf-8")
        (epic / "stories" / "invoice-export" / "story.md").write_text(STORY, encoding="utf-8")
        return root

    def write(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "tk-004-export-invoices.md"
        self.addCleanup(lambda: __import__("shutil").rmtree(path.parent))
        path.write_text(text, encoding="utf-8")
        return path

    def test_links_point_from_the_task_to_its_story(self):
        linked = TASK.replace("rigor: normal\n", "rigor: normal\nepic: EP-003\nsource_story: ST-012\n"
                              "covers:\n  - 1\n  - 3\n")
        self.assertEqual(task_lane.lint(self.write(linked)), [])
        self.assertEqual(task_lane.covered(task_lane.task_props(self.write(linked))), [1, 3])
        errors = task_lane.lint(self.write(linked.replace("  - 1\n  - 3\n", "")))
        self.assertIn("covers must list the source story's criterion numbers this task delivers", errors)
        errors = task_lane.lint(self.write(TASK.replace("rigor: normal\n",
                                                        "rigor: normal\nepic: E3\ncovers:\n  - 1\n")))
        self.assertIn("epic must be EP-<number>", errors)
        self.assertIn("covers needs a source_story", errors)

    def test_a_story_is_delivered_once_merged_tasks_cover_every_criterion(self):
        stories = task_lane.story_index(self.project())
        merged = [{"id": "TK-001", "title": "a", "source_story": "ST-012", "covers": ["1", "2"]}]
        opened = [{"id": "TK-002", "title": "b", "source_story": "ST-012", "covers": ["3"]},
                  {"id": "TK-003", "title": "fix", "epic": "EP-003"}]
        group = task_lane.board(stories, merged, opened)["EP-003"]["ST-012"]
        self.assertEqual((group["covered"], group["missing"], group["delivered"]), ([1, 2], [3], False))
        epics = task_lane.board(stories, merged + [{**opened[0]}], [])
        self.assertTrue(epics["EP-003"]["ST-012"]["delivered"])
        standalone = task_lane.board(stories, [], opened[1:])["EP-003"]["standalone"]
        self.assertEqual([task["id"] for task in standalone["tasks"]], ["TK-003"])

    def test_a_story_changed_after_an_open_task_was_requested_warns(self):
        stories = task_lane.story_index(self.project())
        opened = [{"id": "TK-002", "source_story": "ST-012",
                   "requested_at": "2026-10-16T10:00:00+00:00"}]
        later = {"ST-012": "2026-10-16T11:00:00+00:00"}
        earlier = {"ST-012": "2026-10-16T09:00:00+00:00"}
        self.assertEqual([item["task"] for item in task_lane.drift(stories, opened, later)], ["TK-002"])
        self.assertEqual(task_lane.drift(stories, opened, earlier), [])

class RetrospectiveCostTests(unittest.TestCase):
    """Cost per task from transcripts, waits with an owner, reading waste, targets and history."""

    def stamp(self, minute: int) -> str:
        return f"2026-10-16T10:{minute:02d}:00+00:00"

    def test_minutes_split_by_who_the_task_waited_on(self):
        times = {"requested_at": self.stamp(0), "approved_at": self.stamp(3), "pr_opened": self.stamp(10),
                 "ci_green": self.stamp(16), "review": self.stamp(18), "merged": self.stamp(20)}
        base = int(datetime(2026, 10, 16, 10, tzinfo=timezone.utc).timestamp() // 60)
        active = {base + minute for minute in (0, 1, 4, 5, 6, 7, 8, 9, 11)}
        waits = task_lane.classify(times, active, "normal", 0)
        self.assertEqual(waits, {"agent_work": 9.0, "ci_wait": 5.0, "review_wait": 2.0, "owner_wait": 1.0,
                                 "idle": 3.0})
        self.assertEqual(sum(waits.values()), 20.0)
        high = task_lane.classify(times, active, "high", 0)
        self.assertEqual((high["owner_wait"], high["idle"]), (3.0, 1.0))

    def transcripts(self) -> tuple[Path, Path]:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(home))
        project = home / "work" / "shop"
        project.mkdir(parents=True)
        folder = home / "claude" / str(project.resolve()).replace("/", "-")
        (folder / "s1" / "subagents").mkdir(parents=True)
        def record(minute, kind, branch="task/tk-004-export", **extra):
            return json.dumps({"type": kind, "timestamp": f"2026-10-16T10:{minute:02d}:00Z",
                               "gitBranch": branch, **extra})
        main = [record(1, "user", message={"content": "go"}),
                record(2, "assistant", message={"id": "m1", "model": "claude-opus-5-5", "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/big.md"}}],
                    "usage": {"input_tokens": 10, "output_tokens": 5}}, effort="high"),
                record(2, "assistant", message={"id": "m1", "model": "claude-opus-5-5", "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 40}}, effort="high"),
                record(3, "user", message={"content": [{"type": "tool_result", "content": "x" * 300000}]}),
                record(4, "assistant", branch="task/tk-009-other",
                       message={"id": "m2", "model": "claude-opus-5-5", "content": [],
                                "usage": {"output_tokens": 7}})]
        main += [record(5, "assistant", message={"id": f"r{n}", "model": "claude-opus-5-5", "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/big.md"}}],
                    "usage": {"output_tokens": 1}}) for n in range(4)]
        (folder / "s1.jsonl").write_text("\n".join(main) + "\n", encoding="utf-8")
        (folder / "s1" / "subagents" / "agent-a1.jsonl").write_text(
            record(6, "assistant", message={"id": "a1", "model": "claude-sonnet-5", "content": [],
                                            "usage": {"output_tokens": 100}}) + "\n", encoding="utf-8")
        (folder / "s1" / "subagents" / "agent-a1.meta.json").write_text(
            json.dumps({"agentType": "software-engineering-team:backend-developer"}), encoding="utf-8")
        return home, project

    def test_cost_and_reading_waste_come_from_the_task_branch(self):
        import session_cost
        home, project = self.transcripts()
        with mock.patch.object(session_cost, "worktrees", return_value=[project.resolve()]):
            events = session_cost.scan(project, 0, home / "claude", home / "codex")
        mine = [event for event in events if session_cost.belongs(event, "task/tk-004-export", 0, 1e12)]
        cost = session_cost.cost(mine)
        self.assertEqual(cost["agents"], 1)
        self.assertEqual(cost["models"], {"claude-opus-5-5": 5, "claude-sonnet-5": 1})
        self.assertEqual(cost["tokens_by_role"]["main"]["output"], 44)
        self.assertEqual(cost["tokens_by_role"]["backend-developer"]["output"], 100)
        reading = session_cost.reading(mine)
        self.assertEqual(reading, {"repeated_reads": {"/x/big.md": 5}, "results_over_200kb": 1})

    def test_every_proposal_names_evidence_owner_gain_and_check(self):
        row = task_lane.task_row(RetrospectiveTests.task, pr("2026-10-16T10:10:00Z", "2026-10-16T10:30:00Z",
                                                             "2026-10-16T10:12:00Z", "2026-10-16T10:31:00Z"))
        row["waits"] = {"agent_work": 10.0, "ci_wait": 15.0, "review_wait": 0.0, "owner_wait": 3.0, "idle": 3.0}
        row["reading"] = {"repeated_reads": {"/x/big.md": 6}, "results_over_200kb": 2}
        found = task_lane.proposals([row])
        self.assertLessEqual(len(found), 5)
        for item in found:
            for key in ("evidence", "cause_owner", "expected_gain", "verify", "proposal"):
                self.assertTrue(item[key], (item["id"], key))
        owners = {item["id"]: item["cause_owner"] for item in task_lane.proposals([row])}
        self.assertEqual(owners.get("RETRO-WAIT-CI-WAIT"), "project")

    def test_a_task_over_its_cost_budget_is_flagged(self):
        rows = [task_lane.task_row({**RetrospectiveTests.task, "id": task_id},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T10:10:00Z"))
                for task_id in ("TK-001", "TK-002")]
        budget = task_lane.COST_BUDGETS["build"]["s"]
        cost = {"main": {"usd": 0.0}, "tokens": dict.fromkeys(task_lane.COST_TOKENS, 0)}
        rows[0]["cost"], rows[1]["cost"] = {**cost, "usd": budget + 1}, {**cost, "usd": budget}
        data = task_lane.report(rows, "latest 20")
        self.assertEqual(data["targets"]["build"]["over_cost_budget"], ["TK-001"])

    def test_a_knowledge_task_is_approved_by_its_request_and_needs_no_criteria(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        path = root / "tk-009-fix-wording.md"
        path.write_text(task_lane.task_text("TK-009", "Fix wording", "Reword rule 3.",
                                            "2026-10-16T10:00:00Z", "normal", kind="knowledge"),
                        encoding="utf-8")
        self.assertEqual(task_lane.lint(path), [])
        props = task_lane.task_props(path)
        self.assertEqual((props["task_kind"], props["approved_at"]), ("knowledge", props["requested_at"]))

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_the_report_sums_each_kind_and_ranks_its_levers_by_the_target_gap(self):
        build = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-001"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T10:40:00Z"))
        knowledge = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-002", "task_kind": "knowledge"},
                                       pr("2026-10-16T10:01:00Z", None, None, "2026-10-16T10:04:00Z"))
        data = task_lane.report([build, knowledge], "latest 20")
        self.assertEqual(set(data["targets"]), {"build", "knowledge"})
        self.assertEqual(data["targets"]["build"]["total"]["average"], 40.0)
        self.assertIn("define", data["targets"]["knowledge"]["steps"])
        self.assertTrue(data["proposals"][0]["evidence"].startswith("build: "))
        self.assertIn("vs target 5 min", data["proposals"][0]["evidence"])
        self.assertEqual(data["targets"]["knowledge"]["average_target"], 5)

    def test_targets_and_the_previous_run_are_compared(self):
        rows = [task_lane.task_row({**RetrospectiveTests.task, "id": "TK-001", "size": "l"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T11:00:00Z")),
                task_lane.task_row({**RetrospectiveTests.task, "id": "TK-002"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T10:10:00Z"))]
        goal = task_lane.targets(rows, [row["total"] for row in rows])["build"]
        self.assertEqual((goal["average"], goal["average_met"]), (35.0, False))
        self.assertEqual((goal["max"], goal["max_met"]), (60.0, False))
        self.assertEqual(goal["over_size_target"], ["TK-001", "TK-002"])
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        data = task_lane.report(rows, "latest 20")
        self.assertIsNone(task_lane.previous_run(root, "latest 20"))
        task_lane.remember(root, data)
        before = task_lane.previous_run(root, "latest 20")
        self.assertEqual((before["average"], before["merged"]), (35.0, 2))

    def test_plugin_causes_go_to_the_ledger_and_known_issues_get_a_comment(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        data = {"scope": "latest 20", "merged": 1, "target_minutes": 15,
                "request_to_merge_minutes": {"average": 30, "median": 30, "max": 30},
                "proposals": [{"id": "RETRO-REVIEW", "step": "review", "proposal": "p", "evidence": "e",
                               "cause_owner": "plugin", "expected_gain": "g", "verify": "v"},
                              {"id": "RETRO-CI", "step": "ci", "proposal": "p", "evidence": "e",
                               "cause_owner": "project", "expected_gain": "g", "verify": "v"},
                              {"id": "RETRO-WRITE", "step": "write", "proposal": "p", "evidence": "e",
                               "cause_owner": "process", "expected_gain": "g", "verify": "v"}]}
        calls = []

        def fake(*args, **_kwargs):
            calls.append(args)
            if args[:3] == ("git", "remote", "get-url"):
                return "https://github.com/acme/shop.git"
            if args[:3] == ("gh", "issue", "list"):
                return json.dumps([{"number": 7, "state": "CLOSED", "url": "u7"}]) \
                    if "RETRO-CI" in " ".join(args) else "[]"
            return "https://github.com/acme/shop/issues/8"

        args = SimpleNamespace(ids="1,2,3", delivery=None, since=None, recent=20, no_sessions=True)
        with mock.patch.object(task_lane, "gather", return_value=data), \
                mock.patch.object(task_lane, "run", side_effect=fake):
            result = task_lane.cmd_file_issues(args, root)
            again = task_lane.cmd_file_issues(SimpleNamespace(**{**vars(args), "ids": "1"}), root)
            with self.assertRaises(task_lane.Refused):
                task_lane.cmd_file_issues(SimpleNamespace(**{**vars(args), "ids": "RETRO-NOPE"}), root)
        self.assertEqual(result["repo"], "acme/shop")
        created = [call for call in calls if call[:3] == ("gh", "issue", "create")]
        self.assertEqual([call[3:5] for call in created], [("--repo", "acme/shop")])
        self.assertEqual([item["id"] for item in result["plugin_ledger"]], ["RETRO-REVIEW"])
        self.assertFalse(again["plugin_ledger"][0]["added"])
        ledger = (root / task_lane.LEDGER).read_text(encoding="utf-8")
        self.assertEqual(ledger.count("| RETRO-REVIEW: "), 1)
        self.assertEqual(result["commented"], [{"id": "RETRO-CI", "url": "u7", "state": "CLOSED"}])
        self.assertEqual([item["id"] for item in result["opened"]], ["RETRO-WRITE"])
        self.assertFalse(any("RETRO-REVIEW" in " ".join(call) for call in calls if call[:2] == ("gh", "issue")))


class RetrospectiveDollarTests(unittest.TestCase):
    """Dollars, startup context, task attribution and kept costs in the retrospective."""

    def setUp(self):
        import session_cost
        self.session_cost = session_cost
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home))
        self.project = self.home / "work" / "shop"
        self.project.mkdir(parents=True)
        self.enterContext(mock.patch.object(session_cost, "worktrees", return_value=[self.project.resolve()]))
        self.folder = self.home / "claude" / str(self.project.resolve()).replace("/", "-")
        (self.folder / "s1" / "subagents").mkdir(parents=True)
        self.prices = session_cost.load_prices(self.project)

    @staticmethod
    def record(minute, kind, branch="main", **extra):
        return json.dumps({"type": kind, "timestamp": f"2026-10-16T10:{minute:02d}:00Z",
                           "gitBranch": branch, **extra})

    def call(self, minute, key, branch="main", model="claude-opus-5-5", **usage):
        return self.record(minute, "assistant", branch,
                           message={"id": key, "model": model, "content": [], "usage": usage})

    def agent(self, name, lines, description="", agent_type="software-engineering-team:backend-developer"):
        base = self.folder / "s1" / "subagents"
        (base / f"agent-{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (base / f"agent-{name}.meta.json").write_text(
            json.dumps({"agentType": agent_type, "description": description}), encoding="utf-8")

    def scan(self):
        return self.session_cost.scan(self.project, 0, self.home / "claude", self.home / "codex")

    def test_a_spawn_is_priced_once_per_message_and_starts_with_its_first_prompt(self):
        self.agent("r1", [
            self.record(1, "user", message={"content": "Review the diff."}),
            self.call(2, "a1", input_tokens=2, cache_creation_input_tokens=60000, output_tokens=1,
                      cache_creation={"ephemeral_5m_input_tokens": 50000, "ephemeral_1h_input_tokens": 10000}),
            self.call(2, "a1", input_tokens=2, cache_creation_input_tokens=60000, output_tokens=50,
                      cache_creation={"ephemeral_5m_input_tokens": 50000, "ephemeral_1h_input_tokens": 10000}),
            self.call(3, "a2", input_tokens=1, cache_read_input_tokens=60000, output_tokens=100)],
            description="Review TK-004", agent_type="software-engineering-team:code-reviewer")
        cost = self.session_cost.cost(self.scan(), self.prices)
        # a1: 2*4 + 50000*5 + 10000*8 (1h write) + 50*20; a2: 1*4 + 60000*0.2 + 100*20, per 1M tokens.
        self.assertEqual(cost["usd"], 0.345)
        self.assertEqual(cost["tokens"], {"input": 3, "output": 150, "cache_read": 60000, "cache_write": 60000})
        spawn = cost["spawns"][0]
        self.assertEqual((spawn["startup_context"], spawn["class"], spawn["role"], spawn["purpose"]),
                         (60002, "review", "code-reviewer", "Review TK-004"))
        self.assertEqual(cost["by_class"], {"review": 0.345})
        self.assertEqual(cost["main"]["usd"], 0.0)

    def test_haiku_switches_to_its_long_context_rates_above_100k_prompt_tokens(self):
        def price(cache_read):
            return self.session_cost.usd({"model": "claude-haiku-5-5", "tokens": {
                "input": 1000, "cache_write": 0, "cache_read": cache_read, "output": 1000}}, self.prices)
        self.assertAlmostEqual(price(90000), 0.0015)
        self.assertAlmostEqual(price(100000), 0.008)
        self.assertIsNone(self.session_cost.usd({"model": "claude-unknown-1", "tokens": {
            "input": 1, "cache_write": 0, "cache_read": 0, "output": 1}}, self.prices))
        self.assertIsNotNone(self.session_cost.rates("claude-opus-5-5[1m]", self.prices))

    def test_a_fast_call_costs_its_multiplier(self):
        call = {"model": "claude-opus-5-5", "tokens": {"input": 1000, "cache_write": 0, "cache_read": 0, "output": 1000}}
        self.assertAlmostEqual(self.session_cost.usd(call, self.prices), 0.024)
        self.assertAlmostEqual(self.session_cost.usd({**call, "fast": True}, self.prices), 0.048)
        self.assertAlmostEqual(self.session_cost.usd({**call, "model": "claude-sonnet-5-5", "fast": True}, self.prices),
                               0.012)

    def test_a_project_price_file_overrides_a_model(self):
        (self.project / ".agentrof").mkdir()
        (self.project / ".agentrof" / "pricing.json").write_text(json.dumps({"models": {
            "claude-opus-5-5": {"input": 1, "cache_write_5m": 1, "cache_write_1h": 1, "cache_read": 1,
                                "output": 1}}}), encoding="utf-8")
        prices = self.session_cost.load_prices(self.project)
        self.assertEqual(prices["claude-opus-5-5"]["output"], 1)
        self.assertEqual(prices["claude-sonnet-5-5"]["output"], 10)

    def test_spawns_follow_their_task_id_branches_their_task_and_only_the_main_session_splits_by_window(self):
        (self.folder / "s1.jsonl").write_text("\n".join([
            self.call(5, "m1", output_tokens=10), self.call(25, "m2", output_tokens=20),
            self.call(50, "m3", output_tokens=40),
            self.call(45, "m4", branch="task/tk-002-b", output_tokens=400)]) + "\n", encoding="utf-8")
        self.agent("d1", [self.record(39, "user", message={"content": "Build it on task/tk-001-a."}),
                          self.call(40, "d1", output_tokens=100)], description="Developer")
        self.agent("d2", [self.record(9, "user", message={"content": "Compare TK-001 with TK-002."}),
                          self.call(10, "d2", output_tokens=200)])
        # Started on the base branch; the checkout switched to a task branch under it.
        self.agent("d3", [self.record(11, "user", message={"content": "Research."}),
                          self.call(46, "d3", branch="task/tk-002-b", output_tokens=1000)])
        tasks = [{**RetrospectiveTests.task, "id": "TK-001", "file": "tk-001-a.md"},
                 {**RetrospectiveTests.task, "id": "TK-002", "file": "tk-002-b.md",
                  "requested_at": "2026-10-16T10:03:00+00:00"}]
        rows = [task_lane.task_row(tasks[0], pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")),
                task_lane.task_row(tasks[1], pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:30:00Z"))]
        rest = task_lane.attach_sessions(rows, tasks, self.scan(), False, 1e12, self.prices)
        # d2 names two tasks: other work, unattributed, never split into a task's startup context.
        self.assertEqual([row["cost"]["tokens"]["output"] for row in rows], [105, 425])
        self.assertEqual(rest["tokens"]["output"], 1240)
        self.assertEqual(rows[0]["cost"]["main"]["tokens"]["output"], 5)
        self.assertEqual(len(rows[0]["cost"]["spawns"]), 1)
        # Every scanned token lands exactly once: tasks plus the unattributed rest equal the scan.
        self.assertEqual(sum(row["cost"]["tokens"]["output"] for row in rows) + rest["tokens"]["output"],
                         self.session_cost.cost(self.scan(), self.prices)["tokens"]["output"])

    def test_readiness_work_after_merge_remains_attributed_to_the_task(self):
        (self.folder / "s1.jsonl").write_text(self.call(25, "after-merge", output_tokens=10) + "\n", encoding="utf-8")
        task = {**RetrospectiveTests.task, "id": "TK-001", "file": "tk-001-a.md"}
        row = task_lane.task_row(task, pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z"))
        row["ready_at"] = "2026-10-16T10:30:00+00:00"
        rest = task_lane.attach_sessions([row], [task], self.scan(), False, 1e12, self.prices)
        self.assertEqual(row["cost"]["tokens"]["output"], 10)
        self.assertEqual(rest["tokens"]["output"], 0)

    def test_a_task_session_counts_its_calls_before_start_once_across_a_resume(self):
        # Base branch named task/...; m1 runs before start; m2 streams twice; the resumed run adds m3.
        (self.folder / "s1.jsonl").write_text("\n".join([
            self.call(1, "m1", branch="task/trial", model="claude-sonnet-5-5", output_tokens=100),
            self.call(6, "m2", branch="task/tk-001-a", model="claude-sonnet-5-5", output_tokens=1),
            self.call(6, "m2", branch="task/tk-001-a", model="claude-sonnet-5-5", output_tokens=200),
            self.call(30, "m3", branch="task/tk-001-a", model="claude-sonnet-5-5", output_tokens=300)]) + "\n",
            encoding="utf-8")
        task = {**RetrospectiveTests.task, "id": "TK-001", "file": "tk-001-a.md",
                "requested_at": "2026-10-16T10:05:00+00:00"}
        row = task_lane.task_row(task, pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:40:00Z"))
        rest = task_lane.attach_sessions([row], [task], self.scan(), False, 1e12, self.prices)
        self.assertEqual((row["cost"]["main"]["tokens"]["output"], row["cost"]["usd"], rest["usd"]), (600, 0.006, 0))

    def test_codex_tokens_split_cached_and_written_input_and_name_their_branch(self):
        folder = self.home / "codex" / "2026" / "10" / "16"
        folder.mkdir(parents=True)
        lines = [{"timestamp": "2026-10-16T10:01:00Z", "type": "session_meta", "payload": {
                     "cwd": str(self.project.resolve()), "git": {"branch": "task/tk-004-export"},
                     "source": {"subagent": {"thread_spawn": {"agent_path": "team/code_reviewer"}}}}},
                 {"timestamp": "2026-10-16T10:01:00Z", "type": "event_msg",
                  "payload": {"type": "user_message", "message": "Check TK-004."}},
                 {"timestamp": "2026-10-16T10:01:00Z", "type": "turn_context",
                  "payload": {"model": "gpt-6.1-sol", "effort": "high"}},
                 {"timestamp": "2026-10-16T10:02:00Z", "type": "event_msg", "payload": {
                     "type": "token_count", "info": {"last_token_usage": {
                         "input_tokens": 1000, "cached_input_tokens": 600, "cache_write_input_tokens": 100,
                         "output_tokens": 50}}}}]
        (folder / "rollout-x.jsonl").write_text("\n".join(map(json.dumps, lines)) + "\n", encoding="utf-8")
        calls = [event for event in self.scan() if event["kind"] == "call"]
        self.assertEqual((calls[0]["task"], calls[0]["branch"], calls[0]["role"]),
                         ("TK-004", "task/tk-004-export", "code-reviewer"))
        cost = self.session_cost.cost(calls, self.prices)
        self.assertEqual(cost["tokens"], {"input": 300, "output": 50, "cache_read": 600, "cache_write": 100})
        self.assertEqual(cost["usd"], 0.0014)

    def costed_row(self, task_id="TK-001"):
        row = task_lane.task_row({**RetrospectiveTests.task, "id": task_id},
                                 pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z"))
        spawn = {"role": "code-reviewer", "model": "claude-opus-5-5", "purpose": "", "class": "review",
                 "effort": "xhigh", "startup_context": 64000, "startup_usd": 0.4,
                 "tokens": {"input": 1, "output": 1000, "cache_read": 1000000, "cache_write": 64000}, "usd": 1.0}
        row["cost"] = {"agents": 1, "models": {}, "efforts": {}, "tokens_by_role": {},
                       "tokens": {"input": 2, "output": 2000, "cache_read": 2000000, "cache_write": 64000},
                       "usd": 2.0, "main": {"tokens": {}, "usd": 1.0}, "spawns": [spawn],
                       "by_model": {"claude-opus-5-5": 2.0}, "by_class": {"main": 1.0, "review": 1.0},
                       "unpriced_models": []}
        row["waits"] = dict.fromkeys(task_lane.WAITS, 0.0)
        return row

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_the_report_prices_each_task_and_kind_and_ranks_cost_levers_with_time(self):
        data = task_lane.report([self.costed_row()], "latest 20")
        cost = data["targets"]["build"]["cost"]
        self.assertEqual((cost["usd_per_task"], cost["spawns_per_task"], cost["startup_context_per_spawn"],
                          cost["main_share"]), (2.0, 1.0, 64000, 0.5))
        self.assertEqual(cost["tokens_per_task"]["cache_read"], 2000000)
        ids = [item["id"] for item in data["proposals"]]
        for lever in ("STARTUP", "EFFORT", "MAIN"):
            self.assertIn(f"RETRO-COST-{lever}", ids)
        self.assertLessEqual(len(ids), task_lane.MAX_PROPOSALS)
        header = task_lane.export(data, "csv").splitlines()[0].split(",")
        self.assertTrue({"usd", "input_tokens", "cache_write_tokens", "cache_read_tokens", "spawns",
                         "startup_context", "main_usd"} <= set(header))
        self.assertIn("2.0", task_lane.export(data, "csv").splitlines()[1].split(","))

    def test_a_merged_task_keeps_its_cost_once_and_later_reports_read_it_first(self):
        root = self.project
        data = task_lane.report([self.costed_row()], "latest 20")
        task_lane.remember(root, data)
        task_lane.remember(root, data)
        kept = task_lane.kept_costs(root)
        self.assertEqual(list(kept), ["TK-001"])
        self.assertEqual(sum(1 for item in task_lane.history(root) if item.get("type") == "task_cost"), 1)
        self.assertEqual(task_lane.previous_run(root, "latest 20")["usd_per_task"], 2.0)
        tasks = root / task_lane.TASKS
        tasks.mkdir(parents=True)
        (tasks / "tk-001-a.md").write_text(TASK.replace("TK-004", "TK-001"), encoding="utf-8")
        args = SimpleNamespace(delivery=None, since=None, recent=20, deep=False, no_sessions=False)
        with mock.patch.object(task_lane, "pull_requests", return_value={"TK-001": {"number": 9}}), \
                mock.patch.object(task_lane, "task_records", tree_records), \
                mock.patch.object(task_lane, "pr_times", return_value=pr(
                    "2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")), \
                mock.patch.object(self.session_cost, "scan", side_effect=AssertionError("scanned")):
            again = task_lane.gather(root, args)
        self.assertEqual(again["per_task"][0]["cost"]["usd"], 2.0)
        # A rescan prices from the transcripts again and appends a newer row; the old one stays.
        fixed = {**again["per_task"][0]["cost"], "usd": 1.5}
        with mock.patch.object(task_lane, "pull_requests", return_value={"TK-001": {"number": 9}}), \
                mock.patch.object(task_lane, "task_records", tree_records), \
                mock.patch.object(task_lane, "pr_times", return_value=pr(
                    "2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")), \
                mock.patch.object(task_lane, "attach_sessions",
                                  side_effect=lambda rows, *rest: rows[0].update(cost=fixed, waits=None)) as scanned:
            rescanned = task_lane.gather(root, SimpleNamespace(**{**vars(args), "rescan": True}))
        scanned.assert_called_once()
        task_lane.remember(root, rescanned, rescan=True)
        self.assertEqual(sum(1 for item in task_lane.history(root) if item.get("type") == "task_cost"), 2)
        self.assertEqual(task_lane.kept_costs(root)["TK-001"]["cost"]["usd"], 1.5)


class RetrospectiveQualityTests(unittest.TestCase):
    """Review findings, CI first pass, escaped defects and story coverage beside time and cost."""

    def row(self, task_id, quality=None, **task):
        row = task_lane.task_row({**RetrospectiveTests.task, "id": task_id, **task},
                                 {**pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z"),
                                  "red": [], **(quality or {})})
        row["quality"].update(fixed_by=[], story_coverage=None)
        return row

    def test_merge_writes_the_review_counts_and_the_report_reads_them(self):
        root = GitHubCommandTests.project(self, "normal")
        calls = []
        head = GitHubCommandTests.head
        def run(*args, **kwargs):
            calls.append(args)
            if args == ("git", "rev-parse", "HEAD"):
                return head
            return json.dumps({"headRefOid": head, "baseRefName": "main", "state": "OPEN"}) if "view" in args else ""
        with mock.patch.object(task_lane, "run", side_effect=run), \
                mock.patch.object(task_lane, "review_problems", return_value=[]):
            task_lane.cmd_merge(SimpleNamespace(task="TK-004", pr="9", style="merge", detail=None,
                                                head=head, critical=0, non_critical=3, filed=2), root)
        note = next(call[-1] for call in calls if call[:3] == ("gh", "pr", "comment"))
        self.assertIn("findings: critical 0, non-critical 3, filed 2", note)
        comments = [{"body": note}, {"body": "unrelated findings: critical 9, non-critical 9, filed 9"}]
        self.assertEqual(task_lane.findings(comments), {"critical": 0, "non_critical": 3, "filed": 2})
        self.assertEqual(task_lane.findings([{"body": task_lane.REVIEW_MARK}])["critical"], None)

    def test_ci_first_pass_reads_the_first_head_and_counts_red_runs_before_the_merge(self):
        def run(sha, minute, conclusion, attempt=1):
            return {"head_sha": sha, "created_at": f"2026-10-16T10:{minute:02d}:00Z",
                    "conclusion": conclusion, "run_attempt": attempt}
        green = [run("a", 1, "success"), run("a", 1, "skipped")]
        self.assertEqual(task_lane.first_pass(green), {"ci_first_pass": True, "red_runs": 0})
        red = [run("a", 1, "failure"), run("b", 5, "success", attempt=2), run("b", 5, "cancelled"),
               run("c", 30, "failure")]
        self.assertEqual(task_lane.first_pass(red, "2026-10-16T10:20:00Z"),
                         {"ci_first_pass": False, "red_runs": 2})
        self.assertEqual(task_lane.first_pass([]), {"ci_first_pass": None, "red_runs": None})
        with mock.patch.object(task_lane, "run", return_value=json.dumps({"workflow_runs": green})) as called:
            self.assertEqual(task_lane.ci_runs(Path("."), "task/tk-004-x"), green)
        self.assertIn("branch=task%2Ftk-004-x", called.call_args[0][2])
        with mock.patch.object(task_lane, "run", side_effect=task_lane.Refused("no actions")):
            self.assertEqual(task_lane.ci_runs(Path("."), "task/tk-004-x"), [])

    def test_a_fixing_task_names_the_task_it_repairs_and_counts_as_its_escaped_defect(self):
        path = Path(tempfile.mkdtemp()) / "tk-009-fix.md"
        self.addCleanup(lambda: __import__("shutil").rmtree(path.parent))
        path.write_text(task_lane.task_text("TK-009", "Fix export", "Fix it.", "2026-10-16T10:00:00Z",
                                            "normal", kind="knowledge", fixes="TK-004"), encoding="utf-8")
        self.assertEqual(task_lane.task_props(path)["fixes"], "TK-004")
        self.assertEqual(task_lane.lint(path), [])
        path.write_text(path.read_text(encoding="utf-8").replace("fixes: TK-004", "fixes: 4"), encoding="utf-8")
        self.assertIn("fixes must be TK-<number>", task_lane.lint(path))
        with mock.patch.object(task_lane, "run", return_value=str(path.parent / ".git")), \
                self.assertRaisesRegex(task_lane.Refused, "--fixes must be"):
            task_lane.cmd_start(SimpleNamespace(delivery=None, at=None, request="r", request_file=None,
                                                title="t", epic=None, source_story=None, covers=None,
                                                fixes="TK-4"), path.parent)
        rows = [task_lane.task_row({**RetrospectiveTests.task, "id": "TK-004"}, None)]
        tasks = [{"id": "TK-004"}, {"id": "TK-009", "fixes": "TK-004"}, {"id": "TK-011", "fixes": "TK-004"}]
        task_lane.link_quality(rows, tasks, {})
        self.assertEqual(rows[0]["quality"]["fixed_by"], ["TK-009", "TK-011"])

    def test_story_coverage_is_the_share_of_criteria_the_delivered_tasks_cover(self):
        stories = task_lane.story_index(TaskLinkTests.project(self))
        tasks = [{"id": "TK-001", "status": "delivered", "source_story": "ST-012", "covers": ["1", "2"]},
                 {"id": "TK-002", "status": "active", "source_story": "ST-012", "covers": ["3"]}]
        rows = [task_lane.task_row({**RetrospectiveTests.task, **task}, None) for task in tasks]
        task_lane.link_quality(rows, tasks, stories)
        self.assertEqual([row["quality"]["story_coverage"] for row in rows], [0.67, 0.67])

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_the_report_shows_quality_per_task_and_kind_and_ranks_its_levers(self):
        rows = [self.row("TK-001", {"critical": 1, "non_critical": 2, "filed": 2, "ci_first_pass": False,
                                    "red_runs": 2}),
                self.row("TK-002", {"critical": 0, "non_critical": 0, "filed": 0, "ci_first_pass": True,
                                    "red_runs": 0}, source_story="ST-012")]
        rows[0]["quality"]["fixed_by"] = ["TK-005"]
        rows[1]["quality"]["story_coverage"] = 0.67
        data = task_lane.report(rows, "latest 20")
        spec = data["targets"]["build"]["quality"]
        self.assertEqual((spec["critical_per_task"], spec["filed_share"], spec["first_pass_rate"],
                          spec["red_runs_per_task"], spec["escaped_rate"], spec["coverage"]),
                         (0.5, 1.0, 0.5, 1.0, 0.5, {"ST-012": 0.67}))
        ids = [item["id"] for item in data["proposals"]]
        for lever in ("CI-FIRST-PASS", "ESCAPED", "CRITICAL"):
            self.assertIn(f"RETRO-QUALITY-{lever}", ids)
        lines = task_lane.export(data, "csv").splitlines()
        header = lines[0].split(",")
        self.assertTrue({"critical", "non_critical", "filed", "ci_first_pass", "red_runs", "fixes", "fixed_by",
                         "story_coverage"} <= set(header))
        self.assertEqual(lines[1].split(",")[header.index("fixed_by")], "TK-005")
        clean = task_lane.report([self.row("TK-003", {"critical": 0, "non_critical": 0, "filed": 0,
                                                      "ci_first_pass": True, "red_runs": 0})], "latest 20")
        self.assertFalse([item for item in clean["proposals"] if "QUALITY" in item["id"]])

    def test_quality_is_kept_once_compared_with_the_previous_run_and_recomputed_on_rescan(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        quality = {"critical": 1, "non_critical": 0, "filed": 0, "ci_first_pass": False, "red_runs": 3}
        data = task_lane.report([self.row("TK-001", quality)], "latest 20")
        task_lane.remember(root, data)
        task_lane.remember(root, data)
        self.assertEqual(task_lane.kept_quality(root)["TK-001"]["quality"], quality)
        self.assertEqual(sum(1 for item in task_lane.history(root) if item.get("type") == "task_quality"), 1)
        before = task_lane.previous_run(root, "latest 20")
        self.assertEqual(before["quality"]["first_pass_rate"], 0.0)
        tasks = root / task_lane.TASKS
        tasks.mkdir(parents=True)
        (tasks / "tk-001-a.md").write_text(TASK.replace("TK-004", "TK-001"), encoding="utf-8")
        later = (tasks / "tk-002-b.md")
        later.write_text(TASK.replace("TK-004", "TK-002").replace("rigor: normal\n", "rigor: normal\nfixes: TK-001\n"),
                         encoding="utf-8")
        args = SimpleNamespace(delivery=None, since=None, recent=20, deep=False, no_sessions=True)
        seen = []

        def times(_root, number, runs=True, causes=None):
            seen.append(runs)
            return pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")
        with mock.patch.object(task_lane, "pull_requests", return_value={"TK-001": {"number": 9}}), \
                mock.patch.object(task_lane, "task_records", tree_records), \
                mock.patch.object(task_lane, "pr_times", side_effect=times):
            again = task_lane.gather(root, args)
            self.assertEqual(seen, [False])
            self.assertEqual(again["per_task"][0]["quality"]["red_runs"], 3)
            self.assertEqual(again["per_task"][0]["quality"]["fixed_by"], ["TK-002"])
            task_lane.gather(root, SimpleNamespace(**{**vars(args), "rescan": True}))
        self.assertEqual(seen, [False, True])


class RetrospectiveReworkTests(unittest.TestCase):
    """Why a task did not finish in one pass: rework events, their causes, recurring groups,
    the lessons file, memory candidates and process levers."""

    causes = task_lane.rework_causes()

    def item(self, task_id, day, *texts, test="", kind="build"):
        events = [{"ref": f"run:{task_id}{n}", "source": "ci", "at": f"2026-10-{day:02d}T10:05:00Z",
                   "test": test, "text": text} for n, text in enumerate(texts)]
        return {"task_id": task_id, "kind": kind, "requested_at": f"2026-10-{day:02d}T10:00:00Z",
                "merged": f"2026-10-{day:02d}T10:20:00Z", "total": 20.0, "rework_auto": events}

    def groups(self, items, causes=None):
        causes = causes or self.causes
        window = task_lane.rework_window([task_lane.rework_record(item, causes) for item in items], causes)
        return {group["key"]: group for group in task_lane.rework_groups(window, causes)}

    def test_a_cause_takes_the_first_matching_hint_and_unmatched_text_stays_unclassified(self):
        cases = {"lint / ruff: would reformat app.py": "lint-format",
                 "tests: FAILED tests/test_a.py::test_x - AssertionError": "test-failure",
                 "tests: re-run attempt 2": "flaky-test", "Merge branch 'main' into task/tk-1-x": "merge-conflict",
                 "review: missing test for the empty month": "review-missing-test",
                 "fix: security review found the token kept in the checkout": "review-finding",
                 "no tests ran for the changed module": "test-impact", "wip": task_lane.UNCLASSIFIED,
                 "test: cover the empty month": "review-missing-test", "style: wrap long lines": "lint-format",
                 "code-reviewer changes: deadline unbounded": "review-finding"}
        for text, category in cases.items():
            self.assertEqual(task_lane.cause_of(text, self.causes), category, text)
        owners = {item["owner"] for item in self.causes["categories"]}
        self.assertEqual(owners, set(task_lane.REWORK_OWNERS))
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / ".agentrof").mkdir()
        (root / task_lane.PROJECT_REWORK).write_text(json.dumps({
            "recurring": {"min_events": 2}, "categories": [
                {"id": "seed-data", "owner": "project", "description": "seed", "rule": "r", "hints": ["\\bwip\\b"]},
                {"id": "lint-format", "owner": "process", "description": "d", "rule": "r", "hints": ["ruff"]}]}),
            encoding="utf-8")
        own = task_lane.rework_causes(root)
        self.assertEqual(own["recurring"], {**self.causes["recurring"], "min_events": 2})
        self.assertEqual(task_lane.cause_of("wip", own), "seed-data")
        self.assertEqual(next(item for item in own["categories"] if item["id"] == "lint-format")["owner"], "process")
        (root / task_lane.PROJECT_REWORK).write_text(json.dumps({"categories": [
            {"id": "x", "owner": "someone", "hints": []}]}), encoding="utf-8")
        with self.assertRaises(task_lane.Refused):
            task_lane.rework_causes(root)

    def test_a_failed_log_gives_its_job_test_and_error_line_without_secret_lines(self):
        log = ("tests\tRun tests\t2026-10-16T10:00:00.1Z export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123\n"
               "tests\tRun tests\t2026-10-16T10:00:01.1Z key Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb\n"
               "tests\tRun tests\t2026-10-16T10:00:02.1Z FAIL: test_empty (tests.test_export.ExportTests.test_empty)\n"
               "tests\tRun tests\t2026-10-16T10:00:03.1Z AssertionError: 0 != 1\n"
               "tests\tRun tests\t2026-10-16T10:00:04.1Z ##[error]Process completed with exit code 1.")
        found = task_lane.failure(log, 240)
        self.assertEqual(found, {"job": "tests", "test": "tests.test_export.ExportTests.test_empty",
                                 "text": "AssertionError: 0 != 1"})
        self.assertEqual(task_lane.failure("j\ts\t2026-10-16T10:00:00Z FAILED tests/test_a.py::test_x - x", 240)["test"],
                         "tests/test_a.py::test_x")
        self.assertEqual(task_lane.failure("j\ts\t2026-10-16T10:00:00Z ##[error]Process completed with exit code 2.",
                                           240)["text"], "##[error]Process completed with exit code 2.")
        self.assertEqual(task_lane.scrub("password: hunter2\nfine " + "x" * 300, 20), "fine " + "x" * 15)

    def test_red_runs_and_re_runs_are_events_and_only_the_first_red_runs_read_their_log(self):
        def run(number, conclusion, attempt=1):
            return {"id": number, "name": "tests", "created_at": f"2026-10-16T10:{number:02d}:00Z",
                    "updated_at": f"2026-10-16T10:{number + 1:02d}:00Z", "conclusion": conclusion,
                    "run_attempt": attempt}
        runs = [run(n, "failure") for n in range(1, 6)] + [run(7, "success", attempt=2), run(30, "failure")]
        calls = []
        log = "tests\tRun\t2026-10-16T10:00:00Z FAILED tests/test_a.py::test_x - AssertionError"
        with mock.patch.object(task_lane, "run", side_effect=lambda *a, **k: calls.append(a) or log):
            events = task_lane.red_events(Path("."), runs, "2026-10-16T10:20:00Z", self.causes["ci_logs"])
        self.assertEqual(len(calls), self.causes["ci_logs"]["max_calls_per_task"])
        self.assertEqual([event["ref"] for event in events],
                         ["run:1", "run:2", "run:3", "run:4", "run:5", "run:7:1"])
        self.assertEqual((events[0]["test"], events[0]["at"]), ("tests/test_a.py::test_x", "2026-10-16T10:02:00Z"))
        self.assertEqual((events[4]["test"], events[4]["text"]), ("", "tests"))
        self.assertEqual(task_lane.cause_of(events[5]["text"], self.causes), "flaky-test")
        view = {"createdAt": "2026-10-16T10:00:00Z", "mergedAt": "2026-10-16T10:20:00Z", "headRefName": "task/tk-4-x",
                "comments": [{"body": task_lane.REVIEW_MARK + "\n\nmissing test for an empty month\n\n"
                                      "findings: critical 1, non-critical 0, filed 0"}],
                "commits": [{"oid": "a" * 40, "committedDate": "2026-10-16T09:59:00Z", "messageHeadline": "docs"},
                            {"oid": "b" * 40, "committedDate": "2026-10-16T10:09:00Z", "messageHeadline": "fix"}]}

        def answer(*args, **_kwargs):
            return json.dumps(view) if args[1] == "pr" else json.dumps({"workflow_runs": runs}) \
                if args[1] == "api" else log
        with mock.patch.object(task_lane, "run", side_effect=answer):
            seen = task_lane.pr_times(Path("."), 9, causes=self.causes)
        self.assertEqual(seen["review_note"], "missing test for an empty month")
        self.assertEqual(seen["revisions"], [{"sha": "bbbbbbb", "at": "2026-10-16T10:09:00Z", "subject": "fix"}])
        self.assertEqual(len(seen["red"]), 6)

    def test_a_pr_opened_on_a_local_proof_is_green_when_it_opens_and_its_ci_step_is_the_proofs_minutes(self):
        record = {"head": "a" * 40, "commands": [
            {"command": "make test", "seconds": 110.2, "finished_at": "2026-10-16T09:58:00+00:00"},
            {"command": "python3 gate.pyz check", "seconds": 9.8, "finished_at": "2026-10-16T09:59:00+00:00"}]}
        view = {"createdAt": "2026-10-16T10:00:00Z", "mergedAt": "2026-10-16T10:20:00Z", "headRefName": "task/tk-4-x",
                "statusCheckRollup": [], "commits": [],
                "comments": [{"createdAt": "2026-10-16T10:12:00Z", "body": task_lane.REVIEW_MARK}],
                "body": "Summary\n" + task_lane.proof_section(record) + "\n## Live evidence\n\nnone\n"}
        with mock.patch.object(task_lane, "run", return_value=json.dumps(view)):
            seen = task_lane.pr_times(Path("."), 9, runs=False)
        self.assertEqual((seen["ci_green"], seen["proof_minutes"]), ("2026-10-16T10:00:00Z", 2.0))
        row = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-004"}, seen)
        self.assertEqual((row["steps"]["ci"], row["steps"]["merge_wait"]), (2.0, 8.0))
        # A PR whose checks ran keeps its CI times, whatever its body says.
        checked = {**view, "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS",
                                                  "startedAt": "2026-10-16T10:00:00Z",
                                                  "completedAt": "2026-10-16T10:05:00Z"}]}
        with mock.patch.object(task_lane, "run", return_value=json.dumps(checked)):
            seen = task_lane.pr_times(Path("."), 9, runs=False)
        self.assertEqual(seen["ci_green"], "2026-10-16T10:05:00Z")
        self.assertNotIn("proof_minutes", seen)
        # A body with no proof section leaves the CI step unknown, as before.
        with mock.patch.object(task_lane, "run", return_value=json.dumps({**view, "body": "Summary"})):
            self.assertIsNone(task_lane.pr_times(Path("."), 9, runs=False)["ci_green"])

    def test_only_unexplained_commits_after_the_pr_opened_are_revision_events(self):
        task = {**RetrospectiveTests.task, "role_checks": [
            {"at": "2026-10-16T10:04:00Z", "role": "software-architect", "verdict": "changes", "note": "split the module"},
            {"at": "2026-10-16T10:06:00Z", "role": "software-architect", "verdict": "go", "note": "-"}]}
        red = [{"ref": "run:1", "source": "ci", "at": "2026-10-16T10:12:00Z", "test": "", "text": "lint: ruff"}]
        commits = [{"sha": "aaaaaaa", "at": "2026-10-16T10:13:00Z", "subject": "fix lint"},
                   {"sha": "bbbbbbb", "at": "2026-10-16T10:14:00Z", "subject": "address review"},
                   {"sha": "ccccccc", "at": "2026-10-16T10:15:00Z", "subject": "Merge branch 'main'"},
                   {"sha": "ddddddd", "at": "2026-10-16T10:16:00Z", "subject": "tweak"}]
        events = task_lane.auto_events(task, {**pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z"),
                                              "red": red, "critical": 1, "review_note": "", "revisions": commits})
        self.assertEqual([event["ref"] for event in events],
                         ["role:software-architect:2026-10-16T10:04:00Z", "run:1", "review:critical:1",
                          "commit:ccccccc", "commit:ddddddd"])
        self.assertIsNone(task_lane.auto_events(task, pr("2026-10-16T10:10:00Z", None, None, None)))
        self.assertEqual(len(task_lane.auto_events(task, None)), 1)

    def test_a_cause_recurs_at_its_threshold_and_a_test_failing_in_two_tasks_is_a_repeat_offender(self):
        groups = self.groups([self.item("TK-001", 1, "ruff failed", "ruff again"), self.item("TK-002", 2, "ruff")])
        self.assertEqual((groups["lint-format"]["status"], groups["lint-format"]["events"],
                          groups["lint-format"]["tasks"]), ("recurring", 3, ["TK-001", "TK-002"]))
        once = self.groups([self.item("TK-001", 1, "ruff", "ruff", "ruff")])
        self.assertEqual(once["lint-format"]["status"], "rare")
        test = "tests/test_a.py::test_x"
        groups = self.groups([self.item("TK-001", 1, "FAILED x", test=test), self.item("TK-002", 2, "FAILED x", test=test),
                              self.item("TK-003", 3, "wip")])
        offender = groups[f"test:{test}"]
        self.assertEqual((offender["status"], offender["repeat_offender"], offender["owner"]),
                         ("recurring", True, "project"))
        self.assertIn(test, offender["rule"])
        self.assertEqual(groups["test-failure"]["status"], "rare")
        self.assertEqual(groups[task_lane.UNCLASSIFIED]["status"], task_lane.UNCLASSIFIED)
        self.assertEqual(offender["minutes"], {"TK-001": 15.0, "TK-002": 15.0})
        narrow = {**self.causes, "recurring": {**self.causes["recurring"], "window_tasks": 1}}
        self.assertEqual(list(self.groups([self.item("TK-001", 1, "ruff"), self.item("TK-002", 2, "wip")], narrow)),
                         [task_lane.UNCLASSIFIED])

    def test_a_recorded_cause_classifies_its_event_or_adds_one_and_lints(self):
        root = GitHubCommandTests.project(self, "normal")
        args = SimpleNamespace(task="TK-004", category="spec-gap", note="criterion 2 had two readings",
                               ref="commit:abc1234", at="2026-10-16T10:30:00Z")
        task_lane.cmd_rework(args, root)
        with mock.patch.object(task_lane, "project_root", return_value=root), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(task_lane.main(["rework", "--task", "TK-004", "--category", "environment",
                                             "--note", "runner lost", "--at", "2026-10-16T10:30:00Z"]), 0)
        path = task_lane.task_path(root, "TK-004")
        self.assertEqual(task_lane.lint(path), [])
        notes = task_lane.rework_notes(path)
        self.assertEqual([(note["category"], note["ref"]) for note in notes],
                         [("spec-gap", "commit:abc1234"), ("environment", "")])
        record = task_lane.rework_record({"task_id": "TK-004", "merged": "2026-10-16T11:00:00Z", "rework_notes": notes,
                                          "rework_auto": [{"ref": "commit:abc1234", "source": "revision",
                                                           "at": "2026-10-16T10:40:00Z", "test": "", "text": "wip"}]},
                                         self.causes)
        self.assertEqual([(event["category"], event["source"]) for event in record["events"]],
                         [("spec-gap", "revision"), ("environment", "note")])
        self.assertEqual(record["minutes"], 30.0)
        for bad in ({"category": "nope"}, {"ref": "abc"}, {"note": "token: x"}):
            with self.assertRaises(task_lane.Refused):
                task_lane.cmd_rework(SimpleNamespace(**{**vars(args), **bad}), root)
        self.assertNotIn(args.note, task_lane.task_body(path))

    def test_the_lessons_file_updates_in_place_turns_quiet_entries_fixed_and_caps_its_entries(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        causes = {**self.causes, "lessons": {**self.causes["lessons"], "max_open": 2, "fixed_after_tasks": 2}}
        items = [self.item("TK-001", 1, "ruff", "mypy", "ambiguous"), self.item("TK-002", 2, "ruff", "mypy", "ambiguous"),
                 self.item("TK-003", 3, "ruff", "mypy", "ambiguous", "ruff")]

        def write(chosen):
            records = [task_lane.rework_record(item, causes) for item in chosen]
            return task_lane.write_lessons(root, task_lane.rework_report(records, [], causes), causes)
        first = write(items)
        self.assertEqual((first["added"], first["over_cap"]), (["lint-format", "spec-gap"], ["type-error"]))
        path = root / causes["lessons"]["path"]
        text = path.read_text(encoding="utf-8")
        self.assertIn("4 events in 3 tasks", text)
        path.write_text(text.replace("- Rule: Run the project's formatter", "- Rule: Run make fmt, the formatter"),
                        encoding="utf-8")
        again = write(items + [self.item("TK-004", 4, "ruff")])
        text = path.read_text(encoding="utf-8")
        self.assertEqual((again["added"], text.count("### lint-format")), ([], 1))
        self.assertIn("- Rule: Run make fmt, the formatter", text)
        self.assertIn("5 events in 4 tasks", text)
        quiet = write(items + [self.item(f"TK-01{n}", 10 + n, "wip") for n in range(3)])
        self.assertEqual(sorted(quiet["fixed"]), [])
        quiet = write([self.item(f"TK-01{n}", 10 + n, "wip") for n in range(3)])
        self.assertEqual(sorted(quiet["fixed"]), ["lint-format", "spec-gap"])
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("Status: fixed."), 2)
        self.assertNotIn("Promote: memory", text)
        self.assertTrue(text.startswith("# Lessons learnt\n"))

    def test_at_most_five_project_lessons_are_memory_candidates_and_process_causes_become_levers(self):
        project = [item["id"] for item in self.causes["categories"] if item["owner"] == "project"]
        texts = {"environment": "rate limit", "flaky-test": "flaky", "lint-format": "ruff", "type-error": "mypy",
                 "docs-gate": "vault", "review-missing-test": "untested", "review-finding": "security review",
                 "test-failure": "FAILED"}
        self.assertEqual(sorted(texts), sorted(project))
        items = [self.item(f"TK-00{n}", n, *texts.values(), "ambiguous", "ambiguous") for n in range(1, 4)]
        records = [task_lane.rework_record(item, self.causes) for item in items]
        rework = task_lane.rework_report(records, [], self.causes)
        self.assertEqual(len(rework["promote"]), self.causes["promote_max"])
        self.assertTrue(all(item["key"] in project for item in rework["promote"]))
        self.assertIn("(rework seen 3 times in 3 tasks)", rework["promote"][0]["rule"])
        levers = task_lane.rework_proposals(records, self.causes)
        self.assertEqual([item["id"] for item in levers], ["RETRO-REWORK-SPEC-GAP"])
        lever = levers[0]
        self.assertEqual((lever["cause_owner"], lever["weight"]), ("process", 1.0))
        self.assertIn("6 rework events in 3 of 3 tasks", lever["evidence"])
        self.assertIn("up to 15.0 rework min per affected task", lever["expected_gain"])
        self.assertTrue(lever["verify"])

    @mock.patch.object(task_lane, "MIN_PROPOSAL_TASKS", 1)
    def test_the_report_shows_rework_per_task_and_kind_keeps_it_once_and_groups_across_history(self):
        def row(task_id, minute, red):
            return task_lane.task_row({**RetrospectiveTests.task, "id": task_id},
                                      {**pr("2026-10-16T10:10:00Z", None, None, f"2026-10-16T10:{minute}:00Z"),
                                       "red": red})
        red = [{"ref": "run:1", "source": "ci", "at": "2026-10-16T10:12:00Z", "test": "", "text": "ambiguous"}]
        older = {"type": "task_rework", "task_id": "TK-001", "kind": "build", "requested_at": "2026-10-10T10:00:00Z",
                 "merged": "2026-10-10T10:30:00Z", "total": 30.0, "events": [dict(red[0], at="2026-10-10T10:12:00Z")] * 2}
        rows = [row("TK-004", 20, red), row("TK-005", 30, [])]
        data = task_lane.report(rows, "latest 20", extra=[{**older, "rework_auto": older["events"], "rework_notes": []}])
        self.assertEqual(data["targets"]["build"]["rework"],
                         {"tasks": 2, "one_pass": 1, "one_pass_rate": 0.5, "events": 1, "minutes": 8.0,
                          "minutes_share": 0.16})
        self.assertEqual(data["rework"]["groups"][0]["status"], "recurring")
        self.assertIn("RETRO-REWORK-SPEC-GAP", [item["id"] for item in data["proposals"]])
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        task_lane.remember(root, data)
        task_lane.remember(root, data)
        kept = task_lane.kept_rework(root)
        self.assertEqual(sorted(kept), ["TK-004", "TK-005"])
        self.assertEqual(kept["TK-004"]["events"], red)
        self.assertNotIn("category", kept["TK-004"]["events"][0])
        self.assertEqual(sum(1 for item in task_lane.history(root) if item.get("type") == "task_rework"), 2)
        task_lane.remember(root, data, rescan=True)
        self.assertEqual(sum(1 for item in task_lane.history(root) if item.get("type") == "task_rework"), 4)


class RetrospectiveHonestyTests(unittest.TestCase):
    """Bounded figures, rework that counts only real rework, steps that add up, proposals backed
    by enough tasks, and durations with a verdict and a baseline."""

    def row(self, task_id, merged="2026-10-16T10:20:00Z", kind="build", size="s", **pr_extra):
        return task_lane.task_row({**RetrospectiveTests.task, "id": task_id, "task_kind": kind, "size": size},
                                  {**pr("2026-10-16T10:10:00Z", "2026-10-16T10:15:00Z", "2026-10-16T10:18:00Z",
                                        merged), **pr_extra})

    def test_merge_refuses_a_filed_count_above_the_found_count(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(TASK, encoding="utf-8")
        args = SimpleNamespace(task="TK-004", pr="9", style="merge", detail=None, head="a" * 40,
                               critical=0, non_critical=2, filed=712)
        with mock.patch.object(task_lane, "run") as run, self.assertRaisesRegex(task_lane.Refused, "finding counts"):
            task_lane.cmd_merge(args, root)
        run.assert_not_called()

    def test_a_share_never_exceeds_the_whole(self):
        row = self.row("TK-001", critical=0, non_critical=1, filed=9)
        self.assertEqual(task_lane.quality_summary([row])["filed_share"], 1.0)
        self.assertEqual(task_lane.share(30, 20), 1.0)

    def test_merges_from_main_the_docs_commit_and_the_first_feature_commit_are_not_rework(self):
        def commit(sha, minute, subject):
            return {"oid": sha * 7, "committedDate": f"2026-10-16T10:{minute:02d}:00Z", "messageHeadline": subject}
        commits = [commit("a", 9, "docs: deliver task TK-004"), commit("b", 11, "Add the export"),
                   commit("c", 12, "Merge branch 'main' into task/tk-004-export"),
                   commit("d", 13, "Merge remote-tracking branch 'origin/main' into task/tk-004-export"),
                   commit("e", 14, "test: cover the empty month")]
        self.assertEqual([item["sha"] for item in task_lane.revisions(commits, "2026-10-16T10:10:00Z")],
                         ["eeeeeee"])
        self.assertEqual(task_lane.revisions(commits[1:2], "2026-10-16T10:10:00Z"), [])

    def test_steps_add_up_and_a_missing_step_says_why(self):
        row = task_lane.task_row(RetrospectiveTests.task, pr("2026-10-16T10:10:00Z", None, "2026-10-16T10:18:00Z",
                                                             "2026-10-16T10:20:00Z"))
        self.assertEqual((row["steps"]["ci"], row["steps"]["merge_wait"]), (None, None))
        self.assertEqual(row["missing"], {"ci": "CI not green", "merge_wait": "CI not green"})
        self.assertEqual(row["unaccounted"], 2.0)
        full = self.row("TK-002")
        self.assertEqual((full["unaccounted"], full["missing"]), (0.0, {}))

    def test_a_kind_needs_five_merged_tasks_before_a_proposal(self):
        four = [self.row(f"TK-00{n}", merged="2026-10-16T11:00:00Z") for n in range(1, 5)]
        data = task_lane.report(four, "latest 20")
        self.assertEqual(data["proposals"], [])
        self.assertTrue(task_lane.report(four + [self.row("TK-005", merged="2026-10-16T11:00:00Z")],
                                         "latest 20")["proposals"])

    def test_one_proposal_per_cause_across_kinds(self):
        rows = [self.row(f"TK-0{n:02d}", merged="2026-10-16T11:00:00Z", kind=kind)
                for n, kind in enumerate(["build"] * 5 + ["knowledge"] * 5)]
        ids = [item["id"] for item in task_lane.report(rows, "latest 20")["proposals"]]
        self.assertEqual(len(ids), len(set(ids)))
        review = next(item for item in task_lane.report(rows, "latest 20")["proposals"] if item["id"] == "RETRO-REVIEW")
        self.assertEqual(sorted(review["kinds"]), ["build", "knowledge"])

    def test_size_targets_are_calibrated_from_history(self):
        history = [{"task_id": f"TK-1{n}", "kind": "build", "size": "m", "total": 10.0 + n} for n in range(4)]
        goal = task_lane.targets([self.row("TK-001", size="m")], [], history)["build"]["calibrated"]
        self.assertEqual(goal["m"], {"tasks": 5, "median": 12.0})
        self.assertEqual(goal["s"], {"tasks": 0, "median": None})

    def test_durations_carry_steps_a_verdict_a_baseline_and_a_cost_method_flag(self):
        data = task_lane.report([self.row("TK-001")], "latest 20")
        text = task_lane.render(data)
        self.assertIn("Verdict: average 20.0 vs target 5 min (missed); baseline none", text)
        self.assertIn("Steps (average min): define 3.0 / write 7.0 / ci 5.0 / review 8.0 / merge_wait 2.0"
                      " / unaccounted 0.0", text)
        self.assertIn("- TK-001: 20.0 min vs s target 5 (missed); define 3.0 / write 7.0", text)
        before = {"at": "2026-10-10T00:00:00Z", "merged": 3, "average": 12.0, "median": 12.0, "max": 14.0,
                  "cost_method": "aaa"}
        again = task_lane.render({**task_lane.report([self.row("TK-001")], "latest 20", before), "cost_method": "bbb"})
        self.assertIn("baseline 12.0 min average at the previous retrospective (2026-10-10)", again)
        self.assertIn("Cost method changed since the previous retrospective (aaa -> bbb)", again)
        costed = task_lane.render(task_lane.report([RetrospectiveDollarTests.costed_row(self)], "latest 20"))
        self.assertIn("Cost build: $2.00 over 1 tasks", costed)


class TaskTriggerTests(unittest.TestCase):
    def project(self, task: str = TASK) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(task, encoding="utf-8")
        return root

    def test_the_risk_flags_are_the_class_map_flags(self):
        declared = json.loads((ROOT / "tools/data/models.json").read_text(encoding="utf-8"))["risk_flags"]
        flags = [rule["flag"] for rule in task_lane.task_triggers()["risk_flags"]]
        self.assertEqual(sorted(flags), sorted(declared))

    def test_paths_and_words_make_method_skills_mandatory_and_propose_risks(self):
        triggers = task_lane.task_triggers()
        text = "Add a migration for the orders table"
        files = {"deploy/Dockerfile", "web/src/Orders.tsx", "db/migrations/0003_orders.sql"}
        self.assertEqual(task_lane.triggered(triggers["method_skills"], "skill", text, files),
                         ["data-design", "experience-design", "verify"])
        self.assertEqual(task_lane.triggered(triggers["risk_flags"], "flag", text, files), ["migration"])
        self.assertEqual(task_lane.triggered(triggers["method_skills"], "skill", "Fix a Go handler", {"cmd/main.go"}), [])

    def test_a_project_rule_naming_a_renamed_skill_maps_to_the_new_name(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "workspace").mkdir()
        (root / "workspace/config.json").write_text(json.dumps({"task_triggers": {"method_skills": [
            {"skill": "docker-compose", "path_globs": ["*infra/*"]}]}}), encoding="utf-8")
        triggers = task_lane.task_triggers(root)
        self.assertEqual(task_lane.triggered(triggers["method_skills"], "skill", "", {"infra/x"}),
                         ["verify"])
        self.assertEqual(task_lane.applied_skills({"applied_skills": "docker-compose"}), ["verify"])

    def test_a_changed_source_is_reported_at_review(self):
        root = self.project()
        doc = root / "workspace/docs/solution-design/decision.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("v1\n", encoding="utf-8")
        recorded = task_lane.hashed_sources(root, ["workspace/docs/solution-design/decision.md"])
        self.assertEqual(task_lane.changed_sources(root, recorded), [])
        doc.write_text("v2\n", encoding="utf-8")
        self.assertEqual([item["path"] for item in task_lane.changed_sources(root, recorded)],
                         ["workspace/docs/solution-design/decision.md"])

    def test_review_inputs_route_reviewers_and_report_a_missing_skill(self):
        task = TASK.replace("rigor: normal\n", "rigor: normal\nrisks: auth\napplied_skills: data-design\n")
        root = self.project(task)
        self.assertEqual(task_lane.lint(root / task_lane.TASKS / "tk-004-export-invoices.md"), [])
        changed = {"api/models/invoice.py", "workspace/docs/business-analysis/rules.md"}
        with mock.patch.object(task_lane, "changed_files", return_value=changed), \
                mock.patch.object(task_lane, "run", return_value="diff --git a/api/models.py"):
            result = task_lane.cmd_review_inputs(SimpleNamespace(task="TK-004", base="main"), root)
        self.assertEqual(result["reviewers"], ["reviewer", "critical-reviewer"])
        self.assertEqual((root / result["diff_file"]).read_text(encoding="utf-8"), "diff --git a/api/models.py")
        self.assertEqual(result["missing_skills"], ["data-design"])

    def test_an_unknown_risk_flag_fails_lint(self):
        path = self.project(TASK.replace("rigor: normal\n", "rigor: normal\nrisks: weather\n"))
        self.assertTrue(any(error.startswith("risks must be among")
                            for error in task_lane.lint(path / task_lane.TASKS / "tk-004-export-invoices.md")))


if __name__ == "__main__":
    unittest.main()
