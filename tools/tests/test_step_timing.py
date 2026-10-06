"""Per-step timing (`step_timing` at `recorded`, budgets from `step_budgets`,
#441): spans for flow steps and role spawns land in a durable run file, a span
that ends over its budget is reported at once with its phase breakdown,
largest contributor and lever, the run report compares every step with its
budget, and at `off` nothing is written."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import step_timing  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

ON = {"step_timing": {"value": "recorded", "default": "off", "source": "policy"},
      "step_budgets": {"value": "enforced", "default": "off", "source": "policy",
                       "parameters": {"backlog_revision": 10, "confirmation_rereview": 3}}}
NO_BUDGETS = {"step_timing": {"value": "recorded", "default": "off", "source": "policy"}}
OFF = {"step_timing": {"value": "off", "default": "off", "source": "default"}}


def at(minute: float) -> str:
    seconds = int(minute * 60)
    return f"2026-01-01T{10 + seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}Z"


class StepTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve() / "project"
        self.root.mkdir()
        init_repository(self.root)

    def run_file(self, run: str = "r1") -> Path:
        return self.root / ".agentrof/agent-marketplace/timing" / f"{run}.jsonl"

    def start(self, step, minute, values=ON, **kwargs):
        return step_timing.start(self.root, values, run="r1", step=step, at=at(minute), **kwargs)

    def end(self, span, minute, values=ON, **kwargs):
        return step_timing.end(self.root, values, run="r1", span=span, at=at(minute), **kwargs)

    def test_off_writes_nothing(self) -> None:
        self.assertEqual(self.start("author", 0, values=OFF), {"ok": True, "recorded": False})
        self.assertEqual(self.end("author#1", 1, values=OFF), {"ok": True, "recorded": False})
        self.assertFalse((self.root / ".agentrof").exists())

    def test_cli_with_no_policy_is_off(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = step_timing.main(["start", "--run", "r1", "--step", "author",
                                     "--project-root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "recorded": False})
        self.assertFalse((self.root / ".agentrof").exists())

    def test_records_are_durable_outside_runtime_scratch(self) -> None:
        span = self.start("author", 0)["span"]
        self.end(span, 2, metrics={"manifest_bytes": 900})
        path = self.run_file()
        self.assertTrue(path.is_file())
        self.assertNotIn(".runtime", path.parts)
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event"] for event in events], ["start", "end"])
        self.assertEqual(events[1]["metrics"], {"manifest_bytes": 900})

    def test_span_ids_are_deterministic_per_step_and_role(self) -> None:
        step = self.start("review", 0)["span"]
        first = self.start("review", 0, kind="spawn", role="backlog-reviewer", parent=step,
                           phase="review")["span"]
        self.end(first, 1)
        second = self.start("review", 1, kind="spawn", role="backlog-reviewer", parent=step,
                            phase="re_review")["span"]
        self.assertEqual((step, first, second),
                         ("review#1", "review/backlog-reviewer#1", "review/backlog-reviewer#2"))

    def test_a_linked_timing_folder_or_run_file_is_refused(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        folder = self.root / ".agentrof/agent-marketplace"
        folder.mkdir(parents=True)
        (folder / "timing").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(step_timing.Refused) as caught:
            self.start("author", 0)
        self.assertEqual(caught.exception.code, "TIMING_UNSAFE_PATH")
        self.assertEqual(list(outside.iterdir()), [])
        (folder / "timing").unlink()
        (folder / "timing").mkdir()
        victim = self.root.parent / "victim.txt"
        self.run_file().symlink_to(victim)
        with self.assertRaises(step_timing.Refused) as caught:
            self.start("author", 0)
        self.assertEqual(caught.exception.code, "TIMING_UNSAFE_PATH")
        self.assertFalse(victim.exists())

    def test_an_event_missing_its_keys_is_corrupt(self) -> None:
        self.run_file().parent.mkdir(parents=True)
        for line in ('{"event": "start"}', '{"event": "end", "at": "x"}', '{"span": "a#1"}', "[1]"):
            with self.subTest(line=line):
                self.run_file().write_text(line + "\n", encoding="utf-8")
                with self.assertRaises(step_timing.Refused) as caught:
                    self.start("author", 0)
                self.assertEqual(caught.exception.code, "TIMING_CORRUPT")

    def test_an_active_delivery_reads_its_pinned_policy(self) -> None:
        import process_policy
        from unittest import mock
        pinned = {"values": ON, "policy": {}, "source": "pinned"}
        current = (OFF, {})
        with mock.patch.object(process_policy, "delivery_values", return_value=pinned) as read, \
                mock.patch.object(process_policy, "effective_values", return_value=current):
            self.assertEqual(step_timing.policy_values(self.root, "DLV-001"), ON)
            self.assertEqual(step_timing.policy_values(self.root), OFF)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = step_timing.main(["start", "--run", "r1", "--step", "author",
                                         "--project-root", str(self.root), "--delivery", "DLV-001",
                                         "--at", at(0)])
            self.assertEqual(code, 0, output.getvalue())
            self.assertTrue(json.loads(output.getvalue())["recorded"])
            self.assertEqual(read.call_args.args[1], "DLV-001")

    def test_refusals(self) -> None:
        with self.assertRaises(step_timing.Refused) as caught:
            self.start("spawned", 0, kind="spawn", role="qa-engineer")
        self.assertEqual(caught.exception.code, "TIMING_SPAWN_NEEDS_PARENT")
        with self.assertRaises(step_timing.Refused) as caught:
            self.end("missing#1", 1)
        self.assertEqual(caught.exception.code, "TIMING_UNKNOWN_SPAN")
        span = self.start("author", 0)["span"]
        self.end(span, 1)
        with self.assertRaises(step_timing.Refused) as caught:
            self.end(span, 2)
        self.assertEqual(caught.exception.code, "TIMING_SPAN_ENDED")
        later = self.start("later", 5)["span"]
        with self.assertRaises(step_timing.Refused) as caught:
            self.end(later, 4)
        self.assertEqual(caught.exception.code, "TIMING_BAD_TIME")

    def test_within_budget_reports_no_overrun(self) -> None:
        span = self.start("backlog_revision", 0)["span"]
        result = self.end(span, 10)
        self.assertNotIn("overrun", result)
        self.assertEqual(result["seconds"], 600.0)

    def test_overrun_is_reported_at_end_with_breakdown_and_lever(self) -> None:
        step = self.start("backlog_revision", 0)["span"]
        reader = self.start("backlog_revision", 0, kind="spawn", role="backlog-reviewer",
                            parent=step, phase="review")["span"]
        writer = self.start("backlog_revision", 0, kind="spawn", role="product-owner",
                            parent=step, phase="writing")["span"]
        self.end(writer, 4)
        self.end(reader, 9)
        wait = self.start("backlog_revision", 9, kind="spawn", parent=step, phase="waiting",
                          role="coordinator")["span"]
        self.end(wait, 12)
        result = self.end(step, 14)
        found = result["overrun"]
        self.assertEqual(found["budget"], "backlog_revision")
        self.assertEqual(found["budget_minutes"], 10)
        self.assertEqual(found["over_minutes"], 4.0)
        # Writer and reader ran in parallel, so the children sum past the
        # step's wall clock and leave no unattributed rest.
        self.assertEqual(found["by_phase"], {"review": 540.0, "waiting": 180.0, "writing": 240.0})
        self.assertEqual(found["largest_phase"], "review")
        self.assertEqual(found["largest_contributor"]["span"], reader)
        self.assertIn("review_scope impact_closure", found["lever"])
        events = [json.loads(line) for line in self.run_file().read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "overrun")
        self.assertEqual(events[-1]["span"], step)

    def test_unattributed_rest_is_a_contributor(self) -> None:
        step = self.start("confirmation_rereview", 0)["span"]
        reader = self.start("confirmation_rereview", 0, kind="spawn", role="backlog-reviewer",
                            parent=step, phase="re_review")["span"]
        self.end(reader, 1)
        found = self.end(step, 5)["overrun"]
        self.assertEqual(found["by_phase"], {"re_review": 60.0, "unattributed": 240.0})
        self.assertEqual(found["largest_phase"], "unattributed")

    def test_explicit_budget_and_no_budget(self) -> None:
        span = self.start("4-challenge", 0, budget="confirmation_rereview")["span"]
        self.assertIn("overrun", self.end(span, 4))
        other = self.start("unbudgeted", 0)["span"]
        self.assertNotIn("overrun", self.end(other, 60))
        span = step_timing.start(self.root, NO_BUDGETS, run="r2", step="backlog_revision",
                                 at=at(0))["span"]
        self.assertNotIn("overrun", step_timing.end(self.root, NO_BUDGETS, run="r2", span=span,
                                                    at=at(59)))

    def test_open_span_past_budget_is_listed(self) -> None:
        self.start("confirmation_rereview", 0)
        self.assertEqual(step_timing.open_overruns(self.root, ON, "r1", at(2))["overruns"], [])
        late = step_timing.open_overruns(self.root, ON, "r1", at(4))["overruns"]
        self.assertEqual([item["span"] for item in late], ["confirmation_rereview#1"])

    def test_report_compares_each_step_with_its_budget_and_is_kept(self) -> None:
        within = self.start("backlog_revision", 0)["span"]
        self.end(within, 8)
        over = self.start("confirmation_rereview", 8)["span"]
        self.end(over, 12, metrics={"closure_size": 3})
        self.start("unbudgeted", 12)
        result = step_timing.report(self.root, ON, "r1", write=True)
        verdicts = {row["span"]: row["verdict"] for row in result["steps"]}
        self.assertEqual(verdicts, {within: "within", over: "over", "unbudgeted#1": "open"})
        self.assertEqual(result["over_budget"], [over])
        self.assertEqual(result["open"], ["unbudgeted#1"])
        self.assertEqual(result["wall_minutes"], 12.0)
        row = next(row for row in result["steps"] if row["span"] == over)
        self.assertEqual(row["metrics"], {"closure_size": 3})
        kept = self.root / result["path"]
        self.assertEqual(kept.name, "r1.report.json")
        self.assertEqual(json.loads(kept.read_text())["over_budget"], [over])

    def test_runs_lists_run_files(self) -> None:
        self.start("a", 0)
        step_timing.start(self.root, ON, run="r2", step="b", at=at(0))
        self.assertEqual(step_timing.runs(self.root)["runs"], ["r1", "r2"])


if __name__ == "__main__":
    unittest.main()
