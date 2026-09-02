"""Deterministic contracts behind the real-host installation smoke."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tools import build_distributions
from tools import smoke_plugin_installs as smoke


ROOT = Path(__file__).resolve().parents[2]


def write_claude_runtime_marker(
    root: Path, pid: int = 123, proc_start: str = "Tue Sep  1 22:49:42 2026",
) -> Path:
    directory = root / smoke.CLAUDE_IN_USE_ROOT
    directory.mkdir(exist_ok=True)
    marker = directory / str(pid)
    marker.write_text(
        json.dumps(
            {"pid": pid, "procStart": proc_start}, separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return marker


class HostSmokeContracts(unittest.TestCase):
    def test_checkout_is_channel_closed_and_contains_one_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = smoke.checkout_marketplace(ROOT, Path(temporary) / "catalog")
            self.assertTrue((target / ".claude-plugin/marketplace.json").is_file())
            self.assertTrue((target / ".agents/plugins/marketplace.json").is_file())
            for host in build_distributions.HOSTS:
                packages = sorted(path.name for path in (target / "dist" / host).iterdir()
                                  if path.is_dir())
                self.assertEqual(packages, [smoke.TEAM])

    def test_native_packages_execute_fresh_setup(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "project"
                smoke.init_project(project, os.environ.copy())
                smoke.exercise_package(
                    ROOT / "dist" / host / smoke.TEAM,
                    project,
                    os.environ.copy(),
                )
    def test_installed_package_smoke_requires_product_and_delivery_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            for relative in (
                "scripts/setup_project.py", "scripts/setup_check.py",
                "scripts/requirement_route.py", "scripts/backlog_compile.py",
                "scripts/stage_package.py", "scripts/ba_compile.py",
                "scripts/landscape_check.py", "scripts/design_system_compile.py",
                "scripts/experience_compile.py",
                "scripts/experience_application_check.py",
                "scripts/architecture_compile.py",
                "scripts/delivery_compile.py", "scripts/delivery_git.py",
                "scripts/delivery_provider.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(smoke.SmokeFailure, "delivery_compile.py"):
                (root / "scripts/delivery_compile.py").unlink()
                smoke.exercise_package(root, Path(temporary) / "project", os.environ.copy())

    def test_package_execution_disables_local_bytecode_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            for relative in (
                "scripts/setup_project.py", "scripts/setup_check.py",
                "scripts/requirement_route.py", "scripts/backlog_compile.py",
                "scripts/stage_package.py", "scripts/ba_compile.py",
                "scripts/landscape_check.py", "scripts/design_system_compile.py",
                "scripts/experience_compile.py",
                "scripts/experience_application_check.py",
                "scripts/architecture_compile.py", "scripts/delivery_compile.py",
                "scripts/delivery_git.py", "scripts/delivery_provider.py",
                "skill-content/experience-modeling/data/experience-schema.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with mock.patch.object(
                smoke, "run", side_effect=smoke.SmokeFailure("stop after env capture")
            ) as runner, self.assertRaises(smoke.SmokeFailure):
                smoke.exercise_package(
                    root, Path(temporary) / "project", os.environ.copy()
                )
            self.assertEqual(
                runner.call_args.args[1]["PYTHONDONTWRITEBYTECODE"], "1"
            )

    def test_real_host_smokes_disable_bytecode_for_every_command(self):
        installed = Path("/installed/software-engineering-team")
        expected_skills = {
            f"{smoke.TEAM}:{path.parent.name}"
            for path in (
                ROOT / "plugins" / smoke.TEAM / "skill-content"
            ).glob("*/SKILL.md")
            if "exposure: entry" in path.read_text(encoding="utf-8")
        }
        with mock.patch.object(smoke, "require_cli"), \
                mock.patch.object(smoke, "init_project"), \
                mock.patch.object(smoke, "run", return_value="{}") as runner, \
                mock.patch.object(smoke, "installed_root", return_value=installed), \
                mock.patch.object(smoke, "exercise_package"), \
                mock.patch.object(
                    smoke, "codex_skill_names", return_value=expected_skills
                ):
            smoke.smoke_claude("agentrof/agent-marketplace@stable")
            smoke.smoke_codex(
                ROOT, "agentrof/agent-marketplace@stable"
            )
        self.assertTrue(runner.call_args_list)
        self.assertTrue(all(
            call.args[1].get("PYTHONDONTWRITEBYTECODE") == "1"
            for call in runner.call_args_list
        ))

    def test_real_host_workflow_runs_the_install_smoke(self):
        workflow = (ROOT / ".github/workflows/release-hosts.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", workflow)
        self.assertIn("tools/smoke_plugin_installs.py --channel checkout", workflow)

    def test_installed_package_must_match_candidate_provenance_and_hashes(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                expected = ROOT / "dist" / host / smoke.TEAM
                installed = Path(temporary) / smoke.TEAM
                shutil.copytree(expected, installed)
                smoke.verify_installed_package(installed, expected, host)
                (installed / "constitution.md").write_text(
                    "tampered package\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(smoke.SmokeFailure, "hash differs"):
                    smoke.verify_installed_package(installed, expected, host)

    def test_installed_package_must_match_attested_executable_modes(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                expected = ROOT / "dist" / host / smoke.TEAM
                installed = Path(temporary) / smoke.TEAM
                shutil.copytree(expected, installed)
                script = installed / "scripts/backlog_compile.py"
                if not build_distributions.is_executable(script):
                    self.skipTest("fixture filesystem has no executable mode")
                script.chmod(script.stat().st_mode & ~0o111)
                with self.assertRaisesRegex(smoke.SmokeFailure, "mode differs"):
                    smoke.verify_installed_package(installed, expected, host)

    def test_installed_package_rejects_unattested_extra_entries(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                expected = ROOT / "dist" / host / smoke.TEAM
                installed = Path(temporary) / smoke.TEAM
                shutil.copytree(expected, installed)
                rogue = installed / "agents/rogue-release-agent.md"
                rogue.write_text("unattested\n", encoding="utf-8")
                with self.assertRaisesRegex(smoke.SmokeFailure, "tree differs"):
                    smoke.verify_installed_package(installed, expected, host)

    def test_claude_install_accepts_only_attested_runtime_markers(self):
        expected = ROOT / "dist" / "claude" / smoke.TEAM
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / smoke.TEAM
            shutil.copytree(expected, installed)
            (installed / smoke.CLAUDE_IN_USE_ROOT).mkdir()
            smoke.verify_installed_package(installed, expected, "claude")
            write_claude_runtime_marker(installed, 123)
            write_claude_runtime_marker(installed, 456, "process-start-token")
            smoke.verify_installed_package(installed, expected, "claude")

    def test_runtime_marker_namespace_requires_exact_claude_contract(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                expected_source = ROOT / "dist" / host / smoke.TEAM
                expected = Path(temporary) / "expected"
                installed = Path(temporary) / "installed"
                shutil.copytree(expected_source, expected)
                shutil.copytree(expected_source, installed)
                if host == "claude":
                    for root in (expected, installed):
                        provenance = root / build_distributions.PROVENANCE
                        payload = json.loads(provenance.read_text(encoding="utf-8"))
                        payload["runtime_contracts"].append("unknown_contract_v1")
                        provenance.write_text(
                            json.dumps(payload, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                write_claude_runtime_marker(installed)
                with self.assertRaisesRegex(smoke.SmokeFailure, "tree differs"):
                    smoke.verify_installed_package(installed, expected, host)

    def test_claude_runtime_marker_contract_rejects_malformed_entries(self):
        invalid_markers = {
            "nondigit name": ("worker", b'{"pid":123,"procStart":"start"}'),
            "leading zero": ("0123", b'{"pid":123,"procStart":"start"}'),
            "pid mismatch": ("123", b'{"pid":456,"procStart":"start"}'),
            "boolean pid": ("1", b'{"pid":true,"procStart":"start"}'),
            "pid overflow": (
                "4294967296", b'{"pid":4294967296,"procStart":"start"}'
            ),
            "missing field": ("123", b'{"pid":123}'),
            "extra field": (
                "123", b'{"pid":123,"procStart":"start","extra":true}'
            ),
            "duplicate field": (
                "123", b'{"pid":123,"pid":123,"procStart":"start"}'
            ),
            "invalid json": ("123", b"{"),
            "empty start": ("123", b'{"pid":123,"procStart":""}'),
            "control start": ("123", b'{"pid":123,"procStart":"line\\n"}'),
            "surrogate start": ("123", b'{"pid":123,"procStart":"\\ud800"}'),
            "long start": (
                "123",
                b'{"pid":123,"procStart":"' + (b"x" * 257) + b'"}',
            ),
            "oversized marker": ("123", b"x" * 513),
        }
        provenance = {"runtime_contracts": smoke.CLAUDE_RUNTIME_CONTRACTS}
        for label, (name, content) in invalid_markers.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                installed = Path(temporary)
                directory = installed / smoke.CLAUDE_IN_USE_ROOT
                directory.mkdir()
                (directory / name).write_bytes(content)
                inventory = smoke.package_inventory(installed, "claude")
                with self.assertRaises(smoke.SmokeFailure):
                    smoke.claude_runtime_inventory_entries(
                        installed, inventory, provenance, "claude",
                    )

    def test_claude_runtime_marker_contract_rejects_unsafe_tree_shapes(self):
        provenance = {"runtime_contracts": smoke.CLAUDE_RUNTIME_CONTRACTS}
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            nested = installed / smoke.CLAUDE_IN_USE_ROOT / "123"
            nested.mkdir(parents=True)
            (nested / "payload").write_text("rogue", encoding="utf-8")
            inventory = smoke.package_inventory(installed, "claude")
            with self.assertRaisesRegex(smoke.SmokeFailure, "runtime entry"):
                smoke.claude_runtime_inventory_entries(
                    installed, inventory, provenance, "claude",
                )
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            (installed / smoke.CLAUDE_IN_USE_ROOT).write_text(
                "not a directory", encoding="utf-8"
            )
            inventory = smoke.package_inventory(installed, "claude")
            with self.assertRaisesRegex(smoke.SmokeFailure, "runtime directory"):
                smoke.claude_runtime_inventory_entries(
                    installed, inventory, provenance, "claude",
                )
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            directory = installed / smoke.CLAUDE_IN_USE_ROOT
            directory.mkdir()
            target = installed / "target"
            target.write_text("{}", encoding="utf-8")
            try:
                (directory / "123").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"fixture filesystem cannot create symlinks: {exc}")
            with self.assertRaisesRegex(smoke.SmokeFailure, "contains a link"):
                smoke.package_inventory(installed, "claude")
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            marker = write_claude_runtime_marker(installed)
            marker.chmod(marker.stat().st_mode | stat.S_IXUSR)
            if not marker.stat().st_mode & 0o111:
                self.skipTest("fixture filesystem has no executable mode")
            inventory = smoke.package_inventory(installed, "claude")
            with self.assertRaisesRegex(smoke.SmokeFailure, "runtime marker"):
                smoke.claude_runtime_inventory_entries(
                    installed, inventory, provenance, "claude",
                )

    def test_codex_upgrade_rechecks_the_candidate_package_not_skill_names(self):
        expected_package = ROOT / "dist" / "codex" / smoke.TEAM
        expected_skills = {
            f"{smoke.TEAM}:{path.parent.name}"
            for path in (
                ROOT / "plugins" / smoke.TEAM / "skill-content"
            ).glob("*/SKILL.md")
            if "exposure: entry" in path.read_text(encoding="utf-8")
        }
        installed = [Path("/installed/first"), Path("/installed/updated")]
        with mock.patch.object(smoke, "require_cli"), \
                mock.patch.object(smoke, "run", return_value="{}"), \
                mock.patch.object(smoke, "installed_root", side_effect=installed), \
                mock.patch.object(smoke, "exercise_package"), \
                mock.patch.object(
                    smoke, "codex_skill_names", return_value=expected_skills
                ), \
                mock.patch.object(smoke, "verify_installed_package") as verify:
            smoke.smoke_codex(ROOT, "agentrof/agent-marketplace@stable", expected_package)

        self.assertEqual(
            verify.call_args_list,
            [
                mock.call(installed[0], expected_package, "codex"),
                mock.call(installed[1], expected_package, "codex"),
            ],
        )

    def test_public_smoke_runs_both_hosts_and_retries_from_fresh_calls(self):
        expected_sha = "a" * 40
        failure = smoke.SmokeFailure("transient host failure")
        with mock.patch.object(smoke, "require_public_stable") as stable, \
                mock.patch.object(
                    smoke, "smoke_claude", side_effect=[failure, None]
                ) as claude, \
                mock.patch.object(smoke, "smoke_codex") as codex, \
                mock.patch.object(smoke.time, "sleep") as sleep:
            smoke.smoke_public(
                ROOT,
                {"claude", "codex"},
                expected_sha,
                attempts=2,
                retry_delay=0,
            )
        self.assertEqual(claude.call_count, 2)
        codex.assert_called_once()
        self.assertGreaterEqual(stable.call_count, 4)
        sleep.assert_called_once_with(0)

    def test_public_smoke_rejects_wrong_or_missing_exact_sha(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, "40-hex"):
            smoke.smoke_public(ROOT, {"claude", "codex"}, "abc")

    def test_public_smoke_adapter_policy_always_comes_from_trusted_verifier(self):
        expected_sha = "a" * 40
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(
                    smoke.build_distributions,
                    "load_adapters",
                    return_value={"claude": object(), "codex": object()},
                ) as load_adapters, \
                mock.patch.object(smoke, "require_public_stable"), \
                mock.patch.object(smoke, "smoke_claude"), \
                mock.patch.object(smoke, "smoke_codex"):
            smoke.smoke_public(
                Path(temporary), {"claude", "codex"}, expected_sha,
                attempts=1, retry_delay=0,
            )
        load_adapters.assert_called_once_with(smoke.VERIFIER_ROOT)

    def test_public_cli_selects_all_hosts_from_trusted_verifier(self):
        expected_sha = "a" * 40
        trusted_adapters = {"claude": object(), "codex": object()}
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(
                    smoke.build_distributions,
                    "load_adapters",
                    return_value=trusted_adapters,
                ) as load_adapters, \
                mock.patch.object(smoke, "smoke_public") as public, \
                mock.patch.object(sys, "argv", [
                    "smoke_plugin_installs.py",
                    "--channel", "public",
                    "--root", temporary,
                    "--expected-sha", expected_sha,
                ]):
            self.assertEqual(smoke.main(), 0)
        self.assertTrue(load_adapters.call_args_list)
        self.assertTrue(all(
            call == mock.call(smoke.VERIFIER_ROOT)
            for call in load_adapters.call_args_list
        ))
        public.assert_called_once_with(
            Path(temporary).resolve(),
            {"claude", "codex"},
            expected_sha,
            attempts=3,
            retry_delay=5,
        )


if __name__ == "__main__":
    unittest.main()
