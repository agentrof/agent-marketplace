"""Scaffolder tests: compliant components are born, not fixed.

Every scaffold output, once registered, must pass the validator with zero
findings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
        migration = json.loads((
            self.root / "plugins" / "demo-team" / "migrations" / "manifest.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(migration["component"], "demo-team")
        self.assertEqual(migration["project_contract"], {
            "baseline": 1, "current": 1, "steps": [],
        })
        for host in ("claude", "codex"):
            self.assertTrue((
                self.root / "dist" / host / "demo-team" / ".agent-marketplace-package.json"
            ).is_file())
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

    def test_rejects_unknown_plugin_without_writes(self):
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        with self.assertRaisesRegex(SystemExit, "does not exist"):
            scaffold.new_agent(self.root, "ghost-team", "coordinator")
        with self.assertRaisesRegex(SystemExit, "does not exist"):
            scaffold.new_skill(self.root, "ghost-team", "start", "entry")
        after = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        self.assertEqual(after, before)

    def test_rejects_duplicate_agent_without_distribution_drift(self):
        agent = self.root / "plugins" / fixtures.PLUGIN / "agents" / "planner.md"
        before = agent.read_bytes()
        with self.assertRaisesRegex(SystemExit, "already exists"):
            scaffold.new_agent(self.root, fixtures.PLUGIN, "planner")
        self.assertEqual(agent.read_bytes(), before)

    def test_rejects_duplicate_skill_without_distribution_drift(self):
        skill = (self.root / "plugins" / fixtures.PLUGIN / "skill-content"
                 / "notes" / "SKILL.md")
        before = skill.read_bytes()
        with self.assertRaisesRegex(SystemExit, "already exists"):
            scaffold.new_skill(self.root, fixtures.PLUGIN, "notes", "entry")
        self.assertEqual(skill.read_bytes(), before)

    def test_rejects_unknown_skill_kind_before_writing(self):
        target = (self.root / "plugins" / fixtures.PLUGIN / "skill-content"
                  / "domain-notes")
        with self.assertRaisesRegex(SystemExit, "entry or hidden"):
            scaffold.new_skill(self.root, fixtures.PLUGIN, "domain-notes", "private")
        self.assertFalse(target.exists())

    def test_new_plugin_rolls_back_every_surface_on_build_failure(self):
        claude_market = self.root / ".claude-plugin" / "marketplace.json"
        codex_market = self.root / ".agents" / "plugins" / "marketplace.json"
        versions = self.root / "versions.json"
        before = (
            claude_market.read_bytes(), codex_market.read_bytes(),
            versions.read_bytes(),
        )
        with mock.patch.object(
                scaffold, "sync_distributions", side_effect=RuntimeError("build failed")):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                scaffold.new_plugin(self.root, "demo-team")
        self.assertEqual(
            (
                claude_market.read_bytes(), codex_market.read_bytes(),
                versions.read_bytes(),
            ), before
        )
        for path in (
            self.root / "plugins" / "demo-team",
            self.root / "platforms" / "claude" / "demo-team",
            self.root / "platforms" / "codex" / "demo-team",
        ):
            self.assertFalse(path.exists(), path)

    def test_new_agent_rolls_back_on_build_failure(self):
        target = (self.root / "plugins" / fixtures.PLUGIN / "agents"
                  / "coordinator.md")
        with mock.patch.object(
                scaffold, "sync_distributions", side_effect=RuntimeError("build failed")):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                scaffold.new_agent(self.root, fixtures.PLUGIN, "coordinator")
        self.assertFalse(target.exists())

    def test_new_skill_rolls_back_on_build_failure(self):
        target = (self.root / "plugins" / fixtures.PLUGIN / "skill-content"
                  / "domain-notes")
        with mock.patch.object(
                scaffold, "sync_distributions", side_effect=RuntimeError("build failed")):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                scaffold.new_skill(
                    self.root, fixtures.PLUGIN, "domain-notes", "hidden"
                )
        self.assertFalse(target.exists())

    def test_new_plugin_rejects_malformed_registry_before_writing(self):
        marketplace = self.root / ".agents" / "plugins" / "marketplace.json"
        marketplace.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "registry is unreadable"):
            scaffold.new_plugin(self.root, "demo-team")
        self.assertFalse((self.root / "plugins" / "demo-team").exists())
        self.assertFalse((self.root / "platforms" / "claude" / "demo-team").exists())
        self.assertFalse((self.root / "platforms" / "codex" / "demo-team").exists())

    def test_native_repository_scaffolds_both_host_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A native team is scaffolded inside the complete repository
            # contract. PMO owns the canonical common fragment and both
            # hosts own one shared delta, so an empty registry is no longer
            # a buildable marketplace source tree.
            fixtures.make_valid_root(root)
            scaffold.new_plugin(root, "demo-team")
            scaffold.new_agent(root, "demo-team", "coordinator")
            scaffold.new_skill(root, "demo-team", "start", "entry")
            plugin = root / "plugins" / "demo-team"
            self.assertTrue((root / "platforms" / "claude" / "demo-team"
                             / "manifest.json").is_file())
            self.assertTrue((root / "platforms" / "codex" / "demo-team"
                             / "manifest.json").is_file())
            self.assertTrue((root / "dist" / "claude" / "demo-team"
                             / ".claude-plugin" / "plugin.json").is_file())
            self.assertTrue((root / "dist" / "codex" / "demo-team"
                             / ".codex-plugin" / "plugin.json").is_file())
            self.assertTrue((root / "dist" / "claude" / "demo-team"
                             / "scripts" / "team_guard.py").is_file())
            self.assertTrue((root / "dist" / "codex" / "demo-team"
                             / "scripts" / "team_guard.py").is_file())
            self.assertTrue((root / "dist" / "codex" / "demo-team"
                             / "scripts" / "generate_codex_project.py").is_file())
            claude_manifest = json.loads((
                root / "platforms" / "claude" / "demo-team" / "manifest.json"
            ).read_text(encoding="utf-8"))
            self.assertIn("project-management-office",
                          claude_manifest["dependencies"])
            codex_manifest = json.loads((
                root / "platforms" / "codex" / "demo-team" / "manifest.json"
            ).read_text(encoding="utf-8"))
            self.assertNotIn("dependencies", codex_manifest)
            self.assertTrue(
                codex_manifest["interface"]["longDescription"].startswith(
                    "Requires Project Management Office."
                )
            )
            for host in ("claude", "codex"):
                contract = (root / "platforms" / host / "demo-team"
                            / "host-contract.md").read_text(encoding="utf-8")
                self.assertIn(
                    "AGENT_MARKETPLACE_PMO_READY: project-management-office", contract
                )
                self.assertIn(
                    "no files or project state were changed", contract.lower()
                )
            project = root / "consumer"
            (project / ".git").mkdir(parents=True)
            (project / "workspace").mkdir()
            (project / "workspace" / "config.json").write_text(
                json.dumps({"managed_by": "demo-team"}), encoding="utf-8"
            )
            generator = (
                root / "dist" / "codex" / "demo-team" / "scripts"
                / "generate_codex_project.py"
            )
            process = subprocess.run(
                [sys.executable, str(generator), "--project-root", str(project)],
                capture_output=True,
                text=True,
                env={**os.environ},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            generated = project / ".codex" / "agents" / "coordinator.toml"
            self.assertTrue(generated.is_file())
            self.assertTrue(generated.read_text(encoding="utf-8").startswith(
                "# Generated by Agent Marketplace demo-team; do not edit by hand."
            ))
            findings = validate.run(root)
            self.assertEqual(findings, [], f"native scaffold must be clean: {findings}")


if __name__ == "__main__":
    unittest.main()
