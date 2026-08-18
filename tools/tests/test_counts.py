"""Counts tool tests: derived numbers live only in the README marker block."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import counts  # noqa: E402
README = """# Fixture Marketplace

Intro text.

<!-- counts:start -->
stale
<!-- counts:end -->

Outro text.
"""


class CountsTests(unittest.TestCase):
    def make_root(self) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        plugin = root / "plugins" / "sample-team"
        (plugin / "agents").mkdir(parents=True)
        (plugin / "agents" / "developer.md").write_text(
            "# Developer\n", encoding="utf-8"
        )
        entry = plugin / "skill-content" / "entry-skill"
        entry.mkdir(parents=True)
        (entry / "SKILL.md").write_text(
            "---\nname: entry-skill\nexposure: entry\n---\n", encoding="utf-8"
        )
        knowledge = plugin / "skill-content" / "knowledge-skill"
        knowledge.mkdir(parents=True)
        (knowledge / "SKILL.md").write_text(
            "---\nname: knowledge-skill\nexposure: knowledge\n---\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(README, encoding="utf-8")
        return root

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_compute_counts_from_tree(self):
        root = self.make_root()
        computed = counts.compute(root)
        self.assertEqual(computed["plugins"], 1)
        self.assertEqual(computed["agents"], 1)
        self.assertEqual(computed["entry_skills"], 1)
        self.assertEqual(computed["knowledge_skills"], 1)

    def test_inject_rewrites_only_marker_block(self):
        root = self.make_root()
        block = counts.render_block(counts.compute(root))
        old, new = counts.inject(root / "README.md", block)
        self.assertIn("stale", old)
        self.assertNotIn("stale", new)
        self.assertIn("Intro text.", new)
        self.assertIn("Outro text.", new)
        self.assertIn("| 1 | 1 | 1 | 1 |", new)

    def test_check_detects_drift(self):
        root = self.make_root()
        block = counts.render_block(counts.compute(root))
        _, fresh = counts.inject(root / "README.md", block)
        (root / "README.md").write_text(fresh, encoding="utf-8")
        # Current -> no drift.
        _, same = counts.inject(root / "README.md", block)
        self.assertEqual(fresh, same)
        # Hand-edit a number inside the block -> drift.
        tampered = fresh.replace("| 1 | 1 | 1 | 1 |", "| 9 | 9 | 9 | 9 |")
        (root / "README.md").write_text(tampered, encoding="utf-8")
        old, new = counts.inject(root / "README.md", block)
        self.assertNotEqual(old, new, "hand-edited counts must register as drift")

    def test_missing_markers_is_fatal(self):
        root = self.make_root()
        (root / "README.md").write_text("# No markers here\n", encoding="utf-8")
        block = counts.render_block(counts.compute(root))
        with self.assertRaises(SystemExit):
            counts.inject(root / "README.md", block)


if __name__ == "__main__":
    unittest.main()
