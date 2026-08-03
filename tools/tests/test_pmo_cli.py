"""Unit tests for the PMO plugin's central-database CLI."""

import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI_PATH = REPO / "plugins" / "project-management-office" / "scripts" / "pmo_cli.py"

spec = importlib.util.spec_from_file_location("pmo_cli", CLI_PATH)
pmo_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pmo_cli)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = pmo_cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


BACKLOG = {
    "epics": [
        {"external_id": "EP-01", "title": "Account management", "goal": "Users manage accounts."}
    ],
    "stories": [
        {
            "external_id": "WP-01", "epic": "EP-01", "title": "Password reset flow",
            "type": "feature", "priority": "critical: walking skeleton",
            "scope": "Request, email token, set new password.",
            "excludes": "Two-factor reset.",
            "dor": "Brief BR-001..BR-004 accepted.",
            "dod": "All ACs pass; review and qa green.",
            "dod_items": [
                "A member with a valid token sets a new password and signs in with it.",
                "An expired token is rejected with the documented message.",
            ],
        },
        {
            "external_id": "WP-02", "epic": "EP-01", "title": "Profile editing",
            "type": "feature", "priority": "high: closes the account loop",
            "depends_on": [{"item": "WP-01",
                            "reason": "editing needs the authenticated session WP-01 ships"}],
            "scope": "Edit display name and avatar.",
            "excludes": "Email change.",
            "dor": "Brief BR-005 accepted.",
            "dod": "All ACs pass.",
        },
    ],
    "criteria": [
        {"criterion_id": "AC-001", "story": "WP-01", "disposition": "covered"},
        {"criterion_id": "AC-002", "disposition": "deferred", "reason": "Out of v1."},
    ],
    "open_questions": ["Which mail provider?"],
}

class PmoCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("AGENTROF_HOME")
        os.environ["AGENTROF_HOME"] = str(Path(self.tmp.name) / "agentrof")
        # The worktree-binding guard compares the caller's cwd against each
        # order's claimed worktree, so tests run from real directories.
        self.wt_main = Path(self.tmp.name) / "wt-main"
        self.wt_two = Path(self.tmp.name) / "wt-two"
        self.wt_main.mkdir()
        self.wt_two.mkdir()
        self._old_cwd = os.getcwd()
        os.chdir(self.wt_main)
        code, _, err = run(["init-db"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["project", "register", "--key", "shop",
                            "--name", "Shop", "--team", "software-engineering-team"])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        os.chdir(self._old_cwd)
        if self._old_home is None:
            os.environ.pop("AGENTROF_HOME", None)
        else:
            os.environ["AGENTROF_HOME"] = self._old_home
        self.tmp.cleanup()

    def db(self):
        con = sqlite3.connect(Path(os.environ["AGENTROF_HOME"]) / "agentrof.db")
        con.row_factory = sqlite3.Row
        return con

    def import_backlog(self, data=None):
        path = Path(self.tmp.name) / "backlog.json"
        path.write_text(json.dumps(data or BACKLOG), encoding="utf-8")
        return run(["item", "import", "--project-key", "shop",
                    "--json-file", str(path)])

    def init_wo(self, wo_key="wo1", worktree=None, story="WP-01"):
        worktree = worktree or str(self.wt_main)
        argv = ["work-order", "init", "--project-key", "shop",
                "--work-order-key", wo_key,
                "--request", "build it", "--worktree", worktree]
        if story:
            argv += ["--story", story]
        return run(argv)

    # -- clock ------------------------------------------------------------------

    def test_now_prints_iso_utc(self):
        from datetime import datetime
        code, out, err = run(["now"])
        self.assertEqual(code, 0, err)
        value = out.strip()
        self.assertTrue(value.endswith("+00:00"), value)
        datetime.fromisoformat(value)  # parseable or it raises

    def test_now_date_and_compact_shapes(self):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        code, out, _ = run(["now", "--date"])
        self.assertEqual((code, out.strip()), (0, today.isoformat()))
        code, out, _ = run(["now", "--compact"])
        self.assertEqual((code, out.strip()), (0, today.strftime("%Y%m%d")))

    def test_now_flags_are_exclusive(self):
        code, _, _ = run(["now", "--date", "--compact"])
        self.assertEqual(code, 2)

    def test_wo_init_rejects_wrong_date_prefix(self):
        from datetime import datetime, timedelta, timezone
        self.import_backlog()
        today = datetime.now(timezone.utc).date()
        for prefix in ((today - timedelta(days=2)).strftime("%Y%m%d"),
                       (today + timedelta(days=1)).strftime("%Y%m%d"),
                       "99999999"):
            code, _, err = self.init_wo(wo_key=f"{prefix}-password-reset")
            self.assertEqual(code, 1, err)
            self.assertIn("now --compact", err)

    def test_wo_init_accepts_current_dated_and_undated_keys(self):
        from datetime import datetime, timedelta, timezone
        self.import_backlog()
        today = datetime.now(timezone.utc).date()
        code, _, err = self.init_wo(
            wo_key=f"{today.strftime('%Y%m%d')}-password-reset")
        self.assertEqual(code, 0, err)
        yesterday = (today - timedelta(days=1)).strftime("%Y%m%d")
        code, _, err = self.init_wo(wo_key=f"{yesterday}-midnight-lane",
                                    worktree=str(self.wt_two), story="")
        self.assertEqual(code, 0, err)

    # -- database and project --------------------------------------------------

    def test_init_db_idempotent(self):
        code1, _, _ = run(["init-db"])
        code2, _, _ = run(["init-db"])
        self.assertEqual((code1, code2), (0, 0))

    def test_uninitialized_db_fails_cleanly(self):
        os.environ["AGENTROF_HOME"] = str(Path(self.tmp.name) / "fresh")
        code, _, err = run(["resume-info", "--project-key", "shop"])
        self.assertEqual(code, 1)
        self.assertIn("init-db", err)

    def test_fresh_db_schema(self):
        con = self.db()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],
                         pmo_cli.SCHEMA_VERSION)
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in ("work_orders", "work_order_steps", "work_item_deps",
                      "dod_items", "task_attempts", "issue_candidates"):
            self.assertIn(table, tables)
        self.assertNotIn("runs", tables)

    def test_refuses_noncurrent_schema(self):
        con = self.db()
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        code, _, err = run(["init-db"])
        self.assertEqual(code, 1)
        self.assertIn("does not match this CLI", err)

    # -- issue candidates ------------------------------------------------------

    def test_issue_open_mints_global_id_without_project(self):
        code, out, err = run(["issue", "open", "--title", "gh missing",
                              "--kind", "defect", "--evidence", "session A"])
        self.assertEqual(code, 0, err)
        self.assertIn("IC-001", out)
        code, out, _ = run(["issue", "open", "--title", "capture idea",
                            "--kind", "improvement"])
        self.assertIn("IC-002", out)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertIsNone(row["project_id"])
        self.assertIsNone(row["work_order_id"])
        self.assertEqual(row["status"], "candidate")

    def test_issue_id_sequence_is_global_not_project_scoped(self):
        run(["issue", "open", "--title", "a", "--kind", "defect"])
        code, out, err = run(["issue", "open", "--title", "b", "--kind",
                              "improvement", "--project-key", "shop"])
        self.assertEqual(code, 0, err)
        self.assertIn("IC-002", out)  # shared sequence across project-less/bound
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-002'").fetchone()
        project = self.db().execute(
            "SELECT id FROM projects WHERE project_key='shop'").fetchone()
        self.assertEqual(row["project_id"], project["id"])

    def test_issue_open_binds_work_order(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["issue", "open", "--title", "wo defect",
                            "--kind", "defect", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        order = self.db().execute(
            "SELECT id, project_id FROM work_orders WHERE work_order_key='wo1'"
        ).fetchone()
        self.assertEqual(row["work_order_id"], order["id"])
        self.assertEqual(row["project_id"], order["project_id"])

    def test_issue_open_rejects_bad_kind(self):
        code, _, _ = run(["issue", "open", "--title", "x", "--kind", "bug"])
        self.assertEqual(code, 2)

    def test_issue_update_fields_and_dismiss(self):
        run(["issue", "open", "--title", "candidate", "--kind", "defect"])
        code, out, err = run(["issue", "update", "--issue", "IC-001",
                              "--title", "new", "--status", "dismissed"])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertEqual(row["title"], "new")
        self.assertEqual(row["status"], "dismissed")

    def test_issue_update_cannot_set_filed(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        code, _, _ = run(["issue", "update", "--issue", "IC-001",
                          "--status", "filed"])
        self.assertEqual(code, 2)  # filed is set only by 'issue file'

    def test_issue_update_needs_a_field(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        code, _, err = run(["issue", "update", "--issue", "IC-001"])
        self.assertEqual(code, 2)
        self.assertIn("nothing to update", err)

    def test_issue_file_records_url_and_event(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        url = "https://github.com/agentrof/agent-marketplace/issues/9"
        code, _, err = run(["issue", "file", "--issue", "IC-001", "--url", url])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertEqual(row["status"], "filed")
        self.assertEqual(row["issue_url"], url)
        event = self.db().execute(
            "SELECT 1 FROM events WHERE action='issue_candidate_filed'").fetchone()
        self.assertTrue(event)

    def test_issue_file_refuses_double_file(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        run(["issue", "file", "--issue", "IC-001", "--url", "https://a"])
        code, _, err = run(["issue", "file", "--issue", "IC-001",
                            "--url", "https://b"])
        self.assertEqual(code, 2)
        self.assertIn("already filed", err)

    def test_issue_list_filters_by_status_json(self):
        run(["issue", "open", "--title", "a", "--kind", "defect"])
        run(["issue", "open", "--title", "b", "--kind", "improvement"])
        run(["issue", "update", "--issue", "IC-002", "--status", "dismissed"])
        code, out, err = run(["issue", "list", "--status", "candidate", "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["external_id"] for r in rows], ["IC-001"])

    # -- backlog import ------------------------------------------------------

    def test_import_green_and_upsert(self):
        code, out, err = self.import_backlog()
        self.assertEqual(code, 0, err)
        code, _, _ = self.import_backlog()  # idempotent re-import
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "story", "--json"])
        stories = json.loads(out)
        self.assertEqual([s["external_id"] for s in stories], ["WP-01", "WP-02"])

    def test_import_rejects_empty_required_fields(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["dod"] = "  "
        bad["stories"][1]["scope"] = ""
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("WP-01: required field 'dod' is empty", err)
        self.assertIn("WP-02: required field 'scope' is empty", err)

    def test_import_rejects_unknown_epic(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["epic"] = "EP-99"
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("epic 'EP-99' not found", err)

    def test_import_rejects_bad_priority(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["priority"] = "P1"
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("priority tier", err)

    def test_import_materializes_structured_deps(self):
        self.import_backlog()
        code, out, _ = run(["item", "list-deps", "--project-key", "shop", "--json"])
        self.assertEqual(code, 0)
        deps = json.loads(out)
        self.assertEqual(deps, [{
            "item": "WP-02", "depends_on": "WP-01",
            "reason": "editing needs the authenticated session WP-01 ships",
        }])

    def test_import_rejects_dependency_cycle(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][0]["depends_on"] = [{"item": "WP-02", "reason": "loop"}]
        code, _, err = self.import_backlog(data)
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)

    def test_import_syncs_dod_items(self):
        self.import_backlog()
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-01", "--json"])
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "pending" for r in rows))
        # verify one, then re-import with a changed pending statement:
        # the verified row survives, the stale pending row is replaced
        run(["item", "set-dod", "--project-key", "shop",
             "--dod-id", str(rows[0]["id"]), "--status", "verified"])
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][0]["dod_items"] = [
            "A member with a valid token sets a new password and signs in with it.",
            "A reused token is rejected and the event is logged.",
        ]
        self.import_backlog(data)
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-01", "--json"])
        rows = json.loads(out)
        statements = {r["statement"]: r["status"] for r in rows}
        self.assertEqual(statements[
            "A member with a valid token sets a new password and signs in with it."
        ], "verified")
        self.assertIn("A reused token is rejected and the event is logged.",
                      statements)
        self.assertNotIn("An expired token is rejected with the documented message.",
                         statements)

    # -- dependencies and ordering --------------------------------------------

    def test_add_remove_dep_and_cycle_guard(self):
        self.import_backlog()
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "WP-02",
                            "--reason", "closes the loop"])
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)
        self.assertIn("WP-01", err)
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "WP-01"])
        self.assertEqual(code, 1)
        self.assertIn("itself", err)
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "EP-01"])
        self.assertEqual(code, 1)
        self.assertIn("epic", err.lower())
        code, _, _ = run(["item", "remove-dep", "--project-key", "shop",
                          "--item", "WP-02", "--depends-on", "WP-01"])
        self.assertEqual(code, 0)
        code, _, err = run(["item", "remove-dep", "--project-key", "shop",
                            "--item", "WP-02", "--depends-on", "WP-01"])
        self.assertEqual(code, 1)

    def test_item_order_deterministic_with_priority_tiebreak(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"].append({
            "external_id": "WP-03", "epic": "EP-01", "title": "Avatar upload",
            "type": "feature", "priority": "low: cosmetic tail",
            "scope": "s", "excludes": "x",
            "dor": "d", "dod": "d",
        })
        self.import_backlog(data)
        code, out, _ = run(["item", "order", "--project-key", "shop",
                            "--kind", "story", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        # WP-01 (critical) before WP-03 (low); WP-02 only after its dep
        self.assertEqual(result["order"], ["WP-01", "WP-02", "WP-03"])
        self.assertEqual(result["cycles"], [])

    # -- dod items -------------------------------------------------------------

    def test_dod_transitions(self):
        self.import_backlog()
        code, out, _ = run(["item", "add-dod", "--project-key", "shop",
                            "--item", "WP-02",
                            "--statement", "The avatar renders on the profile page."])
        self.assertEqual(code, 0)
        dod_id = out.strip().split()[3]
        code, _, err = run(["item", "set-dod", "--project-key", "shop",
                            "--dod-id", dod_id, "--status", "failed"])
        self.assertEqual(code, 1)
        self.assertIn("failure-reason", err)
        code, _, _ = run(["item", "set-dod", "--project-key", "shop",
                          "--dod-id", dod_id, "--status", "failed",
                          "--failure-reason", "renders a broken image link"])
        self.assertEqual(code, 0)
        code, _, _ = run(["item", "set-dod", "--project-key", "shop",
                          "--dod-id", dod_id, "--status", "verified"])
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-02", "--json"])
        row = json.loads(out)[0]
        self.assertEqual(row["status"], "verified")
        self.assertTrue(row["verified_at"])
        self.assertEqual(row["failure_reason"], "")

    # -- priority --------------------------------------------------------------

    def test_priority_enum_on_update(self):
        self.import_backlog()
        code, _, err = run(["item", "update", "--project-key", "shop",
                            "--external-id", "WP-01", "--priority", "P1"])
        self.assertEqual(code, 1)
        self.assertIn("priority tier", err)
        code, _, _ = run(["item", "update", "--project-key", "shop",
                          "--external-id", "WP-01",
                          "--priority", "medium: demoted after the checkpoint"])
        self.assertEqual(code, 0)

    # -- work-order lifecycle guards -------------------------------------------

    def test_wo_init_snapshots_directory_brief(self):
        """An analysis-space brief snapshots as a whole tree."""
        self.import_backlog()
        space = Path(self.tmp.name) / "erp"
        (space / "rules").mkdir(parents=True)
        (space / "_generated").mkdir()
        (space / "space.md").write_text("# ERP\n", encoding="utf-8")
        (space / "rules" / "core.md").write_text("| BR-ERP-001 |\n", encoding="utf-8")
        (space / "_generated" / "registry.json").write_text("{}", encoding="utf-8")
        order_dir = Path(self.tmp.name) / "orders" / "wo1"
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "wo1", "--request", "build it",
                            "--worktree", str(self.wt_main), "--story", "WP-01",
                            "--order-dir", str(order_dir), "--brief", str(space)])
        self.assertEqual(code, 0, err)
        snap = order_dir / "brief-snapshot"
        self.assertTrue((snap / "space.md").is_file())
        self.assertTrue((snap / "rules" / "core.md").is_file())
        self.assertTrue((snap / "_generated" / "registry.json").is_file())

    def test_wo_init_rejects_file_brief(self):
        self.import_backlog()
        brief = Path(self.tmp.name) / "brief.md"
        brief.write_text("# Brief\n", encoding="utf-8")
        order_dir = Path(self.tmp.name) / "orders" / "wo-file"
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "wo-file", "--request", "build it",
                            "--worktree", str(self.wt_main), "--story", "WP-01",
                            "--order-dir", str(order_dir), "--brief", str(brief)])
        self.assertEqual(code, 1)
        self.assertIn("analysis-space directory", err)
        self.assertFalse(order_dir.exists())

    def test_same_worktree_refused(self):
        self.import_backlog()
        self.assertEqual(self.init_wo()[0], 0)
        code, _, err = self.init_wo(wo_key="wo2", story="WP-02")
        self.assertEqual(code, 1)
        self.assertIn("already holds worktree", err)

    def test_same_story_refused_disjoint_allowed(self):
        self.import_backlog()
        self.assertEqual(self.init_wo()[0], 0)
        code, _, err = self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-01")
        self.assertEqual(code, 1)
        self.assertIn("already claimed", err)
        code, _, err = self.init_wo(wo_key="wo3", worktree=str(self.wt_two), story="WP-02")
        self.assertEqual(code, 0, err)

    def test_story_claim_marks_in_development(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "story", "--status", "in_development",
                            "--json"])
        story = json.loads(out)[0]
        self.assertEqual(story["external_id"], "WP-01")
        # the claimed story points at the work order delivering it
        con = self.db()
        order = con.execute("SELECT id FROM work_orders WHERE"
                            " work_order_key = 'wo1'").fetchone()
        self.assertEqual(story["work_order_id"], order["id"])

    def test_unknown_story_refused(self):
        code, _, err = self.init_wo(story="WP-99")
        self.assertEqual(code, 1)
        self.assertIn("not in the backlog", err)

    def test_transition_guard(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-step", "--work-order-key", "wo1",
                            "--step", "2", "--status", "in_progress"])
        self.assertEqual(code, 1)
        self.assertIn("transition guard", err)

    def test_complete_guard_full_chain(self):
        """Story work orders: steps, findings, coverage and ledger all guard
        completion."""
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("steps not done", err)
        for step in ("0", "1", "2", "3", "4", "5"):
            run(["work-order", "set-step", "--work-order-key", "wo1",
                 "--step", step, "--status", "in_progress"])
            run(["work-order", "set-step", "--work-order-key", "wo1",
                 "--step", step, "--status", "done"])
        run(["finding", "open", "--work-order-key", "wo1", "--source", "review",
             "--severity", "high", "--summary", "broken thing"])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("open findings remain", err)
        run(["finding", "update", "--work-order-key", "wo1", "--finding", "F-001",
             "--status", "fixed", "--round", "1"])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("no coverage rows", err)
        run(["coverage", "import", "--work-order-key", "wo1",
             "--json-file", self.coverage_file()])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("no ledger line", err)
        code, _, err = run(["checkpoint", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 0, err)

    def test_storyless_work_order_completes_without_coverage(self):
        self.import_backlog()
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "atomic1",
                            "--request", "small fix",
                            "--worktree", str(self.wt_main)])
        self.assertEqual(code, 0, err)
        for step in ("0", "1", "2", "3", "4", "5"):
            run(["work-order", "set-step", "--work-order-key", "atomic1",
                 "--step", step, "--status", "in_progress"])
            run(["work-order", "set-step", "--work-order-key", "atomic1",
                 "--step", step, "--status", "done"])
        code, _, err = run(["work-order", "set-status",
                            "--work-order-key", "atomic1",
                            "--status", "complete"])
        self.assertEqual(code, 0, err)

    def test_release_frees_worktree_and_claim(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["work-order", "release", "--work-order-key", "wo1"])
        self.assertEqual(code, 0)
        code, _, err = self.init_wo(wo_key="wo2")
        self.assertEqual(code, 0, err)

    # -- ownership -----------------------------------------------------------

    def test_ownership_snake_case_and_cross_order_overlap(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo1",
                            "--ownership", '{"backend-dev": ["a/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("snake_case", err)
        code, _, _ = run(["work-order", "set-ownership",
                          "--work-order-key", "wo1",
                          "--ownership",
                          '{"backend_developer": ["apps/backend/"]}'])
        self.assertEqual(code, 0)
        self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-02")
        os.chdir(self.wt_two)  # wo2's mutations belong to its own worktree
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo2",
                            "--ownership",
                            '{"frontend_developer": ["apps/backend/app/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("across work orders", err)
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo2",
                            "--ownership",
                            '{"frontend_developer": ["apps/frontend/"]}'])
        self.assertEqual(code, 0, err)

    # -- worktree binding and reactivation (mechanical guards) -----------------

    def test_midflight_verbs_bound_to_worktree(self):
        """Mid-flight mutations are refused from outside the order's claimed
        worktree, naming the owning path; the same call passes from inside."""
        self.import_backlog()
        self.init_wo()  # claimed by wt_main
        os.chdir(self.wt_two)
        for argv in (
            ["work-order", "set-step", "--work-order-key", "wo1",
             "--step", "0", "--status", "done"],
            ["work-order", "record-gate", "--work-order-key", "wo1",
             "--gate", "g", "--decision", "approve"],
            ["finding", "open", "--work-order-key", "wo1",
             "--source", "review", "--severity", "low", "--summary", "x"],
            ["task", "open", "--work-order-key", "wo1",
             "--role", "qa_engineer", "--step", "1", "--title", "t"],
        ):
            code, _, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("belongs to worktree", err)
            self.assertIn(str(self.wt_main.name), err)
        os.chdir(self.wt_main)
        code, _, err = run(["work-order", "set-step", "--work-order-key", "wo1",
                            "--step", "0", "--status", "done"])
        self.assertEqual(code, 0, err)

    def test_release_works_from_anywhere(self):
        self.import_backlog()
        self.init_wo()
        os.chdir(self.wt_two)  # the recovery verb stays unrestricted
        code, _, err = run(["work-order", "release", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)

    def test_reactivation_revalidates_claims(self):
        """set-status back to an active status re-runs the init claim checks:
        a story or worktree taken meanwhile refuses by name."""
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        # another lane claims the same story while wo1 is parked
        self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-01")
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 1)
        self.assertIn("cannot reactivate", err)
        self.assertIn("wo2", err)
        # free the story again: reactivation now succeeds
        os.chdir(self.wt_two)
        run(["work-order", "release", "--work-order-key", "wo2"])
        os.chdir(self.wt_main)
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 0, err)

    def test_reactivation_refuses_taken_worktree(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        # a storyless order grabs the SAME worktree while wo1 is parked
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "squatter",
                            "--request", "atomic fix",
                            "--worktree", str(self.wt_main)])
        self.assertEqual(code, 0, err)
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 1)
        self.assertIn("now holds worktree", err)

    # -- item ready (dispatch surface) -----------------------------------------

    def ready(self):
        code, out, err = run(["item", "ready", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_item_ready_dep_gating(self):
        self.import_backlog()
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], ["WP-01"])
        self.assertEqual(result["blocked"][0]["external_id"], "WP-02")
        blocker = result["blocked"][0]["blocked_by"][0]
        self.assertEqual(blocker["item"], "WP-01")
        self.assertIn("authenticated session", blocker["reason"])
        run(["item", "update", "--project-key", "shop",
             "--external-id", "WP-01", "--status", "done"])
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], ["WP-02"])
        self.assertEqual(result["blocked"], [])

    def test_item_ready_excludes_claimed(self):
        self.import_backlog()
        self.init_wo()  # claims WP-01
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], [])
        claimed = result["claimed"][0]
        self.assertEqual(claimed["external_id"], "WP-01")
        self.assertEqual(claimed["work_order_key"], "wo1")
        self.assertIn("wt-main", claimed["worktree"])

    def test_item_ready_stale_in_development(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        result = self.ready()  # story stayed in_development, claim freed
        self.assertEqual(result["stale_in_development"], ["WP-01"])

    def test_item_ready_orders_by_topo_priority(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][1]["depends_on"] = []
        data["stories"].append({
            "external_id": "WP-03", "epic": "EP-01", "title": "Avatar upload",
            "type": "feature", "priority": "low: cosmetic tail",
            "scope": "s", "excludes": "x",
            "dor": "d", "dod": "d",
        })
        self.import_backlog(data)
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]],
                         ["WP-01", "WP-02", "WP-03"])  # critical, high, low
        self.assertEqual([r["topo_position"] for r in result["ready"]],
                         [1, 2, 3])

    def test_resume_info_carries_ownership_map(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "set-ownership", "--work-order-key", "wo1",
             "--ownership", '{"backend_developer": ["apps/backend/"]}'])
        code, out, _ = run(["resume-info", "--project-key", "shop", "--json"])
        info = json.loads(out)
        self.assertEqual(info["active_work_orders"][0]["ownership"],
                         {"backend_developer": ["apps/backend/"]})

    # -- tasks and attempts ----------------------------------------------------

    def test_task_open_touch_close(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["task", "open", "--work-order-key", "wo1",
                          "--role", "backend_developer", "--step", "2",
                          "--title", "implement slice"])
        self.assertEqual(code, 0)
        code, _, _ = run(["task", "touch", "--project-key", "shop",
                          "--role", "backend_developer", "--phase", "start"])
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "task", "--json"])
        task = json.loads(out)[0]
        self.assertEqual(task["status"], "in_progress")
        self.assertTrue(task["started_at"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop"])
        code, _, _ = run(["task", "close", "--work-order-key", "wo1",
                          "--role", "backend_developer", "--outcome", "done"])
        self.assertEqual(code, 0)
        _, out, _ = run(["item", "list", "--project-key", "shop",
                         "--kind", "task", "--json"])
        task = json.loads(out)[0]
        self.assertEqual(task["status"], "done")
        self.assertTrue(task["finished_at"])

    def test_task_touch_without_open_task_auto_creates(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["task", "touch", "--project-key", "shop",
                            "--role", "qa_engineer", "--phase", "start"])
        self.assertEqual(code, 0)
        self.assertIn("auto-opened", out)

    def test_concurrent_task_starts_commit_without_loss(self):
        self.import_backlog()
        self.init_wo()
        processes = []
        for index in range(12):
            processes.append(subprocess.Popen(
                [sys.executable, str(CLI_PATH), "task", "touch",
                 "--project-key", "shop", "--role", f"worker_{index}",
                 "--phase", "start", "--session-id", f"session-{index}"],
                cwd=self.wt_main,
                env={**os.environ},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))
        for process in processes:
            _, err = process.communicate(timeout=40)
            self.assertEqual(process.returncode, 0, err)
        code, out, err = run([
            "item", "list", "--project-key", "shop", "--kind", "task", "--json"
        ])
        self.assertEqual(code, 0, err)
        tasks = json.loads(out)
        self.assertEqual({task["role"] for task in tasks}, {
            f"worker_{index}" for index in range(12)
        })
        code, _, err = run(["verify"])
        self.assertEqual(code, 0, err)

    def test_attempt_lifecycle(self):
        """Every dispatch is one attempt row: start opens, stop closes with
        cost, a second start supersedes the dangling first."""
        self.import_backlog()
        self.init_wo()
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s1", "--agent", "backend-developer"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s2", "--agent", "backend-developer"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop",
             "--session-id", "s2", "--cost-usd", "1.25"])
        con = self.db()
        attempts = [dict(r) for r in con.execute(
            "SELECT * FROM task_attempts ORDER BY attempt")]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["outcome"], "failed")
        self.assertIn("superseded", attempts[0]["failure_reason"])
        self.assertEqual(attempts[0]["session_id"], "s1")
        self.assertEqual(attempts[1]["outcome"], "done")
        self.assertEqual(attempts[1]["cost_usd"], 1.25)
        self.assertEqual(attempts[1]["agent_name"],
                         "backend-developer")
        actions = []
        for row in con.execute("SELECT action FROM events ORDER BY id"):
            actions.append(row["action"])
        self.assertIn("attempt_started", actions)
        self.assertIn("attempt_finished", actions)

    def test_task_close_closes_running_attempt(self):
        self.import_backlog()
        self.init_wo()
        run(["task", "touch", "--project-key", "shop",
             "--role", "qa_engineer", "--phase", "start", "--session-id", "s1"])
        run(["task", "close", "--work-order-key", "wo1",
             "--role", "qa_engineer", "--outcome", "blocked"])
        con = self.db()
        attempt = con.execute("SELECT * FROM task_attempts").fetchone()
        self.assertEqual(attempt["outcome"], "blocked")
        self.assertTrue(attempt["finished_at"])

    # -- coverage, budgets, ledger --------------------------------------------

    def coverage_file(self):
        path = Path(self.tmp.name) / "coverage.json"
        path.write_text(json.dumps({"rows": [
            {"id": "AC-001", "result": "PASS", "tests": ["test_reset[AC-001]"]},
            {"id": "AC-002", "result": "NO-TEST", "tests": []},
        ]}), encoding="utf-8")
        return str(path)

    def test_coverage_list_reads_story_criteria(self):
        self.import_backlog()
        code, out, err = run(["coverage", "list", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["criterion_id"] for r in rows],
                         ["AC-001", "AC-002"])
        self.assertEqual(rows[0]["story"], "WP-01")
        self.assertEqual(rows[0]["disposition"], "covered")
        self.assertEqual(rows[1]["story"], None)
        self.assertEqual(rows[1]["disposition"], "deferred")
        self.assertEqual(rows[1]["reason"], "Out of v1.")
        code, out, _ = run(["coverage", "list", "--project-key", "shop",
                            "--story", "WP-01", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual([r["criterion_id"] for r in json.loads(out)],
                         ["AC-001"])

    def test_ledger_list_decodes_finding_counts(self):
        self.import_backlog()
        self.init_wo()
        run(["finding", "open", "--work-order-key", "wo1", "--source", "qa",
             "--severity", "low", "--summary", "cosmetic"])
        run(["ledger", "checkpoint", "--work-order-key", "wo1",
             "--escaped-defect"])
        run(["ledger", "checkpoint", "--work-order-key", "wo1"])
        code, out, err = run(["ledger", "list", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["story"], "WP-01")
        self.assertEqual(rows[0]["work_order_key"], "wo1")
        self.assertEqual(rows[0]["finding_counts"], {"qa_low": 1})
        self.assertTrue(rows[0]["escaped_defect"])
        self.assertNotIn("finding_counts_json", rows[0])
        code, out, _ = run(["ledger", "list", "--project-key", "shop",
                            "--tail", "1", "--json"])
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["escaped_defect"])

    def test_coverage_import_and_replace(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["coverage", "import", "--work-order-key", "wo1",
                            "--json-file", self.coverage_file()])
        self.assertEqual(code, 0, err)
        code, _, _ = run(["coverage", "import", "--work-order-key", "wo1",
                          "--json-file", self.coverage_file()])
        self.assertEqual(code, 0)  # re-import replaces, does not duplicate

    def test_budget_and_ledger(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["budget", "set", "--work-order-key", "wo1",
                          "--budget-id", "BR-010", "--verdict", "unverified",
                          "--reason", "load-only budget; no rig in scope"])
        self.assertEqual(code, 0)
        run(["finding", "open", "--work-order-key", "wo1", "--source", "qa",
             "--severity", "low", "--summary", "cosmetic"])
        code, _, _ = run(["ledger", "checkpoint", "--work-order-key", "wo1",
                          "--escaped-defect"])
        self.assertEqual(code, 0)
        code, out, _ = run(["ledger", "list", "--project-key", "shop",
                            "--json"])
        self.assertEqual(code, 0)
        row = json.loads(out)[0]
        self.assertEqual(row["finding_counts"], {"qa_low": 1})
        self.assertTrue(row["escaped_defect"])

    # -- audit trail ----------------------------------------------------------

    def test_every_mutation_writes_an_event(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "set-step", "--work-order-key", "wo1", "--step", "0",
             "--status", "done"])
        run(["work-order", "record-gate", "--work-order-key", "wo1",
             "--gate", "design_gate", "--decision", "approved"])
        run(["work-order", "bump", "--work-order-key", "wo1",
             "--counter", "review"])
        code, out, _ = run(["resume-info", "--project-key", "shop",
                            "--events", "50", "--json"])
        self.assertEqual(code, 0)
        actions = [e["action"] for e in json.loads(out)["recent_events"]]
        for expected in ("backlog_imported", "work_order_initialized",
                         "step_changed", "gate_recorded", "round_bumped"):
            self.assertIn(expected, actions)

    def test_resume_info_reports_work_order_shape(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["resume-info", "--project-key", "shop", "--json"])
        self.assertEqual(code, 0)
        info = json.loads(out)
        self.assertEqual(len(info["active_work_orders"]), 1)
        active = info["active_work_orders"][0]
        self.assertEqual(active["current_step"], "0")
        self.assertIn("WP-01", active["story"])

    def test_work_order_validate(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["work-order", "validate", "--work-order-key", "wo1"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
