"""Scaffolder tests: compliant components are born, not fixed.

Every scaffold output, once registered, must pass the validator with zero
findings.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import fixtures  # noqa: E402
import scaffold  # noqa: E402
import validate  # noqa: E402


# new-plugin registers itself in the marketplace.


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixtures.make_valid_root(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_plugin_passes_validation(self):
        scaffold.new_plugin(self.root, "demo-team")
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded plugin must be clean: {findings}")

    def test_new_agent_passes_validation(self):
        scaffold.new_agent(self.root, fixtures.PLUGIN, "coordinator")
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded agent must be clean: {findings}")

    def test_new_skill_both_kinds_pass_validation(self):
        scaffold.new_skill(self.root, fixtures.PLUGIN, "intake", "entry")
        scaffold.new_skill(self.root, fixtures.PLUGIN, "domain-notes", "hidden")
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded skills must be clean: {findings}")

    def test_rejects_non_kebab_names(self):
        with self.assertRaises(SystemExit):
            scaffold.new_plugin(self.root, "Bad_Name")
        with self.assertRaises(SystemExit):
            scaffold.new_agent(self.root, fixtures.PLUGIN, "CamelCase")
        with self.assertRaises(SystemExit):
            scaffold.new_skill(self.root, fixtures.PLUGIN, "snake_case", "entry")

    def test_native_repository_scaffolds_both_host_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.write(root / ".claude-plugin" / "marketplace.json", json.dumps({
                "name": "agent-marketplace", "owner": {"name": "Agentrof"},
                "metadata": {"description": "native", "version": "0.1.0"},
                "plugins": [],
            }))
            fixtures.write(root / ".agents" / "plugins" / "marketplace.json",
                           json.dumps({
                               "name": "agent-marketplace",
                               "interface": {"displayName": "Agent Marketplace"},
                               "plugins": [],
                           }))
            (root / "plugins").mkdir()
            (root / "tools" / "data").mkdir(parents=True)
            shutil.copyfile(fixtures.REAL_MODEL_CONFIG,
                            root / "tools" / "data" / "models.json")
            shutil.copyfile(fixtures.REAL_LIMITS_CONFIG,
                            root / "tools" / "data" / "limits.json")
            scaffold.new_plugin(root, "demo-team")
            plugin = root / "plugins" / "demo-team"
            self.assertTrue((root / "platforms" / "claude" / "demo-team"
                             / "manifest.json").is_file())
            self.assertTrue((root / "platforms" / "codex" / "demo-team"
                             / "manifest.json").is_file())
            self.assertTrue((root / "dist" / "claude" / "demo-team"
                             / ".claude-plugin" / "plugin.json").is_file())
            self.assertTrue((root / "dist" / "codex" / "demo-team"
                             / ".codex-plugin" / "plugin.json").is_file())
            findings = validate.run(root)
            self.assertEqual(findings, [], f"native scaffold must be clean: {findings}")


if __name__ == "__main__":
    unittest.main()
