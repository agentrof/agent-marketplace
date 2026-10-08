"""Process switch source_rebind (#463): a backlog revision that only rebinds its
upstream sources reuses the approved review of every epic its changed sources
leave unimpacted, through an owner-approved receipt that every later check
replays from Git, and a Delivery keeps its execution approval across it."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.tests.levels import integration

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tools/tests"))
import backlog_compile as compiler  # noqa: E402
import backlog_migration  # noqa: E402
import backlog_rebind as rebind  # noqa: E402
import backlog_review_inputs  # noqa: E402
import delivery_compile  # noqa: E402
import process_policy  # noqa: E402
import requirement_compile  # noqa: E402
import stage_package  # noqa: E402
from tools.tests import test_backlog_requirement_bindings as bindings  # noqa: E402
from tools.tests.backlog_fixture import (  # noqa: E402
    CONSTRAINT, CRITERION, DESIGN, EXPERIENCE, _author_story, _complete_review_body)

FIRST = "delivery-fixture"
SECOND = "billing"
SOLUTION = "solution-design/landscape"
DECISION = "solution-design/decisions/fixture-cache"
REFS = [bindings.BA, SOLUTION, bindings.DESIGN, bindings.APPLICATION, bindings.PROCESS]
GIT = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
DECISION_TEXT = ("---\ntype: decision\ntitle: Fixture cache\nstatus: accepted\n"
                 "decision_kind: technology-selection\napplies_to:\n  - api\n"
                 "selected_technology: redis\ntags:\n  - doc/decision\n  - status/accepted\n"
                 "{extra}---\n\n# Fixture cache\n\n| id | rule |\n|---|---|\n"
                 "| SOL-CACHE-001 | {rule} |\n| SOL-CACHE-002 | Entries never outlive a session. |\n")


def quiet(function, *args, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = function(*args, **kwargs)
    return code, output.getvalue()


@integration
class SourceRebindTests(unittest.TestCase):
    """A two-epic backlog approved with bindings, then a Solution revision."""

    def setUp(self):
        from tools.tests.test_delivery_compile import DeliveryCompilerTests
        self.fixture = DeliveryCompilerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.docs, self.project = self.fixture.docs, self.fixture.root
        # The binding fixture replaces an empty mapping with its own, so this one starts filled.
        self.receipts = {SOLUTION: bindings.digest("5")}
        mocks = contextlib.ExitStack()
        self.addCleanup(mocks.close)
        holder = bindings.RequirementBindingTests()
        holder.docs = self.docs
        mocks.enter_context(holder.upstreams(receipts=self.receipts))
        mocks.enter_context(mock.patch.object(compiler, "validate_experience_ref"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.fixture.approve_verification_contract()
        self.write_decision("Entries expire after ten minutes.")
        self.requirement()
        self.commit("Approved sources")
        self.pin_solution()
        self.revise()
        self.add_second_epic()
        self.review_epic(FIRST, 2)
        self.review_epic(SECOND, 1)
        self.complete_root(2)
        code, output = quiet(compiler.approve, SimpleNamespace(docs=str(self.docs)))
        self.assertEqual(code, 0, output)
        self.commit("Approved backlog revision 2")
        self.predecessor = self.head()
        self.before_hash = self.package_hash()

    # Fixture steps.

    def git(self, *argv: str) -> str:
        return subprocess.run(["git", *GIT, *argv], cwd=self.project, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def package_hash(self) -> str:
        return compiler.parse_front_matter(self.docs / "backlog/backlog.md")[0]["package_hash"]

    def write_decision(self, rule: str, extra: str = "") -> None:
        path = self.docs / f"{DECISION}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DECISION_TEXT.format(rule=rule, extra=extra), encoding="utf-8")

    def pin_solution(self) -> None:
        """Approve the Solution package at its current content, as its compiler would."""
        landscape = self.docs / "solution-design/landscape.md"
        props, body = compiler.parse_front_matter(landscape)
        props["package_hash"] = stage_package.tree_hash(
            self.docs / "solution-design", {"package_hash", "package_status", "package_approved_at_utc"})
        landscape.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.receipts[SOLUTION] = props["package_hash"]
        self.commit("Approved Solution package")

    def requirement(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            path = requirement_compile.create_requirement(
                self.docs, "cache-rules", "Cache rules", "technical", "high", None, [])
        props, body = requirement_compile.split_note(path)
        for placeholder, text in {
            "TODO: state the requested change and who needs it.": "Cache the session lookups.",
            "TODO: state the observable outcome and acceptance boundary.": "- Lookups are cached.",
            "TODO: define included and excluded behavior.": "Include the cache. Exclude eviction tuning.",
            "TODO: record evidence, constraints and urgency rationale.": "Lookups are slow.",
        }.items():
            body = body.replace(placeholder, text)
        for stage in requirement_compile.STAGES:
            body = body.replace(f"| {stage} | required |  | TODO: explain why this stage must change. |",
                                f"| {stage} | not_applicable |  | No {stage} surface changes. |")
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            requirement_compile.approve_requirement(path)

    def revise(self) -> None:
        code, output = quiet(compiler.begin_revision, SimpleNamespace(
            docs=str(self.docs), delivery_snapshot="", planning_mode="requirement",
            requirement_ref="REQ-001", absent_input=[], input_ref=list(REFS)))
        self.assertEqual(code, 0, output)

    def add_second_epic(self) -> None:
        code, output = quiet(compiler.stub_epic, SimpleNamespace(
            docs=str(self.docs), slug=SECOND, id="EP-002", title=None,
            goal="Bill customers for the approved account capabilities.", new_review=False))
        self.assertEqual(code, 0, output)
        code, output = quiet(compiler.stub_story, SimpleNamespace(
            docs=str(self.docs), epic=SECOND, slug="bl-001", id="BL-001", title=None,
            scope="Deliver the observable outcome defined by BL-001.", work_kind="feature",
            criterion_ref=[CRITERION], experience_ref=[EXPERIENCE], evidence_ref=[],
            uses_design=[DESIGN], constrained_by=[CONSTRAINT], implements=[], from_requirement=False))
        self.assertEqual(code, 0, output)
        folder = self.docs / "backlog/epics" / SECOND / "stories/bl-001"
        _author_story(folder / "story.md", folder / "test-plan.md", "BL-001")

    def review_epic(self, slug: str, number: int, *, new_round: bool = True, extra: str = "") -> Path:
        if new_round and number > 1:
            code, output = quiet(compiler.stub_epic, SimpleNamespace(
                docs=str(self.docs), slug=slug, id=None, title=None, goal=None, new_review=True))
            self.assertEqual(code, 0, output)
        path = self.docs / "backlog/epics" / slug / f"reviews/round-{number}-epic-review.md"
        props, body = compiler.parse_front_matter(path)
        stories = sorted((self.docs / "backlog/epics" / slug / "stories").iterdir())
        props.update(verdict="approved", dependency_refs=[],
                     verifies=[link for story in stories for link in (
                         f"[[backlog/epics/{slug}/stories/{story.name}/story|{story.name.upper()}]]",
                         f"[[backlog/epics/{slug}/stories/{story.name}/test-plan|{story.name.upper()}-TP]]")],
                     scenario_refs=[f"{story.name.upper()}-TS-001" for story in stories])
        complete = _complete_review_body(str(props["title"]),
                                         compiler.backlog_contract()["required_epic_review_sections"])
        nav = body.split(compiler.NAV_MARKER, 1)
        path.write_text(compiler.front_matter(
            props, complete + extra + ("\n" + compiler.NAV_MARKER + nav[1] if len(nav) == 2 else "")),
            encoding="utf-8")
        return path

    def complete_root(self, number: int, *, keep_compiled: bool = False) -> None:
        path = self.docs / f"backlog/reviews/round-{number}-backlog-review.md"
        props, body = compiler.parse_front_matter(path)
        props.update(verdict="approved", dependency_refs=[], related_to=[
            f"[[backlog/epics/{FIRST}/epic|EP-001]]", f"[[backlog/epics/{SECOND}/epic|EP-002]]"])
        sections = compiler.backlog_review_sections("requirement")
        complete = _complete_review_body(str(props["title"]), sections,
                                         ("| REQ-001 | BL-001 | covered |",))
        if keep_compiled:
            main = body.split(compiler.NAV_MARKER, 1)[0]
            for name in ("Cross-Epic Overlap", "Findings", "Verdict"):
                main = compiler.replace_section(main, name, compiler.section(complete, name))
            complete = main
        nav = body.split(compiler.NAV_MARKER, 1)
        path.write_text(compiler.front_matter(
            props, complete + ("\n" + compiler.NAV_MARKER + nav[1] if len(nav) == 2 else "")),
            encoding="utf-8")

    def switch_on(self) -> None:
        exists = process_policy.path_for(self.docs).exists()
        for argv in (["begin-revision" if exists else "init"],
                     ["set", "--switch", rebind.SWITCH, "--value", rebind.VALUE], ["approve"]):
            code, output = quiet(process_policy.main, [argv[0], "--docs", str(self.docs), *argv[1:]])
            self.assertEqual(code, 0, output)

    def revise_solution(self, rule: str = "Entries expire after five minutes.", extra: str = "") -> None:
        self.write_decision(rule, extra)
        self.commit("Revised Solution decision")
        self.pin_solution()

    def plan(self, *epics: str) -> dict:
        return rebind.plan(self.docs, self.predecessor, list(epics))

    def record(self, *epics: str) -> dict:
        code, output = quiet(rebind.record_root_review, SimpleNamespace(
            docs=str(self.docs), source_commit=self.predecessor, review_epic=list(epics)))
        self.assertEqual(code, 0, output)
        return json.loads(output)["receipt"]

    def apply(self, receipt: dict, *epics: str) -> tuple[int, str]:
        return quiet(rebind.command, SimpleNamespace(
            docs=str(self.docs), command="apply-source-rebind", source_commit=self.predecessor,
            review_epic=list(epics), approve_receipt=receipt["owner_approval"]))

    def rebind_revision(self, rule: str = "Entries expire after five minutes.", extra: str = "") -> None:
        self.switch_on()
        self.revise_solution(rule, extra)
        self.revise()

    def epic_rounds(self, slug: str) -> list[str]:
        return sorted(path.name for path in (self.docs / "backlog/epics" / slug / "reviews").glob("*.md"))

    # Tests.

    def test_a_solution_revision_no_story_cites_reuses_every_epic_review(self):
        self.rebind_revision()
        receipt = self.record()
        self.assertEqual([(row["id"], row["disposition"]) for row in receipt["epics"]],
                         [("EP-001", "reused"), ("EP-002", "reused")])
        change, = receipt["binding_changes"]
        self.assertEqual((change["stage"], change["changed_documents"], change["changed_ids"]),
                         ("solution-design", [f"{DECISION}.md"], ["SOL-CACHE-001"]))
        self.assertEqual(receipt["root_scope"]["cited_stories"], [])
        manifest = backlog_review_inputs.manifest(self.docs)
        self.assertEqual(manifest["source_rebind"], rebind.VALUE)
        self.assertIn(f"{DECISION}.md", manifest["paths"])
        self.assertIn("SOL-CACHE-001", manifest["check"]["source_rebind"]["source_diff"][0]["diff"])
        self.assertTrue(all(row["read"] == "summary"
                            for row in manifest["check"]["backlog_graph"]["stories"].values()))
        with self.assertRaisesRegex(backlog_review_inputs.InputError, "reuses its approved review"):
            backlog_review_inputs.manifest(self.docs, epic="EP-001")
        self.complete_root(3, keep_compiled=True)
        code, output = quiet(compiler.approve, SimpleNamespace(docs=str(self.docs)))
        self.assertEqual(code, 1)
        self.assertIn("approved only by apply-source-rebind", output)
        code, output = self.apply(receipt)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.epic_rounds(FIRST), ["round-1-epic-review.md", "round-2-epic-review.md"])
        self.assertEqual(self.epic_rounds(SECOND), ["round-1-epic-review.md"])
        sealed = rebind.receipts(self.docs)[0]
        self.assertEqual(sealed["after_package_hash"], self.package_hash())
        self.commit("Approved source rebind")
        chain = backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash())
        self.assertEqual((chain.aliases, chain.impacted), ({}, frozenset()))

    def test_a_changed_story_reviews_only_its_epic(self):
        self.rebind_revision()
        story = self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md"
        story.write_text(story.read_text(encoding="utf-8").replace(
            "Administrative bulk operations remain outside this slice.",
            "Administrative bulk operations and refunds remain outside this slice."), encoding="utf-8")
        receipt = self.record()
        self.assertEqual({row["id"]: row["disposition"] for row in receipt["epics"]},
                         {"EP-001": "reused", "EP-002": "reviewed"})
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt)
        self.assertEqual(code, 1, output)
        self.assertIn("EP-002 is impacted by the source rebind", output)
        self.assertEqual(rebind.receipts(self.docs), [])
        self.review_epic(SECOND, 2)
        manifest = backlog_review_inputs.manifest(self.docs, epic="EP-002")
        self.assertEqual(manifest["check"]["backlog_graph"]["stories"]["BL-001"]["read"], "full")
        code, output = self.apply(receipt)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.epic_rounds(FIRST), ["round-1-epic-review.md", "round-2-epic-review.md"])

    def test_a_byte_identical_story_that_cites_a_changed_row_is_impacted(self):
        story = self.docs / "backlog/epics" / FIRST / "stories/auth-01/story.md"
        self.assertIn("AUTH-01", story.read_text(encoding="utf-8"))
        self.cite_in_predecessor(story, "Honour SOL-CACHE-001 for every lookup.")
        self.rebind_revision()
        receipt = self.plan()
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertEqual(row["disposition"], "reviewed")
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/story.md: cites SOL-CACHE-001",
                      row["impacted_by"])
        self.assertEqual(receipt["root_scope"]["cited_stories"], ["AUTH-01"])

    def test_a_reused_review_that_quotes_a_changed_document_is_impacted(self):
        self.cite_in_predecessor(
            self.docs / "backlog/epics" / SECOND / "reviews/round-1-epic-review.md",
            f"The cache rule [[{DECISION}|Fixture cache]] bounds the billing lookups.", review=True)
        self.rebind_revision()
        row = next(row for row in self.plan()["epics"] if row["id"] == "EP-002")
        self.assertEqual(row["disposition"], "reviewed")
        self.assertIn(f"backlog/epics/{SECOND}/reviews/round-1-epic-review.md: cites {DECISION}",
                      row["impacted_by"])

    def test_a_changed_inbound_relation_from_an_upstream_note_is_impacted(self):
        target = f"backlog/epics/{SECOND}/stories/bl-001/story"
        self.rebind_revision(rule="Entries expire after ten minutes.",
                             extra=f"related_to:\n  - \"[[{target}|BL-001]]\"\n")
        row = next(row for row in self.plan()["epics"] if row["id"] == "EP-002")
        self.assertIn(f"{target}.md: inbound {DECISION}", row["impacted_by"])

    def test_a_root_reader_finding_moves_an_epic_to_review(self):
        self.rebind_revision()
        receipt = self.record("EP-001")
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertEqual((row["disposition"], row["impacted_by"]),
                         ("reviewed", [f"backlog/epics/{FIRST}/epic.md: root reader finding"]))
        self.assertEqual(rebind.reader_scope_epics(compiler.parse_front_matter(
            self.docs / "backlog/reviews/round-3-backlog-review.md")[1]), ["EP-001"])

    def test_refusals(self):
        self.rebind_revision()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        for key, value in (("owner_approval", "sha256:" + "0" * 64),
                           ("before_package_hash", "sha256:" + "1" * 64), ("kind", "other"),
                           ("predecessor_commit", "HEAD"), ("files", {}), ("unexpected", "field")):
            with self.subTest(forged=key):
                forged = {**receipt, key: value}
                if key not in {"owner_approval", "predecessor_commit", "files", "kind"}:
                    forged["owner_approval"] = rebind.approval_hash(forged)
                with self.assertRaises(ValueError):
                    rebind.shape_findings(forged)
                    rebind.compare(forged, receipt)
        with self.subTest(case="stale approval hash"):
            code, output = quiet(rebind.command, SimpleNamespace(
                docs=str(self.docs), command="apply-source-rebind", source_commit=self.predecessor,
                review_epic=[], approve_receipt="sha256:" + "2" * 64))
            self.assertEqual(code, 1)
            self.assertIn("exact planned receipt hash", output)
        with self.subTest(case="uncommitted source"):
            self.write_decision("Entries expire after one minute.")
            landscape = self.docs / "solution-design/landscape.md"
            root = self.docs / "backlog/backlog.md"
            originals = {path: path.read_bytes() for path in (landscape, root)}
            self.pin_solution_uncommitted()
            with self.assertRaisesRegex(ValueError, "no committed approval"):
                self.plan()
            for path, data in originals.items():
                path.write_bytes(data)
            self.write_decision("Entries expire after five minutes.")
        with self.subTest(case="graph gap"):
            gap = {"path": f"backlog/epics/{FIRST}/epic.md", "reason": "unresolved_relation",
                   "key": "related_to", "value": f"[[{DECISION}#Missing|Fixture cache]]"}
            with mock.patch("task_inputs.closure_raw", return_value={
                    "changed": [], "closure": [], "widened_by": [], "proven_unchanged": [],
                    "graph_gaps": [gap, {**gap, "value": "unrelated|binding"}]}):
                with self.assertRaisesRegex(ValueError, f"changed sources: backlog/epics/{FIRST}/epic.md;"):
                    self.plan()
        with self.subTest(case="switch at default"):
            exists = process_policy.path_for(self.docs).exists()
            self.assertTrue(exists)
            for argv in (["begin-revision"], ["set", "--switch", rebind.SWITCH, "--default"], ["approve"]):
                self.assertEqual(quiet(process_policy.main, [argv[0], "--docs", str(self.docs), *argv[1:]])[0], 0)
            code, output = self.apply(receipt)
            self.assertEqual(code, 1)
            self.assertIn("source_rebind must select receipt_when_unchanged", output)
            code, output = quiet(compiler.check, SimpleNamespace(
                docs=str(self.docs), approved=False, pre_approval=True, render=False, json=True,
                pin_reviews=False))
            self.assertIn("source_rebind must select receipt_when_unchanged", output)

    def test_a_root_change_beyond_the_bindings_takes_the_standard_path(self):
        self.rebind_revision()
        root = self.docs / "backlog/backlog.md"
        props, body = compiler.parse_front_matter(root)
        root.write_text(compiler.front_matter(props, body.replace("\n", "\nScope changed.\n", 1)),
                        encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "root changed beyond its bindings"):
            self.plan()

    def test_two_rebinds_chain_and_a_crlf_receipt_still_replays(self):
        self.rebind_revision()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        self.assertEqual(self.apply(receipt)[0], 0)
        self.commit("First source rebind")
        middle = self.package_hash()
        self.predecessor = self.head()
        self.revise_solution("Entries expire after two minutes.")
        self.revise()
        receipt = self.record()
        self.complete_root(4, keep_compiled=True)
        code, output = self.apply(receipt)
        self.assertEqual(code, 0, output)
        self.commit("Second source rebind")
        chain = backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash())
        self.assertIsNotNone(chain)
        self.assertNotEqual(middle, self.package_hash())
        files = sorted((self.docs / rebind.RECEIPTS).glob("*.json"))
        self.assertEqual(len(files), 2)
        for path in files:
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertIsNotNone(backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash()))
        duplicate = dict(json.loads(files[0].read_text(encoding="utf-8")))
        duplicate["reader_epics"] = ["EP-001"]
        duplicate["owner_approval"] = rebind.approval_hash(duplicate)
        (files[0].parent / (duplicate["owner_approval"][7:] + ".json")).write_bytes(
            backlog_migration.encoded(duplicate))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash())

    def test_a_sealed_receipt_replays_only_from_its_exact_git_evidence(self):
        self.rebind_revision()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        self.assertEqual(self.apply(receipt)[0], 0)
        sealed = rebind.receipts(self.docs)[0]
        with self.assertRaisesRegex(ValueError, "not committed"):
            rebind.replay(self.docs, sealed, final=True)
        self.commit("Approved source rebind")
        self.assertEqual(rebind.replay(self.docs, sealed, final=True), sealed)
        stale = {**sealed, "after_package_hash": "sha256:" + "3" * 64}
        with self.assertRaisesRegex(ValueError, "not the approved backlog postimage"):
            rebind.replay(self.docs, stale, final=True)
        before = {**sealed, "before_package_hash": "sha256:" + "4" * 64}
        before["owner_approval"] = rebind.approval_hash(before)
        with self.assertRaisesRegex(ValueError, "differs from its predecessor approval"):
            rebind.replay(self.docs, before, final=True)
        understated = json.loads(json.dumps(sealed))
        understated["root_scope"]["source_diff_paths"] = []
        understated["owner_approval"] = rebind.approval_hash(understated)
        with self.assertRaises(ValueError):
            rebind.replay(self.docs, understated, final=True)
        path = next((self.docs / rebind.RECEIPTS).glob("*.json"))
        path.write_bytes(backlog_migration.encoded(stale))
        self.assertIsNone(backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash()))

    def test_delivery_keeps_its_execution_approval_across_a_rebind(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.fixture.approve_dod()
        args = SimpleNamespace(docs=str(self.docs), id=None, slug="cache", goal="Cache lookups",
                               outcome="Faster lookups", target_branch="main", story=["AUTH-01"])
        code, output = quiet(delivery_compile.init_delivery, args)
        self.assertEqual(code, 0, output)
        approval = SimpleNamespace(docs=str(self.docs), delivery="DLV-001")
        code, output = quiet(delivery_compile.approve_scope, approval)
        self.assertEqual(code, 0, output)
        root = delivery_compile.find_delivery(self.docs, "DLV-001")
        item = next(root.glob("items/*/item.md"))
        props, body = delivery_compile.split_note(item)
        props.update(path_claims=["src/auth.py"], contract_claims=["auth:session"])
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        code, output = quiet(delivery_compile.approve_execution, approval)
        self.assertEqual(code, 0, output)
        self.commit("Approved execution")
        preserved = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.rebind_revision()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        self.assertEqual(self.apply(receipt)[0], 0)
        self.commit("Approved source rebind")
        check = SimpleNamespace(docs=str(self.docs), delivery="DLV-001")
        code, output = quiet(delivery_compile.check_delivery, check)
        self.assertEqual(code, 0, output)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        path = next((self.docs / rebind.RECEIPTS).glob("*.json"))
        sealed = json.loads(path.read_text(encoding="utf-8"))
        sealed["epics"] = [{**row, "disposition": "reviewed",
                            "impacted_by": [f"backlog/epics/{FIRST}/stories/auth-01/story.md: closure"]}
                           if row["id"] == "EP-001" else row for row in sealed["epics"]]
        sealed["epics"][0].pop("reused_review", None)
        with mock.patch.object(rebind, "receipts", return_value=[sealed]), \
                mock.patch.object(rebind, "replay"):
            errors = delivery_compile.delivery_source_findings(
                self.docs, root, delivery_compile.split_note(root / "delivery.md")[0])[1]
        self.assertTrue(any("impacted by a source rebind" in error for error in errors), errors)
        path.unlink()
        errors = delivery_compile.delivery_source_findings(
            self.docs, root, delivery_compile.split_note(root / "delivery.md")[0])[1]
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog", errors)

    # Helpers that rewrite the approved predecessor.

    def cite_in_predecessor(self, path: Path, line: str, *, review: bool = False) -> None:
        """Add a citation to an approved note, then re-approve revision 2 as one approval."""
        props, body = compiler.parse_front_matter(path)
        main, marker, nav = body.partition(compiler.NAV_MARKER)
        path.write_text(compiler.front_matter(props, main.rstrip() + "\n\n" + line + "\n\n" + marker + nav),
                        encoding="utf-8")
        self.restamp()
        self.commit("Approved backlog revision 2 with a citation")
        self.predecessor = self.head()
        self.before_hash = self.package_hash()

    def restamp(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        paths = compiler.package_paths(record, self.docs)
        for path in paths:
            props, body = compiler.parse_front_matter(path)
            if props.get("source_hash"):
                props["source_hash"] = compiler.digest(path)
                path.write_text(compiler.front_matter(props, body), encoding="utf-8")
        root = self.docs / "backlog/backlog.md"
        props, body = compiler.parse_front_matter(root)
        props["source_hash"] = compiler.digest(root)
        props["package_hash"] = compiler.package_digest(self.docs, paths)
        root.write_text(compiler.front_matter(props, body), encoding="utf-8")

    def pin_solution_uncommitted(self) -> None:
        """Bind the working tree's Solution package, which no commit approved."""
        landscape = self.docs / "solution-design/landscape.md"
        props, body = compiler.parse_front_matter(landscape)
        props["package_hash"] = stage_package.tree_hash(
            self.docs / "solution-design", {"package_hash", "package_status", "package_approved_at_utc"})
        landscape.write_text(compiler.front_matter(props, body), encoding="utf-8")
        root = self.docs / "backlog/backlog.md"
        root_props, root_body = compiler.parse_front_matter(root)
        root_props["input_bindings"] = [
            f"solution-design|{SOLUTION}|{props['package_hash']}" if value.startswith("solution-design|")
            else value for value in root_props["input_bindings"]]
        root.write_text(compiler.front_matter(root_props, root_body), encoding="utf-8")


class SourceRebindUnitTests(unittest.TestCase):
    def test_content_form_ignores_lifecycle_line_endings_and_navigation(self):
        text = ("---\ntype: story\nstatus: planned\napproved_at_utc: x\nsource_hash: y\ntags:\n"
                "  - status/planned\n  - doc/story\n---\n\n# Story\n\nBody.\n\n"
                + compiler.NAV_MARKER + "\n- [[maps/backlog|Backlog map]]\n")
        moved = text.replace("status: planned", "status: approved").replace("source_hash: y", "source_hash: z")
        self.assertEqual(rebind.content_hash(text), rebind.content_hash(moved.replace("\n", "\r\n")))
        self.assertNotEqual(rebind.content_hash(text), rebind.content_hash(text.replace("Body.", "Edited.")))

    def test_citations_respect_identifier_and_link_boundaries(self):
        links, ids = ["solution-design/decisions/cache"], ["SOL-CACHE-001"]
        self.assertEqual(rebind.cites("[[solution-design/decisions/cache|Cache]] SOL-CACHE-001.", links, ids),
                         ["SOL-CACHE-001", "solution-design/decisions/cache"])
        self.assertEqual(rebind.cites("[[solution-design/decisions/cache-two|C]] SOL-CACHE-0012", links, ids), [])
        self.assertEqual(rebind.cites("| [[solution-design/decisions/cache\\|Cache]] |", links, ids),
                         ["solution-design/decisions/cache"])

    def test_receipt_paths_refuse_windows_separators_and_escapes(self):
        receipt = {"schema_version": 1, "kind": rebind.KIND, "predecessor_commit": "a" * 40,
                   "before_package_hash": "sha256:" + "0" * 64, "package_version": "2026.10.1",
                   "reader_epics": [], "epics": [],
                   "files": [{"path": "backlog\\epics\\a\\epic.md"}]}
        receipt["owner_approval"] = rebind.approval_hash(receipt)
        with self.assertRaisesRegex(ValueError, "outside the canonical backlog"):
            rebind.shape_findings(receipt)
        receipt["files"] = [{"path": "backlog/epics/a/epic.md"}]
        receipt["owner_approval"] = rebind.approval_hash(receipt)
        rebind.shape_findings(receipt)
        with self.assertRaisesRegex(ValueError, "outside the vault"):
            backlog_migration.safe_path(Path("docs"), "../backlog/backlog.md")

    def test_the_owner_approval_hash_excludes_the_sealed_postimage(self):
        receipt = {"schema_version": 1, "kind": rebind.KIND}
        self.assertEqual(rebind.approval_hash(receipt),
                         rebind.approval_hash({**receipt, "after_package_hash": "sha256:" + "1" * 64,
                                               "owner_approval": "anything"}))

    def test_without_a_source_rebind_section_approval_reads_nothing(self):
        record = {"backlog_reviews": [{"body": "## Verdict\n", "props": {}, "round": 1, "path": "x"}]}
        with mock.patch.dict(sys.modules, {"backlog_rebind": None}):
            self.assertEqual(compiler.source_rebind_findings(record, Path("docs")), [])


if __name__ == "__main__":
    unittest.main()
