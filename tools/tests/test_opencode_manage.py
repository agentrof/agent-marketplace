"""Focused safety checks for OpenCode runtime binding."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MANAGE_PATH = (
    ROOT
    / "platforms/opencode/software-engineering-team/overlay/scripts/opencode_manage.py"
)
SPEC = importlib.util.spec_from_file_location("opencode_manage", MANAGE_PATH)
assert SPEC is not None and SPEC.loader is not None
MANAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANAGE)


class OpenCodeManageTests(unittest.TestCase):
    def test_timed_out_opencode_command_terminates_its_process_tree(self):
        class Process:
            pid = 123

            def communicate(self, timeout):
                raise subprocess.TimeoutExpired(["opencode"], timeout)

        process = Process()
        with mock.patch.object(MANAGE.subprocess, "Popen", return_value=process):
            with mock.patch.object(MANAGE, "terminate_process_tree") as terminate:
                with self.assertRaisesRegex(RuntimeError, "runtime_unbound"):
                    MANAGE.run_opencode(["opencode", "debug", "config"], ROOT)

        terminate.assert_called_once_with(process)

    def test_effective_config_retries_a_transient_runtime_failure_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            plugin = project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("export {};\n", encoding="utf-8")
            calls = 0

            timeouts = []

            def fake_run(argv, _project, *, stdout=None, timeout=None):
                nonlocal calls
                calls += 1
                timeouts.append(timeout)
                if calls == 1:
                    raise RuntimeError("runtime_unbound")
                assert stdout is not None
                stdout.write(json.dumps({"plugin": [str(plugin)]}).encode())
                return subprocess.CompletedProcess(argv, 0, None, "")

            with mock.patch.object(MANAGE, "run_opencode", side_effect=fake_run):
                fingerprint, plugins = MANAGE.effective_config("opencode", project)

        self.assertEqual(calls, 2)
        self.assertEqual(timeouts, [MANAGE.OPENCODE_CONFIG_TIMEOUT] * 2)
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(plugins, [plugin.resolve()])

    def test_effective_config_does_not_retry_an_invalid_config_contract(self):
        calls = 0

        def fake_run(argv, _project, *, stdout=None, timeout=None):
            nonlocal calls
            del timeout
            calls += 1
            assert stdout is not None
            stdout.write(b"[]")
            return subprocess.CompletedProcess(argv, 0, None, "")

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(MANAGE, "run_opencode", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "hook_contract_incompatible"):
                    MANAGE.effective_config("opencode", Path(temporary))

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
