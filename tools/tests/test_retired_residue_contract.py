"""Regression coverage for retired PMO, dispatcher and database surfaces."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import validate  # noqa: E402


class RetiredResidueContractTests(unittest.TestCase):
    def findings_for(self, root: Path) -> list[validate.Finding]:
        findings: list[validate.Finding] = []
        validate.check_retired_operations_residue(
            validate.build_tree(root), findings
        )
        return findings

    def test_each_retired_runtime_shape_fails_the_same_repository_check(self):
        cases = {
            "pmo-package": (
                "plugins/software-engineering-team/legacy.txt",
                "project-management-office\n",
            ),
            "shared-dispatcher": (
                "platforms/shared/software-engineering-team/legacy.md",
                "Run marketplace_run.py before continuing.\n",
            ),
            "sqlite-import": (
                "plugins/software-engineering-team/scripts/legacy.py",
                "from sqlite3 import connect\n",
            ),
            "work-order": (
                "dist/claude/software-engineering-team/legacy.md",
                "Create a work order.\n",
            ),
            "project-key": (
                "plugins/software-engineering-team/legacy.md",
                "Persist project_key for routing.\n",
            ),
            "global-agentrof": (
                "platforms/shared/software-engineering-team/legacy.md",
                "Store state below $HOME/.agentrof.\n",
            ),
            "challenge-history": (
                "plugins/software-engineering-team/legacy.md",
                "Write a challenge_round after each reviewer.\n",
            ),
            "audit-history": (
                "plugins/software-engineering-team/legacy.md",
                "Append an audit_history entry.\n",
            ),
            "lock-field": (
                "plugins/software-engineering-team/legacy.md",
                "locked: true\n",
            ),
        }
        for label, (relative, content) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text(content, encoding="utf-8")
                findings = self.findings_for(root)
                self.assertEqual(len(findings), 1, findings)
                self.assertEqual(findings[0].check, "retired_operations_residue")
                self.assertEqual(findings[0].path, relative)

    def test_retired_component_paths_fail_even_without_matching_content(self):
        cases = (
            "plugins/project-management-office/dashboard/index.html",
            "platforms/shared/software-engineering-team/control-tower/SKILL.md",
            "dist/codex/software-engineering-team/challenge-round/state.json",
        )
        for relative in cases:
            with self.subTest(relative=relative), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text("active component\n", encoding="utf-8")
                findings = self.findings_for(root)
                self.assertEqual(len(findings), 1, findings)
                self.assertEqual(findings[0].path, relative)

    def test_database_files_and_migration_catalogs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "dist/codex/software-engineering-team/state.sqlite"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"state")
            migrations = (
                root / "plugins/software-engineering-team/migrations/project"
            )
            migrations.mkdir(parents=True)

            findings = self.findings_for(root)
            self.assertEqual(
                {(finding.path, finding.message) for finding in findings},
                {
                    (
                        "dist/codex/software-engineering-team/state.sqlite",
                        "database file remains in a packaged or release surface",
                    ),
                    (
                        "plugins/software-engineering-team/migrations",
                        "obsolete project-contract migration catalog remains",
                    ),
                },
            )

    def test_user_docs_and_test_fixtures_are_outside_the_residue_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("docs/history.md", "tools/tests/legacy-fixture.md"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "Historical PMO and SQLite migration fixture.\n",
                    encoding="utf-8",
                )
            self.assertEqual(self.findings_for(root), [])


if __name__ == "__main__":
    unittest.main()
