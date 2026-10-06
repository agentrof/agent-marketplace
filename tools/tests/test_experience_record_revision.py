"""Regression contract for atomic revision of an Experience record closure."""

import copy
import json
import io
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tools.tests import test_experience_compile as fixtures
from tools.tests import test_vault_hook as hook_fixtures


compiler = fixtures.experience_compile
application = fixtures.experience_application_check


def binding_rows(*spaces):
    rows = [f"business-analysis|business-analysis/{space}/space|sha256:{'1' * 64}"
            for space in spaces]
    rows.append(f"solution-design|solution-design/space|sha256:{'2' * 64}")
    rows.append(f"design-system|design-system/space|sha256:{'3' * 64}")
    return rows


def process_ref(owner):
    return f"business-analysis/{owner}/processes/{owner}"


def flow_path(number):
    return f"flows/flow-{number}-flow-set.md"


def flow_owner(number):
    return "checkout" if number <= 23 else "returns"


def synthetic_world(*, returns_action="update"):
    """The record set the full-vault fixture builds, as compiler-read rows.

    Twelve seeds, twenty-one reverse dependents and a cycle through FLW-001.
    """
    owner_rows = {"checkout": [], "returns": []}
    for number in range(1, 34):
        owner = flow_owner(number)
        row = {
            "type": "flow-set", "title": f"Flow {number}", "id": f"FLW-{number:03d}",
            "revision": 1, "record_state": "active",
            "derives_from": [process_ref(owner)],
        }
        if number == 1:
            row["flow_refs"] = ["checkout:FLW-023@r1"]
        elif number > 12:
            predecessor = 1 if number == 13 else number - 1
            reference = f"{flow_owner(predecessor)}:FLW-{predecessor:03d}@r1"
            if number % 2:
                row["related_to"] = [
                    f"[[experience-design/experiences/{flow_owner(predecessor)}/"
                    f"flows/flow-{predecessor}-flow-set|{reference}]]"
                ]
            else:
                row["flow_refs"] = [reference]
        owner_rows[owner].append({**row, "path": flow_path(number)})
    plan = {
        "schema_version": 2, "origin_mode": "manual",
        "input_bindings": binding_rows("checkout", "returns"),
        "actions": [
            {
                "primary_process_ref": process_ref(owner), "experience": owner,
                "target_experience": "",
                "action": "update" if owner == "checkout" else returns_action,
                "affected_records": [row["id"] for row in owner_rows[owner]],
                "expected_package": {
                    "status": "approved", "revision": 1,
                    "source_hash": "sha256:" + "4" * 64,
                },
                "reason": "Revise exact records.",
            }
            for owner in ("checkout", "returns")
        ],
        "application_action": "update",
        "expected_application": {
            "exists": True, "status": "approved", "revision": 1,
            "artifact_tree_hash": "sha256:" + "5" * 64,
            "package_set_hash": "sha256:" + "6" * 64,
            "application_hash": "sha256:" + "7" * 64,
        },
    }
    plan["proposal_hash"] = compiler.proposal_digest(plan)
    opened = {action["experience"] for action in plan["actions"]
              if action["action"] == "update"}
    predecessors = {
        owner: {row["id"]: {key: value for key, value in row.items()}
                for row in rows}
        for owner, rows in owner_rows.items() if owner in opened
    }
    return {"plan": plan, "owner_rows": owner_rows, "predecessors": predecessors}


def row_for(world, reference):
    owner, _separator, rest = reference.partition(":")
    ident = rest.partition("@")[0]
    return next(row for row in world["owner_rows"][owner] if row["id"] == ident)


def decide(world, requested):
    actions = compiler.record_revision_actions(world["plan"], requested)
    return compiler.record_revision_closure(
        requested, actions, world["predecessors"], world["owner_rows"],
    )


class RecordRevisionDecisionTests(unittest.TestCase):
    """The revise-records rules, decided on the fixture's record set in memory."""

    def assert_refused_unchanged(self, world, requested, message):
        before = copy.deepcopy(world)
        with self.assertRaisesRegex(ValueError, message):
            decide(world, requested)
        self.assertEqual(world, before)

    def test_twelve_seeds_close_over_thirty_three_records_with_cycle_and_typed_aliases(self):
        world = synthetic_world()
        seeds = [f"checkout:FLW-{number:03d}@r1" for number in range(1, 13)]
        selected, replacements, current = decide(world, seeds)
        everything = {
            f"{owner}:{row['id']}@r1": row
            for owner, rows in world["owner_rows"].items() for row in rows
        }
        self.assertEqual(len(selected), 33)
        self.assertEqual(replacements, {
            ref: ref.replace("@r1", "@r2") for ref in everything
        })
        revised = {}
        for reference, row in everything.items():
            data = {key: value for key, value in row.items() if key != "path"}
            expected = dict(data, revision=2, supersedes=reference)
            for field in compiler.REFERENCE_FIELDS:
                if field in expected:
                    expected[field] = [value.replace("@r1", "@r2") for value in expected[field]]
            updated = compiler.revised_record_data(
                copy.deepcopy(data), 1, reference, replacements,
            )
            self.assertEqual(updated, expected, reference)
            revised[reference] = updated
        for owner, rows in world["owner_rows"].items():
            for index, row in enumerate(rows):
                rows[index] = {**revised[f"{owner}:{row['id']}@r1"], "path": row["path"]}
        again_selected, again, _current = decide(world, seeds)
        self.assertEqual(again, replacements)
        unchanged = [
            reference for reference, row in everything.items()
            if compiler.revised_record_data(
                copy.deepcopy(revised[reference]), 1, reference, again,
            ) != revised[reference]
        ]
        self.assertEqual(unchanged, [])
        self.assertEqual(again_selected, selected)

    def test_malformed_and_duplicate_refs_are_refused(self):
        for refs in (
            ["checkout:FLW-001"], ["checkout:FLW-001@r0"],
            ["checkout:FLW-001@r1", "checkout:FLW-001@r1"],
        ):
            with self.subTest(refs=refs):
                self.assert_refused_unchanged(
                    synthetic_world(), refs,
                    "^record refs must be unique exact predecessor references$",
                )

    def test_unknown_and_stale_refs_are_refused(self):
        for refs, unknown in (
            (["checkout:FLW-999@r1"], "checkout:FLW-999@r1"),
            (["missing:FLW-001@r1"], "missing:FLW-001@r1"),
            (["checkout:FLW-001@r2"], "checkout:FLW-001@r2"),
            (["checkout:FLW-001@r1", "returns:FLW-999@r1"], "returns:FLW-999@r1"),
        ):
            with self.subTest(refs=refs):
                self.assert_refused_unchanged(
                    synthetic_world(), refs,
                    "^records must name active predecessors in open updates: "
                    + re.escape(unknown) + "$",
                )

    def test_wrong_proposal_is_refused(self):
        plan = synthetic_world()["plan"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "revision-scope.json"
            path.write_bytes(compiler.canonical(plan))
            self.assertEqual(
                compiler.load_scope_plan(str(path), plan["proposal_hash"]), plan,
            )
            with self.assertRaisesRegex(
                ValueError, "^scope plan hash does not match the approved proposal$",
            ):
                compiler.load_scope_plan(str(path), "sha256:" + "0" * 64)

    def test_retired_child_reference_rejects_same_and_cross_package(self):
        for owner in ("checkout", "returns"):
            with self.subTest(owner=owner):
                world = synthetic_world()
                world["owner_rows"][owner].append({
                    "type": "flow-set", "title": "Retired flow", "id": "FLW-090",
                    "revision": 1, "record_state": "retired",
                    "flow_refs": ["checkout:FLW-002@r1"],
                    "path": "flows/retired-flow-set.md",
                })
                self.assert_refused_unchanged(
                    world, ["checkout:FLW-002@r1"],
                    f"^retired dependent {owner}:FLW-090 cannot be revised$",
                )

    def test_selected_typed_wikilinks_reject_missing_or_mismatched_targets(self):
        # The alias may name either the revised seed or another unchanged record.
        for field in compiler.REFERENCE_FIELDS:
            for target in (
                "missing/note",
                "experience-design/experiences/checkout/flows/flow-3-flow-set",
            ):
                for alias in ("checkout:FLW-002@r1", "checkout:FLW-001@r1"):
                    with self.subTest(field=field, target=target, alias=alias):
                        world = synthetic_world()
                        row_for(world, "checkout:FLW-002@r1")[field] = [f"[[{target}|{alias}]]"]
                        self.assert_refused_unchanged(
                            world, ["checkout:FLW-002@r1"],
                            "^checkout:FLW-002 typed link target differs from its exact reference$",
                        )

    def test_bare_exact_references_remain_allowed_in_all_typed_fields(self):
        for field in compiler.REFERENCE_FIELDS:
            with self.subTest(field=field):
                world = synthetic_world()
                row = row_for(world, "checkout:FLW-002@r1")
                row[field] = ["checkout:FLW-002@r1"]
                selected, replacements, _current = decide(world, ["checkout:FLW-002@r1"])
                self.assertEqual(selected, {("checkout", "FLW-002")})
                self.assertEqual(
                    replacements, {"checkout:FLW-002@r1": "checkout:FLW-002@r2"},
                )
                data = {key: value for key, value in row.items() if key != "path"}
                updated = compiler.revised_record_data(
                    copy.deepcopy(data), 1, "checkout:FLW-002@r1", replacements,
                )
                self.assertNotEqual(updated, data)
                self.assertEqual(updated[field], ["checkout:FLW-002@r2"])

    def test_missing_record_argument_rejects_at_cli_boundary_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            errors = io.StringIO()
            with redirect_stderr(errors), redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                compiler.main([
                    "revise-records", "--root", str(root),
                    "--scope-plan", str(root / "revision-scope.json"),
                    "--proposal-hash", "sha256:" + "0" * 64,
                ])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--record-ref", errors.getvalue())
            self.assertEqual(list(root.iterdir()), [])

    def predecessors_for(self, world, data, ledger):
        action = world["plan"]["actions"][0]
        return compiler.open_update_predecessors(
            "checkout", world["plan"], action, data, ledger,
        )

    def approved_history(self, world):
        return [{
            "package_revision": 1, "source_hash": "sha256:" + "4" * 64,
            "records": list(world["predecessors"]["checkout"].values()),
        }]

    def test_open_update_reads_its_approved_predecessor_records(self):
        world = synthetic_world()
        data = {"revision": 2, "input_bindings": compiler.package_binding_rows(
            world["plan"], process_ref("checkout"),
        )}
        reads = []

        def ledger(revision):
            reads.append(revision)
            return self.approved_history(world), []

        self.assertEqual(
            self.predecessors_for(world, data, ledger), world["predecessors"]["checkout"],
        )
        self.assertEqual(reads, [2])

    def test_open_input_bindings_drift_rejects_before_reading_history(self):
        world = synthetic_world()
        stale = binding_rows("checkout")
        stale[0] = stale[0].replace("1" * 64, "9" * 64)
        reads = []
        with self.assertRaisesRegex(
            ValueError, "^checkout open input bindings differ from the scope$",
        ):
            self.predecessors_for(
                world, {"revision": 2, "input_bindings": stale},
                lambda revision: reads.append(revision),
            )
        self.assertEqual(reads, [])

    def test_corrupt_predecessor_history_rejects(self):
        world = synthetic_world()
        data = {"revision": 2, "input_bindings": compiler.package_binding_rows(
            world["plan"], process_ref("checkout"),
        )}
        finding = "_ledger/records/FLW-001/r1.json is missing or stale"
        for history, findings in (
            (self.approved_history(world), [finding]), ([], []),
        ):
            with self.subTest(findings=findings):
                with self.assertRaisesRegex(
                    ValueError,
                    "^checkout needs intact predecessor history: "
                    + re.escape(str(findings)) + "$",
                ):
                    self.predecessors_for(world, data, lambda _r: (history, findings))

    def test_application_review_phase_rejects(self):
        plan = synthetic_world()["plan"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / compiler.GENERATED).mkdir()
            compiler.write_open_application_state(
                root, plan, plan["proposal_hash"], phase="in_review",
            )
            before = fixtures.ExperienceCompilerTests.tree_snapshot(root)
            with self.assertRaisesRegex(
                ValueError,
                "^application open revision is not bound to the approved scope-plan action$",
            ):
                compiler.validate_open_application_state(
                    root, plan=plan, proposal_hash=plan["proposal_hash"],
                    expected_phase="draft",
                )
            self.assertEqual(fixtures.ExperienceCompilerTests.tree_snapshot(root), before)

    def test_manually_advanced_child_revision_rejects(self):
        world = synthetic_world()
        row = row_for(world, "checkout:FLW-001@r1")
        row.update(revision=3, supersedes="checkout:FLW-001@r1")
        self.assert_refused_unchanged(
            world, ["checkout:FLW-001@r1"],
            "^checkout:FLW-001 has stale identity or revision$",
        )

    def test_input_drift_rejects_with_findings(self):
        plan = synthetic_world()["plan"]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            compiler.stage_package, "verify", return_value=({}, ["input receipt is stale"]),
        ) as verify:
            findings = compiler.verify_scope_inputs(
                Path(temporary) / "docs", plan, require_committed=True,
            )
        self.assertEqual(findings, ["input receipt is stale"] * 4)
        self.assertEqual(verify.call_count, 4)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(compiler.print_problems(findings, False), 1)
        self.assertIn("stale", output.getvalue())

    def open_package(self, temporary, plan, *, status, opened):
        package = Path(temporary) / "experiences/checkout"
        (package / compiler.GENERATED).mkdir(parents=True)
        (package / "experience.md").write_text(compiler.render_fm({
            "experience_id": "checkout", "primary_process_ref": process_ref("checkout"),
            "origin_mode": "manual", "status": status, "revision": 2,
        }, "# Checkout\n"), encoding="utf-8")
        if opened:
            compiler.write_open_revision(
                package, plan, plan["actions"][0], plan["proposal_hash"],
            )
        return package

    def assert_open_revision_rejected(self, *, status, opened, message):
        plan = synthetic_world()["plan"]
        with tempfile.TemporaryDirectory() as temporary:
            package = self.open_package(temporary, plan, status=status, opened=opened)
            before = fixtures.ExperienceCompilerTests.tree_snapshot(package)
            with self.assertRaisesRegex(ValueError, message):
                compiler.validate_open_revision(
                    package, plan, plan["actions"][0], plan["proposal_hash"],
                    expected_status="draft",
                )
            self.assertEqual(fixtures.ExperienceCompilerTests.tree_snapshot(package), before)

    def test_open_draft_revision_is_accepted(self):
        plan = synthetic_world()["plan"]
        with tempfile.TemporaryDirectory() as temporary:
            package = self.open_package(temporary, plan, status="draft", opened=True)
            self.assertIsNone(compiler.validate_open_revision(
                package, plan, plan["actions"][0], plan["proposal_hash"],
                expected_status="draft",
            ))

    def test_review_phase_rejects(self):
        self.assert_open_revision_rejected(
            status="in_review", opened=True,
            message="^checkout lifecycle identity, phase or successor revision drifted after opening$",
        )

    def test_unopened_update_package_rejects(self):
        self.assert_open_revision_rejected(
            status="draft", opened=False,
            message="^checkout is missing compiler-owned open revision state$",
        )

    def test_dependent_owner_outside_update_scope_rejects(self):
        self.assert_refused_unchanged(
            synthetic_world(returns_action="reuse"), ["checkout:FLW-001@r1"],
            "^dependent returns:FLW-024 is outside the open update scope$",
        )


class ExperienceRecordRevisionTests(unittest.TestCase):
    """Full-vault smokes: the success path, one refusal and the write rollback."""

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

    def fixture(self, temporary):
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
            plan_path = fixture["docs"] / "revision-scope.json"
            plan_path.write_bytes(compiler.canonical(plan))
            for owner in ("checkout", "returns"):
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

    def assert_rejected_unchanged(self, fixture, refs=None, message=None):
        before = self.helpers.tree_snapshot(fixture["docs"])
        code, output, errors = self.revise(fixture, refs)
        self.assertEqual(code, 2, output + errors)
        self.assertTrue(output or errors)
        if message is not None:
            self.assertIn(message, output + errors)
        self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    @integration
    def test_input_drift_rejects_with_its_findings_before_reading_the_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            before = self.helpers.tree_snapshot(fixture["docs"])
            with self.upstream(fixture["new_receipts"]), mock.patch.object(
                compiler.stage_package, "verify", return_value=({}, ["input receipt is stale"]),
            ):
                code, output, errors = self.helpers.run_in_process(*self.arguments(fixture))
            self.assertEqual(code, 1, output + errors)
            lines = (output + errors).splitlines()
            self.assertTrue(lines)
            self.assertEqual(set(lines), {"ERROR input receipt is stale"})
            self.assertEqual(self.helpers.tree_snapshot(fixture["docs"]), before)

    @integration
    def test_application_review_phase_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            compiler.write_open_application_state(
                fixture["root"], fixture["plan"], fixture["plan"]["proposal_hash"],
                phase="in_review",
            )
            self.assert_rejected_unchanged(fixture)

    @integration
    def test_review_phase_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            with self.upstream(fixture["new_receipts"]):
                self.successful(
                    "enter-review", "--experience-root", fixture["root"] / "experiences/checkout",
                )
            self.assert_rejected_unchanged(fixture)

    @integration
    def test_an_owner_with_invalid_records_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            source = fixture["root"] / "experiences/returns/flows/flow-24-flow-set.md"
            duplicate = source.with_name("flow-99-flow-set.md")
            duplicate.write_bytes(source.read_bytes())
            self.assert_rejected_unchanged(
                fixture, message="returns invalid records: flows/flow-99-flow-set.md: duplicate package record id FLW-024")

    def test_twelve_seeds_revise_thirty_three_with_cycle_and_typed_aliases(self):
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

    def test_corrupt_predecessor_snapshot_rejects_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            path = fixture["root"] / "experiences/checkout/_ledger/records/FLW-001/r1.json"
            data = json.loads(path.read_bytes())
            data["title"] = "Tampered title"
            path.write_bytes(compiler.canonical(data))
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
