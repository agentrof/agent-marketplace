"""Native lifecycle signals remain private, bounded and advisory."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/retrospective_hook.py"


class RetrospectiveHookTests(unittest.TestCase):
    @integration
    def test_subprocess_hook_drains_detached_report_on_both_host_payloads(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary).resolve()
                project = home / "project"
                init_repository(project)
                (project / "workspace").mkdir()
                (project / "workspace/config.json").write_text("{}", encoding="utf-8")
                session = "fixture-session"
                stamp = "2026-11-05T00:00:00Z"
                if host == "claude":
                    transcript = home / "claude/projects/fixture" / f"{session}.jsonl"
                    rows = [{"type": "assistant", "sessionId": session, "cwd": str(project),
                             "timestamp": stamp, "message": {"id": "reply-1", "model": "fixture",
                             "content": [], "usage": {"input_tokens": 10, "output_tokens": 2}}}]
                    operation = "PostToolUseFailure"
                else:
                    transcript = home / "codex/sessions" / f"rollout-{session}.jsonl"
                    rows = [{"type": "session_meta", "payload": {"id": session, "cwd": str(project)}},
                            {"type": "event_msg", "timestamp": stamp, "payload": {"type": "token_count",
                             "info": {"last_token_usage": {"input_tokens": 10, "output_tokens": 2},
                                      "total_token_usage": {"input_tokens": 10, "output_tokens": 2}}}}]
                    operation = "PostToolUse"
                transcript.parent.mkdir(parents=True)
                transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                environment = dict(os.environ, CLAUDE_CONFIG_DIR=str(home / "claude"),
                    CODEX_HOME=str(home / "codex"), PYTHONDONTWRITEBYTECODE="1",
                    PYTHONPATH=os.pathsep.join((str(HOOK.parent),
                        str(ROOT / "plugins/software-engineering-team/scripts"))))
                payload = {"hook_event_name": operation, "session_id": session,
                           "cwd": str(project), "transcript_path": str(transcript),
                           "tool_name": "Bash", "tool_use_id": "failed-tool",
                           "tool_input": {"command": "PRIVATE_COMMAND"},
                           "tool_response": {"exit_code": 7, "output": "PRIVATE_OUTPUT"}}
                result = subprocess.run([sys.executable, str(HOOK), "--host", host], cwd=project,
                    env=environment, input=json.dumps(payload), text=True, capture_output=True,
                    timeout=5, check=True)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")
                directory = project / ".git/agentrof/runtime/work-queue"
                report_path = directory / "observations/latest.json"
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if report_path.is_file() and not (directory / "observer.json").exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(report_path.is_file(), "hook did not produce a detached report")
                self.assertFalse((directory / "observer.json").exists(), "observer did not finish")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["event_counts"], {"failure": 1})
                self.assertEqual(report["coverage"]["state"], "observed")
                self.assertEqual(report["usage"]["input_including_cache"], 10)
                self.assertEqual(report["usage"]["output"], 2)
                event = report["events"][0]["event"]
                self.assertEqual((event["host"], event["operation"], event["exit_code"]), (host, operation, 7))
                self.assertNotIn("PRIVATE_", json.dumps(report))
                state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
                self.assertEqual([job["state"] for job in state["jobs"]], ["completed"])
                self.assertEqual(list((directory / "inbox").glob("*.json")), [])

    def test_native_lifecycle_and_failures_emit_only_safe_metadata(self):
        spec = importlib.util.spec_from_file_location("tested_retrospective_hook", HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        collector = types.SimpleNamespace(emit=mock.Mock(return_value={"queued": True}), resume=mock.Mock())
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            project = home / "project"
            (project / "workspace").mkdir(parents=True)
            (project / "workspace/config.json").write_text("{}", encoding="utf-8")
            claude = home / "claude/projects/project/session-123.jsonl"
            codex = home / "codex/sessions/2026/rollout-session-123.jsonl"
            for path in (claude, codex):
                path.parent.mkdir(parents=True)
                path.write_text("{}\n", encoding="utf-8")
            environment = {"CLAUDE_CONFIG_DIR": str(home / "claude"), "CODEX_HOME": str(home / "codex")}
            with mock.patch.dict(os.environ, environment), mock.patch.dict(sys.modules, {"retrospective": collector}):
                for host, path in (("claude", claude), ("codex", codex)):
                    for operation in hook.EVENTS[host]:
                        with self.subTest(host=host, operation=operation):
                            collector.emit.reset_mock()
                            collector.resume.reset_mock()
                            payload = {"hook_event_name": operation, "session_id": "session-123",
                                       "turn_id": "turn-1", "cwd": str(project), "transcript_path": str(path),
                                       "tool_name": "Bash", "tool_use_id": "tool-1", "duration_ms": 12,
                                       "tool_input": {"command": "echo secret-value"},
                                       "tool_response": {"exit_code": 1, "output": "secret-value"},
                                       "error": "Exit code 1\nsecret-value", "last_assistant_message": "secret-value"}
                            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                                self.assertEqual(hook.main(["--host", host]), 0)
                            collector.emit.assert_called_once()
                            root, event = collector.emit.call_args.args
                            self.assertEqual(root, project)
                            self.assertEqual(event["operation"], operation)
                            self.assertEqual(event["transcript_status"], "available")
                            self.assertEqual(event["transcript_path"], str(path))
                            self.assertRegex(event["source_id"], r"^[0-9a-f]{32}$")
                            self.assertNotIn("secret-value", json.dumps(event))
                            collector.resume.assert_not_called()
                payload = {"hook_event_name": "PostToolUse", "session_id": "session-123",
                           "cwd": str(project), "tool_name": "Bash", "tool_response": {"exit_code": 0}}
                collector.emit.reset_mock()
                hook.observe(payload, "codex")
                collector.emit.assert_not_called()
                for response in ("ordinary output", "Process exited with code 2\nsecret-value", None):
                    with self.subTest(unknown_response=response):
                        collector.emit.reset_mock()
                        payload["tool_response"] = response
                        hook.observe(payload, "codex")
                        collector.emit.assert_called_once()
                        event = collector.emit.call_args.args[1]
                        self.assertEqual(event["kind"], "session")
                        self.assertEqual(event["status"], "unknown")
                        self.assertEqual(event["operation"], "PostToolUse")
                        self.assertNotIn("exit_code", event)
                        self.assertNotIn("error_category", event)
                        self.assertNotIn("secret-value", json.dumps(event))
                payload["tool_name"] = "Read"
                collector.emit.reset_mock()
                hook.observe(payload, "codex")
                collector.emit.assert_not_called()
                payload.update(tool_name="Bash", tool_response={"isError": True})
                hook.observe(payload, "codex")
                self.assertEqual(collector.emit.call_args.args[1]["kind"], "failure")
                self.assertEqual(collector.emit.call_args.args[1]["status"], "failed")
                outside = home / "outside.jsonl"
                outside.write_text("secret-value", encoding="utf-8")
                payload.update(hook_event_name="Stop", transcript_path=str(outside))
                hook.observe(payload, "claude")
                event = collector.emit.call_args.args[1]
                self.assertEqual(event["transcript_status"], "rejected")
                self.assertNotIn("transcript_path", event)
                missing = claude.with_name("later-session.jsonl")
                payload["transcript_path"] = str(missing)
                hook.observe(payload, "claude")
                self.assertEqual(collector.emit.call_args.args[1]["transcript_status"], "missing")
                first_source = collector.emit.call_args.args[1]["source_id"]
                hook.observe(payload, "claude")
                self.assertNotEqual(collector.emit.call_args.args[1]["source_id"], first_source)
                collector.emit.reset_mock()
                payload["tool_input"] = {"command": "python3 /package/retrospective.py drain"}
                hook.observe(payload, "claude")
                collector.emit.assert_not_called()
                payload.pop("tool_input")
                collector.emit.side_effect = OSError("secret-value")
                with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                        mock.patch.object(sys, "stdout", io.StringIO()) as output, \
                        mock.patch.object(sys, "stderr", io.StringIO()) as error:
                    self.assertEqual(hook.main(["--host", "claude"]), 0)
                    self.assertEqual(output.getvalue(), "")
                    self.assertEqual(error.getvalue(), "Retrospective tracking unavailable (OSError).\n")
                collector.emit.side_effect = None
                for result, category in (({"queued": False, "error_category": "PermissionError"}, "PermissionError"),
                                         ({"queued": True, "observer_started": False, "error_category": "TimeoutExpired"}, "TimeoutExpired"),
                                         ({"queued": False, "error_category": "secret-value\npayload"}, "unknown")):
                    collector.emit.return_value = result
                    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                            mock.patch.object(sys, "stderr", io.StringIO()) as error:
                        self.assertEqual(hook.main(["--host", "claude"]), 0)
                        self.assertEqual(error.getvalue(), f"Retrospective tracking unavailable ({category}).\n")
                collector.emit.return_value = {"queued": True, "observer_started": False, "status": "running"}
                with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                        mock.patch.object(sys, "stderr", io.StringIO()) as error:
                    self.assertEqual(hook.main(["--host", "claude"]), 0)
                    self.assertEqual(error.getvalue(), "")

    def test_host_manifests_cover_supported_terminal_and_failure_events(self):
        for host, expected in (
                ("claude", {"SessionStart", "Stop", "SessionEnd", "SubagentStop", "StopFailure", "PostToolUseFailure"}),
                ("codex", {"SessionStart", "Stop", "SessionEnd", "SubagentStop", "Interrupt", "PostToolUse"})):
            path = ROOT / f"platforms/{host}/software-engineering-team/overlay/hooks/hooks.json"
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            actual = {event for event, entries in hooks.items() for entry in entries for handler in entry["hooks"]
                      if "scripts/retrospective_hook.py" in handler["command"]}
            self.assertEqual(actual, expected)
            if host == "codex":
                self.assertNotIn("PostToolUseFailure", hooks)


if __name__ == "__main__":
    unittest.main()
