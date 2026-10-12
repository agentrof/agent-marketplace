"""Native idle-sleep assertions release their resources on every exit path."""
from __future__ import annotations

import ctypes
import io
from pathlib import Path
import platform
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platforms/shared/software-engineering-team/overlay/scripts"))
import host_activity


class HostActivityTests(unittest.TestCase):
    def test_assertions_release_on_exception_and_preserve_windows_state(self):
        release = mock.Mock(return_value=True)
        with mock.patch.object(host_activity.platform, "system", return_value="Darwin"), \
                mock.patch.object(host_activity, "macos_assertion", return_value=release):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with host_activity.keep_awake() as state:
                    self.assertTrue(state["held"])
                    raise RuntimeError("interrupted")
            release.assert_called_once_with()
            self.assertTrue(state["released"])

        api = mock.Mock()
        api.SetThreadExecutionState.side_effect = [0x80000002, 0x80000001]
        with mock.patch.object(host_activity.platform, "system", return_value="Windows"), \
                mock.patch.object(host_activity, "windows_api", return_value=api):
            with host_activity.keep_awake() as state:
                self.assertTrue(state["held"])
            self.assertEqual(api.SetThreadExecutionState.call_args_list,
                             [mock.call(0x80000001), mock.call(0x80000002)])
            self.assertTrue(state["released"])
            api.SetThreadExecutionState.side_effect = None
            api.SetThreadExecutionState.return_value = 0
            with host_activity.keep_awake() as failed:
                self.assertFalse(failed["held"])
                self.assertIn("refused", failed["reason"])

    def test_linux_requires_ready_ack_and_cleans_up_failed_helpers(self):
        for ready, running in ((b"1", True), (b"", True), (b"1", False)):
            with self.subTest(ready=ready, running=running):
                process = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(ready))
                process.poll.return_value = None if running else 1
                with mock.patch.object(host_activity.platform, "system", return_value="Linux"), \
                        mock.patch.object(host_activity.shutil, "which", return_value="/usr/bin/systemd-inhibit"), \
                        mock.patch.object(host_activity.subprocess, "Popen", return_value=process) as start, \
                        mock.patch.object(host_activity.select, "select", return_value=([process.stdout], [], [])):
                    with host_activity.keep_awake() as state:
                        self.assertEqual(state["held"], ready == b"1")
                    self.assertEqual(state["held"], ready == b"1" and running)
                    self.assertTrue(process.stdin.closed)
                    self.assertTrue(process.stdout.closed)
                    process.wait.assert_called_once_with(timeout=3)
                    self.assertNotIn("shell", start.call_args.kwargs)
                    self.assertIn("--what=idle:sleep", start.call_args.args[0])
        with mock.patch.object(host_activity.platform, "system", return_value="Linux"), \
                mock.patch.object(host_activity.shutil, "which", return_value=None):
            with host_activity.keep_awake() as state:
                self.assertFalse(state["held"])
                self.assertIn("unavailable", state["reason"])

    def test_clocks_use_suspend_excluding_and_including_counters_on_each_os(self):
        with mock.patch.object(host_activity.time, "time", return_value=6000), \
                mock.patch.object(host_activity.time, "monotonic", return_value=2000):
            with mock.patch.object(host_activity.platform, "system", return_value="Darwin"), \
                    mock.patch.object(host_activity.time, "CLOCK_UPTIME_RAW", 99, create=True), \
                    mock.patch.object(host_activity.time, "CLOCK_MONOTONIC_RAW", 100, create=True), \
                    mock.patch.object(host_activity.time, "clock_gettime", create=True, side_effect=[2000, 6000]) as read:
                self.assertEqual(host_activity.clock_sample(), (6000, 2000, 6000))
                self.assertEqual(read.call_args_list, [mock.call(99), mock.call(100)])
            with mock.patch.object(host_activity.platform, "system", return_value="Linux"), \
                    mock.patch.object(host_activity.time, "CLOCK_BOOTTIME", 101, create=True), \
                    mock.patch.object(host_activity.time, "clock_gettime", create=True, return_value=6000):
                self.assertEqual(host_activity.clock_sample(), (6000, 2000, 6000))
            api = mock.Mock()
            def awake(pointer):
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint64))[0] = 123 * 10_000_000
                return 1
            api.QueryUnbiasedInterruptTime.side_effect = awake
            with mock.patch.object(host_activity.platform, "system", return_value="Windows"), \
                    mock.patch.object(host_activity, "windows_api", return_value=api):
                self.assertEqual(host_activity.clock_sample(), (6000, 123, 2000))
                api.QueryUnbiasedInterruptTime.side_effect = OSError("unavailable")
                self.assertEqual(host_activity.clock_sample(), (6000, None, None))

    def test_native_desktop_assertion_and_clocks(self):
        if platform.system() not in {"Darwin", "Windows"}:
            return  # Linux CI may have no logind; its protocol is tested with an acknowledged helper above.
        with host_activity.keep_awake() as state:
            self.assertTrue(state["held"], state)
            wall, awake, elapsed = host_activity.clock_sample()
            self.assertTrue(all(isinstance(value, float) and value > 0 for value in (wall, awake, elapsed)))
        self.assertTrue(state["released"])


if __name__ == "__main__":
    unittest.main()
