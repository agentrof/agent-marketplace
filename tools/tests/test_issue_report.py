"""Issue-report lifecycle and fixed external-filing contracts."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
ISSUE_COMPILE = SCRIPTS / "issue_compile.py"
FILE_ISSUE = SCRIPTS / "file_issue.py"
SETUP_PROJECT = SCRIPTS / "setup_project.py"
VAULT_CHECK = SCRIPTS / "vault_check.py"
sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location("file_issue_test", FILE_ISSUE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IssueReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.issue = load_module()

    def workspace(self, root: Path) -> Path:
        workspace = root / "workspace"
        docs = workspace / "docs"
        docs.mkdir(parents=True)
        (docs / "home.md").write_text(
            "---\ntype: home\ntitle: Knowledge Base\ntags:\n"
            "  - doc/home\n---\n\n# Knowledge Base\n",
            encoding="utf-8",
        )
        (workspace / "config.json").write_text(json.dumps({
            "schema_version": 2,
            "team_id": "software-engineering-team",
            "output_language": "English",
            "terminology_language": "English",
        }), encoding="utf-8")
        return docs

    def run_compile(self, *args: str):
        return subprocess.run(
            [sys.executable, str(ISSUE_COMPILE), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def create_complete_report(self, root: Path):
        docs = self.workspace(root)
        result = self.run_compile(
            "init", "--docs", str(docs), "--slug", "refresh-breaks",
            "--title", "Refresh breaks", "--kind", "defect", "--id", "ISSUE-001",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = docs / "issues/refresh-breaks.md"
        text = report.read_text(encoding="utf-8")
        lines = []
        for line in text.splitlines():
            if line.startswith("TODO:"):
                lines.append("Observed and reproducible evidence is recorded here.")
            else:
                lines.append(line)
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return docs, report

    def approve(self, report: Path):
        result = self.run_compile("approve", "--report", str(report))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_uses_direct_title_and_renders_graph_navigation(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, report = self.create_complete_report(Path(temporary))
            text = report.read_text(encoding="utf-8")
            self.assertIn("title: Refresh breaks", text)
            self.assertIn("# Refresh breaks", text)
            self.assertIn("[[maps/issues|Issue reports]]", text)
            self.assertIn(
                "[[maps/issues|Issue reports]]",
                (docs / "home.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "[[issues/refresh-breaks|Refresh breaks]]",
                (docs / "maps/issues.md").read_text(encoding="utf-8"),
            )
            checked = self.run_compile("check", "--docs", str(docs), "--render")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_stub_and_duplicate_identity_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = self.workspace(Path(temporary))
            created = self.run_compile(
                "init", "--docs", str(docs), "--slug", "first",
                "--title", "First", "--kind", "defect", "--id", "ISSUE-001",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("placeholder text", checked.stdout)
            duplicate = self.run_compile(
                "init", "--docs", str(docs), "--slug", "second",
                "--title", "Second", "--kind", "improvement", "--id", "ISSUE-001",
            )
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("already owned", duplicate.stderr)

    def test_check_rejects_duplicate_identity_and_stale_navigation(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, first = self.create_complete_report(Path(temporary))
            second = docs / "issues/other.md"
            second.write_bytes(first.read_bytes())
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("ISSUE-001 has multiple owners", checked.stdout)
            self.assertIn("maps/issues.md is missing or stale", checked.stdout)

            second.unlink()
            (docs / "maps/issues.md").write_text("stale\n", encoding="utf-8")
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("maps/issues.md is missing or stale", checked.stdout)

    def test_approval_is_utc_stamped_and_body_tamper_is_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            text = report.read_text(encoding="utf-8")
            self.assertIn("status: approved", text)
            self.assertIn("approved_at_utc:", text)
            self.assertIn("source_hash: sha256:", text)
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            report.write_text(text.replace(
                "Observed and reproducible evidence is recorded here.",
                "Changed after approval.", 1,
            ), encoding="utf-8")
            stale = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(stale.returncode, 1)
            self.assertIn("source_hash is stale", stale.stdout)

    def test_approved_report_passes_the_full_project_vault_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = subprocess.run(
                [sys.executable, str(SETUP_PROJECT), "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            docs = project / "workspace/docs"
            created = self.run_compile(
                "init", "--docs", str(docs), "--slug", "refresh-breaks",
                "--title", "Refresh breaks", "--kind", "defect", "--id", "ISSUE-001",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            report = docs / "issues/refresh-breaks.md"
            text = report.read_text(encoding="utf-8")
            report.write_text("\n".join(
                "Observed and reproducible evidence is recorded here."
                if line.startswith("TODO:") else line
                for line in text.splitlines()
            ) + "\n", encoding="utf-8")
            self.approve(report)
            gate = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "check", "--vault", str(docs),
                 "--json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
            portable = subprocess.run(
                [sys.executable, str(project / ".github/agentrof/vault-gate.pyz"),
                 "check", "--project-root", str(project), "--json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                portable.returncode, 0, portable.stdout + portable.stderr
            )
            names = [
                item["name"] for item in json.loads(portable.stdout)["results"]
            ]
            self.assertIn("issue-reports", names)

    def test_draft_report_cannot_be_dry_run_or_posted(self):
        with tempfile.TemporaryDirectory() as temporary:
            _docs, report = self.create_complete_report(Path(temporary))
            with mock.patch.object(self.issue, "create_issue") as create:
                error = io.StringIO()
                with redirect_stderr(error):
                    code = self.issue.main(["--report", str(report), "--dry-run"])
            self.assertEqual(code, 2)
            create.assert_not_called()
            self.assertIn("not approved", error.getvalue())

    def test_approved_dry_run_uses_fixed_target_without_posting(self):
        with tempfile.TemporaryDirectory() as temporary:
            _docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            with mock.patch.object(self.issue, "create_issue") as create:
                output = io.StringIO()
                with redirect_stdout(output):
                    code = self.issue.main(["--report", str(report), "--dry-run"])
            self.assertEqual(code, 0)
            create.assert_not_called()
            self.assertIn(self.issue.MARKETPLACE_REPO, output.getvalue())

    def test_successful_post_marks_the_same_report_filed(self):
        with tempfile.TemporaryDirectory() as temporary:
            _docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            url = "https://github.com/agentrof/agent-marketplace/issues/1"
            with mock.patch.object(
                self.issue, "create_issue", return_value=url
            ) as create:
                output = io.StringIO()
                with redirect_stdout(output):
                    code = self.issue.main(["--report", str(report)])
            self.assertEqual(code, 0, output.getvalue())
            create.assert_called_once()
            text = report.read_text(encoding="utf-8")
            self.assertIn("status: filed", text)
            self.assertIn(f"external_url: {url}", text)
            self.assertIn("filed_at_utc:", text)

            with mock.patch.object(self.issue, "create_issue") as duplicate:
                error = io.StringIO()
                with redirect_stderr(error):
                    repeated = self.issue.main(["--report", str(report)])
            self.assertEqual(repeated, 2)
            duplicate.assert_not_called()
            self.assertIn("not-yet-filed", error.getvalue())

    def test_filed_url_tamper_and_local_persistence_failure_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            url = "https://github.com/agentrof/agent-marketplace/issues/42"
            with mock.patch.object(
                self.issue, "create_issue", return_value=url
            ), mock.patch.object(
                self.issue.issue_compile, "mark_filed",
                side_effect=ValueError("disk full"),
            ):
                output = io.StringIO()
                error = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    code = self.issue.main(["--report", str(report)])
            self.assertEqual(code, 3)
            self.assertIn(url, output.getvalue())
            self.assertIn("Do not retry", error.getvalue())
            self.assertIn(url, error.getvalue())
            self.assertIn("status: approved", report.read_text(encoding="utf-8"))

            self.issue.issue_compile.mark_filed(report, url)
            text = report.read_text(encoding="utf-8").replace(
                url, "https://example.invalid/issues/42"
            )
            report.write_text(text, encoding="utf-8")
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("canonical agentrof/agent-marketplace", checked.stdout)

    def test_unsupported_hand_authored_closed_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            text = report.read_text(encoding="utf-8").replace(
                "status: approved", "status: closed"
            ).replace("status/approved", "status/closed")
            report.write_text(text, encoding="utf-8")
            checked = self.run_compile("check", "--docs", str(docs))
            self.assertEqual(checked.returncode, 1)
            self.assertIn("not a legal issue-report status", checked.stdout)

    def test_report_outside_the_canonical_workspace_cannot_be_filed(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs, report = self.create_complete_report(Path(temporary))
            self.approve(report)
            outside = Path(temporary) / "issues/copied.md"
            outside.parent.mkdir()
            outside.write_bytes(report.read_bytes())
            with mock.patch.object(self.issue, "create_issue") as create:
                error = io.StringIO()
                with redirect_stderr(error):
                    code = self.issue.main(["--report", str(outside)])
            self.assertEqual(code, 2)
            create.assert_not_called()
            self.assertIn("workspace/docs/issues", error.getvalue())

    def test_no_cli_or_token_fails_without_network(self):
        with mock.patch.object(self.issue.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.issue.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "nothing was posted"):
                self.issue.create_issue("title", "body")
            urlopen.assert_not_called()

    def test_api_fallback_uses_only_fixed_repository(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"html_url":"https://example.test/1"}'
        )
        with mock.patch.object(self.issue.shutil, "which", return_value=None), \
                mock.patch.object(self.issue, "token", return_value="secret"), \
                mock.patch.object(
                    self.issue.urllib.request, "urlopen", return_value=response
                ) as urlopen:
            result = self.issue.create_issue("title", "body")
        self.assertEqual(result, "https://example.test/1")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.github.com/repos/agentrof/agent-marketplace/issues",
        )


if __name__ == "__main__":
    unittest.main()
