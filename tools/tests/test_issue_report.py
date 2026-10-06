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
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import validate  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

PLUGIN = ROOT / "plugins/software-engineering-team"
FILE_ISSUE = PLUGIN / "scripts/file_issue.py"
ISSUE_SKILL = PLUGIN / "skill-content/issue-report/SKILL.md"
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
        with mock.patch("sys.stdin", io.StringIO(body)), \
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
        with mock.patch.object(self.issue, "create_issue", return_value=
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


class UpstreamConfidentialityTests(unittest.TestCase):
    """The Agent Marketplace repository is public, so text sent to it never
    identifies the project it comes from (#357)."""

    def test_the_skill_scans_the_payload_before_it_is_shown_and_filed(self):
        raw = ISSUE_SKILL.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("\n## Confidentiality\n", raw)
        for term in ("An issue never identifies the reporting project or its data",
                     "commit id, local or home path, story, Delivery, scenario or requirement id",
                     '"in one measured project"',
                     "retell every project detail as Confidentiality requires"):
            with self.subTest(term=term):
                self.assertIn(term, text)
        scan = text.index("Scan the exact title and body before showing them")
        self.assertLess(text.index("## Confidentiality"), text.index("## Procedure"))
        self.assertLess(scan, text.index("Present the exact payload in chat"))
        self.assertLess(text.index("Present the exact payload in chat"),
                        text.index("invoke the packaged `scripts/file_issue.py`"))
        self.assertIn("`Revise` changes the payload in chat, scans it again as step 3 does", text)

    def test_a_payload_the_filer_refuses_returns_to_revise(self):
        text = " ".join(ISSUE_SKILL.read_text(encoding="utf-8").split())
        for term in ("exactly once per approved payload",
                     "with `--project-root` set to the root of the project in scope",
                     "a home-directory path, the project's checkout path, also written from"
                     " the home directory, a Git remote URL or its owner/repo, or the project's"
                     " repository or folder name as a word in any case, also inside"
                     " percent-encoded, JSON-escaped and file URL text",
                     "Without a Git checkout at the project root it checks home-directory"
                     " paths only and prints a notice saying so",
                     "names each fragment's kind and position, never its value",
                     "When that reason says the payload identifies the reporting project,"
                     " continue as `Revise`",
                     "A refused payload is never filed"):
            with self.subTest(term=term):
                self.assertIn(term, text)

    def test_every_role_and_session_reads_the_rule(self):
        # The same list as the skill and the maintainer protocol, so a role
        # keeps package paths and anonymous numbers a useful report needs.
        listed = ("no project or code name, repository, link or issue reference, commit id,"
                  " local or home path, story, Delivery, scenario or requirement id, measured"
                  " data presented as this project's, domain, client or person")
        for relative in ("constitution.md", "templates/project-instructions/common.md"):
            with self.subTest(source=relative):
                text = " ".join((PLUGIN / relative).read_text(encoding="utf-8").split())
                self.assertIn("never identifies this project", text)
                self.assertIn(listed, text)
                self.assertIn("the files a pull request adds included", text)
                self.assertIn("anonymous", text)



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

    def test_a_scp_or_ssh_remote_is_refused_by_its_owner_and_repository(self):
        for url in ("git@github.com:fixture-owner/fixture-app.git",
                    "ssh://git@gitlab.example.test:22/fixture-owner/fixture-app.git"):
            with self.subTest(url=url):
                self.remote(url)
                code, error, create = self.file("Report", "See fixture-owner/fixture-app.\n")
                self.assertEqual(code, 2)
                create.assert_not_called()
                self.assertIn("remote repository name at body line 1, column 5", error)

    def test_a_payload_without_those_fragments_is_filed_from_the_checkout(self):
        code, error, create = self.file(
            "Refresh fails", "## Summary\nRefresh fails in an owner/repository checkout.\n")
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()

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
        for url in ("hub:fixture-owner/fixture-app", "github.com:fixture-owner/fixture-app.git"):
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

    def test_the_marketplace_name_is_no_project_name(self):
        fork = self.root.parent / "agent-marketplace"
        fork.mkdir()
        init_repository(fork)
        subprocess.run(["git", "-C", str(fork), "remote", "add", "origin",
                        "https://github.com/fixture-owner/agent-marketplace.git"], check=True)
        code, error, create = self.file(
            "Refresh fails", "The agent-marketplace refresh fails.\n", "--project-root", str(fork))
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()

    def test_the_filer_refuses_the_home_paths_the_validator_refuses(self):
        self.assertEqual(self.issue.HOME_PATH_RE.pattern, validate.HOME_PATH_RE.pattern)
        self.assertEqual(self.issue.SYSTEM_HOMES, validate.SYSTEM_HOMES)

    def test_a_system_or_service_home_names_no_person_and_is_filed(self):
        code, error, create = self.file(
            "CI fails", "The job fails in /home/runner/work/app/app/tools/check.py.\n")
        self.assertEqual((code, error), (0, ""))
        create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
