"""Opt-in live test groups: a Verification Contract declares `live_test_command`
with one `{group}` placeholder, `live_test_workdir` and `live_groups`, all or
none, and the QA reader runs one declared group at a time through
`run --kind live_test --group <group>`, each group keeping its own evidence
outside the required checks."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
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

    def test_the_fields_bind_the_approved_source_hash(self):
        props = operation_compile.initial_props("verification", []) | LIVE
        changed = props | {"live_groups": ["smoke"]}
        self.assertNotEqual(operation_compile.source_hash(props, "# Body"),
                            operation_compile.source_hash(changed, "# Body"))


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
