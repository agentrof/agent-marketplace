"""Vault query CLI (plugins/software-engineering-team/scripts/vault_query.py):
every verb, the JSON index under the project runtime scratch, incremental
rebuild by file hash and the stale-cache cases."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_query  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402
from tools.tests.test_impact_closure import REQ, VAULT, note, stamp  # noqa: E402


class VaultQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.docs = self.project / "workspace" / "docs"
        for rel, text in VAULT.items():
            path = self.docs / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (self.docs / "backlog/story-g.md").write_text(
            note("story", "Story G", extra="id: ST-900\naliases:\n  - Gee"), encoding="utf-8")
        (self.docs / "backlog/story-t.md").write_text(
            note("story", "Story T", {"related_to": [(REQ, "Req A")]},
                 body="Needs ST-900 first.\n\nThe Orders queue drains nightly."),
            encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_query(self, *argv: str, code: int = 0) -> dict:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = vault_query.main(["--docs", str(self.docs), *argv])
        self.assertEqual(result, code, err.getvalue())
        return json.loads(out.getvalue()) if code == 0 else {"stderr": err.getvalue()}

    @property
    def cache(self) -> Path:
        return self.project / ".agentrof/agent-marketplace/.runtime/vault-index/index.json"

    def test_index_is_json_under_the_project_runtime_scratch(self) -> None:
        result = self.run_query("gaps")
        self.assertEqual(result["cache"]["path"], str(self.cache))
        self.assertTrue(result["cache"]["full"])
        data = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertIn("backlog/story-b.md", data["notes"])
        self.assertTrue((self.cache.parent / "index-notes").is_dir())

    def test_closure(self) -> None:
        result = self.run_query("closure", "--changed", f"{REQ}.md")
        self.assertEqual(result["closure"], [
            "backlog/story-b.md", "backlog/story-c.md", "backlog/story-d.md",
            "backlog/story-t.md", f"{REQ}.md"])

    def test_closure_matches_the_uncached_closure(self) -> None:
        import impact_closure
        cached = self.run_query("closure", "--changed", "backlog/story-g.md")
        cached.pop("cache")
        self.assertEqual(cached, json.loads(json.dumps(
            impact_closure.closure(self.docs, ["backlog/story-g.md"]))))

    def test_related_groups_edges_by_key_and_tier(self) -> None:
        result = self.run_query("related", "backlog/story-c.md")
        self.assertEqual(result["outgoing"]["depends_on"],
                         [{"note": "backlog/story-b.md", "tiers": ["frontmatter"]}])
        self.assertEqual(result["incoming"]["related_to"],
                         [{"note": "backlog/story-d.md", "tiers": ["frontmatter"]}])

    def test_who_cites_by_id_includes_text_mentions(self) -> None:
        result = self.run_query("who-cites", "ST-900")
        self.assertEqual(result["note"], "backlog/story-g.md")
        self.assertEqual(result["cited_by"]["mentions"],
                         [{"note": "backlog/story-t.md", "tiers": ["text"]}])

    def test_path_between_notes(self) -> None:
        result = self.run_query("path", "backlog/story-d.md", "Req A")
        self.assertEqual([(hop["from"], hop["to"], hop["key"], hop["direction"])
                          for hop in result["path"]], [
            ("backlog/story-d.md", "backlog/story-c.md", "related_to", "out"),
            ("backlog/story-c.md", "backlog/story-b.md", "depends_on", "out"),
            ("backlog/story-b.md", f"{REQ}.md", "implements", "out")])
        self.assertIsNone(self.run_query("path", "backlog/story-u.md", f"{REQ}.md")["path"])

    def test_find_by_id_alias_title_and_path(self) -> None:
        for ref in ("ST-900", "gee", "Story G", "story-g"):
            with self.subTest(ref=ref):
                self.assertEqual([n["path"] for n in self.run_query("find", ref)["notes"]],
                                 ["backlog/story-g.md"])
        self.assertIn("names 0 notes", self.run_query("related", "nothing", code=1)["stderr"])

    def test_hash_proves_unchanged_and_reports_stale(self) -> None:
        digest = stamp(self.docs / "backlog/story-u.md")
        result = self.run_query("hash", "backlog/story-u.md")
        self.assertEqual((result["approval_hash"], result["scheme"], result["proven_unchanged"]),
                         (digest, "backlog", True))
        path = self.docs / "backlog/story-u.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Content.", "Edited."),
                        encoding="utf-8")
        result = self.run_query("hash", "backlog/story-u.md")
        self.assertFalse(result["proven_unchanged"])
        self.assertTrue(result["stamped"])
        self.assertFalse(self.run_query("hash", "backlog/story-b.md")["stamped"])
        path.write_text(VAULT["backlog/story-u.md"], encoding="utf-8")
        self.assertFalse(self.run_query("hash", "backlog/story-u.md")["stamped"])

    def test_changed_since_git_ref(self) -> None:
        init_repository(self.project)
        git = ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "add", "workspace"], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        (self.docs / "backlog/story-b.md").write_text(note("story", "Story B2"), encoding="utf-8")
        (self.docs / "backlog/new.md").write_text(note("story", "New"), encoding="utf-8")
        result = self.run_query("changed-since", "HEAD")
        self.assertEqual(result["changed"], ["backlog/new.md", "backlog/story-b.md"])
        self.assertIn("vault_query:", self.run_query("changed-since", "nope", code=1)["stderr"])

    def test_changed_since_refuses_a_git_option_as_its_ref(self) -> None:
        init_repository(self.project)
        git = ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "add", "workspace"], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        target = self.project / "written.txt"
        for ref in (f"--output={target}", "-p"):
            with self.subTest(ref=ref):
                self.assertIn("not a Git option",
                              self.run_query("changed-since", "--", ref, code=1)["stderr"])
        self.assertFalse(target.exists())

    def test_gaps_filter_by_reason(self) -> None:
        gaps = self.run_query("gaps", "--reason", "text_only_relation")["gaps"]
        self.assertEqual(gaps, [{"path": "backlog/story-g.md", "reason": "text_only_relation",
                                 "source": "backlog/story-t.md", "tiers": ["text"],
                                 "suggested_fix": "declare the typed relation backlog/story-t.md"
                                 " names to backlog/story-g.md by identifier in the front"
                                 " matter of the citing note"}])
        self.assertGreater(len(self.run_query("gaps")["gaps"]), len(gaps))

    def test_search_returns_ids_and_line_anchors(self) -> None:
        hits = self.run_query("search", "orders QUEUE")["hits"]
        self.assertEqual(hits, [{"path": "backlog/story-t.md", "id": None, "line": 13,
                                 "anchor": "backlog/story-t.md:13",
                                 "text": "The Orders queue drains nightly."}])
        hits = self.run_query("search", "ST-900")["hits"]
        self.assertEqual([(h["path"], h["id"]) for h in hits],
                         [("backlog/story-g.md", "ST-900"), ("backlog/story-t.md", None)])
        limited = self.run_query("search", "Content", "--limit", "2")
        self.assertEqual((len(limited["hits"]), limited["truncated"]), (2, True))

    def test_incremental_rebuild_rescans_only_changed_files(self) -> None:
        self.run_query("gaps")
        warm = self.run_query("gaps")["cache"]
        self.assertEqual((warm["full"], warm["changed"], warm["removed"]), (False, [], []))
        (self.docs / "backlog/story-g.md").write_text(
            note("story", "Story G", {"related_to": [(REQ, "Req A")]}, extra="id: ST-900"),
            encoding="utf-8")
        (self.docs / "backlog/loop-y.md").unlink()
        scanned = []
        real = vault_query.vault_check.scan_note
        def spy(root, path, *args, **kwargs):
            scanned.append(path.relative_to(root).as_posix())
            return real(root, path, *args, **kwargs)
        with mock.patch.object(vault_query.vault_check, "scan_note", spy):
            result = self.run_query("closure", "--changed", f"{REQ}.md")
        self.assertEqual(result["cache"]["changed"], ["backlog/story-g.md"])
        self.assertEqual(result["cache"]["removed"], ["backlog/loop-y.md"])
        self.assertFalse(result["cache"]["full"])
        self.assertEqual(scanned, ["backlog/story-g.md"])
        self.assertIn("backlog/story-g.md", result["closure"])
        shards = {p.name for p in (self.cache.parent / "index-notes").iterdir()}
        self.assertEqual(len(shards), len(json.loads(self.cache.read_text())["notes"]))

    def test_stale_cache_same_size_and_mtime_needs_verify(self) -> None:
        self.run_query("gaps")
        path = self.docs / "backlog/story-c.md"
        info = path.stat()
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("Story B", "Story X", 1), encoding="utf-8")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(self.run_query("gaps")["cache"]["changed"], [])
        self.assertEqual(self.run_query("--verify", "gaps")["cache"]["changed"],
                         ["backlog/story-c.md"])

    def test_touched_file_with_same_bytes_is_not_a_change(self) -> None:
        self.run_query("gaps")
        path = self.docs / "backlog/story-c.md"
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
        self.assertEqual(self.run_query("gaps")["cache"]["changed"], [])

    def test_stale_cache_after_code_change_or_damage_rebuilds_fully(self) -> None:
        self.run_query("gaps")
        with mock.patch.object(vault_query, "builder_hash", return_value="other"):
            self.assertTrue(self.run_query("gaps")["cache"]["full"])
        self.cache.write_text("{not json", encoding="utf-8")
        result = self.run_query("related", "backlog/story-c.md")
        self.assertTrue(result["cache"]["full"])
        self.assertIn("depends_on", result["outgoing"])
        shard = next((self.cache.parent / "index-notes").iterdir())
        shard.write_text("garbage", encoding="utf-8")
        (self.docs / "backlog/story-b.md").write_text(note("story", "Story B"), encoding="utf-8")
        self.assertIn("depends_on", self.run_query("related", "backlog/story-c.md")["outgoing"])


if __name__ == "__main__":
    unittest.main()
