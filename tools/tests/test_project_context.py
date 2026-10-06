"""Real source files and graphs exercise the bounded reading contract."""

from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))

import context_catalog  # noqa: E402
import impact_closure  # noqa: E402
import project_context  # noqa: E402
import vault_query  # noqa: E402
import task_inputs  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402
from tools.tests.test_impact_closure import note  # noqa: E402


class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.docs = self.project / "workspace/docs"
        self.write("backlog/example.md", note("story", "Example task", extra="id: ST-901",
            relations={"constrained_by": [("operation/verification-contract", "Verification")],
                       "derives_from": [("backlog/epic", "Epic")]},
            body="## Scope\n\nShip the example.\n\n## Acceptance\n\nKeep the invariant."))
        self.write("backlog/epic.md", note("epic", "Example epic", relations={
            "related_to": [("backlog/unrelated", "Unrelated")]}))
        self.write("backlog/unrelated.md", note("story", "Unrelated", body="Unrelated content. " * 100))
        self.write("operation/verification-contract.md", note("verification-contract", "Verification",
            body="## Contract\n\nThe invariant is mandatory."))
        self.write("business-analysis/example/domains/orders/rules/orders-rules.md",
            note("rule_set", "Order rules", body="## Rules <!-- sec: rules -->\n\n"
                 "| id | statement | kind | status | cites |\n|---|---|---|---|---|\n"
                 "| BR-ORD-001 | Keep the first rule. | constraint | active | |\n"
                 "| BR-ORD-002 | Keep the second rule. | constraint | active | |"))

    def write(self, relative, text):
        path = self.docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def index(self):
        vault = impact_closure.load_vault(self.docs)
        edges, gaps, _tiers = impact_closure.graph(vault, impact_closure.closure_policy(vault.policy))
        return {"catalog": context_catalog.catalog(vault),
                "edges": [[s, t, key, sorted(tiers)] for (s, t, key), tiers in edges.tiers.items()],
                "files": vault_query.scan_files(self.docs, {}, verify=True), "gaps": gaps}

    def plan(self, **kwargs):
        params = dict(entry="deliver", role="backend-developer", refs=["ST-901"])
        params.update(kwargs)
        return project_context.resolve_context(self.project, self.index(), **params)

    def test_primary_and_constraints_do_not_expand_all_epic_siblings(self):
        plan = self.plan()
        self.assertEqual({row["path"] for row in plan["must_read"]},
                         {"backlog/example.md", "operation/verification-contract.md"})
        self.assertEqual(plan["coverage"]["optional_units"], 1)
        self.assertNotIn("backlog/unrelated.md", json.dumps(plan))
        self.assertFalse(plan["approval_authority"])

    def test_review_purpose_adds_parent_context_without_all_siblings(self):
        plan = self.plan(purpose="review")
        paths = {row["path"] for row in plan["must_read"]}
        self.assertIn("backlog/epic.md", paths)
        self.assertNotIn("backlog/unrelated.md", paths)

    def test_qualified_rule_selects_only_its_row_and_table_header(self):
        data = self.index()
        plan = self.plan(entry="business-analysis", role="business-analyst", refs=["example:BR-ORD-001"])
        self.assertEqual(plan["status"], "ready")
        rows = plan["must_read"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "row")
        result = context_catalog.read_units(self.docs, data["catalog"], [rows[0]["unit_id"]], 10000)
        text = result["units"][0]["text"]
        self.assertIn("| id | statement |", text)
        self.assertIn("## Rules", text)
        self.assertIn("BR-ORD-001", text)
        self.assertNotIn("BR-ORD-002", text)
        self.assertNotIn("references", result["units"][0])

    def test_duplicate_bare_id_is_not_silently_resolved(self):
        rule = (self.docs / "business-analysis/example/domains/orders/rules/orders-rules.md").read_text()
        self.write("business-analysis/another/domains/orders/rules/orders-rules.md", rule)
        with self.assertRaisesRegex(ValueError, "2 matches"):
            self.plan(refs=["BR-ORD-001"])
        self.assertEqual(len(self.plan(refs=["another:BR-ORD-001"])["must_read"]), 1)

    def test_full_document_subsumes_a_requested_section(self):
        plan = self.plan(refs=["ST-901", "backlog/example.md::section:Scope"])
        self.assertEqual(sum(row["path"] == "backlog/example.md" for row in plan["must_read"]), 1)

    def test_file_budget_has_an_explicit_bound_continuation(self):
        data = self.index()
        plan = self.plan(budget={"max_files": 1})
        self.assertEqual(plan["status"], "needs_split")
        self.assertEqual(len(plan["must_read"]), 1)
        second = project_context.expand_context(self.project, data, plan, reason="Read remaining constraint")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(second["must_read"][0]["path"], "operation/verification-contract.md")
        self.assertEqual(second["coverage"]["required_units"], 2)

    def test_oversized_required_source_is_not_dropped_or_truncated(self):
        plan = self.plan(budget={"max_source_bytes": 1})
        self.assertEqual(plan["status"], "needs_split")
        self.assertEqual(plan["must_read"], [])
        self.assertGreater(plan["oversized"]["available_smaller_units"], 0)
        self.assertEqual(plan["coverage"]["remaining_required_units"], 2)

    def test_read_cli_preserves_incomplete_plan_status(self):
        plan = project_context.resolve_context(self.project, project_context.load_index(self.project),
            entry="deliver", role="backend-developer", refs=["ST-901"], budget={"max_source_bytes": 1})
        path = self.project / "plan.json"
        path.write_text(json.dumps(plan))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = project_context.main(["--project-root", str(self.project), "read", "--plan", str(path)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "needs_split")
        self.assertEqual(result["coverage"]["remaining_required_units"], 2)

    def test_invalid_or_too_small_budget_is_refused(self):
        for budget in ({"max_files": 0}, {"unknown": 1}, {"max_files": True}, {"max_metadata_bytes": 1}):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.plan(budget=budget)

    def test_successful_metadata_includes_its_hash_within_budget(self):
        self.assertEqual(self.plan(budget={"max_metadata_bytes": 8000})["status"], "ready")
        for limit in (1800, 3000, 8000):
            try:
                plan = self.plan(budget={"max_metadata_bytes": limit})
            except ValueError:
                continue
            self.assertLessEqual(len(project_context.encoded(plan)), limit)

    def test_unknown_reference_and_wrong_role_are_refused(self):
        with self.assertRaisesRegex(ValueError, "0 matches"):
            self.plan(refs=["unknown"])
        with self.assertRaisesRegex(ValueError, "role"):
            self.plan(entry="business-analysis", role="backend-developer")

    def test_changed_source_rejects_old_plan_even_with_old_index(self):
        data = self.index()
        plan = self.plan()
        path = self.docs / "backlog/example.md"
        path.write_text(path.read_text().replace("Ship", "Send"))
        with self.assertRaisesRegex(ValueError, "stale"):
            project_context.validate_plan(self.project, data, plan)

    def test_changed_relations_reject_expansion(self):
        plan = self.plan(budget={"max_files": 1})
        self.write("backlog/new.md", note("story", "New task", relations={
            "depends_on": [("backlog/example", "Example")]}))
        with self.assertRaisesRegex(ValueError, "stale"):
            project_context.expand_context(self.project, self.index(), plan, reason="Continue")

    def test_edited_plan_and_missing_expansion_reason_are_refused(self):
        data = self.index()
        plan = self.plan()
        modified = copy.deepcopy(plan)
        modified["must_read"] = []
        with self.assertRaisesRegex(ValueError, "modified"):
            project_context.validate_plan(self.project, data, modified)
        with self.assertRaisesRegex(ValueError, "reason"):
            project_context.expand_context(self.project, data, plan, reason="")

    def test_irrelevant_gaps_are_not_returned_as_required_sources(self):
        plan = self.plan()
        self.assertTrue(all(row.get("path") != "backlog/unrelated.md" for row in plan["gaps"]))

    def test_generated_views_are_not_source_units(self):
        self.write("backlog/_generated/report.md", "<!-- generated by tool -->\n# Example\n" * 100)
        self.assertNotIn("backlog/_generated/report.md", self.index()["catalog"]["documents"])

    def test_compiler_receipt_changes_reach_the_bound_document(self):
        relative = "experience-design/_generated/application-registry.json"
        self.write(relative, json.dumps({"application_revision": 1, "application_hash": "sha256:" + "a" * 64}))
        self.write("backlog/bound.md", note("backlog", "Bound package", extra=
            'input_bindings:\n  - "experience-design|application@r1|sha256:' + 'a' * 64 + '"'))
        vault = impact_closure.load_vault(self.docs)
        snapshot = impact_closure.snapshot(vault, proofs={})
        result = impact_closure.closure_from(snapshot, [relative], impact_closure.closure_policy(vault.policy))
        self.assertIn("backlog/bound.md", result["closure"])

    def test_long_acceptance_sections_offer_individual_items(self):
        self.write("backlog/large.md", note("story", "Large task", extra="id: ST-902",
            body="## Acceptance\n\n- [ ] First requirement.\n- [ ] Second requirement.\n"))
        data = self.index()["catalog"]
        items = [data["units"][unit] for unit in data["documents"]["backlog/large.md"]["units"]
                 if data["units"][unit]["kind"] == "item"]
        self.assertEqual(len(items), 2)
        first = context_catalog.read_units(self.docs, data, [items[0]["unit_id"]], 1000)
        self.assertIn("First requirement", first["units"][0]["text"])
        self.assertNotIn("Second requirement", first["units"][0]["text"])
        self.assertIn("## Acceptance", first["units"][0]["text"])

    def test_component_ids_and_exact_architecture_revisions(self):
        self.write("solution-design/components/worker/component.md", note("solution-component", "Worker",
            extra="component_id: worker"))
        self.write("system-architecture/decisions/example.md", note("decision", "Architecture decision",
            extra="record_id: ADR-901\nrevision: 2\nrecord_state: active\nrevision_state: sealed"))
        self.write("system-architecture/_ledger/records/ADR-901/r1.json", json.dumps({
            "exact_ref": "ARC:ROOT:ADR-901@r1", "content": "Earlier decision", "revision": 1}))
        data = self.index()["catalog"]
        self.assertEqual(context_catalog.resolve(data, "worker")[0]["path"],
                         "solution-design/components/worker/component.md")
        old = context_catalog.resolve(data, "ARC:ROOT:ADR-901@r1")[0]
        current = context_catalog.resolve(data, "ARC:ROOT:ADR-901@r2")[0]
        self.assertTrue(old["historical"])
        self.assertNotEqual(old["path"], current["path"])
        read = context_catalog.read_units(self.docs, data, [old["unit_id"]], 10000)
        self.assertIn("Earlier decision", read["units"][0]["text"])

    def test_memory_is_explicit_and_does_not_modify_project_sources(self):
        path = self.project / "workspace/memory/current-plan.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Current plan\n\nThe selected work is pending.\n")
        before = {str(p): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        data = self.index()
        plan = self.plan(refs=["workspace/memory/current-plan.md"])
        self.assertEqual(plan["must_read"][0]["authority"], "owner_context")
        project_context.external_sources(self.project, data["catalog"], plan["request"]["refs"],
                                         json.loads(project_context.POLICY.read_text()))
        read = context_catalog.read_units(self.docs, data["catalog"],
                                         [plan["must_read"][0]["unit_id"]], 10000)
        self.assertIn("pending", read["units"][0]["text"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})

    def test_external_source_cannot_escape_its_project(self):
        with self.assertRaises(ValueError):
            self.plan(refs=["workspace/memory/../../outside.md"])

    def test_external_link_cannot_read_another_source_root(self):
        memory = self.project / "workspace/memory"
        memory.mkdir()
        foreign = self.project / "unlisted.md"
        foreign.write_text("Outside the declared source root.")
        (memory / "linked.md").symlink_to(foreign)
        with self.assertRaisesRegex(ValueError, "declared source root"):
            self.plan(refs=["workspace/memory/linked.md"])

    def test_byte_budget_applies_to_actual_read(self):
        data = self.index()["catalog"]
        unit = context_catalog.resolve(data, "example:BR-ORD-001")[0]
        result = context_catalog.read_units(self.docs, data, [unit["unit_id"]], 1)
        self.assertEqual(result["status"], "needs_split")
        self.assertNotIn("units", result)

    def test_expansion_does_not_repeat_previously_returned_units(self):
        data = self.index()
        first = self.plan()
        second = project_context.expand_context(self.project, data, first,
            refs=["backlog/epic.md"], reason="Inspect parent scope")
        old = {row["unit_id"] for row in first["must_read"]}
        self.assertFalse(old & {row["unit_id"] for row in second["must_read"]})
        self.assertEqual(second["coverage"]["previously_returned_units"], 2)

    def test_row_citations_keep_the_exact_constraint_record(self):
        path = self.docs / "business-analysis/example/domains/orders/rules/orders-rules.md"
        target = path.relative_to(self.docs).as_posix().removesuffix(".md")
        path.write_text(path.read_text().replace(
            "first rule. | constraint | active | |",
            f"first rule. | constraint | active | [[{target}\\|example:BR-ORD-002]] |"))
        plan = self.plan(refs=["example:BR-ORD-001"])
        self.assertEqual({row["label"] for row in plan["must_read"]}, {"BR-ORD-001", "BR-ORD-002"})

    def test_missing_required_reference_never_reports_ready(self):
        path = self.docs / "backlog/example.md"
        path.write_text(path.read_text().replace("operation/verification-contract", "operation/missing"))
        plan = self.plan()
        self.assertEqual(plan["status"], "needs_resolution")
        self.assertGreater(plan["unresolved_required_count"], 0)

    def test_task_binds_plan_and_preserves_instruction_selection(self):
        init_repository(self.project)
        (self.project / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}))
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)
        relative = ".agentrof/agent-marketplace/.runtime/reading.json"
        path = self.project / relative
        path.parent.mkdir(parents=True)
        # Use the production loader so the plan includes its builder identity.
        plan = project_context.resolve_context(self.project, project_context.load_index(self.project),
            entry="deliver", role="backend-developer", refs=["ST-901"])
        path.write_text(json.dumps(plan))
        arguments = dict(project=self.project, entry="deliver", role="backend-developer", mode="consume")
        plain = task_inputs.manifest(**arguments)
        bound = task_inputs.manifest(**arguments, context_plan=relative)
        self.assertEqual(bound["required_reads"], plain["required_reads"])
        self.assertEqual(bound["project_reading"]["plan_hash"], plan["plan_hash"])
        self.assertIn("workspace/docs/backlog/example.md", {r["path"] for r in bound["project_inputs"]})
        (self.docs / "backlog/example.md").write_text(note("story", "Changed task", extra="id: ST-901"))
        with self.assertRaisesRegex(ValueError, "stale"):
            task_inputs.manifest(**arguments, context_plan=relative)

    def test_pinned_contract_uses_the_matching_historical_bytes(self):
        import operation_compile
        import context_history
        import ba_compile
        contract = self.docs / "operation/verification-contract.md"
        text = note("verification-contract", "Verification", body="Earlier approved behavior.",
                    extra="revision: 1").replace("status: draft", "status: approved")
        props, start, _error = ba_compile.parse_frontmatter(text)
        digest = operation_compile.source_hash(props, "\n".join(text.splitlines()[start - 1:]))
        contract.write_text(text.replace("revision: 1", f"revision: 1\nsource_hash: {digest}"))
        init_repository(self.project)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)
        self.write("backlog/pinned.md", note("delivery-item", "Pinned work", extra=
            f"id: ITEM-901\nverification_contract_ref: operation/verification-contract.md\n"
            f"verification_contract_hash: {digest}"))
        contract.write_text(note("verification-contract", "Verification", body="Different current behavior."))
        plan = self.plan(refs=["ITEM-901"])
        historic = next(row for row in plan["must_read"] if row.get("historical"))
        self.assertEqual(historic["bound_hash"], digest)
        self.assertEqual(historic["source_state"]["revision"], 1)
        raw = context_history.git_source(self.project, "workspace/docs/" + historic["path"], historic["git_revision"])
        self.assertIn(b"Earlier approved behavior", raw)
        self.assertNotIn(b"Different current behavior", raw)
        data = self.index()["catalog"]
        data["units"][historic["unit_id"]] = historic
        result = context_catalog.read_units(self.docs, data, [historic["unit_id"]], 10000)
        self.assertIn("Earlier approved behavior", result["units"][0]["text"])


if __name__ == "__main__":
    unittest.main()
