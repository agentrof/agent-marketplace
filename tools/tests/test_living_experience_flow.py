from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import experience_application_check
import experience_compile


class LivingExperienceFlowTests(unittest.TestCase):
    def root(self, temporary: str) -> Path:
        root = Path(temporary) / "workspace/docs/experience-design"
        root.mkdir(parents=True)
        return root

    def reviewed_registry(self, root: Path) -> dict:
        artifact = root / "artifacts/prototype/page.html"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("<main>free prototype</main>\n", encoding="utf-8")
        state = root / "_generated/open-application-revision.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"opened_revision": 1}) + "\n", encoding="utf-8")
        registry, findings = experience_application_check.compile_application(root)
        self.assertEqual(findings, [])
        return registry

    def test_reviewer_attestation_binds_snapshot_not_ui_structure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            registry = self.reviewed_registry(root)
            attestation = root / "review.json"
            attestation.write_text(json.dumps({
                "schema_version": 4,
                "proposal_hash": "sha256:" + "1" * 64,
                "artifact_tree_hash": registry["artifact_tree_hash"],
                "application_package_set_hash": registry["package_set_hash"],
                "application_hash": registry["application_hash"],
                "application_revision": registry["application_revision"],
                "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                "reviewer_role": "experience-reviewer",
                "advisories": ["Consider documenting the loading state."],
            }) + "\n", encoding="utf-8")

            experience_compile.validate_reviewer_attestation(
                str(attestation), "sha256:" + "1" * 64, registry,
            )

    def test_reviewer_attestation_detects_stale_artifact_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            registry = self.reviewed_registry(root)
            attestation = root / "review.json"
            attestation.write_text(json.dumps({
                "schema_version": 4,
                "proposal_hash": "sha256:" + "2" * 64,
                "artifact_tree_hash": "sha256:" + "0" * 64,
                "application_package_set_hash": registry["package_set_hash"],
                "application_hash": registry["application_hash"],
                "application_revision": registry["application_revision"],
                "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                "reviewer_role": "experience-reviewer",
                "advisories": [],
            }) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stale"):
                experience_compile.validate_reviewer_attestation(
                    str(attestation), "sha256:" + "2" * 64, registry,
                )


if __name__ == "__main__":
    unittest.main()
