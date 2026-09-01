"""Fail-closed contracts for the dynamic host adapter registry."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402


class AdapterRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root, build=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def adapter_json(self, host: str) -> tuple[Path, dict]:
        path = self.root / "platforms" / host / "adapter.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def write_adapter(self, path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_registry_discovers_each_declared_host_in_stable_order(self):
        adapters = build_distributions.load_adapters(self.root)
        self.assertEqual(tuple(adapters), ("claude", "codex"))
        self.assertTrue(all(
            adapter.metadata["artifact_kind"] == "native_marketplace"
            for adapter in adapters.values()
        ))

    def test_registry_discovery_never_writes_python_bytecode(self):
        build_distributions.load_adapters(self.root)
        self.assertEqual(
            [path for path in self.root.rglob("*")
             if build_distributions.is_python_cache(path)],
            [],
        )

    def test_duplicate_projection_root_is_rejected(self):
        path, value = self.adapter_json("claude")
        value["projection_root"] = ".codex"
        self.write_adapter(path, value)
        product_path = self.root / "product.json"
        product = json.loads(product_path.read_text(encoding="utf-8"))
        product["project_environment"]["projection_roots"]["claude"] = ".codex"
        product_path.write_text(json.dumps(product, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate projection roots"):
            build_distributions.load_adapters(self.root)

    def test_adapter_api_mismatch_is_rejected(self):
        path, value = self.adapter_json("claude")
        value["adapter_api_version"] = 999
        self.write_adapter(path, value)
        with self.assertRaisesRegex(ValueError, "unsupported adapter schema or API"):
            build_distributions.load_adapters(self.root)

    def test_product_entry_without_adapter_is_rejected(self):
        path = self.root / "platforms" / "codex" / "adapter.json"
        path.unlink()
        with self.assertRaisesRegex(ValueError, "registry differs"):
            build_distributions.load_adapters(self.root)

    def test_adapterless_platform_root_is_rejected(self):
        retired = self.root / "platforms" / "retired"
        retired.mkdir()
        (retired / "host-contract.md").write_text("stale\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "platform roots must exactly match"):
            build_distributions.load_adapters(self.root)

    def test_marker_owned_retired_host_tree_is_replaced(self):
        marker, _ = build_distributions.packaging_names(self.root)
        retired_package = self.root / "dist" / "retired" / fixtures.PLUGIN
        retired_package.mkdir(parents=True)
        (retired_package / marker).write_text("generated\n", encoding="utf-8")

        build_distributions.replace_generated(self.root, self.root / "dist")

        self.assertEqual(
            {path.name for path in (self.root / "dist").iterdir() if path.is_dir()},
            {"claude", "codex"},
        )

    def test_unmanaged_tree_is_not_replaced(self):
        (self.root / "dist" / "unmanaged").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "unmanaged dist tree"):
            build_distributions.replace_generated(self.root, self.root / "dist")


if __name__ == "__main__":
    unittest.main()
