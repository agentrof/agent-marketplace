"""Same-candidate independent verification and write barrier contracts."""
from __future__ import annotations

import contextlib
import copy
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
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import delivery_compile as delivery
import delivery_verification as verification
from git_fixture import init_repository, remove_temporary


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

    def test_raw_evidence_rejects_changed_environment_and_missing_output_at_seal(self):
        self.freeze()
        self.settle()
        with mock.patch.dict(os.environ, {"VERIFICATION_TEST_ENV": "changed"}):
            with self.assertRaisesRegex(RuntimeError, "environment changed"):
                verification.validate(self.root, "DLV-001", "AUTH-01")
        raw = verification.read_session(self.root)["raw_evidence"]["test"]
        (verification.session_path(self.root).parent / raw["output_file"]).unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or changed"):
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
        with verification.command_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "command to exit"):
                verification.register_result(self.root, self.result())

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

    def test_runtime_evidence_requires_unchanged_environment_and_fresh_events(self):
        item, body = delivery.split_note(self.root / self.item_path)
        item.update(runtime_required=True, environment_contract_ref="operation/environment-contract")
        self.write(self.item_path, delivery.frontmatter(item, body))
        self.note("workspace/docs/operation/environment-contract.md", {"status": "approved", "env_command": self.command, "env_workdir": ".", "scenarios": ["baseline"], "service_catalog": []})
        self.commit()
        self.freeze()
        for verb, value in (("down", None), ("up", None), ("seed", "baseline"), ("logs", None), ("down", None)):
            verification.run_environment(self.root, verb, value)
        session = verification.read_session(self.root)
        evidence = {"event_hashes": [event["evidence_hash"] for event in session["runtime"]["events"]]}
        verification.require_runtime_evidence(self.root, session, evidence)
        with mock.patch.dict(os.environ, {"RUNTIME_CONFIGURATION": "changed"}):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                verification.require_runtime_evidence(self.root, session, evidence)
        latest = max(event["completed_at"] for event in session["runtime"]["events"])
        with mock.patch.object(verification.time, "time", return_value=latest + 86401):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                verification.require_runtime_evidence(self.root, session, evidence)

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
