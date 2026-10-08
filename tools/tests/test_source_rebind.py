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
EVICTION = "solution-design/decisions/fixture-eviction"
# A landscape as the solution flow keeps it (landscape-docs.md): the Engagements
# index, Target deltas, Transition steps and Components rows cite decisions.
LANDSCAPE_BODY = """# Solution Landscape

## Summary

Approved solution boundary.

| slug | status | decisions |
|---|---|---|
{engagements}

## Current

Nothing built yet.

## Target

{target}

## Transition

{transition}

## Components

| component | verdict | decision | engagement | status |
|---|---|---|---|---|
{components}
"""
CACHE_ROWS = {
    "engagements": f"| fixture-cache | Status: approved 2026-01-05 | [[{DECISION}\\|SD-001]] |",
    "target": f"- Session lookups read through a cache ([[{DECISION}\\|SD-001]]).",
    "transition": f"1. Introduce the session cache ([[{DECISION}\\|SD-001]]); precondition: none.",
    "components": (f"| session cache | buy | [[{DECISION}\\|SD-001]] |"
                   " [[solution-design/engagements/fixture-cache\\|fixture-cache]] | decided |"),
}
EVICTION_ROWS = {
    "engagements": f"| fixture-eviction | Status: approved 2026-02-01 | [[{EVICTION}\\|SD-002]] |",
    "target": f"- Idle sessions are evicted first ([[{EVICTION}\\|SD-002]]).",
    "transition": f"2. Add idle eviction ([[{EVICTION}\\|SD-002]]); precondition: the session cache.",
    "components": (f"| cache eviction | build | [[{EVICTION}\\|SD-002]] |"
                   " [[solution-design/engagements/fixture-eviction\\|fixture-eviction]] | decided |"),
}

AUDIT = "solution-design/decisions/fixture-audit"
AUDIT_ROWS = {
    "engagements": f"| fixture-audit | Status: approved 2026-03-01 | [[{AUDIT}\\|SD-003]] |",
    "target": f"- Cache reads are audited ([[{AUDIT}\\|SD-003]]).",
    "transition": f"3. Add the read audit ([[{AUDIT}\\|SD-003]]); precondition: the session cache.",
    "components": (f"| cache audit | build | [[{AUDIT}\\|SD-003]] |"
                   " [[solution-design/engagements/fixture-audit\\|fixture-audit]] | decided |"),
}


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
        self.added_rows: list[dict] = []
        self.write_landscape(CACHE_ROWS)
        self.write_engagement("fixture-cache")
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

    def write_landscape(self, *row_sets: dict) -> None:
        landscape = self.docs / "solution-design/landscape.md"
        props, _body = compiler.parse_front_matter(landscape)
        landscape.write_text(compiler.front_matter(props, LANDSCAPE_BODY.format(**{
            key: "\n".join(rows[key] for rows in row_sets) for key in CACHE_ROWS})), encoding="utf-8")

    def write_engagement(self, slug: str) -> None:
        path = self.docs / f"solution-design/engagements/{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: engagement\ntitle: {slug}\n---\n\n# {slug}\n\n## Summary\n\n"
                        "Status: approved 2026-01-05\n", encoding="utf-8")

    def write_ledger(self, *rows: tuple[str, list[tuple[str, str]]]) -> None:
        """Write the Experience application ledger: one (application hash, packages) row per revision."""
        ledger, previous, revisions = self.docs / "experience-design/_ledger/application-revisions.json", \
            "sha256:" + "0" * 64, []
        for number, (application, packages) in enumerate(rows, start=1):
            revisions.append({"application_revision": number, "previous_application_hash": previous,
                              "application_hash": application,
                              "packages": [{"result_ref": ref, "package_hash": value} for ref, value in packages]})
            previous = application
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"schema_version": 3, "revisions": revisions}), encoding="utf-8")

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

    def revise(self, refs: list[str] | None = None) -> None:
        code, output = quiet(compiler.begin_revision, SimpleNamespace(
            docs=str(self.docs), delivery_snapshot="", planning_mode="requirement",
            requirement_ref="REQ-001", absent_input=[], input_ref=list(refs or REFS)))
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

    def switch_on(self, **others: str) -> None:
        exists = process_policy.path_for(self.docs).exists()
        for argv in (["begin-revision" if exists else "init"],
                     ["set", "--switch", rebind.SWITCH, "--value", rebind.VALUE],
                     *(["set", "--switch", key, "--value", value] for key, value in others.items()),
                     ["approve"]):
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

    def apply(self, receipt: dict, *epics: str, source_gate: bool = False) -> tuple[int, str]:
        return quiet(rebind.command, SimpleNamespace(
            docs=str(self.docs), command="apply-source-rebind", source_commit=self.predecessor,
            review_epic=list(epics), approve_receipt=receipt["owner_approval"], source_gate=source_gate))

    def rebind_revision(self, rule: str = "Entries expire after five minutes.", extra: str = "",
                        **switches: str) -> None:
        self.switch_on(**switches)
        self.revise_solution(rule, extra)
        self.revise()

    def fold_in(self, decision: str = EVICTION, alias: str = "SD-002", rows: dict = EVICTION_ROWS,
                **switches: str) -> None:
        """A Solution revision that only adds a decision, its engagement and its landscape rows."""
        if switches or not self.added_rows:
            self.switch_on(**switches)
        slug = decision.rsplit("/", 1)[1]
        name = slug.removeprefix("fixture-")
        (self.docs / f"{decision}.md").write_text(
            DECISION_TEXT.format(rule=f"The {name} rule holds.", extra=f"aliases:\n  - {alias}\n")
            .replace("Fixture cache", f"Fixture {name}").replace("SOL-CACHE-00", f"SOL-{name.upper()}-00"),
            encoding="utf-8")
        self.write_engagement(slug)
        self.added_rows.append(rows)
        self.write_landscape(CACHE_ROWS, *self.added_rows)
        self.commit(f"Solution revision: the {name} decision folded into the landscape")
        self.pin_solution()
        self.revise()

    def epic_rounds(self, slug: str) -> list[str]:
        return sorted(path.name for path in (self.docs / "backlog/epics" / slug / "reviews").glob("*.md"))

    # Tests.

    def test_a_solution_revision_no_story_cites_reuses_every_epic_review(self):
        self.fold_in()
        receipt = self.record()
        self.assertEqual([(row["id"], row["disposition"]) for row in receipt["epics"]],
                         [("EP-001", "reused"), ("EP-002", "reused")])
        change, = receipt["binding_changes"]
        self.assertEqual((change["stage"], change["changed_documents"]),
                         ("solution-design", [f"{EVICTION}.md", "solution-design/engagements/fixture-eviction.md",
                                              "solution-design/landscape.md"]))
        self.assertIn("SOL-EVICTION-001", change["changed_ids"])
        self.assertEqual(receipt["root_scope"]["cited_stories"], [])
        manifest = backlog_review_inputs.manifest(self.docs)
        self.assertEqual(manifest["source_rebind"], rebind.VALUE)
        self.assertIn(f"{EVICTION}.md", manifest["paths"])
        self.assertIn("SOL-EVICTION-001", manifest["check"]["source_rebind"]["source_diff"][0]["diff"])
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
        self.fold_in()
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
        self.assertIn("AUTH-01", receipt["root_scope"]["cited_stories"])

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
        self.fold_in()
        receipt = self.record("EP-001")
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertEqual((row["disposition"], row["impacted_by"]), ("reviewed", [
            f"backlog/epics/{FIRST}/epic.md: root reader finding",
            f"backlog/epics/{FIRST}/stories/auth-01/story.md: epic impacted",
            f"backlog/epics/{FIRST}/stories/auth-01/test-plan.md: epic impacted"]))
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
        self.fold_in()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        self.assertEqual(self.apply(receipt)[0], 0)
        self.commit("First source rebind")
        middle = self.package_hash()
        self.predecessor = self.head()
        self.fold_in(AUDIT, "SD-003", AUDIT_ROWS)
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
        skipped = []
        self.assertIsNotNone(backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash(),
                                                         skipped))
        self.assertEqual([note.split(":", 1)[0] for note in skipped],
                         [f"{rebind.RECEIPTS}/{duplicate['owner_approval'][7:]}.json"])
        self.commit("A second receipt for the same step")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash())

    def test_a_sealed_receipt_replays_only_from_its_exact_git_evidence(self):
        self.fold_in()
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

    def approved_delivery(self) -> Path:
        """An execution-approved Delivery of AUTH-01 on the approved predecessor."""
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
        return root

    def delivery_check(self) -> tuple[int, str]:
        return quiet(delivery_compile.check_delivery, SimpleNamespace(docs=str(self.docs), delivery="DLV-001"))

    def test_delivery_keeps_its_execution_approval_across_a_rebind(self):
        root = self.approved_delivery()
        preserved = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.fold_in()
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
        with mock.patch.object(rebind, "committed_receipts", return_value=([sealed], [])), \
                mock.patch.object(rebind, "replay"):
            errors = delivery_compile.delivery_source_findings(
                self.docs, root, delivery_compile.split_note(root / "delivery.md")[0])[1]
        self.assertTrue(any("impacted by a source rebind" in error for error in errors), errors)
        path.unlink()
        errors = delivery_compile.delivery_source_findings(
            self.docs, root, delivery_compile.split_note(root / "delivery.md")[0])[1]
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog", errors)


    def assert_epic_impact_reaches_delivery(self, *epics: str, edit: bool = False) -> None:
        """EP-001 is impacted as a whole: its story is read in full and its Delivery Item refused."""
        self.approved_delivery()
        if edit:
            self.rebind_revision()
        else:
            self.fold_in()
        receipt = self.record(*epics)
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/story.md: epic impacted",
                      next(row for row in receipt["epics"] if row["id"] == "EP-001")["impacted_by"])
        self.assertIn("AUTH-01", receipt["root_scope"]["cited_stories"])
        self.review_epic(FIRST, 3)
        if edit:
            self.review_epic(SECOND, 2)
        manifest = backlog_review_inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(manifest["check"]["backlog_graph"]["stories"]["AUTH-01"]["read"], "full")
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt, *epics)
        self.assertEqual(code, 0, output)
        self.commit("Approved source rebind")
        code, output = self.delivery_check()
        self.assertEqual(code, 1, output)
        self.assertIn("Story AUTH-01 is impacted by a source rebind", output)

    def test_a_root_reader_finding_refuses_its_delivery_items_and_reads_their_stories(self):
        self.assert_epic_impact_reaches_delivery("EP-001")

    def test_a_reused_review_citation_refuses_its_delivery_items_and_reads_their_stories(self):
        self.cite_in_predecessor(self.docs / "backlog/epics" / FIRST / "reviews/round-2-epic-review.md",
                                 f"AUTH-01 relies on [[{DECISION}|Fixture cache]].", review=True)
        self.assert_epic_impact_reaches_delivery(edit=True)

    def test_a_decision_status_change_impacts_its_citing_stories(self):
        story = self.docs / "backlog/epics" / FIRST / "stories/auth-01/story.md"
        self.cite_in_predecessor(story, f"Sessions follow [[{DECISION}|Fixture cache]].")
        self.switch_on()
        path = self.docs / f"{DECISION}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("status: accepted", "status: rejected")
                        .replace("status/accepted", "status/rejected"), encoding="utf-8")
        self.commit("Rejected the decision")
        self.pin_solution()
        self.revise()
        receipt = self.plan()
        self.assertEqual(receipt["binding_changes"][0]["changed_documents"], [f"{DECISION}.md"])
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/story.md: cites {DECISION}", row["impacted_by"])

    def test_a_revision_that_changes_no_authored_document_is_refused(self):
        application = "experience-design|application@"
        self.write_ledger(("sha256:" + "1" * 64, []), (bindings.digest("9"), []))
        artifact = self.docs / "experience-design/artifacts/index.html"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("<main>app</main>\n", encoding="utf-8")
        self.commit("Experience application revision 2")
        self.switch_on()
        self.write_ledger(("sha256:" + "1" * 64, []), (bindings.digest("9"), []), (bindings.digest("3"), []))
        artifact.write_text("<main>edited app</main>\n", encoding="utf-8")
        self.commit("Experience application revision 3: a prototype edit only")
        self.receipts["application@r3"] = bindings.digest("3")
        self.revise([ref if ref != bindings.APPLICATION else "application@r3" for ref in REFS])
        self.assertTrue(any(value.startswith(application + "r3") for value in compiler.values(
            compiler.parse_front_matter(self.docs / "backlog/backlog.md")[0], "input_bindings")))
        with self.assertRaisesRegex(ValueError, "no authored document changed .changed files:"
                                                " experience-design/artifacts/index.html"):
            self.plan()

    def test_a_landscape_update_for_a_new_decision_reuses_every_unrelated_epic(self):
        self.fold_in()
        receipt = self.plan()
        self.assertEqual([(row["id"], row["disposition"]) for row in receipt["epics"]],
                         [("EP-001", "reused"), ("EP-002", "reused")])
        change, = receipt["binding_changes"]
        self.assertEqual(change["changed_documents"], [
            f"{EVICTION}.md", "solution-design/engagements/fixture-eviction.md", "solution-design/landscape.md"])
        self.assertEqual(change["changed_rows"], [
            "Components / cache eviction", "Summary / fixture-eviction",
            "Target / Idle sessions are evicted first ([[solution-design/decisions/fixture-eviction\\|SD-002]]).",
            "Transition / Add idle eviction ([[solution-design/decisions/fixture-eviction\\|SD-002]]);"
            " precondition: the session cache."])
        self.assertTrue(rebind.mechanical(receipt))

    def test_a_modified_landscape_row_reviews_every_story_the_landscape_constrains(self):
        story = self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md"
        self.cite_in_predecessor(story, f"Billing lookups use [[{DECISION}|the session cache]].")
        self.switch_on()
        self.write_landscape(dict(CACHE_ROWS, components=CACHE_ROWS["components"].replace(
            "| decided |", "| adopted |")))
        self.commit("Solution revision: the session cache is adopted")
        self.pin_solution()
        self.revise()
        receipt = self.plan()
        change, = receipt["binding_changes"]
        self.assertEqual((change["changed_documents"], change["changed_rows"]),
                         (["solution-design/landscape.md"], ["Components / session cache"]))
        epics = {row["id"]: row for row in receipt["epics"]}
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/story.md: cites solution-design/landscape",
                      epics["EP-001"]["impacted_by"])
        self.assertIn(f"backlog/epics/{SECOND}/stories/bl-001/story.md: cites {DECISION}",
                      epics["EP-002"]["impacted_by"])
        self.assertFalse(change["additions_only"])
        self.assertFalse(rebind.mechanical(receipt))
        with self.subTest(case="a landscape change outside its rows"):
            props, body = compiler.parse_front_matter(self.docs / "solution-design/landscape.md")
            self.assertIsNone(rebind.landscape_delta(
                compiler.front_matter(props, body),
                compiler.front_matter(props, body.replace("Nothing built yet.", "A cache runs."))))

    def test_the_source_gate_approves_only_a_mechanical_receipt(self):
        self.fold_in(dependent_rebind_gate="with_source")
        receipt = self.record()
        self.assertEqual({row["disposition"] for row in receipt["epics"]}, {"reused"})
        code, output = quiet(rebind.command, SimpleNamespace(
            docs=str(self.docs), command="plan-source-rebind", source_commit=self.predecessor, review_epic=[]))
        self.assertEqual((code, json.loads(output)["mechanical"]), (0, True))
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt, source_gate=True)
        self.assertEqual(code, 0, output)
        self.assertEqual(rebind.receipts(self.docs)[0]["owner_approval"], receipt["owner_approval"])

    def test_a_receipt_that_is_not_mechanical_takes_the_ordinary_approval(self):
        self.fold_in(dependent_rebind_gate="with_source")
        receipt = self.record("EP-001")
        self.assertEqual(receipt["reader_epics"], ["EP-001"])
        self.review_epic(FIRST, 3)
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt, "EP-001", source_gate=True)
        self.assertEqual(code, 1, output)
        self.assertIn("the receipt is not mechanical", output)
        code, output = self.apply(receipt, "EP-001")
        self.assertEqual(code, 0, output)

    def test_the_source_gate_needs_the_with_source_value(self):
        self.fold_in()
        receipt = self.record()
        self.assertEqual({row["disposition"] for row in receipt["epics"]}, {"reused"})
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt, source_gate=True)
        self.assertEqual(code, 1, output)
        self.assertIn("--source-gate needs dependent_rebind_gate with_source", output)

    def test_plain_approve_refuses_a_source_rebind_round_even_with_its_receipt_file(self):
        self.fold_in()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        path = rebind.receipt_path(self.docs, receipt["owner_approval"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(backlog_migration.encoded(receipt))
        code, output = quiet(compiler.approve, SimpleNamespace(docs=str(self.docs)))
        self.assertEqual(code, 1, output)
        self.assertIn("approved only by apply-source-rebind", output)
        self.assertEqual(compiler.parse_front_matter(self.docs / "backlog/backlog.md")[0]["status"], "draft")

    def test_the_pin_chain_skips_malformed_and_uncommitted_receipt_files(self):
        self.fold_in()
        receipt = self.record()
        self.complete_root(3, keep_compiled=True)
        self.assertEqual(self.apply(receipt)[0], 0)
        self.commit("Approved source rebind")
        migrations = self.docs / backlog_migration.RECEIPTS
        migrations.mkdir(parents=True, exist_ok=True)
        (migrations / "broken.json").write_text("{", encoding="utf-8")
        stray = self.docs / rebind.RECEIPTS / ("f" * 64 + ".json")
        stray.write_text("{}", encoding="utf-8")
        chain = backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash())
        self.assertEqual((chain.aliases, chain.impacted), ({}, frozenset()))
        skipped = []
        chain = backlog_migration.pin_chain(self.docs, self.before_hash, self.package_hash(), skipped)
        self.assertEqual((chain.aliases, chain.impacted), ({}, frozenset()))
        self.assertEqual([note.split(":", 1)[0] for note in skipped],
                         [f"{backlog_migration.RECEIPTS}/broken.json", f"{rebind.RECEIPTS}/{stray.name}"])

    def test_an_edit_below_the_navigation_marker_is_impacted(self):
        self.rebind_revision()
        story = self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md"
        text = story.read_text(encoding="utf-8")
        self.assertIn(compiler.NAV_MARKER, text)
        story.write_text(text.rstrip() + "\n\nRefunds are now in scope.\n", encoding="utf-8")
        row = next(row for row in self.plan()["epics"] if row["id"] == "EP-002")
        self.assertIn(f"backlog/epics/{SECOND}/stories/bl-001/story.md: changed", row["impacted_by"])

    def test_the_aliases_of_a_changed_decision_are_changed_ids(self):
        self.write_decision("Entries expire after ten minutes.", "aliases:\n  - SD-001\n")
        self.commit("Decision alias")
        self.pin_solution()
        root = self.docs / "backlog/backlog.md"
        props, body = compiler.parse_front_matter(root)
        props["input_bindings"] = [f"solution-design|{SOLUTION}|{self.receipts[SOLUTION]}"
                                   if value.startswith("solution-design|") else value
                                   for value in props["input_bindings"]]
        root.write_text(compiler.front_matter(props, body), encoding="utf-8")
        plan = self.docs / "backlog/epics" / FIRST / "stories/auth-01/test-plan.md"
        self.cite_in_predecessor(plan, "Scenarios assume the cache decision SD-001.")
        self.switch_on()
        path = self.docs / f"{DECISION}.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "# Fixture cache\n", "# Fixture cache\n\nCached entries are shared across tenants.\n"),
            encoding="utf-8")
        self.commit("Revised the decision's prose")
        self.pin_solution()
        self.revise()
        receipt = self.plan()
        self.assertEqual(receipt["binding_changes"][0]["changed_ids"], ["SD-001"])
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/test-plan.md: cites SD-001", row["impacted_by"])

    def test_an_added_source_family_counts_every_document_as_changed(self):
        package = self.docs / "experience-design/experiences/checkout"
        package.mkdir(parents=True, exist_ok=True)
        (package / "experience.md").write_text("---\ntype: experience\nstatus: approved\n---\n\n# Checkout\n",
                                               encoding="utf-8")
        (package / "flow.md").write_text("---\ntype: flow\n---\n\n| id | step |\n|---|---|\n"
                                         "| EXP-CHK-001 | Pay by card. |\n", encoding="utf-8")
        self.write_ledger((bindings.digest("9"), [("checkout@r1", bindings.digest("c"))]))
        self.commit("Checkout Experience approved")
        story = self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md"
        self.cite_in_predecessor(story, "Billing follows EXP-CHK-001.")
        self.switch_on()
        self.receipts["checkout@r1"] = bindings.digest("c")
        self.revise([*REFS, "checkout@r1"])
        receipt = self.plan()
        change, = receipt["binding_changes"]
        self.assertEqual((change["before_hash"], change["before_commit"]), (None, None))
        self.assertNotIn("before_ref", change)
        self.assertTrue({"experience-design/experiences/checkout/experience.md",
                         "experience-design/experiences/checkout/flow.md"} <= set(change["changed_documents"]))
        self.assertIn("EXP-CHK-001", change["changed_ids"])
        row = next(row for row in receipt["epics"] if row["id"] == "EP-002")
        self.assertIn(f"backlog/epics/{SECOND}/stories/bl-001/story.md: cites EXP-CHK-001", row["impacted_by"])

    def test_a_story_that_implements_a_changed_requirement_is_impacted(self):
        story = self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md"
        self.assertEqual(compiler.parse_front_matter(story)[0]["implements"],
                         ["[[requirements/req-001-cache-rules|REQ-001]]"])
        self.rebind_revision()
        with mock.patch.object(rebind, "requirement_hash",
                               side_effect=["sha256:" + "a" * 64, "sha256:" + "b" * 64]):
            receipt = self.plan()
        row = next(row for row in receipt["epics"] if row["id"] == "EP-002")
        self.assertIn(f"backlog/epics/{SECOND}/stories/bl-001/story.md: implements REQ-001", row["impacted_by"])

    def test_an_edited_decision_the_landscape_indexes_reviews_every_story_it_constrains(self):
        self.rebind_revision()
        receipt = self.plan()
        change, = receipt["binding_changes"]
        self.assertEqual(change["changed_documents"], [f"{DECISION}.md"])
        for row in receipt["epics"]:
            self.assertEqual(row["disposition"], "reviewed")
        self.assertIn(f"backlog/epics/{SECOND}/stories/bl-001/story.md: closure",
                      next(row for row in receipt["epics"] if row["id"] == "EP-002")["impacted_by"])
        self.assertFalse(change["additions_only"])
        self.assertFalse(rebind.mechanical(receipt))

    def test_a_superseded_decision_repointed_in_the_landscape_is_never_mechanical(self):
        self.switch_on(dependent_rebind_gate="with_source")
        new = "solution-design/decisions/fixture-cache-memcached"
        (self.docs / f"{new}.md").write_text(
            DECISION_TEXT.format(rule="Entries expire after one minute.", extra="aliases:\n  - SD-002\n")
            .replace("redis", "memcached").replace("Fixture cache", "Fixture cache memcached")
            .replace("SOL-CACHE-00", "SOL-MEMC-00"), encoding="utf-8")
        old = self.docs / f"{DECISION}.md"
        old.write_text(old.read_text(encoding="utf-8").replace("status: accepted", "status: superseded")
                       .replace("status/accepted", "status/superseded")
                       .replace("tags:", f'superseded_by: "[[{new}]]"\ntags:'), encoding="utf-8")
        self.write_landscape({key: value.replace(DECISION + "\\|SD-001", new + "\\|SD-002")
                              for key, value in CACHE_ROWS.items()})
        self.commit("Supersede the session cache decision")
        self.pin_solution()
        self.revise()
        receipt = self.record()
        change, = receipt["binding_changes"]
        self.assertIn("Components / session cache", change["changed_rows"])
        for row in receipt["epics"]:
            self.assertEqual(row["disposition"], "reviewed", row)
        story = f"backlog/epics/{SECOND}/stories/bl-001/story.md"
        self.assertIn(f"{story}: cites solution-design/landscape",
                      next(row for row in receipt["epics"] if row["id"] == "EP-002")["impacted_by"])
        self.assertEqual(receipt["root_scope"]["cited_stories"], ["AUTH-01", "BL-001"])
        self.assertFalse(change["additions_only"])
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt, source_gate=True)
        self.assertEqual(code, 1, output)
        self.assertIn("the receipt is not mechanical", output)

    def test_a_rejected_decision_reaches_a_story_citing_only_the_landscape_and_its_delivery(self):
        self.approved_delivery()
        self.switch_on()
        path = self.docs / f"{DECISION}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("status: accepted", "status: rejected")
                        .replace("status/accepted", "status/rejected"), encoding="utf-8")
        landscape = self.docs / "solution-design/landscape.md"
        landscape.write_text(landscape.read_text(encoding="utf-8").replace(
            "| session cache | buy |", "| session cache | rejected |"), encoding="utf-8")
        self.commit("Reject the session cache decision")
        self.pin_solution()
        self.revise()
        story = self.docs / "backlog/epics" / FIRST / "stories/auth-01/story.md"
        self.assertNotIn(DECISION, story.read_text(encoding="utf-8"))
        receipt = self.record()
        row = next(row for row in receipt["epics"] if row["id"] == "EP-001")
        self.assertEqual(row["disposition"], "reviewed")
        self.assertIn(f"backlog/epics/{FIRST}/stories/auth-01/story.md: cites solution-design/landscape",
                      row["impacted_by"])
        self.review_epic(FIRST, 3)
        self.review_epic(SECOND, 2)
        self.complete_root(3, keep_compiled=True)
        code, output = self.apply(receipt)
        self.assertEqual(code, 0, output)
        self.commit("Approved source rebind")
        code, output = self.delivery_check()
        self.assertEqual(code, 1, output)
        self.assertIn("Story AUTH-01 is impacted by a source rebind", output)

    def test_a_design_system_page_override_reuses_every_epic(self):
        first, second = "sha256:" + "d" * 64, "sha256:" + "e" * 64
        master = self.docs / "design-system/MASTER.md"
        props, body = compiler.parse_front_matter(master)
        props.update(status="approved", revision=1, baseline_hash=first)
        master.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.commit("Design System revision 1 approved")
        self.receipts[bindings.DESIGN] = first
        root = self.docs / "backlog/backlog.md"
        root_props, root_body = compiler.parse_front_matter(root)
        root_props["input_bindings"] = [f"design-system|{bindings.DESIGN}|{first}"
                                        if value.startswith("design-system|") else value
                                        for value in root_props["input_bindings"]]
        root.write_text(compiler.front_matter(root_props, root_body), encoding="utf-8")
        self.cite_in_predecessor(self.docs / "backlog/epics" / SECOND / "stories/bl-001/story.md",
                                 "Billing copy is unchanged.")
        self.switch_on()
        page = self.docs / "design-system/pages/admin-reports.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("---\ntype: page-override\ntitle: Admin reports\n---\n\n# Admin reports\n\n"
                        "Dense tables.\n", encoding="utf-8")
        props, body = compiler.parse_front_matter(master)
        props.update(revision=2, supersedes_hash=first, baseline_hash=second)
        master.write_text(compiler.front_matter(props, body), encoding="utf-8")
        self.commit("Design System revision 2: an admin page override only")
        self.receipts[bindings.DESIGN] = second
        self.revise()
        receipt = self.plan()
        change, = (change for change in receipt["binding_changes"] if change["stage"] == "design-system")
        self.assertEqual(change["changed_documents"], ["design-system/pages/admin-reports.md"])
        self.assertEqual({row["disposition"] for row in receipt["epics"]}, {"reused"})

    def test_an_experience_revision_of_anchor_stamps_only_changes_no_document(self):
        folder = self.docs / "experience-design/experiences/checkout"
        folder.mkdir(parents=True, exist_ok=True)
        anchor = folder / "experience.md"
        anchor.write_text("---\ntype: experience\nstatus: approved\nrevision: 1\napproval_revision: 1\n"
                          "registry_hash: sha256:" + "1" * 64 + "\ntags:\n  - status/approved\n"
                          "  - doc/experience\n---\n\n# Checkout\n\nPay by card.\n", encoding="utf-8")
        self.commit("Experience revision 1")
        before = self.head()
        anchor.write_text(anchor.read_text(encoding="utf-8").replace("revision: 1\n", "revision: 2\n")
                          .replace("1" * 64, "3" * 64).replace("  - doc/experience\n", ""), encoding="utf-8")
        self.commit("Experience revision 2: its approval stamps only")
        prefix = rebind.docs_prefix(self.project, self.docs)
        changed, _texts, _others = rebind.package_diff(
            self.project, prefix, f"{prefix}/experience-design/experiences/checkout", before, self.head())
        self.assertEqual(changed, [])
        anchor.write_text(anchor.read_text(encoding="utf-8").replace("Pay by card.", "Pay by invoice."),
                          encoding="utf-8")
        self.commit("Experience revision 3: an authored change")
        changed, _texts, _others = rebind.package_diff(
            self.project, prefix, f"{prefix}/experience-design/experiences/checkout", before, self.head())
        self.assertEqual(changed, ["experience-design/experiences/checkout/experience.md"])

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
    def test_content_form_ignores_lifecycle_and_line_endings_but_not_navigation(self):
        text = ("---\ntype: story\nstatus: planned\napproved_at_utc: x\nsource_hash: y\ntags:\n"
                "  - status/planned\n  - doc/story\n---\n\n# Story\n\nBody.\n\n"
                + compiler.NAV_MARKER + "\n- [[maps/backlog|Backlog map]]\n")
        moved = text.replace("status: planned", "status: approved").replace("source_hash: y", "source_hash: z")
        self.assertEqual(rebind.content_hash(text), rebind.content_hash(moved.replace("\n", "\r\n")))
        self.assertNotEqual(rebind.content_hash(text), rebind.content_hash(text.replace("Body.", "Edited.")))
        # Every byte the approval stamp covers counts, the navigation zone included.
        self.assertNotEqual(rebind.content_hash(text), rebind.content_hash(text + "Refunds are in scope.\n"))

    def test_a_relation_edge_is_its_key_and_target(self):
        before = "---\ntype: decision\nrelated_to:\n  - \"[[backlog/epics/a/epic|A]]\"\n---\n\n# D\n"
        after = before.replace("related_to:", "governs:")
        self.assertEqual(rebind.relation_targets(before), {("related_to", "backlog/epics/a/epic")})
        self.assertEqual(rebind.relation_targets(before) ^ rebind.relation_targets(after),
                         {("related_to", "backlog/epics/a/epic"), ("governs", "backlog/epics/a/epic")})

    @integration
    def test_the_approval_of_a_source_hash_is_its_oldest_committed_anchor(self):
        from tools.tests.git_fixture import init_repository, temporary_directory
        hashes = {name: "sha256:" + name * 64 for name in "abcde"}
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project, initial_branch="main")
            docs = project / "workspace/docs"
            ledger = docs / "experience-design/_ledger/application-revisions.json"
            landscape = docs / "solution-design/landscape.md"
            ledger.parent.mkdir(parents=True)
            landscape.parent.mkdir(parents=True)
            commits, rows, previous = [], [], "sha256:" + "0" * 64

            def commit(message: str) -> str:
                for argv in (["add", "-A"], [*GIT, "commit", "-qm", message], ["rev-parse", "HEAD"]):
                    out = subprocess.run(["git", *argv], cwd=project, check=True, capture_output=True, text=True)
                return out.stdout.strip()

            for number, (application, package) in enumerate(
                    ((hashes["a"], hashes["c"]), (hashes["b"], hashes["d"]), (hashes["e"], hashes["d"])), 1):
                rows.append({"application_revision": number, "previous_application_hash": previous,
                             "application_hash": application,
                             "packages": [{"result_ref": "checkout@r2" if number > 1 else "checkout@r1",
                                           "package_hash": package}]})
                previous = application
                ledger.write_text(json.dumps({"schema_version": 3, "revisions": rows}), encoding="utf-8")
                landscape.write_text(f"---\ntype: landscape\npackage_hash: {hashes['abc'[number - 1]]}\n---\n\n"
                                     f"# Landscape\n\nSupersedes {hashes['a']}.\n", encoding="utf-8")
                commits.append(commit(f"revision {number}"))
            # Only the hash field counts, not a later mention of the same hash.
            _folder, anchor = rebind.source_location("workspace/docs", "solution-design", SOLUTION)
            self.assertEqual(rebind.receipt_commit(project, anchor, hashes["a"]), commits[0])
            self.assertEqual(rebind.receipt_commit(project, anchor, hashes["c"]), commits[2])
            # Revision 3 repeats revision 2's hashes; each approval stays where it was.
            _folder, anchor = rebind.source_location("workspace/docs", "experience-design", "application@r2")
            self.assertEqual([rebind.receipt_commit(project, anchor, hashes[name], ref) for name, ref in (
                ("a", "application@r1"), ("b", "application@r2"), ("d", "checkout@r2"))],
                [commits[0], commits[1], commits[1]])
            self.assertIsNone(rebind.receipt_commit(project, anchor, hashes["d"], "checkout@r1"))

    def test_a_reordered_transition_counts_the_whole_landscape(self):
        head = ("---\ntitle: L\npackage_hash: x\n---\n# L\n\n## Summary\n\ntext\n\n## Current\n\nnone\n\n"
                "## Target\n\n## Transition\n\n")
        first, second = "- Introduce cache ([[d/a\\|SD-001]]).\n", "- Add eviction ([[d/b\\|SD-002]]).\n"
        before = head + first + second + "\n## Components\n"
        self.assertIsNone(rebind.landscape_delta(before, head + second + first + "\n## Components\n"))
        added = rebind.landscape_delta(before, head + first + "- Add audit ([[d/c\\|SD-003]]).\n" + second
                                       + "\n## Components\n")
        self.assertEqual((added["links"], added["added_only"]), (["d/c"], True))

    def test_a_malformed_application_ledger_holds_nothing(self):
        for packages in (5, "checkout@r1", {"result_ref": "checkout@r1"}):
            data = json.dumps({"revisions": [{"previous_application_hash": "sha256:" + "0" * 64,
                                              "application_hash": "sha256:" + "1" * 64,
                                              "packages": packages}]}).encode()
            with self.subTest(packages=packages):
                self.assertFalse(rebind.ledger_holds(data, "checkout@r1", "sha256:" + "1" * 64))
                self.assertFalse(rebind.ledger_holds(data, "application@r1", "sha256:" + "1" * 64))
        for value in (b"[]", b'{"revisions": 5}', b'{"revisions": [5]}', b"\xff"):
            with self.subTest(ledger=value):
                self.assertFalse(rebind.ledger_holds(value, "application@r1", "sha256:" + "1" * 64))

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
