"""The verification reference derives every command from the current engine."""

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import verification_reference as reference


class VerificationReferenceTests(unittest.TestCase):
    def test_every_verb_has_current_arguments_and_a_finite_guard_closure(self):
        data = reference.catalog()
        self.assertIn("freeze", data["commands"])
        freeze = reference.reference(data, "freeze")
        self.assertIn({"flags": ["--delivery"], "required": True}, freeze["arguments"])
        self.assertIn("freeze", freeze["guards_and_outputs"])
        refusals = freeze["guards_and_outputs"]["freeze"]["refusals"]
        self.assertIn("verification command is still running", refusals)
        self.assertIn("candidate", freeze["guards_and_outputs"]["freeze"]["output_keys"])
        for verb in data["commands"]:
            self.assertEqual(reference.reference(data, verb)["verb"], verb)
        changed = Path(reference.engine.__file__).read_text().replace(
            "verification command is still running", "verification command remains active")
        self.assertNotEqual(reference.catalog(changed)["source_hash"], data["source_hash"])
        self.assertIn("verification command remains active",
                      reference.reference(reference.catalog(changed), "freeze")["guards_and_outputs"]["freeze"]["refusals"])
        with self.assertRaisesRegex(ValueError, "unknown verification verb"):
            reference.reference(data, "missing")
        self.assertEqual(reference.message(ast.parse('f"missing {name}"', mode="eval").body), "missing {name}")
