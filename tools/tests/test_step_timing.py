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
from tools.tests.levels import integration
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
# step_baseline at median: the median of the last 3 finished spans, once 2 exist, times 1.5.
MEDIAN = {"value": "median", "default": "off", "source": "policy",
          "parameters": {"runs": 3, "min_samples": 2, "ratio_percent": 150}}
BASELINE = {**NO_BUDGETS, "step_baseline": MEDIAN}
BASELINE_AND_BUDGETS = {**ON, "step_baseline": MEDIAN}


def at(minute: float) -> str:
    seconds = int(minute * 60)
    return f"2026-01-01T{10 + seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}Z"


class StepTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve() / "project"
        self.root.mkdir()

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

    @integration
    def test_cli_with_no_policy_is_off(self) -> None:
        init_repository(self.root)
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

    def history_of(self, step: str, *minutes: float, values=BASELINE) -> None:
        """Earlier runs of *step*, one run file each, lasting *minutes* in turn, all before minute 0 of r1."""
        for index, length in enumerate(minutes):
            run = f"past{index}"
            span = step_timing.start(self.root, values, run=run, step=step,
                                     at=f"2025-12-01T{index:02d}:00:00Z")["span"]
            step_timing.end(self.root, values, run=run, span=span,
                            at=f"2025-12-01T{index:02d}:{int(length):02d}:{int(length * 60) % 60:02d}Z")

    def test_a_step_far_past_its_own_median_is_over_without_a_fixed_maximum(self) -> None:
        self.history_of("live_run:smoke", 10, 10, 12)
        within = self.start("live_run:smoke", 0, values=BASELINE)["span"]
        self.assertNotIn("overrun", self.end(within, 15, values=BASELINE))
        # The 15-minute run joins the window: the last three are 10, 12 and 15.
        over = self.start("live_run:smoke", 15, values=BASELINE)["span"]
        found = self.end(over, 35, values=BASELINE)["overrun"]
        self.assertEqual((found["basis"], found["baseline_minutes"], found["threshold_minutes"],
                          found["over_minutes"], found["budget"]), ("baseline", 12.0, 18.0, 2.0, None))

    def test_the_baseline_reads_only_the_last_runs_and_needs_its_samples(self) -> None:
        self.history_of("live_step:a", 50)
        span = self.start("live_step:a", 0, values=BASELINE)["span"]
        self.assertNotIn("overrun", self.end(span, 59, values=BASELINE))
        self.history_of("live_step:b", 50, 50, 4, 4, 4)
        span = self.start("live_step:b", 0, values=BASELINE)["span"]
        self.assertEqual(self.end(span, 7, values=BASELINE)["overrun"]["baseline_minutes"], 4.0)
        # At off the same history reports nothing.
        span = step_timing.start(self.root, NO_BUDGETS, run="r3", step="live_step:b", at=at(0))["span"]
        self.assertNotIn("overrun", step_timing.end(self.root, NO_BUDGETS, run="r3", span=span, at=at(59)))

    def test_the_larger_of_maximum_and_baseline_applies(self) -> None:
        self.history_of("confirmation_rereview", 1, 1, values=BASELINE_AND_BUDGETS)
        span = self.start("confirmation_rereview", 0, values=BASELINE_AND_BUDGETS)["span"]
        found = self.end(span, 4, values=BASELINE_AND_BUDGETS)["overrun"]
        self.assertEqual((found["basis"], found["threshold_minutes"], found["baseline_minutes"]),
                         ("budget", 3.0, 1.0))

    def test_an_open_watched_step_shows_what_it_is_doing(self) -> None:
        self.history_of("live_run:smoke", 2, 2)
        scratch = self.root / ".agentrof/agent-marketplace/.runtime/scratch"
        scratch.mkdir(parents=True)
        (scratch / "live.log").write_text("case 1 passed\ncase 2 waiting for registry\n\n", encoding="utf-8")
        (scratch / "progress.json").write_text(json.dumps(
            {"state": "running", "elapsed_seconds": 300.0, "output_path": str(scratch / "live.log"),
             "adapter_observation": {"test_id": "case 2"}}), encoding="utf-8")
        watch = ".agentrof/agent-marketplace/.runtime/scratch/progress.json"
        self.start("live_run:smoke", 0, values=BASELINE, watch=watch)
        late = step_timing.open_overruns(self.root, BASELINE, "r1", at(5))["overruns"]
        self.assertEqual(late[0]["doing"], {"watch": watch, "last_line": "case 2 waiting for registry",
                                            "progress": {"state": "running", "elapsed_seconds": 300.0,
                                                         "adapter_observation": {"test_id": "case 2"}}})
        report = step_timing.report(self.root, BASELINE, "r1")
        self.assertEqual(report["steps"][0]["basis"], "baseline")
        with self.assertRaises(step_timing.Refused) as caught:
            self.start("other", 0, values=BASELINE, watch="../outside.json")
        self.assertEqual(caught.exception.code, "TIMING_BAD_WATCH")

    def test_runs_lists_run_files(self) -> None:
        self.start("a", 0)
        step_timing.start(self.root, ON, run="r2", step="b", at=at(0))
        self.assertEqual(step_timing.runs(self.root)["runs"], ["r1", "r2"])


if __name__ == "__main__":
    unittest.main()
