"""Unit tests for the issue-desk filing helper.

The load-bearing guarantee: this script files to exactly one repository and
cannot be redirected. Several tests assert that lock from different angles.
"""

import importlib.util
import io
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "dist" / "claude" / "project-management-office" / "scripts"
          / "file_issue.py")

spec = importlib.util.spec_from_file_location("file_issue", SCRIPT)
file_issue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(file_issue)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = file_issue.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class FileIssueTests(unittest.TestCase):
    def test_target_is_locked(self):
        self.assertEqual(file_issue.MARKETPLACE_REPO, "agentrof/agent-marketplace")
        self.assertEqual(file_issue.resolve_target(), "agentrof/agent-marketplace")

    def test_slug_parsing_variants(self):
        for url in ("https://github.com/agentrof/agent-marketplace",
                    "https://github.com/agentrof/agent-marketplace.git",
                    "git@github.com:agentrof/agent-marketplace.git",
                    "https://github.com/agentrof/agent-marketplace/"):
            self.assertEqual(file_issue._slug(url), "agentrof/agent-marketplace")
        self.assertEqual(file_issue._slug("https://github.com/agentrof"), "")

    def test_manifest_mismatch_refuses(self):
        original = file_issue._repo_from_manifest
        file_issue._repo_from_manifest = lambda: "someone/else"
        try:
            with self.assertRaises(SystemExit) as ctx:
                file_issue.resolve_target()
            self.assertIn("refusing to file", str(ctx.exception))
        finally:
            file_issue._repo_from_manifest = original

    def test_dry_run_posts_nothing(self):
        def boom(*a, **k):
            raise AssertionError("network must not be touched on --dry-run")
        original = file_issue.urllib.request.urlopen
        file_issue.urllib.request.urlopen = boom
        try:
            code, out, _ = run(["--title", "x", "--dry-run"])
        finally:
            file_issue.urllib.request.urlopen = original
        self.assertEqual(code, 0)
        self.assertIn("agentrof/agent-marketplace", out)

    def test_no_gh_no_token_fails_without_posting(self):
        orig_gh, orig_token = file_issue._gh_available, file_issue._token
        file_issue._gh_available = lambda: False
        file_issue._token = lambda: ""
        try:
            code, _, err = run(["--title", "x"])
        finally:
            file_issue._gh_available, file_issue._token = orig_gh, orig_token
        self.assertEqual(code, 1)
        self.assertIn("nothing was posted", err)

    def test_api_targets_only_the_locked_repo(self):
        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return (b'{"html_url":'
                        b'"https://github.com/agentrof/agent-marketplace/issues/42"}')

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            return FakeResp()

        orig_gh, orig_token = file_issue._gh_available, file_issue._token
        orig_open = file_issue.urllib.request.urlopen
        file_issue._gh_available = lambda: False
        file_issue._token = lambda: "tok"
        file_issue.urllib.request.urlopen = fake_urlopen
        try:
            code, out, err = run(["--title", "t", "--body", "b"])
        finally:
            file_issue._gh_available, file_issue._token = orig_gh, orig_token
            file_issue.urllib.request.urlopen = orig_open
        self.assertEqual(code, 0, err)
        self.assertEqual(
            captured["url"],
            "https://api.github.com/repos/agentrof/agent-marketplace/issues")
        self.assertEqual(captured["method"], "POST")
        self.assertIn("issues/42", out)

    def test_source_names_no_other_repository(self):
        """Mechanical proof of the single-target lock: every owner/repo slug
        in the source is the marketplace's own."""
        source = SCRIPT.read_text(encoding="utf-8")
        slugs = re.findall(r"github\.com[/:]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
                           source)
        offenders = {s.rstrip(".") for s in slugs} - {"agentrof/agent-marketplace"}
        self.assertEqual(offenders, set(), f"unexpected repo targets: {offenders}")


if __name__ == "__main__":
    unittest.main()
