"""Lifecycle-first QA and current-candidate pre-freeze rehearsal evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import os
import shlex
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugins/software-engineering-team/scripts"), str(ROOT / "tools/tests")]
import delivery_compile as delivery
import delivery_verification as verification
import operation_compile
import test_delivery_verification as base
from tools.tests.levels import integration


TABLE = """# Verification Contract

## Verification Rehearsals

| Step | Phase | Group | Target | Waiver |
| --- | --- | --- | --- | --- |
| restart | lifecycle | smoke | tests/live.py::test_restart | - |
| request | live | smoke | tests/live.py::test_request | - |
"""


class PlanTests(unittest.TestCase):
    def test_rehearsal_plan_is_optional_but_validates_every_declared_target(self):
        props = {"live_groups": ["smoke"], "live_diagnostic_command": "run {group}"}
        rows, errors = operation_compile.verification_rehearsals(props, TABLE)
        self.assertEqual(errors, [])
        self.assertEqual([row["phase"] for row in rows], ["lifecycle", "live"])
        self.assertEqual(operation_compile.verification_rehearsals({}, "# Contract"), ([], []))
        for groups in (None, 42, "smoke", {}):
            with self.subTest(groups=groups):
                self.assertTrue(operation_compile.verification_rehearsals({**props, "live_groups": groups}, TABLE)[1])
        for body in (TABLE.replace("| request |", "| restart |"),
                     TABLE.replace("| live |", "| unknown |"),
                     TABLE.replace("| smoke |", "| other |"),
                     TABLE.replace("tests/live.py::test_request", "--all")):
            with self.subTest(body=body):
                self.assertTrue(operation_compile.verification_rehearsals(props, body)[1])

    def test_start_pass_refuses_untried_steps_before_regression_or_freeze(self):
        with mock.patch.object(delivery, "delivery_switch_value", return_value="after_freeze"), \
                mock.patch.object(verification, "require_rehearsals", side_effect=RuntimeError("untried restart")), \
                mock.patch.object(verification, "regression_selection") as selection, \
                mock.patch.object(verification, "regression_run") as run, \
                mock.patch.object(verification, "freeze") as freeze:
            result = verification.start_pass(Path.cwd(), "DLV-001", "AUTH-01")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_step"], "rehearsals")
        selection.assert_not_called()
        run.assert_not_called()
        freeze.assert_not_called()


@integration
class RehearsalTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    settle = base.VerificationTests.settle

    def declare(self, *, runtime=False, waiver=False):
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        props, _body = delivery.split_note(path)
        props.update(live_test_command=executable + " live.py {group}", live_test_workdir=".",
                     live_diagnostic_command=executable + " live.py {group}", live_groups=["smoke"],
                     approved_at_utc="2026-01-01T00:00:00Z")
        body = TABLE.replace("| tests/live.py::test_restart | - |",
                             "| tests/live.py::test_restart | Owner accepts unavailable restart infrastructure for this revision. |") if waiver else TABLE
        props["source_hash"] = operation_compile.receipt_hash(props, body)
        self.write(path.relative_to(self.root), delivery.frontmatter(props, body))
        self.write("live.py", "import json, os, pathlib, sys\n"
                   "p = pathlib.Path(os.environ['AGENTROF_VERIFICATION_SCRATCH'])\n"
                   "if 'AGENTROF_LIVE_TESTS' in os.environ:\n"
                   "    selected = json.loads(pathlib.Path(os.environ['AGENTROF_LIVE_TESTS']).read_text())\n"
                   "    assert len(selected['selected_test_ids']) == 1\n"
                   "    cases = {target: 'passed' for target in selected['selected_test_ids']}\n"
                   "    (p / 'live-case-report.json').write_text(json.dumps({'schema_version':1,'groups':{'smoke':cases}}))\n"
                   "print('live target executed')\n")
        if runtime:
            item, body = delivery.split_note(self.root / self.item_path)
            item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
            self.write(self.item_path, delivery.frontmatter(item, body))
            self.note("workspace/docs/operation/environment-contract.md",
                      {"status": "approved", "env_command": self.command, "env_workdir": ".",
                       "scenarios": ["baseline"], "service_catalog": ["api"]})
        self.commit()

    def test_untried_steps_block_freeze_and_rehearsal_records_bind_current_candidate(self):
        self.declare()
        with mock.patch.object(verification, "qa_preflight", return_value=True):
            status = verification.rehearsal_status(self.root, "DLV-001", "AUTH-01")
            self.assertEqual([row["state"] for row in status["steps"]], ["pending", "pending"])
            self.assertFalse(verification.start_pass(self.root, "DLV-001", "AUTH-01")["ok"])
            with self.assertRaisesRegex(RuntimeError, "untried verification steps"):
                self.freeze()
            with mock.patch.object(verification, "require_repair_review", side_effect=RuntimeError("review first")), \
                    mock.patch.object(verification, "private_checkout_run") as run:
                with self.assertRaisesRegex(RuntimeError, "review first"):
                    verification.rehearse(self.root, "DLV-001", "AUTH-01")
                run.assert_not_called()
            with mock.patch.object(verification, "private_checkout_run", return_value=(
                    subprocess.CompletedProcess("probe", 1, b"failed"), True, "", {}, {})) as run:
                failed = verification.rehearse(self.root, "DLV-001", "AUTH-01")
                self.assertEqual((failed["exit_code"], failed["failed_step"], run.call_count), (1, "restart", 1))
            with mock.patch.object(verification, "private_checkout_run", wraps=verification.private_checkout_run) as run:
                rehearsed = verification.rehearse(self.root, "DLV-001", "AUTH-01")
                self.assertEqual(rehearsed["exit_code"], 0)
                self.assertEqual(run.call_count, 2)
                verification.rehearse(self.root, "DLV-001", "AUTH-01")
                self.assertEqual(run.call_count, 2)
            records = json.loads(verification.raw_output_path(self.root, "scratch/rehearsals.json").read_text())
            verification.raw_output_path(self.root, records["restart"]["output_file"]).write_bytes(b"changed")
            self.assertEqual([row["state"] for row in verification.rehearsal_status(
                self.root, "DLV-001", "AUTH-01")["steps"]], ["pending", "passed"])
            with mock.patch.object(verification, "private_checkout_run", wraps=verification.private_checkout_run) as run:
                verification.rehearse(self.root, "DLV-001", "AUTH-01")
                self.assertEqual(run.call_count, 1)
            frozen = self.freeze()
            self.assertEqual([row["state"] for row in frozen["verification_rehearsals"]["steps"]], ["passed", "passed"])
            self.settle()
            self.write("src/product.py", "value = 3\n")
            self.commit()
            self.assertEqual([row["state"] for row in verification.rehearsal_status(
                self.root, "DLV-001", "AUTH-01")["steps"]], ["pending", "pending"])

    def test_waiver_needs_current_contract_approval_and_enters_the_pass_receipt(self):
        self.declare(waiver=True)
        with mock.patch.object(verification, "qa_preflight", return_value=True):
            status = verification.rehearsal_status(self.root, "DLV-001", "AUTH-01")
            self.assertEqual([row["state"] for row in status["steps"]], ["waived", "pending"])
            verification.rehearse(self.root, "DLV-001", "AUTH-01")
            frozen = self.freeze()
            waived = frozen["verification_rehearsals"]["steps"][0]
            self.assertIn("Owner accepts", waived["waiver"])
            self.assertTrue(waived["approval_hash"].startswith("sha256:"))
            self.settle()
            path = self.root / "workspace/docs/operation/verification-contract.md"
            path.write_text(path.read_text().replace("Owner accepts", "Owner now accepts"))
            self.commit()
            with self.assertRaisesRegex(RuntimeError, "current approved"):
                verification.rehearsal_status(self.root, "DLV-001", "AUTH-01")

    def test_lifecycle_checks_precede_live_groups_and_final_logs_follow_them(self):
        self.declare(runtime=True)
        self.freeze()
        with mock.patch.object(verification, "qa_preflight", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "lifecycle down/up/seed/logs first"):
                verification.run_check(self.root, "live_test", group="smoke")
            for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("logs", None)):
                verification.run_environment(self.root, verb, value)
            verification.run_check(self.root, "live_test", group="smoke")
            verification.run_environment(self.root, "logs")
            verification.run_environment(self.root, "down")
            session = verification.read_session(self.root)
            events = session["runtime"]["events"]
            evidence = {"event_hashes": [event["evidence_hash"] for event in events]}
            verification.require_runtime_evidence(self.root, session, evidence)
            missing = copy.deepcopy(session)
            del missing["runtime"]["events"][-2]
            evidence = {"event_hashes": [event["evidence_hash"] for event in missing["runtime"]["events"]]}
            with self.assertRaisesRegex(RuntimeError, "final log check after"):
                verification.require_runtime_evidence(self.root, missing, evidence)


    def approve(self, events):
        verification.register_result(self.root, self.result())
        final = self.result("qa_engineer", "qa_final")
        final["checks"]["fresh_runtime"]["event_hashes"] = [event["evidence_hash"] for event in events]
        verification.register_result(self.root, final)
        verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_long_lived_passes_reuse_fresh_evidence_of_the_same_environment_code(self):
        self.declare(runtime=True)
        with mock.patch.object(verification, "runtime_lifecycle_policy", return_value=("long_lived", ["deploy/*"])):
            first = self.freeze()
            self.assertEqual(verification.runtime_lifecycle(self.root, first)["reason"], "fresh_evidence_missing")
            with self.assertRaisesRegex(RuntimeError, "fresh runtime requires a successful down before up"):
                verification.run_environment(self.root, "up")
            for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("logs", None), ("down", None)):
                verification.run_environment(self.root, verb, value)
            self.approve(verification.read_session(self.root)["runtime"]["events"])

            self.write("src/product.py", "value = 3\n")
            self.commit()
            second = self.freeze()
            self.assertEqual(verification.runtime_lifecycle(self.root, second)["reason"], "fresh_evidence_held")
            with mock.patch.object(verification, "qa_preflight", return_value=True), \
                    self.assertRaisesRegex(RuntimeError, "up/seed/logs on the long-lived instance first"):
                verification.run_check(self.root, "live_test", group="smoke")
            for verb, value in (("up", None), ("seed", "baseline"), ("logs", None)):
                self.assertEqual(verification.run_environment(self.root, verb, value)["exit_code"], 0)
            session = verification.read_session(self.root)
            events = session["runtime"]["events"]
            self.assertEqual({event["lifecycle"] for event in events}, {"long_lived"})
            self.assertTrue(session["runtime"]["active"])
            self.assertFalse(verification.runtime_needs_cleanup(session))
            evidence = {"event_hashes": [event["evidence_hash"] for event in events]}
            unseeded = copy.deepcopy(session)
            del unseeded["runtime"]["events"][1]
            with self.assertRaisesRegex(RuntimeError, "long-lived runtime evidence requires recorded up/seed/logs"):
                verification.require_runtime_evidence(self.root, unseeded, {"event_hashes": [
                    event["evidence_hash"] for event in unseeded["runtime"]["events"]]})
            unproven = copy.deepcopy(session)
            del unproven["fresh_runtime_history"]
            with self.assertRaisesRegex(RuntimeError, r"follows the long_lived lifecycle but this candidate requires"
                                                      r" fresh \(fresh_evidence_missing\)"):
                verification.require_runtime_evidence(self.root, unproven, evidence)
            self.approve(events)

            self.write("deploy/values.yaml", "replicas: 2\n")
            self.commit()
            third = self.freeze()
            self.assertEqual(verification.runtime_lifecycle(self.root, third),
                             {"mode": "fresh", "reason": "environment_code_changed",
                              "matched_paths": ["deploy/values.yaml"]})
            with self.assertRaisesRegex(RuntimeError, "fresh runtime requires a successful down before up"):
                verification.run_environment(self.root, "up")

if __name__ == "__main__":
    unittest.main()
