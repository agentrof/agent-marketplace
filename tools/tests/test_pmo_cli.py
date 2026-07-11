"""Unit tests for the PMO plugin's central-database CLI."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI_PATH = REPO / "plugins" / "pmo" / "scripts" / "pmo_cli.py"

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
            "type": "feature", "priority": "P1", "dependency": "",
            "scope": "Request, email token, set new password.",
            "excludes": "Two-factor reset.",
            "dor": "Brief BR-001..BR-004 accepted.",
            "dod": "All ACs pass; review and qa green.",
        },
        {
            "external_id": "WP-02", "epic": "EP-01", "title": "Profile editing",
            "type": "feature", "priority": "P2", "dependency": "WP-01",
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
        code, _, err = run(["init-db"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["project", "register", "--key", "shop",
                            "--name", "Shop", "--team", "software-team"])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("AGENTROF_HOME", None)
        else:
            os.environ["AGENTROF_HOME"] = self._old_home
        self.tmp.cleanup()

    def import_backlog(self, data=None):
        path = Path(self.tmp.name) / "backlog.json"
        path.write_text(json.dumps(data or BACKLOG), encoding="utf-8")
        return run(["item", "import", "--project-key", "shop",
                    "--json-file", str(path)])

    def init_run(self, run_key="r1", worktree="/w/main", story="WP-01"):
        argv = ["run", "init", "--project-key", "shop", "--run-key", run_key,
                "--request", "build it", "--worktree", worktree]
        if story:
            argv += ["--story", story]
        return run(argv)

    # -- database and project ------------------------------------------------

    def test_init_db_idempotent(self):
        code1, _, _ = run(["init-db"])
        code2, _, _ = run(["init-db"])
        self.assertEqual((code1, code2), (0, 0))

    def test_uninitialized_db_fails_cleanly(self):
        os.environ["AGENTROF_HOME"] = str(Path(self.tmp.name) / "fresh")
        code, _, err = run(["resume-info", "--project-key", "shop"])
        self.assertEqual(code, 1)
        self.assertIn("init-db", err)

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

    # -- run lifecycle guards ------------------------------------------------

    def test_same_worktree_refused(self):
        self.import_backlog()
        self.assertEqual(self.init_run()[0], 0)
        code, _, err = self.init_run(run_key="r2", story="WP-02")
        self.assertEqual(code, 1)
        self.assertIn("already holds worktree", err)

    def test_same_story_refused_disjoint_allowed(self):
        self.import_backlog()
        self.assertEqual(self.init_run()[0], 0)
        code, _, err = self.init_run(run_key="r2", worktree="/w/two", story="WP-01")
        self.assertEqual(code, 1)
        self.assertIn("already claimed", err)
        code, _, err = self.init_run(run_key="r3", worktree="/w/two", story="WP-02")
        self.assertEqual(code, 0, err)

    def test_story_claim_marks_in_development(self):
        self.import_backlog()
        self.init_run()
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "story", "--status", "in_development",
                            "--json"])
        self.assertEqual(json.loads(out)[0]["external_id"], "WP-01")

    def test_unknown_story_refused(self):
        code, _, err = self.init_run(story="WP-99")
        self.assertEqual(code, 1)
        self.assertIn("not in the backlog", err)

    def test_transition_guard(self):
        self.import_backlog()
        self.init_run()
        code, _, err = run(["run", "set-step", "--run-key", "r1",
                            "--step", "2", "--status", "in_progress"])
        self.assertEqual(code, 1)
        self.assertIn("transition guard", err)

    def test_run_complete_guard_steps_and_findings(self):
        self.import_backlog()
        self.init_run()
        code, _, err = run(["run", "set-status", "--run-key", "r1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("steps not done", err)
        for step in ("0", "1", "2", "3", "4", "5"):
            run(["run", "set-step", "--run-key", "r1", "--step", step,
                 "--status", "in_progress"])
            run(["run", "set-step", "--run-key", "r1", "--step", step,
                 "--status", "done"])
        run(["finding", "open", "--run-key", "r1", "--source", "review",
             "--severity", "high", "--summary", "broken thing"])
        code, _, err = run(["run", "set-status", "--run-key", "r1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("open findings remain", err)
        run(["finding", "update", "--run-key", "r1", "--finding", "F-001",
             "--status", "fixed", "--round", "1"])
        code, _, err = run(["run", "set-status", "--run-key", "r1",
                            "--status", "complete"])
        self.assertEqual(code, 0, err)

    def test_release_frees_worktree_and_claim(self):
        self.import_backlog()
        self.init_run()
        code, _, _ = run(["run", "release", "--run-key", "r1"])
        self.assertEqual(code, 0)
        code, _, err = self.init_run(run_key="r2")
        self.assertEqual(code, 0, err)

    # -- ownership -----------------------------------------------------------

    def test_ownership_snake_case_and_cross_run_overlap(self):
        self.import_backlog()
        self.init_run()
        code, _, err = run(["run", "set-ownership", "--run-key", "r1",
                            "--ownership", '{"backend-dev": ["a/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("snake_case", err)
        code, _, _ = run(["run", "set-ownership", "--run-key", "r1",
                          "--ownership",
                          '{"backend_developer": ["apps/backend/"]}'])
        self.assertEqual(code, 0)
        self.init_run(run_key="r2", worktree="/w/two", story="WP-02")
        code, _, err = run(["run", "set-ownership", "--run-key", "r2",
                            "--ownership",
                            '{"frontend_developer": ["apps/backend/app/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("across runs", err)
        code, _, err = run(["run", "set-ownership", "--run-key", "r2",
                            "--ownership",
                            '{"frontend_developer": ["apps/frontend/"]}'])
        self.assertEqual(code, 0, err)

    # -- tasks ---------------------------------------------------------------

    def test_task_open_touch_close(self):
        self.import_backlog()
        self.init_run()
        code, _, _ = run(["task", "open", "--run-key", "r1",
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
        code, _, _ = run(["task", "close", "--run-key", "r1",
                          "--role", "backend_developer", "--outcome", "done"])
        self.assertEqual(code, 0)
        _, out, _ = run(["item", "list", "--project-key", "shop",
                         "--kind", "task", "--json"])
        task = json.loads(out)[0]
        self.assertEqual(task["status"], "done")
        self.assertTrue(task["finished_at"])

    def test_task_touch_without_open_task_auto_creates(self):
        self.import_backlog()
        self.init_run()
        code, out, _ = run(["task", "touch", "--project-key", "shop",
                            "--role", "qa_engineer", "--phase", "start"])
        self.assertEqual(code, 0)
        self.assertIn("auto-opened", out)

    # -- coverage, budgets, ledger, renders -----------------------------------

    def coverage_file(self):
        path = Path(self.tmp.name) / "coverage.json"
        path.write_text(json.dumps({"rows": [
            {"id": "AC-001", "result": "PASS", "tests": ["test_reset[AC-001]"]},
            {"id": "AC-002", "result": "NO-TEST", "tests": []},
        ]}), encoding="utf-8")
        return str(path)

    def test_coverage_import_and_replace(self):
        self.import_backlog()
        self.init_run()
        code, _, err = run(["coverage", "import", "--run-key", "r1",
                            "--json-file", self.coverage_file()])
        self.assertEqual(code, 0, err)
        code, _, _ = run(["coverage", "import", "--run-key", "r1",
                          "--json-file", self.coverage_file()])
        self.assertEqual(code, 0)  # re-import replaces, does not duplicate

    def test_budget_and_ledger(self):
        self.import_backlog()
        self.init_run()
        code, _, _ = run(["budget", "set", "--run-key", "r1",
                          "--budget-id", "BR-010", "--verdict", "unverified",
                          "--reason", "load-only budget; no rig in scope"])
        self.assertEqual(code, 0)
        run(["finding", "open", "--run-key", "r1", "--source", "qa",
             "--severity", "low", "--summary", "cosmetic"])
        code, _, _ = run(["ledger", "checkpoint", "--run-key", "r1",
                          "--escaped-defect"])
        self.assertEqual(code, 0)
        out_path = Path(self.tmp.name) / "ledger.md"
        code, _, _ = run(["render", "ledger", "--project-key", "shop",
                          "--out", str(out_path)])
        self.assertEqual(code, 0)
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("qa_low: 1", text)
        self.assertIn("YES", text)

    def test_render_backlog_deterministic_and_marked(self):
        self.import_backlog()
        out_path = Path(self.tmp.name) / "backlog.md"
        run(["render", "backlog", "--project-key", "shop", "--out", str(out_path)])
        first = out_path.read_text(encoding="utf-8")
        run(["render", "backlog", "--project-key", "shop", "--out", str(out_path)])
        second = out_path.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertIn("generated by pmo render", first)
        self.assertIn("WP-01 Password reset flow", first)
        self.assertIn("AC-002", first)
        self.assertIn("Which mail provider?", first)

    # -- audit trail ----------------------------------------------------------

    def test_every_mutation_writes_an_event(self):
        self.import_backlog()
        self.init_run()
        run(["run", "set-step", "--run-key", "r1", "--step", "0",
             "--status", "done"])
        run(["run", "record-gate", "--run-key", "r1", "--gate", "design_gate",
             "--decision", "approved"])
        run(["run", "bump", "--run-key", "r1", "--counter", "review"])
        code, out, _ = run(["resume-info", "--project-key", "shop",
                            "--events", "50", "--json"])
        self.assertEqual(code, 0)
        actions = [e["action"] for e in json.loads(out)["recent_events"]]
        for expected in ("backlog_imported", "run_initialized", "step_changed",
                         "gate_recorded", "round_bumped"):
            self.assertIn(expected, actions)

    def test_resume_info_reports_run_shape(self):
        self.import_backlog()
        self.init_run()
        code, out, _ = run(["resume-info", "--project-key", "shop", "--json"])
        self.assertEqual(code, 0)
        info = json.loads(out)
        self.assertEqual(len(info["active_runs"]), 1)
        active = info["active_runs"][0]
        self.assertEqual(active["current_step"], "0")
        self.assertIn("WP-01", active["story"])

    def test_run_validate(self):
        self.import_backlog()
        self.init_run()
        code, _, _ = run(["run", "validate", "--run-key", "r1"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
