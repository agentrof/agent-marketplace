from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import task_lane  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402
from tools.tests.levels import integration  # noqa: E402


@integration
class ReadinessTests(unittest.TestCase):
    def test_ready_verifies_integration_and_preserves_separate_shared_history(self):
        with tempfile.TemporaryDirectory() as raw:
            root, linked = Path(raw) / "main", Path(raw) / "linked"
            init_repository(root, initial_branch="main")
            actual_run = task_lane.run

            def git(*args):
                return actual_run("git", "-c", "user.name=Test", "-c",
                                  "user.email=test@example.invalid", *args, cwd=root)

            path = root / task_lane.TASKS / "tk-004-export.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "---\ntype: task\nid: TK-004\ntitle: Export\nstatus: active\nsize: s\nrigor: normal\n"
                "requested_at: 2026-10-16T10:00:00+00:00\napproved_at: 2026-10-16T10:01:00+00:00\n"
                "---\n## Request\n\nExport records.\n", encoding="utf-8")
            (root / "workspace/config.json").write_text("{}\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("commit", "-qm", "integrated task", "--allow-empty")
            merged = git("rev-parse", "HEAD")
            git("commit", "-qm", "deployment revision", "--allow-empty")
            revision = git("rev-parse", "HEAD")
            git("worktree", "add", "-qb", "readiness", str(linked))
            evidence = linked / "readiness.txt"
            evidence.write_text("Preview smoke probe passed for the deployment revision.\n", encoding="utf-8")
            (Path(raw) / "outside.txt").write_text("outside", encoding="utf-8")
            legacy = linked / task_lane.HISTORY
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"type":"legacy-observation"}\n', encoding="utf-8")
            remote = {"number": 9, "state": "OPEN", "mergeCommit": {"oid": merged},
                      "mergedAt": "2026-10-16T10:10:00Z"}
            calls = []

            def run(*args, **kwargs):
                if args[0] == "gh":
                    calls.append(args)
                    self.assertEqual(args[:3], ("gh", "pr", "view"))
                    return json.dumps(remote)
                return actual_run(*args, **kwargs)

            args = SimpleNamespace(task="TK-004", pr="9", revision=revision,
                                   environment="preview", evidence="readiness.txt")
            report_args = SimpleNamespace(since=None, recent=20, no_sessions=True)

            def gather():
                timing = {"pr": "9", "pr_opened": "2026-10-16T10:04:00Z",
                          "ci_green": "2026-10-16T10:08:00Z", "review": "2026-10-16T10:09:00Z",
                          "merged": remote["mergedAt"], "red": []}
                with mock.patch.object(task_lane, "task_records", return_value=[path]), \
                        mock.patch.object(task_lane, "pull_requests", return_value={"TK-004": {"number": 9}}), \
                        mock.patch.object(task_lane, "pr_times", return_value=timing), \
                        mock.patch.object(task_lane, "cost_method", return_value=None):
                    return task_lane.gather(root, report_args)

            with mock.patch.object(task_lane, "run", side_effect=run), \
                    mock.patch("retrospective.emit", return_value={"queued": True, "event_id": "ready-observation"}), \
                    mock.patch.object(task_lane, "now", return_value="2026-10-16T10:20:00+00:00") as clock:
                self.assertIsNone(gather()["per_task"][0]["ready_at"])
                with self.assertRaisesRegex(task_lane.Refused, "merged pull request"):
                    task_lane.cmd_ready(args, linked)
                remote["state"] = "MERGED"
                for name in ("missing.txt", "../outside.txt", str(evidence)):
                    with self.subTest(evidence=name), self.assertRaisesRegex(task_lane.Refused, "project-relative"):
                        task_lane.cmd_ready(SimpleNamespace(**{**vars(args), "evidence": name}), linked)
                with self.assertRaises(task_lane.Refused):
                    task_lane.cmd_ready(SimpleNamespace(**{**vars(args), "revision": base}), linked)
                self.assertFalse((root / task_lane.HISTORY).exists())
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(task_lane.main([
                        "--project", str(linked), "ready", "--task", "TK-004", "--pr", "9",
                        "--revision", revision, "--environment", "preview", "--evidence", "readiness.txt"]), 0)
                receipt = json.loads(output.getvalue())
                self.assertEqual(receipt["evidence_source"], "worker_report")
                self.assertIn("retro_job", receipt)
                self.assertNotIn("retro_queue_error", receipt)
                self.assertEqual(receipt["merge_commit"], merged)
                self.assertEqual(receipt["revision"], revision)
                self.assertEqual(receipt["evidence_sha256"], hashlib.sha256(evidence.read_bytes()).hexdigest())
                clock.return_value = "2026-10-16T10:21:00+00:00"
                self.assertEqual(task_lane.cmd_ready(args, linked), receipt)
                self.assertEqual(legacy.read_text(encoding="utf-8"), '{"type":"legacy-observation"}\n')
                self.assertEqual(len(task_lane.history(root)), 1)
                self.assertEqual(len(task_lane.history(linked)), 2)
                report = gather()
                row = report["per_task"][0]
                self.assertEqual(row["total"], 10.0)
                self.assertEqual(row["request_to_ready_minutes"], 20.0)
                self.assertEqual(row["ready_at"], "2026-10-16T10:20:00+00:00")
                self.assertEqual(row["ready_environment"], "preview")
                self.assertEqual(report["request_to_ready_minutes"]["count"], 1)
                self.assertEqual(report["request_to_merge_minutes"]["average"], 10.0)
                self.assertIn("request to ready 20.0 min", task_lane.render(report))
                self.assertIn("ready_environment", task_lane.export(report, "csv").splitlines()[0])
                task_lane.remember(root, report)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(task_lane.append_history, checkout, [{"type": "parallel", "value": value}])
                               for value, checkout in enumerate((root, linked))]
                    for future in futures:
                        future.result()
                records = task_lane.history(root)
                self.assertEqual(sum(item.get("type") == "task_ready" for item in records), 1)
                self.assertEqual(sum(item.get("type") == "parallel" for item in records), 2)
                for changes in ({"ready_at": "2026-10-16T10:09:00+00:00"},
                                {"pr": "8"}, {"evidence_source": "host_observation"}):
                    with mock.patch.object(task_lane, "history", return_value=[{**receipt, **changes}]):
                        task_lane.attach_readiness(root, [row])
                    self.assertIsNone(row["ready_at"])
                    self.assertIsNone(row["request_to_ready_minutes"])
                    self.assertEqual(row["total"], 10.0)
                self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
