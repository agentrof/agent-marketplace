"""Validator test suite: the guard that guards the guard.

Contract per fixture: planting exactly one defect on a valid tree yields
exactly one finding, of the matching check, with a non-empty remediation.
The meta-test locks the check registry to the fixture registry.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import fixtures  # noqa: E402
import validate  # noqa: E402


class ValidatorFixtureTests(unittest.TestCase):
    def run_on(self, build_defect=None, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.make_valid_root(root)
            if build_defect:
                build_defect(root)
            if extra:
                extra(root)
            return validate.run(root)

    def test_valid_root_is_clean(self):
        findings = self.run_on()
        self.assertEqual(findings, [], f"valid root must be clean, got: {findings}")

    def test_meta_registry_lockstep(self):
        """A check without a fixture (or vice versa) cannot merge."""
        self.assertEqual(
            set(validate.CHECKS.keys()),
            set(fixtures.BUILDERS.keys()),
            "validator checks and fixture builders must match one-to-one",
        )

    def test_each_fixture_yields_exactly_one_matching_finding(self):
        for check_id, builder in sorted(fixtures.BUILDERS.items()):
            with self.subTest(check=check_id):
                findings = self.run_on(builder)
                self.assertEqual(
                    len(findings), 1,
                    f"{check_id}: expected exactly one finding, got {findings}",
                )
                finding = findings[0]
                self.assertEqual(finding.check, check_id)
                self.assertTrue(
                    finding.remediation.strip(),
                    f"{check_id}: remediation must be non-empty",
                )

    def test_out_of_scope_violations_are_not_caught(self):
        findings = self.run_on(extra=fixtures.plant_out_of_scope_violations)
        self.assertEqual(
            findings, [],
            "violations under assets/ and memory/ must never be flagged",
        )

    def test_output_is_deterministic(self):
        def all_defects(root: Path) -> None:
            for builder in fixtures.BUILDERS.values():
                builder(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.make_valid_root(root)
            all_defects(root)
            first = validate.run(root)
            second = validate.run(root)
            self.assertEqual(first, second, "two runs must be byte-identical")
            self.assertGreater(len(first), 0)


if __name__ == "__main__":
    unittest.main()
