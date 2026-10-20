"""The workspace guard refuses a new top-level folder the config does not allow.

The hook runs through the launcher as hooks.json runs it.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.tests import python_entry

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
HOOK = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/workspace_guard.py"
LAUNCHER = ROOT / "platforms/shared/_team/overlay/scripts/hook_launcher.py"


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "workspace/config.json"
        (self.root / "workspace/docs/legacy").mkdir(parents=True)
        self.config.write_text("{}\n", encoding="utf-8")

    def write(self, path: str, tool: str = "Write"):
        payload = {"tool_name": tool, "cwd": str(self.root), "tool_input": {"file_path": path}}
        if tool == "apply_patch":
            payload["tool_input"] = {"command": f"*** Begin Patch\n*** Add File: {path}\n+x\n*** End Patch"}
        env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
        return python_entry.run([LAUNCHER, HOOK], input=json.dumps(payload), env=env)

    def test_allowed_and_existing_folders_pass_and_new_ones_are_refused(self):
        for path in ("workspace/project/crm/a.json", "workspace/docs/operation/x.md",
                     "workspace/docs/legacy/y.md", "workspace/notes.md", "other/z.md"):
            self.assertEqual(self.write(path).returncode, 0, path)
        for path, tool in (("workspace/catalog/a.json", "Write"),
                           ("workspace/docs/requirements/r.md", "apply_patch")):
            refused = self.write(path, tool)
            self.assertEqual(refused.returncode, 2, path)
            self.assertIn("workspace/project/<name>/", refused.stderr)

    def test_the_config_allowlist_decides_and_an_unreadable_config_allows(self):
        self.config.write_text(json.dumps({"workspace_layout": {
            "workspace": ["docs", "catalog"], "docs": []}}), encoding="utf-8")
        self.assertEqual(self.write("workspace/catalog/a.json").returncode, 0)
        self.assertEqual(self.write("workspace/apps/web/a.ts").returncode, 2)
        self.config.write_text("{not json", encoding="utf-8")
        allowed = self.write("workspace/catalog2/a.json")
        self.assertEqual(allowed.returncode, 0)
        self.assertIn("write allowed", allowed.stderr)


if __name__ == "__main__":
    unittest.main()
