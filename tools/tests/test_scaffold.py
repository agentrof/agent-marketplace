"""Scaffolding keeps new plugins standalone and migration-free."""

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
    def test_new_plugin_has_no_dependency_or_migration_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root)
            scaffold.new_plugin(root, "sample-team")
            plugin = root / "plugins" / "sample-team"
            self.assertTrue((plugin / "scripts/marketplace_paths.py").is_file())
            self.assertFalse((plugin / "migrations").exists())
            for host in ("claude", "codex"):
                self.assertTrue((root / "dist" / host / "sample-team").is_dir())
            claude = (root / "platforms/claude/sample-team/manifest.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("project-management-office", claude)
            self.assertNotIn('"dependencies"', claude)


if __name__ == "__main__":
    unittest.main()
