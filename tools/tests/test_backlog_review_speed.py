"""Backlog review speed switches: each keeps every default manifest, task and
compiler output as released and changes the backlog review only at its own
non-default value.

- review_scope_record both_scopes: an epic reader's manifest measures both
  review_manifest_scope read sets, and a command reports the blocking findings
  that cite a note outside the bounded read set (#395).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile as backlog  # noqa: E402
import backlog_review_inputs as inputs  # noqa: E402
import test_review_manifest_scope as scope_tests  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
