"""An identical binding preserves only complete, freshly verified receipts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unittest
from unittest import mock

from tools.tests import test_requirement_compile as requirement_tests
from tools.tests import test_operation_governance as operation_tests
from tools.tests.git_fixture import init_repository, remove_temporary

requirement = requirement_tests.requirement_compile
stage_package = requirement.stage_package


class RequirementBindingRetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = requirement_tests.RequirementCompilerTests()
        self.fixture.setUp()
        self.addCleanup(remove_temporary, self.fixture.temporary)
        self.root, self.docs = self.fixture.root, self.fixture.docs
        self.path = self.fixture.complete_draft()
        requirement.approve_requirement(self.path)
        self.ba = self.docs / "business-analysis/foundation/space.md"
        self.ba.parent.mkdir(parents=True)
        self.ba.write_text(
            "---\ntype: space\nstatus: approved\n---\n\n# Foundation\n\nApproved compatibility fixture.\n",
            encoding="utf-8",
        )
        operation_tests.OperationGovernanceTests().approved_solution(self.docs)
        import design_system_compile
        self.design = self.docs / "design-system/MASTER.md"
        self.design.parent.mkdir(parents=True)
        props = {"type": "design-master", "status": "approved"}
        body = "# Design Master\n\nApproved compatibility fixture.\n"
        self.design.write_text(requirement.render_note(props, body), encoding="utf-8")
        props["baseline_hash"] = design_system_compile.baseline_hash(self.design.parent)
        self.design.write_text(requirement.render_note(props, body), encoding="utf-8")
        init_repository(self.root)
        self.commit()
        self.refs = {
            "business-analysis": "business-analysis/foundation/space",
            "solution-design": "solution-design/landscape",
            "design-system": "design-system/MASTER",
        }
        for stage, reference in self.refs.items():
            receipt, errors = stage_package.verify(self.docs, stage, reference, require_committed=True)
            self.assertEqual(errors, [])
            self.assertEqual(receipt["verification_profile"], "legacy-readonly")
            requirement.bind_stage(self.path, stage, reference)
        self.commit()

    def commit(self):
        for args in (["add", "-A"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                      "commit", "-q", "-m", "Approved fixture"]):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def state(self):
        return requirement.stage_results(requirement.split_note(self.path)[1])

    def preserved_preimage(self):
        os.utime(self.path, ns=(1600000000123456700, 1600000000123456700))
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    def assert_preserved(self, preimage):
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), preimage)

    def test_identical_legacy_binding_keeps_bytes_mtime_and_downstream_receipts(self):
        before = self.state()
        preimage = self.preserved_preimage()
        with mock.patch.object(stage_package, "verify", wraps=stage_package.verify) as verify:
            requirement.bind_stage(self.path, "business-analysis", "[[" + self.refs["business-analysis"] + "|Foundation]]")
        self.assert_preserved(preimage)
        self.assertEqual(self.state(), before)
        self.assertEqual(verify.call_count, 3)
        for stage in ("solution-design", "design-system"):
            verify.assert_any_call(self.path.parents[1], stage, *before[stage][0], require_committed=True)

    def test_identical_modern_binding_still_runs_the_real_package_gate(self):
        from tools.tests import test_ba_compile as ba_tests
        space = self.docs / "business-analysis/erp"
        ba_tests.make_valid_space(space)
        code, out, err = ba_tests.run(["approve-package", "--space", str(space), "--vault-root", str(self.docs)])
        self.assertEqual(code, 0, out + err)
        self.commit()
        reference = "business-analysis/erp/space"
        receipt, errors = stage_package.verify(self.docs, "business-analysis", reference, require_committed=True)
        self.assertEqual(errors, [])
        self.assertEqual(receipt["verification_profile"], "strict-current")
        requirement.bind_stage(self.path, "business-analysis", reference)
        for stage in ("solution-design", "design-system"):
            requirement.bind_stage(self.path, stage, self.refs[stage])
        before = self.state()
        preimage = self.preserved_preimage()
        with mock.patch.object(ba_tests.ba, "classify_package", wraps=ba_tests.ba.classify_package) as classify:
            requirement.bind_stage(self.path, "business-analysis", reference)
        self.assertTrue(classify.called)
        self.assert_preserved(preimage)
        self.assertEqual(self.state(), before)

    def test_changed_approved_predecessor_still_invalidates_downstream(self):
        before = self.state()
        self.ba.write_text(self.ba.read_text(encoding="utf-8") + "\nA new approved constraint.\n", encoding="utf-8")
        self.commit()
        requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
        self.assertEqual(set(self.state()), {"business-analysis"})
        self.assertNotEqual(self.state()["business-analysis"], before["business-analysis"])

    def test_uncommitted_requested_package_is_not_a_retry(self):
        self.ba.write_text(self.ba.read_text(encoding="utf-8") + "\nUncommitted constraint.\n", encoding="utf-8")
        preimage = self.preserved_preimage()
        with self.assertRaisesRegex(ValueError, "uncommitted package changes"):
            requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
        self.assert_preserved(preimage)

    def test_incorrect_expected_hash_is_still_refused(self):
        preimage = self.preserved_preimage()
        with self.assertRaisesRegex(ValueError, "hash is stale"):
            requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"], "sha256:" + "0" * 64)
        self.assert_preserved(preimage)

    def test_stale_retained_package_cannot_be_silently_preserved(self):
        landscape = self.docs / "solution-design/landscape.md"
        landscape.write_text(landscape.read_text(encoding="utf-8") + "\nUnapproved scope change.\n", encoding="utf-8")
        self.commit()
        preimage = self.preserved_preimage()
        with self.assertRaisesRegex(ValueError, "not an approved/current|hash is stale"):
            requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
        self.assert_preserved(preimage)

    def test_corrupt_receipt_preimages_are_refused_without_repairing_them(self):
        original = self.path.read_bytes()
        for corruption in ("missing_results_hash", "stale_results_hash", "missing_current_digest",
                           "missing_downstream_digest", "invalid_digest", "duplicate_receipt", "unknown_stage"):
            with self.subTest(corruption=corruption):
                self.path.write_bytes(original)
                props, body = requirement.split_note(self.path)
                stage = "business-analysis" if corruption == "missing_current_digest" else "solution-design"
                reference, digest = self.state()[stage][0]
                if corruption.startswith("missing_") and corruption.endswith("digest"):
                    body = body.replace(f"| {stage} | {reference} | {digest} |", f"| {stage} | {reference} |  |")
                elif corruption == "invalid_digest":
                    body = body.replace(f"| {stage} | {reference} | {digest} |", f"| {stage} | {reference} | invalid |")
                elif corruption == "duplicate_receipt":
                    row = f"| {stage} | {reference} | {digest} |"
                    body = body.replace(row, row + "\n" + row)
                elif corruption == "unknown_stage":
                    body = body.replace("|---|---|---|", "|---|---|---|\n| unknown-stage | input | sha256:" + "1" * 64 + " |")
                props["stage_results_hash"] = "sha256:" + hashlib.sha256(json.dumps(
                    requirement.stage_results(body), sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest()
                if corruption == "missing_results_hash":
                    props.pop("stage_results_hash")
                elif corruption == "stale_results_hash":
                    props["stage_results_hash"] = "sha256:" + "0" * 64
                self.path.write_text(requirement.render_note(props, body), encoding="utf-8")
                preimage = self.preserved_preimage()
                with self.assertRaisesRegex(ValueError, "Stage Results|stage_results_hash"):
                    requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
                self.assert_preserved(preimage)

    def test_unapproved_requirement_edit_cannot_use_the_noop_path(self):
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("Allow enterprise users", "Allow external users"), encoding="utf-8")
        preimage = self.preserved_preimage()
        with self.assertRaisesRegex(ValueError, "approved source_hash is stale"):
            requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
        self.assert_preserved(preimage)

    def test_experience_retry_uses_exact_membership_and_ignores_argument_order(self):
        # Package publication is covered by Experience's own suite. Here the
        # real membership validator consumes controlled current ledger rows.
        hashes = {"application@r1": "sha256:" + "a" * 64, "checkout@r1": "sha256:" + "b" * 64,
                  "profile@r1": "sha256:" + "c" * 64}
        actual_verify = stage_package.verify

        def verify(docs, stage, reference, expected_hash="", **options):
            if stage != "experience-design":
                return actual_verify(docs, stage, reference, expected_hash, **options)
            if expected_hash and expected_hash != hashes[reference]:
                return None, ["Experience package hash is stale"]
            return {"result_ref": reference, "package_hash": hashes[reference],
                    "verification_profile": "strict-current", "current": True, "committed": True}, []

        with mock.patch.object(stage_package, "experience_application_process_refs", return_value=["checkout@r1", "profile@r1"]) as members, \
                mock.patch.object(stage_package, "verify", side_effect=verify):
            requirement.bind_stage(self.path, "experience-design", list(hashes))
            before = self.state()
            preimage = self.preserved_preimage()
            requirement.bind_stage(self.path, "experience-design", list(reversed(hashes)))
            self.assert_preserved(preimage)
            self.assertEqual(self.state(), before)
            with self.assertRaisesRegex(ValueError, "exact process receipt set"):
                requirement.bind_stage(self.path, "experience-design", ["application@r1", "checkout@r1"])
            self.assert_preserved(preimage)
            members.return_value = ["checkout@r1"]
            with self.assertRaisesRegex(ValueError, "retained experience-design"):
                requirement.bind_stage(self.path, "business-analysis", self.refs["business-analysis"])
            self.assert_preserved(preimage)

    def test_real_empty_experience_receipt_retries_but_a_new_revision_is_not_current(self):
        import experience_application_check
        root = self.docs / "experience-design"
        root.mkdir()

        def publish():
            ledger, errors = experience_application_check.verified_application_ledger(root)
            self.assertEqual(errors, [])
            state = root / "_generated/open-application-revision.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"opened_revision": len(ledger) + 1}), encoding="utf-8")
            registry, errors = experience_application_check.compile_application(root)
            self.assertEqual(errors, [])
            state.unlink()
            experience_application_check.write_registry_and_ledger(root, registry)
            self.commit()
            return f"application@r{registry['application_revision']}"

        reference = publish()
        receipt, errors = stage_package.verify(self.docs, "experience-design", reference, require_committed=True)
        self.assertEqual(errors, [])
        self.assertEqual(receipt["verification_profile"], "strict-current")
        requirement.bind_stage(self.path, "experience-design", reference)
        preimage = self.preserved_preimage()
        requirement.bind_stage(self.path, "experience-design", reference)
        self.assert_preserved(preimage)
        self.assertNotEqual(publish(), reference)
        preimage = self.preserved_preimage()
        with self.assertRaisesRegex(ValueError, "exact process receipt set|approved/current"):
            requirement.bind_stage(self.path, "experience-design", reference)
        self.assert_preserved(preimage)


if __name__ == "__main__":
    unittest.main()
