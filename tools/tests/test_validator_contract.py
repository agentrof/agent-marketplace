"""Mutation tests for the repository validator's active architecture rules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import fixtures  # noqa: E402
import validate  # noqa: E402


class ValidatorContractTests(unittest.TestCase):
    def fixture(self, temporary: str) -> Path:
        root = Path(temporary)
        fixtures.make_valid_root(root)
        return root

    @staticmethod
    def checks(root: Path) -> list[str]:
        return [finding.check for finding in validate.run(root)]

    def test_valid_single_team_fixture_is_clean_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            first = validate.run(root)
            second = validate.run(root)
            self.assertEqual(first, [])
            self.assertEqual(first, second)

    def test_database_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            database = root / "plugins/software-engineering-team/cache.sqlite"
            database.write_bytes(b"fixture")
            self.assertIn("packaged_state_files", self.checks(root))

    def test_skill_project_scope_is_closed_and_external_is_entry_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "issue-report/SKILL.md"
            )
            text = path.read_text(encoding="utf-8").replace(
                "project_scope: external", "project_scope: remote"
            )
            path.write_text(text, encoding="utf-8")
            self.assertIn("frontmatter_shape", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "issue-report/SKILL.md"
            )
            text = path.read_text(encoding="utf-8").replace(
                "exposure: entry", "exposure: internal"
            )
            path.write_text(text, encoding="utf-8")
            self.assertIn("frontmatter_shape", self.checks(root))

    def test_plugin_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "platforms/claude/software-engineering-team/manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["dependencies"] = ["some-team"]
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            self.assertIn("single_team_contract", self.checks(root))

    def test_graph_palette_identity_query_and_rgb_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "obsidian-vault/data/vault-policy.json"
            )
            policy = json.loads(path.read_text(encoding="utf-8"))
            story = next(
                group for group in policy["graph_color_groups"]
                if group["id"] == "story"
            )
            story["query"] = "tag:#doc/wrong"
            story["rgb"] = -1
            path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
            self.assertIn("vault_policy_shape", self.checks(root))

    def test_lazy_fragment_property_drift_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "obsidian-vault/data/vault-policy.json"
            )
            policy = json.loads(path.read_text(encoding="utf-8"))
            del policy["property_types"]["package_status"]
            path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertTrue(any(
                finding.check == "vault_policy_shape"
                and "lazy_fragments['business_analysis']"
                in finding.message
                and "package_status" in finding.message
                for finding in findings
            ))

    def test_vault_policy_and_types_seed_property_maps_cannot_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/templates/vault/"
                ".obsidian/types.json"
            )
            types = json.loads(path.read_text(encoding="utf-8"))
            types["types"]["owner_role"] = "number"
            path.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertTrue(any(
                finding.check == "vault_policy_shape"
                and "types.json property map" in finding.message
                for finding in findings
            ))

    def test_delivery_contract_set_and_merge_policy_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            data = (
                root / "plugins/software-engineering-team/skill-content/"
                "deliver/data"
            )
            receipt = data / "delivery-receipt-contract.json"
            receipt.unlink()
            self.assertIn("delivery_contract_shape", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            protocol_path = (
                root / "plugins/software-engineering-team/skill-content/"
                "deliver/data/delivery-protocol-1.json"
            )
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            protocol["merge_policy"] = "provider default"
            protocol_path.write_text(
                json.dumps(protocol, indent=2) + "\n", encoding="utf-8"
            )
            self.assertIn("delivery_contract_shape", self.checks(root))

    def test_execution_profile_tables_are_validated(self):
        for relative, mutate in (
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["high"].update(model="gpt-5")),
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["low"].update(effort="extreme")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["inherit"].update(effort="low")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"].update(fast={})),
                ("tools/data/models.json",
                 lambda value: value["reasoning_levels"].append("extreme"))):
            with self.subTest(path=relative), \
                    tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                self.assertIn("execution_profiles", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "tools/data/models.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["reasoning_levels"].remove("low")
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertIn(
                ("tools/data/models.json", "model_config_shape"),
                {(finding.path, finding.check) for finding in findings},
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            (root / "platforms/codex/execution-profiles.json").unlink()
            findings = validate.run(root)
            self.assertIn(
                ("platforms/codex/execution-profiles.json", "execution_profiles"),
                {(finding.path, finding.check) for finding in findings},
            )

    def test_delivery_verification_policy_rejects_diagnostic_seal_and_weakened_scope(self):
        for mutate in (
                lambda value: value["final_modes"].update(qa_engineer=["qa_diagnostic", "qa_final"]),
                lambda value: value["mutation_scope"].update(unknown_file_policy="skip"),
                lambda value: value.update(raw_evidence_max_age_seconds=86401),
                lambda value: value.update(undeclared_field=True)):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / "plugins/software-engineering-team/skill-content/deliver/data/delivery-verification-policy.json"
                value = json.loads(path.read_text())
                mutate(value)
                path.write_text(json.dumps(value))
                self.assertIn("delivery_contract_shape", self.checks(root))


if __name__ == "__main__":
    unittest.main()
