"""Deterministic contracts behind the real-host installation smoke."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tools import build_distributions
from tools import smoke_plugin_installs as smoke

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
    def test_codex_install_receipt_selects_cache_instead_of_marketplace_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, cached = root / "marketplace/package", root / "codex/plugins/cache/package"
            source.mkdir(parents=True)
            cached.mkdir(parents=True)
            entry = {"pluginId": f"{smoke.TEAM}@{smoke.MARKETPLACE}", "name": smoke.TEAM,
                     "marketplaceName": smoke.MARKETPLACE, "version": "1.0.0", "installed": True,
                     "enabled": True, "source": {"source": "local", "path": str(source)}}
            inventory = {"installed": [entry]}
            receipt = {key: entry[key] for key in ("pluginId", "name", "marketplaceName", "version")}
            receipt["installedPath"] = str(cached)
            self.assertEqual(smoke.installed_root(inventory, "codex", receipt), cached)
            self.assertEqual(smoke.installed_root({"installed": [{**entry, "installedPath": str(cached)}]},
                                                 "codex"), cached)
            with self.assertRaises(smoke.SmokeFailure):
                smoke.installed_root(inventory, "codex")
            for key, value in (("version", "2.0.0"), ("pluginId", "another@marketplace"),
                               ("installedPath", str(root / "missing")), ("installedPath", "relative/cache")):
                with self.subTest(key=key, value=value), self.assertRaises(smoke.SmokeFailure):
                    smoke.installed_root(inventory, "codex", {**receipt, key: value})
            for changed in ({**entry, "enabled": False}, {**entry, "installed": False},
                            {**entry, "version": None}, {**entry, "installedPath": str(source)}):
                with self.subTest(changed=changed), self.assertRaises(smoke.SmokeFailure):
                    smoke.installed_root({"installed": [changed]}, "codex", receipt)

    def test_codex_catalog_requires_enabled_entry_from_exact_installed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            installed = root / "installed"
            source = installed / "skills/deliver/SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_text("entry", encoding="utf-8")
            hooks_path = installed / "hooks/hooks.json"
            hooks_path.parent.mkdir()
            hooks_path.write_text('{"hooks": {}}', encoding="utf-8")
            name = f"{smoke.TEAM}:deliver"
            valid = {"name": name, "path": str(source), "enabled": True,
                     "pluginId": f"{smoke.TEAM}@{smoke.MARKETPLACE}"}
            cases = [
                (valid, {name}),
                ({key: value for key, value in valid.items() if key != "pluginId"}, {name}),
                ({**valid, "enabled": False}, set()),
                ({**valid, "enabled": 1}, set()),
                ({key: value for key, value in valid.items() if key != "enabled"}, set()),
                ({key: value for key, value in valid.items() if key != "path"}, set()),
                ({**valid, "path": "skills/deliver/SKILL.md"}, set()),
                ({**valid, "path": str(root / "unrelated/SKILL.md")}, set()),
                ({**valid, "pluginId": f"{smoke.TEAM}@another-marketplace"}, set()),
            ]
            for entry, expected in cases:
                with self.subTest(entry=entry):
                    responses = [
                        {"id": 1, "result": {}},
                        {"id": 2, "result": {"data": [{
                            "cwd": str(project), "skills": [entry],
                        }]}},
                        {"id": 3, "result": {"data": [{
                            "cwd": str(project), "hooks": [], "errors": [], "warnings": [],
                        }]}},
                    ]
                    process = mock.Mock(
                        stdin=io.StringIO(),
                        stdout=io.StringIO("\n".join(json.dumps(row) for row in responses)),
                    )
                    with mock.patch.object(smoke.subprocess, "Popen", return_value=process):
                        self.assertEqual(smoke.codex_skill_names({}, project, installed), expected)
                    requests = [json.loads(row) for row in process.stdin.getvalue().splitlines()]
                    self.assertEqual(requests[-1], {"method": "hooks/list", "id": 3,
                                                   "params": {"cwds": [str(project)]}})
                    process.terminate.assert_called_once()
            responses[2] = {"id": 3, "error": {"code": -32601, "message": "Method not found"}}
            process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO(
                "\n".join(json.dumps(row) for row in responses)))
            with mock.patch.object(smoke.subprocess, "Popen", return_value=process), \
                    self.assertRaisesRegex(smoke.SmokeFailure, "hooks/list is unavailable"):
                smoke.codex_skill_names({}, project, installed)
            process.terminate.assert_called_once()

    def test_codex_configured_hooks_match_source_and_normalized_identity_without_claiming_trust(self):
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary).resolve()
            source = installed / "hooks/hooks.json"
            source.parent.mkdir()
            command = 'python3 "${PLUGIN_ROOT}/scripts/observer.py"'
            source.write_text(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": command}
            ]}]}}), encoding="utf-8")
            valid = {"key": f"{smoke.TEAM}@{smoke.MARKETPLACE}:hooks/hooks.json:post_tool_use:0:0",
                "eventName": "postToolUse", "handlerType": "command",
                "command": command.replace("${PLUGIN_ROOT}", str(installed)), "matcher": "Bash",
                "timeoutSec": 600, "async": False, "statusMessage": None, "additionalContextLimit": None,
                "source": "plugin", "sourcePath": str(source), "pluginId": f"{smoke.TEAM}@{smoke.MARKETPLACE}",
                "enabled": True, "isManaged": False, "trustStatus": "untrusted",
                "currentHash": "sha256:1110d33b4a535788399bc4e27b297734a203baabf7f9c5f41c5a6c55d36d62f3"}
            entry = {"hooks": [valid], "errors": [], "warnings": []}
            smoke.verify_codex_hooks(entry, installed)
            for field, value in (("sourcePath", str(installed / "wrong/hooks.json")),
                                 ("pluginId", "another@marketplace"), ("eventName", "stop"),
                                 ("command", "echo different"), ("matcher", None), ("timeoutSec", 1),
                                 ("currentHash", "sha256:" + "0" * 64), ("enabled", False)):
                with self.subTest(field=field), self.assertRaises(smoke.SmokeFailure):
                    smoke.verify_codex_hooks({**entry, "hooks": [{**valid, field: value}]}, installed)
            for change in ({"hooks": []}, {"hooks": [valid, valid]},
                           {"errors": None}, {"errors": {}}, {"errors": [{"message": "failed"}]}):
                with self.subTest(change=change), self.assertRaises(smoke.SmokeFailure):
                    smoke.verify_codex_hooks({**entry, **change}, installed)
            with self.assertRaises(smoke.SmokeFailure):
                smoke.verify_codex_hooks({"hooks": [valid]}, installed)
            source.write_text(source.read_text(encoding="utf-8").replace('"Bash"', '"Read"'), encoding="utf-8")
            with self.assertRaises(smoke.SmokeFailure):
                smoke.verify_codex_hooks(entry, installed)

    def test_checkout_is_channel_closed_and_contains_one_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = smoke.checkout_marketplace(ROOT, Path(temporary) / "catalog")
            self.assertTrue((target / ".claude-plugin/marketplace.json").is_file())
            self.assertTrue((target / ".agents/plugins/marketplace.json").is_file())
            for host in build_distributions.HOSTS:
                packages = sorted(path.name for path in (target / "dist" / host).iterdir()
                                  if path.is_dir())
                self.assertEqual(packages, [smoke.TEAM])

    def test_installed_package_smoke_requires_setup_and_fast_lane_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            for relative in (
                "scripts/setup_project.py", "scripts/setup_check.py",
                "scripts/task_lane.py", "flows/fast-lane.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(smoke.SmokeFailure, "task_lane.py"):
                (root / "scripts/task_lane.py").unlink()
                smoke.exercise_package(root, Path(temporary) / "project", os.environ.copy())

    def test_package_execution_disables_local_bytecode_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            for relative in (
                "scripts/setup_project.py", "scripts/setup_check.py",
                "scripts/task_lane.py", "flows/fast-lane.md",
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

    def test_installed_package_must_match_candidate_provenance_and_hashes(self):
        host = "codex"
        with tempfile.TemporaryDirectory() as temporary:
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
        host = "codex"
        with tempfile.TemporaryDirectory() as temporary:
            expected = ROOT / "dist" / host / smoke.TEAM
            installed = Path(temporary) / smoke.TEAM
            shutil.copytree(expected, installed)
            script = installed / "scripts/task_lane.py"
            script.chmod(script.stat().st_mode | 0o100)
            if not build_distributions.is_executable(script):
                self.skipTest("fixture filesystem has no executable mode")
            with self.assertRaisesRegex(smoke.SmokeFailure, "mode differs"):
                smoke.verify_installed_package(installed, expected, host)

    def test_installed_package_rejects_unattested_extra_entries(self):
        host = "codex"
        with tempfile.TemporaryDirectory() as temporary:
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

    def test_claude_runtime_marker_contract_rejects_a_malformed_marker(self):
        provenance = {"runtime_contracts": smoke.CLAUDE_RUNTIME_CONTRACTS}
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            directory = installed / smoke.CLAUDE_IN_USE_ROOT
            directory.mkdir()
            # The marker's name and its pid disagree.
            (directory / "123").write_bytes(b'{"pid":456,"procStart":"start"}')
            inventory = smoke.package_inventory(installed, "claude")
            with self.assertRaises(smoke.SmokeFailure):
                smoke.claude_runtime_inventory_entries(
                    installed, inventory, provenance, "claude",
                )

    def test_claude_runtime_marker_contract_rejects_a_nested_tree(self):
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
                ) as catalog, \
                mock.patch.object(smoke, "verify_installed_package") as verify:
            smoke.smoke_codex(ROOT, "agentrof/agent-marketplace@stable", expected_package)

        self.assertEqual(
            verify.call_args_list,
            [
                mock.call(installed[0], expected_package, "codex"),
                mock.call(installed[1], expected_package, "codex"),
            ],
        )
        self.assertEqual([call.args[2] for call in catalog.call_args_list], installed)

    def test_public_smoke_retries_only_failed_host_with_fresh_sha_checks(self):
        expected_sha = "a" * 40
        failure = smoke.SmokeFailure("transient host failure")
        for failed_host in ("claude", "codex"):
            with self.subTest(failed_host=failed_host), \
                    mock.patch.object(smoke, "require_public_stable") as stable, \
                    mock.patch.object(smoke, "smoke_claude") as claude, \
                    mock.patch.object(smoke, "smoke_codex") as codex, \
                    mock.patch.object(smoke.time, "sleep") as sleep:
                failed = claude if failed_host == "claude" else codex
                passed = codex if failed_host == "claude" else claude
                failed.side_effect = [failure, None]
                smoke.smoke_public(ROOT, {"claude", "codex"}, expected_sha,
                                   attempts=2, retry_delay=0)
                self.assertEqual(failed.call_count, 2)
                passed.assert_called_once()
                self.assertEqual(stable.call_count, 5)
                self.assertTrue(all(call.args[0] == expected_sha for call in stable.call_args_list))
                sleep.assert_called_once_with(0)

        with mock.patch.object(smoke, "require_public_stable",
                               side_effect=[None, failure, None, None, None, None]), \
                mock.patch.object(smoke, "smoke_claude") as claude, \
                mock.patch.object(smoke, "smoke_codex") as codex, \
                mock.patch.object(smoke.time, "sleep"):
            smoke.smoke_public(ROOT, {"claude", "codex"}, expected_sha, attempts=2)
        self.assertEqual(claude.call_count, 2)
        codex.assert_called_once()

        with mock.patch.object(smoke, "require_public_stable"), \
                mock.patch.object(smoke, "smoke_claude") as claude, \
                mock.patch.object(smoke, "smoke_codex", side_effect=failure) as codex, \
                mock.patch.object(smoke.time, "sleep"), \
                self.assertRaisesRegex(smoke.SmokeFailure, "codex.*2 fresh attempt"):
            smoke.smoke_public(ROOT, {"claude", "codex"}, expected_sha, attempts=2)
        claude.assert_called_once()
        self.assertEqual(codex.call_count, 2)

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

    def test_checkout_cli_binds_both_hosts_to_the_exact_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "catalog"
            with mock.patch.object(smoke, "checkout_marketplace", return_value=source), \
                    mock.patch.object(smoke, "smoke_claude") as claude, \
                    mock.patch.object(smoke, "smoke_codex") as codex, \
                    mock.patch.object(sys, "argv", [
                        "smoke_plugin_installs.py", "--channel", "checkout", "--root", str(root),
                    ]):
                self.assertEqual(smoke.main(), 0)
            claude.assert_called_once_with(source, expected=root / "dist/claude" / smoke.TEAM)
            codex.assert_called_once_with(root, source, expected=root / "dist/codex" / smoke.TEAM)

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
