"""Lane table and lane isolation: a restarted session never relaunches a lane
that finished (`lane_table` at `recorded`), and a fix lane never works in, or
switches the branch of, the main checkout (`lane_isolation` at
`scratch_clone`). Also pins that the fixed-cost and fact-check switches bind
their references only at their non-default values (#432, #422)."""

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
import lane_table  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402


def call(module, argv: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = module.main(argv)
    return code, json.loads(output.getvalue())


@integration
class LaneTableTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        base = Path(temporary.name).resolve()
        self.main = base / "main"
        self.clone = base / "scratch" / "lane-a"
        init_repository(self.main, initial_branch="main")
        self.clone.mkdir(parents=True)

    def lanes(self, *argv: str) -> tuple[int, dict]:
        return call(lane_table, ["--root", str(self.main), *argv])

    def test_a_finished_lane_is_listed_and_never_started_again(self):
        code, _ = self.lanes("start", "--run", "run-1", "--lane", "a", "--workdir", str(self.clone))
        self.assertEqual(code, 0)
        self.lanes("start", "--run", "run-1", "--lane", "b", "--workdir", str(self.clone))
        self.lanes("finish", "--run", "run-1", "--lane", "a", "--state", "finished",
                   "--note", "branch fix-a pushed")
        code, pending = self.lanes("pending", "--run", "run-1")
        self.assertEqual((pending["finished"], pending["resume"], pending["retry"]), (["a"], ["b"], []))
        code, refused = self.lanes("start", "--run", "run-1", "--lane", "a", "--workdir", str(self.clone))
        self.assertEqual((code, refused["code"]), (2, "LANE_ALREADY_FINISHED"))
        self.assertIn("branch fix-a pushed", refused["message"])
        table = self.main / ".agentrof/agent-marketplace/.runtime/lanes/run-1.json"
        self.assertTrue(table.is_file())

    def test_a_failed_lane_may_start_again(self):
        self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(self.clone))
        self.lanes("finish", "--run", "r", "--lane", "a", "--state", "failed")
        self.assertEqual(self.lanes("pending", "--run", "r")[1]["retry"], ["a"])
        code, _ = self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(self.clone))
        self.assertEqual(code, 0)

    def test_the_default_isolation_accepts_the_main_checkout(self):
        code, _ = self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(self.main))
        self.assertEqual(code, 0)

    def test_scratch_clone_refuses_a_lane_inside_the_main_checkout(self):
        for workdir in (self.main, self.main / "sub"):
            with self.subTest(workdir=workdir.name):
                code, refused = self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(workdir),
                                           "--isolation", "scratch_clone", "--main", str(self.main))
                self.assertEqual((code, refused["code"]), (2, "LANE_IN_MAIN_CHECKOUT"))
        code, _ = self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(self.clone),
                             "--isolation", "scratch_clone", "--main", str(self.main))
        self.assertEqual(code, 0)

    def test_check_refuses_when_the_main_checkout_branch_changed(self):
        self.lanes("start", "--run", "r", "--lane", "a", "--workdir", str(self.clone),
                   "--isolation", "scratch_clone", "--main", str(self.main))
        self.assertEqual(self.lanes("check", "--run", "r", "--main", str(self.main))[0], 0)
        subprocess.run(["git", "-C", str(self.main), "checkout", "-q", "-b", "lane-branch"],
                       check=True, capture_output=True)
        code, refused = self.lanes("check", "--run", "r", "--main", str(self.main))
        self.assertEqual((code, refused["code"]), (2, "LANE_MAIN_BRANCH_CHANGED"))


@integration
class FixedCostSwitchBindingTests(unittest.TestCase):
    """Each new switch binds its reference only at its non-default value."""

    CASES = (
        ("item_qa_tier", "change_tier_per_item", "deliver", "qa-engineer",
         "skill-content/qa-verification/references/switch-item_qa_tier-change_tier_per_item.md", ()),
        ("item_review_scale", "by_change_size", "deliver", "code-reviewer",
         "skill-content/code-review/references/switch-item_review_scale-by_change_size.md",
         ("changed_lines", "200")),
        ("item_cost_report", "per_step", "deliver", "delivery-coordinator",
         "skill-content/deliver/references/switch-item_cost_report-per_step.md", ()),
        ("lane_table", "recorded", "deliver", "delivery-coordinator",
         "skill-content/deliver/references/switch-lane_table-recorded.md", ()),
        ("lane_isolation", "scratch_clone", "deliver", "delivery-coordinator",
         "skill-content/deliver/references/switch-lane_isolation-scratch_clone.md", ()),
        ("requirement_fact_check", "pre_approval_reader", "requirement", "business-analyst",
         "skill-content/requirement/references/switch-requirement_fact_check-pre_approval_reader.md", ()),
    )

    def test_each_switch_binds_its_reference_only_at_its_value(self):
        for switch, value, entry, role, reference, parameter in self.CASES:
            with self.subTest(switch=switch):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(remove_temporary, temporary)
                project = Path(temporary.name).resolve()
                init_repository(project, initial_branch="main")
                docs = project / "workspace/docs"
                (docs / "maps").mkdir(parents=True)

                def commit() -> None:
                    for argv in (["config", "user.email", "test@example.com"], ["config", "user.name", "Test"],
                                 ["add", "--all"], ["commit", "-q", "--allow-empty", "-m", "fixture"]):
                        subprocess.run(["git", "-C", str(project), *argv], check=True, capture_output=True)

                def bound() -> bool:
                    result = task_inputs.manifest(entry=entry, role=role, mode="review", project=project)
                    return reference in result["required_reads"]

                commit()
                self.assertFalse(bound())
                self.assertEqual(call(process_policy, ["init", "--docs", str(docs)])[0], 0)
                self.assertEqual(call(process_policy, [
                    "set", "--docs", str(docs), "--switch", switch, "--value", value])[0], 0)
                if parameter:
                    self.assertEqual(call(process_policy, [
                        "set", "--docs", str(docs), "--switch", switch, "--parameter", parameter[0],
                        "--value", parameter[1]])[0], 0)
                self.assertEqual(call(process_policy, ["approve", "--docs", str(docs)])[0], 0)
                commit()
                self.assertTrue(bound())


if __name__ == "__main__":
    unittest.main()
