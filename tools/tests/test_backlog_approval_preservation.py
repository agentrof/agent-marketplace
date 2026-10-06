"""Incremental approval preserves the exact evidence of unchanged backlog notes."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from tools.tests.levels import integration
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.tests import backlog_fixture
from tools.tests.git_fixture import init_repository, remove_temporary

import process_policy

compiler = backlog_fixture.backlog_compile
# The pin of a round written before the project had a Process Policy.
NO_POLICY = {"process_policy_path": "delivery/process-policy.md", "process_policy_revision": 0}


class EarlierClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@integration
class BacklogApprovalPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.project = Path(self.temporary.name).resolve()
        self.docs = self.project / "workspace/docs"
        with mock.patch.object(compiler, "datetime", EarlierClock):
            backlog_fixture.make_approved_backlog(self.docs)
        self.root = self.docs / "backlog/backlog.md"
        self.old_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.story = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        self.epic_review = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        init_repository(self.project)
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

    def test_committed_sources_batch_is_byte_exact_with_unusual_paths(self):
        name = "unicode-ç-space.md" if os.name == "nt" else "unicode-ç-tab\tline\n.md"
        path = self.docs / "backlog" / name
        path.write_bytes(b"\x00\xff\r\n")
        self.commit()
        with mock.patch.object(compiler.subprocess, "run", wraps=subprocess.run) as run:
            committed = compiler.committed_approval_sources(self.project, self.docs)
        self.assertEqual(committed[path], b"\x00\xff\r\n")
        self.assertEqual(len(run.call_args_list), 3)
        self.assertEqual(run.call_args_list[1].args[0][6], self.git("rev-parse", "HEAD").strip())
        self.assertFalse(any("show" in call.args[0] for call in run.call_args_list))
        self.assertEqual(sum("cat-file" in call.args[0] for call in run.call_args_list), 1)
        path.write_bytes(b"working edit")
        self.assertEqual(compiler.committed_approval_sources(self.project, self.docs)[path], b"\x00\xff\r\n")

    def test_committed_sources_rejects_truncated_batch(self):
        actual_run = subprocess.run
        def damaged(argv, **kwargs):
            result = actual_run(argv, **kwargs)
            if "cat-file" in argv:
                result.stdout = result.stdout[:-1]
            return result
        with mock.patch.object(compiler.subprocess, "run", side_effect=damaged):
            with self.assertRaises(ValueError):
                compiler.committed_approval_sources(self.project, self.docs)
        # A failed read is not kept.
        self.assertEqual(compiler.committed_approval_sources(self.project, self.docs)[self.root],
                         self.root.read_bytes())

    def git_runs(self):
        return mock.patch.object(compiler.subprocess, "run", wraps=subprocess.run)

    @staticmethod
    def subcommands(run):
        return [next(arg for arg in call.args[0][1:] if not arg.startswith("-")) for call in run.call_args_list]

    def history_reads(self, commit):
        relative = self.root.relative_to(self.project).as_posix()
        return (compiler.committed_approval_sources(self.project, self.docs, commit),
                compiler.history_listing(self.project, commit, relative),
                compiler.history_blob(self.project, commit, relative))

    def test_reading_a_commit_again_starts_no_git_process(self):
        head = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(compiler.history_commits(self.project, self.root), [head])
        with self.git_runs() as run:
            first = self.history_reads(head)
        # git log printed the full object id, so no read resolves it again.
        self.assertEqual(self.subcommands(run), ["ls-tree", "cat-file", "ls-tree", "cat-file"])
        self.assertEqual(first[2], self.root.read_bytes())
        with self.git_runs() as run:
            self.assertEqual(self.history_reads(head), first)
        self.assertEqual(run.call_args_list, [])
        # HEAD is resolved on every call; only the reads below its resolution are kept.
        with self.git_runs() as run:
            self.assertEqual(self.history_reads("HEAD"), first)
        self.assertEqual(self.subcommands(run), ["rev-parse"] * 3)

    def test_a_moved_head_is_read_fresh(self):
        old_head = self.git("rev-parse", "HEAD").strip()
        before = self.history_reads("HEAD")
        self.root.write_bytes(self.root.read_bytes() + b"\nA committed edit.\n")
        self.commit()
        with self.git_runs() as run:
            after = self.history_reads("HEAD")
        self.assertEqual(self.subcommands(run), ["rev-parse", "ls-tree", "cat-file",
                                                 "rev-parse", "ls-tree", "rev-parse", "cat-file"])
        self.assertEqual(after[0][self.root], self.root.read_bytes())
        self.assertEqual(after[2], self.root.read_bytes())
        self.assertNotEqual(after[1], before[1])
        self.assertEqual(self.history_reads(old_head), before)

    def test_kept_reads_equal_fresh_reads(self):
        head = self.git("rev-parse", "HEAD").strip()
        self.history_reads(head)
        with self.git_runs() as run:
            kept = self.history_reads(head)
        self.assertEqual(run.call_args_list, [])
        with mock.patch.dict(compiler._GIT_OBJECT_READS, clear=True), self.git_runs() as run:
            fresh = self.history_reads(head)
        self.assertEqual(self.subcommands(run), ["rev-parse", "ls-tree", "cat-file", "ls-tree", "cat-file"])
        self.assertEqual(kept, fresh)
        self.assertEqual(list(kept[0].items()), list(fresh[0].items()))

    def test_another_spelling_of_the_project_keeps_its_paths_without_a_new_read(self):
        head = self.git("rev-parse", "HEAD").strip()
        sources = compiler.committed_approval_sources(self.project, self.docs, head)
        alias = self.project / "workspace" / ".."
        with self.git_runs() as run:
            aliased = compiler.committed_approval_sources(alias, alias / "workspace/docs", head)
        self.assertEqual(run.call_args_list, [])
        self.assertEqual([(path.relative_to(alias), content) for path, content in aliased.items()],
                         [(path.relative_to(self.project), content) for path, content in sources.items()])

    def test_a_caller_cannot_change_a_kept_read(self):
        head = self.git("rev-parse", "HEAD").strip()
        sources, listing, blob = self.history_reads(head)
        expected = (dict(sources), list(listing), blob)
        sources[self.root] = b"changed"
        sources.pop(self.story)
        sources[self.project / "added.md"] = b"added"
        listing[:] = [b"changed"]
        self.assertIs(type(blob), bytes)
        self.assertEqual(self.history_reads(head), expected)
        self.assertEqual(self.history_reads("HEAD"), expected)

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

    @contextlib.contextmanager
    def fixture_input_selection(self):
        # Keep the test's legacy upstream fixtures while exercising real backlog
        # collection, revision writes, draft structure and approval readiness.
        with mock.patch.object(compiler, "resolve_manual_input_bindings", return_value=([], [])), mock.patch.object(
            compiler, "planning_package_findings", return_value=("", [], []),
        ):
            yield

    def render_relations(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = compiler.vault_check.cmd_render_relations(
                SimpleNamespace(vault=self.docs), compiler.vault_check.load_policy(compiler.POLICY_PATH),
            )
        self.assertEqual(code, 0, output.getvalue())

    def test_begin_revision_creates_a_unique_pending_review_and_retains_structured_context(self):
        acceptance = self.docs / "business-analysis/delivery/domains/identity/acceptance/delivery-acceptance.md"
        acceptance.write_text(acceptance.read_text() + "\n| AC-DEL-002 | Administrative delegation can be requested. |\n")
        registry_path = self.docs / "business-analysis/delivery/_generated/registry.json"
        registry = json.loads(registry_path.read_text())
        registry["ids"]["AC-DEL-002"] = {**registry["ids"]["AC-DEL-001"]}
        registry_path.write_text(json.dumps(registry))
        props, body = compiler.parse_front_matter(self.old_review)
        deferred_row = (
            "| [[business-analysis/delivery/domains/identity/acceptance/delivery-acceptance\\|delivery:AC-DEL-002]] "
            "| product_owner | Administrative delegation belongs to a later approved scope. "
            "| Revisit when the owner approves the administration requirement. |"
        )
        body = body.replace("|---|---|---|---|", "|---|---|---|---|\n" + deferred_row)
        requirement_context = "## Requirement Coverage\n\n| Requirement | Receipt |\n|---|---|\n| REQ-001 | application@r1 |\n"
        body = body.replace(compiler.NAV_MARKER, requirement_context + "\n" + compiler.NAV_MARKER)
        self.old_review.write_text(compiler.front_matter(props, body))
        props["source_hash"] = compiler.digest(self.old_review)
        self.old_review.write_text(compiler.front_matter(props, body))
        self.refresh_package_hash()
        self.assert_full_gate()
        self.commit()
        original = self.source_bytes()
        output = io.StringIO()
        with self.fixture_input_selection(), contextlib.redirect_stdout(output):
            code = compiler.begin_revision(SimpleNamespace(
                docs=str(self.docs), delivery_snapshot="", planning_mode="manual",
                requirement_ref="", input_ref=["ba", "solution", "design", "application"],
            ))
            record, errors = compiler.collect(self.docs, review_inputs=True)
            _record, strict_errors = compiler.collect(self.docs)
            check_output = io.StringIO()
            with contextlib.redirect_stdout(check_output):
                check_code = compiler.main(["check", "--docs", str(self.docs), "--json"])
            approval_code, approval_output = self.approve()
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(errors, [])
        self.assertTrue(any("needs section-specific" in error for error in strict_errors), strict_errors)
        self.assertEqual(check_code, 1, check_output.getvalue())
        self.assertIn("needs section-specific", check_output.getvalue())
        self.assertEqual(approval_code, 1, approval_output)
        new_review = self.docs / "backlog/reviews/round-2-backlog-review.md"
        review_props, review_body = compiler.parse_front_matter(new_review)
        self.assertEqual(review_props["title"], "Backlog review round 2 for Product Backlog")
        self.assertIn("# " + review_props["title"] + "\n", review_body)
        self.assertEqual(review_props["aliases"], ["BACKLOG-REVIEW-002"])
        self.assertEqual(review_props["status"], "draft")
        self.assertNotIn("verdict", review_props)
        for key in ("approved_at_utc", "source_hash"):
            self.assertNotIn(key, review_props)
        self.assertIn("TODO: cite the exact reviewed vault note", compiler.section(review_body, "Verdict"))
        self.assertNotEqual(compiler.section(review_body, "Verdict"), compiler.section(body, "Verdict"))
        self.assertEqual(compiler.raw_section(review_body, "Deferred Criteria").strip(),
                         compiler.raw_section(body, "Deferred Criteria").strip())
        self.assertIn(deferred_row, review_body)
        self.assertNotIn("Requirement Coverage", compiler.headings(review_body))
        self.assertNotIn("is supported by the cited inputs", review_body)
        self.assertIn("[[backlog/reviews/round-1-backlog-review", review_body)
        for key in ("derives_from", "related_to", "dependency_refs"):
            self.assertEqual(review_props[key], props[key])
        self.assertIn("latest cross-epic backlog review verdict is not approved", compiler.approval_readiness_findings(record))
        for path, content in original.items():
            if path != self.root:
                self.assertEqual(path.read_bytes(), content, path)
        vault = compiler.vault_check.build_vault(self.docs, compiler.vault_check.load_policy(compiler.POLICY_PATH))
        findings = []
        compiler.vault_check.check_title_shape(vault, findings)
        compiler.vault_check.check_frontmatter_props(vault, findings)
        affected = {new_review.relative_to(self.docs).as_posix()}
        self.assertEqual([finding for finding in findings if finding.path in affected], [])

    def test_generated_epic_relation_update_keeps_approval_and_other_source_bytes(self):
        self.render_relations()
        self.assert_full_gate()
        self.commit()
        original = self.source_bytes()
        epic = self.docs / "backlog/epics/delivery-fixture/epic.md"
        before_props, before_body = compiler.parse_front_matter(epic)
        self.revise()
        self.render_relations()
        rendered = epic.read_bytes()
        self.assertNotEqual(rendered, original[epic])
        after_props, after_body = compiler.parse_front_matter(epic)
        self.assertEqual(after_props, before_props)
        self.assertEqual(compiler.without_generated_relations(after_body), compiler.without_generated_relations(before_body))
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assertEqual(epic.read_bytes(), rendered)
        for path, content in original.items():
            if path not in {self.root, epic}:
                self.assertEqual(path.read_bytes(), content, path)
        self.assert_full_gate()
        vault = compiler.vault_check.build_vault(self.docs, compiler.vault_check.load_policy(compiler.POLICY_PATH))
        findings = []
        compiler.vault_check.check_relation_projections(vault, findings)
        self.assertEqual(findings, [])

    def test_authored_epic_change_is_not_preserved_as_a_generated_relation_update(self):
        self.render_relations()
        self.commit()
        self.revise()
        self.render_relations()
        epic = self.docs / "backlog/epics/delivery-fixture/epic.md"
        old_props, body = compiler.parse_front_matter(epic)
        body = body.replace("# Delivery Fixture", "# Delivery Fixture\n\nThe revised scope includes administrative account delegation.")
        epic.write_text(compiler.front_matter(old_props, body))
        self.assertNotEqual(old_props["source_hash"], compiler.digest(epic))
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        props, _body = compiler.parse_front_matter(epic)
        self.assertNotEqual(props["approved_at_utc"], old_props["approved_at_utc"])
        self.assertNotEqual(props["source_hash"], old_props["source_hash"])
        self.assertEqual(props["source_hash"], compiler.digest(epic))
        self.assert_full_gate()

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

    def policy(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = process_policy.main([argv[0], "--docs", str(self.docs), *argv[1:]])
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())

    def pin_of(self, path):
        props, _body = compiler.parse_front_matter(path)
        return {key: props[key] for key in process_policy.PIN_FIELDS if key in props}

    def new_epic_review_round(self):
        props, body = compiler.parse_front_matter(self.epic_review)
        props["round"] = 2
        props["aliases"] = ["EP-001-REVIEW-002"]
        compiler.status_tag(props, "draft")
        for key in ("approved_at_utc", "source_hash"):
            props.pop(key, None)
        review = self.epic_review.with_name("round-2-epic-review.md")
        review.write_text(compiler.front_matter(props, body))
        return review

    def approve_under_policy(self):
        """Approve a revision with a new root and epic review under an approved policy."""
        self.policy("init")
        self.policy("set", "--switch", "review_panels", "--value", "lens_panel")
        self.policy("approve")
        self.commit()
        pin, errors = process_policy.approved_snapshot(self.docs)
        self.assertEqual(errors, [])
        self.assertEqual(pin["process_policy_revision"], 1)
        reviews = [self.revise(), self.new_epic_review_round()]
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        return pin, reviews

    def test_approval_records_the_process_policy_pin_in_the_root_and_each_review_it_approves(self):
        previous = self.source_bytes()
        # The fixture was approved without a policy, so nothing records one.
        self.assertEqual([path for path in previous if self.pin_of(path)], [])
        pin, reviews = self.approve_under_policy()
        record = self.assert_full_gate()
        for path in [self.root, *reviews]:
            self.assertEqual(self.pin_of(path), pin, path)
        props = list(compiler.parse_front_matter(self.root)[0])
        start = props.index("approved_at_utc") + 1
        self.assertEqual(props[start:start + 3], list(process_policy.PIN_FIELDS))
        # The vault already declares the pin properties a Delivery records.
        vault = compiler.vault_check.build_vault(self.docs, compiler.vault_check.load_policy(compiler.POLICY_PATH))
        findings = []
        compiler.vault_check.check_frontmatter_props(vault, findings)
        self.assertEqual([finding for finding in findings
                          if any(key in finding.message for key in process_policy.PIN_FIELDS)], [])
        # Epics, stories and test plans record none; the reviews approved
        # before the policy keep their bytes and their record of no policy.
        for path in compiler.package_paths(record, self.docs):
            if path not in {self.root, *reviews}:
                self.assertEqual(self.pin_of(path), {}, path)
        for path in (self.old_review, self.epic_review):
            self.assertEqual(path.read_bytes(), previous[path])
        # The pin records what the revision ran under; a later policy never stales it.
        self.policy("begin-revision")
        self.policy("set", "--switch", "review_panels", "--default")
        self.policy("approve")
        self.assert_full_gate()
        self.assertEqual(self.pin_of(self.root), pin)

    def test_a_new_revision_drops_the_root_pin_and_pins_its_new_round(self):
        pin, _reviews = self.approve_under_policy()
        self.commit()
        output = io.StringIO()
        with self.fixture_input_selection(), contextlib.redirect_stdout(output):
            code = compiler.begin_revision(SimpleNamespace(
                docs=str(self.docs), delivery_snapshot="", planning_mode="manual",
                requirement_ref="", input_ref=["ba", "solution", "design", "application"],
            ))
        self.assertEqual(code, 0, output.getvalue())
        # The root's pin belongs to the approval stamp; the new round records
        # the policy in force as begin-revision writes it.
        self.assertEqual(self.pin_of(self.root), {})
        self.assertEqual(self.pin_of(self.docs / "backlog/reviews/round-3-backlog-review.md"), pin)

    def next_round(self, review):
        """Write the next round of a review as the Product Owner does, by hand."""
        props, body = compiler.parse_front_matter(review)
        number = int(props["round"]) + 1
        props = compiler.without_policy_pin(props)
        props["round"] = number
        props["aliases"] = [props["aliases"][0].rsplit("-", 1)[0] + f"-{number:03d}"]
        compiler.status_tag(props, "draft")
        for key in ("approved_at_utc", "source_hash"):
            props.pop(key, None)
        path = review.with_name(review.name.replace(f"round-{number - 1}-", f"round-{number}-"))
        path.write_text(compiler.front_matter(props, body))
        return path

    def check(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = compiler.main(["check", "--docs", str(self.docs), "--json"])
        return code, json.loads(output.getvalue())

    def approve_policy(self, *row):
        self.policy("begin-revision" if process_policy.path_for(self.docs).exists() else "init")
        for switch in process_policy.table_rows(
                process_policy.parse(process_policy.path_for(self.docs))[1])[0]:
            self.policy("set", "--switch", switch, "--default")
        if row:
            self.policy("set", "--switch", row[0], "--value", row[1])
        self.policy("approve")
        pin, errors = process_policy.approved_snapshot(self.docs)
        self.assertEqual(errors, [])
        return pin

    def relative(self, paths):
        return [path.relative_to(self.docs).as_posix() for path in paths]

    def test_a_round_the_owner_writes_is_pinned_when_a_check_first_sees_it(self):
        first = self.approve_policy("review_panels", "lens_panel")
        reviews = [self.revise(), self.new_epic_review_round()]
        approved = {path: path.read_bytes() for path in (self.old_review, self.epic_review)}
        code, result = self.check()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["pinned_reviews"],
                         [path.relative_to(self.docs).as_posix() for path in reviews])
        for path in reviews:
            self.assertEqual(self.pin_of(path), first, path)
        # An approved round is never touched, and a pinned one is never pinned again.
        for path, data in approved.items():
            self.assertEqual(path.read_bytes(), data)
        second = self.approve_policy()
        pinned = {path: path.read_bytes() for path in reviews}
        code, result = self.check()
        self.assertNotIn("pinned_reviews", result)
        self.assertEqual({path: path.read_bytes() for path in reviews}, pinned)
        self.assertNotEqual(first, second)
        # The status reader never writes.
        with contextlib.redirect_stdout(io.StringIO()):
            compiler.main(["status", "--docs", str(self.docs), "--json"])
        self.assertEqual({path: path.read_bytes() for path in reviews}, pinned)

    def test_rounds_written_before_the_first_policy_record_that_they_ran_under_none(self):
        reviews = [self.revise(), self.new_epic_review_round()]
        # Without a policy a check writes nothing.
        before = compiler.snapshot_tree(self.docs)
        code, result = self.check()
        self.assertEqual(code, 0, result)
        self.assertNotIn("pinned_reviews", result)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)
        # The first policy's init records it before the policy exists, so the
        # first check under that policy no longer claims them for it.
        self.assertEqual(self.policy("init")["pinned_reviews"], self.relative(reviews))
        for path in reviews:
            self.assertEqual(self.pin_of(path), NO_POLICY, path)
        self.policy("set", "--switch", "review_panels", "--value", "lens_panel")
        self.policy("approve")
        code, result = self.check()
        self.assertEqual(code, 0, result)
        self.assertNotIn("pinned_reviews", result)
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        for path in self.relative(reviews):
            self.assertIn(f"{path} records no Process Policy, but the approval runs under Process"
                          " Policy revision 1; write a new review round and rerun its review"
                          " under the current Process Policy, or approve a Process Policy"
                          " revision that sets back the values it ran under: switch"
                          " review_panels ran at single_reader and the approval's policy sets"
                          " lens_panel", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_a_first_policy_that_keeps_the_backlog_defaults_agrees_with_rounds_before_it(self):
        reviews = [self.revise(), self.new_epic_review_round()]
        # The owner's first policy sets only a switch no backlog flow owns.
        self.policy("init")
        self.policy("set", "--switch", "owner_gates", "--value", "two_fixed_gates")
        self.policy("approve")
        pin, _errors = process_policy.approved_snapshot(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assert_full_gate()
        self.assertEqual(self.pin_of(self.root), pin)
        for path in reviews:
            self.assertEqual(self.pin_of(path), NO_POLICY, path)
        # The vault reads revision 0 as the number the property declares.
        vault = compiler.vault_check.build_vault(self.docs, compiler.vault_check.load_policy(compiler.POLICY_PATH))
        findings = []
        compiler.vault_check.check_frontmatter_props(vault, findings)
        self.assertEqual([finding for finding in findings
                          if any(key in finding.message for key in process_policy.PIN_FIELDS)], [])

    def test_an_approval_accepts_a_review_whose_backlog_switches_a_revision_kept(self):
        first = self.approve_policy("review_panels", "lens_panel")
        self.commit()
        reviews = [self.revise(), self.new_epic_review_round()]
        code, result = self.check()
        self.assertEqual(result["pinned_reviews"], self.relative(reviews))
        # The owner then sets a switch that only Delivery flows own.
        self.policy("begin-revision")
        self.policy("set", "--switch", "owner_gates", "--value", "two_fixed_gates")
        self.policy("approve")
        second, _errors = process_policy.approved_snapshot(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assert_full_gate()
        # The root records the approval's revision; each review keeps the one it ran under.
        self.assertEqual(self.pin_of(self.root), second)
        for path in reviews:
            self.assertEqual(self.pin_of(path), first, path)

    def test_an_approval_refuses_a_review_whose_pinned_revision_cannot_be_read_back(self):
        # Revision 1 is never committed, so once revision 2 replaces it no
        # file holds its values.
        self.approve_policy("review_panels", "lens_panel")
        reviews = [self.revise(), self.new_epic_review_round()]
        code, result = self.check()
        self.assertEqual(code, 0, result)
        self.policy("begin-revision")
        self.policy("set", "--switch", "owner_gates", "--value", "two_fixed_gates")
        self.policy("approve")
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        for path in self.relative(reviews):
            self.assertIn(f"{path} records Process Policy revision 1, whose switch values this"
                          " approval cannot read back to compare: the pinned Process Policy"
                          " revision 1", output)
        self.assertIn("or write a new review round and rerun its review under the current"
                      " Process Policy", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)

    def test_a_policy_revision_records_the_revision_a_round_no_check_saw_ran_under(self):
        first = self.approve_policy("review_panels", "lens_panel")
        self.commit()
        # The Product Owner writes both rounds and no check runs before the
        # owner revises the policy.
        reviews = [self.revise(), self.new_epic_review_round()]
        self.assertEqual(self.policy("begin-revision")["pinned_reviews"], self.relative(reviews))
        for path in reviews:
            self.assertEqual(self.pin_of(path), first, path)
        self.policy("set", "--switch", "review_panels", "--default")
        self.policy("approve")
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        for path in self.relative(reviews):
            self.assertIn(f"{path} records Process Policy revision 1, but the approval runs under"
                          " Process Policy revision 2;", output)
        self.assertIn("switch review_panels ran at lens_panel and the approval's policy sets"
                      " single_reader", output)

    def test_an_approval_refuses_a_review_that_ran_under_another_policy(self):
        first = self.approve_policy("review_panels", "lens_panel")
        self.commit()
        reviews = [self.revise(), self.new_epic_review_round()]
        code, result = self.check()
        self.assertEqual(code, 0, result)
        # The owner revises the policy after the reviews ran and before approval.
        second = self.approve_policy()
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        for path in reviews:
            self.assertIn(f"{path.relative_to(self.docs).as_posix()} records Process Policy"
                          " revision 1, but the approval runs under Process Policy revision 2;"
                          " write a new review round and rerun its review under the current"
                          " Process Policy", output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)
        # New rounds rerun under the current policy approve; the earlier
        # rounds keep the pin of the policy they ran under.
        rerun = [self.next_round(path) for path in reviews]
        code, result = self.check()
        self.assertEqual(result["pinned_reviews"],
                         [path.relative_to(self.docs).as_posix() for path in rerun])
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assert_full_gate()
        for path in (self.root, *rerun):
            self.assertEqual(self.pin_of(path), second, path)
        for path in reviews:
            self.assertEqual(self.pin_of(path), first, path)

    def test_rounds_the_compiler_writes_record_the_policy_in_force(self):
        pin = self.approve_policy("review_panels", "lens_panel")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.stub_epic(SimpleNamespace(
                docs=str(self.docs), slug="second", id="EP-002", title="Second",
                goal="Deliver a separate customer outcome.")), 0)
        self.assertEqual(self.pin_of(self.docs / "backlog/epics/second/reviews/round-1-epic-review.md"),
                         pin)
        fresh = self.project / "fresh" / "workspace" / "docs"
        (fresh / "maps").mkdir(parents=True)
        self.policy_at(fresh, "init")
        self.policy_at(fresh, "set", "--switch", "review_panels", "--value", "lens_panel")
        self.policy_at(fresh, "approve")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.init(SimpleNamespace(docs=str(fresh))), 0)
        self.assertEqual(self.pin_of(fresh / "backlog/reviews/round-1-backlog-review.md"),
                         process_policy.approved_snapshot(fresh)[0])
        # A draft policy records nothing; the first check after its approval pins the round.
        self.policy("begin-revision")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.stub_epic(SimpleNamespace(
                docs=str(self.docs), slug="third", id="EP-003", title="Third",
                goal="Deliver a third customer outcome.")), 0)
        self.assertEqual(self.pin_of(self.docs / "backlog/epics/third/reviews/round-1-epic-review.md"),
                         {})

    def policy_at(self, docs, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = process_policy.main([argv[0], "--docs", str(docs), *argv[1:]])
        self.assertEqual(code, 0, output.getvalue())

    def test_a_draft_process_policy_refuses_a_new_approval_before_any_write(self):
        self.policy("init")
        # Re-approving the approved revision records nothing new, so it still passes.
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 0, output)
        self.assertEqual(compiler.snapshot_tree(self.docs), before)
        self.revise()
        before = compiler.snapshot_tree(self.docs)
        result, output = self.approve()
        self.assertEqual(result, 1, output)
        self.assertIn("Process Policy revision 1 is a draft", output)
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
