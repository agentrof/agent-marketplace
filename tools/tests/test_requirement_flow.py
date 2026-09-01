import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import requirement_compile
import experience_compile


class RequirementFlowTests(unittest.TestCase):
    @staticmethod
    def approved_requirement_with_experience_reuse(docs: Path) -> Path:
        path = requirement_compile.create_requirement(
            docs, "market", "Market", "feature", "normal", "REQ-001", [],
        )
        props, body = requirement_compile.split_note(path)
        replacements = {
            "TODO: state the requested change and who needs it.":
                "A customer needs a bounded marketplace change.",
            "TODO: state the observable outcome and acceptance boundary.":
                "The outcome has an observable acceptance boundary.",
            "TODO: define included and excluded behavior.":
                "The scope is explicit and exclusions are known.",
            "TODO: record evidence, constraints and urgency rationale.":
                "Evidence and constraints are reviewed.",
            "TODO: explain why this stage must change.":
                "This stage is required by the approved request.",
            "| experience-design | required |  |":
                "| experience-design | reuse | application@r1, checkout@r1 |",
        }
        for old, new in replacements.items():
            body = body.replace(old, new)
        props.update({
            "status": "approved",
            "approved_at_utc": "2026-01-01T00:00:00Z",
            "tags": ["doc/requirement", "status/approved"],
        })
        props["source_hash"] = requirement_compile.semantic_hash(props, body)
        path.write_text(
            requirement_compile.render_note(props, body), encoding="utf-8",
        )
        return path

    def test_required_rows_cannot_carry_reuse_references(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "docs"
            path = requirement_compile.create_requirement(docs, "market", "Market", "feature", "normal", "REQ-001", [])
            props, body = requirement_compile.split_note(path)
            body = body.replace("| business-analysis | required |  |", "| business-analysis | required | [[business-analysis/market/space|Market]] |")
            path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
            self.assertTrue(any("required must have an empty reuse_refs set" in item
                                for item in requirement_compile.requirement_findings(path)))

    def test_semantic_revision_clears_all_stage_results(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "docs"
            path = requirement_compile.create_requirement(docs, "market", "Market", "feature", "normal", "REQ-001", [])
            props, body = requirement_compile.split_note(path)
            body = body.replace("TODO: state the requested change and who needs it.", "A customer needs a bounded marketplace change.")
            body = body.replace("TODO: state the observable outcome and acceptance boundary.", "The outcome has an observable acceptance boundary.")
            body = body.replace("TODO: define included and excluded behavior.", "The scope is explicit and exclusions are known.")
            body = body.replace("TODO: record evidence, constraints and urgency rationale.", "Evidence and constraints are reviewed.")
            body = body.replace("TODO: explain why this stage must change.", "This stage is needed for the approved change.")
            props.update({"status": "approved", "approved_at_utc": "2026-01-01T00:00:00Z"})
            props["source_hash"] = requirement_compile.semantic_hash(props, body)
            props["tags"] = ["doc/requirement", "status/approved"]
            path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
            requirement_compile.begin_revision(path)
            revised, revised_body = requirement_compile.split_note(path)
            self.assertEqual(revised["status"], "draft")
            self.assertFalse(any(reference for reference, _digest in requirement_compile.stage_results(revised_body).values()))

    def test_experience_upstream_hash_excludes_its_own_receipt_set(self):
        upstream = {
            "business-analysis": [("business-analysis/market/space", "sha256:ba")],
            "solution-design": [("solution-design/landscape", "sha256:solution")],
            "design-system": [("design-system/MASTER", "sha256:design")],
        }
        before = experience_compile.requirement_upstream_hash(upstream)
        upstream["experience-design"] = [
            ("application@r1", "sha256:application"),
            ("checkout@r1", "sha256:experience"),
        ]
        self.assertEqual(before, experience_compile.requirement_upstream_hash(upstream))

    def test_revision_can_open_from_ledger_verified_historical_experience_reuse(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "docs"
            path = self.approved_requirement_with_experience_reuse(docs)

            def process_refs(_docs, application_ref, *, allow_historical=False):
                self.assertEqual(application_ref, "application@r1")
                return ["checkout@r1"] if allow_historical else ["checkout@r2"]

            with mock.patch.object(
                requirement_compile.stage_package,
                "experience_application_process_refs",
                side_effect=process_refs,
            ):
                strict = requirement_compile.requirement_findings(
                    path, require_approved=True,
                )
                self.assertTrue(any("experience-design reuse" in item for item in strict))
                requirement_compile.begin_revision(path)

            revised, revised_body = requirement_compile.split_note(path)
            self.assertEqual(revised["status"], "draft")
            self.assertEqual(revised["revision"], 2)
            self.assertEqual(requirement_compile.stage_results(revised_body), {})

    def test_non_experience_reuse_error_does_not_claim_experience_receipts(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "docs"
            path = requirement_compile.create_requirement(
                docs, "market", "Market", "feature", "normal", "REQ-001", [],
            )
            props, body = requirement_compile.split_note(path)
            body = body.replace(
                "| business-analysis | required |  |",
                "| business-analysis | reuse | scope@r1, scope@r2 |",
            )
            path.write_text(
                requirement_compile.render_note(props, body), encoding="utf-8",
            )
            findings = requirement_compile.requirement_findings(path)
            self.assertIn(
                "business-analysis reuse requires exactly one package receipt",
                findings,
            )
            self.assertFalse(any(
                "business-analysis" in item and "Experience application" in item
                for item in findings
            ))


if __name__ == "__main__":
    unittest.main()
