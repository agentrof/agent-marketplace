"""Gate 3 tests for the closed, host-neutral coordinator result envelope."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))

import delivery_result  # noqa: E402


class DeliveryResultTests(unittest.TestCase):
    def test_hash_is_order_invariant_and_envelope_is_closed(self):
        first = delivery_result.from_raw(
            "start-item",
            {"ok": True, "item": "a" * 40, "slot": "b" * 40,
             "observations": [], "planned_mutations": []},
        )
        second = delivery_result.from_raw(
            "start-item",
            {"slot": "b" * 40, "item": "a" * 40, "ok": True,
             "planned_mutations": [], "observations": []},
        )
        self.assertEqual(first["mutation_plan_hash"], second["mutation_plan_hash"])
        self.assertEqual(set(first), {
            "schema_version", "ok", "operation", "mutation_state",
            "mutation_plan_hash", "observations", "planned_mutations", "findings",
        })
        self.assertEqual(first["mutation_state"], "complete")

    def test_denials_are_none_and_uncertain_provider_errors_are_uncertain(self):
        denied = delivery_result.from_raw(
            "open-pr", {"ok": False, "errors": ["DELIVERY_PR_STATE_INVALID: draft required"]}
        )
        self.assertEqual(denied["mutation_state"], "none")
        self.assertEqual(denied["findings"][0]["code"], "DELIVERY_PR_STATE_INVALID")
        uncertain = delivery_result.from_raw(
            "open-pr", {"ok": False, "mutation_state": "uncertain",
                         "errors": ["DELIVERY_PR_UNCERTAIN: response lost"]}
        )
        self.assertEqual(uncertain["mutation_state"], "uncertain")
        self.assertEqual(uncertain["findings"][0]["code"], "DELIVERY_PR_UNCERTAIN")

    def test_malformed_records_fail_closed_inside_valid_envelope(self):
        result = delivery_result.from_raw(
            "inspect",
            {"ok": True, "planned_mutations": [{"kind": "ref_update"}],
             "observations": [{"kind": "ref", "target": "x", "value": "absent"},
                              {"kind": "ref", "target": "x", "value": "absent"}]},
        )
        self.assertEqual(result["mutation_state"], "complete")
        self.assertTrue(any(item["code"] == "DELIVERY_INPUT_INVALID" for item in result["findings"]))
        delivery_result.validate_envelope(result)


if __name__ == "__main__":
    unittest.main()
