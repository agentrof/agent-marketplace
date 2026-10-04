"""Switch `test_engines`: at `single` QA's final test run is one approved
command; at `partitioned`, where the Verification Contract declares a
partition command, its engines and a Test Partitions table, `run --kind test`
runs every partition in its own private clone, in parallel over isolated
engines, and merges them into one record that evidence approval checks
partition by partition (#386)."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_compile as delivery  # noqa: E402
import delivery_verification as verification  # noqa: E402
import operation_compile  # noqa: E402
import test_pre_handoff_regression as base  # noqa: E402
import test_test_group_report as grouped  # noqa: E402
from git_fixture import remove_temporary  # noqa: E402

SWITCH = "test_engines"
VALUE = "partitioned"
REFERENCE = "skill-content/deliver/references/switch-test_engines-partitioned.md"
GROUPS = ["st001", "st002", "st005"]
ENGINES = ["engine-a", "engine-b"]
COMMAND = base.PYTHON + " partition.py"
# Three partitions, one per group, each taking an engine alone.
TABLE = """## Test Partitions

| partition | groups | profile |
|---|---|---|
| api | st001 | cluster |
| web | st002 | cluster |
| new | st005 | cluster |
"""
# The approved partition command: it runs the groups its partition file names,
# but for the reused ids, after the delay partition-delay.txt asks for, fails
# the partition fail-partition.txt names, writes a group report and logs when
# it ran on which engine to FIXTURE_LOG.
PARTITION = """import json, os, pathlib, runpy, sys, time
part = json.loads(pathlib.Path(os.environ["AGENTROF_TEST_PARTITION"]).read_text(encoding="utf-8"))
assert part["engine"] == os.environ["AGENTROF_TEST_ENGINE"]
started = time.time()
delay = pathlib.Path("partition-delay.txt")
time.sleep(float(delay.read_text(encoding="utf-8")) if delay.exists() else 0)
reused = set(part["reused_test_ids"])
failing = pathlib.Path("fail-partition.txt")
failed, groups = [], {}
for group in part["groups"]:
    entry = groups[group] = {"status": "passed", "passed": 0, "failed": 0, "skipped": 0}
    for path in sorted(pathlib.Path("tests", group).rglob("test_*.py")):
        for name, value in sorted(runpy.run_path(str(path)).items()):
            if name.startswith("test_") and callable(value):
                identifier = path.as_posix() + "::" + name
                if identifier in reused:
                    entry["skipped"] += 1
                    print("REUSED " + identifier)
                    continue
                try:
                    value()
                    entry["passed"] += 1
                    print("PASS " + identifier)
                except AssertionError:
                    entry["failed"] += 1
                    entry["status"] = "failed"
                    failed.append(identifier)
                    print("FAIL " + identifier)
if failing.exists() and failing.read_text(encoding="utf-8").strip() == part["partition"]:
    failed.append("forced")
    print("FAIL forced")
report = pathlib.Path(os.environ["AGENTROF_VERIFICATION_SCRATCH"]) / "reports" / "groups.json"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({"schema_version": 1, "groups": groups}), encoding="utf-8")
if os.environ.get("FIXTURE_LOG"):
    with open(os.environ["FIXTURE_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"partition": part["partition"], "engine": part["engine"], "start": started,
                              "end": time.time(), "scratch": os.environ["AGENTROF_VERIFICATION_SCRATCH"],
                              "reused": sorted(reused)}) + "\\n")
sys.exit(1 if failed else 0)
"""


def contract_errors(fields: dict, body: str = "") -> list[str]:
    with tempfile.TemporaryDirectory() as raw:
        props = operation_compile.initial_props("verification", [])
        props.update(test_workdir=".", **fields)
        text = operation_compile.render(props, "# Verification Contract\n\n" + body)
        return [error for error in operation_compile.check_contract(Path(raw), "verification", text)[1]
                if re.search(r"partition|engine|profile|group", error)]


class ContractTests(unittest.TestCase):
    PLAN = {"test_groups": GROUPS, "test_group_report": "reports/groups.json",
            "test_partition_command": COMMAND, "test_engines": ENGINES}

    def test_the_check_takes_a_plan_that_places_every_group_once(self):
        self.assertEqual(contract_errors({}), [])
        self.assertEqual(contract_errors(self.PLAN, TABLE), [])
        self.assertEqual(contract_errors({**self.PLAN, "shared_profiles": ["cluster"]}, TABLE), [])

    def test_the_check_refuses_a_partial_or_inconsistent_plan(self):
        together = ("test_partition_command, test_engines and a Test Partitions table are declared together or"
                    " not at all")
        for fields, body, message in (
                ({"test_partition_command": COMMAND}, "", together),
                ({**self.PLAN}, "", together),
                ({key: value for key, value in self.PLAN.items() if key != "test_engines"}, TABLE, together),
                ({"shared_profiles": ["cluster"]}, "", "shared_profiles requires"),
                ({**self.PLAN, "test_engines": ["a", "a"]}, TABLE, "test_engines must list unique engine ids"),
                ({**self.PLAN, "test_engines": ["a b"]}, TABLE, "test_engines must list unique engine ids"),
                ({**self.PLAN, "shared_profiles": ["hermetic"]}, TABLE, "shared_profiles must list unique profiles"),
                ({key: value for key, value in self.PLAN.items() if not key.startswith("test_group")}, TABLE,
                 "a Test Partitions table needs test_groups"),
                (self.PLAN, TABLE.replace("| new | st005 | cluster |\n", ""), "group st005 is in no partition"),
                (self.PLAN, TABLE.replace("| st002 |", "| st002, st001 |"), "group st001 is in partitions api and web"),
                (self.PLAN, TABLE.replace("| st005 |", "| st005, st009 |"), "names st009, which test_groups"),
                (self.PLAN, TABLE.replace("| web |", "| api |"), "names a partition more than once"),
                (self.PLAN, TABLE.replace("| web |", "| we/b |"), "needs a partition id and a profile id")):
            with self.subTest(fields=sorted(fields), message=message):
                errors = contract_errors(fields, body)
                self.assertTrue(any(message in error for error in errors), errors)

    def test_the_environment_contract_names_its_engines_as_ids(self):
        with tempfile.TemporaryDirectory() as raw:
            props = operation_compile.initial_props("environment", [])
            for engines, valid in ((ENGINES, True), (["a", "a"], False), (["../a"], False), ("a", False)):
                with self.subTest(engines=engines):
                    text = operation_compile.render({**props, "test_engines": engines}, "# Environment Contract\n")
                    errors = operation_compile.check_contract(Path(raw), "environment", text)[1]
                    self.assertEqual(any("test_engines" in error for error in errors), not valid, errors)


class BindingTests(unittest.TestCase):
    setUp = base.BindingTests.setUp
    bound = base.BindingTests.bound

    def test_only_delivery_execution_tasks_at_partitioned_bind_the_reference(self):
        tasks = (("deliver", "delivery-coordinator"), ("deliver", "qa-engineer"),
                 ("execution-plan", "qa-engineer"), ("configure", "qa-engineer"))
        for entry, role in tasks:
            with self.subTest(value="single", task=(entry, role)):
                self.assertFalse(self.bound(entry, role, REFERENCE))
        base.policy(self.docs, "init")
        base.policy(self.docs, "set", "--switch", SWITCH, "--value", VALUE)
        base.policy(self.docs, "approve")
        for entry, role in tasks:
            with self.subTest(value=VALUE, task=(entry, role)):
                self.assertEqual(self.bound(entry, role, REFERENCE), entry == "deliver")


class PartitionedRunTests(unittest.TestCase):
    """The pre-handoff fixture's DLV-002 Item ST-005, at pre_handoff_regression touched_suites, with a
    partition command over its three test directories as groups."""

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
    validate = base.CandidateTests.validate
    rewrite_session = base.CandidateTests.rewrite_session

    def frozen(self, value: str | None = VALUE, *, groups: bool = False, table: str = TABLE,
               delay: float = 0.0, failing: str = "", shared: list[str] | None = None,
               provisioned: list[str] = ENGINES) -> None:
        """Build the fixture with test_engines at *value*, test_group_report on when *groups*, declare the
        partition plan *table* and two engines, repair the regression and freeze after a passing
        pre-handoff run."""
        real = base.policy

        def policy(docs, *argv):
            if argv[0] == "approve":
                if value is not None:
                    real(docs, "set", "--switch", SWITCH, "--value", value)
                if groups:
                    real(docs, "set", "--switch", "test_group_report", "--value", "refuse_missing_groups")
            real(docs, *argv)

        with mock.patch.object(base, "policy", policy):
            self.build()
        self.write("src/api/limit.txt", "10\n")
        self.write("partition.py", PARTITION)
        if groups:
            # The pre-handoff run's diagnostic adapter reports its groups too.
            self.write("diagnose.py", grouped.reporting(base.ADAPTER, "adapter-mode.txt"))
        if delay:
            self.write("partition-delay.txt", str(delay))
        if failing:
            self.write("fail-partition.txt", failing)
        self.contract(test_groups=GROUPS, test_group_report="reports/groups.json",
                      test_partition_command=COMMAND, test_engines=ENGINES, shared_profiles=shared)
        path = self.root / base.DOCS / "operation/verification-contract.md"
        props, body = delivery.split_note(path)
        path.write_text(delivery.frontmatter(props, body.rstrip() + "\n\n" + table), encoding="utf-8")
        self.note(base.DOCS + "operation/environment-contract.md", {
            "type": "environment-contract", "status": "approved", "test_engines": provisioned})
        self.commit("Repair, and partition the suite")
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"]), (0, True))
        self.freeze()

    def final_run(self) -> tuple[dict, list[dict]]:
        log = self.root.parent / (self.root.name + "-partitions.log")
        self.addCleanup(lambda: log.unlink(missing_ok=True))
        with mock.patch.dict(os.environ, {"FIXTURE_LOG": str(log)}):
            raw = verification.run_check(self.root, "test")
        runs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return raw, runs

    def register(self, raw: dict) -> None:
        for role in ("code_reviewer", "qa_engineer"):
            session = verification.read_session(self.root)
            result = {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                      "role": role, "mode": "review_initial" if role == "code_reviewer" else "qa_final",
                      "verdict": "passed", "report": f"Independent {role} result", "findings": [],
                      "checks": {name: {"passed": True, "evidence": "Independently verified"}
                                 for name in verification.required_checks(self.root, session["candidate"], role)}}
            if role == "qa_engineer":
                result["checks"]["full_test_suite"].update(
                    command=COMMAND, exit_code=0, environment=raw["identity"]["environment_hash"],
                    raw_evidence_hash=raw["evidence_hash"])
            verification.register_result(self.root, result)

    def test_at_single_a_declared_plan_changes_no_run(self):
        self.frozen(value=None)
        raw, runs = self.final_run()
        self.assertEqual(runs, [])
        self.assertEqual(raw["identity"]["command"], base.PYTHON + " run_all.py")
        self.assertNotIn("test_partitions", raw["identity"])
        self.assertNotIn("partitions", raw)

    def test_every_partition_runs_in_its_own_clone_and_one_record_merges_them(self):
        self.frozen()
        raw, runs = self.final_run()
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        self.assertEqual(raw["identity"]["command"], COMMAND)
        self.assertEqual(raw["identity"]["test_partitions"], {
            "partitions": [{"partition": "api", "groups": ["st001"], "profile": "cluster"},
                           {"partition": "web", "groups": ["st002"], "profile": "cluster"},
                           {"partition": "new", "groups": ["st005"], "profile": "cluster"}],
            "test_engines": ENGINES, "shared_profiles": []})
        self.assertEqual([record["partition"] for record in raw["partitions"]], ["api", "web", "new"])
        for record in raw["partitions"]:
            with self.subTest(partition=record["partition"]):
                self.assertEqual((record["exit_code"], record["candidate_intact"], record["passed"]), (0, True, True))
                self.assertIn(record["engine"], ENGINES)
                output = verification.raw_output_path(self.root, record["output_file"]).read_bytes()
                self.assertIn(b"PASS tests/" + record["groups"][0].encode(), output.replace(b"REUSED", b"PASS"))
        # Each partition ran with its own scratch; the earlier story's reused target left every selection.
        self.assertEqual(sorted(run["partition"] for run in runs), ["api", "new", "web"])
        self.assertEqual(len({run["scratch"] for run in runs}), 3)
        self.assertTrue(all(run["reused"] == ["tests/st001/test_api.py::test_limit"] for run in runs))
        merged = verification.raw_output_path(self.root, raw["output_file"]).read_text(encoding="utf-8")
        self.assertIn("REUSED tests/st001/test_api.py::test_limit", merged)
        self.assertIn("== partition new on engine", merged)
        # No other command receives the partition variables, an inherited one included.
        with mock.patch.dict(os.environ, {"AGENTROF_TEST_PARTITION": "x", "AGENTROF_TEST_ENGINE": "engine-a"}):
            environment = verification.command_environment(self.root)
            self.assertFalse(set(verification.PARTITION_VARIABLES) & set(environment))
            names = verification.environment_identity(self.root, environment)["environment_variables"]
            self.assertFalse(set(verification.PARTITION_VARIABLES) & set(names))
        self.assertIsNone(verification.environment_holder(self.root))
        self.register(raw)
        self.validate()

    def test_partitions_run_concurrently_and_never_share_an_exclusive_engine(self):
        self.frozen(delay=1.0)
        started = time.monotonic()
        raw, runs = self.final_run()
        wall = time.monotonic() - started
        self.assertEqual(raw["exit_code"], 0)
        durations = sum(record["duration_seconds"] for record in raw["partitions"])
        self.assertLess(wall, durations)
        for engine in ENGINES:
            spans = sorted((run["start"], run["end"]) for run in runs if run["engine"] == engine)
            with self.subTest(engine=engine):
                self.assertTrue(all(earlier[1] <= later[0] for earlier, later in zip(spans, spans[1:])), spans)
        # Never more partitions at once than engines.
        moments = sorted([(run["start"], 1) for run in runs] + [(run["end"], -1) for run in runs],
                         key=lambda item: (item[0], item[1]))
        running = peak = 0
        for _moment, change in moments:
            running += change
            peak = max(peak, running)
        self.assertLessEqual(peak, len(ENGINES))
        # The next run starts the longest recorded partition first.
        recorded = json.loads((verification.session_path(self.root).parent / "partition-durations.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(sorted(recorded), ["api", "new", "web"])

    def test_the_longest_recorded_partition_runs_first_and_an_unrecorded_one_before_it(self):
        self.frozen()
        plan = [{"partition": name, "groups": [name], "profile": "cluster"} for name in ("a", "b", "c", "d")]
        verification.partition_durations_path(self.root).write_text(
            json.dumps({"a": 5.0, "b": 50.0, "d": 20.0}), encoding="utf-8")
        self.assertEqual([entry["partition"] for entry in verification.partition_order(self.root, plan)],
                         ["c", "b", "d", "a"])

    def test_shared_profile_partitions_may_share_an_engine(self):
        table = TABLE.replace("| web | st002 | cluster |", "| web | st002 | hermetic |").replace(
            "| new | st005 | cluster |", "| new | st005 | hermetic |")
        self.frozen(table=table, delay=1.0, shared=["hermetic"])
        raw, runs = self.final_run()
        self.assertEqual(raw["exit_code"], 0)
        cluster = next(run for run in runs if run["partition"] == "api")
        for run in runs:
            if run["partition"] != "api" and run["engine"] == cluster["engine"]:
                with self.subTest(partition=run["partition"]):
                    self.assertTrue(run["end"] <= cluster["start"] or cluster["end"] <= run["start"])

    def test_a_failing_partition_never_stops_the_others_and_is_never_approved(self):
        self.frozen(failing="web")
        raw, runs = self.final_run()
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (1, True))
        self.assertEqual(sorted(run["partition"] for run in runs), ["api", "new", "web"])
        outcome = {record["partition"]: (record["exit_code"], record["passed"]) for record in raw["partitions"]}
        self.assertEqual(outcome, {"api": (0, True), "web": (1, False), "new": (0, True)})
        self.assertIsNone(verification.environment_holder(self.root))

    def test_a_partition_that_cannot_start_is_recorded_failed(self):
        self.frozen()
        real = verification.private_checkout_run

        def broken(root, scratch, *args, **kwargs):
            if scratch.name == "new":
                raise RuntimeError("engine unavailable")
            return real(root, scratch, *args, **kwargs)

        with mock.patch.object(verification, "private_checkout_run", broken):
            raw, runs = self.final_run()
        self.assertEqual(sorted(run["partition"] for run in runs), ["api", "web"])
        new = raw["partitions"][2]
        self.assertEqual((new["exit_code"], new["candidate_intact"], new["passed"]), (None, False, False))
        self.assertIn("engine unavailable", new["error"])
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (1, False))

    def test_the_group_reports_of_every_partition_merge_into_one_record(self):
        self.frozen(groups=True)
        raw, _runs = self.final_run()
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        self.assertEqual(sorted(raw["test_groups"]), GROUPS)
        self.register(raw)
        self.validate()

    def forge(self, change) -> None:
        """Apply *change* to QA's settled partitioned test record, keeping every hash that binds it."""
        def rewrite(session):
            raw = session["raw_evidence"]["test"]
            change(raw)
            raw.pop("evidence_hash")
            raw["evidence_hash"] = verification.digest(raw)
            result = session["workers"]["qa_engineer"]["result"]
            result.pop("result_hash")
            result["checks"]["full_test_suite"]["raw_evidence_hash"] = raw["evidence_hash"]
            result["result_hash"] = verification.digest(result)
        self.rewrite_session(rewrite)

    def test_approval_refuses_a_record_missing_doubling_failing_a_partition_or_of_another_command(self):
        self.frozen()
        raw, _runs = self.final_run()
        self.register(raw)
        self.validate()
        original = verification.session_path(self.root).read_bytes()

        def failed(record):
            record["partitions"][1].update(exit_code=1, passed=False)

        for message, change in (
                ("lacks partition web", lambda record: record["partitions"].pop(1)),
                ("holds partition api more than once",
                 lambda record: record["partitions"].append(dict(record["partitions"][0]))),
                ("holds failed or not intact partition web", failed),
                ("does not run the test partition plan",
                 lambda record: record["identity"]["test_partitions"].update(test_engines=["engine-a"])),
                ("does not run the approved command in its approved workdir",
                 lambda record: record["identity"].update(command=base.PYTHON + " run_all.py"))):
            with self.subTest(message=message):
                verification.session_path(self.root).write_bytes(original)
                self.forge(change)
                with self.assertRaisesRegex(RuntimeError, "full_test_suite evidence " + re.escape(message)):
                    self.validate()

    def test_an_engine_the_environment_contract_does_not_provision_refuses_the_run(self):
        self.frozen(provisioned=["engine-a"])
        with self.assertRaisesRegex(RuntimeError, "provisions no test engine engine-b"):
            verification.run_check(self.root, "test")
        self.assertIsNone(verification.environment_holder(self.root))


if __name__ == "__main__":
    unittest.main()
