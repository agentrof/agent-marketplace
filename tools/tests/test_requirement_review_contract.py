"""Static contracts for transient, read-only Requirement review."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


class RequirementReviewContracts(unittest.TestCase):
    def test_backlog_reviewer_is_read_only_and_returns_structured_findings(self):
        reviewer = read("agents/backlog-reviewer.md")
        self.assertIn("tools: Read, Grep, Glob", reviewer)
        self.assertIn("Return findings to the invoking workflow", reviewer)
        self.assertIn("relation_audit", reviewer)
        self.assertIn("no writes performed", reviewer)
        self.assertNotIn("Write only the designated review note", reviewer)

    def test_backlog_flow_waits_before_single_writer_and_root_review(self):
        flow = " ".join(read("flows/backlog-planning.md").split())
        epic_wait = flow.index("Wait for every epic reviewer")
        epic_writer = flow.index("The Product Owner is the single writer")
        root_start = flow.index("Only after every epic package and review is green")
        root_wait = flow.index("Wait for its return", root_start)
        self.assertLess(epic_wait, epic_writer)
        self.assertLess(epic_writer, root_start)
        self.assertLess(root_start, root_wait)
        self.assertIn("no host-specific command is canonical", flow)
        self.assertIn("leaving review-completion checks to the final gate", flow)
        final_check = flow.index("After the root review is authored")
        self.assertLess(root_wait, final_check)
        self.assertIn("Both must pass before the package can be offered for approval", flow)

    def test_pre_backlog_challenge_has_no_durable_review_history(self):
        challenge = " ".join(read("skill-content/challenge-review/SKILL.md").split())
        triage = " ".join(
            read("skill-content/challenge-review/references/triage.md").split()
        )
        business_analysis = " ".join(
            read("skill-content/business-analysis/SKILL.md").split()
        )
        self.assertIn("not to a durable review history", challenge)
        self.assertIn("do not persist reviewer output as an audit log", challenge)
        self.assertIn("No reviewer-state field, transcript or audit", triage)
        self.assertIn("Do not create", business_analysis)
        self.assertIn("review-history documents", business_analysis)

    def test_epic_scope_retains_root_and_dependency_context(self):
        agent = " ".join(read("agents/backlog-reviewer.md").split())
        flow = " ".join(read("flows/backlog-planning.md").split())
        self.assertIn("Every epic review includes the root backlog", agent)
        self.assertIn("incoming and outgoing dependency closure", agent)
        self.assertIn("shared contract/source context", agent)
        self.assertIn("The root review reads the root backlog, every epic", agent)
        self.assertIn("every story and every story test plan", agent)
        self.assertIn("--epic <EP-ID>", flow)
        self.assertIn("--root", flow)
        self.assertIn("--expected-hash <source_hash>", flow)
        self.assertIn("Unresolved closure fails before dispatch", flow)
        self.assertIn("evidence outside the manifest", flow)
        self.assertNotIn(
            "1. Read the root backlog, every epic, every story and every story test plan.",
            agent,
        )

    def test_draft_review_scaffolds_do_not_masquerade_as_source_defects(self):
        agent = " ".join(read("agents/backlog-reviewer.md").split())
        flow = " ".join(read("flows/backlog-planning.md").split())
        self.assertIn("audit source membership against the manifest's expected", agent)
        self.assertIn("pending writer work, not source defects", agent)
        self.assertIn("incorrect nonempty declarations remain findings", agent)
        self.assertIn("stale completed review evidence offered for the current candidate", agent)
        self.assertIn("not missing source membership", agent)
        self.assertIn("do not by themselves request source changes", flow)
        self.assertIn("final compiler requires the exact written relation sets", flow)
        self.assertIn("complete review prose before approval", flow)

    def test_solution_entries_share_one_plan_with_all_required_lenses(self):
        plan = read("skill-content/solution-architecture/references/challenge-lenses.md")
        primary = plan.split("## Required primary lenses", 1)[1].split(
            "## Targeted specialists", 1
        )[0]
        self.assertEqual(
            set(re.findall(r"^- \*\*([^:]+):\*\*", primary, re.MULTILINE)),
            {
                "technology-fit-and-traceability",
                "sustainability-and-operability",
                "cost-and-lock-in",
                "security-and-compliance",
            },
        )
        for path in (
            "agents/solution-reviewer.md",
            "flows/solution-design.md",
            "skill-content/solution-design/SKILL.md",
            "skill-content/solution-design/references/engagement-session.md",
        ):
            with self.subTest(path=path):
                text = read(path)
                self.assertIn("solution-architecture/references/challenge-lenses.md", text)
                self.assertIn("primary", text)
        self.assertNotIn("one fresh, read-only challenger per lens", plan)
        self.assertIn("not four additional reviewer assignments", plan)

    def test_solution_specialists_preserve_independence_and_unknown_risk(self):
        plan = " ".join(
            read("skill-content/solution-architecture/references/challenge-lenses.md").split()
        )
        for risk in (
            "authorization", "privacy", "regulatory", "migration",
            "irreversible loss", "external integration", "topology", "unquantified",
        ):
            with self.subTest(risk=risk):
                self.assertIn(risk, plan)
        self.assertIn("independent of both the writer and the primary reviewer", plan)
        self.assertIn("Unknown impact is an unresolved question", plan)
        self.assertIn("full affected contracts and dependency context", plan)
        self.assertIn("wait for every selected reader before", plan)

    def test_experience_risk_checkpoints_cannot_replace_final_attestation(self):
        flow = " ".join(read("flows/experience-design.md").split())
        agent = " ".join(read("agents/experience-reviewer.md").split())
        self.assertNotIn("after each meaningful authoring milestone", flow)
        for risk in (
            "authorization or privacy", "cross-process ownership",
            "primary navigation", "irreversible user actions",
        ):
            with self.subTest(risk=risk):
                self.assertIn(risk, flow)
        self.assertIn("layout-only variants and cosmetic edits", flow)
        self.assertIn("Early advice never replaces the final snapshot review", flow)
        self.assertIn("never emits an approval attestation", agent)
        self.assertIn("advisory notes never block approval", agent)
        self.assertIn("findings are advisory and never prevent a prototype receipt", flow)

    def test_experience_final_review_follows_last_change_and_precedes_approval(self):
        flow = " ".join(read("flows/experience-design.md").split())
        start = flow.index("After the last authored package or artifact change")
        enter = flow.index("application lifecycle to `in_review`", start)
        final = flow.index("fresh final review", enter)
        approve = flow.index("run one `experience_compile.py approve-set`", final)
        self.assertLess(start, enter)
        self.assertLess(enter, final)
        self.assertLess(final, approve)
        self.assertIn("even a cosmetic edit cannot reuse evidence for different bytes", flow)
        agent = read("agents/experience-reviewer.md")
        for field in (
            "proposal_hash", "artifact_tree_hash", "application_package_set_hash",
            "application_hash", "application_revision", "reviewed_at_utc",
        ):
            with self.subTest(field=field):
                self.assertIn("`" + field + "`", agent)

    def test_retries_remain_finding_driven_and_code_minors_do_not_block(self):
        bank = " ".join(
            read("skill-content/challenge-review/references/lens-bank.md").split()
        )
        challenge = " ".join(read("skill-content/challenge-review/SKILL.md").split())
        code = " ".join(read("skill-content/code-review/SKILL.md").split())
        self.assertNotIn("Round 2 and 3", bank)
        self.assertNotIn("plus one lens that was silent", bank)
        self.assertIn("any additional lens whose evidence or risk changed", bank)
        self.assertIn("Once blocking gaps are closed", challenge)
        self.assertIn("do not require an extra clean round", challenge)
        self.assertIn("minors NEVER block", code)
        self.assertIn("Fixed order, all three on every cycle", code)
        self.assertIn("re-check what changed plus anything a fix could have touched", code)

    def test_backlog_review_blocks_only_on_critical_or_major_findings(self):
        agent = " ".join(read("agents/backlog-reviewer.md").split())
        flow = " ".join(read("flows/backlog-planning.md").split())
        skill = " ".join(read("skill-content/product-planning/SKILL.md").split())
        records = " ".join(
            read("skill-content/product-planning/references/structured-records.md").split()
        )
        self.assertIn("## Review findings", records)
        findings = records.split("## Review findings", 1)[1]
        self.assertNotIn("An unresolved finding keeps the review at", agent)
        self.assertIn(
            "Only an open critical or major finding keeps the review at `changes_requested`",
            agent,
        )
        self.assertIn(
            "Review findings section of "
            "`skill-content/product-planning/references/structured-records.md`",
            agent,
        )
        for row in ("| `critical` | yes |", "| `major` | yes |", "| `minor` | no |"):
            with self.subTest(row=row):
                self.assertIn(row, findings)
        for rule in (
            "never changes a returned severity",
            "Compiler and vault-gate errors are not rated",
            "is major, never minor",
            "never blocks approval and never starts another review round",
            "Fix it only in a writer pass that already carries a blocking fix",
            "| finding | owner_role | reason | revisit_trigger |",
            "It does not re-audit unchanged text",
            "closes only through a confirmed fix or disproof",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, findings)
        self.assertIn("only a critical or major finding blocks", flow)
        self.assertIn("the Product Owner preserves every returned severity", flow)
        self.assertIn("the manifest files whose `sha256` changed", flow)
        self.assertIn("A minor finding never blocks or starts another round", flow)
        self.assertIn("`Accepted Minor Findings` is optional in any review note", flow)
        self.assertIn("Only critical and major findings block.", skill)
        self.assertIn("rating, recording or re-reviewing review findings", skill)

    def test_solution_review_blocks_only_on_critical_or_major_findings(self):
        plan = " ".join(
            read("skill-content/solution-architecture/references/challenge-lenses.md").split()
        )
        reviewer = " ".join(read("agents/solution-reviewer.md").split())
        self.assertIn("## Severity", plan)
        severity = plan.split("## Severity", 1)[1].split("## Return and disposition", 1)[0]
        for row in ("| `critical` | yes |", "| `major` | yes |", "| `minor` | no |"):
            with self.subTest(row=row):
                self.assertIn(row, severity)
        self.assertIn("is major, never minor", severity)
        self.assertIn("never changes a returned severity", severity)
        self.assertIn("requests changes only while a critical or major finding is open", plan)
        self.assertIn("A minor finding never blocks and never starts another review", plan)
        self.assertIn("meaning a fix for a critical or major finding", plan)
        self.assertNotIn("verdict and blockers", reviewer)
        self.assertIn("severity (`critical`, `major` or `minor`)", reviewer)
        self.assertIn("requests changes only while a critical or major finding is open", reviewer)


if __name__ == "__main__":
    unittest.main()
