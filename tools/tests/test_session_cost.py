from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platforms/shared/software-engineering-team/overlay/scripts"))
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import session_cost  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402
from tools.tests.levels import integration  # noqa: E402


@integration
class SessionObservationTests(unittest.TestCase):
    def test_native_command_failure_metadata_is_deduplicated_without_output_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, codex = home / "project", home / "codex"
            root.mkdir()
            codex.mkdir()
            init_repository(root)
            failed = {"type": "event_msg", "timestamp": "2026-11-04T10:00:00Z", "payload": {
                "type": "item_completed", "thread_id": "main", "turn_id": "turn",
                "item": {"type": "CommandExecution", "id": "exec-failed", "status": "failed",
                         "exit_code": 7, "duration": {"secs": 2, "nanos": 125_000_000},
                         "command": "private command", "aggregated_output": "private output"}}}
            successful = {"type": "event_msg", "timestamp": "2026-11-04T10:00:01Z", "payload": {
                "type": "item_completed", "item": {"type": "CommandExecution", "id": "exec-ok",
                "status": "completed", "exit_code": 0, "aggregated_output": "Exit code: 7; failure"}}}
            malformed = json.loads(json.dumps(failed))
            malformed["payload"]["item"].update(id="exec-malformed", status={"private": "raw metadata"},
                                               duration={"secs": 10**400, "nanos": 0})
            invalid_id = json.loads(json.dumps(failed))
            invalid_id["payload"]["item"]["id"] = {"private": "invalid identity"}
            transcript = codex / "main.jsonl"
            rows = [{"type": "session_meta", "payload": {"id": "main", "cwd": str(root)}},
                    failed, failed, successful, malformed, invalid_id]
            transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = session_cost.observe(root, claude_home=home / "claude", codex_home=codex,
                expected_sessions=[{"host": "codex", "session_id": "main", "path": str(transcript)}])
            failures = [event for event in result["events"] if event["kind"] == "failure"]
            self.assertEqual(len(failures), 2)
            self.assertEqual(failures[0]["event_id"], "exec-failed")
            self.assertEqual(failures[0]["lane"], "codex:main")
            self.assertEqual(failures[0]["exit_code"], 7)
            self.assertEqual(failures[0]["seconds"], 2.125)
            self.assertEqual(failures[0]["observation_source"], "native_item_completed")
            self.assertEqual(failures[1]["status"], "unknown")
            self.assertIsNone(failures[1]["seconds"])
            self.assertTrue({"invalid_native_failure_id", "invalid_native_failure_status",
                             "invalid_native_failure_duration"}.issubset(
                {item["reason"] for item in result["coverage"]["issues"]}))
            for secret in ("private command", "private output", "raw metadata", "invalid identity"):
                self.assertNotIn(secret, json.dumps(result))

    def test_complete_hashed_source_satisfies_disjoint_scoped_visibility(self):
        import review_execution

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / "project"
            root.mkdir()
            init_repository(root)
            codex = home / "codex"
            codex.mkdir()
            source = home / "skill" / "SKILL.md"
            source.parent.mkdir()
            content = "First required section.\nOther guidance.\nLast required section.\n"
            scoped = "First required section.\nLast required section.\n"
            source.write_text(content)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            runtime = root / session_cost.RUNTIME_REVIEW
            runtime.mkdir(parents=True)
            (runtime / "TK-001.methods.json").write_text(json.dumps({"companion/security-audit": {
                "source_path": str(source), "skill_sha256": source_hash,
                "content_sha256": hashlib.sha256(scoped.encode()).hexdigest()}}))
            transcript = codex / "session.jsonl"

            def observe(visible):
                rows = [
                    {"type": "session_meta", "payload": {"id": "main", "cwd": str(root)}},
                    {"type": "response_item", "payload": {"type": "function_call_output", "output": visible}},
                ]
                transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                with mock.patch.object(review_execution, "source_excerpt", return_value={
                        "sha256": source_hash, "content": scoped, "delivery": "scoped_sections"}):
                    return session_cost.observe(root, claude_home=home / "claude", codex_home=codex,
                        expected_sessions=[{"host": "codex", "session_id": "main", "path": str(transcript)}])

            complete = observe(content)
            self.assertEqual(complete["deliveries"][0]["state"], "observed")
            self.assertNotIn("_full_text", json.dumps(complete))
            self.assertNotIn("Other guidance.", json.dumps(complete))
            self.assertEqual(observe("First required section.\n")["deliveries"][0]["state"], "unknown")
            source.write_text(content + "Changed after receipt.\n")
            self.assertEqual(observe(content)["deliveries"][0]["state"], "unknown")

    def test_linked_review_usage_visibility_deduplication_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, worktree = home / "project", home / "worker"
            root.mkdir()
            init_repository(root)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "--allow-empty", "-qm", "Baseline"], cwd=root, check=True)
            subprocess.run(["git", "worktree", "add", "-qb", "task/tk-001-check", str(worktree)],
                           cwd=root, check=True, capture_output=True)
            codex, claude = home / "codex", home / "claude"
            codex.mkdir()
            claude.mkdir()
            runtime = worktree / session_cost.RUNTIME_REVIEW
            attempt = runtime / "attempt"
            attempt.mkdir(parents=True)
            packet = "Review TK-001 using the complete supplied procedure."
            (attempt / "packet.txt").write_text(packet)
            content = (ROOT / "plugins/software-engineering-team/skill-content/verify/SKILL.md").read_text()
            method = home / "skill" / "SKILL.md"
            method.parent.mkdir()
            method.write_text(content)
            (runtime / "TK-001.methods.json").write_text(json.dumps({"verify": {
                "source_path": str(method), "skill_sha256": hashlib.sha256(method.read_bytes()).hexdigest()}}))

            def write_rows(path, rows):
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

            def native_rows(identity, cwd, amount, visible):
                usage = {"input_tokens": amount, "cached_input_tokens": 20, "output_tokens": 5}
                return [
                    {"type": "session_meta", "payload": {"id": identity, "cwd": str(cwd), "source": "exec"}},
                    {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "medium"}},
                    {"type": "response_item", "timestamp": "2026-11-04T10:00:00Z", "payload": visible},
                    *[{"type": "event_msg", "timestamp": "2026-11-04T10:01:00Z", "payload": {
                        "type": "token_count", "info": {"last_token_usage": usage,
                                                          "total_token_usage": usage}}}] * 2]

            main = codex / "rollout-main.jsonl"
            graph_content = "Use the bounded graph query."
            graph_source = hashlib.sha256(graph_content.encode()).hexdigest()
            graph_output = "NODE app.py\n"
            graph_loader = {"task_id": "TK-001", "companion": "graphify", "skill_sha256": graph_source,
                            "content": graph_content, "action": {"ok": True, "truncated": False,
                            "text": graph_output, "stdout_sha256": hashlib.sha256(graph_output.encode()).hexdigest()}}
            cut_loader = {**graph_loader, "action": {**graph_loader["action"], "truncated": True}}
            write_rows(main, native_rows("main", root, 100, {"type": "function_call_output",
                "output": json.dumps({"output": json.dumps({"content": content, "method": "verify",
                    "task_id": "TK-001", "skill_sha256": hashlib.sha256(content.encode()).hexdigest()}) +
                    "\n" + json.dumps(graph_loader) + "\n" + json.dumps(cut_loader)})}) + [
                        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": packet}}])
            child = codex / "rollout-child.jsonl"
            write_rows(child, native_rows("child", worktree, 50, {"type": "message", "role": "user",
                                                                   "content": packet}))
            write_rows(codex / "copied-child.jsonl", list(session_cost.records(child)))
            stdout = attempt / "stdout.jsonl"
            write_rows(stdout, [{"type": "thread.started", "thread_id": "child"},
                {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 20,
                                                       "output_tokens": 5}}])
            record = {"host": "codex", "stdout_path": str(stdout), "finished_at": "2026-11-04T10:02:00Z",
                      "packet_sha256": hashlib.sha256(packet.encode()).hexdigest(),
                      "stdout_sha256": hashlib.sha256(stdout.read_bytes()).hexdigest(),
                      "requested": {"agent": "reviewer", "model": "gpt-6.1-sol", "effort": "medium"}}
            (runtime / "candidate.native-review.json").write_text(json.dumps(record))
            expected = [{"host": "codex", "session_id": "main", "path": str(main), "deliveries": [
                {"name": "companion/graphify", "task": "TK-001", "source_sha256": graph_source,
                 "content_sha256": graph_source}]}]
            result = session_cost.observe(root, claude_home=claude, codex_home=codex, expected_sessions=expected)
            self.assertEqual(result["usage"]["input_including_cache"], 150)
            self.assertEqual(result["usage"]["output"], 10)
            self.assertEqual(result["coverage"]["state"], "observed")
            self.assertEqual(result["coverage"]["observed_sessions"], ["codex:child", "codex:main"])
            self.assertTrue(all(item["state"] == "observed" for item in result["deliveries"]))
            self.assertEqual(len([item for item in result["deliveries"] if item["kind"] == "graph_result"]), 1)
            reviewers = [event for event in result["events"] if event["kind"] == "call" and event["role"] == "reviewer"]
            self.assertEqual(len(reviewers), 1)
            self.assertEqual(reviewers[0]["task"], "TK-001")
            self.assertNotIn(content, json.dumps(result))
            explicit_empty = session_cost.observe(root, claude_home=claude, codex_home=codex, expected_sessions=[])
            self.assertNotIn("codex:main", explicit_empty["coverage"]["observed_sessions"])

            regressed = native_rows("reset", root, 100, {})
            regressed.append({"type": "event_msg", "timestamp": "2026-11-04T10:02:00Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {"input_tokens": 5},
                                                  "total_token_usage": {"input_tokens": 5}}}})
            problems = []
            session_cost.codex_events(main, root, source_records=regressed, problems=problems)
            self.assertEqual(problems, ["non_monotonic_usage"])

            # A completed local stream still supplies totals when native persistence is unavailable.
            child.unlink()
            (codex / "copied-child.jsonl").unlink()
            method.unlink()
            fallback = session_cost.observe(root, claude_home=claude, codex_home=codex, expected_sessions=expected)
            self.assertEqual(fallback["usage"]["input_including_cache"], 150)
            self.assertEqual(next(item for item in fallback["deliveries"] if item["kind"] == "review_packet")["state"], "unknown")
            self.assertEqual(next(item for item in fallback["deliveries"] if item["kind"] == "method")["state"], "observed")
            self.assertIsNone(session_cost.usd(next(event for event in fallback["events"]
                if event.get("usage_scope") == "session_total"), {}))
            missing = session_cost.observe(root, claude_home=claude, codex_home=codex,
                expected_sessions=[{"host": "codex", "session_id": "missing", "path": str(codex / "absent.jsonl")}])
            self.assertEqual(missing["coverage"]["state"], "partial")
            self.assertTrue(any(item["reason"] == "expected_session_unavailable" for item in missing["coverage"]["issues"]))
            with mock.patch.object(session_cost, "MAX_SCAN_BYTES", 1):
                limited = session_cost.observe(root, claude_home=claude, codex_home=codex, expected_sessions=expected)
            self.assertIsNone(limited["usage"])
            self.assertEqual(limited["coverage"]["state"], "unavailable")
            self.assertTrue(any(item["reason"] == "scan_limit" for item in limited["coverage"]["issues"]))

            claude_result = session_cost.stream_usage([{"type": "result", "modelUsage": {
                "claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 3, "cacheReadInputTokens": 20,
                                       "cacheCreationInputTokens": 4, "costUSD": 0.01}}}], "claude", {})
            self.assertEqual(claude_result[0]["tokens"], {"input": 10, "output": 3, "cache_read": 20, "cache_write": 4})
            self.assertEqual(session_cost.usd(claude_result[0], {}), 0.01)

            parent_path = claude / "parent.jsonl"
            child_path = claude / "agent-worker.jsonl"
            for path, message_id, agent_id in ((parent_path, "parent-message", ""),
                                                (child_path, "child-message", "worker")):
                write_rows(path, [{"type": "assistant", "timestamp": "2026-11-04T10:01:00Z",
                    "sessionId": "claude-parent", "agentId": agent_id, "cwd": str(root),
                    "message": {"id": message_id, "model": "claude-sonnet-5-5", "content": [],
                                "usage": {"input_tokens": 7, "output_tokens": 2}}}])
            claude_observed = session_cost.observe(root, claude_home=claude, codex_home=codex,
                expected_sessions=[{"host": "claude", "session_id": "claude-parent", "path": str(path),
                                    "agent_id": "worker" if path == child_path else "",
                                    "role": "agent" if path == child_path else "main"}
                                   for path in (parent_path, child_path)])
            claude_calls = [event for event in claude_observed["events"]
                            if event["kind"] == "call" and event["lane"].startswith("claude:")]
            self.assertEqual(len(claude_calls), 2)
            self.assertEqual({event["role"] for event in claude_calls}, {"main", "agent"})
            self.assertEqual(len({event["lane"] for event in claude_calls}), 2)

            # Retained metadata binds a removed checkout to an actual native session without copying its text.
            write_rows(child, native_rows("child", worktree, 50, {"type": "message", "role": "user", "content": packet}))
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=True)
            retained = {"host": "codex", "session_id": "child", "worktree": str(worktree), "role": "reviewer",
                        "task": "TK-001", "packet_sha256": hashlib.sha256(packet.encode()).hexdigest(),
                        "usage": {"input_tokens": 50, "cached_input_tokens": 20, "output_tokens": 5}}
            cleaned = session_cost.observe(root, claude_home=claude, codex_home=codex,
                                           expected_sessions=expected + [retained])
            self.assertEqual(cleaned["usage"]["input_including_cache"], 150)
            self.assertEqual(cleaned["deliveries"][0]["state"], "observed")
            self.assertTrue(any(item["membership_source"] == "retained_event" for item in cleaned["coverage"]["sources"]))

            archived = codex / "archived_sessions"
            archived.mkdir()
            archive = archived / "rollout-main.jsonl"
            archive.write_bytes(main.read_bytes())
            archived_result = session_cost.observe(root, claude_home=claude, codex_home=codex / "sessions",
                expected_sessions=[{"host": "codex", "session_id": "main", "path": str(archive)}])
            self.assertEqual(archived_result["usage"]["input_including_cache"], 100)

            unknown = codex / "unknown.jsonl"
            write_rows(unknown, [{"type": "session_meta", "payload": {"id": "unknown", "cwd": str(root)}}])
            unknown_expected = {"host": "codex", "session_id": "unknown", "path": str(unknown)}
            partial = session_cost.observe(root, claude_home=claude, codex_home=codex,
                                           expected_sessions=expected + [unknown_expected])
            self.assertEqual(partial["coverage"]["state"], "partial")
            self.assertTrue(any(item["reason"] == "missing_usage" for item in partial["coverage"]["issues"]))
            zero = native_rows("unknown", root, 0, {})
            for row in zero:
                if row.get("payload", {}).get("type") == "token_count":
                    usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
                    row["payload"]["info"] = {"last_token_usage": usage, "total_token_usage": usage}
            write_rows(unknown, zero)
            measured_zero = session_cost.observe(root, claude_home=claude, codex_home=codex,
                                                 expected_sessions=[unknown_expected])
            self.assertEqual(measured_zero["coverage"]["state"], "observed")
            self.assertEqual(measured_zero["usage"]["input_including_cache"], 0)

            write_rows(child_path, [{"type": "user", "sessionId": "claude-parent", "agentId": "worker",
                                    "cwd": str(root), "message": {"content": "Inspect this task."}}])
            child_missing_usage = session_cost.observe(root, claude_home=claude, codex_home=codex,
                expected_sessions=[{"host": "claude", "session_id": "claude-parent", "path": str(parent_path)},
                                   {"host": "claude", "session_id": "claude-parent", "agent_id": "worker",
                                    "path": str(child_path)}])
            self.assertEqual(child_missing_usage["coverage"]["state"], "partial")
            self.assertTrue(any(item.get("agent_id") == "worker" and item["reason"] == "missing_usage"
                                for item in child_missing_usage["coverage"]["issues"]))


if __name__ == "__main__":
    unittest.main()
