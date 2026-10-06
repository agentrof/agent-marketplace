"""New level grammar must not trap an intact predecessor outside revision."""

import contextlib
import io
import json
import unittest
from unittest import mock

from tools.tests import test_backlog_revision_atomicity as base

compiler = base.compiler
LEGACY_LEVEL = "unit (historical explanation)"


@base.integration
class BacklogLevelRevisionIntakeTests(unittest.TestCase):
    files = base.BacklogRevisionAtomicityTests.files
    run_revision = base.BacklogRevisionAtomicityTests.run_revision

    def setUp(self):
        author_story = base.fixtures.backlog_fixture._author_story

        def legacy_story(story_path, test_path, story_id):
            author_story(story_path, test_path, story_id)
            text = test_path.read_text(encoding="utf-8")
            text = text.replace("- automation: required", "- level: " + LEGACY_LEVEL
                                + "\n- automation: required")
            test_path.write_text(text, encoding="utf-8")

        # Simulate an earlier compiler that accepted explanatory level text.
        with mock.patch.object(base.fixtures.backlog_fixture, "_author_story", legacy_story), \
                mock.patch.object(compiler, "LEVELS", (*compiler.LEVELS, LEGACY_LEVEL)):
            base.BacklogRevisionAtomicityTests.setUp(self)
        self.plan = next((self.docs / "backlog").rglob("test-plan.md"))

    def test_intact_legacy_predecessor_opens_and_keeps_evidence_exact(self):
        before = self.plan.read_bytes()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        repairs = json.loads(output)["level_repairs_required"]
        self.assertTrue(repairs)
        self.assertEqual(self.plan.read_bytes(), before)
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"):
            _record, errors = compiler.collect(self.docs)
        self.assertTrue(any("level must be one of" in error for error in errors))
        approval_output = io.StringIO()
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(approval_output):
            self.assertEqual(compiler.approve(self.args), 1)
        self.assertIn("level must be one of", approval_output.getvalue())

    def test_changed_legacy_predecessor_still_refuses_without_writes(self):
        self.plan.write_bytes(self.plan.read_bytes() + b"\nUnapproved change.\n")
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("source_hash", output)
        self.assertEqual(self.files(), before)

    def test_an_unrelated_source_error_is_not_deferred(self):
        collect = compiler.collect

        def broken_source(*args, **kwargs):
            record, errors = collect(*args, **kwargs)
            return record, [*errors, "a scenario is missing Given"]

        before = self.files()
        with mock.patch.object(compiler, "collect", side_effect=broken_source):
            result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("missing Given", output)
        self.assertEqual(self.files(), before)

    def test_a_new_level_error_during_revision_rolls_back(self):
        collect = compiler.collect
        calls = 0

        def changed_candidate(*args, **kwargs):
            nonlocal calls
            calls += 1
            record, errors = collect(*args, **kwargs)
            if calls > 1:
                errors.append("new-plan.md scenario NEW-TS-001 level must be one of unit, fixture, live")
            return record, errors

        before = self.files()
        with mock.patch.object(compiler, "collect", side_effect=changed_candidate):
            result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("revision rolled back", output)
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
