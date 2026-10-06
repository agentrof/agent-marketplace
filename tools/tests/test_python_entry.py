"""Isolation and observable entry-point behavior for in-process rule tests."""

import os
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tools.tests import python_entry


class PythonEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def script(self, text):
        path = self.root / "entry.py"
        path.write_text(text, encoding="utf-8")
        return path

    def test_binary_streams_argv_environment_cwd_and_exit_code(self):
        entry = self.script(
            "import os, sys\n"
            "assert sys.argv[1] == 'two words'\n"
            "assert os.getcwd() == os.environ['CASE_ROOT']\n"
            "assert 'CASE_ABSENT' not in os.environ\n"
            "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
            "sys.stderr.write('refused\\n')\n"
            "raise SystemExit(2)\n")
        result = python_entry.run([entry, "two words"], cwd=self.root,
                                  env={"CASE_ROOT": str(self.root)}, input="ö byte input\n")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, "ö byte input\n", "refused\n"))

    def test_exit_none_and_text_match_interpreter_semantics(self):
        for expression, expected in (("None", (0, "")), ("'reason'", (1, "reason\n"))):
            with self.subTest(expression=expression):
                result = python_entry.run([self.script(f"raise SystemExit({expression})\n")])
                self.assertEqual((result.returncode, result.stderr), expected)

    def test_captured_crlf_and_cr_are_normalized_like_subprocess_text_mode(self):
        entry = self.script(
            "import sys\n"
            "sys.stdout.buffer.write(b'first\\r\\nsecond\\rthird\\n')\n"
            "sys.stderr.buffer.write(b'error\\r\\nretry\\r')\n"
            "sys.stdout.reconfigure(newline='\\r\\n')\n"
            "sys.stderr.reconfigure(newline='\\r\\n')\n"
            "print('Windows output')\n"
            "print('Windows error', file=sys.stderr)\n")
        result = python_entry.run([entry])
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "first\nsecond\nthird\nWindows output\n",
                          "error\nretry\nWindows error\n"))

    def test_native_lanes_register_each_entry_case_explicitly(self):
        from tools import ci_tests
        root = Path(__file__).resolve().parents[2]
        ids = set(ci_tests.inventory(root)[0])
        policy = json.loads((root / "tools/data/ci-test-policy.json").read_text(encoding="utf-8"))
        for group in ("platform", "macos", "windows"):
            with self.subTest(group=group):
                self.assertEqual(set(policy["groups"][group]["tests"]) - ids, set())
        entry_cases = {name for name in ids
                       if name.startswith("tools.tests.test_python_entry.PythonEntryTests.")}
        self.assertTrue(entry_cases)
        self.assertTrue(entry_cases <= set(policy["groups"]["platform"]["tests"]))

    def test_state_and_imports_are_restored_even_when_the_entry_raises(self):
        old = (sys.argv, sys.path, sys.stdin, sys.stdout, sys.stderr, sys.version_info,
               tempfile.tempdir, Path.cwd(), dict(os.environ))
        (self.root / "entry_sibling.py").write_text("VALUE = 'first'\n", encoding="utf-8")
        entry = self.script("import entry_sibling, tempfile\n"
                            "print(entry_sibling.VALUE)\n"
                            "tempfile.tempdir = 'changed'\n"
                            "raise ValueError('entry failed')\n")
        with self.assertRaisesRegex(ValueError, "entry failed"):
            python_entry.run([entry], cwd=self.root, env={}, version="3.9.6")
        current = (sys.argv, sys.path, sys.stdin, sys.stdout, sys.stderr, sys.version_info,
                   tempfile.tempdir, Path.cwd(), dict(os.environ))
        self.assertEqual(current, old)
        self.assertNotIn("entry_sibling", sys.modules)
        (self.root / "entry_sibling.py").write_text("VALUE = 'second'\n", encoding="utf-8")
        entry = self.script("import entry_sibling\nprint(entry_sibling.VALUE)\n")
        self.assertEqual(python_entry.run([entry]).stdout, "second\n")

    def test_script_cannot_evade_the_unit_process_boundary(self):
        from tools.ci_tests import UnitBoundary, UnitBoundaryError
        entry = self.script("import subprocess\nsubprocess.run(['never-start-this'])\n")
        with UnitBoundary(unit_only=True) as boundary:
            boundary.watch(self.id())
            try:
                with self.assertRaises(UnitBoundaryError):
                    python_entry.run([entry])
            finally:
                boundary.release()
