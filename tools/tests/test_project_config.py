"""Closed bootstrap config and convergent setup migration tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
SETUP = SCRIPTS / "setup_project.py"
CONFIG = SCRIPTS / "project_config.py"

CANONICAL_KEYS = {
    "schema_version", "team_id", "output_language", "terminology_language",
    "doc_type_designations", "doc_type_designation_history",
}


class ProjectConfigTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )

    def setup_config(self, project: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        result = self.run_script(SETUP, "--project-root", str(project))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return project / "workspace" / "config.json"

    def test_fresh_setup_writes_only_the_closed_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.setup_config(Path(temporary))
            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(set(value), CANONICAL_KEYS)
            self.assertEqual(value["schema_version"], 1)
            self.assertEqual(value["team_id"], "software-engineering-team")
            checked = self.run_script(CONFIG, "check", "--config", str(config))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_only_language_fields_have_a_config_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.setup_config(Path(temporary))
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

    def test_check_rejects_retired_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.setup_config(Path(temporary))
            value = json.loads(config.read_text(encoding="utf-8"))
            value["scale"] = "small"
            value["unknown"] = True
            config.write_text(json.dumps(value), encoding="utf-8")
            checked = self.run_script(CONFIG, "check", "--config", str(config))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("unknown or retired field: scale", checked.stdout)
            self.assertIn("unknown or retired field: unknown", checked.stdout)

    def test_setup_replaces_legacy_config_and_preserves_allowed_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            workspace = project / "workspace"
            workspace.mkdir()
            legacy = {
                "team_id": "software-engineering-team",
                "output_language": "Turkish",
                "terminology_language": "English",
                "doc_type_designations": {"story": "Story"},
                "doc_type_designation_history": {"story": ["Work item"]},
                "backend_stack": "python-fastapi", "test_command": "make test",
                "mutation_command": "make mutation", "env_command": "./tools/env",
                "max_parallel": 2, "scale": "small", "unknown": "discard",
            }
            (workspace / "config.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            inspected = self.run_script(SETUP, "inspect", "--project-root", str(project), "--json")
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            plan = json.loads(inspected.stdout)
            config_op = next(item for item in plan["operations"] if item["surface"] == "workspace_config")
            self.assertEqual(config_op["action"], "replace")
            self.assertIn("backend_stack", config_op["removed_fields"])
            self.assertIn("unknown", config_op["removed_fields"])
            applied = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            current = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(set(current), CANONICAL_KEYS)
            self.assertEqual(current["output_language"], "Turkish")
            self.assertEqual(current["doc_type_designations"]["story"], "Story")
            self.assertIn("verification-contract", current["doc_type_designations"])
            docs = workspace / "docs"
            self.assertTrue((docs / "operation" / "verification-contract.md").is_file())
            self.assertTrue((docs / "operation" / "environment-contract.md").is_file())
            self.assertTrue((docs / "delivery" / "governance" / "governance.md").is_file())
            rerun = self.run_script(SETUP, "inspect", "--project-root", str(project), "--json")
            self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
            self.assertEqual(json.loads(rerun.stdout)["operations"], [])

    def test_future_schema_is_never_downgraded(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            workspace = project / "workspace"
            workspace.mkdir()
            (workspace / "config.json").write_text(json.dumps({
                "schema_version": 99, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
                "doc_type_designations": {}, "doc_type_designation_history": {},
            }), encoding="utf-8")
            result = self.run_script(SETUP, "inspect", "--project-root", str(project))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("future schema_version", result.stderr)

    def test_setup_moves_a_recognized_legacy_environment_contract_transactionally(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            workspace = project / "workspace"
            (workspace / "environment").mkdir(parents=True)
            (workspace / "config.json").write_text(json.dumps({
                "team_id": "software-engineering-team",
            }), encoding="utf-8")
            legacy = workspace / "environment" / "contract.md"
            legacy.write_text(
                "---\ntype: environment-contract\nstatus: draft\nrevision: 1\n"
                "env_command: ./tools/env\nenv_workdir: .\nscenarios:\n  - default\n"
                "tolerated_warnings:\nservice_catalog:\n---\n\n# Environment\n",
                encoding="utf-8",
            )
            inspected = self.run_script(SETUP, "inspect", "--project-root", str(project), "--json")
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            operations = json.loads(inspected.stdout)["operations"]
            self.assertTrue(any(item["action"] == "delete" and item["path"] == "workspace/environment/contract.md"
                                for item in operations))
            applied = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            canonical = workspace / "docs" / "operation" / "environment-contract.md"
            self.assertTrue(canonical.is_file())
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
