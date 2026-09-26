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

    @staticmethod
    def refusal_findings(text: str) -> list[tuple[str, str]]:
        result = delivery_result.from_raw("merge-pr", {"ok": False, "errors": [text]})
        return [(finding["code"], finding["message"]) for finding in result["findings"]]

    def test_unknown_uppercase_first_word_stays_in_the_message(self):
        for text in ("HEAD moved since the fence was written", "MASTER.md is missing frontmatter",
                     "DELIVERY_NOT_A_CODE: detail stays"):
            with self.subTest(text=text):
                self.assertEqual(self.refusal_findings(text), [("DELIVERY_INPUT_INVALID", text)])

    def test_known_code_prefix_becomes_the_finding_code(self):
        self.assertEqual(
            self.refusal_findings("DELIVERY_WORKTREE_UNSAFE: commit or remove changes before push: a.md"),
            [("DELIVERY_WORKTREE_UNSAFE", "commit or remove changes before push: a.md")],
        )

    def test_known_code_keeps_every_line_of_its_detail(self):
        self.assertEqual(
            self.refusal_findings("DELIVERY_WORKTREE_UNSAFE: commit or remove changes:\na.md\nb.md"),
            [("DELIVERY_WORKTREE_UNSAFE", "commit or remove changes:\na.md\nb.md")],
        )

    def test_message_without_a_code_prefix_is_kept_whole(self):
        for text in ("project root is not a Git worktree", "GitHub PR head changed during merge"):
            with self.subTest(text=text):
                self.assertEqual(self.refusal_findings(text), [("DELIVERY_INPUT_INVALID", text)])

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

    def test_mutation_plan_hash_is_bound_to_the_payload(self):
        result = delivery_result.from_raw(
            "status", {"ok": True, "fence": "a" * 40}
        )
        result["observations"].append({
            "kind": "ref", "target": "integration", "value": "b" * 40,
        })
        with self.assertRaises(ValueError):
            delivery_result.validate_envelope(result)


if __name__ == "__main__":
    unittest.main()
