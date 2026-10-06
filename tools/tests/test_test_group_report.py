"""Switch `test_group_report`: at `off` a test run knows no groups; at
`refuse_missing_groups`, where the Verification Contract declares
`test_groups` and `test_group_report`, every test run reads the group report
the approved command writes, records each declared group and is not intact
when the report lacks one, and evidence approval refuses a final test run
with a group that did not pass (#384)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_verification as verification  # noqa: E402
import operation_compile  # noqa: E402
import test_pre_handoff_regression as base  # noqa: E402
from git_fixture import remove_temporary  # noqa: E402

SWITCH = "test_group_report"
VALUE = "refuse_missing_groups"
REFERENCE = "skill-content/deliver/references/switch-test_group_report-refuse_missing_groups.md"
GROUPS = ["st001", "st002", "st005"]
REPORT = "reports/groups.json"
# Both approved commands report every declared group by test directory, a
# group they run no case of as passed, and drop or misreport one as their mode
# file asks.
REPORTING = """import json, os, pathlib
def report(groups):
    for group in {groups!r}:
        groups.setdefault(group, {{"status": "passed", "passed": 0, "failed": 0, "skipped": 0}})
    mode = pathlib.Path({mode!r}).read_text(encoding="utf-8").split() if pathlib.Path(
        {mode!r}).exists() else []
    if mode[:1] == ["drop"]:
        groups.pop(mode[1])
    if mode[:1] == ["uncollect"]:
        groups[mode[1]] = {{"status": "not_collected", "passed": 0, "failed": 0, "skipped": 0}}
    path = pathlib.Path(os.environ["AGENTROF_VERIFICATION_SCRATCH"]) / {report!r}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({{"schema_version": 1, "groups": groups}}), encoding="utf-8")
"""
COUNTING = """
groups = {}
def count(identifier, outcome):
    entry = groups.setdefault(identifier.split("/")[1], {"status": "passed", "passed": 0, "failed": 0, "skipped": 0})
    entry[outcome] += 1
    if outcome == "failed":
        entry["status"] = "failed"
"""


def reporting(script: str, mode: str) -> str:
    """*script*, an approved command of the pre-handoff fixture, writing a group report of what it ran as the
    file *mode* asks."""
    body = (script.replace('print("PASS " + identifier)', 'print("PASS " + identifier); count(identifier, "passed")')
            .replace("failed.append(identifier)", 'failed.append(identifier); count(identifier, "failed")')
            .replace('print("REUSED " + identifier)', 'print("REUSED " + identifier); count(identifier, "skipped")')
            .replace("sys.exit(1 if failed else 0)", "report(groups)\nsys.exit(1 if failed else 0)"))
    head, _, rest = body.partition("\n")
    return head + "\n" + REPORTING.format(groups=GROUPS, report=REPORT, mode=mode) + COUNTING + rest


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.docs = Path(temporary.name)

    def errors(self, **fields) -> list[str]:
        props = operation_compile.initial_props("verification", [])
        props.update(test_workdir=".", **fields)
        text = operation_compile.render(props, "# Verification Contract\n")
        _receipt, errors = operation_compile.check_contract(self.docs, "verification", text)
        return [error for error in errors if "test_group" in error]

    def test_the_check_takes_unique_literal_groups_and_a_scratch_path_both_or_neither(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.errors(test_groups=["api", "web"], test_group_report="reports/groups.json"), [])
        pair = "test_groups and test_group_report are declared together or not at all"
        self.assertEqual(self.errors(test_groups=["api"]), [pair])
        self.assertEqual(self.errors(test_group_report="groups.json"), [pair])
        groups = "test_groups must list unique literal group ids"
        for value in (["api", "api"], [], ["-k api"], [" api"], "api", ["a\tb"]):
            with self.subTest(test_groups=value):
                self.assertEqual(self.errors(test_groups=value, test_group_report="groups.json"), [groups])
        path = "test_group_report must be a normalized relative path under the verification scratch"
        for value in (".", "/tmp/groups.json", "../groups.json", "reports/../groups.json", "reports//groups.json",
                      "c:groups.json", "reports\\groups.json", "groups.json.", ["groups.json"]):
            with self.subTest(test_group_report=value):
                self.assertEqual(self.errors(test_groups=["api"], test_group_report=value), [path])


@integration
class BindingTests(unittest.TestCase):
    setUp = base.BindingTests.setUp
    bound = base.BindingTests.bound

    def test_only_delivery_execution_tasks_at_refuse_missing_groups_bind_the_reference(self):
        tasks = (("deliver", "delivery-coordinator"), ("deliver", "qa-engineer"), ("deliver", "code-reviewer"),
                 ("execution-plan", "qa-engineer"), ("configure", "qa-engineer"))
        for entry, role in tasks:
            with self.subTest(value="off", task=(entry, role)):
                self.assertFalse(self.bound(entry, role, REFERENCE))
        base.policy(self.docs, "init")
        base.policy(self.docs, "set", "--switch", SWITCH, "--value", VALUE)
        base.policy(self.docs, "approve")
        for entry, role in tasks:
            with self.subTest(value=VALUE, task=(entry, role)):
                self.assertEqual(self.bound(entry, role, REFERENCE), entry == "deliver")


@integration
class GroupReportTests(unittest.TestCase):
    """The pre-handoff fixture's DLV-002 Item ST-005, at pre_handoff_regression touched_suites, with
    commands that report its three test directories as groups."""

    setUp = base.CandidateTests.setUp
    git = base.CandidateTests.git
    commit = base.CandidateTests.commit
    write = base.CandidateTests.write
    note = base.CandidateTests.note
    plan = base.CandidateTests.plan
    plan_path = base.CandidateTests.plan_path
    plan_hash = base.CandidateTests.plan_hash
    item = base.CandidateTests.item
    contract = base.CandidateTests.contract
    build = base.CandidateTests.build
    run_regression = base.CandidateTests.run_regression
    freeze = base.CandidateTests.freeze
    reader_result = base.CandidateTests.reader_result
    approve_evidence = base.CandidateTests.approve_evidence

    def repaired(self, value: str | None = VALUE, mode: str = "", declared: bool = True) -> None:
        """Build the fixture with test_group_report at *value*, repair its regression and commit commands
        that report their groups, under a contract that declares them when *declared*, the full suite
        reporting as *mode* asks."""
        real = base.policy

        def policy(docs, *argv):
            if argv[0] == "approve" and value is not None:
                real(docs, "set", "--switch", SWITCH, "--value", value)
            real(docs, *argv)

        with mock.patch.object(base, "policy", policy):
            self.build()
        self.write("src/api/limit.txt", "10\n")
        self.write("run_all.py", reporting(base.FULL_SUITE, "suite-mode.txt"))
        self.write("diagnose.py", reporting(base.ADAPTER, "adapter-mode.txt"))
        if mode:
            self.write("suite-mode.txt", mode + "\n")
        if declared:
            self.contract(test_groups=GROUPS, test_group_report=REPORT)
        self.commit("Repair, and report the test groups")

    def frozen(self, **changes) -> dict:
        self.repaired(**changes)
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"]), (0, True))
        self.freeze()
        return run

    def report_path(self) -> Path:
        return verification.session_path(self.root).parent / "scratch" / REPORT

    def test_at_off_a_declared_group_report_changes_no_run(self):
        run = self.frozen(value=None)
        self.assertFalse({"test_groups", "missing_test_groups"} & set(run))
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        self.assertNotIn("test_group_report", raw["identity"])
        self.assertFalse({"test_groups", "missing_test_groups"} & set(raw))
        self.approve_evidence()

    def test_every_run_records_each_declared_group_and_a_passing_one_approves(self):
        run = self.frozen()
        # The pre-handoff run selects ST-001's and ST-005's targets; the adapter reports ST-002 with no case.
        self.assertEqual(run["test_groups"], {
            "st001": {"status": "passed", "passed": 1, "failed": 0, "skipped": 0},
            "st002": {"status": "passed", "passed": 0, "failed": 0, "skipped": 0},
            "st005": {"status": "passed", "passed": 1, "failed": 0, "skipped": 0}})
        self.assertEqual(run["missing_test_groups"], [])
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        self.assertEqual(raw["identity"]["test_group_report"], {"test_groups": GROUPS, "test_group_report": REPORT})
        # The earlier story's target is reused from the pre-handoff run, so its group runs no case of its own.
        self.assertEqual(raw["test_groups"]["st001"], {"status": "passed", "passed": 0, "failed": 0, "skipped": 1})
        self.assertEqual(verification.read_session(self.root)["raw_evidence"]["test"]["test_groups"],
                         raw["test_groups"])
        diagnostic = verification.session_path(self.root).parent / "scratch" / "selection.json"
        diagnostic.write_text(json.dumps({"schema_version": 1, "candidate_hash": raw["identity"]["candidate_hash"],
                                          "failed_test_ids": [],
                                          "affected_test_ids": ["tests/st002/test_web.py::test_page"]}),
                              encoding="utf-8")
        spot = verification.run_check(self.root, "diagnostic_test", selection_file=diagnostic)
        self.assertEqual((spot["candidate_intact"], spot["test_groups"]["st002"]["passed"]), (True, 1))
        self.approve_evidence()

    def test_a_group_the_report_lacks_leaves_the_run_not_intact_and_unapproved(self):
        self.frozen(mode="drop st002")
        self.report_path().write_text("stale", encoding="utf-8")
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, False))
        self.assertEqual(raw["missing_test_groups"], ["st002"])
        self.assertEqual(raw["test_groups"]["st002"], {"status": "missing"})
        self.assertNotIn("test_group_report_problem", raw)
        verification.register_result(self.root, self.reader_result("code_reviewer"))
        with self.assertRaisesRegex(RuntimeError, "full_test_suite requires successful same-candidate command"):
            verification.register_result(self.root, self.reader_result("qa_engineer"))

    def test_a_command_that_writes_no_report_misses_every_group(self):
        self.repaired()
        self.write("run_all.py", base.FULL_SUITE)
        self.commit("Withdraw the full suite's group report")
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"]), (0, True))
        self.freeze()
        # A report an earlier run left behind never counts for the next one.
        self.report_path().write_text(json.dumps({"schema_version": 1, "groups": {
            group: {"status": "passed", "passed": 1, "failed": 0, "skipped": 0} for group in GROUPS}}),
            encoding="utf-8")
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, False))
        self.assertEqual(raw["missing_test_groups"], GROUPS)
        self.assertEqual(raw["test_group_report_problem"], "the command wrote no group report")

    def test_approval_refuses_a_group_that_did_not_collect(self):
        self.frozen(mode="uncollect st005")
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        self.assertEqual(raw["test_groups"]["st005"]["status"], "not_collected")
        verification.register_result(self.root, self.reader_result("code_reviewer"))
        with self.assertRaisesRegex(RuntimeError, "^" + re.escape(
                "full_test_suite evidence records test groups that did not pass: st005 not_collected") + "$"):
            verification.register_result(self.root, self.reader_result("qa_engineer"))

    def test_a_pre_handoff_run_whose_report_lacks_a_group_blocks_the_freeze_and_names_it(self):
        self.repaired()
        self.write("adapter-mode.txt", "drop st005\n")
        self.commit("An adapter that drops a group")
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"], run["missing_test_groups"]), (0, False, ["st005"]))
        with self.assertRaisesRegex(RuntimeError, "its group report lacks st005"):
            self.freeze()

    def test_a_contract_that_declares_one_field_refuses_the_run(self):
        self.repaired(declared=False)
        self.contract(test_groups=GROUPS)
        self.commit("Declare the groups without their report")
        with self.assertRaisesRegex(RuntimeError, "declared together or not at all"):
            self.run_regression()


if __name__ == "__main__":
    unittest.main()
