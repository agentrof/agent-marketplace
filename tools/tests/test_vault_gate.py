from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import unittest
import zipfile
from pathlib import Path, PurePosixPath

from tools.tests import backlog_fixture
from tools.tests.git_fixture import init_repository


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins/software-engineering-team"
SETUP = PLUGIN / "scripts/setup_project.py"
GATE_INSTALLER = PLUGIN / "scripts/vault_gate.py"


def literal_text(token: tokenize.TokenInfo) -> str | None:
    try:
        value = ast.literal_eval(token.string)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def named_data_paths(source: bytes) -> set[str]:
    """Paths under a package data root that one script names.

    A name is a string literal, or string literals joined with / across
    lines. Read from tokens, so the check does not reuse the installer's
    own scan.
    """
    named, run, joined = set(), [], False
    for token in tokenize.tokenize(io.BytesIO(source).readline):
        if token.type in (tokenize.NL, tokenize.COMMENT):
            continue
        text = literal_text(token) if token.type == tokenize.STRING else None
        if text is not None and (joined or not run):
            run, joined = [*run, text], False
        elif token.exact_type == tokenize.SLASH and run and not joined:
            joined = True
        else:
            parts = PurePosixPath("/".join(run)).parts
            if len(parts) > 1 and parts[0] in ("skill-content", "templates"):
                named.add("/".join(parts))
            run, joined = ([text] if text is not None else []), False
    return named


class PortableVaultGateTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        init_repository(root)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / ".github/agentrof/vault-gate.pyz"

    def test_archive_has_opaque_snapshot_checker_without_ui_templates(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            listed = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertIn(listed.returncode, (0, 1), listed.stdout + listed.stderr)
            installed = subprocess.run(
                [sys.executable, str(GATE_INSTALLER), "install", "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            self.assertTrue(gate.is_file())

    def test_gate_does_not_reject_arbitrary_prototype_extensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            artifact = project / "workspace/docs/experience-design/artifacts/src/demo.tsx"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("export const demo = true\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            payload = json.loads(result.stdout)
            text = json.dumps(payload)
            self.assertNotIn("only artifacts/application.html", text)
            self.assertNotIn("forbidden by the subtree's exact artifact-path contract", text)

    def test_archive_checks_an_approved_backlog_as_the_package_does(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary).resolve()
            gate = self.setup_project(project)
            backlog_fixture.make_approved_backlog(project / "workspace/docs")
            verdicts = []
            for checker in (gate, GATE_INSTALLER):
                result = subprocess.run(
                    [sys.executable, str(checker), "check", "--project-root", str(project), "--json"],
                    capture_output=True, text=True, check=False,
                )
                self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                verdicts.append({
                    item["name"]: item for item in json.loads(result.stdout)["results"]
                })
            archive, package = verdicts
            backlog = archive["backlog:approved"]
            self.assertTrue(backlog["ok"], backlog["stdout"] + backlog["stderr"])
            self.assertEqual(
                {name: item["ok"] for name, item in archive.items()},
                {name: item["ok"] for name, item in package.items()},
            )

    def test_archive_carries_every_package_data_path_its_scripts_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            installed = subprocess.run(
                [sys.executable, str(GATE_INSTALLER), "install", "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            with zipfile.ZipFile(project / ".github/agentrof/vault-gate.pyz") as archive:
                entries = set(archive.namelist())
                named = set().union(*(
                    named_data_paths(archive.read(entry))
                    for entry in entries if entry.endswith(".py")
                ))
        self.assertIn("skill-content/configure/data/process-switches.json", named)
        missing = []
        for relative in sorted(named):
            source = PLUGIN / relative
            files = [source] if source.is_file() else sorted(
                path for path in source.rglob("*") if path.is_file())
            missing += [
                path.relative_to(PLUGIN).as_posix() for path in files
                if path.relative_to(PLUGIN).as_posix() not in entries
            ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
