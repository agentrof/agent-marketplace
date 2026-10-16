"""Fast lane: switch `process_profile` keeps every request on the standard
ceremonies at `governed`. At `fast_lane`, one task, one branch and one
pull request carry a change; the task holds the request and approval times,
GitHub the rest, and the retrospective derives steps and proposals from them."""

from __future__ import annotations

import contextlib
import io
import json
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
import architecture_compile  # noqa: E402
import process_policy  # noqa: E402
import task_lane  # noqa: E402
import vault_check  # noqa: E402

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


class SwitchTests(unittest.TestCase):
    def test_the_switch_defaults_to_governed_and_offers_the_fast_lane(self):
        spec = process_policy.load_registry()["process_profile"]
        self.assertEqual(spec["default"], "governed")
        self.assertEqual(spec["values"], ["governed", "fast_lane"])
        self.assertNotIn("delivery-execution", spec["spec"]["flows"])
        self.assertNotIn("delivery-planning", spec["spec"]["flows"])

    def test_profile_reads_governed_without_a_policy_and_fast_lane_when_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            (docs / "delivery").mkdir(parents=True)
            self.assertEqual(process_policy.process_profile(docs), "governed")
            run_policy(docs, "init")
            run_policy(docs, "set", "--switch", "process_profile", "--value", "fast_lane")
            self.assertEqual(process_policy.process_profile(docs), "governed",
                             "a draft policy never opens the fast lane")
            run_policy(docs, "approve")
            self.assertEqual(process_policy.process_profile(docs), "fast_lane")

    def test_the_planning_entries_redirect_and_the_stage_flows_anchor_the_switch(self):
        for entry in ("deliver-governed", "delivery-plan", "execution-plan"):
            text = (TEAM / "skill-content" / entry / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("use `/deliver` or\n`/requirement`", text, entry)
        for flow in ("requirement", "business-analysis", "solution-design", "design-system",
                     "experience-design", "backlog-planning", "fast-lane"):
            text = (TEAM / "flows" / f"{flow}.md").read_text(encoding="utf-8")
            self.assertIn("Switch `process_profile`", text, flow)


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


class FastLaneOwnershipTests(unittest.TestCase):
    def test_a_task_owns_architecture_only_in_the_fast_lane(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            (docs / "delivery").mkdir(parents=True)
            tasks = docs / "backlog" / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "tk-004-export-invoices.md").write_text(TASK, encoding="utf-8")
            self.assertIsNone(architecture_compile.task_owner(docs, "TK-004"))
            run_policy(docs, "init")
            run_policy(docs, "set", "--switch", "process_profile", "--value", "fast_lane")
            run_policy(docs, "approve")
            path, props, ref = architecture_compile.task_owner(docs, "TK-004")
            self.assertEqual((path.name, ref), ("tk-004-export-invoices.md", "TK-004"))
            self.assertIsNone(architecture_compile.task_owner(docs, "ST-004"))
            (tasks / "tk-004-export-invoices.md").write_text(
                TASK.replace("status: active", "status: dropped"), encoding="utf-8")
            with self.assertRaises(ValueError):
                architecture_compile.task_owner(docs, "TK-004")

    def test_task_owners_need_no_inbound_map_link(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.assertIn("task", policy["navigation_exempt_types"])
        task = SimpleNamespace(rel="backlog/tasks/tk-004-a.md", fm={"type": "task"},
                               generated=False)
        story = SimpleNamespace(rel="backlog/epics/e/stories/s/story.md", fm={"type": "story"},
                                generated=False)
        vault = SimpleNamespace(policy=policy, notes={task.rel: task, story.rel: story})
        self.assertEqual([note.rel for note in vault_check.navigated(vault)], [story.rel])

    def test_the_task_type_is_registered_in_the_vault_policy(self):
        policy = json.loads((TEAM / "skill-content/obsidian-vault/data/vault-policy.json")
                            .read_text(encoding="utf-8"))
        self.assertIn("task", policy["extra_doc_types"])
        self.assertIn("retrospective", policy["extra_doc_types"])
        for key, kind in (("requested_at", "datetime"), ("size", "text"), ("rigor", "text"),
                          ("delivery", "text")):
            self.assertEqual(policy["property_types"][key], kind)


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

    def test_the_link_properties_are_registered(self):
        policy = json.loads((TEAM / "skill-content/obsidian-vault/data/vault-policy.json")
                            .read_text(encoding="utf-8"))
        for key, kind in (("epic", "text"), ("source_story", "text"), ("covers", "multitext")):
            self.assertEqual(policy["property_types"][key], kind)
        self.assertEqual(policy["type_path_patterns"]["task"],
                         ["^backlog/tasks/tk-[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*\\.md$"])


class FastLaneContractTests(unittest.TestCase):
    """At fast_lane, contracts, the DoD and Governance change by plain pull request."""

    def project(self, fast: bool) -> Path:
        docs = Path(tempfile.mkdtemp()) / "workspace" / "docs"
        self.addCleanup(lambda: __import__("shutil").rmtree(docs.parent.parent))
        (docs / "delivery").mkdir(parents=True)
        if fast:
            run_policy(docs, "init")
            run_policy(docs, "set", "--switch", "process_profile", "--value", "fast_lane")
            run_policy(docs, "approve")
        return docs

    def edited_contract(self, docs: Path) -> None:
        """A contract a pull request edited to approved, with no stamp, citation or revision."""
        import operation_compile
        with contextlib.redirect_stdout(io.StringIO()):
            operation_compile.main(["init", "--docs", str(docs), "--kind", "verification"])
        path = operation_compile.contract_path(docs, "verification")
        props, body = operation_compile.parse(path)
        props.update(status="approved", test_command="make test")
        props.pop("revision")
        path.write_text(operation_compile.render(props, body), encoding="utf-8")

    def test_a_plain_pull_request_contract_is_in_force(self):
        import operation_compile
        for fast in (True, False):
            docs = self.project(fast)
            self.edited_contract(docs)
            value, errors = operation_compile.check_contract(docs, "verification")
            with self.subTest(fast=fast):
                if fast:
                    self.assertEqual(errors, [])
                    self.assertTrue(value["current"])
                    self.assertNotIn("paired_environment", value)
                    self.assertEqual(operation_compile.pairing_findings(docs, value), [])
                else:
                    self.assertIn("approved contract source_hash is stale", errors)
                    self.assertIn("revision must be a positive integer", errors)
                    self.assertIn("approved contract must cite at least one accepted Solution decision"
                                  " in constrained_by", errors)
                props, body = operation_compile.parse(operation_compile.contract_path(docs, "verification"))
                self.assertEqual(operation_compile.contract_in_force(docs, props, body), fast)

    def test_between_gates_never_refuses_a_shared_document_in_the_fast_lane(self):
        import delivery_compile
        docs = self.project(True)
        with mock.patch.object(delivery_compile, "delivery_dirs", side_effect=AssertionError("read")):
            self.assertEqual(delivery_compile.between_gates_refusals(docs, "Verification Contract revision 2"), [])

    def test_the_dod_needs_no_fixed_headings_or_stamp(self):
        import delivery_compile
        text = ("---\ntype: definition-of-done\nid: DOD\nstatus: approved\n---\n# Definition of Done\n\n"
                "Tests pass in CI.\n\n<!-- sec: nav -->\n## Navigation\n\n- [[maps/delivery|Delivery]]\n")
        for fast in (True, False):
            docs = self.project(fast)
            path = docs / "delivery" / "definition-of-done.md"
            path.write_text(text, encoding="utf-8")
            errors = delivery_compile.check_dod(path)
            with self.subTest(fast=fast):
                if fast:
                    self.assertEqual(errors, [])
                    snapshot, snapshot_errors = delivery_compile.approved_dod_source(docs)
                    self.assertEqual(snapshot_errors, [])
                    self.assertTrue(snapshot["definition_of_done_source_hash"].startswith("sha256:"))
                else:
                    self.assertIn("DoD missing sections: Commands, Evidence Rules, Quality Gates", errors)
                    self.assertIn("approved DoD source_hash is stale", errors)

    def test_item_contract_bindings_follow_the_contract_in_force(self):
        import delivery_compile
        docs = self.project(True)
        self.edited_contract(docs)
        stale = {"runtime_required": False, "verification_contract_ref": "operation/verification-contract",
                 "verification_contract_hash": "sha256:old"}
        self.assertEqual(delivery_compile.item_operation_findings(docs, stale), [])

    def test_governance_is_optional_for_activation(self):
        import delivery_git
        docs = self.project(True)
        self.assertEqual(delivery_git.project_max_parallel(docs.parent.parent, "none"), 1)
        with self.assertRaises(RuntimeError):
            delivery_git.project_max_parallel(self.project(False).parent.parent, "none")

    def test_the_hook_lets_a_plain_pull_request_edit_contracts_and_governance(self):
        sys.path.insert(0, str(ROOT / "platforms/shared/software-engineering-team/overlay/scripts"))
        import vault_hook
        for fast in (True, False):
            docs = self.project(fast)
            self.edited_contract(docs)
            for rel in ("operation/verification-contract.md", "delivery/governance/governance.md"):
                target = docs / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text("---\ntype: delivery-governance\nstatus: approved\n---\n# G\n",
                                      encoding="utf-8")
                with self.subTest(fast=fast, rel=rel), contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = vault_hook.pre_target({"file_path": str(target), "old_string": "a",
                                                  "new_string": "b"})
                    self.assertEqual(code == 0, fast)


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

    def test_a_project_price_file_overrides_a_model(self):
        (self.project / ".agentrof").mkdir()
        (self.project / ".agentrof" / "pricing.json").write_text(json.dumps({"models": {
            "claude-opus-5-5": {"input": 1, "cache_write_5m": 1, "cache_write_1h": 1, "cache_read": 1,
                                "output": 1}}}), encoding="utf-8")
        prices = self.session_cost.load_prices(self.project)
        self.assertEqual(prices["claude-opus-5-5"]["output"], 1)
        self.assertEqual(prices["claude-sonnet-5-5"]["output"], 10)

    def test_spawns_follow_their_task_id_branches_their_task_and_the_rest_splits_by_window(self):
        (self.folder / "s1.jsonl").write_text("\n".join([
            self.call(5, "m1", output_tokens=10), self.call(25, "m2", output_tokens=20),
            self.call(50, "m3", output_tokens=40),
            self.call(45, "m4", branch="task/tk-002-b", output_tokens=400)]) + "\n", encoding="utf-8")
        self.agent("d1", [self.record(39, "user", message={"content": "Build it on task/tk-001-a."}),
                          self.call(40, "d1", output_tokens=100)], description="Developer")
        self.agent("d2", [self.record(9, "user", message={"content": "Compare TK-001 with TK-002."}),
                          self.call(10, "d2", output_tokens=200)])
        tasks = [{**RetrospectiveTests.task, "id": "TK-001", "file": "tk-001-a.md"},
                 {**RetrospectiveTests.task, "id": "TK-002", "file": "tk-002-b.md",
                  "requested_at": "2026-10-16T10:03:00+00:00"}]
        rows = [task_lane.task_row(tasks[0], pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:20:00Z")),
                task_lane.task_row(tasks[1], pr("2026-10-16T10:10:00Z", None, None, "2026-10-16T10:30:00Z"))]
        rest = task_lane.attach_sessions(rows, tasks, self.scan(), False, 1e12, self.prices)
        self.assertEqual([row["cost"]["tokens"]["output"] for row in rows], [205, 525])
        self.assertEqual(rest["tokens"]["output"], 40)
        self.assertEqual(rows[0]["cost"]["main"]["tokens"]["output"], 5)
        self.assertEqual(len(rows[0]["cost"]["spawns"]), 2)

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


class GovernedRenameTests(unittest.TestCase):
    OLD = __import__("re").compile(r"/deliver(?= DLV-| \{)|--entry deliver(?![-\w])|entry=\"deliver\"")

    def test_no_governed_surface_names_the_old_entry(self):
        governed = [*(TEAM / "flows").glob("*.md"),
                    *(TEAM / "skill-content" / "deliver-governed").rglob("*.md"),
                    *(TEAM / "skill-content" / "deliver" / "references").glob("switch-*.md"),
                    *(TEAM / "skill-content" / "delivery-plan").rglob("*.md"),
                    *(TEAM / "skill-content" / "execution-plan").rglob("*.md"),
                    *(TEAM / "scripts").glob("delivery_*.py"),
                    *(ROOT / "platforms").glob("*/software-engineering-team/host-contract.md")]
        offenders = [f"{path.relative_to(ROOT)}:{number}"
                     for path in governed
                     for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                     if self.OLD.search(line)]
        self.assertEqual(offenders, [])

    def test_the_governed_entry_binds_its_references_from_the_shared_folder(self):
        import task_inputs
        route = task_inputs.catalog()["entries"]["deliver-governed"]
        self.assertEqual(route["flows"], ["delivery-execution"])
        self.assertEqual(route["reference_skills"], ["deliver"])
        self.assertEqual(task_inputs.catalog()["entries"]["deliver"]["flows"], ["fast-lane"])


class EntryExposureTests(unittest.TestCase):
    # Agents may call every entry the user can; arming autopilot stays the user's.
    MODEL_INVOCABLE = ["business-analysis", "configure", "deliver", "demo", "design-system",
                       "experience-design", "issue-report", "organize-docs", "requirement",
                       "retrospective", "setup", "sketch", "solution-design"]
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

    def test_the_planning_entries_are_hidden_like_internal_skills(self):
        claude = ROOT / "dist" / "claude" / "software-engineering-team" / "skills"
        codex = ROOT / "dist" / "codex" / "software-engineering-team" / "skills"
        internal = (claude / "code-review" / "SKILL.md").read_text(encoding="utf-8")
        policy = [line for line in internal.splitlines()[1:4] if "invoc" in line]
        for entry in ("backlog-plan", "delivery-plan", "execution-plan", "deliver-governed"):
            text = (claude / entry / "SKILL.md").read_text(encoding="utf-8")
            self.assertEqual([line for line in text.splitlines()[1:5] if "invoc" in line], policy,
                             entry)
            self.assertFalse((codex / entry).exists(), entry)
            source = (TEAM / "skill-content" / entry / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("exposure: entry\nuser_invocable: false\n", source)


if __name__ == "__main__":
    unittest.main()
