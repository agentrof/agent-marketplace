"""Reader waves: switch `reader_waves` keeps today's wave start at
`as_slots_free` and, at `all_at_once`, has each host contract start every
reader of a review or recheck wave together, Codex after closing every
finished agent thread."""

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
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "reader_waves"
OWNERS = ["backlog-planning", "business-analysis", "design-system", "execution-planning",
          "operation", "solution-design"]
TASKS = (("backlog-plan", "backlog-reviewer"), ("business-analysis", "analysis-challenger"),
         ("design-system", "design-system-reviewer"), ("solution-design", "solution-reviewer"))


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


class ReaderWavesTests(unittest.TestCase):
    def test_registry_declares_the_switch_with_todays_wave_start_as_default(self):
        spec = process_policy.load_registry()[SWITCH]
        self.assertEqual((spec["default"], list(spec["values"])),
                         ("as_slots_free", ["as_slots_free", "all_at_once"]))
        self.assertEqual(spec["spec"]["flows"], OWNERS)
        self.assertEqual(spec["spec"]["issue"], 399)

    def test_every_owning_flow_anchors_the_switch(self):
        for flow in OWNERS:
            with self.subTest(flow=flow):
                self.assertIn("Switch `reader_waves`: at `all_at_once`, every reader of a"
                              " review or recheck wave starts at once",
                              flat(TEAM / "flows" / f"{flow}.md"))

    def test_codex_closes_finished_threads_and_starts_every_reader_before_waiting(self):
        text = flat(ROOT / "platforms/codex/software-engineering-team/host-contract.md")
        rule = text[text.index("Under switch `reader_waves` at `all_at_once`"):]
        rule = rule[:rule.index(" - ")]
        for phrase in ("closing every finished agent thread with `close_agent`",
                       "a writer between its passes included",
                       "`agents.max_concurrent_threads_per_session`",
                       "start every reader of the wave before waiting on any of them",
                       "the readers with the largest inputs first",
                       "names the wave size and how many of its readers run at once"):
            self.assertIn(phrase, rule)

    def test_claude_spawns_a_wave_in_one_message(self):
        text = flat(ROOT / "platforms/claude/software-engineering-team/host-contract.md")
        self.assertIn("Under switch `reader_waves` at `all_at_once`, spawn every reader of a"
                      " review or recheck wave in one message", text)

    def test_the_switch_binds_no_instruction_file_of_its_own(self):
        # The host contract carries the whole rule, so a task binds the same
        # instruction files at either value.
        bound = {}
        for value in ("as_slots_free", "all_at_once"):
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(remove_temporary, temporary)
            project = Path(temporary.name).resolve()
            docs = project / "workspace/docs"
            init_repository(project, initial_branch="main")
            (docs / "maps").mkdir(parents=True)
            for argv in (["init"], ["set", "--switch", SWITCH, "--value", value], ["approve"]):
                code, output = quiet(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])
                self.assertEqual(code, 0, output)
            for argv in (["add", "--all"], ["-c", "user.email=t@example.com", "-c", "user.name=T",
                                            "commit", "-q", "-m", "policy"]):
                subprocess.run(["git", "-C", str(project), *argv], check=True, capture_output=True)
            bound[value] = {
                f"{entry}:{role}": sorted(task_inputs.manifest(
                    entry=entry, role=role, mode="review", project=project)["required_reads"])
                for entry, role in TASKS}
        self.assertEqual(bound["as_slots_free"], bound["all_at_once"])


if __name__ == "__main__":
    unittest.main()
