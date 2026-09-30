"""Reviewer manifests preserve scope, dependency closure and source freshness."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog_compile as backlog
import backlog_review_inputs as inputs
from backlog_fixture import _complete_review_body, make_approved_backlog


class ReadSessionTests(unittest.TestCase):
    def test_session_read_computes_once_per_key_and_never_caches_errors(self):
        calls = []

        def compute():
            calls.append(1)
            return {"value": len(calls)}

        self.assertEqual(backlog.session_read(("key",), compute), {"value": 1})
        self.assertEqual(backlog.session_read(("key",), compute), {"value": 2})
        failures = []

        def failing():
            failures.append(1)
            raise OSError("unreadable")

        with backlog.experience_validation_session():
            first = backlog.session_read(("key",), compute)
            self.assertIs(backlog.session_read(("key",), compute), first)
            backlog.session_read(("other",), compute)
            self.assertEqual(len(calls), 4)
            for _ in range(2):
                with self.assertRaises(OSError):
                    backlog.session_read(("broken",), failing)
            self.assertEqual(len(failures), 2)
            with backlog.experience_validation_session():
                self.assertIs(backlog.session_read(("key",), compute), first)
        with backlog.experience_validation_session():
            self.assertEqual(backlog.session_read(("key",), compute), {"value": 5})


class BacklogReviewInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name) / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs.parent / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        }), encoding="utf-8")
        # This existing builder uses a legacy approved upstream package. The
        # manifest's structure and closure are real; modern receipt dispatch is
        # exercised separately with its resolver mocked, not claimed by this seed.
        make_approved_backlog(self.docs, "ST-001", "ST-002", "ST-003", "ST-004")
        with contextlib.redirect_stdout(io.StringIO()):
            backlog.stub_epic(SimpleNamespace(docs=str(self.docs), slug="second", id="EP-002",
                title="Second", goal="Deliver a separate customer outcome."))
        for identity in ("st-003", "st-004"):
            old = "backlog/epics/delivery-fixture/stories/" + identity
            new = "backlog/epics/second/stories/" + identity
            shutil.move(str(self.docs / old), str(self.docs / new))
            for path in self.docs.rglob("*.md"):
                path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            path = self.docs / new / "story.md"
            props, body = backlog.parse_front_matter(path)
            props["derives_from"] = ["[[backlog/epics/second/epic|EP-002]]"]
            path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.refresh_reviews()

    def refresh_reviews(self):
        record, _ = backlog.collect(self.docs)
        root = backlog.latest(record["backlog_reviews"])
        props = root["props"]
        props["related_to"] = [f"[[{epic['path'][:-3]}|{epic['id']}]]" for epic in record["epics"]]
        props["dependency_refs"] = sorted(backlog.dependency_edges(record["stories"], False, record))
        (self.docs / root["path"]).write_text(backlog.front_matter(props, root["body"]), encoding="utf-8")
        for epic in record["epics"]:
            review = backlog.latest(epic["reviews"])
            props = review["props"]
            props["verdict"] = "approved"
            props["verifies"] = [f"[[{path[:-3]}|{story['id']}]]" for story in epic["stories"]
                                 for path in (story["path"], story["test_plan"])]
            props["scenario_refs"] = [scenario for story in epic["stories"] for scenario in story["scenario_ids"]]
            props["dependency_refs"] = sorted(backlog.dependency_edges(epic["stories"], True, record))
            body = _complete_review_body(props["title"], backlog.backlog_contract()["required_epic_review_sections"])
            (self.docs / review["path"]).write_text(backlog.front_matter(props, body), encoding="utf-8")
        record, errors = backlog.collect(self.docs)
        self.assertEqual(errors, [])

    def story(self, number):
        epic = "delivery-fixture" if number <= 2 else "second"
        return self.docs / f"backlog/epics/{epic}/stories/st-{number:03d}/story.md"

    def depends(self, source, target):
        path = self.story(source)
        props, body = backlog.parse_front_matter(path)
        link = f"[[{self.story(target).relative_to(self.docs).as_posix()[:-3]}|ST-{target:03d}]]"
        props["depends_on"] = [link]
        body = body.replace("## Dependencies\n\nNone.", "## Dependencies\n\n- " + link + ": Supplies the required input.")
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")

    def add_scope_text(self, number, text):
        """Write into Scope; text after the closing navigation is never read."""
        path = self.story(number)
        props, body = backlog.parse_front_matter(path)
        body = body.replace("\n## Non-Goals", "\n" + text + "\n\n## Non-Goals", 1)
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")

    def draft_reviews(self):
        """Recreate the real compiler-owned review scaffolds before authoring."""
        (self.docs / "backlog/reviews/round-1-backlog-review.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backlog.init(SimpleNamespace(docs=str(self.docs))), 0)
        for slug, identity in (("delivery-fixture", "EP-001"), ("second", "EP-002")):
            (self.docs / f"backlog/epics/{slug}/reviews/round-1-epic-review.md").unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(backlog.stub_epic(SimpleNamespace(docs=str(self.docs),
                    slug=slug, id=identity, title=slug, goal="Deliver the declared customer outcome.")), 0)

    def test_real_draft_review_scaffolds_allow_input_discovery_only(self):
        self.draft_reviews()
        for scope in ("EP-001", "EP-002", None):
            value = inputs.manifest(self.docs, epic=scope)
            self.assertTrue(value["ok"])
            self.assertIn(value["review"]["path"], value["paths"])
        _, errors = backlog.collect(self.docs)
        self.assertTrue(any("section-specific" in error for error in errors))
        self.assertTrue(any("does not exactly cover" in error for error in errors))

    def test_new_pending_review_round_uses_current_scope_without_approval(self):
        path = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        props, _ = backlog.parse_front_matter(path)
        props.update(round=2, status="draft", verdict="pending")
        props["tags"] = ["doc/epic-review", "status/draft"]
        for key in ("verifies", "scenario_refs", "dependency_refs"):
            props.pop(key, None)
        second = path.with_name("round-2-epic-review.md")
        second.write_text(backlog.front_matter(props, backlog.review_body("Review round 2",
            backlog.backlog_contract()["required_epic_review_sections"])), encoding="utf-8")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(value["review"]["path"], second.relative_to(self.docs).as_posix())
        self.assertIn(path.relative_to(self.docs).as_posix(), value["paths"])
        self.assertEqual(len(value["review"]["expected_relations"]["verifies"]), 4)
        record, errors = backlog.collect(self.docs)
        self.assertTrue(errors)
        self.assertTrue(any("verdict is not approved" in error for error in backlog.approval_readiness_findings(record)))

    def test_draft_review_discovery_retains_source_and_dependency_validation(self):
        self.draft_reviews()
        original = self.story(1).read_text(encoding="utf-8")
        for key, target in (("depends_on", "[[backlog/epics/missing/stories/missing/story|ST-099]]"),
                            ("constrained_by", "[[solution-design/missing|Missing]]")):
            props, body = backlog.parse_front_matter(self.story(1))
            props[key] = [target]
            self.story(1).write_text(backlog.front_matter(props, body), encoding="utf-8")
            with self.assertRaises(inputs.InputError):
                inputs.manifest(self.docs, epic="EP-001")
            self.story(1).write_text(original, encoding="utf-8")
        self.depends(1, 3)
        self.depends(3, 1)
        with self.assertRaisesRegex(inputs.InputError, "cycle"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_review_existing_broken_evidence_links_still_fail_closed(self):
        self.draft_reviews()
        review = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        with review.open("a", encoding="utf-8") as handle:
            handle.write("\n## Additional evidence\n[[solution-design/missing|Missing evidence]]\n")
        with self.assertRaises(inputs.InputError):
            inputs.manifest(self.docs, epic="EP-001")

    def test_two_epics_have_six_six_and_eleven_primary_documents(self):
        one = inputs.manifest(self.docs, epic="EP-001")
        two = inputs.manifest(self.docs, epic="EP-002")
        root = inputs.manifest(self.docs)
        self.assertEqual([len(value["primary_paths"]) for value in (one, two, root)], [6, 6, 11])
        self.assertNotIn(self.story(3).relative_to(self.docs).as_posix(), one["paths"])
        self.assertNotIn(self.story(1).relative_to(self.docs).as_posix(), two["paths"])
        self.assertEqual(len(root["review"]["expected_relations"]["related_to"]), 2)
        self.assertEqual(len(one["review"]["expected_relations"]["verifies"]), 4)
        self.assertEqual(one["review"]["expected_relations"]["scenario_refs"], ["ST-001-TS-001", "ST-002-TS-001"])
        self.assertEqual(root["paths"], sorted(set(root["paths"])))

    def test_upstream_sources_and_review_findings_remain_accessible(self):
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertIn("design-system/MASTER.md", value["context_paths"])
        self.assertIn("solution-design/landscape.md", value["context_paths"])
        self.assertIn("experience-design/experiences/checkout/screens/checkout-screen.md", value["context_paths"])
        self.assertIn("backlog/epics/delivery-fixture/reviews/round-1-epic-review.md", value["context_paths"])
        self.assertNotIn("maps/backlog.md", value["paths"])

    def test_outgoing_transitive_and_incoming_dependencies_expand_context(self):
        self.depends(1, 3)
        self.depends(3, 4)
        self.refresh_reviews()
        value = inputs.manifest(self.docs, epic="EP-001")
        for number in (3, 4):
            path = self.story(number).relative_to(self.docs).as_posix()
            self.assertIn(path, value["context_paths"])
            self.assertIn(path.replace("story.md", "test-plan.md"), value["context_paths"])
        self.assertIn("backlog/epics/second/epic.md", value["context_paths"])
        incoming = inputs.manifest(self.docs, epic="EP-002")
        self.assertIn(self.story(1).relative_to(self.docs).as_posix(), incoming["context_paths"])
        self.assertEqual(inputs.manifest(self.docs)["review"]["expected_relations"]["dependency_refs"], ["ST-001 -> ST-003"])

    def test_dependency_cycle_and_missing_dependency_fail_closed(self):
        self.depends(1, 3)
        self.depends(3, 1)
        with self.assertRaisesRegex(inputs.InputError, "cycle"):
            inputs.manifest(self.docs, epic="EP-001")
        self.story(3).unlink()
        with self.assertRaises(inputs.InputError):
            inputs.manifest(self.docs)

    def test_semantic_contract_links_are_followed_transitively(self):
        master = self.docs / "design-system/MASTER.md"
        with master.open("a", encoding="utf-8") as handle:
            handle.write("\nThe service contract is [[solution-design/contracts/service|Service contract]].\n")
        contract = self.docs / "solution-design/contracts/service.md"
        contract.parent.mkdir(parents=True)
        contract.write_text("---\ntype: api-contract\ntitle: Service\n---\n\n# Service\n", encoding="utf-8")
        # Editing the sealed Solution package stales its receipt; restamp the
        # fixture's legacy upstream hash to isolate the manifest link closure.
        landscape = self.docs / "solution-design/landscape.md"
        props, body = backlog.parse_front_matter(landscape)
        import stage_package
        props["package_hash"] = stage_package.tree_hash(landscape.parent,
            {"package_hash", "package_status", "package_approved_at_utc"})
        landscape.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.assertIn("solution-design/contracts/service.md", inputs.manifest(self.docs, epic="EP-001")["paths"])

    def test_missing_or_malformed_semantic_link_fails_closed(self):
        master = self.docs / "design-system/MASTER.md"
        original = master.read_text(encoding="utf-8")
        master.write_text(original + "\n[[design-system/missing|Missing]]", encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "targets missing note"):
            inputs.manifest(self.docs, epic="EP-001")
        master.write_text(original, encoding="utf-8")
        self.add_scope_text(1, "[[design-system/MASTER|Broken]")
        with self.assertRaisesRegex(inputs.InputError, "malformed reviewer source wikilink in "
                                    "backlog/epics/delivery-fixture/stories/st-001/story.md"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_upstream_link_text_that_cannot_parse_is_named_for_the_reader(self):
        self.assertNotIn("unparsed_link_sources", inputs.manifest(self.docs, epic="EP-001"))
        with (self.docs / "design-system/MASTER.md").open("a", encoding="utf-8") as handle:
            handle.write("\nThe approved scan splits rows with cells[1:-1]] first.\n"
                         "It follows [[solution-design/landscape|Solution\nLandscape]].\n")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(value["unparsed_link_sources"], ["design-system/MASTER.md"])

    def test_code_is_never_a_review_link(self):
        path = self.story(1)
        props, body = backlog.parse_front_matter(path)
        props["priority_reason"] = "ST-001 serves `pages/api/[[...resource]].ts` for the approved epic outcome."
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.add_scope_text(1, "It adds `pages/api/v1/health-checks/[[...resource]].ts`.\n\n"
                               "- Example:\n\n  ```text\n  [[example/only]]\n  ```")
        self.assertTrue(inputs.manifest(self.docs, epic="EP-001")["ok"])
        self.add_scope_text(1, "It follows [[design-system/missing|Missing guidance]].")
        with self.assertRaisesRegex(inputs.InputError, "targets missing note: design-system/missing"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_review_links_are_the_links_the_vault_checker_reads(self):
        import vault_check
        note = self.docs / "design-system/parity.md"
        note.write_text(
            "---\ntype: design-master\ntitle: Parity\n---\n# Parity\n\n"
            "[[design-system/MASTER|Design Master]] beside `[[code/span]]`.\n"
            "  ```text\n  [[indented/fence]]\n  ```\n"
            "````\n[[longer/fence]]\n````\n"
            "~~~\n[[solution-design/landscape|Landscape]]\n~~~\n"
            "![[_attachments/flow.png|320]] then `a` [`b`[c|C]]\n", encoding="utf-8")
        _props, body = backlog.parse_front_matter(note)
        found = inputs.links(inputs.semantic_body(body), "design-system/parity.md", set())
        vault = vault_check.scan_note(self.docs, note, "<!-- generated by").wikilinks
        self.assertEqual(found, [(embed, f"[[{inner}]]") for _line, embed, _t, _a, _l, inner in vault])
        self.assertEqual([link for _embed, link in found], [
            "[[design-system/MASTER|Design Master]]", "[[solution-design/landscape|Landscape]]",
            "[[_attachments/flow.png|320]]", "[[c|C]]"])

    def test_upstream_compilers_alias_free_links_resolve_but_backlog_links_need_aliases(self):
        entity = "business-analysis/delivery/domains/identity/entities/account-entity"
        rules = "business-analysis/delivery/domains/identity/rules/account-rules"
        for path, props in ((entity, {"type": "entity", "title": "Account"}),
                            (rules, {"type": "rule_set", "title": "Account rules", "status": "approved",
                                     "governs": [f"[[{entity}]]"]})):
            (self.docs / path).parent.mkdir(parents=True)
            (self.docs / f"{path}.md").write_text(backlog.front_matter(props, "# Note\n"), encoding="utf-8")
        self.add_scope_text(1, f"It applies [[{rules}|Account rules]].")
        self.assertIn(f"{entity}.md", inputs.manifest(self.docs, epic="EP-001")["context_paths"])
        entity_note = (self.docs / f"{entity}.md").read_bytes()
        (self.docs / f"{entity}.md").unlink()
        with self.assertRaisesRegex(inputs.InputError, f"{rules}.md targets missing note: {entity}"):
            inputs.manifest(self.docs, epic="EP-001")
        (self.docs / f"{entity}.md").write_bytes(entity_note)
        self.add_scope_text(1, f"It restates [[{rules}]].")
        with self.assertRaisesRegex(inputs.InputError, "st-001/story.md must have a display alias"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_embeds_resolve_as_the_vault_file_they_name(self):
        attachment = self.docs / "_attachments/flow.png"
        attachment.parent.mkdir()
        attachment.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.add_scope_text(1, "![[_attachments/flow.png]]")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(next(item["reasons"] for item in value["files"] if item["path"] == "_attachments/flow.png"),
                         ["embed from backlog/epics/delivery-fixture/stories/st-001/story.md"])
        attachment.unlink()
        with self.assertRaisesRegex(inputs.InputError, "embeds a missing or invalid file"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_generated_inverse_links_do_not_pull_unrelated_epic(self):
        path = self.story(1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n## Related knowledge <!-- sec: relations:generated:start -->\n"
                         "[[backlog/epics/second/stories/st-003/story|ST-003]]\n"
                         "<!-- sec: relations:generated:end -->\n")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertNotIn(self.story(3).relative_to(self.docs).as_posix(), value["paths"])

    def test_navigation_variants_do_not_hide_later_semantic_sections(self):
        for marker in ("<!-- sec: nav -->", "## Navigation <!-- sec: nav -->"):
            body = ("# Story\n\n" + marker + "\n[[maps/backlog|Backlog]]\n\n"
                    "## Contract\n[[solution-design/landscape|Contract]]\n")
            semantic = inputs.semantic_body(body)
            self.assertNotIn("maps/backlog", semantic)
            self.assertIn("[[solution-design/landscape|Contract]]", semantic)

    def test_application_receipt_resolution_is_unique_and_hash_bound(self):
        master = self.docs / "design-system/MASTER.md"
        props, body = backlog.parse_front_matter(master)
        props["application_ref"] = "application@r1"
        master.write_text(backlog.front_matter(props, body), encoding="utf-8")
        receipt_path = self.docs / "experience-design/application/application.json"
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text('{"application_revision": 1}\n', encoding="utf-8")
        receipt = {"result_ref": "application@r1", "path": str(receipt_path.resolve())}
        original_candidates = inputs.stage_package.candidates

        def candidates(docs, stage):
            return [*original_candidates(docs, stage), receipt]

        with mock.patch.object(inputs.stage_package, "candidates", side_effect=candidates):
            value = inputs.manifest(self.docs, epic="EP-001")
            relative = receipt_path.relative_to(self.docs).as_posix()
            self.assertIn(relative, value["context_paths"])
            self.assertEqual(next(item["sha256"] for item in value["files"] if item["path"] == relative),
                             inputs.file_hash(receipt_path))
            receipt_path.write_text('{"application_revision": 2}\n', encoding="utf-8")
            with self.assertRaisesRegex(inputs.InputError, "stale"):
                inputs.manifest(self.docs, epic="EP-001", expected_hash=value["source_hash"])
        for choices in ([], [receipt, receipt]):
            with mock.patch.object(inputs.stage_package, "candidates", return_value=choices):
                with self.assertRaisesRegex(inputs.InputError, "uniquely"):
                    inputs.manifest(self.docs, epic="EP-001")

    def test_case_ambiguous_reference_is_refused(self):
        with mock.patch.object(Path, "iterdir", return_value=iter([self.docs / "backlog", self.docs / "Backlog"])):
            with self.assertRaisesRegex(inputs.InputError, "ambiguous"):
                inputs.regular_file(self.docs, "backlog/backlog.md")

    def test_unchanged_manifest_is_stable_read_only_and_fresh(self):
        before = {str(path): path.read_bytes() for path in self.docs.rglob("*") if path.is_file()}
        first = inputs.manifest(self.docs, epic="EP-001")
        second = inputs.manifest(self.docs, epic="EP-001", expected_hash=first["source_hash"])
        self.assertEqual(first, second)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.docs.rglob("*") if path.is_file()})

    def test_outside_epic_edit_invalidates_a_previous_manifest(self):
        previous = inputs.manifest(self.docs, epic="EP-001")
        with self.story(4).open("a", encoding="utf-8") as handle:
            handle.write("\nA changed external story may change review context.\n")
        with self.assertRaisesRegex(inputs.InputError, "stale"):
            inputs.manifest(self.docs, epic="EP-001", expected_hash=previous["source_hash"])

    def test_input_policy_helper_change_invalidates_previous_manifest(self):
        previous = inputs.manifest(self.docs, epic="EP-001")
        original_hash = inputs.file_hash
        def changed_policy(path):
            return "sha256:" + "0" * 64 if path.name == "backlog_input_policy.py" else original_hash(path)
        with mock.patch.object(inputs, "file_hash", side_effect=changed_policy):
            with self.assertRaisesRegex(inputs.InputError, "stale"):
                inputs.manifest(self.docs, epic="EP-001", expected_hash=previous["source_hash"])

    def test_new_incoming_dependency_invalidates_previous_manifest(self):
        previous = inputs.manifest(self.docs, epic="EP-001")
        self.depends(3, 1)
        self.refresh_reviews()
        with self.assertRaisesRegex(inputs.InputError, "stale"):
            inputs.manifest(self.docs, epic="EP-001", expected_hash=previous["source_hash"])

    def test_ambiguous_epic_and_duplicate_ids_fail_closed(self):
        with self.assertRaisesRegex(inputs.InputError, "uniquely"):
            inputs.manifest(self.docs, epic="missing")
        props, body = backlog.parse_front_matter(self.story(3))
        props["id"] = "ST-001"
        self.story(3).write_text(backlog.front_matter(props, body), encoding="utf-8")
        with self.assertRaises(inputs.InputError):
            inputs.manifest(self.docs)

    def test_path_escape_and_symlink_are_refused(self):
        for path in ("../outside.md", "/outside.md", "backlog//backlog.md", "backlog/../backlog.md"):
            with self.assertRaises(inputs.InputError):
                inputs.regular_file(self.docs, path)
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(inputs.InputError):
                inputs.regular_file(self.docs, "backlog/backlog.md")

    def test_current_source_changes_during_collect_are_refused(self):
        original = backlog.collect
        def changing(docs, **kwargs):
            result = original(docs, **kwargs)
            with self.story(4).open("a", encoding="utf-8") as handle:
                handle.write("\nConcurrent edit.\n")
            return result
        with mock.patch.object(backlog, "collect", side_effect=changing):
            with self.assertRaisesRegex(inputs.InputError, "changed during"):
                inputs.manifest(self.docs, epic="EP-001")

    def test_check_block_carries_compiler_facts_for_the_current_review(self):
        value = inputs.manifest(self.docs, epic="EP-001")
        check = value["check"]
        self.assertEqual(check["source_errors"], [])
        self.assertEqual(check["review_note"], {
            "path": "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md",
            "pending_findings": [],
        })
        self.assertEqual(check["relation_audit"], {key: {"state": "exact"} for key in (
            "dependency_refs", "derives_from", "scenario_refs", "verifies")})
        self.assertEqual(check["counts"], {
            "epics": 1, "stories": 2, "test_plans": 2, "scenarios": 2,
            "planning_sources": 2, "dependency_refs": 0,
        })
        self.assertEqual(check["stories"], {
            identity: {"epic": "EP-001", "scenarios": 1,
                       "source_scenarios": {"delivery:AC-DEL-001": [f"{identity}-TS-001"]}}
            for identity in ("ST-001", "ST-002")
        })
        self.assertEqual(value["source_hash"], inputs.digest(
            {key: item for key, item in value.items() if key != "source_hash"}))
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001",
                                         expected_hash=value["source_hash"]), value)

    def test_root_check_counts_the_backlog_its_dependencies_and_deferrals(self):
        self.depends(1, 3)
        self.refresh_reviews()
        registry = self.docs / "business-analysis/delivery/_generated/registry.json"
        data = json.loads(registry.read_text(encoding="utf-8"))
        data["ids"]["AC-DEL-002"] = dict(data["ids"]["AC-DEL-001"])
        registry.write_text(json.dumps(data), encoding="utf-8")
        review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        header = "|---|---|---|---|\n"
        row = ("| [[business-analysis/delivery/domains/identity/acceptance/delivery-acceptance"
               "\\|delivery:AC-DEL-002]] | product_owner | The partner registration contract is"
               " not approved yet. | Revisit when the partner registration contract is approved. |\n")
        text = review.read_text(encoding="utf-8")
        self.assertIn(header, text)
        review.write_text(text.replace(header, header + row, 1), encoding="utf-8")
        check = inputs.manifest(self.docs)["check"]
        self.assertEqual(check["review_note"], {
            "path": "backlog/reviews/round-1-backlog-review.md", "pending_findings": []})
        self.assertEqual(check["relation_audit"], {key: {"state": "exact"} for key in (
            "dependency_refs", "derives_from", "related_to")})
        self.assertEqual(check["counts"], {
            "epics": 2, "stories": 4, "test_plans": 4, "scenarios": 4,
            "planning_sources": 4, "dependency_refs": 1, "deferred_criteria": 1,
        })
        self.assertEqual({identity: facts["epic"] for identity, facts in check["stories"].items()},
                         {"ST-001": "EP-001", "ST-002": "EP-001",
                          "ST-003": "EP-002", "ST-004": "EP-002"})

    def test_draft_review_check_reports_pending_writer_work_for_that_note_only(self):
        self.draft_reviews()
        epic = inputs.manifest(self.docs, epic="EP-001")["check"]
        self.assertEqual(epic["source_errors"], [])
        self.assertEqual(epic["relation_audit"]["verifies"], {"state": "pending"})
        self.assertEqual(epic["relation_audit"]["scenario_refs"], {"state": "pending"})
        pending = epic["review_note"]["pending_findings"]
        self.assertTrue(pending)
        self.assertEqual(pending, sorted(set(pending)))
        self.assertTrue(any("section-specific" in finding for finding in pending))
        self.assertFalse([finding for finding in pending
                          if "backlog/epics/second/" in finding or "backlog/reviews/" in finding])
        root = inputs.manifest(self.docs)["check"]
        self.assertEqual(root["relation_audit"]["related_to"], {"state": "pending"})
        self.assertTrue(root["review_note"]["pending_findings"])
        self.assertFalse([finding for finding in root["review_note"]["pending_findings"]
                          if "/reviews/round-1-epic-review.md" in finding])

    def stub_story(self, epic="delivery-fixture"):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backlog.stub_story(SimpleNamespace(
                docs=str(self.docs), epic=epic, slug="job-worker", id="ST-005",
                title="Job worker", scope=None, work_kind="technical", criterion_ref=[],
                experience_ref=[], evidence_ref=["[[solution-design/decisions/fixture-api|Fixture API]]"],
                uses_design=[], constrained_by=[], implements=[])), 0)
        return self.docs / f"backlog/epics/{epic}/stories/job-worker"

    def test_an_epic_reader_lists_a_stub_outside_its_paths_without_failing(self):
        folder = self.stub_story("second")
        story, plan = (path.relative_to(self.docs).as_posix()
                       for path in (folder / "story.md", folder / "test-plan.md"))
        value = inputs.manifest(self.docs, epic="EP-001")
        carried = value["check"]["scaffold_findings"]
        self.assertTrue(carried)
        self.assertTrue(all(finding.startswith((story + " ", plan + " ")) for finding in carried))
        self.assertNotIn(story, value["paths"])
        self.assertNotIn(plan, value["paths"])
        self.assertEqual(value["check"]["source_errors"], [])
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001", expected_hash=value["source_hash"]), value)
        # The epic that holds the stub, the root package and approval still wait for it.
        for scope in ("EP-002", None):
            with self.assertRaisesRegex(inputs.InputError, "untouched"):
                inputs.manifest(self.docs, epic=scope)
        _, errors = backlog.collect(self.docs)
        self.assertLessEqual(set(carried), set(errors))
        # An authored finding outside the epic's paths still fails it.
        props, body = backlog.parse_front_matter(self.story(3))
        props["constrained_by"] = ["[[solution-design/missing|Missing]]"]
        self.story(3).write_text(backlog.front_matter(props, body), encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "targets missing note: solution-design/missing"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_a_stub_in_an_epic_readers_dependency_closure_still_fails_it(self):
        self.stub_story("second")
        path = self.story(1)
        props, body = backlog.parse_front_matter(path)
        link = "[[backlog/epics/second/stories/job-worker/story|ST-005]]"
        props["depends_on"] = [link]
        body = body.replace("## Dependencies\n\nNone.", "## Dependencies\n\n- " + link + ": Supplies the job input.")
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "job-worker/story.md has an untouched"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_writer_manifest_carries_the_stubs_a_reader_refuses(self):
        self.assertNotIn("scaffold_findings", inputs.manifest(self.docs, epic="EP-001")["check"])
        folder = self.stub_story()
        story, plan = (path.relative_to(self.docs).as_posix()
                       for path in (folder / "story.md", folder / "test-plan.md"))
        with self.assertRaisesRegex(inputs.InputError, "untouched") as refused:
            inputs.manifest(self.docs, epic="EP-001")
        refused_findings = str(refused.exception).removeprefix(
            "backlog structure is invalid: ").split("; ")
        value = inputs.manifest(self.docs, epic="EP-001", writer=True)
        carried = value["check"]["scaffold_findings"]
        self.assertEqual(carried, sorted(refused_findings))
        self.assertTrue(all(finding.startswith((story + " ", plan + " ")) for finding in carried))
        self.assertTrue(any("scenarios are not classified" in finding for finding in carried))
        self.assertIn(story, value["primary_paths"])
        self.assertIn(plan, value["primary_paths"])
        self.assertEqual(value["check"]["source_errors"], [])
        _, errors = backlog.collect(self.docs)
        self.assertLessEqual(set(carried), set(errors))
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001", writer=True,
                                         expected_hash=value["source_hash"]), value)

    def test_writer_manifest_still_fails_on_findings_that_are_not_stubs(self):
        folder = self.stub_story()
        plan = folder / "test-plan.md"
        stub = plan.read_text(encoding="utf-8")
        authored = stub
        for coverage in backlog.SCENARIO_COVERAGE_CLASSES:
            authored = authored.replace(
                f"| {coverage} | not_applicable | - | {backlog.COVERAGE_REASON_STUB} |",
                f"| {coverage} | not_applicable | - | The worker exposes no {coverage} behavior to verify. |")
        plan.write_text(authored, encoding="utf-8")
        # With no stub row left, an unclassified scenario is an authored gap.
        with self.assertRaisesRegex(inputs.InputError, "not classified by Coverage Classes"):
            inputs.manifest(self.docs, epic="EP-001", writer=True)
        plan.write_text(stub.replace(
            f"| empty | not_applicable | - | {backlog.COVERAGE_REASON_STUB} |",
            "| empty | not_applicable | - | n/a |"), encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "class empty needs a concrete reason"):
            inputs.manifest(self.docs, epic="EP-001", writer=True)
        plan.write_text(stub, encoding="utf-8")
        self.depends(1, 3)
        self.depends(3, 1)
        with self.assertRaisesRegex(inputs.InputError, "cycle"):
            inputs.manifest(self.docs, epic="EP-001", writer=True)

    def test_check_block_audits_declared_against_expected_relations(self):
        path = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        props, body = backlog.parse_front_matter(path)
        props["verifies"] = props["verifies"][:-1] + props["verifies"][:1] + [
            "[[backlog/epics/second/stories/st-003/story|ST-003]]"]
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(value["check"]["relation_audit"]["verifies"], {
            "state": "differs",
            "missing": ["backlog/epics/delivery-fixture/stories/st-002/test-plan"],
            "extra": ["backlog/epics/second/stories/st-003/story"],
            "duplicates": ["backlog/epics/delivery-fixture/stories/st-001/story"],
        })
        self.assertIn(
            "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md verifies set does not"
            " exactly cover every story and test plan",
            value["check"]["review_note"]["pending_findings"],
        )
        self.assertEqual(len(value["review"]["expected_relations"]["verifies"]), 4)

    def test_each_input_is_validated_once_per_run_and_again_at_close(self):
        # Closure walks revisit shared paths; validation cost must follow the
        # read set, not the number of visits.
        original = inputs.regular_file
        calls = []

        def counting(docs, relative):
            calls.append(relative)
            return original(docs, relative)

        with mock.patch.object(inputs, "regular_file", side_effect=counting):
            value = inputs.manifest(self.docs, epic="EP-001")
        snapshot = inputs.snapshot(self.docs)
        self.assertEqual(len(calls), 2 * len(snapshot) + 2 * len(value["paths"]))
        for path in value["paths"]:
            self.assertEqual(calls.count(path), 2 + 2 * (path in snapshot), path)

    def test_ba_registry_is_parsed_once_per_read_session(self):
        value = ("[[business-analysis/delivery/domains/identity/acceptance/"
                 "delivery-acceptance|delivery:AC-DEL-001]]")
        registry = self.docs / "business-analysis/delivery/_generated/registry.json"
        original = Path.read_text
        reads = []

        def counting(path, *args, **kwargs):
            if path == registry:
                reads.append(path)
            return original(path, *args, **kwargs)

        errors = []
        with mock.patch.object(Path, "read_text", counting):
            for _ in range(2):
                backlog.validate_criterion_ref(self.docs, value, "label", errors)
            self.assertEqual(len(reads), 2)
            with backlog.experience_validation_session():
                for _ in range(3):
                    backlog.validate_criterion_ref(self.docs, value, "label", errors)
            self.assertEqual(len(reads), 3)
            with backlog.experience_validation_session():
                backlog.validate_criterion_ref(self.docs, value, "label", errors)
            self.assertEqual(len(reads), 4)
        self.assertEqual(errors, [])

    def test_ba_space_is_scanned_once_per_candidate_session(self):
        import ba_compile
        space = self.docs / "business-analysis/delivery"
        stage = inputs.stage_package
        with mock.patch.object(ba_compile, "scan_space", wraps=ba_compile.scan_space) as scan:
            stage._session_ba_scan(space)
            stage._session_ba_scan(space)
            self.assertEqual(scan.call_count, 2)
            with stage.candidate_session():
                first = stage._session_ba_scan(space)
                self.assertIs(stage._session_ba_scan(space), first)
                with stage.candidate_session():
                    self.assertIs(stage._session_ba_scan(space), first)
            self.assertEqual(scan.call_count, 3)
            with stage.candidate_session():
                self.assertIsNot(stage._session_ba_scan(space), first)
            self.assertEqual(scan.call_count, 4)

    def test_cli_reports_failure_and_success_without_writing_manifest(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = inputs.main(["--docs", str(self.docs), "--root"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = inputs.main(["--docs", str(self.docs), "--root", "--expected-hash", "sha256:stale"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
