"""Level change map: switch `level_change_map` keeps today's handoff at `off` and, at
`assertion_map`, has the writer map the assertions of each scenario whose Test Plan
level an Item converts, which `freeze` checks before the readers start and the code
reviewer reads where the check flags a pair (#440)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_verification as verification  # noqa: E402
import test_pre_handoff_regression as base  # noqa: E402

SWITCH = "level_change_map"
VALUE = "assertion_map"
REFERENCE = "skill-content/deliver/references/switch-level_change_map-assertion_map.md"
SCENARIO = "ST-001-TS-001"
THEN = "the value holds"
LIVE = "    assert pathlib.Path('src/api/limit.txt').read_text(encoding='utf-8').strip() == '10'"
UNIT = """def limit_rule(text):
    return text.strip()


def test_limit_rule():
    assert limit_rule(" 10 ") == '10'
    assert len(limit_rule(" 10 ")) == 2
"""
BEFORE = {"path": "tests/st001/test_api.py", "code": LIVE.strip(), "kind": "value", "expected": "'10'"}
AFTER = {"path": "tests/st001/test_rules.py", "code": "assert limit_rule(\" 10 \") == '10'", "kind": "value",
         "expected": "'10'"}
COUNT = {"path": "tests/st001/test_rules.py", "code": "assert len(limit_rule(\" 10 \")) == 2", "kind": "count",
         "expected": None}


def leveled(identifier: str, target: str, level: str | None) -> str:
    """A scenario of the fixture's shape with *level* stated, or none."""
    text = base.scenario(identifier, "required", target)
    return text if level is None else text.replace("- source_refs:", f"- level: {level}\n- source_refs:")


class BindingTests(unittest.TestCase):
    setUp = base.BindingTests.setUp
    bound = base.BindingTests.bound

    def test_only_delivery_execution_tasks_at_assertion_map_bind_the_reference(self):
        tasks = (("deliver", "backend-developer"), ("deliver", "delivery-coordinator"),
                 ("deliver", "code-reviewer"), ("execution-plan", "qa-engineer"), ("backlog-plan", "qa-engineer"))
        for entry, role in tasks:
            with self.subTest(value="off", task=(entry, role)):
                self.assertFalse(self.bound(entry, role, REFERENCE))
        base.policy(self.docs, "init")
        base.policy(self.docs, "set", "--switch", SWITCH, "--value", VALUE)
        base.policy(self.docs, "approve")
        for entry, role in tasks:
            with self.subTest(value=VALUE, task=(entry, role)):
                self.assertEqual(self.bound(entry, role, REFERENCE), entry == "deliver")


class AssertionMapTests(unittest.TestCase):
    """The pre-handoff fixture's DLV-002 Item ST-005, whose candidate also moves the merged ST-001's live
    scenario to unit level and rewrites its test."""

    setUp = base.CandidateTests.setUp
    git = base.CandidateTests.git
    commit = base.CandidateTests.commit
    write = base.CandidateTests.write
    note = base.CandidateTests.note
    plan = base.CandidateTests.plan
    plan_path = base.CandidateTests.plan_path
    plan_hash = base.CandidateTests.plan_hash
    item = base.CandidateTests.item
    contract = base.CandidateTests.contract
    build = base.CandidateTests.build
    freeze = base.CandidateTests.freeze

    def converted(self, value: str | None = VALUE, level: str | None = "unit",
                  target: str = "tests/st001/test_rules.py::test_limit_rule", write: bool = True) -> None:
        """Build the fixture, set level_change_map at *value* and commit a candidate that states *level* for
        ST-001's scenario with automation target *target*, writing the unit test when *write*."""
        self.build(value=None)
        if value is not None:
            base.policy(self.docs, "init")
            base.policy(self.docs, "set", "--switch", SWITCH, "--value", value)
            base.policy(self.docs, "approve")
        self.plan("ST-001", leveled(SCENARIO, target, level),
                  base.scenario("ST-001-TS-002", "manual", "tests/st001/manual-check.md"))
        if write:
            self.write("tests/st001/test_rules.py", UNIT)
        self.commit("Move ST-001's decision to unit level")

    def map(self, scenarios: dict | None = None, **entry) -> Path:
        """Write the Item's assertion map: *scenarios* as given, else one entry for the converted scenario."""
        if scenarios is None:
            scenarios = {SCENARIO: {"then": THEN, "before": [BEFORE], "after": [AFTER], **entry}}
        path = verification.assertion_map_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "scenarios": scenarios}), encoding="utf-8")
        return path

    def status(self) -> dict:
        return verification.assertion_map_status(self.root, "DLV-002", "ST-005")

    def refused(self, message: str) -> None:
        with self.assertRaisesRegex(RuntimeError, "^assertion map: .*" + message):
            self.freeze()

    def test_off_freezes_a_conversion_as_released_and_the_map_verb_refuses(self):
        self.converted(value=None)
        self.assertNotIn("assertion_map", self.freeze()["candidate"])
        with self.assertRaisesRegex(RuntimeError, "^the assertion map is checked only at process switch"
                                                  " level_change_map assertion_map; DLV-002 runs it at off$"):
            self.status()

    def test_a_conversion_without_a_map_refuses_the_freeze_and_the_verb_offers_its_template(self):
        self.converted()
        self.refused("no assertion map names the assertions of the converted scenarios ST-001-TS-001$")
        status = self.status()
        self.assertEqual(status["converted_scenarios"], [{
            "story": "ST-001", "scenario": SCENARIO, "test_plan": base.PLANS + "st-001/test-plan.md", "then": THEN,
            "before": {"level": None, "automation_target": "tests/st001/test_api.py::test_limit"},
            "after": {"level": "unit", "automation_target": "tests/st001/test_rules.py::test_limit_rule"}}])
        self.assertEqual(status["template"], {"schema_version": 1, "scenarios": {
            SCENARIO: {"then": THEN, "before": [], "after": []}}})
        self.assertEqual(set(status["kinds"]), {"value", "error", "count", "existence"})
        self.assertEqual(status["assertion_map_file"], str(verification.assertion_map_path(self.root)))

    def test_a_complete_map_freezes_and_reaches_every_reader_with_nothing_flagged(self):
        self.converted()
        path = self.map()
        candidate = self.freeze()["candidate"]
        self.assertEqual(candidate["assertion_map"]["problems"], [])
        self.assertEqual(candidate["assertion_map"]["flagged"], [])
        self.assertTrue(candidate["assertion_map"]["map_hash"].startswith("sha256:"))
        manifest = verification.manifest(self.root, "DLV-002", "ST-005", "code_reviewer", "review_initial")
        self.assertEqual(manifest["assertion_map"], candidate["assertion_map"])
        # The candidate binds the map, so a map changed after the freeze drifts it.
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "verification candidate or source bindings changed"):
            verification.manifest(self.root, "DLV-002", "ST-005", "code_reviewer", "review_initial")

    def test_a_weakened_pair_is_flagged_for_the_code_reviewer_never_refused(self):
        self.converted()
        self.map(after=[COUNT])
        flagged = self.freeze()["candidate"]["assertion_map"]["flagged"]
        self.assertEqual(flagged, [{"story": "ST-001", "scenario": SCENARIO, "then": THEN,
                                    "flags": ["weakened_kind", "expected_value_lost"],
                                    "lost_expected_values": ["'10'"], "before": [BEFORE], "after": [COUNT]}])
        # A count beside the value comparison keeps the pair unflagged.
        self.map(after=[AFTER, COUNT])
        self.assertEqual(self.status()["flagged"], [])

    def test_every_incomplete_or_unanchored_entry_refuses_the_freeze(self):
        self.converted()
        cases = (
            ({"then": "another outcome"}, "ST-001-TS-001 maps another then than its Test Plan's Then: the value"
                                          " holds"),
            ({"after": []}, "ST-001-TS-001 names no after assertion"),
            ({"before": [{**BEFORE, "code": "assert limit == '10'"}]},
             "ST-001-TS-001 before assertion 1 is no line of tests/st001/test_api.py in the integration base"),
            ({"after": [{**AFTER, "path": "tests/st001/test_api.py"}]},
             "ST-001-TS-001 after assertion 1 is no line of tests/st001/test_api.py in the candidate"),
            ({"after": [{**AFTER, "kind": "smoke"}]},
             "ST-001-TS-001 after assertion 1 names kind 'smoke', none of value, error, count, existence"),
            ({"after": [{**AFTER, "expected": "'20'"}]},
             "ST-001-TS-001 after assertion 1 names an expected value its code does not hold; name null for none"),
            ({"after": [{**AFTER, "code": AFTER["code"] + "\nassert True"}]},
             "ST-001-TS-001 after assertion 1 names no single line of code"),
            ({"after": [{**AFTER, "path": "../outside.py"}]},
             "ST-001-TS-001 after assertion 1 names no normalized repository path"),
            ({"after": [{key: AFTER[key] for key in ("path", "code", "kind")}]},
             "ST-001-TS-001 after assertion 1 holds other fields than path, code, kind, expected"))
        for entry, message in cases:
            with self.subTest(message=message):
                self.map(**entry)
                self.refused(message.replace("(", r"\(").replace(")", r"\)") + "$")
        self.map(scenarios={})
        self.refused("ST-001-TS-001 has no map entry of then, before and after$")
        self.map(scenarios={SCENARIO: {"then": THEN, "before": [BEFORE], "after": [AFTER]},
                            "ST-005-TS-001": {"then": THEN, "before": [BEFORE], "after": [AFTER]}})
        self.refused("ST-005-TS-001 is no scenario the Item converts$")
        verification.assertion_map_path(self.root).write_text("[]", encoding="utf-8")
        self.refused("the assertion map holds no schema_version 1 and scenarios$")

    def not_converted(self) -> None:
        # The Item's own new scenario, which no integration bound, is no conversion either.
        self.assertEqual(self.status()["converted_scenarios"], [])
        self.assertEqual(self.freeze()["candidate"]["assertion_map"]["problems"], [])

    def test_a_rewritten_target_whose_level_is_unchanged_is_no_conversion(self):
        self.converted(level=None)
        self.not_converted()

    def test_a_level_change_in_a_target_the_item_leaves_untouched_is_no_conversion(self):
        self.converted(target="tests/st001/test_api.py::test_limit", write=False)
        self.not_converted()

    def test_a_story_whose_integrated_revision_is_gone_refuses_instead_of_dropping_a_conversion(self):
        self.converted()
        self.item(base.EARLIER, "ST-001", "integrated", ["src/api", "tests/st001"],
                  test_plan_source_hash="sha256:" + "0" * 64)
        self.commit("Edit ST-001's Item record")
        with self.assertRaisesRegex(RuntimeError, "^ST-001 integrated a Test Plan revision neither the candidate"
                                                  " nor its history holds, so whether ST-001-TS-001 changed"
                                                  " level cannot be decided$"):
            self.status()


if __name__ == "__main__":
    unittest.main()
