"""Opt-in live test groups: a Verification Contract declares `live_test_command`
with one `{group}` placeholder, `live_test_workdir` and `live_groups`, all or
none, and the QA reader runs one declared group at a time through
`run --kind live_test --group <group>`, each group keeping its own evidence
outside the required checks."""

from __future__ import annotations

import contextlib
import io
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import threading
import time
from unittest import mock
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import delivery_compile as delivery  # noqa: E402
import delivery_verification as verification  # noqa: E402
import operation_compile  # noqa: E402
import test_delivery_verification as base  # noqa: E402
from git_fixture import remove_temporary  # noqa: E402

LIVE = {"live_test_command": "make test-live GROUP={group}", "live_test_workdir": ".",
        "live_groups": ["smoke", "billing"]}


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.docs = Path(temporary.name)

    def errors(self, **fields) -> list[str]:
        props = operation_compile.initial_props("verification", [])
        props.update(**fields)
        text = operation_compile.render(props, "# Verification Contract\n")
        _receipt, errors = operation_compile.check_contract(self.docs, "verification", text)
        return [error for error in errors if "live_" in error]

    def test_the_three_fields_are_declared_together_and_valid(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.errors(**LIVE), [])
        self.assertEqual(self.errors(**{**LIVE, "live_test_workdir": "apps/api"}), [])
        pair = "live_test_command, live_test_workdir and live_groups are declared together or not at all"
        for name in LIVE:
            with self.subTest(missing=name):
                self.assertEqual(self.errors(**{key: value for key, value in LIVE.items() if key != name}), [pair])
        command = "live_test_command must be a non-empty command containing {group} exactly once"
        for value in ("make test-live", "run {group} {group}", " "):
            with self.subTest(live_test_command=value):
                self.assertEqual(self.errors(**{**LIVE, "live_test_command": value}), [command])
        workdir = "live_test_workdir must be a normalized repository-relative path"
        for value in ("", "/srv", "../out", "apps/../api", "apps//api", "c:api", "apps\\api"):
            with self.subTest(live_test_workdir=value):
                self.assertEqual(self.errors(**{**LIVE, "live_test_workdir": value}), [workdir])
        groups = "live_groups must list unique group names of letters, digits, '.', '_' and '-'"
        for value in ([], ["smoke", "smoke"], ["-k"], ["a b"], ["a;b"], ["$(x)"], "smoke"):
            with self.subTest(live_groups=value):
                self.assertEqual(self.errors(**{**LIVE, "live_groups": value}), [groups])

    def test_live_group_run_is_per_group_or_combined_and_only_beside_the_live_fields(self):
        for value in ("per_group", "combined"):
            with self.subTest(live_group_run=value):
                self.assertEqual(self.errors(**{**LIVE, "live_group_run": value}), [])
        self.assertEqual(self.errors(**{**LIVE, "live_group_run": "parallel"}),
                         ["live_group_run must be one of per_group, combined"])
        self.assertEqual(self.errors(live_group_run="combined"),
                         ["live_group_run needs live_test_command, live_test_workdir and live_groups"])

    def test_live_group_paths_name_declared_groups_or_the_catch_all(self):
        paths = ["smoke=src/smoke/*", "billing=src/billing/*", "*=Makefile"]
        self.assertEqual(self.errors(**LIVE, live_group_paths=paths), [])
        self.assertEqual(operation_compile.live_impact_map({"live_group_paths": paths}),
                         {"*": ["Makefile"], "billing": ["src/billing/*"], "smoke": ["src/smoke/*"]})
        message = ("live_group_paths must list unique '<group>=<glob>' entries naming a declared live group or '*'")
        for value in ([], ["audit=src/*"], ["smoke="], ["smoke"], ["smoke=a", "smoke=a"], "smoke=a", [3]):
            with self.subTest(live_group_paths=value):
                self.assertEqual(self.errors(**LIVE, live_group_paths=value), [message])
        self.assertEqual(self.errors(live_group_paths=paths), ["live_group_paths needs live test groups"])

    def test_the_fields_bind_the_approved_source_hash(self):
        props = operation_compile.initial_props("verification", []) | LIVE
        changed = props | {"live_groups": ["smoke"]}
        self.assertNotEqual(operation_compile.source_hash(props, "# Body"),
                            operation_compile.source_hash(changed, "# Body"))


REUSE = {**LIVE, "live_test_command": "live {group}", "live_group_run": "combined",
         "live_group_paths": ["smoke=src/smoke/*", "billing=src/billing/*", "*=live.py"]}
ENVIRONMENT = {"environment_variables": ["AGENTROF_ITEM_ID"], "environment_hash": "hmac-sha256:" + "0" * 64,
               "python": "3", "platform": "host", "git": "git"}


def base_entry(**fields) -> dict:
    """A passing combined base run of both groups, recorded as record_live_base keeps it."""
    entry = {"product_commit": "a" * 40, "candidate_hash": "sha256:base",
             "map_hash": verification.digest(operation_compile.live_impact_map(REUSE)),
             "evidence_hash": "sha256:evidence", "live_group": "smoke,billing", "command": "live smoke,billing",
             "workdir": ".", **ENVIRONMENT, "exit_code": 0, "candidate_intact": True,
             "output_file": "scratch/base.log", "output_sha256": "0" * 64, "completed_at": time.time(),
             "cases": {"tests/live.py::test_billing": "passed"}}
    entry.update(fields)
    return entry


class LiveBaseProblemTests(unittest.TestCase):
    """At live_case_reuse delta_since_green a group reuses its base run only while every binding holds."""

    def problem(self, entry=None, group="billing", contract=REUSE, ancestor=True, changed=("src/smoke/a.py",),
                environment=ENVIRONMENT):
        return verification.live_base_problem(base_entry() if entry is None else entry, group, contract,
                                              ancestor=ancestor, changed=None if changed is None else list(changed),
                                              environment=environment)

    def test_a_delta_that_leaves_the_group_untouched_reuses_its_base(self):
        self.assertIsNone(self.problem())
        self.assertIsNone(self.problem(changed=["docs/readme.md"], environment=None))

    def test_each_broken_binding_is_refused_by_name(self):
        cases = {
            "has no recorded passing base run": {"entry": {}},
            "did not pass intact": {"entry": base_entry(exit_code=1)},
            "expired": {"entry": base_entry(completed_at=0)},
            "live_group_paths changed": {"contract": {**REUSE, "live_group_paths": ["billing=src/*"]}},
            "approved live command changed": {"contract": {**REUSE, "live_test_command": "other {group}"}},
            "ran in another declared environment": {"environment": {**ENVIRONMENT, "python": "4"}},
            "is no ancestor": {"ancestor": False},
            "cannot be read": {"changed": None},
            "src/billing/b.py changed since the base run of live group billing and matches one of its live paths":
                {"changed": ["src/billing/b.py"]},
            "live.py changed since the base run of live group billing and matches a catch-all live path":
                {"changed": ["live.py"]},
        }
        for message, arguments in cases.items():
            with self.subTest(message):
                self.assertIn(message, self.problem(**arguments) or "")
        unmapped = {**REUSE, "live_group_paths": ["smoke=src/smoke/*"]}
        entry = base_entry(map_hash=verification.digest(operation_compile.live_impact_map(unmapped)))
        self.assertIn("declares no live_group_paths", self.problem(entry, contract=unmapped))

    def test_a_reuse_record_names_each_group_with_its_base_run(self):
        entry = base_entry()
        raw = {"identity": {"kind": "live_test", "live_group": "smoke", **ENVIRONMENT,
                            "reused_live_base": {"billing": "sha256:other"}},
               "reused_live_base": {"billing": entry}}
        raw["evidence_hash"] = verification.digest(raw)
        session = {"candidate": {"delivery": "delivery", "product_commit": "b" * 40}}
        with mock.patch.object(verification, "live_case_reuse", return_value=True):
            self.assertEqual(verification.live_reuse_problem(Path("."), session, raw, REUSE),
                             "names reused live groups apart from their base runs")
        with mock.patch.object(verification, "live_case_reuse", return_value=False):
            self.assertIn("only process switch live_case_reuse delta_since_green",
                          verification.live_reuse_problem(Path("."), session, raw, REUSE))

    def test_a_live_only_finding_closes_on_reused_evidence_while_its_files_are_unchanged(self):
        finding = {"id": "QA-1", "role": "qa_engineer", "severity": "minor", "status": "open",
                   "live_group": "billing", "file": "src/billing/b.py", "verification": "billing live case"}
        plan = {"groups": {"billing": {"reusable": True, "base_evidence_hash": "sha256:evidence",
                                       "changed_paths": ["src/smoke/a.py"]}}}
        session = {"unresolved_findings": [finding], "previous_candidate": None}
        row, = verification.carried_findings(Path("."), session, {}, False, plan)
        self.assertEqual((row["action"], row["live_base_evidence_hash"]), ("close_on_reused_live", "sha256:evidence"))
        plan["groups"]["billing"]["changed_paths"] = ["src/billing/b.py"]
        self.assertEqual(verification.carried_findings(Path("."), session, {}, False, plan)[0]["action"], "reexamine")
        plan["groups"]["billing"] = {"reusable": False, "reason": "selected"}
        self.assertEqual(verification.carried_findings(Path("."), session, {}, False, plan)[0]["action"], "reexamine")
        self.assertEqual(verification.carried_findings(Path("."), session, {}, False), [])


@integration
class LiveCaseReuseTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    settle = base.VerificationTests.settle

    def enable(self, value="delta_since_green"):
        original = delivery.delivery_switch_value
        patch = mock.patch.object(delivery, "delivery_switch_value", side_effect=lambda docs, identifier, name:
                                  value if name == "live_case_reuse" else original(docs, identifier, name))
        patch.start()
        self.addCleanup(patch.stop)
        return patch

    def declare(self) -> dict:
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract.update(REUSE, live_test_command=executable + " live.py {group}")
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.write("live.py", "import json, os, pathlib, sys\n"
                   "groups = sys.argv[1].split(',')\n"
                   "report = {g: {'tests/live.py::test_' + g: 'passed'} for g in groups}\n"
                   "pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'], 'live-case-report.json')"
                   ".write_text(json.dumps({'schema_version': 1, 'groups': report}))\n"
                   "print('BASE', os.environ.get('AGENTROF_LIVE_BASE_COMMIT', '-'))\n")
        self.write("src/smoke/a.txt", "1\n")
        self.write("src/billing/b.txt", "1\n")
        self.commit()
        return contract

    def test_a_nightly_run_of_an_ancestor_commit_is_a_base(self):
        self.declare()
        nightly = verification.git(self.root, "rev-parse", "HEAD")
        item = (delivery.split_note(self.root / self.directory / "delivery.md")[0]["id"],
                delivery.split_note(self.root / self.item_path)[0]["story_id"])
        with self.assertRaisesRegex(RuntimeError, "live-base serves only process switch live_case_reuse"):
            verification.live_base_run(self.root, *item, nightly, "smoke,billing")
        self.enable()
        recorded = verification.live_base_run(self.root, *item, nightly, "smoke,billing")
        self.assertEqual((recorded["exit_code"], recorded["candidate_intact"]), (0, True))
        self.write("src/smoke/a.txt", "2\n")
        self.commit()
        self.freeze()
        plan = verification.manifest(self.root, *item, "qa_engineer", "qa_final")["live_reuse"]
        self.assertEqual((plan["selected_groups"], plan["base_commit"]), (["smoke"], nightly))
        self.assertIsNone(plan["groups"]["billing"]["base_candidate_hash"])
        run = verification.run_check(self.root, "live_test", group="smoke")
        self.assertEqual(run["identity"]["reused_live_base"], {"billing": recorded["evidence_hash"]})

    def test_a_pass_reuses_the_groups_its_delta_leaves_untouched_and_refuses_a_changed_base(self):
        self.enable()
        contract = self.declare()
        frozen = self.freeze()
        item = (frozen["candidate"]["delivery"], frozen["candidate"]["story"])
        first = verification.run_check(self.root, "live_test", group="smoke,billing")
        self.assertNotIn("reused_live_base", first["identity"])
        self.settle()
        base_commit = frozen["candidate"]["product_commit"]

        self.write("src/smoke/a.txt", "2\n")
        self.commit()
        self.freeze()
        plan = verification.manifest(self.root, *item, "qa_engineer", "qa_final")["live_reuse"]
        self.assertEqual((plan["selected_groups"], plan["reusable_groups"], plan["base_commit"]),
                         (["smoke"], ["billing"], base_commit))
        second = verification.run_check(self.root, "live_test", group="smoke")
        self.assertEqual(second["identity"]["reused_live_base"], {"billing": first["evidence_hash"]})
        self.assertEqual(second["reused_live_base"]["billing"]["cases"], {"tests/live.py::test_billing": "passed"})
        self.assertIn("BASE " + base_commit, verification.raw_output_path(self.root, second["output_file"]).read_text())
        session = verification.read_session(self.root)
        recorded = session["raw_evidence"]["live_test:smoke"]
        self.assertIsNone(verification.live_reuse_problem(self.root, session, recorded, contract))
        output = verification.raw_output_path(self.root, first["output_file"])
        kept = output.read_bytes()
        checks = self.result("qa_engineer", "qa_final")["checks"]
        session = verification.read_session(self.root)
        verification.require_raw_evidence(self.root, session, checks)
        output.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "live_test:smoke evidence reuses a base run of live group billing"
                                                  " whose output is missing or changed"):
            verification.require_raw_evidence(self.root, session, checks)
        output.write_bytes(kept)
        self.settle()

        self.write("live.py", self.root.joinpath("live.py").read_text() + "# catch-all change\n")
        self.commit()
        self.freeze()
        plan = verification.manifest(self.root, *item, "qa_engineer", "qa_final")["live_reuse"]
        self.assertEqual(plan["selected_groups"], ["smoke", "billing"])
        self.assertIn("matches a catch-all live path", plan["groups"]["billing"]["reason"])



@integration
class PassCarryOverTests(unittest.TestCase):
    """At pass_carry_over per_item the runner keeps each pass record, a failed pass's passed cases included,
    and names the Item's record to the next pass; at off it names none."""
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    settle = base.VerificationTests.settle

    def declare(self) -> None:
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract.update(LIVE, live_test_command=executable + " live.py {group}")
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.write("live.py", "import json, os, pathlib, subprocess\n"
                   "named = os.environ.get('AGENTROF_CARRY_OVER_RECORD')\n"
                   "kept = json.loads(pathlib.Path(named).read_text()) if named and os.path.isfile(named) else None\n"
                   "print('RECORD', '-' if named is None else len(kept['passes']) if kept else 0)\n"
                   "head = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()\n"
                   "case = lambda name, outcome: {'case_id': 'tests/t.py::' + name, 'outcome': outcome,\n"
                   "                              'input_paths': ['tests/t.py'], 'input_digest': 'sha256:1'}\n"
                   "pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'], 'carry-over-pass.json').write_text(\n"
                   "    json.dumps({'schema_version': 1, 'project_key': 'p', 'delivery_id': os.environ['AGENTROF_DELIVERY_ID'],\n"
                   "                'item_id': os.environ['AGENTROF_ITEM_ID'], 'run_id': head, 'product_commit': head,\n"
                   "                'candidate_hash': (kept or {}).get('candidate_hash'), 'carried': [],\n"
                   "                'fresh': [case('a', 'passed'), case('b', 'failed')]}))\n"
                   "raise SystemExit(1)\n")
        self.commit()

    def output(self, run) -> str:
        return verification.raw_output_path(self.root, run["output_file"]).read_text()

    def test_a_failed_pass_keeps_its_passed_cases_for_the_next_pass_of_the_item(self):
        original = delivery.delivery_switch_value
        mock.patch.object(delivery, "delivery_switch_value", side_effect=lambda docs, identifier, name:
                          "per_item" if name == "pass_carry_over" else original(docs, identifier, name)).start()
        self.addCleanup(mock.patch.stopall)
        self.declare()
        frozen = self.freeze()
        first = verification.run_check(self.root, "live_test", group="smoke")
        self.assertEqual(first["exit_code"], 1)
        self.assertIn("RECORD 0", self.output(first))
        self.assertNotIn("carry_over_record", first["identity"])
        kept = verification.read_carry_over_record(self.root)
        self.assertEqual({key: kept[key] for key in ("project_key", "delivery_id", "item_id", "candidate_hash")},
                         {"project_key": "p", "delivery_id": "DLV-001", "item_id": "AUTH-01",
                          "candidate_hash": frozen["candidate"]["candidate_hash"]})
        self.assertEqual([(row["product_commit"], [(case["case_id"], case["outcome"]) for case in row["fresh"]])
                          for row in kept["passes"]],
                         [(frozen["candidate"]["product_commit"],
                           [("tests/t.py::a", "passed"), ("tests/t.py::b", "failed")])])

        self.settle()
        self.write("src/next.txt", "2\n")
        self.commit()
        second_candidate = self.freeze()["candidate"]
        second = verification.run_check(self.root, "live_test", group="smoke")
        self.assertIn("RECORD 1", self.output(second))
        self.assertEqual(second["identity"]["carry_over_record"],
                         verification.digest({**kept, "candidate_hash": second_candidate["candidate_hash"]}))
        passes = verification.read_carry_over_record(self.root)["passes"]
        self.assertEqual([row["candidate_hash"] for row in passes],
                         [None, second_candidate["candidate_hash"]])

    def test_without_the_switch_no_record_is_named_or_kept(self):
        self.declare()
        self.freeze()
        with mock.patch.dict(os.environ, {"AGENTROF_CARRY_OVER_RECORD": str(self.root / "foreign.json")}):
            run = verification.run_check(self.root, "live_test", group="smoke")
        self.assertIn("RECORD -", self.output(run))
        self.assertIsNone(verification.read_carry_over_record(self.root))
        self.assertNotIn("AGENTROF_CARRY_OVER_RECORD", run["identity"]["environment_variables"])

    def test_a_pass_record_of_another_item_is_not_kept(self):
        current = {"delivery": "DLV-001", "story": "AUTH-01", "candidate_hash": "sha256:c"}
        scratch = verification.session_path(self.root).parent / "scratch"
        scratch.mkdir(parents=True)
        entry = {"schema_version": 1, "project_key": "p", "delivery_id": "DLV-001", "item_id": "AUTH-02",
                 "run_id": "r", "product_commit": "c", "carried": [], "fresh": []}
        (scratch / "carry-over-pass.json").write_text(json.dumps(entry))
        self.assertIsNone(verification.keep_carry_over_pass(self.root, current))
        (scratch / "carry-over-pass.json").write_text(json.dumps({**entry, "item_id": "AUTH-01"}))
        self.assertEqual(len(verification.keep_carry_over_pass(self.root, current)["passes"]), 1)
        self.assertEqual(len(verification.keep_carry_over_pass(self.root, current)["passes"]), 1)


@integration
class LiveRunTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    settle = base.VerificationTests.settle

    def declare(self, **fields) -> None:
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract.update(fields)
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.write("live.py", "import os, sys\nprint('LIVE', sys.argv[1], os.getcwd())\n")
        self.commit()
        self.freeze()

    def test_failed_live_cases_block_freeze_until_the_repaired_candidate_passes(self):
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.declare(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_groups=["smoke"], live_diagnostic_command=executable + " live.py {group}")
        self.settle()
        self.write("live.py", "import json, os, pathlib, sys\n"
                   "p = pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'])\n"
                   "status = 'passed' if pathlib.Path('src/repaired.txt').exists() else 'failed'\n"
                   "(p / 'live-case-report.json').write_text(json.dumps({'schema_version':1, 'groups':{'smoke':{'tests/live.py::test_case':status}}}))\n"
                   "if 'AGENTROF_LIVE_TESTS' not in os.environ: sys.exit(1)\n"
                   "selection = json.loads(pathlib.Path(os.environ['AGENTROF_LIVE_TESTS']).read_text())\n"
                   "assert selection['selected_test_ids'] == ['tests/live.py::test_case']\n"
                   "print('selected only failed live case')\n"
                   "sys.exit(0 if pathlib.Path('src/repaired.txt').exists() else 1)\n")
        self.commit()
        verification.freeze(self.root, "DLV-001", "AUTH-01", fresh=True)
        verification.register_result(self.root, self.result())
        failure = self.result("qa_engineer", "qa_final", "failed")
        report_path = verification.raw_output_path(self.root, "scratch/live-case-report.json")
        report_path.write_text(json.dumps({"schema_version": 1, "groups": {"smoke": {"test": []}}}))
        with self.assertRaisesRegex(RuntimeError, "literal test ids"):
            verification.live_case_report(self.root)
        report_path.unlink()
        failure["failed_live_cases"] = {"smoke": ["tests/live.py::test_case"]}
        with self.assertRaisesRegex(RuntimeError, "recorded live run evidence"):
            verification.register_result(self.root, failure)
        verification.run_check(self.root, "live_test", group="smoke")
        verification.register_result(self.root, failure)
        with self.assertRaisesRegex(RuntimeError, "passing repair-live"):
            self.freeze()
        failed = verification.repair_live(self.root, "DLV-001", "AUTH-01")
        self.assertEqual(failed["exit_code"], 1)
        with self.assertRaisesRegex(RuntimeError, "passing repair-live"):
            self.freeze()
        self.write("src/repaired.txt", "fixed\n")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "passing repair-live"):
            self.freeze()
        passed = verification.repair_live(self.root, "DLV-001", "AUTH-01")
        self.assertEqual(passed["exit_code"], 0)
        self.assertIn("selected only failed live case", verification.raw_output_path(
            self.root, passed["runs"]["smoke"]["output_file"]).read_text())
        frozen = self.freeze()
        self.assertEqual(frozen["live_repair"]["evidence_hash"], passed["evidence_hash"])

    def test_live_preflight_resumes_pending_phases_once_on_the_same_candidate(self):
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.declare(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_groups=["smoke"], live_resume_command=executable + " live.py {group}")
        self.settle()
        phase_hash = "sha256:" + hashlib.sha256(b"unit passed").hexdigest()
        checkpoint = {"schema_version": 1, "failure": "infrastructure_preflight", "preflight": "dns",
                      "failed_cases": 0, "started_cases_in_phase": 0,
                      "completed_phases": {"unit": phase_hash}, "pending_phases": ["live"],
                      "phase_evidence": {"unit": {"file": "scratch/unit-proof.json", "sha256": phase_hash}}}
        self.write("live.py", "import json, os, pathlib, sys\n"
                   "p = pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'])\n"
                   "if 'AGENTROF_LIVE_RESUME' not in os.environ:\n"
                   "    (p / 'unit-proof.json').write_bytes(b'unit passed')\n"
                   "    (p / 'live-checkpoint.json').write_text(" + repr(json.dumps(checkpoint)) + ")\n"
                   "    print('unit complete; DNS preflight unavailable')\n"
                   "    sys.exit(2)\n"
                   "resume = json.loads(pathlib.Path(os.environ['AGENTROF_LIVE_RESUME']).read_text())\n"
                   "assert resume['pending_phases'] == ['live']\n"
                   "assert resume['completed_phases']['unit'] == " + repr(phase_hash) + "\n"
                   "print('preflight retried; live complete')\n")
        self.commit()
        verification.freeze(self.root, "DLV-001", "AUTH-01", fresh=True)
        first = verification.run_check(self.root, "live_test", group="smoke")
        self.assertEqual(first["exit_code"], 2)
        self.assertEqual(first["live_checkpoint"], checkpoint)
        proof = verification.raw_output_path(self.root, "scratch/unit-proof.json")
        proof.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "one fresh preflight-only"):
            verification.run_check(self.root, "live_test", group="smoke", resume=True)
        proof.write_bytes(b"unit passed")
        resumed = verification.run_check(self.root, "live_test", group="smoke", resume=True)
        self.assertEqual(resumed["exit_code"], 0)
        self.assertEqual(resumed["resume_count"], 1)
        self.assertEqual(resumed["prior_attempt"]["evidence_hash"], first["evidence_hash"])
        self.assertEqual(resumed["identity"]["candidate_hash"], first["identity"]["candidate_hash"])
        with self.assertRaisesRegex(RuntimeError, "one fresh preflight-only"):
            verification.run_check(self.root, "live_test", group="smoke", resume=True)
        for field in ("failed_cases", "started_cases_in_phase"):
            self.assertFalse(verification.valid_live_checkpoint({**checkpoint, field: 1}))

    def test_running_live_group_exposes_heartbeat_output_and_adapter_progress(self):
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.declare(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_groups=["smoke"])
        self.settle()
        self.write("live.py", "import json, os, pathlib, time\n"
                   "p = pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'])\n"
                   "(p / 'live-progress.json').write_text(json.dumps({'test_id':'slow_case','cases_done':1,'cases_total':2}))\n"
                   "print('first case finished', flush=True)\n"
                   "while not (p / 'release-live').exists(): time.sleep(0.02)\n")
        self.commit()
        verification.freeze(self.root, "DLV-001", "AUTH-01", fresh=True)
        result = []
        worker = threading.Thread(target=lambda: result.append(
            verification.run_check(self.root, "live_test", group="smoke")))
        worker.start()
        release = verification.raw_output_path(self.root, "scratch/release-live")
        try:
            deadline = time.monotonic() + 10
            observed = None
            while time.monotonic() < deadline:
                summary = verification.status_summary(self.root)
                observed = summary["runs"].get("live_test:smoke")
                if observed and observed.get("adapter_observation", {}).get("test_id") == "slow_case":
                    break
                time.sleep(0.02)
            self.assertIsNotNone(observed)
            self.assertEqual(observed["state"], "running")
            self.assertEqual(observed["adapter_observation"]["cases_done"], 1)
            self.assertGreater(observed["elapsed_seconds"], 0)
            self.assertIn("first case finished", Path(observed["output_path"]).read_text())
            self.assertEqual(verification.status_summary(self.root, "live_test:smoke")["runs"]
                             ["live_test:smoke"]["state"], "running")
        finally:
            release.touch()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["exit_code"], 0)
        self.assertNotIn("state", verification.status_summary(self.root, "live_test:smoke")["runs"]
                         ["live_test:smoke"])

    def test_each_declared_group_runs_through_the_test_path_with_its_own_evidence(self):
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.declare(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_groups=["smoke", "billing"])
        smoke = verification.run_check(self.root, "live_test", group="smoke")
        self.assertEqual((smoke["exit_code"], smoke["candidate_intact"], smoke["reused"]), (0, True, False))
        self.assertEqual(smoke["identity"]["live_group"], "smoke")
        self.assertEqual(smoke["identity"]["command"], executable + " live.py smoke")
        self.assertEqual(smoke["identity"]["execution_isolation"], "private_clone_v1")
        self.assertIn("environment_hash", smoke["identity"])
        output = verification.raw_output_path(self.root, smoke["output_file"]).read_text()
        self.assertIn("LIVE smoke", output)
        billing = verification.run_check(self.root, "live_test", group="billing")
        self.assertIn("LIVE billing", verification.raw_output_path(self.root, billing["output_file"]).read_text())
        evidence = verification.read_session(self.root)["raw_evidence"]
        self.assertEqual(evidence["live_test:smoke"]["evidence_hash"], smoke["evidence_hash"])
        self.assertEqual(evidence["live_test:billing"]["evidence_hash"], billing["evidence_hash"])
        self.assertNotEqual(smoke["evidence_hash"], billing["evidence_hash"])
        self.assertTrue(verification.run_check(self.root, "live_test", group="smoke")["reused"])
        self.assertFalse(verification.run_check(self.root, "live_test", group="smoke", fresh=True)["reused"])
        summary = verification.status_summary(self.root, "live_test:billing")
        self.assertEqual(summary["runs"]["live_test:billing"]["live_group"], "billing")
        current = verification.read_session(self.root)["candidate"]
        self.assertFalse(any("live" in check for check in verification.required_checks(self.root, current, "qa_engineer")))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = verification.main(["--worktree", str(self.root), "run", "--kind", "live_test", "--group", "billing"])
        self.assertEqual((code, json.loads(stdout.getvalue())["identity"]["live_group"]), (0, "billing"))
        self.settle()

    def test_a_combined_contract_runs_the_selected_groups_in_one_live_run(self):
        """At live_group_run combined, --group a,b or a repeated --group substitutes the names once,
        comma-separated in live_groups order, and keeps one record for the combined run."""
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.declare(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_groups=["smoke", "billing", "audit"], live_group_run="combined")
        with self.assertRaisesRegex(RuntimeError, "not declared"):
            verification.run_check(self.root, "live_test", group="smoke,,billing")
        combined = verification.run_check(self.root, "live_test", group="billing,smoke")
        self.assertEqual((combined["exit_code"], combined["identity"]["live_group"]), (0, "smoke,billing"))
        self.assertEqual(combined["identity"]["command"], executable + " live.py smoke,billing")
        self.assertIn("LIVE smoke,billing", verification.raw_output_path(self.root, combined["output_file"]).read_text())
        evidence = verification.read_session(self.root)["raw_evidence"]
        self.assertEqual(evidence["live_test:smoke,billing"]["evidence_hash"], combined["evidence_hash"])
        self.assertNotIn("live_test:smoke", evidence)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = verification.main(["--worktree", str(self.root), "run", "--kind", "live_test",
                                      "--group", "smoke", "--group", "billing"])
        value = json.loads(stdout.getvalue())
        self.assertEqual((code, value["reused"], value["evidence_hash"]), (0, True, combined["evidence_hash"]))
        self.assertEqual(verification.run_check(self.root, "live_test", group="audit")["identity"]["live_group"], "audit")
        with self.assertRaisesRegex(RuntimeError, "names each declared group once"):
            verification.run_check(self.root, "live_test", group="smoke,smoke")
        interface = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")["live_test_interface"]
        self.assertEqual((interface["run"], interface["groups"]), ("combined", ["smoke", "billing", "audit"]))

    def test_a_per_group_contract_refuses_several_groups_in_one_call(self):
        self.declare(live_test_command="make live GROUP={group}", live_test_workdir=".", live_groups=["smoke", "billing"])
        with self.assertRaisesRegex(RuntimeError, "one live run of several groups needs live_group_run: combined"):
            verification.run_check(self.root, "live_test", group="smoke,billing")
        interface = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")["live_test_interface"]
        self.assertNotIn("run", interface)
        self.assertEqual(interface["command"], "run --kind live_test --group <group>")

    def test_a_contract_without_live_groups_runs_none(self):
        self.declare()
        with self.assertRaisesRegex(RuntimeError, "declares no valid live_test_command"):
            verification.run_check(self.root, "live_test", group="smoke")

    def test_only_a_declared_group_runs(self):
        self.declare(live_test_command="make live GROUP={group}", live_test_workdir=".", live_groups=["smoke"])
        with self.assertRaisesRegex(RuntimeError, "live group 'billing' is not declared"):
            verification.run_check(self.root, "live_test", group="billing")
        with self.assertRaisesRegex(RuntimeError, "requires --group"):
            verification.run_check(self.root, "live_test")
        with self.assertRaisesRegex(RuntimeError, "other kinds take no --group"):
            verification.run_check(self.root, "test", group="smoke")
        self.assertNotIn("live_test:billing", verification.read_session(self.root)["raw_evidence"])


if __name__ == "__main__":
    unittest.main()
