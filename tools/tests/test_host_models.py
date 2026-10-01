"""Each host's own model list, read from the binary that runs the session (#349).

Every host package ships `scripts/host_models.py`: it finds the host binary,
reads that binary's model list without a model request and judges a model
`available`, `unavailable` or `unverified`. Fake `claude` and `codex`
executables print the shapes the real ones print: Claude Code 2.1.284's reply
to the `initialize` control request (`SDKControlInitializeResponse` and
`ModelInfo`, https://code.claude.com/docs/en/agent-sdk/typescript), and
Codex 0.159.2's `debug models` catalog and `models_cache.json`
(codex-rs/models-manager at rust-v0.159.3). No test starts a model or runs a
real host binary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[1]
SHARED = REPO / "platforms/shared/_team/overlay/scripts"
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))
# A host package ships the shared core beside its host_models.py.
sys.path.insert(0, str(SHARED))

import fixtures  # noqa: E402
import host_listing  # noqa: E402


def load(name: str, host: str):
    path = REPO / "platforms" / host / "_team/overlay/scripts/host_models.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


claude_models = load("claude_host_models", "claude")
codex_models = load("codex_host_models", "codex")

L4 = ["low", "medium", "high", "xhigh"]
L5 = L4 + ["max"]
L6 = L5 + ["ultra"]
# Claude Code 2.1.284's `initialize` models for a first-party API-key setup.
CLAUDE_ROWS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5[1m]",
     "displayName": "Default (recommended)", "supportsEffort": True,
     "supportedEffortLevels": L5},
    {"value": "opus[1m]", "resolvedModel": "claude-opus-5-5[1m]",
     "displayName": "Opus (1M context)", "supportsEffort": True, "supportedEffortLevels": L5},
    {"value": "claude-fable-5-1", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "supportsEffort": True, "supportedEffortLevels": L5},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "supportsEffort": True, "supportedEffortLevels": L5},
    {"value": "sonnet[1m]", "resolvedModel": "claude-sonnet-5-5[1m]",
     "displayName": "Sonnet 5.5 (1M context)", "supportsEffort": True,
     "supportedEffortLevels": L5},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku"},
]
CLAUDE_LISTED = {"claude-opus-5-5": L5, "claude-fable-5-1": L5, "claude-sonnet-5-5": L5,
                 "claude-haiku-4-5-20251001": []}
SIGNED_IN = {"tokenSource": "claude.ai", "apiProvider": "firstParty"}
SIGNED_OUT = {"tokenSource": "none", "apiProvider": "firstParty"}
CLAUDE_PROBE = ["-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--no-session-persistence", "--strict-mcp-config", "--settings",
                '{"disableAllHooks":true}']


def codex_catalog(*entries: tuple) -> list:
    """Catalog entries in the shape `codex debug models` prints."""
    return [{"slug": slug, "display_name": slug, "visibility": visibility, "priority": index,
             "supported_reasoning_levels": [{"effort": effort, "description": effort}
                                            for effort in efforts]}
            for index, (slug, visibility, efforts) in enumerate(entries)]


# An account catalog that lacks `gpt-6.1-sol` and lists a hidden model.
ACCOUNT = codex_catalog(
    ("gpt-6-luna", "list", L5), ("gpt-5.6-terra", "list", L6), ("gpt-5.6-luna", "list", L5),
    ("gpt-5.5", "list", L4), ("codex-auto-review", "hide", L5))
# `codex debug models --bundled` of Codex 0.159.2.
BUNDLED = codex_catalog(
    ("gpt-6-astra", "list", L6), ("gpt-6.1-sol", "list", L6), ("gpt-6-sol", "list", L6),
    ("gpt-6-luna", "list", L5), ("gpt-5.6-sol", "list", L6), ("gpt-5.6-terra", "list", L6),
    ("gpt-5.6-luna", "list", L5), ("gpt-daybreak-blue-latest", "hide", L6),
    ("gpt-daybreak-red-latest", "hide", L6), ("gpt-5.5", "list", L4),
    ("codex-auto-review", "hide", L5))

def alive(pid: int) -> bool:
    """Whether a process runs; a zombie waiting for its reaper counts as gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                           text=True, check=False).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def workspace(case: unittest.TestCase) -> tuple:
    """A temporary base directory and an empty directory inside it."""
    work = tempfile.TemporaryDirectory()
    case.addCleanup(work.cleanup)
    # Resolved, so a path the module resolves compares equal on macOS.
    base = Path(work.name).resolve()
    (base / "empty").mkdir()
    return base, base / "empty"


@unittest.skipIf(os.name != "posix", "the fake host binaries are POSIX shell scripts")
class ClaudeListingTests(unittest.TestCase):
    """The `initialize` reply of the binary that runs the session is the list."""

    def setUp(self) -> None:
        self.base, self.empty = workspace(self)
        self.claude = self.fake("session")

    def fake(self, directory: str, version: str = "2.1.284") -> fixtures.FakeHost:
        host = fixtures.FakeHost(self.base / directory, "claude")
        host.answer("--version", stdout=f"{version} (Claude Code)\n")
        host.answer("*", reply={"models": CLAUDE_ROWS, "account": SIGNED_IN})
        return host

    def environ(self, **extra) -> dict:
        return {"PATH": str(self.empty), "HOME": str(self.base),
                "CLAUDE_CODE_EXECPATH": str(self.claude.path), **extra}

    def listing(self, timeout: float = 30.0, **extra) -> dict:
        min_version = extra.pop("min_version", None)
        return claude_models.list_models(self.environ(**extra), timeout=timeout,
                                         min_version=min_version)

    def test_a_signed_in_binary_lists_its_account_s_models_with_their_efforts(self):
        listing = self.listing()
        self.assertEqual(listing["status"], "ok", listing)
        self.assertEqual((listing["binary"], listing["version"], listing["view"]),
                         (str(self.claude.path), "2.1.284", "account"))
        # A context window variant is the same model.
        self.assertEqual(listing["models"],
                         {model: {"efforts": levels} for model, levels in CLAUDE_LISTED.items()})
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"):
            self.assertEqual(claude_models.verdict(model, listing), "available")
        for model in ("claude-opus-4-1", "opus", "claude-opus-5-5[1m]"):
            self.assertEqual(claude_models.verdict(model, listing), "unavailable")
        self.assertEqual(claude_models.efforts("claude-sonnet-5-5", listing), L5)
        self.assertEqual(claude_models.efforts("claude-haiku-4-5-20251001", listing), [])
        self.assertIsNone(claude_models.efforts("claude-opus-4-1", listing))
        self.assertIn("claude.ai", listing["detail"])

    def test_only_a_claude_ai_login_reflects_the_account(self):
        # Claude Code names the source of its auth token; only a claude.ai
        # login gets the account's own catalog.
        for source in ("claude.ai", "CLAUDE_CODE_OAUTH_TOKEN",
                       "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"):
            with self.subTest(source=source):
                self.claude.answer("*", reply={"models": CLAUDE_ROWS,
                                               "account": {"tokenSource": source}})
                self.assertEqual(self.listing()["view"], "account")

    def test_without_a_signed_in_account_a_listed_model_stays_unverified(self):
        for name, account in (("token source none", SIGNED_OUT), ("no account", None),
                              ("empty token source", {"tokenSource": ""}),
                              ("gateway token", {"tokenSource": "ANTHROPIC_AUTH_TOKEN"}),
                              ("key helper", {"tokenSource": "apiKeyHelper"}),
                              ("unknown source", {"tokenSource": "something-new"})):
            with self.subTest(case=name):
                reply = {"models": CLAUDE_ROWS}
                if account is not None:
                    reply["account"] = account
                self.claude.answer("*", reply=reply)
                listing = self.listing()
                self.assertEqual((listing["status"], listing["view"]), ("ok", "generic"))
                self.assertEqual(claude_models.verdict("claude-opus-5-5", listing), "unverified")
                self.assertEqual(claude_models.verdict("claude-opus-4-1", listing),
                                 "unavailable")
                self.assertEqual(claude_models.efforts("claude-opus-5-5", listing), L5)

    def test_the_probe_sends_initialize_alone_with_every_hook_off(self):
        self.listing()
        calls = self.claude.calls()
        self.assertEqual([call["argv"] for call in calls], [["--version"], CLAUDE_PROBE])
        lines = calls[1]["stdin"].splitlines()
        self.assertEqual(len(lines), 1, lines)
        request = json.loads(lines[0])
        self.assertEqual((request["type"], request["request"]), (
            "control_request", {"subtype": "initialize"}))
        self.assertIsInstance(request["request_id"], str)

    def test_the_probe_loads_no_mcp_server(self):
        # `claude -p` starts the project's `.mcp.json` servers without the
        # approval an interactive session asks for, and every user and plugin
        # server (https://code.claude.com/docs/en/mcp, "Project scope"); with
        # `--strict-mcp-config` and no `--mcp-config` it loads none.
        self.listing()
        probe = self.claude.calls()[1]["argv"]
        self.assertIn("--strict-mcp-config", probe)
        self.assertFalse([arg for arg in probe if arg.startswith("--mcp-config")], probe)

    def test_only_the_reply_to_its_own_request_is_the_list(self):
        noise = ["Loading plugins...", json.dumps({"type": "system", "subtype": "init"})]
        self.claude.answer("*", stdout="".join(line + "\n" for line in noise),
                           reply={"models": CLAUDE_ROWS, "account": SIGNED_IN})
        self.assertEqual(self.listing()["status"], "ok")
        self.claude.answer("*", reply={"models": CLAUDE_ROWS, "account": SIGNED_IN},
                           request_id="req_other")
        listing = self.listing()
        self.assertEqual(listing["status"], "failed")
        self.assertIn("no `initialize` reply", listing["detail"])

    def test_every_failure_gives_unverified(self):
        rows = [dict(CLAUDE_ROWS[3])]
        cases = (
            ("exit without reply", {"exit": 1}, "exited with status 1"),
            ("not json", {"stdout": "Invalid API key\n"}, "no `initialize` reply"),
            ("error reply", {"reply": {"error": "boom"}, "subtype": "error"},
             "`initialize` reply is an error"),
            ("models not a list", {"reply": {"models": {"value": "sonnet"}}},
             "no model list"),
            ("row not an object", {"reply": {"models": ["sonnet"]}}, "not a model list"),
            ("efforts malformed", {"reply": {"models": [
                {**rows[0], "supportedEffortLevels": "high"}]}}, "not a model list"),
            ("no models", {"reply": {"models": []}}, "lists no model"),
        )
        for name, case, detail in cases:
            with self.subTest(case=name):
                self.claude.answer("*", **case)
                listing = self.listing()
                self.assertEqual(listing["status"], "failed", listing)
                self.assertIn(detail, listing["detail"])
                self.assertEqual(listing["models"], {})
                self.assertEqual(claude_models.verdict("claude-sonnet-5-5", listing),
                                 "unverified")
                self.assertIsNone(claude_models.efforts("claude-sonnet-5-5", listing))

    def test_a_probe_that_hangs_is_stopped_at_the_time_limit(self):
        pid_file, orphan_file = self.base / "probe.pid", self.base / "orphan.pid"
        self.claude.answer("*", sleep=60, pid_file=str(pid_file), orphan_file=str(orphan_file),
                           reply={"models": CLAUDE_ROWS, "account": SIGNED_IN})
        started = time.monotonic()
        listing = self.listing(timeout=3)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(listing["status"], "failed")
        self.assertIn("timed out after 3 s", listing["detail"])
        self.assertEqual(claude_models.verdict("claude-opus-5-5", listing), "unverified")
        # The probe and a process it started, which held its output open, are gone.
        for path in (pid_file, orphan_file):
            self.assertTrue(wait_gone(int(path.read_text(encoding="utf-8"))), path.name)

    def test_a_version_check_that_hangs_ends_the_listing_at_the_time_limit(self):
        self.claude.answer("--version", sleep=60, stdout="2.1.284 (Claude Code)\n")
        started = time.monotonic()
        listing = self.listing(timeout=2)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((listing["status"], listing["binary"]), ("failed", None))
        self.assertIn("timed out after 2 s", listing["detail"])

    def test_the_binary_is_the_session_s_own_or_a_recent_enough_claude_on_path(self):
        node = fixtures.FakeHost(self.base / "node", "node")
        node.answer("--version", stdout="v22.9.0\n")
        own = self.fake("own")
        with self.subTest(case="CLAUDE_PID when CLAUDE_CODE_EXECPATH is no Claude Code"), \
                mock.patch.object(host_listing, "process_executable",
                                  return_value=str(own.path)) as executable:
            listing = self.listing(CLAUDE_CODE_EXECPATH=str(node.path), CLAUDE_PID="4242")
            self.assertEqual((listing["status"], listing["binary"]), ("ok", str(own.path)))
            self.assertEqual(executable.call_args[0][0], 4242)
        on_path = self.fake("path", version="2.1.290")
        environ = {"PATH": str(on_path.path.parent), "HOME": str(self.base)}
        for min_version, status in (("2.1.284", "ok"), ("2.1.290", "ok"),
                                    ("2.1.300", "no_binary"), (None, "no_binary")):
            with self.subTest(case="claude on PATH", min_version=min_version):
                listing = claude_models.list_models(environ, min_version=min_version)
                self.assertEqual(listing["status"], status, listing)
                if status == "ok":
                    self.assertEqual(listing["binary"], str(on_path.path))
                elif min_version:
                    self.assertIn(f"below {min_version}", listing["detail"])
                else:
                    self.assertIn("min_cli_version", listing["detail"])
        with self.subTest(case="a missing CLAUDE_CODE_EXECPATH falls through"):
            listing = claude_models.list_models(
                {**environ, "CLAUDE_CODE_EXECPATH": str(self.base / "gone")},
                min_version="2.1.284")
            self.assertEqual(listing["binary"], str(on_path.path))
        with self.subTest(case="no binary at all"):
            listing = claude_models.list_models({"PATH": str(self.empty)})
            self.assertEqual((listing["status"], listing["binary"], listing["models"]),
                             ("no_binary", None, {}))
            self.assertEqual(claude_models.verdict("claude-opus-5-5", listing), "unverified")


@unittest.skipIf(os.name != "posix", "the fake host binaries are POSIX shell scripts")
class CodexListingTests(unittest.TestCase):
    """The binary's version picks its cached account catalog or `debug models`."""

    def setUp(self) -> None:
        self.base, self.empty = workspace(self)
        self.home = self.base / "codex-home"
        self.home.mkdir()
        self.codex = self.fake("bin")
        # A test run under Codex, or on a Mac with the ChatGPT app, never
        # reaches the real binary.
        for patcher in (mock.patch.object(host_listing, "ancestor_executables", return_value=[]),
                        mock.patch.object(codex_models, "APP_FOLDERS", ())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, directory: str, version: str = "0.159.2") -> fixtures.FakeHost:
        host = fixtures.FakeHost(self.base / directory, "codex")
        host.answer("--version", stdout=f"codex-cli {version}\n")
        host.answer("debug models", stdout=json.dumps({"models": ACCOUNT}) + "\n")
        host.answer("debug models --bundled", stdout=json.dumps({"models": BUNDLED}) + "\n")
        return host

    def environ(self, **extra) -> dict:
        return {"PATH": str(self.codex.path.parent), "HOME": str(self.base),
                "CODEX_HOME": str(self.home), **extra}

    def cache(self, age: timedelta = timedelta(hours=1), version: str = "0.159.2",
              **override) -> None:
        fetched = datetime.now(timezone.utc) - age
        data = {"fetched_at": fetched.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "etag": "W/\"abc\"",
                "client_version": version, "identity": "identity-not-for-output",
                "models": ACCOUNT, **override}
        (self.home / "models_cache.json").write_text(json.dumps(data), encoding="utf-8")

    def commands(self) -> list:
        return [call["argv"] for call in self.codex.calls()]

    def test_a_fresh_cache_of_this_version_is_the_account_catalog(self):
        self.cache()
        listing = codex_models.list_models(self.environ())
        self.assertEqual(listing["status"], "ok", listing)
        self.assertEqual((listing["binary"], listing["version"], listing["view"]),
                         (str(self.codex.path), "0.159.2", "account"))
        self.assertEqual(listing["models"], {entry["slug"]: {"efforts": [
            level["effort"] for level in entry["supported_reasoning_levels"]],
            "visibility": entry["visibility"]} for entry in ACCOUNT})
        self.assertEqual(codex_models.verdict("gpt-6-luna", listing), "available")
        self.assertEqual(codex_models.verdict("gpt-6.1-sol", listing), "unavailable")
        self.assertEqual(codex_models.efforts("gpt-5.5", listing), L4)
        self.assertIn("models_cache.json", listing["detail"])
        # The cache is read for its catalog alone, with no host command.
        self.assertEqual(self.commands(), [["--version"]])
        self.assertNotIn("identity-not-for-output", json.dumps(listing))

    def test_a_listing_keeps_each_model_s_visibility_and_judges_hidden_ones_too(self):
        # Codex's picker shows only `list` entries; `hide` and `none` stay
        # in the catalog, so a verdict judges them like any other model.
        for name, source in (("cache", "models_cache.json"), ("debug models", "account")):
            with self.subTest(source=name):
                if name == "cache":
                    self.cache(models=ACCOUNT + codex_catalog(("gpt-reserve", "none", L4)))
                else:
                    (self.home / "models_cache.json").unlink(missing_ok=True)
                    self.codex.answer("debug models", stdout=json.dumps(
                        {"models": ACCOUNT + codex_catalog(("gpt-reserve", "none", L4))}) + "\n")
                listing = codex_models.list_models(self.environ())
                self.assertEqual((listing["status"], listing["view"]), ("ok", "account"))
                self.assertIn(source, listing["detail"])
                visibility = {model: entry.get("visibility")
                              for model, entry in listing["models"].items()}
                self.assertEqual(visibility["gpt-6-luna"], "list")
                self.assertEqual(visibility["codex-auto-review"], "hide")
                self.assertEqual(visibility["gpt-reserve"], "none")
                self.assertEqual(codex_models.verdict("codex-auto-review", listing), "available")
                self.assertEqual(codex_models.verdict("gpt-reserve", listing), "available")

    def test_a_stale_or_foreign_cache_falls_back_to_debug_models(self):
        cases = (
            ("older than a day", {"age": timedelta(hours=25)}, "more than 24 hours"),
            ("another version", {"version": "0.159.1"}, "0.159.1, not 0.159.2"),
            ("from the future", {"age": -timedelta(hours=1)}, "in the future"),
            ("no fetch time", {"fetched_at": None}, "no fetch time"),
            ("no catalog", {"models": "gpt-6-luna"}, "no model catalog"),
            ("not json", None, "no readable"),
        )
        for name, change, detail in cases:
            with self.subTest(case=name):
                self.codex.log.unlink(missing_ok=True)
                if change is None:
                    (self.home / "models_cache.json").write_text("{", encoding="utf-8")
                else:
                    self.cache(**change)
                listing = codex_models.list_models(self.environ())
                self.assertEqual((listing["status"], listing["view"]), ("ok", "account"))
                self.assertIn(detail, listing["detail"])
                self.assertEqual(self.commands(), [["--version"], ["debug", "models"],
                                                   ["debug", "models", "--bundled"]])
                self.assertEqual(codex_models.verdict("gpt-6-luna", listing), "available")
        (self.home / "models_cache.json").unlink()
        listing = codex_models.list_models(self.environ())
        self.assertEqual((listing["status"], listing["view"]), ("ok", "account"))

    def test_a_listing_equal_to_the_bundled_catalog_reflects_no_account(self):
        self.codex.answer("debug models", stdout=json.dumps({"models": BUNDLED}) + "\n")
        listing = codex_models.list_models(self.environ())
        self.assertEqual((listing["status"], listing["view"]), ("ok", "generic"))
        self.assertEqual(codex_models.verdict("gpt-6.1-sol", listing), "unverified")
        self.assertEqual(codex_models.verdict("gpt-9-ghost", listing), "unavailable")
        self.assertEqual(codex_models.efforts("gpt-6.1-sol", listing), L6)
        self.assertIn("bundled", listing["detail"])

    def test_inside_the_sandbox_the_listing_reflects_no_account(self):
        for variable in ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED"):
            with self.subTest(variable=variable):
                self.codex.log.unlink(missing_ok=True)
                listing = codex_models.list_models(self.environ(**{variable: "1"}))
                self.assertEqual((listing["status"], listing["view"]), ("ok", "generic"))
                self.assertIn("sandbox", listing["detail"])
                self.assertEqual(self.commands(), [["--version"], ["debug", "models"]])
                self.assertEqual(codex_models.verdict("gpt-6-luna", listing), "unverified")

    def test_an_unknown_bundled_catalog_reflects_no_account(self):
        self.codex.answer("debug models --bundled", exit=2)
        listing = codex_models.list_models(self.environ())
        self.assertEqual((listing["status"], listing["view"]), ("ok", "generic"))
        self.assertIn("--bundled", listing["detail"])

    def test_every_listing_failure_gives_unverified(self):
        cases = (
            ("exit status", {"exit": 3, "stdout": "boom"}, "exited with status 3"),
            ("not json", {"stdout": "Not signed in.\n"}, "not a model catalog"),
            ("no models", {"stdout": json.dumps({"models": []})}, "lists no model"),
            ("no slug", {"stdout": json.dumps({"models": [{"display_name": "x"}]})},
             "not a model catalog"),
            ("efforts malformed", {"stdout": json.dumps({"models": [
                {"slug": "gpt-6-luna", "supported_reasoning_levels": ["high"]}]})},
             "not a model catalog"),
        )
        for name, case, detail in cases:
            with self.subTest(case=name):
                self.codex.answer("debug models", **case)
                listing = codex_models.list_models(self.environ())
                self.assertEqual(listing["status"], "failed", listing)
                self.assertIn(detail, listing["detail"])
                self.assertEqual(codex_models.verdict("gpt-6-luna", listing), "unverified")
                self.assertIsNone(codex_models.efforts("gpt-6-luna", listing))

    def test_a_listing_that_hangs_is_stopped_at_the_time_limit(self):
        pid_file = self.base / "listing.pid"
        self.codex.answer("debug models", sleep=60, pid_file=str(pid_file))
        started = time.monotonic()
        listing = codex_models.list_models(self.environ(), timeout=3)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(listing["status"], "failed")
        self.assertIn("timed out after 3 s", listing["detail"])
        self.assertTrue(wait_gone(int(pid_file.read_text(encoding="utf-8"))))

    def test_the_binary_is_found_in_the_documented_order(self):
        self.cache()
        ancestor = self.fake("ancestor")
        real = self.fake("arg0-target")
        alias = self.base / "arg0"
        alias.mkdir()
        (alias / "apply_patch").symlink_to(real.path)
        configured = self.fake("configured")
        bundled = self.fake("Applications/Tool.app/Contents/Resources/codex-cli/bin")
        empty = str(self.empty)
        steps = (
            ("the nearest codex ancestor",
             {"ancestors": ["/bin/zsh", str(ancestor.path), "/sbin/launchd"]},
             {"PATH": empty}, str(ancestor.path)),
            ("the apply_patch alias target", {}, {"PATH": str(alias)}, str(real.path)),
            ("CODEX_CLI_PATH", {}, {"PATH": empty, "CODEX_CLI_PATH": str(configured.path)},
             str(configured.path)),
            ("codex on PATH", {}, {}, str(self.codex.path)),
            ("an app's bundled CLI", {"folders": (str(self.base / "Applications"),)},
             {"PATH": empty}, str(bundled.path)),
        )
        for name, patches, environ, binary in steps:
            with self.subTest(step=name), \
                    mock.patch.object(host_listing, "ancestor_executables",
                                      return_value=patches.get("ancestors", [])), \
                    mock.patch.object(codex_models, "APP_FOLDERS",
                                      patches.get("folders", ())):
                listing = codex_models.list_models(self.environ(**environ))
                self.assertEqual((listing["status"], listing["binary"]), ("ok", binary),
                                 listing)
        other = self.fake("other", version="0.159.1")
        with self.subTest(step="CODEX_VERSION skips another version"), \
                mock.patch.object(host_listing, "ancestor_executables",
                                  return_value=[str(other.path)]):
            listing = codex_models.list_models(self.environ(CODEX_VERSION="0.159.2"))
            self.assertEqual(listing["binary"], str(self.codex.path))
        with self.subTest(step="no binary"):
            listing = codex_models.list_models(self.environ(PATH=empty, CODEX_VERSION="0.159.3"))
            self.assertEqual((listing["status"], listing["binary"], listing["models"]),
                             ("no_binary", None, {}))
            self.assertEqual(codex_models.verdict("gpt-6-luna", listing), "unverified")


class VerdictTests(unittest.TestCase):
    """Both hosts judge a model against a listing the same way."""

    def listing(self, status: str = "ok", view: str = "account") -> dict:
        return {"status": status, "binary": "/bin/host", "version": "1.0.0", "view": view,
                "models": {"model-a": {"efforts": ["high"]}}, "detail": ""}

    def test_both_hosts_share_one_interface_and_one_verdict(self):
        for module in (claude_models, codex_models):
            with self.subTest(host=module.HOST):
                self.assertIs(module.verdict, host_listing.verdict)
                self.assertIs(module.efforts, host_listing.efforts)
        with mock.patch.object(host_listing, "ancestor_executables", return_value=[]), \
                mock.patch.object(codex_models, "APP_FOLDERS", ()):
            empty = {"PATH": "", "HOME": tempfile.gettempdir()}
            keys = {module.HOST: set(module.list_models(empty, timeout=5))
                    for module in (claude_models, codex_models)}
        self.assertEqual(keys["claude"], {"status", "binary", "version", "view", "models",
                                          "detail"})
        self.assertEqual(keys["claude"], keys["codex"])

    def test_the_verdict_table(self):
        for status, view, model, expected in (
                ("ok", "account", "model-a", "available"),
                ("ok", "generic", "model-a", "unverified"),
                ("ok", "account", "model-b", "unavailable"),
                ("ok", "generic", "model-b", "unavailable"),
                ("failed", "account", "model-a", "unverified"),
                ("no_binary", "generic", "model-b", "unverified")):
            with self.subTest(status=status, view=view, model=model):
                self.assertEqual(host_listing.verdict(model, self.listing(status, view)),
                                 expected)
        malformed = {**self.listing(), "models": ["model-a"]}
        self.assertEqual(host_listing.verdict("model-a", malformed), "unverified")
        self.assertEqual(host_listing.verdict("model-a", None), "unverified")
        self.assertEqual(host_listing.efforts("model-a", self.listing()), ["high"])
        self.assertIsNone(host_listing.efforts("model-a", self.listing("failed")))
        self.assertIsNone(host_listing.efforts("model-b", self.listing()))


class SetupCheckTests(unittest.TestCase):
    """What both project generators make of a listing: verdicts, fallbacks and notes."""

    ROWS = {
        "architect": {"tier": "high", "model": "model-a", "effort": "high"},
        "developer": {"tier": "medium", "model": "model-a", "effort": "medium"},
        "reviewer": {"tier": "low", "model": "model-b", "effort": "low"},
        "writer": {"tier": "medium", "model": "session", "effort": "medium"},
    }
    LISTING = {"status": "ok", "binary": "/opt/host/bin/host", "version": "1.2.3",
               "view": "account", "detail": "the account's list",
               "models": {"model-b": {"efforts": ["low"]}, "model-c": {"efforts": ["low"]}}}

    def test_the_check_judges_each_model_the_roles_run(self):
        pins = host_listing.pinned(self.ROWS, "session", ("high", "medium", "low"))
        self.assertEqual(pins, {
            "model-a": {"tiers": ["high", "medium"], "roles": ["architect", "developer"]},
            "model-b": {"tiers": ["low"], "roles": ["reviewer"]}})
        check = host_listing.model_check(self.LISTING, pins)
        self.assertEqual(check, {"status": "ok", "binary": "/opt/host/bin/host",
                                 "version": "1.2.3", "view": "account",
                                 "detail": "the account's list",
                                 "verdicts": {"model-a": "unavailable", "model-b": "available"}})
        self.assertEqual(host_listing.unavailable(check), {"model-a"})
        self.assertEqual(host_listing.model_check(None, pins), {"status": "not_run"})
        self.assertEqual(host_listing.unavailable({"status": "not_run"}), set())

    def test_an_effort_a_listed_model_outside_the_catalog_does_not_take_is_named(self):
        tiers = {"high": {"model": "model-c", "effort": "high"},
                 "medium": {"model": "model-c", "effort": "low"},
                 "low": {"model": "model-b", "effort": "high"},
                 "other": {"model": "session", "effort": "high"},
                 "unlisted": {"model": "model-z", "effort": "high"}}
        self.assertEqual(
            host_listing.effort_conflicts(self.LISTING, tiers, catalog={"model-b"}),
            [{"tier": "high", "model": "model-c", "effort": "high", "efforts": ["low"]}])
        failed = {**self.LISTING, "status": "failed", "models": {}}
        self.assertEqual(host_listing.effort_conflicts(failed, tiers, catalog=set()), [])

    def test_the_notes_name_the_tier_the_roles_and_the_model(self):
        pins = host_listing.pinned(self.ROWS, "session", ("high", "medium", "low"))
        check = host_listing.model_check({**self.LISTING, "view": "generic"}, pins)
        conflicts = [{"tier": "high", "model": "model-c", "effort": "high", "efforts": ["low"]}]
        notes = host_listing.check_notes("host-project", check, pins, {"model-a"}, conflicts,
                                         "the parent session's model")
        self.assertEqual(len(notes), 3, notes)
        self.assertEqual(notes[0], (
            "host-project: warning: model-a is unavailable for the high and medium tiers:"
            " this host's own model list (generic view, /opt/host/bin/host 1.2.3) does not hold"
            " it. Their roles architect, developer run on the parent session's model at their"
            " own effort; every setup or refresh judges it again."))
        self.assertEqual(notes[1], (
            "host-project: note: model-b keeps its pin unverified: the account's list; the"
            " run-time rule covers it."))
        self.assertEqual(notes[2], (
            "host-project: warning: the high tier runs model-c at effort high, which this"
            " host's own model list does not name for it (low); choose another effort through"
            " /configure models."))
        recorded = host_listing.check_notes("host-project", {"status": "not_run"}, pins,
                                            {"model-b"}, [], "the session's model")
        self.assertIn("model-b is unavailable for the low tier: the last setup judged it"
                      " unavailable. Its roles reviewer run", recorded[0])


class AncestorTests(unittest.TestCase):
    """The Codex binary is found as the nearest `codex` process above setup."""

    def test_a_ps_table_keeps_paths_with_spaces(self):
        table = host_listing.parse_ps_table(
            "  300 200 /bin/sh\n"
            "200 100 /opt/Host Tools/bin/host\n"
            "garbage line\n"
            "  1     0 /sbin/init\n")
        self.assertEqual(table, {
            300: (200, "/bin/sh"),
            200: (100, "/opt/Host Tools/bin/host"),
            1: (0, "/sbin/init")})
        self.assertEqual(host_listing.walk(300, table.get),
                         ["/bin/sh", "/opt/Host Tools/bin/host"])
        looped = {10: (11, "/a"), 11: (10, "/b")}
        self.assertEqual(host_listing.walk(10, looped.get), ["/a", "/b"])

    @unittest.skipIf(os.name != "posix", "symbolic links stand in for /proc/<pid>/exe")
    def test_a_proc_entry_reads_its_parent_and_executable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "100").mkdir()
            (root / "100" / "stat").write_text(
                "100 (code (x) d) S 50 100 100 0 -1 4194560\n", encoding="utf-8")
            (root / "100" / "exe").symlink_to("/opt/codex/bin/codex")
            (root / "50").mkdir()
            (root / "50" / "stat").write_text("50 (zsh) S 1 50 50\n", encoding="utf-8")
            self.assertEqual(host_listing.proc_entry(100, root), (50, "/opt/codex/bin/codex"))
            self.assertEqual(host_listing.proc_entry(50, root), (1, ""))
            self.assertIsNone(host_listing.proc_entry(7, root))

    @unittest.skipIf(os.name != "posix", "the walk reads /proc or ps")
    def test_the_live_walk_starts_at_the_parent_process(self):
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import host_listing;"
                " print(json.dumps(host_listing.ancestor_executables("
                "host_listing.Deadline(10))))")
        result = subprocess.run([sys.executable, "-c", code, str(SHARED)], capture_output=True,
                                text=True, timeout=60, check=True)
        ancestors = json.loads(result.stdout)
        self.assertTrue(ancestors, result.stderr)
        # This test runs the walk in a child of its own Python process.
        self.assertTrue(Path(ancestors[0]).name.lower().startswith("python"), ancestors)


class HostContractTests(unittest.TestCase):
    """Both host contracts state the one strategy in the same words."""

    STRATEGY = (
        "A role whose pinned model cannot run falls back to this session's model with a"
        " visible warning, by one strategy on both hosts:",
        "Setup and refresh read this host's own model list from the binary that runs this"
        " session, without a model request, and judge each model a role is pinned to, the"
        " project's `tier_models` included: `available` when the list holds it and reflects"
        " the signed-in account, `unavailable` when the list does"
        " not hold it, since this binary cannot run it, and `unverified` when no list could be"
        " read or the list reflects no account. Every probe has a time limit, and one that"
        " fails gives `unverified`, never an error that stops setup.",
        "The roles of an `unavailable` model run on this session's model at their own effort,"
        " and setup prints a warning that names the model, its tiers and its roles; every"
        " setup or refresh judges the model again. An `unverified` model keeps its pin with a"
        " note, and the run-time rule covers it.",
        "At run time a role whose pinned model fails gets a warning that never blocks work"
        " and at most one start again on this session's model, only when the failed run"
        " changed nothing.",
    )

    def contract(self, host: str) -> str:
        return " ".join((REPO / "platforms" / host / fixtures.PLUGIN / "host-contract.md")
                        .read_text(encoding="utf-8").split())

    def test_both_contracts_state_the_strategy_and_where_the_hosts_differ(self):
        claude, codex = self.contract("claude"), self.contract("codex")
        for fragment in self.STRATEGY:
            with self.subTest(fragment=fragment[:60]):
                self.assertIn(fragment, claude)
                self.assertIn(fragment, codex)
        for fragment in (
                "On Claude Code the binary is `CLAUDE_CODE_EXECPATH` or the executable of"
                " `CLAUDE_PID`, else `claude` on PATH when its version meets the highest"
                " `min_cli_version` of the catalog models the tiers run.",
                "The list is the `models` of its reply to the `initialize` control request,"
                " and it reflects the account only when the reply's `account.tokenSource`"
                " names a claude.ai login.",
                "The request runs with every hook off and loads no MCP server,"
                " `--strict-mcp-config` without `--mcp-config`, so it starts none of the"
                " project's `.mcp.json` servers, which an interactive session asks the user to"
                " approve first, and no user or plugin server.",
                "Claude Code reports a failed role to hooks, so the plugin's hooks below add"
                " the run-time warning."):
            with self.subTest(fragment=fragment[:60]):
                self.assertIn(fragment, claude)
        for fragment in (
                "On Codex the binary is the nearest `codex` process above setup, since Codex"
                " exports no variable that names it, else the target of the `apply_patch`"
                " alias on PATH, `CODEX_CLI_PATH` when the environment carries it, `codex` on"
                " PATH or the ChatGPT app's bundled CLI, and its version must equal"
                " `CODEX_VERSION` when that is set.",
                "The list is the account catalog Codex caches in `models_cache.json` in its"
                " home when the cache comes from this binary's version and is at most 24"
                " hours old, otherwise the output of `codex debug models`, which reflects no"
                " account when it can only have printed the catalog bundled with the binary:"
                " inside the command sandbox, with a `CODEX_SANDBOX` variable set, or when it"
                " equals `codex debug models --bundled`.",
                "omit `model` and keep `model_reasoning_effort`",
                "Codex has no hook event for a failed role, so the run-time warning comes from"
                " the coordinator rule below."):
            with self.subTest(fragment=fragment[:60]):
                self.assertIn(fragment, codex)
        self.assertNotIn("when a `codex` executable is on PATH", codex)


@unittest.skipIf(os.name != "posix", "the fake host binaries are POSIX shell scripts")
class PackagingTests(unittest.TestCase):
    """Every host package ships its own host_models.py beside the shared core."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_each_package_ships_its_host_s_module_and_runs_it(self):
        for host in ("claude", "codex"):
            scripts = self.root / "dist" / host / fixtures.PLUGIN / "scripts"
            with self.subTest(host=host):
                self.assertEqual(
                    (scripts / "host_models.py").read_bytes(),
                    (REPO / "platforms" / host / "_team/overlay/scripts/host_models.py")
                    .read_bytes())
                self.assertEqual((scripts / "host_listing.py").read_bytes(),
                                 (SHARED / "host_listing.py").read_bytes())
        with tempfile.TemporaryDirectory() as raw:
            claude = fixtures.FakeHost(Path(raw) / "bin", "claude")
            claude.answer("--version", stdout="2.1.284 (Claude Code)\n")
            claude.answer("*", reply={"models": CLAUDE_ROWS, "account": SIGNED_OUT})
            script = self.root / "dist/claude" / fixtures.PLUGIN / "scripts/host_models.py"
            result = subprocess.run(
                [sys.executable, str(script), "claude-opus-5-5", "claude-opus-4-1"],
                capture_output=True, text=True, timeout=60, check=False,
                env={"PATH": str(Path(raw) / "bin"), "HOME": raw,
                     "CLAUDE_CODE_EXECPATH": str(claude.path), "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["listing"]["view"], "generic")
        self.assertEqual(report["verdicts"], {"claude-opus-5-5": "unverified",
                                              "claude-opus-4-1": "unavailable"})
        self.assertEqual(report["efforts"], {"claude-opus-5-5": L5, "claude-opus-4-1": None})


if __name__ == "__main__":
    unittest.main()
