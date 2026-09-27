"""Atomic text writers leave a file the mode a plain write would give it.

tempfile.mkstemp creates its file owner-only and os.replace keeps that mode,
so every writer that replaces through a temporary file sets the mode first:
a new file takes 0666 less the umask and an existing file keeps its own.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import architecture_compile  # noqa: E402
import delivery_compile  # noqa: E402
import operation_compile  # noqa: E402
import project_config  # noqa: E402
import requirement_compile  # noqa: E402
import setup_project  # noqa: E402
from tools.tests.test_writer_line_endings import SHARED_TEAM, load  # noqa: E402


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@unittest.skipIf(os.name == "nt", "POSIX file mode contract")
class AtomicTextModeTests(unittest.TestCase):
    def writers(self) -> dict:
        instructions = load(
            "mode_project_instructions", SHARED_TEAM / "project_instructions.py",
        )
        projection = load(
            "mode_generate_codex_project",
            ROOT / "platforms/codex/_team/overlay/scripts/generate_codex_project.py",
            SHARED_TEAM,
        )
        return {
            "setup": setup_project.atomic_text,
            "workspace config": lambda path, text: project_config.atomic(
                path, {"text": text},
            ),
            "requirement": requirement_compile.atomic_text,
            "delivery": delivery_compile.atomic_text,
            "operation": operation_compile.atomic_text,
            "architecture": architecture_compile.atomic,
            "project instructions": instructions.atomic_write,
            "codex projection": projection.atomic_write,
        }

    def test_every_atomic_text_writer_gives_a_new_file_the_umask_mode(self):
        for umask, expected in ((0o022, 0o644), (0o027, 0o640)):
            previous = os.umask(umask)
            try:
                for name, write in self.writers().items():
                    with self.subTest(writer=name, umask=oct(umask)), \
                            tempfile.TemporaryDirectory() as temporary:
                        path = Path(temporary) / "created.md"
                        write(path, "created\n")
                        self.assertEqual(mode(path), expected)
            finally:
                os.umask(previous)

    def test_every_atomic_text_writer_keeps_an_existing_file_mode(self):
        for name, write in self.writers().items():
            with self.subTest(writer=name), \
                    tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "existing.md"
                path.write_bytes(b"before\n")
                path.chmod(0o664)
                write(path, "after\n")
                self.assertNotEqual(path.read_bytes(), b"before\n")
                self.assertEqual(mode(path), 0o664)


if __name__ == "__main__":
    unittest.main()
