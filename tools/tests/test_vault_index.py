"""SQLite source coverage, incremental namespace updates and publication."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import context_catalog
import impact_closure
import project_context
import vault_index
import vault_query
from tools.tests.test_impact_closure import VAULT, note


class VaultIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.docs = self.project / "workspace/docs"
        self.docs.mkdir(parents=True)
        self.stores = []
        self.addCleanup(lambda: [store.close() for store in self.stores])

    def write(self, path, text):
        target = self.docs / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def load(self, **kwargs):
        data, status = vault_query.locked_refresh(self.docs, vault_query.default_cache(self.docs), **kwargs)
        self.stores.append(data.store)
        return data, status

    def query(self, *args, expected=0):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = vault_query.main(["--docs", str(self.docs), *args])
        self.assertEqual(code, expected, out.getvalue())
        return json.loads(out.getvalue())

    def seed(self):
        for path, text in VAULT.items():
            self.write(path, text)

    def assert_graph(self, data):
        vault = impact_closure.load_vault(self.docs)
        snapshot = impact_closure.snapshot(vault)
        self.assertEqual({(s, t, k): set(tiers) for s, t, k, tiers in data["edges"]}, snapshot["edges"])
        self.assertEqual(data["citers"], snapshot["citers"])
        self.assertEqual(sorted(data["gaps"], key=vault_index.encoded), sorted(snapshot["gaps"], key=vault_index.encoded))

    def test_scope_prunes_every_artifact_tree_and_preserves_receipts(self):
        self.write("requirements/a.md", note("requirement", "A"))
        self.write("requirements/artifacts/deep/a.md", "not valid frontmatter")
        self.write("requirements/deep/artifacts/a.json", "invalid JSON")
        self.write("artifacts/a.json", "invalid JSON")
        self.write("requirements/artifacts-guide.md", note("requirement", "Guide"))
        self.write("api/catalog.json", json.dumps({"label": "Source facts"}))
        self.write("requirements/image.png", "not indexed")
        self.write(".obsidian/settings.json", "invalid")
        data, _ = self.load()
        self.assertEqual(set(data["files"]), {"requirements/a.md", "requirements/artifacts-guide.md", "api/catalog.json"})
        self.assertEqual(set(data["catalog"]["documents"]), set(data["files"]))
        self.assertFalse((vault_query.default_cache(self.docs).with_name("index.json")).exists())
        self.assertFalse(vault_query.default_cache(self.docs).with_name("index-notes").exists())

    def test_empty_and_missing_vault_have_distinct_states(self):
        result = self.query("index", "ensure")
        self.assertEqual(result["status"], "ready_empty")
        self.assertTrue(vault_query.default_cache(self.docs).is_file())
        self.docs.rmdir()
        result = self.query("index", "ensure", expected=1)
        self.assertEqual(result["status"], "needs_setup")

    def test_index_receipt_discovery_never_globs_excluded_artifacts(self):
        self.write("requirements/a.md", note("requirement", "A"))
        self.write("experience-design/artifacts/deep/application-revisions.json", "invalid")
        self.write("business-analysis/artifacts/_generated/registry.json", "invalid")
        self.write("experience-design/application-revisions.json", json.dumps({"revisions": []}))
        self.write("experience-design/experiences/sample/_ledger/records/group/r1.json",
                   json.dumps({"exact_ref": "sample:REC-001@r1", "content": "Source."}))
        with mock.patch.object(Path, "glob", side_effect=AssertionError("index discovery must use its eligible inventory")):
            data, _ = self.load()
        self.assertEqual(len(context_catalog.resolve(data["catalog"], "sample:REC-001@r1")), 1)
        self.assertEqual(set(data["files"]), {"requirements/a.md", "experience-design/application-revisions.json",
            "experience-design/experiences/sample/_ledger/records/group/r1.json"})

    def test_catalog_parses_only_changed_note_and_matches_fresh_graph(self):
        self.seed()
        data, _ = self.load()
        self.assert_graph(data)
        data.store.close()
        self.write("backlog/story-b.md", note("story", "Story B", {"implements": [("requirements/req-a", "Requirement")]}, body="Updated source."))
        with mock.patch.object(context_catalog, "catalog", wraps=context_catalog.catalog) as parser:
            current, state = self.load()
        self.assertEqual(state["changed"], ["backlog/story-b.md"])
        self.assertEqual(parser.call_count, 1)
        self.assert_graph(current)

    def test_added_and_conflicting_aliases_rebind_existing_unresolved_source(self):
        self.write("backlog/source.md", note("story", "Source", extra="scenario_refs:\n  - ST-001-TS-001"))
        data, _ = self.load()
        self.assertTrue([g for g in data["gaps"] if g.get("key") == "scenario_refs"])
        data.store.close()
        self.write("backlog/plan.md", note("test-plan", "Plan", body="## ST-001-TS-001\n\nScenario."))
        data, _ = self.load()
        self.assertFalse([g for g in data["gaps"] if g.get("key") == "scenario_refs"])
        self.assert_graph(data)
        data.store.close()
        self.write("backlog/other-plan.md", note("test-plan", "Other", body="## ST-001-TS-001\n\nOther scenario."))
        data, _ = self.load()
        self.assertTrue([g for g in data["gaps"] if g.get("key") == "scenario_refs"])
        self.assertEqual(len(context_catalog.resolve(data["catalog"], "ST-001-TS-001")), 2)
        self.assert_graph(data)

    def test_delete_and_move_remove_addresses_and_search_rows(self):
        path = self.write("requirements/a.md", note("requirement", "A", body="Distinct source phrase."))
        data, _ = self.load()
        data.store.close()
        target = self.docs / "requirements/b.md"
        path.rename(target)
        data, status = self.load()
        self.assertEqual(status["removed"], ["requirements/a.md"])
        self.assertEqual(status["changed"], ["requirements/b.md"])
        hits = self.query("search-sections", "Distinct")["hits"]
        self.assertTrue(hits)
        self.assertEqual({row["path"] for row in hits}, {"requirements/b.md"})

    def test_index_check_is_readonly_and_detects_missing_fts(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        before = {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        self.assertTrue(self.query("index", "check")["verified"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()})
        with sqlite3.connect(vault_query.default_cache(self.docs)) as connection:
            connection.execute("DELETE FROM search_units")
        self.assertEqual(self.query("index", "check", expected=1)["status"], "incomplete")
        self.query("index", "rebuild")
        self.assertEqual(self.query("index", "check")["status"], "ready")

    def test_failed_rebuild_keeps_published_generation_and_reader_snapshot(self):
        path = self.write("requirements/a.md", note("requirement", "A", body="Original source."))
        data, first = self.load()
        original = data["catalog"]["documents"]["requirements/a.md"]
        path.write_text("---\ntype: [broken\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load(rebuild=True)
        self.assertEqual(data.store.meta("generation"), first["generation"])
        self.assertEqual(data["catalog"]["documents"]["requirements/a.md"], original)
        path.write_text(note("requirement", "A", body="New source."), encoding="utf-8")
        current, second = self.load()
        self.assertGreater(second["generation"], first["generation"])
        self.assertEqual(data["catalog"]["documents"]["requirements/a.md"], original)
        self.assertNotEqual(current["catalog"]["documents"]["requirements/a.md"], original)

    def test_no_cache_does_not_write_database_or_reading_state(self):
        self.write("requirements/a.md", note("requirement", "A", body="## Conditions\n\n" + "Keep 🙂. " * 80))
        data = project_context.load_index(self.project, no_cache=True)
        self.stores.append(data.store)
        plan = project_context.resolve_context(self.project, data, entry="requirement", role="business-analyst",
            refs=["requirements/a.md"], budget={"max_source_bytes": 100}, persist_state=False)
        while plan["status"] != "ready":
            project_context.read_plan(self.project, data, plan)
            plan = project_context.expand_context(self.project, data, plan, reason="Read remaining source", persist_state=False)
        self.assertFalse((self.project / ".agentrof").exists())

    def test_checkout_bindings_refuse_foreign_database(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        with sqlite3.connect(cache) as connection:
            connection.execute("UPDATE meta SET payload=? WHERE key='docs'", (json.dumps("different checkout"),))
        with self.assertRaisesRegex(ValueError, "another checkout"):
            self.load()

    def test_source_changes_during_indexing_do_not_publish(self):
        path = self.write("requirements/a.md", note("requirement", "A"))
        data, initial = self.load()
        data.store.close()
        path.write_text(note("requirement", "A", body="Updated."), encoding="utf-8")
        actual = context_catalog.catalog
        def racing(*args, **kwargs):
            result = actual(*args, **kwargs)
            path.write_text(note("requirement", "A", body="Changed during build."), encoding="utf-8")
            return result
        with mock.patch.object(context_catalog, "catalog", side_effect=racing), self.assertRaisesRegex(ValueError, "stale source|changed during indexing"):
            self.load()
        self.assertEqual(self.query("index", "status")["generation"], initial["generation"])

    def test_excluded_artifact_change_refreshes_owner_proof_without_index_entry(self):
        self.write("requirements/a.md", note("requirement", "A"))
        artifact = self.write("requirements/artifacts/source.bin", "Approved bytes")
        def proof(_root, _notes):
            return {"requirements/a.md": {"approval_hash": "sha256:synthetic-owner-binding",
                    "scheme": "synthetic-owner", "proven": artifact.read_text() == "Approved bytes"}}
        with mock.patch.object(impact_closure, "proofs_for", side_effect=proof) as owner:
            self.assertTrue(self.query("hash", "requirements/a.md")["proven_unchanged"])
            artifact.write_text("Changed bytes")
            result = self.query("hash", "requirements/a.md")
            self.assertFalse(result["proven_unchanged"])
            self.assertEqual(result["cache"]["changed"], [])
            self.assertEqual(owner.call_count, 2)
        data, _ = self.load()
        self.assertNotIn("requirements/artifacts/source.bin", data["files"])

    def test_generic_json_does_not_promote_receipt_looking_fields(self):
        self.write("api/source.json", json.dumps({"exact_ref": "ARC:ROOT:CON-001@r1", "status": "approved", "value": "Source"}))
        data, _ = self.load()
        self.assertEqual(context_catalog.resolve(data["catalog"], "ARC:ROOT:CON-001@r1"), [])
        self.assertEqual(context_catalog.resolve(data["catalog"], "api/source.json")[0]["kind"], "json")


if __name__ == "__main__":
    unittest.main()
