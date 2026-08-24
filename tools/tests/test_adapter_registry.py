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
        self.assertEqual(tuple(adapters), ("claude", "codex", "opencode"))
        self.assertEqual(adapters["opencode"].metadata["artifact_kind"], "project_projection")

    def test_duplicate_projection_root_is_rejected(self):
        path, value = self.adapter_json("opencode")
        value["projection_root"] = ".codex"
        self.write_adapter(path, value)
        with self.assertRaisesRegex(ValueError, "projection_root differs"):
            build_distributions.load_adapters(self.root)

    def test_adapter_api_mismatch_is_rejected(self):
        path, value = self.adapter_json("opencode")
        value["adapter_api_version"] = 999
        self.write_adapter(path, value)
        with self.assertRaisesRegex(ValueError, "unsupported adapter schema or API"):
            build_distributions.load_adapters(self.root)

    def test_product_entry_without_adapter_is_rejected(self):
        path = self.root / "platforms" / "opencode" / "adapter.json"
        path.unlink()
        with self.assertRaisesRegex(ValueError, "registry differs"):
            build_distributions.load_adapters(self.root)


if __name__ == "__main__":
    unittest.main()
