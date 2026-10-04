"""Fallback to the session's model when a role's pinned model cannot run (#349).

Claude Code: the plugin's hook on `Agent` reads recorded hook payloads in the
shapes the hooks reference documents (https://code.claude.com/docs/en/hooks,
"PostToolUse input", "PostToolUseFailure input" and the `Agent` tool's
`tool_response`), with the error text of the errors reference
(https://code.claude.com/docs/en/errors).

Setup on both hosts: the project generators judge each pinned model against
the host's own model list, read by `host_models.py` from fake `claude` and
`codex` executables that the environment pins, so no real host binary or
models cache is reached. No test starts a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import git_fixture  # noqa: E402

PREFIX = f"{fixtures.PLUGIN}:"
HOOK = "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook_launcher.py scripts/model_fallback.py"
# A role is spawned again only when its failed run changed nothing, judged by
# the content its task manifest binds: `git status` reads the same after a
# further edit to an already modified file.
WRITER_GUARD = ("{Start} it again only when the failed run changed nothing: its task manifest's"
                " `write_boundary` is `read_only`, as a reader's is, or the `task_inputs.py`"
                " invocation that derived that manifest, run again with `--expected-hash"
                " <source_hash>`, still passes, since the manifest binds the content of its inputs"
                " and of every modified or new source it covers")
# The packaged script every entry derives a role's task manifest with.
TASK_INPUTS = fixtures.REAL_REPOSITORY / "plugins" / fixtures.PLUGIN / "scripts/task_inputs.py"

# Common input fields of every recorded payload.
SESSION = {
    "session_id": "abc123",
    "transcript_path": "/Users/dev/.claude/projects/app/00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
    "cwd": "/Users/dev/app",
    "permission_mode": "default",
}
# A foreground subagent whose request finds no model fails with this message:
# Claude Code 2.1.284 words an API 404 as an issue with the selected model of
# error type model_not_found, and a subagent's error detail adds the error
# type, the HTTP status, the request id and the model sent to the API.
NOT_FOUND = ("Agent terminated early due to an API error: There's an issue with the selected"
             " model ({model}). It may not exist or you may not have access to it. Run /model to"
             " pick a different model. (error type model_not_found, HTTP 404, request id"
             " req_011CVxyz, model sent to the API: {model})")
# The 404 form when Claude Code knows the provider's ID of a fallback model.
DEPLOYMENT = ("Agent terminated early due to an API error: The model {model} is not available on"
              " your bedrock deployment. Try switching to {fallback}, or ask your admin to enable"
              " this model. (error type model_not_found, HTTP 404, request id req_011CVxyz, model"
              " sent to the API: {model})")
# Amazon Bedrock's answer for a model ID it does not know.
BEDROCK_ID = ("Agent terminated early due to an API error: API Error ({model}): The provided model"
              " identifier is invalid.. (error type model_not_found, HTTP 400, request id"
              " req_011CVxyz, model sent to the API: {model})")
VERSION_GATE = ("Agent terminated early due to an API error: API Error: 400 Claude Code"
                " 2.1.284 does not support this model; version 2.1.290 or newer is required."
                " Run 'claude update', or update the Claude desktop app, then try again.")
PLAN = ("Agent terminated early due to an API error: Claude Opus is not available with the"
        " Claude Pro plan. If you have updated your subscription plan recently, run /logout"
        " and /login for the plan to take effect.")
ACCESS = ("Agent terminated early due to an API error: AccessDeniedException (403): You"
          " don't have access to the model with the specified model ID. model: {model}")


def api_error(message: str, kind: str, status: int, model: str) -> str:
    """Another API error of a subagent on its pin, in Claude Code 2.1.284's detail."""
    return (f"Agent terminated early due to an API error: {message} (error type {kind}, HTTP"
            f" {status}, request id req_011CVxyz, model sent to the API: {model})")


def agent_input(role: str, **extra) -> dict:
    return {"description": "Review the frozen candidate", "prompt": "Review it.",
            "subagent_type": role, **extra}


def failure(role: str, error: str, **extra) -> dict:
    """A recorded PostToolUseFailure payload of an Agent call."""
    return {**SESSION, "hook_event_name": "PostToolUseFailure", "tool_name": "Agent",
            "tool_input": agent_input(role, **extra), "tool_use_id": "toolu_01ABC123",
            "error": error, "is_interrupt": False, "duration_ms": 4187}


def completed(role: str, resolved: str, **extra) -> dict:
    """A recorded PostToolUse payload of a foreground Agent call."""
    return {**SESSION, "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": agent_input(role, **extra), "tool_use_id": "toolu_01ABC124",
            "tool_response": {
                "status": "completed", "agentId": "a4d2c8f1e0b3a297",
                "content": [{"type": "text", "text": "No blocking findings."}],
                "resolvedModel": resolved, "totalTokens": 12450, "totalDurationMs": 48211,
                "totalToolUseCount": 7, "usage": {"input_tokens": 8320, "output_tokens": 410}},
            "duration_ms": 48230}


def launched(role: str, resolved: str) -> dict:
    """A recorded PostToolUse payload of a background Agent launch."""
    return {**SESSION, "hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": agent_input(role), "tool_use_id": "toolu_01ABC125",
            "tool_response": {
                "status": "async_launched", "agentId": "b5e3d9f2a1c4b308",
                "description": "Review the frozen candidate", "prompt": "Review it.",
                "outputFile": "/tmp/claude/tasks/b5e3d9f2a1c4b308.output",
                "resolvedModel": resolved},
            "duration_ms": 31}


class ClaudeFallbackHookTests(unittest.TestCase):
    """The hook warns the user and asks for one re-spawn on the session's model."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root)
        cls.package = cls.root / "dist/claude" / fixtures.PLUGIN
        auto = json.loads(build_distributions.execution_profile_path(cls.root, "claude")
                          .read_text(encoding="utf-8"))["profiles"]["auto"]
        cls.opus, cls.sonnet = auto["high"]["model"], auto["low"]["model"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_hook(self, payload, **environment) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items()
               if key != "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"}
        return subprocess.run(
            [sys.executable, str(self.package / "scripts/hook_launcher.py"), "scripts/model_fallback.py"],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True, text=True, check=False, timeout=60,
            env={**env, "PYTHONDONTWRITEBYTECODE": "1", **environment})

    def response(self, payload, **environment) -> dict | None:
        result = self.run_hook(payload, **environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def test_every_agent_call_and_failure_runs_the_hook(self):
        for source in (self.root / "platforms/claude" / fixtures.PLUGIN / "overlay/hooks",
                       self.package / "hooks"):
            events = json.loads((source / "hooks.json").read_text(encoding="utf-8"))["hooks"]
            for event in ("PostToolUse", "PostToolUseFailure"):
                with self.subTest(source=source, event=event):
                    groups = [group for group in events[event]
                              if any(HOOK in handler["command"] for handler in group["hooks"])]
                    self.assertEqual([group["matcher"] for group in groups], ["Agent"])
                    self.assertEqual(groups[0]["hooks"], [{"type": "command", "command": HOOK,
                                                           "timeout": 10}])
        self.assertTrue((self.package / "scripts/model_fallback.py").is_file())
        self.assertFalse((self.root / "dist/codex" / fixtures.PLUGIN
                          / "scripts/model_fallback.py").exists())

    def test_the_host_contract_and_docs_state_the_fallback(self):
        contract = " ".join((fixtures.REAL_REPOSITORY / "platforms/claude" / fixtures.PLUGIN
                             / "host-contract.md").read_text(encoding="utf-8").split())
        for fragment in (
                "A role whose pinned model cannot run falls back to this session's model with a"
                " visible warning",
                "An organization model policy (`availableModels`, `deniedModels` or an Enterprise"
                " restriction) that blocks a pinned ID already runs the role on the main"
                " conversation's model, and an interactive session shows Claude Code's warning",
                # Claude Code's own wording (https://code.claude.com/docs/en/errors).
                "When a role's Agent result or completion message names its pinned model as"
                " unavailable or refused in Claude Code's wording, error type `model_not_found`,"
                " `There's an issue with the selected model`, `is not available on your ..."
                " deployment`, `is not available with the ... plan` or `don't have access to the"
                " model`, or as too new for this Claude Code, `does not support this model`, tell"
                " the user which role, which model and why.",
                WRITER_GUARD.format(start="spawn", Start="Spawn")
                + "; otherwise stop and report.",
                "That spawn names the same `subagent_type` with `model` set to the family alias"
                " of this session's model",
                "If it fails too, stop and report both errors.",
                "a background role reports its failure only in its completion message",
                "Neither hook fires for a spawn that passes `model` or under"
                " `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`"):
            self.assertIn(fragment, contract)
        self.assertNotIn("retired", contract.split("A role whose pinned model cannot run", 1)[1]
                         .split("Every build also ships the `-mechanical`", 1)[0])
        authoring = " ".join((fixtures.REAL_REPOSITORY / "docs/authoring.md").read_text(
            encoding="utf-8").split("## Execution profiles", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn("A role whose pinned model cannot run falls back to the session's model"
                      " with a visible warning, by one strategy on both hosts.", authoring)
        self.assertIn("`model_fallback.py` under `PostToolUseFailure` and `PostToolUse`",
                      authoring)
        self.assertIn("when the failed run changed nothing, that is its task is read-only or the"
                      " `task_inputs.py` invocation that derived its manifest still passes with"
                      " `--expected-hash`, which compares the content of its inputs and of every"
                      " modified or new source it covers, spawn the same role once more with"
                      " `model` set to the session's family alias", authoring)
        self.assertNotIn("`git status` over that scope reads as it did before", authoring)

    def test_the_validator_requires_the_hook(self):
        self.assertEqual(fixtures.validator_findings(self.root, "single_team_contract"), [])
        hooks = self.root / "platforms/claude" / fixtures.PLUGIN / "overlay/hooks/hooks.json"
        original = hooks.read_bytes()
        data = json.loads(original)
        for event in ("PostToolUse", "PostToolUseFailure"):
            data["hooks"][event] = [group for group in data["hooks"][event]
                                    if group.get("matcher") != "Agent"]
        hooks.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            findings = fixtures.validator_findings(self.root, "single_team_contract")
        finally:
            hooks.write_bytes(original)
        self.assertTrue(any(finding.check == "single_team_contract"
                            and "Claude hooks lack 'model_fallback.py'" in finding.message
                            for finding in findings), findings)

    def test_an_unavailable_pinned_model_asks_for_one_respawn_on_the_session_model(self):
        role = f"{PREFIX}code-reviewer"
        provider = f"us.anthropic.{self.opus}-v1:0"
        for name, error in (("not found", NOT_FOUND.format(model=self.opus)),
                            ("provider form", NOT_FOUND.format(model=provider)),
                            ("not on the deployment", DEPLOYMENT.format(
                                model=provider, fallback=f"us.anthropic.{self.sonnet}-v1:0")),
                            ("unknown provider ID", BEDROCK_ID.format(model=provider)),
                            ("too new for this Claude Code", VERSION_GATE),
                            ("refused by the plan", PLAN),
                            ("refused by the provider", ACCESS.format(model=provider))):
            with self.subTest(error=name):
                output = self.response(failure(role, error))
                self.assertIsNotNone(output)
                self.assertIn(role, output["systemMessage"])
                self.assertIn(self.opus, output["systemMessage"])
                self.assertIn("once more on this session's model if the failed run changed"
                              " nothing", output["systemMessage"])
                specific = output["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "PostToolUseFailure")
                context = " ".join(specific["additionalContext"].split())
                for fragment in (
                        f"pinned model {self.opus}",
                        "Tell the user which role, which model and why",
                        # A writer that failed mid-run must not repeat its edits.
                        WRITER_GUARD.format(start="spawn", Start="Spawn") + ".",
                        "Otherwise stop and report the error and the changed paths to the user",
                        f"spawn the same subagent_type {role} once more",
                        "with `model` set to the family alias of the model this conversation"
                        " runs on, such as `opus` or `sonnet`",
                        "If that spawn fails too, stop and report both errors"):
                    self.assertIn(fragment, context)
                self.assertNotIn("\n", output["systemMessage"])

    def test_the_pin_is_each_role_s_own(self):
        lens = f"{PREFIX}code-reviewer-lens"
        output = self.response(failure(lens, NOT_FOUND.format(model=self.sonnet)))
        self.assertIn(self.sonnet, output["systemMessage"])
        # A not-found error that names another model is not the pin's.
        self.assertIsNone(self.response(failure(lens, NOT_FOUND.format(model=self.opus))))

    def test_other_failures_and_other_calls_stay_silent(self):
        role = f"{PREFIX}code-reviewer"
        for name, payload in (
                ("overloaded", failure(role, "Agent terminated early due to an API error:"
                                             f" overloaded_error (529): model: {self.opus}")),
                ("usage limit", failure(role, "Agent terminated early due to an API error:"
                                              " You've hit your Opus limit · resets 3:45pm")),
                ("effort refused", failure(role, "Agent terminated early due to an API error:"
                                                 " API Error: 400 output_config.effort 'xhigh'"
                                                 " is not supported when thinking is disabled"
                                                 f" on this model. model: {self.opus}")),
                # Errors about something else, whose detail names the pin only
                # as the model sent to the API.
                ("a retired tool", failure(role, api_error(
                    "API Error: 400 web_search_20250305 has been retired; use"
                    " web_search_20260209", "invalid_request", 400, self.opus))),
                ("an unavailable tool", failure(role, api_error(
                    "API Error: 400 the advisor tool is not available for this organization",
                    "invalid_request", 400, self.opus))),
                ("a file not found", failure(role, api_error(
                    "API Error: 400 File not found: file_011CVabc", "invalid_request", 400,
                    self.opus))),
                ("a gateway's 404", failure(role, api_error(
                    "API Error: 502 the upstream answered 404 for /v1/messages", "server_error",
                    502, self.opus))),
                ("another model", failure(role, NOT_FOUND.format(
                    model="claude-haiku-4-5-20251001"))),
                ("built-in agent", failure("Explore", NOT_FOUND.format(model=self.opus))),
                ("another plugin", failure("other-team:code-reviewer",
                                           NOT_FOUND.format(model=self.opus))),
                ("unknown role", failure(f"{PREFIX}ghost", NOT_FOUND.format(model=self.opus))),
                ("path escape", failure(f"{PREFIX}../agents/code-reviewer",
                                        NOT_FOUND.format(model=self.opus))),
                ("another tool", {**failure(role, NOT_FOUND.format(model=self.opus)),
                                  "tool_name": "Bash"}),
                ("another event", {**failure(role, NOT_FOUND.format(model=self.opus)),
                                   "hook_event_name": "PreToolUse"})):
            with self.subTest(payload=name):
                self.assertIsNone(self.response(payload))
        for text in ("", "not json", "[1, 2]", json.dumps({"tool_name": "Agent"})):
            with self.subTest(text=text):
                self.assertIsNone(self.response(text))

    def test_a_payload_is_read_as_utf8_under_any_locale(self):
        # Claude Code writes the payload as UTF-8; a prompt in Turkish must not
        # silence the hook where Python's stdin encoding is ASCII.
        payload = failure(f"{PREFIX}code-reviewer", NOT_FOUND.format(model=self.opus))
        payload["tool_input"]["prompt"] = "Gözden geçir: İşlem ağacı, şema ve çıktı."
        env = {key: value for key, value in os.environ.items()
               if key != "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"}
        result = subprocess.run(
            [sys.executable, str(self.package / "scripts/hook_launcher.py"), "scripts/model_fallback.py"],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True, check=False, timeout=60,
            env={**env, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "ascii"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.opus, json.loads(result.stdout)["systemMessage"])

    def test_a_model_the_user_chose_never_triggers_it_and_a_respawn_never_loops(self):
        role = f"{PREFIX}code-reviewer"
        error = NOT_FOUND.format(model=self.opus)
        # The re-spawn passes `model`, as does a user who picks one for a role.
        for model in ("opus", "sonnet"):
            with self.subTest(model=model):
                self.assertIsNone(self.response(failure(role, error, model=model)))
                self.assertIsNone(self.response(completed(role, self.sonnet, model=model)))
        for value in ("1", "true", "yes"):
            with self.subTest(force=value):
                self.assertIsNone(self.response(failure(role, error),
                                                CLAUDE_CODE_SUBAGENT_MODEL_FORCE=value))
                self.assertIsNone(self.response(completed(role, self.sonnet),
                                                CLAUDE_CODE_SUBAGENT_MODEL_FORCE=value))
        for value in ("0", "false", ""):
            with self.subTest(force=value):
                self.assertIsNotNone(self.response(failure(role, error),
                                                   CLAUDE_CODE_SUBAGENT_MODEL_FORCE=value))

    def test_a_role_that_started_on_another_model_is_reported_and_kept(self):
        role = f"{PREFIX}product-owner"
        for name, payload in (("foreground", completed(role, self.sonnet)),
                              ("background", launched(role, self.sonnet))):
            with self.subTest(run=name):
                output = self.response(payload)
                self.assertIsNotNone(output)
                for value in (role, self.opus, self.sonnet):
                    self.assertIn(value, output["systemMessage"])
                specific = output["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "PostToolUse")
                context = " ".join(specific["additionalContext"].split())
                for fragment in (f"started on {self.sonnet} instead of its pinned model"
                                 f" {self.opus}",
                                 "an organization model policy",
                                 "Tell the user which role ran on which model and why",
                                 "Keep this run and do not spawn the role again"):
                    self.assertIn(fragment, context)

    def test_a_role_on_its_pin_or_an_unknown_provider_form_stays_silent(self):
        role = f"{PREFIX}product-owner"
        for resolved in (self.opus, f"{self.opus}[1m]", f"us.anthropic.{self.opus}-v1:0",
                         f"{self.opus}@20260922",
                         "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2",
                         ""):
            with self.subTest(resolved=resolved):
                self.assertIsNone(self.response(completed(role, resolved)))
        payload = completed(role, self.sonnet)
        del payload["tool_response"]["resolvedModel"]
        self.assertIsNone(self.response(payload))


class ChangedNothingTests(unittest.TestCase):
    """A failed writer changed nothing only when its task manifest still holds."""

    @staticmethod
    def git(root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True).stdout

    @staticmethod
    def task(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TASK_INPUTS), *args], capture_output=True,
                              text=True, check=False, timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_a_further_edit_to_a_modified_file_counts_as_a_change(self):
        with git_fixture.temporary_directory() as raw:
            root = Path(raw).resolve()
            git_fixture.init_repository(root)
            space = "workspace/docs/business-analysis/orders/space.md"
            path = root / space
            path.parent.mkdir(parents=True)
            path.write_text("---\ntype: space\nowner_role: business_analyst\n---\n\n# Orders\n",
                            encoding="utf-8")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "Fixture")
            # An earlier pass left the writer's document modified and a new
            # folder untracked, as a flow that commits only at its end does.
            path.write_text(path.read_text(encoding="utf-8") + "\nEarlier pass.\n",
                            encoding="utf-8")
            notes = path.parent / "notes"
            notes.mkdir()
            (notes / "first.md").write_text("# First\n", encoding="utf-8")
            scope = str(path.parent.relative_to(root))
            derive = ["--entry", "business-analysis", "--role", "business-analyst",
                      "--mode", "revise", "--project-root", str(root), "--input", space]
            derived = self.task(*derive)
            self.assertEqual(derived.returncode, 0, derived.stdout + derived.stderr)
            manifest = json.loads(derived.stdout)
            self.assertEqual(manifest["write_boundary"], "named_owner_only")
            self.assertEqual([row["path"] for row in manifest["write_scope"]["allowed_write_area"]],
                             [space])
            check = [*derive, "--expected-hash", manifest["source_hash"]]
            status = self.git(root, "status", "--porcelain", "--", scope)
            self.assertEqual(sorted(status.splitlines()), [
                f" M {space}", f"?? {scope}/notes/"])
            # A run that failed before it wrote anything passes the check.
            self.assertEqual(self.task(*check).returncode, 0)
            # A writer that failed after a further edit to the modified file and
            # a new file in the untracked folder leaves `git status` as it was;
            # the manifest check sees both.
            path.write_text(path.read_text(encoding="utf-8") + "\nHalf of a fix.\n",
                            encoding="utf-8")
            (notes / "second.md").write_text("# Second\n", encoding="utf-8")
            self.assertEqual(self.git(root, "status", "--porcelain", "--", scope), status)
            stale = self.task(*check)
            self.assertEqual(stale.returncode, 1, stale.stdout)
            self.assertIn("task inputs are stale", json.loads(stale.stdout)["error"])
            # The new file alone is a change as well.
            path.write_text(path.read_text(encoding="utf-8").replace("\nHalf of a fix.\n", ""),
                            encoding="utf-8")
            self.assertEqual(self.task(*check).returncode, 1)
            (notes / "second.md").unlink()
            self.assertEqual(self.task(*check).returncode, 0)


RENDERED = f"{fixtures.PLUGIN}-"
OWNER = f"# Generated by Agent Marketplace {fixtures.PLUGIN}; do not edit by hand."


class RenderedRoleFallbackTests(unittest.TestCase):
    """A role setup rendered into the project, `<plugin>-<role>`, falls back like the
    plugin's own role, on the pin of its rendered file or else of the tier map."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.root = base / "marketplace"
        fixtures.make_valid_root(cls.root)
        cls.package = cls.root / "dist/claude" / fixtures.PLUGIN
        auto = json.loads(build_distributions.execution_profile_path(cls.root, "claude")
                          .read_text(encoding="utf-8"))["profiles"]["auto"]
        cls.opus, cls.sonnet = auto["high"]["model"], auto["low"]["model"]
        cls.project = base / "project"
        (cls.project / "workspace").mkdir(parents=True)
        (cls.project / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": fixtures.PLUGIN,
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        rendered = subprocess.run(
            [sys.executable, str(cls.package / "scripts/generate_claude_project.py"), "apply",
             "--project-root", str(cls.project), "--scope", "local"],
            capture_output=True, text=True, check=False, timeout=120,
            env=fixtures.isolated_hosts(os.environ, base / "isolation"))
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr
        cls.agents = cls.project / ".claude/agents"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def response(self, payload: dict, project: bool = True) -> dict | None:
        env = {key: value for key, value in os.environ.items()
               if key not in {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_PROJECT_DIR"}}
        if project:
            env["CLAUDE_PROJECT_DIR"] = str(self.project)
        result = subprocess.run(
            [sys.executable, str(self.package / "scripts/hook_launcher.py"), "scripts/model_fallback.py"],
            input=json.dumps(payload), capture_output=True, text=True, check=False,
            timeout=60, env={**env, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return json.loads(result.stdout) if result.stdout.strip() else None

    def rewrite(self, role: str, text: str | None) -> None:
        """Replace one rendered file for this test, restoring it afterwards."""
        path = self.agents / f"{RENDERED}{role}.md"
        original = path.read_bytes() if path.is_file() else None
        self.addCleanup(lambda: path.write_bytes(original) if original is not None
                        else path.unlink(missing_ok=True))
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text, encoding="utf-8")

    def test_a_rendered_role_falls_back_on_its_rendered_pin(self):
        agent = f"{RENDERED}code-reviewer"
        output = self.response(failure(agent, NOT_FOUND.format(model=self.opus)))
        self.assertIsNotNone(output)
        self.assertIn(f"{agent} could not run on its pinned model {self.opus}",
                      output["systemMessage"])
        self.assertIn(f"spawn the same subagent_type {agent} once more",
                      " ".join(output["hookSpecificOutput"]["additionalContext"].split()))
        substituted = self.response(completed(agent, self.sonnet))
        self.assertIn(f"{agent} is pinned to {self.opus} but started on {self.sonnet}",
                      substituted["systemMessage"])
        self.assertIsNone(self.response(completed(agent, self.opus)))
        # The payload's cwd serves when CLAUDE_PROJECT_DIR is absent.
        payload = failure(agent, NOT_FOUND.format(model=self.opus))
        payload["cwd"] = str(self.project)
        self.assertIsNotNone(self.response(payload, project=False))

    def test_the_rendered_file_pins_before_the_tier_map(self):
        # A file a refresh has not reached yet keeps the pin it was rendered with.
        agent = f"{RENDERED}qa-engineer"
        stale = (self.agents / f"{agent}.md").read_text(encoding="utf-8").replace(
            f"model: {self.opus}", f"model: {self.sonnet}", 1)
        self.rewrite("qa-engineer", stale)
        self.assertIn(self.sonnet, self.response(
            failure(agent, NOT_FOUND.format(model=self.sonnet)))["systemMessage"])
        self.assertIsNone(self.response(failure(agent, NOT_FOUND.format(model=self.opus))))
        self.assertIsNone(self.response(completed(agent, self.sonnet)))

    def test_without_its_rendered_file_the_tier_map_pins_the_role(self):
        agent = f"{RENDERED}domain-expert"
        self.rewrite("domain-expert", None)
        self.assertIn(self.opus, self.response(
            failure(agent, NOT_FOUND.format(model=self.opus)))["systemMessage"])
        lens = f"{RENDERED}solution-reviewer-lens"
        self.rewrite("solution-reviewer-lens", None)
        self.assertIn(self.sonnet, self.response(
            failure(lens, NOT_FOUND.format(model=self.sonnet)))["systemMessage"])

    def test_without_a_pin_it_stays_silent(self):
        error = NOT_FOUND.format(model=self.opus)
        self.rewrite("helper", "---\nname: software-engineering-team-helper\n"
                               f"description: The user's own.\nmodel: {self.opus}\n---\n\nMine.\n")
        self.rewrite("product-owner", (self.agents / f"{RENDERED}product-owner.md").read_text(
            encoding="utf-8").replace(OWNER + "\n", "", 1))
        self.rewrite("ux-designer", (self.agents / f"{RENDERED}ux-designer.md").read_text(
            encoding="utf-8").replace(f"model: {self.opus}", "model: inherit", 1))
        for role in ("helper", "product-owner", "ux-designer", "ghost", "../agents/code-reviewer"):
            with self.subTest(role=role):
                self.assertIsNone(self.response(failure(f"{RENDERED}{role}", error)))
                self.assertIsNone(self.response(completed(f"{RENDERED}{role}", self.sonnet)))


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


def codex_catalog(*slugs: str, efforts: tuple = ("low", "medium", "high", "xhigh")) -> list:
    """Catalog entries in the shape `codex debug models` prints and Codex caches."""
    return [{"slug": slug, "display_name": slug, "visibility": "list", "priority": index,
             "supported_reasoning_levels": [{"effort": effort, "description": effort}
                                            for effort in efforts]}
            for index, slug in enumerate(slugs)]


# A version no Codex release has, so the model check takes only the fake.
FAKE_CODEX_VERSION = "0.159.2-test"


class CodexModelCheckTests(unittest.TestCase):
    """Apply judges each model a role is pinned to against Codex's own model list."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root, build=False)
        # Two pinned models whatever the package defaults: the low tier moves
        # to another model of the catalog.
        table_path = build_distributions.execution_profile_path(cls.root, "codex")
        table = json.loads(table_path.read_text(encoding="utf-8"))
        catalog = json.loads(build_distributions.model_catalog_path(cls.root, "codex").read_text(
            encoding="utf-8"))["models"]
        cls.pinned = table["profiles"]["auto"]["high"]["model"]
        cls.second = sorted(model for model in catalog if model != cls.pinned)[0]
        efforts = catalog[cls.second]["efforts"]
        table["profiles"]["auto"]["low"] = {"model": cls.second,
                                            "effort": "high" if "high" in efforts else efforts[0]}
        table_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        build_distributions.replace_generated(cls.root, cls.root / "dist")
        package = cls.root / "dist/codex" / fixtures.PLUGIN
        cls.generator = package / "scripts/generate_codex_project.py"
        # Every generated role: its pinned model and its tier's effort.
        cls.pins = {}
        for path in sorted((package / "agents").glob("*.md")):
            fields = build_distributions.parse_frontmatter(path)[0]
            cls.pins[path.stem] = (fields["model"], fields["model_reasoning_effort"])
        cls.on_pinned = sorted(role for role, (model, _effort) in cls.pins.items()
                            if model == cls.pinned)
        cls.on_second = sorted(role for role, (model, _effort) in cls.pins.items()
                             if model == cls.second)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        base = Path(self.work.name)
        self.project = base / "project"
        git_fixture.init_repository(self.project, initial_branch="main")
        self.home = base / "codex-home"
        self.home.mkdir()
        self.empty = base / "empty"
        self.empty.mkdir()
        self.isolation = base / "isolation"
        self.codex = fixtures.FakeHost(base / "bin", "codex")
        self.codex.answer("--version", stdout=f"codex-cli {FAKE_CODEX_VERSION}\n")
        self.listing(self.pinned, self.second, "gpt-5.5")

    def listing(self, *slugs: str, bundled: bool = False) -> None:
        """What `codex debug models` prints, and the bundled catalog it equals or not."""
        printed = json.dumps({"models": codex_catalog(*slugs)}) + "\n"
        self.codex.answer("debug models", stdout=printed)
        self.codex.answer("debug models --bundled", stdout=printed if bundled else json.dumps(
            {"models": codex_catalog(self.pinned, self.second, "gpt-6-astra")}) + "\n")

    def cache(self, *slugs: str, efforts: tuple = ("low", "medium", "high", "xhigh")) -> None:
        """The account catalog Codex caches, fetched an hour ago by the fake's version."""
        fetched = datetime.now(timezone.utc) - timedelta(hours=1)
        (self.home / "models_cache.json").write_text(json.dumps({
            "fetched_at": fetched.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "client_version": FAKE_CODEX_VERSION,
            "models": codex_catalog(*slugs, efforts=efforts)}), encoding="utf-8")

    def environment(self, codex: bool) -> dict:
        env = fixtures.isolated_hosts(os.environ, self.isolation)
        # The fake catalog declares its own account context.
        env = {key: value for key, value in env.items() if not key.startswith("CODEX_SANDBOX")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "CODEX_HOME": str(self.home),
                    "PATH": str(self.codex.path.parent if codex else self.empty)})
        if codex:
            env["CODEX_VERSION"] = FAKE_CODEX_VERSION
        return env

    def generate(self, *args: str, codex: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.generator), *args, "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            env=self.environment(codex))

    def generated(self, *args: str, codex: bool = True) -> tuple[dict, str]:
        result = self.generate(*args, codex=codex)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), " ".join(result.stderr.split())

    def commands(self) -> list:
        return [call["argv"] for call in self.codex.calls()]

    def role_files(self) -> dict[str, str]:
        return {path.stem: path.read_text(encoding="utf-8")
                for path in sorted((self.project / ".codex/agents").glob("*.toml"))}

    def assert_pinned(self, files: dict[str, str], roles) -> None:
        for role in roles:
            model, effort = self.pins[role]
            with self.subTest(role=role):
                self.assertEqual(role_settings(files[role]), [
                    f'model = "{model}"', f'model_reasoning_effort = "{effort}"'])
                self.assertNotIn("# Model", files[role])

    def assert_fallen_back(self, files: dict[str, str], model: str, record: str) -> None:
        for role, (pinned, effort) in self.pins.items():
            if pinned != model:
                continue
            with self.subTest(role=role):
                self.assertEqual(files[role].splitlines()[1], f"# {record}: {model}")
                self.assertEqual(role_settings(files[role]),
                                 [f'model_reasoning_effort = "{effort}"'])

    def test_apply_moves_an_unavailable_model_s_roles_to_the_session_model(self):
        self.assertTrue(self.on_pinned and self.on_second)
        self.cache(self.second, "gpt-5.5")
        result, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(result["execution_profile"], "auto")
        check = result["model_check"]
        self.assertEqual({key: check[key] for key in ("status", "binary", "version", "view")},
                         {"status": "ok", "binary": str(self.codex.path),
                          "version": FAKE_CODEX_VERSION, "view": "account"})
        self.assertIn("models_cache.json", check["detail"])
        self.assertEqual(check["verdicts"], {self.pinned: "unavailable", self.second: "available"})
        self.assertEqual(result["model_fallbacks"], {self.pinned: self.on_pinned})
        files = self.role_files()
        self.assertEqual(set(files), set(self.pins))
        self.assert_fallen_back(files, self.pinned, "Model unavailable")
        self.assert_pinned(files, self.on_second)
        self.assertIn(f"codex-project: warning: {self.pinned} is unavailable for the high and medium"
                      " tiers: this host's own model list (account view,", warning)
        self.assertIn(f"Their roles {', '.join(self.on_pinned)} run on the parent session's model"
                      " at their own effort; every setup or refresh judges it again.", warning)
        self.assertEqual({role: result["roles"][role]["model_source"] for role in self.on_pinned},
                         {role: "fallback" for role in self.on_pinned})
        # The cached account catalog answers: no command beyond the version.
        self.assertEqual(self.commands(), [["--version"]])
        checked, _ = self.generated("check", "--scope", "local")
        self.assertEqual((checked["changes"], checked["model_fallbacks"]),
                         ([], {self.pinned: self.on_pinned}))
        self.assertEqual(checked["model_check"], {"status": "not_run"})
        inspected, _ = self.generated("inspect", "--scope", "local")
        self.assertEqual(inspected["changes"], [])
        self.assertEqual(self.commands(), [["--version"]])

    def test_a_setup_fallback_follows_the_current_verdict(self):
        self.cache(self.second)
        self.generated("apply", "--scope", "local")
        self.assert_fallen_back(self.role_files(), self.pinned, "Model unavailable")
        self.cache(self.pinned, self.second)
        restored, _ = self.generated("apply", "--scope", "all", "--seed-user-files")
        self.assertEqual(restored["model_check"]["verdicts"][self.pinned], "available")
        self.assertEqual(restored["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        # A fallback the list no longer backs ends too: without a verdict the pin stays.
        self.cache(self.second)
        self.generated("apply", "--scope", "local")
        (self.home / "models_cache.json").unlink()
        self.codex.answer("debug models", exit=3, stdout="boom")
        unverified, note = self.generated("apply", "--scope", "local")
        self.assertEqual(unverified["model_check"]["status"], "failed")
        self.assertEqual(unverified["model_check"]["verdicts"],
                         {self.pinned: "unverified", self.second: "unverified"})
        self.assertEqual(unverified["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.assertIn(f"codex-project: note: {', '.join(sorted((self.pinned, self.second)))} keep their pins"
                      " unverified:",
                      note)
        self.assertIn("`codex debug models` exited with status 3", note)

    def test_a_list_that_reflects_no_account_keeps_every_listed_pin_with_a_note(self):
        self.listing(self.pinned, self.second, bundled=True)
        result, note = self.generated("apply", "--scope", "local")
        self.assertEqual((result["model_check"]["status"], result["model_check"]["view"]),
                         ("ok", "generic"))
        self.assertEqual(result["model_check"]["verdicts"],
                         {self.pinned: "unverified", self.second: "unverified"})
        self.assertEqual(result["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.assertIn("keep their pins unverified: no readable models_cache.json;"
                      " `codex debug models` printed the catalog bundled with the binary, so it"
                      " reflects no account; the run-time rule covers them.", note)
        # A catalog listing only: no command starts a model.
        self.assertEqual(self.commands(), [["--version"], ["debug", "models"],
                                           ["debug", "models", "--bundled"]])

    def test_fake_catalog_ignores_ambient_sandbox_markers(self):
        self.listing(self.pinned, self.second, bundled=True)
        markers = {"CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"}
        ambient = {**os.environ, **markers}
        with mock.patch.object(os, "environ", ambient):
            result, note = self.generated("apply", "--scope", "local")
            self.assertEqual(result["model_check"]["view"], "generic")
            self.assertIn("printed the catalog bundled with the binary", note)
            self.assertEqual({key: os.environ[key] for key in markers}, markers)

    def test_without_a_codex_list_every_pin_stays_and_it_says_so(self):
        self.cache(self.second)
        self.generated("apply", "--scope", "local")
        self.assert_fallen_back(self.role_files(), self.pinned, "Model unavailable")
        # The isolated codex lists nothing, so the check judges no model.
        result, note = self.generated("apply", "--scope", "local", codex=False)
        self.assertEqual(result["model_check"]["status"], "failed")
        self.assertEqual(result["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.assertIn(f"codex-project: note: {', '.join(sorted((self.pinned, self.second)))} keep their pins"
                      " unverified:",
                      note)
        self.assertIn("`codex debug models` exited with status 1", note)

    def test_inherit_model_records_a_run_time_fallback_until_restore_model(self):
        self.cache(self.pinned, self.second)
        result, warning = self.generated("apply", "--scope", "local", "--inherit-model", self.pinned)
        self.assertEqual(result["model_fallbacks"], {self.pinned: self.on_pinned})
        files = self.role_files()
        self.assert_fallen_back(files, self.pinned, "Model fallback")
        self.assert_pinned(files, self.on_second)
        self.assertIn(f"{self.pinned} is marked unavailable by --inherit-model", warning)
        # A refresh keeps a run-time record although the list holds the model.
        refreshed, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(refreshed["model_check"]["verdicts"][self.pinned], "available")
        self.assertEqual(refreshed["model_fallbacks"], {self.pinned: self.on_pinned})
        self.assertEqual(self.role_files(), files)
        self.assertIn(f"{self.pinned} is recorded unavailable by --inherit-model", warning)
        restored, _ = self.generated("apply", "--scope", "local", "--restore-model", self.pinned)
        self.assertEqual(restored["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        # An explicit auto profile restores every run-time record as well.
        self.generated("apply", "--scope", "local", "--inherit-model", self.second)
        auto, _ = self.generated("apply", "--scope", "local", "--execution-profile", "auto")
        self.assertEqual(auto["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        for args, message in (
                (("--inherit-model", "gpt-9-ghost"),
                 "no role is pinned to 'gpt-9-ghost'; the pinned models are"),
                (("--scope", "tracked", "--inherit-model", self.pinned),
                 "--inherit-model applies only to the local agent projection"),
                (("--restore-model", "gpt-9-ghost"),
                 "no role is pinned to 'gpt-9-ghost'; the pinned models are"),
                (("--scope", "tracked", "--restore-model", self.pinned),
                 "--restore-model applies only to the local agent projection"),
                (("--inherit-model", self.pinned, "--restore-model", self.pinned),
                 f"--inherit-model and --restore-model both name {self.pinned!r}")):
            with self.subTest(args=args):
                refused = self.generate("apply", *args)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(message, refused.stderr)
        self.assert_pinned(self.role_files(), self.pins)

    def test_a_restored_pin_the_list_lacks_falls_back_by_its_verdict(self):
        # A retried role that errors too leaves no run-time record behind; the
        # list still judges the model at setup.
        self.cache(self.second)
        self.generated("apply", "--scope", "local", "--inherit-model", self.pinned)
        restored, _ = self.generated("apply", "--scope", "local", "--restore-model", self.pinned)
        self.assertEqual(restored["model_fallbacks"], {self.pinned: self.on_pinned})
        self.assert_fallen_back(self.role_files(), self.pinned, "Model unavailable")

    def test_the_inherit_profile_keeps_each_role_s_effort_and_runs_no_check(self):
        result, _ = self.generated("apply", "--scope", "local", "--execution-profile", "inherit")
        self.assertEqual(result["model_check"], {"status": "not_run"})
        self.assertEqual(result["model_fallbacks"], {})
        self.assertEqual(self.commands(), [])
        for role, text in self.role_files().items():
            with self.subTest(role=role):
                self.assertEqual(text.splitlines()[1], "# Execution profile: inherit")
                self.assertEqual(role_settings(text),
                                 [f'model_reasoning_effort = "{self.pins[role][1]}"'])

    def test_a_project_model_is_judged_and_its_effort_checked_against_the_list(self):
        config = self.project / "workspace/config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "schema_version": 2, "team_id": fixtures.PLUGIN, "output_language": "English",
            "terminology_language": "English",
            "tier_models": {"codex": {"high": {"model": "gpt-5.5", "effort": "xhigh"},
                                      "medium": {"model": "gpt-9-ghost"}}}}), encoding="utf-8")
        self.cache(self.second, "gpt-5.5", efforts=("low", "medium", "high"))
        result, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(result["model_check"]["verdicts"],
                         {"gpt-5.5": "available", "gpt-9-ghost": "unavailable",
                          self.second: "available"})
        on_ghost = sorted(role for role, row in result["roles"].items()
                          if row["tier"] == "medium")
        self.assertEqual(result["model_fallbacks"], {"gpt-9-ghost": on_ghost})
        self.assertIn("codex-project: warning: gpt-9-ghost is unavailable for the medium tier",
                      warning)
        # A listed model outside the catalog keeps the effort the project set,
        # with a warning when its list does not name that effort.
        self.assertIn("codex-project: warning: the high tier runs gpt-5.5 at effort xhigh,"
                      " which this host's own model list does not name for it (low, medium,"
                      " high); choose another effort through /configure models.", warning)
        high = next(role for role, row in result["roles"].items() if row["tier"] == "high")
        self.assertEqual(role_settings(self.role_files()[high]),
                         ['model = "gpt-5.5"', 'model_reasoning_effort = "xhigh"'])


    def test_the_host_contract_and_docs_state_the_check_and_the_runtime_rule(self):
        contract = " ".join((fixtures.REAL_REPOSITORY / "platforms/codex" / fixtures.PLUGIN
                             / "host-contract.md").read_text(encoding="utf-8").split())
        for fragment in (
                # test_host_models checks the setup-time strategy both contracts share.
                "omit `model` and keep `model_reasoning_effort`",
                # Only an availability error moves a role, in Codex 0.159.1's
                # wording of the service's answer (codex-rs core/tests/suite/compact.rs).
                "When a role's spawn ends in `Agent errored: ...` and the error names its pinned"
                " model as unknown, unsupported, not found or not supported with this account,"
                " such as `Model not found <model>` or `The '<model>' model is not supported"
                " when using Codex with a ChatGPT account.`, tell the user which role, which"
                " model and why",
                "Any other error, such as a rate limit whose text names the model, moves no"
                " role: report it as it is.",
                WRITER_GUARD.format(start="start", Start="Start")
                + "; otherwise stop and report, and record no fallback.",
                "To start it again, run `<absolute-python>"
                " <absolute-package-scripts>/generate_codex_project.py apply --project-root"
                " <root> --scope local --inherit-model <model>`",
                "start the same `agent_type` again",
                "If it errors again, run that command with `--restore-model <model>` in place of"
                " `--inherit-model <model>`, which renders those roles with their pin again and"
                " drops the record, then stop and report both errors.",
                "--execution-profile inherit`, which omits `model` from every role file and"
                " keeps `model_reasoning_effort`"):
            self.assertIn(fragment, contract)
        self.assertNotIn("If it errors again, stop and report both errors.", contract)
        authoring = " ".join((fixtures.REAL_REPOSITORY / "docs/authoring.md").read_text(
            encoding="utf-8").split("## Execution profiles", 1)[1].split("\n## ", 1)[0].split())
        for fragment in ("omits `model` from every role file and keeps `model_reasoning_effort`",
                         "judge each model a role is pinned to, the project's `tier_models`"
                         " included",
                         "their files record the verdict, so `check` and `inspect` reproduce it,"
                         " and every setup or refresh judges the model again",
                         "otherwise the output of `codex debug models`, which reflects no account"
                         " inside the command sandbox or when it equals `codex debug models"
                         " --bundled`",
                         "Codex has no hook event for a failed role",
                         "`Agent errored: ...` that names its pinned model as unknown, unsupported,"
                         " not found or not supported with the account",
                         "tells the user and, when the failed run changed nothing, runs"
                         " `generate_codex_project.py apply --scope local --inherit-model <model>`",
                         "That run-time record stays until `--restore-model <model>` or"
                         " `--execution-profile auto`.",
                         "the coordinator runs it with `--restore-model <model>`, which renders"
                         " the pin again, before it stops"):
            self.assertIn(fragment, authoring)
        self.assertNotIn("omits both keys", authoring)
        self.assertNotIn("when a `codex` executable is on PATH", authoring)
        # The fallback belongs to the model exception of the default-path rule.
        architecture = " ".join((fixtures.REAL_REPOSITORY / "docs/architecture.md").read_text(
            encoding="utf-8").split())
        invariant = architecture.split(" 30. ", 1)[1].split(" 31. ", 1)[0]
        self.assertIn("so a catalog change reaches every role under any policy, and a role whose"
                      " pinned model cannot run falls back to the session's model with a warning",
                      invariant)

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def claude_rows(*models: str, efforts: list = CLAUDE_EFFORTS) -> list:
    """`initialize` model rows in Claude Code 2.1.284's shape."""
    return [{"value": model, "resolvedModel": model, "displayName": model,
             "supportsEffort": True, "supportedEffortLevels": list(efforts)} for model in models]


class ClaudeModelCheckTests(unittest.TestCase):
    """Apply judges each model a rendered role runs against Claude Code's own list."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root)
        cls.package = cls.root / "dist/claude" / fixtures.PLUGIN
        cls.generator = cls.package / "scripts/generate_claude_project.py"
        cls.map = json.loads((cls.package / "templates/tier-map.json").read_text(
            encoding="utf-8"))["hosts"]["claude"]
        cls.opus, cls.sonnet = cls.map["tiers"]["high"]["model"], cls.map["tiers"]["low"]["model"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        base = Path(self.work.name)
        self.project = base / "project"
        git_fixture.init_repository(self.project, initial_branch="main")
        self.config({})
        self.isolation = base / "isolation"
        self.empty = base / "empty"
        self.empty.mkdir()
        self.claude = fixtures.FakeHost(base / "session", "claude")
        self.claude.answer("--version", stdout="2.1.284 (Claude Code)\n")
        self.reply(self.sonnet, "claude-haiku-4-5-20251001")

    def config(self, extra: dict) -> None:
        path = self.project / "workspace/config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 2, "team_id": fixtures.PLUGIN, "output_language": "English",
            "terminology_language": "English", **extra}), encoding="utf-8")

    def reply(self, *models: str, source: str = "claude.ai", efforts: list = CLAUDE_EFFORTS):
        self.claude.answer("*", reply={"models": claude_rows(*models, efforts=efforts),
                                       "account": {"tokenSource": source}})

    def environment(self, **extra) -> dict:
        env = fixtures.isolated_hosts(os.environ, self.isolation)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_CODE_EXECPATH": str(self.claude.path),
                    **extra})
        return {key: value for key, value in env.items() if value is not None}

    def generated(self, *args: str, **extra) -> tuple[dict, str]:
        result = subprocess.run(
            [sys.executable, str(self.generator), *args, "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            env=self.environment(**extra))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), " ".join(result.stderr.split())

    def rendered(self) -> dict[str, list[str]]:
        """Each rendered role's frontmatter lines."""
        found = {}
        for path in sorted((self.project / ".claude/agents").glob(f"{RENDERED}*.md")):
            text = path.read_text(encoding="utf-8")
            found[path.stem[len(RENDERED):]] = text[4:text.index("\n---\n", 4)].splitlines()
        return found

    def roles_on(self, *tiers: str) -> list[str]:
        roles = json.loads((self.package / "templates/tier-map.json").read_text(
            encoding="utf-8"))["roles"]
        return sorted(role for tier in tiers for role in roles[tier])

    def test_apply_renders_an_unavailable_model_s_roles_on_the_session_model(self):
        result, warning = self.generated("apply", "--scope", "local")
        check = result["model_check"]
        self.assertEqual({key: check[key] for key in ("status", "binary", "version", "view")},
                         {"status": "ok", "binary": str(self.claude.path), "version": "2.1.284",
                          "view": "account"})
        self.assertEqual(check["verdicts"], {self.opus: "unavailable", self.sonnet: "available"})
        on_opus = self.roles_on("high", "medium")
        self.assertEqual(result["model_fallbacks"], {self.opus: on_opus})
        rendered = self.rendered()
        for role in on_opus:
            with self.subTest(role=role):
                tier = "high" if role in self.roles_on("high") else "medium"
                lines = rendered[role]
                # The stamp names the settings the config resolves, so the
                # session start check finds the render current.
                self.assertIn(f" settings sha256:{settings_digest(self.opus, tier, self.map)}",
                              lines[1])
                self.assertEqual(lines[2], f"# Model unavailable: {self.opus}")
                self.assertIn("model: inherit", lines)
                self.assertIn(f"effort: {self.map['tiers'][tier]['effort']}", lines)
                self.assertEqual(result["roles"][role]["model_source"], "fallback")
        for role in self.roles_on("low"):
            with self.subTest(role=role):
                self.assertIn(f"model: {self.sonnet}", rendered[role])
                self.assertNotIn(f"# Model unavailable: {self.opus}", rendered[role])
        self.assertIn(f"claude-project: warning: {self.opus} is unavailable for the high and"
                      " medium tiers: this host's own model list (account view,", warning)
        self.assertIn(f"Their roles {', '.join(on_opus)} run on this session's model at their"
                      " own effort; every setup or refresh judges it again.", warning)
        # The probe sent its one `initialize` request, and no user message.
        probes = [call for call in self.claude.calls() if call["argv"] != ["--version"]]
        self.assertEqual(len(probes), 1)
        self.assertEqual([json.loads(line)["type"] for line in probes[0]["stdin"].splitlines()],
                         ["control_request"])
        checked, _ = self.generated("check", "--scope", "local")
        self.assertEqual((checked["changes"], checked["model_fallbacks"]),
                         ([], {self.opus: on_opus}))
        self.assertEqual(checked["model_check"], {"status": "not_run"})
        inspected, _ = self.generated("inspect", "--scope", "local")
        self.assertEqual(inspected["model_fallbacks"], {self.opus: on_opus})
        self.assertEqual(len([call for call in self.claude.calls()
                              if call["argv"] != ["--version"]]), 1)
        guard = subprocess.run(
            [sys.executable, str(self.package / "scripts/hook_launcher.py"), "scripts/team_guard.py", "register"],
            input=json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                              "session_id": "fallback", "cwd": str(self.project)}),
            capture_output=True, text=True, check=False, timeout=60,
            env=self.environment(CLAUDE_PROJECT_DIR=str(self.project)))
        self.assertEqual((guard.returncode, guard.stderr), (0, ""))
        self.assertNotIn("AGENT_MARKETPLACE_RENDERED_AGENTS: stale", guard.stdout)

    def test_a_fallback_follows_the_verdict_at_every_refresh(self):
        self.generated("apply", "--scope", "local")
        self.reply(self.opus, self.sonnet)
        restored, _ = self.generated("apply", "--scope", "local")
        self.assertEqual(restored["model_fallbacks"], {})
        self.assertTrue(all(f"model: {self.opus}" in lines
                            for role, lines in self.rendered().items()
                            if role in self.roles_on("high", "medium")))
        # A list that reflects no account judges nothing unavailable it holds.
        self.reply(self.sonnet, source="apiKeyHelper")
        generic, note = self.generated("apply", "--scope", "local")
        self.assertEqual((generic["model_check"]["view"], generic["model_check"]["verdicts"]),
                         ("generic", {self.opus: "unavailable", self.sonnet: "unverified"}))
        self.assertIn(f"claude-project: note: {self.sonnet} keeps its pin unverified:", note)
        self.assertIn("token source apiKeyHelper is no claude.ai login", note)
        # A failed probe judges nothing, so every pin stays.
        self.claude.answer("*", exit=1)
        failed, note = self.generated("apply", "--scope", "local")
        self.assertEqual(failed["model_check"]["status"], "failed")
        self.assertEqual(failed["model_fallbacks"], {})
        self.assertIn(f"{self.opus}, {self.sonnet} keep their pins unverified: Claude Code's"
                      " `initialize` probe failed", note)

    def test_the_cli_floor_is_the_highest_minimum_of_the_models_the_tiers_run(self):
        floors = {model: entry["min_cli_version"] for model, entry in self.map["models"].items()}
        floor = max(floors[self.opus], floors[self.sonnet], key=release)
        major, minor, patch = release(floor)
        older = f"{major}.{minor}.{patch - 1}" if patch else f"{major}.{minor - 1}.999"
        # The case needs the high tier's model to run on a CLI the low tier's cannot.
        self.assertLess(release(floors[self.opus]), release(older))
        on_path = fixtures.FakeHost(Path(self.work.name) / "path", "claude")
        on_path.answer("--version", stdout=f"{older} (Claude Code)\n")
        on_path.answer("*", reply={"models": claude_rows(self.opus, self.sonnet),
                                   "account": {"tokenSource": "claude.ai"}})
        outside = {"CLAUDE_CODE_EXECPATH": None, "PATH": str(on_path.path.parent)}
        result, note = self.generated("apply", "--scope", "local", **outside)
        self.assertEqual(result["model_check"]["status"], "no_binary")
        self.assertIn(f"`claude` on PATH ({on_path.path}) is {older}, below {floor}", note)
        # With the high tier's model alone on every tier the floor is that model's own minimum.
        self.config({"tier_models": {"claude": {"low": {"model": self.opus}}}})
        result, _ = self.generated("apply", "--scope", "local", **outside)
        self.assertEqual((result["model_check"]["status"], result["model_check"]["binary"]),
                         ("ok", str(on_path.path)))

    def test_a_project_model_is_judged_and_its_effort_checked_against_the_list(self):
        fable, ghost = "claude-fable-5-1", "claude-ghost-9-9"
        self.assertNotIn(fable, self.map["models"])
        self.config({"tier_models": {"claude": {"high": {"model": fable, "effort": "max"},
                                                "low": {"model": ghost}}}})
        self.reply(self.opus, fable, efforts=["low", "medium", "high"])
        result, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(result["model_check"]["verdicts"],
                         {fable: "available", ghost: "unavailable", self.opus: "available"})
        self.assertEqual(result["model_fallbacks"], {ghost: self.roles_on("low")})
        self.assertIn(f"claude-project: warning: the high tier runs {fable} at effort max, which"
                      " this host's own model list does not name for it (low, medium, high);"
                      " choose another effort through /configure models.", warning)
        high = self.roles_on("high")[0]
        self.assertIn(f"model: {fable}", self.rendered()[high])
        self.assertIn("effort: max", self.rendered()[high])


def release(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def settings_digest(model: str, tier: str, host_map: dict) -> str:
    """The settings digest the stamp of a role on ``tier`` carries for ``model``."""
    effort = host_map["tiers"][tier].get("effort")
    return hashlib.sha256(json.dumps({"model": model, "effort": effort},
                                     sort_keys=True).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
