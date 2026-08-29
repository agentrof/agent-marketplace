from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "plugins/software-engineering-team/scripts/setup_project.py"
GATE_INSTALLER = ROOT / "plugins/software-engineering-team/scripts/vault_gate.py"


class PortableVaultGateTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / ".github/agentrof/vault-gate.pyz"

    def test_archive_has_opaque_snapshot_checker_without_ui_templates(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            listed = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertIn(listed.returncode, (0, 1), listed.stdout + listed.stderr)
            installed = subprocess.run(
                [sys.executable, str(GATE_INSTALLER), "install", "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            self.assertTrue(gate.is_file())

    def test_gate_does_not_reject_arbitrary_prototype_extensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            artifact = project / "workspace/docs/experience-design/artifacts/src/demo.tsx"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("export const demo = true\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            payload = json.loads(result.stdout)
            text = json.dumps(payload)
            self.assertNotIn("only artifacts/application.html", text)
            self.assertNotIn("forbidden by the subtree's exact artifact-path contract", text)


if __name__ == "__main__":
    unittest.main()
