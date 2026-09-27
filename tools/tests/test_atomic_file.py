"""Atomic writers leave a file the mode a plain write would give it.

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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import architecture_compile  # noqa: E402
import atomic_file  # noqa: E402
import delivery_compile  # noqa: E402
import experience_application_check  # noqa: E402
import experience_compile  # noqa: E402
import operation_compile  # noqa: E402
import project_config  # noqa: E402
import requirement_compile  # noqa: E402
import setup_project  # noqa: E402
import vault_check  # noqa: E402
from tools.tests.test_writer_line_endings import SHARED_TEAM, load  # noqa: E402

PLUGIN_ID = "fixture-plugin"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def target(temporary: str, name: str) -> Path:
    """A package-projected plugin file, which every writer here can write."""
    return Path(temporary) / "vault" / ".obsidian" / "plugins" / PLUGIN_ID / name


def reconcile_payload(path: Path, text: str) -> None:
    """Converge one projected plugin file as `vault_check.py normalize` does.

    The reconcile writes a file the vault lacks only where the previous
    package shipped a directory, so a missing path first becomes one.
    """
    root = path.parents[3]
    payload = root.parent / "payload"
    source = payload / "plugins" / PLUGIN_ID / path.name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(text.encode("utf-8"))
    if not path.exists():
        path.mkdir(parents=True)
    vault_check.payload_reconcile(root, {"community_plugins": [PLUGIN_ID]}, payload)


@unittest.skipIf(os.name == "nt", "POSIX file mode contract")
class AtomicWriterModeTests(unittest.TestCase):
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
            "experience compiler": lambda path, text: (
                experience_compile.atomic_write_bytes(path, text.encode("utf-8"))
            ),
            "experience application": lambda path, text: (
                experience_application_check._atomic_write(
                    path, text.encode("utf-8"),
                )
            ),
            "vault payload reconcile": reconcile_payload,
        }

    def test_every_atomic_writer_gives_a_new_file_the_umask_mode(self):
        for umask, expected in ((0o022, 0o644), (0o027, 0o640)):
            previous = os.umask(umask)
            try:
                for name, write in self.writers().items():
                    with self.subTest(writer=name, umask=oct(umask)), \
                            tempfile.TemporaryDirectory() as temporary:
                        path = target(temporary, "created.js")
                        write(path, "created\n")
                        self.assertTrue(path.is_file())
                        self.assertEqual(mode(path), expected)
            finally:
                os.umask(previous)

    def test_every_atomic_writer_keeps_an_existing_file_mode(self):
        for name, write in self.writers().items():
            with self.subTest(writer=name), \
                    tempfile.TemporaryDirectory() as temporary:
                path = target(temporary, "existing.js")
                path.parent.mkdir(parents=True)
                path.write_bytes(b"before\n")
                path.chmod(0o664)
                write(path, "after\n")
                self.assertNotEqual(path.read_bytes(), b"before\n")
                self.assertEqual(mode(path), 0o664)


class AtomicFileFailureTests(unittest.TestCase):
    def test_a_failed_replacement_removes_its_read_only_temporary(self):
        real_unlink = Path.unlink

        def windows_unlink(path: Path, missing_ok: bool = False) -> None:
            # Native Windows refuses to delete a read-only file.
            if path.exists() and not os.access(path, os.W_OK):
                raise PermissionError(13, "Access is denied", str(path))
            real_unlink(path, missing_ok=missing_ok)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "target.md"
            path.write_bytes(b"before\n")
            path.chmod(0o444)
            with mock.patch.object(
                atomic_file.os, "replace",
                side_effect=PermissionError(13, "Access is denied"),
            ), mock.patch.object(Path, "unlink", windows_unlink), \
                    self.assertRaises(PermissionError):
                atomic_file.replace_bytes(path, b"after\n")
            self.assertEqual(
                [item.name for item in Path(temporary).iterdir()], ["target.md"],
            )
            self.assertEqual(path.read_bytes(), b"before\n")
            path.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
