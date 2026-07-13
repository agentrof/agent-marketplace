"""Unit tests for the PMO hook scripts, using payload shapes captured from a
live probe session (SessionStart/SubagentStart/SubagentStop/SessionEnd/
PreToolUse as emitted by the host)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "pmo" / "scripts"


def run_hook(script: str, payload: dict, env: dict, args: list[str] | None = None):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *(args or [])],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_cli(argv: list[str], env: dict):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "pmo_cli.py"), *argv],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


class PmoHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {"AGENTROF_HOME": str(root / "agentrof")}
        self.project_root = root / "proj"
        (self.project_root / "workspace").mkdir(parents=True)
        (self.project_root / "workspace" / "config.json").write_text(
            json.dumps({"managed_by": "software-team", "project_key": "shop"}),
            encoding="utf-8",
        )
        run_cli(["init-db"], self.env)
        run_cli(["project", "register", "--key", "shop", "--team", "software-team"],
                self.env)
        backlog = root / "backlog.json"
        backlog.write_text(json.dumps({
            "epics": [{"external_id": "EP-01", "title": "Core"}],
            "stories": [{
                "external_id": "WP-01", "epic": "EP-01", "title": "Slice one",
                "scope": "s", "excludes": "x", "dor": "d", "dod": "d",
            }],
        }), encoding="utf-8")
        run_cli(["item", "import", "--project-key", "shop",
                 "--json-file", str(backlog)], self.env)
        run_cli(["work-order", "init", "--project-key", "shop",
                 "--work-order-key", "wo1",
                 "--request", "build", "--worktree", str(self.project_root),
                 "--story", "WP-01"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **extra):
        base = {
            "session_id": "s1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(self.project_root),
            "hook_event_name": "",
        }
        base.update(extra)
        return base

    def tasks(self):
        _, out, _ = run_cli(["item", "list", "--project-key", "shop",
                             "--kind", "task", "--json"], self.env)
        return json.loads(out)

    def events(self):
        _, out, _ = run_cli(["resume-info", "--project-key", "shop",
                             "--events", "50", "--json"], self.env)
        return [e["action"] for e in json.loads(out)["recent_events"]]

    def test_subagent_start_records_task(self):
        # agent_type arrives marketplace-namespaced for plugin agents
        code, _, err = run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart",
            agent_id="a1",
            agent_type="software-team:software-team-backend-developer",
        ), self.env, ["start"])
        self.assertEqual(code, 0, err)
        tasks = self.tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["role"], "backend_developer")
        self.assertEqual(tasks[0]["status"], "in_progress")
        self.assertTrue(tasks[0]["started_at"])

    def test_subagent_stop_stamps_finish(self):
        run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart",
            agent_id="a1", agent_type="software-team-qa-engineer",
        ), self.env, ["start"])
        code, _, _ = run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStop",
            agent_id="a1", agent_type="software-team-qa-engineer",
            last_assistant_message="done",
        ), self.env, ["stop"])
        self.assertEqual(code, 0)
        self.assertTrue(self.tasks()[0]["finished_at"])

    def test_non_team_agent_is_ignored(self):
        code, _, _ = run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart",
            agent_id="a2", agent_type="Explore",
        ), self.env, ["start"])
        self.assertEqual(code, 0)
        self.assertEqual(self.tasks(), [])

    def test_unrelated_cwd_is_ignored(self):
        code, _, _ = run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart",
            cwd=self.tmp.name,
            agent_id="a3", agent_type="software-team-backend-developer",
        ), self.env, ["start"])
        self.assertEqual(code, 0)
        self.assertEqual(self.tasks(), [])

    def test_session_start_injects_resume_context(self):
        code, out, err = run_hook("hook_session_start.py", self.payload(
            hook_event_name="SessionStart", source="startup",
        ), self.env)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        context = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("wo1", context)
        self.assertIn("WP-01", context)
        self.assertIn("work order", context)
        launcher = Path(self.env["AGENTROF_HOME"]) / "bin" / "pmo_cli.py"
        self.assertTrue(launcher.is_file())
        dashboard_module = Path(self.env["AGENTROF_HOME"]) / "bin" / "pmo_dashboard.py"
        self.assertTrue(dashboard_module.is_file())
        dashboard_index = Path(self.env["AGENTROF_HOME"]) / "dashboard" / "index.html"
        self.assertTrue(dashboard_index.is_file())

    def test_session_start_quiet_outside_projects(self):
        code, out, _ = run_hook("hook_session_start.py", self.payload(
            hook_event_name="SessionStart", source="startup", cwd=self.tmp.name,
        ), self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_session_end_flags_dangling_work_order(self):
        code, _, _ = run_hook("hook_session_end.py", self.payload(
            hook_event_name="SessionEnd", reason="other",
        ), self.env)
        self.assertEqual(code, 0)
        self.assertIn("session_ended_with_active_work_order", self.events())

    def open_second_lane(self):
        """A second project worktree with its own active work order."""
        lane_root = Path(self.tmp.name) / "lane-b"
        (lane_root / "workspace").mkdir(parents=True)
        (lane_root / "workspace" / "config.json").write_text(
            json.dumps({"managed_by": "software-team", "project_key": "shop"}),
            encoding="utf-8",
        )
        backlog = Path(self.tmp.name) / "backlog-b.json"
        backlog.write_text(json.dumps({
            "epics": [{"external_id": "EP-01", "title": "Core"}],
            "stories": [{
                "external_id": "WP-02", "epic": "EP-01", "title": "Slice two",
                "scope": "s", "excludes": "x", "dor": "d", "dod": "d",
            }],
        }), encoding="utf-8")
        run_cli(["item", "import", "--project-key", "shop",
                 "--json-file", str(backlog)], self.env)
        run_cli(["work-order", "init", "--project-key", "shop",
                 "--work-order-key", "wo-lane-b",
                 "--request", "build", "--worktree", str(lane_root),
                 "--story", "WP-02"], self.env)
        return lane_root

    def test_session_end_ignores_other_worktrees(self):
        """Closing one session flags only ITS worktree's order as dangling;
        healthy parallel lanes stay unflagged."""
        self.open_second_lane()
        code, _, _ = run_hook("hook_session_end.py", self.payload(
            hook_event_name="SessionEnd", reason="other",
        ), self.env)  # cwd = project_root (lane A)
        self.assertEqual(code, 0)
        _, out, _ = run_cli(["resume-info", "--project-key", "shop",
                             "--events", "50", "--json"], self.env)
        dangling = [e for e in json.loads(out)["recent_events"]
                    if e["action"] == "session_ended_with_active_work_order"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0]["payload"].get("current_step"), "0")

    def test_session_start_partitions_lanes(self):
        """Resume context separates this worktree's order from parallel
        lanes elsewhere."""
        lane_root = self.open_second_lane()
        code, out, err = run_hook("hook_session_start.py", self.payload(
            hook_event_name="SessionStart", source="startup",
        ), self.env)  # cwd = project_root (lane A, holds wo1)
        self.assertEqual(code, 0, err)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("THIS worktree", context)
        self.assertIn("wo1", context.split("OTHER worktrees")[0])
        self.assertIn("OTHER worktrees", context)
        self.assertIn("wo-lane-b", context.split("OTHER worktrees")[1])
        self.assertIn("program entry", context)

    def attempts(self):
        import sqlite3
        con = sqlite3.connect(Path(self.env["AGENTROF_HOME"]) / "agentrof.db")
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM task_attempts ORDER BY attempt")]
        con.close()
        return rows

    def test_subagent_lifecycle_records_attempts(self):
        run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart",
            agent_id="a1", agent_type="software-team:software-team-backend-developer",
        ), self.env, ["start"])
        first = self.attempts()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["outcome"], "running")
        self.assertEqual(first[0]["session_id"], "s1")
        self.assertEqual(first[0]["agent_name"], "software-team-backend-developer")
        # a second dispatch before the first stop supersedes the dangling one
        run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStart", session_id="s2",
            agent_id="a2", agent_type="software-team:software-team-backend-developer",
        ), self.env, ["start"])
        run_hook("hook_subagent.py", self.payload(
            hook_event_name="SubagentStop", session_id="s2",
            agent_id="a2", agent_type="software-team:software-team-backend-developer",
        ), self.env, ["stop"])
        attempts = self.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["outcome"], "failed")
        self.assertIn("superseded", attempts[0]["failure_reason"])
        self.assertEqual(attempts[1]["outcome"], "done")
        self.assertTrue(attempts[1]["finished_at"])
        self.assertIn("attempt_started", self.events())
        self.assertIn("attempt_finished", self.events())

    def test_guard_denies_db_writes_and_allows_others(self):
        db = Path(self.env["AGENTROF_HOME"]) / "agentrof.db"
        for target in (db, Path(str(db) + "-wal")):
            code, _, err = run_hook("hook_guard_db.py", self.payload(
                hook_event_name="PreToolUse", tool_name="Write",
                tool_use_id="t1",
                tool_input={"file_path": str(target), "content": "x"},
            ), self.env)
            self.assertEqual(code, 2)
            self.assertIn("PMO CLI", err)
        code, _, _ = run_hook("hook_guard_db.py", self.payload(
            hook_event_name="PreToolUse", tool_name="Write",
            tool_use_id="t2",
            tool_input={"file_path": str(self.project_root / "a.txt"),
                        "content": "x"},
        ), self.env)
        self.assertEqual(code, 0)

    def test_guard_denies_generated_view_edits(self):
        docs = self.project_root / "workspace" / "docs"
        run_cli(["render", "backlog", "--project-key", "shop",
                 "--out", str(docs / "backlog.md")], self.env)
        code, _, err = run_hook("hook_guard_db.py", self.payload(
            hook_event_name="PreToolUse", tool_name="Edit",
            tool_use_id="t3",
            tool_input={"file_path": str(docs / "backlog.md"),
                        "old_string": "planned", "new_string": "done"},
        ), self.env)
        self.assertEqual(code, 2)
        self.assertIn("GENERATED view", err)
        (docs / "notes.md").write_text("# Notes\n", encoding="utf-8")
        code, _, _ = run_hook("hook_guard_db.py", self.payload(
            hook_event_name="PreToolUse", tool_name="Edit",
            tool_use_id="t4",
            tool_input={"file_path": str(docs / "notes.md"),
                        "old_string": "Notes", "new_string": "Team notes"},
        ), self.env)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
