"""Multi-harness runtime tests: payload normalization across the three
hook stdin schemas, the integrity tripwire, CLI-side lifecycle inference,
the plugin-root dispatcher and the harness-neutral dashboard catalog."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PMO_SCRIPTS = REPO / "plugins" / "project-management-office" / "scripts"
SET_SCRIPTS = REPO / "plugins" / "software-engineering-team" / "scripts"

sys.path.insert(0, str(PMO_SCRIPTS))

import hook_common  # noqa: E402


def run_script(script_path: Path, payload: dict | None, env: dict,
               args: list[str] | None = None):
    proc = subprocess.run(
        [sys.executable, str(script_path), *(args or [])],
        input=json.dumps(payload) if payload is not None else "",
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_cli(argv: list[str], env: dict):
    return run_script(PMO_SCRIPTS / "pmo_cli.py", None, env, argv)


class NormalizePayloadTests(unittest.TestCase):
    def test_claude_code_shape_passes_through(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": "/proj",
            "tool_input": {"file_path": "/proj/a.md", "content": "x"},
        })
        self.assertEqual(out["tool_name"], "Write")
        self.assertEqual(out["file_targets"],
                         [{"file_path": "/proj/a.md", "content": "x"}])

    def test_cursor_camelcase_and_workspace_roots(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "preToolUse", "tool_name": "edit_file",
            "workspace_roots": ["/proj"],
            "tool_input": {"file_path": "b.md", "old_string": "a",
                           "new_string": "b"},
        })
        self.assertEqual(out["hook_event_name"], "PreToolUse")
        self.assertEqual(out["tool_name"], "Edit")
        self.assertEqual(out["cwd"], "/proj")
        self.assertEqual(out["file_targets"][0]["new_string"], "b")
        self.assertNotIn("content", out["file_targets"][0])

    def test_cursor_shell_and_file_edit_events_imply_tools(self):
        shell = hook_common.normalize_payload({
            "hook_event_name": "beforeShellExecution",
            "command": "rm -rf x", "cwd": "/proj",
        })
        self.assertEqual(shell["tool_name"], "Bash")
        self.assertEqual(shell["tool_input"]["command"], "rm -rf x")
        edited = hook_common.normalize_payload({
            "hook_event_name": "afterFileEdit",
            "file_path": "/proj/workspace/docs/n.md", "cwd": "/proj",
        })
        self.assertEqual(edited["tool_name"], "Edit")
        self.assertEqual(edited["file_targets"][0]["file_path"],
                         "/proj/workspace/docs/n.md")

    def test_codex_apply_patch_envelope(self):
        patch = ("*** Begin Patch\n"
                 "*** Update File: workspace/docs/note.md\n"
                 "+added line one\n"
                 "-removed line\n"
                 "+added line two\n"
                 "*** Add File: workspace/docs/new.md\n"
                 "+fresh content\n"
                 "*** End Patch\n")
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": "/proj", "tool_input": {"patch": patch},
        })
        self.assertEqual(out["tool_name"], "Write")
        self.assertEqual(len(out["file_targets"]), 2)
        self.assertEqual(out["file_targets"][0]["file_path"],
                         "workspace/docs/note.md")
        self.assertIn("added line one", out["file_targets"][0]["content"])
        self.assertNotIn("removed line", out["file_targets"][0]["content"])

    def test_harness_detection_from_stdin_field(self):
        self.assertEqual(
            hook_common.detect_harness({"cursor_version": "2.5.1"}),
            "cursor")


class GuardAcrossHarnessesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENTROF_HOME": str(self.home)}
        run_cli(["init-db"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cursor_payload_direct_db_write_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "preToolUse", "tool_name": "write",
            "workspace_roots": [self.tmp.name],
            "tool_input": {"file_path": str(self.home / "agentrof.db"),
                           "content": "x"},
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("PMO CLI", err)

    def test_shell_command_naming_db_file_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "beforeShellExecution",
            "command": f"sqlite3 {self.home}/agentrof.db 'DELETE FROM events'",
            "cwd": self.tmp.name,
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("database", err)

    def test_ordinary_shell_command_passes(self):
        code, _, _ = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "beforeShellExecution",
            "command": "ls -la", "cwd": self.tmp.name,
        }, self.env)
        self.assertEqual(code, 0)


class IntegrityTripwireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENTROF_HOME": str(self.home)}
        run_cli(["init-db"], self.env)
        run_cli(["project", "register", "--key", "shop"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verify_clean_after_cli_mutations(self):
        code, out, _ = run_cli(["verify"], self.env)
        self.assertEqual(code, 0, out)

    def test_foreign_write_detected_by_verify_and_wo_validate(self):
        con = sqlite3.connect(self.home / "agentrof.db")
        con.execute("UPDATE projects SET name = 'tampered'")
        con.commit()
        con.close()
        code, _, err = run_cli(["verify"], self.env)
        self.assertEqual(code, 1)
        self.assertIn("fingerprint", err)
        # the next sanctioned mutation re-stamps; verify goes green again
        run_cli(["project", "register", "--key", "other"], self.env)
        code, _, _ = run_cli(["verify"], self.env)
        self.assertEqual(code, 0)

    def test_verify_json_shape(self):
        code, out, _ = run_cli(["verify", "--json"], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "problem": ""})

    def test_ensure_bootstraps_and_reports(self):
        code, out, _ = run_cli(["ensure"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("ensure complete", out)
        self.assertTrue((self.home / "bin" / "pmo_cli.py").is_file())
        self.assertTrue((self.home / "bin" / "agentrof_run.py").is_file())


class LifecycleInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.env = {"AGENTROF_HOME": str(self.home)}
        self.project_root = root / "proj"
        (self.project_root / "workspace").mkdir(parents=True)
        (self.project_root / "workspace" / "config.json").write_text(
            json.dumps({"project_key": "shop"}), encoding="utf-8")
        run_cli(["init-db"], self.env)
        run_cli(["project", "register", "--key", "shop"], self.env)
        backlog = root / "backlog.json"
        backlog.write_text(json.dumps({
            "epics": [{"external_id": "EP-01", "title": "Core"}],
            "stories": [{
                "external_id": "WP-01", "epic": "EP-01", "title": "Slice",
                "scope": "s", "excludes": "x", "dor": "d", "dod": "d",
            }],
        }), encoding="utf-8")
        run_cli(["item", "import", "--project-key", "shop",
                 "--json-file", str(backlog)], self.env)
        run_cli(["work-order", "init", "--project-key", "shop",
                 "--work-order-key", "wo1", "--request", "build",
                 "--worktree", str(self.project_root),
                 "--story", "WP-01"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def cli_in_project(self, argv):
        proc = subprocess.run(
            [sys.executable, str(PMO_SCRIPTS / "pmo_cli.py"), *argv],
            capture_output=True, text=True, cwd=str(self.project_root),
            env={**os.environ, **self.env},
        )
        return proc.returncode, proc.stdout, proc.stderr

    def attempts(self):
        con = sqlite3.connect(self.home / "agentrof.db")
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM task_attempts ORDER BY id")]
        con.close()
        return rows

    def test_task_close_synthesizes_attempt_without_hooks(self):
        code, out, err = self.cli_in_project(
            ["task", "open", "--work-order-key", "wo1",
             "--role", "backend_developer", "--step", "2",
             "--title", "implement"])
        self.assertEqual(code, 0, err)
        code, _, err = self.cli_in_project(
            ["task", "close", "--work-order-key", "wo1",
             "--role", "backend_developer", "--outcome", "done"])
        self.assertEqual(code, 0, err)
        rows = self.attempts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cli_inferred")
        self.assertEqual(rows[0]["outcome"], "done")
        self.assertTrue(rows[0]["finished_at"])

    def test_session_reconcile_appends_once(self):
        code, out, _ = run_cli(
            ["session-reconcile", "--project-key", "shop",
             "--worktree", str(self.project_root)], self.env)
        self.assertEqual(code, 0)
        self.assertIn("appended 1", out)
        code, out, _ = run_cli(
            ["session-reconcile", "--project-key", "shop",
             "--worktree", str(self.project_root)], self.env)
        self.assertEqual(code, 0)
        self.assertIn("appended 0", out, "dedup on the newest marker event")


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENTROF_HOME": str(self.home)}
        self.plugin_root = Path(self.tmp.name) / "install" / "sample-team"
        (self.plugin_root / ".claude-plugin").mkdir(parents=True)
        (self.plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team", "version": "1.0.0"}),
            encoding="utf-8")
        (self.plugin_root / "scripts").mkdir()
        (self.plugin_root / "scripts" / "hello.py").write_text(
            "import sys\nprint('hello', sys.argv[1])\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def dispatch(self, argv):
        return run_script(PMO_SCRIPTS / "agentrof_run.py", None, self.env,
                          argv)

    def test_register_path_run_harness(self):
        code, out, err = self.dispatch(
            ["register", "--plugin", "sample-team",
             "--root", str(self.plugin_root), "--harness", "codex"])
        self.assertEqual(code, 0, err)
        code, out, _ = self.dispatch(["path", "sample-team", "scripts/hello.py"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.strip(),
            str((self.plugin_root / "scripts" / "hello.py").resolve()))
        code, out, _ = self.dispatch(
            ["run", "sample-team", "scripts/hello.py", "world"])
        self.assertEqual(code, 0)
        self.assertIn("hello world", out)
        code, out, _ = self.dispatch(["harness"])
        self.assertEqual(out.strip(), "codex")

    def test_unregistered_plugin_names_the_remedy(self):
        code, _, err = self.dispatch(["run", "ghost-team", "scripts/x.py"])
        self.assertEqual(code, 1)
        self.assertIn("setup entry", err)

    def test_missing_root_after_update_errors(self):
        self.dispatch(["register", "--plugin", "sample-team",
                       "--root", str(self.plugin_root)])
        import shutil
        shutil.rmtree(self.plugin_root)
        code, _, err = self.dispatch(["path", "sample-team", "scripts/hello.py"])
        self.assertEqual(code, 1)
        self.assertIn("setup entry", err)

    def test_hook_registration_feeds_dispatcher(self):
        code, _, err = run_script(
            SET_SCRIPTS / "vault_hook.py",
            {"hook_event_name": "sessionStart", "cursor_version": "2.5"},
            self.env, ["register"])
        self.assertEqual(code, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8"))
        self.assertIn("software-engineering-team", registry["plugins"])
        self.assertEqual(registry["harness"], "cursor")


class SyncCodexAgentsTests(unittest.TestCase):
    def test_sync_into_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex"
            env = {"CODEX_HOME": str(codex_home)}
            source = SET_SCRIPTS.parent / "codex" / "agents"
            code, out, err = run_script(
                SET_SCRIPTS / "sync_codex_agents.py", None, env)
            if not source.is_dir():
                self.assertEqual(code, 1)
                self.assertIn("codex/agents", err)
                return
            self.assertEqual(code, 0, err)
            synced = sorted((codex_home / "agents").glob("*.toml"))
            self.assertEqual(len(synced), len(list(source.glob("*.toml"))))
            code2, out2, _ = run_script(
                SET_SCRIPTS / "sync_codex_agents.py", None, env)
            self.assertEqual(code2, 0)
            self.assertIn("0 synced", out2, "second run must be a no-op")


class DashboardCatalogTests(unittest.TestCase):
    def test_plugin_roots_registry_feeds_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "agentrof"
            home.mkdir(parents=True)
            install = Path(tmp) / "cache" / "project-management-office"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "project-management-office",
                            "version": "1.2.0",
                            "description": "backbone"}), encoding="utf-8")
            (home / "plugin_roots.json").write_text(json.dumps({
                "schema_version": 1, "harness": "codex",
                "plugins": {"project-management-office": {
                    "root": str(install), "version": "1.2.0",
                    "registered_at": "2026-07-18T00:00:00+00:00"}},
            }), encoding="utf-8")
            env = {**os.environ, "AGENTROF_HOME": str(home),
                   "AGENTROF_PLUGINS_DIR": str(Path(tmp) / "no-claude")}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys, json;"
                 f"sys.path.insert(0, {str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            catalog = json.loads(proc.stdout)
            team = catalog["teams"]["project-management-office"]
            self.assertEqual(team["kind"], "backbone")
            self.assertEqual(team["installs"][0]["scope"], "harness:codex")


if __name__ == "__main__":
    unittest.main()
