"""The resolver output is a ranked starting point, never a reading obligation."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "software-engineering-team"
TREES = (
    ROOT / "plugins" / PACKAGE,
    ROOT / "dist" / "claude" / PACKAGE,
    ROOT / "dist" / "codex" / PACKAGE,
)
RULE = (
    "Their output (`must_read`, suggested units, continuations, manual reads) "
    "is a ranked starting point, never an obligation."
)
POINTER = "ranked starting point, never a reading obligation"
ADVISORY = {
    "scripts/project_context.py": "Read the selected evidence manually when the task needs it",
    "scripts/delivery_verification.py": "When the task needs this source, read it from the bound candidate",
}
RETIRED = ("owning-task obligation", "before completing its obligation")


class ResolverReadingRuleTest(unittest.TestCase):
    def test_constitution_states_rule_in_source_and_distributions(self) -> None:
        for tree in TREES:
            with self.subTest(tree=tree.relative_to(ROOT)):
                text = (tree / "constitution.md").read_text(encoding="utf-8")
                self.assertIn(RULE, text)
                self.assertIn("never read every listed unit one by one", text)
                self.assertIn("never record an unread suggested unit as a finding", text)

    def test_task_input_contract_points_to_rule(self) -> None:
        for tree in TREES:
            with self.subTest(tree=tree.relative_to(ROOT)):
                text = " ".join((tree / "templates" / "task-input-contract.md")
                                .read_text(encoding="utf-8").split())
                self.assertIn(POINTER, text)

    def test_suggestion_next_actions_are_advisory(self) -> None:
        for tree in TREES:
            for relative, advisory in ADVISORY.items():
                with self.subTest(tree=tree.relative_to(ROOT), script=relative):
                    text = (tree / relative).read_text(encoding="utf-8")
                    self.assertIn(advisory, text)
                    for retired in RETIRED:
                        self.assertNotIn(retired, text)


if __name__ == "__main__":
    unittest.main()
