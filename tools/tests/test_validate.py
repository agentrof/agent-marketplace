"""Validator test suite: the guard that guards the guard.

Contract per fixture: planting exactly one defect on a valid tree yields
exactly one finding, of the matching check, with a non-empty remediation.
The meta-test locks the check registry to the fixture registry.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import fixtures  # noqa: E402
import validate  # noqa: E402


class ValidatorFixtureTests(unittest.TestCase):
    def run_on(self, build_defect=None, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.make_valid_root(root)
            if build_defect:
                build_defect(root)
            if extra:
                extra(root)
            return validate.run(root)

    def test_valid_root_is_clean(self):
        findings = self.run_on()
        self.assertEqual(findings, [], f"valid root must be clean, got: {findings}")

    def test_agent_and_skill_names_are_scoped_per_plugin(self):
        import json
        from fixtures import VALID_AGENT, VALID_SKILL, write

        def add_second_plugin(root: Path) -> None:
            marketplace_path = root / ".claude-plugin" / "marketplace.json"
            marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
            marketplace["plugins"].append({
                "name": "second-team",
                "source": "./plugins/second-team",
                "description": "fixture plugin",
                "version": "0.0.1",
                "license": "MIT",
            })
            write(marketplace_path, json.dumps(marketplace, indent=2))
            write(root / "plugins" / "second-team" / ".claude-plugin"
                  / "plugin.json", json.dumps({
                      "name": "second-team",
                      "version": "0.0.1",
                      "description": "fixture plugin",
                      "license": "MIT",
                  }, indent=2))
            write(root / "plugins" / "second-team" / "agents" / "planner.md",
                  VALID_AGENT)
            write(root / "plugins" / "second-team" / "skills" / "notes"
                  / "SKILL.md", VALID_SKILL)

        findings = self.run_on(extra=add_second_plugin)
        self.assertEqual(findings, [], findings)

    def test_meta_registry_lockstep(self):
        """A check without a fixture (or vice versa) cannot merge."""
        self.assertEqual(
            set(validate.CHECKS.keys()),
            set(fixtures.BUILDERS.keys()),
            "validator checks and fixture builders must match one-to-one",
        )

    def test_each_fixture_yields_exactly_one_matching_finding(self):
        for check_id, builder in sorted(fixtures.BUILDERS.items()):
            with self.subTest(check=check_id):
                findings = self.run_on(builder)
                self.assertEqual(
                    len(findings), 1,
                    f"{check_id}: expected exactly one finding, got {findings}",
                )
                finding = findings[0]
                self.assertEqual(finding.check, check_id)
                self.assertTrue(
                    finding.remediation.strip(),
                    f"{check_id}: remediation must be non-empty",
                )

    def test_color_completeness_uncolored_type_fires(self):
        """A doc type without a tag:#doc/<type> color group is an error;
        adding a type forces its color in the same commit."""
        import json
        from fixtures import PLUGIN, VALID_VAULT_POLICY, write

        def uncolored(root: Path) -> None:
            policy = json.loads(json.dumps(VALID_VAULT_POLICY))
            policy["extra_doc_types"].append("sketch")
            write(root / "plugins" / PLUGIN / "skills" / "notes" / "data"
                  / "vault-policy.json", json.dumps(policy, indent=2))

        findings = self.run_on(uncolored)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "vault_policy_shape")
        self.assertIn("no graph color group", findings[0].message)

    def test_color_completeness_dead_legend_fires(self):
        """A color group whose tag names no known type is a dead legend
        entry; graph.json parity noise is tolerated (the seed derives
        from the valid policy)."""
        import json
        from fixtures import PLUGIN, VALID_VAULT_POLICY, write

        def dead_legend(root: Path) -> None:
            policy = json.loads(json.dumps(VALID_VAULT_POLICY))
            policy["graph_color_groups"].append("tag:#doc/ghost")
            write(root / "plugins" / PLUGIN / "skills" / "notes" / "data"
                  / "vault-policy.json", json.dumps(policy, indent=2))

        findings = self.run_on(dead_legend)
        messages = [f.message for f in findings
                    if f.check == "vault_policy_shape"]
        self.assertTrue(any("names no known doc type" in m
                            for m in messages), findings)

    def test_agent_missing_output_contract_fires(self):
        """Dropping the required output_contract key is a single
        frontmatter_shape finding naming the missing key."""
        from fixtures import PLUGIN, VALID_AGENT, write

        def drop_contract(root: Path) -> None:
            text = VALID_AGENT.replace("output_contract: prose\n", "")
            write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)

        findings = self.run_on(drop_contract)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "frontmatter_shape")
        self.assertIn("output_contract", findings[0].message)

    def test_agent_bad_output_contract_enum_fires(self):
        """A value outside the enum is a single frontmatter_shape finding."""
        from fixtures import PLUGIN, VALID_AGENT, write

        def bad_contract(root: Path) -> None:
            text = VALID_AGENT.replace("output_contract: prose",
                                       "output_contract: bogus")
            write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)

        findings = self.run_on(bad_contract)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "frontmatter_shape")
        self.assertIn("output_contract", findings[0].message)

    def test_agent_human_title_is_derived_from_canonical_id(self):
        from fixtures import PLUGIN, VALID_AGENT, write

        def bad_title(root: Path) -> None:
            text = VALID_AGENT.replace("# Planner", "# Planning Agent")
            write(root / "plugins" / PLUGIN / "agents" / "planner.md", text)

        findings = self.run_on(bad_title)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "agent_name")
        self.assertIn("agent title", findings[0].message)
        self.assertEqual(validate.display_title("qa-devops-api"), "QA DevOps API")

    def test_size_caps_read_from_limits_file(self):
        """Lowering a cap in the fixture's limits.json makes size_caps
        fire: the data file is live, not a decorative mirror."""
        import json

        def lower_agent_cap(root: Path) -> None:
            path = root / "tools" / "data" / "limits.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["authoring_caps"]["agent_body_max_lines"] = 10
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        findings = self.run_on(lower_agent_cap)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "size_caps")
        self.assertIn("(cap 10)", findings[0].message)

    def test_missing_limits_file_falls_back(self):
        """Deleting limits.json yields only the shape finding: size_caps
        neither crashed nor fired, the constants carried it."""

        def drop_limits(root: Path) -> None:
            (root / "tools" / "data" / "limits.json").unlink()

        findings = self.run_on(drop_limits)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0].check, "limits_config_shape")

    def test_shipped_limits_match_fallback_constants(self):
        """The data file and the in-code fallbacks move together; a cap
        bump edits both in one commit."""
        caps = {
            "agent_body_max_lines": validate.AGENT_BODY_MAX_LINES,
            "skill_max_lines": validate.SKILL_MAX_LINES,
            "skill_warn_lines": validate.SKILL_WARN_LINES,
            "skill_max_bytes": validate.SKILL_MAX_BYTES,
            "constitution_max_lines": validate.CONSTITUTION_MAX_LINES,
            "flow_max_lines": validate.FLOW_MAX_LINES,
            "reference_warn_lines": validate.REFERENCE_WARN_LINES,
        }
        import json
        shipped = json.loads(
            fixtures.REAL_LIMITS_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(shipped["authoring_caps"], caps)

    def test_out_of_scope_violations_are_not_caught(self):
        findings = self.run_on(extra=fixtures.plant_out_of_scope_violations)
        self.assertEqual(
            findings, [],
            "violations under assets/ and memory/ must never be flagged",
        )

    def test_output_is_deterministic(self):
        def all_defects(root: Path) -> None:
            for builder in fixtures.BUILDERS.values():
                builder(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.make_valid_root(root)
            all_defects(root)
            first = validate.run(root)
            second = validate.run(root)
            self.assertEqual(first, second, "two runs must be byte-identical")
            self.assertGreater(len(first), 0)


if __name__ == "__main__":
    unittest.main()
