"""Only the packaged autopilot.py writes the autopilot grant and arming record.

The guard hook runs through the launcher as hooks.json runs it, in process.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tools.tests import python_entry

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
HOOK = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/autopilot_guard.py"
LAUNCHER = ROOT / "platforms/shared/_team/overlay/scripts/hook_launcher.py"
AUTOPILOT_SCRIPT = ROOT / "plugins/software-engineering-team/skill-content/autopilot/scripts/autopilot.py"
RUNTIME = Path(".agentrof/agent-marketplace/.runtime/autopilot")
GRANT = {"schema_version": 1, "id": "AP-1", "host": "claude", "state": "active",
         "granted_at": "2026-10-01T21:00:00Z", "expires_at": "2026-10-01T23:00:00Z",
         "classes": ["choice"], "armed_by": {"guard": "user_prompt_hook", "session_id": "s"}}


class AutopilotGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        # Recovery capsules and provenance marks live under the temporary directory.
        for patcher in (mock.patch.dict(os.environ, {"TMPDIR": str(base)}),
                        mock.patch.object(tempfile, "tempdir", str(base))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = base / "project"
        (self.root / "workspace").mkdir(parents=True)
        (self.root / "workspace/config.json").write_text("{}\n", encoding="utf-8")
        self.runtime = self.root / RUNTIME
        self.runtime.mkdir(parents=True)
        self.session = f"guard-{uuid.uuid4().hex[:8]}"

    def run_hook(self, mode: str, payload: dict):
        env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
        return python_entry.run([LAUNCHER, HOOK, mode], input=json.dumps(payload), env=env)

    def shell_event(self, command: str, change):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.root),
                   "session_id": self.session, "tool_use_id": uuid.uuid4().hex}
        if os.name == "nt":
            payload["shell_family"] = "cmd"
        before = self.run_hook("pre", payload)
        self.assertEqual(before.returncode, 0, before.stderr)
        change()
        return self.run_hook("post", payload)

    def write(self, name: str, value: dict) -> None:
        (self.runtime / name).write_text(json.dumps(value), encoding="utf-8")

    def test_write_into_the_runtime_is_denied(self):
        result = self.run_hook("pre", {
            "tool_name": "Write", "cwd": str(self.root), "session_id": self.session,
            "tool_input": {"file_path": str(RUNTIME / "grant.json"), "content": "{}"}})
        self.assertEqual(result.returncode, 2)
        self.assertIn("only the user arms a grant", result.stderr)

    def test_a_shell_tampered_grant_and_its_arming_record_are_restored(self):
        self.write("arming.json", {"armed_at": "2026-10-01T21:00:00Z", "arguments": "on"})
        arming = (self.runtime / "arming.json").read_bytes()

        def consume_and_grant():
            (self.runtime / "arming.json").unlink()
            self.write("grant.json", GRANT)

        result = self.shell_event("python3 make_grant.py", consume_and_grant)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.runtime / "grant.json").exists())
        self.assertEqual((self.runtime / "arming.json").read_bytes(), arming)

    def test_the_packaged_autopilot_script_may_write(self):
        argv = [sys.executable, str(AUTOPILOT_SCRIPT), "--project-root", str(self.root), "status"]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        result = self.shell_event(command, lambda: self.write("grant.json", GRANT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runtime / "grant.json").exists())

    def test_deleting_the_grant_is_allowed(self):
        self.write("grant.json", GRANT)
        result = self.shell_event("rm grant.json", (self.runtime / "grant.json").unlink)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.runtime / "grant.json").exists())


if __name__ == "__main__":
    unittest.main()
