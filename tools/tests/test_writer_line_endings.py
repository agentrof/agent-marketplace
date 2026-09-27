"""Package writers emit LF bytes where the platform line separator is CRLF.

Native Windows turns every newline=None text-mode write into CRLF. Under the
managed ``workspace/docs/** -text`` rule those bytes reach the repository
verbatim, so a file written there flips every line when another OS rewrites it.
"""

from __future__ import annotations

import _pyio
import argparse
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
UI_DESIGN = PLUGIN / "skill-content" / "ui-ux-design" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import architecture_compile  # noqa: E402
import ba_compile  # noqa: E402
import backlog_compile  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_governance  # noqa: E402
import design_system_compile  # noqa: E402
import landscape_check  # noqa: E402
import operation_compile  # noqa: E402
import project_config  # noqa: E402
import requirement_compile  # noqa: E402
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
            "requirement records": self.requirement_records,
            "delivery records": self.delivery_records,
            "delivery writer receipts": self.delivery_writer_receipts,
            "delivery governance": self.delivery_governance,
            "operation contracts": self.operation_contracts,
            "architecture records": self.architecture_records,
            "business analysis": self.business_analysis,
            "backlog": self.backlog,
            "design system": self.design_system,
            "solution landscape": self.solution_landscape,
            "vault": self.vault,
            "vault hook": self.vault_hook,
            "project instructions": self.project_instructions,
            "codex projection": self.codex_projection,
            "qa scenario report": self.qa_scenario_report,
            "ui design system": self.ui_design_system,
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

    def requirement_records(self, root: Path) -> list[Path]:
        path = root / "requirements" / "req-001-checkout.md"
        requirement_compile.atomic_text(path, SAMPLE)
        return [path]

    def delivery_records(self, root: Path) -> list[Path]:
        path = root / "delivery" / "item.md"
        delivery_compile.atomic_text(path, SAMPLE)
        return [path]

    def delivery_writer_receipts(self, root: Path) -> list[Path]:
        path = root / "writer-receipt.json"
        delivery_git._write_writer_receipt_locked(path, {
            "delivery_id": "DLV-001", "story_id": "AUTH-01",
        })
        return [path]

    def delivery_governance(self, root: Path) -> list[Path]:
        docs = root / "docs"
        docs.mkdir()
        delivery_governance.init(argparse.Namespace(
            docs=str(docs), max_parallel=2,
        ))
        return [docs / "maps" / "delivery.md", delivery_governance.path_for(docs)]

    def operation_contracts(self, root: Path) -> list[Path]:
        docs = root / "docs"
        docs.mkdir()
        operation_compile.init(argparse.Namespace(
            docs=str(docs), kind="verification", constrained_by=[],
        ))
        workflow = root / ".github" / "workflows" / "tests.yml"
        operation_compile.atomic_text(workflow, SAMPLE)
        return [
            docs / "maps" / "operation.md",
            operation_compile.contract_path(docs, "verification"), workflow,
        ]

    def architecture_records(self, root: Path) -> list[Path]:
        path = root / "system-architecture" / "architecture.md"
        architecture_compile.atomic(path, SAMPLE)
        return [path]

    def business_analysis(self, root: Path) -> list[Path]:
        schema = ba_compile.load_schema(ba_compile.DEFAULT_SCHEMA)
        stub = root / "glossary.md"
        ba_compile.write_stub(schema, stub, "glossary", "Glossary")
        space = root / "space.md"
        space.write_bytes(b"# Space\n")
        ba_compile.atomic_replace(space, SAMPLE)
        return [stub, space]

    def backlog(self, root: Path) -> list[Path]:
        home = root / "home.md"
        home.write_bytes(b"# Home\n")
        backlog_compile.ensure_home_map(root)
        note = root / "backlog.md"
        note.write_bytes(b"# Backlog\n")
        backlog_compile.append_nav(note, ["[[maps/backlog|Backlog map]]"])
        return [home, note]

    def design_system(self, root: Path) -> list[Path]:
        master = root / "MASTER.md"
        master.write_bytes(
            b"---\ntype: design-master\nstatus: draft\ntags:\n"
            b"  - status/draft\n---\n\n# Master\n"
        )
        design_system_compile.rewrite_frontmatter(
            master, {"status": "approved"}, set(),
        )
        return [master]

    def solution_landscape(self, root: Path) -> list[Path]:
        landscape = root / "landscape.md"
        landscape.write_bytes(b"---\nstatus: draft\n---\n\n# Landscape\n")
        landscape_check.rewrite_frontmatter(landscape, {"status": "approved"})
        return [landscape]

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

    def qa_scenario_report(self, root: Path) -> list[Path]:
        report = load(
            "line_ending_scenario_report",
            PLUGIN / "skill-content/qa-verification/scripts/scenario_report.py",
        )
        brief, junit = root / "brief.md", root / "junit.xml"
        brief.write_bytes(b"- AC-001\n")
        junit.write_bytes(
            b'<testsuite><testcase classname="suite" name="test_AC-001"/>'
            b"</testsuite>\n"
        )
        output = root / "coverage.json"
        report.main([
            "--brief", str(brief), "--junit", str(junit),
            "--json-out", str(output),
        ])
        return [output]

    def ui_design_system(self, root: Path) -> list[Path]:
        generator = load(
            "line_ending_design_system", UI_DESIGN / "design_system.py", UI_DESIGN,
        )
        result = generator.persist_design_system(
            {"project_name": "Demo"}, page="Checkout",
            output_dir=str(root / "design-system"),
        )
        return [Path(value) for value in result["created_files"]]


if __name__ == "__main__":
    unittest.main()
