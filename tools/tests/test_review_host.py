"""Native review transports retain permissions and reject incomplete output."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def adapter(host):
    path = ROOT / f"platforms/{host}/software-engineering-team/overlay/scripts/review_host.py"
    spec = importlib.util.spec_from_file_location(f"review_test_{host}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewHostTests(unittest.TestCase):
    def test_claude_generator_merges_local_companion_routing(self):
        path = ROOT / "platforms/claude/_team/overlay/scripts/generate_claude_project.py"
        spec = importlib.util.spec_from_file_location("review_claude_generator", path)
        generator = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(ROOT / "plugins/software-engineering-team/scripts"),
                                            str(ROOT / "platforms/shared/_team/overlay/scripts"), *sys.path]):
            spec.loader.exec_module(generator)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, plugin = root / "project", root / "plugin"
            (project / ".claude/agents").mkdir(parents=True)
            (plugin / "templates").mkdir(parents=True)
            (plugin / "templates/companion-routing.json").write_text("{}", encoding="utf-8")
            settings = project / ".claude/settings.local.json"
            content = '{"skillOverrides":{"graphify":"user-invocable-only"}}\n'
            routing = types.SimpleNamespace(plan=mock.Mock(return_value={
                "changes": {settings: content}, "current_surfaces": {"local:settings": "before"},
                "target_surfaces": {"local:settings": "after"}, "notes": ["Scoped routing"],
            }))
            empty = {"changes": {}, "choice_requests": [], "current_surfaces": {}, "target_surfaces": {}}
            with mock.patch.dict(sys.modules, {"companion_routing": routing}), \
                    mock.patch.object(generator.project_instructions, "plugin_name", return_value="software-engineering-team"), \
                    mock.patch.object(generator.project_instructions, "plan_portable_project_files", return_value=empty), \
                    mock.patch.object(generator, "render_agents", return_value=({}, {})), \
                    mock.patch.object(generator, "plan_agents", return_value=({}, {}, {}, [])):
                result = generator.preview(project, plugin, "workspace", scope="local")
                self.assertEqual(result["changes"], {settings: content})
                self.assertEqual(result["target_surfaces"], {"local:settings": "after"})
                self.assertEqual(result["companion_notes"], ["Scoped routing"])
                written, restart = generator.materialize(project, plugin, "workspace", scope="local")
                self.assertEqual(written, [settings])
                self.assertEqual(settings.read_bytes(), content.encode("utf-8"))
                self.assertTrue(restart)
                routing.plan.reset_mock()
                generator.preview(project, plugin, "workspace", scope="tracked")
                routing.plan.assert_not_called()

    def test_native_commands_and_observed_results_keep_review_boundaries(self):
        report = {"head": "abc", "verdict": "approve", "checks": [], "findings": [], "limitations": []}
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            schema = project / "schema.json"
            schema.write_text('{"type":"object"}', encoding="utf-8")
            for host in ("claude", "codex"):
                with self.subTest(host=host):
                    module = adapter(host)
                    self.assertEqual(module.HOST, host)
                    settings = {"host": host, "access": "read", "model": "explicit-model", "effort": "high"}
                    before = [{"name": "plugin.server", "enabled": True, "transport": {"type": "stdio"}}]
                    after = [{"name": "plugin.server", "enabled": False}]
                    inventories = [subprocess.CompletedProcess([], 0, json.dumps(rows), "") for rows in [before, after]]
                    with mock.patch.object(module.shutil, "which", return_value=f"/{host}"), \
                            mock.patch("subprocess.run", side_effect=inventories) as probe:
                        args = module.command(project, settings, schema, project)
                    self.assertIn("explicit-model", args)
                    self.assertNotIn("--ephemeral", args)
                    self.assertFalse(any("bypass" in arg for arg in args))
                    self.assertFalse(any("ignore-user-config" in arg for arg in args))
                    if host == "claude":
                        inherited = {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "synthetic-test-key",
                                     "CLAUDE_CODE_CHILD_SESSION": "1", "HOME": "/synthetic-home",
                                     "CLAUDE_CODE_EFFORT_LEVEL": "high", "CLAUDE_CODE_MAX_RETRIES": "10",
                                     "CLAUDE_CODE_RETRY_WATCHDOG": "1"}
                        with mock.patch.dict(os.environ, inherited, clear=True):
                            child = module.environment()
                            self.assertEqual(dict(os.environ), inherited)
                            self.assertEqual(child, {key: value for key, value in inherited.items()
                                                     if key not in {"CLAUDECODE", "CLAUDE_CODE_EFFORT_LEVEL"}}
                                             | {"PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_CODE_MAX_RETRIES": "1"})
                        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob")
                        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
                        self.assertIn("--plugin-dir", args)
                        probe.assert_not_called()
                        events = [
                            {"type": "assistant", "message": {"model": "served-model"}},
                            {"type": "result", "is_error": False, "subtype": "success", "session_id": "native-session",
                             "structured_output": report, "modelUsage": {"served-model": {"inputTokens": 10}}},
                        ]
                    else:
                        self.assertEqual(args[-1], "-")
                        self.assertEqual(args[1:3], ["--no-daemon", "exec"])
                        self.assertTrue(all(call.args[0][1] == "--no-daemon" for call in probe.call_args_list))
                        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
                        self.assertIn('approval_policy="never"', args)
                        self.assertIn("features.multi_agent=false", args)
                        self.assertIn("features.apps=false", args)
                        self.assertEqual(probe.call_count, 2)
                        self.assertIn('"plugin.server"={enabled=false,command=', " ".join(args))
                        self.assertEqual(json.loads((project / "host-preflight.json").read_text())["mcp_servers"], after)
                        events = [
                            {"type": "thread.started", "thread_id": "native-session"},
                            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}},
                            {"type": "turn.completed", "usage": {"input_tokens": 10}},
                        ]
                    parsed = module.parse_output("\n".join(json.dumps(event) for event in events))
                    self.assertEqual(parsed["session_id"], "native-session")
                    self.assertEqual(parsed["report"], report)
                    self.assertEqual(parsed["observed_model"], "served-model" if host == "claude" else None)
                    self.assertIsNone(parsed["observed_effort"])
                    with self.assertRaises(ValueError):
                        module.parse_output('{"type":"turn.failed"}')
                    invalid = [dict(event) for event in events]
                    if host == "claude":
                        invalid[-1]["structured_output"] = "not a report"
                    else:
                        invalid[0]["thread_id"] = {"invalid": "session"}
                    with self.assertRaises(ValueError):
                        module.parse_output("\n".join(json.dumps(event) for event in invalid))
                    with self.assertRaises(ValueError):
                        module.command(project, {**settings, "model": "session"}, schema, project)
                    with mock.patch.object(module.shutil, "which", return_value=None), self.assertRaises(ValueError):
                        module.command(project, settings, schema, project)

            module = adapter("codex")
            with mock.patch.object(module.shutil, "which", return_value="/codex"), \
                    mock.patch.object(module, "mcp_inventory", return_value=before), self.assertRaises(ValueError):
                module.command(project, {"host": "codex", "access": "read", "model": "explicit", "effort": "high"}, schema, project)
