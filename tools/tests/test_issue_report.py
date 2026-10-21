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
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import validate  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

PLUGIN = ROOT / "plugins/software-engineering-team"
FILE_ISSUE = PLUGIN / "scripts/file_issue.py"
NO_CHECKOUT_NOTICE = ("file_issue: notice: no Git checkout at the project root, so only"
                      " home-directory paths are checked\n")


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
        # No checkout: only home-directory paths are checked, and no Git process starts.
        with mock.patch("sys.stdin", io.StringIO(body)), mock.patch.object(self.issue, "git_lines", return_value=[]), \
                redirect_stdout(output), redirect_stderr(error):
            code = self.issue.main(["--title", title, "--approved-payload-sha256", self.issue.payload_hash(title, body)])
        return code, output.getvalue(), error.getvalue()

    def test_preview_and_exact_approval_bind_content_before_any_network(self):
        title, body = "Context misses a constraint", "## Proposed Solution\nPreserve typed constraints."
        def invoke(*args):
            out = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO(body)), redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = self.issue.main(["--title", title, *args])
            return code, out.getvalue()
        with mock.patch.object(self.issue, "git_lines", return_value=[]), \
                mock.patch.object(self.issue, "create_issue", return_value=
                "https://github.com/agentrof/agent-marketplace/issues/42") as create:
            code, preview = invoke("--preview")
            self.assertEqual(code, 0)
            payload = json.loads(preview)
            self.assertEqual(payload["body"], body)
            self.assertEqual(invoke()[0], 2)
            self.assertEqual(invoke("--approved-payload-sha256", self.issue.payload_hash(title, "old body"))[0], 2)
            create.assert_not_called()
            self.assertEqual(invoke("--approved-payload-sha256", payload["payload_sha256"])[0], 0)
            create.assert_called_once_with(title, body)

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
        self.assertEqual(error, NO_CHECKOUT_NOTICE)
        self.assertEqual(
            output,
            "Opened #42: "
            "https://github.com/agentrof/agent-marketplace/issues/42\n",
        )
        create.assert_called_once_with(
            "Broken refresh", "## Summary\nBroken."
        )
        self.assertEqual(after, before)

    def test_definite_ambiguous_and_noncanonical_outcomes_are_distinct(self):
        cases = ((self.issue.NotOpened("authentication rejected"), 2, "Not opened: authentication rejected"),
                 (self.issue.OutcomeUnknown("request timed out"), 3, "do not retry automatically"),
                 ("https://example.invalid/issues/42", 3, "Outcome unknown, do not retry automatically"))
        for outcome, code, fragment in cases:
            with self.subTest(fragment=fragment):
                effect = {"return_value": outcome} if isinstance(outcome, str) else {"side_effect": outcome}
                with mock.patch.object(self.issue, "create_issue", **effect):
                    result = self.run_main("Title", "Body")
                self.assertEqual(result[:2], (code, ""))
                self.assertIn(fragment, result[2])
                self.assertNotIn("Opened", result[2])

    def test_the_filer_refuses_the_home_paths_the_validator_refuses(self):
        self.assertEqual(self.issue.HOME_PATH_RE.pattern, validate.HOME_PATH_RE.pattern)
        self.assertEqual(self.issue.SYSTEM_HOMES, validate.SYSTEM_HOMES)

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


@integration
class ProjectFragmentRefusalTests(unittest.TestCase):
    """Before any request, the filer refuses a title or body that holds a home
    directory, the reporting checkout's path, its Git remote or the project's
    name (#357)."""

    REMOTE = "https://github.com/fixture-owner/fixture-app.git"

    @classmethod
    def setUpClass(cls) -> None:
        cls.issue = load_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve() / "fixture-checkout"
        (self.root / "docs").mkdir(parents=True)
        init_repository(self.root)
        self.remote(self.REMOTE)
        previous = Path.cwd()
        os.chdir(self.root / "docs")
        self.addCleanup(os.chdir, previous)

    def remote(self, url: str) -> None:
        subprocess.run(["git", "-C", str(self.root), "remote", "remove", "origin"],
                       capture_output=True, check=False)
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin", url], check=True)

    def file(self, title: str, body: str, *arguments: str):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(
                self.issue, "create_issue",
                return_value="https://github.com/agentrof/agent-marketplace/issues/5") as create, \
                mock.patch("sys.stdin", io.StringIO(body)), \
                redirect_stdout(output), redirect_stderr(error):
            code = self.issue.main(["--title", title, "--approved-payload-sha256", self.issue.payload_hash(title, body), *arguments])
        return code, error.getvalue(), create

    def test_each_fragment_is_refused_by_kind_and_position_and_never_echoed(self):
        checkout = str(self.root)
        body = ("## Summary\n"
                f"Seen in {checkout}/workspace/docs.\n"
                f"Remote: {self.REMOTE}\n"
                "Repository fixture-owner/fixture-app broke.\n"
                "Transcript at /Users/fixture/.claude/projects/run.jsonl\n"
                "Scratch at /tmp/claude-501/-Users-fixture-app/notes.md\n")
        code, error, create = self.file("Crash in fixture-owner/fixture-app", body)
        self.assertEqual(code, 2)
        create.assert_not_called()
        self.assertIn("Not opened: the title or body identifies the reporting project", error)
        for expected in ("remote repository name at title line 1, column 10",
                         "checkout path at body line 2, column 9",
                         "remote URL at body line 3, column 9",
                         "remote repository name at body line 4, column 12",
                         "home-directory path at body line 5, column 15",
                         "home-directory path at body line 6, column 28",
                         "project name at body line 6, column 35"):
            with self.subTest(expected=expected):
                self.assertIn(expected, error)
        self.assertEqual(error.count(" at "), 7, error)
        for value in (checkout, self.REMOTE, "fixture-owner", "fixture-app", "Users"):
            with self.subTest(value=value):
                self.assertNotIn(value, error)

    def test_the_marketplace_remote_is_not_the_reporting_project(self):
        self.remote("https://github.com/agentrof/agent-marketplace.git")
        code, error, create = self.file(
            "Refresh fails", "Target: agentrof/agent-marketplace\n")
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()

    def test_outside_a_git_checkout_only_home_paths_are_refused_and_a_notice_says_so(self):
        notice = NO_CHECKOUT_NOTICE
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            os.chdir(temporary)
            try:
                filed = self.file("Report", "Seen in fixture-owner/fixture-app at /srv/app.\n")
                refused = self.file("Report", "Seen in /home/fixture/app.\n")
            finally:
                os.chdir(previous)
        self.assertEqual(filed[:2], (0, notice))
        self.assertEqual(refused[0], 2)
        self.assertTrue(refused[1].startswith(notice), refused[1])
        self.assertIn("home-directory path at body line 1, column 9", refused[1])

    def test_the_project_root_argument_names_the_checkout_to_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            os.chdir(temporary)
            try:
                code, error, create = self.file(
                    "Report", f"Seen in {self.root}/docs.\n", "--project-root", str(self.root / "docs"))
                missing = self.file("Report", "Body.\n", "--project-root", str(Path(temporary) / "gone"))
            finally:
                os.chdir(previous)
        self.assertEqual(code, 2)
        create.assert_not_called()
        self.assertIn("checkout path at body line 1, column 9", error)
        self.assertNotIn("notice", error)
        self.assertEqual(missing[0], 2)
        missing[2].assert_not_called()
        self.assertIn("Not opened: --project-root is not a directory", missing[1])

    def test_the_checkout_path_written_from_the_home_directory_is_refused(self):
        rest = self.root.name
        with mock.patch.dict(os.environ, {"HOME": str(self.root.parent)}):
            for written in (f"~/{rest}/docs", f"$HOME/{rest}/docs", f"${{HOME}}/{rest}/docs",
                            f"%USERPROFILE%\\{rest}\\docs"):
                with self.subTest(written=written):
                    code, error, create = self.file("Report", f"Seen in {written}.\n")
                    self.assertEqual(code, 2)
                    create.assert_not_called()
                    self.assertIn("checkout path at body line 1, column 9", error)

    def test_a_remote_is_read_as_git_rewrites_it_and_in_its_userless_scp_form(self):
        subprocess.run(["git", "-C", str(self.root), "config",
                        "url.https://github.com/.insteadOf", "hub:"], check=True)
        for url in ("hub:fixture-owner/fixture-app", "github.com:fixture-owner/fixture-app.git",
                    "ssh://git@gitlab.example.test:22/fixture-owner/fixture-app.git"):
            for body in ("Fork of fixture-owner/fixture-app fails.\n",
                         "See https://github.com/fixture-owner/fixture-app/issues/3.\n"):
                with self.subTest(url=url, body=body):
                    self.remote(url)
                    code, error, create = self.file("Report", body)
                    self.assertEqual(code, 2)
                    create.assert_not_called()
                    self.assertIn("at body line 1", error)

    def test_the_project_name_is_refused_as_a_word_in_any_case_and_never_echoed(self):
        for title, body, column in (
                ("Report", "In FIXTURE-APP the nightly export fails.\n", 4),
                ("Report", "The Fixture-Checkout suite broke.\n", 5)):
            with self.subTest(body=body):
                code, error, create = self.file(title, body)
                self.assertEqual(code, 2)
                create.assert_not_called()
                self.assertIn(f"project name at body line 1, column {column}", error)
                self.assertNotIn("fixture-app", error.lower())
                self.assertNotIn("fixture-checkout", error.lower())
        code, error, create = self.file("Report", "The fixture-application tests pass.\n")
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()

    def test_encoded_and_file_url_text_is_decoded_before_the_check(self):
        checkout = str(self.root)
        for body, expected in (
                (f"Open vscode://file{checkout}/docs/x.md:10\n", "checkout path at body line 1, column 19"),
                (f"Open file://localhost{checkout}/docs/x.md\n", "checkout path at body line 1, column 22"),
                ('{"cwd": "' + checkout.replace("/", "\\/") + '"}\n',
                 "checkout path at body line 1, column 10"),
                ("See fixture-owner%2Ffixture-app\n", "remote repository name at body line 1, column 5"),
                ("GET /open?path=%2FUsers%2Ffixture%2Fnotes.txt\n",
                 "home-directory path at body line 1, column 16"),
                ("Open file:///c%3A/Users/fixture/notes.txt\n",
                 "home-directory path at body line 1, column 14")):
            with self.subTest(body=body):
                code, error, create = self.file("Report", body)
                self.assertEqual(code, 2)
                create.assert_not_called()
                self.assertIn(expected, error)
                self.assertEqual(error.count(" at "), 1, error)

    def test_a_system_or_service_home_names_no_person_and_is_filed(self):
        code, error, create = self.file(
            "CI fails", "The job fails in /home/runner/work/app/app/tools/check.py.\n")
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
