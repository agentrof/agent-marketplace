"""Review panels: one shared protocol, lens data per step, the review mode
switch that keeps the single reviewer by default, and the lens tier."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
PANELS = "skill-content/challenge-review/data/review-panels.json"
PROTOCOL = "skill-content/challenge-review/references/review-panel.md"
BACKLOG_LENSES = [
    "scope-and-slicing",
    "criteria-coverage-and-test-design",
    "dependencies-and-role-ownership",
]
# step: (owning flow, reader role, lens ids in default panel order)
APPROVED_PANELS = {
    "backlog_epic": ("backlog-planning", "backlog-reviewer", BACKLOG_LENSES),
    "backlog_root": ("backlog-planning", "backlog-reviewer", BACKLOG_LENSES),
    "solution_design": ("solution-design", "solution-reviewer", [
        "technology-fit-and-traceability",
        "sustainability-and-operability",
        "cost-and-lock-in",
        "security-and-compliance",
    ]),
    "design_system": ("design-system", "design-system-reviewer", [
        "token-system-and-catalog",
        "accessibility-and-states",
        "contradictions-and-upstream-fit",
    ]),
    "operation_verification": ("operation", "devops-engineer", ["command-safety", "boundary-fit"]),
    "operation_environment": ("operation", "qa-engineer", ["command-safety", "boundary-fit"]),
}
LENS_READERS = {
    "analysis-challenger", "backlog-reviewer", "design-system-reviewer",
    "domain-expert", "solution-reviewer",
}
# flow: the single-reviewer path that stays the default until promotion.
SINGLE_PATHS = {
    "backlog-planning": (
        "In `single` mode give one fresh `backlog-reviewer` the returned manifest",
        "In `single` mode invoke one fresh `backlog-reviewer` with that manifest",
        "In `single` mode rerun only the affected reviewer",
    ),
    "solution-design": (
        "In `single` mode, the default, spawn one independent primary"
        " `solution-reviewer` for all four challenge lenses",
    ),
    "design-system": (
        "In `single` mode, the default, spawn `design-system-reviewer` read-only with"
        " MASTER, catalog, page overrides and the semantic token, accessibility and"
        " contradiction lens",
    ),
    "operation": (
        "In `single` mode, the default, spawn the non-writing counterpart as a"
        " read-only reviewer",
        "command safety lens and `SELF-CHECK`",
    ),
}


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def flat(relative: str) -> str:
    return " ".join(read(relative).split())


def tier(agent: str) -> str:
    match = re.search(r"^reasoning: (\S+)$", read(f"agents/{agent}.md"), re.MULTILINE)
    assert match, agent
    return match.group(1)


class ReviewPanelProtocolTests(unittest.TestCase):
    def test_protocol_defines_parallel_single_assignment_readers(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "one fresh, read-only reader per lens assignment, in parallel, over the same inputs",
            "never runs beside one",
            "exactly one assignment",
            "Never pass another reader's reply, conversation history or the writer's interpretation",
            "Start every reader of the panel together through the host's parallel agent invocation",
            "Wait for every reader before any writer action",
            "every lens belongs to exactly one assignment",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)

    def test_merge_keeps_every_severity_and_mints_no_identity(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "share one root cause merge into one finding only for triage clarity",
            "keeps every reader's evidence and the highest returned severity",
            "Merging mints no id and keeps no counter or transcript",
            "the writer never lowers a returned severity",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)

    def test_verdict_minor_and_re_review_rules(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "approved only when every assignment has returned, no lens has an open critical"
            " or major finding and the owning compiler checks named by the flow are green",
            "A minor finding never blocks and never starts a round",
            "rerun only the assignments that returned such a finding",
            "The first rerun assignment in `default_panel` order also performs the"
            " changed-text check",
            "no clean extra round follows",
            "adds that lens's assignment",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)
        for row in ("| `critical` | yes |", "| `major` | yes |", "| `minor` | no |"):
            with self.subTest(row=row):
                self.assertIn(row, protocol)

    def test_one_result_per_role_steps_wait_for_a_merge_step(self):
        protocol = flat(PROTOCOL)
        self.assertIn(
            "Delivery code review and QA, and the Experience attestation, keep one reader"
            " per role because their machine interfaces accept one result per role", protocol,
        )
        self.assertIn("through a merge step that produces that one result", protocol)
        for flow in ("delivery-execution", "delivery-planning", "experience-design"):
            with self.subTest(flow=flow):
                self.assertNotRegex(read(f"flows/{flow}.md"), r"review\s+panel\s+`")

    def test_challenge_review_skill_links_the_protocol(self):
        skill = read("skill-content/challenge-review/SKILL.md")
        self.assertIn("[review-panel](references/review-panel.md)", skill)
        self.assertIn("Read when a flow reaches a review step that"
                      " `data/review-panels.json` declares", skill)
        self.assertIn("data/review-panels.json", skill)
        self.assertIn("its flow's single reviewer by default, or its review panel",
                      " ".join(skill.split()))

    def test_review_mode_switch_selects_the_single_reviewer_by_default(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "`review_mode` in `data/review-panels.json` is the one switch between the two"
            " review paths of every declared step",
            "A flow reads it before each review step",
            "`single`, the default: the step runs the one reviewer its flow names, on that"
            " flow's single-reviewer path. The rest of this file does not apply.",
            "`panel`: the step runs its review panel as this file defines",
            "so the mode also selects the tier of the read-only document readers",
            "never a per-project edit",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)


class ReviewPanelDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(read(PANELS))
        self.steps = self.data["review_steps"]

    def test_switch_ships_single_and_keeps_readers_on_their_pre_panel_tier(self):
        # Owner rule: the single reviewer stays the default until at least 5
        # panel passes across 2 flows match its valid-major recall in at most
        # half its wall time. Promotion edits review_mode, never this test's
        # expectation of the other mode.
        self.assertEqual(self.data["review_mode"], "single")
        self.assertEqual(self.data["review_modes"], {
            "single": {"tier_overrides": {"lens": "high"}},
            "panel": {"tier_overrides": {}},
        })

    def test_default_panels_match_the_approved_scope(self):
        self.assertEqual(set(self.steps), set(APPROVED_PANELS))
        for step, (_flow, role, lenses) in APPROVED_PANELS.items():
            with self.subTest(step=step):
                spec = self.steps[step]
                self.assertEqual(spec["reader_role"], role)
                self.assertEqual([lens["id"] for lens in spec["lenses"]], lenses)
                self.assertEqual(spec["default_panel"], [[lens] for lens in lenses])
                self.assertTrue(all(lens["focus"].strip() for lens in spec["lenses"]))

    def test_every_step_is_anchored_in_its_own_flow(self):
        # Solution Design binds the protocol through its single review plan.
        plan = "skill-content/solution-architecture/references/challenge-lenses.md"
        for step, (flow, _role, _lenses) in APPROVED_PANELS.items():
            with self.subTest(step=step):
                text = read(f"flows/{flow}.md")
                self.assertIn(f"review panel `{step}`", " ".join(text.split()))
                self.assertIn("`review_mode` in", text)
                if flow == "solution-design":
                    self.assertIn(plan, text)
                    text = read(plan)
                self.assertIn("challenge-review/references/review-panel.md", text)

    def test_every_panel_flow_keeps_its_single_reviewer_path(self):
        for flow, rules in SINGLE_PATHS.items():
            text = flat(f"flows/{flow}.md")
            for rule in rules:
                with self.subTest(flow=flow, rule=rule):
                    self.assertIn(rule, text)

    def test_backlog_lenses_partition_the_review_note_sections(self):
        policy = json.loads(read("skill-content/obsidian-vault/data/vault-policy.json"))
        for step, key in (("backlog_epic", "required_epic_review_sections"),
                          ("backlog_root", "required_backlog_review_sections")):
            with self.subTest(step=step):
                spec = self.steps[step]
                self.assertEqual(spec["review_note"]["sections"], f"backlog_contract.{key}")
                owned = [section for lens in spec["lenses"] for section in lens["covers"]]
                owned += spec["review_note"]["panel_sections"]
                self.assertEqual(sorted(owned), sorted(policy["backlog_contract"][key]))
                self.assertEqual(len(owned), len(set(owned)))
                self.assertEqual(spec["review_note"]["panel_sections"], ["Findings", "Verdict"])


class ReviewPanelFlowTests(unittest.TestCase):
    def test_backlog_panels_share_one_manifest_and_one_writer(self):
        flow = flat("flows/backlog-planning.md")
        for rule in (
            "Every lens reader receives the same returned manifest",
            "it carries no lens key, so one manifest serves the whole panel",
            "Wait for every epic reviewer to return before any writer action",
            "Wait for every root reviewer to return",
            "--expected-hash <source_hash>",
            "Panel lens readers take these facts as given",
            "In `panel` mode it merges findings that share one root cause",
            "Findings and Verdict come from the merged panel result",
            "rerun only the lens assignments that returned those findings",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, flow)
        reviewer = flat("agents/backlog-reviewer.md")
        for rule in (
            "One reviewer covers every step below in `single` review mode, the default",
            "In `panel` mode a reader holds one lens assignment of review panel"
            " `backlog_epic` or `backlog_root`",
            "The manifest's `check` block is compiler fact: a lens reader takes it as"
            " given and never recounts or re-derives it",
            "- `lens`: a lens reader's assigned lens ids; the single reviewer omits it.",
            "expected, actual, missing and extra source target sets per typed relation",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, reviewer)

    def test_solution_panel_replaces_the_primary_and_keeps_its_guard(self):
        flow = flat("flows/solution-design.md")
        self.assertIn("The panel replaces the single primary reviewer and never runs beside one", flow)
        self.assertIn("they do not form a second panel", flow)
        plan = flat("skill-content/solution-architecture/references/challenge-lenses.md")
        self.assertNotIn("## Required primary lenses", plan)
        self.assertIn("could not start two full panels", plan)
        self.assertIn("a specialist is never a second panel", plan)

    def test_design_system_and_operation_panels(self):
        design = flat("flows/design-system.md")
        self.assertIn("one read-only `design-system-reviewer` per lens assignment, in parallel", design)
        self.assertIn("Resolve every critical or major finding before approval", design)
        self.assertIn("severity (`critical`, `major` or `minor` from the panel protocol's table)",
                      flat("agents/design-system-reviewer.md"))
        operation = flat("flows/operation.md")
        for rule in (
            "review panel `operation_verification` for a Verification Contract",
            "review panel `operation_environment` for an Environment Contract",
            "read-only and on that role's own tier",
            "--mode review --skill challenge-review",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, operation)
        self.assertIn("adds `--skill challenge-review`", flat("templates/task-input-contract.md"))

    def test_skills_describe_both_review_paths(self):
        for path, rule in (
            ("skill-content/backlog-plan/SKILL.md",
             "fresh read-only reviews in the flow's review mode"),
            ("skill-content/design-system/SKILL.md",
             "in the flow's review mode, one reviewer or its review panel"),
            ("skill-content/solution-design/SKILL.md",
             "one independent primary `solution-reviewer`, or in `panel` review mode one"
             " panel of `solution-reviewer` lens readers"),
            ("skill-content/solution-design/references/engagement-session.md",
             "its primary reviewer, or in `panel` review mode its panel of lens readers"),
            ("skill-content/product-planning/references/structured-records.md",
             "reruns only the affected reviewer or, in `panel` review mode, only the lens"
             " assignments that returned those findings"),
            ("skill-content/solution-architecture/references/challenge-lenses.md",
             "`single`, the default: one fresh, read-only `solution-reviewer` is the"
             " primary reviewer"),
        ):
            with self.subTest(path=path):
                self.assertIn(rule, flat(path))

    def test_host_contracts_dispatch_panels_in_parallel_on_the_lens_tier(self):
        contracts = {
            host: " ".join(
                (ROOT / "platforms" / host / "software-engineering-team" / "host-contract.md")
                .read_text(encoding="utf-8").split()
            )
            for host in ("claude", "codex")
        }
        self.assertIn("spawn every reader of the panel in one message", contracts["claude"])
        self.assertIn("The `lens` tier of the read-only document lens readers is `sonnet` at"
                      " effort `high`", contracts["claude"])
        self.assertIn("start every reader of the panel before waiting on any of them",
                      contracts["codex"])
        self.assertIn("sets effort `high` and no model", contracts["codex"])
        for host, contract in contracts.items():
            with self.subTest(host=host):
                self.assertIn("the default `single` review mode renders", contract)
                self.assertIn("`tier_overrides` in"
                              " `skill-content/challenge-review/data/review-panels.json`",
                              contract)


class LensTierTests(unittest.TestCase):
    def test_only_the_read_only_document_readers_use_the_lens_tier(self):
        agents = {path.stem for path in (TEAM / "agents").glob("*.md")}
        self.assertEqual({agent for agent in agents if tier(agent) == "lens"}, LENS_READERS)
        for agent in sorted(LENS_READERS):
            with self.subTest(agent=agent):
                self.assertIn("tools: Read, Grep, Glob", read(f"agents/{agent}.md"))
        for agent, expected in (("experience-reviewer", "high"), ("code-reviewer", "high"),
                                ("qa-engineer", "medium"), ("devops-engineer", "medium")):
            with self.subTest(agent=agent):
                self.assertEqual(tier(agent), expected)

    def test_lens_tier_is_declared_for_every_host(self):
        models = json.loads((ROOT / "tools/data/models.json").read_text(encoding="utf-8"))
        self.assertIn("lens", models["reasoning_levels"])
        tables = {
            host: json.loads((ROOT / "platforms" / host / "execution-profiles.json")
                             .read_text(encoding="utf-8"))["profiles"]["auto"]
            for host in ("claude", "codex")
        }
        self.assertEqual(tables["claude"]["lens"], {"model": "sonnet", "effort": "high"})
        self.assertEqual(tables["codex"]["lens"], {"effort": "high"})


if __name__ == "__main__":
    unittest.main()
