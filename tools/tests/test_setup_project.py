from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_project.py"
CHECK = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_check.py"
BACKLOG = ROOT / "plugins" / "software-engineering-team" / "scripts" / "backlog_compile.py"
PREPARATION = ROOT / "plugins" / "software-engineering-team" / "scripts" / "preparation_check.py"


class SetupProjectTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_bootstrap_is_project_local_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            first = self.run_script(SETUP, "--project-root", str(project), "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["next_entry"], "business-analysis")
            runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
            self.assertEqual(Path(payload["runtime_root"]), runtime.resolve())
            self.assertTrue(runtime.is_dir())
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertIn("/.agentrof/", (project / ".gitignore").read_text())
            ignored = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q",
                 ".agentrof/agent-marketplace/.runtime/probe"],
                cwd=project, check=False,
            )
            self.assertEqual(ignored.returncode, 0)
            runtime_sentinel = runtime / "cache.txt"
            runtime_sentinel.write_text("disposable\n", encoding="utf-8")
            authored = project / "README.user.md"
            authored.write_text("user content\n", encoding="utf-8")
            second = self.run_script(SETUP, "--project-root", str(project), "--json")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(authored.read_text(encoding="utf-8"), "user content\n")
            self.assertEqual(runtime_sentinel.read_text(encoding="utf-8"), "disposable\n")
            checked = self.run_script(CHECK, "check", "--project-root", str(project), "--json")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            gate = project / ".github" / "agentrof" / "vault-gate.pyz"
            self.assertTrue(gate.is_file())
            portable = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(portable.returncode, 0, portable.stderr)
            before = self.run_script(
                PREPARATION, "status", "--project-root", str(project), "--json"
            )
            self.assertEqual(before.returncode, 1, before.stderr)
            shutil.rmtree(project / ".agentrof")
            after = self.run_script(
                PREPARATION, "status", "--project-root", str(project), "--json"
            )
            self.assertEqual(after.returncode, 1, after.stderr)
            self.assertEqual(json.loads(after.stdout), json.loads(before.stdout))

            config = json.loads(
                (project / "workspace/config.json").read_text(encoding="utf-8")
            )
            serialized = json.dumps(config, sort_keys=True)
            for retired_identity in (
                "build_id", "contract_version", "marketplace_release",
                "source_commit", "source_ref",
            ):
                self.assertNotIn(retired_identity, serialized)

    def test_preflight_allows_an_unconfigured_git_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            result = self.run_script(
                CHECK, "preflight", "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_setup_converges_retired_config_without_a_migration_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            workspace = project / "workspace"
            workspace.mkdir()
            config_path = workspace / "config.json"
            config_path.write_text(json.dumps({
                "agent_marketplace": {
                    "team_id": "software-engineering-team",
                    "project_contract_version": 5,
                    "vault": {"status": "active"},
                },
                "managed_by": (
                    "software-engineering-team plugin; change only through "
                    "the configure entry"
                ),
                "project_origin": "greenfield",
                "model_overrides": {"backend_developer": "retired"},
                "test_command": "make test",
                "source_dirs": ["workspace/apps"],
                "custom_project_field": "preserved",
            }) + "\n", encoding="utf-8")

            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["team_id"], "software-engineering-team")
            self.assertEqual(config["project_origin"], "greenfield")
            self.assertEqual(config["custom_project_field"], "preserved")
            self.assertEqual(config["test_command"], "make test")
            self.assertEqual(config["source_dirs"], ["workspace/apps"])
            self.assertNotIn("agent_marketplace", config)
            self.assertNotIn("managed_by", config)
            self.assertNotIn("model_overrides", config)

    def test_noncanonical_managed_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            alternate = project / "alternate"
            alternate.mkdir()
            (alternate / "config.json").write_text(json.dumps({
                "team_id": "software-engineering-team",
                "project_origin": "greenfield",
            }) + "\n", encoding="utf-8")
            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("non-canonical managed workspace", result.stderr)

    def test_workspace_compatibility_flag_accepts_only_canonical_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            accepted = self.run_script(
                SETUP, "--project-root", str(project),
                "--workspace", "workspace", "--json",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            rejected = self.run_script(
                SETUP, "--project-root", str(project),
                "--workspace", "other", "--json",
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("invalid choice", rejected.stderr)

    def test_setup_check_rejects_database_state_in_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            database = (
                project / ".agentrof" / "agent-marketplace" / ".runtime"
                / "cache.sqlite"
            )
            database.write_bytes(b"not a database")
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1)
            findings = json.loads(checked.stdout)["findings"]
            self.assertTrue(any("forbidden in runtime" in item
                                for item in findings))

    def test_backlog_stubs_do_not_bypass_authored_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stderr)
            docs = project / "workspace" / "docs"
            for command in (
                ["init"],
                ["stub-epic", "customer-account-access", "--id", "EP-001"],
                ["stub-story", "customer-account-access", "register-account", "--id", "ST-001"],
            ):
                result = self.run_script(BACKLOG, *command, "--docs", str(docs))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            check = self.run_script(
                BACKLOG, "check", "--render", "--docs", str(docs)
            )
            self.assertEqual(check.returncode, 1, check.stdout + check.stderr)

    def test_runtime_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            target = Path(temporary) / "outside"
            project.mkdir()
            target.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (project / ".agentrof").symlink_to(target, target_is_directory=True)
            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("runtime path is symlinked", result.stderr)
            self.assertFalse((target / "agent-marketplace").exists())

    def test_concurrent_identical_setup_converges(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            command = [
                sys.executable, str(SETUP), "--project-root", str(project), "--json"
            ]
            processes = [
                subprocess.Popen(
                    command, cwd=ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                )
                for _ in range(2)
            ]
            results = [process.communicate(timeout=60) for process in processes]
            for process, (stdout, stderr) in zip(processes, results):
                self.assertEqual(process.returncode, 0, stdout + stderr)
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
