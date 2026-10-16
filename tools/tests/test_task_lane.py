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
        self.assertIn("RETRO-OPEN-TASKS", [item["id"] for item in data["proposals"]])

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
        text = task_lane.render(data)
        self.assertIn("1. [RETRO-CI]", text)
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

        args = SimpleNamespace(ids="1", delivery=None, since=None, recent=20)
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
