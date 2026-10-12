"""A Test Plan scenario may state how it affects the project's shared test
environment, `environment_effect`, and, only when it changes it, the parts it
`touches`, so a runner can order and parallelize live scenarios.
`backlog_compile.py check` and `approve` refuse any other value."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import test_test_cost_budget as cost  # noqa: E402

LABEL = "scenario ST-001-TS-001"


class FieldRuleTests(unittest.TestCase):
    def test_effect_plan_deduplicates_targets_and_keeps_unknown_rows_serial(self):
        def row(identifier, target, effect=None):
            fields = {"level": "live", "automation": "required", "automation_target": target}
            if effect:
                fields["environment_effect"] = effect
            return {"id": identifier, "fields": fields}
        plan = backlog_compile.live_environment_plan([
            row("first", "tests::shared", "builds_shared"), row("duplicate", "tests::shared", "builds_shared"),
            row("reader", "tests::read", "reads"), row("unknown", "tests::unknown"),
            row("last", "tests::remove", "removes")])
        self.assertEqual(plan["target_count"], 4)
        self.assertEqual(plan["shared_environment_instances"], 1)
        self.assertEqual(plan["missing_environment_effect"], ["unknown"])
        self.assertEqual([phase["effect"] for phase in plan["phases"]], ["builds_shared", "reads", "changes", "removes"])
        self.assertTrue(plan["phases"][1]["parallel"])
        self.assertTrue(plan["phases"][-1]["reset_before"])
        with self.assertRaisesRegex(ValueError, "conflicting"):
            backlog_compile.live_environment_plan([row("a", "same", "reads"), row("b", "same", "changes")])

    def findings(self, **fields: str) -> list[str]:
        return backlog_compile.environment_effect_findings(fields, LABEL)

    def test_every_declared_effect_is_accepted_and_touches_only_with_changes(self):
        for effect in ("builds", "builds_shared", "builds_fixture", "reads", "changes", "resets", "removes"):
            with self.subTest(effect=effect):
                self.assertEqual(self.findings(environment_effect=effect), [])
        self.assertEqual(self.findings(environment_effect="changes", touches="accounts, ledger/entries"), [])
        self.assertEqual(self.findings(environment_effect="changes", touches="[accounts, queue.jobs]"), [])
        self.assertEqual(self.findings(), [])

    def test_unknown_values_and_misplaced_or_malformed_touches_are_refused(self):
        effects = "builds, builds_shared, builds_fixture, reads, changes, resets, removes"
        cases = (
            ({"environment_effect": "writes"}, [f"{LABEL} environment_effect must be one of {effects}, not writes"]),
            ({"environment_effect": ""}, [f"{LABEL} environment_effect must be one of {effects}, not (empty)"]),
            ({"environment_effect": "builds_fixture", "touches": "accounts"},
             [f"{LABEL} touches is allowed only with environment_effect: changes"]),
            ({"environment_effect": "reads", "touches": "accounts"},
             [f"{LABEL} touches is allowed only with environment_effect: changes"]),
            ({"touches": "accounts"}, [f"{LABEL} touches is allowed only with environment_effect: changes"]),
            ({"environment_effect": "changes", "touches": ""}, [f"{LABEL} touches must list at least one name"]),
            ({"environment_effect": "changes", "touches": "accounts, two words"},
             [f"{LABEL} touches names must be letters, digits, '.', '_', '/' or '-': two words"]),
            ({"environment_effect": "changes", "touches": "accounts, accounts"}, [f"{LABEL} repeats a touches name"]),
        )
        self.assertTrue(self.findings(environment_effect="changes", touches="accounts,,ledger"))
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(self.findings(**fields), expected)

    def test_touches_may_list_its_names_as_nested_bullets(self):
        fields, duplicates = backlog_compile.scenario_fields(
            "\n- environment_effect: changes\n- touches:\n  - accounts\n  - ledger\n- Given: a\n")
        self.assertEqual((fields["touches"], duplicates), ("accounts, ledger", []))
        self.assertEqual(backlog_compile.environment_effect_findings(fields, LABEL), [])


class ParameterCaseTests(unittest.TestCase):
    def findings(self, **fields: str) -> list[str]:
        return backlog_compile.environment_effect_findings(fields, LABEL)

    def test_plan_preserves_parameter_cases_and_orders_fixture_ranks(self):
        rows = [{"id": "one", "fields": {"level": "live", "automation": "required", "automation_target": "test",
                 "environment_effect": "builds_fixture:20 [later], builds_fixture:1 [first], reads [read]"}}]
        plan = backlog_compile.live_environment_plan(rows)
        self.assertEqual([row["parameter"] for row in plan["phases"][0]["targets"]], ["first", "later"])
        self.assertEqual(plan["target_count"], 3)

    def test_a_list_of_effect_items_naming_parameter_cases_is_accepted(self):
        for value in ("builds_shared [core], builds [full]", "reads, changes [full]",
                      "builds [core], reads [full], resets [other]", "changes [a, b]",
                      "reads [a b],builds [c]", "reads [core]"):
            with self.subTest(value=value):
                self.assertEqual(self.findings(environment_effect=value), [])

    def test_a_parameter_named_twice_two_default_items_and_malformed_items_are_refused(self):
        effects = "builds, builds_shared, builds_fixture, reads, changes, resets, removes"
        refused = f"{LABEL} environment_effect must be one of {effects}, not "
        cases = (
            ("builds [core], reads [core]", [f"{LABEL} environment_effect names parameter core twice"]),
            ("builds [core], reads [ core ]", [f"{LABEL} environment_effect names parameter core twice"]),
            ("builds, reads", [f"{LABEL} environment_effect has more than one item without a parameter"]),
            ("builds, reads [core], resets", [f"{LABEL} environment_effect has more than one item without a parameter"]),
            ("reading [core]", [refused + "reading [core]"]),
            ("reads [core", [refused + "reads [core"]),
            ("reads [core]x", [refused + "reads [core]x"]),
            ("builds [core],", [refused + "(empty)"]),
            ("builds_shared [core], writes [full]", [refused + "writes [full]"]),
        )
        self.assertTrue(self.findings(environment_effect="reads []"))
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(self.findings(environment_effect=value), expected)

    def test_touches_is_allowed_when_any_item_is_changes_and_refused_otherwise(self):
        self.assertEqual(self.findings(environment_effect="reads, changes [x]", touches="accounts"), [])
        self.assertEqual(self.findings(environment_effect="changes [x], reads [y]", touches="accounts"), [])
        self.assertEqual(
            self.findings(environment_effect="reads, builds [x]", touches="accounts"),
            [f"{LABEL} touches is allowed only when an environment_effect item is changes"])
        self.assertEqual(
            self.findings(environment_effect="reads", touches="accounts"),
            [f"{LABEL} touches is allowed only with environment_effect: changes"])

    def test_a_plain_single_effect_is_judged_as_before(self):
        effects = "builds, builds_shared, builds_fixture, reads, changes, resets, removes"
        for effect in ("builds", "builds_shared", "reads", "changes", "resets", "removes"):
            self.assertEqual(self.findings(environment_effect=effect), [])
        for value, shown in (("writes", "writes"), ("", "(empty)"), ("reads reads", "reads reads")):
            self.assertEqual(self.findings(environment_effect=value),
                             [f"{LABEL} environment_effect must be one of {effects}, not {shown}"])


class FixtureRankTests(unittest.TestCase):
    """builds_fixture may carry a rank 0..999, `builds_fixture:<rank>`, also inside a parameter list;
    a plain builds_fixture ranks 50, and no other effect takes a rank."""

    def findings(self, effect: str) -> list[str]:
        return backlog_compile.environment_effect_findings({"environment_effect": effect}, LABEL)

    def test_a_ranked_or_plain_builds_fixture_is_accepted_alone_and_in_a_parameter_list(self):
        for effect in ("builds_fixture", "builds_fixture:0", "builds_fixture:50", "builds_fixture:999",
                       "builds_fixture:10 [core], builds [full]", "builds_shared [core], builds_fixture:7 [full]",
                       "builds_fixture:3, reads [smoke]"):
            with self.subTest(effect=effect):
                self.assertEqual(self.findings(effect), [])
        self.assertEqual([backlog_compile.fixture_rank(item) for item in
                          ("builds_fixture", "builds_fixture:0", "builds_fixture:999 [full]", "reads")],
                         [50, 0, 999, None])

    def test_a_rank_out_of_range_or_on_another_effect_is_refused(self):
        rank = f"{LABEL} environment_effect builds_fixture rank must be an integer from 0 to 999, not "
        cases = (
            ("builds_fixture:1000", [rank + "1000"]),
            ("builds_fixture:-1", [rank + "-1"]),
            ("builds_fixture:x", [rank + "x"]),
            ("builds_fixture:", [rank + "(empty)"]),
            ("builds_shared [core], builds_fixture:1000 [full]", [rank + "1000"]),
            ("reads:5", [f"{LABEL} environment_effect takes a rank only on builds_fixture, not reads:5"]),
            ("builds_fixture:5 [core], builds_shared:2 [full]",
             [f"{LABEL} environment_effect takes a rank only on builds_fixture, not builds_shared:2 [full]"]),
        )
        for effect, expected in cases:
            with self.subTest(effect=effect):
                self.assertEqual(self.findings(effect), expected)


class CheckTests(unittest.TestCase):
    def test_check_accepts_the_bullets_and_refuses_an_unknown_effect(self):
        fixture = cost.CostProject(self)
        released = fixture.check()
        self.assertEqual(json.loads(released[1])["ok"], True, released[1])
        fixture.state(1, "environment_effect: changes", "touches: accounts, ledger")
        self.assertEqual(fixture.check(), released)
        for fields, message in ((("environment_effect: mutates",), "environment_effect must be one of"),
                                (("environment_effect: reads", "touches: accounts"),
                                 "touches is allowed only with environment_effect: changes")):
            with self.subTest(fields=fields):
                fixture = cost.CostProject(self)
                fixture.state(1, *fields)
                result = fixture.result()
                self.assertFalse(result["ok"])
                self.assertIn(f"{LABEL} {message}", " ".join(result["errors"]))

    def test_check_accepts_parameter_case_effects_and_refuses_a_repeated_parameter(self):
        fixture = cost.CostProject(self)
        released = fixture.check()
        fixture.state(1, "environment_effect: builds_shared [core], changes [full]", "touches: accounts")
        self.assertEqual(fixture.check(), released)
        for fields, message in ((("environment_effect: builds [core], reads [core]",),
                                 "environment_effect names parameter core twice"),
                                (("environment_effect: builds, reads",),
                                 "environment_effect has more than one item without a parameter"),
                                (("environment_effect: reads [core",), "environment_effect must be one of"),
                                (("environment_effect: reads, builds [full]", "touches: accounts"),
                                 "touches is allowed only when an environment_effect item is changes")):
            with self.subTest(fields=fields):
                fixture = cost.CostProject(self)
                fixture.state(1, *fields)
                result = fixture.result()
                self.assertFalse(result["ok"])
                self.assertIn(f"{LABEL} {message}", " ".join(result["errors"]))


if __name__ == "__main__":
    unittest.main()
