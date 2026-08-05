"""Deterministic host-CLI simulations for the real release smoke workflow."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import smoke_plugin_installs as smoke  # noqa: E402


TEAM = "software-engineering-team"


class FakeHostCli:
    def __init__(self, root: Path):
        self.root = root
        self.installed: dict[str, bool] = {}
        self.generator_runs = 0
        self.commands: list[tuple[str, ...]] = []

    def install_path(self, plugin: str, host: str) -> str:
        return str(self.root / "installs" / host / plugin)

    def claude_inventory(self) -> str:
        return json.dumps([
            {
                "id": f"{name}@{smoke.MARKETPLACE}",
                "enabled": enabled,
                "installPath": self.install_path(name, "claude"),
            }
            for name, enabled in sorted(self.installed.items())
        ])

    def codex_inventory(self, available: bool = False) -> str:
        data = {
            "installed": [
                {
                    "name": name,
                    "enabled": enabled,
                    "installedPath": self.install_path(name, "codex"),
                }
                for name, enabled in sorted(self.installed.items())
            ]
        }
        if available:
            data["available"] = [
                {"name": smoke.PMO}, {"name": TEAM},
            ]
        return json.dumps(data)

    def __call__(self, command: list[str], env: dict, input_text: str = "") -> str:
        del env, input_text
        self.commands.append(tuple(command))
        if command[0] == "git":
            return ""
        if command[:2] == ["claude", "--version"]:
            return "claude-simulated\n"
        if command[:2] == ["codex", "--version"]:
            return "codex-simulated\n"
        if command[0] == sys.executable and command[1].endswith(
                ("pmo_cli.py", "marketplace_run.py")):
            return "pmo: project 'smoke' registered\n"
        if command[0] == sys.executable and command[1].endswith(
                "generate_claude_project.py"):
            self.generator_runs += 1
            return json.dumps({
                "changes": ["CLAUDE.md"] if self.generator_runs == 1 else [],
            })
        if command[0] == sys.executable and command[1].endswith(
                "generate_codex_project.py"):
            self.generator_runs += 1
            return json.dumps({
                "written": ["AGENTS.md"] if self.generator_runs == 1 else [],
            })
        if command[0] == "claude":
            action = command[2]
            if action == "marketplace":
                return ""
            if action == "install":
                team = command[3].split("@", 1)[0]
                self.installed[team] = True
                self.installed[smoke.PMO] = True
                return ""
            if action == "update":
                return ""
            if action == "list":
                return self.claude_inventory()
            if action == "disable":
                self.installed[command[3].split("@", 1)[0]] = False
                return ""
            if action == "enable":
                self.installed[command[3].split("@", 1)[0]] = True
                return ""
            if action == "uninstall":
                self.installed.pop(command[3].split("@", 1)[0], None)
                return ""
        if command[0] == "codex":
            if command[2:4] == ["marketplace", "add"]:
                return "{}"
            if command[2:4] == ["marketplace", "upgrade"]:
                return "{}"
            action = command[2]
            if action == "list":
                return self.codex_inventory("--available" in command)
            if action == "add":
                self.installed[command[3].split("@", 1)[0]] = True
                return "{}"
            if action == "remove":
                self.installed.pop(command[3].split("@", 1)[0], None)
                return "{}"
        raise AssertionError(f"unexpected simulated command: {command}")


def entry_skills() -> list[dict]:
    skills = []
    for path in (REPO / "plugins" / TEAM / "skill-content").iterdir():
        if path.is_dir() and "exposure: entry" in (
                path / "SKILL.md").read_text(encoding="utf-8"):
            skills.append({"name": f"{TEAM}:{path.name}"})
    return skills


class SmokeWorkflowSimulationTests(unittest.TestCase):
    def test_checkout_catalog_rewrites_stable_sources_to_local_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = smoke.checkout_marketplace(REPO, Path(tmp) / "catalog")
            claude = json.loads((target / ".claude-plugin" / "marketplace.json").read_text())
            codex = json.loads((target / ".agents" / "plugins" / "marketplace.json").read_text())
            self.assertTrue(all(
                entry["source"].startswith("./dist/claude/")
                for entry in claude["plugins"]
            ))
            self.assertTrue(all(
                entry["source"]["source"] == "local"
                and entry["source"]["path"].startswith("./dist/codex/")
                for entry in codex["plugins"]
            ))

    def test_claude_lifecycle_simulation_proves_native_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHostCli(Path(tmp))
            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake), \
                    mock.patch.object(smoke, "assert_team_gate") as gate, \
                    mock.patch.object(smoke, "mark_pmo_ready") as ready:
                smoke.smoke_claude(REPO, [TEAM])
            self.assertEqual(fake.installed, {TEAM: True, smoke.PMO: True})
            self.assertEqual(fake.generator_runs, 2)
            self.assertEqual([call.args[-1] for call in gate.call_args_list], [2, 0, 2])
            ready.assert_called_once()
            install_index = next(
                index for index, command in enumerate(fake.commands)
                if command[:3] == ("claude", "plugin", "install")
            )
            first_inventory = next(
                index for index, command in enumerate(fake.commands)
                if command[:3] == ("claude", "plugin", "list")
            )
            self.assertLess(install_index, first_inventory)

    def test_codex_lifecycle_simulation_proves_explicit_pmo_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHostCli(Path(tmp))

            def skills(_env, _project):
                return entry_skills() if TEAM in fake.installed else []

            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake), \
                    mock.patch.object(smoke, "assert_team_gate") as gate, \
                    mock.patch.object(smoke, "mark_pmo_ready") as ready, \
                    mock.patch.object(smoke, "codex_skills", side_effect=skills):
                smoke.smoke_codex(REPO, [TEAM], smoke.PUBLIC_MARKETPLACE)
            self.assertEqual(fake.installed, {TEAM: True, smoke.PMO: True})
            self.assertEqual(fake.generator_runs, 2)
            self.assertEqual([call.args[-1] for call in gate.call_args_list], [2, 0])
            ready.assert_called_once()
            add_commands = [
                command for command in fake.commands
                if command[:3] == ("codex", "plugin", "add")
            ]
            self.assertEqual(
                [command[3].split("@", 1)[0] for command in add_commands[:2]],
                [TEAM, smoke.PMO],
            )
            self.assertIn(
                ("codex", "plugin", "marketplace", "upgrade",
                 smoke.MARKETPLACE, "--json"),
                fake.commands,
            )

    def test_claude_smoke_rejects_missing_native_dependency(self):
        class MissingDependency(FakeHostCli):
            def __call__(self, command, env, input_text=""):
                if command[:3] == ["claude", "plugin", "install"]:
                    self.commands.append(tuple(command))
                    self.installed[TEAM] = True
                    return ""
                return super().__call__(command, env, input_text)

        with tempfile.TemporaryDirectory() as tmp:
            fake = MissingDependency(Path(tmp))
            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake):
                with self.assertRaisesRegex(smoke.SmokeFailure, smoke.PMO):
                    smoke.smoke_claude(REPO, [TEAM])

    def test_codex_smoke_rejects_incomplete_available_inventory(self):
        class IncompleteInventory(FakeHostCli):
            def codex_inventory(self, available=False):
                data = json.loads(super().codex_inventory(available))
                if available:
                    data["available"] = [{"name": smoke.PMO}]
                return json.dumps(data)

        with tempfile.TemporaryDirectory() as tmp:
            fake = IncompleteInventory(Path(tmp))
            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake):
                with self.assertRaisesRegex(
                        smoke.SmokeFailure, "available inventory is incomplete"):
                    smoke.smoke_codex(REPO, [TEAM])

    def test_codex_smoke_rejects_non_idempotent_setup(self):
        class NonIdempotentSetup(FakeHostCli):
            def __call__(self, command, env, input_text=""):
                if command[0] == sys.executable and command[1].endswith(
                        "generate_codex_project.py"):
                    self.generator_runs += 1
                    return json.dumps({"written": ["AGENTS.md"]})
                return super().__call__(command, env, input_text)

        with tempfile.TemporaryDirectory() as tmp:
            fake = NonIdempotentSetup(Path(tmp))
            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake), \
                    mock.patch.object(smoke, "assert_team_gate"), \
                    mock.patch.object(smoke, "mark_pmo_ready"):
                with self.assertRaisesRegex(smoke.SmokeFailure, "not idempotent"):
                    smoke.smoke_codex(REPO, [TEAM])

    def test_codex_smoke_rejects_internal_skill_exposure(self):
        internal = next(
            path.name
            for path in (REPO / "plugins" / TEAM / "skill-content").iterdir()
            if path.is_dir() and "exposure: internal" in
            (path / "SKILL.md").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHostCli(Path(tmp))

            def skills(_env, _project):
                if TEAM not in fake.installed:
                    return []
                return [*entry_skills(), {"name": f"{TEAM}:{internal}"}]

            with mock.patch.object(smoke, "require_cli"), \
                    mock.patch.object(smoke, "run", side_effect=fake), \
                    mock.patch.object(smoke, "assert_team_gate"), \
                    mock.patch.object(smoke, "mark_pmo_ready"), \
                    mock.patch.object(smoke, "codex_skills", side_effect=skills):
                with self.assertRaisesRegex(smoke.SmokeFailure, "exposed internal"):
                    smoke.smoke_codex(REPO, [TEAM])

    def test_team_names_excludes_operations_backbone(self):
        self.assertEqual(smoke.team_names(REPO), [TEAM])

    def test_assert_enabled_rejects_missing_claude_dependency(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, smoke.PMO):
            smoke.assert_enabled([
                {"id": f"{TEAM}@market", "enabled": True},
            ], {TEAM, smoke.PMO}, "claude")

    def test_assert_enabled_rejects_missing_codex_team(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, TEAM):
            smoke.assert_enabled({
                "installed": [{"name": smoke.PMO, "enabled": True}],
            }, {TEAM, smoke.PMO}, "codex")

    def test_codex_install_path_falls_back_to_source_path(self):
        inventory = {"installed": [{
            "name": TEAM,
            "enabled": True,
            "source": {"path": "/tmp/simulated-team"},
        }]}
        self.assertEqual(
            smoke.plugin_install_path(inventory, TEAM, "codex"),
            Path("/tmp/simulated-team"),
        )

    def test_install_path_rejects_inventory_without_location(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, "no install path"):
            smoke.plugin_install_path({
                "installed": [{"name": TEAM, "enabled": True}],
            }, TEAM, "codex")

    def test_run_surfaces_command_stdout_and_stderr(self):
        completed = SimpleNamespace(
            returncode=7, stdout="partial output", stderr="host failure"
        )
        with mock.patch.object(smoke.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(smoke.SmokeFailure, "host failure") as ctx:
                smoke.run(["codex", "plugin", "list"], {})
        self.assertIn("partial output", str(ctx.exception))

    def test_require_cli_rejects_missing_binary(self):
        with mock.patch.object(smoke.shutil, "which", return_value=None):
            with self.assertRaisesRegex(smoke.SmokeFailure, "codex"):
                smoke.require_cli("codex")

    def test_hook_payload_carries_session_project_and_tool_shape(self):
        payload = json.loads(smoke.hook_payload(
            "session-one", Path("/tmp/project"), "PreToolUse", "Write"
        ))
        self.assertEqual(payload["session_id"], "session-one")
        self.assertEqual(payload["permission_mode"], "default")
        self.assertEqual(payload["tool_name"], "Write")
        self.assertTrue(payload["tool_input"]["file_path"].endswith(
            "/tmp/project/smoke-change.txt"))


if __name__ == "__main__":
    unittest.main()
