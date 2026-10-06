"""Impact closure (plugins/software-engineering-team/scripts/impact_closure.py)
on synthetic vaults: dependents, constraints, cycles, shared-contract and
policy widening, graph gaps, relation heal with closure recompute, and the
approval-hash proof of unchanged notes."""

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
        return impact_closure.closure(self.docs, list(changed))

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

    def test_stale_index_disagreement_heals_by_render(self) -> None:
        self.write_matrix(("backlog/story-b", "implements", REQ))
        self.assertTrue([g for g in self.closure()["graph_gaps"]
                         if g["reason"] == "tier_disagreement"])
        with self.assertRaises(PermissionError):
            impact_closure.heal_views(self.docs, role="backlog-reviewer")
        healed = impact_closure.heal_views(self.docs, role="product-owner")
        self.assertIn("maps/_generated/cross-subtree-matrix.md", healed["views"])
        result = self.closure()
        self.assertTrue(result["tiers"]["body"])
        self.assertFalse([g for g in result["graph_gaps"] if g["reason"] == "tier_disagreement"])

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

    def test_heal_adds_relation_and_closure_recomputes(self) -> None:
        self.assertNotIn("backlog/story-g.md", self.closure(f"{REQ}.md")["closure"])
        result = impact_closure.heal_relation(
            self.docs, "backlog/story-g.md", f"{REQ}.md", "related_to",
            "Story G's scope restates Req A", role="product-owner")
        self.assertEqual(result["status"], "written")
        self.assertIn(f'+  - "[[{REQ}|Req A]]"', result["diff"])
        self.assertIn("maps/_generated/relation-status.md", result["views"])
        self.assertIn(f"{REQ}.md", result["views"])
        self.assertEqual(self.edge_tiers(self.closure(f"{REQ}.md"), "backlog/story-g.md",
                                         f"{REQ}.md", "related_to"),
                         ["index", "frontmatter", "body"])
        self.assertIn("Story G", (self.docs / f"{REQ}.md").read_text(encoding="utf-8"))
        after = self.closure(f"{REQ}.md")
        self.assertIn("backlog/story-g.md", after["closure"])
        self.assertNotIn("backlog/story-g.md", {gap["path"] for gap in after["graph_gaps"]})
        again = impact_closure.heal_relation(
            self.docs, "backlog/story-g.md", f"{REQ}.md", "related_to",
            "same", role="product-owner")
        self.assertEqual(again["status"], "unchanged")

    def test_heal_on_approved_note_stales_its_stamp(self) -> None:
        stamp(self.docs / "backlog/story-g.md")
        result = impact_closure.heal_relation(
            self.docs, "backlog/story-g.md", f"{REQ}.md", "related_to",
            "evidence", role="product-owner")
        self.assertTrue(result["approval_stale"])
        self.assertIn("backlog/story-g.md", self.closure()["stale_approved"])

    def test_heal_refuses_read_only_context(self) -> None:
        before = (self.docs / "backlog/story-g.md").read_bytes()
        for kwargs in ({"role": "backlog-reviewer"},
                       {"role": "product-owner", "mode": "review"},
                       {"role": "qa-engineer", "entry": "backlog_plan"}):
            with self.subTest(**kwargs), self.assertRaises(PermissionError):
                impact_closure.heal_relation(
                    self.docs, "backlog/story-g.md", f"{REQ}.md", "related_to",
                    "evidence", **kwargs)
        self.assertEqual((self.docs / "backlog/story-g.md").read_bytes(), before)

    def test_heal_refuses_when_the_vault_fails_its_relation_contract(self) -> None:
        (self.docs / "backlog/story-h.md").write_text(
            note("story", "Story H", {"related_to": [("backlog/gone", "Gone")]}), encoding="utf-8")
        before = (self.docs / "backlog/story-g.md").read_bytes()
        with self.assertRaises(impact_closure.HealRefused):
            impact_closure.heal_relation(self.docs, "backlog/story-g.md", f"{REQ}.md",
                                         "related_to", "e", role="product-owner")
        self.assertEqual((self.docs / "backlog/story-g.md").read_bytes(), before)

    def test_heal_refuses_contract_violations(self) -> None:
        refused = impact_closure.HealRefused
        cases = (
            ("backlog/story-g.md", f"{REQ}.md", "related_to", ""),          # no evidence
            ("backlog/story-g.md", f"{REQ}.md", "depends_on", "e"),         # outside contract
            ("backlog/story-g.md", "backlog/story-g.md", "related_to", "e"),  # self edge
            ("backlog/story-g.md", "backlog/gone.md", "related_to", "e"),   # missing target
        )
        for source, target, kind, evidence in cases:
            with self.subTest(kind=kind, target=target), self.assertRaises(refused):
                impact_closure.heal_relation(self.docs, source, target, kind, evidence,
                                             role="product-owner")

    def test_heal_validates_the_postimage_before_writing(self) -> None:
        with self.assertRaisesRegex(impact_closure.HealRefused,
                                    "^relation contract refuses the heal: .*cannot target type"):
            impact_closure.heal_relation(self.docs, "backlog/story-g.md", f"{REQ}.md",
                                         "uses_design", "e", role="product-owner")
        self.assertFalse((self.docs / "maps").exists())

    def test_heal_refuses_acyclic_cycle(self) -> None:
        impact_closure.heal_relation(self.docs, "backlog/story-g.md", "backlog/story-u.md",
                                     "derives_from", "e", role="product-owner")
        with self.assertRaises(impact_closure.HealRefused):
            impact_closure.heal_relation(self.docs, "backlog/story-u.md", "backlog/story-g.md",
                                         "derives_from", "e", role="product-owner")

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

    def test_cli_closure_and_read_only_heal(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = impact_closure.main(["closure", "--docs", str(self.docs),
                                        "--changed", f"{REQ}.md"])
        self.assertEqual(code, 0)
        self.assertIn("backlog/story-d.md", json.loads(out.getvalue())["closure"])
        with contextlib.redirect_stderr(io.StringIO()):
            code = impact_closure.main([
                "heal", "--docs", str(self.docs), "--source", "backlog/story-g.md",
                "--target", f"{REQ}.md", "--kind", "related_to", "--evidence", "e",
                "--role", "backlog-reviewer"])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
