"""Same-candidate independent verification and write barrier contracts."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from tools.tests.levels import integration
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import delivery_compile as delivery
import delivery_verification as verification
import file_lock
from git_fixture import init_repository, remove_temporary


@integration
class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.root = Path(self.temporary.name).resolve()
        init_repository(self.root, initial_branch="main")
        self.write(".gitignore", ".agentrof/\n")
        self.write("src/product.py", "value = 1\n")
        self.commit()
        base = verification.git(self.root, "rev-parse", "HEAD")
        self.directory = "workspace/docs/delivery/deliveries/dlv-001-sample"
        self.item_path = self.directory + "/items/auth-01/item.md"
        self.note(self.item_path, {
            "type": "delivery-item", "title": "Authentication", "status": "active", "story_id": "AUTH-01",
            "story_path": "backlog/story.md", "test_plan_path": "backlog/test-plan.md",
            "item_plan_hash": "sha256:plan", "verification_schedule": "parallel_snapshot_v1",
            "integration_base_commit": base, "verification_contract_ref": "operation/verification-contract",
            "role_sequence": ["backend_developer", "code_reviewer", "qa_engineer"],
        })
        self.note(self.directory + "/delivery.md", {"type": "delivery", "id": "DLV-001", "definition_of_done_path": "delivery/definition-of-done.md"})
        self.note(self.directory + "/execution-plan.md", {"type": "execution-plan", "plan_hash": "sha256:plan"})
        for name in ("backlog/story.md", "backlog/test-plan.md", "delivery/definition-of-done.md"):
            self.note("workspace/docs/" + name, {"status": "approved"})
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.command = executable + ' -c "print(123)"'
        self.note("workspace/docs/operation/verification-contract.md", {"type": "verification-contract", "status": "approved", "test_command": self.command, "test_workdir": ".", "mutation_disposition": "not_applicable", "dependency_audit_disposition": "not_applicable"})
        for name, kind in (("code-review.md", "code-review"), ("verification.md", "verification")):
            self.note(self.directory + "/items/auth-01/" + name, {"type": kind, "title": name, "status": "draft", "tags": [], "item_plan_hash": "sha256:plan"})
        self.write("src/product.py", "value = 2\n")
        self.commit()
        # Other independent implementation workers may edit the package in this
        # shared checkout. Source-identity drift itself has a separate test.
        self.identity = mock.patch.object(verification, "instruction_identity", return_value="sha256:policy")
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def note(self, relative, props):
        self.write(relative, delivery.frontmatter(props, "# Evidence\n\nReviewed independently.\n"))

    def commit(self):
        for args in (("add", "-A"), ("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Candidate")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def freeze(self):
        return verification.freeze(self.root, "DLV-001", "AUTH-01")

    def result(self, role="code_reviewer", mode="review_initial", verdict="passed"):
        session = verification.read_session(self.root)
        checks = {name: {"passed": True, "evidence": "Independently verified"}
                  for name in verification.required_checks(self.root, session["candidate"], role)}
        if role == "qa_engineer" and mode == "qa_final" and verdict == "passed":
            raw = verification.run_check(self.root, "test")
            checks["full_test_suite"].update(command=self.command, exit_code=0,
                                              environment=raw["identity"]["environment_hash"],
                                              raw_evidence_hash=raw["evidence_hash"])
        return {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                "role": role, "mode": mode, "verdict": verdict, "report": f"Independent {role} result", "checks": checks}

    def settle(self):
        verification.register_result(self.root, self.result())
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))

    # Two shells of one host, apart only in their shell and session variables (#356).
    SHELL = {"PWD": "/reader/worktree", "OLDPWD": "/reader/previous", "SHLVL": "2", "_": "/usr/bin/reader-tool",
             "READER_SESSION_ID": "qa-reader"}
    OTHER_SHELL = {"PWD": "/coordinator/checkout", "OLDPWD": "/coordinator/previous", "SHLVL": "5",
                   "_": "/usr/bin/coordinator-tool", "READER_SESSION_ID": "coordinator", "TERM_SESSION_ID": "w0t1p0"}

    def declare_variables(self, *names):
        """Approve a Verification Contract whose commands read the named variables."""
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract["command_variables"] = list(names)
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.commit()

    def forge_test_identity(self, change):
        """Apply *change* to the settled test evidence's identity, keeping every hash that binds it consistent."""
        self.forge_identity("test", "full_test_suite", change)

    def forge_identity(self, kind, check, change):
        """Apply *change* to the identity of QA's settled *kind* evidence, keeping every hash that binds it."""
        session = verification.read_session(self.root)
        raw = copy.deepcopy(session["raw_evidence"][kind])
        change(raw["identity"])
        raw.pop("evidence_hash")
        raw["evidence_hash"] = verification.digest(raw)
        session["raw_evidence"][kind] = raw
        result = copy.deepcopy(session["workers"]["qa_engineer"]["result"])
        result.pop("result_hash")
        result["checks"][check]["raw_evidence_hash"] = raw["evidence_hash"]
        if check == "full_test_suite":
            result["checks"][check]["environment"] = raw["identity"].get("environment_hash")
        result["result_hash"] = verification.digest(result)
        session["workers"]["qa_engineer"]["result"] = result
        verification.write_session(self.root, session)

    def platform_subprocess_probe(self):
        # Python 3.9 on Windows queries its platform through a string subprocess.
        # Exercise that nested call even on hosts where platform() uses no shell.
        identity = verification.platform.platform()
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        command = executable + ' -c "print(456)"'
        def probe():
            self.assertEqual(subprocess.check_output(command, shell=True, text=True).strip(), "456")
            return identity
        return mock.patch.object(verification.platform, "platform", side_effect=probe)

    def prepare_diagnostic(self):
        self.write("focused_tests.py", """import unittest
class Focused(unittest.TestCase):
    def test_failed(self): self.assertEqual(1 + 1, 2)
    def test_affected(self): self.assertEqual(2 + 2, 4)
    def test_unselected(self): self.fail('unselected test must not run during diagnosis')
""")
        self.write("diagnose.py", """import json, os, pathlib, sys, unittest
selection = json.loads(pathlib.Path(os.environ['AGENTROF_DIAGNOSTIC_TESTS']).read_text())
suite = unittest.defaultTestLoader.loadTestsFromNames(selection['selected_test_ids'])
sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
""")
        contract_path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(contract_path)
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        contract["diagnostic_test_command"] = executable + " diagnose.py"
        self.write(contract_path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.commit()
        frozen = self.freeze()
        selector = verification.session_path(self.root).parent / "scratch/selection.json"
        selector.parent.mkdir(exist_ok=True)
        selection = {"schema_version": 1, "candidate_hash": frozen["candidate"]["candidate_hash"],
                     "failed_test_ids": ["focused_tests.Focused.test_failed"],
                     "affected_test_ids": ["focused_tests.Focused.test_affected"]}
        selector.write_text(json.dumps(selection), encoding="utf-8")
        return selector, selection

    def test_focused_diagnostic_runs_selected_tests_but_cannot_replace_final_suite(self):
        selector, selection = self.prepare_diagnostic()
        with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
            verification.guard_write(self.root, [self.root / "src/product.py"])
        raw = verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        self.assertEqual(raw["exit_code"], 0)
        self.assertTrue(raw["candidate_intact"])
        self.assertTrue(raw["selection_intact"])
        output = verification.raw_output_path(self.root, raw["output_file"]).read_text()
        self.assertIn("Ran 2 tests", output)
        self.assertNotIn("test_unselected", output)
        self.assertEqual(raw["diagnostic_selection"]["selected_test_ids"], sorted(selection["failed_test_ids"] + selection["affected_test_ids"]))
        reused = verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        self.assertTrue(reused["reused"])
        selection["affected_test_ids"] = []
        selector.write_text(json.dumps(selection), encoding="utf-8")
        changed = verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        self.assertFalse(changed["reused"])
        self.assertNotEqual(raw["identity"]["diagnostic_selection_hash"], changed["identity"]["diagnostic_selection_hash"])
        result = self.result("qa_engineer", "qa_diagnostic")
        result["mode"] = "qa_final"
        result["checks"]["full_test_suite"].update(command=self.command, exit_code=0,
            environment=raw["identity"]["environment_hash"], raw_evidence_hash=raw["evidence_hash"])
        with self.assertRaisesRegex(RuntimeError, "same-candidate command evidence"):
            verification.register_result(self.root, result)
        verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))
        verification.resume_qa(self.root)
        original = subprocess.run
        observed = []
        def full_suite(command, *args, **kwargs):
            if command == self.command:
                observed.append(command)
                self.assertNotIn("AGENTROF_DIAGNOSTIC_TESTS", kwargs["env"])
            return original(command, *args, **kwargs)
        with self.platform_subprocess_probe(), \
                mock.patch.dict(os.environ, {"AGENTROF_DIAGNOSTIC_TESTS": "inherited-selection.json"}), \
                mock.patch.object(verification.subprocess, "run", side_effect=full_suite):
            self.settle()
        self.assertEqual(observed, [self.command])
        verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_diagnostic_selection_validation_and_final_command_boundary(self):
        selector, selection = self.prepare_diagnostic()
        for patch in ({"candidate_hash": "stale"}, {"schema_version": True}, {"other": "field"},
                      {"failed_test_ids": [], "affected_test_ids": []},
                      {"failed_test_ids": ["-k"]}, {"failed_test_ids": ["bad\nidentifier"]},
                      {"failed_test_ids": ["same", "same"]}, {"failed_test_ids": [1]}):
            with self.subTest(patch=patch):
                selector.write_text(json.dumps({**selection, **patch}), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "diagnostic"):
                    verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        selector.write_text(json.dumps(selection), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "requires --selection-file"):
            verification.run_check(self.root, "diagnostic_test")
        with self.assertRaisesRegex(RuntimeError, "final commands"):
            verification.run_check(self.root, "test", selection_file=selector)
        escaped = selector.parent / ".." / "scratch" / selector.name
        with self.assertRaisesRegex(RuntimeError, "isolated scratch"):
            verification.run_check(self.root, "diagnostic_test", selection_file=escaped)
        literal = {**selection, "failed_test_ids": ["test[param; $(not_a_command)]"]}
        selector.write_text(json.dumps(literal), encoding="utf-8")
        parsed = verification.diagnostic_selection(self.root, selector, verification.read_session(self.root)["candidate"])
        self.assertEqual(parsed["failed_test_ids"], literal["failed_test_ids"])

    def test_diagnostic_selector_edit_and_restore_invalidates_command_evidence(self):
        selector, _ = self.prepare_diagnostic()
        contract, _ = delivery.split_note(self.root / "workspace/docs/operation/verification-contract.md")
        original = subprocess.run
        def mutate(command, *args, **kwargs):
            if command == contract["diagnostic_test_command"]:
                for target in (selector, Path(kwargs["env"]["AGENTROF_DIAGNOSTIC_TESTS"])):
                    before = target.read_bytes()
                    target.write_bytes(b"temporary selection")
                    target.write_bytes(before)
                return subprocess.CompletedProcess(command, 0, b"selected tests passed")
            return original(command, *args, **kwargs)
        with self.platform_subprocess_probe(), mock.patch.object(verification.subprocess, "run", side_effect=mutate):
            raw = verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        self.assertFalse(raw["selection_intact"])
        self.assertFalse(raw["candidate_intact"])
        self.assertFalse(verification.run_check(self.root, "diagnostic_test", selection_file=selector)["reused"])

    def test_manifest_exposes_diagnostic_input_and_complete_final_result_fields(self):
        self.prepare_diagnostic()
        diagnostic = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_diagnostic")
        self.assertEqual(diagnostic["required_checks"], [])
        self.assertTrue(diagnostic["diagnostic_interface"]["available"])
        self.assertFalse(diagnostic["diagnostic_interface"]["terminal_evidence"])
        final = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")
        checks = final["result_interface"]["checks"]
        self.assertEqual(set(checks), set(final["required_checks"]))
        self.assertEqual(set(checks["full_test_suite"]), {"passed", "evidence", "command", "exit_code", "environment", "raw_evidence_hash"})
        self.assertFalse(checks["full_test_suite"]["passed"])

    def test_legacy_schedule_preserves_bytes_and_hash(self):
        props = {"role_sequence": ["backend_developer", "code_reviewer", "qa_engineer"]}
        original = copy.deepcopy(props)
        before = delivery.content_hash(props, "body")
        self.assertEqual(delivery.verification_schedule(props), "sequential_v1")
        self.assertEqual(delivery.execution_phases(props), [["backend_developer"], ["code_reviewer"], ["qa_engineer"]])
        self.assertEqual(props, original)
        self.assertEqual(delivery.content_hash(props, "body"), before)
        props["verification_schedule"] = "parallel_snapshot_v1"
        self.assertEqual(delivery.execution_phases(props)[-1], ["code_reviewer", "qa_engineer"])
        self.assertNotEqual(delivery.content_hash(props, "body"), before)
        with self.assertRaises(ValueError):
            delivery.verification_schedule({"verification_schedule": "unknown"})

    def test_both_independent_roles_must_settle_before_writes_and_approval(self):
        frozen = self.freeze()
        product = self.root / "src/product.py"
        report = self.root / self.directory / "items/auth-01/code-review.md"
        for path in (product, report):
            with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
                verification.guard_write(self.root, [path])
        verification.guard_write(self.root, [verification.session_path(self.root).parent / "scratch/output.txt"])
        verification.register_result(self.root, self.result())
        with self.assertRaisesRegex(RuntimeError, "qa_engineer"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
            verification.guard_write(self.root)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        verification.guard_write(self.root)
        validated = verification.validate(self.root, "DLV-001", "AUTH-01")
        self.assertEqual(validated["candidate"], frozen["candidate"])
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-001", "story": "AUTH-01"})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.approve_item_evidence(args), 0)
        qa, _ = delivery.split_note(self.root / self.directory / "items/auth-01/verification.md")
        self.assertEqual(qa["verified_commit"], frozen["candidate"]["product_commit"])
        self.assertEqual(qa["verification_mode"], "qa_final")

    def test_diagnostic_settles_without_approval_and_can_resume_unchanged(self):
        self.freeze()
        verification.register_result(self.root, self.result())
        verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))
        verification.guard_write(self.root)
        with self.assertRaisesRegex(RuntimeError, "final passed qa_engineer"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        verification.resume_qa(self.root)
        with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
            verification.guard_write(self.root)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_resumed_diagnostic_preserves_findings_and_nonblocking_dispositions(self):
        self.freeze()
        diagnostic = self.result("qa_engineer", "qa_diagnostic", "failed")
        diagnostic["findings"] = [
            {"id": "QA-1", "severity": "major", "status": "open", "verification": "Recheck failure"},
            {"id": "QA-2", "severity": "Low", "status": "open", "verification": "Document limitation"},
        ]
        verification.register_result(self.root, diagnostic)
        verification.resume_qa(self.root)
        manifest = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")
        self.assertEqual([finding["id"] for finding in manifest["unresolved_findings"]], ["QA-1", "QA-2"])
        final = self.result("qa_engineer", "qa_final")
        with self.assertRaisesRegex(RuntimeError, "inherited finding"):
            verification.register_result(self.root, final)
        final["findings"] = copy.deepcopy(diagnostic["findings"])
        final["findings"][0]["severity"] = "minor"
        with self.assertRaisesRegex(RuntimeError, "severity must be preserved"):
            verification.register_result(self.root, final)
        final["findings"][0].update(severity="major", status="resolved", verification="Failure independently rechecked")
        verification.register_result(self.root, final)
        verification.register_result(self.root, self.result())
        verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_runtime_paths_reject_windows_junction_metadata_hardlinks_and_aliases(self):
        self.freeze()
        scratch = verification.session_path(self.root).parent / "scratch"
        scratch.mkdir()
        original = Path.lstat
        def junction(path, *args, **kwargs):
            if path == scratch:
                return type("Metadata", (), {"st_mode": stat.S_IFDIR, "st_file_attributes": 0x400})()
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", junction):
            with self.assertRaisesRegex(RuntimeError, "junctions"):
                verification.raw_output_path(self.root, "scratch/output.log")
        for relative in ("scratch//output.log", "scratch/./output.log", "scratch/output.log:stream"):
            with self.subTest(relative=relative), self.assertRaisesRegex(RuntimeError, "isolated scratch"):
                verification.raw_output_path(self.root, relative)
        lock = verification.session_path(self.root).with_name("commands.lock")
        other = scratch / "aliased-lock"
        other.write_text("unchanged")
        try:
            os.link(other, lock)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with self.assertRaisesRegex(RuntimeError, "regular and unshared"):
            verification.command_active(self.root)
        self.assertEqual(other.read_text(), "unchanged")

    def test_final_qa_needs_complete_and_real_command_evidence(self):
        self.freeze()
        result = self.result("qa_engineer", "qa_diagnostic")
        result["mode"] = "qa_final"
        result["checks"]["full_test_suite"].update(command=self.command, exit_code=0, environment="fixture")
        with self.assertRaisesRegex(RuntimeError, "same-candidate command evidence"):
            verification.register_result(self.root, result)
        result = self.result("qa_engineer", "qa_final")
        del result["checks"]["right_reason"]
        with self.assertRaisesRegex(RuntimeError, "right_reason"):
            verification.register_result(self.root, result)

    def test_source_policy_and_candidate_changes_invalidate_result(self):
        self.freeze()
        result = self.result()
        with mock.patch.object(verification, "instruction_identity", return_value="changed"):
            with self.assertRaisesRegex(RuntimeError, "bindings changed"):
                verification.register_result(self.root, result)
        self.write("src/product.py", "value = 3\n")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "bindings changed"):
            verification.register_result(self.root, result)
        for role, mode in (("code_reviewer", "review_initial"), ("qa_engineer", "qa_final")):
            cancelled = {**result, "role": role, "mode": mode, "verdict": "cancelled", "cancellation_confirmed": True}
            verification.register_result(self.root, cancelled)
        verification.guard_write(self.root)

    def test_raw_output_reuse_requires_exact_bytes_and_fresh_failure_invalidates(self):
        self.freeze()
        first = verification.run_check(self.root, "test")
        reused = verification.run_check(self.root, "test")
        self.assertFalse(first["reused"])
        self.assertTrue(reused["reused"])
        self.assertEqual(first["evidence_hash"], reused["evidence_hash"])
        (verification.session_path(self.root).parent / first["output_file"]).write_bytes(b"tampered")
        self.assertFalse(verification.run_check(self.root, "test")["reused"])
        original = subprocess.run
        def fail_shell(command, *args, **kwargs):
            return subprocess.CompletedProcess(command, 1, b"failed") if command == self.command else original(command, *args, **kwargs)
        with self.platform_subprocess_probe(), mock.patch.object(verification.subprocess, "run", side_effect=fail_shell):
            failed = verification.run_check(self.root, "test", fresh=True)
        self.assertEqual(failed["exit_code"], 1)
        self.assertEqual(verification.read_session(self.root)["raw_evidence"]["test"]["exit_code"], 1)

    def status(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = verification.main(["--worktree", str(self.root), "status", *arguments])
        return code, json.loads(output.getvalue())

    def test_status_summary_prints_run_outcomes_without_the_session_bindings(self):
        frozen = self.freeze()
        raw = verification.run_check(self.root, "test")
        code, summary = self.status("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(summary["session_id"], frozen["session_id"])
        self.assertEqual(summary["candidate_hash"], frozen["candidate"]["candidate_hash"])
        self.assertEqual(summary["workers"], {"code_reviewer": "running", "qa_engineer": "running"})
        run = summary["runs"]["test"]
        self.assertEqual({key: run[key] for key in ("exit_code", "candidate_intact", "evidence_hash")},
                         {"exit_code": 0, "candidate_intact": True, "evidence_hash": raw["evidence_hash"]})
        self.assertEqual(run["environment_hash"], raw["identity"]["environment_hash"])
        self.assertEqual(Path(run["output_path"]).read_text().strip(), "123")
        self.assertNotIn("source_observations", json.dumps(summary))
        _code, whole = self.status()
        self.assertLess(len(json.dumps(summary)), len(json.dumps(whole)))
        code, narrowed = self.status("--run", "test")
        self.assertEqual((code, narrowed["runs"]), (0, {"test": run}))
        code, missing = self.status("--run", "mutation")
        self.assertEqual(code, 2)
        self.assertIn("no mutation run is recorded", missing["errors"][0])

    def test_forged_session_and_missing_runtime_do_not_grant_approval(self):
        self.freeze()
        path = verification.session_path(self.root)
        current = json.loads(path.read_text())
        current["workers"]["qa_engineer"]["state"] = "settled"
        path.write_text(json.dumps(current))
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_scoped_mutation_file_is_compiler_derived_and_no_shell_path_interpolation(self):
        frozen = self.freeze()
        value = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")
        scope = json.loads(Path(value["mutation_scope_file"]).read_text())
        self.assertEqual(scope["files"], ["src/product.py"])
        self.assertEqual(scope["candidate_hash"], frozen["candidate"]["candidate_hash"])
        self.assertEqual(scope["scope"], "whole_changed_files")

    def test_the_run_identity_names_the_declared_variables_it_covers_without_their_values(self):
        self.declare_variables("ITEM_DATABASE_URL", "ITEM_TOKEN")
        self.freeze()
        secret = "s3cret-item-token-value"
        with mock.patch.dict(os.environ, {**self.SHELL, "ITEM_TOKEN": secret, "LC_TIME": "C",
                                          "AGENTROF_DIAGNOSTIC_TESTS": "inherited-selection.json"}):
            os.environ.pop("ITEM_DATABASE_URL", None)
            raw = verification.run_check(self.root, "test")
        names = raw["identity"]["environment_variables"]
        self.assertEqual(names, sorted(set(names)))
        # The fixed variables and the contract's are covered even unset, and the namespaces as they are set.
        for name in ("AGENTROF_MUTATION_FILES", "AGENTROF_VERIFICATION_SCRATCH", "HOME", "ITEM_DATABASE_URL",
                     "ITEM_TOKEN", "LANG", "LC_TIME", "PATH", "TZ"):
            self.assertIn(name, names)
        declared = {"HOME", "ITEM_DATABASE_URL", "ITEM_TOKEN", "LANG", "PATH", "TZ"}
        self.assertEqual([name for name in names if name not in declared
                          and not name.startswith(("AGENTROF_", "LC_"))], [])
        for name in (*self.SHELL, "AGENTROF_DIAGNOSTIC_TESTS"):
            self.assertNotIn(name, names)
        self.assertRegex(raw["identity"]["environment_hash"], r"^hmac-sha256:[0-9a-f]{64}$")
        self.assertNotIn(secret, verification.session_path(self.root).read_text(encoding="utf-8"))
        self.assertNotIn(secret, json.dumps(raw))
        # Only its hash binds the secret, so a changed secret is still a changed identity.
        with mock.patch.dict(os.environ, {**self.SHELL, "ITEM_TOKEN": "another-value", "LC_TIME": "C"}):
            os.environ.pop("ITEM_DATABASE_URL", None)
            self.assertFalse(verification.run_check(self.root, "test")["reused"])

    def test_a_recorded_run_is_reused_from_another_shell_but_not_after_a_declared_variable_changes(self):
        self.declare_variables("ITEM_DATABASE_URL")
        self.freeze()
        base = {**self.SHELL, "ITEM_DATABASE_URL": "postgres://item-a"}
        with mock.patch.dict(os.environ, base):
            first = verification.run_check(self.root, "test")
        self.assertFalse(first["reused"])
        with mock.patch.dict(os.environ, {**base, **self.OTHER_SHELL}):
            again = verification.run_check(self.root, "test")
        self.assertTrue(again["reused"])
        self.assertEqual(again["evidence_hash"], first["evidence_hash"])
        for label, change in (("PATH", {"PATH": os.environ.get("PATH", "") + os.pathsep + "/opt/other/bin"}),
                              ("contract variable", {"ITEM_DATABASE_URL": "postgres://item-b"}),
                              ("locale", {"LC_ALL": "C"}),
                              ("time zone", {"TZ": "Pacific/Auckland"}),
                              ("runner namespace", {"AGENTROF_SAMPLE": "set"})):
            with self.subTest(changed=label):
                with mock.patch.dict(os.environ, base):
                    verification.run_check(self.root, "test")
                with mock.patch.dict(os.environ, {**base, **change}):
                    self.assertFalse(verification.run_check(self.root, "test")["reused"])

    def test_approval_checks_the_recorded_identity_and_never_the_checkers_environment(self):
        self.freeze()
        with mock.patch.dict(os.environ, self.SHELL):
            self.settle()
        # Another shell approves, with another declared value too: approval never compares the two.
        checker = {**self.OTHER_SHELL, "VERIFICATION_TEST_ENV": "changed",
                   "PATH": os.environ.get("PATH", "") + os.pathsep + "/opt/approver/bin"}
        with mock.patch.dict(os.environ, checker):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        names = verification.read_session(self.root)["raw_evidence"]["test"]["identity"]["environment_variables"]
        refusals = (
            ("full_test_suite evidence carries the whole-environment hash of an earlier runner, which binds the shell"
             " that ran it; freeze the candidate again with freeze --fresh and rerun both readers",
             lambda identity: identity.pop("environment_variables")),
            ("full_test_suite evidence does not cover PATH",
             lambda identity: identity["environment_variables"].remove("PATH")),
            ("full_test_suite evidence does not cover AGENTROF_MUTATION_FILES",
             lambda identity: identity["environment_variables"].remove("AGENTROF_MUTATION_FILES")),
            ("full_test_suite evidence covers OLDPWD, PWD, which no declaration names",
             lambda identity: identity.update(environment_variables=sorted([*names, "OLDPWD", "PWD"]))),
            ("full_test_suite evidence must name each variable it covers once, in order",
             lambda identity: identity.update(environment_variables=sorted(names, reverse=True))),
            ("full_test_suite evidence records no environment hash",
             lambda identity: identity.update(environment_hash="changed")),
            ("full_test_suite evidence records no python",
             lambda identity: identity.pop("python")),
            ("full_test_suite evidence does not run the approved command in its approved workdir",
             lambda identity: identity.update(command=self.command + " --other")),
            ("full_test_suite evidence does not run the approved command in its approved workdir",
             lambda identity: identity.update(kind="mutation")),
        )
        original = verification.session_path(self.root).read_bytes()
        for message, change in refusals:
            with self.subTest(message=message):
                verification.session_path(self.root).write_bytes(original)
                self.forge_test_identity(change)
                with mock.patch.dict(os.environ, checker), self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    verification.validate(self.root, "DLV-001", "AUTH-01")
        verification.session_path(self.root).write_bytes(original)
        raw = verification.read_session(self.root)["raw_evidence"]["test"]
        with mock.patch.object(verification.time, "time", return_value=raw["completed_at"] + 86401), \
                self.assertRaisesRegex(RuntimeError, "full_test_suite evidence expired"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        (verification.session_path(self.root).parent / raw["output_file"]).unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or changed"):
            verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_the_tracked_environment_hash_checks_no_guess_of_a_declared_value(self):
        """Evidence approval writes QA's checks, the run identity's environment hash among them, into the Item's
        tracked verification record. A hash of a few guessable values would let anyone who reads that record
        confirm a guess of a declared credential offline, so the hash is keyed with a random key that never
        leaves the Item's verification runtime, where every identity is compared (#356)."""
        self.declare_variables("ITEM_TOKEN")
        self.freeze()
        with mock.patch.dict(os.environ, {"ITEM_TOKEN": "7319"}):
            self.settle()
            environment = verification.command_environment(self.root)
        identity = verification.read_session(self.root)["raw_evidence"]["test"]["identity"]
        # Every covered value known and the declared one guessed right: the guess still checks nothing.
        values = {name: verification.variable_value(environment, name) for name in identity["environment_variables"]}
        self.assertEqual(values["ITEM_TOKEN"], "7319")
        canonical = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        self.assertNotEqual(identity["environment_hash"], "sha256:" + hashlib.sha256(canonical).hexdigest())
        key = verification.session_path(self.root).parent / "identity.key"
        self.assertEqual(len(key.read_bytes()), 32)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        self.assertEqual(identity["environment_hash"],
                         "hmac-sha256:" + hmac.new(key.read_bytes(), canonical, hashlib.sha256).hexdigest())
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-001", "story": "AUTH-01"})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.approve_item_evidence(args), 0)
        record = (self.root / self.directory / "items/auth-01/verification.md").read_text(encoding="utf-8")
        self.assertIn(identity["environment_hash"], record)
        self.assertNotIn("7319", record)

    def test_a_contract_that_names_a_runner_variable_refuses_before_any_command_runs(self):
        """A run identity drops a selection file the runner writes and binds it by content, so a contract that
        declared one would refuse QA's evidence at every check, a fresh rerun too: the run refuses at once and
        names the contract's error (#356)."""
        self.declare_variables("ITEM_TOKEN", "AGENTROF_REUSED_TESTS")
        self.freeze()
        refusal = ("command_variables must not name AGENTROF_REUSED_TESTS, a variable of the runner's own AGENTROF_"
                   " namespace, which run evidence binds without a declaration")
        for fresh in (False, True):
            with self.subTest(fresh=fresh), self.assertRaisesRegex(RuntimeError, "^" + re.escape(refusal) + "$"):
                verification.run_check(self.root, "test", fresh=fresh)
        self.assertNotIn("test", verification.read_session(self.root)["raw_evidence"])
        for name in ("AGENTROF_DIAGNOSTIC_TESTS", "AGENTROF_MUTATION_FILES", "agentrof_reused_tests"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, f"^command_variables must not name {name},"):
                verification.contract_variables({"command_variables": ["ITEM_TOKEN", name]})

    def test_every_final_record_runs_in_the_full_suites_declared_environment(self):
        """Every final record of a session shares the declared environment of its full_test_suite evidence, as
        the comparison with one environment did before (#356), and only that evidence may reuse a pre-handoff
        run (#354)."""
        contract_path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(contract_path)
        contract.update(mutation_disposition="required", mutation_command=self.command,
                        dependency_audit_disposition="required", dependency_audit_command=self.command)
        self.write(contract_path.relative_to(self.root).as_posix(), delivery.frontmatter(contract, body))
        self.commit()
        frozen = self.freeze()
        verification.register_result(self.root, self.result())
        kinds = {"full_test_suite": "test", "mutation_whole_changed_files": "mutation",
                 "dependency_audit": "dependency_audit"}

        def final(elsewhere=None):
            """QA's final result, the check *elsewhere* names run in another declared time zone."""
            result = self.result("qa_engineer", "qa_diagnostic")
            result.update(mode="qa_final", checks={
                name: {"passed": True, "evidence": "Independently verified"}
                for name in verification.required_checks(self.root, frozen["candidate"], "qa_engineer")})
            for check, kind in kinds.items():
                with mock.patch.dict(os.environ, {"TZ": "Pacific/Chatham"} if check == elsewhere else {}):
                    raw = verification.run_check(self.root, kind)
                result["checks"][check]["raw_evidence_hash"] = raw["evidence_hash"]
                if check == "full_test_suite":
                    result["checks"][check].update(command=self.command, exit_code=0,
                                                   environment=raw["identity"]["environment_hash"])
            result["checks"]["mutation_whole_changed_files"]["files"] = frozen["candidate"]["mutation_files"]
            return result

        for check in ("mutation_whole_changed_files", "dependency_audit"):
            with self.subTest(elsewhere=check), self.assertRaisesRegex(
                    RuntimeError, f"^{check} evidence ran in another environment than full_test_suite evidence$"):
                verification.register_result(self.root, final(check))
        verification.register_result(self.root, final())
        verification.validate(self.root, "DLV-001", "AUTH-01")
        reuse = {"evidence_hash": "sha256:" + "0" * 64, "test_ids": ["tests/test_product.py::test_value"],
                 "earlier_stories": [{"delivery": "DLV-000", "story": "AUTH-00",
                                      "test_ids": ["tests/test_product.py::test_value"]}]}
        original = verification.session_path(self.root).read_bytes()
        for check, kind in kinds.items():
            if check == "full_test_suite":
                continue
            with self.subTest(reuse=check):
                verification.session_path(self.root).write_bytes(original)
                self.forge_identity(kind, check, lambda identity: identity.update(reused_pre_handoff=reuse))
                with self.assertRaisesRegex(RuntimeError, f"^{check} evidence reuses a pre-handoff run, which only"
                                                          " the full test suite does$"):
                    verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_repair_preserves_finding_ids_and_requires_explicit_resolution(self):
        self.freeze()
        failed = self.result(verdict="failed")
        failed["findings"] = [{"id": "CR-1", "severity": "major", "verification": "Run regression", "status": "open"}]
        verification.register_result(self.root, failed)
        verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic", "failed"))
        self.write("src/product.py", "value = 3\n")
        self.commit()
        self.freeze()
        manifest = verification.manifest(self.root, "DLV-001", "AUTH-01", "code_reviewer", "review_repair")
        self.assertEqual(manifest["repair_delta"], ["src/product.py"])
        self.assertEqual(manifest["unresolved_findings"][0]["id"], "CR-1")
        result = self.result(mode="review_repair")
        with self.assertRaisesRegex(RuntimeError, "inherited finding"):
            verification.register_result(self.root, result)
        result["findings"] = [{"id": "CR-1", "severity": "major", "verification": "Regression passes", "status": "resolved"}]
        verification.register_result(self.root, result)

    def test_runtime_mutation_and_audit_requirements_come_from_item_and_contract(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item["runtime_required"] = True
        self.write(self.item_path, delivery.frontmatter(item, body))
        contract_path = "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(self.root / contract_path)
        contract.update(mutation_disposition="required", mutation_command=self.command,
                        dependency_audit_disposition="required", dependency_audit_command=self.command)
        self.write(contract_path, delivery.frontmatter(contract, body))
        self.commit()
        frozen = self.freeze()
        checks = verification.required_checks(self.root, frozen["candidate"], "qa_engineer")
        self.assertIn("fresh_runtime", checks)
        self.assertIn("mutation_whole_changed_files", checks)
        self.assertIn("dependency_audit", checks)
        result = self.result("qa_engineer", "qa_final")
        result["checks"]["mutation_whole_changed_files"]["files"] = []
        with self.assertRaisesRegex(RuntimeError, "every compiler-selected"):
            verification.register_result(self.root, result)


    def test_mutation_scope_excludes_environment_assets_config_and_tests_but_expands_approved_paths(self):
        names = ["src/settings.json", "tests/test_product.py", "web/logo.svg", "workspace/environment/start.py", "src/new.unknown"]
        for name in names:
            self.write(name, "example")
        self.commit()
        self.assertEqual(verification.mutation_scope(self.root, names), ["src/new.unknown"])
        path = "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(self.root / path)
        contract["mutation_include_paths"] = ["src/settings.json", "tests/test_product.py", "workspace/environment"]
        self.write(path, delivery.frontmatter(contract, body))
        self.assertEqual(verification.mutation_scope(self.root, names), ["src/settings.json", "tests/test_product.py", "src/new.unknown"])

    def test_inspect_and_diff_read_exact_frozen_git_bytes_without_writing(self):
        frozen = self.freeze()
        self.assertEqual(verification.inspect_candidate(self.root, "src/product.py")["content"], "value = 2\n")
        self.assertEqual(verification.inspect_candidate(self.root, "src/product.py", base=True)["content"], "value = 1\n")
        self.assertIn("+value = 2", verification.candidate_diff(self.root, ["src/product.py"])["diff"])
        with self.assertRaisesRegex(RuntimeError, "normalized"):
            verification.inspect_candidate(self.root, "../private")
        self.assertIn("content", verification.inspect_instruction(self.root, "constitution.md"))
        with self.assertRaisesRegex(RuntimeError, "normalized"):
            verification.inspect_instruction(self.root, "scripts/../../private")
        self.assertEqual(verification.require_current(self.root, frozen), frozen["candidate"])

    def test_same_candidate_complete_freeze_is_idempotent_and_severity_cannot_bypass(self):
        first = self.freeze()
        result = self.result()
        for severity in ("HIGH", "High", "critical", "P1", "undocumented"):
            result["findings"] = [{"id": "CR-1", "severity": severity, "verification": "Check", "status": "open"}]
            with self.assertRaises(RuntimeError):
                verification.register_result(self.root, result)
        self.settle()
        reused = self.freeze()
        self.assertEqual(reused["session_id"], first["session_id"])
        self.assertTrue(reused["reused"])
        self.assertNotEqual(verification.freeze(self.root, "DLV-001", "AUTH-01", fresh=True)["session_id"], first["session_id"])

    def test_command_checkout_is_private_and_cannot_change_reviewers_product(self):
        self.freeze()
        original = subprocess.run
        observed = []
        def mutant(command, *args, **kwargs):
            if command == self.command:
                working = Path(kwargs["cwd"])
                observed.append(working)
                self.assertNotEqual(working, self.root)
                (working / "src/product.py").write_text("mutant")
                return subprocess.CompletedProcess(command, 0, b"mutated")
            return original(command, *args, **kwargs)
        with self.platform_subprocess_probe(), mock.patch.object(verification.subprocess, "run", side_effect=mutant):
            raw = verification.run_check(self.root, "test")
        self.assertTrue(observed)
        self.assertFalse(raw["candidate_intact"])
        self.assertEqual((self.root / "src/product.py").read_text(), "value = 2\n")
        with verification.command_lock(self.root, "qa_engineer", "run --kind test"):
            with self.assertRaisesRegex(RuntimeError, "to exit before settling its reader"):
                verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))

    # A suite command that runs until the test releases it.
    GATED_SUITE = """import pathlib, sys, time
gate = pathlib.Path(sys.argv[1])
(gate / "started").write_text("started", encoding="utf-8")
deadline = time.monotonic() + 60
while not (gate / "release").exists() and time.monotonic() < deadline:
    time.sleep(0.02)
print("suite passed")
"""

    def test_a_finished_reader_registers_while_the_other_readers_command_runs(self):
        """The code reviewer registers while QA's verification command holds the command lock; QA's own result
        waits for its command, and every barrier holds (#355)."""
        markers = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, markers)
        gate = Path(markers.name).resolve()
        (gate / "suite.py").write_text(self.GATED_SUITE, encoding="utf-8")
        arguments = [sys.executable, str(gate / "suite.py"), str(gate)]
        self.command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract["test_command"] = self.command
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.commit()
        self.freeze()
        outcome: dict = {}

        def run() -> None:
            try:
                outcome["run"] = verification.run_check(self.root, "test")
            except Exception as exc:  # noqa: BLE001 - reported by the assertions below
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()

        def release() -> None:
            (gate / "release").write_text("release", encoding="utf-8")
            thread.join(60)

        self.addCleanup(release)
        deadline = time.monotonic() + 60
        while not (gate / "started").exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((gate / "started").exists(), outcome)
        holder = verification.command_holder(self.root)
        self.assertEqual({key: holder.get(key) for key in ("role", "command", "pid")},
                         {"role": "qa_engineer", "command": "run --kind test", "pid": os.getpid()})
        self.assertRegex(holder["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        waiting = (r"^wait for qa_engineer's verification command `run --kind test` in process \d+ since \S+ to exit"
                   r" before settling its reader; `wait --role qa_engineer` returns once it has$")
        with self.assertRaisesRegex(RuntimeError, waiting):
            verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))
        verification.register_result(self.root, self.result())
        session = verification.read_session(self.root)
        self.assertEqual((session["workers"]["code_reviewer"]["state"], session["workers"]["qa_engineer"]["state"]),
                         ("settled", "running"))
        # QA still reads and its command still runs, so the writer and the approval keep waiting.
        with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
            verification.guard_write(self.root, [self.root / "src/product.py"])
        with self.assertRaisesRegex(RuntimeError, "verification command is still running"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        release()
        self.assertNotIn("error", outcome)
        raw = outcome["run"]
        self.assertEqual((raw["exit_code"], raw["candidate_intact"]), (0, True))
        # The command's record reached the session that the code reviewer's registration rewrote.
        session = verification.read_session(self.root)
        self.assertEqual(session["raw_evidence"]["test"]["evidence_hash"], raw["evidence_hash"])
        self.assertEqual(session["workers"]["code_reviewer"]["state"], "settled")
        self.assertIsNone(verification.command_holder(self.root))
        self.assertFalse(verification.session_path(self.root).with_name("command-owner.json").exists())
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        verification.validate(self.root, "DLV-001", "AUTH-01")

    # An environment command whose up runs until the test releases it.
    GATED_ENVIRONMENT = """import pathlib, sys, time
gate = pathlib.Path(sys.argv[1])
if sys.argv[2] == "up":
    (gate / "started").write_text("started", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not (gate / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
print(sys.argv[2])
"""

    def test_qa_never_settles_while_its_environment_verb_runs(self):
        """An environment verb holds the verification command lock as QA's command, so QA's own result waits
        for it and the code reviewer, which runs none, registers meanwhile (#355)."""
        markers = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, markers)
        gate = Path(markers.name).resolve()
        (gate / "environment.py").write_text(self.GATED_ENVIRONMENT, encoding="utf-8")
        arguments = [sys.executable, str(gate / "environment.py"), str(gate)]
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {
            "status": "approved", "env_command": subprocess.list2cmdline(arguments) if os.name == "nt"
            else shlex.join(arguments), "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        self.assertEqual(verification.run_environment(self.root, "down")["exit_code"], 0)
        outcome: dict = {}

        def run() -> None:
            try:
                outcome["event"] = verification.run_environment(self.root, "up")
            except Exception as exc:  # noqa: BLE001 - reported by the assertions below
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()

        def release() -> None:
            (gate / "release").write_text("release", encoding="utf-8")
            thread.join(60)

        self.addCleanup(release)
        deadline = time.monotonic() + 60
        while not (gate / "started").exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((gate / "started").exists(), outcome)
        holder = verification.command_holder(self.root)
        self.assertEqual({key: holder.get(key) for key in ("role", "command", "pid")},
                         {"role": "qa_engineer", "command": "environment --verb up", "pid": os.getpid()})
        with self.assertRaisesRegex(RuntimeError, r"^wait for qa_engineer's verification command `environment --verb"
                                                  r" up` in process \d+ since \S+ to exit before settling its"
                                                  r" reader; `wait --role qa_engineer` returns once it has$"):
            verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))
        verification.register_result(self.root, self.result())
        release()
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["event"]["exit_code"], 0)
        self.assertEqual(verification.read_session(self.root)["workers"]["code_reviewer"]["state"], "settled")

    def test_a_command_that_has_not_recorded_its_owner_holds_every_reader(self):
        self.freeze()
        lock = verification.session_path(self.root).with_name("commands.lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, descriptor)
        self.assertTrue(file_lock.try_lock(descriptor))
        self.addCleanup(file_lock.unlock, descriptor)
        self.assertEqual(verification.command_holder(self.root), {})
        for role, mode in (("code_reviewer", "review_initial"), ("qa_engineer", "qa_diagnostic")):
            with self.subTest(role=role), self.assertRaisesRegex(
                    RuntimeError, "^wait for a verification command that has not recorded its owner yet to exit"
                                  f" before settling its reader; `wait --role {role}` returns once it has$"):
                verification.register_result(self.root, self.result(role, mode))
            # The wait holds the same readers the registration refuses.
            with self.subTest(role=role, step="wait"):
                waited = verification.wait_for_release(self.root, role, 0.2)
                self.assertEqual((waited["released"], waited["holder"]),
                                 (False, {"lock": "verification_command"}))

    def test_wait_returns_once_the_readers_own_command_exits(self):
        """QA waits for its own verification command through wait, which returns as soon as the command exits
        and otherwise at the call's bound with the holder; the code reviewer, which runs none, is never held."""
        markers = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, markers)
        gate = Path(markers.name).resolve()
        (gate / "suite.py").write_text(self.GATED_SUITE, encoding="utf-8")
        arguments = [sys.executable, str(gate / "suite.py"), str(gate)]
        self.command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(path)
        contract["test_command"] = self.command
        self.write(path.relative_to(self.root), delivery.frontmatter(contract, body))
        self.commit()
        self.freeze()
        outcome: dict = {}

        def run() -> None:
            try:
                outcome["run"] = verification.run_check(self.root, "test")
            except Exception as exc:  # noqa: BLE001 - reported by the assertions below
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()

        def release() -> None:
            (gate / "release").write_text("release", encoding="utf-8")
            thread.join(60)

        self.addCleanup(release)
        deadline = time.monotonic() + 60
        while not (gate / "started").exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((gate / "started").exists(), outcome)
        waited = verification.wait_for_release(self.root, "code_reviewer", 5)
        self.assertEqual((waited["released"], "holder" in waited), (True, False))
        self.assertLess(waited["waited_seconds"], 1)
        # A wait with no role also waits for the environment lock, which the command holds outside its own.
        for role, lock in (("qa_engineer", "verification_command"), (None, "environment")):
            with self.subTest(role=role):
                started = time.monotonic()
                waited = verification.wait_for_release(self.root, role, 0.3)
                self.assertGreaterEqual(time.monotonic() - started, 0.3)
                self.assertFalse(waited["released"])
                self.assertEqual({key: waited["holder"].get(key) for key in ("lock", "command", "pid")},
                                 {"lock": lock, "command": "run --kind test", "pid": os.getpid()})
                self.assertEqual(waited["bound_seconds"], verification.policy()["wait_bound_seconds"])
                self.assertEqual(waited["next"], "call wait again now, as a tool call of its own")
        timer = threading.Timer(0.5, lambda: (gate / "release").write_text("release", encoding="utf-8"))
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        waited = verification.wait_for_release(self.root, "qa_engineer", 60)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((waited["released"], "holder" in waited, "next" in waited), (True, False, False))
        # The command records its evidence before it releases its lock, so QA reads it at once.
        recorded = verification.read_session(self.root)["raw_evidence"]["test"]
        thread.join(60)
        self.assertNotIn("error", outcome)
        self.assertEqual(recorded["evidence_hash"], outcome["run"]["evidence_hash"])
        self.assertTrue(verification.wait_for_release(self.root, None, 5)["released"])
        verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic"))

    def test_wait_never_blocks_past_the_policy_bound(self):
        """One wait call blocks at most the policy's wait_bound_seconds, which a call may shorten but never
        lengthen, and it only reads lock state, so it answers before any freeze."""
        bound = verification.policy()["wait_bound_seconds"]
        waited = verification.wait_for_release(self.root)
        self.assertEqual({key: waited[key] for key in ("role", "released", "bound_seconds")},
                         {"role": None, "released": True, "bound_seconds": bound})
        self.assertLess(waited["waited_seconds"], 1)
        self.assertIsNone(verification.read_session(self.root, required=False))
        for role, seconds in ((None, 0), (None, -1), (None, bound + 0.5), (None, float("nan")),
                              (None, float("inf")), ("delivery_coordinator", 1)):
            with self.subTest(role=role, seconds=seconds), self.assertRaises(RuntimeError):
                verification.wait_for_release(self.root, role, seconds)
        for arguments, code in ((["wait", "--seconds", "1"], 0), (["wait", "--role", "qa_engineer"], 0),
                                (["wait", "--seconds", str(bound + 1)], 2)):
            output = io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(output):
                self.assertEqual(verification.main(["--worktree", str(self.root), *arguments]), code)
            reply = json.loads(output.getvalue())
            if code:
                self.assertEqual(reply, {"ok": False, "errors": [
                    f"one wait blocks for more than 0 and at most {bound} seconds, so the next model call finds"
                    " the prompt cache warm; call wait again to wait longer"]})
            else:
                self.assertEqual((reply["ok"], reply["released"], reply["bound_seconds"]), (True, True, bound))

    def test_every_refusal_that_leaves_a_role_waiting_names_wait(self):
        """A role that a held command makes wait learns from the refusal itself that wait is how it waits."""
        self.freeze()
        with verification.command_lock(self.root, "qa_engineer", "run --kind test"):
            with self.assertRaisesRegex(RuntimeError, "^another verification command is still running; `wait`"
                                                      " returns once it has exited$"):
                with verification.command_lock(self.root, "qa_engineer", "run --kind mutation"):
                    pass
            with self.assertRaisesRegex(RuntimeError, "^wait for the verification command to exit before resuming"
                                                      " QA; `wait` returns once it has$"):
                verification.resume_qa(self.root)

    def test_instructions_bound_every_wait_by_the_policy(self):
        """The flow and both host contracts state the policy's wait bound, and the Bash timeout Claude's contract
        gives a wait call outlasts it, so the runner, never the host, ends each wait."""
        bound = verification.policy()["wait_bound_seconds"]

        def text(relative):
            return " ".join((ROOT / relative).read_text(encoding="utf-8").split())

        flow = text("plugins/software-engineering-team/flows/delivery-execution.md")
        for phrase in (f"`wait_bound_seconds`, {bound} seconds, in any one tool call",
                       "never through a sleep, a polling loop or a long timeout of its own",
                       "`wait --role <role>` returns once that reader's own verification command has exited",
                       "The code reviewer runs no command, so it registers its finished result at once and returns"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flow)
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                self.assertIn(f"longer than the Delivery runner's {bound}-second `wait` bound",
                              text(f"platforms/{host}/software-engineering-team/host-contract.md"))
        timeout = re.search(r"Bash `timeout` of (\d+)", text("platforms/claude/software-engineering-team/host-contract.md"))
        self.assertGreater(int(timeout.group(1)), bound * 1000)

    def test_original_write_then_restore_invalidates_source_observations(self):
        frozen = self.freeze()
        path = self.root / "src/product.py"
        old = path.read_bytes()
        metadata = path.stat()
        path.write_bytes(b"temporary mutant")
        path.write_bytes(old)
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, metadata.st_mtime_ns)
        with self.assertRaisesRegex(RuntimeError, "bindings changed"):
            verification.require_current(self.root, frozen)

    def test_windows_source_observation_uses_native_change_time(self):
        import ctypes
        from types import SimpleNamespace
        path = self.root / "src/product.py"
        changed = [111]
        def native_query(handle, kind, output, size):
            self.assertEqual(kind, 0)
            output._obj.change = changed[0]
            return True
        kernel = SimpleNamespace(GetFileInformationByHandleEx=mock.Mock(side_effect=native_query))
        with mock.patch.object(verification.os, "name", "nt"), \
                mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True), \
                mock.patch.dict(sys.modules, {"msvcrt": SimpleNamespace(get_osfhandle=lambda descriptor: descriptor)}):
            first = verification.source_file_generation(path)
            changed[0] = 222
            second = verification.source_file_generation(path)
            self.assertEqual(first[:-1], second[:-1])
            self.assertEqual((first[-1], second[-1]), (111, 222))
            changed[0] = 0
            with self.assertRaisesRegex(RuntimeError, "trustworthy source change timestamp"):
                verification.source_file_generation(path)

    def test_runtime_commands_are_approved_scoped_and_retain_fresh_lifecycle_evidence(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": ["api"]})
        self.commit()
        self.freeze()
        with self.assertRaisesRegex(RuntimeError, "down before up"):
            verification.run_environment(self.root, "up")
        with self.assertRaisesRegex(RuntimeError, "exact approved"):
            verification.run_environment(self.root, "seed", "baseline; touch unsafe")
        events = []
        for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("url", "api"), ("logs", None), ("down", None)):
            event = verification.run_environment(self.root, verb, value)
            self.assertEqual(event["exit_code"], 0)
            events.append(event["evidence_hash"])
        result = self.result("qa_engineer", "qa_final")
        result["checks"]["fresh_runtime"]["event_hashes"] = events
        verification.register_result(self.root, result)
        verification.register_result(self.root, self.result())
        verification.validate(self.root, "DLV-001", "AUTH-01")


    def test_runtime_mutation_invalidates_evidence_but_teardown_remains_available(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        verification.run_environment(self.root, "down")
        original = subprocess.run
        def mutate_on_up(command, *args, **kwargs):
            if isinstance(command, str) and command.endswith(" up"):
                (Path(kwargs["cwd"]) / "src/product.py").write_text("changed runtime candidate")
                return subprocess.CompletedProcess(command, 0, b"started")
            return original(command, *args, **kwargs)
        with mock.patch.object(verification.subprocess, "run", side_effect=mutate_on_up):
            event = verification.run_environment(self.root, "up")
        self.assertFalse(event["candidate_intact"])
        with self.assertRaisesRegex(RuntimeError, "checkout changed"):
            verification.run_environment(self.root, "logs")
        self.assertEqual(verification.run_environment(self.root, "down")["exit_code"], 0)
        self.assertFalse(verification.read_session(self.root)["runtime"]["active"])

    @contextlib.contextmanager
    def git_for_windows_checkouts(self, *, honors_longpaths=True):
        """Check private clones out as Git for Windows does by default.

        Without core.longpaths, or with *honors_longpaths* false whatever the
        config says, it creates no file whose absolute path reaches 260
        characters, and the checkout still exits 0. Yields the root of each
        clone checked out.
        """
        original = verification.git
        roots = []

        def git(root, *args):
            output = original(root, *args)
            if args[:1] == ("checkout",):
                roots.append(Path(root))
                configured = subprocess.run(["git", "-C", str(root), "config", "--bool", "core.longpaths"],
                                            capture_output=True, text=True, check=False).stdout.strip()
                if configured != "true" or not honors_longpaths:
                    for relative in original(root, "ls-files", "-z").split("\0"):
                        if relative and len(str(Path(root) / relative)) >= 260:
                            (Path(root) / relative).unlink(missing_ok=True)
            return output

        with mock.patch.object(verification, "git", side_effect=git):
            yield roots

    def freeze_runtime_candidate_with_a_deep_path(self) -> str:
        """Freeze a runtime candidate that tracks a path deep enough to reach 260 characters in
        either private clone, and return that path."""
        deep = "src/" + "/".join(["nested-package-level"] * 7) + "/module.py"
        self.write(deep, "value = 3\n")
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        return deep

    def test_private_checkouts_hold_a_tracked_path_past_the_windows_path_limit(self):
        """The private clones sit deep in the verification scratch, so a tracked path that fits
        the project reaches 260 characters there. Git for Windows must still check it out, or
        neither checkout counts as intact and the runtime one refuses up."""
        deep = self.freeze_runtime_candidate_with_a_deep_path()
        with self.git_for_windows_checkouts() as clones:
            with self.subTest(checkout="runtime"):
                events = [verification.run_environment(self.root, verb) for verb in ("down", "up")]
                self.assertEqual([event["candidate_intact"] for event in events], [True, True])
            with self.subTest(checkout="command"):
                self.assertTrue(verification.run_check(self.root, "test")["candidate_intact"])
        self.assertEqual(len(clones), 2)
        for clone in clones:
            self.assertGreaterEqual(len(str(clone / deep)), 260)

    def test_a_private_checkout_that_leaves_a_tracked_path_out_names_it(self):
        """A checkout that leaves a tracked file out differs from its commit before any command
        ran. The runtime refusal and each record that is not intact name the missing path, so a
        short checkout no longer reads as a changed one (#358); what each checkout allows stays."""
        deep = self.freeze_runtime_candidate_with_a_deep_path()
        with self.git_for_windows_checkouts(honors_longpaths=False) as clones:
            down = verification.run_environment(self.root, "down")
            with self.assertRaises(RuntimeError) as refusal:
                verification.run_environment(self.root, "up")
            raw = verification.run_check(self.root, "test")
        self.assertEqual(len(clones), 2)
        # Git lists src/ first; how many workspace notes also reach the limit depends on the host's scratch.
        message = str(refusal.exception)
        self.assertTrue(message.startswith(f"runtime checkout changed (missing {deep}"), message)
        self.assertTrue(message.endswith("); only teardown is allowed before a new verification session"), message)
        for record in (down, raw):
            self.assertEqual((record["exit_code"], record["candidate_intact"]), (0, False))
            self.assertTrue(record["checkout_difference"].startswith("missing " + deep), record["checkout_difference"])

    def test_a_checkout_difference_names_five_paths_then_counts_the_rest(self):
        for index in range(6):
            self.write(f"src/part{index}.py", "value = 0\n")
        self.commit()
        self.assertEqual(verification.checkout_difference(self.root), "")
        for index in range(6):
            (self.root / f"src/part{index}.py").unlink()
        self.write("src/product.py", "value = 9\n")
        self.assertEqual(verification.checkout_difference(self.root),
                         ", ".join(f"missing src/part{index}.py" for index in range(5)) + " and 2 more")
        for index in range(1, 6):
            self.write(f"src/part{index}.py", "value = 0\n")
        self.assertEqual(verification.checkout_difference(self.root),
                         "missing src/part0.py, changed src/product.py")

    def test_failed_or_interrupted_runtime_start_requires_cleanup_before_cancellation(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        original = subprocess.run
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                verification.run_environment(self.root, "down")
                def unsuccessful_up(command, *args, **kwargs):
                    if isinstance(command, str) and command.endswith(" up"):
                        if interrupted:
                            raise OSError("command execution interrupted")
                        return subprocess.CompletedProcess(command, 1, b"partially started")
                    return original(command, *args, **kwargs)
                with mock.patch.object(verification.subprocess, "run", side_effect=unsuccessful_up):
                    if interrupted:
                        with self.assertRaisesRegex(OSError, "interrupted"):
                            verification.run_environment(self.root, "up")
                    else:
                        self.assertEqual(verification.run_environment(self.root, "up")["exit_code"], 1)
                cancelled = self.result("qa_engineer", "qa_final", "cancelled")
                cancelled["cancellation_confirmed"] = True
                with self.assertRaisesRegex(RuntimeError, "tear the environment down"):
                    verification.register_result(self.root, cancelled)
                verification.run_environment(self.root, "down")
                self.assertFalse(verification.runtime_needs_cleanup(verification.read_session(self.root)))
                if interrupted:
                    history = verification.read_session(self.root)["runtime"]["interrupted_commands"]
                    self.assertEqual(history[-1]["verb"], "up")
        verification.register_result(self.root, cancelled)

    # Parallel lanes share one Item environment, so its lock serializes their commands (#327).
    HOLDING_ENVIRONMENT = """import pathlib, sys, time
here = pathlib.Path(__file__).resolve().parent
if sys.argv[1] == "up":
    (here / "holding").write_text("up")
    deadline = time.monotonic() + 60
    while not (here / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
(here / ("done-" + sys.argv[1])).write_text("done")
print(sys.argv[1])
"""

    def lane_fixture(self):
        """Give the Item two lanes and an approved environment whose `up` holds until released."""
        markers = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, markers)
        self.markers = Path(markers.name).resolve()
        script = self.markers / "environment.py"
        script.write_text(self.HOLDING_ENVIRONMENT, encoding="utf-8")
        arguments = [sys.executable, str(script)]
        command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract",
                    implementation_schedule="parallel_lanes_v1",
                    role_sequence=["backend_developer", "devops_engineer", "code_reviewer", "qa_engineer"],
                    lane_scopes=["backend_developer:src", "devops_engineer:deploy"], lane_seams=[])
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md",
                  {"status": "approved", "env_command": command, "env_workdir": ".",
                   "scenarios": ["baseline"], "service_catalog": ["api"]})
        self.commit()

    def start_lane_holder(self) -> subprocess.Popen:
        """Run devops_engineer's `environment --verb up` in another process until the test releases it."""
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "plugins/software-engineering-team/scripts/delivery_verification.py"),
             "--worktree", str(self.root), "lane-run", "--delivery", "DLV-001", "--story", "AUTH-01",
             "--role", "devops_engineer", "--kind", "environment", "--verb", "up"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def finish():
            (self.markers / "release").write_text("go", encoding="utf-8")
            try:
                process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            # A killed holder's environment command outlives it; let it leave the worktree.
            deadline = time.monotonic() + 10
            while not (self.markers / "done-up").exists() and time.monotonic() < deadline:
                time.sleep(0.02)

        self.addCleanup(finish)
        deadline = time.monotonic() + 60
        while not (self.markers / "holding").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("the lane holder never took the Item environment")
            time.sleep(0.02)
        return process

    def lane(self, role: str, kind: str, verb: str | None = None, value: str | None = None) -> dict:
        return verification.lane_run(self.root, "DLV-001", "AUTH-01", role, kind, verb, value)

    def assert_environment_free(self) -> None:
        self.assertIsNone(verification.environment_holder(self.root))
        self.assertFalse(verification.environment_lock_paths(self.root)[1].exists())

    def test_parallel_lanes_run_environment_verbs_and_verification_commands_one_at_a_time(self):
        """While one lane runs an environment verb, every other environment verb and verification
        command of the Item is refused with its holder named (rv-accept-ideas-24)."""
        self.lane_fixture()
        holder = self.start_lane_holder()
        busy = (r"^DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by devops_engineer, running"
                rf" `environment --verb up` in process {holder.pid} since \S+; run this after it finishes$")
        with self.assertRaisesRegex(RuntimeError, busy):
            self.lane("backend_developer", "test")
        with self.assertRaisesRegex(RuntimeError, busy):
            self.lane("backend_developer", "environment", "seed", "baseline")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = verification.main(["--worktree", str(self.root), "lane-run", "--delivery", "DLV-001",
                                      "--story", "AUTH-01", "--role", "backend_developer", "--kind", "test"])
        self.assertEqual(code, 2)
        self.assertRegex(json.loads(output.getvalue())["errors"][0], busy)
        self.assertEqual(verification.environment_holder(self.root)["holder"], "devops_engineer")
        (self.markers / "release").write_text("go", encoding="utf-8")
        finished, _ = holder.communicate(timeout=60)
        self.assertEqual(holder.returncode, 0, finished)
        self.assertEqual(json.loads(finished)["exit_code"], 0)
        # The holder released the lock as it finished, so the next lane runs.
        self.assert_environment_free()
        result = self.lane("backend_developer", "test")
        self.assertEqual((result["exit_code"], result["interrupted_holder"]), (0, None))
        self.assertEqual(Path(result["output_file"]).read_text(encoding="utf-8").strip(), "123")
        # After the freeze the reader's verification commands and environment verbs take the same lock.
        self.freeze()
        with verification.environment_lock(self.root, "devops_engineer", "environment --verb down"):
            held = (r"^DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by devops_engineer,"
                    rf" running `environment --verb down` in process {os.getpid()} since ")
            with self.assertRaisesRegex(RuntimeError, held):
                verification.run_check(self.root, "test")
            with self.assertRaisesRegex(RuntimeError, held):
                verification.run_environment(self.root, "down")
        self.assertNotIn("test", verification.read_session(self.root)["raw_evidence"])
        self.assertNotIn("runtime", verification.read_session(self.root))

    def test_the_environment_lock_is_released_on_every_exit_path(self):
        self.lane_fixture()
        original = subprocess.run

        def command_ends(outcome):
            def run(command, *args, **kwargs):
                if not kwargs.get("shell"):
                    return original(command, *args, **kwargs)
                if isinstance(outcome, BaseException):
                    raise outcome
                return subprocess.CompletedProcess(command, outcome, b"failed\n")
            return mock.patch.object(verification.subprocess, "run", side_effect=run)

        self.assertEqual(self.lane("backend_developer", "test")["exit_code"], 0)
        self.assert_environment_free()
        with command_ends(3):
            self.assertEqual(self.lane("devops_engineer", "environment", "down")["exit_code"], 3)
        self.assert_environment_free()
        for failure in (OSError("command could not start"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with command_ends(failure), self.assertRaises(type(failure)):
                    self.lane("devops_engineer", "environment", "up")
                self.assert_environment_free()
        with mock.patch.object(verification.atomic_file, "replace_text", side_effect=OSError("disk full")), \
                self.assertRaisesRegex(OSError, "disk full"):
            self.lane("backend_developer", "test")
        self.assert_environment_free()
        for refused in (("backend_developer", "environment", "seed", "unknown"),
                        ("qa_engineer", "test"), ("backend_developer", "test", "up")):
            with self.subTest(refused=refused), self.assertRaises(RuntimeError):
                self.lane(*refused)
            self.assert_environment_free()
        self.freeze()
        self.assertEqual(verification.run_check(self.root, "test")["exit_code"], 0)
        self.assert_environment_free()
        with self.assertRaisesRegex(RuntimeError, "down before up"):
            verification.run_environment(self.root, "up")
        self.assert_environment_free()
        self.assertEqual(verification.run_environment(self.root, "down")["exit_code"], 0)
        self.assert_environment_free()
        with self.assertRaisesRegex(RuntimeError, "READERS_ACTIVE"):
            self.lane("backend_developer", "test")
        self.assert_environment_free()

    def test_only_the_operating_system_frees_a_dead_holders_lock(self):
        """A holder that dies loses the lock with its process; its owner record never holds it."""
        self.lane_fixture()
        holder = self.start_lane_holder()
        with self.assertRaisesRegex(RuntimeError, "^DELIVERY_ENVIRONMENT_BUSY: "):
            self.lane("backend_developer", "test")
        holder.kill()
        holder.communicate()
        owner = verification.environment_lock_paths(self.root)[1]
        self.assertEqual(json.loads(owner.read_text(encoding="utf-8"))["pid"], holder.pid)
        self.assertIsNone(verification.environment_holder(self.root))
        (self.markers / "release").write_text("go", encoding="utf-8")
        result = self.lane("backend_developer", "test")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual({key: result["interrupted_holder"][key] for key in ("holder", "command", "pid")},
                         {"holder": "devops_engineer", "command": "environment --verb up", "pid": holder.pid})
        self.assert_environment_free()
        # A record naming a live process holds nothing without its process's lock.
        record = {"holder": "devops_engineer", "command": "environment --verb up", "pid": os.getpid(),
                  "started_at": "2026-01-01T00:00:00Z"}
        owner.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(verification.environment_holder(self.root))
        self.assertEqual(self.lane("backend_developer", "test")["interrupted_holder"], record)
        self.assert_environment_free()

    def test_lane_commands_drop_interpreter_search_paths_outside_the_item_worktree(self):
        """A lane command never inherits a search path that names another checkout, so a fixture run
        cannot import that checkout's code; entries inside the Item worktree stay (#327)."""
        self.lane_fixture()
        names = ("PYTHONPATH", "PYTHONHOME", "NODE_PATH")
        script = self.markers / "search_paths.py"
        script.write_text(f"import json, os\nprint(json.dumps({{name: os.environ.get(name) for name in {names!r}}}))\n",
                          encoding="utf-8")
        arguments = [sys.executable, str(script)]
        contract_path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(contract_path)
        contract["test_command"] = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        self.write(contract_path.relative_to(self.root).as_posix(), delivery.frontmatter(contract, body))
        self.commit()
        outside = self.markers / "other-checkout"
        outside.mkdir()
        inside = [str(self.root / "src"), "src"]
        escaping = [str(outside / "scripts"), os.path.join("..", "escape")]
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
            escaping.append("linked")
        except OSError:
            pass  # A host without symlinks still checks every other entry.
        inherited = {"PYTHONPATH": os.pathsep.join([*escaping[:1], *inside, *escaping[1:]]),
                     "PYTHONHOME": str(outside), "NODE_PATH": str(outside / "node_modules")}
        with mock.patch.dict(os.environ, inherited):
            result = self.lane("backend_developer", "test")
        output = Path(result["output_file"]).read_text(encoding="utf-8")
        self.assertEqual(result["exit_code"], 0, output)
        self.assertEqual(json.loads(output), {"PYTHONPATH": os.pathsep.join(inside), "PYTHONHOME": None,
                                              "NODE_PATH": None})
        self.assertEqual(result["dropped_search_paths"], {
            "PYTHONPATH": escaping, "PYTHONHOME": [str(outside)], "NODE_PATH": [str(outside / "node_modules")]})
        reference = " ".join((ROOT / "plugins/software-engineering-team/skill-content/deliver/references"
                              / "switch-implementation_schedule-parallel_lanes_v1.md")
                             .read_text(encoding="utf-8").split())
        self.assertEqual(verification.LANE_SEARCH_PATH_VARIABLES, names)
        self.assertIn("The interpreter search paths `PYTHONPATH`, `PYTHONHOME` and `NODE_PATH` are unset or"
                      " point only inside the Item worktree.", reference)
        self.assertIn("keeps every entry inside the worktree", reference)

    def test_lane_commands_never_receive_an_inherited_runner_selection(self):
        """Only the run that writes a selection file names it, so a lane command never receives
        AGENTROF_DIAGNOSTIC_TESTS or AGENTROF_REUSED_TESTS from the coordinator's shell, which would let a
        reuse-aware test command skip suites while the lane reports green."""
        self.lane_fixture()
        names = verification.SELECTION_VARIABLES
        script = self.markers / "selections.py"
        script.write_text(f"import json, os\nprint(json.dumps({{name: os.environ.get(name) for name in {names!r}}}))\n",
                          encoding="utf-8")
        arguments = [sys.executable, str(script)]
        contract_path = self.root / "workspace/docs/operation/verification-contract.md"
        contract, body = delivery.split_note(contract_path)
        contract["test_command"] = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        self.write(contract_path.relative_to(self.root).as_posix(), delivery.frontmatter(contract, body))
        self.commit()
        # The partition variables of switch test_engines partitioned are per-run inputs too (#386).
        self.assertEqual(names, ("AGENTROF_DIAGNOSTIC_TESTS", "AGENTROF_REUSED_TESTS",
                                 "AGENTROF_TEST_PARTITION", "AGENTROF_TEST_ENGINE"))
        with mock.patch.dict(os.environ, {"AGENTROF_DIAGNOSTIC_TESTS": "inherited-selection.json",
                                          "AGENTROF_REUSED_TESTS": "inherited-reuse.json",
                                          "AGENTROF_TEST_PARTITION": "inherited-partition.json",
                                          "AGENTROF_TEST_ENGINE": "inherited-engine"}):
            result = self.lane("backend_developer", "test")
        output = Path(result["output_file"]).read_text(encoding="utf-8")
        self.assertEqual(result["exit_code"], 0, output)
        self.assertEqual(json.loads(output), {name: None for name in names})
        reference = " ".join((ROOT / "plugins/software-engineering-team/skill-content/deliver/references"
                              / "switch-implementation_schedule-parallel_lanes_v1.md")
                             .read_text(encoding="utf-8").split())
        self.assertIn("`lane-run` also unsets the runner's selection variables `AGENTROF_DIAGNOSTIC_TESTS` and"
                      " `AGENTROF_REUSED_TESTS`", reference)

    def test_runtime_evidence_shares_one_recorded_identity_and_stays_fresh(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        with mock.patch.dict(os.environ, self.SHELL):
            for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("logs", None), ("down", None)):
                verification.run_environment(self.root, verb, value)
        session = verification.read_session(self.root)
        evidence = {"event_hashes": [event["evidence_hash"] for event in session["runtime"]["events"]]}
        verification.require_runtime_evidence(self.root, session, evidence)
        # Another shell checks the events, with another declared value too: it never compares them with itself.
        with mock.patch.dict(os.environ, {**self.OTHER_SHELL, "RUNTIME_CONFIGURATION": "changed",
                                          "PATH": os.environ.get("PATH", "") + os.pathsep + "/opt/checker/bin"}):
            verification.require_runtime_evidence(self.root, session, evidence)

        def forged(change) -> tuple[dict, dict]:
            value = copy.deepcopy(session)
            event = value["runtime"]["events"][2]
            change(event["environment_identity"])
            event.pop("evidence_hash")
            event["evidence_hash"] = verification.digest(event)
            return value, {"event_hashes": [entry["evidence_hash"] for entry in value["runtime"]["events"]]}

        for message, change in (
                ("runtime command evidence ran in more than one environment",
                 lambda identity: identity.update(environment_hash="hmac-sha256:" + "0" * 64)),
                ("runtime command evidence carries the whole-environment hash of an earlier runner, which binds the"
                 " shell that ran it; freeze the candidate again with freeze --fresh and rerun both readers",
                 lambda identity: identity.pop("environment_variables")),
                ("runtime command evidence does not cover PATH",
                 lambda identity: identity["environment_variables"].remove("PATH"))):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, re.escape(message)):
                verification.require_runtime_evidence(self.root, *forged(change))
        latest = max(event["completed_at"] for event in session["runtime"]["events"])
        with mock.patch.object(verification.time, "time", return_value=latest + 86401):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                verification.require_runtime_evidence(self.root, session, evidence)
        # QA's final evidence runs in one declared environment: a test run in another one is refused.
        verification.register_result(self.root, self.result())
        with mock.patch.dict(os.environ, {**self.SHELL, "PATH": os.environ.get("PATH", "") + os.pathsep + "/opt/qa"}):
            final = self.result("qa_engineer", "qa_final")
        final["checks"]["fresh_runtime"]["event_hashes"] = evidence["event_hashes"]
        with self.assertRaisesRegex(RuntimeError, "runtime command evidence ran in another environment than"
                                                  " full_test_suite evidence"):
            verification.register_result(self.root, final)
        with mock.patch.dict(os.environ, self.OTHER_SHELL):
            final = self.result("qa_engineer", "qa_final")
        final["checks"]["fresh_runtime"]["event_hashes"] = evidence["event_hashes"]
        verification.register_result(self.root, final)
        with mock.patch.dict(os.environ, {**self.OTHER_SHELL,
                                          "PATH": os.environ.get("PATH", "") + os.pathsep + "/opt/approver/bin"}):
            verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_failed_runtime_retries_qa_without_repeating_same_candidate_review(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        frozen = self.freeze()
        verification.register_result(self.root, self.result())
        reviewer = verification.read_session(self.root)["workers"]["code_reviewer"]
        raw_test = verification.run_check(self.root, "test")
        verification.run_environment(self.root, "down")
        original = subprocess.run
        old_checkout = []
        def unsuccessful_up(command, *args, **kwargs):
            if isinstance(command, str) and command.endswith(" up"):
                old_checkout.append(Path(kwargs["cwd"]))
                (old_checkout[0] / "src/product.py").write_text("partial runtime preparation")
                return subprocess.CompletedProcess(command, 1, b"partially started")
            return original(command, *args, **kwargs)
        with mock.patch.object(verification.subprocess, "run", side_effect=unsuccessful_up):
            self.assertEqual(verification.run_environment(self.root, "up")["exit_code"], 1)
        failed = self.result("qa_engineer", "qa_final", "failed")
        failed["findings"] = [{"id": "QA-RUNTIME", "severity": "major", "status": "open", "verification": "Rerun the fresh runtime protocol"}]
        with self.assertRaisesRegex(RuntimeError, "tear the environment down"):
            verification.register_result(self.root, failed)
        verification.run_environment(self.root, "down")
        verification.register_result(self.root, failed)
        old_runtime = verification.read_session(self.root)["runtime"]
        old_outputs = {event["output_file"]: verification.raw_output_path(self.root, event["output_file"]).read_bytes()
                       for event in old_runtime["events"]}
        resumed = verification.resume_qa(self.root)
        self.assertEqual(resumed["session_id"], frozen["session_id"])
        self.assertEqual(resumed["candidate"], frozen["candidate"])
        self.assertEqual(resumed["workers"]["code_reviewer"], reviewer)
        self.assertEqual(resumed["runtime_attempts"], [old_runtime])
        self.assertEqual(resumed["qa_attempts"][0]["result"]["verdict"], "failed")
        self.assertNotIn("runtime", resumed)
        with self.assertRaisesRegex(RuntimeError, "fresh runtime evidence"):
            verification.require_runtime_evidence(self.root, resumed, {"event_hashes": [event["evidence_hash"] for event in old_runtime["events"]]})
        with self.assertRaisesRegex(RuntimeError, "down before up"):
            verification.run_environment(self.root, "up")
        events = []
        for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("logs", None), ("down", None)):
            events.append(verification.run_environment(self.root, verb, value))
        self.assertNotEqual(events[0]["attempt_id"], old_runtime["attempt_id"])
        self.assertTrue(all(event["candidate_intact"] for event in events))
        self.assertEqual((old_checkout[0] / "src/product.py").read_text(), "partial runtime preparation")
        self.assertFalse(set(old_outputs) & {event["output_file"] for event in events})
        for name, content in old_outputs.items():
            self.assertEqual(verification.raw_output_path(self.root, name).read_bytes(), content)
        final = self.result("qa_engineer", "qa_final")
        self.assertEqual(final["checks"]["full_test_suite"]["raw_evidence_hash"], raw_test["evidence_hash"])
        final["findings"] = [{**failed["findings"][0], "status": "resolved", "verification": "Fresh runtime protocol passed"}]
        final["checks"]["fresh_runtime"]["event_hashes"] = [event["evidence_hash"] for event in old_runtime["events"]]
        with self.assertRaisesRegex(RuntimeError, "exact event hashes"):
            verification.register_result(self.root, final)
        final["checks"]["fresh_runtime"]["event_hashes"] = [event["evidence_hash"] for event in events]
        verification.register_result(self.root, final)
        verified = verification.validate(self.root, "DLV-001", "AUTH-01")
        self.assertEqual(verified["workers"]["code_reviewer"], reviewer)
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-001", "story": "AUTH-01"})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.approve_item_evidence(args), 0)
        with self.assertRaisesRegex(RuntimeError, "failed final QA"):
            verification.resume_qa(self.root)

    def test_failed_final_qa_cannot_resume_a_changed_candidate(self):
        self.freeze()
        verification.register_result(self.root, self.result("qa_engineer", "qa_final", "failed"))
        self.write("src/product.py", "value = 3\n")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "bindings changed"):
            verification.resume_qa(self.root)


    @unittest.skipIf(os.name == "nt", "native Windows rejects carriage returns in file names")
    def test_candidate_git_paths_preserve_carriage_returns_newlines_and_leading_spaces(self):
        names = ["src/Icon\r.txt", "src/new\nline.py", " leading.py"]
        for name in names:
            self.write(name, "value = 1\n")
        self.commit()
        listed = verification.git(self.root, "ls-files", "-z").split("\0")
        self.assertTrue(set(names).issubset(listed))
        frozen = self.freeze()
        self.assertTrue(set(names).issubset(frozen["candidate"]["changed_files"]))
        self.assertTrue(set(names).issubset(frozen["candidate"]["source_observations"]))
        self.assertEqual(verification.require_current(self.root, frozen), frozen["candidate"])


    def review_loop(self, value="blocking_delta"):
        import process_policy
        docs = self.root / "workspace/docs"
        def policy(*argv):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]), 0)
        policy("init")
        policy("set", "--switch", "review_loop", "--value", value)
        policy("approve")
        self.commit()

    def approve_evidence(self):
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-001", "story": "AUTH-01"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery.approve_item_evidence(args)
        self.assertEqual(code, 0, output.getvalue())
        return delivery.section_bodies(delivery.split_note(self.root / self.directory / "items/auth-01/code-review.md")[1])

    def test_current_keeps_code_review_minors_as_notes(self):
        self.freeze()
        result = self.result()
        result["findings"] = [{"id": "CR-2", "severity": "minor", "status": "open", "verification": "Rename it"}]
        verification.register_result(self.root, result)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        self.assertEqual(self.approve_evidence()["Deviations and Follow-ups"], delivery.SECTION_PLACEHOLDER)

    def test_blocking_delta_code_review_minors_are_follow_ups_on_the_record(self):
        self.review_loop()
        self.freeze()
        item = (self.root / self.item_path).read_bytes()
        result = self.result()
        minor = {"id": "CR-2", "severity": "MINOR", "status": "open", "verification": "Rename it"}
        result["findings"] = [dict(minor)]
        with self.assertRaisesRegex(RuntimeError, "code review follow-ups are incomplete: CR-2 needs its file; "
                                                  "CR-2 needs its description; CR-2 owner_role must be one of: "
                                                  "backend_developer; CR-2 needs a concrete revisit_trigger"):
            verification.register_result(self.root, result)
        minor.update(file="src/product.py:1", description="The name value hides | its unit.",
                     owner_role="frontend_developer", revisit_trigger="Revisit at the next change to src/product.py.")
        result["findings"] = [dict(minor)]
        with self.assertRaisesRegex(RuntimeError, "CR-2 owner_role must be one of: backend_developer$"):
            verification.register_result(self.root, result)
        minor["owner_role"] = "backend_developer"
        # A resolved minor is no follow-up, so it needs no follow-up fields.
        result["findings"] = [dict(minor), {"id": "CR-1", "severity": "minor", "status": "resolved",
                                            "verification": "Renamed"}]
        verification.register_result(self.root, result)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        self.assertEqual(self.approve_evidence()["Deviations and Follow-ups"], "\n".join([
            delivery.ITEM_FOLLOW_UPS, "",
            "| finding | severity | file | description | owner_role | revisit_trigger |",
            "|---|---|---|---|---|---|",
            "| CR-2 | MINOR | src/product.py:1 | The name value hides \\| its unit. | backend_developer "
            "| Revisit at the next change to src/product.py. |"]))
        # Evidence approval writes only the two reports; the Item keeps its approved bytes.
        self.assertEqual((self.root / self.item_path).read_bytes(), item)

    def test_blocking_delta_records_that_no_follow_up_is_open(self):
        self.review_loop()
        self.freeze()
        self.settle()
        self.assertEqual(self.approve_evidence()["Deviations and Follow-ups"], delivery.ITEM_FOLLOW_UPS + " none.")

    def claim(self, identifier="CR-1", severity="major", **extra):
        return {"id": identifier, "severity": severity, "status": "open", "verification": "Rerun the regression",
                "file": "src/product.py:1", "description": "The value can overflow its column.", **extra}

    def ruling(self, finding="CR-1", claimed="major", ruling="minor",
               reason="src/product.py:1 assigns a constant, so no input reaches the column.", **extra):
        row = {"finding": finding, "claimed_severity": claimed, "calibrated_severity": ruling, "reason": reason}
        if ruling == "minor":
            row.update(owner_role="backend_developer", revisit_trigger="Revisit at the next change to src/product.py.")
        return {**row, **extra}

    def calibration(self, claims, rows):
        """The calibration reader's own result: its rulings on the claims as returned."""
        session = verification.read_session(self.root)
        return {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                "role": "code_reviewer", "mode": "calibration", "report": "Independent calibration of the claims",
                "claims": claims, "calibration": rows}

    def test_blocking_delta_calibrates_every_open_blocking_claim_before_it_gates(self):
        self.review_loop()
        self.freeze()
        failed = self.result(verdict="failed")
        failed["findings"] = [self.claim(), self.claim("CR-2", "critical")]
        invalid = self.ruling("CR-2", "critical", "invalid",
                              reason="src/product.py:1 is the only write, and it holds no secret or input.")
        missing = "exactly one row for each open critical or major claim no earlier calibration ruled: CR-1, CR-2"
        refusals = (
            (missing, [self.ruling()]),
            (missing, [self.ruling(), invalid, self.ruling()]),
            ("CR-2 calibration must record the claimed severity critical",
             [self.ruling(), {**invalid, "claimed_severity": "major"}]),
            ("CR-1 calibrated_severity must confirm major or be minor or invalid",
             [self.ruling(ruling="critical"), invalid]),
            ("CR-1 calibration reason must cite the candidate text as path:line",
             [self.ruling(reason="The value is a constant, so no input reaches the column."), invalid]),
            ("CR-2 calibration reason must cite the candidate text as path:line",
             [self.ruling(), {**invalid, "reason": "src/missing.py:1 is the only write of the value."}]),
            # The frozen candidate's src/product.py holds one line.
            ("CR-1 calibration reason must cite the candidate text as path:line",
             [self.ruling(reason="src/product.py:12 assigns a constant, so no input reaches the column."),
              invalid]),
            ("CR-1 calibrated minor needs an owner_role of backend_developer and a concrete revisit_trigger",
             [self.ruling(owner_role="qa_engineer"), invalid]),
        )
        for message, rows in refusals:
            with self.subTest(message=message, rows=len(rows)):
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    verification.register_calibration(self.root, self.calibration(failed["findings"], rows))
        # The claiming result registers only after the calibration reader's own result.
        with self.assertRaisesRegex(RuntimeError, "register the calibration reader's result with calibrate"
                                                  " for exactly the open critical or major claims no earlier"
                                                  " calibration ruled, as returned: CR-1, CR-2"):
            verification.register_result(self.root, failed)
        verification.register_calibration(self.root, self.calibration(failed["findings"], [self.ruling(), invalid]))
        # A calibrated pass still proves that every review pass ran.
        with self.assertRaisesRegex(RuntimeError, "a calibrated code_reviewer pass requires correctness evidence"):
            verification.register_result(self.root, {**failed, "checks": {}})
        # Every claim lowered or disproved: the review passes without a repair cycle.
        verification.register_result(self.root, failed)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        verification.validate(self.root, "DLV-001", "AUTH-01")
        section = self.approve_evidence()["Deviations and Follow-ups"]
        self.assertEqual(section.split("\n\n" + delivery.ITEM_CALIBRATION, 1), ["\n".join([
            delivery.ITEM_FOLLOW_UPS, "",
            "| finding | severity | file | description | owner_role | revisit_trigger |",
            "|---|---|---|---|---|---|",
            "| CR-1 | minor | src/product.py:1 | The value can overflow its column. | backend_developer "
            "| Revisit at the next change to src/product.py. |"]), "\n".join([
            "", "",
            "| finding | claimed_severity | calibrated_severity | reason |",
            "|---|---|---|---|",
            "| CR-1 | major | minor | src/product.py:1 assigns a constant, so no input reaches the column. |",
            "| CR-2 | critical | invalid | src/product.py:1 is the only write, and it holds no secret or input. |"])])

    def test_the_claiming_reviewer_never_calibrates_its_own_claims(self):
        # The reviewer that claimed two majors lowers both in its own result.
        self.review_loop()
        self.freeze()
        failed = self.result(verdict="failed")
        failed["findings"] = [self.claim(), self.claim("CR-2")]
        failed["calibration"] = [self.ruling(), self.ruling("CR-2")]
        with self.assertRaisesRegex(RuntimeError, "calibration rows come only from the calibration reader's own"
                                                  " result, registered with calibrate; the claiming result"
                                                  " carries none"):
            verification.register_result(self.root, failed)
        del failed["calibration"]
        # A calibration of other claims, or of edited claims, never stands in.
        verification.register_calibration(self.root, self.calibration(
            [self.claim(), self.claim("CR-2", description="The value is unused.")],
            [self.ruling(), self.ruling("CR-2")]))
        with self.assertRaisesRegex(RuntimeError, "for exactly the open critical or major claims no earlier"
                                                  " calibration ruled, as returned: CR-1, CR-2"):
            verification.register_result(self.root, failed)
        with self.assertRaisesRegex(RuntimeError, "already calibrated; each claim is ruled once"):
            verification.register_calibration(self.root, self.calibration(
                failed["findings"], [self.ruling(), self.ruling("CR-2")]))
        stored = verification.read_session(self.root)["calibration"]["result"]
        self.assertEqual(stored["result_hash"], verification.digest(
            {key: item for key, item in stored.items() if key != "result_hash"}))
        # A calibration comes before the claiming result settles, never after it.
        self.freeze_fresh()
        verification.register_result(self.root, self.result())
        with self.assertRaisesRegex(RuntimeError, "calibrate the claims before the claiming code review result"
                                                  " is registered"):
            verification.register_calibration(self.root, self.calibration(
                failed["findings"], [self.ruling(), self.ruling("CR-2")]))

    def freeze_fresh(self):
        session = verification.read_session(self.root)
        for role, worker in session["workers"].items():
            if worker["state"] == "running":
                cancelled = self.result(role, "review_initial" if role == "code_reviewer" else "qa_final", "cancelled")
                verification.register_result(self.root, {**cancelled, "cancellation_confirmed": True})
        return verification.freeze(self.root, "DLV-001", "AUTH-01", fresh=True)

    def test_calibration_runs_only_on_an_open_blocking_claim(self):
        self.review_loop()
        self.freeze()
        result = self.result()
        result["findings"] = [self.claim(severity="minor", owner_role="backend_developer",
                                         revisit_trigger="Revisit at the next change to src/product.py.")]
        with self.assertRaisesRegex(RuntimeError, "calibration claims must list each open critical or major"
                                                  " claim once, as returned"):
            verification.register_calibration(self.root, self.calibration(result["findings"], [self.ruling()]))
        verification.register_calibration(self.root, self.calibration([self.claim()], [self.ruling()]))
        with self.assertRaisesRegex(RuntimeError, "no earlier calibration ruled, as returned: none"):
            verification.register_result(self.root, result)

    def test_a_confirmed_claim_gates_and_each_ruling_carries_to_the_next_cycle(self):
        self.review_loop()
        self.freeze()
        failed = self.result(verdict="failed")
        failed["findings"] = [self.claim(), self.claim("CR-2"), self.claim("CR-3")]
        verification.register_calibration(self.root, self.calibration(failed["findings"], [
            self.ruling(ruling="major", reason="src/product.py:1 writes the value from user input unchecked."),
            self.ruling("CR-2"),
            self.ruling("CR-3", ruling="invalid", reason="src/product.py:1 never reads the value it is said to read.")]))
        verification.register_result(self.root, failed)
        verification.register_result(self.root, self.result("qa_engineer", "qa_diagnostic", "failed"))
        with self.assertRaisesRegex(RuntimeError, "same-candidate final passed code_reviewer"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        self.write("src/product.py", "value = 3\n")
        self.commit()
        self.freeze()
        unresolved = {finding["id"]: finding for finding in verification.manifest(
            self.root, "DLV-001", "AUTH-01", "code_reviewer", "review_repair")["unresolved_findings"]}
        self.assertEqual(sorted(unresolved), ["CR-1", "CR-2"])
        self.assertEqual((unresolved["CR-1"]["severity"], unresolved["CR-1"]["calibrated_severity"]),
                         ("major", "major"))
        self.assertEqual((unresolved["CR-2"]["severity"], unresolved["CR-2"]["claimed_severity"],
                          unresolved["CR-2"]["owner_role"]), ("minor", "major", "backend_developer"))
        follow_up = {key: unresolved["CR-2"][key] for key in (
            "id", "severity", "status", "verification", "file", "description", "owner_role", "revisit_trigger")}
        repeat = self.result(mode="review_repair", verdict="failed")
        repeat["findings"] = [self.claim(), follow_up]
        with self.assertRaisesRegex(RuntimeError, "an earlier calibration already ruled CR-1"):
            verification.register_calibration(self.root, self.calibration([self.claim()], [
                self.ruling(reason="src/product.py:1 assigns a constant, so no input reaches it.")]))
        with self.assertRaisesRegex(RuntimeError, "inherited finding severity must be preserved"):
            verification.register_result(self.root, {**repeat, "findings": [self.claim(), {**follow_up, "severity": "major"}]})
        repair = self.result(mode="review_repair")
        repair["findings"] = [self.claim(status="resolved"), follow_up]
        verification.register_result(self.root, repair)

    def test_current_ignores_calibration_rows(self):
        self.freeze()
        failed = self.result(verdict="failed")
        failed["findings"] = [self.claim()]
        with self.assertRaisesRegex(RuntimeError, "severity calibration runs only at review_loop blocking_delta"):
            verification.register_calibration(self.root, self.calibration([self.claim()], [self.ruling()]))
        self.assertNotIn("calibration", verification.read_session(self.root))
        failed["calibration"] = [self.ruling()]
        verification.register_result(self.root, failed)
        verification.register_result(self.root, self.result("qa_engineer", "qa_final"))
        with self.assertRaisesRegex(RuntimeError, "same-candidate final passed code_reviewer"):
            verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_durable_diagnostic_or_mismatched_report_cannot_integrate(self):
        item = {"verification_schedule": "parallel_snapshot_v1"}
        review = {"verification_candidate_hash": "sha256:candidate", "verification_result_hash": "sha256:review", "verification_mode": "review_initial"}
        qa = {"verification_candidate_hash": "sha256:candidate", "verification_result_hash": "sha256:qa", "verification_mode": "qa_diagnostic"}
        with self.assertRaisesRegex(RuntimeError, "final qa_engineer"):
            verification.validate_evidence(item, review, qa)
        qa["verification_mode"] = "qa_final"
        verification.validate_evidence(item, review, qa)
        qa["verification_candidate_hash"] = "sha256:different"
        with self.assertRaisesRegex(RuntimeError, "same verification candidate"):
            verification.validate_evidence(item, review, qa)


if __name__ == "__main__":
    unittest.main()
