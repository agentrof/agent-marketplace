"""Backlog review speed switches: each keeps every default manifest, task and
compiler output as released and changes the backlog review only at its own
non-default value.

- review_scope_record both_scopes: an epic reader's manifest measures both
  review_manifest_scope read sets, and a command reports the blocking findings
  that cite a note outside the bounded read set (#395).
- remediation_writers per_epic: an epic writer reads its epic's review scope
  and writes only its epic's notes, and a cross-epic writer writes only the
  notes it is given (#394).
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


def process_policy_path(docs: Path) -> Path:
    import process_policy
    return process_policy.path_for(docs)


if __name__ == "__main__":
    unittest.main()
