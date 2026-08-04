"""Cross-host marketplace upgrade lifecycle and fail-closed safety cases."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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


class UpgradeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.project = root / "project"
        self.project.mkdir()
        self.env = {"AGENTROF_HOME": str(self.home)}
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
        database = self.home / "agentrof.db"
        con = sqlite3.connect(database)
        triggers = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
            " AND name LIKE 'agentrof_writer_%'"
        )]
        for trigger in triggers:
            con.execute(f"DROP TRIGGER {trigger}")
        con.execute("DROP INDEX IF EXISTS idx_projects_uuid")
        con.execute("DROP TABLE IF EXISTS schema_migrations")
        con.execute("ALTER TABLE projects DROP COLUMN repository_fingerprint")
        con.execute("ALTER TABLE projects DROP COLUMN project_uuid")
        con.execute("DELETE FROM meta WHERE key='writer_epoch'")
        con.execute("PRAGMA user_version=3")
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('fingerprint', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (content_fingerprint(con),),
        )
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
            if path.name == ".agentrof-package.json" or "__pycache__" in path.parts:
                continue
            files[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        (root / ".agentrof-package.json").write_text(json.dumps({
            "schema_version": 1,
            "component": component,
            "host": host,
            "version": version,
            "files": files,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def status(self):
        result = self.cli(
            "upgrade", "status", "--project-root", str(self.project), "--json",
            check=False,
        )
        return result, json.loads(result.stdout)

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
        self.assertEqual(status["status"], "AGENTROF_UPGRADE_REQUIRED_READY")
        self.assertIn("DATABASE_SCHEMA:3->4", status["reasons"])
        self.assertIn("PROJECT_CONTRACT:unversioned->1", status["reasons"])

        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        )
        plan = json.loads(planned.stdout)
        self.assertEqual(plan["status"], "AGENTROF_UPGRADE_APPLY_READY")
        self.assertIn(".agentrof/project.json", plan["project_files"])
        self.assertIn("CLAUDE.md", plan["project_files"])
        self.assertIn("AGENTS.md", plan["project_files"])
        self.assertIn("database", plan["backup_policy"])

        applied = self.cli("upgrade", "apply", "--plan-id", plan["plan_id"])
        result = json.loads(applied.stdout)
        self.assertEqual(
            result["status"], "AGENTROF_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(
            (self.project / "user-code.txt").read_text(encoding="utf-8"),
            "user-owned\n",
        )
        config = json.loads((
            self.project / "workspace" / "config.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(config["team_id"], TEAM)
        self.assertEqual(config["custom_user_key"], "preserve-me")
        self.assertNotIn("managed_by", config)
        state = json.loads((
            self.project / ".agentrof" / "project.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(state["hosts"], ["claude", "codex"])
        self.assertEqual(set(state["components"]), {
            "project-management-office", TEAM,
        })
        self.assertTrue(any(key.startswith("claude:")
                            for key in state["managed_surfaces"]))
        self.assertTrue(any(key.startswith("codex:")
                            for key in state["managed_surfaces"]))

        con = sqlite3.connect(self.home / "agentrof.db")
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 4)
        migration = con.execute(
            "SELECT migration_id, from_version, to_version"
            " FROM schema_migrations"
        ).fetchone()
        self.assertEqual(migration, (
            "project-management-office.database.3-4", 3, 4,
        ))
        identity = con.execute(
            "SELECT project_uuid, repository_fingerprint FROM projects"
            " WHERE project_key='shop'"
        ).fetchone()
        self.assertEqual(identity[0], state["project_id"])
        self.assertEqual(identity[1], state["repository_fingerprint"])
        with self.assertRaisesRegex(sqlite3.OperationalError,
                                    "agentrof_writer_epoch"):
            con.execute("UPDATE projects SET name='stale-writer'")
        con.close()

        pending_result, pending = self.status()
        self.assertEqual(pending_result.returncode, 0, pending_result.stderr)
        self.assertEqual(pending["status"], "PROJECT_UPGRADE_PR_PENDING")
        denied = self.team_guard("Write", {
            "file_path": str(self.project / "normal-work.txt"), "content": "x",
        })
        self.assertEqual(denied.returncode, 2)
        self.assertIn("PROJECT_UPGRADE_PR_PENDING", denied.stderr)
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
        run(["git", "add", "--", *plan["project_files"][1:]], cwd=self.project)
        admitted_commit = self.team_guard("Bash", {"command": commit_command})
        self.assertEqual(admitted_commit.returncode, 0, admitted_commit.stderr)
        committed = run([
            "git", "commit", "-qm", "chore: apply Agent Marketplace upgrade"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        current_result, current = self.status()
        self.assertEqual(current_result.returncode, 0, current_result.stderr)
        self.assertEqual(current["status"], "AGENTROF_CURRENT")

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
        self.assertFalse((self.project / ".agentrof" / "project.json").exists())

    def test_unmanaged_instruction_collision_blocks_plan(self):
        (self.project / "CLAUDE.md").write_text(
            "# User instructions\n\nNever replace this.\n", encoding="utf-8"
        )
        run(["git", "add", "CLAUDE.md"], cwd=self.project)
        run(["git", "commit", "-qm", "user-instructions"], cwd=self.project)
        before = (self.project / "CLAUDE.md").read_bytes()
        result = self.cli(
            "upgrade", "plan", "--project-root", str(self.project), check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unmanaged CLAUDE.md collision", result.stderr)
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), before)
        self.assertFalse((self.project / ".agentrof" / "project.json").exists())

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
        self.assertFalse((self.project / ".agentrof" / "project.json").exists())

    def test_competing_sessions_block_plan_without_mutation(self):
        sessions = self.home / "sessions"
        sessions.mkdir()
        for index in range(2):
            (sessions / f"session-{index}.json").write_text(json.dumps({
                "session_id": f"session-{index}", "pmo_ready": True,
            }), encoding="utf-8")
        result, status = self.status()
        self.assertEqual(result.returncode, 1)
        self.assertIn("CLOSE_OTHER_SESSIONS_REQUIRED:1", status["blockers"])
        planned = self.cli(
            "upgrade", "plan", "--project-root", str(self.project), check=False
        )
        self.assertEqual(planned.returncode, 1)
        self.assertFalse((self.project / ".agentrof" / "project.json").exists())

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

    def test_interrupted_post_swap_run_recovers_from_journal(self):
        plan = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        original = UPGRADE.run_adapter

        def fail_apply(root, action, project, workspace):
            if action == "apply":
                raise UPGRADE.UpgradeError("injected adapter failure")
            return original(root, action, project, workspace)

        with mock.patch.object(UPGRADE, "run_adapter", side_effect=fail_apply):
            with self.assertRaisesRegex(UPGRADE.UpgradeError, "injected"):
                UPGRADE.apply(
                    self.home, self.home / "agentrof.db", 4, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        run_id = maintenance["run_id"]
        journal = json.loads((
            self.home / "upgrades" / run_id / "journal.json"
        ).read_text())
        self.assertEqual(journal["phase"], "recovery_required")
        recovered = UPGRADE.recover(
            self.home, self.home / "agentrof.db", 4, run_id
        )
        self.assertEqual(
            recovered["status"], "AGENTROF_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        self.assertFalse((self.home / "maintenance.json").exists())
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
                    self.home, self.home / "agentrof.db", 4, plan["plan_id"]
                )
        maintenance = json.loads((self.home / "maintenance.json").read_text())
        run_id = maintenance["run_id"]
        journal_path = self.home / "upgrades" / run_id / "journal.json"
        before = json.loads(journal_path.read_text())["project_snapshots"]
        claude = self.project / "CLAUDE.md"
        claude.write_text(
            claude.read_text(encoding="utf-8") + "\n# User recovery note\n",
            encoding="utf-8",
        )
        with mock.patch.object(UPGRADE, "sync_project_identity", side_effect=original):
            recovered = UPGRADE.recover(
                self.home, self.home / "agentrof.db", 4, run_id
            )
        self.assertEqual(
            recovered["status"], "AGENTROF_UPGRADE_COMPLETE_RESTART_REQUIRED"
        )
        after = json.loads(journal_path.read_text())["project_snapshots"]
        self.assertEqual(after, before)
        self.assertIn("# User recovery note", claude.read_text(encoding="utf-8"))

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
                "current": 4,
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
            "database", 2, 4,
        )
        self.assertEqual([step["from"] for step in chain], [2, 3])

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
                "database", 2, 4,
            )

    def test_adding_second_host_upgrades_the_same_project_contract(self):
        registry = self.registry()
        for component in ("project-management-office", TEAM):
            registry["plugins"][component]["hosts"].pop("codex")
        self.save_registry(registry)
        first = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        self.cli("upgrade", "apply", "--plan-id", first["plan_id"])
        run(["git", "add", "--", *first["project_files"]], cwd=self.project)
        committed = run([
            "git", "commit", "-qm", "chore: apply Agent Marketplace upgrade"
        ], cwd=self.project)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        self.assertFalse((self.project / "AGENTS.md").exists())

        self.write_dual_host_registry()
        status_result, status = self.status()
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        self.assertEqual(status["status"], "AGENTROF_UPGRADE_REQUIRED_READY")
        self.assertTrue(any(value.startswith("HOST_SURFACES:claude->claude,codex")
                            for value in status["reasons"]))
        second = json.loads(self.cli(
            "upgrade", "plan", "--project-root", str(self.project)
        ).stdout)
        self.assertIn("AGENTS.md", second["project_files"])
        self.cli("upgrade", "apply", "--plan-id", second["plan_id"])
        state = json.loads((
            self.project / ".agentrof" / "project.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(state["hosts"], ["claude", "codex"])
        self.assertTrue((self.project / ".codex" / "agents").is_dir())


if __name__ == "__main__":
    unittest.main()
