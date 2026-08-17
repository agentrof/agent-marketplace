"""Project config keeps preparation and future delivery contracts coherent."""

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

    def set_field(self, config: Path, field: str, value: object, *extra: str):
        encoded = value if isinstance(value, str) else json.dumps(value)
        return self.run_script(
            CONFIG, "set", "--config", str(config), "--field", field,
            "--value", encoded, *extra,
        )

    def test_delivery_fields_are_optional_validated_and_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config(project)
            values = {
                "backend_stack": "python-fastapi",
                "frontend_stack": "react-typescript",
                "environment_stack": "docker-compose",
                "databases": ["sql", "nosql"],
                "test_command": "make test",
                "mutation_command": "make mutation",
                "env_command": "./tools/env",
                "source_dirs": ["workspace/apps/api", "workspace/apps/web"],
                "max_parallel": 3,
            }
            for field, value in values.items():
                result = self.set_field(config, field, value)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            checked = self.run_script(CONFIG, "check", "--config", str(config))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            rerun = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
            current = json.loads(config.read_text(encoding="utf-8"))
            for field, value in values.items():
                self.assertEqual(current[field], value)

    def test_invalid_write_is_rejected_without_changing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.setup_config(Path(temporary))
            before = config.read_bytes()
            invalid = self.set_field(config, "source_dirs", ["../outside"])
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("repository-relative", invalid.stderr)
            self.assertEqual(config.read_bytes(), before)
            unknown_limit = self.set_field(config, "limits", {"unknown": 1})
            self.assertEqual(unknown_limit.returncode, 1)
            self.assertIn("unknown key", unknown_limit.stderr)
            self.assertEqual(config.read_bytes(), before)

    def test_limits_are_active_and_dry_run_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.setup_config(Path(temporary))
            before = config.read_bytes()
            preview = self.set_field(
                config, "limits",
                {"nesting_warn_depth": 4, "nesting_fail_depth": 7,
                 "nav_peer_min": 0, "nav_peer_max": 8},
                "--dry-run", "--json",
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["dry_run"])
            self.assertEqual(config.read_bytes(), before)
            inverted = self.set_field(
                config, "limits",
                {"nesting_warn_depth": 7, "nesting_fail_depth": 4},
            )
            self.assertEqual(inverted.returncode, 1)
            self.assertIn("must be lower", inverted.stderr)
            one_sided = self.set_field(
                config, "limits", {"nesting_warn_depth": 999},
            )
            self.assertEqual(one_sided.returncode, 1)
            self.assertIn("effective nesting_warn_depth", one_sided.stderr)
            nav_inverted = self.set_field(
                config, "limits", {"nav_peer_min": 999},
            )
            self.assertEqual(nav_inverted.returncode, 1)
            self.assertIn("effective nav_peer_min", nav_inverted.stderr)

    def test_documented_delivery_fields_have_a_writer_and_consumers(self):
        fields = {
            "backend_stack", "frontend_stack", "environment_stack",
            "databases", "test_command", "mutation_command", "env_command",
            "source_dirs", "max_parallel", "limits",
        }
        help_result = self.run_script(CONFIG, "set", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        contract = (
            ROOT / "plugins/software-engineering-team/skill-content/configure"
            / "references/config-contract.md"
        ).read_text(encoding="utf-8")
        for field in fields:
            self.assertIn(field, help_result.stdout)
            self.assertIn(f"`{field}`", contract)
        self.assertNotIn("`model_overrides`", contract)
        consumers = {
            "limits": ["scripts/ba_compile.py", "scripts/experience_compile.py",
                       "scripts/vault_check.py"],
            "test_command": ["skill-content/qa-verification/SKILL.md"],
            "mutation_command": ["skill-content/qa-verification/SKILL.md"],
            "env_command": ["skill-content/docker-compose/SKILL.md"],
        }
        plugin = ROOT / "plugins/software-engineering-team"
        for field, relatives in consumers.items():
            for relative in relatives:
                self.assertIn(
                    field, (plugin / relative).read_text(encoding="utf-8"),
                    f"{field} lost its declared consumer {relative}",
                )

    def test_origin_writer_is_retired_and_legacy_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config(project)
            retired = self.run_script(
                CONFIG, "set-origin", "--config", str(config),
                "--origin", "existing",
            )
            self.assertEqual(retired.returncode, 2)
            legacy = json.loads(config.read_text(encoding="utf-8"))
            legacy["project_origin"] = "existing"
            config.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
            checked = self.run_script(CONFIG, "check", "--config", str(config))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("retired", checked.stdout)


if __name__ == "__main__":
    unittest.main()
