"""QA verbs a Verification Contract declares run through the bound environment command."""
from __future__ import annotations

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
import delivery_compile as delivery
import delivery_verification as verification
import operation_compile
import test_delivery_verification as base
from tools.tests.levels import integration


TABLE = """# Verification Contract

## QA Verbs

| Verb | Command | Workdir |
| --- | --- | --- |
| recorded_results | results-reader --last | . |
"""


def write_contract(path: Path, body: str, approved: bool = True) -> None:
    props = {"type": "verification-contract", "status": "approved", "revision": 1}
    if approved:
        props["source_hash"] = operation_compile.source_hash(props, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(operation_compile.render(props, body).encode())


class QaVerbTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.contract = self.root / "workspace/docs/operation/verification-contract.md"

    def test_table_declares_unique_verbs_with_exact_commands(self):
        self.assertEqual(operation_compile.qa_verbs("# Contract"), ([], []))
        rows, errors = operation_compile.qa_verbs(TABLE)
        self.assertEqual(errors, [])
        self.assertEqual(rows, [{"verb": "recorded_results", "command": "results-reader --last", "workdir": "."}])
        for text in (TABLE.replace("recorded_results", "up"), TABLE.replace("recorded_results", "two words"),
                     TABLE.replace("results-reader --last", ""), TABLE.replace("--last", "{{target}}"),
                     TABLE.replace("--last", "token=abc123"), TABLE.replace("| . |", "| ../outside |"),
                     TABLE + "| recorded_results | other-reader | . |\n"):
            with self.subTest(text=text):
                self.assertTrue(operation_compile.qa_verbs(text)[1])

    def test_only_an_unchanged_approved_contract_declares_verbs(self):
        write_contract(self.contract, TABLE)
        self.assertEqual(list(verification.declared_qa_verbs(self.root)), ["recorded_results"])
        write_contract(self.contract, TABLE, approved=False)
        with self.assertRaisesRegex(RuntimeError, "unchanged approved"):
            verification.declared_qa_verbs(self.root)
        write_contract(self.contract, "# Verification Contract\n")
        self.assertEqual(verification.declared_qa_verbs(self.root), {})

    def test_undeclared_verbs_are_refused_before_any_command(self):
        write_contract(self.contract, TABLE)
        with mock.patch.object(verification.subprocess, "run", side_effect=AssertionError("command executed")):
            with self.assertRaisesRegex(RuntimeError, "QA Verbs"):
                verification.run_environment(self.root, "other_reader")

    def test_cli_accepts_a_declared_verb(self):
        with mock.patch.object(verification, "run_environment",
                               return_value={"exit_code": 0, "candidate_intact": True}) as run, \
                mock.patch("builtins.print"):
            code = verification.main(["--worktree", str(self.root), "environment", "--verb", "recorded_results"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(self.root, "recorded_results", None)


@integration
class QaVerbIntegrationTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result

    def test_declared_verb_records_evidence_inside_the_fresh_lifecycle(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {
            "status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"],
            "service_catalog": []})
        argv = [sys.executable, "-c", "print('recorded results')"]
        reader = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        props, _body = operation_compile.parse(path)
        contract = TABLE.replace("results-reader --last", reader)
        props["source_hash"] = operation_compile.source_hash(props, contract)
        self.write(path.relative_to(self.root), operation_compile.render(props, contract))
        self.commit()
        self.freeze()
        events = []
        for verb, value in (("down", None), ("recorded_results", None), ("up", None), ("seed", "baseline"),
                            ("recorded_results", None), ("logs", None), ("down", None)):
            event = verification.run_environment(self.root, verb, value)
            self.assertEqual((event["exit_code"], event["candidate_intact"]), (0, True))
            events.append(event)
        declared = events[1]
        self.assertEqual(declared["command"], reader)
        self.assertEqual(declared["output"].strip(), "recorded results")
        with self.assertRaisesRegex(RuntimeError, "does not accept a value"):
            verification.run_environment(self.root, "recorded_results", "baseline")
        result = self.result("qa_engineer", "qa_final")
        result["checks"]["fresh_runtime"]["event_hashes"] = [event["evidence_hash"] for event in events]
        verification.register_result(self.root, result)
        verification.register_result(self.root, self.result())
        candidate = verification.read_session(self.root)["candidate"]
        verification.validate(self.root, candidate["delivery"], candidate["story"])


if __name__ == "__main__":
    unittest.main()
