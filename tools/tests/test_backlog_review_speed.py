"""Backlog review speed switches: each keeps every default manifest, task and
compiler output as released and changes the backlog review only at its own
non-default value.

- review_scope_record both_scopes: an epic reader's manifest measures both
  review_manifest_scope read sets, and a command reports the blocking findings
  that cite a note outside the bounded read set (#395).
- remediation_writers per_epic: an epic writer reads its epic's review scope
  and writes only its epic's notes, and a cross-epic writer writes only the
  notes it is given (#394).
- root_review_scope revision_delta: a revision's root reader reads in full only
  the changed stories and their neighbours, with every other story as a
  hash-bound summary in the compiler's graph (#405).
- remediation_bookkeeping compiler: one compiler command writes the rechecks'
  closure rows, the expected manifest hashes and the preservation report
  (#398).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile as backlog  # noqa: E402
import backlog_review_inputs as inputs  # noqa: E402
import task_inputs  # noqa: E402
import test_review_manifest_scope as scope_tests  # noqa: E402
from backlog_fixture import (CONSTRAINT, CRITERION, DESIGN, EXPERIENCE,  # noqa: E402
                             _author_story, make_approved_backlog)
from git_fixture import init_repository, remove_temporary  # noqa: E402
from test_review_manifest_scope import choose, policy  # noqa: E402

REVIEW = "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"


def run(argv: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = inputs.main(argv)
    return code, json.loads(output.getvalue())


def backlog_fixture(case: type) -> type:
    """Give a test case the two-epic backlog of test_backlog_review_inputs; its
    chain() makes ST-001 link a rule set whose body and relation's links reach
    past the bounded read set."""
    base = scope_tests.BoundedManifestTests
    for name in ("setUp", "refresh_reviews", "story", "depends", "add_scope_text", "relative",
                 "note", "chain"):
        setattr(case, name, getattr(base, name))
    return case


@backlog_fixture
class ReviewScopeRecordTests(unittest.TestCase):

    def returned_findings(self, rows: list[tuple[str, str, str]]) -> None:
        path = self.docs / REVIEW
        props, body = backlog.parse_front_matter(path)
        table = ["## Returned Findings", "", "| finding | severity | description |",
                 "|---|---|---|", *(f"| {finding} | {severity} | {text} |"
                                    for finding, severity, text in rows), ""]
        body = body.replace("## Verdict", "\n".join(table) + "\n## Verdict", 1)
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")

    def test_off_measures_nothing_and_refuses_the_record_options(self):
        self.chain()
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertNotIn("scope_sizes", value)
        self.assertNotIn("review_scope_record", value)
        code, result = run(["--docs", str(self.docs), "--epic", "EP-001", "--scope-findings"])
        self.assertEqual(code, 1)
        self.assertIn("review_scope_record", result["errors"][0])

    def test_both_scopes_measures_each_read_set_and_binds_the_sizes(self):
        self.chain()
        transitive = inputs.manifest(self.docs, epic="EP-001", scope="transitive")
        bounded = inputs.manifest(self.docs, epic="EP-001", scope="bounded")
        choose(self.docs, "both_scopes", switch="review_scope_record")
        value = inputs.manifest(self.docs, epic="EP-001")
        self.assertEqual(value["review_scope_record"], "both_scopes")
        sizes = value["scope_sizes"]
        self.assertEqual(sizes["read"], "transitive")
        self.assertNotIn("transitive_budget", sizes)
        for name, derived in (("transitive", transitive), ("bounded", bounded)):
            with self.subTest(scope=name):
                self.assertEqual(sizes[name]["files"], len(derived["paths"]))
                self.assertEqual(sizes[name]["source_bytes"], sum(
                    (self.docs / path).stat().st_size for path in derived["paths"]))
        self.assertGreater(sizes["transitive"]["source_bytes"], sizes["bounded"]["source_bytes"])
        # The reader still reads the value in force, and the recheck holds.
        self.assertEqual(value["paths"], transitive["paths"])
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001",
                                         expected_hash=value["source_hash"]), value)
        # Only an epic reader measures; the root and a writer read as before.
        self.assertNotIn("scope_sizes", inputs.manifest(self.docs))
        self.assertNotIn("scope_sizes", inputs.manifest(self.docs, epic="EP-001", writer=True))

    def test_an_owner_budget_flags_a_transitive_read_set_over_it(self):
        self.chain()
        choose(self.docs, "both_scopes", switch="review_scope_record")
        size = inputs.manifest(self.docs, epic="EP-001")["scope_sizes"]["transitive"]["source_bytes"]
        for limit, over in ((size - 1, True), (size, False)):
            with self.subTest(limit=limit):
                policy(self.docs, "begin-revision")
                policy(self.docs, "set", "--switch", "review_scope_record",
                       "--parameter", "transitive_source_bytes", "--value", str(limit))
                policy(self.docs, "approve")
                self.assertEqual(inputs.manifest(self.docs, epic="EP-001")["scope_sizes"]
                                 ["transitive_budget"], {"source_bytes": limit, "over": over})

    def test_scope_findings_name_blocking_findings_outside_the_bounded_set(self):
        notes = self.chain()
        choose(self.docs, "both_scopes", switch="review_scope_record")
        story = "backlog/epics/delivery-fixture/stories/st-001/story"
        self.returned_findings([
            ("F-1", "major", f"[[{notes['far'][:-3]}\\|Ledger]] states a rule the story drops."),
            ("F-2", "critical", f"[[{story}\\|ST-001]] Scope contradicts its acceptance rule."),
            ("F-3", "minor", f"[[{notes['body'][:-3]}\\|Audit]] wording differs from the rule."),
        ])
        record = self.docs.parent / "measurements/review-scope.jsonl"
        code, result = run(["--docs", str(self.docs), "--epic", "EP-001", "--scope-findings",
                            "--record", str(record)])
        self.assertEqual(code, 0, result)
        self.assertEqual((result["source"], result["blocking"], result["blocking_outside_bounded"]),
                         ("review note", 2, 1))
        self.assertEqual({row["finding"]: row["outside_bounded"] for row in result["findings"]},
                         {"F-1": [notes["far"]], "F-2": []})
        code, manifest = run(["--docs", str(self.docs), "--epic", "EP-001", "--record", str(record)])
        self.assertEqual(code, 0, manifest)
        rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["kind"] for row in rows], ["findings", "manifest"])
        self.assertEqual(rows[1]["scope_sizes"], manifest["scope_sizes"])

    def test_the_record_defaults_to_the_workspace_file_and_never_lies_outside_it(self):
        self.chain()
        choose(self.docs, "both_scopes", switch="review_scope_record")
        code, manifest = run(["--docs", str(self.docs), "--epic", "EP-001", "--record"])
        self.assertEqual(code, 0, manifest)
        record = self.docs.parent / "measurements/review-scope.jsonl"
        self.assertEqual([(row["kind"], row["source_hash"]) for row in map(
            json.loads, record.read_text(encoding="utf-8").splitlines())],
            [("manifest", manifest["source_hash"])])
        # The record first lived under .agentrof/, outside the workspace; inside
        # the vault it would be a non-markdown file in a note subtree.
        for path in (self.docs.parents[1] / ".agentrof/agent-marketplace/measurements/review-scope.jsonl",
                     self.docs / "backlog/review-scope.jsonl"):
            with self.subTest(path=path):
                code, result = run(["--docs", str(self.docs), "--epic", "EP-001", "--record", str(path)])
                self.assertEqual(code, 1, result)
                self.assertIn("must lie in the workspace outside the docs vault", result["errors"][0])
                self.assertFalse(path.exists())
        self.assertEqual(len(record.read_text(encoding="utf-8").splitlines()), 1)

    def test_the_record_is_a_file_git_keeps_under_setups_ignore_rules(self):
        """The backlog revision commits the record (#395): setup's managed
        .gitignore ignores the runtime root where it first lived, and a record
        Git ignores is refused instead of silently left out of the commit."""
        import setup_project

        project = self.docs.parents[1].resolve()
        init_repository(project)
        ignore = project / ".gitignore"
        ignore.write_text(setup_project.managed_block("workspace") + "\n", encoding="utf-8")

        def ignored(path: Path) -> bool:
            return subprocess.run(["git", "check-ignore", "--quiet", "--", str(path)], cwd=project,
                                  check=False).returncode == 0

        self.assertTrue(ignored(project / ".agentrof/agent-marketplace/measurements/review-scope.jsonl"))
        record = inputs.record_path(self.docs, None)
        self.assertEqual(record, self.docs.resolve().parent / "measurements/review-scope.jsonl")
        self.assertFalse(ignored(record))
        ignore.write_text(ignore.read_text(encoding="utf-8") + "workspace/measurements/\n",
                          encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "Git ignores the review scope record"):
            inputs.record_path(self.docs, None)
        with self.assertRaisesRegex(inputs.InputError, "Git ignores the review scope record"):
            inputs.record_path(self.docs, str(self.docs.parent / "measurements/other.jsonl"))

    def test_scope_findings_read_a_claim_record_and_its_calibrated_severity(self):
        notes = self.chain()
        choose(self.docs, "both_scopes", switch="review_scope_record")
        claims = self.docs.parent / "claims.json"
        claims.write_text(json.dumps({"findings": [
            {"id": "F-1", "severity": "major", "anchor": notes["far"]},
            {"id": "F-2", "severity": "minor", "anchor": notes["body"]}]}), encoding="utf-8")
        code, result = run(["--docs", str(self.docs), "--epic", "EP-001", "--scope-findings",
                            "--findings", str(claims)])
        self.assertEqual(code, 0, result)
        self.assertEqual((result["source"], result["findings"]),
                         ("findings record", [{"finding": "F-1", "severity": "major",
                                               "notes": [notes["far"]],
                                               "outside_bounded": [notes["far"]]}]))
        story = "backlog/epics/delivery-fixture/stories/st-001/story"
        self.returned_findings([("F-1", "major", f"[[{story}\\|ST-001]] Scope drops a rule.")])
        path = self.docs / REVIEW
        props, body = backlog.parse_front_matter(path)
        body = body.replace("## Verdict", "## Severity Calibration\n\n| finding | claimed_severity |"
                            " calibrated_severity | reason |\n|---|---|---|---|\n| F-1 | major |"
                            f" minor | [[{story}\\|ST-001]] Scope and Acceptance name one rule. |"
                            "\n\n## Verdict", 1)
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.assertEqual(inputs.scope_findings(self.docs, "EP-001")["blocking"], 0)


def commit_all(root: Path) -> None:
    for args in (("add", "-A"), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                 "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class GitBacklogFixture:
    """EP-001 holds ST-001 and EP-002 holds ST-002, both finished, committed."""

    @staticmethod
    def build(case: unittest.TestCase) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(remove_temporary, temporary)
        root = Path(temporary.name).resolve()
        docs = root / "workspace/docs"
        (docs / "maps").mkdir(parents=True)
        (root / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            make_approved_backlog(docs, "ST-001")
            backlog.stub_epic(SimpleNamespace(docs=str(docs), slug="second", id="EP-002",
                                              title="Second", goal="Deliver a separate customer outcome."))
            backlog.stub_story(SimpleNamespace(
                docs=str(docs), epic="second", slug="st-002", id="ST-002", title=None, scope=None,
                work_kind="feature", criterion_ref=[CRITERION], experience_ref=[EXPERIENCE],
                evidence_ref=[], uses_design=[DESIGN], constrained_by=[CONSTRAINT]))
        folder = docs / "backlog/epics/second/stories/st-002"
        _author_story(folder / "story.md", folder / "test-plan.md", "ST-002")
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        commit_all(root)
        return root, docs


class RemediationWritersTests(unittest.TestCase):
    FIRST = "workspace/docs/backlog/epics/delivery-fixture/stories/st-001/story.md"
    SECOND = "workspace/docs/backlog/epics/second/stories/st-002/story.md"

    def setUp(self):
        self.root, self.docs = GitBacklogFixture.build(self)
        self.findings = ".agentrof/agent-marketplace/.runtime/cross-epic.json"
        path = self.root / self.findings
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"findings": [
            {"id": "F-1", "severity": "major", "anchor": self.FIRST,
             "text": "ST-001 and ST-002 both claim the account export."}]}), encoding="utf-8")

    def choose(self, *pairs: tuple[str, str]) -> None:
        exists = process_policy_path(self.docs).exists()
        policy(self.docs, "begin-revision" if exists else "init")
        for switch, value in pairs:
            policy(self.docs, "set", "--switch", switch, "--value", value)
        policy(self.docs, "approve")
        commit_all(self.root)

    def writer(self, **extra) -> dict:
        return task_inputs.manifest(entry="backlog-plan", role="product-owner", mode="revise",
                                    project=self.root, **extra)

    def allowed(self, task: dict) -> set[str]:
        return {row["path"] for row in task["write_scope"]["allowed_write_area"]}

    def link_far_note(self) -> None:
        """ST-001 links a rule set whose body links a note two hops out."""
        base = self.docs / "business-analysis/delivery/domains/identity"
        far = base / "entities/audit-entity.md"
        far.parent.mkdir(parents=True, exist_ok=True)
        far.write_text(backlog.front_matter({"type": "entity", "title": "Audit"}, "# Audit\n"),
                       encoding="utf-8")
        rules = base / "rules/account-rules.md"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text(backlog.front_matter(
            {"type": "rule_set", "title": "Account rules", "status": "approved"},
            "# Account rules\n\nAudit follows [[business-analysis/delivery/domains/identity/"
            "entities/audit-entity|Audit]].\n"), encoding="utf-8")
        story = self.root / self.FIRST
        props, body = backlog.parse_front_matter(story)
        body = body.replace("\n## Non-Goals", "\nIt applies [[business-analysis/delivery/domains/"
                            "identity/rules/account-rules|Account rules]].\n\n## Non-Goals", 1)
        story.write_text(backlog.front_matter(props, body), encoding="utf-8")
        commit_all(self.root)

    def test_an_epic_writer_reads_its_review_scope_only_at_per_epic(self):
        self.link_far_note()
        self.choose(("review_manifest_scope", "bounded"))
        reader = inputs.manifest(self.docs, epic="EP-001")
        single = inputs.manifest(self.docs, epic="EP-001", writer=True)
        self.assertNotIn("remediation_writers", single)
        self.assertNotIn("review_manifest_scope", single)
        self.assertLess(set(reader["paths"]), set(single["paths"]))
        self.choose(("remediation_writers", "per_epic"))
        writer = inputs.manifest(self.docs, epic="EP-001", writer=True)
        self.assertEqual((writer["remediation_writers"], writer["review_manifest_scope"]),
                         ("per_epic", "bounded"))
        self.assertEqual(writer["paths"], reader["paths"])
        self.assertEqual(inputs.manifest(self.docs, epic="EP-001", writer=True,
                                         expected_hash=writer["source_hash"]), writer)
        task = self.writer(epic="EP-001")
        self.assertIn("skill-content/backlog-plan/references/switch-remediation_writers-per_epic.md",
                      task["required_reads"])
        allowed = self.allowed(task)
        self.assertIn(self.FIRST, allowed)
        self.assertFalse({path for path in allowed if "/second/" in path})
        self.assertNotIn("workspace/docs/backlog/backlog.md", allowed)

    def test_a_cross_epic_writer_writes_only_its_inputs_at_per_epic(self):
        task = dict(findings=self.findings, inputs=[self.FIRST, self.SECOND])
        self.assertEqual(self.writer(**task)["write_scope"]["status"], "unresolved")
        self.choose(("remediation_writers", "per_epic"))
        cross = self.writer(**task)
        self.assertEqual(cross["write_scope"]["status"], "resolved")
        self.assertEqual(self.allowed(cross), {self.FIRST, self.SECOND})
        # Without findings it is no remediation writer, and a reader never writes.
        self.assertEqual(self.writer(inputs=[self.FIRST])["write_scope"]["status"], "unresolved")
        reader = task_inputs.manifest(entry="backlog-plan", role="backlog-reviewer", mode="review",
                                      project=self.root, **task)
        self.assertEqual(reader["write_scope"]["status"], "read_only")


class RootReviewScopeTests(unittest.TestCase):
    """An approved four-story backlog reopened as revision 2."""

    EPIC = "backlog/epics/delivery-fixture"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.docs = Path(temporary.name) / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs.parent / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        make_approved_backlog(self.docs, "ST-001", "ST-002", "ST-003", "ST-004")
        self.reopen(2)

    def reopen(self, revision: int) -> None:
        """Open a draft revision as begin-revision does, with a fresh root round."""
        root = self.docs / "backlog/backlog.md"
        props, body = backlog.parse_front_matter(root)
        backlog.status_tag(props, "draft")
        props["revision"] = revision
        for key in ("approved_at_utc", "source_hash", "package_hash"):
            props.pop(key, None)
        root.write_text(backlog.front_matter(props, body), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            backlog.stub_backlog_review(SimpleNamespace(docs=str(self.docs)))

    def path(self, number: int, name: str = "story") -> str:
        return f"{self.EPIC}/stories/st-{number:03d}/{name}.md"

    def depends(self, source: int, target: int) -> None:
        path = self.docs / self.path(source)
        props, body = backlog.parse_front_matter(path)
        link = f"[[{self.path(target)[:-3]}|ST-{target:03d}]]"
        props["depends_on"] = [*backlog.values(props, "depends_on"), link]
        body = body.replace("## Dependencies\n\nNone.",
                            "## Dependencies\n\n- " + link + ": Supplies the required input.")
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")

    def choose(self, limit: int | None = 60) -> None:
        exists = process_policy_path(self.docs).exists()
        policy(self.docs, "begin-revision" if exists else "init")
        policy(self.docs, "set", "--switch", "root_review_scope", "--value", "revision_delta")
        if limit is not None:
            policy(self.docs, "set", "--switch", "root_review_scope", "--parameter",
                   "max_delta_share_percent", "--value", str(limit))
        policy(self.docs, "approve")

    def test_full_reads_every_story_and_names_no_delta(self):
        self.depends(3, 4)
        value = inputs.manifest(self.docs)
        self.assertNotIn("root_review_scope", value)
        self.assertNotIn("check", value)
        for number in range(1, 5):
            self.assertIn(self.path(number, "test-plan"), value["paths"])

    def test_a_delta_read_names_changed_stories_and_neighbours_in_full(self):
        self.depends(3, 4)
        full = inputs.manifest(self.docs)
        self.choose()
        value = inputs.manifest(self.docs)
        self.assertEqual(value["root_review_scope"], "revision_delta")
        self.assertEqual(value["revision_delta"], {
            "changed": ["ST-003"], "neighbours": ["ST-004"], "share_percent": 50,
            "max_share_percent": 60, "read": "delta"})
        for number in (3, 4):
            for name in ("story", "test-plan"):
                self.assertIn(self.path(number, name), value["primary_paths"])
        for number in (1, 2):
            for name in ("story", "test-plan"):
                self.assertNotIn(self.path(number, name), value["paths"])
        self.assertIn(f"{self.EPIC}/epic.md", value["primary_paths"])
        self.assertLess(set(value["paths"]), set(full["paths"]))
        graph = value["check"]["backlog_graph"]
        self.assertEqual({identity: row["read"] for identity, row in graph["stories"].items()},
                         {"ST-001": "summary", "ST-002": "summary", "ST-003": "full",
                          "ST-004": "full"})
        self.assertEqual(graph["stories"]["ST-001"]["story_sha256"],
                         inputs.file_hash(self.docs / self.path(1)))
        self.assertEqual(graph["stories"]["ST-003"]["depends_on"], ["ST-004"])
        self.assertEqual(graph["dependency_edges"],
                         sorted(backlog.dependency_edges(backlog.collect(self.docs)[0]["stories"],
                                                         None, backlog.collect(self.docs)[0])))
        # The root review's compiler facts come with the delta read.
        self.assertEqual(value["check"]["counts"]["stories"], 4)
        self.assertEqual(inputs.manifest(self.docs, expected_hash=value["source_hash"]), value)
        # The hash binds every backlog byte, an unchanged story's included.
        summary = self.docs / self.path(1)
        summary.write_text(summary.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(inputs.InputError, "stale"):
            inputs.manifest(self.docs, expected_hash=value["source_hash"])

    def test_an_unchanged_story_a_delta_note_links_is_read_alone(self):
        self.depends(3, 4)
        path = self.docs / self.path(3)
        props, body = backlog.parse_front_matter(path)
        body = body.replace("\n## Non-Goals", f"\nIt hands over to [[{self.path(1)[:-3]}|ST-001]]."
                            "\n\n## Non-Goals", 1)
        path.write_text(backlog.front_matter(props, body), encoding="utf-8")
        self.choose()
        value = inputs.manifest(self.docs)
        self.assertIn(self.path(1), value["context_paths"])
        self.assertNotIn(self.path(1, "test-plan"), value["paths"])

    def test_the_whole_package_is_read_when_the_delta_cannot_stand_alone(self):
        self.depends(3, 4)
        self.choose(limit=40)
        value = inputs.manifest(self.docs)
        self.assertEqual((value["revision_delta"]["read"], value["revision_delta"]["reason"]),
                         ("full", "the delta holds more than 40% of the stories"))
        self.assertIn(self.path(1, "test-plan"), value["paths"])
        self.choose(limit=60)
        code, result = run(["--docs", str(self.docs), "--root", "--full-root-reason",
                            "ST-003 may overlap ST-001's export scope"])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["revision_delta"]["reason"],
                         "reader request: ST-003 may overlap ST-001's export scope")
        self.assertIn(self.path(1, "test-plan"), result["paths"])
        self.reopen(1)
        self.assertEqual(inputs.manifest(self.docs)["revision_delta"]["reason"],
                         "first backlog revision")

    def test_a_root_reader_task_carries_the_delta_and_a_reader_request(self):
        self.depends(3, 4)
        self.choose()
        root = self.docs.parent.parent
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        commit_all(root)
        task = dict(entry="backlog-plan", role="backlog-reviewer", mode="review", project=root,
                    epic="")
        delta = task_inputs.manifest(**task)
        self.assertIn("skill-content/backlog-plan/references/switch-root_review_scope-revision_delta.md",
                      delta["required_reads"])
        self.assertEqual(delta["backlog_scope"]["revision_delta"]["read"], "delta")
        full = task_inputs.manifest(**task, full_root_reason="Overlap with an unchanged story.")
        self.assertEqual(full["backlog_scope"]["revision_delta"]["reason"],
                         "reader request: Overlap with an unchanged story.")
        with self.assertRaisesRegex(ValueError, "full root read request"):
            task_inputs.manifest(**dict(task, epic="EP-001"), full_root_reason="Read it all.")
        with self.assertRaisesRegex(ValueError, "root review task"):
            task_inputs.manifest(**dict(task, epic=None), full_root_reason="Read it all.")

    def test_a_reader_request_needs_the_value_and_a_cycle_still_fails(self):
        with self.assertRaisesRegex(inputs.InputError, "full root read request"):
            inputs.manifest(self.docs, full_root_reason="Read everything.")
        self.choose()
        self.depends(3, 4)
        self.depends(4, 3)
        with self.assertRaisesRegex(inputs.InputError, "cycle"):
            inputs.manifest(self.docs)


class RemediationBookkeepingTests(unittest.TestCase):
    REVIEW = "backlog/epics/second/reviews/round-1-epic-review.md"
    PLAN = "[[backlog/epics/second/stories/st-002/test-plan|ST-002-TP]]"

    def setUp(self):
        self.root, self.docs = GitBacklogFixture.build(self)
        self.runtime = self.root / ".agentrof/agent-marketplace/.runtime"
        self.runtime.mkdir(parents=True)
        self.closures = self.runtime / "closures.json"
        self.report = self.runtime / "remediation-report.json"

    def choose(self, value: str) -> None:
        exists = process_policy_path(self.docs).exists()
        policy(self.docs, "begin-revision" if exists else "init")
        policy(self.docs, "set", "--switch", "remediation_bookkeeping", "--value", value)
        policy(self.docs, "approve")
        commit_all(self.root)

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write_closures(self, *rows: dict) -> None:
        self.closures.write_text(json.dumps({"closures": [dict({
            "review": self.REVIEW, "finding": "F-1", "reader": "criteria-coverage",
            "result": "closed", "manifest_hash": "sha256:" + "a" * 64,
            "evidence": f"{self.PLAN} The plan now asserts the lockout after five attempts."},
            **row) for row in rows]}), encoding="utf-8")

    def record(self, *extra: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = backlog.main(["record-rechecks", "--docs", str(self.docs), "--closures",
                                 str(self.closures), "--candidate", self.candidate, "--report",
                                 str(self.report), *extra])
        return code, json.loads(output.getvalue())

    def test_the_command_runs_only_at_compiler(self):
        self.candidate = self.head()
        self.write_closures({})
        code, result = self.record()
        self.assertEqual(code, 1)
        self.assertIn("remediation_bookkeeping compiler", result["errors"][0])
        self.assertFalse(self.report.exists())

    def test_one_command_writes_closures_hashes_and_the_preservation_report(self):
        self.choose("compiler")
        self.candidate = self.head()
        self.write_closures({}, {"finding": "F-2", "reader": "dependencies", "result": "open"})
        code, result = self.record()
        self.assertEqual(code, 0, result)
        body = (self.docs / self.REVIEW).read_text(encoding="utf-8")
        self.assertIn("## Recheck Closures\n\n| finding | reader | result | manifest_hash |"
                      " evidence |\n|---|---|---|---|---|\n| F-1 | criteria-coverage | closed |", body)
        self.assertIn("[[backlog/epics/second/stories/st-002/test-plan\\|ST-002-TP]]", body)
        self.assertLess(body.index("## Recheck Closures"), body.index("## Verdict"))
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report, result["report"])
        self.assertEqual(report["candidate"], self.candidate)
        self.assertEqual(report["expected_hashes"],
                         {"EP-002": inputs.manifest(self.docs, epic="EP-002")["source_hash"]})
        preservation = report["preservation"]
        self.assertEqual((preservation["changed"], preservation["added"], preservation["removed"],
                          preservation["violations"]), ([self.REVIEW], [], [], []))
        self.assertGreater(preservation["preserved"], 0)
        # The check validates the rows it wrote, and a rerun writes the same bytes.
        props, review_body = backlog.parse_front_matter(self.docs / self.REVIEW)
        self.assertEqual(backlog.recheck_closure_record(self.docs, review_body, self.REVIEW, props), [])
        written = (self.docs / self.REVIEW).read_bytes()
        self.assertEqual(self.record()[0], 0)
        self.assertEqual((self.docs / self.REVIEW).read_bytes(), written)
        self.assertEqual(self.record("--verify")[0], 0)

    def test_verify_refuses_a_hand_edit_and_a_stale_report(self):
        self.choose("compiler")
        self.candidate = self.head()
        self.write_closures({})
        self.assertEqual(self.record()[0], 0)
        path = self.docs / self.REVIEW
        path.write_text(path.read_text(encoding="utf-8").replace("| closed |", "| open |"),
                        encoding="utf-8")
        code, result = self.record("--verify")
        self.assertEqual(code, 1)
        self.assertIn(f"{self.REVIEW} Recheck Closures differ from the closures", result["errors"])
        self.assertTrue(any(error.startswith("report is stale") for error in result["errors"]))

    def test_rows_are_validated_and_approved_reviews_stay_immutable(self):
        self.choose("compiler")
        self.candidate = self.head()
        self.write_closures({"result": "maybe", "manifest_hash": "sha256:short"})
        code, result = self.record()
        self.assertEqual(code, 1)
        self.assertTrue(any("result must be closed or open" in error for error in result["errors"]))
        self.assertTrue(any("manifest_hash must be" in error for error in result["errors"]))
        self.assertNotIn("Recheck Closures", (self.docs / self.REVIEW).read_text(encoding="utf-8"))
        approved = "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md"
        self.write_closures({"review": approved})
        self.assertIn("is approved; an approved review is immutable", self.record()[1]["errors"][0])
        # A hand edit of an approved review since the candidate is a violation.
        path = self.docs / approved
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.write_closures({})
        code, result = self.record()
        self.assertEqual(code, 1)
        self.assertEqual(result["report"]["preservation"]["violations"],
                         [f"{approved} is an approved review the candidate holds; it is immutable"])

    def test_at_writer_a_recheck_section_is_authored_text(self):
        self.candidate = self.head()
        path = self.docs / self.REVIEW
        props, body = backlog.parse_front_matter(path)
        body = body.replace("## Verdict", "## Recheck Closures\n\nThe readers closed F-1.\n\n"
                            "## Verdict", 1)
        self.assertEqual(backlog.recheck_closure_record(self.docs, body, self.REVIEW, props), [])
        self.choose("compiler")
        self.assertTrue(backlog.recheck_closure_record(self.docs, body, self.REVIEW, props))


def process_policy_path(docs: Path) -> Path:
    import process_policy
    return process_policy.path_for(docs)


if __name__ == "__main__":
    unittest.main()
