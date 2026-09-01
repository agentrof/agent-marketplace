import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"


class BacklogCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_new_backlog_requires_an_explicit_mode(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs")
            self.assertNotEqual(result.returncode, 0)

    def test_manual_mode_requires_all_four_input_refs(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs", "--planning-mode", "manual",
                                  "--input-ref", "[[business-analysis/a/space|A]]")
            self.assertNotEqual(result.returncode, 0)

    def test_requirement_mode_requires_requirement_ref(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli("init", "--docs", Path(raw) / "docs", "--planning-mode", "requirement")
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
