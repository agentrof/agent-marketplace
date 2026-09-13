"""Incremental approval preserves the exact evidence of unchanged backlog notes."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.tests import backlog_fixture

compiler = backlog_fixture.backlog_compile


class EarlierClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


class BacklogApprovalPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.docs = self.project / "workspace/docs"
        with mock.patch.object(compiler, "datetime", EarlierClock):
            backlog_fixture.make_approved_backlog(self.docs)
        self.root = self.docs / "backlog/backlog.md"
        self.old_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.story = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        self.epic_review = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        self.git("init")
        self.git("config", "user.name", "Jane Doe")
        self.git("config", "user.email", "jane@example.invalid")
        self.commit()

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-m", "Approved backlog fixture")

    def source_bytes(self):
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        return {path: path.read_bytes() for path in compiler.package_paths(record, self.docs)}

    def revise(self):
        props, body = compiler.parse_front_matter(self.root)
        props["revision"] = int(props.get("revision", 1)) + 1
        compiler.status_tag(props, "draft")
        for key in ("approved_at_utc", "source_hash", "package_hash"):
            props.pop(key, None)
        self.root.write_text(compiler.front_matter(props, body))
        props, body = compiler.parse_front_matter(self.old_review)
        props["round"] = 2
        props["aliases"] = ["BACKLOG-REVIEW-002"]
        compiler.status_tag(props, "draft")
        for key in ("approved_at_utc", "source_hash"):
            props.pop(key, None)
        review = self.docs / "backlog/reviews/round-2-backlog-review.md"
        review.write_text(compiler.front_matter(props, body))
        return review

    def approve(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = compiler.approve(SimpleNamespace(docs=str(self.docs)))
        return result, output.getvalue()

    def assert_full_gate(self):
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        self.assertEqual(compiler.approval_findings(record, self.docs), [])
        return record

    def test_first_approval_is_still_complete(self):
        record = self.assert_full_gate()
        self.assertEqual(record["backlog"]["props"]["status"], "approved")
        for path in compiler.package_paths(record, self.docs):
            self.assertEqual(compiler.approval_stamp_findings(path, self.docs), [])
        self.assertEqual(len({compiler.parse_front_matter(path)[0]["approved_at_utc"] for path in compiler.package_paths(record, self.docs)}), 1)

    def test_narrow_revision_preserves_all_unchanged_sources_and_reviews(self):
        previous = self.source_bytes()
        new_review = self.revise()
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        for path, before in previous.items():
            if path != self.root:
                self.assertEqual(path.read_bytes(), before, path)
        self.assertNotEqual(compiler.parse_front_matter(new_review)[0]["approved_at_utc"], compiler.parse_front_matter(self.old_review)[0]["approved_at_utc"])
        self.assert_full_gate()

    def test_changed_current_story_is_restamped_without_changing_other_sources(self):
        previous = self.source_bytes()
        self.revise()
        self.story.write_text(self.story.read_text().replace("Administrative bulk operations", "Administrative batch operations"))
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        for path, before in previous.items():
            if path not in {self.root, self.story}:
                self.assertEqual(path.read_bytes(), before, path)
        props, _body = compiler.parse_front_matter(self.story)
        self.assertNotEqual(props["approved_at_utc"], "2025-01-01T10:00:00+00:00")
        self.assertEqual(props["source_hash"], compiler.digest(self.story))
        self.assert_full_gate()

    def test_changed_current_story_with_rehashed_stamp_is_still_restamped(self):
        self.revise()
        props, body = compiler.parse_front_matter(self.story)
        body = body.replace("Administrative bulk operations", "Administrative batch operations")
        self.story.write_text(compiler.front_matter(props, body))
        props["source_hash"] = compiler.digest(self.story)
        self.story.write_text(compiler.front_matter(props, body))
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assertNotEqual(compiler.parse_front_matter(self.story)[0]["approved_at_utc"], "2025-01-01T10:00:00+00:00")
        self.assert_full_gate()

    def test_navigation_render_preserves_approved_source_bytes_after_parent_title_change(self):
        previous = self.source_bytes()
        self.revise()
        props, body = compiler.parse_front_matter(self.root)
        props["title"] = "Account backlog"
        self.root.write_text(compiler.front_matter(props, body.replace("# Backlog", "# Account backlog")))
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        record = self.assert_full_gate()
        approved = self.source_bytes()
        compiler.render_backlog_navigation(record, self.docs)
        self.assertEqual(self.source_bytes(), approved)
        for path, before in previous.items():
            if path != self.root:
                self.assertEqual(path.read_bytes(), before, path)
        self.assert_full_gate()

    def test_old_or_current_approved_review_cannot_be_resigned(self):
        self.revise()
        for path in (self.old_review, self.epic_review):
            for mode in ("stale", "rehashed", "stripped"):
                with self.subTest(path=path.name, mode=mode):
                    original = path.read_bytes()
                    props, body = compiler.parse_front_matter(path)
                    body = body.replace("supported by", "supported directly by")
                    if mode == "stripped":
                        props.pop("approved_at_utc")
                        props.pop("source_hash")
                    path.write_text(compiler.front_matter(props, body))
                    if mode == "rehashed":
                        props, body = compiler.parse_front_matter(path)
                        props["source_hash"] = compiler.digest(path)
                        path.write_text(compiler.front_matter(props, body))
                    before = compiler.snapshot_tree(self.docs)
                    result, output = self.approve()
                    self.assertEqual(result, 1, output)
                    self.assertIn("prior review approval must remain byte-exact", output)
                    self.assertEqual(compiler.snapshot_tree(self.docs), before)
                    path.write_bytes(original)

    def test_removed_historical_review_is_rejected_before_any_write(self):
        self.revise()
        self.old_review.unlink()
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        self.assertIn("prior review approval was removed or renamed", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_idempotent_approval_does_not_rewrite_any_file(self):
        before = compiler.snapshot_tree(self.docs)
        first, first_output = self.approve()
        second, second_output = self.approve()
        self.assertEqual((first, second), (0, 0))
        self.assertEqual(first_output, second_output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def refresh_package_hash(self):
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        props, body = compiler.parse_front_matter(self.root)
        props["package_hash"] = compiler.package_digest(self.docs, compiler.package_paths(record, self.docs))
        self.root.write_text(compiler.front_matter(props, body))

    def test_idempotent_approval_rejects_rehashed_committed_review(self):
        props, body = compiler.parse_front_matter(self.old_review)
        self.old_review.write_text(compiler.front_matter(props, body.replace("supported by", "supported directly by")))
        props["source_hash"] = compiler.digest(self.old_review)
        self.old_review.write_text(compiler.front_matter(props, body.replace("supported by", "supported directly by")))
        self.refresh_package_hash()
        self.assert_full_gate()
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        self.assertIn("prior review approval must remain byte-exact", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_idempotent_approval_rejects_deleted_historical_review(self):
        self.revise()
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.commit()
        self.old_review.unlink()
        self.refresh_package_hash()
        self.assert_full_gate()
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        self.assertIn("prior review approval was removed or renamed", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_idempotent_approval_accepts_a_new_uncommitted_approved_round(self):
        self.revise()
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        before = compiler.snapshot_tree(self.docs)
        result, repeated = self.approve()
        self.assertEqual(result, 0, repeated)
        self.assertEqual(json.loads(repeated), json.loads(output))
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_idempotent_first_approval_does_not_require_an_initial_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            backlog_fixture.make_approved_backlog(docs)
            before = compiler.snapshot_tree(docs)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = compiler.approve(SimpleNamespace(docs=str(docs)))
            self.assertEqual(result, 0, output.getvalue())
            self.assertEqual(compiler.snapshot_tree(docs), before)

    def test_invalid_utc_timestamp_is_not_accepted_by_full_gate(self):
        props, body = compiler.parse_front_matter(self.story)
        props["approved_at_utc"] = "2025-01-01T12:00:00+02:00"
        self.story.write_text(compiler.front_matter(props, body))
        props["source_hash"] = compiler.digest(self.story)
        self.story.write_text(compiler.front_matter(props, body))
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        self.assertTrue(any("not a UTC timestamp" in finding for finding in compiler.approval_findings(record, self.docs)))

    def test_rolls_back_if_a_preserved_source_changes_during_final_render(self):
        self.revise()
        before = compiler.snapshot_tree(self.docs)
        original_render = compiler.render

        def bad_render(record, docs):
            original_render(record, docs)
            self.old_review.write_bytes(self.old_review.read_bytes() + b"\n")

        with mock.patch.object(compiler, "render", side_effect=bad_render):
            result, output = self.approve()
        self.assertEqual(result, 1, output)
        self.assertIn("unchanged approved source was modified", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_begin_revision_requires_the_approved_head_preimage(self):
        self.story.write_text(self.story.read_text().replace("Administrative bulk operations", "Administrative batch operations"))
        props, body = compiler.parse_front_matter(self.story)
        props["source_hash"] = compiler.digest(self.story)
        self.story.write_text(compiler.front_matter(props, body))
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        props, body = compiler.parse_front_matter(self.root)
        props["package_hash"] = compiler.package_digest(self.docs, compiler.package_paths(record, self.docs))
        self.root.write_text(compiler.front_matter(props, body))
        self.assert_full_gate()
        before = compiler.snapshot_tree(self.docs)
        output = io.StringIO()
        with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            result = compiler.begin_revision(SimpleNamespace(docs=str(self.docs), delivery_snapshot="", planning_mode="requirement", requirement_ref="REQ-001", input_ref=[]))
        self.assertEqual(result, 1)
        self.assertIn("byte-exact in committed HEAD", output.getvalue())
        self.assertEqual(compiler.snapshot_tree(self.docs), before)


if __name__ == "__main__":
    unittest.main()
