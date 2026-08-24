"""Focused behavior checks for the executable OpenCode compatibility probe."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "platforms" / "opencode" / "real_host_probe.py"
SPEC = importlib.util.spec_from_file_location("opencode_real_host_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class OpenCodeRealHostProbeTests(unittest.TestCase):
    def test_windows_tui_input_waits_for_a_rendered_command_bar(self):
        self.assertFalse(PROBE.tui_windows_ready(b"ctrl+p"))
        self.assertFalse(PROBE.tui_windows_ready(b"commands"))
        self.assertTrue(PROBE.tui_windows_ready(b"tab ctrl+p commands"))
        self.assertEqual(
            PROBE.TUI_COMMAND,
            "/issue-report Prepare a deterministic probe issue\r",
        )

    def test_environment_isolates_the_opencode_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = PROBE.environment(root)
            self.assertTrue((root / "home").is_dir())
            self.assertTrue((root / "tmp").is_dir())

        self.assertEqual(environment["OPENCODE_TEST_HOME"], str(root / "home"))
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(environment[name], str(root / "tmp"))

    def test_bind_runtime_has_a_dedicated_first_bootstrap_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            project = root / "project"
            project.mkdir()
            executable = root / "opencode"
            executable.write_text("placeholder", encoding="utf-8")
            calls: list[tuple[list[str], float]] = []
            original_command = PROBE.command

            def fake_command(argv, *, cwd, environment, timeout=45.0):
                del cwd, environment
                calls.append((argv, timeout))
                if "apply" in argv:
                    (project / ".opencode" / "agentrof" / "agent-marketplace").mkdir(
                        parents=True
                    )
                    (project / ".opencode" / "agents").mkdir(parents=True)
                return subprocess.CompletedProcess(argv, 0, "", "")

            PROBE.command = fake_command
            try:
                manage = PROBE.configure_project(package, project, executable, {})
            finally:
                PROBE.command = original_command

        self.assertEqual(manage, project / ".opencode/agentrof/agent-marketplace/manage.py")
        self.assertEqual(calls[-1][0][3], "bind-runtime")
        self.assertEqual(calls[-1][1], PROBE.CONFIGURATION_TIMEOUT)
        self.assertGreater(PROBE.CONFIGURATION_TIMEOUT, 45.0)

    def test_windows_tui_cleanup_terminates_the_process_tree(self):
        with mock.patch.object(PROBE.subprocess, "run") as run:
            PROBE.terminate_windows_process_tree(123)

        run.assert_called_once_with(
            ["taskkill", "/PID", "123", "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    def test_windows_tui_cleanup_ignores_missing_taskkill(self):
        with mock.patch.object(PROBE.subprocess, "run", side_effect=OSError):
            PROBE.terminate_windows_process_tree(123)

        PROBE.terminate_windows_process_tree(None)


if __name__ == "__main__":
    unittest.main()
