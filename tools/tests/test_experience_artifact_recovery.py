"""Explicit artifact recovery must retain history and the normal approval gate."""

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools.tests import test_experience_compile as fixtures
from tools.tests.git_fixture import init_repository, remove_temporary

compiler = fixtures.experience_compile
application = fixtures.experience_application_check


class ArtifactRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.project = Path(self.temporary.name).resolve()
        self.helpers = fixtures.ExperienceCompilerTests()
        self.fixture = self.helpers.orphaned_create_scope(
            self.temporary.name, publish_open_packages=True,
        )
        code, output, errors = self.helpers.rehydrate_published_scope(self.fixture)
        self.assertEqual(code, 0, output + errors)
        self.root = compiler.root_for(self.fixture["root"])
        self.receipts = [
            {"stage": stage, "result_ref": reference, "package_hash": digest}
            for stage, reference, digest in compiler.input_rows(self.fixture["old_plan"])
        ]
        self.upstream = self.helpers.recovery_contract(self.receipts)
        self.upstream.__enter__()
        self.addCleanup(self.upstream.__exit__, None, None, None)
        self.original = json.loads((self.root / application.REGISTRY_RELATIVE).read_text())
        rows = [*self.original["artifact_files"], application.artifact_path_row(Path(".DS_Store"), application.sha(b"lost metadata"), len(b"lost metadata"))]
        rows.sort(key=application.artifact_row_path)
        self.history = []
        previous = application.GENESIS_APPLICATION_HASH
        for revision in range(1, 6):
            registry = {**self.original, "application_revision": revision, "artifact_files": rows, "artifact_tree_hash": application.artifact_tree_hash(rows), "previous_application_hash": previous}
            registry.pop("application_hash")
            registry["application_hash"] = application.sha(application.canonical(registry))
            self.history.append(registry)
            previous = registry["application_hash"]
        (self.root / application.REGISTRY_RELATIVE).write_bytes(application.canonical(self.history[-1]))
        (self.root / application.LEDGER_RELATIVE).write_bytes(application.canonical({"schema_version": 3, "revisions": self.history}))
        (self.project / ".gitignore").write_text(".agentrof/\n.DS_Store\n")
        init_repository(self.project)
        self.git("config", "user.name", "Jane Doe")
        self.git("config", "user.email", "jane@example.invalid")
        self.commit()

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-m", "Capture artifact recovery fixture")

    def proposal(self, *, reason="Restore the clone using the recorded complete artifact delta.", recover=True):
        return self.helpers.run_in_process(
            "propose", "--root", self.root, "--origin-mode", "manual",
            "--application-action", "update", "--reason", reason,
            *(["--recover-artifacts"] if recover else []),
        )

    def prepare(self):
        before = self.helpers.tree_snapshot(self.fixture["docs"])
        code, output, errors = self.proposal()
        self.assertEqual(code, 0, output + errors)
        self.assertEqual(before, self.helpers.tree_snapshot(self.fixture["docs"]))
        self.plan = json.loads(output)
        self.plan_path = self.project / ".agentrof/artifact-recovery.json"
        self.plan_path.parent.mkdir(exist_ok=True)
        self.plan_path.write_bytes(compiler.canonical(self.plan))
        return self.plan

    def command(self, verb, *extra):
        return self.helpers.run_in_process(
            verb, "--root", self.root, "--scope-plan", self.plan_path,
            "--proposal-hash", self.plan["proposal_hash"], *extra,
        )

    def successful(self, verb, *extra):
        code, output, errors = self.command(verb, *extra)
        self.assertEqual(code, 0, output + errors)
        return output

    def test_metadata_recovery_is_explicit_review_gated_and_preserves_history(self):
        code, _output, errors = self.proposal(recover=False)
        self.assertEqual(code, 2)
        self.assertIn("not proposal-ready", errors)
        plan = self.prepare()
        self.assertEqual(plan["schema_version"], 4)
        delta = plan["artifact_recovery"]["delta"]
        self.assertEqual([row["path"] for row in delta["policy_excluded"]], [".DS_Store"])
        self.assertEqual([delta[key] for key in ("added", "changed", "removed")], [[], [], []])
        self.successful("begin-application-revision")
        self.assertEqual(compiler.read_open_application_state(self.root)["opened_revision"], 6)
        code, _output, errors = self.command("approve-set")
        self.assertEqual(code, 2)
        self.assertIn("in_review", errors)
        self.successful("enter-application-review")
        code, _output, errors = self.command("approve-set")
        self.assertEqual(code, 2)
        self.assertIn("attestation", errors)
        registry, findings = application.compile_application(self.root)
        self.assertEqual(findings, [])
        attestation = self.project / ".agentrof/review.json"
        attestation.write_bytes(compiler.canonical({
            "schema_version": 4, "reviewer_role": "experience-reviewer", "advisories": [],
            "proposal_hash": plan["proposal_hash"],
            "artifact_tree_hash": registry["artifact_tree_hash"],
            "application_package_set_hash": registry["package_set_hash"],
            "application_hash": registry["application_hash"],
            "application_revision": 6,
            "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        }))
        self.successful("approve-set", "--review-attestation", attestation)
        history, findings = application.verified_application_ledger(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(history[:5], self.history)
        self.assertEqual(len(history), 6)
        self.assertEqual(history[-1]["packages"], self.original["packages"])
        self.assertEqual(application.compile_application(self.root, True)[1], [])

    def test_meaningful_changes_are_complete_and_require_committed_sources(self):
        artifact = self.root / "artifacts/prototype.bin"
        artifact.write_bytes(b"revised evidence")
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("byte-exact in HEAD", errors)
        self.commit()
        plan = self.prepare()
        delta = plan["artifact_recovery"]["delta"]
        self.assertEqual(delta["changed"][0]["before"]["path"], "prototype.bin")
        self.assertEqual(delta["changed"][0]["after"]["sha256"], application.sha(b"revised evidence"))
        artifact.unlink()
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("deletions committed", errors)
        added = self.root / "artifacts/new.bin"
        added.write_bytes(b"replacement")
        self.commit()
        code, output, errors = self.proposal()
        self.assertEqual(code, 0, output + errors)
        delta = json.loads(output)["artifact_recovery"]["delta"]
        self.assertEqual([row["path"] for row in delta["added"]], ["new.bin"])
        self.assertEqual([row["path"] for row in delta["removed"]], ["prototype.bin"])

    def test_begin_review_and_approval_reject_artifact_toctou(self):
        for phase in ("before_begin", "draft", "in_review"):
            with self.subTest(phase=phase):
                self.prepare()
                if phase != "before_begin":
                    self.successful("begin-application-revision")
                if phase == "in_review":
                    self.successful("enter-application-review")
                artifact = self.root / "artifacts/prototype.bin"
                original = artifact.read_bytes()
                artifact.write_bytes(original + b"changed after proposal")
                before = self.helpers.tree_snapshot(self.root)
                verb = {"before_begin": "begin-application-revision", "draft": "enter-application-review", "in_review": "approve-set"}[phase]
                code, _output, errors = self.command(verb)
                self.assertEqual(code, 2)
                self.assertIn("byte-exact in HEAD", errors)
                self.assertEqual(before, self.helpers.tree_snapshot(self.root))
                artifact.write_bytes(original)
                compiler.open_application_state_path(self.root).unlink(missing_ok=True)
                (self.root / application.REGISTRY_RELATIVE).write_bytes(application.canonical(self.history[-1]))

    def test_proof_reason_hash_schema_and_exact_delta_are_not_forgeable(self):
        code, _out, errors = self.proposal(reason="   ")
        self.assertEqual(code, 2)
        self.assertIn("nonempty --reason", errors)
        plan = self.prepare()
        cases = (
            lambda p: p["artifact_recovery"].update(reason="Different approved reason"),
            lambda p: p["artifact_recovery"].update(unrecognized=True),
            lambda p: p["artifact_recovery"]["delta"].update(policy_excluded=[]),
            lambda p: p["artifact_recovery"]["observed_artifact_files"].append(p["artifact_recovery"]["observed_artifact_files"][0]),
            lambda p: p["expected_application"].update(revision=True),
        )
        for change in cases:
            mutated = copy.deepcopy(plan)
            change(mutated)
            self.plan_path.write_bytes(compiler.canonical(mutated))
            code, _out, _errors = self.command("begin-application-revision")
            self.assertEqual(code, 2)
            self.assertFalse(compiler.open_application_state_path(self.root).exists())
        forged = copy.deepcopy(plan)
        forged["artifact_recovery"]["ledger_sha256"] = application.sha(b"false history")
        forged["proposal_hash"] = compiler.proposal_digest(forged)
        with self.assertRaisesRegex(ValueError, "proof changed"):
            compiler.validate_artifact_recovery(self.root, forged)

    def test_immutable_ledger_and_registry_must_remain_head_exact(self):
        self.prepare()
        for path in (self.root / application.LEDGER_RELATIVE, self.root / application.REGISTRY_RELATIVE):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                code, _out, errors = self.command("begin-application-revision")
                self.assertEqual(code, 2)
                self.assertIn("committed in HEAD", errors)
                path.write_bytes(original)
        history = copy.deepcopy(self.history)
        history[0]["application_hash"] = application.sha(b"tampered")
        (self.root / application.LEDGER_RELATIVE).write_bytes(application.canonical({"schema_version": 3, "revisions": history}))
        code, _out, errors = self.command("begin-application-revision")
        self.assertEqual(code, 2)
        self.assertIn("immutable application history", errors)

    def test_open_process_or_application_and_stale_upstream_are_denied(self):
        self.prepare()
        self.successful("begin-application-revision")
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("no open application", errors)
        compiler.open_application_state_path(self.root).unlink()
        package = self.root / "experiences/checkout"
        data, body = compiler.fm(package / "experience.md")
        data["status"] = "draft"
        compiler.rewrite(package / "experience.md", data, body)
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("no open process", errors)
        data["status"] = "approved"
        compiler.rewrite(package / "experience.md", data, body)
        with mock.patch.object(compiler.stage_package, "verify", return_value=({}, ["upstream is stale"])):
            code, _out, errors = self.command("begin-application-revision")
            self.assertEqual(code, 2)
            self.assertIn("upstream is stale", errors)

    def test_interrupted_transaction_and_tampered_open_state_are_denied(self):
        self.prepare()
        transaction_id = compiler.begin_transaction(self.root, "render")
        journal_before = compiler.transaction_journal(self.root).read_bytes()
        for invoke in (self.proposal, lambda: self.command("begin-application-revision")):
            code, _out, errors = invoke()
            self.assertEqual(code, 2)
            self.assertIn("conflicting transaction", errors)
            self.assertEqual(compiler.transaction_journal(self.root).read_bytes(), journal_before)
        compiler.rollback_transaction(self.root, transaction_id)
        self.successful("begin-application-revision")
        state_path = compiler.open_application_state_path(self.root)
        state = compiler.read_open_application_state(self.root)
        state["proposal_hash"] = application.sha(b"another proposal")
        state_path.write_bytes(compiler.canonical(state))
        code, _out, errors = self.command("enter-application-review")
        self.assertEqual(code, 2)
        self.assertIn("not bound", errors)

    def test_ignored_untracked_meaningful_sources_are_denied(self):
        (self.project / ".gitignore").write_text(".agentrof/\n.DS_Store\n*.ignored\n")
        self.commit()
        (self.root / "artifacts/new.ignored").write_bytes(b"important evidence")
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("tracked and byte-exact", errors)

    def test_begin_retry_rechecks_the_exact_draft_proof(self):
        self.prepare()
        self.successful("begin-application-revision")
        (self.root / "artifacts/prototype.bin").write_bytes(b"drift after draft opened")
        for committed in (False, True):
            with self.subTest(committed=committed):
                if committed:
                    self.commit()
                before = self.helpers.tree_snapshot(self.root)
                code, _out, errors = self.command("begin-application-revision")
                self.assertEqual(code, 2)
                self.assertIn("proof changed" if committed else "byte-exact in HEAD", errors)
                self.assertEqual(before, self.helpers.tree_snapshot(self.root))

    def test_legacy_receipt_without_an_inventory_cannot_seed_artifact_recovery(self):
        legacy = {
            "schema_version": 2, "application_revision": 1,
            "source_hash": application.sha(b"old source"),
            "package_set_hash": self.original["package_set_hash"],
            "coverage_hash": application.sha(b"old coverage"),
            "design_system": {"package_hash": application.sha(b"old design"), "revision": 1, "master_source_hash": application.sha(b"old master")},
            "runtime_sha256": application.sha(b"old runtime"),
            "packages": self.original["packages"], "coverage": {},
            "previous_application_hash": application.GENESIS_APPLICATION_HASH,
        }
        legacy["application_hash"] = application.sha(application.canonical(legacy))
        (self.root / application.REGISTRY_RELATIVE).write_bytes(application.canonical(legacy))
        (self.root / application.LEDGER_RELATIVE).write_bytes(application.canonical({"schema_version": 2, "revisions": [legacy]}))
        self.commit()
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("predecessor with an exact artifact inventory", errors)

    def test_new_committed_artifact_after_proposal_is_denied(self):
        self.prepare()
        (self.root / "artifacts/additional.bin").write_bytes(b"new evidence")
        self.commit()
        code, _out, errors = self.command("begin-application-revision")
        self.assertEqual(code, 2)
        self.assertIn("proof changed", errors)

    def test_noop_recovery_is_denied_and_ordinary_flow_remains_available(self):
        (self.root / application.REGISTRY_RELATIVE).write_bytes(application.canonical(self.original))
        (self.root / application.LEDGER_RELATIVE).write_bytes(application.canonical({"schema_version": 3, "revisions": [self.original]}))
        self.commit()
        code, _out, errors = self.proposal()
        self.assertEqual(code, 2)
        self.assertIn("explicit artifact inventory delta", errors)
        code, output, errors = self.proposal(recover=False)
        self.assertEqual(code, 0, output + errors)
        self.assertEqual(json.loads(output)["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
