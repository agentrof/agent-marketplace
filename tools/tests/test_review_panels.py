"""Review panels: lens data per step, one protocol that a task binds only at
switch `review_panels: lens_panel`, and `-lens` reader variants on the `low`
tier that leave the single reviewers and every default-path instruction as
released."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
PANELS = "skill-content/challenge-review/data/review-panels.json"
LENS_VARIANTS = ["backlog-reviewer", "design-system-reviewer", "solution-reviewer"]


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def tier(agent: str) -> str:
    match = re.search(r"^reasoning: (\S+)$", read(f"agents/{agent}.md"), re.MULTILINE)
    assert match, agent
    return match.group(1)


class ReviewPanelFlowTests(unittest.TestCase):
    def test_solution_panel_lenses_are_the_primary_reviewer_lenses(self):
        plan = read("skill-content/solution-architecture/references/challenge-lenses.md")
        primary = plan.split("## Required primary lenses", 1)[1].split("## Targeted specialists", 1)[0]
        self.assertEqual(
            re.findall(r"^- \*\*([^:]+):\*\*", primary, re.MULTILINE),
            [lens["id"] for lens in json.loads(read(PANELS))["review_steps"]["solution_design"]["lenses"]],
        )


class LensVariantTierTests(unittest.TestCase):
    def test_canonical_readers_keep_their_released_tier(self):
        agents = {path.stem for path in (TEAM / "agents").glob("*.md")}
        self.assertEqual({agent for agent in agents if tier(agent) in {"lens", "low"}}, set())
        for agent in (*LENS_VARIANTS, "analysis-challenger", "domain-expert"):
            with self.subTest(agent=agent):
                self.assertEqual(tier(agent), "high")
                self.assertIn("tools: Read, Grep, Glob", read(f"agents/{agent}.md"))


if __name__ == "__main__":
    unittest.main()
