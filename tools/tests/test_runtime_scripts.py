"""Unit tests for the software-team plugin's file-facing enforcement scripts
(run state and ownership moved to the PMO plugin's CLI; see test_pmo_cli)."""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "software-team" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact_check = load("artifact_check")
contract_check = load("contract_check")
atomic_tripwire = load("atomic_tripwire")


def run(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = module.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class ArtifactCheckTests(unittest.TestCase):
    def test_missing_sections_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.md"
            f.write_text("# Title\n\n## Purpose\ntext\n", encoding="utf-8")
            code, _, err = run(artifact_check, [
                "--path", str(f), "--require-sections", "Purpose,Business Rules",
            ])
            self.assertEqual(code, 1)
            self.assertIn("Business Rules", err)
            code, _, _ = run(artifact_check, ["--path", str(f), "--require-sections", "Purpose"])
            self.assertEqual(code, 0)

    def test_empty_artifact_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.md"
            f.write_text("  \n", encoding="utf-8")
            code, _, _ = run(artifact_check, ["--path", str(f)])
            self.assertEqual(code, 1)


class ContractCheckTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "api-contract.md"
            f.write_text(text, encoding="utf-8")
            return run(contract_check, ["--contract", str(f)])

    def test_endpoint_without_error_cases_fails(self):
        code, _, err = self.check(
            "# Contract\n\n## GET /things\nReturns things.\n\n## POST /things\n"
            "Creates.\n\nError cases: 401, 422, 409.\n"
        )
        self.assertEqual(code, 1)
        self.assertIn("GET /things", err)

    def test_all_endpoints_covered_passes(self):
        code, _, _ = self.check(
            "## GET /things\nError cases: 401.\n\n## POST /things\nError cases: 401, 422.\n"
        )
        self.assertEqual(code, 0)

    def test_zero_endpoints_is_an_error(self):
        code, _, _ = self.check("# Contract\nNothing here.\n")
        self.assertEqual(code, 2)


class TripwireTests(unittest.TestCase):
    def test_schema_touch_trips(self):
        code, _, err = run(atomic_tripwire, [
            "--files", "workspace/apps/backend/app/models/customer.py",
        ])
        self.assertEqual(code, 1)
        self.assertIn("NOT ATOMIC", err)

    def test_clean_change_passes(self):
        code, _, _ = run(atomic_tripwire, [
            "--files", "workspace/apps/frontend/src/SaveButton.tsx",
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
