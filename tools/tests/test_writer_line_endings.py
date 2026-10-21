"""Package writers emit LF bytes where the platform line separator is CRLF.

Native Windows turns every newline=None text-mode write into CRLF. Under the
managed ``workspace/docs/** -text`` rule those bytes reach the repository
verbatim, so a file written there flips every line when another OS rewrites it.
"""

from __future__ import annotations

import _pyio
import ast
import builtins
import contextlib
import io
import os
import pathlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"
SCRIPTS = PLUGIN / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import atomic_file  # noqa: E402

SAMPLE = "first line\nsecond line\n"
MODE = re.compile(r"[rwxabtU+]{1,4}")
TEMPORARY_FILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}


@contextlib.contextmanager
def windows_text_mode():
    """Translate newline=None text writes to CRLF, as native Windows does.

    CPython's C text layer fixes its write separator when it is compiled, so
    the pure-Python layer, which reads os.linesep, stands in for it.
    """
    def windows_open(file, mode="r", buffering=-1, encoding=None, errors=None,
                     newline=None, closefd=True, opener=None):
        return _pyio.open(file, mode, buffering, encoding, errors, newline,
                          closefd, opener)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(os, "linesep", "\r\n"))
        stack.enter_context(mock.patch.object(io, "open", windows_open))
        stack.enter_context(mock.patch.object(builtins, "open", windows_open))
        # Python 3.10 binds Path.open to io.open once, on its path accessor.
        accessor = getattr(pathlib, "_NormalAccessor", None)
        if accessor is not None and vars(accessor).get("open") is io.open:
            stack.enter_context(mock.patch.object(
                accessor, "open", staticmethod(windows_open)))
        yield


def keyword(node: ast.Call, name: str):
    return next((item.value for item in node.keywords if item.arg == name), None)


def translating_write(node: ast.Call) -> str | None:
    """Name the call when it can write text with the platform separator."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    owner = func.value.id if isinstance(func, ast.Attribute) \
        and isinstance(func.value, ast.Name) else None
    if name == "write_text":
        # Path.write_text takes no newline argument before Python 3.10; a
        # two-argument call is setup's own RefreshSnapshot.write_text.
        if keyword(node, "encoding") is not None or len(node.args) == 1:
            return "Path.write_text translates newlines"
        return None
    if name in TEMPORARY_FILES:
        mode = keyword(node, "mode") or (node.args[0] if node.args else None)
        if mode is None:
            return None
    elif name == "open" and owner == "os":
        return None
    elif (name == "open" and not isinstance(func, ast.Attribute)) \
            or (name in {"open", "fdopen"} and owner in {"io", "os"}):
        mode = keyword(node, "mode") or (node.args[1] if len(node.args) > 1 else None)
    elif name == "open":
        mode = keyword(node, "mode") or (node.args[0] if node.args else None)
    else:
        return None
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return None
    value = mode.value
    if not MODE.fullmatch(value) or "b" in value or not set(value) & set("wax+"):
        return None
    newline = keyword(node, "newline")
    if isinstance(newline, ast.Constant) and newline.value in {"\n", ""}:
        return None
    return f"text mode {value!r} without newline='\\n'"


def package_sources() -> list[Path]:
    overlays = (path for path in (ROOT / "platforms").rglob("*.py")
                if "overlay" in path.parts)
    return sorted({*PLUGIN.rglob("*.py"), *overlays})


class WriterLineEndingTests(unittest.TestCase):
    def test_the_shared_writer_emits_lf_under_a_crlf_platform_separator(self):
        # Every package writer family is atomic_file.replace_text.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "AGENTS.md"
            with windows_text_mode():
                atomic_file.replace_text(path, SAMPLE)
            self.assertEqual(path.read_bytes(), SAMPLE.encode("utf-8"))

    def test_no_package_source_writes_text_with_the_platform_separator(self):
        findings = []
        for source in package_sources():
            tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    reason = translating_write(node)
                    if reason:
                        findings.append(
                            f"{source.relative_to(ROOT).as_posix()}:"
                            f"{node.lineno}: {reason}"
                        )
        self.assertEqual(findings, [], "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
