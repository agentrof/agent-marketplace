"""Process Policy: the governed project document that records explicit
process switch choices over the package registry's defaults."""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))

import process_policy  # noqa: E402


FIXTURE_REGISTRY = {
    "schema_version": 1,
    "switches": {
        "fixture_mode": {
            "summary": "How the fixture step runs.",
            "flows": ["operation"],
            "values": [
                {"id": "current", "tradeoffs": "Today's behaviour."},
                {"id": "fast", "tradeoffs": "Fewer passes, unmeasured recall."},
            ],
            "default": "current",
            "metric": "Minutes per fixture step.",
            "promotion": {"unit": "3 Deliveries", "threshold": "Half the baseline minutes."},
        },
        "other_mode": {
            "summary": "How another step runs.",
            "flows": ["operation"],
            "values": [
                {"id": "standard", "tradeoffs": "Today's behaviour."},
                {"id": "light", "tradeoffs": "One gate instead of two."},
            ],
            "default": "standard",
            "metric": "Minutes to plan publication.",
            "promotion": {"unit": "3 Deliveries", "threshold": "At most 30 minutes."},
        },
    },
}


def run(*argv: str) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main(list(argv))
    return code, json.loads(output.getvalue())


class ProcessPolicyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.package = base / "package"
        self.write_registry(FIXTURE_REGISTRY)
        self.docs = base / "workspace" / "docs"
        self.docs.mkdir(parents=True)
        patcher = mock.patch.object(process_policy, "PACKAGE", self.package)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_registry(self, registry: dict) -> None:
        path = self.package / process_policy.REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    def cli(self, *argv: str) -> tuple[int, dict]:
        return run(argv[0], "--docs", str(self.docs), *argv[1:])

    @property
    def path(self) -> Path:
        return self.docs / process_policy.RELATIVE

    def approved_with(self, **values: str) -> None:
        self.assertEqual(self.cli("init")[0], 0)
        for switch, value in values.items():
            self.assertEqual(self.cli("set", "--switch", switch, "--value", value)[0], 0)
        self.assertEqual(self.cli("approve")[0], 0)

    def test_no_document_means_every_switch_follows_its_default(self):
        self.assertEqual(self.cli("check"), (0, {"ok": True, "exists": False,
                                                 "path": "operation/process-policy.md",
                                                 "errors": []}))
        self.assertEqual(process_policy.approved_snapshot(self.docs), ({}, []))
        code, result = self.cli("value", "--switch", "fixture_mode")
        self.assertEqual(code, 0)
        self.assertEqual((result["value"], result["source"], result["policy"]),
                         ("current", "default", None))

    def test_init_set_approve_and_revise_follow_one_lifecycle(self):
        code, result = self.cli("init")
        self.assertEqual((code, result["status"], result["revision"]), (0, "draft", 1))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("| Switch | Value |\n| --- | --- |\n", text)
        self.assertIn("A switch without a row follows its package default.", text)
        self.assertEqual(self.cli("init")[0], 1)
        # A draft is not in force: readers refuse it instead of guessing a value.
        code, result = self.cli("value", "--switch", "fixture_mode")
        self.assertEqual(code, 1)
        self.assertIn("Process Policy revision 1 is a draft", result["errors"][0])

        code, result = self.cli("set", "--switch", "fixture_mode", "--value", "fast")
        self.assertEqual((code, result["value"], result["source"], result["changed"]),
                         (0, "fast", "policy", True))
        self.assertIn("| `fixture_mode` | `fast` |", self.path.read_text(encoding="utf-8"))
        code, result = self.cli("approve")
        self.assertEqual((code, result["revision"]), (0, 1))
        props, body = process_policy.parse(self.path)
        self.assertEqual(props["status"], "approved")
        self.assertEqual(props["tags"], ["doc/process-policy", "status/approved"])
        self.assertEqual(props["source_hash"], process_policy.policy_hash(props, body))
        self.assertEqual(process_policy.approved_snapshot(self.docs), ({
            "process_policy_path": "operation/process-policy.md",
            "process_policy_revision": 1,
            "process_policy_source_hash": props["source_hash"],
        }, []))
        code, result = self.cli("value", "--switch", "fixture_mode")
        self.assertEqual((result["value"], result["source"]), ("fast", "policy"))
        self.assertEqual(result["policy"]["process_policy_revision"], 1)

        code, result = self.cli("set", "--switch", "other_mode", "--value", "light")
        self.assertEqual(code, 1)
        self.assertIn("changes only in a revision", result["errors"][0])
        self.assertEqual(self.cli("approve")[0], 1)
        code, result = self.cli("begin-revision")
        self.assertEqual((code, result["revision"]), (0, 2))
        self.assertEqual(process_policy.parse(self.path)[0]["status"], "draft")
        self.assertNotIn("source_hash", process_policy.parse(self.path)[0])

    def test_a_row_records_only_an_explicit_non_default_choice(self):
        self.approved_with(fixture_mode="fast", other_mode="light")
        self.cli("begin-revision")
        before = self.path.read_bytes()
        code, result = self.cli("set", "--switch", "other_mode", "--value", "light")
        self.assertEqual((code, result["changed"]), (0, False))
        self.assertEqual(self.path.read_bytes(), before)
        # Choosing the default removes the row, so a later promoted default applies.
        code, result = self.cli("set", "--switch", "fixture_mode", "--value", "current")
        self.assertEqual((code, result["source"], result["changed"]), (0, "default", True))
        code, result = self.cli("set", "--switch", "other_mode", "--default")
        self.assertEqual((code, result["value"], result["source"]), (0, "standard", "default"))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("`fixture_mode`", text)
        self.assertNotIn("`other_mode`", text)

    def test_rows_are_validated_against_the_registry(self):
        self.approved_with(fixture_mode="fast")
        for switch, value, fragment in (
                ("ghost_mode", "fast", "switch 'ghost_mode' is not declared by this package"),
                ("fixture_mode", "ghost", "switch 'fixture_mode' has no value 'ghost'")):
            with self.subTest(fragment=fragment):
                self.cli("begin-revision")
                code, result = self.cli("set", "--switch", switch, "--value", value)
                self.assertEqual(code, 1)
                self.assertIn(fragment, result["errors"][0])
                self.assertEqual(self.cli("approve")[0], 0)

    def test_hand_edits_and_malformed_tables_are_refused(self):
        self.approved_with(fixture_mode="fast")
        original = self.path.read_text(encoding="utf-8")
        cases = (
            (original.replace("`fast`", "`current`"), "approved Process Policy source_hash is stale"),
            (original.replace("| `fixture_mode` | `fast` |",
                              "| `fixture_mode` | `fast` |\n| `fixture_mode` | `current` |"),
             "switch 'fixture_mode' has more than one row"),
            (original.replace("| Switch | Value |", "| Name | Value |"),
             "Switches table must start with the header | Switch | Value |"),
            (original.replace("| `fixture_mode` | `fast` |", "| `fixture_mode` |"),
             "Switches row must hold a switch and a value"),
            (original.replace("## Switches", "## Choices"), "missing sections: Switches"),
            (original.replace("`fast`", "`ghost`"), "switch 'fixture_mode' has no value 'ghost'"),
            (original.replace("type: process-policy", "type: definition-of-done"),
             "type must be process-policy"),
            (original.replace("revision: 1", "revision: 0"), "revision must be a positive integer"),
        )
        for text, fragment in cases:
            with self.subTest(fragment=fragment):
                self.path.write_text(text, encoding="utf-8")
                code, result = self.cli("check")
                self.assertEqual(code, 1)
                self.assertTrue(any(fragment in error for error in result["errors"]),
                                result["errors"])
                snapshot, errors = process_policy.approved_snapshot(self.docs)
                self.assertEqual(snapshot, {})
                self.assertTrue(errors)
                with self.assertRaises(ValueError):
                    process_policy.effective_values(self.docs)

    def test_a_row_for_a_retired_switch_is_repaired_through_a_revision(self):
        self.approved_with(fixture_mode="fast", other_mode="light")
        registry = json.loads(json.dumps(FIXTURE_REGISTRY))
        del registry["switches"]["other_mode"]
        self.write_registry(registry)
        code, result = self.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("switch 'other_mode' is not declared by this package", result["errors"])
        # /configure process takes the rows to repair from the switch listing.
        code, listed = self.cli("switches")
        self.assertEqual((code, listed["undeclared"]),
                         (1, {"switches": ["other_mode"], "parameters": []}))
        self.assertEqual(self.cli("approve")[0], 1)
        self.assertEqual(self.cli("begin-revision")[0], 0)
        self.assertEqual(self.cli("set", "--switch", "other_mode", "--value", "light")[0], 1)
        code, result = self.cli("set", "--switch", "other_mode", "--default")
        self.assertEqual((code, result["changed"]), (0, True))
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertEqual(self.cli("check")[0], 0)
        self.assertNotIn("undeclared", self.cli("switches")[1])

    def test_switches_lists_the_choice_gate_text_and_the_value_in_force(self):
        self.approved_with(other_mode="light")
        code, result = self.cli("switches")
        self.assertEqual(code, 0)
        listed = {item["switch"]: item for item in result["switches"]}
        self.assertEqual(list(listed), ["fixture_mode", "other_mode"])
        self.assertEqual((listed["other_mode"]["value"], listed["other_mode"]["source"]),
                         ("light", "policy"))
        self.assertEqual((listed["fixture_mode"]["value"], listed["fixture_mode"]["source"]),
                         ("current", "default"))
        self.assertEqual([value["id"] for value in listed["other_mode"]["values"]],
                         ["standard", "light"])
        self.assertEqual(listed["other_mode"]["values"][1]["tradeoffs"], "One gate instead of two.")
        self.assertEqual(result["policy"]["status"], "approved")



LIMITS = "skill-content/fixture-method/data/limits.json"
PARAMETERIZED_SWITCH = {
    "summary": "Whether the fixture step is capped.",
    "flows": ["operation"],
    "values": [
        {"id": "open", "tradeoffs": "Today's behaviour."},
        {"id": "capped", "tradeoffs": "Capped by owner-set limits."},
    ],
    "default": "open",
    "parameters": {
        "summary": "Owner-set limit per fixture measure.",
        "values": ["capped"],
        "declared_by": {"path": LIMITS, "key": "measures"},
        "type": "positive_integer",
        "min_count": 1,
    },
    "metric": "Minutes per capped step.",
    "promotion": {"unit": "3 Deliveries", "threshold": "Half the baseline minutes."},
}


class ProcessPolicyParameterTests(unittest.TestCase):
    """A switch value may take owner-set parameters, validated against the
    registry's declaration; every other switch reads as it did before."""

    # The lifecycle tests' fixture, reused without inheriting their tests.
    tearDown = ProcessPolicyLifecycleTests.tearDown
    write_registry = ProcessPolicyLifecycleTests.write_registry
    cli = ProcessPolicyLifecycleTests.cli
    path = ProcessPolicyLifecycleTests.path

    def setUp(self) -> None:
        ProcessPolicyLifecycleTests.setUp(self)
        registry = json.loads(json.dumps(FIXTURE_REGISTRY))
        registry["switches"]["limit_mode"] = json.loads(json.dumps(PARAMETERIZED_SWITCH))
        self.write_registry(registry)
        limits = self.package / LIMITS
        limits.parent.mkdir(parents=True, exist_ok=True)
        limits.write_text(json.dumps({"schema_version": 1, "measures": {
            "criteria": {"summary": "Criteria per story.", "derivation": "count"},
            "scenarios": {"summary": "Scenarios per plan.", "derivation": "count"},
        }}), encoding="utf-8")

    def refused(self, *argv: str) -> str:
        code, result = self.cli(*argv)
        self.assertEqual(code, 1, result)
        return result["errors"][0]

    def test_a_value_that_takes_parameters_needs_its_minimum_and_valid_ids(self):
        self.assertEqual(self.cli("init")[0], 0)
        self.assertIn("apply only at capped; set its value first", self.refused(
            "set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12"))
        self.assertEqual(self.cli("set", "--switch", "limit_mode", "--value", "capped")[0], 0)
        self.assertIn("switch 'limit_mode' at 'capped' needs at least 1 of its parameters"
                      " ['criteria', 'scenarios']", self.refused("approve"))
        for value, fragment in (("0", "must be a positive integer, not '0'"),
                                ("-3", "must be a positive integer, not '-3'"),
                                ("1.5", "must be a positive integer, not '1.5'"),
                                ("twelve", "must be a positive integer, not 'twelve'")):
            with self.subTest(value=value):
                self.assertIn(fragment, self.refused(
                    "set", "--switch", "limit_mode", "--parameter", "criteria", "--value", value))
        self.assertIn("switch 'limit_mode' has no parameter 'ghost'; its parameters are"
                      " ['criteria', 'scenarios']", self.refused(
                          "set", "--switch", "limit_mode", "--parameter", "ghost", "--value", "3"))
        self.assertIn("switch 'fixture_mode' declares no parameters", self.refused(
            "set", "--switch", "fixture_mode", "--parameter", "criteria", "--value", "3"))
        code, result = self.cli("set", "--switch", "limit_mode", "--parameter", "criteria",
                                "--value", "12")
        self.assertEqual((code, result["value"], result["source"], result["changed"]),
                         (0, 12, "policy", True))
        self.assertIn("| `limit_mode` | `criteria` | `12` |", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.cli("approve")[0], 0)
        code, result = self.cli("value", "--switch", "limit_mode")
        self.assertEqual((code, result["value"], result["parameters"]),
                         (0, "capped", {"criteria": 12}))
        # A switch without parameters reports exactly what it reported before.
        code, result = self.cli("value", "--switch", "fixture_mode")
        self.assertNotIn("parameters", result)
        values, _snapshot = process_policy.effective_values(self.docs, self.package)
        self.assertEqual(values["limit_mode"]["parameters"], {"criteria": 12})
        self.assertEqual(sorted(key for key, value in values.items() if "parameters" in value),
                         ["limit_mode"])

    def test_a_value_without_parameters_removes_their_rows(self):
        self.assertEqual(self.cli("init")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--value", "capped")
        self.cli("set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12")
        self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios", "--value", "30")
        self.assertEqual(self.cli("approve")[0], 0)
        approved = self.path.read_text(encoding="utf-8")
        self.cli("begin-revision")
        code, result = self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios",
                                "--default")
        self.assertEqual((code, result["value"], result["source"]), (0, None, "unset"))
        code, result = self.cli("set", "--switch", "limit_mode", "--default")
        self.assertEqual((code, result["value"], result["removed_parameters"]),
                         (0, "open", {"criteria": 12}))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("## Parameters", text)
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertNotEqual(approved, self.path.read_text(encoding="utf-8"))
        code, result = self.cli("value", "--switch", "limit_mode")
        self.assertEqual((result["value"], result["source"], result["parameters"]),
                         ("open", "default", {}))

    def test_parameters_are_hashed_and_hand_edits_are_refused(self):
        self.assertEqual(self.cli("init")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--value", "capped")
        self.cli("set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12")
        self.assertEqual(self.cli("approve")[0], 0)
        original = self.path.read_text(encoding="utf-8")
        row = "| `limit_mode` | `criteria` | `12` |"
        cases = (
            (original.replace("`12`", "`13`"), "approved Process Policy source_hash is stale"),
            (original.replace(row, row + "\n" + row),
             "parameter 'criteria' of switch 'limit_mode' has more than one row"),
            (original.replace("| Switch | Parameter | Value |", "| Switch | Name | Value |"),
             "Parameters table must start with the header | Switch | Parameter | Value |"),
            (original.replace(row, "| `limit_mode` | `criteria` |"),
             "Parameters row must hold a switch, a parameter and a value"),
            (original.replace("| `limit_mode` | `capped` |\n", ""),
             "parameters of switch 'limit_mode' apply only at capped; its value is 'open'"),
            (original.replace(row, "| `ghost_mode` | `criteria` | `12` |"),
             "parameter 'criteria' names switch 'ghost_mode', which this package does not declare"),
        )
        for text, fragment in cases:
            with self.subTest(fragment=fragment):
                self.path.write_text(text, encoding="utf-8")
                code, result = self.cli("check")
                self.assertEqual(code, 1)
                self.assertTrue(any(fragment in error for error in result["errors"]),
                                result["errors"])
                with self.assertRaises(ValueError):
                    process_policy.effective_values(self.docs, self.package)
        self.path.write_text(original, encoding="utf-8")
        code, result = self.cli("check")
        self.assertEqual((code, result["parameters"]), (0, {"limit_mode": {"criteria": "12"}}))

    def test_switches_lists_the_parameter_declaration_and_the_values_set(self):
        self.assertEqual(self.cli("init")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--value", "capped")
        self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios", "--value", "30")
        self.assertEqual(self.cli("approve")[0], 0)
        code, result = self.cli("switches")
        listed = {item["switch"]: item for item in result["switches"]}
        self.assertEqual(listed["limit_mode"]["parameters"], {
            "summary": "Owner-set limit per fixture measure.", "values": ["capped"],
            "type": "positive_integer", "min_count": 1,
            "declared": [{"id": "criteria", "summary": "Criteria per story."},
                         {"id": "scenarios", "summary": "Scenarios per plan."}],
            "in_force": {"scenarios": 30}})
        self.assertNotIn("parameters", listed["fixture_mode"])

    def test_a_parameter_the_package_retired_is_listed_and_repaired_in_a_revision(self):
        self.assertEqual(self.cli("init")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--value", "capped")
        self.cli("set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12")
        self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios", "--value", "30")
        self.assertEqual(self.cli("approve")[0], 0)
        limits = self.package / LIMITS
        measures = json.loads(limits.read_text(encoding="utf-8"))
        del measures["measures"]["scenarios"]
        limits.write_text(json.dumps(measures), encoding="utf-8")
        code, listed = self.cli("switches")
        self.assertEqual((code, listed["undeclared"]), (1, {
            "switches": [], "parameters": [{"switch": "limit_mode", "parameter": "scenarios"}]}))
        self.assertEqual(self.cli("begin-revision")[0], 0)
        code, result = self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios",
                                "--default")
        self.assertEqual((code, result["changed"]), (0, True))
        self.assertEqual(self.cli("approve")[0], 0)
        code, listed = self.cli("switches")
        self.assertEqual(code, 0)
        self.assertNotIn("undeclared", listed)

    def test_a_promoted_value_ships_package_limits_and_stays_non_default(self):
        registry = json.loads((self.package / process_policy.REGISTRY).read_text(encoding="utf-8"))
        registry["switches"]["limit_mode"]["parameters"]["package_limits"] = {"criteria": 8}
        self.write_registry(registry)
        declared = process_policy.load_registry()["limit_mode"]
        self.assertEqual((declared["default"], declared["parameters"]["package_limits"]),
                         ("open", {"criteria": 8}))
        self.assertEqual(self.cli("init")[0], 0)
        self.assertEqual(self.cli("set", "--switch", "limit_mode", "--value", "capped")[0], 0)
        # A package limit counts towards min_count and applies while the row is unset.
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertEqual(self.cli("value", "--switch", "limit_mode")[1]["parameters"],
                         {"criteria": 8})
        self.assertEqual(self.cli("begin-revision")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12")
        self.cli("set", "--switch", "limit_mode", "--parameter", "scenarios", "--value", "30")
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertEqual(self.cli("value", "--switch", "limit_mode")[1]["parameters"],
                         {"criteria": 12, "scenarios": 30})
        listed = {item["switch"]: item for item in self.cli("switches")[1]["switches"]}
        self.assertEqual(listed["limit_mode"]["parameters"]["package_limits"], {"criteria": 8})
        # At the default no parameter applies.
        self.assertEqual(self.cli("begin-revision")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--default")
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertEqual(self.cli("value", "--switch", "limit_mode")[1]["parameters"], {})
        for limits in ({"ghost": 3}, {"criteria": 0}, {"criteria": True}, {"criteria": "8"}):
            with self.subTest(limits=limits):
                registry["switches"]["limit_mode"]["parameters"]["package_limits"] = limits
                self.write_registry(registry)
                with self.assertRaisesRegex(ValueError, "declares invalid parameters"):
                    process_policy.load_registry()

    def test_an_invalid_parameter_declaration_fails_the_registry(self):
        cases = (
            ({"declared_by": {"path": "../outside.json", "key": "measures"}}, "cannot be read"),
            ({"declared_by": {"path": LIMITS, "key": "ghost"}}, "cannot be read"),
            ({"type": "fraction"}, "declares invalid parameters"),
            ({"values": ["open"]}, "declares invalid parameters"),
            ({"min_count": 3}, "declares invalid parameters"),
        )
        for change, fragment in cases:
            with self.subTest(change=change):
                registry = json.loads(json.dumps(FIXTURE_REGISTRY))
                registry["switches"]["limit_mode"] = json.loads(json.dumps(PARAMETERIZED_SWITCH))
                registry["switches"]["limit_mode"]["parameters"].update(change)
                self.write_registry(registry)
                with self.assertRaisesRegex(ValueError, fragment):
                    process_policy.load_registry(self.package)



SKILLS = "plugins/software-engineering-team/skill-content"
FLOWS = "plugins/software-engineering-team/flows"
HOSTS = {host: f"platforms/{host}/software-engineering-team/host-contract.md"
         for host in ("claude", "codex")}
SWITCH_REFERENCE = re.compile(r"switch-([a-z][a-z0-9_]*)-([a-z][a-z0-9_]*)\.md")
# The value each switch ships at: a flip changes every project that chose nothing.
RELEASED_DEFAULTS = {
    "pr_proof": "remote_ci",
    "step_budgets": "off",
    "step_timing": "off",
}
# The rules that keep a switch value safe, in the files agents read them from:
# who decides, who reads independently, which severity holds, which gate stays
# and which writes never run at once. Any other sentence may be reworded.
SAFETY_RULES = {
    "pr_proof": {
        f"{SKILLS}/deliver/references/switch-pr_proof-local_record.md": (
            "`open-pr` refuses before it pushes",
            "never edit the record",
        ),
    },
    "step_timing": {
        f"{SKILLS}/deliver/references/switch-step_timing-recorded.md": (
            "a role never times another agent",
            "is reported to the owner at once",
            "never an estimate",
            "never delete or rewrite a row without the owner's approval",
        ),
    },
    "step_budgets": {
        f"{SKILLS}/deliver/references/switch-step_budgets-enforced.md": (
            "A budget is a maximum, never an expectation",
            "Enforcement is reporting only",
            "a review is never cut to stay under one",
        ),
    },
}
# Choices reach a project only through the Process Policy lifecycle.
POLICY_RULES = {
    f"{SKILLS}/configure/references/process-policy.md": (
        "Never choose for the user and never skip a switch",
        "The package default is the recommended option and comes first",
        "Ask the approval choice gate. On rejection write nothing",
        "`workspace/config.json` never holds a process choice",
        "never hand-edit the Switches table or the lifecycle fields",
    ),
    f"{SKILLS}/configure/SKILL.md": (
        "`process`, use `process_policy.py`. Never hand-edit its lifecycle fields",),
    f"{SKILLS}/configure/references/config-contract.md": (
        "the config stays closed, and the Process Policy is the one place for process choices",),
}


def flat_text(relative: str) -> str:
    return " ".join((ROOT / relative).read_text(encoding="utf-8").split())


class ProcessSwitchContractTests(unittest.TestCase):
    """One registry-driven check of every switch, beside the validator's anchor,
    reference and shape rules, and the safety rules each value keeps."""

    def test_every_switch_ships_at_its_released_default_with_scoped_instructions(self):
        switches = json.loads((TEAM / process_policy.REGISTRY).read_text(encoding="utf-8"))["switches"]
        self.assertEqual(sorted(switches), sorted(RELEASED_DEFAULTS))
        self.assertEqual(sorted(SAFETY_RULES), sorted(RELEASED_DEFAULTS))
        references: dict = {}
        for path in sorted(TEAM.glob("skill-content/*/references/switch-*.md")):
            name, value = SWITCH_REFERENCE.fullmatch(path.name).groups()
            references.setdefault((name, value), []).append(path.relative_to(ROOT).as_posix())
        orchestration = flat_text("docs/orchestration.md")
        for name, spec in sorted(switches.items()):
            values = [value["id"] for value in spec["values"]]
            with self.subTest(switch=name):
                self.assertEqual(spec["default"], RELEASED_DEFAULTS[name])
                self.assertIn("with every shared quality guard holding and the owner's approval",
                              spec["promotion"]["threshold"])
                self.assertIn(f"switch `{name}`", orchestration.replace("Switch", "switch"))
                for value in values:
                    self.assertIn(f"`{value}`", orchestration)
                for value in values:
                    if value == spec["default"]:
                        continue
                    self.assertTrue(references.get((name, value)), value)
                    for relative in references[(name, value)]:
                        text = flat_text(relative)
                        for term in (f"`{name}`", f"`{value}`", "Process Policy"):
                            self.assertIn(term, text, relative)

    def test_the_safety_rules_stay_where_agents_read_them(self):
        rules = [(name, relative, rule) for name, files in sorted(SAFETY_RULES.items())
                 for relative, texts in files.items() for rule in texts]
        rules += [("process_policy", relative, rule) for relative, texts in POLICY_RULES.items()
                  for rule in texts]
        for name, relative, rule in rules:
            with self.subTest(switch=name, path=relative, rule=rule[:60]):
                self.assertIn(rule, flat_text(relative))


# The review and reading switches of #441: each owning flow anchors its
# non-default value and names the reference that defines it.
READING_SWITCHES = {
    "step_timing": ("recorded", "switch-step_timing-recorded.md"),
    "step_budgets": ("enforced", "switch-step_budgets-enforced.md"),
}
# Initial targets in minutes, maximums that only report.
STEP_BUDGET_TARGETS = {"backlog_revision": 10, "confirmation_rereview": 3,
                       "delivery_and_execution_planning": 15, "reader_closure_unit": 5,
                       "requirement_technical": 10, "story_end_to_end": 180}
# The QA step keys, whose length depends on the project, ship no target (#543).
QA_STEP_KEYS = ("freeze", "live_run", "live_step", "manual_scenario", "pre_handoff", "qa_lifecycle_gate")
SOURCES_FIRST = "Sources first, per constitution section 5"


class ReadingSwitchTests(unittest.TestCase):
    """Impact-closure reading, per-unit readers, concurrent levels, the role
    digest and step timing ship off, and their flow text resolves (#441)."""

    def setUp(self) -> None:
        self.switches = json.loads((TEAM / process_policy.REGISTRY).read_text(
            encoding="utf-8"))["switches"]

    def test_each_switch_has_two_values_and_ships_today_s_behaviour(self):
        for name, (value, _reference) in READING_SWITCHES.items():
            spec = self.switches[name]
            with self.subTest(switch=name):
                self.assertEqual([item["id"] for item in spec["values"]],
                                 [RELEASED_DEFAULTS[name], value])
                self.assertEqual(spec["default"], RELEASED_DEFAULTS[name])
                self.assertEqual(spec["issue"], 441)
                self.assertEqual(spec["reference_scope"], "owning_flows")
        flows = sorted(path.stem for path in (ROOT / FLOWS).glob("*.md"))
        for name in ("step_timing", "step_budgets"):
            self.assertEqual(sorted(self.switches[name]["flows"]), flows, name)

    def test_every_owning_flow_anchors_the_value_and_names_its_reference(self):
        for name, (value, reference) in READING_SWITCHES.items():
            path = f"skill-content/deliver/references/{reference}"
            self.assertTrue((TEAM / path).is_file(), path)
            for flow in self.switches[name]["flows"]:
                text = flat_text(f"{FLOWS}/{flow}.md")
                with self.subTest(switch=name, flow=flow):
                    self.assertIn(f"Switch `{name}`: at `{value}`", text)
                    self.assertIn(path, text)

    def test_step_budgets_are_reporting_maximums_with_the_targets(self):
        spec = self.switches["step_budgets"]["parameters"]
        declared = json.loads((TEAM / spec["declared_by"]["path"]).read_text(
            encoding="utf-8"))[spec["declared_by"]["key"]]
        self.assertEqual(spec["package_limits"], STEP_BUDGET_TARGETS)
        self.assertEqual(sorted(declared), sorted([*STEP_BUDGET_TARGETS, *QA_STEP_KEYS]))
        for budget, item in declared.items():
            with self.subTest(budget=budget):
                self.assertTrue(item["summary"].startswith("Maximum minutes"))
        self.assertIn("never an expectation", self.switches["step_budgets"]["summary"])

    def test_every_role_reads_its_sources_first(self):
        constitution = flat_text("plugins/software-engineering-team/constitution.md")
        section = constitution[constitution.index("## 5. Sources first"):
                               constitution.index("## Escape hatch")]
        for term in ("sources the task file lists", "grep `workspace/docs/`",
                     "on your initiative or the parent agent's direction",
                     "Record the sources and reasons", "preserve every owning-flow gate",
                     "`context_findings`", "only explicit user approval of the displayed payload"):
            with self.subTest(rule=term):
                self.assertIn(term, section)
        for agent in sorted((TEAM / "agents").glob("*.md")):
            text = " ".join(agent.read_text(encoding="utf-8").split())
            with self.subTest(agent=agent.name):
                self.assertIn(SOURCES_FIRST + ": read the sources the task file lists", text)
                self.assertIn("preserve gates and return `context_findings`", text)
        self.assertIn(SOURCES_FIRST + ": every role reads the sources the task file lists",
                      flat_text(f"{FLOWS}/fast-lane.md"))

class MeasuredBaselineTests(unittest.TestCase):
    """The registry ships to every user, so a baseline measured in a project is
    retold anonymously and never names that project's Deliveries or stories (#357)."""

    def test_a_measured_baseline_is_retold_as_one_measured_project(self):
        switches = json.loads((TEAM / process_policy.REGISTRY).read_text(
            encoding="utf-8"))["switches"]
        cited = []
        for name, spec in sorted(switches.items()):
            for text in [value["tradeoffs"] for value in spec["values"]] \
                    + [spec["promotion"]["threshold"]]:
                with self.subTest(switch=name, text=text[:40]):
                    self.assertNotRegex(text, r"\b(?:DLV|ST|REQ)-\d")
            default = next(value for value in spec["values"] if value["id"] == spec["default"])
            _behaviour, colon, evidence = default["tradeoffs"].partition("measured against:")
            if colon:
                cited.append(name)
                with self.subTest(switch=name):
                    self.assertTrue(evidence.startswith(" in one measured project"), evidence)
        self.assertEqual(cited, [])


if __name__ == "__main__":
    unittest.main()
