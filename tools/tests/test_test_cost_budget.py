"""Process switch test_cost_budget: a Test Plan scenario may state the rows
its automation target runs and how they are split; at `flag_serial_rows` the
backlog compiler, the review manifests and the Delivery proposal list each
automation-required scenario that runs more rows serially than the owner's
limit, and never fail a check (#385)."""

from __future__ import annotations

import json
import re
import sys
import unittest
from tools.tests.levels import integration
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import backlog_fixture  # noqa: E402
import backlog_review_inputs  # noqa: E402
import delivery_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import test_story_size_budget as size  # noqa: E402

SWITCH = "test_cost_budget"
VALUE = "flag_serial_rows"
REFERENCE = "skill-content/product-planning/references/switch-test_cost_budget-flag_serial_rows.md"
LIMITS = "skill-content/product-planning/data/test-cost-limits.json"
TARGET_LINE = re.compile(r"(?m)^- automation_target: .*$")


def with_rows(text: str, *fields: str) -> str:
    """A Test Plan whose first scenario also states *fields* below its automation target."""
    return TARGET_LINE.sub(lambda match: match.group(0) + "".join(f"\n- {field}" for field in fields), text, 1)


class CostProject(size.Project):
    def choose(self, *values: tuple[str, str], rows: int | None = None) -> None:
        """Approve a policy revision that sets exactly these values and the serial-row limit *rows*."""
        if process_policy.path_for(self.docs).exists():
            self.policy("begin-revision")
            for switch in process_policy.table_rows(
                    process_policy.parse(process_policy.path_for(self.docs))[1])[0]:
                self.policy("set", "--switch", switch, "--default")
        else:
            self.policy("init")
        for switch, value in values:
            self.policy("set", "--switch", switch, "--value", value)
        if rows is not None:
            self.policy("set", "--switch", SWITCH, "--parameter", "serial_rows", "--value", str(rows))
        self.policy("approve")

    def plan(self, number: int) -> Path:
        return self.story_path(number).with_name("test-plan.md")

    def state(self, number: int, *fields: str) -> None:
        path = self.plan(number)
        path.write_text(with_rows(path.read_text(encoding="utf-8"), *fields), encoding="utf-8")

    def result(self) -> dict:
        code, output = self.check()
        result = json.loads(output)
        self.test.assertEqual((code, result["errors"]), (0 if result["ok"] else 1, result["errors"]))
        return result


def flag(story: str, rows: int, split: str | None = None) -> dict:
    return {"story": story, "scenario": f"{story}-TS-001",
            "automation_target": f"tests/{story.lower().replace('-', '_')}.py::test_{story.lower().replace('-', '_')}",
            "rows": rows, "row_split": split}


class RegistryTests(unittest.TestCase):
    def test_the_switch_takes_one_owner_set_limit_only_at_flag_serial_rows(self):
        spec = process_policy.load_registry()[SWITCH]
        self.assertEqual((spec["default"], [value for value in spec["values"]]), ("off", ["off", VALUE]))
        self.assertEqual((spec["parameters"]["values"], sorted(spec["parameters"]["ids"]),
                          spec["parameters"]["type"]), ([VALUE], ["serial_rows"], "positive_integer"))


class FieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CostProject(self)

    def test_the_check_refuses_invalid_rows_and_splits_at_every_value(self):
        for policy in ((), ((SWITCH, VALUE),)):
            for fields, message in ((("rows: 0",), "rows must be a positive integer"),
                                    (("rows: 2.5",), "rows must be a positive integer"),
                                    (("rows: many",), "rows must be a positive integer"),
                                    (("rows: 10", "row_split: parallel"),
                                     "row_split must be one of serial, sharded, grouped")):
                with self.subTest(policy=policy, fields=fields):
                    fixture = CostProject(self)
                    if policy:
                        fixture.choose(*policy, rows=5)
                    fixture.state(1, *fields)
                    result = fixture.result()
                    self.assertFalse(result["ok"])
                    self.assertIn("scenario ST-001-TS-001 " + message, " ".join(result["errors"]))

    def test_valid_fields_check_as_before_at_off(self):
        released = self.fx.check()
        self.assertNotIn("test_cost", released[1])
        self.fx.state(1, "rows: 260", "row_split: serial")
        self.assertEqual(self.fx.check(), released)


class FlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CostProject(self)

    def test_a_scenario_over_the_limit_that_runs_serially_is_listed_and_never_fails_the_check(self):
        self.fx.state(1, "rows: 260")
        self.fx.state(2, "rows: 260", "row_split: sharded")
        released = self.fx.check()
        self.fx.choose((SWITCH, VALUE), rows=100)
        result = self.fx.result()
        self.assertTrue(result["ok"])
        self.assertEqual(result["test_cost"], {"switch": SWITCH, "value": VALUE, "limits": {"serial_rows": 100},
                                               "serial_row_scenarios": [flag("ST-001", 260)]})
        # Apart from the block, the output is the released one.
        self.assertEqual({key: item for key, item in result.items() if key != "test_cost"}, json.loads(released[1]))

    def test_only_a_serial_or_unsplit_scenario_above_the_limit_is_listed(self):
        self.fx.choose((SWITCH, VALUE), rows=100)
        for fields, listed in ((("rows: 100",), False), (("rows: 101",), True),
                               (("rows: 101", "row_split: serial"), True),
                               (("rows: 500", "row_split: grouped"), False),
                               (("rows: 500", "row_split: sharded"), False), ((), False)):
            with self.subTest(fields=fields):
                fixture = CostProject(self)
                fixture.choose((SWITCH, VALUE), rows=100)
                fixture.state(1, *fields)
                scenarios = fixture.result()["test_cost"]["serial_row_scenarios"]
                self.assertEqual([entry["scenario"] for entry in scenarios], ["ST-001-TS-001"] if listed else [])

    def test_a_manual_scenario_is_never_listed(self):
        path = self.fx.plan(1)
        path.write_text(path.read_text(encoding="utf-8").replace("- automation: required", "- automation: manual"),
                        encoding="utf-8")
        self.fx.state(1, "rows: 999")
        self.fx.choose((SWITCH, VALUE), rows=1)
        self.assertEqual(self.fx.result()["test_cost"]["serial_row_scenarios"], [])


class ReviewManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CostProject(self, build=size.two_epic_backlog)

    def test_the_manifest_carries_the_flags_of_its_scope_only_at_flag_serial_rows(self):
        self.fx.state(1, "rows: 300")
        plain = backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")
        self.assertNotIn("check", plain)
        self.fx.choose((SWITCH, VALUE), rows=50)
        epic = backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")
        self.assertEqual(sorted(epic["check"]), ["test_cost"])
        self.assertEqual(epic["check"]["test_cost"]["serial_row_scenarios"], [flag("ST-001", 300)])
        second = backlog_review_inputs.manifest(self.fx.docs, epic="EP-002")
        self.assertEqual(second["check"]["test_cost"]["serial_row_scenarios"], [])
        root = backlog_review_inputs.manifest(self.fx.docs)
        self.assertEqual(root["check"]["test_cost"]["serial_row_scenarios"], [flag("ST-001", 300)])
        self.fx.choose()
        self.assertEqual(backlog_review_inputs.manifest(self.fx.docs, epic="EP-001"), plain)


def flagged_backlog_with_dod(fixture: CostProject) -> None:
    author = backlog_fixture._author_story

    def counted(story, test_plan, story_id):
        author(story, test_plan, story_id)
        test_plan.write_text(with_rows(test_plan.read_text(encoding="utf-8"), "rows: 260"), encoding="utf-8")

    with mock.patch.object(backlog_fixture, "_author_story", counted):
        size.committed_backlog_with_dod(fixture)


@integration
class DeliveryProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CostProject(self, build=flagged_backlog_with_dod)

    def propose(self, slug: str) -> dict:
        args = SimpleNamespace(docs=str(self.fx.docs), id=None, slug=slug, goal="Authenticate",
                               outcome="Users sign in", target_branch="main", story=["ST-001"])
        code, output = size.quiet(delivery_compile.init_delivery, args)
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def test_init_lists_the_selected_stories_flagged_scenarios_only_at_flag_serial_rows(self):
        released = self.propose("released")
        self.assertEqual(sorted(released), ["id", "ok", "path", "slug", "stories"])
        self.fx.choose((SWITCH, VALUE), rows=100)
        result = self.propose("flagged")
        self.assertEqual(result["test_cost"], {"switch": SWITCH, "value": VALUE, "limits": {"serial_rows": 100},
                                               "serial_row_scenarios": [flag("ST-001", 260)]})
        items = sorted(self.fx.docs.glob("delivery/deliveries/*/items/st-001/item.md"))
        first, second = (delivery_compile.split_note(path)[0] for path in items)
        self.assertEqual(sorted(first), sorted(second))


@integration
class TaskBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CostProject(self, build=size.committed_brief)

    def bound(self) -> dict:
        tasks = (("backlog-plan", "product-owner", "revise"), ("backlog-plan", "qa-engineer", "revise"),
                 ("backlog-plan", "backlog-reviewer", "review"), ("delivery-plan", "product-owner", "revise"),
                 ("execution-plan", "qa-engineer", "revise"), ("deliver", "qa-engineer", "review"))
        bound = {}
        for task in tasks:
            reads = task_inputs.manifest(entry=task[0], role=task[1], mode=task[2],
                                         project=self.fx.root)["required_reads"]
            self.assertEqual(REFERENCE in reads, LIMITS in reads, task)
            bound[task] = REFERENCE in reads
        return bound

    def test_only_planning_tasks_bind_the_reference_and_only_at_flag_serial_rows(self):
        self.assertFalse(any(self.bound().values()))
        self.fx.choose((SWITCH, VALUE), rows=100)
        for task, bound in self.bound().items():
            with self.subTest(task=task):
                self.assertEqual(bound, task[0] in {"backlog-plan", "delivery-plan"})


if __name__ == "__main__":
    unittest.main()
