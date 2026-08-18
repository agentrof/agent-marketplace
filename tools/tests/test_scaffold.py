"""Scaffolding keeps new plugins standalone and state-free."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import fixtures  # noqa: E402
import scaffold  # noqa: E402


class ScaffoldContracts(unittest.TestCase):
    def test_new_plugin_has_no_dependency_or_extra_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root)
            scaffold.new_plugin(root, "sample-team")
            plugin = root / "plugins" / "sample-team"
            self.assertTrue((plugin / "scripts/marketplace_paths.py").is_file())
            self.assertEqual(
                {path.name for path in plugin.iterdir()},
                {"agents", "scripts", "skill-content", "templates"},
            )
            for host in ("claude", "codex"):
                self.assertTrue((root / "dist" / host / "sample-team").is_dir())
            claude = (root / "platforms/claude/sample-team/manifest.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"dependencies"', claude)

    def test_new_agent_has_no_hidden_runtime_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root)
            scaffold.new_plugin(root, "sample-team")
            scaffold.new_agent(root, "sample-team", "sample-reviewer")
            text = (root / "plugins/sample-team/agents/sample-reviewer.md").read_text(
                encoding="utf-8"
            ).lower()
            for forbidden in ("runtime state", "run folder", "during runs"):
                self.assertNotIn(forbidden, text)
            self.assertIn("project-local input", text)

    def test_new_skills_have_no_hidden_runtime_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root)
            scaffold.new_plugin(root, "sample-team")
            scaffold.new_skill(root, "sample-team", "sample-entry", "entry")
            scaffold.new_skill(root, "sample-team", "sample-knowledge", "hidden")
            for relative in (
                "plugins/sample-team/skill-content/sample-entry/SKILL.md",
                "plugins/sample-team/skill-content/sample-knowledge/SKILL.md",
            ):
                text = (root / relative).read_text(encoding="utf-8").lower()
                for forbidden in ("runtime state", "run folder", "during runs"):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
