"""Failed revision writes restore owned paths without undoing unrelated work."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.tests import test_backlog_requirement_bindings as fixtures

compiler = fixtures.compiler


class BacklogRevisionAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RequirementBindingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.docs = self.fixture.docs
        with contextlib.redirect_stdout(io.StringIO()):
            fixtures.backlog_fixture.make_approved_backlog(self.docs)
        self.fixture.requirement({})
        self.root = self.docs / "backlog/backlog.md"
        self.review = self.docs / "backlog/reviews/round-2-backlog-review.md"
        self.home = self.docs / "home.md"
        self.home.write_text("# Home\n\nKeep the project notes.\n", encoding="utf-8")
        self.unrelated = self.docs / "unrelated.txt"
        self.unrelated.write_bytes(b"Unrelated source.\n")
        fixtures.init_repository(self.fixture.project)
        for args in (["add", "-A"], ["-c", "user.name=Fixture", "-c",
                "user.email=fixture@example.invalid", "commit", "-qm", "Approved fixture"]):
            subprocess.run(["git", *args], cwd=self.fixture.project,
                           check=True, capture_output=True)
        self.args = SimpleNamespace(docs=str(self.docs), delivery_snapshot="",
            planning_mode="requirement", requirement_ref="REQ-001",
            input_ref=[fixtures.BA, fixtures.SOLUTION, fixtures.DESIGN,
                       fixtures.APPLICATION, fixtures.PROCESS])

    def files(self):
        return {path.relative_to(self.docs).as_posix(): path.read_bytes()
                for path in self.docs.rglob("*") if path.is_file()}

    def run_revision(self, *, allow_legacy_experience=False):
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(self.fixture.upstreams())
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(output))
            if allow_legacy_experience:
                # The compatibility seed's screen wikilink predates the modern
                # intake syntax. This override isolates write/render rollback.
                stack.enter_context(mock.patch.object(compiler, "validate_experience_ref"))
            result = compiler.begin_revision(self.args)
        return result, output.getvalue()

    def test_real_post_write_validation_failure_restores_original_package(self):
        before = self.files()
        result, output = self.run_revision()
        self.assertEqual(result, 1, output)
        self.assertIn("experience_ref must be an exact", output)
        self.assertIn("revision rolled back", output)
        self.assertEqual(self.files(), before)
        self.assertFalse(self.review.exists())

    def test_partial_navigation_render_exception_restores_only_owned_paths(self):
        before = self.files()
        original_render = compiler.render_backlog_navigation
        concurrent = self.docs / "unrelated-new.txt"

        def failed_render(record, docs):
            original_render(record, docs)
            self.assertIn("[[maps/backlog|Backlog map]]", self.home.read_text(encoding="utf-8"))
            self.unrelated.write_bytes(b"Concurrent unrelated update.\n")
            concurrent.write_bytes(b"Concurrent new source.\n")
            raise RuntimeError("injected navigation failure")

        with mock.patch.object(compiler, "render_backlog_navigation", side_effect=failed_render):
            result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("revision rolled back: injected navigation failure", output)
        before["unrelated.txt"] = b"Concurrent unrelated update.\n"
        before["unrelated-new.txt"] = b"Concurrent new source.\n"
        self.assertEqual(self.files(), before)

    def test_new_navigation_file_is_removed_when_render_fails(self):
        navigation = self.docs / "maps/backlog.md"
        navigation.unlink()
        before = self.files()
        original_render = compiler.render_backlog_navigation

        def failed_render(record, docs):
            original_render(record, docs)
            self.assertTrue(navigation.is_file())
            raise ValueError("injected invalid navigation")

        with mock.patch.object(compiler, "render_backlog_navigation", side_effect=failed_render):
            result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertFalse(navigation.exists())
        self.assertEqual(self.files(), before)

    def test_review_write_error_restores_the_already_written_root(self):
        before = self.files()
        write_bytes = Path.write_bytes

        def failed_write(path, content):
            if path == self.review:
                raise OSError("injected review write failure")
            return write_bytes(path, content)

        with mock.patch.object(Path, "write_bytes", failed_write):
            result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("revision rolled back: injected review write failure", output)
        self.assertEqual(self.files(), before)

    def test_success_keeps_revision_and_preserves_frozen_story_bytes(self):
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        props, _body = compiler.parse_front_matter(self.root)
        self.assertEqual(props["status"], "draft")
        self.assertEqual(props["revision"], 2)
        self.assertTrue(self.review.is_file())
        for relative, content in before.items():
            if "/stories/" in relative or relative == "unrelated.txt":
                self.assertEqual((self.docs / relative).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
