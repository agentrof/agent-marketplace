"""Owner handoffs keep a frozen QA pass open without granting reader writes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugins/software-engineering-team/scripts"), str(ROOT / "tools/tests")]
import delivery_verification as verification
import operation_compile
import test_delivery_verification as base
from tools.tests.levels import integration


TABLE = """# Verification Contract

## Owner Steps

| Step | Command | Expected output | Resume at | Timeout seconds |
| --- | --- | --- | --- | --- |
| stop-holder | owner-stop | holder stopped | Continue the restart scenario | 30 |
"""


class OwnerStepTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.current = {"candidate_hash": "sha256:candidate", "delivery": "DLV-001", "story": "AUTH-01"}
        self.session = {"schema_version": 1, "session_id": "frozen-pass", "candidate": self.current,
                        "workers": {role: {"state": "running"} for role in verification.ROLES},
                        "raw_evidence": {"test": {"evidence_hash": "sha256:passed"}},
                        "unresolved_findings": [], "metrics": {"command_cache_hits": 0}}
        verification.write_session(self.root, self.session)
        self.contract = self.root / "workspace/docs/operation/verification-contract.md"
        self.contract.parent.mkdir(parents=True)
        props = {"type": "verification-contract", "status": "approved", "revision": 1}
        props["source_hash"] = operation_compile.source_hash(props, TABLE)
        self.contract.write_bytes(operation_compile.render(props, TABLE).encode())
        for target, options in (("require_current", {"return_value": self.current}),
                                ("command_active", {"return_value": False})):
            patcher = mock.patch.object(verification, target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def output(self, **changes):
        return {"session_id": "frozen-pass", "candidate_hash": "sha256:candidate", "step": "stop-holder",
                "command": "owner-stop", "exit_code": 0, "output": "holder stopped\n",
                "owner_confirmed": True, **changes}

    def test_plan_validates_declared_handoffs(self):
        self.assertEqual(operation_compile.owner_steps("# Contract"), ([], []))
        rows, errors = operation_compile.owner_steps(TABLE)
        self.assertEqual(errors, [])
        self.assertEqual((rows[0]["runner"], rows[0]["timeout_seconds"]), ("owner", 30))
        for text in (TABLE.replace("| 30 |", "| 0 |"), TABLE.replace("| 30 |", "| never |"),
                     TABLE.replace("| 30 |", "| " + "9" * 400 + " |"),
                     TABLE.replace("owner-stop", ""), TABLE.replace("holder stopped", ""),
                     TABLE + "| stop-holder | owner-stop | done | Continue scenario | 30 |\n"):
            with self.subTest(text=text):
                self.assertTrue(operation_compile.owner_steps(text)[1])

    def test_wait_keeps_evidence_and_refuses_early_result_or_commands(self):
        with mock.patch.object(verification.subprocess, "run", side_effect=AssertionError("owner command executed")):
            pending = verification.await_owner_step(self.root, "stop-holder")
            self.assertEqual(pending["state"], "awaiting_owner_step")
            self.assertEqual(verification.await_owner_step(self.root, "stop-holder"), pending)
            self.assertEqual(verification.read_session(self.root)["raw_evidence"], self.session["raw_evidence"])
            for verdict in ("passed", "failed"):
                with self.assertRaisesRegex(RuntimeError, "awaiting_owner_step"):
                    verification.register_result(self.root, {"role": "qa_engineer", "mode": "qa_final", "verdict": verdict})
            with self.assertRaisesRegex(RuntimeError, "awaiting_owner_step"):
                verification._run_check(self.root, "test")
            with mock.patch.object(verification, "wait_holder", return_value=None):
                waiting = verification.wait_for_release(self.root, "qa_engineer", 0.01)
            self.assertFalse(waiting["released"])
            self.assertEqual(waiting["holder"]["step"], "stop-holder")
            completed = verification.record_owner_step(self.root, self.output())
            self.assertEqual(completed["state"], "passed")
            self.assertEqual(completed["resume_at"], "Continue the restart scenario")
            self.assertEqual(verification.record_owner_step(self.root, self.output()), completed)
            verification.require_owner_step_progress(self.root, verification.read_session(self.root), final=True)
        self.assertEqual(verification.read_session(self.root)["workers"], self.session["workers"])

    def test_ordered_steps_cannot_be_skipped(self):
        props, _body = operation_compile.parse(self.contract)
        body = TABLE + "| start-holder | owner-start | holder started | Continue the live checks | 30 |\n"
        props["source_hash"] = operation_compile.source_hash(props, body)
        self.contract.write_bytes(operation_compile.render(props, body).encode())
        with self.assertRaisesRegex(RuntimeError, "next declared"):
            verification.await_owner_step(self.root, "start-holder")
        verification.await_owner_step(self.root, "stop-holder")
        verification.record_owner_step(self.root, self.output())
        with self.assertRaisesRegex(RuntimeError, "passing owner steps: start-holder"):
            verification.require_owner_step_progress(self.root, verification.read_session(self.root), final=True)
        verification.await_owner_step(self.root, "start-holder")
        verification.record_owner_step(self.root, self.output(
            step="start-holder", command="owner-start", output="holder started"))
        verification.require_owner_step_progress(self.root, verification.read_session(self.root), final=True)

    def test_output_is_bound_to_owner_command_and_unchanged_candidate(self):
        verification.await_owner_step(self.root, "stop-holder")
        before = verification.read_session(self.root)
        for changes in ({"session_id": "other"}, {"candidate_hash": "sha256:other"},
                        {"step": "other"}, {"command": "other"}, {"owner_confirmed": False},
                        {"exit_code": True}, {"output": []}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                verification.record_owner_step(self.root, self.output(**changes))
            self.assertEqual(verification.read_session(self.root), before)
        with mock.patch.object(verification, "require_current", side_effect=RuntimeError("candidate changed")):
            with self.assertRaisesRegex(RuntimeError, "candidate changed"):
                verification.record_owner_step(self.root, self.output())
        self.assertEqual(verification.read_session(self.root), before)

    def test_failed_output_and_timeout_cannot_pass(self):
        for changes in ({"exit_code": 1}, {"output": "holder still running"}):
            with self.subTest(changes=changes):
                verification.write_session(self.root, copy.deepcopy(self.session))
                verification.await_owner_step(self.root, "stop-holder")
                self.assertEqual(verification.record_owner_step(self.root, self.output(**changes))["state"], "failed")
                with self.assertRaisesRegex(RuntimeError, "passing owner steps"):
                    verification.require_owner_step_progress(self.root, verification.read_session(self.root), final=True)
        verification.write_session(self.root, copy.deepcopy(self.session))
        with mock.patch.object(verification.time, "time", return_value=1000):
            verification.await_owner_step(self.root, "stop-holder")
        with mock.patch.object(verification.time, "time", return_value=1030):
            self.assertEqual(verification.owner_step_progress(verification.read_session(self.root))[0]["state"], "timed_out")
            with self.assertRaisesRegex(RuntimeError, "deadline expired"):
                verification.record_owner_step(self.root, self.output())
            verification.register_result(self.root, {"role": "qa_engineer", "mode": "qa_final", "verdict": "failed",
                "session_id": "frozen-pass", "candidate_hash": "sha256:candidate", "report": "Owner step timed out"})
        self.assertEqual(verification.read_session(self.root)["workers"]["qa_engineer"]["result"]["verdict"], "failed")


@integration
class OwnerStepIntegrationTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result

    def test_owner_handoff_preserves_one_frozen_pass(self):
        with tempfile.TemporaryDirectory() as external:
            holder = Path(external) / "holder"
            holder.write_text("running", encoding="utf-8")
            script = Path(external) / "owner.py"
            script.write_text("from pathlib import Path\nPath(__file__).with_name('holder').unlink()\nprint('holder stopped')\n", encoding="utf-8")
            argv = [sys.executable, str(script)]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            path = self.root / "workspace/docs/operation/verification-contract.md"
            props, _body = operation_compile.parse(path)
            body = TABLE.replace("owner-stop", command)
            props["source_hash"] = operation_compile.source_hash(props, body)
            self.write(path.relative_to(self.root), operation_compile.render(props, body))
            self.commit()
            frozen = self.freeze()
            first = verification.run_check(self.root, "test")
            pending = verification.await_owner_step(self.root, "stop-holder")
            self.assertTrue(holder.exists())
            with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
                verification.guard_write(self.root)
            output = subprocess.run(argv, cwd=external, capture_output=True, text=True, check=True)
            verification.record_owner_step(self.root, {
                "session_id": frozen["session_id"], "candidate_hash": frozen["candidate"]["candidate_hash"],
                "step": pending["step"], "command": pending["command"], "exit_code": output.returncode,
                "output": output.stdout, "owner_confirmed": True})
            self.assertFalse(holder.exists())
            self.assertEqual(verification.run_check(self.root, "test")["evidence_hash"], first["evidence_hash"])
            verification.register_result(self.root, self.result())
            verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
            complete = verification.validate(self.root, "DLV-001", "AUTH-01")
            self.assertEqual(complete["session_id"], frozen["session_id"])
            self.assertEqual(complete["workers"]["qa_engineer"]["result"]["owner_steps"][0]["state"], "passed")


if __name__ == "__main__":
    unittest.main()
