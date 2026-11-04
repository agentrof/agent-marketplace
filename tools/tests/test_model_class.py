"""Explicit compute profiles, lossless override migration and native launch boundaries."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests.levels import integration

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
SHARED = ROOT / "platforms/shared/_team/overlay/scripts"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(SCRIPTS))

import build_distributions
import model_class as resolver
import role_settings

SOURCE = ROOT / "plugins/software-engineering-team"


def load_generator(host):
    path = ROOT / "platforms" / host / "_team/overlay/scripts" / f"generate_{host}_project.py"
    spec = importlib.util.spec_from_file_location(f"launch_test_{host}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapters = build_distributions.load_adapters(ROOT)
        cls.map = build_distributions.class_map(ROOT, SOURCE, cls.adapters)

    def test_each_profile_resolves_to_its_pinned_native_identity(self):
        self.assertEqual(set(self.map["model_classes"]), {"light", "work", "review", "critical"})
        self.assertEqual(len(self.map["launch_profiles"]), 5)
        self.assertTrue(role_settings.valid_class_map(self.map))
        for host in self.adapters:
            for agent, spec in self.map["launch_profiles"].items():
                with self.subTest(host=host, agent=agent):
                    result = resolver.resolve(self.map, {}, host, spec["profile"], access=spec["access"])
                    setting = self.map["hosts"][host]["classes"][spec["profile"]]
                    self.assertEqual(result["agent"], agent)
                    self.assertEqual(result["requested_model"], setting["model"])
                    self.assertEqual(result["requested_effort"], setting["effort"])
                    self.assertEqual(result["spawn"], {})
                    self.assertIsNone(result["observed_model"])
                    self.assertIsNone(result["observed_effort"])
                    self.assertEqual(result["runtime_status"], "unverified")

    def test_critical_authoring_and_review_share_compute_but_keep_access(self):
        for host in self.adapters:
            author = resolver.resolve(self.map, {}, host, "critical", access="write")
            review = resolver.resolve(self.map, {}, host, "critical", access="read")
            self.assertEqual(author["agent"], "critical-worker")
            self.assertEqual(review["agent"], "critical-reviewer")
            self.assertEqual(author["model"], review["model"])
            self.assertEqual(author["effort"], review["effort"])

    def test_permissions_are_declared_without_inventing_runtime_enforcement(self):
        for host in self.adapters:
            result = resolver.resolve(self.map, {}, host, "review", access="read")
            self.assertTrue(result["permissions"]["declared_read_only"])
            self.assertFalse(result["permissions"]["runtime_verified"])
            self.assertEqual(result["permissions"]["definition_restricts_tools"], host == "claude")
        result = resolver.resolve(self.map, {}, "codex", "review", access="read")
        self.assertIn("cannot enforce", result["permissions"]["note"])

    def test_project_overrides_keep_profile_access_and_explicit_session_effort(self):
        config = {"class_models": {"claude": {"critical": {"effort": "max"}},
                                    "codex": {"work": {"model": "session", "effort": "low"}}}}
        critical = resolver.resolve(self.map, config, "claude", "critical", access="read")
        self.assertEqual(critical["effort"], "max")
        self.assertEqual(critical["effort_source"], "config")
        work = resolver.resolve(self.map, config, "codex", "work", access="write")
        self.assertEqual((work["model"], work["effort"]), ("session", "low"))
        self.assertEqual(work["agent"], "worker")
        self.assertEqual(work["spawn"], {})

    def test_unknown_or_retired_selection_is_refused(self):
        for host, profile, access in (("missing", "work", "write"), ("codex", "routine_review", "read"),
                                      ("codex", "review", "write"), ("claude", "light", "read")):
            with self.subTest(host=host, profile=profile, access=access), self.assertRaises(ValueError):
                resolver.resolve(self.map, {}, host, profile, access=access)
        with self.assertRaisesRegex(ValueError, "role_classes is retired"):
            resolver.resolve(self.map, {"role_classes": {"reviewer": "critical"}},
                             "codex", "review", access="read")

    def test_legacy_migration_merges_agreeing_explicit_values_without_mutation(self):
        config = {"team_id": "fixture", "class_models": {"codex": {
            "routine_authoring": {"effort": "low"},
            "complex_authoring": {"effort": "xhigh"},
            "security_review": {"effort": "xhigh"},
        }}}
        before = copy.deepcopy(config)
        result = role_settings.migrate_legacy_overrides(config, self.map)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["config"]["class_models"]["codex"],
                         {"work": {"effort": "low"}, "critical": {"effort": "xhigh"}})
        self.assertEqual(len(result["changes"]), 3)
        self.assertEqual(config, before)
        repeated = role_settings.migrate_legacy_overrides(result["config"], self.map)
        self.assertEqual(repeated["changes"], [])
        self.assertEqual(repeated["conflicts"], [])

    def test_legacy_migration_conflicts_preserve_every_original_value(self):
        cases = [
            {"class_models": {"codex": {"complex_authoring": {"effort": "high"},
                                         "security_review": {"effort": "xhigh"}}}},
            {"class_models": {"claude": {"orchestration": {"effort": "low"}}}},
            {"role_classes": {"code-reviewer": "security_review"}},
            {"tier_models": {"codex": {"medium": {"effort": "low"}}}},
            {"role_classes": []},
            {"class_models": {"unknown": {"work": {"effort": "low"}}}},
        ]
        for config in cases:
            with self.subTest(config=config):
                before = copy.deepcopy(config)
                result = role_settings.migrate_legacy_overrides(config, self.map)
                self.assertTrue(result["conflicts"])
                self.assertEqual(result["config"], before)
                self.assertEqual(config, before)

    def test_launch_registry_is_the_only_source_of_native_definitions(self):
        registry = build_distributions.launch_registry(SOURCE)
        for host, adapter in self.adapters.items():
            for name, spec in registry["profiles"].items():
                artifacts = adapter.module.agent_artifacts(
                    {"execution_profile": self.map["hosts"][host]["classes"],
                     "instructions": registry["instructions"]}, name, spec)
                self.assertEqual(len(artifacts), 1)
                path, text = artifacts[0]
                self.assertEqual(path, f"agents/{name}.md")
                self.assertIn(f"model: {self.map['hosts'][host]['classes'][spec['profile']]['model']}", text)
                self.assertNotIn("model_class:", text)
                self.assertEqual("tools: Read, Grep, Glob" in text, spec["access"] == "read")
                if host == "claude":
                    self.assertIn("omitClaudeMd: true", text)
                    if spec["access"] == "write":
                        self.assertIn("Agent", text.split("disallowedTools: ")[1].splitlines()[0])

    def test_launch_map_rejects_duplicate_or_missing_identity(self):
        broken = copy.deepcopy(self.map)
        broken["launch_profiles"]["critical-reviewer"]["access"] = "write"
        self.assertFalse(role_settings.valid_class_map(broken))
        broken = copy.deepcopy(self.map)
        del broken["launch_profiles"]["worker"]
        self.assertFalse(role_settings.valid_class_map(broken))


class NativeProjectionTests(unittest.TestCase):
    def test_codex_unavailable_model_stays_pinned_and_read_intent_is_truthful(self):
        generator = load_generator("codex")
        fields = {"name": "reviewer", "description": "Review a candidate.",
                  "model": "gpt-6.1-sol", "model_reasoning_effort": "medium",
                  "tools": "Read, Grep, Glob"}
        text = generator.agent_toml(fields, "Review the provided evidence.\n", "fixture",
                                    unavailable=frozenset({"gpt-6.1-sol"}))
        self.assertIn('model = "gpt-6.1-sol"', text)
        self.assertIn('model_reasoning_effort = "medium"', text)
        self.assertNotIn("sandbox_mode =", text)
        self.assertIn("cannot override the parent sandbox", text)
        with self.assertRaisesRegex(ValueError, "unsupported execution profile"):
            generator.agent_toml(fields, "Review.", "fixture", profile="inherit")

    def test_claude_unavailable_model_stays_pinned_with_read_whitelist(self):
        generator = load_generator("claude")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "agents/reviewer.md"
            source.parent.mkdir()
            source.write_text("---\nname: reviewer\ndescription: Review.\nmodel: claude-sonnet-5-5\n"
                              "effort: medium\ntools: Read, Grep, Glob\n---\nReview evidence.\n")
            setting = {"model": "claude-sonnet-5-5", "effort": "medium"}
            text = generator.render_agent(source, "fixture", setting, setting, root, root, "1", True)
            self.assertIn("model: claude-sonnet-5-5", text)
            self.assertIn("tools: Read, Grep, Glob", text)
            self.assertNotIn("model: inherit", text)

    def test_stamp_ignores_release_version_but_tracks_source_renderer_and_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "agents/worker.md"
            source.parent.mkdir()
            source.write_text("Bounded task.\n")
            renderer = root / "scripts/generate_fixture_project.py"
            renderer.parent.mkdir()
            renderer.write_text("render_one()\n")
            setting = {"model": "fixture-model", "effort": "medium"}
            stamp = role_settings.stamp("fixture", "1", source, setting)
            self.assertEqual(stamp, role_settings.stamp("fixture", "2", source, setting))
            renderer.write_text("render_two()\n")
            self.assertNotEqual(stamp, role_settings.stamp("fixture", "1", source, setting))
            renderer.write_text("render_one()\n")
            source.write_text("New boundary.\n")
            self.assertNotEqual(stamp, role_settings.stamp("fixture", "1", source, setting))
            source.write_text("Bounded task.\n")
            self.assertNotEqual(stamp, role_settings.stamp("fixture", "1", source,
                                                         {**setting, "effort": "high"}))


@integration
class ResolverCommandTests(unittest.TestCase):
    def test_installed_command_resolves_profile_without_task_size_escalation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "workspace/config.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team",
                                          "output_language": "English", "terminology_language": "English"}))
            script = ROOT / "dist/codex/software-engineering-team/scripts/model_class.py"
            result = subprocess.run([sys.executable, str(script), "resolve", "--project-root", raw,
                                     "--profile", "critical", "--access", "write", "--host", "codex",
                                     "--task", "TK-001", "--json"], capture_output=True, text=True,
                                    check=False, timeout=60, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["agent"], "critical-worker")
            self.assertEqual(payload["task"], "TK-001")
            self.assertEqual(payload["runtime_status"], "unverified")


if __name__ == "__main__":
    unittest.main()
