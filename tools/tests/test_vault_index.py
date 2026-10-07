"""SQLite source coverage, incremental namespace updates and publication."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import hashlib
import gc
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import context_catalog
import file_lock
import impact_closure
import project_context
import vault_index
import vault_query
from tools.tests.test_impact_closure import VAULT, note
from tools.tests.levels import integration


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
        self.assertEqual(data["gaps"], snapshot["gaps"])

    def assert_fresh(self, data):
        fresh, _ = vault_index.refresh(self.docs, vault_query.default_cache(self.docs), vault_query.builder_hash(),
                                       persist=False, rebuild=True)
        try:
            self.assertEqual(vault_index.projection_mismatches(data.store, fresh.store), [])
        finally:
            fresh.store.close()

    def other_project(self, name):
        docs = Path(self.temp.name).resolve() / name / "workspace/docs"
        (docs / "requirements").mkdir(parents=True)
        (docs / "requirements/a.md").write_text(note("requirement", "A"), encoding="utf-8")
        return docs

    def run_cli(self, docs, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = vault_query.main(["--docs", str(docs), *args])
        return code, out.getvalue(), err.getvalue()

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
        with contextlib.closing(sqlite3.connect(vault_query.default_cache(self.docs))) as connection, connection:
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

    def test_foreign_checkout_binding_is_rebuilt_without_serving_its_rows(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        with contextlib.closing(sqlite3.connect(cache)) as connection, connection:
            connection.execute("UPDATE meta SET payload=? WHERE key='docs'", (json.dumps("different checkout"),))
            connection.execute("INSERT INTO notes VALUES('foreign.md','{}','','')")
        status = self.query("index", "status", expected=1)
        self.assertEqual((status["status"], status["reasons"], status["docs"]), ("incompatible", ["docs"], "different checkout"))
        data, state = self.load()
        self.assertTrue(state["full"])
        self.assertNotIn("foreign.md", data["notes"])
        self.assertEqual(data.store.meta("docs"), str(self.docs))

    def test_moved_checkout_rebuilds_its_own_cache(self):
        docs = self.other_project("one")
        self.assertEqual(self.run_cli(docs, "index", "ensure")[0], 0)
        moved = docs.parents[1].with_name("two")
        docs.parents[1].rename(moved)
        docs = moved / "workspace/docs"
        code, out, err = self.run_cli(docs, "find", "A")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertTrue(result["cache"]["full"])
        self.assertEqual([row["path"] for row in result["notes"]], ["requirements/a.md"])
        self.assertEqual(json.loads(self.run_cli(docs, "index", "rebuild")[1])["status"], "ready")
        data = project_context.load_index(moved)
        self.stores.append(data.store)
        self.assertEqual(data.store.meta("docs"), str(docs))

    def test_link_and_relative_spellings_bind_one_checkout(self):
        docs = self.other_project("real")
        (docs.parents[1] / "src").mkdir()
        alias = docs.parents[2] / "alias"
        try:
            alias.symlink_to(docs.parents[1], target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory links unavailable: {exc}")
        for spelling in (alias / "workspace/docs", docs.parents[1] / "src/../workspace/docs", docs):
            with self.subTest(spelling=str(spelling)):
                code, out, err = self.run_cli(spelling, "find", "A")
                self.assertEqual(code, 0, err)
                data = project_context.load_index(docs.parents[1])
                self.stores.append(data.store)
                self.assertEqual(data.store.meta("docs"), str(docs.resolve()))

    def test_incompatible_table_shapes_are_replaced_by_a_full_rebuild(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        with contextlib.closing(sqlite3.connect(vault_query.default_cache(self.docs))) as connection, connection:
            connection.execute("ALTER TABLE notes ADD COLUMN later TEXT")
            connection.execute("UPDATE meta SET payload='99' WHERE key='schema_version'")
        data, status = self.load()
        self.assertTrue(status["full"])
        self.assertEqual(len(data.store.connection.execute("PRAGMA table_info(notes)").fetchall()), 4)
        self.assert_graph(data)

    def test_full_text_layout_this_sqlite_cannot_open_is_recompiled(self):
        path = self.write("requirements/a.md", note("requirement", "A", body="Distinct phrase."))
        data, _ = self.load()
        data.store.close()
        with contextlib.closing(sqlite3.connect(vault_query.default_cache(self.docs))) as connection, connection:
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='search_text'").fetchone()[0]
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute("UPDATE sqlite_master SET sql=? WHERE name='search_text'",
                               (sql.replace("tokenize='unicode61'", "tokenize='unicode61',future_option=1"),))
        path.write_text(note("requirement", "A", body="Distinct phrase, edited."), encoding="utf-8")
        result = self.query("search-sections", "Distinct")
        self.assertTrue(result["hits"])
        self.assertTrue(result["cache"]["full"])
        self.assertTrue(self.query("index", "check")["verified"])

    def test_database_sidecars_refuse_aliases_before_opening(self):
        source = self.write("requirements/a.md", note("requirement", "A"))
        original = source.read_bytes()
        cache = vault_query.default_cache(self.docs)
        cache.parent.mkdir(parents=True)
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(cache) + suffix)
                os.link(source, sidecar)
                try:
                    with self.assertRaisesRegex(ValueError, "unaliased"):
                        self.load()
                    with self.assertRaisesRegex(ValueError, "unaliased"):
                        vault_index.inspect_index(self.docs, cache, vault_query.builder_hash())
                    self.assertEqual(source.read_bytes(), original)
                    self.assertFalse(cache.exists())
                finally:
                    sidecar.unlink()

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
        import ba_compile
        space = self.docs / "business-analysis/space-a"
        artifact = self.write("business-analysis/space-a/artifacts/evidence.md", "Approved evidence.\n")
        body = "---\ntype: space\ntitle: Space A\npackage_status: approved\npackage_hash: {}\n---\n\n# Space A\n\nContent.\n"
        self.write("business-analysis/space-a/space.md", body.format("pending"))
        self.write("business-analysis/space-a/space.md", body.format(ba_compile.package_hash(space)))
        self.assertTrue(self.query("hash", "business-analysis/space-a/space.md")["proven_unchanged"])
        artifact.write_text("Changed evidence.\n", encoding="utf-8")
        result = self.query("hash", "business-analysis/space-a/space.md")
        self.assertFalse(result["proven_unchanged"])
        self.assertEqual(result["cache"]["changed"], [])
        data, _ = self.load()
        self.assertNotIn("business-analysis/space-a/artifacts/evidence.md", data["files"])

    def test_generic_json_does_not_promote_receipt_looking_fields(self):
        self.write("api/source.json", json.dumps({"exact_ref": "ARC:ROOT:CON-001@r1", "status": "approved", "value": "Source"}))
        data, _ = self.load()
        self.assertEqual(context_catalog.resolve(data["catalog"], "ARC:ROOT:CON-001@r1"), [])
        self.assertEqual(context_catalog.resolve(data["catalog"], "api/source.json")[0]["kind"], "json")

    def test_bound_reference_removal_matches_fresh_graph(self):
        target = self.write("requirements/a.md", note("requirement", "A", extra="aliases:\n  - REQ-001"))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        self.write("backlog/b.md", note("story", "B",
            extra=f'requirement_ref: "REQ-001|requirements/a.md|sha256:{digest}"'))
        data, _ = self.load()
        self.assertTrue(data.store.edges_for("backlog/b.md"))
        data.store.close()
        target.unlink()
        data, status = self.load()
        self.assertEqual(status["removed"], ["requirements/a.md"])
        self.assert_graph(data)
        self.assertTrue([gap for gap in data["gaps"] if gap.get("key") == "requirement_ref"])

    def test_physically_corrupt_cache_is_recompiled_without_losing_capsules(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        capsule = cache.parent / "reading-state" / "keep.json"
        capsule.parent.mkdir()
        capsule.write_bytes(b'{"verified":"retained"}')
        cache.write_bytes(b"not a SQLite database")
        result = self.query("index", "rebuild")
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["cache"]["recovered"])
        self.assertEqual(capsule.read_bytes(), b'{"verified":"retained"}')
        self.assertTrue(self.query("index", "check")["verified"])

    def test_failed_corrupt_recovery_preserves_the_previous_files(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        cache.write_bytes(b"not a SQLite database")
        self.write("requirements/b.md", "---\ntype: [malformed\n")
        original = cache.read_bytes()
        with self.assertRaises(ValueError):
            self.load(rebuild=True)
        self.assertEqual(cache.read_bytes(), original)
        self.assertFalse(list(cache.parent.glob(".index-recovery-*")))

    def test_readonly_existing_database_uses_source_derived_memory_fallback(self):
        source = self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        original = cache.read_bytes()
        cache.chmod(0o444)
        source.write_text(note("requirement", "A", body="Changed source."), encoding="utf-8")
        try:
            data = project_context.load_index(self.project)
            self.stores.append(data.store)
            self.assertEqual(data["catalog"]["documents"]["requirements/a.md"]["source_hash"],
                "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(cache.read_bytes(), original)
        finally:
            cache.chmod(0o644)

    def test_index_check_replays_graph_and_search_projection(self):
        self.write("requirements/a.md", note("requirement", "A"))
        self.write("backlog/b.md", note("story", "B", {"implements": [("requirements/a", "A")]}))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        for damage in ("graph", "search"):
            with self.subTest(damage=damage):
                with contextlib.closing(sqlite3.connect(cache)) as connection, connection:
                    if damage == "graph":
                        connection.execute("DELETE FROM edge_facts")
                    else:
                        rowid = connection.execute("SELECT rowid FROM search_units LIMIT 1").fetchone()[0]
                        connection.execute("DELETE FROM search_text WHERE rowid=?", (rowid,))
                        connection.execute("INSERT INTO search_text(rowid,title,body) VALUES(?,?,?)", (rowid, "Incorrect", "Tokens"))
                result = self.query("index", "check", expected=1)
                self.assertEqual(result["status"], "incomplete")
                self.assertFalse(result["verified"])
                self.query("index", "rebuild")
                self.assertTrue(self.query("index", "check")["verified"])

    def test_index_check_coexists_with_a_live_reader_without_cache_writes(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        before = {path: path.read_bytes() for path in vault_query.default_cache(self.docs).parent.rglob("*") if path.is_file()}
        self.assertTrue(self.query("index", "check")["verified"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(data["catalog"]["documents"]["requirements/a.md"]["title"], "A")

    @integration
    def test_recovery_waits_for_process_readers_and_resumes_after_close(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        script = ("import sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import project_context; "
            "root=Path(sys.argv[2]); data=project_context.load_index(root); "
            "(root/'reader-ready').write_text('ready'); sys.stdin.readline(); data.store.close()")
        process = subprocess.Popen([sys.executable, "-c", script, str(ROOT / "plugins/software-engineering-team/scripts"),
            str(self.project)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not (self.project / "reader-ready").exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail("the child reader did not acquire its database lease")
                time.sleep(0.02)
            cache = vault_query.default_cache(self.docs)
            original = cache.read_bytes()
            settings = vault_index.policy()
            with mock.patch.object(vault_index, "policy", return_value=dict(settings, busy_timeout_ms=50)):
                with self.assertRaisesRegex(ValueError, "active readers"):
                    vault_index.recover_database(self.docs, cache, vault_query.builder_hash())
            self.assertEqual(cache.read_bytes(), original)
        finally:
            _out, err = process.communicate(input="\n" if process.poll() is None else None, timeout=15)
        self.assertEqual(process.returncode, 0, err)
        cache.write_bytes(b"not a SQLite database")
        self.assertEqual(self.query("index", "rebuild")["status"], "ready")

    def test_canonical_scope_addition_and_removal_rebind_existing_references(self):
        self.write("backlog/source.md", note("story", "Source", extra="governs:\n  - scope-one"))
        data, _ = self.load()
        self.assertTrue([gap for gap in data["gaps"] if gap.get("key") == "governs"])
        data.store.close()
        target = self.write("business-analysis/scope-one/space.md", note("space", "Scope one"))
        data, _ = self.load()
        self.assert_graph(data)
        self.assertFalse([gap for gap in data["gaps"] if gap.get("key") == "governs"])
        data.store.close()
        target.unlink()
        data, _ = self.load()
        self.assert_graph(data)
        self.assertTrue([gap for gap in data["gaps"] if gap.get("key") == "governs"])

    def test_reader_finalization_releases_lease_during_snapshot_lock(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        self.stores.remove(data.store)
        cache = vault_query.default_cache(self.docs)
        guard = vault_index.DatabaseLease(cache, exclusive=True, name=".snapshot")
        failures = []
        settings = vault_index.policy()
        try:
            with mock.patch.object(sys, "unraisablehook", side_effect=lambda value: failures.append(value.exc_value)), \
                    mock.patch.object(vault_index, "policy", return_value=dict(settings, busy_timeout_ms=25)):
                del data
                gc.collect()
            self.assertEqual(failures, [])
            lease = vault_index.DatabaseLease(cache, exclusive=True, timeout=0.05)
            lease.close()
        finally:
            guard.close()

    def test_borrowed_mapping_iterators_keep_their_reader_alive(self):
        self.write("requirements/a.md", note("requirement", "A"))
        for key in ("documents", "aliases"):
            with self.subTest(mapping=key):
                data, _ = self.load()
                self.stores.remove(data.store)
                iterator = iter(data["catalog"][key])
                del data
                gc.collect()
                self.assertTrue(list(iterator))
                gc.collect()
                lease = vault_index.DatabaseLease(vault_query.default_cache(self.docs), exclusive=True, timeout=0.05)
                lease.close()

    def test_snapshot_copy_retries_a_concurrent_last_reader_checkpoint(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        cache = vault_query.default_cache(self.docs)
        actual = vault_index.snapshot_file_hash
        copies = []
        def checkpointing(path, destination=None):
            result = actual(path, destination)
            if path == cache and destination is not None:
                copies.append(path)
                if len(copies) == 1:
                    data.store.close()
            return result
        with mock.patch.object(vault_index, "snapshot_file_hash", side_effect=checkpointing):
            self.assertTrue(self.query("index", "check")["verified"])
        self.assertGreaterEqual(len(copies), 2)

    def test_restored_source_cannot_publish_an_intermediate_generation(self):
        path = self.write("requirements/a.md", note("requirement", "A", body="Original source."))
        original = path.read_bytes()
        alternate = original.replace(b"Original source.", b"Intermediate source.")
        data, first = self.load()
        data.store.close()
        actual = vault_index.Store.update_note
        def transient(store, relative):
            path.write_bytes(alternate)
            return actual(store, relative)
        try:
            with mock.patch.object(vault_index.Store, "update_note", transient):
                with self.assertRaisesRegex(ValueError, "source changed while indexing"):
                    self.load(rebuild=True)
        finally:
            path.write_bytes(original)
        self.assertEqual(self.query("index", "status")["generation"], first["generation"])
        data, status = self.load()
        self.assertEqual(status["changed"], [])
        self.assertEqual(data["catalog"]["documents"]["requirements/a.md"]["source_hash"],
            "sha256:" + hashlib.sha256(original).hexdigest())

    def test_json_source_hash_matches_the_captured_inventory(self):
        path = self.write("api/source.json", '{"marker":"original"}')
        original = path.read_bytes()
        data, first = self.load()
        data.store.close()
        actual = vault_index.Store.update_json
        def transient(store, relative, canonical=False):
            path.write_bytes(b'{"marker":"intermediate"}')
            return actual(store, relative, canonical)
        try:
            with mock.patch.object(vault_index.Store, "update_json", transient):
                with self.assertRaisesRegex(ValueError, "source changed while indexing"):
                    self.load(rebuild=True)
        finally:
            path.write_bytes(original)
        self.assertEqual(self.query("index", "status")["generation"], first["generation"])

    def test_registry_reference_edges_use_the_verified_owner_snapshot(self):
        self.write("business-analysis/scope-one/a.md", note("story", "Target A"))
        self.write("business-analysis/scope-one/b.md", note("story", "Target B"))
        self.write("backlog/source.md", note("story", "Source", extra="related_to:\n  - scope-one:ACT-001"))
        registry = self.write("business-analysis/scope-one/_generated/registry.json",
            json.dumps({"ids": {"ACT-001": {"doc": "a.md"}}}))
        original = registry.read_bytes()
        alternate = json.dumps({"ids": {"ACT-001": {"doc": "b.md"}}}).encode()
        actual = vault_index.vault_check.relation_edges
        def racing(vault, **kwargs):
            if set(vault.notes) == {"backlog/source.md"}:
                registry.write_bytes(alternate)
                try:
                    return actual(vault, **kwargs)
                finally:
                    registry.write_bytes(original)
            return actual(vault, **kwargs)
        with mock.patch.object(vault_index.vault_check, "relation_edges", side_effect=racing):
            data, _ = self.load()
        self.assertEqual(data.store.edges_for("backlog/source.md"),
            [["backlog/source.md", "business-analysis/scope-one/a.md", "related_to", ["frontmatter"]]])
        self.assert_graph(data)

    def test_registry_namespace_rejects_bytes_outside_the_inventory_snapshot(self):
        self.write("business-analysis/scope-one/a.md", note("story", "Target A"))
        self.write("backlog/source.md", note("story", "Source", extra="related_to:\n  - scope-one:ACT-001"))
        registry = self.write("business-analysis/scope-one/_generated/registry.json",
            json.dumps({"ids": {"ACT-001": {"doc": "a.md"}}}))
        original = registry.read_bytes()
        actual = vault_index.Store.update_json
        def racing(store, relative, canonical=False):
            if relative.endswith("registry.json"):
                registry.write_bytes(b'{"ids":{}}')
                try:
                    return actual(store, relative, canonical)
                finally:
                    registry.write_bytes(original)
            return actual(store, relative, canonical)
        with mock.patch.object(vault_index.Store, "update_json", racing):
            with self.assertRaisesRegex(ValueError, "source changed while indexing"):
                self.load()
        data, _ = self.load()
        self.assert_graph(data)

    def test_section_search_recovers_corruption_detected_during_the_query(self):
        self.write("requirements/a.md", note("requirement", "Source", body="Distinct phrase."))
        data, _ = self.load()
        data.store.close()
        cache = vault_query.default_cache(self.docs)
        with contextlib.closing(sqlite3.connect(cache)) as connection, connection:
            self.assertTrue(connection.execute("SELECT 1 FROM search_text_data WHERE id>10").fetchone())
            connection.execute("UPDATE search_text_data SET block=x'00' WHERE id>10")
        result = self.query("search-sections", "Distinct")
        self.assertTrue(result["hits"])
        self.assertTrue(result["cache"]["recovered"])
        self.assertTrue(self.query("index", "check")["verified"])

    def test_query_corruption_recovery_is_bounded_to_one_retry(self):
        self.write("requirements/a.md", note("requirement", "Source"))
        error = sqlite3.DatabaseError("database disk image is malformed")
        with mock.patch.object(vault_query, "q_search_sections", side_effect=error) as search, \
                mock.patch.object(vault_query, "locked_refresh", wraps=vault_query.locked_refresh) as loader:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = vault_query.main(["--docs", str(self.docs), "search-sections", "Source"])
        self.assertEqual(code, 1)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(loader.call_count, 2)
        lease = vault_index.DatabaseLease(vault_query.default_cache(self.docs), exclusive=True, timeout=0.05)
        lease.close()

    def test_extension_eligibility_and_processing_use_the_same_normalization(self):
        self.write("api/catalog.JSON", '{"label":"Eligible source"}')
        self.write("requirements/a.MD", note("requirement", "Copy", extra="aliases:\n  - REQ-001"))
        self.write("requirements/a.md", note("requirement", "Original", extra="aliases:\n  - REQ-001"))
        self.write("backlog/story.md", note("story", "Story", extra="requirement_ref: REQ-001"))
        data, status = self.load()
        self.assertEqual(set(data["files"]), set(data["catalog"]["documents"]))
        self.assertEqual(len(context_catalog.resolve(data["catalog"], "api/catalog.JSON")), 1)
        # vault_check reads notes by their exact .md extension; other spellings stay reported sources.
        self.assertEqual(context_catalog.resolve(data["catalog"], "requirements/a.MD"), [])
        self.assertEqual([row["path"] for row in status["unparsed"]], ["requirements/a.MD"])
        self.assert_graph(data)
        self.assertTrue(self.query("search-sections", "Eligible")["hits"])
        self.assertTrue(self.query("index", "check")["verified"])


    def test_unparseable_sources_are_reported_without_blocking_navigation(self):
        self.write("requirements/a.md", note("requirement", "A"))
        self.write("backlog/_generated/registry.json", '{\n<<<<<<< ours\n"rows": []\n=======\n}\n')
        self.write("api/surrogate.json", '{"value": "\\ud800"}')
        data, status = self.load()
        self.assertEqual([row["path"] for row in status["unparsed"]],
                         ["api/surrogate.json", "backlog/_generated/registry.json"])
        self.assertEqual(self.query("find", "A")["notes"][0]["path"], "requirements/a.md")
        self.assertEqual(self.query("index", "status")["unparsed"], status["unparsed"])
        self.assert_graph(data)
        data.store.close()
        self.write("system-architecture/_ledger/records/CON-001/r1.json", "{")
        with self.assertRaisesRegex(ValueError, "system-architecture/_ledger/records/CON-001/r1.json"):
            self.load()

    def test_json_sources_stay_out_of_graph_identities_note_sets_and_line_search(self):
        self.seed()
        self.write("business-analysis/scope-one/_generated/registry.json",
                   json.dumps({"ids": {}, "statement": "Shared phrase alpha"}))
        self.write("solution-design/_generated/caps.json", json.dumps({"label": "Shared phrase alpha"}))
        self.write("backlog/story-j.md", note("story", "Story J", body="Shared phrase alpha.",
                   extra="source_ref: solution-design/_generated/caps.json"))
        data, _ = self.load()
        self.assert_graph(data)
        self.assertEqual({hit["path"] for hit in self.query("search", "Shared phrase")["hits"]}, {"backlog/story-j.md"})
        for changed in ("backlog/story-j.md", "solution-design/_generated/caps.json"):
            with self.subTest(changed=changed):
                result = self.query("closure", "--changed", changed)
                result.pop("cache")
                self.assertEqual(result, impact_closure.closure(self.docs, [changed]))
        self.assertEqual(context_catalog.resolve(data["catalog"], "solution-design/_generated/caps.json")[0]["kind"], "json")

    def test_links_inside_the_vault_are_read_and_directory_links_skipped(self):
        target = self.write("backlog/story-b.md", note("story", "Story B"))
        try:
            (self.docs / "backlog/alias.md").symlink_to(target)
            (self.docs / "shared").symlink_to(self.docs / "backlog", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        data, _ = self.load()
        self.assertIn("backlog/alias.md", data["files"])
        self.assertNotIn("shared/story-b.md", data["files"])
        self.assert_graph(data)

    def test_excluded_link_targets_regraph_their_owner_when_they_change(self):
        self.write("requirements/a.md", note("requirement", "A",
                   extra='evidence_ref: "[[requirements/artifacts/evidence|Evidence]]"'))
        self.write("requirements/b.md", note("requirement", "B",
                   extra='requirement_ref: "[[Requirements/A|A]]"\nother_ref: "[[requirements//a|A]]"'))
        data, _ = self.load()
        self.assert_graph(data)
        data.store.close()
        artifact = self.write("requirements/artifacts/evidence.md", "Evidence.")
        data, status = self.load()
        self.assertEqual(status["changed"], [])
        self.assert_graph(data)
        self.assertFalse([gap for gap in data["gaps"] if gap.get("key") == "evidence_ref"])
        data.store.close()
        artifact.unlink()
        data, _ = self.load()
        self.assert_graph(data)
        self.assertTrue([gap for gap in data["gaps"] if gap.get("key") == "evidence_ref"])

    def test_resolver_rebuilds_a_cache_found_corrupt_while_resolving(self):
        self.write("requirements/a.md", note("requirement", "A"))
        actual = project_context.resolve_context
        calls = []
        def damaged(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return actual(*args, **kwargs)
        with mock.patch.object(project_context, "resolve_context", side_effect=damaged), \
                mock.patch.object(vault_index, "recover_database", wraps=vault_index.recover_database) as recover:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = project_context.main(["--project-root", str(self.project), "resolve", "--entry", "requirement",
                                             "--role", "business-analyst", "--ref", "requirements/a.md"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(json.loads(out.getvalue())["status"], "ready")
            calls.clear()
            plan = project_context.task_context(self.project, entry="requirement", role="business-analyst",
                                                mode="review", paths={"workspace/docs/requirements/a.md"})
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(recover.call_count, 2)

    def test_sealed_record_identities_keep_their_typed_authored_relation(self):
        self.write("system-architecture/orders/data-model.md", note("data-model", "Orders data",
                   extra="record_id: DAT-001\nrevision: 1\ncomponent_ref: orders"))
        self.write("system-architecture/orders/module.md", note("architecture-module", "Orders module",
                   extra='constrained_by:\n  - "ARC:orders:DAT-001@r1"'))
        self.write("system-architecture/_ledger/records/DAT-001/r1.json",
                   json.dumps({"exact_ref": "ARC:orders:DAT-001@r1", "content": "---\ntype: data-model\n---\n"}))
        data, _ = self.load()
        self.assert_graph(data)
        targets = {(t, k) for _s, t, k, _tiers in data.store.edges_for("system-architecture/orders/module.md")}
        self.assertIn(("system-architecture/orders/data-model.md", "constrained_by"), targets)

    def test_gap_lookup_splits_large_path_sets(self):
        self.seed()
        data, _ = self.load()
        paths = set(data["files"])
        expected = [gap for gap in data["gaps"] if {gap.get("path"), gap.get("source"), gap.get("target")} & paths]
        with mock.patch.object(vault_index, "CHUNK", 2):
            self.assertEqual(data.store.gaps_for(paths), expected)

    def test_python_without_sqlite_gets_a_concrete_diagnostic(self):
        self.write("requirements/a.md", note("requirement", "A"))
        with mock.patch.object(vault_index, "sqlite3", None):
            with self.assertRaisesRegex(ValueError, "lacks the sqlite3 module"):
                vault_index.capabilities()
            plan = project_context.task_context(self.project, entry="requirement", role="business-analyst",
                                                mode="review", paths={"workspace/docs/requirements/a.md"})
        self.assertEqual(plan["status"], "unavailable")
        self.assertIn("sqlite3", plan["reason"])

    def test_map_home_and_registry_identities_rebind_their_citers(self):
        self.write("maps/backlog.md", note("moc", "Backlog map", extra="aliases:\n  - Backlog"))
        self.write("home.md", note("home", "Home", extra="id: HOME-001"))
        self.write("backlog/source.md", note("story", "Source",
                   extra="related_to:\n  - Delivery board\n  - HOME-001\n  - scope-one:ACT-001"))
        self.write("business-analysis/scope-one/_generated/registry.json", json.dumps({"ids": {"ACT-001": {"doc": "a.md"}}}))
        steps = [
            lambda: self.write("maps/backlog.md", note("moc", "Backlog map", extra="aliases:\n  - Delivery board")),
            lambda: self.write("business-analysis/scope-one/a.md", note("story", "Target")),
            lambda: (self.docs / "business-analysis/scope-one/a.md").unlink(),
            lambda: self.write("home.md", note("home", "Home", extra="id: HOME-002")),
        ]
        for step in [lambda: None, *steps]:
            step()
            data, _ = self.load()
            self.assert_graph(data)
            self.assert_fresh(data)
            data.store.close()

    def test_no_write_reads_reconcile_a_private_copy_of_the_warm_cache(self):
        self.seed()
        data, _ = self.load()
        data.store.close()
        folder = vault_query.default_cache(self.docs).parent
        before = {path: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        with mock.patch.object(context_catalog, "catalog", side_effect=AssertionError("recompiled")):
            index = project_context.load_index(self.project, no_cache=True)
            self.stores.append(index.store)
            self.assertEqual(index["catalog"]["documents"]["backlog/story-b.md"]["title"], "Story B")
        self.write("backlog/story-b.md", note("story", "Story B", {"implements": [("requirements/req-a", "Requirement")]},
                   body="Changed source."))
        index = project_context.load_index(self.project, no_cache=True)
        self.stores.append(index.store)
        self.assert_graph(index)
        self.assertEqual(before, {path: path.read_bytes() for path in folder.iterdir() if path.is_file()})

    def test_post_write_sync_never_waits_on_the_writer_or_fails_the_hook(self):
        spec = importlib.util.spec_from_file_location(
            "index_sync_hook", ROOT / "platforms/shared/software-engineering-team/overlay/scripts/vault_hook.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.write("requirements/a.md", note("requirement", "A"))
        cache = vault_query.default_cache(self.docs)
        cache.parent.mkdir(parents=True)
        descriptor = os.open(cache.parent / ".lock", os.O_RDWR | os.O_CREAT)
        try:
            file_lock.lock(descriptor)
            hook.sync_vault_index(self.docs, [self.docs / "requirements/a.md"])
        finally:
            file_lock.unlock(descriptor)
            os.close(descriptor)
        self.assertFalse(cache.exists())
        hook.sync_vault_index(self.docs, [self.docs / ".obsidian/app.json"])
        self.assertFalse(cache.exists())
        with mock.patch.object(vault_query, "locked_refresh", side_effect=TypeError("aliases: 5")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            hook.sync_vault_index(self.docs, [self.docs / "requirements/a.md"])
        self.assertIn("requires reconciliation", err.getvalue())
        hook.sync_vault_index(self.docs, [self.docs / "requirements/a.md"])
        self.assertTrue(cache.exists())

    def test_gaps_keep_the_canonical_order_and_duplicates(self):
        self.write("maps/_generated/cross-subtree-matrix.md",
                   "<!-- generated by vault_check render-relations; do not edit by hand -->\n# Matrix\n")
        self.write("requirements/req-a.md", note("requirement", "Req A"))
        self.write("backlog/story-a.md", note("story", "Story A", {"implements": [("requirements/req-a", "Req A")],
                   "related_to": [("requirements/req-a", "Req A")]}, extra="notes_ref: \"[[backlog/removed|Removed]]\"\n"
                   "requirement_ref:\n  - REQ-999\n  - REQ-999"))
        self.write("backlog/story-z.md", note("story", "Story Z", extra="id: ST-900"))
        self.write("backlog/story-m.md", note("story", "Story M", body="Mentions ST-900."))
        data, _ = self.load()
        self.assert_graph(data)
        plan = project_context.resolve_context(self.project, data, entry="requirement", role="business-analyst",
                                               refs=["backlog/story-a.md"], persist_state=False)
        self.assertEqual(plan["gaps"][0]["reason"], "unresolved_relation")

    def test_section_search_ignores_terms_without_tokens(self):
        self.write("requirements/a.md", note("requirement", "A", body="Orders -> billing handoff."))
        self.assertTrue(self.query("search-sections", "Orders -> billing")["hits"])

    def test_note_identity_fields_stay_strings(self):
        self.write("requirements/year.md", "---\ntype: requirement\nid: 42\ntitle: 2024\n---\n\n# Year\n")
        self.write("requirements/plan-2024.md", note("requirement", "Plan for 2024"))
        self.assertEqual([row["path"] for row in self.query("find", "2024")["notes"]], ["requirements/plan-2024.md"])
        data, _ = self.load()
        row = data["notes"]["requirements/year.md"]
        self.assertEqual((row["id"], row["title"]), ("", ""))

    def test_incremental_publication_matches_fresh_compilation_for_edit_sequences(self):
        paths = ["requirements/req-a.md", "requirements/req-b.md", "backlog/s1.md", "backlog/s2.md",
                 "maps/backlog.md", "home.md", "business-analysis/scope-one/rules.md"]
        records = ["business-analysis/scope-one/_generated/registry.json", "api/x.json",
                   "system-architecture/_ledger/records/CON-001/r1.json"]
        names = ["ST-001", "ST-002", "REQ-001", "Alpha", "scope-one:BR-ONE-001", "ARC:CON-001@r1",
                 "api/x.json", "[[backlog/s1|S1]]", "[[requirements/req-a|A]]", "[[maps/backlog|M]]"]
        def markdown(rng, path):
            lines = ["---", f"type: {rng.choice(['story', 'requirement', 'rule-set'])}", f"title: T{rng.randint(1, 3)}"]
            if rng.random() < 0.6:
                lines.append(f"id: {rng.choice(['ST-001', 'ST-002', 'REQ-001'])}")
            if rng.random() < 0.4:
                lines += ["aliases:", f"  - {rng.choice(['Alpha', 'Beta', 'REQ-001'])}"]
            for key in rng.sample(["implements", "related_to", "depends_on"], rng.randint(0, 2)):
                lines += [f"{key}:", f'  - "{rng.choice(names)}"']
            lines += ["---", "", "# Heading", "", f"See [[{rng.choice(paths)[:-3]}]] and {rng.choice(['ST-002', 'REQ-001'])}."]
            if rng.random() < 0.4:
                lines += ["", "| id | text |", "| --- | --- |", "| BR-ONE-001 | rule |"]
            return "\n".join(lines) + "\n"
        def record(rng, path):
            if path.startswith("business-analysis"):
                return json.dumps({"ids": {"BR-ONE-001": {"doc": rng.choice(["rules.md", "missing.md"])}}})
            if "_ledger" in path:
                return json.dumps({"exact_ref": "ARC:CON-001@r1", "content": "---\ntype: decision\n---\n"})
            return json.dumps({"label": rng.choice(names)})
        for seed in range(3):
            rng = random.Random(seed)
            for step in range(15):
                with self.subTest(seed=seed, step=step):
                    path = rng.choice(paths + records)
                    if (self.docs / path).exists() and rng.random() < 0.3:
                        (self.docs / path).unlink()
                    else:
                        self.write(path, record(rng, path) if path.endswith(".json") else markdown(rng, path))
                    data, _ = self.load()
                    try:
                        self.assert_fresh(data)
                        self.assert_graph(data)
                    finally:
                        data.store.close()

    def test_edits_rescan_only_their_own_sources(self):
        self.seed()
        self.write("backlog/story-z.md", note("story", "Story Z", extra="id: ST-900"))
        self.write("business-analysis/scope-one/_generated/registry.json", json.dumps({"ids": {}}))
        data, _ = self.load()
        data.store.close()
        self.write("business-analysis/scope-one/_generated/registry.json", json.dumps({"ids": {}, "source_hashes": {"a": "b"}}))
        self.write("backlog/story-b.md", note("story", "Story B", {"implements": [("requirements/req-a", "Req A")]},
                   body="Edited body mentioning ST-900."))
        actual_graph, actual_text = vault_index.Store.graph_owner, impact_closure.text_tier
        scanned = []
        def text_tier(vault, edges, targets):
            scanned.append(sorted(vault.notes))
            return actual_text(vault, edges, targets)
        with mock.patch.object(vault_index.Store, "graph_owner", autospec=True, side_effect=actual_graph) as graph, \
                mock.patch.object(impact_closure, "text_tier", side_effect=text_tier):
            data, _ = self.load()
        self.assertLess(graph.call_count, 5)
        self.assertEqual(scanned, [["backlog/story-b.md"]])
        self.assert_graph(data)

    def test_status_reports_binding_schema_and_known_freshness(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        status = self.query("index", "status")
        self.assertEqual((status["docs"], status["schema_version"], status["possibly_stale"]),
                         (str(self.docs), vault_index.SCHEMA_VERSION, False))
        self.write("requirements/b.md", note("requirement", "B"))
        self.assertTrue(self.query("index", "status")["possibly_stale"])

    def test_check_reports_a_concurrent_edit_as_stale(self):
        path = self.write("requirements/a.md", note("requirement", "A"))
        data, _ = self.load()
        data.store.close()
        actual = vault_index.refresh
        def racing(*args, **kwargs):
            path.write_text(note("requirement", "A", body="Edited during the check."), encoding="utf-8")
            return actual(*args, **kwargs)
        with mock.patch.object(vault_index, "refresh", side_effect=racing):
            self.assertEqual(self.query("index", "check", expected=1)["status"], "stale")

    def test_inspection_copies_without_the_writer_lock_and_recovery_sweeps_candidates(self):
        self.write("requirements/a.md", note("requirement", "A"))
        data, first = self.load()
        data.store.close()
        with mock.patch.object(file_lock, "lock", side_effect=AssertionError("writer lock")):
            self.assertTrue(self.query("index", "check")["verified"])
        cache = vault_query.default_cache(self.docs)
        stale = cache.parent / ".index-recovery-interrupted"
        stale.mkdir()
        (stale / "index.db").write_bytes(b"partial")
        with contextlib.closing(sqlite3.connect(cache)) as connection, connection:
            connection.execute("UPDATE search_text_data SET block=x'00' WHERE id>10")
        result = self.query("search-sections", "A")
        self.assertTrue(result["cache"]["recovered"])
        self.assertGreater(result["cache"]["generation"], first["generation"])
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
