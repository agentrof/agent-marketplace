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
        run_cli(["run", "init", "--project-key", "shop", "--run-key", "r1",
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
        self.assertIn("r1", context)
        self.assertIn("WP-01", context)
        launcher = Path(self.env["AGENTROF_HOME"]) / "bin" / "pmo_cli.py"
        self.assertTrue(launcher.is_file())

    def test_session_start_quiet_outside_projects(self):
        code, out, _ = run_hook("hook_session_start.py", self.payload(
            hook_event_name="SessionStart", source="startup", cwd=self.tmp.name,
        ), self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_session_end_flags_dangling_run(self):
        code, _, _ = run_hook("hook_session_end.py", self.payload(
            hook_event_name="SessionEnd", reason="other",
        ), self.env)
        self.assertEqual(code, 0)
        self.assertIn("session_ended_with_active_run", self.events())

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
