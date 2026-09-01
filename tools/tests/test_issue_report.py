"""Stateless issue preview and fixed external-filing contracts."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins/software-engineering-team"
FILE_ISSUE = PLUGIN / "scripts/file_issue.py"
ISSUE_SKILL = PLUGIN / "skill-content/issue-report/SKILL.md"
VAULT_POLICY = (
    PLUGIN / "skill-content/obsidian-vault/data/vault-policy.json"
)


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

    def run_main(self, title: str, body: str):
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(body)), \
                redirect_stdout(output), redirect_stderr(error):
            code = self.issue.main(["--title", title])
        return code, output.getvalue(), error.getvalue()

    def test_skill_is_chat_previewed_external_and_stateless(self):
        text = ISSUE_SKILL.read_text(encoding="utf-8")
        for required in (
            "project_scope: external",
            "agentrof/agent-marketplace",
            "Summary",
            "Reproduction or Motivation",
            "Expected Behavior",
            "Actual Behavior",
            "Impact",
            "Evidence and Context",
            "`Open issue`, `Revise` or `Cancel`",
            "standard input",
            "Outcome unknown, do not retry automatically",
            "does not require project setup",
        ):
            self.assertIn(required, text)
        for retired in (
            "workspace/docs",
            "obsidian-vault",
            "issue_compile.py",
            "--report",
            "--dry-run",
            "source_hash",
        ):
            self.assertNotIn(retired, text)

    def test_vault_backlog_and_portable_gate_have_no_issue_contract(self):
        policy = json.loads(VAULT_POLICY.read_text(encoding="utf-8"))
        self.assertNotIn("issues", policy["subtrees"])
        self.assertNotIn("issue-report", policy["extra_doc_types"])
        self.assertNotIn("issue_report", policy["type_path_patterns"])
        self.assertNotIn("issue_report", policy["status_values"])
        self.assertNotIn(
            "issue-report", policy["fragment_graph_groups"]["backlog"]
        )
        self.assertNotIn(
            "issue-report",
            {group["id"] for group in policy["graph_color_groups"]},
        )
        for retired_property in ("issue_kind", "external_url", "filed_at_utc"):
            self.assertNotIn(retired_property, policy["property_types"])

        graph = (
            PLUGIN / "templates/vault/.obsidian/graph.json"
        ).read_text(encoding="utf-8")
        types = (
            PLUGIN / "templates/vault/.obsidian/types.json"
        ).read_text(encoding="utf-8")
        backlog = (PLUGIN / "scripts/backlog_compile.py").read_text(
            encoding="utf-8"
        )
        portable = (PLUGIN / "scripts/vault_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("doc/issue-report", graph)
        self.assertNotIn("issue_kind", types)
        self.assertNotIn('"issue-report"', backlog)
        self.assertNotIn("issue_compile.py", portable)
        self.assertFalse((PLUGIN / "scripts/issue_compile.py").exists())
        self.assertFalse((PLUGIN / "templates/vault/maps/issues.md").exists())

    def test_empty_title_and_body_fail_before_network(self):
        with mock.patch.object(self.issue, "create_issue") as create:
            code, output, error = self.run_main("   ", "body")
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertIn("Not opened: title is empty", error)
            code, output, error = self.run_main("Title", "   \n")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("Not opened: stdin body is empty", error)
        create.assert_not_called()

    def test_confirmed_url_is_the_only_success_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            before = {path.relative_to(root): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            previous = Path.cwd()
            os.chdir(root)
            try:
                with mock.patch.object(
                    self.issue,
                    "create_issue",
                    return_value=(
                        "https://github.com/agentrof/agent-marketplace/issues/42"
                    ),
                ) as create:
                    code, output, error = self.run_main(
                        "  Broken refresh  ", "\n## Summary\nBroken.\n"
                    )
            finally:
                os.chdir(previous)
            after = {path.relative_to(root): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(
            output,
            "Opened #42: "
            "https://github.com/agentrof/agent-marketplace/issues/42\n",
        )
        create.assert_called_once_with(
            "Broken refresh", "## Summary\nBroken."
        )
        self.assertEqual(after, before)

    def test_noncanonical_success_response_is_unknown_not_opened(self):
        with mock.patch.object(
            self.issue,
            "create_issue",
            return_value="https://example.invalid/issues/42",
        ):
            code, output, error = self.run_main("Title", "Body")
        self.assertEqual(code, 3)
        self.assertEqual(output, "")
        self.assertNotIn("Opened", error)
        self.assertIn("Outcome unknown, do not retry automatically", error)

    def test_failed_filing_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            before = {path.relative_to(root): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            previous = Path.cwd()
            os.chdir(root)
            try:
                with mock.patch.object(
                    self.issue,
                    "create_issue",
                    side_effect=self.issue.NotOpened("authentication rejected"),
                ):
                    code, output, error = self.run_main("Title", "Body")
            finally:
                os.chdir(previous)
            after = {path.relative_to(root): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("Not opened", error)
        self.assertEqual(after, before)

    def test_definite_and_ambiguous_failures_have_distinct_outcomes(self):
        with mock.patch.object(
            self.issue,
            "create_issue",
            side_effect=self.issue.NotOpened("authentication rejected"),
        ):
            definite = self.run_main("Title", "Body")
        self.assertEqual(definite[0], 2)
        self.assertEqual(definite[1], "")
        self.assertIn("Not opened: authentication rejected", definite[2])

        with mock.patch.object(
            self.issue,
            "create_issue",
            side_effect=self.issue.OutcomeUnknown("request timed out"),
        ):
            ambiguous = self.run_main("Title", "Body")
        self.assertEqual(ambiguous[0], 3)
        self.assertEqual(ambiguous[1], "")
        self.assertIn("do not retry automatically", ambiguous[2])

    def test_no_cli_or_token_is_definitely_not_opened(self):
        with mock.patch.object(self.issue.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.issue.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(
                self.issue.NotOpened, "GH_TOKEN/GITHUB_TOKEN"
            ):
                self.issue.create_issue("title", "body")
        urlopen.assert_not_called()

    def test_gh_uses_fixed_api_endpoint_and_stdin_payload(self):
        authenticated = subprocess.CompletedProcess([], 0, "", "")
        created = subprocess.CompletedProcess(
            [], 0,
            "https://github.com/agentrof/agent-marketplace/issues/7\n", ""
        )
        with mock.patch.object(self.issue.shutil, "which", return_value="/bin/gh"), \
                mock.patch.object(
                    self.issue.subprocess, "run",
                    side_effect=[authenticated, created],
                ) as run:
            url = self.issue.create_issue("A title", "A body")
        self.assertEqual(
            url, "https://github.com/agentrof/agent-marketplace/issues/7"
        )
        command = run.call_args_list[1].args[0]
        self.assertIn("repos/agentrof/agent-marketplace/issues", command)
        self.assertNotIn("--body", command)
        self.assertEqual(
            json.loads(run.call_args_list[1].kwargs["input"]),
            {"title": "A title", "body": "A body"},
        )

    def test_gh_auth_rejection_never_attempts_post(self):
        rejected = subprocess.CompletedProcess([], 1, "", "not logged in")
        with mock.patch.object(self.issue.shutil, "which", return_value="/bin/gh"), \
                mock.patch.object(
                    self.issue.subprocess, "run", return_value=rejected
                ) as run:
            with self.assertRaisesRegex(self.issue.NotOpened, "not logged in"):
                self.issue.create_issue("title", "body")
        run.assert_called_once()

    def test_gh_process_start_failure_is_definitely_not_opened(self):
        with mock.patch.object(self.issue.shutil, "which", return_value="/bin/gh"), \
                mock.patch.object(
                    self.issue.subprocess, "run", side_effect=OSError("missing")
                ) as run:
            with self.assertRaisesRegex(self.issue.NotOpened, "check failed"):
                self.issue.create_issue("title", "body")
        run.assert_called_once()

    def test_api_fallback_uses_fixed_repository_and_canonical_url(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"html_url":"https://github.com/agentrof/'
            b'agent-marketplace/issues/9"}'
        )
        with mock.patch.object(self.issue.shutil, "which", return_value=None), \
                mock.patch.object(self.issue, "token", return_value="secret"), \
                mock.patch.object(
                    self.issue.urllib.request,
                    "urlopen",
                    return_value=response,
                ) as urlopen:
            result = self.issue.create_issue("title", "body")
        self.assertEqual(
            result, "https://github.com/agentrof/agent-marketplace/issues/9"
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.github.com/repos/agentrof/agent-marketplace/issues",
        )
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"title": "title", "body": "body"},
        )

    def test_api_4xx_is_definite_and_5xx_is_unknown(self):
        for status, expected in (
            (422, self.issue.NotOpened),
            (503, self.issue.OutcomeUnknown),
        ):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://api.github.com", status, "failure", {},
                    io.BytesIO(b'{"message":"failure"}'),
                )
                with mock.patch.object(
                    self.issue.urllib.request, "urlopen", side_effect=error
                ):
                    with self.assertRaises(expected):
                        self.issue.create_with_api("title", "body", "secret")

    def test_retired_file_and_dry_run_arguments_are_not_accepted(self):
        for arguments in (
            ["--report", "report.md"],
            ["--title", "Title", "--dry-run"],
        ):
            with self.subTest(arguments=arguments), \
                    redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.issue.main(arguments)
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
