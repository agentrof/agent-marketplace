"""Schema compatibility accepts only a replayed committed approval postimage."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.tests.levels import integration

SCRIPTS = Path(__file__).resolve().parents[2] / "plugins/software-engineering-team/scripts"
sys.path.insert(0, str(SCRIPTS))
import backlog_compile as backlog
import backlog_migration as migration
import delivery_compile as delivery

COMMIT = "a" * 40
PLAN = "backlog/epics/example/stories/example/test-plan.md"
ROOT = "backlog/backlog.md"


def stamped(props, body):
    text = backlog.front_matter({**props, "approved_at_utc": "2026-10-01T00:00:00+00:00"}, body)
    return backlog.front_matter({**props, "approved_at_utc": "2026-10-01T00:00:00+00:00",
                                "source_hash": backlog.digest_text(text)}, body).encode("utf-8")


def sources():
    result = {PLAN: stamped({"type": "test-plan", "status": "approved"},
                            "# Test Plan\n\n## EXAMPLE-01-TS-001\n\n"
                            "- level: unit (boundary explanation)\n- automation: required\n"
                            "- automation_target: tests/example.py:test_boundary\n"
                            "- Given: an input\n- When: evaluated\n- Then: accepted\n"),
              ROOT: stamped({"type": "backlog", "status": "approved", "revision": 1,
                             "package_hash": "pending"}, "# Backlog\n"),
              "backlog/reviews/round-1-backlog-review.md": stamped(
                  {"type": "backlog-review", "status": "approved"}, "# Review\nKept evidence.\n")}
    result[ROOT] = migration.replace_stamp(result[ROOT].decode(), "package_hash",
                                           migration.package_hash(result)).encode()
    return result


class BacklogMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name) / "workspace/docs"
        self.docs.mkdir(parents=True)
        self.before = sources()
        self.receipt, self.after = migration.migration_plan(
            self.before, COMMIT, "2026.10.1", "2026.10.2")

    def write(self, files):
        for name, data in files.items():
            path = self.docs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_only_level_lines_and_hash_stamps_change(self):
        original, updated = self.before[PLAN].decode(), self.after[PLAN].decode()
        self.assertIn("- level: unit\n- level_reason: unit (boundary explanation)", updated)
        restored = updated.replace("- level: unit\n- level_reason: unit (boundary explanation)",
                                   "- level: unit (boundary explanation)")
        old = backlog.parse_front_matter_text(original)[0]["source_hash"]
        self.assertEqual(migration.replace_stamp(restored, "source_hash", old), original)
        self.assertEqual(self.before["backlog/reviews/round-1-backlog-review.md"],
                         self.after["backlog/reviews/round-1-backlog-review.md"])
        root_props = backlog.parse_front_matter_text(self.after[ROOT].decode())[0]
        self.assertEqual(root_props["revision"], 1)
        self.assertEqual(root_props["approved_at_utc"], "2026-10-01T00:00:00+00:00")
        self.assertEqual(root_props["package_hash"], migration.package_hash(self.after))
        self.assertEqual(self.receipt["files"], sorted(self.receipt["files"], key=lambda row: row["path"]))

    def test_ambiguous_and_already_split_legacy_values_refuse(self):
        original = self.before[PLAN].decode()
        for value, extra in (("boundary tests", ""), ("unit", "- level: fixture (reason)\n"),
                             ("unit (reason)", "- level_reason: existing\n"),
                             ("unit: ", ""), ("fixtureish reason", ""), ("unit or live", ""), ("unit (", "")):
            with self.subTest(value=value, extra=extra):
                text = original.replace("- level: unit (boundary explanation)",
                                        "- level: " + value + "\n" + extra)
                with self.assertRaisesRegex(ValueError, "unambiguous|duplicate|split"):
                    migration.migrate_test_plan(text.encode())

    def test_pure_enum_and_absent_level_keep_exact_bytes(self):
        for replacement in ("- level: unit", ""):
            with self.subTest(replacement=replacement):
                data = self.before[PLAN].replace(b"- level: unit (boundary explanation)",
                                               replacement.encode())
                self.assertEqual(migration.migrate_test_plan(data), data)

    def test_known_enum_prefixes_preserve_the_whole_original_value(self):
        for level in backlog.LEVELS:
            for delimiter in (" (", ": ", " - "):
                with self.subTest(level=level, delimiter=delimiter):
                    value = level + delimiter + "non-English explanation: \u00e7\u00f6z\u00fcm" + (")" if delimiter == " (" else "")
                    data = self.before[PLAN].replace(b"unit (boundary explanation)", value.encode())
                    updated = migration.migrate_test_plan(data).decode()
                    self.assertIn("- level: " + level + "\n- level_reason: " + value, updated)

    def test_noop_downgrade_and_broken_approval_refuse(self):
        for change, old, new, message in (
                ({PLAN: self.after[PLAN]}, "2026.10.1", "2026.10.2", "intact approved"),
                ({}, "2026.10.3", "2026.10.2", "downgrade"),
                ({}, "unknown", "2026.10.2", "SemVer")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    migration.migration_plan({**self.before, **change}, COMMIT, old, new)
        with self.assertRaisesRegex(ValueError, "no legacy"):
            migration.migration_plan(self.after, COMMIT, "2026.10.1", "2026.10.2")

    def test_replay_refuses_any_receipt_field_tamper_and_content_edit(self):
        self.write(self.after)
        with mock.patch.object(migration, "approved_sources", return_value=(COMMIT, self.before)):
            self.assertEqual(migration.replay_receipt(self.docs, self.receipt), self.receipt)
            for key, value in (("owner_approval", "sha256:" + "0" * 64), ("migration", "unknown"),
                               ("after_package_hash", "sha256:" + "1" * 64),
                               ("unexpected", "data"), ("from_version", "2026.9.1"),
                               ("from_version", None), ("source_commit", []),
                               ("source_commit", "HEAD"), ("files", {}), ("schema_version", True)):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    migration.replay_receipt(self.docs, {**self.receipt, key: value})
            (self.docs / PLAN).write_bytes(self.after[PLAN] + b"Unapproved content.\n")
            with self.assertRaisesRegex(ValueError, "unrelated or concurrent"):
                migration.replay_receipt(self.docs, self.receipt)

    def test_inventory_refuses_addition_removal_and_unrelated_changes(self):
        for operation in ("add", "remove", "edit"):
            with self.subTest(operation=operation):
                self.write(self.after)
                extra = self.docs / "backlog/epics/example/stories/extra/story.md"
                if operation == "add":
                    extra.parent.mkdir(parents=True, exist_ok=True)
                    extra.write_bytes(b"extra")
                elif operation == "remove":
                    (self.docs / PLAN).unlink()
                else:
                    (self.docs / PLAN).write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "inventory|unrelated"):
                    migration.verify_inventory(self.docs, self.before, self.after)
                if extra.exists():
                    extra.unlink()

    def test_exact_partial_postimages_resume_but_tampered_ones_refuse(self):
        self.write(self.before)
        (self.docs / PLAN).write_bytes(self.after[PLAN])
        migration.verify_inventory(self.docs, self.before, self.after, resumable=True)
        with self.assertRaisesRegex(ValueError, "unrelated"):
            migration.verify_inventory(self.docs, self.before, self.after)
        (self.docs / PLAN).write_bytes(self.after[PLAN] + b"extra")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            migration.verify_inventory(self.docs, self.before, self.after, resumable=True)

    def test_delivery_accepts_only_proven_test_plan_and_package_aliases(self):
        source = {"story_id": "EXAMPLE-01", "story_path": "backlog/story.md",
                  "story_source_hash": "story", "test_plan_path": PLAN,
                  "test_plan_source_hash": "new", "owner_role": "backend_developer",
                  "supporting_roles": [], "work_kind": "feature", "depends_on": []}
        item = {**source, "test_plan_source_hash": "old",
                "derives_from": [delivery.link("backlog/story", "EXAMPLE-01")]}
        props = {"status": "review", "backlog_path": ROOT, "backlog_package_hash": "old-package"}
        snapshot = {"backlog_path": ROOT, "backlog_package_hash": "new-package"}
        dod = {key: "unchanged" for key in delivery.DOD_SOURCE_FIELDS}
        props.update(dod)
        with mock.patch.object(delivery, "delivery_source_snapshots",
                               return_value=([(self.docs / "item.md", item)],
                                             {"EXAMPLE-01": source}, snapshot, dod, [])), \
                mock.patch.object(migration, "compatible_pins", return_value={PLAN: ("old", "new")}):
            self.assertEqual(delivery.delivery_source_findings(self.docs, self.docs, props)[1], [])
            for key, value in (("test_plan_source_hash", "foreign"), ("story_source_hash", "foreign"),
                               ("owner_role", "other"), ("test_plan_path", "other.md")):
                with self.subTest(key=key):
                    old = item[key]
                    item[key] = value
                    self.assertTrue(delivery.delivery_source_findings(self.docs, self.docs, props)[1])
                    item[key] = old
        with mock.patch.object(delivery, "delivery_source_snapshots",
                               return_value=([(self.docs / "item.md", item)],
                                             {"EXAMPLE-01": source}, snapshot, dod, [])), \
                mock.patch.object(migration, "compatible_pins", side_effect=ValueError("tampered")):
            errors = delivery.delivery_source_findings(self.docs, self.docs, props)[1]
            self.assertTrue(any("receipt is invalid" in error for error in errors))
            self.assertTrue(any("backlog_package_hash is stale" in error for error in errors))

    def test_receipt_binding_requires_exact_old_and_new_package_hashes(self):
        self.write(self.after)
        directory = self.docs / migration.RECEIPTS
        directory.mkdir(parents=True)
        (directory / "receipt.json").write_bytes(migration.encoded(self.receipt))
        old, new = self.receipt["before_package_hash"], self.receipt["after_package_hash"]
        with mock.patch.object(migration, "approved_sources", return_value=(COMMIT, self.before)):
            aliases = migration.compatible_pins(self.docs, old, new)
            self.assertEqual(aliases[PLAN], (backlog.digest_text(self.before[PLAN].decode()),
                                           backlog.digest_text(self.after[PLAN].decode())))
            self.assertEqual(migration.compatible_pins(self.docs, "foreign", new), {})
            self.assertEqual(migration.compatible_pins(self.docs, old, "foreign"), {})
            (directory / "duplicate.json").write_bytes(migration.encoded(self.receipt))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                migration.compatible_pins(self.docs, old, new)

    def test_default_policy_refuses_migration_and_invalid_policy_propagates(self):
        import process_policy
        with mock.patch.object(process_policy, "effective_values",
                               return_value=({migration.SWITCH: {"value": "reviewed_revision"}}, {})):
            with self.assertRaisesRegex(ValueError, "receipt_only"):
                migration.require_opt_in(self.docs)
        with mock.patch.object(process_policy, "effective_values", side_effect=ValueError("draft policy")):
            with self.assertRaisesRegex(ValueError, "draft policy"):
                migration.require_opt_in(self.docs)
        with mock.patch.object(process_policy, "effective_values",
                               return_value=({migration.SWITCH: {"value": "receipt_only"}}, {})):
            migration.require_opt_in(self.docs)

    def test_apply_approval_and_post_write_refusal_leave_canonical_sources_exact(self):
        self.write(self.before)
        args = SimpleNamespace(docs=str(self.docs), command="apply-schema-migration",
                               source_commit=COMMIT, from_version="2026.10.1", approve_receipt="wrong")
        import setup_project
        with mock.patch.object(migration, "require_opt_in"), \
                mock.patch.object(migration, "approved_sources", return_value=(COMMIT, self.before)), \
                mock.patch.object(migration, "installed_version", return_value="2026.10.2"), \
                mock.patch.object(backlog, "history_project", return_value=self.docs.parent.parent), \
                mock.patch.object(setup_project, "refresh_guard", return_value=contextlib.nullcontext()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(migration.command(args), 1)
            args.approve_receipt = self.receipt["owner_approval"]
            with mock.patch.object(backlog, "collect", return_value=({"backlog": None}, ["injected refusal"])):
                self.assertEqual(migration.command(args), 1)
        self.assertEqual({name: (self.docs / name).read_bytes() for name in self.before}, self.before)
        self.assertFalse(list((self.docs / migration.RECEIPTS).glob("*.json")))


@integration
class BacklogMigrationSmokeTests(unittest.TestCase):
    def test_committed_legacy_backlog_migrates_and_existing_execution_approval_stays_exact(self):
        from tools.tests.test_delivery_compile import DeliveryCompilerTests
        fixture = DeliveryCompilerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        docs = fixture.docs
        plan = next((docs / "backlog").rglob("test-plan.md"))
        original = plan.read_text()
        legacy = "unit (historical explanation)"
        plan.write_bytes(original.replace("- automation: required", "- level: " + legacy + "\n- automation: required").encode())
        text = plan.read_text()
        plan.write_bytes(migration.replace_stamp(text, "source_hash", backlog.digest_text(text)).encode())
        with mock.patch.object(backlog, "LEVELS", (*backlog.LEVELS, legacy)), \
                contextlib.redirect_stdout(io.StringIO()):
            record, errors = backlog.collect(docs, historical_inputs=True)
            self.assertEqual(errors, [])
            root = docs / ROOT
            text = root.read_text()
            root.write_bytes(migration.replace_stamp(text, "package_hash",
                                                      backlog.package_digest(docs, backlog.package_paths(record, docs))).encode())
            fixture.approve_verification_contract()
            fixture.approve_dod()
            args = SimpleNamespace(docs=str(docs), id=None, slug="schema", goal="Schema compatibility",
                                   outcome="Preserve execution approval", target_branch="main", story=["AUTH-01"])
            self.assertEqual(delivery.init_delivery(args), 0)
            approval = SimpleNamespace(docs=str(docs), delivery="DLV-001")
            self.assertEqual(delivery.approve_scope(approval), 0)
            delivery_root = delivery.find_delivery(docs, "DLV-001")
            item = next(delivery_root.glob("items/*/item.md"))
            props, body = delivery.split_note(item)
            props.update(path_claims=["src/auth.py"], contract_claims=["auth:session"])
            delivery.atomic_text(item, delivery.frontmatter(props, body))
            self.assertEqual(delivery.approve_execution(approval), 0)
        fixture.git("add", "--all")
        fixture.git("commit", "-qm", "Approved legacy backlog and execution")
        preserved = {path: path.read_bytes() for path in delivery_root.rglob("*") if path.is_file()}
        reviews = {path: path.read_bytes() for path in (docs / "backlog").rglob("*review.md")}
        import process_policy
        with contextlib.redirect_stdout(io.StringIO()):
            policy_args = SimpleNamespace(docs=str(docs), title=None)
            self.assertEqual(process_policy.init(policy_args), 0)
            self.assertEqual(process_policy.main(["set", "--docs", str(docs), "--switch", migration.SWITCH,
                                                  "--value", "receipt_only"]), 0)
            self.assertEqual(process_policy.approve(policy_args), 0)
        args = SimpleNamespace(docs=str(docs), command="plan-schema-migration", source_commit="HEAD",
                               from_version="2026.10.1")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(migration.command(args), 0)
            receipt = json.loads(output.getvalue())["receipt"]
            args.command = "apply-schema-migration"
            args.approve_receipt = receipt["owner_approval"]
            self.assertEqual(migration.command(args), 0)
            self.assertEqual(migration.command(args), 0)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(delivery.check_delivery(SimpleNamespace(
                docs=str(docs), delivery="DLV-001")), 0,
                output.getvalue())
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        self.assertEqual({path: path.read_bytes() for path in reviews}, reviews)
        self.assertIn(b"- level: unit\n- level_reason: " + legacy.encode(), plan.read_bytes())
        receipt_path = next((docs / migration.RECEIPTS).glob("*.json"))
        migration.replay_receipt(docs, json.loads(receipt_path.read_text()))
        receipt_path.unlink()
        self.assertTrue(delivery.delivery_source_findings(docs, delivery_root,
                       delivery.split_note(delivery_root / "delivery.md")[0])[1])
