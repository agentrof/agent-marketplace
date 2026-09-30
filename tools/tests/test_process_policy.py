"""Process Policy: the governed project document that records explicit
process switch choices over the package registry's defaults."""

from __future__ import annotations

import contextlib
import io
import json
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
        self.assertEqual(self.cli("approve")[0], 1)
        self.assertEqual(self.cli("begin-revision")[0], 0)
        self.assertEqual(self.cli("set", "--switch", "other_mode", "--value", "light")[0], 1)
        code, result = self.cli("set", "--switch", "other_mode", "--default")
        self.assertEqual((code, result["changed"]), (0, True))
        self.assertEqual(self.cli("approve")[0], 0)
        self.assertEqual(self.cli("check")[0], 0)

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


def flat(relative: str) -> str:
    return " ".join((TEAM / relative).read_text(encoding="utf-8").split())


class ConfigureProcessContractTests(unittest.TestCase):
    """/configure process changes switch values only through the policy lifecycle."""

    def test_configure_routes_process_to_its_reference_and_compiler(self):
        skill = flat("skill-content/configure/SKILL.md")
        for rule in ("or a process switch (`process`)",
                     "`references/process-policy.md` for `process` before durable changes",
                     "for `process`, use `process_policy.py`. Never hand-edit their lifecycle fields"):
            with self.subTest(rule=rule):
                self.assertIn(rule, skill)

    def test_one_choice_gate_per_switch_with_the_default_recommended_first(self):
        reference = flat("skill-content/configure/references/process-policy.md")
        for rule in (
                "Ask one choice-gate question per switch, at most four per host call",
                "The package default is the recommended option and comes first",
                "Each option's description carries that value's registry tradeoffs",
                "the question names the switch's metric and promotion unit",
                "Never choose for the user and never skip a switch",
                "When no answer changes a value in force, write nothing and stop",
                "ask the approval choice gate. On rejection write nothing",
                "Choosing the default removes the switch's row",
                "`workspace/config.json` never holds a process choice",
                "never hand-edit the Switches table or the lifecycle fields",
                "a project row is never a promotion"):
            with self.subTest(rule=rule):
                self.assertIn(rule, reference)
        order = [reference.index(verb) for verb in (
            "`process_policy.py switches", "`init`", "`begin-revision`", "`set --switch",
            "`check`", "`approve`")]
        self.assertEqual(order, sorted(order))

    def test_config_stays_closed_and_names_the_process_policy(self):
        contract = flat("skill-content/configure/references/config-contract.md")
        self.assertIn("| Process switch values | `workspace/docs/delivery/process-policy.md` over"
                      " the package registry `data/process-switches.json` | Process Policy"
                      " lifecycle, `/configure process` |", contract)
        self.assertIn("No `scale`, `limits`, stack, source-directory, command or process switch"
                      " field is accepted in config: the config stays closed, and the Process"
                      " Policy is the one place for process choices", contract)


if __name__ == "__main__":
    unittest.main()
