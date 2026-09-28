"""Generated backlog views avoid rewrites but always recheck input receipts."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tests import backlog_fixture
from tools.tests.git_fixture import remove_temporary


compiler = backlog_fixture.backlog_compile


class BacklogRenderNoopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.docs = Path(temporary.name).resolve() / "workspace/docs"
        backlog_fixture.make_approved_backlog(self.docs)
        self.record = self.collect()
        self.render()
        self.generated = self.docs / "backlog/_generated"

    def collect(self):
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        return record

    def render(self):
        compiler.render(self.record, self.docs)
        compiler.render_backlog_navigation(self.record, self.docs)

    def snapshot(self):
        return {
            path.relative_to(self.docs): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.docs.rglob("*") if path.is_file()
        }

    def age_files(self):
        for path in self.docs.rglob("*"):
            if path.is_file():
                os.utime(path, ns=(1_600_000_000_000_000_000,) * 2)

    def test_identical_render_and_navigation_preserve_all_bytes_and_mtimes(self):
        self.age_files()
        before = self.snapshot()
        self.record = self.collect()
        with mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected byte write")), mock.patch.object(
            Path, "write_text", side_effect=AssertionError("unexpected text write"),
        ):
            self.render()
        self.assertEqual(self.snapshot(), before)

    def test_changed_story_priority_updates_board(self):
        board = self.generated / "board.md"
        before = board.read_bytes()
        story = self.docs / self.record["stories"][0]["path"]
        props, body = compiler.parse_front_matter(story)
        self.assertEqual(props["priority"], "must")
        props["priority"] = "should"
        story.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.record = self.collect()
        compiler.render(self.record, self.docs)
        self.assertNotEqual(board.read_bytes(), before)
        self.assertIn("| should |", board.read_text(encoding="utf-8"))

    def test_missing_generated_view_is_recreated_without_rewriting_other_files(self):
        board = self.generated / "board.md"
        self.age_files()
        before = self.snapshot()
        board.unlink()
        written = []
        original_write_bytes = Path.write_bytes

        def write_bytes(path, content):
            written.append(path)
            return original_write_bytes(path, content)

        with mock.patch.object(Path, "write_bytes", write_bytes):
            self.render()
        self.assertEqual(written, [board])
        after = self.snapshot()
        board_ref = board.relative_to(self.docs)
        self.assertEqual(after[board_ref][0], before[board_ref][0])
        self.assertEqual(
            {path: state for path, state in after.items() if path != board_ref},
            {path: state for path, state in before.items() if path != board_ref},
        )

    def test_unchanged_sources_recalculate_external_receipt_currency(self):
        # This isolates the render consumer. The real receipt resolver is mocked
        # at its boundary so its external package can become stale between reads.
        record = copy.deepcopy(self.record)
        digest = "sha256:" + "1" * 64
        reference = "solution-design/landscape"
        record["backlog"]["planning_mode"] = "manual"
        record["backlog"]["props"]["input_bindings"] = [
            f"solution-design|{reference}|{digest}"
        ]
        source_before = {
            path: path.read_bytes() for path in compiler.package_paths(record, self.docs)
        }
        record_before = copy.deepcopy(record)
        view = self.generated / "input-package-coverage.md"
        with mock.patch.object(compiler.stage_package, "verify", side_effect=[
            ({"result_ref": reference, "package_hash": digest}, []),
            (None, ["upstream package has changed"]),
        ]) as verify:
            compiler.render(record, self.docs)
            current_view = view.read_bytes()
            self.assertIn(b"| current |", current_view)
            compiler.render(record, self.docs)
        self.assertNotEqual(view.read_bytes(), current_view)
        self.assertIn(b"not current: upstream package has changed", view.read_bytes())
        expected = mock.call(
            self.docs, "solution-design", reference, digest,
            require_committed=True, require_strict_current=True,
        )
        self.assertEqual(verify.call_args_list, [expected, expected])
        self.assertEqual(record, record_before)
        self.assertEqual({path: path.read_bytes() for path in source_before}, source_before)


if __name__ == "__main__":
    unittest.main()
