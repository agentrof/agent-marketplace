"""Unit tests for the plugin's runtime enforcement scripts."""

import importlib.util
import io
import json
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


state_tool = load("state_tool")
artifact_check = load("artifact_check")
ownership_check = load("ownership_check")
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


class StateToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "workspace"
        self.ws.mkdir()
        (self.ws / "config.json").write_text('{"managed_by": "x"}', encoding="utf-8")
        self.constitution = Path(self.tmp.name) / "constitution.md"
        self.constitution.write_text("# Constitution\n", encoding="utf-8")
        self.brief = Path(self.tmp.name) / "brief.md"
        self.brief.write_text("# Brief\n- BR-001: rule.\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def init(self, run_id="r1"):
        return run(state_tool, [
            "init", "--workspace", str(self.ws), "--run-id", run_id,
            "--request", "test", "--constitution", str(self.constitution),
            "--brief", str(self.brief),
        ])

    def state_path(self, run_id="r1"):
        return self.ws / "runs" / run_id / "state.json"

    def test_init_snapshots_and_locks(self):
        code, _, _ = self.init()
        self.assertEqual(code, 0)
        run_dir = self.ws / "runs" / "r1"
        self.assertTrue((run_dir / "state.json").is_file())
        self.assertTrue((run_dir / "constitution.md").is_file())
        self.assertTrue((run_dir / "brief.snapshot.md").is_file())
        self.assertTrue((run_dir / "config.snapshot.json").is_file())
        self.assertTrue((self.ws / "runs" / ".lock").is_file())

    def test_second_init_refused_by_lock(self):
        self.init("r1")
        code, _, err = self.init("r2")
        self.assertEqual(code, 1)
        self.assertIn("lock", err)

    def test_transition_guard_blocks_skipping(self):
        self.init()
        code, _, err = run(state_tool, [
            "set-step", "--state", str(self.state_path()), "--step", "2",
            "--status", "in_progress",
        ])
        self.assertEqual(code, 1)
        self.assertIn("transition guard", err)

    def test_complete_guard_requires_all_steps_done(self):
        self.init()
        code, _, err = run(state_tool, [
            "set-run-status", "--state", str(self.state_path()), "--status", "complete",
        ])
        self.assertEqual(code, 1)
        self.assertIn("run-complete guard", err)

    def test_validate_flags_bad_enum_and_snake_case(self):
        self.init()
        state = json.loads(self.state_path().read_text())
        state["status"] = "sprinting"
        state["ownership"] = {"software-team-backend-developer": ["a/"]}
        self.state_path().write_text(json.dumps(state))
        code, _, err = run(state_tool, ["validate", "--state", str(self.state_path())])
        self.assertEqual(code, 1)
        self.assertIn("not in enum", err)
        self.assertIn("snake_case", err)

    def test_release_lock(self):
        self.init()
        code, _, _ = run(state_tool, ["release-lock", "--workspace", str(self.ws)])
        self.assertEqual(code, 0)
        self.assertFalse((self.ws / "runs" / ".lock").exists())


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


class OwnershipCheckTests(unittest.TestCase):
    def write_state(self, ownership):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"ownership": ownership}, tmp)
        tmp.close()
        return tmp.name

    def test_prefix_overlap_fails(self):
        path = self.write_state({
            "backend_developer": ["workspace/apps/backend/"],
            "frontend_developer": ["workspace/apps/backend/app/", "workspace/apps/frontend/"],
        })
        code, _, err = run(ownership_check, ["--state", path])
        self.assertEqual(code, 1)
        self.assertIn("overlaps", err)

    def test_disjoint_passes(self):
        path = self.write_state({
            "backend_developer": ["workspace/apps/backend/"],
            "frontend_developer": ["workspace/apps/frontend/"],
        })
        code, _, _ = run(ownership_check, ["--state", path])
        self.assertEqual(code, 0)


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
