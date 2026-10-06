"""Review manifest scope: switch `review_manifest_scope` keeps every backlog
review manifest's read set as released at `transitive` and, at `bounded`,
limits an epic reader's manifest to the epic's dependency closure, the notes
that closure links to or cites and their front-matter relations."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile as backlog
import backlog_review_inputs as inputs
import process_policy
import task_inputs
import test_backlog_review_inputs as review_input_tests
from backlog_fixture import make_approved_backlog
from git_fixture import init_repository

REFERENCE = "skill-content/backlog-plan/references/switch-review_manifest_scope-bounded.md"
FLOW = "flows/backlog-planning.md"
BA = "business-analysis/delivery/domains/identity"


def policy(docs: Path, *argv: str) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main([argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output.getvalue())


def choose(docs: Path, value: str | None, *, switch: str = "review_manifest_scope") -> None:
    """Approve a policy revision that sets ``switch`` to ``value``, or its default."""
    exists = process_policy.path_for(docs).exists()
    policy(docs, "begin-revision" if exists else "init")
    if value is None:
        policy(docs, "set", "--switch", switch, "--default")
    else:
        policy(docs, "set", "--switch", switch, "--value", value)
    policy(docs, "approve")


class ReviewManifestScopeContractTests(unittest.TestCase):
    def test_default_path_instructions_never_name_the_switch(self):
        for path in TEAM.rglob("*.md"):
            relative = path.relative_to(TEAM).as_posix()
            if path.name.startswith("switch-") or relative == FLOW:
                continue
            with self.subTest(path=relative):
                self.assertNotIn("review_manifest_scope", path.read_text(encoding="utf-8"))


class BoundedManifestTests(unittest.TestCase):
    """The two-epic backlog of test_backlog_review_inputs, read under both values."""

    setUp = review_input_tests.BacklogReviewInputTests.setUp
    refresh_reviews = review_input_tests.BacklogReviewInputTests.refresh_reviews
    story = review_input_tests.BacklogReviewInputTests.story
    depends = review_input_tests.BacklogReviewInputTests.depends
    add_scope_text = review_input_tests.BacklogReviewInputTests.add_scope_text
    stub_story = review_input_tests.BacklogReviewInputTests.stub_story

    def relative(self, path: Path) -> str:
        return path.relative_to(self.docs).as_posix()

    def note(self, relative: str, props: dict, body: str = "") -> str:
        path = self.docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(backlog.front_matter(props, f"# {props['title']}\n\n{body}"),
                        encoding="utf-8")
        return relative

    def chain(self) -> dict[str, str]:
        """ST-001 links a rule set whose front matter and body link further notes."""
        notes = {
            "far": self.note(f"{BA}/entities/ledger-entity.md", {"type": "entity", "title": "Ledger"}),
            "body": self.note(f"{BA}/entities/audit-entity.md", {"type": "entity", "title": "Audit"}),
        }
        notes["relation"] = self.note(
            f"{BA}/entities/account-entity.md",
            {"type": "entity", "title": "Account", "governs": [f"[[{notes['far'][:-3]}]]"]})
        notes["linked"] = self.note(
            f"{BA}/rules/account-rules.md",
            {"type": "rule_set", "title": "Account rules", "status": "approved",
             "governs": [f"[[{notes['relation'][:-3]}]]"]},
            f"Audit rules follow [[{notes['body'][:-3]}|Audit]].\n")
        self.add_scope_text(1, f"It applies [[{notes['linked'][:-3]}|Account rules]].")
        return notes

    def test_default_and_a_policy_that_keeps_it_read_the_default_manifests(self):
        self.depends(1, 3)
        self.refresh_reviews()
        # A Process Policy's init first records in each draft round that its
        # review ran under none; recorded here, every read compares the same notes.
        backlog.pin_rounds_before_policy_change(self.docs)
        scopes = {"reader": {"epic": "EP-001"}, "writer": {"epic": "EP-001", "writer": True},
                  "root": {}}
        plain = {name: inputs.manifest(self.docs, **kwargs) for name, kwargs in scopes.items()}
        self.assertNotIn("review_manifest_scope", plain["reader"])
        # A switch no manifest reads leaves every review manifest byte for byte
        # as it is without a policy.
        choose(self.docs, "mechanical", switch="mechanical_pass_tier")
        for name, kwargs in scopes.items():
            with self.subTest(scope=name):
                self.assertEqual(inputs.manifest(self.docs, **kwargs), plain[name])

    def test_an_epic_reader_follows_links_only_from_its_scope_and_closure(self):
        notes = self.chain()
        transitive = inputs.manifest(self.docs, epic="EP-001")
        self.assertLessEqual(set(notes.values()), set(transitive["paths"]))
        choose(self.docs, "bounded")
        bounded = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(bounded["review_manifest_scope"], "bounded")
        self.assertEqual(bounded["primary_paths"], transitive["primary_paths"])
        self.assertIn(notes["linked"], bounded["context_paths"])
        # The linked note's front-matter relation is read one hop further ...
        self.assertIn(notes["relation"], bounded["context_paths"])
        # ... and neither its body links nor that relation's own links are.
        self.assertNotIn(notes["body"], bounded["paths"])
        self.assertNotIn(notes["far"], bounded["paths"])
        self.assertLess(set(bounded["paths"]), set(transitive["paths"]))
        self.assertEqual(bounded["review"], transitive["review"])
        self.assertEqual(bounded.get("check"), transitive.get("check"))

    def test_the_dependency_closure_keeps_its_own_links(self):
        self.depends(1, 3)
        self.depends(3, 4)
        self.refresh_reviews()
        self.add_scope_text(4, f"It applies [[{BA}/acceptance/delivery-acceptance|Acceptance]].")
        choose(self.docs, "bounded")
        value = inputs.manifest(self.docs, epic="EP-001")
        for number in (3, 4):
            path = self.relative(self.story(number))
            self.assertIn(path, value["context_paths"])
            self.assertIn(path.replace("story.md", "test-plan.md"), value["context_paths"])
        self.assertIn("backlog/epics/second/epic.md", value["context_paths"])
        for upstream in ("design-system/MASTER.md", "solution-design/landscape.md",
                         f"{BA}/acceptance/delivery-acceptance.md",
                         "experience-design/experiences/checkout/screens/checkout-screen.md"):
            with self.subTest(upstream=upstream):
                self.assertIn(upstream, value["context_paths"])

    def test_a_story_reached_by_a_link_is_read_without_its_closure(self):
        self.add_scope_text(1, "It hands over to [[backlog/epics/second/stories/st-004/story|ST-004]].")
        story = self.relative(self.story(4))
        plan = story.replace("story.md", "test-plan.md")
        transitive = inputs.manifest(self.docs, epic="EP-001")
        self.assertIn(plan, transitive["paths"])
        choose(self.docs, "bounded")
        bounded = inputs.manifest(self.docs, epic="EP-001")
        self.assertIn(story, bounded["context_paths"])
        self.assertNotIn(plan, bounded["paths"])
        # Its derives_from relation still names its epic one hop further.
        self.assertIn("backlog/epics/second/epic.md", bounded["context_paths"])
        self.assertNotIn(self.relative(self.story(3)), bounded["paths"])

    def test_the_root_backlog_and_review_notes_are_read_without_their_links(self):
        review = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        note = self.note("solution-design/notes/review-evidence.md",
                         {"type": "note", "title": "Review evidence"})
        with review.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## Additional evidence\n[[{note[:-3]}|Review evidence]]\n")
        self.assertIn(note, inputs.manifest(self.docs, epic="EP-001")["paths"])
        choose(self.docs, "bounded")
        bounded = inputs.manifest(self.docs, epic="EP-001")
        self.assertIn(self.relative(review), bounded["paths"])
        self.assertIn("backlog/backlog.md", bounded["primary_paths"])
        self.assertNotIn(note, bounded["paths"])

    def test_a_review_note_linked_from_the_closure_adds_its_relations(self):
        note = self.note("solution-design/notes/review-context.md",
                         {"type": "note", "title": "Review context"})
        review = self.docs / "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        props, body = backlog.parse_front_matter(review)
        props["related_to"] = [f"[[{note[:-3]}|Review context]]"]
        review.write_text(backlog.front_matter(props, body), encoding="utf-8")
        choose(self.docs, "bounded")
        self.assertNotIn(note, inputs.manifest(self.docs, epic="EP-001")["paths"])
        self.add_scope_text(1, f"It follows [[{self.relative(review)[:-3]}|Review]].")
        # Linked from a story, the review note is one hop out and adds its relations.
        self.assertIn(note, inputs.manifest(self.docs, epic="EP-001")["context_paths"])

    def test_an_experience_record_cited_by_id_is_read_as_its_note(self):
        screen = self.note(
            "experience-design/experiences/checkout/screens/confirmation-screen.md",
            {"type": "screen", "title": "Confirmation screen", "id": "SCR-002", "revision": 1,
             "record_state": "active"})
        master = self.docs / "design-system/MASTER.md"
        props, body = backlog.parse_front_matter(master)
        props["screen_refs"] = ["checkout:SCR-002@r1"]
        master.write_text(backlog.front_matter(props, body), encoding="utf-8")
        choose(self.docs, "bounded")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertIn(screen, value["context_paths"])
        self.assertEqual(next(item["reasons"] for item in value["files"] if item["path"] == screen),
                         ["record reference from design-system/MASTER.md"])
        props["screen_refs"] = ["checkout:SCR-009@r1"]
        master.write_text(backlog.front_matter(props, body), encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "cites a missing Experience record"):
            inputs.manifest(self.docs, epic="EP-001")

    def test_root_and_writer_manifests_keep_the_transitive_read_set(self):
        self.chain()
        # A Process Policy's init first records in each draft round that its
        # review ran under none; recorded here, every read compares the same notes.
        backlog.pin_rounds_before_policy_change(self.docs)
        before = {"root": inputs.manifest(self.docs),
                  "writer": inputs.manifest(self.docs, epic="EP-001", writer=True)}
        choose(self.docs, "bounded")
        self.assertEqual(inputs.manifest(self.docs), before["root"])
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001", writer=True), before["writer"])

    def test_an_epic_reader_fails_only_on_the_stubs_its_bounded_manifest_reads(self):
        folder = self.stub_story("second")
        path = folder / "story.md"
        props, body = backlog.parse_front_matter(path)
        link = "[[backlog/epics/second/stories/st-004/story|ST-004]]"
        props["depends_on"] = [link]
        body = body.replace("## Dependencies\n\nNone.",
                            f"## Dependencies\n\n- {link}: Supplies the job input.")
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.add_scope_text(1, "It hands over to [[backlog/epics/second/stories/st-004/story|ST-004]].")
        # The transitive read reaches the stub through ST-004's dependency closure.
        with self.assertRaisesRegex(inputs.InputError, "untouched"):
            inputs.manifest(self.docs, epic="EP-001")
        choose(self.docs, "bounded")
        value = inputs.manifest(self.docs, epic="EP-001")
        carried = value["check"]["scaffold_findings"]
        self.assertTrue(carried)
        self.assertTrue(all(finding.startswith(self.relative(folder) + "/") for finding in carried))
        self.assertNotIn(self.relative(path), value["paths"])
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001",
                                         expected_hash=value["source_hash"]), value)
        # The stub's own epic still reads it.
        with self.assertRaisesRegex(inputs.InputError, "untouched"):
            inputs.manifest(self.docs, epic="EP-002")

    def test_an_edge_to_a_story_read_through_a_link_leaves_a_bounded_manifest_fresh(self):
        self.add_scope_text(1, "It hands over to [[backlog/epics/second/stories/st-004/story|ST-004]].")
        transitive = inputs.manifest(self.docs, epic="EP-001")
        choose(self.docs, "bounded")
        bounded = inputs.manifest(self.docs, epic="EP-001")
        self.assertNotIn(self.relative(self.story(3)), bounded["paths"])
        # ST-003, which the bounded reader never reads, comes to depend on ST-004,
        # which it reads through a link alone: nothing it reads or follows moves.
        self.depends(3, 4)
        self.refresh_reviews()
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001",
                                         expected_hash=bounded["source_hash"]), bounded)
        # The transitive reader follows ST-004's edges, so it reads ST-003 now.
        choose(self.docs, None)
        with self.assertRaisesRegex(inputs.InputError, "stale"):
            inputs.manifest(self.docs, epic="EP-001", expected_hash=transitive["source_hash"])

    def test_an_edge_into_the_dependency_closure_makes_a_bounded_manifest_stale(self):
        choose(self.docs, "bounded")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.depends(3, 1)
        self.refresh_reviews()
        with self.assertRaisesRegex(inputs.InputError, "stale"):
            inputs.manifest(self.docs, epic="EP-001", expected_hash=value["source_hash"])
        self.assertIn(self.relative(self.story(3)),
                      inputs.manifest(self.docs, epic="EP-001")["context_paths"])

    def test_a_changed_value_makes_a_manifest_stale(self):
        choose(self.docs, "bounded")
        bounded = inputs.manifest(self.docs, epic="EP-001")
        choose(self.docs, None)
        transitive = inputs.manifest(self.docs, epic="EP-001")
        self.assertNotIn("review_manifest_scope", transitive)
        for previous in (bounded, transitive):
            choose(self.docs, None if previous is bounded else "bounded")
            with self.subTest(previous=previous.get("review_manifest_scope", "transitive")):
                with self.assertRaisesRegex(inputs.InputError, "stale"):
                    inputs.manifest(self.docs, epic="EP-001", expected_hash=previous["source_hash"])

    def test_a_draft_policy_refuses_an_epic_reader(self):
        choose(self.docs, "bounded")
        policy(self.docs, "begin-revision")
        with self.assertRaisesRegex(inputs.InputError,
                                    "process policy cannot set the review manifest scope: Process"
                                    " Policy revision 2 is a draft"):
            inputs.manifest(self.docs, epic="EP-001")


@integration
class BoundedTaskInputTests(unittest.TestCase):
    def test_a_reviewer_task_binds_the_reference_and_the_bounded_closure_only_at_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            init_repository(root)
            subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"],
                           check=True, capture_output=True)
            docs = root / "workspace/docs"
            (docs / "maps").mkdir(parents=True)
            (root / "workspace/config.json").write_text(json.dumps({
                "schema_version": 2, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
            }), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                make_approved_backlog(docs)

            def commit():
                for args in (("add", "-A"), ("-c", "user.name=Fixture", "-c",
                                             "user.email=fixture@example.invalid", "-c",
                                             "commit.gpgsign=false", "commit", "-qm", "Fixture")):
                    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

            commit()
            tasks = {"reviewer": dict(entry="backlog-plan", role="backlog-reviewer", mode="review"),
                     "writer": dict(entry="backlog-plan", role="product-owner", mode="revise")}
            for value in (None, "bounded"):
                if value:
                    choose(docs, value)
                    commit()
                for name, task in tasks.items():
                    result = task_inputs.manifest(**task, project=root, epic="EP-001")
                    bounded = value == "bounded" and name == "reviewer"
                    with self.subTest(value=value, task=name):
                        self.assertEqual(REFERENCE in result["required_reads"], value == "bounded")
                        self.assertEqual(result["backlog_scope"].get("review_manifest_scope"),
                                         "bounded" if bounded else None)


if __name__ == "__main__":
    unittest.main()
