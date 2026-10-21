"""Packaged surfaces remain free of durable runtime databases."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import validate  # noqa: E402


class PackagedStateContractTests(unittest.TestCase):
    @staticmethod
    def findings_for(root: Path) -> list[validate.Finding]:
        findings: list[validate.Finding] = []
        validate.check_packaged_state_files(validate.build_tree(root), findings)
        return findings

    def test_database_files_are_rejected_from_product_surfaces(self):
        relative = "dist/codex/software-engineering-team/runtime.sqlite3"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"state")
            findings = self.findings_for(root)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual((findings[0].check, findings[0].path), ("packaged_state_files", relative))

    def test_docs_and_test_fixtures_are_not_product_state_surfaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("docs/example.db", "tools/tests/fixture.sqlite"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            self.assertEqual(self.findings_for(root), [])


if __name__ == "__main__":
    unittest.main()
