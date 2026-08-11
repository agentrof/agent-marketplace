"""PMO schema 4 to 5 legacy backlog adoption fixtures."""

from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = (REPO / "plugins" / "project-management-office" / "migrations"
          / "database" / "4-5.py")
RUNNER_V6 = (REPO / "plugins" / "project-management-office" / "migrations"
             / "database" / "5-6.py")


def load_runner():
    spec = importlib.util.spec_from_file_location("pmo_migration_4_5", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_v6_runner():
    spec = importlib.util.spec_from_file_location("pmo_migration_5_6", RUNNER_V6)
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


class PmoMigrationV6Tests(unittest.TestCase):
    def test_experience_runs_claims_and_gates_survive_abandonment_migration(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
          PRAGMA foreign_keys=ON;
          CREATE TABLE projects (id INTEGER PRIMARY KEY);
          CREATE TABLE experience_runs (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            run_key TEXT NOT NULL,
            program_key TEXT NOT NULL,
            release_key TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','released')),
            created_at TEXT NOT NULL,
            released_at TEXT NOT NULL DEFAULT '',
            UNIQUE(project_id, run_key)
          );
          CREATE TABLE experience_node_claims (
            run_id INTEGER NOT NULL REFERENCES experience_runs(id),
            node_ref TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY(run_id,node_ref)
          );
          CREATE TABLE experience_gates (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES experience_runs(id),
            gate_name TEXT NOT NULL,
            decision TEXT NOT NULL,
            revision_hash TEXT NOT NULL,
            decided_by TEXT NOT NULL DEFAULT 'owner',
            decided_at TEXT NOT NULL
          );
          INSERT INTO projects VALUES (1);
          INSERT INTO experience_runs VALUES
            (1,1,'SHOP-EXR-1','PRG-001','','s1','active','t1',''),
            (2,1,'SHOP-EXR-2','PRG-001','','s2','released','t2','t3');
          INSERT INTO experience_node_claims VALUES
            (1,'marketplace','t1'), (2,'checkout','t2');
          INSERT INTO experience_gates VALUES
            (1,1,'program','approved','sha256:a','owner','t1');
        """)
        result = load_v6_runner().migrate(con, {})
        self.assertEqual(result, {"experience_runs_preserved": 2})
        self.assertEqual(con.execute(
            "SELECT run_key,status FROM experience_runs ORDER BY id"
        ).fetchall(), [
            ("SHOP-EXR-1", "active"), ("SHOP-EXR-2", "released"),
        ])
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM experience_node_claims"
        ).fetchone()[0], 2)
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM experience_gates"
        ).fetchone()[0], 1)
        con.execute(
            "UPDATE experience_runs SET status='abandoned', abandoned_at='t4',"
            " abandon_reason='reconcile' WHERE id=1"
        )
        self.assertEqual(con.execute(
            "SELECT status,abandon_reason FROM experience_runs WHERE id=1"
        ).fetchone(), ("abandoned", "reconcile"))
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
