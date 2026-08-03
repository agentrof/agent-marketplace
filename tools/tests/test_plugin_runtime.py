"""Plugin runtime tests: hook payload normalization, the DB guard, the
integrity tripwire, CLI-side lifecycle inference, the plugin-root
dispatcher and the dashboard catalog."""

from __future__ import annotations

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PMO_SCRIPTS = REPO / "dist" / "claude" / "project-management-office" / "scripts"
SET_SCRIPTS = REPO / "dist" / "claude" / "software-engineering-team" / "scripts"

sys.path.insert(0, str(PMO_SCRIPTS))

import hook_common  # noqa: E402


def load_vault_hook():
    if str(SET_SCRIPTS) not in sys.path:
        sys.path.append(str(SET_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "agentrof_vault_hook", SET_SCRIPTS / "vault_hook.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
    def test_shared_golden_patch_corpus_stays_in_parity(self):
        corpus = json.loads((
            REPO / "tools" / "tests" / "data" / "hook_payloads.json"
        ).read_text(encoding="utf-8"))
        vault_hook = load_vault_hook()
        for case in corpus["valid"]:
            with self.subTest(case=case["name"]):
                payload = {
                    "tool_name": "apply_patch",
                    "tool_input": {"patch": case["patch"]},
                }
                pmo = hook_common.normalize_payload(payload)
                vault = vault_hook.normalize(payload)
                self.assertEqual(vault["file_targets"], pmo["file_targets"])
                self.assertEqual(
                    [[item["operation"], item["file_path"]]
                     for item in pmo["file_targets"]],
                    case["operations"],
                )
        for patch in corpus["invalid"]:
            with self.subTest(invalid=patch):
                payload = {"tool_name": "apply_patch", "tool_input": {"patch": patch}}
                pmo = hook_common.normalize_payload(payload)
                vault = vault_hook.normalize(payload)
                self.assertEqual(pmo["file_targets"], [])
                self.assertEqual(vault["file_targets"], [])
                self.assertIn("patch_parse_error", pmo)
                self.assertEqual(
                    vault.get("patch_parse_error"), pmo.get("patch_parse_error")
                )

    def test_claude_code_shape_passes_through(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": "/proj",
            "tool_input": {"file_path": "/proj/a.md", "content": "x"},
        })
        self.assertEqual(out["tool_name"], "Write")
        self.assertEqual(out["file_targets"],
                         [{"file_path": "/proj/a.md", "content": "x"}])

    def test_multiedit_maps_to_edit(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
            "cwd": "/proj",
            "tool_input": {"file_path": "/proj/a.md", "old_string": "a",
                           "new_string": "b"},
        })
        self.assertEqual(out["tool_name"], "Edit")
        self.assertEqual(out["file_targets"][0]["new_string"], "b")
        self.assertNotIn("content", out["file_targets"][0])

    def test_codex_apply_patch_expands_every_operation(self):
        patch = """*** Begin Patch
*** Add File: /proj/new.md
+new text
*** Update File: /proj/edit.md
@@
-old text
+newer text
*** Update File: /proj/source.md
*** Move to: /proj/moved.md
@@
-before
+after
*** Delete File: /proj/deleted.md
-gone
*** End Patch"""
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "cwd": "/proj",
            "tool_input": {"patch": patch},
        })
        self.assertEqual(out["tool_name"], "Edit")
        self.assertNotIn("patch_parse_error", out)
        self.assertEqual(
            [(target["operation"], target["file_path"])
             for target in out["file_targets"]],
            [("add", "/proj/new.md"), ("update", "/proj/edit.md"),
             ("update", "/proj/source.md"),
             ("move-target", "/proj/moved.md"),
             ("delete", "/proj/deleted.md")],
        )
        self.assertEqual(out["file_targets"][0]["content"], "new text")
        self.assertEqual(out["file_targets"][1]["old_string"], "old text")
        self.assertEqual(out["file_targets"][1]["new_string"], "newer text")

    def test_codex_apply_patch_parse_failure_is_explicit(self):
        out = hook_common.normalize_payload({
            "tool_name": "apply_patch", "tool_input": {"patch": "not a patch"}
        })
        self.assertEqual(out["file_targets"], [])
        self.assertIn("patch_parse_error", out)


class DbGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENTROF_HOME": str(self.home)}
        run_cli(["init-db"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_db_write_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": self.tmp.name,
            "tool_input": {"file_path": str(self.home / "agentrof.db"),
                           "content": "x"},
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("PMO CLI", err)

    def test_shell_command_naming_db_file_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "cwd": self.tmp.name,
            "tool_input": {"command":
                           f"sqlite3 {self.home}/agentrof.db"
                           " 'DELETE FROM events'"},
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("database", err)

    def test_ordinary_shell_command_passes(self):
        code, _, _ = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "cwd": self.tmp.name,
            "tool_input": {"command": "ls -la"},
        }, self.env)
        self.assertEqual(code, 0)

    def test_apply_patch_db_write_and_unparseable_patch_fail_closed(self):
        db = self.home / "agentrof.db"
        valid = "\n".join([
            "*** Begin Patch", f"*** Update File: {db}", "@@", "-old",
            "+new", "*** End Patch",
        ])
        for patch, expected in ((valid, "PMO CLI"), ("broken", "fails closed")):
            code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
                "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                "cwd": self.tmp.name, "tool_input": {"patch": patch},
            }, self.env)
            self.assertEqual(code, 2)
            self.assertIn(expected, err)


class VaultHookCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"AGENTROF_HOME": str(Path(self.tmp.name) / "agentrof")}

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_emits_hook_sentinel_as_json(self):
        code, out, err = run_script(
            SET_SCRIPTS / "vault_hook.py",
            {"hook_event_name": "SessionStart"}, self.env, ["register"])
        self.assertEqual(code, 0, err)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENTROF_HOOKS_ACTIVE: software-engineering-team", context)

    def test_apply_patch_multifile_vault_violation_is_denied(self):
        project = Path(self.tmp.name) / "project"
        (project / "workspace" / "docs").mkdir(parents=True)
        patch = "\n".join([
            "*** Begin Patch",
            f"*** Add File: {project / 'ordinary.txt'}", "+safe",
            f"*** Add File: {project / 'workspace/docs/a.md'}",
            "+[relative](b.md)", "*** End Patch",
        ])
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": str(project), "tool_input": {"patch": patch},
        }, self.env, ["pre"])
        self.assertEqual(code, 2)
        self.assertIn("vault-absolute wikilink", err)

    def test_unparseable_apply_patch_fails_closed(self):
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": self.tmp.name, "tool_input": {"patch": "broken"},
        }, self.env, ["pre"])
        self.assertEqual(code, 2)
        self.assertIn("fails closed", err)


class TeamPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.project = root / "project"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "workspace").mkdir()
        (self.project / "workspace" / "config.json").write_text(
            json.dumps({"managed_by": "software-engineering-team"}),
            encoding="utf-8",
        )
        self.env = {"AGENTROF_HOME": str(self.home)}

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, tool="Write", **extra):
        payload = {
            "session_id": "session-one",
            "cwd": str(self.project),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "permission_mode": "default",
            "tool_input": {"file_path": str(self.project / "change.txt"),
                           "content": "change"},
        }
        payload.update(extra)
        return payload

    def guard(self, payload):
        return run_script(
            SET_SCRIPTS / "team_guard.py", payload, self.env, ["pre"]
        )

    def mark_ready(self):
        code, out, err = run_script(
            PMO_SCRIPTS / "hook_session_start.py",
            {"session_id": "session-one", "cwd": str(self.project),
             "hook_event_name": "SessionStart", "source": "startup"},
            self.env,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("AGENTROF_PMO_READY", out)

    def test_missing_pmo_state_denies_every_local_mutation_surface(self):
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        for tool in ("Write", "Edit", "MultiEdit", "apply_patch", "Bash",
                     "exec_command", "shell"):
            with self.subTest(tool=tool):
                code, _, err = self.guard(self.payload(tool))
                self.assertEqual(code, 2)
                self.assertIn("did not mark this session ready", err)
                self.assertIn("No files or project state were changed", err)
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    def test_nonmutation_tool_passes_without_pmo(self):
        code, _, err = self.guard(self.payload("Read"))
        self.assertEqual(code, 0, err)

    def test_exact_read_only_plugin_diagnostics_pass_without_pmo(self):
        for tool, key, command in (
            ("Bash", "command", "claude plugin list --json"),
            ("exec_command", "cmd", "codex plugin list --json"),
            ("shell", "command", "codex plugin list --json"),
        ):
            with self.subTest(tool=tool, command=command):
                payload = self.payload(tool)
                payload["tool_input"] = {key: command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 0, err)

    def test_shell_diagnostic_variants_fail_closed_without_pmo(self):
        for command in (
            "codex plugin list --json && touch change.txt",
            "codex plugin list --json > inventory.json",
            "codex plugin list --available --json",
            "AGENTROF_HOME=/tmp/example codex plugin list --json",
            "codex plugin add project-management-office@agent-marketplace",
        ):
            with self.subTest(command=command):
                payload = self.payload("exec_command")
                payload["tool_input"] = {"cmd": command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 2)
                self.assertIn("did not mark this session ready", err)

    def test_ready_session_passes_and_registers_team(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8")
        )
        self.assertIn("software-engineering-team", registry["plugins"])

    def test_plan_mode_and_foreign_team_fail_closed(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload(permission_mode="plan"))
        self.assertEqual(code, 2)
        self.assertIn("Plan mode", err)
        (self.project / "workspace" / "config.json").write_text(
            json.dumps({"managed_by": "another-team"}), encoding="utf-8"
        )
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("another-team", err)

    def test_session_end_revokes_readiness(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 0, err)
        code, _, err = run_script(
            PMO_SCRIPTS / "hook_session_end.py",
            {"session_id": "session-one", "cwd": str(self.project),
             "hook_event_name": "SessionEnd", "reason": "other"},
            self.env,
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("did not mark this session ready", err)


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

    def test_register_path_run(self):
        code, out, err = self.dispatch(
            ["register", "--plugin", "sample-team",
             "--root", str(self.plugin_root)])
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
        run_cli(["sync-launcher"], self.env)
        code, _, err = run_script(
            SET_SCRIPTS / "team_guard.py",
            {"hook_event_name": "SessionStart"},
            self.env, ["register"])
        self.assertEqual(code, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8"))
        self.assertIn("software-engineering-team", registry["plugins"])

    def test_concurrent_registration_keeps_every_plugin(self):
        processes = []
        for index in range(20):
            processes.append(subprocess.Popen(
                [sys.executable, str(PMO_SCRIPTS / "agentrof_run.py"),
                 "register", "--plugin", f"team-{index}",
                 "--root", str(self.plugin_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **self.env},
            ))
        for process in processes:
            _, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(registry["plugins"]), {f"team-{index}" for index in range(20)}
        )

    def test_codex_manifest_version_is_preferred(self):
        (self.plugin_root / ".codex-plugin").mkdir()
        (self.plugin_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team", "version": "2.0.0"}),
            encoding="utf-8")
        code, _, err = self.dispatch(
            ["register", "--plugin", "sample-team", "--root", str(self.plugin_root)])
        self.assertEqual(code, 0, err)
        registry = json.loads((self.home / "plugin_roots.json").read_text())
        self.assertEqual(registry["plugins"]["sample-team"]["version"], "2.0.0")


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
                "schema_version": 1,
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
            self.assertEqual(team["installs"][0]["version"], "1.2.0")
            self.assertEqual(team["installs"][0]["scope"], "local")

    def test_codex_cache_is_discovered_and_deduplicated_by_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "agentrof"
            home.mkdir()
            cache = Path(tmp) / "codex-cache"
            install = cache / "market" / "software-engineering-team" / "9.2.0"
            (install / ".codex-plugin").mkdir(parents=True)
            (install / ".codex-plugin" / "plugin.json").write_text(json.dumps({
                "name": "software-engineering-team", "version": "9.2.0",
                "description": "team", "author": {"name": "Agentrof"},
            }), encoding="utf-8")
            (install / "skills" / "setup").mkdir(parents=True)
            (install / "skills" / "setup" / "SKILL.md").write_text(
                "---\nname: setup\ndescription: Setup.\n---\n", encoding="utf-8")
            env = {**os.environ, "AGENTROF_HOME": str(home),
                   "AGENTROF_PLUGINS_DIR": str(Path(tmp) / "no-claude"),
                   "AGENTROF_CODEX_PLUGINS_DIR": str(cache)}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys,json;"
                 f"sys.path.insert(0,{str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            teams = json.loads(proc.stdout)["teams"]
            self.assertEqual(list(teams), ["software-engineering-team"])
            self.assertEqual(teams["software-engineering-team"]["installs"], [{
                "version": "9.2.0", "scope": "codex", "project_path": "",
                "last_updated": "",
            }])

    def test_claude_custom_skill_surface_is_scanned_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "agentrof"
            home.mkdir()
            plugins = root / "claude-plugins"
            install = root / "install" / "software-engineering-team"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(json.dumps({
                "name": "software-engineering-team", "version": "9.2.0",
                "description": "team", "dependencies": ["project-management-office"],
                "skills": "./skills/",
            }), encoding="utf-8")
            (install / "skills" / "setup").mkdir(parents=True)
            (install / "skills" / "setup" / "SKILL.md").write_text(
                "---\nname: setup\ndescription: Setup.\n"
                "disable-model-invocation: true\n---\n", encoding="utf-8")
            plugins.mkdir()
            (plugins / "installed_plugins.json").write_text(json.dumps({
                "plugins": {"software-engineering-team@agent-marketplace": [{
                    "installPath": str(install), "version": "9.2.0",
                    "scope": "user", "lastUpdated": "2026-08-03T00:00:00Z",
                }]},
            }), encoding="utf-8")
            env = {**os.environ, "AGENTROF_HOME": str(home),
                   "AGENTROF_PLUGINS_DIR": str(plugins),
                   "AGENTROF_CODEX_PLUGINS_DIR": str(root / "no-codex")}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys,json;"
                 f"sys.path.insert(0,{str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            team = json.loads(proc.stdout)["teams"]["software-engineering-team"]
            self.assertEqual([skill["name"] for skill in team["skills"]], ["setup"])


if __name__ == "__main__":
    unittest.main()
