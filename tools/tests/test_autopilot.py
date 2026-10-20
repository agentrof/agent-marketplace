"""Autopilot entry: user-armed grants, their classes, goals, guards and hooks (#346)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "plugins" / "software-engineering-team"
ENTRY = PACKAGE / "skill-content" / "autopilot"
SCRIPT = ENTRY / "scripts" / "autopilot.py"
for _path in (PACKAGE / "scripts", ROOT / "tools", ROOT / "tools" / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import build_distributions  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
from git_fixture import init_repository, temporary_directory  # noqa: E402


def load_autopilot():
    spec = importlib.util.spec_from_file_location("autopilot_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


autopilot = load_autopilot()
POLICY = json.loads((ENTRY / "data" / "autopilot-policy.json").read_text(encoding="utf-8"))
DEFAULTS = {entry["id"]: entry["default"] for entry in POLICY["classes"]}
ALLOWED = [name for name, default in DEFAULTS.items() if default == "allowed"]
NOW = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
NAME = "software-engineering-team:autopilot"
RUNTIME = Path(".agentrof/agent-marketplace/.runtime/autopilot")
SESSION_ENV = "FIXTURE_SESSION_ID"
ARMED_HOOKS = {"hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command":
                                     "python3 autopilot.py hook user-prompt --host fixture"
                                     f" --entry {NAME} --session-env {SESSION_ENV}"}]}],
    "PreToolUse": [{"matcher": "ask", "hooks": [{"type": "command", "command":
                                                 "python3 autopilot.py hook pre-question"
                                                 " --host fixture"}]}],
}}
# The session variables the hosts set; a test run inside a host session must not inherit them.
HOST_SESSION_VARIABLES = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
HOST_HOOKS = {
    "claude": ("UserPromptExpansion", "^software-engineering-team:autopilot$", "AskUserQuestion",
               '"${CLAUDE_PLUGIN_ROOT}"'),
    "codex": ("UserPromptSubmit", None, "^request_user_input(_async)?$", '"${PLUGIN_ROOT}"'),
}


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout


def make_project(root: Path) -> Path:
    init_repository(root)
    (root / ".gitignore").write_text("/.agentrof/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture")
    return root.resolve()


def expansion(root: Path, arguments: str, **fields) -> dict:
    """The payload a user-typed slash command expansion delivers."""
    return {"session_id": "session-1", "cwd": str(root), "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command", "command_name": NAME, "command_args": arguments,
            "command_source": "plugin", "prompt": f"/{NAME} {arguments}".strip(), **fields}


def prompt(root: Path, text: str, **fields) -> dict:
    """The payload a submitted prompt delivers."""
    return {"session_id": "session-2", "turn_id": "turn-1", "cwd": str(root),
            "hook_event_name": "UserPromptSubmit", "prompt": text, **fields}


class Project:
    """One fixture project; ``armed`` selects a package that declares the user-prompt hook.

    CI inventories only classes that derive from unittest.TestCase alone, so
    the test classes hold this fixture instead of inheriting it.
    """

    def __init__(self, test: unittest.TestCase, armed: bool = False) -> None:
        self.test = test
        stack = contextlib.ExitStack()
        test.addCleanup(stack.close)
        root = Path(stack.enter_context(temporary_directory())).resolve()
        real = getattr(getattr(type(test), test._testMethodName), "_test_level", None) == "integration"
        if real:
            self.root = make_project(root)
        else:
            self.root = root
            (root / ".git").mkdir()
            # Only Git transport is supplied. main_worktree and all grant rules run unchanged.
            def git_output(project, *args):
                if Path(project).resolve() != root:
                    raise AssertionError("unexpected fixture checkout")
                if args == ("rev-parse", "--git-common-dir"):
                    return ".git"
                if args == ("worktree", "list", "--porcelain"):
                    return f"worktree {root}\nHEAD {'0' * 40}\nbranch refs/heads/main\n"
                raise AssertionError("unexpected Git operation: " + " ".join(args))
            import project_paths
            stack.enter_context(mock.patch.object(project_paths, "run_git", side_effect=git_output))
        hooks = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hooks.json"
        if armed:
            hooks.write_text(json.dumps(ARMED_HOOKS), encoding="utf-8")
        stack.enter_context(mock.patch.object(autopilot, "HOOKS", hooks))
        self.state = self.root / RUNTIME

    def run_verb(self, *argv: str, now: datetime = NOW) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = autopilot.main(["--project-root", str(self.root), *argv], now=now)
        return code, out.getvalue(), err.getvalue()

    def hook(self, verb: str, payload, now: datetime = NOW) -> tuple[int, str]:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = autopilot.main(["hook", verb, "--host", "fixture", "--entry", NAME],
                                  now=now, stdin=io.StringIO(text))
        return code, out.getvalue()

    def arm(self, arguments: str, now: datetime = NOW) -> None:
        self.test.assertEqual(self.hook("user-prompt", expansion(self.root, arguments), now), (0, ""))

    def grant(self) -> dict:
        return json.loads((self.state / "grant.json").read_text(encoding="utf-8"))

    def events(self) -> list[dict]:
        path = self.state / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def assert_refused(self, fragment: str, *argv: str, now: datetime = NOW) -> None:
        code, out, err = self.run_verb(*argv, now=now)
        self.test.assertEqual(code, 1, out + err)
        self.test.assertIn(fragment, err)


class GrantTermsTests(unittest.TestCase):
    """A host without the user-prompt hook: `on` reads its own options."""

    def setUp(self) -> None:
        self.p = Project(self)

    def test_on_without_a_duration_lasts_the_default_duration(self):
        code, out, err = self.p.run_verb("on")
        self.assertEqual(code, 0, err)
        grant = self.p.grant()
        self.assertEqual(grant["state"], "active")
        self.assertEqual(grant["granted_at"], stamp(NOW))
        self.assertEqual(grant["expires_at"],
                         stamp(NOW + timedelta(hours=POLICY["default_duration_hours"])))
        self.assertEqual(grant["classes"], ALLOWED)
        self.assertIsNone(grant["goal"])
        self.assertEqual(grant["armed_by"], {"guard": "user_only_entry", "arguments": "on"})
        self.assertIn(grant["id"], out)

    def test_for_above_the_maximum_is_refused(self):
        maximum = POLICY["max_duration_hours"]
        self.p.assert_refused(f"is above the {maximum} h maximum", "on", "--for", f"{maximum + 1}h")
        self.p.assert_refused("maximum away", "on", "--until",
                              stamp(NOW + timedelta(hours=maximum, minutes=1)))
        self.assertFalse((self.p.root / ".agentrof").exists())

    def test_until_in_the_past_is_refused(self):
        self.p.assert_refused("is in the past", "on", "--until", stamp(NOW - timedelta(minutes=1)))
        self.p.assert_refused("give --for or --until, not both", "on", "--for", "1h", "--until",
                              stamp(NOW + timedelta(hours=2)))
        code, _out, err = self.p.run_verb("on", "--until", "2026-10-02T05:00:00+03:00")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.p.grant()["expires_at"], "2026-10-02T02:00:00Z")

    def test_until_a_clock_time_is_its_next_local_occurrence(self):
        code, _out, err = self.p.run_verb("on", "--until", "07:00")
        self.assertEqual(code, 0, err)
        expires = autopilot.parse_stamp(self.p.grant()["expires_at"])
        self.assertTrue(timedelta(0) < expires - NOW <= timedelta(days=1))
        self.assertEqual(expires.astimezone().strftime("%H:%M"), "07:00")

    def test_on_while_active_replaces_the_grant_and_records_the_replacement(self):
        self.p.run_verb("on")
        first = self.p.grant()["id"]
        later = NOW + timedelta(minutes=5)
        code, out, err = self.p.run_verb("on", "--for", "2h", now=later)
        self.assertEqual(code, 0, err)
        second = self.p.grant()
        self.assertNotEqual(second["id"], first)
        self.assertEqual(second["replaces"], first)
        self.assertIn(f"replaces: {first}", out)
        replaced = [event for event in self.p.events() if event["event"] == "replaced"]
        self.assertEqual(len(replaced), 1)
        self.assertEqual((replaced[0]["grant"], replaced[0]["replaced_by"]), (first, second["id"]))

    def test_allow_naming_a_never_class_is_refused_and_named(self):
        for name in (name for name, default in DEFAULTS.items() if default == "never"):
            with self.subTest(name=name):
                self.p.assert_refused(f"class {name!r} is never delegated", "on", "--allow", name)
        self.assertFalse((self.p.root / ".agentrof").exists())

    def test_allow_or_deny_naming_an_unknown_class_is_refused_and_named(self):
        self.p.assert_refused("unknown class 'overtime'", "on", "--allow", "overtime")
        self.p.assert_refused("unknown class 'overtime'", "on", "--deny", "choice,overtime")

    def test_deny_removes_a_default_class_and_allow_adds_an_excluded_one(self):
        code, _out, err = self.p.run_verb("on", "--deny", "merge", "--allow", "release")
        self.assertEqual(code, 0, err)
        classes = self.p.grant()["classes"]
        self.assertNotIn("merge", classes)
        self.assertIn("release", classes)
        self.assertNotIn("phase_start", classes)
        self.p.assert_refused("both allowed and denied", "on", "--allow", "release",
                              "--deny", "release")

    def test_goal_refusals_name_what_is_wrong(self):
        self.p.assert_refused("unknown goal kind 'sprint'; declared kinds: text",
                              "on", "--goal", "sprint:9")
        self.p.assert_refused("names no target", "on", "--goal", "text:")
        self.assertFalse((self.p.root / ".agentrof").exists())

    def test_a_goal_grant_ends_at_its_cap_or_at_the_earlier_given_time(self):
        code, _out, err = self.p.run_verb("on", "--goal", "text:night run")
        self.assertEqual(code, 0, err)
        grant = self.p.grant()
        self.assertEqual(grant["expires_at"],
                         stamp(NOW + timedelta(hours=POLICY["default_goal_cap_hours"])))
        self.assertEqual(grant["goal"]["target"], "night run")
        code, _out, err = self.p.run_verb("on", "--goal", "text:night run", "--for", "3h")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.p.grant()["expires_at"], stamp(NOW + timedelta(hours=3)))


class ArmingTests(unittest.TestCase):
    """A host whose package declares the user-prompt hook: only the typed command arms."""

    def setUp(self) -> None:
        self.p = Project(self, armed=True)

    def test_on_without_a_fresh_arming_record_is_refused_and_writes_nothing(self):
        self.p.assert_refused("no fresh arming record from your own typed entry command", "on")
        self.assertFalse((self.p.root / ".agentrof").exists())
        ttl = timedelta(minutes=POLICY["arming_ttl_minutes"])
        self.p.arm("on --for 2h", now=NOW - ttl - timedelta(minutes=1))
        before = sorted(path.name for path in self.p.state.iterdir())
        self.p.assert_refused("no fresh arming record", "on")
        self.assertEqual(sorted(path.name for path in self.p.state.iterdir()), before)
        self.assertFalse((self.p.state / "grant.json").exists())

    def test_on_takes_the_grant_options_only_from_the_arming_record(self):
        self.p.arm("on --for 3h --deny merge")
        code, out, err = self.p.run_verb("on")
        self.assertEqual(code, 0, err)
        grant = self.p.grant()
        self.assertEqual(grant["expires_at"], stamp(NOW + timedelta(hours=3)))
        self.assertNotIn("merge", grant["classes"])
        self.assertEqual(grant["armed_by"]["guard"], "user_prompt_hook")
        self.assertEqual(grant["armed_by"]["arguments"], "on --for 3h --deny merge")
        self.assertEqual(grant["armed_by"]["session_id"], "session-1")
        self.assertEqual(grant["host"], "fixture")
        self.assertIn("user_prompt_hook", out)
        self.assertFalse((self.p.state / "arming.json").exists())
        self.p.assert_refused("no fresh arming record", "on")

    def test_on_with_options_of_its_own_is_refused_and_keeps_the_arming(self):
        self.p.arm("on --for 1h")
        self.p.assert_refused("run `on` without options", "on", "--for", "72h")
        self.p.assert_refused("run `on` without options", "on", "--allow", "release")
        self.assertTrue((self.p.state / "arming.json").exists())
        self.assertFalse((self.p.state / "grant.json").exists())
        code, _out, err = self.p.run_verb("on")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.p.grant()["expires_at"], stamp(NOW + timedelta(hours=1)))

    def test_invalid_typed_options_are_refused_and_consume_the_arming(self):
        self.p.arm(f"on --for {POLICY['max_duration_hours'] + 1}h")
        self.p.assert_refused("maximum", "on")
        self.assertFalse((self.p.state / "arming.json").exists())
        self.assertFalse((self.p.state / "grant.json").exists())

    def test_user_prompt_hook_arms_only_for_the_typed_entry_on_command(self):
        arming = self.p.state / "arming.json"
        ignored = (
            expansion(self.p.root, "status"),
            expansion(self.p.root, "off"),
            expansion(self.p.root, ""),
            expansion(self.p.root, "on", command_name="software-engineering-team:deliver"),
            expansion(self.p.root, "on", expansion_type="mcp_prompt"),
            expansion(self.p.root, "on --for 72h", agent_id="subagent-1"),
            prompt(self.p.root, "please turn on $software-engineering-team:autopilot for 9h"),
            prompt(self.p.root, f"${NAME} status"),
            prompt(self.p.root, f"${NAME}x on"),
            prompt(self.p.root, "Run the backlog review."),
            prompt(self.p.root, f"${NAME} on --for 72h", agent_id="thread-2", agent_type="worker"),
        )
        for payload in ignored:
            with self.subTest(payload=payload):
                self.assertEqual(self.p.hook("user-prompt", payload), (0, ""))
                self.assertFalse(arming.exists())
        armed = (
            (expansion(self.p.root, "on --for 9h"), "on --for 9h"),
            (prompt(self.p.root, f"  ${NAME} on --goal text:\"finish it\"\nand more"),
             "on --goal text:\"finish it\""),
            (prompt(self.p.root, f"[${NAME}](/skills/autopilot/SKILL.md) on --until 07:00"),
             "on --until 07:00"),
            (prompt(self.p.root, f"/{NAME} on"), "on"),
        )
        for payload, arguments in armed:
            with self.subTest(payload=payload):
                arming.unlink(missing_ok=True)
                self.assertEqual(self.p.hook("user-prompt", payload), (0, ""))
                record = json.loads(arming.read_text(encoding="utf-8"))
                self.assertEqual(record["arguments"], arguments)
                self.assertEqual(record["armed_at"], stamp(NOW))
                self.assertEqual(record["host"], "fixture")


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project(self)

    def record(self, name: str = "choice", now: datetime = NOW, **fields) -> tuple[int, str, str]:
        values = {"question": "Which cache root?",
                  "options": "One per checkout; One per Item", "choice": "One per checkout",
                  "reason": "the recommended option",
                  "target": "workspace/docs/delivery/deliveries/dlv-001-night/delivery.md"
                            "#User Decisions D-07", **fields}
        argv = ["record", "--class", name]
        for key, value in values.items():
            argv += [f"--{key}", value]
        return self.p.run_verb(*argv, now=now)

    def queue(self, name: str = "release", now: datetime = NOW) -> tuple[int, str, str]:
        return self.p.run_verb("queue", "--class", name, "--question", "Publish the tag?",
                               "--options", "Publish; Hold", "--recommendation", "Hold",
                               "--blocks", "release notes", now=now)

    def test_failed_authority_write_keeps_the_previous_bytes(self):
        import atomic_file
        self.p.run_verb("on")
        path = self.p.state / "grant.json"
        before = path.read_bytes()
        changed = {**json.loads(before), "id": "unpublished"}
        with mock.patch.object(atomic_file, "replace_bytes", side_effect=OSError("interrupted write")):
            with self.assertRaises(OSError):
                autopilot.write_private(path, changed)
        self.assertEqual(path.read_bytes(), before)

    def test_check_reports_an_active_an_expired_and_no_grant(self):
        code, out, _err = self.p.run_verb("check")
        self.assertEqual((code, out.strip()), (1, "autopilot: inactive"))
        self.assertFalse((self.p.root / ".agentrof").exists())
        self.p.run_verb("on", "--for", "2h", "--deny", "merge")
        grant = self.p.grant()
        code, out, _err = self.p.run_verb("check", now=NOW + timedelta(minutes=30))
        self.assertEqual(code, 0)
        self.assertIn(f"autopilot: active {grant['id']} until {grant['expires_at']}", out)
        described = {entry["id"]: entry["description"] for entry in POLICY["classes"]}
        self.assertIn("allowed classes:\n"
                      f"- choice: {described['choice']}\n"
                      f"- approval_gate: {described['approval_gate']}\n", out)
        self.assertNotIn("- merge:", out)
        code, out, _err = self.p.run_verb("check", now=NOW + timedelta(hours=2))
        self.assertEqual(code, 1)
        self.assertIn(f"autopilot: expired {grant['id']} at {grant['expires_at']}", out)
        self.assertEqual(self.p.grant()["state"], "expired")

    def test_record_appends_one_decision_and_refuses_a_class_or_no_grant(self):
        code, _out, err = self.record()
        self.assertEqual(code, 1)
        self.assertIn("no active grant", err)
        self.p.run_verb("on")
        code, out, err = self.record(now=NOW + timedelta(minutes=1))
        self.assertEqual(code, 0, err)
        self.assertIn("recorded decision 1", out)
        decision = self.p.events()[-1]
        self.assertEqual(decision, {
            "time": stamp(NOW + timedelta(minutes=1)), "grant": self.p.grant()["id"],
            "event": "decision", "classes": ["choice"], "question": "Which cache root?",
            "options": ["One per checkout", "One per Item"], "choice": "One per checkout",
            "reason": "the recommended option",
            "target": "workspace/docs/delivery/deliveries/dlv-001-night/delivery.md"
                      "#User Decisions D-07"})
        code, _out, err = self.record("release")
        self.assertEqual(code, 1)
        self.assertIn("class 'release' is not allowed by grant", err)
        code, _out, err = self.record(choice="Neither")
        self.assertIn("the choice must be one of the options", err)
        code, _out, err = self.record(options="")
        self.assertEqual(code, 0, err)
        self.assertEqual(len([event for event in self.p.events() if event["event"] == "decision"]), 2)

    def test_desktop_notification_backends_pass_text_as_data(self):
        import importlib.util
        source = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/owner_notification.py"
        spec = importlib.util.spec_from_file_location("notification_test", source)
        notification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notification)
        message = 'Choose "Hold"; $(do-not-run)'
        for system in ("Darwin", "Linux", "Windows"):
            with self.subTest(system=system), mock.patch.object(notification.platform, "system", return_value=system), \
                    mock.patch.object(notification.shutil, "which", return_value="notification-program"), \
                    mock.patch.object(notification.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertTrue(notification.send("Decision", message)["submitted"])
                self.assertNotIn("shell", run.call_args.kwargs)
                self.assertEqual(run.call_args.kwargs["timeout"], 15)
                payload = run.call_args.kwargs["input"]
                self.assertEqual(json.loads(payload)["message"], message) if payload else self.assertIn(message, run.call_args.args[0])

    def test_owner_wait_notifies_once_and_reports_question_age(self):
        self.p.run_verb("on")
        self.queue(now=NOW + timedelta(minutes=2))
        with mock.patch.object(autopilot, "notify_owner", return_value={"submitted": True}) as notify:
            code, output, _ = self.p.run_verb("waiting", "--runnable-task", "unit tests", now=NOW + timedelta(minutes=3))
            self.assertEqual(code, 0)
            notify.assert_not_called()
            self.assertEqual(self.p.run_verb("waiting", now=NOW + timedelta(minutes=4))[0], 0)
            self.assertEqual(self.p.run_verb("waiting", now=NOW + timedelta(minutes=5))[0], 0)
            self.assertEqual(notify.call_count, 1)
            self.assertIn("Publish the tag?", notify.call_args.args[1])
            self.assertIn("release notes", notify.call_args.args[1])
        code, output, _ = self.p.run_verb("report", "--json", now=NOW + timedelta(minutes=10))
        queued = [entry for entry in json.loads(output)["entries"] if entry["event"] == "queued"]
        self.assertEqual(queued[0]["age_seconds"], 480)
        self.assertEqual(sum(entry["event"] == "owner_notified" for entry in self.p.events()), 1)

    def test_queue_appends_one_pending_question(self):
        self.p.run_verb("on")
        code, out, err = self.queue(now=NOW + timedelta(minutes=2))
        self.assertEqual(code, 0, err)
        self.assertIn("queued question 1", out)
        entry = self.p.events()[-1]
        self.assertEqual((entry["event"], entry["classes"], entry["options"],
                          entry["recommendation"], entry["blocks"], entry["time"]),
                         ("queued", ["release"], ["Publish", "Hold"], "Hold", "release notes",
                          stamp(NOW + timedelta(minutes=2))))
        code, out, err = self.queue("choice")
        self.assertEqual(code, 0, err)
        self.assertIn("decided: Hold", out)
        self.assertEqual(self.p.events()[-1]["event"], "decision")
        self.assertEqual(self.p.events()[-1]["choice"], "Hold")

    def test_allowed_queue_decides_unless_held_or_any_effect_is_not_allowed(self):
        self.p.run_verb("on")
        base = ("queue", "--class", "choice", "--question", "Use the bounded option?",
                "--options", "Use; Wait", "--recommendation", "Use")
        code, out, err = self.p.run_verb(*base)
        self.assertEqual(code, 0, err)
        self.assertIn("decided: Use", out)
        self.assertEqual(self.p.events()[-1]["event"], "decision")
        for extra in (("--hold", "The input is ambiguous"), ("--class", "release"),
                      ("--class", "security_settings")):
            with self.subTest(extra=extra):
                code, out, err = self.p.run_verb(*base, *extra)
                self.assertEqual(code, 0, err)
                self.assertIn("queued question", out)
                self.assertEqual(self.p.events()[-1]["event"], "queued")
        self.assertEqual(self.p.events()[-3]["hold"], "The input is ambiguous")
        self.assertEqual(self.p.run_verb(*base, "--hold", " ")[0], 1)
        report = json.loads(self.p.run_verb("report", "--json")[1])
        self.assertEqual(report["counts"], {"decisions": 1, "queued": 3})

    def test_record_takes_every_class_a_decision_touches_and_refuses_one_not_allowed(self):
        self.p.run_verb("on")
        cases = ((["approval_gate", "security_settings"], "class 'security_settings' is never"),
                 (["approval_gate", "phase_start"], "class 'phase_start' is not allowed by grant"))
        for classes, fragment in cases:
            with self.subTest(classes=classes):
                argv = ["record", *(item for name in classes for item in ("--class", name)),
                        "--question", "Approve gate A, which starts DLV-003?", "--choice", "Approve",
                        "--reason", "r", "--target", "t"]
                code, _out, err = self.p.run_verb(*argv)
                self.assertEqual(code, 1)
                self.assertIn(fragment, err)
        code, _out, err = self.p.run_verb("record", "--class", "approval_gate", "--class", "merge",
                                          "--question", "Approve gate B and merge?",
                                          "--choice", "Approve", "--reason", "r", "--target", "t")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.p.events()[-1]["classes"], ["approval_gate", "merge"])
        self.assertIn("decision [approval_gate, merge] Approve gate B and merge?",
                      self.p.run_verb("report")[1])

    def test_off_revokes_the_grant_prints_the_report_and_check_is_inactive(self):
        self.p.run_verb("on")
        self.record()
        code, out, err = self.p.run_verb("off", now=NOW + timedelta(hours=1))
        self.assertEqual(code, 0, err)
        grant = self.p.grant()
        self.assertEqual((grant["state"], grant["ended_at"]),
                         ("revoked", stamp(NOW + timedelta(hours=1))))
        self.assertIn(f"Autopilot report {grant['id']} (revoked)", out)
        self.assertIn("decision [choice] Which cache root? -> One per checkout", out)
        code, out, _err = self.p.run_verb("check", now=NOW + timedelta(hours=1))
        self.assertEqual(code, 1)
        self.assertIn(f"autopilot: revoked {grant['id']}", out)

    def test_status_shows_the_remaining_time_goal_classes_and_counts(self):
        self.p.run_verb("on", "--goal", "text:night run", "--for", "9h")
        self.record()
        self.queue()
        self.queue("phase_start")
        code, out, err = self.p.run_verb("status", now=NOW + timedelta(hours=1, minutes=15))
        self.assertEqual(code, 0, err)
        self.assertIn("(7h 45m left)", out)
        self.assertIn("goal: text night run, read by no compiler; it ends through complete, off"
                      " or the cap", out)
        self.assertIn("allowed classes: choice, approval_gate, merge", out)
        self.assertIn("decisions: 1, queued: 2", out)
        self.assertIn("declared guards: arming user_only_entry, question guard instructions;"
                      " a host can skip a declared hook it has not enabled or trusted", out)
        code, out, _err = self.p.run_verb("status", "--json", now=NOW + timedelta(hours=1))
        status = json.loads(out)
        self.assertEqual((status["active"], status["remaining_minutes"], status["counts"],
                          status["goal_read"]),
                         (True, 480, {"decisions": 1, "queued": 2}, None))

    def test_report_lists_every_decision_and_queued_question_in_time_order(self):
        self.p.run_verb("on")
        self.queue(now=NOW + timedelta(minutes=1))
        self.record(now=NOW + timedelta(minutes=2))
        self.queue("phase_start", now=NOW + timedelta(minutes=3))
        code, out, err = self.p.run_verb("report", "--json", now=NOW + timedelta(minutes=4))
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual([(entry["event"], entry["classes"]) for entry in report["entries"]],
                         [("queued", ["release"]), ("decision", ["choice"]),
                          ("queued", ["phase_start"])])
        times = [entry["time"] for entry in report["entries"]]
        self.assertEqual(times, sorted(times))
        code, out, _err = self.p.run_verb("report")
        lines = [line for line in out.splitlines() if line[:1].isdigit()]
        self.assertEqual([line.split(" ", 3)[2] for line in lines], ["queued", "decision", "queued"])

    def test_the_report_follows_the_grants_the_current_one_replaced(self):
        self.p.run_verb("on", "--for", "2h")
        first = self.p.grant()["id"]
        self.record(question="Merge PR 41 at 01:00?", options="", choice="Merge",
                    now=NOW + timedelta(minutes=30))
        self.p.run_verb("on", "--for", "9h", now=NOW + timedelta(hours=1))
        second = self.p.grant()["id"]
        self.record(question="Merge PR 42 at 03:00?", options="", choice="Merge",
                    now=NOW + timedelta(hours=2))
        code, out, _err = self.p.run_verb("status", now=NOW + timedelta(hours=3))
        self.assertIn("decisions: 2, queued: 0", out)
        code, out, err = self.p.run_verb("off", now=NOW + timedelta(hours=4))
        self.assertEqual(code, 0, err)
        self.assertIn(f"replaces: {first}", out)
        self.assertLess(out.index("Merge PR 41 at 01:00?"), out.index("Merge PR 42 at 03:00?"))
        self.assertIn(f"{first} decision [choice] Merge PR 41 at 01:00?", out)
        self.assertIn(f"{second} decision [choice] Merge PR 42 at 03:00?", out)
        self.assertIn("decisions: 2, queued: 0", out)
        report = json.loads(self.p.run_verb("report", "--json")[1])
        self.assertEqual(report["chain"], [second, first])
        self.assertEqual([entry["question"] for entry in report["entries"]],
                         ["Merge PR 41 at 01:00?", "Merge PR 42 at 03:00?"])

    @integration
    def test_runtime_state_is_private_ignored_and_changes_no_tracked_file(self):
        self.assertEqual(git(self.p.root, "status", "--porcelain", "--ignored"), "")
        self.p.run_verb("on")
        self.record()
        self.queue()
        self.p.run_verb("off")
        self.assertEqual(sorted(path.name for path in self.p.state.iterdir()),
                         ["autopilot.lock", "grant.json", "ledger.jsonl"])
        self.assertEqual(git(self.p.root, "status", "--porcelain"), "")
        self.assertEqual(git(self.p.root, "status", "--porcelain", "--ignored"), "!! .agentrof/\n")
        self.assertEqual(git(self.p.root, "status", "--porcelain", "--untracked-files=no"), "")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.p.state.stat().st_mode), 0o700)
            for path in self.p.state.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    @integration
    def test_the_grant_lives_in_the_main_worktree_for_every_linked_worktree(self):
        linked = self.p.root.parent / f"{self.p.root.name}-item"
        git(self.p.root, "worktree", "add", "-q", "--detach", str(linked))
        self.addCleanup(shutil.rmtree, linked, True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = autopilot.main(["--project-root", str(linked), "on"], now=NOW)
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue((self.p.state / "grant.json").is_file())
        self.assertFalse((linked / ".agentrof").exists())


class BrokenGrantTests(unittest.TestCase):
    """A grant file nothing can read is moved aside, so the next grant can start."""

    def setUp(self) -> None:
        self.p = Project(self, armed=True)

    def broken(self, text: str) -> None:
        self.p.state.mkdir(parents=True, exist_ok=True)
        (self.p.state / "grant.json").write_text(text, encoding="utf-8")

    def assert_moved_aside(self, text: str) -> None:
        moved = sorted(self.p.state.glob("grant.json.broken-*"))
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual(moved[0].read_text(encoding="utf-8"), text)
        broken = [event for event in self.p.events() if event["event"] == "broken"]
        self.assertEqual([event["moved_to"] for event in broken], [moved[0].name])

    def test_an_unreadable_grant_is_moved_aside_and_on_starts_a_new_grant(self):
        self.broken("")
        code, out, err = self.p.run_verb("check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("autopilot: inactive", out)
        self.assertIn("moved", err)
        self.assert_moved_aside("")
        self.p.arm("on --for 2h")
        code, out, err = self.p.run_verb("on")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.p.grant()["state"], "active")

    def test_a_grant_missing_a_field_is_moved_aside_without_a_traceback(self):
        text = json.dumps({"schema_version": 1, "id": "AP-1", "state": "active",
                           "granted_at": stamp(NOW), "classes": ["choice"],
                           "armed_by": {"guard": "user_prompt_hook"}})
        for verb in ("check", "status", "off"):
            with self.subTest(verb=verb):
                self.broken(text)
                for moved in self.p.state.glob("grant.json.broken-*"):
                    moved.unlink()
                (self.p.state / "ledger.jsonl").unlink(missing_ok=True)
                code, out, err = self.p.run_verb(verb)
                self.assertNotIn("Traceback", out + err)
                self.assertFalse((self.p.state / "grant.json").exists())
                self.assert_moved_aside(text)
        self.p.arm("on --for 1h")
        self.assertEqual(self.p.run_verb("on")[0], 0)

    def test_on_reads_the_grant_state_before_it_consumes_the_arming(self):
        self.p.arm("on --for 1h")
        with mock.patch.object(autopilot, "current", side_effect=OSError("grant state is unreadable")):
            code, _out, err = self.p.run_verb("on")
        self.assertEqual(code, 1)
        self.assertIn("grant state is unreadable", err)
        self.assertTrue((self.p.state / "arming.json").exists())
        self.assertEqual(self.p.run_verb("on")[0], 0)


@contextlib.contextmanager
def system_zone(name: str):
    """Run under one system time zone, as the host's own clock would."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


class RecoveryProcessTests(unittest.TestCase):
    @integration
    def test_process_liveness_query_keeps_the_current_process_alive_on_every_os(self):
        import autopilot_recovery as recovery
        self.assertTrue(recovery.process_alive(os.getpid()))


@integration
@unittest.skipIf(os.name == "nt", "time.tzset sets the system zone only on POSIX hosts")
class TransportRecoveryTests(unittest.TestCase):
    @integration
    def test_only_registered_terminal_transport_errors_resume_and_attempts_are_bounded(self):
        import autopilot_recovery as recovery
        import types
        import uuid
        project = Project(self)
        (project.root / "tracked.txt").write_text("before the error")
        git(project.root, "add", "tracked.txt")
        git(project.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "Track the recovery input")
        directory = project.root / ".agentrof/recovery-test"
        directory.mkdir(parents=True)
        session = str(uuid.uuid4())
        transcript = directory / "host.jsonl"
        transcript.write_text(json.dumps({"type": "thread.started", "thread_id": session, "cwd": str(project.root)}) + "\n"
                              + json.dumps({"type": "error", "message": "ENOTFOUND initial"}) + "\n")
        grant = {"id": "grant"}
        unbound = directory / "unbound.jsonl"
        unbound.write_text(json.dumps({"type": "thread.started", "thread_id": session}))
        with self.assertRaisesRegex(ValueError, "no workspace binding"):
            recovery.register(directory, project.root, grant, session, unbound, "qa_engineer", 99999999)
        recovery.register(directory, project.root, grant, session, transcript, "qa_engineer", 99999999)
        recovery.CHILDREN.clear()
        launched = []
        def launch(command, **kwargs):
            launched.append(command)
            kwargs["stdout"].write(json.dumps({"type": "error", "message": "ENOTFOUND " + str(len(launched))}).encode())
            return types.SimpleNamespace(poll=lambda: 1)
        host = types.SimpleNamespace(resume_command=lambda session, prompt: ["host", session, prompt],
                                     spawn_options=lambda: {})
        with mock.patch.object(recovery, "host_adapter", return_value=host), mock.patch.object(recovery, "process_alive", return_value=True) as alive:
            self.assertEqual(recovery.heal(directory, project.root, grant, POLICY, launch), [])
            alive.return_value = False
            for expected in (1, 2, 3):
                result = recovery.heal(directory, project.root, grant, POLICY, launch)
                self.assertEqual(result[0]["attempt"], expected)
            result = recovery.heal(directory, project.root, grant, POLICY, launch)
            self.assertEqual(result[0]["state"], "needs_owner")
            self.assertEqual(recovery.heal(directory, project.root, grant, POLICY, launch), [])
        self.assertEqual(len(launched), 3)
        removed = project.root / "tracked.txt"
        removed.unlink()
        timestamp = transcript.stat().st_mtime_ns + 1_000_000
        os.utime(removed.parent, ns=(timestamp, timestamp))
        self.assertTrue(recovery.wrote_after(project.root, transcript.stat().st_mtime_ns, transcript))
        changed = project.root / "changed.txt"
        changed.write_text("later write")
        self.assertTrue(recovery.wrote_after(project.root, transcript.stat().st_mtime_ns, transcript))
        self.assertTrue(all(command[1] == session for command in launched))
        transcript.write_text(json.dumps({"type": "assistant", "message": "ENOTFOUND is quoted text"}))
        self.assertIsNone(recovery.terminal_error(transcript, POLICY["transport_error_codes"]))

    def test_recovery_is_explicitly_armed_and_expiry_refuses_it(self):
        import autopilot_recovery as recovery
        project = Project(self)
        project.assert_refused("no active grant", "heal")
        with mock.patch.object(recovery, "start", return_value=123):
            self.assertEqual(project.run_verb("on", "--for", "1h", "--recover-transport")[0], 0)
        self.assertTrue(project.grant()["transport_recovery"])
        project.assert_refused("no active grant", "heal", now=NOW + timedelta(hours=2))


class ClockTests(unittest.TestCase):
    """--until reads the system zone across a daylight-saving change, from when the user typed it."""

    def setUp(self) -> None:
        self.p = Project(self, armed=True)

    def test_a_clock_time_crosses_a_daylight_saving_change_in_the_system_zone(self):
        cases = (("Europe/Berlin", datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc),
                  "2026-03-29T05:00:00Z"),
                 ("America/New_York", datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc),
                  "2026-03-08T11:00:00Z"))
        for zone, typed, expected in cases:
            with self.subTest(zone=zone), system_zone(zone):
                self.p.arm("on --until 07:00", now=typed)
                code, _out, err = self.p.run_verb("on", now=typed + timedelta(minutes=1))
                self.assertEqual(code, 0, err)
                self.assertEqual(self.p.grant()["expires_at"], expected)

    def test_a_clock_time_that_passed_after_it_was_typed_is_refused(self):
        typed = datetime(2026, 10, 1, 6, 55, tzinfo=timezone.utc)
        with system_zone("UTC"):
            self.p.arm("on --until 07:00", now=typed)
            self.p.assert_refused("--until 07:00 is in the past", "on",
                                  now=typed + timedelta(minutes=6))
        self.assertFalse((self.p.state / "grant.json").exists())

    def test_a_huge_duration_is_refused_without_a_traceback(self):
        self.p.arm("on --for 99999999999d")
        self.p.assert_refused(f"is above the {POLICY['max_duration_hours']} h maximum", "on")


class FailClosedTests(unittest.TestCase):
    """A package that cannot show its arming hook, or a grant it could not have armed, grants nothing."""

    def setUp(self) -> None:
        self.p = Project(self, armed=True)

    def forge(self, **fields) -> dict:
        grant = {"schema_version": 1, "id": "AP-20261001T210000Z-f0f0", "host": "fixture",
                 "state": "active", "granted_at": stamp(NOW),
                 "expires_at": stamp(NOW + timedelta(hours=2)), "goal": None,
                 "classes": ["choice", "release"],
                 "armed_by": {"guard": "user_prompt_hook", "arguments": "on --for 2h",
                              "session_id": "session-1"},
                 "replaces": None, **fields}
        self.p.state.mkdir(parents=True, exist_ok=True)
        (self.p.state / "grant.json").write_text(json.dumps(grant), encoding="utf-8")
        return grant

    def test_a_built_package_whose_arming_hook_cannot_be_read_refuses_on(self):
        with tempfile.TemporaryDirectory() as raw:
            manifest = Path(raw) / ".agent-marketplace-package.json"
            manifest.write_text("{}\n", encoding="utf-8")
            cases = (('{"hooks": {"UserPromptSubmit": [{"hooks": [', "cannot be read"),
                     (json.dumps({"hooks": {"SessionStart": []}}), "declares no arming hook"))
            with mock.patch.object(autopilot, "MANIFEST", manifest):
                for hooks, fragment in cases:
                    with self.subTest(fragment=fragment):
                        autopilot.HOOKS.write_text(hooks, encoding="utf-8")
                        self.p.assert_refused(fragment, "on", "--for", "72h", "--allow", "release")
                        self.assertFalse((self.p.root / ".agentrof").exists())
                autopilot.HOOKS.unlink()
                self.p.assert_refused("hooks/hooks.json is missing", "on", "--for", "72h")

    def test_a_grant_this_package_could_not_have_armed_is_inactive_everywhere(self):
        maximum = POLICY["max_duration_hours"]
        cases = (
            ({"armed_by": {"guard": "user_only_entry", "arguments": "on --for 72h"}},
             "armed by user_only_entry, but this package arms through user_prompt_hook"),
            ({"expires_at": stamp(NOW + timedelta(hours=maximum + 1))},
             f"longer than the {maximum} h maximum"),
            ({"classes": ["choice", "credentials"]}, "holds never class 'credentials'"),
        )
        question = {"session_id": "session-1", "cwd": str(self.p.root),
                    "hook_event_name": "PreToolUse", "tool_name": "ask", "tool_input": {}}
        for fields, fragment in cases:
            with self.subTest(fragment=fragment):
                grant = self.forge(**fields)
                code, out, _err = self.p.run_verb("check")
                self.assertEqual(code, 1)
                self.assertIn(f"autopilot: inactive {grant['id']}: ", out)
                self.assertIn(fragment, out)
                code, out, _err = self.p.run_verb("status")
                self.assertIn(fragment, out)
                status = json.loads(self.p.run_verb("status", "--json")[1])
                self.assertEqual((status["active"], fragment in status["inactive_reason"]),
                                 (False, True))
                self.assertEqual(self.p.hook("pre-question", question), (0, ""))
                code, _out, err = self.p.run_verb("record", "--class", "choice", "--question", "q",
                                                  "--choice", "a", "--reason", "r", "--target", "t")
                self.assertEqual(code, 1)
                self.assertIn(fragment, err)


class SessionBindingTests(unittest.TestCase):
    """A grant governs only the session and host whose user typed it."""

    def setUp(self) -> None:
        self.p = Project(self, armed=True)
        self.p.arm("on --for 2h")
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-1"}):
            self.assertEqual(self.p.run_verb("on")[0], 0)

    def question(self, session: str) -> dict:
        return {"session_id": session, "cwd": str(self.p.root), "hook_event_name": "PreToolUse",
                "tool_name": "ask", "tool_input": {}}

    def record(self) -> tuple[int, str, str]:
        return self.p.run_verb("record", "--class", "choice", "--question", "q", "--choice", "a",
                               "--reason", "r", "--target", "t")

    def test_the_question_hook_denies_only_the_arming_session(self):
        self.assertEqual(self.p.hook("pre-question", self.question("session-2")), (0, ""))
        code, out = self.p.hook("pre-question", self.question("session-1"))
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("armed in fixture session session-1", reason)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            autopilot.main(["hook", "pre-question", "--host", "other"], now=NOW,
                           stdin=io.StringIO(json.dumps(self.question("session-1"))))
        self.assertEqual(out.getvalue(), "")

    def test_verbs_of_another_session_or_host_are_refused_and_named(self):
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-2"}):
            code, _out, err = self.record()
            self.assertEqual(code, 1)
            self.assertIn("bound to fixture session session-1; this session is session-2", err)
            code, out, _err = self.p.run_verb("check")
            self.assertEqual(code, 1)
            self.assertIn("bound to fixture session session-1", out)
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-1"}):
            self.assertEqual(self.record()[0], 0)
            self.assertEqual(self.p.run_verb("check")[0], 0)
        os.environ.pop(SESSION_ENV, None)
        self.assertEqual(self.record()[0], 0)
        self.assertIn("bound to: fixture session session-1", self.p.run_verb("status")[1])
        other = json.loads(json.dumps(ARMED_HOOKS).replace("--host fixture", "--host other"))
        autopilot.HOOKS.write_text(json.dumps(other), encoding="utf-8")
        code, _out, err = self.record()
        self.assertEqual(code, 1)
        self.assertIn("bound to a fixture session; this package runs other", err)

    def test_status_names_the_bound_session_once(self):
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-2"}):
            out = self.p.run_verb("status")[1]
        self.assertIn("bound to: fixture session session-1; this session is session-2;"
                      " this session asks as usual\n", out)
        self.assertEqual(out.count("session-1"), 1)

    def test_on_refuses_an_arming_typed_in_another_session_and_keeps_it(self):
        self.p.arm("on --for 1h")
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-2"}):
            self.p.assert_refused("typed in fixture session session-1", "on")
        self.assertTrue((self.p.state / "arming.json").exists())
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-1"}):
            self.assertEqual(self.p.run_verb("on")[0], 0)

    def test_any_session_may_end_the_grant(self):
        with mock.patch.dict(os.environ, {SESSION_ENV: "session-2"}):
            code, out, err = self.p.run_verb("complete", "--evidence", "back early")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.p.grant()["state"], "completed")

    def test_on_checks_the_binding_of_the_arming_it_consumes(self):
        self.p.arm("on --for 1h")
        record = json.loads((self.p.state / "arming.json").read_text(encoding="utf-8"))
        swapped = dict(record, session_id="session-3")
        reads = iter([record, swapped])
        real = autopilot.fresh_arming
        with mock.patch.object(autopilot, "fresh_arming",
                               side_effect=lambda *args: real(*args) and next(reads)), \
                mock.patch.dict(os.environ, {SESSION_ENV: "session-1"}):
            self.p.assert_refused("typed in fixture session session-3", "on")
        self.assertTrue((self.p.state / "arming.json").exists())

    def test_a_prompt_without_a_session_never_arms(self):
        (self.p.state / "arming.json").unlink(missing_ok=True)
        self.assertEqual(self.p.hook("user-prompt", expansion(self.p.root, "on", session_id="")),
                         (0, ""))
        self.assertFalse((self.p.state / "arming.json").exists())


class GoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project(self)

    def check(self, now: datetime) -> tuple[int, str]:
        code, out, _err = self.p.run_verb("check", now=now)
        return code, out

    def test_complete_ends_any_grant_and_records_evidence_and_compiler_agreement(self):
        cases = (("text:finish the migration", None), (None, None))
        for goal, agreed in cases:
            with self.subTest(goal=goal):
                self.p.run_verb("on", *(["--goal", goal] if goal else []))
                code, out, err = self.p.run_verb("complete", "--evidence", "PR 41 merged")
                self.assertEqual(code, 0, err)
                completion = self.p.grant()["completion"]
                self.assertEqual(self.p.grant()["state"], "completed")
                self.assertEqual((completion["by"], completion["evidence"],
                                  completion["compiler_agreed"]), ("complete", "PR 41 merged", agreed))
                self.assertIn("completed by complete: PR 41 merged", out)
        self.p.assert_refused("no active grant", "complete", "--evidence", "again")

    def test_a_text_goal_ends_only_through_complete_off_or_its_cap(self):
        self.p.run_verb("on", "--goal", "text:finish the migration", "--for", "5h")
        self.assertEqual(self.p.grant()["goal"]["end"], "none")
        for minutes in (1, 299):
            self.assertEqual(self.check(NOW + timedelta(minutes=minutes))[0], 0)
        self.assertEqual(self.check(NOW + timedelta(hours=5))[0], 1)
        self.assertEqual(self.p.grant()["state"], "expired")
        self.p.run_verb("on", "--goal", "text:finish the migration")
        self.assertEqual(self.check(NOW + timedelta(hours=47))[0], 0)
        self.p.run_verb("off", now=NOW + timedelta(hours=47))
        self.assertEqual(self.p.grant()["state"], "revoked")


class DistributionTests(unittest.TestCase):
    """Both hosts' generated packages: wrappers, canonical content and real hook wiring."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.temporary.name) / "dist"
        build_distributions.build(ROOT, cls.dist)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def package(self, host: str) -> Path:
        return self.dist / host / "software-engineering-team"

    def hook_argv(self, host: str, verb: str) -> list[str]:
        """The hook command the host runs, with the package root it substitutes."""
        text = (self.package(host) / "hooks" / "hooks.json").read_text(encoding="utf-8")
        commands = [handler["command"] for groups in json.loads(text)["hooks"].values()
                    for group in groups for handler in group["hooks"]
                    if f"autopilot.py hook {verb}" in handler["command"]]
        self.assertEqual(len(commands), 1, commands)
        command = commands[0].replace(HOST_HOOKS[host][3], shlex.quote(str(self.package(host))))
        argv = shlex.split(command)
        self.assertEqual(argv[0], "python3")
        return [sys.executable, *argv[1:]]

    @staticmethod
    def clean_env() -> dict:
        return {key: value for key, value in os.environ.items()
                if key not in HOST_SESSION_VARIABLES}

    def run_hook(self, host: str, verb: str, payload, cwd: Path,
                 env: dict | None = None) -> subprocess.CompletedProcess:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(self.hook_argv(host, verb), input=text, cwd=cwd,
                              env=env or self.clean_env(), capture_output=True, text=True,
                              check=False, timeout=60)

    def run_verb(self, host: str, root: Path, *argv: str) -> subprocess.CompletedProcess:
        script = self.package(host) / "skill-content/autopilot/scripts/autopilot.py"
        return subprocess.run([sys.executable, str(script), *argv], cwd=root, env=self.clean_env(),
                              capture_output=True, text=True, check=False, timeout=60)

    @contextlib.contextmanager
    def project(self):
        with temporary_directory() as raw:
            yield make_project(Path(raw))

    def test_the_entry_is_user_invoked_only_on_both_hosts(self):
        claude = (self.package("claude") / "skills/autopilot/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("\ndisable-model-invocation: true\n", claude.split("---", 2)[1])
        codex = self.package("codex") / "skills/autopilot/agents/openai.yaml"
        self.assertIn("allow_implicit_invocation: false", codex.read_text(encoding="utf-8"))
        header = (ENTRY / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        self.assertIn("\nname: autopilot\n", header)
        self.assertIn("\nexposure: entry\n", header)

    def test_both_hosts_declare_the_arming_and_question_hooks(self):
        for host, (event, matcher, question, _root) in HOST_HOOKS.items():
            for source in (ROOT / "platforms" / host / "software-engineering-team/overlay/hooks",
                           self.package(host) / "hooks"):
                with self.subTest(host=host, source=source):
                    hooks = json.loads((source / "hooks.json").read_text(encoding="utf-8"))["hooks"]
                    arming = [group for group in hooks[event]
                              if "hook user-prompt" in group["hooks"][0]["command"]]
                    self.assertEqual([group.get("matcher") for group in arming], [matcher])
                    self.assertIn(f"--host {host} --entry {NAME}",
                                  arming[0]["hooks"][0]["command"])
                    guards = [group for group in hooks["PreToolUse"]
                              if "hook pre-question" in group["hooks"][0]["command"]]
                    self.assertEqual([group["matcher"] for group in guards], [question])

    @integration
    def test_an_agent_side_on_without_arming_is_refused_on_each_arming_host(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root:
                result = self.run_verb(host, root, "on", "--for", "9h")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("run `on` without options", result.stderr)
                result = self.run_verb(host, root, "on")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("no fresh arming record from your own typed entry command",
                              result.stderr)
                self.assertFalse((root / ".agentrof").exists())
                status = json.loads(self.run_verb(host, root, "status", "--json").stdout)
                self.assertEqual((status["arming"], status["question_guard"]),
                                 ("user_prompt_hook", "hook"))

    @integration
    def test_a_copied_package_mints_no_grant_the_installed_package_honours(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root, \
                    tempfile.TemporaryDirectory() as raw:
                copy = Path(raw) / "copy"
                shutil.copytree(self.package(host), copy, ignore=shutil.ignore_patterns("hooks"))
                script = copy / "skill-content/autopilot/scripts/autopilot.py"
                agent_on = [sys.executable, str(script), "on", "--for", "72h", "--allow", "release"]
                result = subprocess.run(agent_on, cwd=root, env=self.clean_env(),
                                        capture_output=True, text=True, check=False, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("hooks/hooks.json is missing", result.stderr)
                self.assertFalse((root / RUNTIME / "grant.json").exists())
                (copy / ".agent-marketplace-package.json").unlink()
                result = subprocess.run(agent_on, cwd=root, env=self.clean_env(),
                                        capture_output=True, text=True, check=False, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.run_verb(host, root, "check")
                self.assertEqual(result.returncode, 1)
                self.assertIn("armed by user_only_entry, but this package arms through"
                              " user_prompt_hook", result.stdout)
                question = {"session_id": "s", "cwd": str(root), "hook_event_name": "PreToolUse",
                            "tool_name": HOST_HOOKS[host][2], "tool_input": {}}
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual((result.returncode, result.stdout), (0, ""))

    @integration
    def test_a_claude_grant_never_governs_a_codex_session(self):
        with self.project() as root:
            self.run_hook("claude", "user-prompt", expansion(root, "on --for 2h",
                                                             session_id="claude-A"), root)
            env = {**self.clean_env(), "CLAUDE_CODE_SESSION_ID": "claude-A"}
            script = self.package("claude") / "skill-content/autopilot/scripts/autopilot.py"
            result = subprocess.run([sys.executable, str(script), "on"], cwd=root, env=env,
                                    capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            asks = {host: {"session_id": session, "cwd": str(root), "hook_event_name": "PreToolUse",
                           "tool_name": HOST_HOOKS[host][2], "tool_input": {}}
                    for host, session in (("claude", "claude-A"), ("codex", "codex-B"))}
            self.assertEqual(self.run_hook("codex", "pre-question", asks["codex"], root).stdout, "")
            other = dict(asks["claude"], session_id="claude-C")
            self.assertEqual(self.run_hook("claude", "pre-question", other, root).stdout, "")
            self.assertIn("deny", self.run_hook("claude", "pre-question", asks["claude"], root).stdout)
            codex = self.package("codex") / "skill-content/autopilot/scripts/autopilot.py"
            env = {**self.clean_env(), "CODEX_THREAD_ID": "codex-B"}
            for argv in (["record", "--class", "merge", "--question", "Merge PR 42?",
                          "--choice", "Merge", "--reason", "r", "--target", "t"], ["check"]):
                result = subprocess.run([sys.executable, str(codex), *argv], cwd=root, env=env,
                                        capture_output=True, text=True, check=False, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("bound to a claude session", result.stdout + result.stderr)

    @integration
    def test_the_codex_default_prompt_of_the_entry_arms_a_grant(self):
        def default_prompt(entry: str) -> str:
            metadata = self.package("codex") / "skills" / entry / "agents/openai.yaml"
            line = next(line for line in metadata.read_text(encoding="utf-8").splitlines()
                        if line.strip().startswith("default_prompt:"))
            return line.split(":", 1)[1].strip().strip('"')

        self.assertEqual(default_prompt("autopilot"), f"${NAME} on")
        self.assertEqual(default_prompt("deliver"),
                         "Use $software-engineering-team:deliver to start this workflow.")
        with self.project() as root:
            payload = prompt(root, default_prompt("autopilot") + " --for 9h")
            result = self.run_hook("codex", "user-prompt", payload, root)
            self.assertEqual((result.returncode, result.stdout), (0, ""))
            arming = json.loads((root / RUNTIME / "arming.json").read_text(encoding="utf-8"))
            self.assertEqual(arming["arguments"], "on --for 9h")

    @integration
    def test_the_typed_entry_command_arms_a_grant_on_each_host(self):
        typed = {"claude": lambda root: expansion(root, "on --for 2h --deny merge"),
                 "codex": lambda root: prompt(root, f"${NAME} on --for 2h --deny merge")}
        for host, payload in typed.items():
            with self.subTest(host=host), self.project() as root:
                result = self.run_hook(host, "user-prompt", payload(root), root)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                result = self.run_verb(host, root, "on")
                self.assertEqual(result.returncode, 0, result.stderr)
                grant = json.loads((root / RUNTIME / "grant.json").read_text(encoding="utf-8"))
                self.assertEqual((grant["host"], grant["armed_by"]["guard"],
                                  grant["armed_by"]["arguments"]),
                                 (host, "user_prompt_hook", "on --for 2h --deny merge"))
                self.assertNotIn("merge", grant["classes"])

    @integration
    def test_every_other_prompt_passes_without_output(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root:
                for payload in (prompt(root, "Plan the next Delivery."), expansion(root, "status"),
                                expansion(root, "on", command_name="software-engineering-team:deliver"),
                                prompt(root, f"${NAME} on", agent_id="thread-2"),
                                "not json", "", {"cwd": str(root)}):
                    result = self.run_hook(host, "user-prompt", payload, root)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertFalse((root / ".agentrof").exists())

    @integration
    def test_the_question_hook_denies_only_while_a_grant_is_active(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root:
                session = "session-1" if host == "claude" else "session-2"
                question = {"session_id": session, "cwd": str(root),
                            "hook_event_name": "PreToolUse", "tool_name": HOST_HOOKS[host][2],
                            "tool_input": {"questions": []}}
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.run_hook(host, "user-prompt", expansion(root, "on --for 1h")
                              if host == "claude" else prompt(root, f"${NAME} on --for 1h"), root)
                self.assertEqual(self.run_verb(host, root, "on").returncode, 0)
                grant = json.loads((root / RUNTIME / "grant.json").read_text(encoding="utf-8"))
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual(result.returncode, 0)
                output = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual((output["hookEventName"], output["permissionDecision"]),
                                 ("PreToolUse", "deny"))
                reason = output["permissionDecisionReason"]
                for fragment in (grant["id"], grant["expires_at"],
                                 *(f"{entry['id']} ({entry['description']})"
                                   for entry in POLICY["classes"]
                                   if entry["id"] in grant["classes"]),
                                 "by every effect of its recommended option",
                                 "--class once per class it touches", "doubt queues it",
                                 "take the recommended option", " check`", " record`",
                                 "governing document", " queue`", "Do not ask the user",
                                 "if it exits 1, ask the user as usual",
                                 "every remaining task waits on a queued question"):
                    self.assertIn(fragment, reason)
                self.assertLess(reason.index(" record`"), reason.index("then apply it"))
                self.assertEqual(self.run_verb(host, root, "off").returncode, 0)
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                path = root / RUNTIME / "grant.json"
                expired = dict(grant, state="active",
                               expires_at=stamp(datetime.now(timezone.utc) - timedelta(minutes=1)))
                path.write_text(json.dumps(expired), encoding="utf-8")
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                path.write_text("{not json", encoding="utf-8")
                for payload in (question, "garbage", ""):
                    result = self.run_hook(host, "pre-question", payload, root)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    @integration
    def test_hooks_outside_a_git_checkout_allow_and_stay_silent(self):
        with tempfile.TemporaryDirectory() as raw:
            outside = Path(raw).resolve()
            # Git must not find a checkout above the temporary directory either.
            env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(outside.parent)}
            for host in HOST_HOOKS:
                for verb, payload in (("pre-question", {"cwd": str(outside)}),
                                      ("user-prompt", expansion(outside, "on"))):
                    result = self.run_hook(host, verb, payload, outside, env)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class ContractTests(unittest.TestCase):
    def test_host_contracts_point_at_the_skill_and_name_their_hooks(self):
        spellings = {"claude": (f"/{NAME}", "UserPromptExpansion", "AskUserQuestion"),
                     "codex": (f"${NAME}", "UserPromptSubmit", "request_user_input")}
        for host, tokens in spellings.items():
            with self.subTest(host=host):
                text = " ".join((ROOT / "platforms" / host / "software-engineering-team"
                                 / "host-contract.md").read_text(encoding="utf-8").split())
                section = text.split("## Autopilot", 1)[1]
                for fragment in (*tokens, "its skill holds the procedure"):
                    self.assertIn(fragment, section)

    def test_the_skill_holds_the_procedure(self):
        skill = " ".join((ENTRY / "SKILL.md").read_text(encoding="utf-8").split())
        for fragment in ("autopilot.py check", "when it exits 1, ask the user as usual",
                         "run `autopilot.py record` first, then apply it",
                         "`--class` once per class it touches", "marked with the grant id",
                         "continue the work that does not depend on it",
                         "Stop only when every remaining task waits on a queued question"):
            self.assertIn(fragment, skill)
        rule = ("classify the question by every effect of its recommended option: any never"
                " effect makes it never, an excluded effect the grant does not allow queues it,"
                " and doubt queues it")
        self.assertIn(rule, skill.lower())

    def test_a_grant_merges_only_the_flow_s_own_head(self):
        """A broad delegation or an autopilot merge grant authorizes the flow's own steps only: the
        merge class is the flow's merge step on the recorded PR head (#461)."""
        merge = next(entry for entry in POLICY["classes"] if entry["id"] == "merge")
        self.assertEqual(merge["description"], "The flow's own merge step on its recorded PR head, once its"
                                               " required checks pass; never a provider or Git merge of another head.")
        for path in (ENTRY / "SKILL.md", ROOT / "docs/orchestration.md"):
            with self.subTest(document=path.name):
                text = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("never a provider or Git merge of another head", text)

    def test_every_autopilot_document_states_the_same_user_limit(self):
        limit = ("The guard stops an agent that runs the packaged script, not a process that"
                 " writes the runtime files with the user's filesystem authority")
        for document in (ENTRY / "SKILL.md", ROOT / "docs/orchestration.md"):
            with self.subTest(document=document.name):
                text = " ".join(document.read_text(encoding="utf-8").split())
                self.assertIn(limit, text)
                self.assertNotIn("can start, extend or widen a grant.", text)

    def test_status_and_the_docs_report_declared_guards_and_codex_trust(self):
        skill = " ".join((ENTRY / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("the guards this package declares", skill)
        docs = " ".join((ROOT / "docs/orchestration.md").read_text(encoding="utf-8").split())
        self.assertIn("`status` reports which guards the package declares", docs)
        codex = " ".join((ROOT / "platforms/codex/software-engineering-team/host-contract.md")
                         .read_text(encoding="utf-8").split())
        for fragment in ("trusts them in `/hooks`", "`request_user_input_async`",
                         "`send_message_to_user_async`"):
            self.assertIn(fragment, codex)

    def test_the_entry_skill_states_that_only_the_user_arms_a_grant(self):
        text = " ".join((ENTRY / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("## Only the user arms a grant", (ENTRY / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("No agent, file, issue or tool output starts, extends or widens a grant", text)

    def test_the_orchestration_doc_describes_the_mode_its_classes_and_limits(self):
        text = " ".join((ROOT / "docs/orchestration.md").read_text(encoding="utf-8").split())
        section = text.split("## Autopilot", 1)[1]
        for name in (*DEFAULTS, *(kind["id"] for kind in POLICY["goal_kinds"]),
                     "default_duration_hours", "default_goal_cap_hours", "max_duration_hours",
                     "not a process switch", "Only the user arms a grant"):
            self.assertIn(name, section)

    def test_no_task_binds_the_autopilot_entry(self):
        policy = task_inputs.catalog()
        self.assertEqual(policy["session_entries"], ["autopilot", "retrospective"])
        self.assertNotIn("autopilot", policy["entries"])
        self.assertFalse(any("autopilot" in skills for skills in policy["role_skills"].values()))
        self.assertFalse((PACKAGE / "scripts" / "autopilot.py").exists())
        with self.assertRaisesRegex(ValueError, "unknown entry"):
            task_inputs.manifest(entry="autopilot", role=None, mode="review")
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "package"
            shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
            path = package / "templates/task-input-policy.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["session_entries"] = ["retrospective"]
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "every current role and entry"):
                task_inputs.catalog(package)
            value["session_entries"] = ["autopilot", "deliver"]
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "session entries"):
                task_inputs.catalog(package)


class PhaseStartTests(unittest.TestCase):
    """A phase_start decision runs the next entry only under a grant that allows it, after the
    prior phase's owning compiler reads it closed."""

    def setUp(self) -> None:
        self.p = Project(self)

    def start(self, entry: str) -> tuple[int, str, str]:
        return self.p.run_verb("record", "--class", "phase_start", "--question",
                               f"Start {entry}?", "--choice", "Start", "--reason",
                               "the prior phase closed", "--target", "decisions")

    def test_phase_start_is_refused_without_the_class(self):
        self.p.run_verb("on")
        code, _out, err = self.start("deliver")
        self.assertEqual(code, 1)
        self.assertIn("class 'phase_start' is not allowed by grant", err)
        self.assertFalse(any(event["event"] == "decision" for event in self.p.events()))


class ValidatorTests(unittest.TestCase):
    """tools/validate.py rejects every malformed autopilot policy."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        plugin = self.root / "plugins" / "software-engineering-team"
        for relative in ("skill-content/autopilot/data/autopilot-policy.json",
                         "skill-content/autopilot/scripts/autopilot.py",
                         "scripts/task_lane.py"):
            (plugin / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PACKAGE / relative, plugin / relative)
        self.path = plugin / "skill-content/autopilot/data/autopilot-policy.json"

    def messages(self, mutate=None) -> list[str]:
        value = json.loads(json.dumps(POLICY))
        if mutate is not None:
            mutate(value)
        self.path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        findings: list = []
        validate.check_autopilot_policy(validate.build_tree(self.root), findings)
        return [finding.message for finding in findings if finding.check == "autopilot_policy"]

    def test_malformed_policies_are_rejected(self):
        def duplicate(key):
            return lambda value: value[key].append(dict(value[key][0]))

        maximum = POLICY["max_duration_hours"]

        def only_allowed(value):
            for entry in value["classes"]:
                entry["default"] = "allowed" if entry["default"] == "never" else entry["default"]

        cases = (
            (duplicate("classes"), "duplicate class 'choice'"),
            (duplicate("goal_kinds"), "duplicate goal kind 'text'"),
            (lambda value: value["classes"][0].update(default="sometimes"),
             "class 'choice' has unknown default 'sometimes'"),
            (only_allowed, "the never set is empty"),
            (lambda value: value.update(default_duration_hours=maximum + 1),
             f"default_duration_hours {maximum + 1} is above max_duration_hours {maximum}"),
            (lambda value: value.update(default_goal_cap_hours=maximum + 1),
             f"default_goal_cap_hours {maximum + 1} is above max_duration_hours {maximum}"),
            (lambda value: value["goal_kinds"][0].update(
                end={"compiler": "scripts/ghost.py", "terminal_statuses": ["done"]}),
             "names compiler 'scripts/ghost.py', which is no package script autopilot.py reads"),
            (lambda value: value.update(arming_ttl_minutes=0),
             "arming_ttl_minutes must be a positive whole number"),
            (lambda value: value["classes"][1].pop("description"),
             "every class holds exactly an id, a description and a default"),
            (lambda value: value.update(schema_version=2), "with schema_version 1"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                messages = self.messages(mutate)
                self.assertTrue(any(fragment in message for message in messages), messages)
        self.path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
        findings: list = []
        validate.check_autopilot_policy(validate.build_tree(self.root), findings)
        self.assertIn("not valid unique-key JSON", findings[0].message)

    def test_a_skill_script_may_import_its_package_scripts_and_only_them(self):
        script = self.root / "plugins/software-engineering-team/skill-content/autopilot/scripts"
        for module, rejected in (("task_lane", False), ("requests", True)):
            with self.subTest(module=module):
                (script / "probe.py").write_text(f"import {module}\n", encoding="utf-8")
                findings: list = []
                validate.check_stdlib_only(validate.build_tree(self.root), findings)
                flagged = [finding for finding in findings if finding.path.endswith("probe.py")]
                self.assertEqual(bool(flagged), rejected, flagged)


@integration
class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "platforms/shared/software-engineering-team/overlay/skill-content/autopilot/scripts"))
        import native_recovery
        import uuid
        self.native = native_recovery
        original = native_recovery.autopilot
        native_recovery.autopilot = autopilot
        self.addCleanup(setattr, native_recovery, "autopilot", original)
        self.p = Project(self)
        self.now = datetime.now(timezone.utc)
        self.owner, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(self.p.run_verb("on", "--for", "1h", "--host", "codex", now=self.now)[0], 0)
        grant = self.p.grant()
        grant["transport_recovery"] = True
        grant["armed_by"]["session_id"] = self.owner
        autopilot.write_private(self.p.state / "grant.json", grant)
        self.grant = grant
        environment = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.p.state)})
        environment.start()
        self.addCleanup(environment.stop)

    def snapshot(self, session=None, *, status="failed", message="ENOTFOUND", thread_status="idle"):
        import time
        return {"schemaVersion": 1, "thread": {"id": session or self.child,
            "cwd": str(self.p.root), "status": {"type": thread_status}},
            "turns": [{"id": "turn-failure", "status": status, "error": {"message": message},
                       "completedAt": time.time()}]}

    @integration
    def test_codex_native_plan_preserves_identity_and_refuses_other_work(self):
        self.native.remember({"session_id": self.owner, "agent_id": self.child,
                              "cwd": str(self.p.root), "agent_type": "qa"}, "codex", self.now)
        plan = self.native.plan(self.p.root, None, self.now, self.grant["id"])
        self.assertEqual(plan["inspect"], sorted([self.owner, self.child]))
        self.assertEqual(plan["enrollment"]["arguments"]["targetThreadId"], self.owner)
        for change in ({"status": "completed"}, {"status": "interrupted"},
                       {"message": "invalid API key"}, {"thread_status": "active"}):
            self.assertEqual(self.native.plan(self.p.root, [self.snapshot(**change)], self.now,
                                                   self.grant["id"])["actions"], [])
        result = self.native.plan(self.p.root, [self.snapshot()], self.now, self.grant["id"])
        self.assertEqual([row["thread_id"] for row in result["actions"]], [self.child])
        message = result["actions"][0]["message"]
        event = {"cwd": str(self.p.root), "session_id": self.owner,
                 "tool_input": {"id": self.child, "message": message, "interrupt": False}}
        changed = {**event, "tool_input": {**event["tool_input"], "message": message + " changed"}}
        self.assertIn("message changed", self.native.dispatch_guard(changed, self.now))
        self.assertIn("expired", self.native.dispatch_guard(event, self.now + timedelta(minutes=6)))
        self.assertIn("another coordinator", self.native.dispatch_guard({**event, "session_id": self.child}, self.now))
        with mock.patch.object(autopilot, "load_native_recovery", side_effect=ValueError("fixture")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                autopilot.run_hook(["native-dispatch"], io.StringIO(json.dumps(event)), self.now)
            self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.native.dispatch_guard({**event, "tool_input": {
            **event["tool_input"], "content": message[:50] + "…"}}, self.now), "")
        self.assertIn("consumed", self.native.dispatch_guard(event, self.now))
        self.assertEqual(self.native.plan(self.p.root, [self.snapshot()], self.now,
                                               self.grant["id"])["actions"], [])
        wrong = self.snapshot(); wrong["thread"]["cwd"] = str(self.p.root.parent)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.native.plan(self.p.root, [wrong], self.now, self.grant["id"])
        self.assertFalse(self.native.plan(self.p.root, None, self.now, "replaced")["active"])

    @integration
    def test_native_enrollment_is_bound_once_and_inactive_hooks_do_nothing(self):
        with mock.patch.object(autopilot, "load_native_recovery", return_value=self.native):
            code, _out, error = self.p.run_verb("native-bind", "--grant", self.grant["id"],
                                              "--automation-id", "fixture-heartbeat", now=self.now)
            self.assertEqual(code, 0, error)
            code, _out, _error = self.p.run_verb("native-bind", "--grant", self.grant["id"],
                                               "--automation-id", "different-heartbeat", now=self.now)
            self.assertEqual(code, 1)
        grant = self.p.grant(); grant["state"] = "revoked"
        autopilot.write_private(self.p.state / "grant.json", grant)
        with self.assertRaises(autopilot.Refusal):
            self.native.remember({"session_id": self.owner, "cwd": str(self.p.root)}, "codex", self.now)

    @integration
    def test_claude_native_wake_is_bounded_and_ignores_unmarked_prose(self):
        grant = self.p.grant(); grant["host"] = "claude"
        autopilot.write_private(self.p.state / "grant.json", grant)
        path = self.p.state / "native-transcript.jsonl"
        event = {"type": "assistant", "is_api_error_message": True,
                 "message": {"content": [{"type": "text", "text": "API Error: 503 temporary service error"}]}}
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        payload = {"session_id": self.owner, "cwd": str(self.p.root), "transcript_path": str(path)}
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(self.native.watch(payload, clock=lambda: self.now, sleep=lambda _: None), 2)
        self.assertIn("same conversation", error.getvalue())
        self.assertEqual(self.native.watch(payload, clock=lambda: self.now, sleep=lambda _: None), 0)
        for index in range(1, 4):
            event["message"]["content"][0]["text"] += str(index)
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(autopilot, "notify_owner"):
                self.assertEqual(self.native.watch(payload, clock=lambda: self.now, sleep=lambda _: None),
                                 2 if index < 3 else 0)
        event["is_api_error_message"] = False
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        self.assertIsNone(self.native.claude_failure(path, POLICY))
        event["is_api_error_message"] = True
        path.write_text(json.dumps(event) + "\n" + json.dumps({"type": "user", "isMeta": True,
                        "message": {"content": "[Request interrupted by user]"}}), encoding="utf-8")
        self.assertIsNone(self.native.claude_failure(path, POLICY))
        grant["state"] = "revoked"
        autopilot.write_private(self.p.state / "grant.json", grant)
        self.assertEqual(self.native.watch(payload, clock=lambda: self.now, sleep=lambda _: None), 0)


if __name__ == "__main__":
    unittest.main()
