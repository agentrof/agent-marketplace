"""A role that starts background work waits for its output files in its own turn,
bounded by the wait bound, instead of relying on a completion notice the host
may deliver to another session."""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import await_outputs  # noqa: E402

MARKER = re.compile(await_outputs.DEFAULT_MARKER, re.MULTILINE)


class AwaitOutputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_main(self, *argv: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = await_outputs.main(list(argv))
        return code, json.loads(output.getvalue())

    def test_every_final_output_returns_at_once(self):
        for name in ("a.md", "b.md"):
            (self.root / name).write_text("findings\nSELF-CHECK: satisfied\n", encoding="utf-8")
        code, value = self.run_main("--file", str(self.root / "a.md"), "--file", str(self.root / "b.md"))
        self.assertEqual(code, 0)
        self.assertEqual(value["pending"], [])
        self.assertEqual(len(value["done"]), 2)
        self.assertNotIn("next", value)

    def test_a_missing_or_unfinished_output_stays_pending_until_the_bound(self):
        (self.root / "partial.md").write_text("still reading\n", encoding="utf-8")
        code, value = self.run_main("--file", str(self.root / "partial.md"),
                                    "--file", str(self.root / "absent.md"), "--seconds", "0.2")
        self.assertEqual(code, await_outputs.PENDING)
        self.assertEqual(len(value["pending"]), 2)
        self.assertEqual(value["missing"], [str((self.root / "absent.md").resolve())])
        self.assertIn("call await_outputs.py again", value["next"])

    def test_the_call_returns_as_soon_as_the_last_output_turns_final(self):
        path = self.root / "late.md"
        timer = threading.Timer(0.3, lambda: path.write_text("SELF-CHECK: done\n", encoding="utf-8"))
        timer.start()
        self.addCleanup(timer.cancel)
        value = await_outputs.await_outputs([path], MARKER, 5)
        self.assertEqual(value["pending"], [])
        self.assertLess(value["waited_seconds"], 5)

    def test_a_custom_marker_and_the_wait_bound(self):
        (self.root / "run.log").write_text("exit_code: 0\n", encoding="utf-8")
        self.assertEqual(self.run_main("--file", str(self.root / "run.log"), "--marker", r"^exit_code: \d+")[0], 0)
        bound = await_outputs.wait_bound()
        for seconds in ("0", str(bound + 1)):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                await_outputs.main(["--file", str(self.root / "run.log"), "--seconds", seconds])


if __name__ == "__main__":
    unittest.main()
