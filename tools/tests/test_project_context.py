"""Real source files and graphs exercise the bounded reading contract."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import errno
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from tools.tests.levels import integration
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))

import context_catalog  # noqa: E402
import context_history  # noqa: E402
import operation_compile  # noqa: E402
import requirement_compile  # noqa: E402
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

    def approved_contract(self, kind, *, leading=2, newline="\n", legacy=False):
        path = self.docs / "operation" / (kind + ".md")
        text = note(kind, "Approved contract", body="The condition is mandatory.",
            extra="revision: 1").replace("status: draft", "status: approved")
        text = text.replace("---\n\n#", "---\n" + "\n" * leading + "#", 1)
        props, body = operation_compile.parse_text(text, path)
        expected = (operation_compile._source_hash(props, body.rstrip() + "\n") if legacy
                    else operation_compile.source_hash(props, body))
        text = text.replace("revision: 1", f"revision: 1\nsource_hash: {expected}")
        raw = text.replace("\n", newline).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path, raw, expected

    def test_bound_operation_hash_uses_owner_parser_across_blank_and_newline_modes(self):
        for kind in ("verification-contract", "environment-contract"):
            for leading in (0, 1, 3):
                for newline in ("\n", "\r\n"):
                    for legacy in (False, True):
                        with self.subTest(kind=kind, leading=leading, newline=newline, legacy=legacy):
                            path, raw, expected = self.approved_contract(kind, leading=leading,
                                newline=newline, legacy=legacy)
                            props, body = operation_compile.parse_text(raw.decode(), path)
                            self.assertEqual(operation_compile.receipt_hash(props, body), expected)
                            self.assertTrue(context_history.matches(path, raw, expected))
                            self.assertFalse(context_history.matches(path, raw.replace(
                                b"mandatory", b"optional"), expected))
                            self.assertFalse(context_history.matches(path, raw, "sha256:" + "0" * 64))
                            self.assertFalse(context_history.matches(path, raw.replace(
                                expected.encode(), ("sha256:" + "0" * 64).encode()), expected))

    def test_bound_requirement_hash_matches_the_owning_body_parser(self):
        path = self.docs / "requirements/selected.md"
        path.parent.mkdir(parents=True)
        for leading in (0, 1, 3):
            for newline in ("\n", "\r\n"):
                with self.subTest(leading=leading, newline=newline):
                    text = note("requirement", "Selected requirement", body="Preserve the required outcome.")
                    text = text.replace("---\n\n#", "---\n" + "\n" * leading + "#", 1)
                    path.write_bytes(text.replace("\n", newline).encode())
                    props, body = requirement_compile.split_note(path)
                    expected = requirement_compile.semantic_hash(props, body)
                    raw = text.replace("status: draft", f"status: draft\nsource_hash: {expected}").replace("\n", newline).encode()
                    self.assertTrue(context_history.matches(path, raw, expected))
                    self.assertFalse(context_history.matches(path, raw.replace(b"required", b"altered"), expected))
                    self.assertFalse(context_history.matches(path, raw, "sha256:" + "0" * 64))
                    self.assertFalse(context_history.matches(path, raw.replace(
                        expected.encode(), ("sha256:" + "0" * 64).encode()), expected))

    def test_current_operation_bindings_resolve_and_read_exact_source_bytes(self):
        for kind in ("verification-contract", "environment-contract"):
            with self.subTest(kind=kind):
                path, raw, expected = self.approved_contract(kind, leading=3, newline="\r\n", legacy=True)
                stem = kind.replace("-", "_")
                self.write("backlog/pinned-current.md", note("delivery-item", "Selected current binding",
                    extra=f"id: ITEM-902\n{stem}_ref: operation/{kind}.md\n{stem}_hash: {expected}"))
                data = self.index()
                plan = project_context.resolve_context(self.project, data, entry="deliver",
                    role="backend-developer", refs=["ITEM-902"])
                self.assertEqual(plan["unresolved_required_count"], 0)
                self.assertEqual(plan["status"], "ready")
                self.assertEqual(context_history.bound_source(self.project, path.relative_to(self.docs).as_posix(), expected),
                                 {"current": True})
                rows = project_context.read_plan(self.project, data, plan)["units"]
                selected = next(row for row in rows if row["path"] == path.relative_to(self.docs).as_posix())
                self.assertNotIn("historical", selected)
                self.assertEqual(selected["text"].encode(), raw)

    def test_historical_operation_bindings_use_owner_normalization_and_exact_git_bytes(self):
        revision = "a" * 40
        for kind in ("verification-contract", "environment-contract"):
            for newline in ("\n", "\r\n"):
                for legacy in (False, True):
                    with self.subTest(kind=kind, newline=newline, legacy=legacy):
                        path, raw, expected = self.approved_contract(kind, leading=3, newline=newline, legacy=legacy)
                        relative = path.relative_to(self.docs).as_posix()
                        path.write_bytes(raw.replace(b"mandatory", b"changed"))
                        stem = kind.replace("-", "_")
                        self.write("backlog/pinned-historical.md", note("delivery-item", "Selected historical binding",
                            extra=f"id: ITEM-903\n{stem}_ref: {relative}\n{stem}_hash: {expected}"))

                        def transport(command, **_kwargs):
                            if command[-2:] == ["rev-parse", "--show-toplevel"]:
                                return subprocess.CompletedProcess(command, 0, str(self.project).encode() + b"\n", b"")
                            if command[-3:] == ["rev-parse", "--verify", "HEAD^{commit}"]:
                                return subprocess.CompletedProcess(command, 0, ("b" * 40).encode() + b"\n", b"")
                            if "log" in command:
                                return subprocess.CompletedProcess(command, 0, revision.encode() + b"\n", b"")
                            if command[-2:] == ["show", f"{revision}:workspace/docs/{relative}"]:
                                return subprocess.CompletedProcess(command, 0, raw, b"")
                            raise AssertionError("unexpected Git transport")

                        with mock.patch.object(context_history.subprocess, "run", side_effect=transport):
                            data = self.index()
                            plan = project_context.resolve_context(self.project, data, entry="deliver",
                                role="backend-developer", refs=["ITEM-903"])
                            self.assertEqual(plan["unresolved_required_count"], 0)
                            selected = next(row for row in project_context.read_plan(self.project, data, plan)["units"]
                                            if row.get("historical"))
                            self.assertEqual(selected["bound_hash"], expected)
                            self.assertEqual(selected["git_revision"], revision)
                            self.assertEqual(selected["text"].encode(), raw)
                            with mock.patch.object(context_history, "matches", wraps=context_history.matches) as verify:
                                self.assertIsNone(context_history.bound_source(self.project, relative, "sha256:" + "0" * 64))
                            self.assertTrue(verify.called)

    def history_cache_fixture(self):
        path, raw, expected = self.approved_contract("verification-contract", leading=3)
        relative = path.relative_to(self.docs).as_posix()
        path.write_bytes(raw.replace(b"mandatory", b"changed"))
        state = {"head": "b" * 40, "raw": raw, "missing": False, "logs": 0, "shows": 0}

        def transport(command, **_kwargs):
            root = command[3]
            if command[-2:] == ["rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(command, 0, root.encode() + b"\n", b"")
            if command[-3:] == ["rev-parse", "--verify", "HEAD^{commit}"]:
                return subprocess.CompletedProcess(command, 0, state["head"].encode() + b"\n", b"")
            if "log" in command:
                state["logs"] += 1
                return subprocess.CompletedProcess(command, 0, ("a" * 40).encode() + b"\n", b"")
            if command[-2:] == ["show", f"{'a' * 40}:workspace/docs/{relative}"]:
                state["shows"] += 1
                return subprocess.CompletedProcess(command, int(state["missing"]),
                    b"" if state["missing"] else state["raw"], b"")
            raise AssertionError("unexpected Git transport")

        return path, relative, expected, state, transport

    def test_positive_history_cache_rechecks_bytes_and_preserves_current_preference(self):
        path, relative, expected, state, transport = self.history_cache_fixture()
        with mock.patch.object(context_history.subprocess, "run", side_effect=transport):
            first = context_history.bound_source(self.project, relative, expected)
            self.assertFalse(first["current"])
            self.assertEqual(state["logs"], 1)
            first["historical_properties"]["title"] = "Mutated caller copy"
            second = context_history.bound_source(self.project, relative, expected)
            self.assertEqual(state["logs"], 1)
            self.assertEqual(state["shows"], 2)
            self.assertNotEqual(second["historical_properties"]["title"], "Mutated caller copy")
            path.write_bytes(state["raw"])
            self.assertEqual(context_history.bound_source(self.project, relative, expected), {"current": True})
            self.assertEqual(state["shows"], 2)

    def test_history_cache_does_not_keep_missing_tampered_head_or_foreign_root_results(self):
        path, relative, expected, state, transport = self.history_cache_fixture()
        with mock.patch.object(context_history.subprocess, "run", side_effect=transport):
            self.assertIsNotNone(context_history.bound_source(self.project, relative, expected))
            original = state["raw"]
            state["raw"] = original.replace(b"mandatory", b"tampered")
            self.assertIsNone(context_history.bound_source(self.project, relative, expected))
            state["raw"] = original
            self.assertIsNotNone(context_history.bound_source(self.project, relative, expected))
            before = state["logs"]
            state["head"] = "c" * 40
            self.assertIsNotNone(context_history.bound_source(self.project, relative, expected))
            self.assertEqual(state["logs"], before + 1)
            other = self.project / "other-root"
            other_path = other / "workspace/docs" / relative
            other_path.parent.mkdir(parents=True)
            other_path.write_bytes(path.read_bytes())
            self.assertIsNotNone(context_history.bound_source(other, relative, expected))
            self.assertEqual(state["logs"], before + 2)
            state["missing"] = True
            self.assertIsNone(context_history.bound_source(other, relative, expected))
            after_missing = state["logs"]
            state["missing"] = False
            self.assertIsNotNone(context_history.bound_source(other, relative, expected))
            self.assertEqual(state["logs"], after_missing + 1)
            self.assertIsNone(context_history.bound_source(self.project, relative, "sha256:" + "0" * 64))

    def test_positive_history_cache_obeys_serialized_byte_policy(self):
        _path, relative, expected, state, transport = self.history_cache_fixture()
        with mock.patch.object(context_history.subprocess, "run", side_effect=transport), \
                mock.patch.object(context_history, "history_limit", return_value=1):
            self.assertIsNotNone(context_history.bound_source(self.project, relative, expected))
            self.assertIsNotNone(context_history.bound_source(self.project, relative, expected))
            self.assertEqual(state["logs"], 2)
            self.assertLessEqual(context_history._HISTORY_CACHE_BYTES, 1)

    def test_history_cache_search_is_pinned_across_interleaved_head_change(self):
        _path, relative, expected, state, transport = self.history_cache_fixture()
        captured = "b" * 40
        switched = False
        logs = []

        def interleaved(command, **kwargs):
            nonlocal switched
            if command[-3:] == ["rev-parse", "--verify", "HEAD^{commit}"] and not switched:
                switched = True
                state["head"] = "c" * 40
                return subprocess.CompletedProcess(command, 0, captured.encode() + b"\n", b"")
            if "log" in command:
                logs.append(command)
                if captured in command:
                    return subprocess.CompletedProcess(command, 0, b"", b"")
            return transport(command, **kwargs)

        with mock.patch.object(context_history.subprocess, "run", side_effect=interleaved):
            self.assertIsNone(context_history.bound_source(self.project, relative, expected))
            self.assertIn(captured, logs[0])
            self.assertNotIn((str(self.project), relative, expected, captured), context_history._HISTORY_CACHE)
            state["head"] = captured
            self.assertIsNone(context_history.bound_source(self.project, relative, expected))
            self.assertEqual(len(logs), 2)
            state["head"] = "invalid-head"
            self.assertIsNone(context_history.bound_source(self.project, relative, expected))
            self.assertEqual(len(logs), 2)

    def test_exact_byte_budget_keeps_row_integrity_and_one_less_pages_it(self):
        data = self.index()
        parent = context_catalog.resolve(data["catalog"], "example:BR-ORD-001")[0]
        params = dict(entry="business-analysis", role="business-analyst", refs=["example:BR-ORD-001"])
        whole = project_context.resolve_context(self.project, data, **params,
            budget={"max_source_bytes": parent["bytes"]})
        self.assertEqual(whole["must_read"][0]["unit_id"], parent["unit_id"])
        self.assertEqual(whole["status"], "ready")
        expected = project_context.read_plan(self.project, data, whole)["units"][0]["text"]
        first = project_context.resolve_context(self.project, data, **params,
            budget={"max_source_bytes": parent["bytes"] - 1})
        self.assertEqual(first["must_read"][0]["ranges"], parent["ranges"])
        self.assertEqual(first["coverage"]["returned_units"], 0)
        second = project_context.expand_context(self.project, data, first, reason="Finish the row")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(second["coverage"]["returned_units"], 1)
        self.assertEqual("".join(project_context.read_plan(self.project, data, page)["units"][0]["text"]
                                 for page in (first, second)), expected)

    def test_duplicate_bare_id_is_not_silently_resolved(self):
        rule = (self.docs / "business-analysis/example/domains/orders/rules/orders-rules.md").read_text()
        self.write("business-analysis/another/domains/orders/rules/orders-rules.md", rule)
        with self.assertRaisesRegex(ValueError, "2 matches"):
            self.plan(refs=["BR-ORD-001"])
        self.assertEqual(len(self.plan(refs=["another:BR-ORD-001"])["must_read"]), 1)

    def mirrored_decision(self, path="system-architecture/decisions/selected.md", *, rows=None):
        rows = rows or [("DEC-TEST-001", "active")]
        body = ("## Decisions\n\n| id | statement | status |\n|---|---|---|\n" +
                "".join(f"| {identity} | Preserve the decision. | {status} |\n" for identity, status in rows) +
                "\n## Conditions\n\nPreserve the additional condition.\n")
        self.write(path, note("decision", "Selected decision", extra="aliases:\n  - DEC-TEST-001", body=body))
        return path

    def test_declared_document_record_mirror_resolves_bare_and_qualified_to_whole_source(self):
        path = self.mirrored_decision()
        data = self.index()["catalog"]
        for reference in ("DEC-TEST-001", f"[[{path.removesuffix('.md')}|DEC-TEST-001]]"):
            with self.subTest(reference=reference):
                hits = context_catalog.resolve(data, reference)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["kind"], "document")
                plan = self.plan(refs=[reference])
                text = context_catalog.read_units(self.docs, data,
                    [plan["must_read"][0]["unit_id"]], 10000)["units"][0]["text"]
                self.assertIn("Preserve the additional condition.", text)
        row = next(unit for unit in data["units"].values() if unit["path"] == path and unit["kind"] == "row")
        self.assertEqual(context_catalog.resolve(data, row["unit_id"]), [row])
        text = context_catalog.read_units(self.docs, data, [row["unit_id"]], 10000)["units"][0]["text"]
        self.assertNotIn("Preserve the additional condition.", text)

    def test_document_record_mirror_keeps_cross_document_ambiguity(self):
        first = self.mirrored_decision()
        second = self.mirrored_decision("system-architecture/decisions/other.md")
        data = self.index()["catalog"]
        hits = context_catalog.resolve(data, "DEC-TEST-001")
        self.assertEqual({unit["path"] for unit in hits}, {first, second})
        self.assertEqual(len(hits), 2)
        with self.assertRaisesRegex(ValueError, "2 matches"):
            self.plan(refs=["DEC-TEST-001"])
        for path in (first, second):
            hit = context_catalog.resolve(data, f"[[{path.removesuffix('.md')}|DEC-TEST-001]]")
            self.assertEqual(len(hit), 1)
            self.assertEqual(hit[0]["kind"], "document")

    def test_document_record_mirror_keeps_duplicate_and_inactive_row_ambiguity(self):
        for rows in ([('DEC-TEST-001', 'active'), ('DEC-TEST-001', 'active')],
                     [('DEC-TEST-001', 'inactive')], [('DEC-TEST-001', 'historical')]):
            with self.subTest(rows=rows):
                path = self.mirrored_decision(rows=rows)
                data = self.index()["catalog"]
                hits = context_catalog.resolve(data, f"[[{path.removesuffix('.md')}|DEC-TEST-001]]")
                self.assertEqual(len(hits), 1 + len(rows))
                with self.assertRaisesRegex(ValueError, "matches"):
                    self.plan(refs=[f"[[{path.removesuffix('.md')}|DEC-TEST-001]]"])

    def test_document_record_mirror_leaves_distinct_rows_and_explicit_section_addresses(self):
        path = self.mirrored_decision(rows=[("DEC-TEST-001", "active"), ("DEC-TEST-002", "active")])
        data = self.index()["catalog"]
        self.assertEqual(context_catalog.resolve(data, "DEC-TEST-001")[0]["kind"], "document")
        distinct = context_catalog.resolve(data, "DEC-TEST-002")
        self.assertEqual(len(distinct), 1)
        self.assertEqual(distinct[0]["kind"], "row")
        section = context_catalog.resolve(data, path + "::section:Conditions")
        self.assertEqual(len(section), 1)
        self.assertEqual(section[0]["kind"], "section")

    def test_record_mirror_does_not_coalesce_other_kinds_or_nondeclared_aliases(self):
        path = "backlog/scenario-mirror.md"
        self.write(path, note("test-plan", "Scenario mirror", extra="aliases:\n  - ST-901-TS-001",
            body="## ST-901-TS-001\n\nPreserve the scenario condition."))
        data = self.index()["catalog"]
        hits = context_catalog.resolve(data, "ST-901-TS-001")
        self.assertEqual({unit["kind"] for unit in hits}, {"document", "scenario"})
        self.assertEqual(len(hits), 2)
        path = self.mirrored_decision()
        self.write(path, (self.docs / path).read_text().replace("  - DEC-TEST-001", "  - Display alias"))
        data = self.index()["catalog"]
        self.assertEqual(context_catalog.resolve(data, "DEC-TEST-001")[0]["kind"], "row")
        self.assertEqual(context_catalog.resolve(data, "Display alias")[0]["kind"], "document")
        document = context_catalog.resolve(data, path)[0]
        row = next(unit for unit in data["units"].values() if unit["path"] == path and unit["kind"] == "row")
        data["aliases"][path] = [document["unit_id"], row["unit_id"]]
        self.assertEqual(len(context_catalog.resolve(data, path)), 2)

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
        self.assertEqual(plan["coverage"]["source_bytes"], 1)
        self.assertEqual(plan["must_read"][0]["parent_kind"], "document")
        self.assertEqual(plan["coverage"]["remaining_required_units"], 2)

    def test_oversized_unicode_source_makes_progress_until_complete(self):
        self.write("backlog/large.md", note("story", "Large", body=
            "## Conditions\n\n" + "- Keep every condition. αβγ🙂\n" * 80))
        data = self.index()
        parent = context_catalog.resolve(data["catalog"], "backlog/large.md")[0]
        expected = context_catalog.read_units(self.docs, data["catalog"],
                                             [parent["unit_id"]], parent["bytes"])["units"][0]["text"]
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["backlog/large.md"],
            budget={"max_source_bytes": 170, "max_metadata_bytes": 4000})
        fragments, pages = [], 0
        while True:
            self.assertTrue(plan["must_read"])
            read = project_context.read_plan(self.project, data, plan)
            self.assertLessEqual(read["bytes"], 170)
            fragments.extend(unit["text"] for unit in read["units"])
            pages += 1
            self.assertLess(pages, parent["bytes"])
            if plan["status"] == "ready":
                break
            self.assertEqual(plan["coverage"]["remaining_required_units"], 1)
            plan = project_context.expand_context(self.project, data, plan, reason="Read remaining conditions")
        self.assertEqual("".join(fragments), expected)
        self.assertGreater(pages, 1)

    def test_oversized_single_line_and_nested_unit_preserve_parent_scope(self):
        self.write("backlog/large.md", note("story", "Large", body=
            "## Acceptance\n\n- If validation fails:\n  - " + "Preserve 🙂. " * 100 + "\n"))
        data = self.index()
        child = next(unit for unit in data["catalog"]["units"].values()
                     if unit["path"] == "backlog/large.md" and unit["kind"] == "item"
                     and unit["label"].startswith("- Preserve"))
        expected = context_catalog.read_units(self.docs, data["catalog"],
                                             [child["unit_id"]], child["bytes"])["units"][0]["text"]
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=[child["unit_id"]], budget={"max_source_bytes": 160})
        text = ""
        while True:
            row = plan["must_read"][0]
            self.assertEqual(row["parent_unit_id"], child["unit_id"])
            self.assertEqual(row["ranges"], child["ranges"])
            text += project_context.read_plan(self.project, data, plan)["units"][0]["text"]
            if plan["status"] == "ready":
                break
            plan = project_context.expand_context(self.project, data, plan, reason="Read the same item")
        self.assertEqual(text, expected)
        self.assertIn("If validation fails", text)

    def test_smaller_expansion_never_completes_original_document_early(self):
        self.write("backlog/large.md", note("story", "Large", body=
            "## First\n\nFirst condition.\n\n## Remaining\n\n" + "Remaining condition.\n" * 100))
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["backlog/large.md"], budget={"max_source_bytes": 200})
        second = project_context.expand_context(self.project, data, plan,
            refs=["backlog/large.md::section:First"], reason="Read the smaller address")
        self.assertEqual(second["status"], "needs_split")
        self.assertEqual(second["coverage"]["required_units"], 1)
        self.assertGreater(second["must_read"][0]["byte_range"][0], 0)
        self.assertIn("backlog/large.md", project_context.request_data(self.project, second)["refs"])

    def test_large_reference_set_and_growing_history_stay_bounded(self):
        refs = []
        for number in range(140):
            relative = f"backlog/selected-{number:03}-" + "source-" * 10 + ".md"
            self.write(relative, note("story", "Selected", body=f"Required condition {number}."))
            refs.append(relative)
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=refs,
            budget={"max_files": 3, "max_source_bytes": 500, "max_metadata_bytes": 5000})
        read_paths, pages = set(), 0
        state_path = plan["request"]["state"]["path"]
        while True:
            self.assertLessEqual(len(project_context.encoded(plan)), 5000)
            self.assertEqual(project_context.request_data(self.project, plan)["refs"], refs)
            self.assertEqual(plan["request"]["state"]["path"], state_path)
            self.assertNotIn("seen_units", plan["request"])
            self.assertTrue(plan["must_read"])
            read = project_context.read_plan(self.project, data, plan)
            read_paths.update(unit["path"] for unit in read["units"])
            pages += 1
            self.assertLessEqual(pages, len(refs))
            if plan["status"] == "ready":
                break
            plan = project_context.expand_context(self.project, data, plan, reason="Read remaining selected sources")
        self.assertEqual(read_paths, set(refs))
        self.assertGreater(pages, 1)

    def large_scope(self, *, no_cache=False, metadata_bytes=3500):
        refs = []
        for number in range(40):
            relative = f"backlog/selected-{number:03}-" + "source-" * 10 + ".md"
            self.write(relative, note("story", "Selected", body=f"Condition {number}."))
            refs.append(relative)
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=refs, persist_state=not no_cache,
            budget={"max_files": 2, "max_source_bytes": 400, "max_metadata_bytes": metadata_bytes})
        return data, plan, refs

    def test_request_capsule_loss_corruption_and_alias_escape_fail_closed(self):
        data, plan, refs = self.large_scope()
        path = self.project / plan["request"]["state"]["path"]
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            project_context.read_plan(self.project, data, plan)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "rerun the original"):
            project_context.expand_context(self.project, data, plan, reason="Continue")
        replacement = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=refs, budget=plan["request"]["budget"])
        self.assertEqual(replacement["plan_hash"], plan["plan_hash"])
        target = self.project / "state-copy.json"
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            project_context.validate_plan(self.project, data, plan)
        modified = copy.deepcopy(plan)
        modified["request"]["state"]["path"] = "../state-copy.json"
        with self.assertRaisesRegex(ValueError, "unsafe"):
            project_context.validate_plan(self.project, data, modified)

    def test_request_capsule_rejects_foreign_root_and_symlink_ancestor(self):
        data, plan, _refs = self.large_scope()
        foreign = self.project / "other-project"
        foreign.mkdir()
        with self.assertRaisesRegex(ValueError, "different project root"):
            project_context.request_data(foreign, plan)
        root = self.project / project_context.STATE_ROOT
        relocated = root.with_name("request-copy")
        root.rename(relocated)
        root.symlink_to(relocated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            project_context.validate_plan(self.project, data, plan)

    def test_inline_large_scope_is_write_free_and_completes(self):
        data, plan, refs = self.large_scope(no_cache=True)
        self.assertEqual(plan["request"]["state"]["storage"], "inline")
        paths = set()
        while True:
            self.assertFalse((self.project / ".agentrof").exists())
            self.assertLessEqual(len(project_context.encoded(plan)), 3500)
            paths.update(row["path"] for row in project_context.read_plan(self.project, data, plan)["units"])
            if plan["status"] == "ready":
                break
            plan = project_context.expand_context(self.project, data, plan, reason="Read selected scope")
        self.assertEqual(paths, set(refs))

    def test_read_only_request_storage_uses_inline_and_keeps_full_scope(self):
        import atomic_file
        with mock.patch.object(atomic_file, "real_directory", side_effect=OSError(errno.EROFS, "Read only")):
            data, plan, refs = self.large_scope()
            self.assertEqual(plan["request"]["state"]["storage"], "inline")
            project_context.validate_plan(self.project, data, plan)
        self.assertEqual(project_context.request_data(self.project, plan)["refs"], refs)
        self.assertFalse((self.project / ".agentrof").exists())

    def test_no_cache_cli_expands_runtime_origin_without_writing_new_state(self):
        import atomic_file
        _data, fixture, refs = self.large_scope()
        data = project_context.load_index(self.project, no_cache=True)
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=refs, budget=fixture["request"]["budget"])
        self.assertEqual(plan["request"]["state"]["storage"], "runtime")
        path = self.project / "runtime-plan.json"
        path.write_text(json.dumps(plan))
        before = {file.relative_to(self.project).as_posix(): file.read_bytes()
                  for file in self.project.rglob("*") if file.is_file()}
        for arguments in (["check", "--plan", str(path)], ["read", "--plan", str(path)],
                          ["expand", "--plan", str(path), "--reason", "Read added selected evidence",
                           "--ref", "operation/verification-contract.md"]):
            with self.subTest(command=arguments[0]), contextlib.redirect_stdout(io.StringIO()) as output, \
                    mock.patch.object(atomic_file, "real_directory", side_effect=AssertionError("unexpected write")), \
                    mock.patch.object(atomic_file, "replace_bytes", side_effect=AssertionError("unexpected write")):
                code = project_context.main(["--project-root", str(self.project), "--no-cache", *arguments])
            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            if arguments[0] == "expand":
                self.assertEqual(result["request"]["state"]["storage"], "inline")
                self.assertFalse(result["request"]["persist_state"])
        after = {file.relative_to(self.project).as_posix(): file.read_bytes()
                 for file in self.project.rglob("*") if file.is_file()}
        self.assertEqual(after, before)
        modified = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["ST-901"], budget=fixture["request"]["budget"])
        modified["request"]["refs"] = refs
        path.write_text(json.dumps(modified))
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                mock.patch.object(atomic_file, "real_directory", side_effect=AssertionError("unexpected write")), \
                mock.patch.object(atomic_file, "replace_bytes", side_effect=AssertionError("unexpected write")):
            code = project_context.main(["--project-root", str(self.project), "--no-cache", "read",
                                         "--plan", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("modified", json.loads(output.getvalue())["reason"])

    def test_no_cache_unchanged_large_seed_reuses_verified_runtime_descriptor(self):
        import atomic_file
        import hashlib
        refs = []
        for number in range(70):
            relative = "backlog/selected-" + hashlib.sha512(str(number).encode()).hexdigest()[:80] + ".md"
            self.write(relative, note("story", "Selected", body="Preserve the condition."))
            refs.append(relative)
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="backlog-plan", role="backlog-reviewer",
            refs=refs, purpose="review", snapshot_scope="selection", budget={"max_metadata_bytes": 4000})
        state = plan["request"]["state"]
        self.assertEqual(state["storage"], "runtime")
        with mock.patch.object(atomic_file, "real_directory", side_effect=AssertionError("unexpected write")), \
                mock.patch.object(atomic_file, "replace_bytes", side_effect=AssertionError("unexpected write")):
            second = project_context.expand_context(self.project, data, plan,
                reason="Read the next verified page", persist_state=False)
            self.assertEqual(second["request"]["state"], state)
            self.assertFalse(second["request"]["persist_state"])
            self.assertEqual(project_context.request_data(self.project, second)["refs"], refs)
            self.assertLessEqual(len(project_context.encoded(second)), 4000)
            project_context.validate_plan(self.project, data, second)
            with self.assertRaisesRegex(ValueError, "metadata budget requires at least"):
                project_context.expand_context(self.project, data, plan,
                    reason="Read added evidence", refs=["backlog/example.md"], persist_state=False)
        state_path = self.project / state["path"]
        state_path.write_bytes(state_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            project_context.expand_context(self.project, data, second,
                reason="Read the next verified page", persist_state=False)

    def test_shared_source_provenance_stays_complete_under_metadata_paging(self):
        refs = []
        for number in range(80):
            relative = f"backlog/source-{number:03}-" + "selected-" * 8 + ".md"
            self.write(relative, note("story", "Selected", relations={
                "constrained_by": [("operation/verification-contract", "Verification")]}))
            refs.append(relative)
        data = project_context.load_index(self.project, no_cache=True)
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=refs,
            budget={"max_files": 2, "max_source_bytes": 500, "max_metadata_bytes": 3500})
        paths, provenance = set(), []
        while True:
            self.assertLessEqual(len(project_context.encoded(plan)), 3500)
            read = project_context.read_plan(self.project, data, plan)
            paths.update(row["path"] for row in read["units"])
            for row in plan["must_read"]:
                if row["path"] == "operation/verification-contract.md":
                    reasons = row["reasons"]
                    provenance = (project_context.navigation_data(self.project, reasons["state"])["reasons"]
                                  if isinstance(reasons, dict) else reasons)
            if plan["status"] == "ready":
                break
            plan = project_context.expand_context(self.project, data, plan, reason="Read selected constraints")
        self.assertEqual(paths, set(refs) | {"operation/verification-contract.md"})
        self.assertEqual(len(provenance), 80)
        import atomic_file
        path = self.project / "provenance-plan.json"
        path.write_text(json.dumps(plan))
        for arguments in (["read"], ["check"], ["expand", "--reason", "Read selected provenance",
                                               "--ref", "operation/verification-contract.md"]):
            with contextlib.redirect_stdout(io.StringIO()) as output, \
                    mock.patch.object(atomic_file, "real_directory", side_effect=AssertionError("unexpected write")), \
                    mock.patch.object(atomic_file, "replace_bytes", side_effect=AssertionError("unexpected write")):
                code = project_context.main(["--project-root", str(self.project), "--no-cache", *arguments,
                                             "--plan", str(path)])
            self.assertEqual(code, 0, output.getvalue())
            if arguments[0] == "expand":
                result = json.loads(output.getvalue())
                self.assertFalse(result["request"]["persist_state"])
                self.assertEqual(result["request"]["state"]["storage"], "inline")

    def test_inline_budget_refusal_reports_the_measured_minimum_without_writes(self):
        with self.assertRaisesRegex(ValueError, "requires at least [0-9]+ bytes"):
            self.large_scope(no_cache=True, metadata_bytes=1000)
        self.assertFalse((self.project / ".agentrof").exists())

    def test_corrupt_and_truncated_inline_state_refuses_before_reading(self):
        data, plan, _refs = self.large_scope(no_cache=True)
        for payload in ("AAAA", plan["request"]["state"]["data"][:-4]):
            modified = copy.deepcopy(plan)
            modified["request"]["state"]["data"] = payload
            with self.subTest(payload=payload[:8]), self.assertRaises(ValueError):
                project_context.read_plan(self.project, data, modified)
        self.assertFalse((self.project / ".agentrof").exists())

    def test_fragment_plan_changes_and_stale_parent_source_are_refused(self):
        self.write("backlog/large.md", note("story", "Large", body="Preserve all conditions. " * 60))
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["backlog/large.md"], budget={"max_source_bytes": 80})
        modified = copy.deepcopy(plan)
        modified["must_read"][0]["byte_range"][1] -= 1
        with self.assertRaisesRegex(ValueError, "modified"):
            project_context.read_plan(self.project, data, modified)
        path = self.docs / "backlog/large.md"
        path.write_text(path.read_text().replace("Preserve", "Change", 1))
        with self.assertRaisesRegex(ValueError, "stale"):
            project_context.expand_context(self.project, data, plan, reason="Continue")

    def test_fragment_cursor_and_continuation_tampering_are_refused(self):
        self.write("backlog/large.md", note("story", "Large", body="Preserve every condition. " * 30))
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["backlog/large.md"], budget={"max_source_bytes": 80})
        for block, key, value in (("request", "unit", 1), ("request", "byte", 100000),
                                  ("request", "scope_hash", "sha256:" + "0" * 64),
                                  ("continuation", "byte", 0)):
            modified = copy.deepcopy(plan)
            modified[block]["cursor"][key] = value
            with self.subTest(block=block, key=key), self.assertRaises(ValueError):
                project_context.read_plan(self.project, data, modified)

    def test_utf8_character_larger_than_budget_has_no_looping_continuation(self):
        path = self.project / "workspace/memory/character.md"
        path.parent.mkdir(parents=True)
        path.write_text("🙂")
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["workspace/memory/character.md"],
            budget={"max_source_bytes": 1})
        self.assertEqual(plan["must_read"], [])
        self.assertEqual(plan["oversized"]["minimum_source_bytes"], 4)
        with self.assertRaisesRegex(ValueError, "at least 4"):
            project_context.expand_context(self.project, data, plan, reason="Continue")

    def test_widened_fragment_scope_rereads_safely_and_keeps_every_obligation(self):
        self.write("backlog/large.md", note("story", "Large", body="Preserve all conditions. " * 60))
        data = self.index()
        plan = project_context.resolve_context(self.project, data, entry="deliver",
            role="backend-developer", refs=["backlog/large.md"], budget={"max_source_bytes": 80})
        wider = project_context.expand_context(self.project, data, plan,
            refs=["operation/verification-contract.md"], reason="Read added constraint")
        self.assertEqual(wider["coverage"]["required_units"], 2)
        self.assertEqual(wider["coverage"]["remaining_required_units"], 2)
        self.assertEqual(wider["must_read"][0]["byte_range"][0], 0)
        self.assertEqual(set(project_context.request_data(self.project, wider)["refs"]),
                         {"backlog/large.md", "operation/verification-contract.md"})

    @integration
    def test_real_cli_large_scope_reads_expands_and_checks_to_completion(self):
        _data, _plan, refs = self.large_scope()
        self.write(refs[0], note("story", "Selected", body="Preserve 🙂. " * 100))
        expected = (self.docs / refs[0]).read_text()
        maximum_pages = sum((self.docs / ref).stat().st_size for ref in refs)
        script = ROOT / "plugins/software-engineering-team/scripts/project_context.py"

        def invoke(arguments):
            result = subprocess.run([sys.executable, str(script), "--project-root", str(self.project),
                                     *arguments], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        arguments = ["resolve", "--entry", "deliver", "--role", "backend-developer",
                     "--max-files", "2", "--max-source-bytes", "400", "--max-metadata-bytes", "3500"]
        for ref in refs:
            arguments.extend(["--ref", ref])
        plan = invoke(arguments)
        self.assertEqual(plan["request"]["state"]["storage"], "runtime")
        self.assertTrue(invoke(["units", "--ref", refs[0]])["units"])
        path = self.project / "cli-plan.json"
        read_paths, text = set(), ""
        fragments = 0
        pages = 0
        while True:
            path.write_text(json.dumps(plan))
            self.assertEqual(invoke(["check", "--plan", str(path)])["status"], "current")
            reading = invoke(["read", "--plan", str(path)])
            self.assertLessEqual(reading["bytes"], 400)
            read_paths.update(row["path"] for row in reading["units"])
            for row in reading["units"]:
                if row["path"] == refs[0]:
                    text += row["text"]
                    fragments += row["kind"] == "fragment"
            pages += 1
            self.assertLessEqual(pages, maximum_pages)
            if plan["status"] == "ready":
                break
            plan = invoke(["expand", "--plan", str(path), "--reason", "Read remaining selected sources"])
            self.assertLessEqual(len(project_context.encoded(plan)), 3500)
        self.assertEqual(read_paths, set(refs))
        self.assertEqual(text, expected)
        self.assertGreater(fragments, 1)
        modified = copy.deepcopy(plan)
        modified["must_read"] = []
        path.write_text(json.dumps(modified))
        refused = subprocess.run([sys.executable, str(script), "--project-root", str(self.project),
                                  "read", "--plan", str(path)], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("modified", json.loads(refused.stdout)["reason"])
        code = self.project / "workspace/environment/check.py"
        code.parent.mkdir(parents=True)
        code.write_text("assert True\n")
        refused = subprocess.run([sys.executable, str(script), "--project-root", str(self.project),
            "resolve", "--entry", "deliver", "--role", "backend-developer", "--ref",
            "workspace/environment/check.py"], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("type", json.loads(refused.stdout)["reason"])

    @integration
    def test_owning_review_closure_seeds_navigation_with_explicit_input(self):
        from tools.tests.backlog_fixture import make_approved_backlog
        root = self.project / "review-project"
        docs = root / "workspace/docs"
        (docs / "maps").mkdir(parents=True)
        (root / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}))
        with contextlib.redirect_stdout(io.StringIO()):
            make_approved_backlog(docs, "ST-001")
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)
        selected = "workspace/docs/backlog/backlog.md"
        manifest = task_inputs.manifest(project=root, entry="backlog-plan", role="backlog-reviewer",
            mode="review", epic="", inputs=[selected])
        required = {"workspace/docs/" + path for path in manifest["backlog_scope"]["paths"]}
        reading = project_context.request_data(root, manifest["project_reading"])
        self.assertLessEqual(required, set(reading["refs"]) | set(reading.get("manual_sources", [])))
        self.assertGreater(len(reading["refs"]), 1)

    def test_mixed_selected_code_preserves_navigation_and_manual_hash_obligation(self):
        code = self.project / "workspace/environment/check.py"
        code.parent.mkdir(parents=True)
        code.write_text("assert True\n")
        result = project_context.task_context(self.project, entry="deliver", role="backend-developer",
            mode="consume", paths={"workspace/docs/backlog/example.md", "workspace/environment/check.py"})
        self.assertNotEqual(result["status"], "unavailable")
        self.assertIn("backlog/example.md", {row["path"] for row in result["must_read"]})
        manual = result["manual_reads"][0]
        self.assertEqual(manual["path"], "workspace/environment/check.py")
        self.assertEqual(manual["source_hash"], context_catalog.digest(code.read_bytes()))
        with self.assertRaisesRegex(ValueError, "type"):
            self.plan(refs=["workspace/environment/check.py"])
        read = project_context.read_plan(self.project, project_context.load_index(self.project), result)
        self.assertEqual(read["status"], "needs_manual_read")
        code.write_text("assert False\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            project_context.validate_plan(self.project, project_context.load_index(self.project), result)

    def test_manual_only_selection_retains_outstanding_evidence_without_empty_success(self):
        code = self.project / "workspace/tests/check.py"
        code.parent.mkdir(parents=True)
        code.write_text("assert True\n")
        plan = project_context.task_context(self.project, entry="deliver", role="qa-engineer",
            mode="review", paths={"workspace/tests/check.py"}, no_cache=True)
        self.assertEqual(plan["must_read"], [])
        self.assertEqual(plan["manual_reads"][0]["path"], "workspace/tests/check.py")
        result = project_context.read_plan(self.project, project_context.load_index(self.project, no_cache=True), plan)
        self.assertEqual(result["status"], "needs_manual_read")
        self.assertEqual(result["manual_reads"][0]["source_hash"], context_catalog.digest(code.read_bytes()))
        self.assertFalse((self.project / ".agentrof").exists())

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
        self.assertEqual(result["plan_status"], "needs_split")
        self.assertEqual(result["bytes"], 1)
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

    def test_loader_reuses_the_existing_index_without_reparsing(self):
        first = project_context.load_index(self.project)
        self.assertTrue((self.project / vault_query.RUNTIME / "index.json").is_file())
        with mock.patch.object(impact_closure, "load_vault_reusing", side_effect=AssertionError("reparsed")):
            second = project_context.load_index(self.project)
        self.assertEqual(first, second)

    def test_index_build_reuses_one_catalog_for_unit_alias_gap_resolution(self):
        self.write("backlog/selected-plan.md", note("test-plan", "Selected plan",
            body="## ST-901-TS-001\n\nPreserve the selected condition."))
        self.write("backlog/selected-review.md", note("backlog-review", "Selected review",
            extra="scenario_refs:\n  - ST-901-TS-001"))
        with mock.patch.object(context_catalog, "catalog", wraps=context_catalog.catalog) as build:
            data = project_context.load_index(self.project, no_cache=True)
        self.assertEqual(build.call_count, 1)
        self.assertFalse([gap for gap in data["gaps"] if gap.get("key") == "scenario_refs"])

    def test_no_cache_mode_creates_no_runtime_files(self):
        project_context.load_index(self.project, no_cache=True)
        self.assertFalse((self.project / ".agentrof").exists())

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

    @integration
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
        self.assertNotIn("workspace/docs/backlog/unrelated.md",
                         {r["path"] for r in bound["canonical_source_inventory"]})
        self.assertGreater(bound["context_inventory"]["count"], len(bound["canonical_source_inventory"]))
        self.assertNotIn("canonical_source_paths", bound)
        (self.docs / "backlog/example.md").write_text(note("story", "Changed task", extra="id: ST-901"))
        with self.assertRaisesRegex(ValueError, "stale"):
            task_inputs.manifest(**arguments, context_plan=relative)

    @integration
    def test_pinned_contract_uses_the_matching_historical_bytes(self):
        import operation_compile
        import context_history
        import ba_compile
        contract = self.docs / "operation/verification-contract.md"
        text = note("verification-contract", "Verification", body="Earlier approved behavior.",
                    extra="revision: 1").replace("status: draft", "status: approved")
        props, body = operation_compile.parse_text(text, contract)
        digest = operation_compile.source_hash(props, body)
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
        expected = result["units"][0]["text"]
        index = self.index()
        page = project_context.resolve_context(self.project, index, entry="deliver",
            role="backend-developer", refs=["ITEM-901"], budget={"max_source_bytes": 80})
        text = ""
        while True:
            read = project_context.read_plan(self.project, index, page)
            for unit in read["units"]:
                if unit.get("historical"):
                    self.assertEqual(unit["bound_hash"], digest)
                    text += unit["text"]
            if page["status"] == "ready":
                break
            page = project_context.expand_context(self.project, index, page, reason="Read the pinned bytes")
        self.assertEqual(text, expected)

    def test_malformed_source_returns_explicit_manual_recovery(self):
        self.write("backlog/example.md", "---\ninvalid frontmatter\n---\nBroken source")
        result = project_context.task_context(self.project, entry="deliver", role="backend-developer",
            mode="consume", paths={"workspace/docs/backlog/example.md"})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["must_read"], [])
        self.assertIn("manual", result["next_action"])

    def test_redirected_vault_never_reads_or_caches_another_project(self):
        with tempfile.TemporaryDirectory() as raw:
            other = Path(raw).resolve()
            (other / "workspace/docs").mkdir(parents=True)
            for directory in ("workspace", "workspace/docs"):
                root = other / ("a" if directory == "workspace" else "b")
                link = root / directory
                link.parent.mkdir(parents=True, exist_ok=True)
                try:
                    link.symlink_to(self.project / directory, target_is_directory=True)
                except OSError:
                    self.skipTest("directory symlinks unavailable")
                for no_cache in (False, True):
                    with self.assertRaisesRegex(ValueError, "selected project"):
                        project_context.load_index(root, no_cache=no_cache)
                self.assertFalse((self.project / vault_query.RUNTIME).exists())

    def test_exact_receipt_wins_over_modified_current_alias(self):
        original = note("decision", "Decision", body="Only use the approved mode.",
            extra="record_id: ADR-901\nrevision: 1\nrevision_state: sealed")
        self.write("system-architecture/decisions/example.md", original.replace(
            "Only use the approved mode.", "Use the altered mode."))
        self.write("system-architecture/_ledger/records/ADR-901/r1.json", json.dumps({
            "exact_ref": "ARC:ROOT:ADR-901@r1", "content": original, "revision": 1}))
        plan = self.plan(refs=["ARC:ROOT:ADR-901@r1"])
        result = context_catalog.read_units(self.docs, self.index()["catalog"],
            [u["unit_id"] for u in plan["must_read"]], 10000)
        self.assertEqual(plan["status"], "ready")
        self.assertIn("Only use the approved mode.", result["units"][0]["text"])
        self.assertNotIn("Use the altered mode.", result["units"][0]["text"])

    def test_nested_item_preserves_parent_condition_and_its_citation(self):
        self.write("backlog/nested.md", note("story", "Nested", body=
            "## Acceptance\n\n- If the signature is invalid, follow [[operation/verification-contract|Verification]]:\n"
            "  - Reject before storing.\n- Accept valid requests."))
        data = self.index()["catalog"]
        child = next(u for u in data["units"].values() if u["kind"] == "item" and
                     u["label"] == "- Reject before storing.")
        plan = self.plan(refs=[child["unit_id"]])
        self.assertIn("operation/verification-contract.md", {u["path"] for u in plan["must_read"]})
        text = context_catalog.read_units(self.docs, data, [child["unit_id"]], 10000)["units"][0]["text"]
        self.assertIn("If the signature is invalid", text)
        self.assertNotIn("Accept valid requests", text)

    @integration
    def test_default_task_handoff_resolves_and_remains_fresh_after_unrelated_edit(self):
        init_repository(self.project)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)
        for entry, role in (("deliver", "backend-developer"), ("business-analysis", "business-analyst"),
                            ("solution-design", "solution-architect"), ("experience-design", "ux-designer"),
                            ("backlog-plan", "product-owner"), ("execution-plan", "delivery-coordinator")):
            args = dict(project=self.project, entry=entry, role=role, mode="consume",
                        inputs=["workspace/docs/backlog/example.md"])
            manifest = task_inputs.manifest(**args)
            self.assertIn("request", manifest["project_reading"], manifest["project_reading"])
            self.assertIn("project_context_first", {c["condition"] for c in manifest["next_transition_conditions"]})
            self.write("backlog/unrelated.md", note("story", "Changed unrelated", body=entry))
            task_inputs.manifest(**args, expected_hash=manifest["source_hash"])



if __name__ == "__main__":
    unittest.main()
