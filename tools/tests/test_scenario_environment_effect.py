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


if __name__ == "__main__":
    unittest.main()
