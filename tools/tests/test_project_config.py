"""Closed project config: layout, field and writer rules."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_proof_reuse_is_optional_validated_and_preserved_by_setup(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        import setup_project

        with tempfile.TemporaryDirectory() as temporary:
            config_path = self.unit_config(Path(temporary))
            base = json.loads(config_path.read_bytes())
            self.assertEqual(project_config.check(base), [])
            args = argparse.Namespace(output_language="English", terminology_language="English")
            fresh, _ = setup_project.desired_config(args, config_path)
            self.assertNotIn("proof_reuse", fresh)
            self.assertNotIn("proof_reuse", project_config.DEFAULTS)
            for fields in (project_config.CONFIG_FIELDS, project_config.ORDINARY_FIELDS,
                           project_config.PROJECT_FIELDS):
                self.assertIn("proof_reuse", fields)
            reuse = {"identity_command": ["python3", "tools/proof_identity.py"]}
            for age in (None, 1, 86400):
                value = dict(reuse)
                if age is not None:
                    value["max_age_seconds"] = age
                self.assertEqual(project_config.check({**base, "proof_reuse": value}), [])
            invalid = [None, False, [], {}, {"identity_command": "echo identity"},
                       {"identity_command": []}, {"identity_command": [" "]},
                       {"identity_command": [True]}, {**reuse, "unknown": True}]
            invalid.extend({**reuse, "max_age_seconds": age}
                           for age in (True, False, 0, 86401, 1.5, "60", None))
            for value in invalid:
                with self.subTest(value=value):
                    self.assertTrue(any("proof_reuse" in error for error in
                                        project_config.check({**base, "proof_reuse": value})))
            written = self.run_script(CONFIG, "set", "--config", str(config_path),
                                      "--field", "proof_reuse", "--value", json.dumps(reuse))
            self.assertEqual(written.returncode, 0, written.stderr)
            before = config_path.read_bytes()
            preserved, changed = setup_project.desired_config(args, config_path)
            self.assertEqual(preserved["proof_reuse"], reuse)
            self.assertNotIn("proof_reuse", changed)
            self.assertEqual(config_path.read_bytes(), before)
            rejected = self.run_script(CONFIG, "set", "--config", str(config_path),
                                       "--field", "proof_reuse", "--value", '{"identity_command":[]}')
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(config_path.read_bytes(), before)

    def test_retired_routing_and_criteria_fields_require_migration(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        for field, value in (("task_criteria_range", [3, 8]), ("role_routing", {})):
            self.assertIn(f"config contains unknown or retired field: {field}",
                          project_config.base_errors({field: value}))
        old = {"task_criteria_range": [3, 8], "role_routing": {}}
        result = project_config.migrate_config(old)
        self.assertEqual(result["config"], {})
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(old, {"task_criteria_range": [3, 8], "role_routing": {}})
        for field, value in (("task_criteria_range", [1, 2]), ("role_routing", {"custom": True})):
            source = {field: value}
            result = project_config.migrate_config(source)
            self.assertTrue(result["conflicts"])
            self.assertEqual(result["config"], source)

    def test_trigger_migration_preserves_predicates_and_refuses_collisions(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        source = {"task_triggers": {
            "method_skills": [{"skill": "sql-database-design", "path_globs": ["db/*"],
                                "exclude_globs": ["db/archive/*"], "keywords": ["ledger"]}],
            "reviewers": [{"role": "security-reviewer", "on_any_risk": True}],
        }}
        before = copy.deepcopy(source)
        result = project_config.migrate_config(source)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["config"]["task_triggers"]["method_skills"],
                         [{**source["task_triggers"]["method_skills"][0], "skill": "data-design"}])
        self.assertEqual(result["config"]["task_triggers"]["reviewers"][0]["role"], "critical-reviewer")
        self.assertEqual(source, before)
        source["task_triggers"]["method_skills"].append(
            {"skill": "nosql-database-design", "path_globs": ["documents/*"]})
        collision = project_config.migrate_config(source)
        self.assertTrue(collision["conflicts"])
        self.assertEqual(collision["config"], source)

    def test_trigger_shape_rejects_ignored_or_unavailable_runtime_rules(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config
        for value in ([], {"unknown": []}, {"reviewers": [{"role": "worker"}]},
                      {"method_skills": [{"skill": "missing"}]},
                      {"risk_flags": [{"flag": "auth", "path_globs": "*"}]},
                      {"reviewers": [{"role": "reviewer", "on_any_risk": "yes"}]}):
            with self.subTest(value=value):
                self.assertTrue(project_config.trigger_errors(value))

    def test_retired_persona_writer_keeps_config_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.unit_config(Path(temporary))
            before = config.read_bytes()
            result = self.run_script(CONFIG, "set-role-class", "--config", str(config),
                                     "--role", "reviewer", "--class", "critical")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("set-role-class is retired", result.stderr)
            self.assertEqual(config.read_bytes(), before)

    def test_reviewed_replacement_previews_then_applies_against_exact_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.unit_config(root)
            old = json.loads(config.read_text())
            old["role_routing"] = {"custom": True}
            config.write_text(json.dumps(old))
            before = config.read_bytes()
            digest = hashlib.sha256(before).hexdigest()
            replacement = root / "reviewed.json"
            proposed = {key: value for key, value in old.items() if key != "role_routing"}
            replacement.write_text(json.dumps(proposed))
            args = ("migrate", "--config", str(config), "--config-file", str(replacement),
                    "--expected-sha256", digest, "--json")
            preview = self.run_script(CONFIG, *args, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            report = json.loads(preview.stdout)
            self.assertEqual((report["before"], report["after"]), (old, proposed))
            self.assertEqual(report["dropped_keys"], ["role_routing"])
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((root / ".agentrof").exists())
            applied = self.run_script(CONFIG, *args)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(json.loads(config.read_bytes()), proposed)
            stale = self.run_script(CONFIG, *args)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("config digest changed", stale.stderr)
            self.assertEqual(json.loads(config.read_bytes()), proposed)

    def test_reviewed_replacement_rejects_invalid_content_and_concurrent_change(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.unit_config(root)
            before = config.read_bytes()
            digest = hashlib.sha256(before).hexdigest()
            replacement = root / "reviewed.json"
            replacement.write_text('{"unknown": true}')
            with self.assertRaisesRegex(ValueError, "invalid replacement config"):
                project_config.migrate_replacement(config, replacement, digest, dry_run=False)
            self.assertEqual(config.read_bytes(), before)
            proposed = json.loads(before)
            proposed["output_language"] = "Turkish"
            replacement.write_text(json.dumps(proposed))
            concurrent = {**proposed, "output_language": "Spanish"}
            writer = project_config.atomic_file.replace_bytes

            def race(path, value, before_replace):
                config.write_text(json.dumps(concurrent))
                writer(path, value, before_replace=before_replace)

            with mock.patch.object(project_config.atomic_file, "replace_bytes", side_effect=race):
                with self.assertRaisesRegex(ValueError, "config digest changed"):
                    project_config.migrate_replacement(config, replacement, digest, dry_run=False)
            self.assertEqual(json.loads(config.read_bytes()), concurrent)

    def test_ordinary_writer_refuses_a_changed_config_under_the_shared_guard(self):
        sys.path.insert(0, str(SCRIPTS))
        import project_config

        with tempfile.TemporaryDirectory() as temporary:
            config = self.unit_config(Path(temporary))
            before = json.loads(config.read_bytes())
            current = {**before, "output_language": "Turkish"}
            config.write_text(json.dumps(current))
            with self.assertRaisesRegex(ValueError, "config changed before write"):
                project_config.atomic(config, before, expected=before)
            self.assertEqual(json.loads(config.read_bytes()), current)

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
