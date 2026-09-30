"""Review panels: lens data per step, one protocol that a task binds only at
switch `review_panels: lens_panel`, and lens-tier reader variants that leave
the single reviewers and every default-path instruction as released."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
PANELS = "skill-content/challenge-review/data/review-panels.json"
PROTOCOL = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
REGISTRY = "skill-content/configure/data/process-switches.json"
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
LENS_VARIANTS = ["backlog-reviewer", "design-system-reviewer", "solution-reviewer"]
# flow: the released single-reviewer path, which stays the default path.
SINGLE_PATHS = {
    "backlog-planning": (
        "Give one fresh `backlog-reviewer` the returned manifest and every named path",
        "Invoke one fresh `backlog-reviewer` with that manifest",
        "rerun only the affected reviewer",
    ),
    "solution-design": (
        "Spawn one independent primary `solution-reviewer` for all four required lenses",
    ),
    "design-system": (
        "Spawn `design-system-reviewer` read-only with MASTER, catalog, page overrides and"
        " the semantic token, accessibility and contradiction lens",
    ),
    "operation": (
        "Spawn the non-writing counterpart as a read-only reviewer",
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
    def test_protocol_applies_only_at_the_lens_panel_value(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "These are the instructions of process switch `review_panels` at `lens_panel`",
            "A task binds this file only when the project's Process Policy selects that value",
            "at the default, `single_reader`, every review runs as its flow and role files"
            " describe",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)
        # A link would make the protocol a conditional read on the default path.
        self.assertNotIn("switch-review_panels", read("skill-content/challenge-review/SKILL.md"))

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
            "Its assignment narrows the coverage its role file describes",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)

    def test_readers_run_as_lens_variants_and_bind_the_protocol(self):
        protocol = flat(PROTOCOL)
        self.assertIn("spawn `backlog-reviewer-lens`, `solution-reviewer-lens` or"
                      " `design-system-reviewer-lens`", protocol)
        self.assertIn("The Operation counterparts run as themselves, on their own tier", protocol)
        self.assertIn("A reader role that does not bind this skill, and the step's writer, add"
                      " `--skill challenge-review`", protocol)

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


class ReviewPanelDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(read(PANELS))
        self.steps = self.data["review_steps"]
        self.switch = json.loads(read(REGISTRY))["switches"]["review_panels"]

    def test_switch_defaults_to_the_single_reader_under_the_owner_flip_rule(self):
        # The single reviewer stays the default until at least 5 panel passes
        # across 2 flows match its valid-major recall in at most half its wall
        # time and the owner approves the flip.
        self.assertEqual(self.switch["default"], "single_reader")
        self.assertEqual([value["id"] for value in self.switch["values"]],
                         ["single_reader", "lens_panel"])
        self.assertEqual(self.switch["flows"],
                         sorted({flow for flow, _role, _lenses in APPROVED_PANELS.values()}))
        self.assertEqual(self.switch["promotion"]["unit"],
                         "At least 5 panel review passes across at least 2 flows.")
        self.assertIn("Panel valid-major recall at least equal to the official review's and panel"
                      " wall time at most 50% of it", self.switch["promotion"]["threshold"])
        self.assertEqual(self.switch["agent_variants"], {"lens_panel": {
            "suffix": "lens", "tier": "lens",
            "description": "Lens-tier reader variant for review panels.",
            "agents": LENS_VARIANTS}})
        self.assertEqual(set(self.data), {"schema_version", "review_steps"})

    def test_default_panels_match_the_approved_scope(self):
        self.assertEqual(set(self.steps), set(APPROVED_PANELS))
        for step, (_flow, role, lenses) in APPROVED_PANELS.items():
            with self.subTest(step=step):
                spec = self.steps[step]
                self.assertEqual(spec["reader_role"], role)
                self.assertEqual([lens["id"] for lens in spec["lenses"]], lenses)
                self.assertEqual(spec["default_panel"], [[lens] for lens in lenses])
                self.assertTrue(all(lens["focus"].strip() for lens in spec["lenses"]))

    def test_every_step_is_anchored_in_its_flow_beside_the_switch(self):
        for step, (flow, _role, _lenses) in APPROVED_PANELS.items():
            with self.subTest(step=step):
                text = flat(f"flows/{flow}.md")
                self.assertIn(f"review panel `{step}`", text)
                self.assertIn("Switch `review_panels`: at `lens_panel`", text)
                self.assertIn(PROTOCOL, text)

    def test_every_panel_flow_keeps_its_released_single_reviewer_path(self):
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
        protocol = flat(PROTOCOL)
        for rule in (
            "The review panel `backlog_epic` runs for each epic and review panel `backlog_root`"
            " for the root, in place of the single `backlog-reviewer`",
            "Every lens reader receives the same returned manifest and every named path",
            "the manifest carries no lens key, so one manifest serves the whole panel",
            "--expected-hash <source_hash>",
            "Lens readers take these facts as given and never recount them",
            "The Product Owner merges findings that share one root cause",
            "Findings and Verdict come from the merged panel result",
            "A re-review reruns only the lens assignments that returned the blocking findings",
            "The metadata-recovery root review is a `backlog_root` panel",
            "report `relation_audit` as `confirmed`",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)

    def test_solution_panel_replaces_the_primary_and_keeps_its_guard(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "The review panel `solution_design` replaces the single primary `solution-reviewer`",
            "run exactly one primary reviewer or one panel per review, never both",
            "a specialist is never a second panel",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)
        plan = flat("skill-content/solution-architecture/references/challenge-lenses.md")
        self.assertIn("## Required primary lenses", plan)
        self.assertNotIn("panel", plan.replace("second full panel", ""))

    def test_solution_panel_lenses_are_the_primary_reviewer_lenses(self):
        plan = read("skill-content/solution-architecture/references/challenge-lenses.md")
        primary = plan.split("## Required primary lenses", 1)[1].split("## Targeted specialists", 1)[0]
        self.assertEqual(
            re.findall(r"^- \*\*([^:]+):\*\*", primary, re.MULTILINE),
            [lens["id"] for lens in json.loads(read(PANELS))["review_steps"]["solution_design"]["lenses"]],
        )

    def test_design_system_and_operation_panels(self):
        protocol = flat(PROTOCOL)
        for rule in (
            "The review panel `design_system` runs one read-only reader per lens assignment, in"
            " parallel",
            "review panel `operation_environment` an Environment Contract",
            "read-only and on that role's own tier",
            "--mode review --skill challenge-review",
            "Resolve every critical or major finding before approval",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, protocol)

    def test_default_path_instructions_name_no_panel_mode(self):
        default_path = [path for path in TEAM.rglob("*.md")
                        if not path.name.startswith("switch-")]
        for path in default_path:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(TEAM).as_posix()):
                self.assertNotIn("review_mode", text)
                self.assertNotRegex(text, r"`(?:single|panel)`\s+(?:review\s+)?mode")

    def test_host_contracts_dispatch_panels_in_parallel_on_the_lens_variants(self):
        contracts = {
            host: " ".join(
                (ROOT / "platforms" / host / "software-engineering-team" / "host-contract.md")
                .read_text(encoding="utf-8").split()
            )
            for host in ("claude", "codex")
        }
        self.assertIn("spawn every reader of the panel in one message", contracts["claude"])
        self.assertIn("on the `lens` tier, `sonnet` at effort `high`", contracts["claude"])
        self.assertIn("start every reader of the panel before waiting on any of them",
                      contracts["codex"])
        self.assertIn("effort `high` and no model", contracts["codex"])
        for host, contract in contracts.items():
            with self.subTest(host=host):
                self.assertIn("Under switch `review_panels` at `lens_panel`", contract)
                self.assertIn("the reviewers themselves keep their own tier", contract)
                self.assertNotIn("tier_overrides", contract)


class LensTierTests(unittest.TestCase):
    def test_canonical_readers_keep_their_released_tier(self):
        agents = {path.stem for path in (TEAM / "agents").glob("*.md")}
        self.assertEqual({agent for agent in agents if tier(agent) == "lens"}, set())
        for agent in (*LENS_VARIANTS, "analysis-challenger", "domain-expert"):
            with self.subTest(agent=agent):
                self.assertEqual(tier(agent), "high")
                self.assertIn("tools: Read, Grep, Glob", read(f"agents/{agent}.md"))

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
