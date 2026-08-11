"""Cross-host marketplace upgrade lifecycle and fail-closed safety cases."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
PMO = REPO / "dist" / "claude" / "project-management-office"
TEAM = "software-engineering-team"


def load_upgrade_core():
    path = PMO / "scripts" / "upgrade_core.py"
    spec = importlib.util.spec_from_file_location("upgrade_core_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UPGRADE = load_upgrade_core()


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None):
    return subprocess.run(
        command, cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=True, text=True, check=False,
    )


def content_fingerprint(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    tables = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name != 'meta' ORDER BY name"
    )]
    for table in tables:
        digest.update(table.encode())
        for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def stamp_integrity(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES ('fingerprint', ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (content_fingerprint(con),),
    )


class UpgradeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.project = root / "project"
        self.project.mkdir()
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "upgrade@example.test"],
            ["git", "config", "user.name", "Upgrade Test"],
        ):
            result = run(command, cwd=self.project)
            self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.project / "workspace"
        workspace.mkdir()
        (workspace / "config.json").write_text(json.dumps({
            "managed_by": TEAM + " plugin; change only through the configure entry",
            "project_key": "shop",
            "custom_user_key": "preserve-me",
        }, indent=2) + "\n", encoding="utf-8")
        (self.project / "user-code.txt").write_text("user-owned\n", encoding="utf-8")
        (self.project / ".gitignore").write_text(
            "custom-user-rule/\nworkspace/work-orders/\n"
            "workspace/planning/\nworkspace/experience-design-work/\n"
            "workspace/design-system-work/\n",
            encoding="utf-8",
        )
        (workspace / "planning").mkdir()
        (workspace / "planning" / "baseline.json").write_text(
            '{"mode":"baseline"}\n', encoding="utf-8"
        )
        legacy_order = workspace / "work-orders" / "legacy-order"
        legacy_order.mkdir(parents=True)
        (legacy_order / "config.snapshot.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (workspace / "experience-design-work").mkdir()
        (workspace / "design-system-work").mkdir()
        run(["git", "add", "."], cwd=self.project)
        result = run(["git", "commit", "-qm", "baseline"], cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.cli("init-db")
        self.cli("project", "register", "--key", "shop", "--team", TEAM)
        self.make_schema_three()
        self.write_dual_host_registry()
        self.cli("sync-launcher", "--force")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args: str, check: bool = True):
        result = run(
            [sys.executable, str(PMO / "scripts" / "pmo_cli.py"), *args],
            env=self.env,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def make_schema_three(self):
        database = self.home / "pmo.db"
        con = sqlite3.connect(database)
        triggers = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
            " AND name LIKE 'agent_marketplace_writer_%'"
        )]
        for trigger in triggers:
            con.execute(f"DROP TRIGGER {trigger}")
        con.execute("DROP INDEX IF EXISTS idx_projects_uuid")
        con.execute("DROP TABLE IF EXISTS schema_migrations")
        con.execute("ALTER TABLE projects DROP COLUMN repository_fingerprint")
        con.execute("ALTER TABLE projects DROP COLUMN project_uuid")
        con.execute("DELETE FROM meta WHERE key='writer_epoch'")
        con.execute("PRAGMA user_version=3")
        stamp_integrity(con)
        con.commit()
        con.close()

    def write_dual_host_registry(self):
        plugins = {}
        for component in ("project-management-office", TEAM):
            hosts = {}
            for host in ("claude", "codex"):
                package = REPO / "dist" / host / component
                manifest = json.loads((
                    package / f".{host}-plugin" / "plugin.json"
                ).read_text(encoding="utf-8"))
                hosts[host] = {
                    "root": str(package),
                    "version": manifest["version"],
                    "registered_at": "",
                }
            plugins[component] = {"hosts": hosts}
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "plugin_roots.json").write_text(json.dumps({
            "schema_version": 2, "plugins": plugins,
        }, indent=2) + "\n", encoding="utf-8")

    def registry(self):
        return json.loads((self.home / "plugin_roots.json").read_text(
            encoding="utf-8"
        ))

    def save_registry(self, value):
        (self.home / "plugin_roots.json").write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    def copy_package(self, host: str, component: str) -> Path:
        target = Path(self.tmp.name) / "packages" / host / component
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / "dist" / host / component, target)
        return target

    def rewrite_provenance(self, root: Path, host: str, component: str, version: str):
        files = {}
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            if path.name == ".agent-marketplace-package.json" or "__pycache__" in path.parts:
                continue
            files[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        (root / ".agent-marketplace-package.json").write_text(json.dumps({
            "schema_version": 1,
            "component": component,
            "host": host,
            "version": version,
            "files": files,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def complete_upgrade_and_commit(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        UPGRADE.apply(self.home, self.home / "pmo.db", 6, plan["plan_id"])
        run(["git", "add", "."], cwd=self.project)
        committed = run([
            "git", "commit", "-qm", "chore: apply Agent Marketplace upgrade"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        return plan

    def install_team_version(self, version: str):
        registry = self.registry()
        for host in ("claude", "codex"):
            package = self.copy_package(host, TEAM)
            manifest_path = package / f".{host}-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = version
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            self.rewrite_provenance(package, host, TEAM, version)
            registry["plugins"][TEAM]["hosts"][host].update({
                "root": str(package), "version": version,
            })
        self.save_registry(registry)

    def status(self):
        result = self.cli(
            "upgrade", "status", "--project-root", str(self.project), "--json",
            check=False,
        )
        return result, json.loads(result.stdout)

    def reset_to_current_database(self):
        (self.home / "pmo.db").unlink()
        self.cli("init-db")
        self.cli("project", "register", "--key", "shop", "--team", TEAM)
        self.cli("project", "register", "--key", "other", "--team", TEAM)

    def insert_other_project_activity(self, activity: str):
        database = self.home / "pmo.db"
        con = sqlite3.connect(database)
        con.create_function(
            "agent_marketplace_writer_epoch", 0, lambda: 1, deterministic=True
        )
        project_id = con.execute(
            "SELECT id FROM projects WHERE project_key='other'"
        ).fetchone()[0]
        stamp = "2026-01-01T00:00:00+00:00"
        if activity == "work_order":
            con.execute(
                "INSERT INTO work_orders"
                " (project_id, work_order_key, request, status, worktree_path,"
                " created_at, updated_at) VALUES (?, 'other-wo', 'build',"
                " 'running', '/other', ?, ?)",
                (project_id, stamp, stamp),
            )
        elif activity == "task_attempt":
            task_id = con.execute(
                "INSERT INTO work_items"
                " (project_id, kind, external_id, title, status, created_at,"
                " updated_at) VALUES (?, 'task', 'OTHER-TASK', 'Task', 'todo',"
                " ?, ?) RETURNING id",
                (project_id, stamp, stamp),
            ).fetchone()[0]
            con.execute(
                "INSERT INTO task_attempts"
                " (task_id, attempt, started_at, outcome)"
                " VALUES (?, 1, ?, 'running')",
                (task_id, stamp),
            )
        elif activity == "experience_run":
            con.execute(
                "INSERT INTO experience_runs"
                " (project_id, run_key, program_key, status, created_at)"
                " VALUES (?, 'OTHER-EXR', 'OTHER-PROGRAM', 'active', ?)",
                (project_id, stamp),
            )
        elif activity == "backlog_plan":
            con.execute(
                "INSERT INTO backlog_plans"
                " (project_id, plan_key, program_key, mode, status, plan_json,"
                " draft_hash, created_at, updated_at) VALUES"
                " (?, 'other-plan', 'OTHER-PROGRAM', 'feature', 'draft', '{}',"
                " 'sha256:test', ?, ?)",
                (project_id, stamp, stamp),
            )
        else:
            self.fail(f"unknown activity fixture: {activity}")
        stamp_integrity(con)
        con.commit()
        con.close()

    def team_guard(self, tool_name: str, tool_input: dict):
        return subprocess.run(
            [sys.executable, str(
                REPO / "dist" / "claude" / TEAM / "scripts" / "team_guard.py"
            ), "pre"],
            input=json.dumps({
                "session_id": "upgrade-session",
                "cwd": str(self.project),
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "permission_mode": "default",
                "tool_input": tool_input,
            }),
            env={**os.environ, **self.env}, capture_output=True, text=True,
            check=False,
        )

    def test_schema_data_and_dual_host_project_upgrade(self):
        result, status = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status["status"], "AGENT_MARKETPLACE_UPGRADE_REQUIRED_READY")
        self.assertIn("DATABASE_SCHEMA:3->6", status["reasons"])
        self.assertIn("PROJECT_CONTRACT:unversioned->5", status["reasons"])

        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        )
        plan = json.loads(planned.stdout)
        self.assertEqual(plan["status"], "AGENT_MARKETPLACE_UPGRADE_APPLY_READY")
        self.assertIn("workspace/config.json", plan["project_files"])
        self.assertIn("CLAUDE.md", plan["project_files"])
        self.assertIn("AGENTS.md", plan["project_files"])
        self.assertIn("database", plan["backup_policy"])

        applied = self.cli("upgrade", "apply", "--plan-id", plan["plan_id"])
        result = json.loads(applied.stdout)
        self.assertEqual(
            result["status"], "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(
            (self.project / "user-code.txt").read_text(encoding="utf-8"),
            "user-owned\n",
        )
        config = json.loads((
            self.project / "workspace" / "config.json"
        ).read_text(encoding="utf-8"))
        self.assertNotIn("team_id", config)
        self.assertEqual(config["project_origin"], "unclassified")
        self.assertEqual(config["custom_user_key"], "preserve-me")
        self.assertNotIn("managed_by", config)
        ignore = (self.project / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("custom-user-rule/", ignore)
        for legacy_rule in (
            "workspace/work-orders/", "workspace/planning/",
            "workspace/design-system-work/",
            "workspace/experience-design-work/",
        ):
            self.assertNotIn(legacy_rule, ignore)
        self.assertIn("agent-marketplace:software-engineering-team:gitignore:start", ignore)
        for rule in ("/.agentrof/", "/.claude/", "/.codex/"):
            self.assertIn(rule, ignore)
        runtime = (self.project / ".agentrof" / "agent-marketplace"
                   / ".runtime")
        self.assertTrue((runtime / "plan" / "baseline.json").is_file())
        self.assertTrue((runtime / "work-orders" / "legacy-order"
                         / "config.snapshot.json").is_file())
        self.assertFalse((self.project / "workspace" / "planning").exists())
        self.assertFalse((self.project / "workspace" / "work-orders").exists())
        self.assertFalse((self.project / "workspace"
                          / "experience-design-work").exists())
        self.assertFalse((self.project / "workspace"
                          / "design-system-work").exists())
        state = config["agent_marketplace"]
        self.assertEqual(state["contract_version"], 5)
        self.assertEqual(state["team_id"], TEAM)
        self.assertEqual(
            state["contract_sha256"], UPGRADE.contract_sha256(state)
        )
        self.assertEqual(state["vault"]["policy_version"], 5)
        self.assertEqual(set(state["components"]), {
            "project-management-office", TEAM,
        })
        self.assertTrue(any(key.startswith("claude:")
                            for key in state["managed_surfaces"]))
        self.assertTrue(any(key.startswith("codex:")
                            for key in state["managed_surfaces"]))

        con = sqlite3.connect(self.home / "pmo.db")
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 6)
        migrations = con.execute(
            "SELECT migration_id, from_version, to_version"
            " FROM schema_migrations ORDER BY from_version"
        ).fetchall()
        self.assertEqual(migrations, [
            ("project-management-office.database.3-4", 3, 4),
            ("project-management-office.database.4-5", 4, 5),
            ("project-management-office.database.5-6", 5, 6),
        ])
        identity = con.execute(
            "SELECT project_uuid, repository_fingerprint FROM projects"
            " WHERE project_key='shop'"
        ).fetchone()
        self.assertEqual(identity[0], state["project_id"])
        self.assertEqual(identity[1], state["repository_fingerprint"])
        with self.assertRaisesRegex(sqlite3.OperationalError,
                                    "agent_marketplace_writer_epoch"):
            con.execute("UPDATE projects SET name='stale-writer'")
        con.close()

        pending_result, pending = self.status()
        self.assertEqual(pending_result.returncode, 0, pending_result.stderr)
        self.assertEqual(pending["status"], "AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING")
        denied = self.team_guard("Write", {
            "file_path": str(self.project / "normal-work.txt"), "content": "x",
        })
        self.assertEqual(denied.returncode, 2)
        self.assertIn("AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING", denied.stderr)
        admitted = self.team_guard("Bash", {
            "command": "git add -- " + " ".join(plan["project_files"]),
        })
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        commit_command = "git commit -m 'chore: apply Agent Marketplace upgrade'"
        denied_commit = self.team_guard("Bash", {"command": commit_command})
        self.assertEqual(denied_commit.returncode, 2)
        run(["git", "add", "--", plan["project_files"][0]], cwd=self.project)
        denied_partial = self.team_guard("Bash", {"command": commit_command})
        self.assertEqual(denied_partial.returncode, 2)
        staged_all = run(
            ["git", "add", "--", *plan["project_files"][1:]],
            cwd=self.project,
        )
        self.assertEqual(staged_all.returncode, 0, staged_all.stderr)
        admitted_commit = self.team_guard("Bash", {"command": commit_command})
        self.assertEqual(
            admitted_commit.returncode, 0,
            admitted_commit.stderr + "\nplanned=" + repr(plan["project_files"])
            + "\nstaged=" + run(
                ["git", "diff", "--cached", "--name-only"], cwd=self.project
            ).stdout + "\nunstaged=" + run(
                ["git", "diff", "--name-only"], cwd=self.project
            ).stdout,
        )
        committed = run([
            "git", "commit", "-qm", "chore: apply Agent Marketplace upgrade"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        current_result, current = self.status()
        self.assertEqual(current_result.returncode, 0, current_result.stderr)
        self.assertEqual(current["status"], "AGENT_MARKETPLACE_CURRENT")

    def test_stale_plan_is_rejected_without_project_mutation(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        before = (self.project / "workspace" / "config.json").read_bytes()
        (self.project / "outside.txt").write_text("new\n", encoding="utf-8")
        run(["git", "add", "outside.txt"], cwd=self.project)
        run(["git", "commit", "-qm", "move-head"], cwd=self.project)
        result = self.cli(
            "upgrade", "apply", "--plan-id", plan["plan_id"], check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("fingerprint changed", result.stderr)
        self.assertEqual(
            (self.project / "workspace" / "config.json").read_bytes(), before
        )
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())

    def test_database_content_change_invalidates_the_plan(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        con = sqlite3.connect(self.home / "pmo.db")
        con.execute("UPDATE projects SET name='changed-after-plan' WHERE project_key='shop'")
        stamp_integrity(con)
        con.commit()
        con.close()

        result = self.cli(
            "upgrade", "apply", "--plan-id", plan["plan_id"], check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("fingerprint changed", result.stderr)
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())

    def test_unmanaged_instruction_requires_choice_without_creating_plan(self):
        (self.project / "CLAUDE.md").write_text(
            "# User instructions\n\nNever replace this.\n", encoding="utf-8"
        )
        run(["git", "add", "CLAUDE.md"], cwd=self.project)
        run(["git", "commit", "-qm", "user-instructions"], cwd=self.project)
        before = (self.project / "CLAUDE.md").read_bytes()
        result = self.cli(
            "upgrade", "plan", "--project-root", str(self.project), check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["status"], "AGENT_MARKETPLACE_UPGRADE_CHOICE_REQUIRED"
        )
        self.assertEqual(payload["plan_id"], "")
        self.assertEqual(
            [request["id"] for request in payload["choice_requests"]],
            ["claude.root.claude"],
        )
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), before)
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())

        aborted = self.cli(
            "upgrade", "plan", "--project-root", str(self.project),
            "--choice", "claude.root.claude=abort",
        )
        self.assertEqual(
            json.loads(aborted.stdout)["status"],
            "AGENT_MARKETPLACE_UPGRADE_CHOICE_ABORTED",
        )
        self.assertFalse((self.home / "upgrades" / "plans").exists())

        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project),
            "--choice", "claude.root.claude=preserve",
        )
        plan = json.loads(planned.stdout)
        self.assertEqual(
            plan["status"], "AGENT_MARKETPLACE_UPGRADE_APPLY_READY"
        )
        self.assertEqual(
            plan["resolved_choices"],
            {"claude.root.claude": "preserve"},
        )
        self.assertIn("CLAUDE.user.md", plan["project_files"])

    def test_unsafe_legacy_workspace_blocks_before_project_mutation(self):
        (self.project / "workspace").rename(self.project / "unsafe workspace")
        run(["git", "add", "-A"], cwd=self.project)
        committed = run([
            "git", "commit", "-qm", "move legacy workspace"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        result, status = self.status()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(
            status["status"], "AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED"
        )
        self.assertIn(
            "UNSAFE_WORKSPACE_NAME:unsafe workspace", status["blockers"]
        )
        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project), check=False
        )
        self.assertEqual(planned.returncode, 1)
        self.assertIn("UNSAFE_WORKSPACE_NAME", planned.stderr)
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        self.assertEqual(after, before)

    def test_dirty_checkout_after_plan_is_rejected(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        (self.project / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        result = self.cli(
            "upgrade", "apply", "--plan-id", plan["plan_id"], check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no longer apply-ready", result.stderr)
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())

    def test_session_records_do_not_block_status_or_plan(self):
        sessions = self.home / "sessions"
        sessions.mkdir()
        for name, ready in (("stale-session", True), ("upgrade-session", False)):
            (sessions / f"{name}.json").write_text(json.dumps({
                "session_id": name,
                "pmo_ready": ready,
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }), encoding="utf-8")
        result, status = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("blocking_sessions", status)
        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        )
        self.assertEqual(
            json.loads(planned.stdout)["status"],
            "AGENT_MARKETPLACE_UPGRADE_APPLY_READY",
        )
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())

    def assert_other_project_activity_blocks(self, activity: str, blocker: str):
        self.reset_to_current_database()
        self.insert_other_project_activity(activity)
        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertIn(blocker, status["blockers"])

    def test_other_project_work_order_blocks_project_only_upgrade(self):
        self.assert_other_project_activity_blocks(
            "work_order", "ACTIVE_WORK_ORDER:other-wo"
        )

    def test_other_project_task_attempt_blocks_project_only_upgrade(self):
        self.assert_other_project_activity_blocks(
            "task_attempt", "RUNNING_TASK_ATTEMPTS:1"
        )

    def test_other_project_experience_run_blocks_project_only_upgrade(self):
        self.assert_other_project_activity_blocks(
            "experience_run", "ACTIVE_EXPERIENCE_RUNS:1"
        )

    def test_other_project_backlog_plan_blocks_project_only_upgrade(self):
        self.assert_other_project_activity_blocks(
            "backlog_plan", "ACTIVE_BACKLOG_PLANS:1"
        )

    def test_prepare_branch_refuses_repository_without_origin(self):
        branch = run(
            ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
        ).stdout.strip()
        with self.assertRaisesRegex(
            UPGRADE.UpgradeError, "does not require an upgrade branch"
        ):
            UPGRADE.prepare_branch(
                self.home, self.home / "pmo.db", 6, self.project
            )
        self.assertEqual(
            run(
                ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
            ).stdout.strip(),
            branch,
        )

    def test_prepare_branch_collision_keeps_default_branch_checked_out(self):
        target = run(
            ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
        ).stdout.strip()
        remote = Path(self.tmp.name) / "collision-remote.git"
        self.assertEqual(run(["git", "init", "--bare", "-q", str(remote)]).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "add", "origin", str(remote)
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "push", "-u", "origin", target
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "set-head", "origin", target
        ], cwd=self.project).returncode, 0)
        stamp = "20260811T104530Z"
        branch = f"agent-marketplace/upgrade-{stamp}"
        self.assertEqual(run([
            "git", "branch", branch
        ], cwd=self.project).returncode, 0)
        with mock.patch.object(UPGRADE, "compact_utc_now", return_value=stamp):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "already exists"):
                UPGRADE.prepare_branch(
                    self.home, self.home / "pmo.db", 6, self.project
                )
        self.assertEqual(
            run(
                ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
            ).stdout.strip(),
            target,
        )

    def test_prepare_branch_refuses_dirty_default_branch_without_creating_ref(self):
        target = run(
            ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
        ).stdout.strip()
        remote = Path(self.tmp.name) / "dirty-remote.git"
        self.assertEqual(run(["git", "init", "--bare", "-q", str(remote)]).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "add", "origin", str(remote)
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "push", "-u", "origin", target
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "set-head", "origin", target
        ], cwd=self.project).returncode, 0)
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(UPGRADE.UpgradeError, "only blocker"):
            UPGRADE.prepare_branch(
                self.home, self.home / "pmo.db", 6, self.project
            )
        refs = run([
            "git", "for-each-ref", "--format=%(refname:short)",
            "refs/heads/agent-marketplace/upgrade-",
        ], cwd=self.project).stdout.strip()
        self.assertEqual(refs, "")

    def test_repository_delivery_falls_back_to_main_or_master(self):
        for target in ("main", "master"):
            with self.subTest(target=target):
                root = Path(self.tmp.name) / f"fallback-{target}"
                remote = Path(self.tmp.name) / f"fallback-{target}.git"
                root.mkdir()
                for command in (
                    ["git", "init", "-q"],
                    ["git", "config", "user.email", "upgrade@example.test"],
                    ["git", "config", "user.name", "Upgrade Test"],
                    ["git", "branch", "-m", target],
                ):
                    self.assertEqual(run(command, cwd=root).returncode, 0)
                (root / "README.md").write_text("fixture\n", encoding="utf-8")
                self.assertEqual(run(["git", "add", "."], cwd=root).returncode, 0)
                self.assertEqual(run([
                    "git", "commit", "-qm", "baseline"
                ], cwd=root).returncode, 0)
                self.assertEqual(run([
                    "git", "init", "--bare", "-q", str(remote)
                ]).returncode, 0)
                self.assertEqual(run([
                    "git", "remote", "add", "origin", str(remote)
                ], cwd=root).returncode, 0)
                self.assertEqual(run([
                    "git", "push", "-u", "origin", target
                ], cwd=root).returncode, 0)
                self.assertEqual(
                    UPGRADE.repository_delivery(root)["target_branch"], target
                )

    def test_nonempty_legacy_design_runtime_blocks_upgrade(self):
        legacy = self.project / "workspace" / "design-system-work"
        (legacy / "candidate.html").write_text(
            "<html></html>\n", encoding="utf-8"
        )
        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "LEGACY_TRANSIENT_CONTENT:workspace/design-system-work",
            status["blockers"],
        )

    def test_runtime_collision_and_symlink_block_upgrade(self):
        target = (self.project / ".agentrof" / "agent-marketplace"
                  / ".runtime" / "plan")
        target.mkdir(parents=True)
        (target / "other.json").write_text("{}\n", encoding="utf-8")
        _result, status = self.status()
        self.assertTrue(any(
            value.startswith("RUNTIME_MIGRATION_COLLISION:")
            for value in status["blockers"]
        ))
        target_file = target / "other.json"
        target_file.unlink()
        target.rmdir()
        legacy = self.project / "workspace" / "experience-design-work"
        legacy.rmdir()
        symlink_target = self.project / "legacy-experience-target"
        symlink_target.mkdir()
        legacy.symlink_to(symlink_target, target_is_directory=True)
        _result, status = self.status()
        self.assertIn(
            "SYMLINKED_RUNTIME_TARGET:workspace/experience-design-work",
            status["blockers"],
        )

    def test_database_upgrade_blocks_active_work_in_another_project(self):
        con = sqlite3.connect(self.home / "pmo.db")
        stamp = "2026-01-01T00:00:00+00:00"
        other_id = con.execute(
            "INSERT INTO projects(project_key, name, created_at)"
            " VALUES ('other', 'Other', ?)",
            (stamp,),
        ).lastrowid
        con.execute(
            "INSERT INTO work_orders(project_id, story_id, work_order_key,"
            " request, status, current_step, worktree_path, bindings_json,"
            " created_at, updated_at) VALUES (?, NULL, 'other-active', 'work',"
            " 'running', '0', '/other', '{}', ?, ?)",
            (other_id, stamp, stamp),
        )
        stamp_integrity(con)
        con.commit()
        con.close()

        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status["status"], "AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED")
        self.assertIn("ACTIVE_WORK_ORDER:other-active", status["blockers"])

    def test_writer_lock_denies_a_concurrent_source_mutation(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.migrate_database_candidate
        race = {"result": "not-attempted"}

        def migrate_and_attempt_write(candidate, target_schema, payload):
            original(candidate, target_schema, payload)
            con = sqlite3.connect(
                self.home / "pmo.db", timeout=0.05, isolation_level=None
            )
            try:
                con.execute(
                    "UPDATE projects SET name='concurrent-write'"
                    " WHERE project_key='shop'"
                )
                race["result"] = "write-succeeded"
            except sqlite3.OperationalError as exc:
                race["result"] = str(exc)
            finally:
                con.close()

        with mock.patch.object(
            UPGRADE, "migrate_database_candidate",
            side_effect=migrate_and_attempt_write,
        ):
            UPGRADE.apply(
                self.home, self.home / "pmo.db", 6, plan["plan_id"]
            )

        self.assertIn("locked", race["result"])
        con = sqlite3.connect(self.home / "pmo.db")
        name = con.execute(
            "SELECT name FROM projects WHERE project_key='shop'"
        ).fetchone()[0]
        con.close()
        self.assertNotEqual(name, "concurrent-write")

    def test_database_change_before_writer_lock_aborts_without_migration(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.lock_database_for_upgrade

        def mutate_then_lock(database):
            con = sqlite3.connect(database)
            con.execute(
                "UPDATE projects SET name='lock-race' WHERE project_key='shop'"
            )
            stamp_integrity(con)
            con.commit()
            con.close()
            return original(database)

        with mock.patch.object(
            UPGRADE, "lock_database_for_upgrade", side_effect=mutate_then_lock,
        ):
            with self.assertRaisesRegex(
                UPGRADE.UpgradeError, "before the writer lock"
            ):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )

        self.assertEqual(
            UPGRADE.database_version(self.home / "pmo.db"), 3
        )
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace" / "project.json").exists())
        self.assertFalse((self.home / "maintenance.json").exists())

    def test_package_tamper_and_dual_host_version_drift_fail_closed(self):
        registry = self.registry()
        tampered = self.copy_package("claude", TEAM)
        (tampered / "constitution.md").write_text("tampered\n", encoding="utf-8")
        registry["plugins"][TEAM]["hosts"]["claude"]["root"] = str(tampered)
        self.save_registry(registry)
        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("PLUGIN_INVENTORY_INVALID" in value
                            for value in status["blockers"]))

        codex = self.copy_package("codex", TEAM)
        manifest_path = codex / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "9.0.0"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self.rewrite_provenance(codex, "codex", TEAM, "9.0.0")
        self.write_dual_host_registry()
        registry = self.registry()
        registry["plugins"][TEAM]["hosts"]["codex"].update({
            "root": str(codex), "version": "9.0.0",
        })
        self.save_registry(registry)
        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "DUAL_HOST_VERSION_MISMATCH:software-engineering-team",
            status["blockers"],
        )

    def test_claude_host_runtime_markers_preserve_package_provenance(self):
        package = self.copy_package("claude", TEAM)
        manifest = json.loads((
            package / ".claude-plugin" / "plugin.json"
        ).read_text(encoding="utf-8"))
        marker_root = package / ".in_use"
        marker_root.mkdir()
        (marker_root / "4242").write_bytes(b"")
        (marker_root / "5151").write_text(json.dumps({
            "pid": 5151,
            "procStart": "Mon Aug 10 07:50:50 2026",
        }), encoding="utf-8")
        (marker_root / "6262.tmp.deadbeef").write_text(json.dumps({
            "pid": 6262,
            "procStart": "Mon Aug 10 07:50:51 2026",
        }), encoding="utf-8")

        provenance = UPGRADE.verify_package_provenance(
            package, "claude", manifest
        )
        self.assertEqual(len(provenance), 64)

    def test_host_runtime_marker_allowlist_fails_closed(self):
        valid = json.dumps({
            "pid": 4242,
            "procStart": "Mon Aug 10 07:50:50 2026",
        })
        invalid = {
            "unknown-name": (Path(".in_use") / "active", valid),
            "nested": (Path(".in_use") / "4242" / "nested", valid),
            "pid-mismatch": (Path(".in_use") / "4242", json.dumps({
                "pid": 5151, "procStart": "Mon Aug 10 07:50:50 2026",
            })),
            "unknown-key": (Path(".in_use") / "4242", json.dumps({
                "pid": 4242,
                "procStart": "Mon Aug 10 07:50:50 2026",
                "extra": True,
            })),
            "malformed-json": (Path(".in_use") / "4242", "not-json"),
            "oversized": (Path(".in_use") / "4242", json.dumps({
                "pid": 4242, "procStart": "x" * 600,
            })),
        }
        for name, (relative, content) in invalid.items():
            with self.subTest(name=name):
                package = Path(self.tmp.name) / "runtime-markers" / name
                shutil.copytree(REPO / "dist" / "claude" / TEAM, package)
                manifest = json.loads((
                    package / ".claude-plugin" / "plugin.json"
                ).read_text(encoding="utf-8"))
                marker = package / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(
                        UPGRADE.UpgradeError, "provenance verification failed"):
                    UPGRADE.verify_package_provenance(
                        package, "claude", manifest
                    )

        symlinked = Path(self.tmp.name) / "runtime-markers" / "symlinked"
        shutil.copytree(REPO / "dist" / "claude" / TEAM, symlinked)
        marker = symlinked / ".in_use" / "4242"
        marker.parent.mkdir()
        marker.symlink_to(symlinked / "constitution.md")
        manifest = json.loads((
            symlinked / ".claude-plugin" / "plugin.json"
        ).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
                UPGRADE.UpgradeError, "provenance verification failed"):
            UPGRADE.verify_package_provenance(
                symlinked, "claude", manifest
            )

        codex = self.copy_package("codex", TEAM)
        marker = codex / ".in_use" / "4242"
        marker.parent.mkdir()
        marker.write_text(valid, encoding="utf-8")
        manifest = json.loads((
            codex / ".codex-plugin" / "plugin.json"
        ).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
                UPGRADE.UpgradeError, "provenance verification failed"):
            UPGRADE.verify_package_provenance(codex, "codex", manifest)

        unknown_contract = self.copy_package("claude", TEAM)
        provenance_path = unknown_contract / ".agent-marketplace-package.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["runtime_contracts"] = ["unknown_runtime_contract"]
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest = json.loads((
            unknown_contract / ".claude-plugin" / "plugin.json"
        ).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
                UPGRADE.UpgradeError, "runtime contract invalid"):
            UPGRADE.verify_package_provenance(
                unknown_contract, "claude", manifest
            )

    def test_interrupted_post_database_commit_recovers_from_journal(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.run_adapter

        def fail_apply(root, action, project, workspace, choices=None,
                       scope="all"):
            if action == "apply":
                raise UPGRADE.UpgradeError("injected adapter failure")
            return original(root, action, project, workspace, choices,
                            scope=scope)

        with mock.patch.object(UPGRADE, "run_adapter", side_effect=fail_apply):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "injected"):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        run_id = maintenance["run_id"]
        journal = json.loads((
            self.home / "upgrades" / run_id / "journal.json"
        ).read_text())
        self.assertEqual(journal["phase"], "recovery_required")
        self.assertTrue((self.project / "workspace" / "planning"
                         / "baseline.json").is_file())
        self.assertFalse((self.project / ".agentrof" / "agent-marketplace"
                          / ".runtime" / "plan").exists())
        recovered = UPGRADE.recover(
            self.home, self.home / "pmo.db", 6, run_id
        )
        self.assertEqual(
            recovered["status"], "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        self.assertFalse((self.home / "maintenance.json").exists())
        self.assertTrue((self.project / ".agentrof" / "agent-marketplace"
                         / ".runtime" / "plan" / "baseline.json").is_file())
        self.assertEqual(
            (self.project / "user-code.txt").read_text(encoding="utf-8"),
            "user-owned\n",
        )

    def test_post_project_interruption_preserves_evidence_and_user_text(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.sync_project_identity
        with mock.patch.object(
            UPGRADE, "sync_project_identity",
            side_effect=UPGRADE.UpgradeError("injected identity failure"),
        ):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "identity"):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        run_id = maintenance["run_id"]
        journal_path = self.home / "upgrades" / run_id / "journal.json"
        before = json.loads(journal_path.read_text())["project_snapshots"]
        claude = self.project / "CLAUDE.md"
        planned_claude = claude.read_bytes()
        claude.write_text(
            claude.read_text(encoding="utf-8") + "\n# User recovery note\n",
            encoding="utf-8",
        )
        with mock.patch.object(UPGRADE, "sync_project_identity", side_effect=original):
            with self.assertRaisesRegex(
                UPGRADE.UpgradeError, "restore the planned managed files"
            ):
                UPGRADE.recover(self.home, self.home / "pmo.db", 6, run_id)
        self.assertIn("# User recovery note", claude.read_text(encoding="utf-8"))
        claude.write_bytes(planned_claude)
        with mock.patch.object(UPGRADE, "sync_project_identity", side_effect=original):
            recovered = UPGRADE.recover(self.home, self.home / "pmo.db", 6, run_id)
        self.assertEqual(
            recovered["status"], "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        after = json.loads(journal_path.read_text())["project_snapshots"]
        self.assertEqual(after, before)
        self.assertNotIn("# User recovery note", claude.read_text(encoding="utf-8"))

    def test_project_only_failure_enters_recovery_instead_of_false_rollback(self):
        self.complete_upgrade_and_commit()
        self.install_team_version("99.0.0")
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        self.assertEqual(plan["database_schema"], 6)
        state_path = self.project / "workspace" / "config.json"

        with mock.patch.object(
            UPGRADE, "sync_project_identity",
            side_effect=UPGRADE.UpgradeError("injected identity failure"),
        ):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "identity"):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )

        maintenance = json.loads((self.home / "maintenance.json").read_text())
        run_id = maintenance["run_id"]
        journal = json.loads((
            self.home / "upgrades" / run_id / "journal.json"
        ).read_text())
        self.assertEqual(journal["phase"], "recovery_required")
        recovered = UPGRADE.recover(
            self.home, self.home / "pmo.db", 6, run_id
        )
        self.assertEqual(
            recovered["status"],
            "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED",
        )
        self.assertTrue(state_path.is_file())

    def test_project_only_mid_apply_failure_also_requires_recovery(self):
        self.complete_upgrade_and_commit()
        self.install_team_version("99.0.0")
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.run_adapter

        def fail_apply(root, action, project, workspace, choices=None,
                       scope="all"):
            if action == "apply":
                raise UPGRADE.UpgradeError("injected project apply failure")
            return original(root, action, project, workspace, choices,
                            scope=scope)

        with mock.patch.object(UPGRADE, "run_adapter", side_effect=fail_apply):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "project apply"):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        journal = json.loads((
            self.home / "upgrades" / maintenance["run_id"] / "journal.json"
        ).read_text())
        self.assertEqual(journal["phase"], "recovery_required")

    def test_recovery_reclaims_only_a_proven_dead_upgrade_lock(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.run_adapter

        def fail_apply(root, action, project, workspace, choices=None,
                       scope="all"):
            if action == "apply":
                raise UPGRADE.UpgradeError("injected adapter failure")
            return original(root, action, project, workspace, choices,
                            scope=scope)

        with mock.patch.object(UPGRADE, "run_adapter", side_effect=fail_apply):
            with self.assertRaises(UPGRADE.UpgradeError):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        lock = self.home / "locks" / "upgrade.lock"
        lock.mkdir(parents=True)
        (lock / "owner.json").write_text(json.dumps({
            "pid": 99999999,
            "host": socket.gethostname(),
            "started_at": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        recovered = UPGRADE.recover(
            self.home, self.home / "pmo.db", 6, maintenance["run_id"]
        )
        self.assertEqual(
            recovered["status"],
            "AGENT_MARKETPLACE_UPGRADE_COMPLETE_RESTART_REQUIRED",
        )
        self.assertFalse(lock.exists())

    def test_recovery_never_reclaims_a_live_upgrade_lock(self):
        lock = self.home / "locks" / "upgrade.lock"
        lock.mkdir(parents=True)
        (lock / "owner.json").write_text(json.dumps({
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        with self.assertRaisesRegex(UPGRADE.UpgradeError, "already held"):
            with UPGRADE.DirectoryLock(
                self.home, "upgrade.lock", reclaim_dead=True
            ):
                self.fail("a live lock must never be reclaimed")

    def test_recovery_never_steals_a_corrupt_empty_upgrade_lock(self):
        lock = self.home / "locks" / "upgrade.lock"
        lock.mkdir(parents=True)
        with self.assertRaisesRegex(UPGRADE.UpgradeError, "already held"):
            with UPGRADE.DirectoryLock(self.home, "upgrade.lock"):
                self.fail("an ownerless lock must remain fail-closed")
        self.assertTrue(lock.is_dir())

    def test_current_project_does_not_require_remote_default_discovery(self):
        self.complete_upgrade_and_commit()
        remote = Path(self.tmp.name) / "uninitialized-remote.git"
        self.assertEqual(
            run(["git", "init", "--bare", "-q", str(remote)]).returncode, 0
        )
        self.assertEqual(run([
            "git", "remote", "add", "origin", str(remote),
        ], cwd=self.project).returncode, 0)
        result, current = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(current["status"], "AGENT_MARKETPLACE_CURRENT")

    def test_precommit_recovery_also_clears_a_proven_dead_lock(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        with mock.patch.object(
            UPGRADE, "lock_database_for_upgrade",
            side_effect=UPGRADE.UpgradeError("injected precommit failure"),
        ):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "precommit"):
                UPGRADE.apply(
                    self.home, self.home / "pmo.db", 6, plan["plan_id"]
                )
        journal_path = sorted((self.home / "upgrades").glob("*/journal.json"))[-1]
        run_id = journal_path.parent.name
        lock = self.home / "locks" / "upgrade.lock"
        lock.mkdir(parents=True)
        (lock / "owner.json").write_text(json.dumps({
            "pid": 99999999,
            "host": socket.gethostname(),
            "started_at": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        recovered = UPGRADE.recover(
            self.home, self.home / "pmo.db", 6, run_id
        )
        self.assertEqual(
            recovered["status"], "AGENT_MARKETPLACE_UPGRADE_REQUIRED_READY"
        )
        self.assertFalse(lock.exists())

    def test_remote_upgrade_stays_pending_until_target_branch_contains_it(self):
        target = run(
            ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
        ).stdout.strip()
        remote = Path(self.tmp.name) / "remote.git"
        self.assertEqual(run(["git", "init", "--bare", "-q", str(remote)]).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "add", "origin", str(remote)
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "push", "-u", "origin", target
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "remote", "set-head", "origin", target
        ], cwd=self.project).returncode, 0)

        self.assertEqual(run([
            "git", "switch", "-c", "feature/unrelated",
        ], cwd=self.project).returncode, 0)
        _, wrong_branch = self.status()
        self.assertIn(
            f"UPGRADE_TARGET_REQUIRED:{target}", wrong_branch["blockers"]
        )
        refused_prepare = self.cli(
            "upgrade", "prepare-branch", "--project-root", str(self.project),
            check=False,
        )
        self.assertEqual(refused_prepare.returncode, 1)
        self.assertIn("only blocker", refused_prepare.stderr)
        self.assertEqual(
            run(
                ["git", "symbolic-ref", "--short", "HEAD"], cwd=self.project
            ).stdout.strip(),
            "feature/unrelated",
        )
        chained_return = self.team_guard("Bash", {
            "command": f"git switch {target} && touch user-code.txt",
        })
        self.assertEqual(chained_return.returncode, 2)
        return_to_target = self.team_guard("Bash", {
            "command": f"git switch {target}",
        })
        self.assertEqual(
            return_to_target.returncode, 0, return_to_target.stderr
        )
        self.assertEqual(run([
            "git", "switch", target,
        ], cwd=self.project).returncode, 0)
        base_head = run(["git", "rev-parse", "HEAD"], cwd=self.project).stdout.strip()
        _, blocked = self.status()
        self.assertIn(f"UPGRADE_BRANCH_REQUIRED:{target}", blocked["blockers"])
        raw_branch = self.team_guard("Bash", {
            "command": "git switch -c agent-marketplace/upgrade-manual",
        })
        self.assertEqual(raw_branch.returncode, 2)
        launcher = self.home / "bin" / "pmo_cli.py"
        admitted = self.team_guard("Bash", {
            "command": f"{launcher} upgrade prepare-branch"
                       f" --project-root {self.project}",
        })
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        prepared = json.loads(self.cli(
            "upgrade", "prepare-branch", "--project-root", str(self.project)
        ).stdout)
        feature = prepared["upgrade_branch"]
        self.assertRegex(
            feature, r"^agent-marketplace/upgrade-[0-9]{8}T[0-9]{6}Z$"
        )
        self.assertEqual(prepared["target_branch"], target)
        self.assertEqual(prepared["base_head"], base_head)
        self.assertEqual(
            prepared["next_status"],
            "AGENT_MARKETPLACE_UPGRADE_REQUIRED_READY",
        )
        self.assertEqual(
            run(["git", "rev-parse", "HEAD"], cwd=self.project).stdout.strip(),
            base_head,
        )
        self.complete_upgrade_and_commit()
        _, pending = self.status()
        self.assertEqual(
            pending["status"], "AGENT_MARKETPLACE_PROJECT_UPGRADE_PR_PENDING"
        )
        push = self.team_guard("Bash", {
            "command": f"git push -u origin {feature}",
        })
        self.assertEqual(push.returncode, 0, push.stderr)
        pr = self.team_guard("Bash", {
            "command": "gh pr create --title 'Apply marketplace upgrade'",
        })
        self.assertEqual(pr.returncode, 0, pr.stderr)

        self.assertEqual(run([
            "git", "switch", target
        ], cwd=self.project).returncode, 0)
        self.assertEqual(run([
            "git", "merge", "--ff-only", feature
        ], cwd=self.project).returncode, 0)
        _, current = self.status()
        self.assertEqual(current["status"], "AGENT_MARKETPLACE_CURRENT")
        self.assertEqual(run([
            "git", "switch", "-c", "feature/after-upgrade",
        ], cwd=self.project).returncode, 0)
        _, feature_current = self.status()
        self.assertEqual(
            feature_current["status"], "AGENT_MARKETPLACE_CURRENT"
        )

    def test_skipped_release_chain_requires_every_step_and_host_parity(self):
        registry = self.registry()
        catalogs = []
        for host in ("claude", "codex"):
            package = self.copy_package(host, "project-management-office")
            catalog_path = package / "migrations" / "manifest.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            step_id = "project-management-office.database.2-3"
            runner = package / "migrations" / "database" / "2-3.py"
            runner.write_text(
                "def migrate(connection, context):\n    return {'step': '2-3'}\n",
                encoding="utf-8",
            )
            catalog["database"] = {
                "baseline": 2,
                "current": 5,
                "steps": [{
                    "id": step_id,
                    "from": 2,
                    "to": 3,
                    "checksum": "sha256:" + hashlib.sha256(
                        runner.read_bytes()
                    ).hexdigest(),
                    "runner": "migrations/database/2-3.py",
                }, *catalog["database"]["steps"]],
            }
            catalog_path.write_text(
                json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
            )
            version = registry["plugins"]["project-management-office"]["hosts"][host]["version"]
            self.rewrite_provenance(
                package, host, "project-management-office", version
            )
            registry["plugins"]["project-management-office"]["hosts"][host]["root"] = str(package)
            catalogs.append((package, catalog))
        self.save_registry(registry)
        chain = UPGRADE.migration_chain(
            {"data_root": str(self.home)}, "project-management-office",
            "database", 2, 5,
        )
        self.assertEqual([step["from"] for step in chain], [2, 3, 4])

        package, catalog = catalogs[-1]
        catalog["database"]["steps"] = catalog["database"]["steps"][1:]
        (package / "migrations" / "manifest.json").write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
        )
        version = registry["plugins"]["project-management-office"]["hosts"]["codex"]["version"]
        self.rewrite_provenance(
            package, "codex", "project-management-office", version
        )
        with self.assertRaisesRegex(UPGRADE.UpgradeError, "catalog mismatch"):
            UPGRADE.migration_chain(
                {"data_root": str(self.home)}, "project-management-office",
                "database", 2, 5,
            )

    def test_single_host_setup_writes_portable_dual_host_contract(self):
        registry = self.registry()
        for component in ("project-management-office", TEAM):
            registry["plugins"][component]["hosts"].pop("codex")
        self.save_registry(registry)
        first = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        self.cli("upgrade", "apply", "--plan-id", first["plan_id"])
        staged = run(
            ["git", "add", "--", *first["project_files"]], cwd=self.project
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)
        committed = run([
            "git", "commit", "-qm", "chore: apply Agent Marketplace upgrade"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        self.assertTrue((self.project / "AGENTS.md").is_file())
        self.assertTrue((self.project / "CLAUDE.md").is_file())
        self.assertFalse((self.project / ".codex").exists())
        config_path = self.project / "workspace" / "config.json"
        tracked_before = config_path.read_bytes()

        self.write_dual_host_registry()
        status_result, status = self.status()
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        self.assertEqual(status["status"], "AGENT_MARKETPLACE_CURRENT")
        environment = json.loads(self.cli(
            "project", "environment-status", "--project-root",
            str(self.project), "--json", check=False,
        ).stdout)
        self.assertEqual(
            environment["status"],
            "AGENT_MARKETPLACE_ENVIRONMENT_RECONCILE_REQUIRED",
        )
        self.assertTrue(any(
            value.startswith("LOCAL_PROJECTION_DRIFT:")
            for value in environment["reasons"]
        ))
        attached = self.cli(
            "project", "attach", "--project-root", str(self.project),
            "--workspace", "workspace", "--json",
        )
        self.assertEqual(json.loads(attached.stdout)["status"],
                         "AGENT_MARKETPLACE_CURRENT")
        self.assertTrue((self.project / ".codex" / "agents").is_dir())
        self.assertTrue((self.project / "AGENTS.user.md").is_file())
        self.assertEqual(config_path.read_bytes(), tracked_before)
        self.assertEqual(
            run(["git", "status", "--porcelain"], cwd=self.project).stdout,
            "",
        )


if __name__ == "__main__":
    unittest.main()
