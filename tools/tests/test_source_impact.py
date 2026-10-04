"""`experience_compile.py source-impact`: which approved Experience packages a
business-analysis change makes stale, whether each is a mechanical or a
semantic rebind (#402), and whether an open rebind changed nothing but the
source receipts, so its snapshot review reads the source delta (#403)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import experience_compile  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

REF = "business-analysis/commerce/space"
OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
RULES = ("---\ntype: rule_set\ntitle: Scoring rules\nstatus: approved\ntags:\n"
         "  - doc/rule-set\n  - status/approved\n---\n\n# Scoring rules\n\n"
         "| id | statement | kind | status | cites |\n|---|---|---|---|---|\n"
         "| BR-SCO-001 | {score} | derivation | active | |\n"
         "| BR-SCO-002 | A lead without a contact is never scored. | constraint | active | |\n")


def space_note(digest: str) -> str:
    return (f"---\ntype: space\ntitle: Commerce\npackage_status: approved\n"
            f"package_hash: {digest}\n---\n\n# Commerce\n")


def experience_note(digest: str, revision: int, status: str = "approved") -> str:
    return ("---\ntype: experience\nexperience_id: leads\norigin_mode: manual\n"
            f"status: {status}\nrevision: {revision}\n"
            "primary_process_ref: business-analysis/commerce/processes/lead-process\n"
            f"input_bindings:\n  - business-analysis|{REF}|{digest}\n"
            "  - solution-design|solution-design/landscape|sha256:" + "c" * 64 + "\n"
            "---\n\n# Leads Experience\n\nLiving process-owned Experience package.\n")


SCREEN = ("---\ntype: experience-screen\nid: SCR-001\nrevision: 1\n---\n\n# Lead list\n\n"
          "Shows each lead with its score, ordered as {cites} defines.\n")


class SourceImpactTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.project = Path(temporary.name).resolve()
        init_repository(self.project, initial_branch="main")
        self.docs = self.project / "workspace/docs"
        self.space = self.docs / "business-analysis/commerce"
        self.root = self.docs / "experience-design"
        self.package = self.root / "experiences/leads"

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> None:
        for argv in (["add", "--all"], ["-c", "user.email=t@example.com", "-c", "user.name=T",
                                        "commit", "-q", "-m", message]):
            subprocess.run(["git", "-C", str(self.project), *argv], check=True,
                           capture_output=True)

    def approved(self, cites: str) -> None:
        """An approved source and an approved Experience that binds it."""
        self.write(self.space / "space.md", space_note(OLD))
        self.write(self.space / "rules/scoring-rules.md",
                   RULES.format(score="The score sums the weighted components."))
        self.write(self.package / "experience.md", experience_note(OLD, 1))
        self.write(self.package / "screens/lead-list-screen.md", SCREEN.format(cites=cites))
        self.write(self.package / "artifacts/list.html", "<main>leads</main>\n")
        self.write(self.root / "artifacts/index.html", "<main>app</main>\n")
        self.commit("approve")

    def revise_source(self) -> None:
        self.write(self.space / "rules/scoring-rules.md", RULES.format(
            score="The score is 0.6 x fit + 0.4 x intent, rounded half up to 1 decimal."))

    def impact(self) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = experience_compile.main(["source-impact", "--root", str(self.root),
                                            "--source-ref", REF])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(len(result["dependents"]), 1, result)
        return result["dependents"][0]

    def test_a_drafted_source_change_no_note_cites_is_a_mechanical_rebind(self):
        self.approved("BR-SCO-002")
        self.revise_source()
        result = self.impact()
        self.assertEqual(result["changed_source_ids"], ["BR-SCO-001"])
        self.assertEqual(result["changed_source_documents"], ["rules/scoring-rules.md"])
        self.assertEqual((result["rebind"], result["package_change"], result["cited_by"]),
                         ("mechanical", "none", []))

    def test_a_note_that_cites_a_changed_row_makes_the_rebind_semantic(self):
        self.approved("BR-SCO-001")
        self.revise_source()
        result = self.impact()
        self.assertEqual(result["rebind"], "semantic")
        self.assertEqual(result["cited_by"], [{"note": "screens/lead-list-screen.md",
                                               "cites": ["BR-SCO-001"]}])

    def test_a_receipt_only_rebind_is_reviewed_against_the_source_delta(self):
        self.approved("BR-SCO-002")
        self.revise_source()
        self.write(self.space / "space.md", space_note(NEW))
        self.commit("approve source revision")
        self.write(self.package / "experience.md", experience_note(NEW, 2, status="in_review"))
        result = self.impact()
        self.assertEqual(result["source_base_commit"], subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD~1"], capture_output=True,
            text=True, check=True).stdout.strip())
        self.assertEqual((result["package_change"], result["review_scope"]),
                         ("source_rebind_only", "source_delta"))

    def test_a_cited_change_or_an_authored_edit_keeps_the_full_review(self):
        for case in ("cited", "note", "artifact", "application"):
            with self.subTest(case=case):
                self.setUp()
                self.approved("BR-SCO-001" if case == "cited" else "BR-SCO-002")
                self.revise_source()
                self.write(self.package / "experience.md",
                           experience_note(NEW, 2, status="in_review"))
                if case == "note":
                    self.write(self.package / "screens/lead-list-screen.md",
                               SCREEN.format(cites="BR-SCO-002") + "Edited.\n")
                if case == "artifact":
                    self.write(self.package / "artifacts/list.html", "<main>edited</main>\n")
                if case == "application":
                    self.write(self.root / "artifacts/new.css", "main {}\n")
                result = self.impact()
                self.assertEqual(result["review_scope"], "full")
                self.assertEqual(result["package_change"],
                                 "source_rebind_only" if case == "cited" else "authored_change")

    def test_a_package_that_binds_another_source_is_not_a_dependent(self):
        self.approved("BR-SCO-002")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = experience_compile.main(["source-impact", "--root", str(self.root),
                                            "--source-ref", "business-analysis/other/space"])
        self.assertEqual((code, json.loads(output.getvalue())["dependents"]), (0, []))


class DependentRebindGateTests(unittest.TestCase):
    REFERENCE = ("skill-content/business-analysis/references/"
                 "switch-dependent_rebind_gate-with_source.md")

    def test_registry_keeps_a_separate_gate_as_default(self):
        import process_policy
        spec = process_policy.load_registry()["dependent_rebind_gate"]
        self.assertEqual((spec["default"], spec["values"]), ("separate", ["separate", "with_source"]))
        self.assertEqual(spec["spec"]["flows"], ["business-analysis", "experience-design"])

    def test_both_owning_flows_name_the_reference(self):
        team = ROOT / "plugins/software-engineering-team"
        for flow in ("business-analysis", "experience-design"):
            with self.subTest(flow=flow):
                text = " ".join((team / "flows" / f"{flow}.md").read_text(encoding="utf-8").split())
                self.assertIn(self.REFERENCE, text)
        text = " ".join((team / self.REFERENCE).read_text(encoding="utf-8").split())
        for phrase in ("`experience_compile.py source-impact --root workspace/docs/experience-design"
                       " --source-ref business-analysis/<space>/space`",
                       "Open the question with one sentence in everyday words",
                       "continue only while it reports `package_change` `source_rebind_only` and"
                       " `rebind` `mechanical`"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
