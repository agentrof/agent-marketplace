"""Cross-host protocol inventory and command-surface contracts."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"


class DeliveryProtocolTests(unittest.TestCase):
    def test_result_and_record_registries_are_closed_and_unique(self):
        result = json.loads((PLUGIN / "skill-content/deliver/data/delivery-result-contract.json").read_text())
        def no_duplicates(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise AssertionError(f"duplicate JSON key: {key}")
                out[key] = value
            return out
        records = json.loads(
            (PLUGIN / "skill-content/deliver/data/delivery-control-record-contract.json").read_text(),
            object_pairs_hook=no_duplicates,
        )
        codes = result["finding_codes"]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,63}", code) for code in codes))
        self.assertEqual(records["schema_version"], 1)
        self.assertEqual(len(records["records"]), len(set(records["records"])))
        self.assertEqual(records["unknown_record_policy"], "fail_closed")
        self.assertEqual(set(records["records"]), set(records["subjects"]))

    def test_coordinator_exposes_planned_internal_verbs(self):
        source = (PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8")
        required = {
            "configure-parallelism", "begin-source-handoff", "authorize-target-update",
            "reauthorize-target-update", "finish-source-handoff", "abort-source-handoff",
            "begin-plan-revision", "finish-plan-revision", "abort-plan-revision",
            "quiesce-upgrade", "upgrade-target-merge", "finish-upgrade", "abort-upgrade", "publish-execution-plan",
            "refresh-target", "claim-items", "start-item", "pause-item", "resume-item",
            "takeover-item", "publish-delivery-review", "invalidate-delivery-review",
            "open-pr", "merge-pr", "cancel-delivery",
        }
        for command in required:
            self.assertIn(f'sub.add_parser("{command}")', source, command)

    def test_both_host_distributions_contain_the_new_canonical_flow_and_contract(self):
        for host in ("claude", "codex"):
            root = ROOT / "dist" / host / "software-engineering-team"
            self.assertTrue((root / "flows/requirement.md").is_file())
            self.assertTrue((root / "scripts/delivery_result.py").is_file())
            self.assertTrue((root / "skill-content/deliver/data/delivery-result-contract.json").is_file())


if __name__ == "__main__":
    unittest.main()
