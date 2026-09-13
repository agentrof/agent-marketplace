from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import experience_application_check as application
import experience_compile as compiler
import stage_package
import vault_check


class ArtifactSnapshotMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.root = self.project / "workspace/docs/experience-design"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.source = self.artifacts / "index.html"
        self.source.write_bytes(b"<main>Approved</main>\n")

    def approve(self, *, legacy_metadata=False):
        state = self.root / "_generated/open-application-revision.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"opened_revision": 1}\n')
        receipt, findings = application.compile_application(self.root)
        self.assertEqual(findings, [])
        if legacy_metadata:
            receipt["artifact_files"].append(application.artifact_path_row(
                Path(".DS_Store"), application.sha(b"old metadata"), 12,
            ))
            receipt["artifact_files"].sort(key=lambda row: row["path"])
            receipt["artifact_tree_hash"] = application.artifact_tree_hash(receipt["artifact_files"])
            receipt["application_hash"] = application.sha(application.canonical({
                key: value for key, value in receipt.items() if key != "application_hash"
            }))
        application.write_registry_and_ledger(self.root, receipt)
        state.unlink()
        return receipt

    def test_known_metadata_churn_at_any_depth_leaves_approval_unchanged(self):
        receipt = self.approve()
        names = json.loads(application.DEFAULT_SCHEMA.read_text())["application"]["os_metadata_basenames"]
        self.assertTrue({".DS_Store", "Thumbs.db", "desktop.ini"}.issubset(names))
        for parent in [self.artifacts, self.artifacts / "nested" / "deep"]:
            parent.mkdir(parents=True, exist_ok=True)
            for name in names:
                path = parent / name
                path.write_bytes(b"OS metadata")
                checked, findings = application.compile_application(self.root, gate=True)
                self.assertEqual(findings, [])
                self.assertEqual(checked, receipt)
                path.write_bytes(b"different operating system bytes")
                self.assertEqual(application.compile_application(self.root, gate=True), (receipt, []))
                path.unlink()
        self.assertEqual(application.approved_snapshot(self.root), (receipt, []))

    def test_dotfiles_ignored_content_and_metadata_named_directories_are_artifacts(self):
        (self.artifacts / ".gitignore").write_text("ignored.bin\n")
        (self.artifacts / "ignored.bin").write_bytes(b"authored")
        (self.artifacts / ".env").write_text("MODE=prototype\n")
        (self.artifacts / ".DS_Store.notes").write_text("authored notes\n")
        (self.artifacts / ".ds_store").write_text("case-sensitive authored name\n")
        (self.artifacts / "Thumbs.db").mkdir()
        (self.artifacts / "Thumbs.db" / "screen.html").write_text("<main>nested</main>\n")
        rows, findings = application.artifact_inventory(self.root)
        self.assertEqual(findings, [])
        self.assertEqual({row["path"] for row in rows}, {
            "index.html", ".gitignore", "ignored.bin", ".env", ".DS_Store.notes",
            ".ds_store", "Thumbs.db/screen.html",
        })

    def test_each_meaningful_file_change_rename_addition_and_deletion_is_detected(self):
        self.approve()
        mutations = (
            lambda: self.source.write_bytes(b"<main>Edited</main>\n"),
            lambda: self.source.rename(self.artifacts / "renamed.html"),
            lambda: (self.artifacts / ".new-hidden-file").write_bytes(b"added"),
            lambda: self.source.unlink(),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                for child in self.artifacts.iterdir():
                    child.unlink()
                self.source.write_bytes(b"<main>Approved</main>\n")
                mutate()
                _receipt, findings = application.approved_snapshot(self.root)
                self.assertIn("approved artifact tree differs from its receipt", findings)

    def test_missing_legacy_metadata_remains_a_stale_current_receipt(self):
        receipt = self.approve(legacy_metadata=True)
        ledger_path = self.root / application.LEDGER_RELATIVE
        ledger_before = ledger_path.read_bytes()
        self.assertEqual(application.verified_application_ledger(self.root), ([receipt], []))
        _checked, findings = application.approved_snapshot(self.root)
        self.assertIn("approved artifact tree differs from its receipt", findings)
        self.assertEqual(ledger_before, ledger_path.read_bytes())
        ledger = json.loads(ledger_before)
        ledger["revisions"][0]["artifact_files"] = [
            row for row in ledger["revisions"][0]["artifact_files"] if row["path"] != ".DS_Store"
        ]
        ledger_path.write_bytes(application.canonical(ledger))
        _rows, findings = application.verified_application_ledger(self.root)
        self.assertTrue(any("artifact_tree_hash does not match" in problem for problem in findings))
        self.assertTrue(any("application_hash is invalid" in problem for problem in findings))

    def test_metadata_symlinks_hardlinks_and_nonregular_files_are_rejected(self):
        noise = self.artifacts / ".DS_Store"
        try:
            noise.symlink_to(self.source)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        _rows, findings = application.artifact_inventory(self.root)
        self.assertIn("artifacts/.DS_Store: symlinks are not permitted in a snapshot", findings)
        noise.unlink()
        os.link(self.source, noise)
        _rows, findings = application.artifact_inventory(self.root)
        self.assertIn("artifacts/.DS_Store: hard-linked files are not permitted in a snapshot", findings)
        noise.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(noise)
            _rows, findings = application.artifact_inventory(self.root)
            self.assertIn("artifacts/.DS_Store: prototype snapshots contain regular files only", findings)

    def test_arbitrary_newline_names_remain_exact(self):
        names = ["line\nbreak.html", "[glob]*.html", ":(glob)*.html"]
        for name in names:
            (self.artifacts / name).write_bytes(b"author owned")
        rows, findings = application.artifact_inventory(self.root)
        self.assertEqual(findings, [])
        self.assertEqual({application.artifact_row_path(row) for row in rows}, {"index.html", *names})
        self.approve()
        self.assertEqual(application.approved_snapshot(self.root)[1], [])

    def test_non_utf8_path_codec_and_filesystem_round_trip(self):
        name = os.fsdecode(b"invalid-utf8-\xff.html")
        row = application.artifact_path_row(Path(name), application.sha(b"opaque"), 6)
        self.assertEqual(application.artifact_row_path(row), name)
        try:
            (self.artifacts / name).write_bytes(b"opaque")
        except (OSError, UnicodeError) as exc:
            self.skipTest(f"filesystem cannot store non-UTF8 names: {exc}")
        self.approve()
        self.assertEqual(application.approved_snapshot(self.root)[1], [])

    def test_metadata_classifier_does_not_require_existing_file(self):
        self.assertTrue(application.is_os_metadata_path("deleted/nested/.DS_Store"))
        self.assertFalse(application.is_os_metadata_path(".DS_Store/real.html"))
        self.assertFalse(application.is_os_metadata_path(".ds_store"))
        self.assertFalse(application.is_os_metadata_path(".gitignore"))

    def test_committed_new_receipt_ignores_metadata_without_ignoring_authored_files(self):
        receipt = self.approve()
        for args in [
            ["init", "-q"], ["config", "user.name", "Jane Doe"],
            ["config", "user.email", "jane@example.invalid"],
            ["add", "--all"], ["commit", "-qm", "Approve prototype"],
        ]:
            subprocess.run(["git", *args], cwd=self.project, check=True, capture_output=True)
        (self.artifacts / ".DS_Store").write_bytes(b"untracked OS metadata")
        self.assertEqual(application.approved_snapshot(self.root), (receipt, []))
        self.assertTrue(stage_package.paths_are_committed(application.artifact_snapshot_paths(self.root, receipt)))
        self.source.write_bytes(b"authored change\n")
        self.assertFalse(stage_package.paths_are_committed(application.artifact_snapshot_paths(self.root, receipt)))

    def test_receipt_inventory_rejects_absolute_and_nul_paths(self):
        for path in ["/outside/artifact", "nested/invalid\0name"]:
            findings = []
            application._inventory_valid([
                {"path": path, "sha256": application.sha(b"opaque"), "size": 6},
            ], "inventory", findings)
            self.assertTrue(any("canonical relative path" in problem for problem in findings))


class CommittedArtifactPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Jane Doe")
        self.git("config", "user.email", "jane@example.invalid")
        self.folder = self.root / "artifacts"
        self.folder.mkdir()
        self.path = self.folder / "index.html"
        self.path.write_bytes(b"approved\n")
        (self.root / ".gitignore").write_text("*.ignored\n")
        self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True,
        )

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Snapshot artifacts")

    def test_exact_tracked_files_and_directories_pass(self):
        self.assertTrue(stage_package.paths_are_committed([self.path]))
        self.assertTrue(stage_package.paths_are_committed([self.folder]))
        self.assertFalse(stage_package.paths_are_committed([]))

    def test_missing_untracked_and_ignored_untracked_files_fail(self):
        for name in ["new.html", "cache.ignored"]:
            path = self.folder / name
            path.write_bytes(b"uncommitted")
            self.assertFalse(stage_package.paths_are_committed([self.path, path]))
            self.assertFalse(stage_package.paths_are_committed([self.folder]))
            path.unlink()
        self.assertFalse(stage_package.paths_are_committed([self.folder / "never-existed.ignored"]))
        self.path.unlink()
        self.assertFalse(stage_package.paths_are_committed([self.path]))
        self.assertFalse(stage_package.paths_are_committed([self.folder]))

    def test_working_bytes_are_checked_even_with_assume_unchanged(self):
        self.git("update-index", "--assume-unchanged", "artifacts/index.html")
        self.path.write_bytes(b"changed\n")
        self.assertFalse(stage_package.paths_are_committed([self.path]))

    def test_working_bytes_are_checked_even_with_skip_worktree(self):
        self.git("update-index", "--skip-worktree", "artifacts/index.html")
        self.path.write_bytes(b"changed\n")
        self.assertFalse(stage_package.paths_are_committed([self.path]))

    def test_staged_bytes_cannot_drift_even_if_working_bytes_match_head(self):
        self.path.write_bytes(b"staged drift\n")
        self.git("add", "artifacts/index.html")
        self.path.write_bytes(b"approved\n")
        self.assertFalse(stage_package.paths_are_committed([self.path]))

    def test_staged_deletion_and_addition_fail(self):
        self.git("rm", "--cached", "artifacts/index.html")
        self.assertFalse(stage_package.paths_are_committed([self.path]))
        added = self.folder / "added.html"
        added.write_bytes(b"new")
        self.git("add", "artifacts/added.html")
        self.assertFalse(stage_package.paths_are_committed([added]))

    def test_replacement_refs_cannot_substitute_committed_artifact_bytes(self):
        original = self.git("rev-parse", "HEAD:artifacts/index.html").stdout.strip().decode()
        replacement = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.root,
            input=b"replacement bytes\n", check=True, capture_output=True,
        ).stdout.strip().decode()
        self.git("replace", original, replacement)
        self.assertTrue(stage_package.paths_are_committed([self.path]))
        self.path.write_bytes(b"replacement bytes\n")
        self.assertFalse(stage_package.paths_are_committed([self.path]))

    def test_symlink_file_and_parent_and_hardlink_fail(self):
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.folder, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertFalse(stage_package.paths_are_committed([alias / self.path.name]))
        copy = self.folder / "copy.html"
        copy.symlink_to(self.path)
        self.assertFalse(stage_package.paths_are_committed([self.folder]))
        self.assertFalse(stage_package.paths_are_committed([copy]))
        copy.unlink()
        os.link(self.path, copy)
        self.assertFalse(stage_package.paths_are_committed([self.path]))

    def test_literal_newline_names_round_trip(self):
        names = ["line\nbreak.html", "[glob]*.html", ":(glob)*.html"]
        paths = [self.root / name for name in names]
        for path in paths:
            path.write_bytes(b"opaque\0artifact\xff")
        self.commit()
        self.assertTrue(stage_package.paths_are_committed(paths))
        self.assertTrue(stage_package.paths_are_committed([self.folder]))
        paths[0].write_bytes(b"modified")
        self.assertFalse(stage_package.paths_are_committed(paths))

    def test_non_utf8_names_round_trip_when_filesystem_supports_them(self):
        path = self.folder / os.fsdecode(b"raw-\xff.html")
        try:
            path.write_bytes(b"opaque\0artifact\xff")
        except (OSError, UnicodeError) as exc:
            self.skipTest(f"filesystem cannot store non-UTF8 names: {exc}")
        self.commit()
        self.assertTrue(stage_package.paths_are_committed([path]))
        path.write_bytes(b"edited")
        self.assertFalse(stage_package.paths_are_committed([path]))


class PackageCommitMetadataTests(unittest.TestCase):
    setUp = CommittedArtifactPathsTests.setUp
    git = CommittedArtifactPathsTests.git
    commit = CommittedArtifactPathsTests.commit

    def test_metadata_changes_additions_and_regular_tracked_deletion_pass(self):
        metadata = self.folder / ".DS_Store"
        metadata.write_bytes(b"old metadata")
        self.commit()
        metadata.write_bytes(b"new metadata")
        self.assertTrue(stage_package.is_committed(self.folder))
        (self.folder / "Thumbs.db").write_bytes(b"unignored new metadata")
        self.assertTrue(stage_package.is_committed(self.folder))
        metadata.unlink()
        self.assertTrue(stage_package.is_committed(self.folder))
        self.git("add", "--all")
        self.assertTrue(stage_package.is_committed(self.folder))
        self.path.write_bytes(b"meaningful changed bytes")
        self.assertFalse(stage_package.is_committed(self.folder))

    def test_metadata_named_directories_do_not_hide_authored_changes(self):
        directory = self.folder / ".DS_Store"
        directory.mkdir()
        authored = directory / "prototype.js"
        authored.write_bytes(b"original source")
        self.commit()
        authored.write_bytes(b"changed source")
        self.assertFalse(stage_package.is_committed(self.folder))

    def test_ignored_metadata_aliases_are_rejected_without_reading_them(self):
        (self.root / ".gitignore").write_text("*.ignored\n.DS_Store\n")
        self.commit()
        metadata = self.folder / ".DS_Store"
        try:
            metadata.symlink_to(self.path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        self.assertFalse(stage_package.is_committed(self.folder))
        metadata.unlink()
        os.link(self.path, metadata)
        self.assertFalse(stage_package.is_committed(self.folder))
        metadata.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(metadata)
            self.assertFalse(stage_package.is_committed(self.folder))
            metadata.unlink()

    def test_deleted_metadata_named_symlink_is_not_treated_as_regular_metadata(self):
        metadata = self.folder / ".DS_Store"
        try:
            metadata.symlink_to(self.path.name)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        self.commit()
        metadata.unlink()
        self.assertFalse(stage_package.is_committed(self.folder))

    def test_package_artifacts_ignore_rules_cannot_hide_uncommitted_source(self):
        (self.root / ".gitignore").write_text("*.ignored\n.env\nsettings.json\n")
        self.commit()
        artifacts = self.folder / "artifacts"
        artifacts.mkdir()
        metadata = artifacts / ".DS_Store"
        metadata.write_bytes(b"ignored operating-system evidence")
        self.assertTrue(stage_package.is_committed(self.folder))
        for name in (".env", "settings.json", "runtime.ignored"):
            with self.subTest(name=name):
                source = artifacts / name
                source.write_bytes(b"meaningful opaque artifact")
                self.assertFalse(stage_package.is_committed(self.folder))
                self.git("add", "-f", "--", source.relative_to(self.root).as_posix())
                self.git("commit", "-qm", "Commit meaningful artifact")
                self.assertTrue(stage_package.is_committed(self.folder))
        metadata.unlink()
        self.assertTrue(stage_package.is_committed(self.folder))

    def test_index_flags_cannot_hide_missing_meaningful_package_artifacts(self):
        artifacts = self.folder / "artifacts"
        artifacts.mkdir()
        source = artifacts / "meaningful.json"
        source.write_bytes(b"approved artifact")
        self.commit()
        relative = source.relative_to(self.root).as_posix()
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag=flag):
                self.git("update-index", flag, "--", relative)
                source.unlink()
                self.assertFalse(stage_package.is_committed(self.folder))
                source.write_bytes(b"approved artifact")
                self.git("update-index", "--no-skip-worktree", "--no-assume-unchanged", "--", relative)

    def test_ignored_meaningful_artifact_still_fails_exact_source_commit_check(self):
        ignored = self.folder / "runtime.ignored"
        ignored.write_bytes(b"meaningful ignored evidence")
        self.assertFalse(stage_package.paths_are_committed([self.path, ignored]))
        self.assertFalse(stage_package.paths_are_committed([self.folder]))


class ProcessArtifactMetadataCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name) / "checkout"
        (self.package / "artifacts/nested").mkdir(parents=True)
        self.data = {"type": "experience", "status": "draft", "revision": 1}
        self.body = "# Checkout\n"
        self.note = self.package / "experience.md"
        self.note.write_text(compiler.render_fm(self.data, self.body))
        self.source = self.package / "artifacts/nested/prototype.bin"
        self.source.write_bytes(b"meaningful evidence")
        self.metadata = self.package / "artifacts/.DS_Store"
        self.metadata.write_bytes(b"old metadata")
        stable = {"type": "experience", "revision": 1}
        self.legacy_hash = "sha256:" + hashlib.sha256(
            b"experience.md\0" + compiler.render_fm(stable, self.body).encode() + b"\0"
            + b"artifacts/.DS_Store\0old metadata\0"
            + b"artifacts/nested/prototype.bin\0meaningful evidence\0"
        ).hexdigest()

    def rewrite(self, **changes):
        self.data.update(changes)
        self.note.write_text(compiler.render_fm(self.data, self.body))

    def test_existing_metadata_bearing_approval_keeps_its_exact_digest(self):
        self.rewrite(status="approved", source_hash=self.legacy_hash)
        before = self.note.read_bytes()
        self.assertEqual(compiler.source_digest(self.package), self.legacy_hash)
        self.assertEqual(before, self.note.read_bytes())
        self.assertEqual(compiler.source_digest(self.package, root_data=dict(self.data)), self.legacy_hash)

    def test_new_revision_excludes_metadata_and_keeps_meaningful_content_bound(self):
        self.rewrite(status="draft", revision=2, source_hash=self.legacy_hash)
        revised = compiler.source_digest(self.package)
        self.assertNotEqual(revised, self.legacy_hash)
        self.metadata.write_bytes(b"changed OS metadata")
        self.assertEqual(compiler.source_digest(self.package), revised)
        (self.package / "artifacts/nested/Thumbs.db").write_bytes(b"new OS metadata")
        self.assertEqual(compiler.source_digest(self.package), revised)
        self.rewrite(status="in_review")
        self.assertEqual(compiler.source_digest(self.package), revised)
        self.rewrite(status="approved", source_hash=revised)
        self.metadata.unlink()
        self.assertEqual(compiler.source_digest(self.package), revised)
        self.source.write_bytes(b"changed meaningful evidence")
        self.assertNotEqual(compiler.source_digest(self.package), revised)

    def test_missing_legacy_metadata_is_not_falsely_recovered(self):
        self.rewrite(status="approved", source_hash=self.legacy_hash)
        self.metadata.unlink()
        self.assertNotEqual(compiler.source_digest(self.package), self.legacy_hash)
        self.assertEqual(compiler.fields(self.package)["source_hash"], self.legacy_hash)

    def test_legacy_fallback_rejects_meaningful_drift_and_fabricated_hashes(self):
        self.rewrite(status="approved", source_hash=self.legacy_hash)
        self.source.write_bytes(b"changed meaningful evidence")
        self.assertNotEqual(compiler.source_digest(self.package), self.legacy_hash)
        forged = "sha256:" + "f" * 64
        self.rewrite(source_hash=forged)
        self.assertNotEqual(compiler.source_digest(self.package), forged)

    def test_retired_legacy_revision_remains_exact_and_detects_drift(self):
        self.rewrite(status="retired", revision=2, source_hash=self.legacy_hash,
                     retired_at_utc="2025-01-01T00:00:00+00:00")
        self.assertEqual(compiler.source_digest(self.package, historical_revision=1), self.legacy_hash)
        self.metadata.write_bytes(b"changed legacy metadata")
        self.assertNotEqual(compiler.source_digest(self.package, historical_revision=1), self.legacy_hash)


class VaultMetadataLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        (self.root / self.policy["home_file"]).write_text("# Home\n")

    def findings(self):
        findings = []
        vault_check.check_vault_layout(vault_check.build_vault(self.root, self.policy), findings)
        return findings

    def test_os_metadata_at_vault_root_and_note_subtrees_is_not_layout_content(self):
        before = self.findings()
        for relative in (".DS_Store", "experience-design/.DS_Store", "backlog/Thumbs.db",
                         "design-system/desktop.ini", ".obsidian/workspace/.DS_Store"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"OS state")
        self.assertEqual(self.findings(), before)
        meaningful = self.root / "experience-design/.author-data"
        meaningful.write_text("authored data")
        self.assertTrue(any(row.path == "experience-design/.author-data" for row in self.findings()))

    def test_metadata_aliases_and_special_files_remain_layout_errors(self):
        target = self.root / "home.md"
        metadata = self.root / ".DS_Store"
        metadata.symlink_to(target)
        self.assertTrue(any(row.path == ".DS_Store" for row in self.findings()))
        metadata.unlink()
        os.link(target, metadata)
        self.assertTrue(any(row.path == ".DS_Store" for row in self.findings()))
        metadata.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(metadata)
            self.assertTrue(any(row.path == ".DS_Store" for row in self.findings()))

    def test_metadata_named_directory_does_not_hide_meaningful_descendants(self):
        path = self.root / "experience-design/.DS_Store/source.js"
        path.parent.mkdir(parents=True)
        path.write_text("product code")
        self.assertTrue(any(row.path == "experience-design/.DS_Store/source.js" for row in self.findings()))


if __name__ == "__main__":
    unittest.main()
