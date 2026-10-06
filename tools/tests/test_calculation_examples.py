"""Calculation examples: switch `calculation_examples` leaves a calculation
rule to the space standard at `off` and, at `required`, makes analysis state
its formula or a worked example that the challenger checks and backlog test
plans read as their oracle (#400)."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "calculation_examples"
REFERENCE = "skill-content/requirements-analysis/references/switch-calculation_examples-required.md"
# The analyst writes the example, the challenger checks it and backlog test
# planning reads it; every one of these tasks binds the reference.
TASKS = (("business-analysis", "business-analyst"), ("business-analysis", "analysis-challenger"),
         ("backlog-plan", "product-owner"), ("backlog-plan", "qa-engineer"),
         ("backlog-plan", "backlog-reviewer"))


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


class CalculationExamplesTests(unittest.TestCase):
    def test_registry_keeps_todays_analysis_as_default(self):
        spec = process_policy.load_registry()[SWITCH]
        self.assertEqual((spec["default"], spec["values"]), ("off", ["off", "required"]))
        self.assertEqual(spec["spec"]["flows"], ["backlog-planning", "business-analysis"])

    def test_the_reference_defines_the_example_the_lens_and_the_oracle(self):
        text = flat(TEAM / REFERENCE)
        for phrase in ("the expected output, then one named parameter change and the expected"
                       " output after it",
                       "checks every BR for its calculation example",
                       "is a major finding with the BR id as its anchor",
                       "takes its expected value from the rule's formula or from the cited worked"
                       " example"):
            self.assertIn(phrase, text)
        for flow in ("business-analysis", "backlog-planning"):
            with self.subTest(flow=flow):
                self.assertIn(REFERENCE, flat(TEAM / "flows" / f"{flow}.md"))

    @integration
    def test_only_required_binds_the_reference_to_every_owning_task(self):
        bound = {}
        for value in ("off", "required"):
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(remove_temporary, temporary)
            project = Path(temporary.name).resolve()
            docs = project / "workspace/docs"
            init_repository(project, initial_branch="main")
            (docs / "maps").mkdir(parents=True)
            for command, *rest in (("set", "--switch", SWITCH, "--value", value), ("approve",)):
                if command == "set":
                    code, output = quiet(process_policy.main, ["init", "--docs", str(docs)])
                    self.assertEqual(code, 0, output)
                code, output = quiet(process_policy.main, [command, "--docs", str(docs), *rest])
                self.assertEqual(code, 0, output)
            for argv in (["add", "--all"], ["-c", "user.email=t@example.com", "-c", "user.name=T",
                                            "commit", "-q", "-m", "policy"]):
                subprocess.run(["git", "-C", str(project), *argv], check=True, capture_output=True)
            bound[value] = {
                f"{entry}:{role}": set(task_inputs.manifest(
                    entry=entry, role=role, mode="review", project=project)["required_reads"])
                for entry, role in TASKS}
        for task, reads in bound["required"].items():
            with self.subTest(task=task):
                self.assertEqual(reads - bound["off"][task], {REFERENCE})


if __name__ == "__main__":
    unittest.main()
