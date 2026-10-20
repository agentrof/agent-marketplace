"""Fast lane: one task, one branch and one
pull request carry a change; the task holds the request and approval times,
GitHub the rest, and the retrospective derives steps and proposals from them."""

from __future__ import annotations

import contextlib
import io
import json
import re
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
import process_policy  # noqa: E402
import task_lane  # noqa: E402

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

## Tests

- tests/test_export.py

<!-- sec: nav -->
## Navigation

- [[maps/backlog|Backlog]]
"""


def run_policy(docs: Path, command: str, *argv: str) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main([command, "--docs", str(docs), *argv])
    if code:
        raise AssertionError(output.getvalue())


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
        bad = (TASK.replace("3. An empty month yields a header-only file.\n", "")
               .replace("size: s", "size: xl").replace("rigor: normal", "rigor: extreme")
               .replace("requested_at: 2026-10-16T10:00:00+00:00\n", ""))
        errors = task_lane.lint(self.write(bad))
        self.assertTrue(any("found 2" in error for error in errors))
        self.assertTrue(any(error.startswith("size") for error in errors))
        self.assertTrue(any(error.startswith("rigor") for error in errors))
        self.assertIn("requested_at is required", errors)

    def test_nine_criteria_are_two_tasks(self):
        many = "\n".join(f"{n}. Criterion {n}." for n in range(1, 10))
        text = TASK.split("## Acceptance Criteria")[0] + "## Acceptance Criteria\n\n" + many \
            + "\n\n## Tests" + TASK.split("## Tests")[1]
        self.assertTrue(any("found 9" in error for error in task_lane.lint(self.write(text))))

    def test_the_skeleton_is_valid_once_filled(self):
        text = task_lane.task_text("TK-007", "Rotate keys", "Rotate the signing keys.",
                                    "2026-10-16T10:00:00+00:00", "DLV-009", "high")
        text = text.replace("1. \n2. \n3. \n", "1. One.\n2. Two.\n3. Three.\n").replace("- \n", "- t\n")
        path = self.write(text)
        self.assertEqual(task_lane.lint(path), [])
        props = task_lane.task_props(path)
        self.assertEqual((props["rigor"], props["delivery"]), ("high", "DLV-009"))

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
        self.assertEqual(task_lane.proposed_rigor("Delete stale tenant data")[0], "high")
        self.assertEqual(task_lane.proposed_rigor("Rotate the API key secrets")[0], "high")
        self.assertEqual(task_lane.proposed_rigor("Add a CSV export button")[0], "normal")

    def test_authority_is_a_business_term_not_an_auth_risk(self):
        self.assertEqual(task_lane.proposed_rigor("Show the approving authority")[0], "normal")
        self.assertEqual(task_lane.proposed_rigor("Add authentication to the export")[0], "high")
        self.assertEqual(task_lane.proposed_rigor("Authorize the reviewer role")[0], "high")

    def test_ids_follow_tasks_and_remote_task_branches(self):
        names = ["workspace/docs/backlog/tasks/tk-003-a.md", "origin/task/tk-011-b"]
        self.assertEqual(task_lane.next_id(names), "TK-012")
        self.assertEqual(task_lane.next_id([]), "TK-001")


def pr(opened, ci, review, merged, checks=None):
    return {"pr": "9", "pr_opened": opened, "ci_green": ci, "review": review,
            "merged": merged, "checks": checks or {}}


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

    def test_an_open_task_has_no_total_and_a_proposal_names_it(self):
        data = task_lane.report([task_lane.task_row(self.task, None)], "latest 20")
        self.assertIsNone(data["per_task"][0]["total"])
        self.assertEqual(data["request_to_merge_minutes"]["count"], 0)
        self.assertIn("RETRO-BUILD-OPEN-TASKS", [item["id"] for item in data["proposals"]])

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
        self.assertEqual(ids[0], "RETRO-BUILD-CI")
        self.assertIn("RETRO-BUILD-CHECK-TESTS", ids)
        self.assertLessEqual(len(ids), 5)
        self.assertEqual(data["slowest"][0]["task_id"], "TK-003")
        text = task_lane.render(data)
        self.assertIn("1. [RETRO-BUILD-CI]", text)
        self.assertIn("Request to merge: average 23.0, median 23.0, max 24.0 min", text)
        self.assertEqual(task_lane.export(data, "csv").splitlines()[0].split(",")[:3],
                         ["task_id", "title", "size"])

    def test_scope_selects_a_delivery_a_window_or_the_latest(self):
        tasks = [{"id": f"TK-00{n}", "requested_at": f"2026-10-{10 + n}T10:00:00+00:00",
                  "delivery": "DLV-007" if n % 2 else ""} for n in range(1, 6)]
        at = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.assertEqual([task["id"] for task in task_lane.select(tasks, delivery="DLV-007")],
                         ["TK-001", "TK-003", "TK-005"])
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

    def test_the_retrospective_note_is_vault_shaped(self):
        data = task_lane.report([task_lane.task_row(self.task, None)], "DLV-007")
        note = task_lane.retrospective_note(data, "DLV-007")
        self.assertIn("type: retrospective", note)
        self.assertIn("<!-- sec: nav -->", note)


class GitHubCommandTests(unittest.TestCase):
    def project(self, rigor: str) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        tasks = root / task_lane.TASKS
        tasks.mkdir(parents=True)
        (tasks / "tk-004-export-invoices.md").write_text(
            TASK.replace("rigor: normal", f"rigor: {rigor}"), encoding="utf-8")
        return root

    def test_normal_rigor_turns_on_auto_merge(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=lambda *a, **k: calls.append(a) or ""):
            result = task_lane.cmd_merge(SimpleNamespace(task="TK-004", pr="9", style="merge",
                                                          detail=None), self.project("normal"))
        self.assertTrue(result["auto_merge"])
        self.assertIn(("gh", "pr", "merge", "9", "--auto", "--merge"), calls)
        self.assertIn(task_lane.REVIEW_MARK, calls[0][-1])

    def test_green_checks_merge_at_once_when_auto_merge_is_refused(self):
        calls = []

        def run(*args, **kwargs):
            calls.append(args)
            if "--auto" in args:
                raise task_lane.Refused("gh pr merge failed: Pull request is in clean status")
            return ""
        with mock.patch.object(task_lane, "run", side_effect=run):
            result = task_lane.cmd_merge(SimpleNamespace(task="TK-004", pr="9", style="merge",
                                                          detail=None), self.project("normal"))
        self.assertTrue(result["merged"])
        self.assertEqual(calls[-1], ("gh", "pr", "merge", "9", "--merge"))

    def test_high_rigor_leaves_the_merge_to_the_owner(self):
        calls = []
        with mock.patch.object(task_lane, "run", side_effect=lambda *a, **k: calls.append(a) or ""):
            result = task_lane.cmd_merge(SimpleNamespace(task="TK-004", pr="9", style="merge",
                                                          detail=None), self.project("high"))
        self.assertFalse(result["auto_merge"])
        self.assertFalse(any(call[:3] == ("gh", "pr", "merge") for call in calls))

    def test_issues_open_only_for_named_proposals_in_the_origin_repository(self):
        root = self.project("normal")
        calls = []

        def fake(*args, **_kwargs):
            calls.append(args)
            if args[:3] == ("git", "remote", "get-url"):
                return "git@github.com:acme/shop.git"
            if args[:3] == ("gh", "pr", "list"):
                return json.dumps([{"number": 9, "headRefName": "task/tk-004-export-invoices"}])
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps({"createdAt": "2026-10-16T10:10:00Z",
                                   "mergedAt": "2026-10-16T10:30:00Z", "comments": [],
                                   "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS",
                                                          "startedAt": "2026-10-16T10:10:00Z",
                                                          "completedAt": "2026-10-16T10:25:00Z"}]})
            if args[:3] == ("gh", "issue", "create"):
                return "https://github.com/acme/shop/issues/1"
            return ""

        args = SimpleNamespace(ids="1", delivery=None, since=None, recent=20, no_sessions=True)
        with mock.patch.object(task_lane, "run", side_effect=fake):
            result = task_lane.cmd_file_issues(args, root)
            with self.assertRaises(task_lane.Refused):
                task_lane.cmd_file_issues(SimpleNamespace(**{**vars(args), "ids": "RETRO-NOPE"}), root)
        created = [call for call in calls if call[:3] == ("gh", "issue", "create")]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][3:5], ("--repo", "acme/shop"))
        self.assertIn("Evidence:", created[0][-1])
        self.assertEqual(result["repo"], "acme/shop")


class RoleRoutingTests(unittest.TestCase):
    def project(self, task: str = TASK) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(task, encoding="utf-8")
        return root

    def roles(self, text: str, kind: str = "build", size: str = "s", root: Path | None = None) -> dict:
        return {item["role"]: item for item in task_lane.route(task_lane.role_rules(root), text, kind, size,
                                                               task_lane.named_files(text))}

    def fake_run(self, changed: str = ""):
        def run(*args, **_kwargs):
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

    def check(self, root: Path, role: str = "software-architect", at: str | None = None) -> dict:
        return task_lane.cmd_role_check(SimpleNamespace(task="TK-004", role=role, verdict="go",
                                                        note="fits the records", at=at), root)

    def test_a_keyword_routes_the_architect_as_required(self):
        found = self.roles("Add an invoice_lines table for partial payments")
        self.assertEqual(found["software-architect"]["mode"], "require")
        self.assertEqual(found["software-architect"]["step"], "pre_check")
        self.assertIn("keyword table", found["software-architect"]["reasons"])
        self.assertNotIn("software-architect", self.roles("Add a CSV export button"))

    def test_a_named_file_routes_the_architect(self):
        found = self.roles("Rename the total column in db/migrations/0007_totals.sql please")
        self.assertIn("file db/migrations/0007_totals.sql matches *migrations/*",
                      found["software-architect"]["reasons"])

    def test_size_l_routes_the_architect_only_for_build_tasks(self):
        self.assertEqual(self.roles("Polish the export", size="l")["software-architect"]["reasons"], ["size l"])
        self.assertNotIn("software-architect", self.roles("Polish the export", size="m"))
        self.assertEqual(self.roles("Rewrite the schema notes", kind="knowledge", size="l"), {})

    def test_start_writes_the_roles_field_and_prints_the_reasons(self):
        root = self.project()
        args = SimpleNamespace(delivery=None, at=None, request="Add a payments table", request_file=None,
                               title="Payments", epic=None, source_story=None, covers=None, fixes=None,
                               knowledge=False, size="s", base="main")
        with self.fake_run():
            result = task_lane.cmd_start(args, root)
        self.assertEqual(result["roles"][0]["role"], "software-architect")
        self.assertIn("keyword table", result["roles"][0]["reasons"])
        props = task_lane.task_props(root / result["task"])
        self.assertEqual(props["roles"], "software-architect:require:pre_check")

    def test_a_required_role_blocks_open_pr_until_its_verdict_is_recorded(self):
        root = self.project(TASK.replace("size: s", "size: l"))
        lint = task_lane.cmd_lint(SimpleNamespace(file=None, task="TK-004"), root)
        self.assertTrue(lint["ok"])
        self.assertIn("role-check --task TK-004 --role software-architect", lint["warnings"][0])
        with self.fake_run(), self.assertRaisesRegex(task_lane.Refused, "software-architect has no recorded verdict"):
            self.open_pr(root)
        self.assertEqual(self.check(root)["pending_roles"], [])
        path = task_lane.task_path(root, "TK-004")
        self.assertEqual(task_lane.lint(path), [])
        self.assertEqual(task_lane.role_checks(path)[0]["note"], "fits the records")
        with self.fake_run():
            self.assertEqual(self.open_pr(root)["pr"], "9")

    def test_a_changed_migration_in_the_diff_requires_the_architect(self):
        root = self.project()
        with self.fake_run("db/migrations/0008_add_index.sql\n"), \
                self.assertRaisesRegex(task_lane.Refused, "software-architect"):
            self.open_pr(root)

    def test_suggested_roles_never_block(self):
        root = self.project(TASK.replace("as CSV.", "as CSV with a regression check."))
        roles, missing = task_lane.routed_roles(root, task_lane.task_path(root, "TK-004"))
        self.assertEqual([(item["role"], item["mode"]) for item in roles], [("qa-engineer", "suggest")])
        self.assertEqual(missing, [])
        with self.fake_run():
            self.assertEqual(self.open_pr(root)["pr"], "9")

    def test_a_project_rule_replaces_adds_or_drops_a_packaged_rule(self):
        root = self.project()
        (root / ".agentrof").mkdir()
        (root / task_lane.PROJECT_ROUTING).write_text(json.dumps({"rules": [
            {"role": "software-architect", "mode": "suggest", "step": "pre_check", "kinds": ["build"],
             "keywords": ["table"], "time_budget_minutes": 2, "ask": "Check the table."},
            {"role": "qa-engineer", "mode": "off", "step": "pre_check"},
            {"role": "security-reviewer", "mode": "require", "step": "pre_check", "kinds": ["build"],
             "path_globs": ["*auth/*"], "time_budget_minutes": 3, "ask": "Check the auth change."}]}),
            encoding="utf-8")
        found = self.roles("Add a table, a regression check and a login screen in src/auth/login.tsx", root=root)
        self.assertEqual(found["software-architect"]["mode"], "suggest")
        self.assertEqual(found["security-reviewer"]["mode"], "require")
        self.assertNotIn("qa-engineer", found)

    def test_the_retrospective_counts_role_checks_and_their_minutes(self):
        root = self.project()
        self.check(root, at="2026-10-16T10:05:00+00:00")
        self.check(root, "qa-engineer", at="2026-10-16T10:06:30+00:00")
        path = task_lane.task_path(root, "TK-004")
        task = {**task_lane.task_props(path), "role_checks": task_lane.role_checks(path)}
        row = task_lane.task_row(task, pr("2026-10-16T10:12:00Z", "2026-10-16T10:15:00Z",
                                          "2026-10-16T10:14:00Z", "2026-10-16T10:16:00Z"))
        self.assertEqual(row["role_checks"], {"checks": 2, "minutes": 3.5})
        data = task_lane.report([row], "latest 20")
        self.assertEqual(data["targets"]["build"]["role_checks"], {"tasks": 1, "checks": 2, "minutes": 3.5})
        self.assertIn("Role checks build: 2 on 1 of 1 tasks, 3.5 min", task_lane.render(data))


@integration
class LocalProofTests(unittest.TestCase):
    """Switch `pr_proof`: at `local_record`, open-pr needs a green local proof of the clean HEAD."""

    def project(self, value: str | None = "local_record") -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        (root / task_lane.TASKS).mkdir(parents=True)
        (root / task_lane.TASKS / "tk-004-export-invoices.md").write_text(TASK, encoding="utf-8")
        docs = root / "workspace" / "docs"
        (docs / "delivery").mkdir(parents=True)
        if value:
            run_policy(docs, "init")
            run_policy(docs, "set", "--switch", "pr_proof", "--value", value)
            run_policy(docs, "approve")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "start")
        return root

    def git(self, root: Path, *args: str) -> str:
        return task_lane.run("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args, cwd=root)

    def declare(self, root: Path, *commands: str) -> None:
        (root / ".agentrof").mkdir(exist_ok=True)
        (root / task_lane.PROJECT_PROOF).write_text(json.dumps({"commands": list(commands)}), encoding="utf-8")

    def prove(self, root: Path) -> dict:
        return task_lane.cmd_prove(SimpleNamespace(task="TK-004", base="main"), root)

    def open_pr(self, root: Path) -> tuple[dict, list]:
        real, calls = task_lane.run, []

        def run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("git", "push") or (args[:2] == ("git", "diff") and "--cached" not in args):
                return ""
            if args[:3] == ("gh", "pr", "list"):
                return "[]"
            if args[:3] == ("gh", "pr", "create"):
                return "https://github.com/acme/shop/pull/9"
            if args[:2] == ("git", "commit"):
                return real("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args[1:], **kwargs)
            return real(*args, **kwargs)
        with mock.patch.object(task_lane, "run", side_effect=run):
            result = task_lane.cmd_open_pr(SimpleNamespace(task="TK-004", base="main"), root)
        return result, calls

    def body(self, calls: list) -> str:
        create = next(call for call in calls if call[:3] == ("gh", "pr", "create"))
        return create[create.index("--body") + 1]

    def test_the_switch_defaults_to_remote_ci(self):
        spec = process_policy.load_registry()["pr_proof"]
        self.assertEqual((spec["default"], spec["values"]), ("remote_ci", ["remote_ci", "local_record"]))

    def test_the_default_opens_the_pull_request_without_a_record(self):
        for value in (None, "remote_ci"):
            root = self.project(value)
            result, calls = self.open_pr(root)
            self.assertEqual(result["pr"], "9")
            self.assertNotIn("Local proof", self.body(calls))

    def test_open_pr_refuses_without_a_record_before_pushing(self):
        root = self.project()
        real = task_lane.run
        with mock.patch.object(task_lane, "run", side_effect=lambda *a, **k: "" if a[:2] == ("git", "diff") else real(*a, **k)), \
                self.assertRaisesRegex(task_lane.Refused, "no local proof record; run prove --task TK-004"):
            task_lane.cmd_open_pr(SimpleNamespace(task="TK-004", base="main"), root)
        self.assertEqual(task_lane.task_props(task_lane.task_path(root, "TK-004"))["status"], "active")

    def test_a_record_of_an_older_head_is_stale(self):
        root = self.project()
        self.declare(root, "true")
        self.assertTrue(self.prove(root)["ok"])
        (root / "src.txt").write_text("change", encoding="utf-8")
        self.git(root, "add", "src.txt")
        self.git(root, "commit", "-qm", "change")
        with self.assertRaisesRegex(task_lane.Refused, "the proof ran on [0-9a-f]{12}, HEAD is"):
            self.open_pr(root)

    def test_a_red_command_records_its_failing_test_ids_and_the_refusal_names_them(self):
        root = self.project()
        noise = "x" * 3000
        self.declare(root, "printf 'FAILED tests/test_a.py::test_one - AssertionError\\nFAIL: test_two (pkg.test_b.Case)\\n"
                     "FAILED tests/test_a.py::test_one - again\\n" + noise + "\\n'; exit 1")
        result = self.prove(root)
        (command,) = result["commands"]
        self.assertEqual(command["failed_tests"], ["tests/test_a.py::test_one", "pkg.test_b.Case.test_two"])
        self.assertNotIn("test_one", command["tail"])
        with self.assertRaisesRegex(task_lane.Refused, "failed: tests/test_a.py::test_one, pkg.test_b.Case.test_two"):
            self.open_pr(root)
        self.declare(root, "true")
        self.assertEqual(self.prove(root)["commands"][0]["failed_tests"], [])

    def test_a_red_command_or_a_dirty_tree_refuses(self):
        root = self.project()
        self.declare(root, "true", "echo boom; exit 3")
        record = self.prove(root)
        self.assertFalse(record["ok"])
        self.assertEqual([item["exit_code"] for item in record["commands"]], [0, 3])
        self.assertIn("boom", record["commands"][1]["tail"])
        with self.assertRaisesRegex(task_lane.Refused, "`echo boom; exit 3` exited 3"):
            self.open_pr(root)
        self.declare(root, "true")
        task_lane.task_path(root, "TK-004").write_text(TASK + "\n", encoding="utf-8")
        self.assertFalse(self.prove(root)["tree_clean"])
        with self.assertRaisesRegex(task_lane.Refused, "uncommitted changes"):
            self.open_pr(root)

    def test_a_green_record_of_head_opens_the_pull_request_with_its_proof(self):
        root = self.project()
        self.declare(root, "true", "echo {base}")
        head = self.git(root, "rev-parse", "HEAD")
        record = self.prove(root)
        self.assertTrue(record["ok"])
        self.assertEqual(record["head"], head)
        self.assertEqual(record["commands"][1]["command"], "echo main")
        saved = json.loads((root / task_lane.PROOFS / "TK-004.json").read_text(encoding="utf-8"))
        self.assertTrue({"exit_code", "seconds", "started_at", "finished_at"} <= set(saved["commands"][0]))
        result, calls = self.open_pr(root)
        self.assertEqual(result["pr"], "9")
        body = self.body(calls)
        self.assertIn(f"## Local proof\n\nEvery check passed on HEAD `{head}` with a clean tree", body)
        self.assertIn("| `echo main` | pass |", body)

    def test_a_role_check_after_the_proof_keeps_it_but_any_other_change_does_not(self):
        root = self.project()
        self.declare(root, "true")
        self.prove(root)
        task_lane.cmd_role_check(SimpleNamespace(task="TK-004", role="software-architect", verdict="go",
                                                 note="fine", at="2026-01-01T00:00:00Z"), root)
        self.git(root, "commit", "-qam", "role check")
        self.assertEqual(task_lane.proof_problems(root, "TK-004")[0], [])
        (root / "other.txt").write_text("x", encoding="utf-8")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "code")
        self.assertTrue(any("run prove again" in item for item in task_lane.proof_problems(root, "TK-004")[0]))


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

    def test_standalone_tasks_need_no_story(self):
        self.assertEqual(task_lane.lint(self.write(TASK.replace("rigor: normal\n",
                                                               "rigor: normal\nepic: EP-003\n"))), [])

    def test_the_story_index_reads_criteria_and_epic(self):
        story = task_lane.story_index(self.project())["ST-012"]
        self.assertEqual(story["epic"], "EP-003")
        self.assertEqual(len(story["criteria"]), 3)
        self.assertTrue(story["criteria"][0].startswith("The export lists"))

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
        text = task_lane.render_board(task_lane.board(stories, merged, opened), stories, [])
        self.assertIn("ST-012 Invoice export: 2/3 criteria, missing [3]", text)
        self.assertIn("never edit by hand", text)

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

    def test_a_knowledge_task_is_measured_against_the_docs_targets(self):
        row = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-003", "size": "m",
                                  "task_kind": "knowledge"},
                                 pr("2026-10-16T10:01:00Z", None, None, "2026-10-16T10:02:30Z"))
        goal = task_lane.targets([row], [row["total"]])
        self.assertEqual(list(goal), ["knowledge"])
        self.assertEqual((goal["knowledge"]["average_target"], goal["knowledge"]["over_size_target"]), (5, []))

    def test_a_knowledge_task_is_approved_by_its_request_and_needs_no_criteria(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        path = root / "tk-009-fix-wording.md"
        path.write_text(task_lane.task_text("TK-009", "Fix wording", "Reword rule 3.",
                                            "2026-10-16T10:00:00Z", "", "normal", kind="knowledge"),
                        encoding="utf-8")
        self.assertEqual(task_lane.lint(path), [])
        props = task_lane.task_props(path)
        self.assertEqual((props["task_kind"], props["approved_at"]), ("knowledge", props["requested_at"]))

    def test_the_report_sums_each_kind_and_ranks_its_levers_by_the_target_gap(self):
        build = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-001"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T10:40:00Z"))
        knowledge = task_lane.task_row({**RetrospectiveTests.task, "id": "TK-002", "task_kind": "knowledge"},
                                       pr("2026-10-16T10:01:00Z", None, None, "2026-10-16T10:04:00Z"))
        data = task_lane.report([build, knowledge], "latest 20")
        self.assertEqual(set(data["targets"]), {"build", "knowledge"})
        self.assertEqual(data["targets"]["build"]["total"]["average"], 40.0)
        self.assertIn("define", data["targets"]["knowledge"]["steps"])
        self.assertTrue(data["proposals"][0]["id"].startswith("RETRO-BUILD-"))
        self.assertIn("vs target 15 min", data["proposals"][0]["evidence"])
        text = task_lane.render(data)
        self.assertIn("- build (1 merged): total 40.0 / 40.0 / 40.0;", text)
        self.assertIn("- knowledge (1 merged): total 4.0 / 4.0 / 4.0;", text)

    def test_targets_and_the_previous_run_are_compared(self):
        rows = [task_lane.task_row({**RetrospectiveTests.task, "id": "TK-001", "size": "l"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T11:00:00Z")),
                task_lane.task_row({**RetrospectiveTests.task, "id": "TK-002"},
                                   pr("2026-10-16T10:05:00Z", None, None, "2026-10-16T10:10:00Z"))]
        goal = task_lane.targets(rows, [row["total"] for row in rows])["build"]
        self.assertEqual((goal["average"], goal["average_met"]), (35.0, False))
        self.assertEqual((goal["max"], goal["max_met"]), (60.0, True))
        self.assertEqual(goal["over_size_target"], ["TK-001", "TK-002"])
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        data = task_lane.report(rows, "latest 20")
        self.assertIsNone(task_lane.previous_run(root, "latest 20"))
        task_lane.remember(root, data)
        before = task_lane.previous_run(root, "latest 20")
        self.assertEqual((before["average"], before["merged"]), (35.0, 2))
        text = task_lane.render(task_lane.report(rows, "latest 20", before))
        self.assertIn("Target build (2): average 35.0 vs 15 min (missed), max 60.0 vs 60 (met), s/m/l 5/15/30;"
                      " over size target: TK-001, TK-002", text)
        self.assertIn("Previous retrospective", text)

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

    def test_a_fast_call_costs_its_multiplier_and_every_price_has_all_rates(self):
        call = {"model": "claude-opus-5-5", "tokens": {"input": 1000, "cache_write": 0, "cache_read": 0, "output": 1000}}
        self.assertAlmostEqual(self.session_cost.usd(call, self.prices), 0.024)
        self.assertAlmostEqual(self.session_cost.usd({**call, "fast": True}, self.prices), 0.048)
        self.assertAlmostEqual(self.session_cost.usd({**call, "model": "claude-sonnet-5-5", "fast": True}, self.prices),
                               0.012)
        rates = {"input", "cache_write_5m", "cache_write_1h", "cache_read", "output"}
        for model, rate in self.prices.items():
            self.assertLessEqual(rates, set(rate), model)
            self.assertTrue(all(rate[key] > 0 for key in rates), model)
            self.assertLess(rate["cache_read"], rate["input"], model)
            if "long_context" in rate:
                self.assertLessEqual(rates | {"above_prompt_tokens"}, set(rate["long_context"]), model)

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

    def test_the_report_prices_each_task_and_kind_and_ranks_cost_levers_with_time(self):
        data = task_lane.report([self.costed_row()], "latest 20")
        cost = data["targets"]["build"]["cost"]
        self.assertEqual((cost["usd_per_task"], cost["spawns_per_task"], cost["startup_context_per_spawn"],
                          cost["main_share"]), (2.0, 1.0, 64000, 0.5))
        self.assertEqual(cost["tokens_per_task"]["cache_read"], 2000000)
        ids = [item["id"] for item in data["proposals"]]
        for lever in ("STARTUP", "EFFORT", "MAIN"):
            self.assertIn(f"RETRO-BUILD-COST-{lever}", ids)
        self.assertLessEqual(len(ids), 4)
        text = task_lane.render(data)
        self.assertIn("  - cost $2.00; tokens input 2 / cache_write 64000 / cache_read 2000000 / output 2000;"
                      " 1 spawns, startup context 64000 per spawn; main session $1.00", text)
        self.assertIn("Cost build: $2.00 over 1 tasks, $2.00 per task;", text)
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
                mock.patch.object(task_lane, "pr_times", return_value=pr(
                    "2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")), \
                mock.patch.object(self.session_cost, "scan", side_effect=AssertionError("scanned")):
            again = task_lane.gather(root, args)
        self.assertEqual(again["per_task"][0]["cost"]["usd"], 2.0)
        self.assertIn("$2.00 and 2066002 tokens per task", task_lane.render(again))
        # A rescan prices from the transcripts again and appends a newer row; the old one stays.
        fixed = {**again["per_task"][0]["cost"], "usd": 1.5}
        with mock.patch.object(task_lane, "pull_requests", return_value={"TK-001": {"number": 9}}), \
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
        with mock.patch.object(task_lane, "run", side_effect=lambda *a, **k: calls.append(a) or ""):
            task_lane.cmd_merge(SimpleNamespace(task="TK-004", pr="9", style="merge", detail=None,
                                                critical=1, non_critical=3, filed=2), root)
        note = calls[0][-1]
        self.assertIn("findings: critical 1, non-critical 3, filed 2", note)
        comments = [{"body": note}, {"body": "unrelated findings: critical 9, non-critical 9, filed 9"}]
        self.assertEqual(task_lane.findings(comments), {"critical": 1, "non_critical": 3, "filed": 2})
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
        path.write_text(task_lane.task_text("TK-009", "Fix export", "Fix it.", "2026-10-16T10:00:00Z", "",
                                            "normal", kind="knowledge", fixes="TK-004"), encoding="utf-8")
        self.assertEqual(task_lane.task_props(path)["fixes"], "TK-004")
        self.assertEqual(task_lane.lint(path), [])
        path.write_text(path.read_text(encoding="utf-8").replace("fixes: TK-004", "fixes: 4"), encoding="utf-8")
        self.assertIn("fixes must be TK-<number>", task_lane.lint(path))
        with self.assertRaises(task_lane.Refused):
            task_lane.cmd_start(SimpleNamespace(delivery=None, at=None, request="r", request_file=None,
                                                title="t", epic=None, source_story=None, covers=None,
                                                fixes="TK-4"), Path("."))
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
            self.assertIn(f"RETRO-BUILD-QUALITY-{lever}", ids)
        for item in data["proposals"]:
            for key in ("evidence", "cause_owner", "expected_gain", "verify", "proposal"):
                self.assertTrue(item[key], (item["id"], key))
        text = task_lane.render(data)
        self.assertIn("- TK-001: 1 / 2 / 2; CI first pass no (2); fixed by TK-005; -", text)
        self.assertIn("- TK-002: 0 / 0 / 0; CI first pass yes (0); fixed by -; ST-012 67%", text)
        self.assertIn("Quality build: critical 0.5 and non-critical 1 per task over 2 reviewed, filed 100%;"
                      " CI first pass 50% over 2 (1 red runs per task); escaped 1/2 (50%): TK-001;"
                      " coverage ST-012 67%", text)
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
        self.assertIn("critical 1 per task, CI first pass 0%, escaped 0%, coverage -",
                      task_lane.render(task_lane.report(data["per_task"], "latest 20", before)))
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
                 "no tests ran for the changed module": "test-impact", "wip": task_lane.UNCLASSIFIED}
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
        self.assertNotIn("criterion", task_lane.routed_roles(root, path)[0].__repr__())

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
        self.assertIn("### lint-format\n\n- Cause: lint or format check\n- Rule: Run the project's formatter", text)
        self.assertIn("- Owner: project. Status: open. Promote: memory.\n- Evidence: 4 events in 3 tasks"
                      " (TK-001, TK-002, TK-003), first 2026-10-01, last 2026-10-03.", text)
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
        self.assertIn("RETRO-BUILD-REWORK-SPEC-GAP", [item["id"] for item in data["proposals"]])
        text = task_lane.render(data)
        self.assertIn("- TK-004: 1 (ci 1); spec-gap 1; 8.0 min", text)
        self.assertIn("- TK-005: none, one pass", text)
        self.assertIn("Rework build: one pass 1/2 (50%), 1 events, 8.0 min after the first event (16% of request to merge)",
                      text)
        self.assertIn("- spec-gap [process]: 3 events in 2 tasks (TK-001, TK-004)", text)
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


class EntryExposureTests(unittest.TestCase):
    # Agents may call every entry the user can; arming autopilot stays the user's.
    MODEL_INVOCABLE = ["configure", "deliver", "issue-report", "retrospective", "setup"]
    USER_ONLY = ["autopilot"]

    def test_the_model_invocable_entries_are_listed_on_both_hosts(self):
        claude = ROOT / "dist" / "claude" / "software-engineering-team" / "skills"
        codex = ROOT / "dist" / "codex" / "software-engineering-team" / "skills"
        invocable, user_only = [], []
        for wrapper in sorted(claude.glob("*/SKILL.md")):
            header = wrapper.read_text(encoding="utf-8").split("---", 2)[1]
            if "user-invocable: false" in header:
                continue
            name = wrapper.parent.name
            (user_only if "disable-model-invocation: true" in header else invocable).append(name)
            policy = (codex / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
            self.assertIn(f"allow_implicit_invocation: {'false' if name in self.USER_ONLY else 'true'}",
                          policy, name)
        self.assertEqual(invocable, self.MODEL_INVOCABLE)
        self.assertEqual(user_only, self.USER_ONLY)

    def test_autopilot_arming_still_needs_the_typed_command(self):
        text = (TEAM / "skill-content" / "autopilot" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("model_invocable: false", text.split("---", 2)[1])
        self.assertIn("`on` refuses without one", " ".join(text.split()))

if __name__ == "__main__":
    unittest.main()
