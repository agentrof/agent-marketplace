from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import retrospective
import work_queue
from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration


@integration
class AutomaticRetrospectiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        init_repository(self.root)

    def emit(self, **event):
        with mock.patch.object(retrospective, "resume", return_value={"observer_started": False}):
            result = retrospective.emit(self.root, {"kind": "failure", "operation": "prove", **event})
        self.assertTrue(result["queued"], result)
        retrospective._import_inbox(self.root, work_queue._directory(self.root), 64)
        return result

    def run_observer(self):
        with mock.patch.object(retrospective, "resume", return_value={"observer_started": False}):
            return retrospective.run(self.root)

    def test_observation_preserves_attempts_and_isolated_grant_capacity(self):
        receipt = self.root / "proof.json"
        before = {"head": "a" * 40, "ok": False, "commands": [{"command": "private command",
                  "exit_code": 1, "seconds": 1.5, "queue_seconds": 0.2, "tail": "private error"}]}
        receipt.write_text(json.dumps(before), encoding="utf-8")
        first = self.emit(task="TK-001", head=before["head"], receipt_path=str(receipt),
                          receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), source_id="attempt-1")
        duplicate = self.emit(task="TK-001", head=before["head"], receipt_path=str(receipt),
                              receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), source_id="attempt-1")
        self.assertEqual(first["event_id"], duplicate["event_id"])
        receipt.write_text(json.dumps({**before, "ok": True, "commands": []}), encoding="utf-8")
        self.emit(kind="completion", task="TK-001", receipt_path=str(receipt), source_id="attempt-2")
        deep = work_queue.enqueue(self.root, "retro", "granted-retro", {"grant": "user"})
        with work_queue._state(self.root) as state:
            automatic = state["jobs"][0]
            automatic.update(state="claimed", attempts=1)
        claimed = work_queue.claim(self.root, "controller", ["retro"], 1, time.time() + 60, 60, 1, scope="user")
        self.assertEqual(claimed["id"], deep["id"])
        result = self.run_observer()
        self.assertEqual(result["completed"], 2)
        saved = retrospective.report(self.root)
        self.assertEqual(saved["queue"], {"completed": 2})
        self.assertEqual(saved["latest"]["proof_seconds"], 1.5)
        self.assertEqual(saved["latest"]["proof_queue_seconds"], 0.2)
        self.assertNotIn("private command", json.dumps(saved))
        self.assertNotIn("private error", json.dumps(saved))
        self.assertEqual(work_queue.status(self.root)["jobs"][-1]["state"], "claimed")
        self.assertFalse(retrospective.emit(self.root, {"kind": "failure", "operation": "prove", "command": "secret"})["queued"])

    def test_launch_reservation_failure_and_crash_are_recoverable(self):
        self.emit(source_id="one")
        original = subprocess.Popen
        launched = []

        def spawn(command, **kwargs):
            if command[0] == sys.executable:
                launched.append(command)
                return mock.Mock(pid=42)
            return original(command, **kwargs)

        def failure(command, **kwargs):
            if command[0] == sys.executable:
                raise OSError("cannot launch")
            return original(command, **kwargs)

        with mock.patch.object(retrospective.subprocess, "Popen", side_effect=failure):
            failure = retrospective.resume(self.root)
        self.assertFalse(failure["observer_started"])
        reservation = work_queue._directory(self.root) / "observer.json"
        self.assertFalse(reservation.exists())
        with mock.patch.object(retrospective.subprocess, "Popen", side_effect=spawn):
            self.assertTrue(retrospective.resume(self.root)["observer_started"])
            self.assertEqual(retrospective.resume(self.root)["status"], "starting")
            self.assertEqual(len(launched), 1)
            current = json.loads(reservation.read_text(encoding="utf-8"))
            reservation.write_text(json.dumps({**current, "launch_until": 0}), encoding="utf-8")
            self.assertTrue(retrospective.resume(self.root)["observer_started"])
            self.assertEqual(len(launched), 2)
        with work_queue._state(self.root) as state:
            state["jobs"][0].update(state="claimed", attempts=1)
        reservation.unlink()
        self.assertEqual(self.run_observer()["completed"], 1)

    def test_arrival_during_collection_is_drained_and_failures_are_bounded(self):
        self.emit(source_id="one")
        original = retrospective.collect
        calls = []

        def collect(root, batch):
            calls.append(batch)
            if len(calls) == 1:
                self.emit(source_id="during-collection")
            return original(root, batch)

        with mock.patch.object(retrospective, "collect", side_effect=collect):
            self.assertEqual(self.run_observer()["completed"], 2)
        self.emit(source_id="failed-collection")
        with mock.patch.object(retrospective, "collect", side_effect=ValueError("collector failed")) as failed:
            self.assertEqual(self.run_observer()["completed"], 0)
            self.assertEqual(failed.call_count, retrospective.policy()["max_attempts"])
        states = retrospective.report(self.root)["queue"]
        self.assertEqual(states, {"completed": 2, "failed": 1})
        self.assertFalse((work_queue._directory(self.root) / "observer.json").exists())

    def test_durable_emit_does_not_wait_for_contended_queue(self):
        original_spawn = subprocess.Popen

        def spawn(command, **kwargs):
            return mock.Mock(pid=42) if command[0] == sys.executable else original_spawn(command, **kwargs)

        with work_queue._state(self.root), mock.patch.object(retrospective.subprocess, "Popen", side_effect=spawn):
            started = time.monotonic()
            result = retrospective.emit(self.root, {"kind": "failure", "operation": "prove", "source_id": "contended"})
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(result["queued"], result)
        directory = work_queue._directory(self.root)
        self.assertEqual(len(list((directory / "inbox").glob("*.json"))), 1)
        (directory / "observer.json").unlink(missing_ok=True)
        self.assertEqual(self.run_observer()["completed"], 1)

    def test_inbox_duplicate_consumed_during_publish_remains_queued(self):
        event = {"kind": "completion", "operation": "train", "head": "a" * 40}
        directory = work_queue._directory(self.root)
        real_link = retrospective.os.link

        def consume_after_collision(source, destination):
            try:
                return real_link(source, destination)
            except FileExistsError:
                retrospective._import_inbox(self.root, directory, 64)
                raise

        with mock.patch.object(retrospective, "resume", return_value={"observer_started": False}):
            first = retrospective.emit(self.root, event)
            with mock.patch.object(retrospective.os, "link", side_effect=consume_after_collision):
                second = retrospective.emit(self.root, event)
        self.assertTrue(second["queued"], second)
        self.assertEqual(second["event_id"], first["event_id"])
        self.assertEqual(len(list((directory / "inbox").glob("*.json"))), 1)
        retrospective._import_inbox(self.root, directory, 64)
        self.assertEqual(len(work_queue.status(self.root)["jobs"]), 1)
        self.assertEqual(list((directory / "inbox").glob("*.json")), [])
        occupied = directory / "not-a-file.json"
        occupied.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            retrospective._publish(occupied, b"{}")

    def test_shutdown_arrival_starts_a_successor_without_losing_the_event(self):
        self.emit(source_id="first")
        original_lock, original_unlock, original_spawn = retrospective._lock, retrospective.file_lock.unlock, subprocess.Popen
        observer_descriptors, threads, results, launches = [], [], [], []

        def lock(directory):
            descriptor = original_lock(directory)
            observer_descriptors.append(descriptor)
            return descriptor

        def arrive():
            results.append(retrospective.emit(self.root, {"kind": "completion", "operation": "ready", "source_id": "last"}))

        def unlock(descriptor, **kwargs):
            if observer_descriptors and descriptor == observer_descriptors[0] and not threads:
                thread = threading.Thread(target=arrive)
                threads.append(thread)
                thread.start()
            return original_unlock(descriptor, **kwargs)

        def spawn(command, **kwargs):
            if command[0] == sys.executable:
                launches.append(command)
                return mock.Mock(pid=42)
            return original_spawn(command, **kwargs)

        with mock.patch.object(retrospective, "_lock", side_effect=lock), \
                mock.patch.object(retrospective.file_lock, "unlock", side_effect=unlock), \
                mock.patch.object(retrospective.subprocess, "Popen", side_effect=spawn):
            retrospective.run(self.root)
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["queued"])
        self.assertEqual(len(launches), 1)
        saved = retrospective.report(self.root)
        self.assertEqual(saved["queue"], {"completed": 1})
        self.assertEqual(saved["inbox_pending"], 1)

    def test_detached_observer_collects_without_grant_or_model_process(self):
        result = retrospective.emit(self.root, {"kind": "completion", "operation": "ready", "task": "TK-001"})
        self.assertTrue(result["observer_started"], result)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = retrospective.report(self.root)
            if saved["queue"] == {"completed": 1}:
                break
            time.sleep(0.05)
        self.assertEqual(saved["queue"], {"completed": 1})
        self.assertEqual(saved["latest"]["tasks"], ["TK-001"])
        output = subprocess.run([sys.executable, str(Path(retrospective.__file__)), "report", "--project",
                                 str(self.root), "--json"], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(output.stdout)["queue"], {"completed": 1})

    def test_completion_survives_cleanup_and_late_session_refresh_preserves_task_metrics(self):
        runtime = self.root / ".agentrof/agent-marketplace/.runtime"
        (runtime / "proof").mkdir(parents=True)
        (runtime / "review").mkdir()
        head = "a" * 40
        proof = runtime / "proof/TK-001.json"
        proof.write_text(json.dumps({"head": head, "ok": True, "commands": [{"seconds": 3, "exit_code": 0}]}), encoding="utf-8")
        methods = runtime / "review/TK-001.methods.json"
        methods.write_text(json.dumps({"companion/graphify": {"load_source": "companion_tool", "skill_sha256": "source",
                           "content_sha256": "scope", "source_scope": {"delivery": "scoped_sections"}}}), encoding="utf-8")
        review = runtime / f"review/{head}.native-review.json"
        review.write_text(json.dumps({"host": "codex", "head": head, "status": "approved", "packet_sha256": "packet",
            "started_at": "2026-11-04T00:00:03Z", "finished_at": "2026-11-04T00:00:05Z",
            "requested": {"model": "model", "effort": "medium"},
            "result": {"session_id": "review-session", "usage": {"input_tokens": 10}, "report": {"checks": []}}}), encoding="utf-8")
        self.emit(kind="completion", operation="ready", task="TK-001", head=head,
                  requested_at="2026-11-04T00:00:00Z", merged_at="2026-11-04T00:00:08Z", ready_at="2026-11-04T00:00:10Z")
        for path in (proof, methods, review):
            path.unlink()
        calls = []

        def observe(root, *, expected_sessions):
            calls.append(expected_sessions)
            return {"coverage": {"state": "observed"}, "usage": {"input_including_cache": 10, "output": 1},
                    "deliveries": [], "events": []}

        with mock.patch.dict(sys.modules, {"session_cost": SimpleNamespace(observe=observe)}):
            self.assertEqual(self.run_observer()["completed"], 1)
            self.emit(kind="session", operation="Stop", host="codex", session_id="main-session", source_id="stop-1")
            self.assertEqual(self.run_observer()["completed"], 1)
            self.emit(kind="session", operation="SubagentStop", host="claude", session_id="parent-session",
                      agent_id="child", transcript_path=str(self.root / "child.jsonl"), source_id="stop-child")
            self.emit(kind="session", operation="Stop", host="claude", session_id="parent-session",
                      transcript_path=str(self.root / "parent.jsonl"), source_id="stop-parent")
            self.assertEqual(self.run_observer()["completed"], 2)
        final = retrospective.report(self.root)["latest"]
        self.assertEqual(final["tasks"], [])
        metric = final["task_metrics"][0]
        self.assertEqual(metric["request_to_ready_seconds"], 10)
        self.assertEqual(metric["request_to_merge_seconds"], 8)
        self.assertEqual(metric["proof_seconds"], 3)
        self.assertEqual(metric["review_seconds"], 2)
        self.assertEqual(metric["methods"][0]["content_sha256"], "scope")
        expected = {row["session_id"]: row for row in calls[-1]}
        self.assertEqual(set(expected), {"main-session", "review-session", "parent-session"})
        self.assertEqual(expected["review-session"]["packet_sha256"], "packet")
        self.assertEqual(expected["review-session"]["usage"], {"input_tokens": 10})
        self.assertEqual(expected["main-session"]["deliveries"][0]["content_sha256"], "scope")
        parents = [row for row in calls[-1] if row["session_id"] == "parent-session"]
        self.assertEqual({row["path"] for row in parents}, {str(self.root / "parent.jsonl"), str(self.root / "child.jsonl")})

    def test_same_session_new_transcript_revision_is_a_new_observation(self):
        transcript = self.root / "session.jsonl"
        transcript.write_text("first\n", encoding="utf-8")
        first = self.emit(kind="session", operation="Stop", host="codex", session_id="session", transcript_path=str(transcript))
        transcript.write_text("first\nsecond\n", encoding="utf-8")
        second = self.emit(kind="session", operation="Stop", host="codex", session_id="session", transcript_path=str(transcript))
        self.assertNotEqual(first["event_id"], second["event_id"])

    def test_native_command_failures_are_reported_from_a_session_trigger(self):
        self.emit(kind="session", operation="Stop", host="codex", session_id="main-session")
        failures = [{"kind": "failure", "observation_source": "native_item_completed",
                     "lane": "codex:main-session", "event_id": f"failure-{index}", "at": index,
                     "role": "main", "task": "TK-001", "tool": "Bash", "status": "failed",
                     "exit_code": 7, "seconds": 0.25, "command": "PRIVATE_COMMAND",
                     "stdout": "PRIVATE_OUTPUT"} for index in range(22)]
        observation = {"coverage": {"state": "observed"}, "usage": None,
                       "deliveries": [], "events": failures}
        with mock.patch.dict(sys.modules, {"session_cost": SimpleNamespace(observe=lambda *a, **k: observation)}):
            self.assertEqual(self.run_observer()["completed"], 1)
        saved = retrospective.report(self.root)
        report = saved["latest"]
        self.assertEqual(report["event_counts"], {"session": 1})
        self.assertEqual(report["native_failures"]["total"], 22)
        self.assertEqual(report["native_failures"]["omitted"], 2)
        self.assertEqual(len(report["native_failures"]["records"]), 20)
        self.assertEqual(report["native_failures"]["records"][0]["event_id"], "failure-2")
        self.assertTrue(any(row["basis"] == "observed_native_command_failures" for row in report["proposals"]))
        self.assertNotIn("PRIVATE_COMMAND", json.dumps(report))
        self.assertNotIn("PRIVATE_OUTPUT", json.dumps(report))
        text = Path(saved["report_path"]).with_name("latest.txt").read_text(encoding="utf-8")
        self.assertIn("Observed native command failures 22, omitted records 2.", text)

    def test_opaque_shell_hints_coalesce_per_session_bucket(self):
        hint = {"kind": "session", "operation": "PostToolUse", "host": "codex",
                "session_id": "main-session", "tool": "Bash", "status": "unknown"}
        transcript = self.root / "session.jsonl"
        identities = set()
        with mock.patch.object(retrospective, "resume", return_value={"observer_started": False}), \
                mock.patch.object(retrospective.time, "time", return_value=101):
            for index in range(100):
                transcript.write_text("x" * index, encoding="utf-8")
                event = {**hint, "source_id": f"event-{index}", "tool_use_id": f"tool-{index}",
                         "turn_id": f"turn-{index}", "duration_ms": index,
                         "transcript_status": "available" if index % 2 else "missing"}
                if index % 2:
                    event["transcript_path"] = str(transcript)
                result = retrospective.emit(self.root, event)
                self.assertTrue(result["queued"], result)
                self.assertEqual(event["source_id"], f"event-{index}")
                identities.add(result["event_id"])
        self.assertEqual(len(identities), 1)
        directory = work_queue._directory(self.root)
        self.assertEqual(len(list((directory / "inbox").glob("*.json"))), 1)
        retrospective._import_inbox(self.root, directory, 64)
        job = work_queue.status(self.root)["jobs"][0]
        self.assertEqual(job["payload"]["refresh_after"], 110)
        self.assertNotIn("transcript_revision", job["payload"])
        self.assertNotIn("transcript_path", job["payload"]["event"])
        with mock.patch.object(retrospective.time, "time", return_value=110):
            following = self.emit(**hint)
        self.assertNotIn(following["event_id"], identities)
        self.assertEqual(len(work_queue.status(self.root)["jobs"]), 2)

    def test_opaque_refresh_waits_for_late_failure_and_keeps_immediate_events(self):
        clock = {"wall": 105.0, "monotonic": 0.0}
        hint = {"kind": "session", "operation": "PostToolUse", "host": "codex",
                "session_id": "main-session", "tool": "Bash", "status": "unknown"}
        failures, batches, sleeps = [], [], []
        original_collect = retrospective.collect

        def collect(root, batch):
            batches.append((clock["wall"], [job["payload"]["event"]["operation"] for job in batch]))
            return original_collect(root, batch)

        def sleep(seconds):
            sleeps.append(seconds)
            clock["wall"] += seconds
            clock["monotonic"] += seconds
            if clock["wall"] >= 109 and not failures:
                failures.append({"kind": "failure", "observation_source": "native_item_completed",
                                 "lane": "codex:main-session", "event_id": "late-command", "at": 109,
                                 "exit_code": 7, "status": "failed"})
                self.emit(**hint, source_id="last-hint")

        def observe(*args, **kwargs):
            return {"coverage": {"state": "unavailable"}, "usage": None,
                    "deliveries": [], "events": list(failures)}

        with mock.patch.object(retrospective.time, "time", side_effect=lambda: clock["wall"]), \
                mock.patch.object(retrospective.time, "monotonic", side_effect=lambda: clock["monotonic"]), \
                mock.patch.object(retrospective.time, "sleep", side_effect=sleep), \
                mock.patch.object(retrospective, "collect", side_effect=collect), \
                mock.patch.dict(sys.modules, {"session_cost": SimpleNamespace(observe=observe)}):
            self.emit(**hint)
            self.emit(kind="session", operation="Stop", host="codex", session_id="main-session")
            self.emit(kind="failure", operation="PostToolUse", host="codex", session_id="main-session",
                      tool="Bash", status="failed", exit_code=7)
            self.emit(kind="completion", operation="ready")
            self.assertEqual(self.run_observer()["completed"], 4)
        self.assertEqual(batches[0][0], 105)
        self.assertCountEqual(batches[0][1], ["Stop", "PostToolUse", "ready"])
        self.assertEqual(batches[1], (110, ["PostToolUse"]))
        self.assertEqual(len(batches), 2)
        self.assertTrue(all(0 < seconds <= 1 for seconds in sleeps))
        saved = retrospective.report(self.root)
        self.assertEqual(saved["queue"], {"completed": 4})
        self.assertEqual(saved["latest"]["native_failures"]["records"][0]["exit_code"], 7)

    def test_deferred_refresh_respects_worker_budget_and_recovers_claim(self):
        clock = {"wall": 101.0, "monotonic": 0.0}
        settings = {**retrospective.policy(), "run_seconds": 2}
        observation = {"coverage": {"state": "unavailable"}, "usage": None,
                       "deliveries": [], "events": []}

        def sleep(seconds):
            clock["wall"] += seconds
            clock["monotonic"] += seconds

        with mock.patch.object(retrospective, "policy", return_value=settings), \
                mock.patch.object(retrospective.time, "time", side_effect=lambda: clock["wall"]), \
                mock.patch.object(retrospective.time, "monotonic", side_effect=lambda: clock["monotonic"]), \
                mock.patch.object(retrospective.time, "sleep", side_effect=sleep), \
                mock.patch.dict(sys.modules, {"session_cost": SimpleNamespace(observe=lambda *a, **k: observation)}):
            self.emit(kind="session", operation="PostToolUse", host="codex", session_id="main-session",
                      tool="Bash", status="unknown")
            with mock.patch.object(retrospective, "resume", return_value={"observer_started": False}) as resume:
                self.assertEqual(retrospective.run(self.root)["completed"], 0)
            self.assertEqual(clock["monotonic"], 2)
            resume.assert_called_once_with(self.root)
            with work_queue._state(self.root) as state:
                job = state["jobs"][0]
                self.assertEqual((job["state"], job["attempts"]), ("pending", 0))
                job.update(state="claimed", attempts=1)
            clock.update(wall=110, monotonic=3)
            self.assertEqual(self.run_observer()["completed"], 1)
        job = work_queue.status(self.root)["jobs"][0]
        self.assertEqual((job["state"], job["attempts"]), ("completed", 2))

    def test_large_terminal_queue_does_not_respawn_an_idle_observer(self):
        for index in range(20):
            work_queue.enqueue(self.root, "retro", f"terminal-{index}",
                               {"origin": work_queue.AUTOMATIC_RETRO_ORIGIN, "metadata": "x" * 60000})
        with work_queue._state(self.root) as state:
            for job in state["jobs"]:
                job["state"] = "completed"
        self.assertTrue(retrospective._has_work(work_queue._directory(self.root)))
        with mock.patch.object(retrospective, "resume") as resume:
            result = retrospective.run(self.root)
        self.assertEqual(result, {"status": "collected", "completed": 0, "observer_started": False})
        resume.assert_not_called()

    def test_older_batches_preserve_newer_method_session_and_usage_views(self):
        directory = work_queue._directory(self.root) / "observations"
        directory.mkdir()

        def job(at, label, tokens):
            return {"events": [{"at": at}], "payload": {"worktree": str(self.root),
                "event": {"task": "TK-001", "operation": "ready", "host": "codex", "session_id": "main",
                          "transcript_path": str(self.root / label)},
                "snapshot": {"receipt_sha256": label, "host": "codex", "session_id": "review",
                             "usage": {"input_tokens": tokens}, "packet_sha256": label,
                             "methods": [{"name": "data-design", "skill_sha256": label}]}}}

        retrospective.tracking(directory, [job(200, "new", 20)])
        result = retrospective.tracking(directory, [job(100, "old", 10)])
        task = result["tasks"][0]
        self.assertEqual(task["methods"]["data-design"]["skill_sha256"], "new")
        self.assertEqual(set(task["receipts"]), {"old", "new"})
        sessions = {row["session_id"]: row for row in result["sessions"]}
        self.assertEqual(sessions["main"]["path"], str(self.root / "new"))
        self.assertEqual(sessions["review"]["packet_sha256"], "new")
        self.assertEqual(sessions["review"]["usage"], {"input_tokens": 20})


    def test_task_identity_coalesces_worktrees_and_existing_duplicates(self):
        directory = work_queue._directory(self.root) / "observations"
        directory.mkdir()
        worker = str(self.root / "task-worktree")
        main = str(self.root)
        old = {"task": "TK-001", "worktree": worker, "last_observed_at": 100,
               "last_operation": "merge", "receipts": {"proof": {"commands": [{"seconds": 3}]}},
               "methods": {"review": {"name": "review", "skill_sha256": "old"}}}
        ready = {"task": "TK-001", "worktree": main, "last_observed_at": 200,
                 "last_operation": "ready", "requested_at": "2026-11-04T00:00:00Z",
                 "ready_at": "2026-11-04T00:00:10Z", "receipts": {"review": {}},
                 "methods": {"review": {"name": "review", "skill_sha256": "new"}}}
        other = {"task": "TK-002", "worktree": worker, "last_observed_at": 150,
                 "receipts": {}, "methods": {}}
        session = {"host": "codex", "session_id": "review-session", "worktree": worker,
                   "last_observed_at": 100, "packet_sha256": "packet"}
        (directory / "tracking.json").write_text(json.dumps({"tasks": [ready, other, old],
                                                           "sessions": [session]}), encoding="utf-8")
        migrated = retrospective.tracking(directory, [])
        tasks = {row["task"]: row for row in migrated["tasks"]}
        self.assertEqual(set(tasks), {"TK-001", "TK-002"})
        self.assertEqual(set(tasks["TK-001"]["receipts"]), {"proof", "review"})
        self.assertEqual(tasks["TK-001"]["methods"]["review"]["skill_sha256"], "new")
        self.assertEqual(set(tasks["TK-001"]["worktrees"]), {main, worker})
        self.assertEqual(migrated["sessions"][0]["worktree"], worker)
        events = [{"kind": "call", "task": "TK-001", "tokens": {"input": 10, "cache_read": 5,
                   "cache_write": 0, "output": 1}}]
        observation = {"coverage": {"state": "observed"}, "usage": {"input_including_cache": 15,
                       "output": 1}, "deliveries": [], "events": events}
        with mock.patch.dict(sys.modules, {"session_cost": SimpleNamespace(observe=lambda *a, **k: observation)}):
            report = retrospective.collect(self.root, [])
        metrics = {row["task"]: row for row in report["task_metrics"]}
        self.assertEqual(metrics["TK-001"]["request_to_ready_seconds"], 10)
        self.assertEqual(metrics["TK-001"]["proof_seconds"], 3)
        self.assertEqual(sum(row["usage"]["input_including_cache"] for row in metrics.values() if row["usage"]), 15)
        (directory / "tracking.json").unlink()
        jobs = [{"events": [{"at": at}], "payload": {"worktree": worktree, "event": {
            "task": "TK-001", "operation": operation}, "snapshot": {}}}
            for at, worktree, operation in ((100, worker, "merge"), (200, main, "ready"))]
        fresh = retrospective.tracking(directory, jobs)
        self.assertEqual(len(fresh["tasks"]), 1)
        self.assertEqual(set(fresh["tasks"][0]["worktrees"]), {main, worker})


if __name__ == "__main__":
    unittest.main()
