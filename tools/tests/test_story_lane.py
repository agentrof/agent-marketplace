"""Fast lane: switch `process_profile` keeps every request on the standard
ceremonies at `standard`. At `fast_lane`, one story card, one branch and one
pull request carry a change; the card holds the request and approval times,
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
import story_lane  # noqa: E402
import vault_check  # noqa: E402

CARD = """---
type: story-card
title: "Export invoices"
status: active
id: FL-004
requested_at: 2026-10-16T10:00:00+00:00
approved_at: 2026-10-16T10:03:00+00:00
size: s
rigor: normal
tags:
  - doc/story-card
  - status/active
aliases:
  - FL-004
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


class CardTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        path = directory / "fl-004-export-invoices.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_complete_card_passes(self):
        self.assertEqual(story_lane.lint(self.write(CARD)), [])

    def test_criteria_size_rigor_and_times_are_checked(self):
        bad = (CARD.replace("3. An empty month yields a header-only file.\n", "")
               .replace("size: s", "size: xl").replace("rigor: normal", "rigor: extreme")
               .replace("requested_at: 2026-10-16T10:00:00+00:00\n", ""))
        errors = story_lane.lint(self.write(bad))
        self.assertTrue(any("found 2" in error for error in errors))
        self.assertTrue(any(error.startswith("size") for error in errors))
        self.assertTrue(any(error.startswith("rigor") for error in errors))
        self.assertIn("requested_at is required", errors)

    def test_nine_criteria_are_two_stories(self):
        many = "\n".join(f"{n}. Criterion {n}." for n in range(1, 10))
        text = CARD.split("## Acceptance Criteria")[0] + "## Acceptance Criteria\n\n" + many \
            + "\n\n## Tests" + CARD.split("## Tests")[1]
        self.assertTrue(any("found 9" in error for error in story_lane.lint(self.write(text))))

    def test_the_skeleton_is_valid_once_filled(self):
        text = story_lane.card_text("FL-007", "Rotate keys", "Rotate the signing keys.",
                                    "2026-10-16T10:00:00+00:00", "DLV-009", "high")
        text = text.replace("1. \n2. \n3. \n", "1. One.\n2. Two.\n3. Three.\n").replace("- \n", "- t\n")
        path = self.write(text)
        self.assertEqual(story_lane.lint(path), [])
        props = story_lane.card_props(path)
        self.assertEqual((props["rigor"], props["delivery"]), ("high", "DLV-009"))

    def test_set_field_adds_and_replaces_only_its_scalar(self):
        path = self.write(CARD.replace("approved_at: 2026-10-16T10:03:00+00:00\n", ""))
        story_lane.set_field(path, "approved_at", "2026-10-16T10:05:00+00:00")
        story_lane.set_field(path, "status", "delivered")
        props = story_lane.card_props(path)
        self.assertEqual(props["approved_at"], "2026-10-16T10:05:00+00:00")
        self.assertEqual(props["status"], "delivered")
        self.assertIn("status/delivered", props["tags"])
        self.assertEqual(story_lane.lint(path), [])

    def test_risky_requests_propose_high_rigor(self):
        self.assertEqual(story_lane.proposed_rigor("Delete stale tenant data")[0], "high")
        self.assertEqual(story_lane.proposed_rigor("Rotate the API key secrets")[0], "high")
        self.assertEqual(story_lane.proposed_rigor("Add a CSV export button")[0], "normal")

    def test_ids_follow_cards_and_remote_story_branches(self):
        names = ["workspace/docs/backlog/cards/fl-003-a.md", "origin/story/fl-011-b"]
        self.assertEqual(story_lane.next_id(names), "FL-012")
        self.assertEqual(story_lane.next_id([]), "FL-001")


def pr(opened, ci, review, merged, checks=None):
    return {"pr": "9", "pr_opened": opened, "ci_green": ci, "review": review,
            "merged": merged, "checks": checks or {}}


class RetrospectiveTests(unittest.TestCase):
    card = {"id": "FL-004", "title": "Export", "size": "s", "rigor": "normal",
            "requested_at": "2026-10-16T10:00:00+00:00",
            "approved_at": "2026-10-16T10:03:00+00:00"}

    def test_steps_split_the_request_to_merge_time(self):
        row = story_lane.story_row(self.card, pr("2026-10-16T10:10:00Z", "2026-10-16T10:15:00Z",
                                                 "2026-10-16T10:13:00Z", "2026-10-16T10:16:00Z"))
        self.assertEqual(row["steps"], {"card": 3.0, "write": 7.0, "ci": 5.0, "review": 3.0,
                                        "merge_wait": 1.0})
        self.assertEqual((row["total"], row["largest_step"]), (16.0, "write"))

    def test_an_open_story_has_no_total_and_a_proposal_names_it(self):
        data = story_lane.report([story_lane.story_row(self.card, None)], "latest 20")
        self.assertIsNone(data["per_story"][0]["total"])
        self.assertEqual(data["request_to_merge_minutes"]["count"], 0)
        self.assertIn("FL-OPEN-STORIES", [item["id"] for item in data["proposals"]])

    def test_report_ranks_proposals_by_measured_minutes(self):
        rows = [story_lane.story_row({**self.card, "id": f"FL-00{n}"},
                                     pr("2026-10-16T10:05:00Z", f"2026-10-16T10:{20 + n}:00Z",
                                        "2026-10-16T10:08:00Z", f"2026-10-16T10:{21 + n}:00Z",
                                        {"tests": 14.0 + n, "vault gate": 1.5}))
                for n in range(1, 4)]
        data = story_lane.report(rows, "latest 20")
        self.assertEqual(data["request_to_merge_minutes"],
                         {"count": 3, "average": 23.0, "median": 23.0, "max": 24.0})
        ids = [item["id"] for item in data["proposals"]]
        self.assertEqual(ids[0], "FL-CI")
        self.assertIn("FL-CHECK-TESTS", ids)
        self.assertLessEqual(len(ids), 5)
        self.assertEqual(data["slowest"][0]["story_id"], "FL-003")
        text = story_lane.render(data)
        self.assertIn("1. [FL-CI]", text)
        self.assertIn("Request to merge: average 23.0, median 23.0, max 24.0 min", text)
        self.assertEqual(story_lane.export(data, "csv").splitlines()[0].split(",")[:3],
                         ["story_id", "title", "size"])

    def test_scope_selects_a_delivery_a_window_or_the_latest(self):
        cards = [{"id": f"FL-00{n}", "requested_at": f"2026-10-{10 + n}T10:00:00+00:00",
                  "delivery": "DLV-007" if n % 2 else ""} for n in range(1, 6)]
        at = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.assertEqual([card["id"] for card in story_lane.select(cards, delivery="DLV-007")],
                         ["FL-001", "FL-003", "FL-005"])
        self.assertEqual([card["id"] for card in story_lane.select(cards, since="3d", at=at)],
                         ["FL-004", "FL-005"])
        self.assertEqual([card["id"] for card in story_lane.select(cards, recent=2)],
                         ["FL-004", "FL-005"])
        with self.assertRaises(story_lane.Refused):
            story_lane.select(cards, since="week")

    def test_github_times_come_from_checks_and_the_review_comment(self):
        checks = [{"name": "tests", "conclusion": "SUCCESS", "startedAt": "2026-10-16T10:05:00Z",
                   "completedAt": "2026-10-16T10:11:00Z"},
                  {"name": "gate", "conclusion": "SUCCESS", "startedAt": "2026-10-16T10:05:00Z",
                   "completedAt": "2026-10-16T10:07:00Z"}]
        self.assertEqual(story_lane.ci_green_at(checks), "2026-10-16T10:11:00Z")
        self.assertIsNone(story_lane.ci_green_at(checks + [{"name": "x", "conclusion": "FAILURE"}]))
        self.assertEqual(story_lane.check_minutes(checks), {"tests": 6.0, "gate": 2.0})
        comments = [{"body": "looks fine", "createdAt": "2026-10-16T10:06:00Z"},
                    {"body": story_lane.REVIEW_MARK, "createdAt": "2026-10-16T10:09:00Z"}]
        self.assertEqual(story_lane.review_at(comments), "2026-10-16T10:09:00Z")

    def test_the_retrospective_note_is_vault_shaped(self):
        data = story_lane.report([story_lane.story_row(self.card, None)], "DLV-007")
        note = story_lane.retrospective_note(data, "DLV-007")
        self.assertIn("type: retrospective", note)
        self.assertIn("<!-- sec: nav -->", note)


class GitHubCommandTests(unittest.TestCase):
    def project(self, rigor: str) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        cards = root / story_lane.CARDS
        cards.mkdir(parents=True)
        (cards / "fl-004-export-invoices.md").write_text(
            CARD.replace("rigor: normal", f"rigor: {rigor}"), encoding="utf-8")
        return root

    def test_normal_rigor_turns_on_auto_merge(self):
        calls = []
        with mock.patch.object(story_lane, "run", side_effect=lambda *a, **k: calls.append(a) or ""):
            result = story_lane.cmd_merge(SimpleNamespace(story="FL-004", pr="9", style="merge",
                                                          detail=None), self.project("normal"))
        self.assertTrue(result["auto_merge"])
        self.assertIn(("gh", "pr", "merge", "9", "--auto", "--merge"), calls)
        self.assertIn(story_lane.REVIEW_MARK, calls[0][-1])

    def test_high_rigor_leaves_the_merge_to_the_owner(self):
        calls = []
        with mock.patch.object(story_lane, "run", side_effect=lambda *a, **k: calls.append(a) or ""):
            result = story_lane.cmd_merge(SimpleNamespace(story="FL-004", pr="9", style="merge",
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
                return json.dumps([{"number": 9, "headRefName": "story/fl-004-export-invoices"}])
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
        with mock.patch.object(story_lane, "run", side_effect=fake):
            result = story_lane.cmd_file_issues(args, root)
            with self.assertRaises(story_lane.Refused):
                story_lane.cmd_file_issues(SimpleNamespace(**{**vars(args), "ids": "FL-NOPE"}), root)
        created = [call for call in calls if call[:3] == ("gh", "issue", "create")]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][3:5], ("--repo", "acme/shop"))
        self.assertIn("Evidence:", created[0][-1])
        self.assertEqual(result["repo"], "acme/shop")


class FastLaneOwnershipTests(unittest.TestCase):
    def test_a_card_owns_architecture_only_in_the_fast_lane(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            (docs / "delivery").mkdir(parents=True)
            cards = docs / "backlog" / "cards"
            cards.mkdir(parents=True)
            (cards / "fl-004-export-invoices.md").write_text(CARD, encoding="utf-8")
            self.assertIsNone(architecture_compile.story_card(docs, "FL-004"))
            run_policy(docs, "init")
            run_policy(docs, "set", "--switch", "process_profile", "--value", "fast_lane")
            run_policy(docs, "approve")
            path, props, ref = architecture_compile.story_card(docs, "FL-004")
            self.assertEqual((path.name, ref), ("fl-004-export-invoices.md", "FL-004"))
            self.assertIsNone(architecture_compile.story_card(docs, "ST-004"))
            (cards / "fl-004-export-invoices.md").write_text(
                CARD.replace("status: active", "status: dropped"), encoding="utf-8")
            with self.assertRaises(ValueError):
                architecture_compile.story_card(docs, "FL-004")

    def test_story_cards_need_no_inbound_map_link(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.assertIn("story-card", policy["navigation_exempt_types"])
        card = SimpleNamespace(rel="backlog/cards/fl-004-a.md", fm={"type": "story-card"},
                               generated=False)
        story = SimpleNamespace(rel="backlog/epics/e/stories/s/story.md", fm={"type": "story"},
                                generated=False)
        vault = SimpleNamespace(policy=policy, notes={card.rel: card, story.rel: story})
        self.assertEqual([note.rel for note in vault_check.navigated(vault)], [story.rel])

    def test_the_card_type_is_registered_in_the_vault_policy(self):
        policy = json.loads((TEAM / "skill-content/obsidian-vault/data/vault-policy.json")
                            .read_text(encoding="utf-8"))
        self.assertIn("story-card", policy["extra_doc_types"])
        self.assertIn("retrospective", policy["extra_doc_types"])
        for key, kind in (("requested_at", "datetime"), ("size", "text"), ("rigor", "text"),
                          ("delivery", "text")):
            self.assertEqual(policy["property_types"][key], kind)


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
    def test_retrospective_is_model_invocable_and_deliver_is_user_only(self):
        skills = ROOT / "dist" / "claude" / "software-engineering-team" / "skills"
        retrospective = (skills / "retrospective" / "SKILL.md").read_text(encoding="utf-8")
        story = (skills / "deliver" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("disable-model-invocation", retrospective)
        self.assertNotIn("user-invocable: false", retrospective)
        self.assertIn("disable-model-invocation: true", story)

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
