"""Revision intake preserves approved history while current coverage still gates review."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from tools.tests import test_backlog_revision_atomicity as fixtures

compiler = fixtures.compiler
import backlog_review_inputs as inputs
import process_policy
import task_inputs


class BacklogUpstreamTransitionTests(unittest.TestCase):
    files = fixtures.BacklogRevisionAtomicityTests.files
    run_revision = fixtures.BacklogRevisionAtomicityTests.run_revision

    def setUp(self):
        fixtures.BacklogRevisionAtomicityTests.setUp(self)
        self.story = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        self.plan = self.story.with_name("test-plan.md")
        self.decision = self.docs / "solution-design/decisions/fixture-api.md"
        receipt = self.docs / "business-analysis/core/space.md"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("# Approved input boundary\n", encoding="utf-8")
        evidence = "[[solution-design/decisions/fixture-api|API decision]]"
        for path in (self.story, self.plan):
            props, body = compiler.parse_front_matter(path)
            props["related_to"] = [evidence]
            compiler.status_tag(props, "planned" if path == self.story else "draft")
            for key in compiler.APPROVAL_FIELDS:
                props.pop(key, None)
            if path == self.story:
                props["work_kind"] = "technical"
            else:
                body = body.replace("- source_refs:", f"- source_refs:\n  - {evidence}", 1)
            path.write_text(compiler.front_matter(props, body), encoding="utf-8")
        props, body = compiler.parse_front_matter(self.root)
        props["analysis_scopes"] = ["delivery"]
        compiler.status_tag(props, "draft")
        for key in compiler.APPROVAL_FIELDS:
            props.pop(key, None)
        self.root.write_text(compiler.front_matter(props, body), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = compiler.approve(self.args)
        self.assertEqual(result, 0, output.getvalue())
        self.commit("Approved evidence boundary")

    def commit(self, message):
        for args in (["add", "-A"], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", message]):
            subprocess.run(["git", *args], cwd=self.fixture.project, check=True, capture_output=True)

    def evolve_inputs(self):
        props, body = compiler.parse_front_matter(self.decision)
        props["status"] = "superseded"
        self.decision.write_text(compiler.front_matter(props, body), encoding="utf-8")
        acceptance = self.docs / "business-analysis/delivery/domains/identity/acceptance/delivery-acceptance.md"
        acceptance.write_text(acceptance.read_text(encoding="utf-8") +
                              "\nA second observable outcome. ^AC-DEL-002\n", encoding="utf-8")
        registry_path = self.docs / "business-analysis/delivery/_generated/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["ids"]["AC-DEL-002"] = dict(registry["ids"]["AC-DEL-001"])
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        self.commit("Approved upstream evolution")

    def run_epic_review(self, slug="delivery-fixture"):
        output = io.StringIO()
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(output):
            code = compiler.main(["stub-epic", slug, "--docs", str(self.docs), "--new-review"])
        return code, json.loads(output.getvalue())

    def writer_task(self, review):
        candidates = [{"result_ref": ref, "path": str(self.docs / "experience-design/experiences/checkout/experience.md")}
                      for ref in (fixtures.fixtures.APPLICATION, fixtures.fixtures.PROCESS)]
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                mock.patch.object(compiler.stage_package, "candidates", return_value=candidates):
            return task_inputs.manifest(entry="backlog-plan", role="product-owner", mode="revise",
                project=self.fixture.project, epic="EP-001",
                inputs=[review.relative_to(self.fixture.project).as_posix()])

    def test_new_epic_review_materializes_fresh_current_policy_round_for_writer_task(self):
        with contextlib.redirect_stdout(io.StringIO()):
            for arguments in (("init", "--docs", str(self.docs)),
                              ("set", "--docs", str(self.docs), "--switch", "review_loop", "--value", "blocking_delta"),
                              ("approve", "--docs", str(self.docs))):
                self.assertEqual(process_policy.main(list(arguments)), 0)
        self.commit("Approved review policy")
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        review = self.docs / "backlog/epics/delivery-fixture/reviews/round-2-epic-review.md"
        with self.assertRaisesRegex(ValueError, "required input is missing"):
            task_inputs.regular(self.fixture.project, review.relative_to(self.fixture.project).as_posix())
        before = self.files()
        result, output = self.run_epic_review()
        self.assertEqual(result, 0, output)
        self.assertTrue(output["created"])
        props, body = compiler.parse_front_matter(review)
        self.assertEqual(props["round"], 2)
        self.assertEqual(props["status"], "draft")
        self.assertEqual({key: props[key] for key in process_policy.PIN_FIELDS},
                         process_policy.approved_snapshot(self.docs)[0])
        for field in ("verdict", "source_hash", "approved_at_utc"):
            self.assertNotIn(field, props)
        self.assertIn("TODO: cite the exact reviewed vault note", body)
        self.assertIn("[[backlog/epics/delivery-fixture/reviews/round-1-epic-review", body)
        self.assertNotIn("is supported by the cited inputs", body)
        after = self.files()
        self.assertEqual(set(after) - set(before), {review.relative_to(self.docs).as_posix()})
        self.assertEqual({path: after[path] for path in before}, before)
        task = self.writer_task(review)
        self.assertEqual(task["backlog_scope"]["review"]["path"], review.relative_to(self.docs).as_posix())
        expected = task["backlog_scope"]["review"]["expected_relations"]
        for field in ("derives_from", "verifies"):
            errors = []
            self.assertEqual(sorted(compiler.link_targets(self.docs, props, field, field, errors)), expected[field])
            self.assertEqual(errors, [])
        for field in ("scenario_refs", "dependency_refs"):
            self.assertEqual(compiler.values(props, field), expected[field])
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.main(["check", "--docs", str(self.docs), "--json"]), 1)
            self.assertEqual(compiler.approve(self.args), 1)

    def test_new_epic_review_retry_preserves_pending_round_bytes(self):
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        self.assertEqual(self.run_epic_review()[0], 0)
        before = self.files()
        result, output = self.run_epic_review()
        self.assertEqual(result, 0, output)
        self.assertFalse(output["created"])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.docs / "backlog/epics/delivery-fixture/reviews/round-3-epic-review.md").exists())

    def test_new_epic_review_accepts_known_story_scaffolds_and_exact_membership(self):
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.main(["stub-story", "delivery-fixture", "second", "--docs", str(self.docs),
                "--id", "ST-002", "--work-kind", "technical",
                "--evidence-ref", "[[solution-design/decisions/fixture-api|API decision]]"]), 0)
        before = self.files()
        result, output = self.run_epic_review()
        self.assertEqual(result, 0, output)
        review = Path(output["review"])
        task = self.writer_task(review)
        self.assertTrue(task["backlog_scope"]["check"]["scaffold_findings"])
        self.assertIn("backlog/epics/delivery-fixture/stories/second/story",
                      task["backlog_scope"]["review"]["expected_relations"]["verifies"])
        props, _body = compiler.parse_front_matter(review)
        self.assertEqual(set(compiler.values(props, "scenario_refs")), {"AUTH-01-TS-001", "ST-002-TS-001"})
        self.assertEqual({path: self.files()[path] for path in before}, before)

    def test_new_epic_review_refuses_invalid_root_scope_and_source_without_writes(self):
        before = self.files()
        self.root.unlink()
        missing_root = self.files()
        self.assertEqual(self.run_epic_review()[0], 1)
        self.assertEqual(self.files(), missing_root)
        self.root.write_bytes(before["backlog/backlog.md"])
        result, output = self.run_epic_review()
        self.assertEqual(result, 1, output)
        self.assertIn("requires a draft backlog", output["errors"][0])
        self.assertEqual(self.files(), before)
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        before = self.files()
        self.assertEqual(self.run_epic_review("missing")[0], 1)
        self.assertEqual(self.files(), before)
        props, body = compiler.parse_front_matter(self.story)
        props["owner_role"] = "unknown_role"
        self.story.write_text(compiler.front_matter(props, body), encoding="utf-8")
        before = self.files()
        result, output = self.run_epic_review()
        self.assertEqual(result, 1, output)
        self.assertIn("owner_role", output["errors"][0])
        self.assertEqual(self.files(), before)

    def test_new_epic_review_rejects_rehashed_prior_review_without_writes(self):
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        old = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        props, body = compiler.parse_front_matter(old)
        body = body.replace(f"# {props['title']}\n", f"# {props['title']}\n\nA new interpretation of the reviewed scope.\n", 1)
        props["source_hash"] = compiler.digest_text(compiler.front_matter(props, body))
        old.write_text(compiler.front_matter(props, body), encoding="utf-8")
        before = self.files()
        result, output = self.run_epic_review()
        self.assertEqual(result, 1, output)
        self.assertIn("prior review approval must remain byte-exact", output["errors"][0])
        self.assertEqual(self.files(), before)

    def test_new_epic_review_postwrite_membership_failure_removes_only_new_round(self):
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        before = self.files()
        review = self.docs / "backlog/epics/delivery-fixture/reviews/round-2-epic-review.md"
        original_collect = compiler.collect

        def damaged(*args, **kwargs):
            if review.exists():
                props, body = compiler.parse_front_matter(review)
                props.pop("verifies")
                review.write_text(compiler.front_matter(props, body), encoding="utf-8")
            return original_collect(*args, **kwargs)

        with mock.patch.object(compiler, "collect", side_effect=damaged):
            result, output = self.run_epic_review()
        self.assertEqual(result, 1, output)
        self.assertIn("verifies set does not exactly cover", output["errors"][0])
        self.assertEqual(self.files(), before)

    def test_evolved_inputs_open_revision_and_only_writer_carries_new_coverage(self):
        self.evolve_inputs()
        before = self.story.read_bytes(), self.plan.read_bytes()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        self.assertIn("delivery:AC-DEL-002", output)
        self.assertEqual((self.story.read_bytes(), self.plan.read_bytes()), before)
        candidates = [{"result_ref": ref, "path": str(self.docs / "experience-design/experiences/checkout/experience.md")}
                      for ref in (fixtures.fixtures.APPLICATION, fixtures.fixtures.PROCESS)]
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                mock.patch.object(compiler.stage_package, "candidates", return_value=candidates):
            record, errors = compiler.collect(self.docs)
            self.assertTrue(any("neither story-covered nor deferred" in error for error in errors), errors)
            writer = inputs.manifest(self.docs, writer=True)
            self.assertIn("delivery:AC-DEL-002", writer["check"]["transition_findings"][0])
            with self.assertRaisesRegex(inputs.InputError, "neither story-covered nor deferred"):
                inputs.manifest(self.docs)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(compiler.approve(self.args), 1)

    def test_revising_the_story_requires_current_evidence(self):
        self.evolve_inputs()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        self.story.write_text(self.story.read_text(encoding="utf-8").replace(
            "Users receive", "Customers receive"), encoding="utf-8")
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"):
            _record, errors = compiler.collect(self.docs, revision_inputs=True)
        self.assertTrue(any("target is not approved/accepted" in error for error in errors), errors)

    def test_missing_evidence_still_refuses_revision_without_writes(self):
        self.evolve_inputs()
        self.decision.unlink()
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("targets missing note", output)
        self.assertEqual(self.files(), before)

    def test_changed_approval_cannot_use_history(self):
        self.evolve_inputs()
        text = self.story.read_text(encoding="utf-8")
        self.story.write_text(text.replace("Users receive", "Customers receive"), encoding="utf-8")
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("approved source_hash is stale", output)
        self.assertEqual(self.files(), before)

    def test_draft_source_never_uses_historical_approval(self):
        self.evolve_inputs()
        props, body = compiler.parse_front_matter(self.decision)
        props["status"] = "draft"
        self.decision.write_text(compiler.front_matter(props, body), encoding="utf-8")
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("target is not approved/accepted", output)
        self.assertEqual(self.files(), before)

    def test_invalid_committed_package_hash_cannot_supply_transition_baseline(self):
        props, body = compiler.parse_front_matter(self.root)
        props["package_hash"] = "sha256:" + "0" * 64
        self.root.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.commit("Invalid approval boundary")
        self.evolve_inputs()
        before = self.files()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 1, output)
        self.assertIn("approved package_hash is stale", output)
        self.assertEqual(self.files(), before)

    def test_new_unstamped_story_cannot_cite_superseded_evidence(self):
        self.evolve_inputs()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        props, body = compiler.parse_front_matter(self.story)
        for key in compiler.APPROVAL_FIELDS:
            props.pop(key, None)
        self.story.write_text(compiler.front_matter(props, body), encoding="utf-8")
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"):
            _record, errors = compiler.collect(self.docs, revision_inputs=True)
        self.assertTrue(any("target is not approved/accepted" in error for error in errors), errors)

    def test_closing_added_coverage_keeps_approved_historical_story_eligible(self):
        self.evolve_inputs()
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        root_review = self.docs / "backlog/reviews/round-2-backlog-review.md"
        text = root_review.read_text(encoding="utf-8")
        header = "|---|---|---|---|"
        row = ("| [[business-analysis/delivery/domains/identity/acceptance/delivery-acceptance#^AC-DEL-002\\|delivery:AC-DEL-002]] "
               "| product_owner | The report change is outside this incremental slice. "
               "| The next report revision revisits this outcome. |")
        root_review.write_text(text.replace(header, header + "\n" + row, 1), encoding="utf-8")
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"):
            _record, errors = compiler.collect(self.docs)
            _record, discovery_errors = compiler.collect(self.docs, review_inputs=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(compiler.approve(self.args), 1)
        self.assertEqual(discovery_errors, [])
        self.assertTrue(any("needs section-specific" in error for error in errors), errors)
        review_props, _body = compiler.parse_front_matter(root_review)
        review_props["verdict"] = "approved"
        authored = fixtures.fixtures.backlog_fixture._complete_review_body(
            str(review_props["title"]), compiler.backlog_contract()["required_backlog_review_sections"])
        authored = authored.replace(header, header + "\n" + row, 1)
        root_review.write_text(compiler.front_matter(review_props, authored), encoding="utf-8")
        with self.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.approve(self.args), 0)

    def test_fresh_review_under_new_policy_preserves_deferrals_and_legacy_followup_history(self):
        old_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        props, body = compiler.parse_front_matter(old_review)
        body += ("\n## Accepted Minor Findings\n\n"
                 "| finding | owner_role | reason | revisit_trigger |\n"
                 "|---|---|---|---|\n"
                 "| [[backlog/backlog\\|Backlog]] Delivery notes repeat the account rule in equivalent wording. "
                 "| product_owner | Both statements preserve one outcome and one owner. "
                 "| Revisit during the next substantive account revision. |\n")
        props["source_hash"] = compiler.digest_text(compiler.front_matter(props, body))
        old_review.write_text(compiler.front_matter(props, body), encoding="utf-8")
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        root_props, root_body = compiler.parse_front_matter(self.root)
        root_props["package_hash"] = compiler.package_digest(self.docs, compiler.package_paths(record, self.docs))
        self.root.write_text(compiler.front_matter(root_props, root_body), encoding="utf-8")
        self.commit("Approved legacy follow-up record")
        old_bytes = old_review.read_bytes()
        deferred = compiler.raw_section(body, "Deferred Criteria").strip()
        with contextlib.redirect_stdout(io.StringIO()):
            for arguments in (("init", "--docs", str(self.docs)),
                              ("set", "--docs", str(self.docs), "--switch", "review_loop", "--value", "blocking_delta"),
                              ("approve", "--docs", str(self.docs))):
                self.assertEqual(process_policy.main(list(arguments)), 0)
        self.commit("Approved current review policy")
        result, output = self.run_revision(allow_legacy_experience=True)
        self.assertEqual(result, 0, output)
        self.assertEqual(old_review.read_bytes(), old_bytes)
        new_props, new_body = compiler.parse_front_matter(self.review)
        self.assertEqual(compiler.raw_section(new_body, "Deferred Criteria").strip(), deferred)
        self.assertFalse(set(compiler.REVIEW_RECORD_SECTIONS) & set(compiler.headings(new_body)))
        self.assertIn("Prior Review Follow-ups", new_body)
        self.assertIn("[[backlog/reviews/round-1-backlog-review", new_body)
        self.assertIn("The Product Owner must triage", new_body)
        self.assertNotIn("is supported by the cited inputs", new_body)
        self.assertIn("TODO: cite the exact reviewed vault note", new_body)
        self.assertEqual(compiler.review_loop_record(self.docs, new_body, str(self.review), new_props), [])


if __name__ == "__main__":
    unittest.main()
