"""The standalone issue filer has no project-state dependency."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins/software-engineering-team/scripts/file_issue.py"


def load_module():
    spec = importlib.util.spec_from_file_location("file_issue_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IssueReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.issue = load_module()

    def test_dry_run_uses_locked_marketplace_target(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--title", "smoke", "--body", "body", "--dry-run"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agentrof/agent-marketplace", result.stdout)

    def test_dry_run_never_calls_external_writer(self):
        with mock.patch.object(self.issue, "create_issue") as create:
            output = io.StringIO()
            with redirect_stdout(output):
                code = self.issue.main([
                    "--title", "No post", "--body", "draft", "--dry-run"
                ])
            self.assertEqual(code, 0)
            create.assert_not_called()
            self.assertIn(self.issue.MARKETPLACE_REPO, output.getvalue())

    def test_body_file_is_read_but_target_remains_locked(self):
        with tempfile.TemporaryDirectory() as temporary:
            body = Path(temporary) / "body.md"
            body.write_text("file body\n", encoding="utf-8")
            with mock.patch.object(
                self.issue, "create_issue", return_value="https://example.test/1"
            ) as create:
                output = io.StringIO()
                with redirect_stdout(output):
                    code = self.issue.main([
                        "--title", "From file", "--body-file", str(body)
                    ])
            self.assertEqual(code, 0)
            create.assert_called_once_with("From file", "file body\n")

    def test_no_cli_or_token_fails_without_network(self):
        with mock.patch.object(self.issue.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.issue.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "nothing was posted"):
                self.issue.create_issue("title", "body")
            urlopen.assert_not_called()

    def test_api_fallback_uses_only_locked_repository(self):
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

    def test_external_failure_is_reported_as_nonzero(self):
        with mock.patch.object(
            self.issue, "create_issue", side_effect=RuntimeError("refused")
        ):
            error = io.StringIO()
            with redirect_stderr(error):
                code = self.issue.main(["--title", "Failure"])
        self.assertEqual(code, 1)
        self.assertIn("refused", error.getvalue())



if __name__ == "__main__":
    unittest.main()
