import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"
sys.path.insert(0, str(COMPILER.parent))
import experience_compile


class ExperienceCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_legacy_program_commands_are_rejected(self):
        result = self.run_cli("init-program", "--root", "/tmp/x", "--program", "PRG-001")
        self.assertNotEqual(result.returncode, 0)

    def test_removed_process_local_artifact_command_is_rejected(self):
        result = self.run_cli(
            "init-artifact", "--experience-root", "/tmp/checkout"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_child_status_is_rejected_by_living_package_check(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\nstatus: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\ninput_bindings:\n---\n# Checkout\n",
                encoding="utf-8",
            )
            child = package / "journeys/checkout-journey.md"
            child.parent.mkdir()
            child.write_text(
                "---\ntype: journey\nid: JRN-001\nrevision: 1\nstatus: approved\nrecord_state: active\nderives_from:\n  - marketplace:PRC-001\n---\n# Checkout\n",
                encoding="utf-8",
            )
            result = self.run_cli("check", "--experience-root", package, "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("child records cannot carry approval state", result.stdout)

    def test_active_state_requires_canonical_state_class(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\n"
                "status: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\n"
                "input_bindings:\n---\n# Checkout\n",
                encoding="utf-8",
            )
            missing = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("requires --state-class", missing.stderr)

            created = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
                "--state-class", "ordinary",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            state = package / "states/ready-state.md"
            state.write_text(
                state.read_text(encoding="utf-8").replace(
                    "state_class: ordinary\n", "", 1,
                ),
                encoding="utf-8",
            )
            checked = self.run_cli(
                "check", "--experience-root", package, "--json",
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("active state needs a canonical state_class", checked.stdout)

    def test_exact_lifecycle_json_rejects_boolean_integer_aliases(self):
        hashes = {
            key: "sha256:" + "1" * 64
            for key in (
                "source_hash", "package_set_hash", "coverage_hash",
                "application_hash", "runtime_sha256",
                "design_system_package_hash",
            )
        }
        preimage = {
            "exists": True, "status": "approved", "revision": True,
            **hashes,
        }
        self.assertFalse(experience_compile.exact_application_preimage(preimage))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            state = root / "_generated/open-application-revision.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({
                "schema_version": True,
                "proposal_hash": "sha256:" + "2" * 64,
                "application_action": "update",
                "package_actions_hash": "sha256:" + "3" * 64,
                "expected_application": {**preimage, "revision": 1},
                "opened_revision": 2,
                "phase": "draft",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid exact schema"):
                experience_compile.read_open_application_state(root)


if __name__ == "__main__":
    unittest.main()
