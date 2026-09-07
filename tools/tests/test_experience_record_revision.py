"""Regression contract for atomic revision of an Experience record closure."""

import json
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest import mock

from tools.tests import test_experience_compile as fixtures
from tools.tests import test_vault_hook as hook_fixtures


compiler = fixtures.experience_compile
application = fixtures.experience_application_check


class ExperienceRecordRevisionTests(unittest.TestCase):
    def setUp(self):
        self.helpers = fixtures.ExperienceCompilerTests()

    def upstream(self, receipts):
        stack = ExitStack()
        stack.enter_context(self.helpers.recovery_contract(receipts))
        stack.enter_context(mock.patch.object(
            compiler.stage_package, "is_committed", return_value=True,
        ))
        stack.enter_context(mock.patch.object(
            compiler.stage_package, "paths_are_committed", return_value=True,
        ))
        return stack

    def successful(self, *args):
        code, output, errors = self.helpers.run_in_process(*args)
        self.assertEqual(code, 0, output + errors)
        return output

    def fixture(self, temporary, *, open_returns=True, returns_action="update",
                retired_owner=None):
        fixture = self.helpers.orphaned_create_scope(
            temporary, publish_application=False,
        )
        root = fixture["root"]
        paths = {}
        # Twelve seeds, twenty-one reverse dependents, and two untouched journeys.
        for number in range(1, 34):
            owner = "checkout" if number <= 23 else "returns"
            package = root / "experiences" / owner
            ident = f"FLW-{number:03d}"
            data = {
                "type": "flow-set", "title": f"Flow {number}", "id": ident,
                "revision": 1, "record_state": "active",
                "derives_from": [compiler.fields(package)["primary_process_ref"]],
            }
            if number == 1:
                data["flow_refs"] = ["checkout:FLW-023@r1"]
            elif number > 12:
                predecessor = 1 if number == 13 else number - 1
                predecessor_owner = "checkout" if predecessor <= 23 else "returns"
                reference = f"{predecessor_owner}:FLW-{predecessor:03d}@r1"
                if number % 2:
                    data["related_to"] = [
                        "[[experience-design/experiences/"
                        f"{predecessor_owner}/flows/flow-{predecessor}-flow-set|"
                        f"{reference}]]"
                    ]
                else:
                    data["flow_refs"] = [reference]
            path = package / "flows" / f"flow-{number}-flow-set.md"
            path.write_text(compiler.render_fm(
                data, f"# Flow {number}\n\nKeep literal checkout:FLW-001@r1.\n"
                "\n| Detail | Value |\n| --- | --- |\n| Author | Jane Doe |\n",
            ), encoding="utf-8")
            paths[f"{owner}:{ident}@r1"] = path
        returns = root / "experiences/returns/experience.md"
        data, body = compiler.fm(returns)
        data["related_process_refs"] = [
            compiler.fields(root / "experiences/checkout")["primary_process_ref"],
        ]
        compiler.rewrite(returns, data, body)
        if retired_owner is not None:
            retired = root / "experiences" / retired_owner / "flows/retired-flow-set.md"
            retired.write_text(compiler.render_fm({
                "type": "flow-set", "title": "Retired flow", "id": "FLW-090",
                "revision": 1, "record_state": "retired",
                "flow_refs": ["checkout:FLW-002@r1"],
            }, "# Retired flow\n\nPreserve the historical reference.\n"), encoding="utf-8")
        for package in compiler.packages(root):
            compiler.render_package_record_navigation(package)
        compiler.render_experience_navigation(root)
        compiler.write_open_application_state(
            root, fixture["old_plan"], fixture["old_plan"]["proposal_hash"],
            phase="draft",
        )
        registry, findings = application.compile_application(root)
        self.assertEqual(findings, [])
        application.write_registry_and_ledger(root, registry)
        compiler.open_application_state_path(root).unlink()
        code, output, errors = self.helpers.rehydrate_published_scope(fixture)
        self.assertEqual(code, 0, output + errors)
        with self.upstream(fixture["new_receipts"]):
            output = self.successful(
                "propose", "--root", root, "--origin-mode", "manual",
                "--process-ref", fixture["old_plan"]["actions"][0]["primary_process_ref"],
                "--process-ref", fixture["old_plan"]["actions"][1]["primary_process_ref"],
                "--ba-ref", fixture["new_receipts"][0]["result_ref"],
                "--solution-ref", fixture["new_receipts"][1]["result_ref"],
                "--design-ref", fixture["new_receipts"][2]["result_ref"],
            )
            plan = json.loads(output)
            if returns_action != "update":
                for action in plan["actions"]:
                    if action["experience"] == "returns":
                        action["action"] = returns_action
                plan["proposal_hash"] = compiler.proposal_digest(plan)
            plan_path = fixture["docs"] / "revision-scope.json"
            plan_path.write_bytes(compiler.canonical(plan))
            for owner in ("checkout", "returns"):
                if owner == "returns" and (not open_returns or returns_action != "update"):
                    continue
                self.successful(
                    "begin-revision", "--experience-root", root / "experiences" / owner,
                    "--scope-plan", plan_path, "--proposal-hash", plan["proposal_hash"],
                )
        compiler.render_experience_navigation(root)
        fixture.update(plan=plan, plan_path=plan_path, paths=paths)
        return fixture

    def arguments(self, fixture, refs=None):
        args = [
            "revise-records", "--root", fixture["root"],
            "--scope-plan", fixture["plan_path"],
            "--proposal-hash", fixture["plan"]["proposal_hash"],
        ]
        for reference in refs if refs is not None else ["checkout:FLW-001@r1"]:
            args.extend(("--record-ref", reference))
        return args

    def revise(self, fixture, refs=None):
        with self.upstream(fixture["new_receipts"]):
            return self.helpers.run_in_process(*self.arguments(fixture, refs))

    def assert_rejected_unchanged(self, fixture, refs=None):
        before = self.helpers.tree_snapshot(fixture["docs"])
        code, output, errors = self.revise(fixture, refs)
        self.assertEqual(code, 2, output + errors)
        self.assertTrue(output or errors)
        self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    def test_fixture_opens_real_approved_record_ledgers(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            for owner in ("checkout", "returns"):
                package = fixture["root"] / "experiences" / owner
                history, findings = compiler.validate_process_ledger(package, 2)
                self.assertEqual(findings, [])
                self.assertEqual(len(history), 1)
                self.assertEqual(compiler.fields(package)["status"], "draft")
                for row in history[0]["records"]:
                    self.assertIsNotNone(compiler.snapshots(package, row["id"], 1))

    def test_twelve_seeds_revise_thirty_three_with_cycle_and_typed_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            before = self.helpers.tree_snapshot(fixture["docs"])
            original = {ref: compiler.fm(path) for ref, path in fixture["paths"].items()}
            seeds = [f"checkout:FLW-{number:03d}@r1" for number in range(1, 13)]
            code, output, errors = self.revise(fixture, seeds)
            self.assertEqual(code, 0, output + errors)
            self.assertEqual(json.loads(output), {
                "ok": True, "changed_records": 33,
                "record_refs": {ref: ref.replace("@r1", "@r2") for ref in original},
            })
            for reference, path in fixture["paths"].items():
                data, body = compiler.fm(path)
                expected, original_body = original[reference]
                expected = dict(expected, revision=2, supersedes=reference)
                for field in compiler.REFERENCE_FIELDS:
                    if field in expected:
                        expected[field] = [value.replace("@r1", "@r2") for value in expected[field]]
                self.assertEqual(data, expected, reference)
                self.assertEqual(body, original_body, reference)
            after = self.helpers.tree_snapshot(fixture["docs"])
            changed_records = {
                path.relative_to(fixture["docs"]).as_posix()
                for path in fixture["paths"].values()
            }
            for name, contents in before.items():
                if name not in changed_records and "/_generated/" not in name:
                    self.assertEqual(after.get(name), contents, name)
            code, output, errors = self.revise(fixture, seeds)
            self.assertEqual(code, 0, output + errors)
            self.assertEqual(json.loads(output)["changed_records"], 0)
            self.assertEqual(json.loads(output)["record_refs"], {
                ref: ref.replace("@r1", "@r2") for ref in original
            })
            self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), after)

    def test_malformed_unknown_stale_and_duplicate_refs_reject_without_writes(self):
        cases = (
            ["checkout:FLW-001"], ["checkout:FLW-001@r0"],
            ["checkout:FLW-999@r1"], ["missing:FLW-001@r1"],
            ["checkout:FLW-001@r2"],
            ["checkout:FLW-001@r1", "checkout:FLW-001@r1"],
            ["checkout:FLW-001@r1", "returns:FLW-999@r1"],
        )
        for refs in cases:
            with self.subTest(refs=refs), tempfile.TemporaryDirectory() as temporary:
                fixture = self.fixture(temporary)
                self.assert_rejected_unchanged(fixture, refs)

    def test_wrong_proposal_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture["plan"]["proposal_hash"] = "sha256:" + "0" * 64
            self.assert_rejected_unchanged(fixture)

    def assert_preflight_rejected_unchanged(self, fixture, refs):
        before = self.helpers.tree_snapshot(fixture["docs"])
        selected_paths = {path.resolve() for path in fixture["paths"].values()}
        with mock.patch.object(
            compiler, "atomic_write_bytes", wraps=compiler.atomic_write_bytes,
        ) as writes:
            code, output, errors = self.revise(fixture, refs)
        record_writes = [
            call.args[0] for call in writes.call_args_list
            if Path(call.args[0]).resolve() in selected_paths
        ]
        self.assertEqual(record_writes, [], "rejection must precede record writes")
        self.assertEqual(code, 2, output + errors)
        self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    def test_retired_child_reference_rejects_same_and_cross_package_before_writes(self):
        for owner in ("checkout", "returns"):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as temporary:
                fixture = self.fixture(temporary, retired_owner=owner)
                package = fixture["root"] / "experiences" / owner
                historical = compiler.snapshots(package, "FLW-090", 1)
                self.assertEqual(historical["record_state"], "retired")
                self.assertEqual(historical["flow_refs"], ["checkout:FLW-002@r1"])
                self.assert_preflight_rejected_unchanged(fixture, ["checkout:FLW-002@r1"])

    def test_selected_typed_wikilinks_reject_missing_or_mismatched_targets_before_writes(self):
        fields = (
            "journey_refs", "flow_refs", "screen_refs", "state_refs",
            "transition_refs", "related_to",
        )
        targets = (
            "missing/note",
            "experience-design/experiences/checkout/flows/flow-3-flow-set",
        )
        # The alias may name either the revised seed or another unchanged record.
        for field in fields:
            for target in targets:
                for alias in ("checkout:FLW-002@r1", "checkout:FLW-001@r1"):
                    with self.subTest(field=field, target=target, alias=alias), \
                            tempfile.TemporaryDirectory() as temporary:
                        fixture = self.fixture(temporary)
                        path = fixture["paths"]["checkout:FLW-002@r1"]
                        data, body = compiler.fm(path)
                        data[field] = [f"[[{target}|{alias}]]"]
                        compiler.rewrite(path, data, body)
                        self.assert_preflight_rejected_unchanged(
                            fixture, ["checkout:FLW-002@r1"],
                        )

    def test_bare_exact_references_remain_allowed_in_all_typed_fields(self):
        for field in (
            "journey_refs", "flow_refs", "screen_refs", "state_refs",
            "transition_refs", "related_to",
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                fixture = self.fixture(temporary)
                path = fixture["paths"]["checkout:FLW-002@r1"]
                data, body = compiler.fm(path)
                data[field] = ["checkout:FLW-002@r1"]
                compiler.rewrite(path, data, body)
                code, output, errors = self.revise(fixture, ["checkout:FLW-002@r1"])
                self.assertEqual(code, 0, output + errors)
                self.assertEqual(json.loads(output), {
                    "ok": True, "changed_records": 1,
                    "record_refs": {"checkout:FLW-002@r1": "checkout:FLW-002@r2"},
                })
                updated, updated_body = compiler.fm(path)
                self.assertEqual(updated[field], ["checkout:FLW-002@r2"])
                self.assertEqual(updated_body, body)

    def test_missing_record_argument_rejects_at_cli_boundary_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            before = self.helpers.tree_snapshot(fixture["docs"])
            result = self.helpers.run_cli(*self.arguments(fixture, []))
            self.assertEqual(result.returncode, 2)
            self.assertIn("--record-ref", result.stderr)
            self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    def test_open_input_bindings_drift_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            path = fixture["root"] / "experiences/checkout/experience.md"
            data, body = compiler.fm(path)
            data["input_bindings"] = fixture["old_plan"]["input_bindings"]
            compiler.rewrite(path, data, body)
            self.assert_rejected_unchanged(fixture)

    def test_corrupt_predecessor_snapshot_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            path = fixture["root"] / "experiences/checkout/_ledger/records/FLW-001/r1.json"
            data = json.loads(path.read_bytes())
            data["title"] = "Tampered title"
            path.write_bytes(compiler.canonical(data))
            self.assert_rejected_unchanged(fixture)

    def test_application_review_phase_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            compiler.write_open_application_state(
                fixture["root"], fixture["plan"], fixture["plan"]["proposal_hash"],
                phase="in_review",
            )
            self.assert_rejected_unchanged(fixture)

    def test_manually_advanced_child_revision_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            path = fixture["paths"]["checkout:FLW-001@r1"]
            data, body = compiler.fm(path)
            data["revision"] = 3
            data["supersedes"] = "checkout:FLW-001@r1"
            compiler.rewrite(path, data, body)
            self.assert_rejected_unchanged(fixture)

    def test_input_drift_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            before = self.helpers.tree_snapshot(fixture["docs"])
            with self.upstream(fixture["new_receipts"]), mock.patch.object(
                compiler.stage_package, "verify", return_value=({}, ["input receipt is stale"]),
            ):
                code, output, errors = self.helpers.run_in_process(*self.arguments(fixture))
            self.assertEqual(code, 1, output + errors)
            self.assertIn("stale", output + errors)
            self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    def test_review_phase_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            with self.upstream(fixture["new_receipts"]):
                self.successful(
                    "enter-review", "--experience-root", fixture["root"] / "experiences/checkout",
                )
            self.assert_rejected_unchanged(fixture)

    def test_unopened_update_package_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_rejected_unchanged(self.fixture(temporary, open_returns=False))

    def test_dependent_owner_outside_update_scope_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary, returns_action="reuse")
            self.assert_rejected_unchanged(fixture)

    def test_write_failure_after_first_record_restores_entire_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            before = self.helpers.tree_snapshot(fixture["docs"])
            original_write = compiler.atomic_write_bytes
            written = []

            def fail_second_record(path, content, *args, **kwargs):
                if Path(path).resolve() in {p.resolve() for p in fixture["paths"].values()}:
                    if written:
                        raise OSError("injected second record write failure")
                    result = original_write(path, content, *args, **kwargs)
                    written.append(Path(path))
                    return result
                return original_write(path, content, *args, **kwargs)

            with mock.patch.object(compiler, "atomic_write_bytes", side_effect=fail_second_record):
                code, output, errors = self.revise(fixture)
            self.assertEqual(len(written), 1)
            self.assertEqual(code, 2, output + errors)
            self.assertIn("injected second record write failure", output + errors)
            self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)


class ExperienceRecordRevisionHookTests(unittest.TestCase):
    def test_only_exact_canonical_root_writer_is_sanctioned(self):
        hook = hook_fixtures.load_hook()
        helpers = hook_fixtures.VaultHookShellContractTests()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs, _config = helpers.project(project)
            root = docs / "experience-design"
            root.mkdir()

            def payload(interpreter=sys.executable, selector="--root", script=fixtures.COMPILER):
                argv = [
                    interpreter, str(script), "revise-records", selector, str(root),
                    "--scope-plan", str(docs / "scope.json"),
                    "--proposal-hash", "sha256:" + "1" * 64,
                    "--record-ref", "checkout:FLW-001@r1",
                ]
                command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
                return helpers.attested_writer_payload(project, command)

            self.assertTrue(hook.sanctioned_application_writer(payload(), docs))
            self.assertFalse(hook.sanctioned_application_writer(payload("python3"), docs))
            self.assertFalse(hook.sanctioned_application_writer(payload(selector="--experience-root"), docs))
            self.assertFalse(hook.sanctioned_application_writer(payload(script=project / "experience_compile.py"), docs))
            manual = {
                "tool_name": "apply_patch", "cwd": str(project),
                "tool_input": {"patch": "*** Begin Patch\n*** Update File: "
                               + str(root / "experiences/checkout/flows/flow-1-flow-set.md")
                               + "\n@@\n-revision: 1\n+revision: 2\n*** End Patch"},
            }
            self.assertFalse(hook.sanctioned_application_writer(manual, docs))
            errors = io.StringIO()
            with redirect_stderr(errors):
                code = hook.pre_target({
                    "file_path": str(root / "experiences/checkout/flows/flow-1-flow-set.md"),
                    "old_string": "revision: 1", "new_string": "revision: 2",
                })
            self.assertEqual(code, 2)
            self.assertIn("machine-managed", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
