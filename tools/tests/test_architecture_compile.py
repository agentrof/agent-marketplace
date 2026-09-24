import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/architecture_compile.py"
VAULT_CHECK = COMPILER.parent / "vault_check.py"


class ArchitectureCompilerTests(unittest.TestCase):
    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(COMPILER), *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def render_relations(self, docs):
        result = subprocess.run([sys.executable, str(VAULT_CHECK), "render-relations", "--vault", str(docs)],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def seal_constrained_decision(self, docs):
        """Seal a root decision that a component runtime view is constrained by."""
        sys.path.insert(0, str(COMPILER.parent))
        import architecture_compile
        self.prepare(docs)
        self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
        self.run_cli("stub", "--docs", docs, "--item-ref", "AUTH-01", "--kind", "decision",
                     "--record-id", "ADR-001", "--slug", "auth-boundary",
                     "--affected-scope", "orders-api", "--affected-scope", "other-api")
        self.run_cli("stub", "--docs", docs, "--item-ref", "AUTH-01", "--kind", "runtime",
                     "--component", "orders-api", "--record-id", "RUN-001", "--slug", "request-path")
        architecture = docs / "system-architecture"
        decision = architecture / "decisions/auth-boundary-decision.md"
        decision.write_text(decision.read_text(encoding="utf-8").replace(
            "## Navigation\n", "## Navigation <!-- sec: nav -->\n", 1), encoding="utf-8")
        runtime = architecture / "components/orders-api/runtime/request-path/runtime.md"
        architecture_compile.rewrite(runtime, {"constrained_by": [
            "[[system-architecture/decisions/auth-boundary-decision|ADR-001]]"]})
        self.render_relations(docs)
        self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
        return decision, runtime

    def prepare(self, docs):
        solution = docs / "solution-design"
        solution.mkdir(parents=True)
        (solution / "landscape.md").write_text(
            "---\ntype: landscape\npackage_status: approved\n---\n# Landscape\n", encoding="utf-8")
        generated = solution / "_generated"
        generated.mkdir()
        (generated / "component-catalog.json").write_text(json.dumps({"components": [{"component_id": "orders-api", "sourcing": "build"}, {"component_id": "other-api", "sourcing": "build"}]}), encoding="utf-8")
        delivery = docs / "delivery/deliveries/dlv-001-test/items/auth-01"
        delivery.mkdir(parents=True)
        (delivery / "item.md").write_text("---\ntype: delivery-item\nstory_id: AUTH-01\nstatus: active\n---\n# Item\n", encoding="utf-8")

    def blank_lines_under_frontmatter(self, path):
        body = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        return len(body) - len(body.lstrip("\n"))

    def test_architecture_is_materialized_and_stamped_only_for_active_item(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("init-component", "--docs", docs, "--component-ref", "orders-api", "--item-ref", "AUTH-01")
            self.run_cli("stub", "--docs", docs, "--item-ref", "AUTH-01", "--kind", "interface", "--component", "orders-api", "--record-id", "IFC-001", "--slug", "orders")
            self.run_cli("render", "--docs", docs)
            stamped = self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.assertTrue(json.loads(stamped.stdout)["architecture_delta_hash"].startswith("sha256:"))

    def test_item_can_stamp_a_second_delta_over_records_it_already_sealed(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("init-component", "--docs", docs, "--component-ref", "orders-api", "--item-ref", "AUTH-01")
            self.run_cli("render", "--docs", docs)
            first = json.loads(self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01").stdout)
            component = docs / "system-architecture/components/orders-api/component.md"
            self.assertIn("revision_state: sealed", component.read_text(encoding="utf-8"))
            self.run_cli("begin-revision", "--docs", docs,
                         "--ref", "ARC:orders-api:HUB-orders-api@r1", "--item-ref", "AUTH-01")
            self.run_cli("render", "--docs", docs)
            second = json.loads(self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01").stdout)
            self.assertNotEqual(second["architecture_delta_hash"], first["architecture_delta_hash"])
            self.assertIn("revision: 2", component.read_text(encoding="utf-8"))
            self.assertIn("revision_state: sealed", component.read_text(encoding="utf-8"))
            root_record = docs / "system-architecture/architecture.md"
            self.assertIn("revision: 1", root_record.read_text(encoding="utf-8"))

    def test_revision_cycles_keep_one_blank_line_under_the_frontmatter(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            root = docs / "system-architecture/architecture.md"
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            for revision in range(1, 4):
                self.run_cli("begin-revision", "--docs", docs,
                             "--ref", f"ARC:ROOT:HUB-ROOT@r{revision}", "--item-ref", "AUTH-01")
                self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
                self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
                self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            self.run_cli("retire", "--docs", docs, "--ref", "ARC:ROOT:HUB-ROOT@r4", "--item-ref", "AUTH-01")
            self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            self.run_cli("check", "--docs", docs)

    def test_next_revision_collapses_blank_lines_a_sealed_record_accumulated(self):
        sys.path.insert(0, str(COMPILER.parent))
        import architecture_compile
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            root = docs / "system-architecture/architecture.md"
            snapshot = docs / "system-architecture/_ledger/records/HUB-ROOT/r1.json"
            # A record and snapshot sealed while every rewrite added a blank line.
            header, body = root.read_text(encoding="utf-8").split("\n---\n", 1)
            root.write_text(header + "\n---\n" + "\n" * 5 + body.lstrip("\n"), encoding="utf-8")
            saved = json.loads(snapshot.read_text(encoding="utf-8"))
            saved.update(content=root.read_text(encoding="utf-8"),
                         source_hash=architecture_compile.source_hash(root))
            snapshot.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.run_cli("check", "--docs", docs)
            sealed = snapshot.read_bytes()
            self.run_cli("begin-revision", "--docs", docs,
                         "--ref", "ARC:ROOT:HUB-ROOT@r1", "--item-ref", "AUTH-01")
            self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.assertEqual(self.blank_lines_under_frontmatter(root), 1)
            self.assertEqual(snapshot.read_bytes(), sealed)
            self.run_cli("check", "--docs", docs)

    def test_architecture_rejects_non_active_item(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            item = docs / "delivery/deliveries/dlv-001-test/items/auth-01/item.md"
            item.write_text("---\ntype: delivery-item\nstory_id: AUTH-01\nstatus: in_scope\n---\n# Item\n", encoding="utf-8")
            result = self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01", expected=2)
            self.assertIn("claimed or active", result.stderr)

    def test_sealing_preserves_quoted_wikilinks_and_item_source_hash(self):
        sys.path.insert(0, str(COMPILER.parent))
        import architecture_compile
        import delivery_compile
        import vault_check
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            root = docs / "system-architecture/architecture.md"
            props = architecture_compile.record_props(root)
            wikilink = "[[solution-design/landscape|Approved Solution]]"
            props["constrained_by"] = [wikilink]
            props["summary"] = wikilink
            body = "# Architecture\n\nAuthored contract and exact punctuation: a | b.\n"
            root.write_text(architecture_compile.frontmatter(props, body), encoding="utf-8")
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            text = root.read_text(encoding="utf-8")
            self.assertIn(f'  - "{wikilink}"\n', text)
            self.assertIn(f'summary: "{wikilink}"\n', text)
            self.assertEqual(architecture_compile.record_props(root)["constrained_by"], [wikilink])
            self.assertEqual(delivery_compile.split_note(root)[1], body.rstrip())
            item = docs / "delivery/deliveries/dlv-001-test/items/auth-01/item.md"
            item_props, item_body = delivery_compile.split_note(item)
            self.assertEqual(item_props["source_hash"], delivery_compile.content_hash(item_props, item_body))
            self.run_cli("check", "--docs", docs)
            policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
            findings = []
            vault_check.check_frontmatter_props(vault_check.build_vault(docs, policy), findings)
            self.assertEqual([finding for finding in findings if finding.path == "system-architecture/architecture.md"
                              and "wikilink" in finding.message], [])

    def test_sealed_record_detects_direct_drift_and_supports_standards(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("stub", "--docs", docs, "--item-ref", "AUTH-01",
                         "--kind", "standard", "--record-id", "STD-001",
                         "--slug", "api-rules", "--affected-scope", "orders-api",
                         "--affected-scope", "other-api")
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            standard = docs / "system-architecture/standards/api-rules-standard.md"
            standard.write_text(standard.read_text(encoding="utf-8") + "\nTampered\n", encoding="utf-8")
            result = self.run_cli("check", "--docs", docs, expected=1)
            self.assertIn("sealed revision differs", result.stdout)

    def test_sealed_root_and_component_hubs_are_in_the_item_delta(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("init-component", "--docs", docs, "--component-ref", "orders-api", "--item-ref", "AUTH-01")
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            root = docs / "system-architecture/architecture.md"
            root.write_text(root.read_text(encoding="utf-8") + "\nTampered hub\n", encoding="utf-8")
            result = self.run_cli("check", "--docs", docs, expected=1)
            self.assertIn("sealed revision differs", result.stdout)

    def test_sealed_record_accepts_its_rerendered_inverse_relation_block(self):
        sys.path.insert(0, str(COMPILER.parent))
        import architecture_compile
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            decision, runtime = self.seal_constrained_decision(docs)
            root = docs / "system-architecture/architecture.md"
            ledger = docs / "system-architecture/_ledger/records"
            self.assertIn("|ARC:orders-api:RUN-001@r1]]", decision.read_text(encoding="utf-8"))
            self.assertNotIn("relations:generated", root.read_text(encoding="utf-8"))
            # Revising the linking record relabels the decision's block and adds one to the root hub.
            self.run_cli("begin-revision", "--docs", docs, "--ref", "ARC:orders-api:RUN-001@r1", "--item-ref", "AUTH-01")
            architecture_compile.rewrite(runtime, {"related_to": ["[[system-architecture/architecture|System Architecture]]"]})
            self.render_relations(docs)
            self.assertIn("|ARC:orders-api:RUN-001@r2]]", decision.read_text(encoding="utf-8"))
            self.assertIn("relations:generated", root.read_text(encoding="utf-8"))
            for record, record_id in ((decision, "ADR-001"), (root, "HUB-ROOT")):
                snapshot = json.loads((ledger / record_id / "r1.json").read_text(encoding="utf-8"))
                self.assertNotEqual(snapshot["content"], record.read_text(encoding="utf-8"))
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("check", "--docs", docs)
            # Dropping the relations removes both blocks again.
            self.run_cli("begin-revision", "--docs", docs, "--ref", "ARC:orders-api:RUN-001@r2", "--item-ref", "AUTH-01")
            architecture_compile.rewrite(runtime, {}, {"constrained_by", "related_to"})
            self.render_relations(docs)
            self.assertNotIn("relations:generated", decision.read_text(encoding="utf-8") + root.read_text(encoding="utf-8"))
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("check", "--docs", docs)
            self.assertEqual(architecture_compile.record_props(runtime)["revision"], 3)
            for record, record_id in ((decision, "ADR-001"), (root, "HUB-ROOT")):
                self.assertEqual(architecture_compile.record_props(record)["revision"], 1)
                self.assertEqual(sorted(path.name for path in (ledger / record_id).iterdir()), ["r1.json"])

    def test_sealed_record_still_refuses_authored_drift_beside_its_relation_block(self):
        sys.path.insert(0, str(COMPILER.parent))
        import architecture_compile
        import vault_check
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            decision, _runtime = self.seal_constrained_decision(docs)
            self.run_cli("begin-revision", "--docs", docs, "--ref", "ARC:orders-api:RUN-001@r1", "--item-ref", "AUTH-01")
            self.render_relations(docs)
            self.run_cli("stamp-item", "--docs", docs, "--item-ref", "AUTH-01")
            current = decision.read_text(encoding="utf-8")
            self.assertIn("|ARC:orders-api:RUN-001@r2]]", current)
            for drifted in (current.replace("## Scope\n", "## Scope\n\nAn unrevised authored claim.\n", 1),
                            current.replace("title: auth-boundary\n", "title: renamed-boundary\n", 1)):
                self.assertNotEqual(drifted, current)
                decision.write_text(drifted, encoding="utf-8")
                result = self.run_cli("check", "--docs", docs, expected=1)
                self.assertIn("decisions/auth-boundary-decision.md sealed revision differs", result.stdout)
            # A snapshot hash rewritten to match the drift still disagrees with the snapshot's content.
            snapshot = docs / "system-architecture/_ledger/records/ADR-001/r1.json"
            sealed = snapshot.read_text(encoding="utf-8")
            snapshot.write_text(json.dumps({**json.loads(sealed), "source_hash": architecture_compile.source_hash(decision)}),
                                encoding="utf-8")
            result = self.run_cli("check", "--docs", docs, expected=1)
            self.assertIn("decisions/auth-boundary-decision.md sealed revision differs", result.stdout)
            snapshot.write_text(sealed, encoding="utf-8")
            # Text placed inside the block is not the exact projection the vault gate requires.
            decision.write_text(current.replace(
                "<!-- sec: relations:generated:end -->",
                "An unrevised authored claim.\n\n<!-- sec: relations:generated:end -->", 1), encoding="utf-8")
            findings = []
            vault_check.check_relation_projections(
                vault_check.build_vault(docs, vault_check.load_policy(vault_check.DEFAULT_POLICY)), findings)
            self.assertIn("system-architecture/decisions/auth-boundary-decision.md",
                          [finding.path for finding in findings if "projection is stale" in finding.message])
            decision.write_text(current, encoding="utf-8")
            self.run_cli("check", "--docs", docs)

    def test_external_component_cannot_gain_a_fake_internal_module(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            catalog = docs / "solution-design/_generated/component-catalog.json"
            catalog.write_text(json.dumps({"components": [
                {"component_id": "orders-api", "sourcing": "build"},
                {"component_id": "other-api", "sourcing": "build"},
                {"component_id": "payments", "sourcing": "third-party"},
            ]}), encoding="utf-8")
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("init-component", "--docs", docs, "--component-ref", "payments", "--item-ref", "AUTH-01")
            rejected = self.run_cli(
                "stub", "--docs", docs, "--item-ref", "AUTH-01", "--kind", "module",
                "--component", "payments", "--record-id", "MOD-001", "--slug", "internal",
                expected=1,
            )
            self.assertIn("only build components", rejected.stdout)

    def test_decision_lca_and_component_claim_are_mechanical(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01")
            self.run_cli("init-component", "--docs", docs, "--component-ref", "orders-api", "--item-ref", "AUTH-01")
            rejected = self.run_cli(
                "stub", "--docs", docs, "--item-ref", "AUTH-01", "--kind", "decision",
                "--component", "other-api", "--record-id", "ADR-001", "--slug", "auth-boundary",
                "--affected-scope", "orders-api", expected=2,
            )
            self.assertIn("must match the affected-scope LCA", rejected.stdout)


if __name__ == "__main__":
    unittest.main()
