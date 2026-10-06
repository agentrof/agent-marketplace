"""Process switch test_levels: a Test Plan scenario may state the level its
automation target runs at; at `declared` the backlog compiler and the review
manifests list each automation-required scenario that states no level and each
fixture or live one that states no reason, and never fail a check (#433). A
stated level outside the three values fails the check at every value."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_fixture  # noqa: E402
import backlog_review_inputs  # noqa: E402
import delivery_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import test_all_switches_on as all_on  # noqa: E402
import test_story_size_budget as size  # noqa: E402
import test_test_cost_budget as cost  # noqa: E402

SWITCH = "test_levels"
VALUE = "declared"
REFERENCE = "skill-content/product-planning/references/switch-test_levels-declared.md"
REASON = "level_reason: a real process group is the only proof that a stop leaves none behind"
INVALID = "scenario ST-001-TS-001 level must be one of unit, fixture, live"


def entry(story: str, **more: str) -> dict:
    name = story.lower().replace("-", "_")
    return {"story": story, "scenario": f"{story}-TS-001",
            "automation_target": f"tests/{name}.py::test_{name}", **more}


def gaps(without_level=(), without_level_reason=()) -> dict:
    """The `test_levels` block of a check or a manifest."""
    return {"switch": SWITCH, "value": VALUE, "without_level": list(without_level),
            "without_level_reason": list(without_level_reason)}


class RegistryTests(unittest.TestCase):
    def test_the_switch_ships_off_for_backlog_planning_and_takes_no_owner_set_parameter(self):
        registry = process_policy.load_registry()
        spec = registry[SWITCH]
        self.assertEqual((spec["default"], list(spec["values"])), ("off", ["off", VALUE]))
        self.assertNotIn("parameters", spec)
        raw = json.loads((TEAM / "skill-content/configure/data/process-switches.json").read_text(
            encoding="utf-8"))["switches"][SWITCH]
        self.assertEqual((raw["flows"], raw["reference_scope"]), (["backlog-planning"], "owning_flows"))
        self.assertNotIn("value_data", raw)
        # No Delivery flow owns it, so a Delivery pins no value of it.
        self.assertNotIn(SWITCH, process_policy.delivery_switches(registry))

    def test_the_default_path_keeps_its_instructions(self):
        # Only the flows anchor the switch; no role, skill or default reference names it.
        for relative in ("agents/qa-engineer.md", "agents/product-owner.md",
                         "agents/business-analyst.md", "agents/backlog-reviewer.md",
                         "skill-content/product-planning/SKILL.md",
                         "skill-content/product-planning/references/structured-records.md",
                         "skill-content/qa-verification/SKILL.md",
                         "skill-content/qa-verification/references/test-design.md",
                         "skill-content/backlog-plan/SKILL.md",
                         "skill-content/delivery-plan/SKILL.md"):
            with self.subTest(path=relative):
                text = (TEAM / relative).read_text(encoding="utf-8")
                self.assertNotIn(SWITCH, text)
                self.assertNotIn("level_reason", text)


class FieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cost.CostProject(self)

    def test_the_check_refuses_a_level_outside_the_three_values_at_every_value(self):
        for policy in ((), ((SWITCH, VALUE),)):
            for fields in (("level: smoke",), ("level: Unit",), ("level:",), ("level: unit, live",)):
                with self.subTest(policy=policy, fields=fields):
                    fixture = cost.CostProject(self)
                    if policy:
                        fixture.choose(*policy)
                    fixture.state(1, *fields)
                    result = fixture.result()
                    self.assertFalse(result["ok"])
                    self.assertIn(INVALID, " ".join(result["errors"]))

    def test_a_repeated_level_or_reason_fails_like_any_scenario_field_and_a_blank_reason_does_not(self):
        for fields, repeated in ((("level: live", "level: unit"), "level"),
                                 (("level: live", REASON, "level_reason: another"), "level_reason")):
            with self.subTest(repeated=repeated):
                fixture = cost.CostProject(self)
                fixture.state(1, *fields)
                result = fixture.result()
                self.assertFalse(result["ok"])
                self.assertIn(f"scenario ST-001-TS-001 repeats {repeated}", " ".join(result["errors"]))
        self.fx.state(1, "level: live", "level_reason:")
        self.assertTrue(self.fx.result()["ok"])

    def test_a_stated_level_and_reason_check_as_before_at_off(self):
        released = self.fx.check()
        self.assertNotIn(SWITCH, released[1])
        for level in ("unit", "fixture", "live"):
            with self.subTest(level=level):
                fixture = cost.CostProject(self)
                fixture.state(1, f"level: {level}", REASON)
                fixture.state(2, f"level: {level}")
                self.assertEqual(fixture.check(), released)
        # A policy that leaves the switch at its default reads nothing either.
        self.fx.state(1, "level: live")
        self.fx.choose(("review_panels", "lens_panel"))
        self.assertEqual(self.fx.check(), released)
        self.fx.choose((SWITCH, VALUE))
        self.assertIn(SWITCH, json.loads(self.fx.check()[1]))
        self.fx.choose()
        self.assertEqual(self.fx.check(), released)


class ListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cost.CostProject(self)

    def test_a_scenario_without_a_level_is_listed_and_never_fails_the_check(self):
        released = self.fx.check()
        self.fx.choose((SWITCH, VALUE))
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["ok"], result["errors"]), (0, True, []))
        self.assertEqual(result[SWITCH], gaps(without_level=[entry("ST-001"), entry("ST-002")]))
        # Apart from the block, the output is the released one.
        self.assertEqual({key: value for key, value in result.items() if key != SWITCH},
                         json.loads(released[1]))

    def test_a_fixture_or_live_scenario_without_a_reason_is_listed_and_a_unit_one_is_not(self):
        cases = (
            ((), [entry("ST-001")], []),
            (("level: unit",), [], []),
            (("level: unit", "level_reason: unneeded for a unit scenario"), [], []),
            (("level: fixture",), [], [entry("ST-001", level="fixture")]),
            (("level: live",), [], [entry("ST-001", level="live")]),
            (("level: live", "level_reason:"), [], [entry("ST-001", level="live")]),
            (("level: fixture", "level_reason: the real entry point reads the rule"), [], []),
            (("level: live", REASON), [], []),
            # A level outside the three values fails the check and is listed nowhere.
            (("level: smoke",), [], []),
        )
        for fields, without_level, without_reason in cases:
            with self.subTest(fields=fields):
                fixture = cost.CostProject(self)
                fixture.state(2, "level: unit")
                fixture.choose((SWITCH, VALUE))
                fixture.state(1, *fields)
                result = fixture.result()
                self.assertEqual(result[SWITCH], gaps(without_level, without_reason))
                self.assertEqual(result["ok"], fields != ("level: smoke",))

    def test_a_manual_scenario_is_never_listed(self):
        path = self.fx.plan(1)
        path.write_text(path.read_text(encoding="utf-8").replace("- automation: required", "- automation: manual"),
                        encoding="utf-8")
        self.fx.state(2, "level: unit")
        self.fx.choose((SWITCH, VALUE))
        self.assertEqual(self.fx.result()[SWITCH], gaps())
        self.fx.state(1, "level: live")
        self.assertEqual(self.fx.result()[SWITCH], gaps())

    def test_a_draft_policy_is_refused_never_read(self):
        self.fx.choose((SWITCH, VALUE))
        self.fx.policy("begin-revision")
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertTrue(any("is a draft" in error for error in result["errors"]), result["errors"])
        self.assertNotIn(SWITCH, result)
        with self.assertRaisesRegex(backlog_review_inputs.InputError, "is a draft"):
            backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")


class SerialRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cost.CostProject(self)
        self.fx.state(1, "rows: 260", "level: live", REASON)
        self.fx.state(2, "rows: 300")

    def flagged(self) -> list[dict]:
        return self.fx.result()["test_cost"]["serial_row_scenarios"]

    def test_a_flagged_scenario_names_its_level_only_while_levels_are_declared(self):
        self.fx.choose((cost.SWITCH, cost.VALUE), rows=100)
        self.assertEqual(self.flagged(), [cost.flag("ST-001", 260), cost.flag("ST-002", 300)])
        self.fx.choose((cost.SWITCH, cost.VALUE), (SWITCH, VALUE), rows=100)
        self.assertEqual(self.flagged(), [{**cost.flag("ST-001", 260), "level": "live"},
                                          {**cost.flag("ST-002", 300), "level": None}])
        # Without test_cost_budget there is no serial-row list to name a level in.
        self.fx.choose((SWITCH, VALUE))
        self.assertNotIn("test_cost", self.fx.result())


class ReviewManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cost.CostProject(self, build=size.two_epic_backlog)
        # EP-001 holds ST-001 and ST-002, EP-002 holds ST-003.
        self.fx.state(1, "level: live")
        plan = self.fx.docs / "backlog/epics/second/stories/st-003/test-plan.md"
        plan.write_text(cost.with_rows(plan.read_text(encoding="utf-8"), "level: unit"), encoding="utf-8")

    def manifest(self, epic: str | None = None) -> dict:
        return backlog_review_inputs.manifest(self.fx.docs, epic=epic)

    def test_the_manifest_carries_the_gaps_of_its_scope_only_at_declared(self):
        plain = self.manifest("EP-001")
        self.assertNotIn("check", plain)
        self.fx.choose((SWITCH, VALUE))
        epic = self.manifest("EP-001")
        both = gaps(without_level=[entry("ST-002")], without_level_reason=[entry("ST-001", level="live")])
        self.assertEqual(sorted(epic["check"]), [SWITCH])
        self.assertEqual(epic["check"][SWITCH], both)
        self.assertEqual(self.manifest("EP-002")["check"][SWITCH], gaps())
        self.assertEqual(self.manifest()["check"][SWITCH], both)
        # The block binds into the hash, so a switch change makes every earlier manifest stale.
        self.assertNotEqual(epic["source_hash"], plain["source_hash"])
        self.fx.choose()
        self.assertEqual(self.manifest("EP-001"), plain)

    def test_the_manifest_names_each_level_in_the_serial_row_flags_it_carries(self):
        self.fx.state(2, "rows: 300")
        self.fx.choose((cost.SWITCH, cost.VALUE), (SWITCH, VALUE), rows=50)
        epic = self.manifest("EP-001")
        self.assertEqual(sorted(epic["check"]), ["test_cost", SWITCH])
        self.assertEqual(epic["check"]["test_cost"]["serial_row_scenarios"],
                         [{**cost.flag("ST-002", 300), "level": None}])
        self.assertEqual(self.manifest()["check"]["test_cost"], epic["check"]["test_cost"])


def leveled_backlog_with_dod(fixture: cost.CostProject) -> None:
    author = backlog_fixture._author_story

    def leveled(story, test_plan, story_id):
        author(story, test_plan, story_id)
        test_plan.write_text(cost.with_rows(test_plan.read_text(encoding="utf-8"), "rows: 260",
                                            "level: live", REASON), encoding="utf-8")

    with mock.patch.object(backlog_fixture, "_author_story", leveled):
        size.committed_backlog_with_dod(fixture)


class DeliveryProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cost.CostProject(self, build=leveled_backlog_with_dod)

    def propose(self, slug: str) -> dict:
        args = SimpleNamespace(docs=str(self.fx.docs), id=None, slug=slug, goal="Authenticate",
                               outcome="Users sign in", target_branch="main", story=["ST-001"])
        code, output = size.quiet(delivery_compile.init_delivery, args)
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def test_init_names_the_level_of_a_listed_scenario_only_while_levels_are_declared(self):
        released = self.propose("released")
        self.fx.choose((cost.SWITCH, cost.VALUE), rows=100)
        self.assertEqual(self.propose("rows")["test_cost"]["serial_row_scenarios"],
                         [cost.flag("ST-001", 260)])
        self.fx.choose((cost.SWITCH, cost.VALUE), (SWITCH, VALUE), rows=100)
        self.assertEqual(self.propose("levels")["test_cost"]["serial_row_scenarios"],
                         [{**cost.flag("ST-001", 260), "level": "live"}])
        # Levels alone add nothing to the proposal.
        self.fx.choose((SWITCH, VALUE))
        self.assertEqual(sorted(self.propose("declared")), sorted(released))


class TaskBindingTests(unittest.TestCase):
    """The reference binds to every task of Backlog Planning at `declared`, and to no other."""

    TASKS = (("backlog-plan", "product-owner", "revise"), ("backlog-plan", "qa-engineer", "revise"),
             ("backlog-plan", "business-analyst", "revise"), ("backlog-plan", "backlog-reviewer", "review"),
             ("delivery-plan", "product-owner", "revise"), ("delivery-plan", "delivery-coordinator", "revise"),
             ("execution-plan", "qa-engineer", "revise"), ("deliver", "qa-engineer", "review"))

    def setUp(self) -> None:
        self.fx = cost.CostProject(self, build=size.committed_brief)

    def bound(self) -> dict:
        return {task: REFERENCE in task_inputs.manifest(entry=task[0], role=task[1], mode=task[2],
                                                        project=self.fx.root)["required_reads"]
                for task in self.TASKS}

    def test_only_backlog_planning_tasks_bind_the_reference_and_only_at_declared(self):
        self.assertFalse(any(self.bound().values()))
        self.fx.choose(("review_panels", "lens_panel"))
        self.assertFalse(any(self.bound().values()))
        self.fx.choose((SWITCH, VALUE))
        for task, bound in self.bound().items():
            with self.subTest(task=task):
                self.assertEqual(bound, task[0] == "backlog-plan")
        self.fx.choose()
        self.assertFalse(any(self.bound().values()))


def all_switches_on_backlog(fixture: cost.CostProject) -> None:
    """Approve a Process Policy with every switch on, then build the approved backlog under it."""
    fixture.policy("init")
    for switch, spec in process_policy.load_registry().items():
        fixture.policy("set", "--switch", switch, "--value",
                       next(value for value in spec["values"] if value != spec["default"]))
    for parameter, limit in all_on.LIMITS.items():
        fixture.policy("set", "--switch", "story_size_budget", "--parameter", parameter,
                       "--value", str(limit))
    fixture.policy("set", "--switch", "root_review_scope", "--parameter",
                   "max_delta_share_percent", "--value", "50")
    fixture.policy("set", "--switch", cost.SWITCH, "--parameter", "serial_rows",
                   "--value", str(all_on.SERIAL_ROWS))
    fixture.policy("set", "--switch", "item_review_scale", "--parameter", "changed_lines",
                   "--value", "200")
    fixture.policy("approve")
    size.approved_backlog(fixture)


class AllSwitchesOnTests(unittest.TestCase):
    """The switch beside every other switch at its non-default value at once."""

    def setUp(self) -> None:
        self.fx = cost.CostProject(self, build=all_switches_on_backlog)

    def test_the_check_and_the_epic_manifest_carry_the_list_with_every_switch_on(self):
        self.assertEqual(self.fx.policy("value", "--switch", SWITCH)["value"], VALUE)
        self.fx.state(1, "rows: 300", "level: live", REASON)
        result = self.fx.result()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result[SWITCH], gaps(without_level=[entry("ST-002")]))
        self.assertEqual(result["test_cost"]["serial_row_scenarios"],
                         [{**cost.flag("ST-001", 300), "level": "live"}])
        manifest = backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")
        self.assertEqual(manifest["check"][SWITCH], result[SWITCH])
        self.assertEqual(manifest["check"]["test_cost"], result["test_cost"])
        self.assertEqual(sorted(manifest["check"]), ["counts", "relation_audit", "review_note",
                                                     "source_errors", "stories", "story_size",
                                                     "test_cost", SWITCH])
        self.assertEqual(backlog_review_inputs.manifest(
            self.fx.docs, epic="EP-001", expected_hash=manifest["source_hash"]), manifest)


if __name__ == "__main__":
    unittest.main()
