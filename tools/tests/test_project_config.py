"""Closed project config: layout, field and writer rules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests import python_entry


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
CONFIG = SCRIPTS / "project_config.py"


class ProjectConfigTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str):
        return python_entry.run([script, *args], cwd=ROOT)

    def unit_config(self, project):
        config = project / "workspace/config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team",
                                      "output_language": "English", "terminology_language": "English"}),
                          encoding="utf-8")
        return config

    def test_layout_rejects_nested_names_and_tests_outside_the_allowlist(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        config = {"tests_root": "workspace/qa",
                  "workspace_layout": {"workspace": ["docs", "apps/x"], "docs": []}}
        errors = project_config.layout_errors(config)
        self.assertIn("workspace_layout.workspace must list unique single path names", errors)
        self.assertIn("tests_root must lie in a folder workspace_layout.workspace allows", errors)
        self.assertEqual(project_config.layout_errors({}), [])

    def test_pr_proof_takes_only_its_two_values(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        self.assertNotIn("pr_proof must be one of remote_ci, local_record",
                         project_config.base_errors({"pr_proof": "local_record"}))
        self.assertIn("pr_proof must be one of remote_ci, local_record",
                      project_config.base_errors({"pr_proof": "ghost"}))

    def test_only_declared_fields_have_a_config_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.unit_config(Path(temporary))
            written = self.run_script(
                CONFIG, "set", "--config", str(config), "--field",
                "output_language", "--value", "Turkish",
            )
            self.assertEqual(written.returncode, 0, written.stdout + written.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "Turkish",
            )
            retired = self.run_script(
                CONFIG, "set", "--config", str(config), "--field", "scale",
                "--value", "small",
            )
            self.assertNotEqual(retired.returncode, 0)
            self.assertIn("invalid choice", retired.stderr)


if __name__ == "__main__":
    unittest.main()
