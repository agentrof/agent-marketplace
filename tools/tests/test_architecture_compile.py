import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/architecture_compile.py"


class ArchitectureCompilerTests(unittest.TestCase):
    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(COMPILER), *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

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

    def test_architecture_rejects_non_active_item(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            self.prepare(docs)
            item = docs / "delivery/deliveries/dlv-001-test/items/auth-01/item.md"
            item.write_text("---\ntype: delivery-item\nstory_id: AUTH-01\nstatus: in_scope\n---\n# Item\n", encoding="utf-8")
            result = self.run_cli("init-root", "--docs", docs, "--item-ref", "AUTH-01", expected=2)
            self.assertIn("claimed or active", result.stderr)

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
