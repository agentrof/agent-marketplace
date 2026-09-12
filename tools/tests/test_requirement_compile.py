"""Gate 1 contract tests for Requirement identity, impact and approval."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import requirement_compile  # noqa: E402
import requirement_route  # noqa: E402
import vault_check  # noqa: E402


class RequirementCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs / "home.md").write_text("# Home\n", encoding="utf-8")
        (self.root / "workspace" / "config.json").write_text(
            json.dumps({
                "schema_version": 2,
                "team_id": "software-engineering-team",
                "output_language": "English",
                "terminology_language": "English",
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def complete_draft(self) -> Path:
        path = requirement_compile.create_requirement(
            self.docs, "saml-access", "Enterprise SAML access", "feature", "high", None, []
        )
        props, body = requirement_compile.split_note(path)
        body = body.replace(
            "TODO: state the requested change and who needs it.",
            "Allow enterprise users to authenticate through the configured identity provider.",
        ).replace(
            "TODO: state the observable outcome and acceptance boundary.",
            "A valid identity can sign in and an invalid assertion is rejected.",
        ).replace(
            "TODO: define included and excluded behavior.",
            "Include SAML callback validation. Exclude account provisioning.",
        ).replace(
            "TODO: record evidence, constraints and urgency rationale.",
            "The identity provider contract is approved and high urgency is justified by the launch boundary.",
        )
        rows = []
        for stage in requirement_compile.STAGES:
            rows.append(
                f"| {stage} | required |  | The {stage} output constrains this change. |"
            )
        old = "\n".join(
            f"| {stage} | required |  | TODO: explain why this stage must change. |"
            for stage in requirement_compile.STAGES
        )
        body = body.replace(old, "\n".join(rows))
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        requirement_compile.render_navigation(self.docs)
        return path

    def test_init_assigns_stable_id_and_navigation(self):
        path = requirement_compile.create_requirement(
            self.docs, "saml-access", "Enterprise SAML access", "feature", "normal", None, []
        )
        self.assertEqual(path.name, "req-001-saml-access.md")
        self.assertIn("[[maps/requirements|Requirements]]", path.read_text(encoding="utf-8"))
        self.assertIn("req-001-saml-access", (self.docs / "maps/requirements.md").read_text(encoding="utf-8"))

    def test_approval_stamps_hash_and_utc_status(self):
        path = self.complete_draft()
        self.assertEqual(requirement_compile.requirement_findings(path), [])
        requirement_compile.approve_requirement(path)
        props, body = requirement_compile.split_note(path)
        self.assertEqual(props["status"], "approved")
        self.assertTrue(requirement_compile.valid_utc(props["approved_at_utc"]))
        self.assertEqual(props["source_hash"], requirement_compile.semantic_hash(props, body))
        self.assertEqual(requirement_compile.requirement_findings(path, require_approved=True), [])

    def test_semantic_edit_invalidates_approval(self):
        path = self.complete_draft()
        requirement_compile.approve_requirement(path)
        text = path.read_text(encoding="utf-8").replace(
            "Allow enterprise users", "Allow external enterprise users", 1
        )
        path.write_text(text, encoding="utf-8")
        self.assertIn("approved source_hash is stale", requirement_compile.requirement_findings(path))

    def test_route_never_fuzzy_resumes_free_text(self):
        path = self.complete_draft()
        requirement_compile.approve_requirement(path)
        free = requirement_route.route(self.docs, "Enterprise SAML access")
        self.assertEqual(free["mode"], "new")
        exact = requirement_route.route(self.docs, "REQ-001")
        self.assertEqual(exact["mode"], "exact")
        self.assertEqual(exact["requirement_id"], "REQ-001")

    def test_route_exposes_state_valid_actions(self):
        path = self.complete_draft()
        draft = requirement_route.route(self.docs, "REQ-001")
        self.assertIn("discard_draft", draft["actions"])
        self.assertIn("approve", draft["actions"])
        requirement_compile.approve_requirement(path)
        approved = requirement_route.route(self.docs, "REQ-001")
        self.assertIn("resolve_no_change", approved["actions"])
        self.assertIn("supersede", approved["actions"])
        requirement_compile.transition_terminal(
            path, "resolved_no_change", "Already satisfied", []
        )
        terminal = requirement_route.route(self.docs, "REQ-001")
        self.assertEqual(terminal["actions"], ["inspect"])

    def test_discard_removes_only_an_uncommitted_draft(self):
        path = self.complete_draft()
        requirement_compile.discard_requirement(path)
        self.assertFalse(path.exists())
        self.assertNotIn("req-001-saml-access", (self.docs / "maps/requirements.md").read_text(encoding="utf-8"))

    def test_supersede_requires_relation_and_terminalizes_old_record(self):
        old = self.complete_draft()
        requirement_compile.approve_requirement(old)
        replacement = requirement_compile.create_requirement(
            self.docs, "saml-access-v2", "Enterprise SAML access v2", "feature", "high", None, []
        )
        props, body = requirement_compile.split_note(replacement)
        body = body.replace("TODO: state the requested change and who needs it.", "Use the revised SAML contract.")
        body = body.replace("TODO: state the observable outcome and acceptance boundary.", "The revised assertion is accepted.")
        body = body.replace("TODO: define included and excluded behavior.", "Include the revised assertion. Exclude provisioning.")
        body = body.replace("TODO: record evidence, constraints and urgency rationale.", "The revised contract is approved.")
        rows = "\n".join(
            f"| {stage} | required |  | The {stage} output constrains this change. |"
            for stage in requirement_compile.STAGES
        )
        old_rows = "\n".join(
            f"| {stage} | required |  | TODO: explain why this stage must change. |"
            for stage in requirement_compile.STAGES
        )
        body = body.replace(old_rows, rows)
        props["supersedes"] = f"[[requirements/{old.stem}|REQ-001]]"
        replacement.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        requirement_compile.render_navigation(self.docs)
        requirement_compile.supersede_requirement(old, replacement)
        old_props, _ = requirement_compile.split_note(old)
        new_props, _ = requirement_compile.split_note(replacement)
        self.assertEqual(old_props["status"], "superseded")
        self.assertEqual(new_props["status"], "approved")
        self.assertIn("superseded_by", old_props)

    def test_no_change_is_terminal_and_keeps_no_backlog_signal(self):
        path = self.complete_draft()
        requirement_compile.approve_requirement(path)
        requirement_compile.transition_terminal(
            path, "resolved_no_change", "The approved behavior already exists.",
            ["[[issues/identity-contract|Identity contract]]"],
        )
        props, _ = requirement_compile.split_note(path)
        self.assertEqual(props["status"], "resolved_no_change")
        self.assertEqual(requirement_compile.requirement_findings(path, require_approved=True), [])

    def test_no_change_uses_a_kebab_case_status_tag(self):
        path = self.complete_draft()
        requirement_compile.approve_requirement(path)
        requirement_compile.transition_terminal(
            path, "resolved_no_change", "The approved behavior already exists.",
            [],
        )

        props, body = requirement_compile.split_note(path)
        self.assertEqual(
            props["tags"], ["doc/requirement", "status/resolved-no-change"],
        )
        findings = []
        vault_check.check_tags_mirror(
            vault_check.build_vault(self.docs, {}), findings,
        )
        self.assertEqual(findings, [])

        props["tags"] = ["doc/requirement", "status/resolved_no_change"]
        path.write_text(
            requirement_compile.render_note(props, body),
            encoding="utf-8",
        )
        self.assertIn(
            "tags must mirror the Requirement type and status",
            requirement_compile.requirement_findings(path, require_approved=True),
        )

    def test_render_navigation_preserves_stage_receipts_and_is_idempotent(self):
        path = self.complete_draft()
        requirement_compile.approve_requirement(path)
        receipt = {
            "result_ref": "business-analysis/foundation/space",
            "package_hash": "sha256:" + "a" * 64,
        }
        with mock.patch.object(
            requirement_compile.stage_package, "verify", return_value=(receipt, []),
        ):
            requirement_compile.bind_stage(
                path, "business-analysis", "business-analysis/foundation/space",
            )

        before = path.read_text(encoding="utf-8")
        props_before, body_before = requirement_compile.split_note(path)
        self.assertIn("## Stage Results <!-- compiler-owned -->", body_before)
        self.assertTrue(props_before["stage_results_hash"])

        self.assertEqual(requirement_compile.render_navigation(self.docs), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(requirement_compile.render_navigation(self.docs), 0)

        props_after, body_after = requirement_compile.split_note(path)
        self.assertEqual(props_after["source_hash"], props_before["source_hash"])
        self.assertEqual(
            props_after["stage_results_hash"], props_before["stage_results_hash"],
        )
        self.assertEqual(
            requirement_compile.section_text(body_after, "Stage Results"),
            requirement_compile.section_text(body_before, "Stage Results"),
        )
        self.assertEqual(body_after.count("## Navigation "), 1)
        self.assertEqual(
            requirement_compile.requirement_findings(path, require_approved=True), [],
        )

    def test_reuse_stage_accepts_escaped_wikilink_aliases(self):
        path = self.complete_draft()
        props, body = requirement_compile.split_note(path)
        body = body.replace(
            "| business-analysis | required |  | The business-analysis output constrains this change. |",
            "| business-analysis | reuse | [[business-analysis/foundation/space\\|Foundation]] | Existing approved package. |",
        )
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        requirement_compile.approve_requirement(path)
        receipt = {
            "result_ref": "business-analysis/foundation/space",
            "package_hash": "sha256:" + "a" * 64,
        }

        with mock.patch.object(
            requirement_compile.stage_package, "verify", return_value=(receipt, []),
        ) as verify:
            requirement_compile.bind_stage(
                path,
                "business-analysis",
                "[[business-analysis/foundation/space\\|Foundation]]",
            )
        verify.assert_called_once_with(
            path.parents[1],
            "business-analysis",
            "business-analysis/foundation/space",
            "",
            require_committed=True,
        )

        _props, bound_body = requirement_compile.split_note(path)
        self.assertEqual(
            requirement_compile.stage_results(bound_body)["business-analysis"],
            [("business-analysis/foundation/space", "sha256:" + "a" * 64)],
        )


if __name__ == "__main__":
    unittest.main()
