"""Git fixture repositories never leave Git writing into a tree a test removes."""

from __future__ import annotations

import ast
import contextlib
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import git_fixture  # noqa: E402

GIT_INIT = "git init".split()


def leading_strings(nodes: list[ast.expr]) -> list[str]:
    values = []
    for node in nodes:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            break
        values.append(node.value)
    return values


def direct_initializations(module: ast.AST) -> list[int]:
    """Lines that run `git init` in any form the suite uses, bypassing init_repository."""
    lines = []
    for node in ast.walk(module):
        if isinstance(node, (ast.List, ast.Tuple)):
            head = leading_strings(node.elts)
            flags_only = head[:1] == GIT_INIT[1:] and len(head) == len(node.elts) \
                and all(value.startswith("-") for value in head[1:])
            if head[:2] == GIT_INIT or flags_only:
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            head = leading_strings(node.args)
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if head[:2] == GIT_INIT or (name == "git" and head[:1] == GIT_INIT[1:]):
                lines.append(node.lineno)
    return sorted(lines)


class GitFixtureTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    def test_fixture_repository_never_starts_automatic_maintenance(self):
        with git_fixture.temporary_directory() as raw:
            for guarded in (True, False):
                with self.subTest(guarded=guarded):
                    root = Path(raw) / ("guarded" if guarded else "unguarded")
                    git_fixture.init_repository(root)
                    if not guarded:
                        for key, _value in git_fixture.AUTOMATIC_MAINTENANCE_OFF:
                            self.git(root, "config", "--unset", key)
                    # Force a pack-writing task on every command and keep it in
                    # the foreground, so any run is visible when commit returns.
                    for key, value in (
                        ("maintenance.autoDetach", "false"),
                        ("gc.autoDetach", "false"),
                        ("maintenance.loose-objects.enabled", "true"),
                        ("maintenance.loose-objects.auto", "-1"),
                    ):
                        self.git(root, "config", key, value)
                    (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
                    self.git(root, "add", "fixture.txt")
                    self.git(
                        root, "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qm", "fixture",
                    )
                    packs = list((root / ".git" / "objects" / "pack").glob("*.pack"))
                    self.assertEqual(bool(packs), not guarded)

    def fixture_tree(self) -> tempfile.TemporaryDirectory:
        temporary = tempfile.TemporaryDirectory()
        objects = Path(temporary.name) / "repository" / ".git" / "objects"
        (objects / "pack").mkdir(parents=True)
        (objects / "pack" / "pack-fixture.pack").write_bytes(b"pack")
        return temporary

    @contextlib.contextmanager
    def refilled_objects(self, times: int):
        """Recreate objects/pack each time removal empties .git/objects, as a detached repack does."""
        real_rmdir = os.rmdir
        refills = []

        def rmdir(path, *args, dir_fd=None, **kwargs):
            if len(refills) < times and os.path.basename(os.fspath(path)) == "objects":
                refills.append(path)
                os.mkdir(os.path.join(os.fspath(path), "pack"), dir_fd=dir_fd)
            return real_rmdir(path, *args, dir_fd=dir_fd, **kwargs)

        with mock.patch.object(os, "rmdir", side_effect=rmdir):
            yield refills

    def test_removal_outlasts_a_write_that_refills_the_object_store(self):
        plain = self.fixture_tree()
        with self.refilled_objects(times=1), self.assertRaises(OSError) as raised:
            plain.cleanup()
        self.assertEqual(raised.exception.errno, errno.ENOTEMPTY)
        shutil.rmtree(plain.name)

        guarded = self.fixture_tree()
        with self.refilled_objects(times=1) as refills:
            git_fixture.remove_temporary(guarded)
        self.assertEqual(len(refills), 1)
        self.assertFalse(Path(guarded.name).exists())

    def test_removal_raises_when_the_tree_keeps_refilling(self):
        temporary = self.fixture_tree()
        with self.refilled_objects(times=100), self.assertRaises(OSError):
            git_fixture.remove_temporary(temporary, attempts=2)
        self.assertTrue(Path(temporary.name).exists())
        shutil.rmtree(temporary.name)

    def test_every_fixture_repository_is_created_through_the_guarded_helper(self):
        """A repository initialized around the helper brings automatic maintenance back."""
        direct = {}
        for module in sorted(TESTS_DIR.glob("*.py")):
            if module.name == "git_fixture.py":
                continue
            lines = direct_initializations(ast.parse(module.read_text(encoding="utf-8")))
            if lines:
                direct[module.name] = lines
        self.assertEqual(direct, {}, "initialize fixture repositories with git_fixture.init_repository")


if __name__ == "__main__":
    unittest.main()
