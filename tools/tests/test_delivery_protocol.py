"""Cross-host protocol inventory and command-surface contracts."""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"


class DeliveryProtocolTests(unittest.TestCase):
    DATA = PLUGIN / "skill-content/deliver/data"

    @classmethod
    def load_contract(cls, name: str) -> dict:
        return json.loads((cls.DATA / name).read_text(encoding="utf-8"))

    def test_result_and_record_registries_are_closed_and_unique(self):
        result = self.load_contract("delivery-result-contract.json")
        def no_duplicates(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise AssertionError(f"duplicate JSON key: {key}")
                out[key] = value
            return out
        records = json.loads(
            (self.DATA / "delivery-control-record-contract.json").read_text(),
            object_pairs_hook=no_duplicates,
        )
        codes = result["finding_codes"]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,63}", code) for code in codes))
        self.assertEqual(records["schema_version"], 1)
        self.assertEqual(len(records["records"]), len(set(records["records"])))
        self.assertEqual(records["unknown_record_policy"], "fail_closed")
        self.assertEqual(set(records["records"]), set(records["subjects"]))

    def test_runtime_record_emitters_match_the_closed_registry(self):
        source = (PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        emitted: dict[str, list[set[str]]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            record = values.get("Record")
            if not isinstance(record, ast.Constant) or not isinstance(record.value, str):
                continue
            fields = set(values) - {"Record", "Protocol"}
            emitted.setdefault(record.value.replace("-", "_"), []).append(fields)

        contract = self.load_contract("delivery-control-record-contract.json")
        registered = contract["records"]
        self.assertEqual(set(emitted), set(registered))
        for record, variants in emitted.items():
            allowed = set(registered[record])
            for fields in variants:
                self.assertLessEqual(fields, allowed, record)

    def test_provider_and_receipt_contracts_match_runtime_surface(self):
        provider = self.load_contract("delivery-provider-contract.json")
        receipt = self.load_contract("delivery-receipt-contract.json")
        provider_source = (PLUGIN / "scripts/delivery_provider.py").read_text(
            encoding="utf-8"
        )
        coordinator_source = (PLUGIN / "scripts/delivery_git.py").read_text(
            encoding="utf-8"
        )

        self.assertEqual(provider["provider"], "github")
        self.assertEqual(provider["adapter"], "delivery_provider.GitHubProvider")
        self.assertTrue(provider["required_capabilities"]["merge_commit"])
        self.assertFalse(provider["required_capabilities"]["squash_merge"])
        self.assertFalse(provider["required_capabilities"]["rebase_merge"])
        self.assertFalse(provider["branch_lifecycle"]["request_head_deletion"])
        self.assertIn("class GitHubProvider", provider_source)
        self.assertIn('"--delete-branch=false"', provider_source)

        self.assertEqual(receipt["kind"], "item-writer-v1")
        self.assertEqual(receipt["states"], ["pending", "verified"])
        self.assertEqual(receipt["provider_receipt"]["kind"], "pr-create-v1")
        self.assertEqual(
            receipt["target_update_receipt"]["kind"], "target-update-v1"
        )
        for literal in ("item-writer-v1", "pr-create-v1", "target-update-v1"):
            self.assertIn(literal, coordinator_source)

    def test_protocol_contract_is_closed(self):
        protocol = self.load_contract("delivery-protocol-1.json")
        self.assertEqual(protocol["protocol_version"], "delivery-protocol-1")
        self.assertEqual(
            protocol["merge_policy"],
            "merge-commit-only; squash and rebase fail closed",
        )

    def test_public_protocol_entries_equal_canonical_entry_skills(self):
        protocol = (ROOT / "docs/requirement-delivery-protocol.md").read_text(
            encoding="utf-8"
        )
        public_block = protocol.split("## Public entry surface", 1)[1].split(
            "```text", 1
        )[1].split("```", 1)[0]
        documented = {
            line.strip().removeprefix("/")
            for line in public_block.splitlines()
            if line.strip().startswith("/")
        }
        canonical = set()
        for path in (PLUGIN / "skill-content").glob("*/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            header = text.split("---", 2)[1]
            if re.search(r"(?m)^exposure:\s*entry\s*$", header):
                canonical.add(path.parent.name)
        self.assertEqual(documented, canonical)

    def test_every_canonical_flow_has_an_explicit_skill_reader(self):
        skill_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((PLUGIN / "skill-content").glob("*/SKILL.md"))
        )
        for flow in sorted((PLUGIN / "flows").glob("*.md")):
            self.assertIn(f"flows/{flow.name}", skill_text, flow.name)

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
            for name in (
                "delivery-result-contract.json",
                "delivery-provider-contract.json",
                "delivery-receipt-contract.json",
            ):
                self.assertTrue((root / "skill-content/deliver/data" / name).is_file())


if __name__ == "__main__":
    unittest.main()
