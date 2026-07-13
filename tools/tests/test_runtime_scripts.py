"""Unit tests for the software-engineering-team plugin's file-facing enforcement scripts
(run state and ownership moved to the PMO plugin's CLI; see test_pmo_cli)."""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "software-engineering-team" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact_check = load("artifact_check")
contract_check = load("contract_check")
atomic_tripwire = load("atomic_tripwire")
landscape_check = load("landscape_check")


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


class LandscapeCheckTests(unittest.TestCase):
    LOG = (
        "# Decision Log\n\n## Summary\n| id | status |\n|---|---|\n\n"
        "## Decisions\n\n## SD-001\n**Title:** Baseline\n**Status:** accepted\n\n"
        "## SD-002\n**Title:** Old queue\n**Status:** superseded\n"
    )
    LAND_OK = (
        "# Landscape\n\n## Summary\nok\n\n## Current\nx\n\n## Target\n"
        "Adopt queue (SD-001)\n\n## Transition\n\n## Components\n"
        "| component | verdict | decision | engagement | status |\n|---|---|---|---|---|\n"
        "| queue | buy | [SD-001](decision-log.md#sd-001) | [q](engagements/q.md) | decided |\n"
    )

    def _tree(self, tmp, log, land, engagement=None):
        tree = Path(tmp) / "solution-design"
        (tree / "engagements").mkdir(parents=True)
        (tree / "decision-log.md").write_text(log, encoding="utf-8")
        (tree / "landscape.md").write_text(land, encoding="utf-8")
        if engagement is not None:
            (tree / "engagements" / "q.md").write_text(engagement, encoding="utf-8")
        return str(tree)

    def test_consistent_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, self.LAND_OK,
                "# Q\n\n## Summary\nStatus: approved 2026-07-13\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, _ = run(landscape_check, ["--tree", tree])
            self.assertEqual(code, 0)

    def test_superseded_citation_fails(self):
        land = self.LAND_OK.replace("sd-001)", "sd-002)").replace("[SD-001]", "[SD-002]")
        land = land.replace("(SD-001)", "(SD-002)")
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp, self.LOG, land)
            code, _, err = run(landscape_check, ["--tree", tree])
            self.assertEqual(code, 1)
            self.assertIn("superseded", err)

    def test_titled_heading_and_bad_status_fail(self):
        log = self.LOG.replace("## SD-001", "## SD-001: Baseline")
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, log, self.LAND_OK,
                "# Q\n\n## Summary\napproved someday\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, ["--tree", tree])
            self.assertEqual(code, 1)
            self.assertIn("bare ids", err)
            self.assertIn("Status line", err)

    def test_stamp_engagement_writes_utc_today_and_checks(self):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, self.LAND_OK,
                "# Q\n\n## Summary\nStatus: open\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "q",
                "--status", "approved"])
            self.assertEqual(code, 0, err)
            text = (Path(tree) / "engagements" / "q.md").read_text(
                encoding="utf-8")
            self.assertIn(f"Status: approved {today}", text)

    def test_future_status_date_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, self.LAND_OK,
                "# Q\n\n## Summary\nStatus: approved 9999-01-01\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, ["--tree", tree])
            self.assertEqual(code, 1)
            self.assertIn("clock", err)

    def test_stamp_missing_engagement_is_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp, self.LOG, self.LAND_OK)
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "nope",
                "--status", "approved"])
            self.assertEqual(code, 2, err)

    def test_stamp_parked_requires_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp, self.LOG, self.LAND_OK)
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "q",
                "--status", "parked"])
            self.assertEqual(code, 2, err)

    def test_stamp_refuses_closed_engagement(self):
        """Closed engagements are append-only: approved or superseded
        Status lines are never restamped."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, self.LAND_OK,
                "# Q\n\n## Summary\nStatus: approved 2026-07-01\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "q",
                "--status", "open"])
            self.assertEqual(code, 1, err)
            self.assertIn("append-only", err)
            text = (Path(tree) / "engagements" / "q.md").read_text(
                encoding="utf-8")
            self.assertIn("Status: approved 2026-07-01", text)

    def test_stamp_refuses_non_status_first_line(self):
        """The stamp replaces a Status line only; prose is never
        clobbered."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, self.LAND_OK,
                "# Q\n\n## Summary\nThe caching question, still framed.\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "q",
                "--status", "approved"])
            self.assertEqual(code, 2, err)
            text = (Path(tree) / "engagements" / "q.md").read_text(
                encoding="utf-8")
            self.assertIn("The caching question, still framed.", text)

    def test_stamp_rolls_back_when_tree_check_fails(self):
        """A stamp never survives a failing tree: the original Status
        line is restored before the FAIL exit."""
        land_bad = self.LAND_OK.replace("Adopt queue (SD-001)",
                                        "Adopt queue with no citation")
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(
                tmp, self.LOG, land_bad,
                "# Q\n\n## Summary\nStatus: open\n\n"
                "## Framing\nf\n\n## Options\no\n\n## Verdict\nv\n")
            code, _, err = run(landscape_check, [
                "--tree", tree, "--stamp-engagement", "q",
                "--status", "approved"])
            self.assertEqual(code, 1, err)
            self.assertIn("rolled back", err)
            text = (Path(tree) / "engagements" / "q.md").read_text(
                encoding="utf-8")
            self.assertIn("Status: open", text)
            self.assertNotIn("Status: approved", text)


class TripwireEnvironmentTests(unittest.TestCase):
    """Content-level check on the compose definition: a healthcheck fix is
    atomic, a service-set change is not."""

    COMPOSE_V1 = (
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "    healthcheck:\n"
        "      test: [\"CMD\", \"app-health\"]\n"
        "      retries: 5\n"
    )

    def _repo_with_change(self, tmp, new_compose):
        import subprocess
        repo = Path(tmp)
        compose = repo / "workspace" / "environment" / "docker-compose.yml"
        compose.parent.mkdir(parents=True)

        def git(*argv):
            subprocess.run(["git", "-C", str(repo), *argv],
                           check=True, capture_output=True)

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        compose.write_text(self.COMPOSE_V1, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        git("checkout", "-q", "-b", "atomic-change")
        compose.write_text(new_compose, encoding="utf-8")
        git("commit", "-q", "-am", "change")
        return str(repo)

    def test_healthcheck_fix_stays_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_change(
                tmp, self.COMPOSE_V1.replace("retries: 5", "retries: 10"))
            code, _, _ = run(atomic_tripwire, [
                "--repo", repo, "--range", "main...atomic-change",
            ])
            self.assertEqual(code, 0)

    def test_added_service_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_change(
                tmp, self.COMPOSE_V1 + "  cache:\n    image: redis:<tag>\n")
            code, _, err = run(atomic_tripwire, [
                "--repo", repo, "--range", "main...atomic-change",
            ])
            self.assertEqual(code, 1)
            self.assertIn("environment service or store set", err)


if __name__ == "__main__":
    unittest.main()
