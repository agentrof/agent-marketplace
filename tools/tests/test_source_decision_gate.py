"""Source decision gate: switch `source_decision_gate` keeps the direction
gate and the exact-change gate at `two_gates` and, at
`one_gate_when_drafted`, approves a reviewed exact change in one owner gate,
which `ba_compile.py approve-package --expected-content-hash` holds to the
content the gate showed (#401)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
import process_policy  # noqa: E402
# The package path, as every other suite imports it: a second import under
# another name would replace the compiler module the other suites patch.
from tools.tests.test_ba_compile import make_valid_space, run  # noqa: E402

SWITCH = "source_decision_gate"
REFERENCE = ("skill-content/business-analysis/references/"
             "switch-source_decision_gate-one_gate_when_drafted.md")
ENTITY = "domains/inventory/entities/stock-item-entity.md"


def flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class ContentHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.docs = Path(self.tmp.name) / "docs"
        self.space = self.docs / "business-analysis" / "erp"
        make_valid_space(self.space)
        self.assertTrue((self.space / ENTITY).is_file())

    def cli(self, *argv: str):
        return run([*argv, "--space", str(self.space), "--vault-root", str(self.docs)])

    def content_hash(self) -> str:
        code, out, err = run(["content-hash", "--space", str(self.space)])
        self.assertEqual(code, 0, out + err)
        return json.loads(out)["content_hash"]

    def open_revision(self) -> None:
        code, out, err = self.cli("approve-package")
        self.assertEqual(code, 0, out + err)
        code, out, err = self.cli("begin-revision", "--doc", ENTITY)
        self.assertEqual(code, 0, out + err)

    def edit_and_review(self) -> None:
        target = self.space / ENTITY
        text = target.read_text(encoding="utf-8").replace(
            "owner_role: business_analyst\n",
            "owner_role: business_analyst\ntags:\n  - doc/entity\n  - status/draft\n", 1)
        target.write_text(text.rstrip() + "\n\nReviewed note.\n", encoding="utf-8")
        self.assertEqual(self.cli("render")[0], 0)
        code, out, err = self.cli("enter-review", "--doc", ENTITY)
        self.assertEqual(code, 0, out + err)

    def test_the_reviewed_draft_and_its_approval_share_one_content_hash(self):
        self.open_revision()
        approved_before = self.content_hash()
        self.edit_and_review()
        shown = self.content_hash()
        self.assertNotEqual(shown, approved_before)
        code, out, err = self.cli("approve", "--doc", ENTITY)
        self.assertEqual(code, 0, out + err)
        code, out, err = self.cli("approve-package", "--expected-content-hash", shown)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.content_hash(), shown)
        self.assertNotEqual(json.loads(out.strip().splitlines()[-1])["package_hash"], shown)

    def test_approve_package_refuses_content_the_gate_did_not_show(self):
        self.open_revision()
        self.edit_and_review()
        shown = self.content_hash()
        self.assertEqual(self.cli("approve", "--doc", ENTITY)[0], 0)
        target = self.space / ENTITY
        target.write_text(target.read_text(encoding="utf-8") + "Unshown change.\n",
                          encoding="utf-8")
        root = (self.space / "space.md").read_bytes()
        code, out, err = self.cli("approve-package", "--expected-content-hash", shown)
        self.assertEqual(code, 1, out + err)
        self.assertIn("differs from the content hash the owner approved", err)
        self.assertEqual((self.space / "space.md").read_bytes(), root)

    def test_lifecycle_stamps_leave_the_content_hash_unchanged(self):
        before = self.content_hash()
        self.open_revision()
        self.assertEqual(self.content_hash(), before)


class InstructionTests(unittest.TestCase):
    def test_registry_keeps_two_gates_as_default(self):
        spec = process_policy.load_registry()[SWITCH]
        self.assertEqual((spec["default"], spec["values"]),
                         ("two_gates", ["two_gates", "one_gate_when_drafted"]))
        self.assertEqual(spec["spec"]["flows"], ["backlog-planning", "business-analysis"])

    def test_the_reference_approves_only_shown_content(self):
        text = flat(TEAM / REFERENCE)
        for phrase in ("A choice pick sets a direction only and never approves a write",
                       "needs no fact, number or preference only the owner holds",
                       "`approve-package --expected-content-hash <content_hash>`",
                       "fall back to `two_gates`",
                       "never treat silence or a timeout as approval"):
            self.assertIn(phrase, text)
        for flow in ("business-analysis", "backlog-planning"):
            with self.subTest(flow=flow):
                self.assertIn(REFERENCE, flat(TEAM / "flows" / f"{flow}.md"))


if __name__ == "__main__":
    unittest.main()
