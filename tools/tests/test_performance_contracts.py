"""Performance changes retain exact candidate trees and fixture isolation."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))

import delivery_git
import fixture_cache
from git_fixture import init_repository, remove_temporary
from tools.tests import test_delivery_git as delivery_tests


class PerformanceContractsTests(unittest.TestCase):
    def test_batched_index_matches_individual_byte_mode_and_delete_updates(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_repository(root)
            original = root / "original-index"
            env = dict(os.environ, GIT_INDEX_FILE=str(original))
            subprocess.run(["git", "read-tree", "--empty"], cwd=root, env=env, check=True)
            blob = subprocess.check_output(["git", "hash-object", "-w", "--stdin"], cwd=root,
                                           input=b"before\r\n").decode().strip()
            delivery_git.update_candidate_index(root, env, [("100755", blob, "removed"), ("100755", blob, "mode")])
            original_bytes = original.read_bytes()
            single, batch = root / "single-index", root / "batch-index"
            shutil.copy2(original, single)
            shutil.copy2(original, batch)
            paths = ["mode", "spaces and café.txt"]
            if os.name != "nt":
                paths += ["colon:name", "tab\tname", "line\nname"]
            entries = []
            for index, path in enumerate(paths):
                content = (path + "\r\n").encode("utf-8") + b"\x00exact"
                oid = subprocess.check_output(["git", "hash-object", "-w", "--stdin"],
                                              cwd=root, input=content).decode().strip()
                entries.append(("100644" if index % 2 == 0 else "100755", oid, path))
            single_env = dict(env, GIT_INDEX_FILE=str(single))
            for mode, oid, path in entries:
                subprocess.run(["git", "update-index", "--add", "--cacheinfo", mode, oid, path],
                               cwd=root, env=single_env, check=True)
            subprocess.run(["git", "update-index", "--force-remove", "--", "removed"],
                           cwd=root, env=single_env, check=True)
            batch_env = dict(env, GIT_INDEX_FILE=str(batch))
            delivery_git.update_candidate_index(root, batch_env, [*entries, ("0", "0" * len(blob), "removed")])
            expected = subprocess.check_output(["git", "write-tree"], cwd=root, env=single_env)
            actual = subprocess.check_output(["git", "write-tree"], cwd=root, env=batch_env)
            self.assertEqual(actual, expected)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertFalse((root / ".git/index").exists())

    def test_execution_seeds_preserve_draft_remote_and_isolate_each_copy(self):
        case = delivery_tests.DeliveryGitTests()
        self.addCleanup(case.doCleanups)
        self.addCleanup(delivery_tests.DeliveryGitTests.tearDownClass)
        with contextlib.redirect_stdout(io.StringIO()):
            first = case.prepare_execution_with_draft_reserved_contracts(runtime=False)
            second = case.prepare_execution_with_draft_reserved_contracts(runtime=False)
        first_root, first_docs, _directory, _item, receipt = first
        second_root, second_docs, _directory, _item, second_receipt = second
        self.assertEqual(receipt, second_receipt)
        self.assertEqual(delivery_git.run_git(second_root / "remote.git", "rev-parse", receipt["refs"]["integration"]), receipt["integration"])
        self.assertEqual(fixture_cache.RepositorySeedCache.snapshot(first_docs),
                         fixture_cache.RepositorySeedCache.snapshot(second_docs))
        changed = first_docs / "operation/verification-contract.md"
        changed.write_text("Only this isolated test changed.\n", encoding="utf-8")
        self.assertNotEqual(changed.read_bytes(), (second_docs / "operation/verification-contract.md").read_bytes())
        self.assertFalse((first_root / ".git/worktrees").exists())
        self.assertFalse((second_root / ".agentrof/agent-marketplace/.runtime").exists())

    def test_fixture_metrics_separate_build_validation_and_copy(self):
        cache = fixture_cache.RepositorySeedCache()
        self.addCleanup(cache.close)

        def build():
            temporary = tempfile.TemporaryDirectory()
            root = Path(temporary.name)
            (root / "workspace/docs").mkdir(parents=True)
            (root / "seed").write_text("immutable\n", encoding="utf-8")
            return temporary, root, root / "workspace/docs"

        before = fixture_cache.phase_totals()
        first, _root, _docs = cache.copy(build)
        self.addCleanup(remove_temporary, first)
        initial = fixture_cache.phase_totals()
        second, _root, _docs = cache.copy(build)
        self.addCleanup(remove_temporary, second)
        final = fixture_cache.phase_totals()
        self.assertGreater(initial["seed_build"], before["seed_build"])
        self.assertEqual(initial["seed_build"], final["seed_build"])
        self.assertGreater(final["seed_copy"], initial["seed_copy"])
        self.assertGreater(final["seed_validate"], initial["seed_validate"])

    def test_seed_rejects_shared_git_pointer_and_explicit_worktree(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".git").write_text("gitdir: /some/shared/location\n")
            with self.assertRaisesRegex(AssertionError, "shared Git"):
                fixture_cache.RepositorySeedCache.require_pre_start(root)
            (root / ".git").unlink()
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "config", "core.worktree", str(root)], check=True)
            with self.assertRaisesRegex(AssertionError, "override its Git worktree"):
                fixture_cache.RepositorySeedCache.require_pre_start(root)

    def test_snapshot_rejects_links_before_descending(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inside, outside = root / "inside", root / "outside"
            inside.mkdir()
            outside.mkdir()
            (outside / "must-not-read").write_text("external")
            try:
                (inside / "linked").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlink creation unavailable")
            original = Path.read_bytes
            def read(path):
                if path.name == "must-not-read":
                    self.fail("read outside the seed before rejecting link")
                return original(path)
            with mock.patch.object(Path, "read_bytes", read):
                with self.assertRaisesRegex(AssertionError, "links or junctions"):
                    fixture_cache.RepositorySeedCache.snapshot(inside)
                with self.assertRaisesRegex(AssertionError, "links or junctions"):
                    fixture_cache.RepositorySeedCache.snapshot(inside / "linked")

    def test_batch_rejects_invalid_input_before_touching_any_index(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            index = root / "candidate-index"
            env = dict(os.environ, GIT_INDEX_FILE=str(index))
            oid = "a" * 40
            for entries in ([("100644", oid, "../outside")], [("100644", oid, "/absolute")],
                            [("100644", oid, "a//b")], [("100644", oid, "a\0b")],
                            [("100644", "a" * 41, "file")], [("0", oid, "file")],
                            [("100644", oid, "file"), ("100644", oid, "file")]):
                with self.subTest(entries=entries), mock.patch.object(subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "invalid candidate"):
                        delivery_git.update_candidate_index(root, env, entries)
                    run.assert_not_called()
            for invalid in ({}, dict(env, GIT_INDEX_FILE=str(root / ".git/index"))):
                with self.assertRaisesRegex(RuntimeError, "isolated temporary index"):
                    delivery_git.update_candidate_index(root, invalid, [("100644", oid, "file")])
            self.assertFalse(index.exists())

    def test_compiler_instance_builder_override_cannot_reuse_a_seed(self):
        from tools.tests import test_delivery_compile as compiler_tests
        case = compiler_tests.DeliveryCompilerTests()
        with mock.patch.object(case, "build_fixture", side_effect=RuntimeError("explicit builder override")), \
                mock.patch.object(compiler_tests._COMPILER_FIXTURE_CACHE, "copy", side_effect=AssertionError("cache used")):
            with self.assertRaisesRegex(RuntimeError, "explicit builder override"):
                case.setUp()

    def test_failed_execution_seed_construction_removes_its_temporary_repository(self):
        case = delivery_tests.DeliveryGitTests()
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        with mock.patch.object(case, "make_project", return_value=(temporary, root)), \
                mock.patch.object(delivery_tests, "make_approved_backlog", side_effect=RuntimeError("construction failed")):
            with self.assertRaisesRegex(RuntimeError, "construction failed"):
                case.build_execution_fixture()
        self.assertFalse(root.exists())

    def test_windows_emulator_seed_is_exact_context_scoped_and_remains_isolated(self):
        case = delivery_tests.DeliveryGitTests()
        self.addCleanup(case.doCleanups)
        contexts = []
        for _ in range(2):
            with delivery_tests.windows_text_pipes(), contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(case.fixture_cache_context_unchanged())
                first = case.prepare_execution_with_draft_reserved_contracts(False)
                context = delivery_tests._WINDOWS_PIPE_FIXTURE_CONTEXT
                contexts.append(context)
                self.assertEqual(len(context["caches"]), 1)
                built = fixture_cache.phase_totals()["seed_build"]
                second = case.prepare_execution_with_draft_reserved_contracts(False)
                self.assertEqual(fixture_cache.phase_totals()["seed_build"], built)
                self.assertEqual(first[-1], second[-1])
                self.assertNotEqual(first[0], second[0])
                self.assertEqual(delivery_git.run_git(first[0], "remote", "get-url", "origin"), str(first[0] / "remote.git"))
                (first[1] / "operation/verification-contract.md").write_text("isolated")
                self.assertNotEqual((first[1] / "operation/verification-contract.md").read_bytes(),
                                    (second[1] / "operation/verification-contract.md").read_bytes())
            self.assertIsNone(delivery_tests._WINDOWS_PIPE_FIXTURE_CONTEXT)
            self.assertTrue(all(cache.temporary is None for cache in context["caches"].values()))
        self.assertIsNot(contexts[0], contexts[1])

    def test_windows_emulator_cache_cannot_hide_other_setup_mocks(self):
        case = delivery_tests.DeliveryGitTests()
        with delivery_tests.windows_text_pipes(), \
                mock.patch.object(case, "build_execution_fixture", side_effect=RuntimeError("real altered builder")), \
                mock.patch.object(fixture_cache.RepositorySeedCache, "copy", side_effect=AssertionError("cache used")):
            with self.assertRaisesRegex(RuntimeError, "real altered builder"):
                case.prepare_execution_with_draft_reserved_contracts(False)
        with mock.patch.object(subprocess, "run", wraps=subprocess.run), delivery_tests.windows_text_pipes():
            self.assertIsNot(delivery_tests._WINDOWS_PIPE_FIXTURE_CONTEXT["base_run"], delivery_tests._NATIVE_SUBPROCESS_RUN)
            with mock.patch.object(case, "build_execution_fixture", side_effect=RuntimeError("altered pipe source")):
                with self.assertRaisesRegex(RuntimeError, "altered pipe source"):
                    case.prepare_execution_with_draft_reserved_contracts(False)


if __name__ == "__main__":
    unittest.main()
