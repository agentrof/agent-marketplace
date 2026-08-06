"""PMO schema 4 to 5 legacy backlog adoption fixtures."""

from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = (REPO / "plugins" / "project-management-office" / "migrations"
          / "database" / "4-5.py")


def load_runner():
    spec = importlib.util.spec_from_file_location("pmo_migration_4_5", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PmoMigrationV5Tests(unittest.TestCase):
    def database(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
          PRAGMA foreign_keys=ON;
          CREATE TABLE projects (id INTEGER PRIMARY KEY, project_key TEXT);
          CREATE TABLE work_items (
            id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT,
            external_id TEXT, status TEXT
          );
          CREATE TABLE story_criteria (
            id INTEGER PRIMARY KEY, project_id INTEGER,
            criterion_id TEXT, story_id INTEGER, disposition TEXT, reason TEXT
          );
          CREATE TABLE work_orders (
            id INTEGER PRIMARY KEY, story_id INTEGER, status TEXT
          );
        """)
        return con

    def test_empty_backlog_creates_no_legacy_program(self):
        con = self.database()
        con.execute("INSERT INTO projects VALUES (1, 'empty')")
        result = load_runner().migrate(con, {})
        self.assertEqual(result["legacy_projects"], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM programs").fetchone()[0], 0)
        self.assertTrue(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'work_item_owners'"
        ).fetchone())
        finding_columns = {
            row[1] for row in con.execute("PRAGMA table_info(planning_findings)")
        }
        self.assertTrue({"finding_kind", "review_rounds"} <= finding_columns)

    def test_unfinished_and_terminal_legacy_variants(self):
        con = self.database()
        con.executemany("INSERT INTO projects VALUES (?, ?)", [(1, "open"), (2, "done")])
        con.executemany("INSERT INTO work_items VALUES (?, ?, 'story', ?, ?)", [
            (1, 1, "WP-01", "planned"),
            (2, 1, "WP-02", "in_development"),
            (3, 2, "WP-03", "done"),
        ])
        con.execute("INSERT INTO story_criteria VALUES (1, 1, 'AC-001', 1, 'covered', '')")
        result = load_runner().migrate(con, {})
        self.assertEqual(result["legacy_projects"], 2)
        programs = con.execute("SELECT project_id, status FROM programs ORDER BY project_id").fetchall()
        self.assertEqual(programs, [(1, "draft"), (2, "complete")])
        releases = con.execute("SELECT status FROM releases ORDER BY id").fetchall()
        self.assertEqual(releases, [("draft",), ("complete",)])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM work_item_releases WHERE provenance='migrated_unverified'").fetchone()[0], 3)
        self.assertEqual(con.execute("SELECT criterion_id FROM story_criteria").fetchone()[0], "legacy:AC-001")
        self.assertEqual(con.execute("SELECT status FROM backlog_plans").fetchone()[0], "draft")
        finding = con.execute("SELECT external_id, severity FROM planning_findings").fetchone()
        self.assertEqual(finding, ("LEGACY-WP-02", "blocker"))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM planning_gates").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
