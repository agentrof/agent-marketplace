"""Impact closure (plugins/software-engineering-team/scripts/impact_closure.py)
on synthetic vaults: dependents, constraints, cycles, shared-contract and
policy widening, upward hops, graph gaps with suggested fixes, the
approval-hash proof of unchanged notes, and that the module never writes the
vault."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import backlog_compile  # noqa: E402
import impact_closure  # noqa: E402


def note(kind: str, title: str, relations: dict | None = None, body: str = "",
         extra: str = "") -> str:
    lines = ["---", f"type: {kind}", f"title: {title}", "status: draft"]
    if extra:
        lines.append(extra)
    for key, targets in (relations or {}).items():
        lines.append(f"{key}:")
        lines.extend(f'  - "[[{target}|{alias}]]"' for target, alias in targets)
    lines += ["---", "", f"# {title}", "", body or "Content.", ""]
    return "\n".join(lines)


REQ = "requirements/req-a"
CONTRACT = "system-architecture/orders-contract"
VAULT = {
    f"{REQ}.md": note("requirement", "Req A"),
    "backlog/story-b.md": note("story", "Story B", {"implements": [(REQ, "Req A")]}),
    "backlog/story-c.md": note("story", "Story C", {"depends_on": [("backlog/story-b", "Story B")]}),
    "backlog/story-d.md": note("story", "Story D", {"related_to": [("backlog/story-c", "Story C")]}),
    f"{CONTRACT}.md": note("interface-contract", "Orders contract"),
    "backlog/story-e.md": note("story", "Story E", {"constrained_by": [(CONTRACT, "Orders contract")]}),
    "backlog/story-f.md": note("story", "Story F", extra=f'contract_ref: "[[{CONTRACT}|Orders contract]]"'),
    "backlog/story-g.md": note("story", "Story G"),
    "backlog/loop-x.md": note("story", "Loop X", {"related_to": [("backlog/loop-y", "Loop Y")]}),
    "backlog/loop-y.md": note("story", "Loop Y", {"related_to": [("backlog/loop-x", "Loop X")]}),
    "backlog/story-u.md": note("story", "Story U", {"related_to": [("backlog/loop-x", "Loop X")]}),
    "delivery/process-policy.md": note("process-policy", "Process policy"),
    "backlog/story-p.md": note("story", "Story P", extra='policy_ref: "[[delivery/process-policy|Policy]]"'),
}


def vault_bytes(docs: Path) -> dict:
    return {p.relative_to(docs).as_posix(): p.read_bytes() for p in docs.rglob("*") if p.is_file()}


def stamp(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("status: draft\n", "status: approved\n", 1)
    digest = backlog_compile.digest_text(text)
    path.write_text(text.replace("status: approved\n", f"status: approved\nsource_hash: {digest}\n", 1),
                    encoding="utf-8")
    return digest


class ImpactClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.docs = Path(self.tmp.name) / "docs"
        for rel, text in VAULT.items():
            path = self.docs / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def closure(self, *changed: str) -> dict:
        """The closure, with each gap's suggested fix checked and set aside."""
        result = impact_closure.closure(self.docs, list(changed))
        self.fixes = {}
        for gap in result["graph_gaps"]:
            fix = gap.pop("suggested_fix")
            self.assertTrue(fix.strip(), gap)
            self.fixes[(gap["path"], gap["reason"])] = fix
        return result

    def test_chain_of_dependents_is_transitive(self) -> None:
        result = self.closure(f"{REQ}.md")
        self.assertEqual(result["changed"], [f"{REQ}.md"])
        self.assertEqual(result["closure"], [
            "backlog/story-b.md", "backlog/story-c.md", "backlog/story-d.md", f"{REQ}.md"])
        self.assertEqual(result["widened_by"], [])

    def test_workspace_prefix_is_normalized(self) -> None:
        self.assertEqual(self.closure(f"workspace/docs/{REQ}.md")["changed"], [f"{REQ}.md"])

    def test_changed_note_pulls_in_its_constraints(self) -> None:
        result = self.closure("backlog/story-e.md")
        self.assertEqual(result["closure"], ["backlog/story-e.md", f"{CONTRACT}.md"])

    def test_cycle_terminates_and_includes_both_members(self) -> None:
        result = self.closure("backlog/loop-x.md")
        self.assertEqual(result["closure"], [
            "backlog/loop-x.md", "backlog/loop-y.md", "backlog/story-u.md"])

    def test_shared_contract_change_widens_to_every_citer(self) -> None:
        result = self.closure(f"{CONTRACT}.md")
        self.assertIn("backlog/story-f.md", result["closure"])
        self.assertIn("backlog/story-e.md", result["closure"])
        self.assertEqual(result["widened_by"], [{
            "path": f"{CONTRACT}.md", "reason": "shared_contract",
            "citers": ["backlog/story-e.md", "backlog/story-f.md"]}])

    def test_process_policy_change_widens(self) -> None:
        result = self.closure("delivery/process-policy.md")
        self.assertEqual(result["widened_by"][0]["reason"], "process_policy")
        self.assertIn("backlog/story-p.md", result["closure"])

    def test_missing_and_unresolved_relations_are_gaps(self) -> None:
        (self.docs / "backlog/story-h.md").write_text(
            note("story", "Story H", {"related_to": [("backlog/gone", "Gone")]}), encoding="utf-8")
        gaps = self.closure(f"{REQ}.md")["graph_gaps"]
        self.assertIn({"path": "backlog/story-g.md", "reason": "no_typed_relations"}, gaps)
        self.assertIn({"path": "backlog/story-h.md", "reason": "unresolved_relation",
                       "key": "related_to", "value": "[[backlog/gone|Gone]]"}, gaps)
        self.assertNotIn("backlog/story-b.md", {gap["path"] for gap in gaps})

    def write_matrix(self, *rows: tuple) -> None:
        matrix = self.docs / "maps/_generated/cross-subtree-matrix.md"
        matrix.parent.mkdir(parents=True, exist_ok=True)
        matrix.write_text(
            "<!-- generated by vault_check render-relations; do not edit by hand -->\n"
            "# Cross-subtree matrix\n\n| Source | Relation | Target | Canonical ref |\n"
            "|---|---|---|---|\n" + "".join(
                f"| [[{s}\\|S]] | `{k}` | [[{t}\\|T]] | `T` |\n" for s, k, t in rows),
            encoding="utf-8")

    def edge_tiers(self, result: dict, source: str, target: str, key: str) -> list:
        return next((e["tiers"] for e in result["edges"] if (e["source"], e["target"], e["key"])
                     == (source, target, key)), [])

    def test_index_tier_edge_counts_and_disagrees_with_frontmatter(self) -> None:
        self.write_matrix(("backlog/story-g", "related_to", REQ),
                          ("backlog/story-b", "implements", REQ))
        result = self.closure(f"{REQ}.md")
        self.assertIn("backlog/story-g.md", result["closure"])
        self.assertTrue(result["tiers"]["index"])
        self.assertEqual(self.edge_tiers(result, "backlog/story-g.md", f"{REQ}.md", "related_to"),
                         ["index"])
        self.assertEqual(self.edge_tiers(result, "backlog/story-b.md", f"{REQ}.md", "implements"),
                         ["index", "frontmatter"])
        disagreements = [g for g in result["graph_gaps"] if g["reason"] == "tier_disagreement"]
        self.assertIn({"path": "backlog/story-g.md", "reason": "tier_disagreement",
                       "key": "related_to", "target": f"{REQ}.md",
                       "tiers": ["index", "frontmatter"], "detail": "missing from frontmatter"},
                      disagreements)
        self.assertIn({"path": "backlog/story-d.md", "reason": "tier_disagreement",
                       "key": "related_to", "target": "backlog/story-c.md",
                       "tiers": ["frontmatter", "index"], "detail": "missing from index"},
                      disagreements)

    def test_absent_index_tier_is_not_a_disagreement(self) -> None:
        result = self.closure(f"{REQ}.md")
        self.assertFalse(result["tiers"]["index"])
        self.assertFalse([g for g in result["graph_gaps"] if g["reason"] == "tier_disagreement"])

    def test_stale_index_disagreement_is_a_gap_with_a_suggested_fix(self) -> None:
        self.write_matrix(("backlog/story-b", "implements", REQ))
        before = vault_bytes(self.docs)
        gaps = [g for g in self.closure()["graph_gaps"] if g["reason"] == "tier_disagreement"]
        self.assertTrue(gaps)
        self.assertTrue(any("re-render" in self.fixes[(gap["path"], gap["reason"])] for gap in gaps))
        self.assertEqual(vault_bytes(self.docs), before)

    def test_frontmatter_tier_edge(self) -> None:
        result = self.closure(f"{REQ}.md")
        self.assertEqual(self.edge_tiers(result, "backlog/story-b.md", f"{REQ}.md", "implements"),
                         ["frontmatter"])

    def test_body_tier_wikilinks_with_alias_and_embed(self) -> None:
        (self.docs / "backlog/story-q.md").write_text(note(
            "story", "Story Q", body=f"See [[{REQ}|the requirement]].\n\n![[backlog/story-c.md]]"),
            encoding="utf-8")
        result = self.closure(f"{REQ}.md")
        self.assertIn("backlog/story-q.md", result["closure"])
        self.assertEqual(self.edge_tiers(result, "backlog/story-q.md", f"{REQ}.md", "links_to"),
                         ["body"])
        self.assertIn("backlog/story-q.md", self.closure("backlog/story-c.md")["closure"])

    def test_navigation_tier_lists_the_changed_note(self) -> None:
        (self.docs / "maps/backlog.md").parent.mkdir(parents=True, exist_ok=True)
        (self.docs / "maps/backlog.md").write_text(
            "---\ntype: moc\ntitle: Backlog\n---\n\n# Backlog\n\n- [[backlog/story-b|Story B]]\n",
            encoding="utf-8")
        result = self.closure("backlog/story-b.md")
        self.assertIn("maps/backlog.md", result["closure"])
        self.assertEqual(self.edge_tiers(result, "maps/backlog.md", "backlog/story-b.md", "lists"),
                         ["navigation"])
        self.assertNotIn("maps/backlog.md", self.closure(f"{REQ}.md")["closure"])

    def test_text_tier_searches_identifiers_of_gap_notes_only(self) -> None:
        (self.docs / "backlog/story-g.md").write_text(
            note("story", "Story G", extra="id: ST-900"), encoding="utf-8")
        (self.docs / "backlog/story-t.md").write_text(
            note("story", "Story T", {"related_to": [(REQ, "Req A")]}, body="Needs ST-900 first."),
            encoding="utf-8")
        result = self.closure("backlog/story-g.md")
        self.assertIn("backlog/story-t.md", result["closure"])
        self.assertEqual(self.edge_tiers(result, "backlog/story-t.md", "backlog/story-g.md",
                                         "mentions"), ["text"])
        self.assertIn({"path": "backlog/story-g.md", "reason": "text_only_relation",
                       "source": "backlog/story-t.md", "tiers": ["text"]}, result["graph_gaps"])

    def test_unchanged_approved_note_is_proven_by_hash(self) -> None:
        digest = stamp(self.docs / "backlog/story-g.md")
        result = self.closure(f"{REQ}.md")
        self.assertIn({"path": "backlog/story-g.md", "approval_hash": digest,
                       "scheme": "backlog"}, result["proven_unchanged"])
        self.assertNotIn("backlog/story-g.md", result["unstamped"])
        self.assertIn("backlog/story-f.md", result["unstamped"])

    def test_changed_approved_note_leaves_the_proven_set(self) -> None:
        path = self.docs / "backlog/story-g.md"
        stamp(path)
        path.write_text(path.read_text(encoding="utf-8").replace("Content.", "Edited."),
                        encoding="utf-8")
        result = self.closure(f"{REQ}.md")
        self.assertNotIn("backlog/story-g.md",
                         {item["path"] for item in result["proven_unchanged"]})
        self.assertEqual(result["stale_approved"], ["backlog/story-g.md"])
        self.assertIn("backlog/story-g.md", result["changed"])
        self.assertIn("backlog/story-g.md", result["closure"])

    def test_a_changed_note_reads_what_it_answers_to_one_hop(self) -> None:
        digest = stamp(self.docs / f"{REQ}.md")
        result = self.closure("backlog/story-b.md")
        self.assertIn(f"{REQ}.md", result["closure"])
        self.assertNotIn(f"{REQ}.md", {row["path"] for row in result["proven_unchanged"]})
        # One hop only: story-c depends on story-b, and what story-b's
        # Requirement answers to is not pulled in through story-c.
        self.assertIn("backlog/story-c.md", result["closure"])
        self.assertTrue(digest)

    def test_every_gap_carries_evidence_and_a_suggested_fix(self) -> None:
        (self.docs / "backlog/story-h.md").write_text(
            note("story", "Story H", {"related_to": [("backlog/gone", "Gone")]}), encoding="utf-8")
        self.closure()
        self.assertIn("backlog/gone", self.fixes[("backlog/story-h.md", "unresolved_relation")])
        self.assertIn("backlog/story-g.md", self.fixes[("backlog/story-g.md", "no_typed_relations")])
        # The module itself returns the fix on every gap.
        for gap in impact_closure.closure(self.docs, [])["graph_gaps"]:
            self.assertTrue(gap["suggested_fix"].strip(), gap)

    def test_the_module_is_read_only(self) -> None:
        for name in ("heal_relation", "heal_views", "with_relation"):
            self.assertFalse(hasattr(impact_closure, name), name)
        before = vault_bytes(self.docs)
        self.closure(f"{REQ}.md")
        impact_closure.vault_views(self.docs)
        self.assertEqual(vault_bytes(self.docs), before)
        for verb in ("heal", "render"):
            with self.subTest(verb=verb), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                impact_closure.main([verb, "--docs", str(self.docs)])

    def test_every_front_matter_reference_is_an_edge(self) -> None:
        notes = {
            "operation/verification-contract.md": note(
                "verification-contract", "Verification contract", extra="id: VEC-001"),
            "delivery/dlv-001/items/st-1/item.md": note(
                "delivery-item", "Item 1",
                extra="verification_contract_ref: operation/verification-contract"),
            "delivery/dlv-001/items/st-2/item.md": note(
                "delivery-item", "Item 2",
                extra='verification_contract_ref: "[[operation/verification-contract|VC]]"'),
            "delivery/dlv-001/items/st-3/item.md": note(
                "delivery-item", "Item 3", extra="verification_contract_ref: VEC-001"),
            "backlog/story-z.md": note("story", "Story Z", extra=f'requirement_ref: "[[{REQ}|Req A]]"'),
            "backlog/story-v.md": note("story", "Story V", extra="requirement_ref: REQ-404"),
        }
        for rel, text in notes.items():
            (self.docs / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.docs / rel).write_text(text, encoding="utf-8")
        result = self.closure("operation/verification-contract.md")
        for item in (1, 2, 3):
            self.assertIn(f"delivery/dlv-001/items/st-{item}/item.md", result["closure"])
            self.assertIn(f"delivery/dlv-001/items/st-{item}/item.md",
                          result["widened_by"][0]["citers"])
        self.assertIn("backlog/story-z.md", self.closure(f"{REQ}.md")["closure"])
        gaps = [gap for gap in self.closure()["graph_gaps"] if gap["path"] == "backlog/story-v.md"]
        self.assertIn({"path": "backlog/story-v.md", "reason": "unresolved_relation",
                       "key": "requirement_ref", "value": "REQ-404"}, gaps)

    def test_a_deleted_note_seeds_the_closure_with_its_earlier_relations(self) -> None:
        text = (self.docs / "backlog/story-b.md").read_text(encoding="utf-8")
        (self.docs / "backlog/story-b.md").unlink()
        result = impact_closure.closure(self.docs, [], deleted={"backlog/story-b.md": text})
        self.assertIn(f"{REQ}.md", result["closure"])
        self.assertEqual(result["deleted"], ["backlog/story-b.md"])
        self.assertNotIn(f"{REQ}.md", impact_closure.closure(self.docs, [])["closure"])

    def test_a_sealed_architecture_record_is_proven_by_its_ledger(self) -> None:
        import architecture_compile
        path = self.docs / "system-architecture/decisions/adr-001.md"
        path.parent.mkdir(parents=True)
        path.write_text(note("architecture-decision", "ADR 1", {"related_to": [(REQ, "Req A")]},
                             extra="record_id: ADR-001\nrevision: 1\nrevision_state: sealed"),
                        encoding="utf-8")
        ledger = self.docs / "system-architecture/_ledger/records/ADR-001/r1.json"
        ledger.parent.mkdir(parents=True)
        sealed = architecture_compile.source_hash(path)
        ledger.write_text(json.dumps({"source_hash": sealed}), encoding="utf-8")
        rel = "system-architecture/decisions/adr-001.md"
        self.assertIn({"path": rel, "approval_hash": sealed, "scheme": "architecture"},
                      self.closure("backlog/story-g.md")["proven_unchanged"])
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
        result = self.closure("backlog/story-g.md")
        self.assertIn(rel, result["stale_approved"])
        self.assertIn(rel, result["closure"])

    def test_record_beyond_appends_once_with_reason(self) -> None:
        manifest = {"closure": []}
        self.assertIs(impact_closure.record_beyond(manifest, "docs/a.md", "unsure"), manifest)
        impact_closure.record_beyond(manifest, "docs/a.md", "unsure")
        self.assertEqual(manifest["beyond_closure"], [{"path": "a.md", "reason": "unsure"}])
        with self.assertRaises(ValueError):
            impact_closure.record_beyond(manifest, "a.md", " ")

    def test_vault_views_lists_present_views(self) -> None:
        (self.docs / "home.md").write_text("# Home\n", encoding="utf-8")
        views = {view["pattern"]: view["paths"]
                 for view in impact_closure.vault_views(self.docs)["views"]}
        self.assertEqual(views["home.md"], ["home.md"])
        self.assertEqual(views["maps/_generated/relation-status.md"], [])
        self.assertEqual(views["delivery/process-policy.md"], ["delivery/process-policy.md"])

    def test_cli_closure(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = impact_closure.main(["closure", "--docs", str(self.docs),
                                        "--changed", f"{REQ}.md"])
        self.assertEqual(code, 0)
        self.assertIn("backlog/story-d.md", json.loads(out.getvalue())["closure"])

if __name__ == "__main__":
    unittest.main()
