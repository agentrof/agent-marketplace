"""Experience-design compiler identity, inheritance, artifact and gate tests."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"


def module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    assert spec.loader is not None
    spec.loader.exec_module(value)
    return value


EXPERIENCE = module("experience_compile_regression", "experience_compile.py")
ARTIFACT = module(
    "experience_artifact_check_regression", "experience_artifact_check.py"
)


def call(target, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = target.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExperienceCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.root = self.project / "workspace/docs/experience-design"
        docs = self.root.parent
        docs.mkdir(parents=True)
        (docs / "home.md").write_text("# Home\n", encoding="utf-8")
        ba = docs / "business-analysis/marketplace"
        (ba / "_generated").mkdir(parents=True)
        (ba / "space.md").write_text(
            "---\nstatus: approved\n---\n# Marketplace\n", encoding="utf-8"
        )
        registry_bytes = (json.dumps({
            "schema_version": 3,
            "codes": {"CAT": "domains/catalog"},
            "ids": {
                "AC-CAT-001": {
                    "doc": "domains/catalog/acceptance/catalog-acceptance.md",
                    "doc_status": "approved",
                },
                "AC-CAT-002": {
                    "doc": "domains/catalog/acceptance/catalog-acceptance.md",
                    "doc_status": "approved",
                },
            },
        }, indent=2, sort_keys=True) + "\n").encode()
        (ba / "_generated/registry.json").write_bytes(registry_bytes)
        self.analysis_hash = "sha256:" + hashlib.sha256(registry_bytes).hexdigest()
        self.assertEqual(call(EXPERIENCE, [
            "init-program", "--root", str(self.root), "--program", "PRG-001",
            "--title", "Marketplace Program",
        ])[0], 0)
        self.assertEqual(call(EXPERIENCE, [
            "init-release", "--root", str(self.root), "--program", "PRG-001",
            "--release", "REL-001", "--title", "Marketplace Release",
        ])[0], 0)
        self.release = self.root / "programs/prg-001/releases/rel-001"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stub(self, kind: str, ident: str, slug: str, *extra: str):
        design = ["--uses-design", "[[design-system/MASTER|Design System]]"] \
            if kind == "screen" else []
        return call(EXPERIENCE, [
            "stub", "--release-root", str(self.release), "--kind", kind,
            "--id", ident, "--slug", slug,
            "--scope", "marketplace#domains/catalog",
            "--analysis-hash", self.analysis_hash,
            "--criterion-set", "marketplace:AC-CAT-001", *design, *extra,
        ])

    def test_init_stub_render_and_stale_analysis_gate(self):
        self.assertEqual(self.stub("journey", "JRN-001", "browse")[0], 0)
        self.assertEqual(self.stub("screen", "SCR-001", "catalog")[0], 0)
        projection = self.release / "spaces/marketplace/domains/catalog/domain.md"
        self.assertTrue(projection.is_file())
        self.assertEqual(call(EXPERIENCE, [
            "render", "--release-root", str(self.release)
        ])[0], 0)
        registry = json.loads(
            (self.release / "_generated/effective-registry.json").read_text()
        )
        self.assertEqual(
            [record["id"] for record in registry["records"]],
            ["JRN-001", "SCR-001"],
        )
        ba_registry = self.root.parent / "business-analysis/marketplace/_generated/registry.json"
        ba_registry.write_text(
            ba_registry.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        code, output, _ = call(EXPERIENCE, [
            "check", "--release-root", str(self.release)
        ])
        self.assertEqual(code, 1)
        self.assertIn("BA registry hash is stale", output)

    def test_duplicate_identity_and_wrong_scope_owner_fail(self):
        self.assertEqual(self.stub("journey", "JRN-001", "browse")[0], 0)
        source = (
            self.release
            / "spaces/marketplace/domains/catalog/journeys/browse-journey.md"
        )
        duplicate = self.release / "journeys/browse-journey.md"
        duplicate.parent.mkdir(exist_ok=True)
        duplicate.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        code, output, _ = call(EXPERIENCE, [
            "check", "--release-root", str(self.release)
        ])
        self.assertEqual(code, 1)
        self.assertIn("duplicate identity", output)
        self.assertIn("scope owner", output)

    def test_release_inheritance_requires_revision_and_exact_supersedes(self):
        self.assertEqual(self.stub("screen", "SCR-001", "catalog")[0], 0)
        self.assertEqual(call(EXPERIENCE, [
            "render", "--release-root", str(self.release)
        ])[0], 0)
        self.assertEqual(call(EXPERIENCE, [
            "init-release", "--root", str(self.root), "--program", "PRG-001",
            "--release", "REL-002", "--inherits", "REL-001",
        ])[0], 0)
        second = self.release.parent / "rel-002"
        code, _, error = call(EXPERIENCE, [
            "stub", "--release-root", str(second), "--kind", "screen",
            "--id", "SCR-001", "--revision", "2", "--slug", "catalog",
            "--scope", "marketplace#domains/catalog",
            "--analysis-hash", self.analysis_hash,
            "--supersedes", "PRG-001:SCR-001@r1",
            "--uses-design", "[[design-system/MASTER|Design System]]",
        ])
        self.assertEqual(code, 0, error)
        self.assertEqual(call(EXPERIENCE, [
            "render", "--release-root", str(second)
        ])[0], 0)

    def test_artifact_rejects_remote_assets_and_metadata_drift(self):
        artifact = self.release / "artifacts/catalog-preview.html"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(
            '<meta name="experience-program" content="WRONG">'
            '<script src="https://example.test/x.js"></script>'
            '<div data-experience-id="SCR-001"></div>',
            encoding="utf-8",
        )
        generated = self.release / "_generated"
        generated.mkdir(exist_ok=True)
        (generated / "effective-registry.json").write_text(json.dumps({
            "program_id": "PRG-001", "release_id": "REL-001",
            "registry_hash": "sha256:x",
        }), encoding="utf-8")
        code, output, _ = call(ARTIFACT, [
            "--artifact", str(artifact), "--release-root", str(self.release),
            "--owner", ".", "--declared-id", "SCR-001", "--json",
        ])
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertTrue(any("remote" in finding for finding in payload["findings"]))

    def test_gate_requires_compiler_bound_approval_stamp(self):
        self.assertEqual(self.stub("journey", "JRN-001", "browse")[0], 0)
        self.assertEqual(call(EXPERIENCE, [
            "render", "--release-root", str(self.release)
        ])[0], 0)
        self.assertEqual(call(EXPERIENCE, [
            "check", "--release-root", str(self.release), "--gate"
        ])[0], 1)
        code, _, error = call(EXPERIENCE, [
            "stamp", "--release-root", str(self.release),
        ])
        self.assertEqual(code, 0, error)
        self.assertEqual(call(EXPERIENCE, [
            "check", "--release-root", str(self.release), "--gate"
        ])[0], 0)


if __name__ == "__main__":
    unittest.main()
