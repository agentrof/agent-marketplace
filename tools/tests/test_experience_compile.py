import json
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"
SETUP = ROOT / "plugins/software-engineering-team/scripts/setup_project.py"
VAULT_CHECK = ROOT / "plugins/software-engineering-team/scripts/vault_check.py"
sys.path.insert(0, str(COMPILER.parent))
import experience_compile
import experience_application_check


class ExperienceCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def run_in_process(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = experience_compile.main([*map(str, args)])
        return code, output.getvalue(), errors.getvalue()

    @staticmethod
    def recovery_contract(receipts):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            experience_compile, "selected_inputs",
            return_value=(receipts, [], {}),
        ))
        stack.enter_context(mock.patch.object(
            experience_compile.stage_package, "verify", return_value=({}, []),
        ))
        stack.enter_context(mock.patch.object(
            experience_compile.stage_package,
            "resolve_ba_process",
            side_effect=lambda _docs, process, **_kwargs: (
                str(process).removesuffix(".md"), []
            ),
        ))
        return stack

    @staticmethod
    def tree_snapshot(root: Path) -> dict[str, tuple]:
        snapshot = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            mode = path.stat().st_mode & 0o777
            snapshot[relative] = (
                ("directory", mode)
                if path.is_dir()
                else ("file", mode, path.read_bytes())
            )
        return snapshot

    def orphaned_create_scope(
        self, temporary: str, *, publish_open_packages: bool = False,
        publish_application: bool = True,
    ) -> dict:
        docs = Path(temporary) / "workspace/docs"
        root = docs / "experience-design"
        root.mkdir(parents=True)
        old_receipts = [
            {
                "stage": stage,
                "result_ref": reference,
                "package_hash": "sha256:" + character * 64,
            }
            for stage, reference, character in (
                ("business-analysis", "business-analysis/commerce/space", "1"),
                ("solution-design", "solution-design/landscape", "2"),
                ("design-system", "design-system/MASTER", "3"),
            )
        ]
        actions = [
            {
                "primary_process_ref": process,
                "experience": experience,
                "target_experience": "",
                "action": "create",
                "affected_records": [],
                "expected_package": {},
                "reason": "Create the process-owned Experience package.",
            }
            for experience, process in (
                (
                    "checkout",
                    "business-analysis/commerce/processes/checkout-process",
                ),
                (
                    "returns",
                    "business-analysis/commerce/processes/returns-process",
                ),
            )
        ]
        old_plan = {
            "schema_version": 2,
            "origin_mode": "manual",
            "input_bindings": experience_compile.binding_rows(old_receipts),
            "actions": actions,
            "application_action": "create",
            "expected_application": {"exists": False},
        }
        old_plan["proposal_hash"] = experience_compile.proposal_digest(old_plan)
        old_plan_path = docs / "old-experience-scope.json"
        old_plan_path.write_bytes(experience_compile.canonical(old_plan))

        record_bytes = {}
        artifact_bytes = {}
        for index, (action, phase) in enumerate(
            zip(actions, ("draft", "in_review")), start=1,
        ):
            experience = str(action["experience"])
            package = root / "experiences" / experience
            for directory, _prefix, _suffix in experience_compile.KIND.values():
                (package / directory).mkdir(parents=True, exist_ok=True)
            for directory in ("artifacts", "_generated", "_ledger"):
                (package / directory).mkdir(exist_ok=True)
            process = str(action["primary_process_ref"])
            package_data = {
                "type": "experience",
                "title": f"{experience.title()} Experience",
                "experience_id": experience,
                "origin_mode": "manual",
                "status": phase,
                "revision": 1,
                "primary_process_ref": process,
                "input_bindings": experience_compile.binding_rows(old_receipts),
                "tags": ["doc/experience", f"status/{phase.replace('_', '-')}"],
            }
            (package / "experience.md").write_text(
                experience_compile.render_fm(
                    package_data,
                    f"# {experience.title()} Experience\n\n"
                    "## Navigation <!-- sec: nav -->\n\n"
                    "[[maps/experience-design|Experience Design]]\n",
                ),
                encoding="utf-8",
            )
            record = package / "journeys" / f"{experience}-journey.md"
            record.write_text(
                experience_compile.render_fm(
                    {
                        "type": "journey",
                        "title": f"{experience.title()} Journey",
                        "id": f"JRN-{index:03d}",
                        "revision": 1,
                        "record_state": "active",
                        "derives_from": [process],
                    },
                    f"# {experience.title()} Journey\n\n"
                    "## Navigation <!-- sec: nav -->\n\n"
                    f"[[experience-design/experiences/{experience}/experience|"
                    f"{experience.title()} Experience]]\n",
                ),
                encoding="utf-8",
            )
            artifact = package / "artifacts" / f"{experience}.bin"
            artifact.write_bytes(f"{experience}-prototype\x00".encode())
            record_bytes[experience] = record.read_bytes()
            artifact_bytes[experience] = artifact.read_bytes()
            experience_compile.write_open_revision(
                package, old_plan, action, old_plan["proposal_hash"],
            )

        application_artifact = root / "artifacts" / "prototype.bin"
        application_artifact.parent.mkdir(parents=True)
        application_artifact.write_bytes(b"application-prototype\x00")
        ledger = root / experience_application_check.LEDGER_RELATIVE
        ledger_bytes = None
        if publish_application:
            experience_compile.write_open_application_state(
                root, old_plan, old_plan["proposal_hash"], phase="draft",
            )
            registry, findings = (
                experience_application_check.compile_application(
                    root,
                    package_paths=None if publish_open_packages else [],
                )
            )
            self.assertEqual(findings, [])
            experience_application_check.write_registry_and_ledger(
                root, registry,
            )
            experience_compile.open_application_state_path(root).unlink()
            ledger_bytes = ledger.read_bytes()
        self.assertFalse(experience_compile.open_application_state_path(root).exists())

        new_receipts = [
            {
                "stage": stage,
                "result_ref": reference,
                "package_hash": "sha256:" + character * 64,
            }
            for stage, reference, character in (
                ("business-analysis", "business-analysis/commerce/space", "a"),
                ("solution-design", "solution-design/landscape", "b"),
                ("design-system", "design-system/MASTER", "c"),
            )
        ]
        return {
            "docs": docs,
            "root": root,
            "old_plan": old_plan,
            "old_plan_path": old_plan_path,
            "new_receipts": new_receipts,
            "record_bytes": record_bytes,
            "artifact_bytes": artifact_bytes,
            "application_artifact_bytes": application_artifact.read_bytes(),
            "ledger_bytes": ledger_bytes,
        }

    def propose_recovery(self, fixture: dict) -> tuple[dict, Path]:
        plan = fixture["old_plan"]
        receipts = fixture["new_receipts"]
        with self.recovery_contract(receipts):
            code, output, errors = self.run_in_process(
                "propose", "--root", fixture["root"],
                "--recover-scope-plan", fixture["old_plan_path"],
                "--recover-proposal-hash", plan["proposal_hash"],
                "--origin-mode", "manual",
                "--ba-ref", receipts[0]["result_ref"],
                "--solution-ref", receipts[1]["result_ref"],
                "--design-ref", receipts[2]["result_ref"],
            )
        self.assertEqual(code, 0, output + errors)
        fresh = json.loads(output)
        path = fixture["docs"] / "fresh-experience-scope.json"
        path.write_bytes(experience_compile.canonical(fresh))
        return fresh, path

    def recover_scope(
        self, fixture: dict, fresh: dict, fresh_path: Path,
    ) -> tuple[int, str, str]:
        old = fixture["old_plan"]
        with self.recovery_contract(fixture["new_receipts"]):
            return self.run_in_process(
                "recover-open-scope", "--root", fixture["root"],
                "--from-scope-plan", fixture["old_plan_path"],
                "--from-proposal-hash", old["proposal_hash"],
                "--scope-plan", fresh_path,
                "--proposal-hash", fresh["proposal_hash"],
            )

    def rehydrate_published_scope(
        self, fixture: dict,
    ) -> tuple[int, str, str]:
        old = fixture["old_plan"]
        return self.run_in_process(
            "rehydrate-published-scope", "--root", fixture["root"],
            "--scope-plan", fixture["old_plan_path"],
            "--proposal-hash", old["proposal_hash"],
            "--application-ref", "application@r1",
        )

    @staticmethod
    def replace_open_scope_bindings(fixture: dict, bindings: list[str]) -> None:
        for experience in ("checkout", "returns"):
            package = fixture["root"] / "experiences" / experience
            data, body = experience_compile.fm(package / "experience.md")
            data["input_bindings"] = bindings
            experience_compile.rewrite(package / "experience.md", data, body)

    def test_recover_open_create_scope_preserves_authored_bytes_and_retries(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)

            self.assertEqual(fresh["actions"], fixture["old_plan"]["actions"])
            self.assertEqual(
                fresh["input_bindings"],
                experience_compile.binding_rows(fixture["new_receipts"]),
            )
            self.assertEqual(fresh["application_action"], "update")
            self.assertEqual(fresh["expected_application"]["revision"], 1)
            self.assertNotEqual(
                fresh["proposal_hash"], fixture["old_plan"]["proposal_hash"],
            )

            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )
            self.assertEqual(code, 0, output + errors)
            self.assertEqual(json.loads(output), {
                "previous_proposal_hash": fixture["old_plan"]["proposal_hash"],
                "proposal_hash": fresh["proposal_hash"],
                "packages": ["checkout", "returns"],
                "status": "draft",
                "changed": True,
            })

            expected_bindings = experience_compile.binding_rows(
                fixture["new_receipts"],
            )
            for experience in ("checkout", "returns"):
                package = fixture["root"] / "experiences" / experience
                data = experience_compile.fields(package)
                self.assertEqual(data["status"], "draft")
                self.assertEqual(data["revision"], 1)
                self.assertEqual(data["input_bindings"], expected_bindings)
                state = experience_compile.read_open_revision(package)
                self.assertEqual(state["proposal_hash"], fresh["proposal_hash"])
                self.assertEqual(state["opened_revision"], 1)
                self.assertEqual(
                    (package / "journeys" / f"{experience}-journey.md").read_bytes(),
                    fixture["record_bytes"][experience],
                )
                self.assertEqual(
                    (package / "artifacts" / f"{experience}.bin").read_bytes(),
                    fixture["artifact_bytes"][experience],
                )

            application_artifact = fixture["root"] / "artifacts/prototype.bin"
            application_ledger = (
                fixture["root"] / experience_application_check.LEDGER_RELATIVE
            )
            self.assertEqual(
                application_artifact.read_bytes(),
                fixture["application_artifact_bytes"],
            )
            self.assertEqual(
                application_ledger.read_bytes(), fixture["ledger_bytes"],
            )
            application_state = experience_compile.read_open_application_state(
                fixture["root"],
            )
            self.assertEqual(
                application_state,
                experience_compile.open_application_payload(
                    fresh, fresh["proposal_hash"], phase="draft",
                ),
            )
            self.assertEqual(application_state["opened_revision"], 2)

            recovered_tree = self.tree_snapshot(fixture["docs"])
            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )
            self.assertEqual(code, 0, output + errors)
            self.assertEqual(json.loads(output), {
                "previous_proposal_hash": fixture["old_plan"]["proposal_hash"],
                "proposal_hash": fresh["proposal_hash"],
                "packages": ["checkout", "returns"],
                "status": "draft",
                "changed": False,
            })
            self.assertEqual(
                self.tree_snapshot(fixture["docs"]), recovered_tree,
            )

            with self.recovery_contract(fixture["new_receipts"]):
                for experience in ("checkout", "returns"):
                    code, output, errors = self.run_in_process(
                        "enter-review", "--experience-root",
                        fixture["root"] / "experiences" / experience,
                    )
                    self.assertEqual(code, 0, output + errors)
                code, output, errors = self.run_in_process(
                    "enter-application-review", "--root", fixture["root"],
                    "--scope-plan", fresh_path,
                    "--proposal-hash", fresh["proposal_hash"],
                )
                self.assertEqual(code, 0, output + errors)
                reviewed, findings = (
                    experience_application_check.compile_application(
                        fixture["root"],
                    )
                )
                self.assertEqual(findings, [])
                attestation = fixture["docs"] / "review-attestation.json"
                attestation.write_text(json.dumps({
                    "schema_version": 4,
                    "proposal_hash": fresh["proposal_hash"],
                    "artifact_tree_hash": reviewed["artifact_tree_hash"],
                    "application_package_set_hash": reviewed[
                        "package_set_hash"
                    ],
                    "application_hash": reviewed["application_hash"],
                    "application_revision": reviewed[
                        "application_revision"
                    ],
                    "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "reviewer_role": "experience-reviewer",
                    "advisories": [],
                }) + "\n", encoding="utf-8")
                code, output, errors = self.run_in_process(
                    "approve-set", "--root", fixture["root"],
                    "--experience", "checkout",
                    "--experience", "returns",
                    "--scope-plan", fresh_path,
                    "--proposal-hash", fresh["proposal_hash"],
                    "--review-attestation", attestation,
                )
                self.assertEqual(code, 0, output + errors)

            receipts = json.loads(output)["receipts"]
            self.assertEqual(
                [receipt["result_ref"] for receipt in receipts],
                ["application@r2", "checkout@r1", "returns@r1"],
            )
            ledger_rows, findings = (
                experience_application_check.verified_application_ledger(
                    fixture["root"],
                )
            )
            self.assertEqual(findings, [])
            self.assertEqual(len(ledger_rows), 2)

    def test_recovery_rebinds_legacy_current_input_metadata_atomically(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            replacement_bindings = experience_compile.binding_rows(
                fixture["new_receipts"],
            )
            self.replace_open_scope_bindings(fixture, replacement_bindings)

            fresh, fresh_path = self.propose_recovery(fixture)
            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )

            self.assertEqual(code, 0, output + errors)
            self.assertTrue(json.loads(output)["changed"])
            for experience in ("checkout", "returns"):
                package = fixture["root"] / "experiences" / experience
                self.assertEqual(
                    experience_compile.fields(package)["input_bindings"],
                    replacement_bindings,
                )
                self.assertEqual(
                    experience_compile.read_open_revision(package)["proposal_hash"],
                    fresh["proposal_hash"],
                )
                self.assertEqual(
                    (package / "journeys" / f"{experience}-journey.md").read_bytes(),
                    fixture["record_bytes"][experience],
                )
                self.assertEqual(
                    (package / "artifacts" / f"{experience}.bin").read_bytes(),
                    fixture["artifact_bytes"][experience],
                )
            self.assertEqual(
                (fixture["root"] / "artifacts/prototype.bin").read_bytes(),
                fixture["application_artifact_bytes"],
            )
            self.assertEqual(
                (fixture["root"] / experience_application_check.LEDGER_RELATIVE)
                .read_bytes(),
                fixture["ledger_bytes"],
            )

    def test_recovery_rejects_legacy_input_metadata_that_is_not_current(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            unexpected = experience_compile.binding_rows(
                fixture["new_receipts"],
            )
            unexpected[-1] = unexpected[-1][:-64] + "9" * 64
            self.replace_open_scope_bindings(fixture, unexpected)

            with self.recovery_contract(fixture["new_receipts"]):
                code, output, errors = self.run_in_process(
                    "propose", "--root", fixture["root"],
                    "--recover-scope-plan", fixture["old_plan_path"],
                    "--recover-proposal-hash", fixture["old_plan"]["proposal_hash"],
                    "--origin-mode", "manual",
                    "--ba-ref", fixture["new_receipts"][0]["result_ref"],
                    "--solution-ref", fixture["new_receipts"][1]["result_ref"],
                    "--design-ref", fixture["new_receipts"][2]["result_ref"],
                )

            self.assertEqual(code, 2, output + errors)
            self.assertIn("input bindings drifted after opening", errors)

    def test_recover_open_scope_rejects_partial_plan_and_rolls_back_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, _fresh_path = self.propose_recovery(fixture)
            partial = json.loads(json.dumps(fresh))
            partial["actions"] = partial["actions"][:1]
            partial["proposal_hash"] = experience_compile.proposal_digest(partial)
            partial_path = fixture["docs"] / "partial-experience-scope.json"
            partial_path.write_bytes(experience_compile.canonical(partial))
            before = self.tree_snapshot(fixture["docs"])

            code, output, errors = self.recover_scope(
                fixture, partial, partial_path,
            )

            self.assertEqual(code, 2, output + errors)
            self.assertIn(
                "recovery scope plans must preserve the complete package action set",
                errors,
            )
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recover_open_scope_rolls_back_after_a_write_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)
            before = self.tree_snapshot(fixture["docs"])

            with mock.patch.object(
                experience_compile, "render_package_projection",
                return_value=2,
            ):
                code, output, errors = self.recover_scope(
                    fixture, fresh, fresh_path,
                )

            self.assertEqual(code, 2, output + errors)
            self.assertIn("recover-open-scope rolled back", errors)
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recover_open_scope_retry_rejects_generated_projection_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)
            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )
            self.assertEqual(code, 0, output + errors)
            application_registry = (
                fixture["root"] / experience_application_check.REGISTRY_RELATIVE
            )
            application_registry.write_text("{}\n", encoding="utf-8")
            before = self.tree_snapshot(fixture["docs"])

            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )

            self.assertEqual(code, 2, output + errors)
            self.assertIn("recovered application projection drifted", errors)
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recover_open_scope_retry_rejects_navigation_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)
            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )
            self.assertEqual(code, 0, output + errors)
            navigation = fixture["docs"] / "maps/experience-design.md"
            navigation.write_text("# tampered\n", encoding="utf-8")
            before = self.tree_snapshot(fixture["docs"])

            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )

            self.assertEqual(code, 2, output + errors)
            self.assertIn("recovered navigation projection drifted", errors)
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recover_open_scope_retry_binds_the_exact_source_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)
            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )
            self.assertEqual(code, 0, output + errors)
            forged_source = json.loads(json.dumps(fixture["old_plan"]))
            forged_source["input_bindings"][0] = (
                forged_source["input_bindings"][0][:-64] + "9" * 64
            )
            forged_source["proposal_hash"] = (
                experience_compile.proposal_digest(forged_source)
            )
            forged_path = fixture["docs"] / "forged-old-scope.json"
            forged_path.write_bytes(experience_compile.canonical(forged_source))

            with self.recovery_contract(fixture["new_receipts"]):
                code, output, errors = self.run_in_process(
                    "recover-open-scope", "--root", fixture["root"],
                    "--from-scope-plan", forged_path,
                    "--from-proposal-hash", forged_source["proposal_hash"],
                    "--scope-plan", fresh_path,
                    "--proposal-hash", fresh["proposal_hash"],
                )

            self.assertEqual(code, 2, output + errors)
            self.assertIn(
                "fresh recovery plan does not bind the source proposal",
                errors,
            )

    def test_recover_open_scope_rejects_another_live_application_proposal(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            fresh, fresh_path = self.propose_recovery(fixture)
            competing = json.loads(json.dumps(fresh))
            competing["actions"][0]["reason"] += " Competing scope."
            competing["proposal_hash"] = experience_compile.proposal_digest(
                competing,
            )
            self.assertNotEqual(
                competing["proposal_hash"], fresh["proposal_hash"],
            )
            experience_compile.write_open_application_state(
                fixture["root"], competing, competing["proposal_hash"],
                phase="draft",
            )
            before = self.tree_snapshot(fixture["docs"])

            code, output, errors = self.recover_scope(
                fixture, fresh, fresh_path,
            )

            self.assertEqual(code, 2, output + errors)
            self.assertIn(
                "application is open for another scope proposal", errors,
            )
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recovery_proposal_rejects_already_published_open_revisions(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(
                raw, publish_open_packages=True,
            )
            old = fixture["old_plan"]
            receipts = fixture["new_receipts"]

            with self.recovery_contract(receipts):
                code, output, errors = self.run_in_process(
                    "propose", "--root", fixture["root"],
                    "--recover-scope-plan", fixture["old_plan_path"],
                    "--recover-proposal-hash", old["proposal_hash"],
                    "--origin-mode", "manual",
                    "--ba-ref", receipts[0]["result_ref"],
                    "--solution-ref", receipts[1]["result_ref"],
                    "--design-ref", receipts[2]["result_ref"],
                )

            self.assertEqual(code, 2, output + errors)
            self.assertIn(
                "open package revisions are already published", errors,
            )

    def test_rehydrate_published_scope_restores_anchored_package_histories(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(
                raw, publish_open_packages=True,
            )
            replacement_bindings = experience_compile.binding_rows(
                fixture["new_receipts"],
            )
            self.replace_open_scope_bindings(fixture, replacement_bindings)
            ledger_path = (
                fixture["root"] / experience_application_check.LEDGER_RELATIVE
            )
            registry_path = (
                fixture["root"] / experience_application_check.REGISTRY_RELATIVE
            )
            ledger_before = ledger_path.read_bytes()
            registry_before = registry_path.read_bytes()

            code, output, errors = self.rehydrate_published_scope(fixture)

            self.assertEqual(code, 0, output + errors)
            self.assertTrue(json.loads(output)["changed"])
            self.assertEqual(ledger_path.read_bytes(), ledger_before)
            self.assertEqual(registry_path.read_bytes(), registry_before)
            self.assertEqual(
                (fixture["root"] / "artifacts/prototype.bin").read_bytes(),
                fixture["application_artifact_bytes"],
            )
            old_bindings = fixture["old_plan"]["input_bindings"]
            published = {
                row["result_ref"]: row["package_hash"]
                for row in json.loads(ledger_before)["revisions"][0]["packages"]
            }
            for experience in ("checkout", "returns"):
                package = fixture["root"] / "experiences" / experience
                data = experience_compile.fields(package)
                self.assertEqual(data["status"], "approved")
                self.assertEqual(data["input_bindings"], old_bindings)
                self.assertEqual(
                    (package / "artifacts" / f"{experience}.bin").read_bytes(),
                    fixture["artifact_bytes"][experience],
                )
                self.assertFalse(
                    (package / "_generated/open-revision.json").exists()
                )
                registry, problems = experience_compile.compile_package(
                    package, True, allow_stale_inputs=True,
                )
                self.assertEqual(problems, [])
                self.assertEqual(
                    registry["package_hash"], published[f"{experience}@r1"],
                )
                history, ledger_problems = (
                    experience_compile.validate_process_ledger(package, 1)
                )
                self.assertEqual(ledger_problems, [])
                self.assertEqual(history, [])
                for record in registry["records"]:
                    self.assertIsNone(experience_compile.snapshots(
                        package, record["id"], record["revision"],
                    ))

            code, output, errors = self.rehydrate_published_scope(fixture)
            self.assertEqual(code, 0, output + errors)
            self.assertFalse(json.loads(output)["changed"])

            with self.recovery_contract(fixture["new_receipts"]):
                code, output, errors = self.run_in_process(
                    "propose", "--root", fixture["root"],
                    "--origin-mode", "manual",
                    "--process-ref", fixture["old_plan"]["actions"][0]["primary_process_ref"],
                    "--process-ref", fixture["old_plan"]["actions"][1]["primary_process_ref"],
                    "--ba-ref", fixture["new_receipts"][0]["result_ref"],
                    "--solution-ref", fixture["new_receipts"][1]["result_ref"],
                    "--design-ref", fixture["new_receipts"][2]["result_ref"],
                )
            self.assertEqual(code, 0, output + errors)
            successor = json.loads(output)
            self.assertEqual(
                [action["action"] for action in successor["actions"]],
                ["update", "update"],
            )
            successor_path = fixture["docs"] / "successor-scope-plan.json"
            successor_path.write_bytes(experience_compile.canonical(successor))
            with self.recovery_contract(fixture["new_receipts"]), mock.patch.object(
                experience_compile.stage_package, "is_committed", return_value=True,
            ), mock.patch.object(
                experience_compile.stage_package, "paths_are_committed",
                return_value=True,
            ):
                for experience in ("checkout", "returns"):
                    code, output, errors = self.run_in_process(
                        "begin-revision", "--experience-root",
                        fixture["root"] / "experiences" / experience,
                        "--scope-plan", successor_path,
                        "--proposal-hash", successor["proposal_hash"],
                    )
                    self.assertEqual(code, 0, output + errors)
            for experience in ("checkout", "returns"):
                package = fixture["root"] / "experiences" / experience
                data = experience_compile.fields(package)
                self.assertEqual(data["status"], "draft")
                self.assertEqual(data["revision"], 2)
                self.assertEqual(
                    data["input_bindings"], replacement_bindings,
                )
                self.assertEqual(
                    experience_compile.read_open_revision(package)["proposal_hash"],
                    successor["proposal_hash"],
                )

    def test_rehydrate_published_scope_rejects_unprovable_package_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(
                raw, publish_open_packages=True,
            )
            package = fixture["root"] / "experiences" / "checkout"
            record = package / "journeys/checkout-journey.md"
            record.write_text(
                record.read_text(encoding="utf-8") + "changed\n",
                encoding="utf-8",
            )
            before = self.tree_snapshot(fixture["docs"])

            code, output, errors = self.rehydrate_published_scope(fixture)

            self.assertEqual(code, 2, output + errors)
            self.assertIn("package hash does not match application@r1", errors)
            self.assertEqual(self.tree_snapshot(fixture["docs"]), before)

    def test_recovery_proposal_rejects_a_scope_with_current_bindings(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(
                raw, publish_application=False,
            )
            old = fixture["old_plan"]
            old_receipts = [
                {
                    "stage": stage,
                    "result_ref": reference,
                    "package_hash": digest,
                }
                for stage, reference, digest in (
                    value.split("|", 2) for value in old["input_bindings"]
                )
            ]

            with self.recovery_contract(old_receipts):
                code, output, errors = self.run_in_process(
                    "propose", "--root", fixture["root"],
                    "--recover-scope-plan", fixture["old_plan_path"],
                    "--recover-proposal-hash", old["proposal_hash"],
                    "--origin-mode", "manual",
                    "--ba-ref", old_receipts[0]["result_ref"],
                    "--solution-ref", old_receipts[1]["result_ref"],
                    "--design-ref", old_receipts[2]["result_ref"],
                )

            self.assertEqual(code, 2, output + errors)
            self.assertIn("requires changed current inputs", errors)

            reordered = json.loads(json.dumps(old))
            reordered["input_bindings"] = list(
                reversed(reordered["input_bindings"])
            )
            self.assertFalse(
                experience_compile.recovery_bindings_changed(reordered, old)
            )

            no_delta = json.loads(json.dumps(old))
            no_delta["schema_version"] = 3
            no_delta["recovery_from_proposal_hash"] = old["proposal_hash"]
            no_delta["proposal_hash"] = experience_compile.proposal_digest(
                no_delta,
            )
            no_delta_path = fixture["docs"] / "no-delta-recovery.json"
            no_delta_path.write_bytes(experience_compile.canonical(no_delta))
            code, output, errors = self.recover_scope(
                fixture, no_delta, no_delta_path,
            )
            self.assertEqual(code, 2, output + errors)
            self.assertIn("requires changed current inputs", errors)

    def test_recovery_proposal_accepts_an_application_receipt_delta_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = self.orphaned_create_scope(raw)
            old = fixture["old_plan"]
            old_receipts = [
                {
                    "stage": stage,
                    "result_ref": reference,
                    "package_hash": digest,
                }
                for stage, reference, digest in (
                    value.split("|", 2) for value in old["input_bindings"]
                )
            ]

            with self.recovery_contract(old_receipts):
                code, output, errors = self.run_in_process(
                    "propose", "--root", fixture["root"],
                    "--recover-scope-plan", fixture["old_plan_path"],
                    "--recover-proposal-hash", old["proposal_hash"],
                    "--origin-mode", "manual",
                    "--ba-ref", old_receipts[0]["result_ref"],
                    "--solution-ref", old_receipts[1]["result_ref"],
                    "--design-ref", old_receipts[2]["result_ref"],
                )

            self.assertEqual(code, 0, output + errors)
            fresh = json.loads(output)
            self.assertEqual(fresh["input_bindings"], old["input_bindings"])
            self.assertEqual(fresh["application_action"], "update")
            self.assertEqual(fresh["expected_application"]["revision"], 1)

    def test_legacy_program_commands_are_rejected(self):
        result = self.run_cli("init-program", "--root", "/tmp/x", "--program", "PRG-001")
        self.assertNotEqual(result.returncode, 0)

    def test_removed_process_local_artifact_command_is_rejected(self):
        result = self.run_cli(
            "init-artifact", "--experience-root", "/tmp/checkout"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_child_status_is_rejected_by_living_package_check(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\nstatus: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\ninput_bindings:\n---\n# Checkout\n",
                encoding="utf-8",
            )
            child = package / "journeys/checkout-journey.md"
            child.parent.mkdir()
            child.write_text(
                "---\ntype: journey\nid: JRN-001\nrevision: 1\nstatus: approved\nrecord_state: active\nderives_from:\n  - marketplace:PRC-001\n---\n# Checkout\n",
                encoding="utf-8",
            )
            result = self.run_cli("check", "--experience-root", package, "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("child records cannot carry approval state", result.stdout)

    def test_active_state_requires_canonical_state_class(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\n"
                "status: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\n"
                "input_bindings:\n---\n# Checkout\n\n"
                "## Navigation <!-- sec: nav -->\n\n"
                "[[maps/experience-design|Experience Design]]\n",
                encoding="utf-8",
            )
            missing = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("requires --state-class", missing.stderr)

            created = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
                "--state-class", "ordinary",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            state = package / "states/ready-state.md"
            state.write_text(
                state.read_text(encoding="utf-8").replace(
                    "state_class: ordinary\n", "", 1,
                ),
                encoding="utf-8",
            )
            checked = self.run_cli(
                "check", "--experience-root", package, "--json",
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("active state needs a canonical state_class", checked.stdout)

    def test_transaction_rollback_restores_root_map_and_home(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            map_path = docs / "maps/experience-design.md"
            map_path.parent.mkdir()
            map_path.write_text("map before\n", encoding="utf-8")
            home = docs / "home.md"
            home.write_text("home before\n", encoding="utf-8")
            if os.name != "nt":
                os.chmod(map_path, 0o1640)
                os.chmod(home, 0o1644)
            map_mode = stat.S_IMODE(map_path.stat().st_mode)
            home_mode = stat.S_IMODE(home.stat().st_mode)
            marker = root / "marker.txt"
            marker.write_text("root before\n", encoding="utf-8")

            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            marker.write_text("root after\n", encoding="utf-8")
            map_path.write_text("map after\n", encoding="utf-8")
            home.write_text("home after\n", encoding="utf-8")
            if os.name == "nt":
                os.chmod(map_path, stat.S_IREAD)
                os.chmod(home, stat.S_IREAD)
            experience_compile.rollback_transaction(root, transaction_id)

            self.assertEqual(marker.read_text(encoding="utf-8"), "root before\n")
            self.assertEqual(map_path.read_text(encoding="utf-8"), "map before\n")
            self.assertEqual(home.read_text(encoding="utf-8"), "home before\n")
            self.assertEqual(stat.S_IMODE(map_path.stat().st_mode), map_mode)
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), home_mode)

    def test_root_and_child_directory_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            docs = project / "workspace/docs"
            docs.mkdir(parents=True)
            outside = project / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"outside\x00")
            root = docs / "experience-design"

            def create_alias(alias: Path, target: Path) -> None:
                if os.name == "nt":
                    result = subprocess.run(
                        [
                            "cmd.exe", "/d", "/c", "mklink", "/J",
                            str(alias), str(target),
                        ],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr,
                    )
                else:
                    alias.symlink_to(target, target_is_directory=True)

            def remove_alias(alias: Path) -> None:
                if os.name == "nt":
                    os.rmdir(alias)
                else:
                    alias.unlink()

            create_alias(root, outside)
            try:
                with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                    experience_compile.root_for(project)
                self.assertEqual(sentinel.read_bytes(), b"outside\x00")
            finally:
                remove_alias(root)

            root.mkdir()
            child = root / "nested"
            create_alias(child, outside)
            try:
                with self.assertRaisesRegex(ValueError, "contains an alias"):
                    experience_compile.validate_mutation_surface(root)
                self.assertEqual(sentinel.read_bytes(), b"outside\x00")
            finally:
                remove_alias(child)

    def test_missing_navigation_map_never_uses_an_aliased_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            docs = project / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            outside = project / "outside-maps"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"outside\x00")
            maps = docs / "maps"
            if os.name == "nt":
                result = subprocess.run(
                    [
                        "cmd.exe", "/d", "/c", "mklink", "/J",
                        str(maps), str(outside),
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr,
                )
            else:
                maps.symlink_to(outside, target_is_directory=True)
            try:
                with self.assertRaisesRegex(ValueError, "navigation owner"):
                    experience_compile.validate_mutation_surface(root)
                with self.assertRaisesRegex(ValueError, "navigation owner"):
                    experience_compile.begin_transaction(root, "render")
                self.assertEqual(sentinel.read_bytes(), b"outside\x00")
                self.assertFalse((outside / "experience-design.md").exists())
            finally:
                if os.name == "nt":
                    os.rmdir(maps)
                else:
                    maps.unlink()

    def test_transaction_rollback_removes_a_new_empty_maps_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            map_path = experience_compile.transaction_map(root)
            map_path.parent.mkdir()
            map_path.write_text("created\n", encoding="utf-8")

            experience_compile.rollback_transaction(root, transaction_id)

            self.assertFalse(map_path.exists())
            self.assertFalse(map_path.parent.exists())

    @unittest.skipIf(os.name == "nt", "POSIX parent-mode contract")
    def test_transaction_rollback_temporarily_opens_readonly_docs_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            marker = root / "marker.txt"
            marker.write_text("before\n", encoding="utf-8")
            transaction_id = experience_compile.begin_transaction(
                root, "recover-open-scope",
            )
            marker.write_text("after\n", encoding="utf-8")
            os.chmod(docs, 0o555)
            try:
                experience_compile.rollback_transaction(root, transaction_id)
                self.assertEqual(marker.read_text(encoding="utf-8"), "before\n")
                self.assertEqual(stat.S_IMODE(docs.stat().st_mode), 0o555)
                self.assertFalse(
                    experience_compile.transaction_journal(root).exists()
                )
            finally:
                os.chmod(docs, 0o755)

    @unittest.skipIf(os.name == "nt", "POSIX directory-mode contract")
    def test_transaction_commit_cleans_a_readonly_artifact_backup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            locked = root / "artifacts/locked"
            locked.mkdir(parents=True)
            (locked / "prototype.bin").write_bytes(b"prototype\x00")
            os.chmod(locked, 0o555)
            transaction_id = experience_compile.begin_transaction(
                root, "recover-open-scope",
            )
            backup = experience_compile.transaction_backup(
                root, transaction_id,
            )
            mutation = root / "mutation.txt"
            mutation.write_text("committed\n", encoding="utf-8")

            experience_compile.commit_transaction(root, transaction_id)

            self.assertTrue(mutation.is_file())
            self.assertFalse(
                experience_compile.transaction_journal(root).exists()
            )
            self.assertFalse(backup.exists())
            os.chmod(locked, 0o755)

    @unittest.skipUnless(
        hasattr(os, "chflags") and bool(getattr(stat, "UF_IMMUTABLE", 0)),
        "macOS immutable-file contract",
    )
    def test_transaction_commit_cleans_an_immutable_backup_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            (root / "marker.bin").write_bytes(b"before\x00")
            transaction_id = experience_compile.begin_transaction(
                root, "recover-open-scope",
            )
            backup = experience_compile.transaction_backup(
                root, transaction_id,
            )
            backup_file = backup / "experience-design/marker.bin"
            try:
                try:
                    os.chflags(backup_file, stat.UF_IMMUTABLE)
                except OSError as exc:
                    self.skipTest(f"filesystem cannot set UF_IMMUTABLE: {exc}")
                experience_compile.commit_transaction(root, transaction_id)
                self.assertFalse(backup.exists())
            finally:
                if backup_file.exists():
                    flags = getattr(backup_file.stat(), "st_flags", 0)
                    if flags & stat.UF_IMMUTABLE:
                        os.chflags(
                            backup_file, flags & ~stat.UF_IMMUTABLE,
                        )

    @unittest.skipUnless(
        hasattr(os, "chflags") and bool(getattr(stat, "UF_IMMUTABLE", 0)),
        "macOS immutable-file contract",
    )
    def test_transaction_rollback_reapplies_immutable_map_preimage(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            map_path = experience_compile.transaction_map(root)
            map_path.parent.mkdir()
            map_path.write_text("before\n", encoding="utf-8")
            try:
                try:
                    os.chflags(map_path, stat.UF_IMMUTABLE)
                except OSError as exc:
                    self.skipTest(f"filesystem cannot set UF_IMMUTABLE: {exc}")
                transaction_id = experience_compile.begin_transaction(
                    root, "render",
                )
                os.chflags(map_path, 0)
                map_path.write_text("after\n", encoding="utf-8")

                experience_compile.rollback_transaction(root, transaction_id)

                self.assertEqual(map_path.read_text(encoding="utf-8"), "before\n")
                self.assertTrue(
                    getattr(map_path.stat(), "st_flags", 0)
                    & stat.UF_IMMUTABLE
                )
            finally:
                if map_path.exists():
                    flags = getattr(map_path.stat(), "st_flags", 0)
                    if flags & stat.UF_IMMUTABLE:
                        os.chflags(map_path, flags & ~stat.UF_IMMUTABLE)

    @unittest.skipUnless(
        hasattr(os, "chflags") and bool(getattr(stat, "UF_IMMUTABLE", 0)),
        "macOS immutable-directory contract",
    )
    def test_transaction_rollback_opens_and_recloses_immutable_docs_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            marker = root / "marker.txt"
            marker.write_text("before\n", encoding="utf-8")
            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            marker.write_text("after\n", encoding="utf-8")
            try:
                try:
                    os.chflags(docs, stat.UF_IMMUTABLE)
                except OSError as exc:
                    self.skipTest(f"filesystem cannot set UF_IMMUTABLE: {exc}")

                experience_compile.rollback_transaction(root, transaction_id)

                self.assertEqual(marker.read_text(encoding="utf-8"), "before\n")
                self.assertTrue(
                    getattr(docs.stat(), "st_flags", 0)
                    & stat.UF_IMMUTABLE
                )
            finally:
                if docs.exists():
                    flags = getattr(docs.stat(), "st_flags", 0)
                    if flags & stat.UF_IMMUTABLE:
                        os.chflags(docs, flags & ~stat.UF_IMMUTABLE)

    @unittest.skipUnless(
        hasattr(os, "chflags") and bool(getattr(stat, "UF_IMMUTABLE", 0)),
        "macOS immutable-file contract",
    )
    def test_transaction_rollback_cleans_an_immutable_journal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            marker = root / "marker.txt"
            marker.write_text("before\n", encoding="utf-8")
            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            journal = experience_compile.transaction_journal(root)
            marker.write_text("after\n", encoding="utf-8")
            try:
                try:
                    os.chflags(journal, stat.UF_IMMUTABLE)
                except OSError as exc:
                    self.skipTest(f"filesystem cannot set UF_IMMUTABLE: {exc}")

                experience_compile.rollback_transaction(root, transaction_id)

                self.assertEqual(marker.read_text(encoding="utf-8"), "before\n")
                self.assertFalse(journal.exists())
            finally:
                if journal.exists():
                    flags = getattr(journal.stat(), "st_flags", 0)
                    if flags & stat.UF_IMMUTABLE:
                        os.chflags(journal, flags & ~stat.UF_IMMUTABLE)

    @unittest.skipUnless(os.name == "nt", "native Windows READONLY contract")
    def test_transaction_rollback_cleans_a_windows_readonly_journal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            marker = root / "marker.txt"
            marker.write_text("before\n", encoding="utf-8")
            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            journal = experience_compile.transaction_journal(root)
            marker.write_text("after\n", encoding="utf-8")
            os.chmod(journal, stat.S_IREAD)

            experience_compile.rollback_transaction(root, transaction_id)

            self.assertEqual(marker.read_text(encoding="utf-8"), "before\n")
            self.assertFalse(journal.exists())

    @unittest.skipUnless(os.name == "nt", "native Windows READONLY contract")
    def test_transaction_commit_cleans_a_windows_readonly_backup_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            (root / "marker.bin").write_bytes(b"before\x00")
            transaction_id = experience_compile.begin_transaction(
                root, "recover-open-scope",
            )
            backup = experience_compile.transaction_backup(
                root, transaction_id,
            )
            backup_file = backup / "experience-design/marker.bin"
            os.chmod(backup_file, stat.S_IREAD)

            experience_compile.commit_transaction(root, transaction_id)

            self.assertFalse(backup.exists())

    def test_transaction_rollback_never_follows_a_replaced_docs_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            docs = project / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            (root / "marker.txt").write_text("before\n", encoding="utf-8")
            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )

            moved_docs = project / "moved-docs"
            docs.rename(moved_docs)
            outside_docs = project / "outside-docs"
            outside_root = outside_docs / "experience-design"
            outside_root.mkdir(parents=True)
            sentinel = outside_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside\x00")
            if os.name == "nt":
                result = subprocess.run(
                    [
                        "cmd.exe", "/d", "/c", "mklink", "/J",
                        str(docs), str(outside_docs),
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr,
                )
            else:
                docs.symlink_to(outside_docs, target_is_directory=True)
            try:
                with self.assertRaisesRegex(ValueError, "parent is an alias"):
                    experience_compile.rollback_transaction(
                        root, transaction_id,
                    )
                self.assertEqual(sentinel.read_bytes(), b"outside\x00")
                self.assertFalse((outside_root / "marker.txt").exists())
            finally:
                if os.name == "nt":
                    os.rmdir(docs)
                else:
                    docs.unlink()
                moved_docs.rename(docs)

    def test_transaction_runtime_never_follows_a_project_local_alias(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            root = project / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            outside = project / "outside-runtime"
            outside.mkdir()
            local_runtime_owner = project / ".agentrof"
            if os.name == "nt":
                result = subprocess.run(
                    [
                        "cmd.exe", "/d", "/c", "mklink", "/J",
                        str(local_runtime_owner), str(outside),
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr,
                )
            else:
                local_runtime_owner.symlink_to(
                    outside, target_is_directory=True,
                )
            try:
                with self.assertRaisesRegex(ValueError, "runtime is an alias"):
                    experience_compile.begin_transaction(root, "render")
                self.assertEqual(list(outside.iterdir()), [])
            finally:
                if os.name == "nt":
                    os.rmdir(local_runtime_owner)
                else:
                    local_runtime_owner.unlink()

    def test_legacy_transaction_journal_remains_readable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            journal = experience_compile.transaction_journal(root)
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({
                "schema_version": 1,
                "transaction_id": "1" * 32,
                "command": "render",
                "root": str(root),
                "root_existed": True,
                "map_path": str(experience_compile.transaction_map(root)),
                "map_existed": False,
                "phase": "prepared",
            }), encoding="utf-8")
            value = experience_compile.read_transaction_journal(root)
            self.assertIsNotNone(value)
            self.assertEqual(value["schema_version"], 1)

    def test_requirement_reference_round_trips_as_a_vault_link(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            requirement = docs / "requirements/req-001-checkout.md"
            requirement.parent.mkdir(parents=True)
            requirement.write_text(
                "---\ntype: requirement\nid: REQ-001\n---\n# Checkout\n",
                encoding="utf-8",
            )
            link = experience_compile.requirement_reference_link(
                docs, "REQ-001",
            )
            self.assertEqual(
                link, "[[requirements/req-001-checkout|REQ-001]]",
            )
            self.assertEqual(
                experience_compile.requirement_reference_value(link),
                "REQ-001",
            )

    def test_all_active_stubs_pass_scoped_vault_and_compiler_contracts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            repository = subprocess.run(
                ["git", "init", "-q", str(project)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(
                repository.returncode, 0,
                repository.stdout + repository.stderr,
            )
            setup = subprocess.run(
                [sys.executable, str(SETUP), "apply", "--project-root",
                 str(project), "--json"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)

            docs = project / "workspace/docs"
            root = docs / "experience-design"
            package = root / "experiences/checkout"
            process = "business-analysis/commerce/processes/checkout-process"
            process_note = (docs / process).with_suffix(".md")
            process_note.parent.mkdir(parents=True)
            process_note.write_text(
                "---\ntype: process\ntitle: Checkout process\n"
                "tags:\n  - doc/process\n---\n\n# Checkout process\n",
                encoding="utf-8",
            )
            design_note = docs / "design-system/MASTER.md"
            design_note.parent.mkdir(parents=True, exist_ok=True)
            design_note.write_text(
                "---\ntype: design-master\ntitle: Product design\n"
                "tags:\n  - doc/design-master\n---\n\n# Product design\n",
                encoding="utf-8",
            )
            template_root = (
                ROOT / "plugins/software-engineering-team/templates/vault"
            )
            for subtree, target, title in (
                ("business-analysis", process, "Checkout process"),
                ("design-system", "design-system/MASTER", "Product design"),
            ):
                map_path = docs / "maps" / f"{subtree}.md"
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_text(
                    (template_root / "maps" / f"{subtree}.md").read_text(
                        encoding="utf-8"
                    ).rstrip()
                    + f"\n\n- [[{target}|{title}]]\n",
                    encoding="utf-8",
                )
            home = docs / "home.md"
            home.write_text(
                home.read_text(encoding="utf-8").rstrip()
                + "\n\n- [[maps/business-analysis|Business Analysis]]"
                + "\n- [[maps/design-system|Design System]]\n",
                encoding="utf-8",
            )
            receipts = [
                {
                    "stage": stage,
                    "result_ref": reference,
                    "package_hash": "sha256:" + character * 64,
                }
                for stage, reference, character in (
                    ("business-analysis", "business-analysis/commerce/space", "a"),
                    ("solution-design", "solution-design/landscape", "b"),
                    ("design-system", "design-system/MASTER", "c"),
                )
            ]
            action = {
                "action": "create",
                "source_experience": "checkout",
                "target_experience": "checkout",
                "proposal_hash": "sha256:" + "1" * 64,
            }
            plan = {"origin_mode": "manual", "actions": [action]}
            init_args = [
                "init", "--root", str(root), "--experience", "checkout",
                "--origin-mode", "manual", "--primary-process-ref", process,
                "--scope-plan", str(project / "scope-plan.json"),
                "--proposal-hash", action["proposal_hash"],
                "--title", "Checkout Experience",
                "--ba-ref", "business-analysis/commerce/space",
                "--solution-ref", "solution-design/landscape",
                "--design-ref", "design-system/MASTER",
            ]
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(
                experience_compile, "selected_inputs",
                return_value=(receipts, [], {}),
            ), mock.patch.object(
                experience_compile, "load_scope_plan", return_value=plan,
            ), mock.patch.object(
                experience_compile, "verify_scope_inputs", return_value=[],
            ), mock.patch.object(
                experience_compile, "process_from_inputs",
                return_value=(process, []),
            ), mock.patch.object(
                experience_compile, "action_for_plan", return_value=action,
            ), mock.patch.object(
                experience_compile, "open_application",
            ), mock.patch.object(
                experience_compile, "write_open_revision",
            ), redirect_stdout(output), redirect_stderr(errors):
                initialized = experience_compile.main(init_args)
            self.assertEqual(initialized, 0, output.getvalue() + errors.getvalue())

            cases = (
                ("journey", "JRN-001", "purchase", "Purchase Journey", []),
                ("flow-set", "FLW-001", "checkout", "Checkout Flow", []),
                (
                    "screen", "SCR-001", "payment", "Payment Screen",
                    ["--uses-design", "design-system/MASTER"],
                ),
                (
                    "state", "STA-001", "ready", "Ready State",
                    ["--state-class", "ordinary"],
                ),
                (
                    "transition", "TRN-001", "submit", "Submit Transition",
                    ["--related-to", "checkout:STA-001@r1"],
                ),
            )
            for kind, identifier, slug, title, extra in cases:
                with self.subTest(kind=kind):
                    created = self.run_cli(
                        "stub", "--experience-root", package, "--kind", kind,
                        "--id", identifier, "--slug", slug, "--title", title,
                        *extra,
                    )
                    self.assertEqual(
                        created.returncode, 0,
                        created.stdout + created.stderr,
                    )
                    directory, _prefix, suffix = experience_compile.KIND[kind]
                    note = package / directory / f"{slug}{suffix}"
                    impacted = subprocess.run(
                        [sys.executable, str(VAULT_CHECK), "check", "--vault",
                         str(docs), "--impact",
                         note.relative_to(docs).as_posix(), "--json"],
                        cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(
                        impacted.returncode, 0,
                        impacted.stdout + impacted.stderr,
                    )

            state = package / "states/ready-state.md"
            self.assertEqual(
                experience_compile.fm(state)[0]["state_class"], "ordinary",
            )
            relations = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "render-relations",
                 "--vault", str(docs)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(
                relations.returncode, 0,
                relations.stdout + relations.stderr,
            )
            vault = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "check", "--vault",
                 str(docs), "--scope", "experience-design", "--json"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(vault.returncode, 0, vault.stdout + vault.stderr)

            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(
                experience_compile.stage_package, "verify",
                return_value=({}, []),
            ), mock.patch.object(
                experience_compile.stage_package, "resolve_ba_process",
                return_value=(process, []),
            ), redirect_stdout(output), redirect_stderr(errors):
                checked = experience_compile.main([
                    "check", "--experience-root", str(package), "--json",
                ])
            self.assertEqual(checked, 0, output.getvalue() + errors.getvalue())

    def test_exact_lifecycle_json_rejects_boolean_integer_aliases(self):
        hashes = {
            key: "sha256:" + "1" * 64
            for key in (
                "artifact_tree_hash", "package_set_hash", "application_hash",
            )
        }
        preimage = {
            "exists": True, "status": "approved", "revision": True,
            **hashes,
        }
        self.assertFalse(experience_compile.exact_application_preimage(preimage))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            state = root / "_generated/open-application-revision.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({
                "schema_version": True,
                "proposal_hash": "sha256:" + "2" * 64,
                "application_action": "update",
                "package_actions_hash": "sha256:" + "3" * 64,
                "expected_application": {**preimage, "revision": 1},
                "opened_revision": 2,
                "phase": "draft",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid exact schema"):
                experience_compile.read_open_application_state(root)


if __name__ == "__main__":
    unittest.main()
