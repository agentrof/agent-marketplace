"""Policy changes open fresh review rounds without rewriting existing evidence."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import unittest
from tools.tests.levels import integration
from unittest import mock

from tools.tests import test_backlog_upstream_transition as fixtures

compiler = fixtures.compiler
process_policy = fixtures.process_policy


@integration
class PendingReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BacklogUpstreamTransitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.docs = self.fixture.docs
        self.policy("init")
        self.policy("approve")
        self.fixture.commit("Approved initial review policy")
        self.initial_pin = process_policy.approved_snapshot(self.docs)[0]
        code, output = self.fixture.run_revision(allow_legacy_experience=True)
        self.assertEqual(code, 0, output)
        code, output = self.fixture.run_epic_review()
        self.assertEqual(code, 0, output)
        self.root_review = self.fixture.review
        self.epic_review = Path(output["review"])

    def policy(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = process_policy.main([args[0], "--docs", str(self.docs), *args[1:]])
        self.assertEqual(code, 0, output.getvalue())

    def change_policy(self, switch=None, value=None, *, commit=True):
        self.policy("begin-revision")
        if switch:
            self.policy("set", "--switch", switch, "--value", value)
        self.policy("approve")
        if commit:
            self.fixture.commit("Approved successor review policy")
        return process_policy.approved_snapshot(self.docs)[0]

    def run_review(self, kind):
        output = io.StringIO()
        argv = (["stub-epic", "delivery-fixture", "--new-review"] if kind == "epic" else
                ["stub-backlog-review"])
        with self.fixture.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(output):
            code = compiler.main([*argv, "--docs", str(self.docs)])
        return code, json.loads(output.getvalue())

    def pin(self, path):
        return compiler.recorded_pin(compiler.parse_front_matter(path)[0])

    def record(self):
        with self.fixture.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"):
            record, errors = compiler.collect(self.docs, review_inputs=True)
        self.assertEqual(errors, [])
        return record

    def completed_current_rounds(self):
        self.change_policy("review_manifest_scope", "bounded")
        paths = []
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            path = Path(result["review"])
            props, _body = compiler.parse_front_matter(path)
            props["verdict"] = "approved"
            sections = compiler.backlog_contract()[f"required_{kind}_review_sections"]
            body = fixtures.fixtures.fixtures.backlog_fixture._complete_review_body(props["title"], sections)
            path.write_text(compiler.front_matter(props, body), encoding="utf-8")
            paths.append(path)
        return paths

    def cli(self, command, *flags):
        output = io.StringIO()
        with self.fixture.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(output):
            code = compiler.main([command, "--docs", str(self.docs), *flags])
        return code, json.loads(output.getvalue())

    def assert_new_rounds(self, pin):
        before = self.fixture.files()
        root_before = self.fixture.root.read_bytes()
        paths = []
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertTrue(result["created"])
            self.assertEqual(result["review_round"], 3)
            path = Path(result["review"])
            paths.append(path)
            props, body = compiler.parse_front_matter(path)
            self.assertEqual(self.pin(path), pin)
            self.assertEqual(props["status"], "draft")
            for field in ("verdict", "source_hash", "approved_at_utc"):
                self.assertNotIn(field, props)
            self.assertIn("TODO: cite the exact reviewed vault note", body)
            previous = self.epic_review if kind == "epic" else self.root_review
            self.assertIn(previous.relative_to(self.docs).as_posix().removesuffix(".md"), body)
            self.assertNotIn("is supported by the cited inputs", body)
        after = self.fixture.files()
        self.assertEqual(set(after) - set(before), {path.relative_to(self.docs).as_posix() for path in paths})
        self.assertEqual({path: after[path] for path in before}, before)
        self.assertEqual(self.fixture.root.read_bytes(), root_before)
        self.assertEqual(self.pin(self.root_review), self.initial_pin)
        self.assertEqual(self.pin(self.epic_review), self.initial_pin)
        record = self.record()
        self.assertEqual(compiler.review_coverage_findings(record, self.docs), [])
        preserved, errors = compiler.preserved_approval_sources(record, self.docs)
        self.assertEqual(errors, [])
        self.assertEqual(compiler.review_pin_findings(record, self.docs, pin, preserved), [])
        with self.fixture.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compiler.main(["check", "--docs", str(self.docs), "--json"]), 1)
            self.assertEqual(compiler.approve(self.fixture.args), 1)
        return paths

    def test_a_fresh_root_round_carries_the_requirement_coverage_rows(self):
        scaffold = "| requirement | story_ids | disposition |\n|---|---|---|"
        props, body = compiler.parse_front_matter(self.root_review)
        # begin-revision opened this Requirement-mode round with an empty table.
        self.assertEqual(compiler.section(body, compiler.REQUIREMENT_COVERAGE), scaffold)
        table = scaffold + "\n| REQ-001 | AUTH-01 | covered |"
        self.root_review.write_text(compiler.front_matter(props, body.replace(scaffold, table)),
                                    encoding="utf-8")
        self.change_policy("review_manifest_scope", "bounded")
        fresh = {}
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertTrue(result["created"])
            fresh[kind] = compiler.parse_front_matter(Path(result["review"]))[1]
        self.assertEqual(compiler.section(fresh["backlog"], compiler.REQUIREMENT_COVERAGE), table)
        self.assertNotIn(compiler.REQUIREMENT_COVERAGE, compiler.headings(fresh["epic"]))

    def test_owned_policy_change_creates_fresh_root_and_epic_with_exact_membership(self):
        pin = self.change_policy("review_manifest_scope", "bounded")
        self.assert_new_rounds(pin)

    def test_current_completed_rounds_approve_without_claiming_old_drafts_then_revise_again(self):
        old_drafts = {path: path.read_bytes() for path in (self.root_review, self.epic_review)}
        old_approved = {path: path.read_bytes() for path in (
            self.docs / "backlog/reviews/round-1-backlog-review.md",
            self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md")}
        current = self.completed_current_rounds()
        code, result = self.cli("check", "--json")
        self.assertEqual(code, 0, result)
        code, result = self.cli("approve")
        self.assertEqual(code, 0, result)
        code, result = self.cli("check", "--approved", "--render", "--json")
        self.assertEqual(code, 0, result)
        for path, data in old_drafts.items():
            self.assertEqual(path.read_bytes(), data)
            props, _body = compiler.parse_front_matter(path)
            self.assertEqual(props["status"], "draft")
            for key in ("source_hash", "approved_at_utc", "verdict"):
                self.assertNotIn(key, props)
            self.assertEqual(compiler.recorded_pin(props), self.initial_pin)
        for path, data in old_approved.items():
            self.assertEqual(path.read_bytes(), data)
        for path in current:
            self.assertEqual(compiler.parse_front_matter(path)[0]["status"], "approved")
        self.fixture.commit("Approved current reviews and retained unapproved history")
        history = compiler.approved_history_sources(self.fixture.fixture.project, self.docs, "HEAD")
        self.assertIsNotNone(history)
        for path, data in old_drafts.items():
            self.assertEqual(history[path], data)
        universe = compiler.prior_approved_universe(self.docs)
        self.assertIn("delivery:AC-DEL-001", universe)
        code, result = self.cli("approve")
        self.assertEqual(code, 0, result)
        code, output = self.fixture.run_revision(allow_legacy_experience=True)
        self.assertEqual(code, 0, output)
        self.assertEqual(compiler.parse_front_matter(self.fixture.root)[0]["revision"], 3)
        for path, data in {**old_drafts, **old_approved}.items():
            self.assertEqual(path.read_bytes(), data)

    def test_old_unapproved_review_contents_remain_bound_into_approved_package_digest(self):
        self.completed_current_rounds()
        code, result = self.cli("approve")
        self.assertEqual(code, 0, result)
        record = self.record()
        original = compiler.package_digest(self.docs, compiler.package_paths(record, self.docs))
        self.root_review.write_text(self.root_review.read_text(encoding="utf-8") +
                                    "\nAn altered historical reader note.\n", encoding="utf-8")
        self.assertNotEqual(compiler.package_digest(self.docs, compiler.package_paths(record, self.docs)), original)
        code, result = self.cli("check", "--approved", "--json")
        self.assertEqual(code, 1, result)
        self.assertTrue(any("package_hash is stale" in item for item in result["errors"]), result)
        self.fixture.commit("Changed unapproved history without a new package approval")
        self.assertIsNone(compiler.approved_history_sources(self.fixture.fixture.project, self.docs, "HEAD"))

    def test_historical_approved_review_mutation_and_removal_still_refuse_approval(self):
        self.completed_current_rounds()
        paths = [self.docs / "backlog/reviews/round-1-backlog-review.md",
                 self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"]
        for path in paths:
            original = path.read_bytes()
            props, body = compiler.parse_front_matter(path)
            changed = body.replace(f"# {props['title']}\n", f"# {props['title']}\n\nA changed historical interpretation.\n", 1)
            props["source_hash"] = compiler.digest_text(compiler.front_matter(props, changed))
            path.write_text(compiler.front_matter(props, changed), encoding="utf-8")
            before = self.fixture.files()
            code, result = self.cli("approve")
            self.assertEqual(code, 1, result)
            self.assertTrue(any("prior review approval must remain byte-exact" in item for item in result["errors"]))
            self.assertEqual(self.fixture.files(), before)
            path.write_bytes(original)
            path.unlink()
            before = self.fixture.files()
            code, result = self.cli("approve")
            self.assertEqual(code, 1, result)
            self.assertTrue(any("removed or renamed" in item for item in result["errors"]))
            self.assertEqual(self.fixture.files(), before)
            path.write_bytes(original)

    def test_historical_approved_review_completion_checks_remain_strict(self):
        self.completed_current_rounds()
        for path, key in ((self.docs / "backlog/reviews/round-1-backlog-review.md", "required_backlog_review_sections"),
                          (self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md", "required_epic_review_sections")):
            original = path.read_bytes()
            props, _body = compiler.parse_front_matter(path)
            body = compiler.review_body(props["title"], compiler.backlog_contract()[key])
            props["source_hash"] = compiler.digest_text(compiler.front_matter(props, body))
            path.write_text(compiler.front_matter(props, body), encoding="utf-8")
            code, result = self.cli("check", "--json")
            self.assertEqual(code, 1, result)
            self.assertTrue(any(path.relative_to(self.docs).as_posix() in item and "needs section-specific" in item
                                for item in result["errors"]), result)
            path.write_bytes(original)

    def test_initial_epic_round_keeps_established_frontmatter_order(self):
        props, _body = compiler.parse_front_matter(self.epic_review)
        self.assertEqual(list(props), ["type", "title", "status", "round", "owner_role", "derives_from",
            "verifies", "scenario_refs", "dependency_refs", "tags", "aliases", *process_policy.PIN_FIELDS])

    def test_fresh_policy_round_retries_preserve_every_pending_byte(self):
        pin = self.change_policy("review_manifest_scope", "bounded")
        paths = self.assert_new_rounds(pin)
        before = self.fixture.files()
        for kind, path in zip(("epic", "backlog"), paths):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertFalse(result["created"])
            self.assertEqual(Path(result["review"]), path)
        self.assertEqual(self.fixture.files(), before)

    def test_delivery_only_policy_change_reuses_current_pending_rounds(self):
        registry = process_policy.load_registry()
        self.assertNotIn(compiler.BACKLOG_FLOW, registry["delivery_path"]["spec"]["flows"])
        self.change_policy("delivery_path", "light_when_eligible")
        before = self.fixture.files()
        for kind, path in (("epic", self.epic_review), ("backlog", self.root_review)):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertFalse(result["created"])
            self.assertEqual(Path(result["review"]), path)
        self.assertEqual(self.fixture.files(), before)

    def test_same_owned_values_in_another_policy_revision_reuse_pending_bytes(self):
        self.change_policy()
        before = self.fixture.files()
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertFalse(result["created"])
        self.assertEqual(self.fixture.files(), before)

    def test_owned_parameter_change_creates_successors_even_with_same_switch_value(self):
        self.policy("begin-revision")
        self.policy("set", "--switch", "story_size_budget", "--value", "propose_split")
        self.policy("set", "--switch", "story_size_budget", "--parameter", "acceptance_criteria", "--value", "5")
        self.policy("approve")
        self.fixture.commit("Approved story size policy")
        pin = process_policy.approved_snapshot(self.docs)[0]
        self.assert_new_rounds(pin)
        self.policy("begin-revision")
        self.policy("set", "--switch", "story_size_budget", "--parameter", "acceptance_criteria", "--value", "6")
        self.policy("approve")
        self.fixture.commit("Approved successor story size limit")
        before = self.fixture.files()
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 0, result)
            self.assertTrue(result["created"])
            self.assertEqual(result["review_round"], 4)
        self.assertEqual({path: self.fixture.files()[path] for path in before}, before)

    def test_both_new_policy_round_paths_bind_real_writer_manifests(self):
        pin = self.change_policy("review_manifest_scope", "bounded")
        epic, root = self.assert_new_rounds(pin)
        task = self.fixture.writer_task(epic)
        self.assertEqual(task["backlog_scope"]["review"]["path"], epic.relative_to(self.docs).as_posix())
        candidates = [{"result_ref": ref,
                       "path": str(self.docs / "experience-design/experiences/checkout/experience.md")}
                      for ref in (fixtures.fixtures.fixtures.APPLICATION, fixtures.fixtures.fixtures.PROCESS)]
        with self.fixture.fixture.upstreams(), mock.patch.object(compiler, "validate_experience_ref"), \
                mock.patch.object(compiler.stage_package, "candidates", return_value=candidates):
            task = fixtures.task_inputs.manifest(entry="backlog-plan", role="product-owner", mode="revise",
                project=self.fixture.fixture.project, epic="",
                inputs=[root.relative_to(self.fixture.fixture.project).as_posix()])
        self.assertEqual(task["backlog_scope"]["review"]["path"], root.relative_to(self.docs).as_posix())
        self.assertIn(root.relative_to(self.docs).as_posix(), task["backlog_scope"]["paths"])

    def test_unreadable_pinned_policy_refuses_both_rounds_without_writes(self):
        # The pin no committed policy holds cannot be compared with its successor.
        self.change_policy("review_manifest_scope", "bounded", commit=False)
        for path in (self.root_review, self.epic_review):
            props, body = compiler.parse_front_matter(path)
            props["process_policy_revision"] = 99
            props["process_policy_source_hash"] = "sha256:" + "f" * 64
            path.write_text(compiler.front_matter(props, body), encoding="utf-8")
        before = self.fixture.files()
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 1, result)
            self.assertIn("pinned Process Policy revision 99", result["errors"][0])
        self.assertEqual(self.fixture.files(), before)

    def test_draft_current_policy_refuses_both_rounds_without_writes(self):
        self.policy("begin-revision")
        before = self.fixture.files()
        for kind in ("epic", "backlog"):
            code, result = self.run_review(kind)
            self.assertEqual(code, 1, result)
            self.assertIn("is a draft", result["errors"][0])
        self.assertEqual(self.fixture.files(), before)

    def test_root_round_carries_deferrals_without_copying_completed_evidence(self):
        self.fixture.evolve_inputs()
        props, old_body = compiler.parse_front_matter(self.root_review)
        body = fixtures.fixtures.fixtures.backlog_fixture._complete_review_body(
            props["title"], compiler.backlog_contract()["required_backlog_review_sections"])
        row = ("| [[business-analysis/delivery/domains/identity/acceptance/delivery-acceptance#^AC-DEL-002\\|delivery:AC-DEL-002]] "
               "| product_owner | This separate outcome remains outside the selected slice. "
               "| Revisit during its next approved scope. |")
        body = body.replace("|---|---|---|---|", "|---|---|---|---|\n" + row, 1)
        self.root_review.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.change_policy("review_manifest_scope", "bounded")
        before = self.fixture.files()
        code, result = self.run_review("backlog")
        self.assertEqual(code, 0, result)
        new = Path(result["review"])
        _props, new_body = compiler.parse_front_matter(new)
        self.assertEqual(compiler.raw_section(new_body, "Deferred Criteria").strip(),
                         compiler.raw_section(body, "Deferred Criteria").strip())
        self.assertNotIn("is supported by the cited inputs", new_body)
        self.assertEqual({path: self.fixture.files()[path] for path in before}, before)

    def test_postwrite_membership_failure_rolls_back_only_the_new_root_round(self):
        self.change_policy("review_manifest_scope", "bounded")
        before = self.fixture.files()
        new = self.docs / "backlog/reviews/round-3-backlog-review.md"
        original_collect = compiler.collect

        def damaged(*args, **kwargs):
            if new.exists():
                props, body = compiler.parse_front_matter(new)
                props.pop("related_to")
                new.write_text(compiler.front_matter(props, body), encoding="utf-8")
            return original_collect(*args, **kwargs)

        with mock.patch.object(compiler, "collect", side_effect=damaged):
            code, result = self.run_review("backlog")
        self.assertEqual(code, 1, result)
        self.assertIn("related_to set does not exactly cover", result["errors"][0])
        self.assertEqual(self.fixture.files(), before)

    def test_new_root_write_failure_preserves_prior_source_and_rounds(self):
        self.change_policy("review_manifest_scope", "bounded")
        before = self.fixture.files()
        new = self.docs / "backlog/reviews/round-3-backlog-review.md"
        original_open = Path.open

        def refused(path, *args, **kwargs):
            if path == new and args == ("xb",):
                raise OSError("injected review creation refusal")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", refused):
            code, result = self.run_review("backlog")
        self.assertEqual(code, 1, result)
        self.assertIn("injected review creation refusal", result["errors"][0])
        self.assertEqual(self.fixture.files(), before)


if __name__ == "__main__":
    unittest.main()
