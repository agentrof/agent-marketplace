from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
import work_queue  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402
from tools.tests.levels import integration  # noqa: E402


@integration
class WorkQueueTests(unittest.TestCase):
    def test_shared_dedup_fencing_reconciliation_and_budgets(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "main"
            linked = Path(raw) / "linked"
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c",
                            "user.email=test@example.invalid", "commit", "-qm", "base", "--allow-empty"], check=True)
            subprocess.run(["git", "-C", str(root), "worktree", "add", "-qb", "worker", str(linked)], check=True)
            with mock.patch.object(work_queue.time, "time", return_value=1000) as clock:
                retro = work_queue.enqueue(root, "retro", "batch:1", {"batch": 1})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(work_queue.enqueue, path, "resolve", "issue:1", {"issue": 1})
                               for path in (root, linked)]
                    jobs = [future.result() for future in futures]
                self.assertEqual(jobs[0]["id"], jobs[1]["id"])
                first = jobs[0]
                second = work_queue.enqueue(linked, "resolve", "issue:2", {"issue": 2})
                with self.assertRaises(ValueError):
                    work_queue.enqueue(root, "resolve", "issue:1", {"issue": 9})
                with self.assertRaises(ValueError):
                    work_queue.enqueue(root, "resolve", "../escape", {})
                with self.assertRaises(ValueError):
                    work_queue.enqueue(root, "detect", "large", {"data": "x" * work_queue.MAX_BYTES})

                def claim(path, owner, deadline=1100, attempts=3):
                    return work_queue.claim(path, owner, ["resolve"], 1, deadline, 10, attempts)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(claim, path, owner)
                               for path, owner in ((root, "owner-a"), (linked, "owner-b"))]
                    claims = [future.result() for future in futures]
                claimed = [job for job in claims if job is not None]
                self.assertEqual(len(claimed), 1)
                self.assertEqual(claimed[0]["id"], first["id"])
                old_token = claimed[0]["lease_token"]
                clock.return_value = 1011
                with self.assertRaises(ValueError):
                    work_queue.finish(linked, first["id"], old_token, "completed", {"result": "late"})
                self.assertEqual(work_queue.status(root)["jobs"][1]["state"], "needs_reconciliation")
                self.assertIsNone(claim(root, "owner-c"))
                with self.assertRaises(ValueError):
                    work_queue.reconcile(root, first["id"], "retry", {})
                work_queue.reconcile(root, first["id"], "retry", {"observed": "old worker stopped; no write occurred"})
                recovered = claim(linked, "owner-c")
                self.assertNotEqual(recovered["lease_token"], old_token)
                with self.assertRaises(ValueError):
                    work_queue.finish(root, first["id"], old_token, "failed", {})
                receipt = work_queue.finish(root, first["id"], recovered["lease_token"], "unknown",
                                            {"request_id": "external-1"})
                self.assertEqual(receipt["state"], "needs_reconciliation")
                self.assertEqual(work_queue.finish(root, first["id"], recovered["lease_token"], "unknown",
                                                  {"request_id": "external-1"}), receipt)
                clock.return_value = 2000
                self.assertEqual(work_queue.status(linked)["jobs"][1]["state"], "needs_reconciliation")
                self.assertIsNone(claim(root, "owner-d", deadline=2100))
                work_queue.reconcile(linked, first["id"], "completed", {"observed": "external-1 committed"})
                self.assertEqual(work_queue.enqueue(root, "resolve", "issue:1", {"issue": 1})["state"], "completed")

                for attempt in (1, 2):
                    current = claim(root, "owner-d", deadline=2100, attempts=2)
                    self.assertEqual(current["id"], second["id"])
                    self.assertEqual(current["attempts"], attempt)
                    result = work_queue.finish(linked, second["id"], current["lease_token"], "failed",
                                               {"observed": "no external write occurred"})
                    self.assertEqual(result["state"], "pending" if attempt == 1 else "failed")
                self.assertIsNone(claim(linked, "owner-e", deadline=2100, attempts=10))
                with mock.patch.object(work_queue, "_state") as storage:
                    self.assertIsNone(claim(root, "owner-e", deadline=1999))
                    storage.assert_not_called()
                final = work_queue.status(root)
                self.assertEqual(len(final["jobs"]), 3)
                self.assertEqual(final["jobs"][0]["id"], retro["id"])
                self.assertEqual(final["jobs"][0]["state"], "pending")
                event_ids = [event["id"] for job in final["jobs"] for event in job["events"]]
                self.assertEqual(len(event_ids), len(set(event_ids)))
                previous = work_queue.enqueue(root, "resolve", "previous:3", {"grant": "previous"})
                current = work_queue.enqueue(root, "resolve", "current:4", {"grant": "current"})
                selected = work_queue.claim(linked, "owner-f", ["resolve", "retro"], 1, 2100, 10, 1,
                                            scope="current")
                self.assertEqual(selected["id"], current["id"])
                work_queue.finish(root, selected["id"], selected["lease_token"], "unknown", {})
                self.assertIsNone(work_queue.claim(root, "owner-g", ["resolve"], 1, 2100, 10, 1,
                                                   scope="previous"))
                work_queue.reconcile(root, selected["id"], "completed", {"observed": "write committed"})
                self.assertIsNone(work_queue.claim(root, "owner-g", ["resolve", "retro"], 1, 2100, 10, 1,
                                                   scope="current"))
                selected = work_queue.claim(root, "owner-g", ["resolve"], 1, 2100, 10, 1,
                                            scope="previous")
                self.assertEqual(selected["id"], previous["id"])
                other = Path(raw) / "other"
                init_repository(other)
                self.assertEqual(work_queue.status(other)["jobs"], [])
                with mock.patch.object(work_queue, "MAX_JOBS", 3):
                    for key in ("done", "failed", "held"):
                        work_queue.enqueue(other, "resolve", key, {"resource_key": "repo:1"})
                        job = work_queue.claim(other, "owner", ["resolve"], 3, 2100, 10, 1)
                        outcome = {"done": "completed", "failed": "failed", "held": "unknown"}[key]
                        work_queue.finish(other, job["id"], job["lease_token"], outcome, {})
                    replace = work_queue.atomic_file.replace_bytes

                    def interrupt_state_write(path, *args, **kwargs):
                        if path.name == "state.json":
                            raise OSError("interrupted before state replacement")
                        return replace(path, *args, **kwargs)

                    with mock.patch.object(work_queue.atomic_file, "replace_bytes", side_effect=interrupt_state_write):
                        with self.assertRaisesRegex(OSError, "interrupted"):
                            work_queue.enqueue(other, "resolve", "next", {"resource_key": "repo:1"})
                    self.assertEqual(len(work_queue.status(other)["jobs"]), 3)
                    work_queue.enqueue(other, "resolve", "next", {"resource_key": "repo:1"})
                    hot = work_queue.status(other)["jobs"]
                    self.assertEqual({job["key"]: job["state"] for job in hot},
                                     {"held": "needs_reconciliation", "next": "pending"})
                    for key, state in (("done", "completed"), ("failed", "failed")):
                        self.assertEqual(work_queue.enqueue(other, "resolve", key,
                                                            {"resource_key": "repo:1"})["state"], state)
                    with self.assertRaisesRegex(ValueError, "different payload"):
                        work_queue.enqueue(other, "resolve", "done", {"resource_key": "repo:2"})
                    self.assertIsNone(work_queue.claim(other, "owner", ["resolve"], 10, 2100, 10, 1))
                    work_queue.enqueue(other, "detect", "extra", {})
                    with self.assertRaisesRegex(ValueError, "active or unresolved"):
                        work_queue.enqueue(other, "detect", "overflow", {})


if __name__ == "__main__":
    unittest.main()
