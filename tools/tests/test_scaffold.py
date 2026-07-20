"""Scaffolder tests: compliant components are born, not fixed.

Every scaffold output, once registered, must pass the validator with zero
findings.
"""

from __future__ import annotations

import json
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


# new-plugin registers itself in the marketplace; scaffold.regenerate
# refreshes the generated harness artifacts after every subcommand.


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixtures.make_valid_root(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_plugin_passes_validation(self):
        scaffold.new_plugin(self.root, "demo-team")
        scaffold.regenerate(self.root)
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded plugin must be clean: {findings}")

    def test_new_agent_passes_validation(self):
        scaffold.new_agent(self.root, fixtures.PLUGIN, "coordinator")
        scaffold.regenerate(self.root)
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded agent must be clean: {findings}")

    def test_new_skill_both_kinds_pass_validation(self):
        scaffold.new_skill(self.root, fixtures.PLUGIN, "intake", "entry")
        scaffold.new_skill(self.root, fixtures.PLUGIN, "domain-notes", "hidden")
        scaffold.regenerate(self.root)
        findings = validate.run(self.root)
        self.assertEqual(findings, [], f"scaffolded skills must be clean: {findings}")

    def test_rejects_non_kebab_names(self):
        with self.assertRaises(SystemExit):
            scaffold.new_plugin(self.root, "Bad_Name")
        with self.assertRaises(SystemExit):
            scaffold.new_agent(self.root, fixtures.PLUGIN, "CamelCase")
        with self.assertRaises(SystemExit):
            scaffold.new_skill(self.root, fixtures.PLUGIN, "snake_case", "entry")


if __name__ == "__main__":
    unittest.main()
