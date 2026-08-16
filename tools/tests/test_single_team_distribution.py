"""Standalone-team and cross-host distribution contracts."""

from __future__ import annotations

import hashlib
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


class SingleTeamDistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalogs_versions_and_packages_name_one_standalone_team(self):
        versions = json.loads(
            (self.root / "versions.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(versions["plugins"]), {fixtures.PLUGIN})

        claude = json.loads(
            (self.root / ".claude-plugin/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        codex = json.loads(
            (self.root / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [entry["name"] for entry in claude["plugins"]],
            [fixtures.PLUGIN],
        )
        self.assertEqual(
            [entry["name"] for entry in codex["plugins"]],
            [fixtures.PLUGIN],
        )

        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(
                    sorted(path.name for path in (self.root / "dist" / host).iterdir()),
                    [fixtures.PLUGIN],
                )
                manifest = json.loads(
                    (
                        self.root
                        / "platforms"
                        / host
                        / fixtures.PLUGIN
                        / "manifest.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertIn(manifest.get("dependencies"), (None, []))

    def test_hosts_share_snapshot_and_host_neutral_canonical_payload(self):
        snapshots = []
        for host in build_distributions.HOSTS:
            package = self.root / "dist" / host / fixtures.PLUGIN
            manifest = json.loads(
                (package / f".{host}-plugin/plugin.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("agent_marketplace", manifest)
            provenance = json.loads(
                (package / ".agent-marketplace-package.json").read_text(
                    encoding="utf-8"
                )
            )
            snapshots.append({
                key: provenance[key] for key in (
                    "build_id",
                    "marketplace_release",
                    "source_channel",
                    "source_ref",
                    "source_commit",
                )
            })
            self.assertEqual(
                json.loads((package / "product.json").read_text(encoding="utf-8")),
                build_distributions.load_product_contract(self.root),
            )
        self.assertEqual(snapshots[0], snapshots[1])

        source = self.root / "plugins" / fixtures.PLUGIN
        for relative in ("constitution.md", "flows", "skill-content"):
            canonical = source / relative
            for host in build_distributions.HOSTS:
                packaged = self.root / "dist" / host / fixtures.PLUGIN / relative
                if canonical.is_file():
                    expected = canonical.read_bytes().replace(b"\r\n", b"\n").replace(
                        b"\r", b"\n"
                    )
                    self.assertEqual(packaged.read_bytes(), expected)
                    continue
                for path in sorted(candidate for candidate in canonical.rglob("*")
                                   if candidate.is_file()):
                    expected = path.read_bytes()
                    if b"\0" not in expected:
                        expected = expected.replace(b"\r\n", b"\n").replace(
                            b"\r", b"\n"
                        )
                    target = packaged / path.relative_to(canonical)
                    self.assertEqual(target.read_bytes(), expected, target)

    def test_provenance_hashes_and_rebuild_are_deterministic(self):
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                package = self.root / "dist" / host / fixtures.PLUGIN
                provenance = json.loads(
                    (package / build_distributions.PROVENANCE).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(provenance["component"], fixtures.PLUGIN)
                self.assertEqual(provenance["host"], host)
                for relative, expected in provenance["files"].items():
                    actual = hashlib.sha256((package / relative).read_bytes()).hexdigest()
                    self.assertEqual(actual, expected, relative)

        self.assertEqual(
            build_distributions.check(self.root, self.root / "dist"),
            [],
        )

    def test_independent_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as first_dir, \
                tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir) / "dist"
            second = Path(second_dir) / "dist"
            build_distributions.build(self.root, first)
            build_distributions.build(self.root, second)
            self.assertEqual(build_distributions.compare_dirs(first, second), [])

    def test_canonical_source_rejects_unknown_component_and_symlink(self):
        plugin = self.root / "plugins" / fixtures.PLUGIN
        unknown = plugin / "runtime-state"
        unknown.mkdir()
        with self.assertRaisesRegex(ValueError, "unsupported canonical top-level"):
            build_distributions.validate_canonical(self.root)
        unknown.rmdir()
        target = plugin / "constitution.md"
        link = plugin / "flows" / "linked.md"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            build_distributions.validate_canonical(self.root)

    def test_python_runtime_caches_never_enter_distributions(self):
        cache = self.root / "plugins" / fixtures.PLUGIN / "scripts/__pycache__"
        cache.mkdir()
        (cache / "probe.cpython-39.pyc").write_bytes(b"cache")
        output = self.root / "cache-build"
        build_distributions.build(self.root, output)
        self.assertEqual(list(output.rglob("__pycache__")), [])
        self.assertEqual(list(output.rglob("*.pyc")), [])

    def test_agent_metadata_is_projected_for_each_host(self):
        canonical = self.root / "plugins" / fixtures.PLUGIN / "agents"
        for source in canonical.glob("*.md"):
            name = source.stem
            claude = (
                self.root / "dist/claude" / fixtures.PLUGIN / "agents" / source.name
            ).read_text(encoding="utf-8")
            codex = (
                self.root / "dist/codex" / fixtures.PLUGIN / "agents" / source.name
            ).read_text(encoding="utf-8")
            self.assertIn(f"name: {name}", claude)
            self.assertIn(f"name: {name}", codex)
            self.assertIn("model:", claude)
            self.assertIn("reasoning:", codex)


if __name__ == "__main__":
    unittest.main()
