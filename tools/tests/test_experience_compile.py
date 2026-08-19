import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"


class ExperienceCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_legacy_program_commands_are_rejected(self):
        result = self.run_cli("init-program", "--root", "/tmp/x", "--program", "PRG-001")
        self.assertNotEqual(result.returncode, 0)

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


if __name__ == "__main__":
    unittest.main()
