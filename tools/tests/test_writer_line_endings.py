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
import importlib.util
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
SHARED_TEAM = ROOT / "platforms" / "shared" / "_team" / "overlay" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import project_config  # noqa: E402
import setup_project  # noqa: E402
import vault_check  # noqa: E402

SAMPLE = "first line\nsecond line\n"
MODE = re.compile(r"[rwxabtU+]{1,4}")
TEMPORARY_FILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}


def load(name: str, path: Path, *imports: Path):
    """Load a shipped script whose sibling imports live in other source trees."""
    added = [str(item) for item in imports if str(item) not in sys.path]
    sys.path[:0] = added
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for item in added:
            sys.path.remove(item)
    return module


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
    def test_the_emulated_platform_separator_reaches_a_translating_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "control.md"
            with windows_text_mode():
                path.write_text(SAMPLE, encoding="utf-8")
            self.assertEqual(
                path.read_bytes(), SAMPLE.replace("\n", "\r\n").encode("utf-8")
            )

    def test_every_writer_family_emits_lf_under_a_crlf_platform_separator(self):
        for family, write in self.families().items():
            with self.subTest(family=family), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with windows_text_mode(), \
                        contextlib.redirect_stdout(io.StringIO()):
                    written = write(root)
                self.assertTrue(written)
                for path in written:
                    data = path.read_bytes()
                    self.assertTrue(b"\n" in data, f"{path.name} holds no line")
                    self.assertFalse(
                        b"\r" in data, f"{path.name} holds carriage returns"
                    )

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

    def families(self) -> dict:
        return {
            "setup refresh": self.setup_refresh,
            "workspace config": self.workspace_config,
            "vault": self.vault,
            "vault hook": self.vault_hook,
            "project instructions": self.project_instructions,
            "codex projection": self.codex_projection,
        }

    def setup_refresh(self, root: Path) -> list[Path]:
        path = root / ".gitattributes"
        setup_project.atomic_text(path, SAMPLE)
        return [path]

    def workspace_config(self, root: Path) -> list[Path]:
        path = root / "workspace" / "config.json"
        project_config.atomic(path, {
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        })
        return [path]

    def vault(self, root: Path) -> list[Path]:
        note = root / "note.md"
        note.write_bytes(
            b"---\ntitle: Note\nstatus: draft\ntags:\n  - status/draft\n---\n"
            b"\n# Note\n"
        )
        vault_check.restamp(note, {"status": "approved"}, "approved")
        graph = root / ".obsidian" / "graph.json"
        graph.parent.mkdir()
        graph.write_bytes(b'{"colorGroups": []}\n')
        vault_check.standardize_graph_colors(
            root, vault_check.load_policy(vault_check.DEFAULT_POLICY),
        )
        return [note, graph]

    def vault_hook(self, root: Path) -> list[Path]:
        hook = load(
            "line_ending_vault_hook",
            ROOT / "platforms/shared/software-engineering-team/overlay/scripts"
            / "vault_hook.py",
        )
        replaced, created = root / "replaced.json", root / "created.json"
        exclusive = root / "exclusive.lock"
        hook.atomic_replace_text(replaced, SAMPLE)
        hook.atomic_create_text(created, SAMPLE)
        hook.exclusive_create_text(exclusive, SAMPLE)
        return [replaced, created, exclusive]

    def project_instructions(self, root: Path) -> list[Path]:
        instructions = load(
            "line_ending_project_instructions", SHARED_TEAM / "project_instructions.py",
        )
        path = root / "AGENTS.md"
        instructions.atomic_write(path, SAMPLE)
        return [path]

    def codex_projection(self, root: Path) -> list[Path]:
        projection = load(
            "line_ending_generate_codex_project",
            ROOT / "platforms/codex/_team/overlay/scripts/generate_codex_project.py",
            SHARED_TEAM,
        )
        path = root / ".codex" / "agents" / "reviewer.toml"
        projection.atomic_write(path, SAMPLE)
        return [path]


if __name__ == "__main__":
    unittest.main()
