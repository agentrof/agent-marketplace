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
import vault_check  # noqa: E402


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
        (self.docs / "maps").mkdir(parents=True)
        (self.docs / "home.md").write_text(
            "---\ntype: home\ntitle: Project home\n---\n\n# Project home\n\n"
            "[[maps/delivery|Delivery]]\n", encoding="utf-8")
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
                                                 "path": "delivery/process-policy.md",
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
            "process_policy_path": "delivery/process-policy.md",
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

    def test_the_policy_is_a_legal_linked_vault_note(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.approved_with(fixture_mode="fast")
        self.cli("begin-revision")
        for step in ("draft", "approved"):
            with self.subTest(step=step):
                if step == "approved":
                    self.assertEqual(self.cli("approve")[0], 0)
                map_text = (self.docs / "maps/delivery.md").read_text(encoding="utf-8")
                self.assertIn("[[delivery/process-policy|Process Policy]]", map_text)
                vault = vault_check.build_vault(self.docs, policy)
                findings = []
                for check in (vault_check.check_frontmatter_props, vault_check.check_nav_footer,
                              vault_check.check_wikilink_resolution, vault_check.check_orphans,
                              vault_check.check_moc_coverage, vault_check.check_title_shape):
                    check(vault, findings)
                self.assertEqual([finding for finding in findings if finding.path in {
                    "delivery/process-policy.md", "maps/delivery.md"}], [])
        vault_check.materialize_payload(self.docs, policy, vault_check.DEFAULT_PAYLOAD)
        vault_check.reconcile_payload_fragment(self.docs, policy, "delivery")
        types = json.loads((self.docs / ".obsidian/types.json").read_text())["types"]
        props, _body = process_policy.parse(self.path)
        for key in props:
            self.assertEqual(types.get(key), policy["property_types"][key], key)


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

    def test_a_policy_with_parameters_is_a_legal_linked_vault_note(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.assertEqual(self.cli("init")[0], 0)
        self.cli("set", "--switch", "limit_mode", "--value", "capped")
        self.cli("set", "--switch", "limit_mode", "--parameter", "criteria", "--value", "12")
        self.assertEqual(self.cli("approve")[0], 0)
        vault = vault_check.build_vault(self.docs, policy)
        findings = []
        for check in (vault_check.check_frontmatter_props, vault_check.check_nav_footer,
                      vault_check.check_wikilink_resolution, vault_check.check_orphans,
                      vault_check.check_title_shape):
            check(vault, findings)
        self.assertEqual([finding for finding in findings
                          if finding.path == "delivery/process-policy.md"], [])


SKILLS = "plugins/software-engineering-team/skill-content"
FLOWS = "plugins/software-engineering-team/flows"
HOSTS = {host: f"platforms/{host}/software-engineering-team/host-contract.md"
         for host in ("claude", "codex")}
SWITCH_REFERENCE = re.compile(r"switch-([a-z][a-z0-9_]*)-([a-z][a-z0-9_]*)\.md")
# The value each switch ships at: a flip changes every project that chose nothing.
RELEASED_DEFAULTS = {
    "code_review_panel": "single_reader",
    "delivery_path": "standard",
    "epic_review_cadence": "wait_per_panel",
    "execution_planning": "per_document",
    "implementation_schedule": "sequential_v1",
    "mechanical_pass_tier": "role_tier",
    "own_target_reuse": "off",
    "owner_gates": "per_step",
    "pre_handoff_regression": "off",
    "qa_gate_order": "plan_first",
    "reader_waves": "as_slots_free",
    "remediation_bookkeeping": "writer",
    "remediation_writers": "single_writer",
    "review_loop": "current",
    "review_manifest_scope": "transitive",
    "review_panels": "single_reader",
    "review_scope_record": "off",
    "root_review_scope": "full",
    "story_size_budget": "off",
}
# The rules that keep a switch value safe, in the files agents read them from:
# who decides, who reads independently, which severity holds, which gate stays
# and which writes never run at once. Any other sentence may be reworded.
SAFETY_RULES = {
    "code_review_panel": {
        f"{SKILLS}/code-review/references/switch-code_review_panel-beside_official.md": (
            "The panel never replaces the official code reviewer",
            "No reader of the step gets another reader's reply, QA's output or evidence that"
            " appears after dispatch",
            "The implementation writer stays idle until `merge-panel` settles the code review",
            "Spawn a fresh `code-reviewer` on its own tier, never a `code-reviewer-lens`",
            "A duplicate never gates on its own",
        ),
        HOSTS["claude"]: ("only the code review panel under switch `code_review_panel` at"
                          " `beside_official` spawns it",),
        HOSTS["codex"]: ("only the code review panel under switch `code_review_panel` at"
                         " `beside_official` starts it",),
    },
    "delivery_path": {
        f"{SKILLS}/delivery-plan/references/switch-delivery_path-light_when_eligible.md": (
            "The light path merges planning steps and owner gates, never checks",
            "The compiler decides eligibility from the records; nothing is assumed",
            "With no limit set no Story is eligible, so small is always the owner's definition",
            "The light path ends at the first failed `light-path-check`",
            "A Delivery never returns to the light path",
            "compose into one gate, never two",
        ),
        f"{SKILLS}/execution-plan/references/switch-delivery_path-light_when_eligible.md": (
            "Write no execution-planning definitions document, Operation contract, architecture"
            " record or any other file",
            "The escalation clause of your role stays as it is",
        ),
        **{path: ("Delivery execution is available only through the exact public entries"
                  " `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.",)
           for path in HOSTS.values()},
    },
    "execution_planning": {
        f"{SKILLS}/execution-plan/references/switch-execution_planning-single_source_bundle.md": (
            "the QA Engineer writes the Verification Contract and the DevOps Engineer the"
            " Environment Contract",
            "An architecture record exists only inside an active Item",
            "Publication carries only the contracts an Item pins",
            "A non-runtime Item never binds the Environment Contract",
            "one that contradicts the owner is critical",
        ),
        f"{SKILLS}/software-architecture/references/switch-execution_planning-single_source_bundle.md": (
            "this file changes where a definition is written, never what the architect decides"
            " or when it escalates",
        ),
        f"{SKILLS}/challenge-review/references/switch-review_panels-lens_panel.md": (
            "run once for each contract the bundle revises, as that contract's counterpart",
        ),
        "plugins/software-engineering-team/agents/software-architect.md": (
            "Escalates and halts, never guesses",),
        "docs/orchestration.md": (
            "A claim on an Operation contract is calibrated by the counterpart of the contract"
            " the claim concerns, the DevOps Engineer for the Verification Contract and the QA"
            " Engineer for the Environment Contract, never by that contract's writer.",
        ),
    },
    "implementation_schedule": {
        f"{SKILLS}/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md": (
            "A lane makes no Git writes: no add, commit,",
            "A lane reports each file it creates, and the coordinator runs `git add -N <path>`"
            " for it, one Git command at a time",
        ),
        **{path: ("writers run at the same time only when their approved lane scopes are"
                  " disjoint",) for path in HOSTS.values()},
        "docs/orchestration.md": (
            "Writers are serialized, except in the two process-switch cases below: the parallel"
            " lanes of `implementation_schedule` and the parallel contract drafts of"
            " `execution_planning`.",
        ),
    },
    "mechanical_pass_tier": {
        f"{SKILLS}/challenge-review/references/switch-mechanical_pass_tier-mechanical.md": (
            "Never mechanical: authoring or rewriting text no finding dictates, design or a"
            " choice between alternatives, triage of findings, code and architecture repairs,"
            " and every review, re-check or calibration.",
            "A variant never reads for a review, re-check or calibration",
            "It never changes a severity, never disputes a finding and never edits text no"
            " finding names.",
            "Every gate before a stamp stays, including the reader barrier, the"
            " `--expected-hash` recheck and the owner's approval.",
            "the Operation counterpart that reviews the other contract runs as its base role",
        ),
    },
    "owner_gates": {
        f"{SKILLS}/deliver/references/switch-owner_gates-two_fixed_gates.md": (
            "Nothing is decided by default",
            "the run never proceeds on a guess",
            "Only the owner's answer closes a question",
            "No approved document changes between the gates unless an `answered` row names it",
            "Ask a decision of these classes at once and name its class in the question",
            "(`delivery_compile.py check` and `check-plan`, `operation_compile.py check`,"
            " `delivery_governance.py check`), present gate A",
        ),
        f"{FLOWS}/execution-planning.md": ("Show the plan to the user only once it passes",),
        f"{SKILLS}/execution-plan/SKILL.md": ("the plan is shown only once it passes",),
        # Decisions of these classes are asked at once, never queued for a gate.
        f"{SKILLS}/deliver/data/owner-decision-classes.json": (
            '"rule_exception"', '"scope_or_grant_change"',
            '"credentials_spending_or_irreversible_action"'),
        HOSTS["claude"]: ("`AskUserQuestion` takes at most four questions per call",),
        HOSTS["codex"]: ("`request_user_input` takes at most three questions per call",),
    },
    "own_target_reuse": {
        f"{SKILLS}/deliver/references/switch-own_target_reuse-spot_run.md": (
            "At `off` of that switch no such run exists and nothing is reused",
            "QA runs itself only the own targets it spot-runs and those the runner keeps with them",
            "names a target that is no `automation_target` of an `automation: required` scenario in the Item's own"
            " Test Plan",
            "It refuses the option with `--fresh`, which reuses nothing, with any kind but `test` and at"
            " `own_target_reuse` `off`",
            "no reused id is, prefixes or lies under a target the command had to run",
            "the audit then reports them as NO-TEST and fails, which is a finding for the Verification Contract",
            "a skip, a retry or a warning it cannot explain is a finding",
        ),
    },
    "pre_handoff_regression": {
        f"{SKILLS}/deliver/references/switch-pre_handoff_regression-touched_suites.md": (
            "A failing run is repaired before the freeze",
            "leaves the run with `selection_intact` false, so it does not pass",
            "`freeze` refuses with `DELIVERY_ENVIRONMENT_BUSY` and names the holder, so a run"
            " still going is never passed over",
            "`run --fresh` reuses no run and runs every suite",
            "No other command receives the variable, an inherited one included",
            "which QA always runs itself",
            "Evidence approval checks the reuse as recorded",
            "Like QA's first gate below, the run reports every failing group of what it runs where the approved"
            " command allows it, so one repair queue holds every failure, and a group that fails to collect is a"
            " failed group, never one left out",
            "A command that stops at its first failing group or drops a group it could not collect is a finding for"
            " the Verification Contract",
            "A group that fails to collect is a failed group: name it in a finding with its collection error, never"
            " leave it out of the result",
        ),
    },
    "qa_gate_order": {
        f"{SKILLS}/qa-verification/references/switch-qa_gate_order-gate_first.md": (
            "at the default, `plan_first`, QA plans and maps every check before it executes anything",
            "start no other `run` or `environment` command until it ends",
            "Read the command's output only once the plan and the matrix are written",
            "no check is planned, or left out, because of what the run showed",
        ),
    },
    "reader_waves": {
        # A wave starts readers only; the writer waits for every reader.
        **{path: ("Under switch `reader_waves` at `all_at_once`",
                  "wait for all of them before triage")
           for path in HOSTS.values()},
    },
    "review_loop": {
        f"{SKILLS}/challenge-review/references/switch-review_loop-blocking_delta.md": (
            "Only a critical or major finding blocks",
            "The writer never lowers a returned severity",
            "A finding that stays critical or major after calibration never enters an"
            " `Accepted Minor Findings` section",
            "The calibration reader is neither the writer nor a reader that returned a finding"
            " of the review",
            "Never pass the writer's triage or interpretation, another reply or the conversation",
            "A row that lowers or invalidates a claim without citing the text is refused: that"
            " claim keeps its claimed severity",
            "Never spawn a `-lens` or `-mechanical` variant for calibration",
        ),
        f"{SKILLS}/code-review/references/switch-review_loop-blocking_delta.md": (
            "Spawn a fresh `code-reviewer` on its own tier, neither an implementation writer nor"
            " the reviewer that returned the claims",
            "Credentials or secrets that reach a client artifact or a log stay critical",
            "Neither the claiming reviewer nor the writer changes a severity",
            "QA keeps its own blocking severities",
        ),
    },
    "epic_review_cadence": {
        f"{SKILLS}/backlog-plan/references/switch-epic_review_cadence-overlap_calibration.md": (
            "It changes when work starts, never what anyone reads or decides",
            "Each claim is still ruled once by a fresh calibration reader, neither the writer nor"
            " a reader that returned a finding of the review",
            "No writer action starts until every epic review and every calibration of its claims"
            " has returned",
        ),
    },
    "remediation_bookkeeping": {
        f"{SKILLS}/backlog-plan/references/switch-remediation_bookkeeping-compiler.md": (
            "The command records; it decides nothing",
            "A closure row only copies what a recheck reader returned",
            "rerun it after any change instead of editing a row, a hash or the report by hand",
            "Each `--expected-hash` recheck the flow requires before accepting a result is"
            " unchanged",
        ),
    },
    "remediation_writers": {
        f"{SKILLS}/backlog-plan/references/switch-remediation_writers-per_epic.md": (
            "The Product Owner stays the only backlog writer role",
            "before any writer starts, as the flow requires",
            "No two writers ever write the same note",
            "the cross-epic writer starts only after every epic writer has returned",
            "Each fix's re-review runs exactly as the step's review loop says",
        ),
    },
    "review_manifest_scope": {
        f"{SKILLS}/backlog-plan/references/switch-review_manifest_scope-bounded.md": (
            "The root manifest and a writer's manifest keep the transitive read set, and backlog"
            " approval still checks the whole backlog",
            "Add each reported note to that reader's task with `--input`, rerun that reader",
            "a review taken under the other value is stale",
        ),
    },
    "review_panels": {
        f"{SKILLS}/challenge-review/references/switch-review_panels-lens_panel.md": (
            "one fresh, read-only reader per lens assignment, in parallel, over the same inputs",
            "Never pass another reader's reply, conversation history or the writer's"
            " interpretation",
            "Wait for every reader before any writer action",
            "keeps every reader's evidence and the highest returned severity",
            "the writer never lowers a returned severity",
            "approved only when every assignment has returned, no lens has an open critical or"
            " major finding and the owning compiler checks named by the flow are green",
            "so a manifest derived under another value is stale",
        ),
        # At the default every panel step keeps its one independent reviewer.
        f"{FLOWS}/backlog-planning.md": (
            "Give one fresh `backlog-reviewer` the returned manifest and every named path",),
        f"{FLOWS}/solution-design.md": (
            "Spawn one independent primary `solution-reviewer` for all four required lenses",),
        f"{FLOWS}/design-system.md": (
            "Spawn `design-system-reviewer` read-only with MASTER, catalog, page overrides and"
            " the semantic token, accessibility and contradiction lens",),
        f"{FLOWS}/operation.md": ("Spawn the non-writing counterpart as a read-only reviewer",),
        **{path: ("the reviewers themselves keep their own tier",) for path in HOSTS.values()},
    },
    "review_scope_record": {
        f"{SKILLS}/backlog-plan/references/switch-review_scope_record-both_scopes.md": (
            "The record measures; it never changes what a reader reads",
            "so no reader may start on an earlier manifest",
            "A record row is measurement data, never review evidence",
            "Never delete or rewrite a row without the owner's approval",
        ),
    },
    "root_review_scope": {
        f"{SKILLS}/backlog-plan/references/switch-root_review_scope-revision_delta.md": (
            "The root review stays the backlog's cross-story gate",
            "backlog approval still checks the whole backlog",
            "never infer the story's content from its summary",
            "so any change to the backlog stales it",
        ),
    },
    "story_size_budget": {
        f"{SKILLS}/product-planning/references/switch-story_size_budget-propose_split.md": (
            "it never fails `backlog_compile.py check`, never blocks a review or an approval and"
            " never rewrites a criterion",
            "Never merge, reword, drop or compress a criterion or a scenario to fit a limit",
            "Ask the owner one choice-gate question per over-budget story",
            "every moved criterion is covered by exactly one of the two stories",
            "Never classify a story to fit a limit",
            "the budget never changes the selection or the scope decision",
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
        "inside a Delivery with `--delivery DLV-###`, which refuses a drifted pin",
        "A Delivery in review or later keeps its pin",
    ),
    f"{SKILLS}/configure/SKILL.md": (
        "for `process`, use `process_policy.py`. Never hand-edit their lifecycle fields",),
    f"{SKILLS}/configure/references/config-contract.md": (
        "the config stays closed, and the Process Policy is the one place for process choices",),
}
# Steps whose order is the rule: the pre-handoff run and the own-target reuse sit
# between the freeze and the readers, and gate-first follows QA's approved commands.
ORDERED_STEPS = {
    f"{FLOWS}/delivery-execution.md": (
        "freeze --delivery DLV-### --story <story>",
        "Switch `pre_handoff_regression`",
        "Switch `own_target_reuse`: at `spot_run`",
        "Invoke Code Review and QA independently",
        "QA uses `run --kind test|mutation|dependency_audit`",
        "Switch `qa_gate_order`: at `gate_first`",
    ),
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
        for relative, steps in ORDERED_STEPS.items():
            text = flat_text(relative)
            with self.subTest(path=relative):
                positions = [text.index(step) for step in steps]
                self.assertEqual(positions, sorted(positions))


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
        self.assertEqual(cited, ["code_review_panel", "delivery_path", "epic_review_cadence",
                                 "execution_planning",
                                 "own_target_reuse", "owner_gates", "pre_handoff_regression",
                                 "qa_gate_order", "reader_waves", "remediation_bookkeeping",
                                 "remediation_writers", "review_scope_record", "root_review_scope"])


if __name__ == "__main__":
    unittest.main()
