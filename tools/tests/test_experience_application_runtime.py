from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import experience_application_check
import experience_compile


class OpaqueExperiencePrototypeTests(unittest.TestCase):
    def root(self, temporary: str) -> Path:
        root = Path(temporary) / "workspace/docs/experience-design"
        root.mkdir(parents=True)
        return root

    @staticmethod
    def open_revision(root: Path, revision: int = 1) -> None:
        target = root / "_generated/open-application-revision.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"opened_revision": revision}) + "\n")

    def test_arbitrary_tree_is_snapshotted_without_content_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            artifacts = root / "artifacts"
            (artifacts / "pages").mkdir(parents=True)
            (artifacts / "pages/overview.html").write_text(
                '<script src="https://example.invalid/anything.js"></script>',
                encoding="utf-8",
            )
            (artifacts / "src/app.tsx").parent.mkdir()
            (artifacts / "src/app.tsx").write_text(
                "export default () => <main style={{display: 'grid'}} />;\n",
                encoding="utf-8",
            )
            (artifacts / "assets/prototype.data").parent.mkdir()
            (artifacts / "assets/prototype.data").write_bytes(b"\x00\xffprototype")
            self.open_revision(root)

            registry, findings = experience_application_check.compile_application(root)

            self.assertEqual(findings, [])
            self.assertEqual(
                [row["path"] for row in registry["artifact_files"]],
                ["assets/prototype.data", "pages/overview.html", "src/app.tsx"],
            )
            self.assertNotIn("runtime_sha256", registry)
            self.assertNotIn("coverage_hash", registry)
            self.assertNotIn("design_system", registry)

    def test_approval_binds_exact_bytes_but_does_not_parse_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            artifact = root / "artifacts/experiment/anything.svelte"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<svelte:window on:keydown />\n", encoding="utf-8")
            self.open_revision(root)
            registry, findings = experience_application_check.compile_application(root)
            self.assertEqual(findings, [])
            experience_application_check.write_registry_and_ledger(root, registry)
            (root / "_generated/open-application-revision.json").unlink()

            checked, findings = experience_application_check.compile_application(root, True)
            self.assertEqual(findings, [])
            self.assertEqual(checked, registry)

            artifact.write_text("<svelte:window on:keyup />\n", encoding="utf-8")
            _checked, findings = experience_application_check.compile_application(root, True)
            self.assertIn(
                "approved application registry is stale or does not match the artifact snapshot",
                findings,
            )

    def test_snapshot_rejects_aliases_not_technologies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            source = artifacts / "index.html"
            source.write_text("<html>anything</html>\n", encoding="utf-8")
            alias = artifacts / "alias.js"
            try:
                os.symlink(source, alias)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.open_revision(root)

            _registry, findings = experience_application_check.compile_application(root)

            self.assertIn("artifacts/alias.js: symlinks are not permitted in a snapshot", findings)

    def test_v2_receipt_can_be_superseded_by_an_opaque_v3_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            artifact = root / "artifacts/legacy/application.html"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<main>legacy prototype</main>\n", encoding="utf-8")
            legacy = {
                "schema_version": 2,
                "application_revision": 1,
                "source_hash": experience_application_check.sha(b"old source"),
                "package_set_hash": experience_application_check.sha(
                    experience_application_check.canonical([])
                ),
                "coverage_hash": experience_application_check.sha(b"old coverage"),
                "design_system": {
                    "package_hash": experience_application_check.sha(b"old package"),
                    "revision": 1,
                    "master_source_hash": experience_application_check.sha(b"old master"),
                },
                "runtime_sha256": experience_application_check.sha(b"old runtime"),
                "packages": [],
                "coverage": {},
                "previous_application_hash": experience_application_check.GENESIS_APPLICATION_HASH,
            }
            legacy["application_hash"] = experience_application_check.sha(
                experience_application_check.canonical(legacy)
            )
            generated = root / "_generated"
            ledger = root / "_ledger"
            generated.mkdir()
            ledger.mkdir()
            (generated / "application-registry.json").write_bytes(
                experience_application_check.canonical(legacy)
            )
            (ledger / "application-revisions.json").write_bytes(
                experience_application_check.canonical({"schema_version": 2, "revisions": [legacy]})
            )

            expected = experience_compile.expected_application(root)
            self.assertEqual(expected["revision"], 1)
            self.assertTrue(expected["artifact_tree_hash"].startswith("sha256:"))
            self.open_revision(root, revision=2)
            registry, findings = experience_application_check.compile_application(root)
            self.assertEqual(findings, [])
            experience_application_check.write_registry_and_ledger(root, registry)
            (root / "_generated/open-application-revision.json").unlink()

            _checked, findings = experience_application_check.compile_application(root, True)
            self.assertEqual(findings, [])
            ledger_value = json.loads((ledger / "application-revisions.json").read_text())
            self.assertEqual(ledger_value["schema_version"], 3)
            self.assertEqual(ledger_value["revisions"][-1]["schema_version"], 3)

    def test_package_artifacts_change_the_process_source_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "checkout"
            package.mkdir()
            (package / "experience.md").write_text(
                "---\ntype: experience\n---\n\n# Checkout\n", encoding="utf-8"
            )
            artifact = package / "artifacts/private/demo.js"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("first\n", encoding="utf-8")
            before = experience_compile.source_digest(package)
            artifact.write_text("second\n", encoding="utf-8")
            self.assertNotEqual(before, experience_compile.source_digest(package))


if __name__ == "__main__":
    unittest.main()
