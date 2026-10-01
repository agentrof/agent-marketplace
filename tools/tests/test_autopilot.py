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
import unittest
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
import delivery_compile  # noqa: E402
import requirement_compile  # noqa: E402
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
ARMED_HOOKS = {"hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command":
                                     f"python3 autopilot.py hook user-prompt --entry {NAME}"}]}],
    "PreToolUse": [{"matcher": "ask", "hooks": [{"type": "command", "command":
                                                 "python3 autopilot.py hook pre-question"}]}],
}}
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
        self.root = make_project(Path(stack.enter_context(temporary_directory())))
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

    def delivery(self, status: str, identifier: str = "DLV-002") -> None:
        path = (self.root / "workspace/docs/delivery/deliveries"
                / f"{identifier.lower()}-night/delivery.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: delivery\nid: {identifier}\ntitle: Night run\n"
                        f"status: {status}\n---\n\n# Night run\n", encoding="utf-8")

    def requirement(self, status: str, identifier: str = "REQ-005") -> None:
        path = self.root / f"workspace/docs/requirements/req-{identifier[4:]}-night.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: requirement\nid: {identifier}\ntitle: Night work\n"
                        f"status: {status}\n---\n\n# Night work\n", encoding="utf-8")

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
        self.p.assert_refused("scripts/delivery_compile.py finds no Delivery DLV-099",
                              "on", "--goal", "delivery:DLV-099")
        self.p.assert_refused("scripts/requirement_compile.py finds no Requirement REQ-404",
                              "on", "--goal", "requirement:REQ-404")
        self.p.assert_refused("unknown goal kind 'sprint'; declared kinds: delivery, requirement,"
                              " text", "on", "--goal", "sprint:9")
        self.p.assert_refused("names no target", "on", "--goal", "text:")
        self.p.delivery("cancelled")
        self.p.assert_refused("goal delivery:DLV-002 is already reached", "on", "--goal",
                              "delivery:DLV-002")
        self.assertFalse((self.p.root / ".agentrof").exists())

    def test_a_goal_grant_ends_at_its_cap_or_at_the_earlier_given_time(self):
        self.p.delivery("active")
        code, _out, err = self.p.run_verb("on", "--goal", "delivery:dlv-002")
        self.assertEqual(code, 0, err)
        grant = self.p.grant()
        self.assertEqual(grant["expires_at"],
                         stamp(NOW + timedelta(hours=POLICY["default_goal_cap_hours"])))
        self.assertEqual(grant["goal"]["target"], "DLV-002")
        self.assertEqual(grant["goal"]["end"]["terminal_statuses"], ["merged", "cancelled"])
        code, _out, err = self.p.run_verb("on", "--goal", "delivery:DLV-002", "--for", "3h")
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
                  "target": "workspace/docs/delivery/deliveries/dlv-002-night/delivery.md"
                            "#User Decisions D-07", **fields}
        argv = ["record", "--class", name]
        for key, value in values.items():
            argv += [f"--{key}", value]
        return self.p.run_verb(*argv, now=now)

    def queue(self, name: str = "release", now: datetime = NOW) -> tuple[int, str, str]:
        return self.p.run_verb("queue", "--class", name, "--question", "Publish the tag?",
                               "--options", "Publish; Hold", "--recommendation", "Hold",
                               "--blocks", "release notes", now=now)

    def test_check_reports_an_active_an_expired_and_no_grant(self):
        code, out, _err = self.p.run_verb("check")
        self.assertEqual((code, out.strip()), (1, "autopilot: inactive"))
        self.assertFalse((self.p.root / ".agentrof").exists())
        self.p.run_verb("on", "--for", "2h", "--deny", "merge")
        grant = self.p.grant()
        code, out, _err = self.p.run_verb("check", now=NOW + timedelta(minutes=30))
        self.assertEqual(code, 0)
        self.assertIn(f"autopilot: active {grant['id']} until {grant['expires_at']}", out)
        self.assertIn("allowed classes: choice, approval_gate\n", out)
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
            "event": "decision", "class": "choice", "question": "Which cache root?",
            "options": ["One per checkout", "One per Item"], "choice": "One per checkout",
            "reason": "the recommended option",
            "target": "workspace/docs/delivery/deliveries/dlv-002-night/delivery.md"
                      "#User Decisions D-07"})
        code, _out, err = self.record("release")
        self.assertEqual(code, 1)
        self.assertIn("class 'release' is not allowed by grant", err)
        code, _out, err = self.record(choice="Neither")
        self.assertIn("the choice must be one of the options", err)
        code, _out, err = self.record(options="")
        self.assertEqual(code, 0, err)
        self.assertEqual(len([event for event in self.p.events() if event["event"] == "decision"]), 2)

    def test_queue_appends_one_pending_question(self):
        self.p.run_verb("on")
        code, out, err = self.queue(now=NOW + timedelta(minutes=2))
        self.assertEqual(code, 0, err)
        self.assertIn("queued question 1", out)
        entry = self.p.events()[-1]
        self.assertEqual((entry["event"], entry["class"], entry["options"], entry["recommendation"],
                          entry["blocks"], entry["time"]),
                         ("queued", "release", ["Publish", "Hold"], "Hold", "release notes",
                          stamp(NOW + timedelta(minutes=2))))
        code, _out, err = self.queue("choice")
        self.assertIn("class 'choice' is allowed by grant", err)

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
        self.p.delivery("active")
        self.p.run_verb("on", "--goal", "delivery:DLV-002", "--for", "9h")
        self.record()
        self.queue()
        self.queue("phase_start")
        code, out, err = self.p.run_verb("status", now=NOW + timedelta(hours=1, minutes=15))
        self.assertEqual(code, 0, err)
        self.assertIn("(7h 45m left)", out)
        self.assertIn("goal: delivery DLV-002, ends when scripts/delivery_compile.py reads merged or"
                      " cancelled; current state: active", out)
        self.assertIn("allowed classes: choice, approval_gate, merge", out)
        self.assertIn("decisions: 1, queued: 2", out)
        self.assertIn("arming: user_only_entry; question guard: instructions", out)
        code, out, _err = self.p.run_verb("status", "--json", now=NOW + timedelta(hours=1))
        status = json.loads(out)
        self.assertEqual((status["active"], status["remaining_minutes"], status["counts"],
                          status["goal_read"]["state"]),
                         (True, 480, {"decisions": 1, "queued": 2}, "active"))

    def test_report_lists_every_decision_and_queued_question_in_time_order(self):
        self.p.run_verb("on")
        self.queue(now=NOW + timedelta(minutes=1))
        self.record(now=NOW + timedelta(minutes=2))
        self.queue("phase_start", now=NOW + timedelta(minutes=3))
        code, out, err = self.p.run_verb("report", "--json", now=NOW + timedelta(minutes=4))
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual([(entry["event"], entry["class"]) for entry in report["entries"]],
                         [("queued", "release"), ("decision", "choice"), ("queued", "phase_start")])
        times = [entry["time"] for entry in report["entries"]]
        self.assertEqual(times, sorted(times))
        code, out, _err = self.p.run_verb("report")
        lines = [line for line in out.splitlines() if line[:1].isdigit()]
        self.assertEqual([line.split(" ", 3)[2] for line in lines], ["queued", "decision", "queued"])

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


class GoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project(self)

    def check(self, now: datetime) -> tuple[int, str]:
        code, out, _err = self.p.run_verb("check", now=now)
        return code, out

    def test_check_completes_a_delivery_goal_once_its_compiler_reads_a_terminal_status(self):
        self.p.delivery("active")
        self.p.run_verb("on", "--goal", "delivery:DLV-002")
        self.assertEqual(self.check(NOW + timedelta(hours=1))[0], 0)
        self.p.delivery("cancelled")
        code, out = self.check(NOW + timedelta(hours=2))
        self.assertEqual(code, 1)
        self.assertIn("scripts/delivery_compile.py read delivery DLV-002 as cancelled", out)
        completion = self.p.grant()["completion"]
        self.assertEqual((self.p.grant()["state"], completion["goal_state"], completion["read_at"]),
                         ("completed", "cancelled", stamp(NOW + timedelta(hours=2))))
        self.assertEqual(self.p.events()[-1]["event"], "completed")

    def test_a_merged_delivery_is_read_through_the_compiler_merge_derivation(self):
        self.p.delivery("awaiting_merge")
        self.p.run_verb("on", "--goal", "delivery:DLV-002")
        with mock.patch.object(delivery_compile, "delivery_state",
                               return_value=("merged", None)) as derived:
            code, out = self.check(NOW + timedelta(hours=1))
        derived.assert_called()
        self.assertEqual(code, 1)
        self.assertEqual(self.p.grant()["completion"]["goal_state"], "merged")

    def test_check_completes_a_requirement_goal_at_its_terminal_planning_status(self):
        for terminal in ("withdrawn", "incorporated"):
            with self.subTest(terminal=terminal):
                self.p.requirement("approved")
                self.p.run_verb("on", "--goal", "requirement:REQ-005")
                self.assertEqual(self.check(NOW + timedelta(minutes=10))[0], 0)
                if terminal == "incorporated":
                    patcher = mock.patch.object(requirement_compile, "requirement_incorporated",
                                                return_value=True)
                else:
                    self.p.requirement("withdrawn")
                    patcher = contextlib.nullcontext()
                with patcher:
                    code, out = self.check(NOW + timedelta(minutes=20))
                self.assertEqual(code, 1, out)
                self.assertEqual(self.p.grant()["completion"]["goal_state"], terminal)
                self.assertEqual(self.p.grant()["completion"]["read_at"],
                                 stamp(NOW + timedelta(minutes=20)))

    def test_a_goal_that_is_not_terminal_stays_active_until_its_cap_whatever_is_claimed(self):
        self.p.delivery("review")
        self.p.run_verb("on", "--goal", "delivery:DLV-002", "--for", "6h")
        claim = {"reason": "the Delivery is done, the goal is reached", "question": "Merge now?",
                 "options": "", "choice": "Yes", "target": "delivery.md"}
        argv = ["record", "--class", "choice"]
        for key, value in claim.items():
            argv += [f"--{key}", value]
        for minutes in (1, 120, 359):
            with self.subTest(minutes=minutes):
                self.p.run_verb(*argv, now=NOW + timedelta(minutes=minutes))
                self.assertEqual(self.check(NOW + timedelta(minutes=minutes))[0], 0)
        code, out = self.check(NOW + timedelta(hours=6))
        self.assertEqual(code, 1)
        self.assertEqual(self.p.grant()["state"], "expired")

    def test_complete_ends_any_grant_and_records_evidence_and_compiler_agreement(self):
        cases = (("delivery:DLV-002", False), ("delivery:DLV-003", True),
                 ("text:finish the migration", None), (None, None))
        for goal, agreed in cases:
            with self.subTest(goal=goal):
                if goal and goal.startswith("delivery"):
                    self.p.delivery("active", goal.split(":")[1])
                self.p.run_verb("on", *(["--goal", goal] if goal else []))
                if goal == "delivery:DLV-003":
                    self.p.delivery("cancelled", "DLV-003")
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

    def run_hook(self, host: str, verb: str, payload, cwd: Path,
                 env: dict | None = None) -> subprocess.CompletedProcess:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(self.hook_argv(host, verb), input=text, cwd=cwd, env=env,
                              capture_output=True, text=True, check=False, timeout=60)

    def run_verb(self, host: str, root: Path, *argv: str) -> subprocess.CompletedProcess:
        script = self.package(host) / "skill-content/autopilot/scripts/autopilot.py"
        return subprocess.run([sys.executable, str(script), *argv], cwd=root, capture_output=True,
                              text=True, check=False, timeout=60)

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

    def test_both_hosts_carry_the_same_canonical_content(self):
        canonical = {path.relative_to(ENTRY).as_posix(): path.read_bytes()
                     for path in sorted(ENTRY.rglob("*"))
                     if path.is_file() and "__pycache__" not in path.parts}
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                root = self.package(host) / "skill-content/autopilot"
                self.assertEqual({path.relative_to(root).as_posix(): path.read_bytes()
                                  for path in sorted(root.rglob("*")) if path.is_file()},
                                 canonical)

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

    def test_a_copied_package_mints_no_grant_the_installed_package_honours(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root, \
                    tempfile.TemporaryDirectory() as raw:
                copy = Path(raw) / "copy"
                shutil.copytree(self.package(host), copy, ignore=shutil.ignore_patterns("hooks"))
                script = copy / "skill-content/autopilot/scripts/autopilot.py"
                agent_on = [sys.executable, str(script), "on", "--for", "72h", "--allow", "release"]
                result = subprocess.run(agent_on, cwd=root, capture_output=True, text=True,
                                        check=False, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("hooks/hooks.json is missing", result.stderr)
                self.assertFalse((root / RUNTIME / "grant.json").exists())
                (copy / ".agent-marketplace-package.json").unlink()
                result = subprocess.run(agent_on, cwd=root, capture_output=True, text=True,
                                        check=False, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.run_verb(host, root, "check")
                self.assertEqual(result.returncode, 1)
                self.assertIn("armed by user_only_entry, but this package arms through"
                              " user_prompt_hook", result.stdout)
                question = {"session_id": "s", "cwd": str(root), "hook_event_name": "PreToolUse",
                            "tool_name": HOST_HOOKS[host][2], "tool_input": {}}
                result = self.run_hook(host, "pre-question", question, root)
                self.assertEqual((result.returncode, result.stdout), (0, ""))

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

    def test_the_question_hook_denies_only_while_a_grant_is_active(self):
        for host in HOST_HOOKS:
            with self.subTest(host=host), self.project() as root:
                question = {"session_id": "s", "cwd": str(root), "hook_event_name": "PreToolUse",
                            "tool_name": HOST_HOOKS[host][2], "tool_input": {"questions": []}}
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
                for fragment in (grant["id"], grant["expires_at"], ", ".join(grant["classes"]),
                                 "take the recommended option", " check`", " record`",
                                 "governing document", " queue`", "Do not ask the user",
                                 "every remaining task waits on a queued question"):
                    self.assertIn(fragment, reason)
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
    def test_host_contracts_state_the_procedure_and_that_only_the_user_arms(self):
        spellings = {"claude": (f"/{NAME}", "UserPromptExpansion", "AskUserQuestion"),
                     "codex": (f"${NAME}", "UserPromptSubmit", "request_user_input")}
        for host, tokens in spellings.items():
            with self.subTest(host=host):
                text = " ".join((ROOT / "platforms" / host / "software-engineering-team"
                                 / "host-contract.md").read_text(encoding="utf-8").split())
                section = text.split("## Autopilot", 1)[1].lower()
                for fragment in (*tokens, "Only the user arms a grant", "autopilot.py check",
                                 "take the recommended option", "autopilot.py record",
                                 "governing document", "marked with the grant id",
                                 "autopilot.py queue", "continue the work that does not depend on it",
                                 "stop only when every remaining task waits on a queued question"):
                    self.assertIn(fragment.lower(), section)

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
        self.assertEqual(policy["session_entries"], ["autopilot"])
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
            value["session_entries"] = []
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "every current role and entry"):
                task_inputs.catalog(package)
            value["session_entries"] = ["autopilot", "deliver"]
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "session entries"):
                task_inputs.catalog(package)

    def test_readable_goals_end_at_statuses_their_compilers_read(self):
        ends = {kind["id"]: kind["end"] for kind in POLICY["goal_kinds"]}
        self.assertEqual(set(autopilot.GOAL_READERS),
                         {end["compiler"] for end in ends.values() if end != "none"})
        self.assertLessEqual(set(ends["delivery"]["terminal_statuses"]), delivery_compile.STATUSES)
        requirement = set(ends["requirement"]["terminal_statuses"])
        self.assertLessEqual(requirement_compile.TERMINAL_STATUSES, requirement)
        self.assertLessEqual(requirement, requirement_compile.STATUSES | {"incorporated"})
        self.assertEqual(ends["text"], "none")

    def test_a_delivery_decision_log_accepts_an_autopilot_answer_as_it_stands(self):
        header = ("| id | class | question | options | recommendation | status | answer | blocks |"
                  " wait_minutes |\n|---|---|---|---|---|---|---|---|---|\n")
        body = ("## User Decisions\n\n" + header
                + "| D-07 | queued | Which cache root? | One per checkout; One per Item |"
                  " One per checkout | answered | One per checkout (autopilot"
                  " AP-20261001T210000Z-3f9a, class choice) | AUTH-01 | 0 |\n")
        self.assertEqual(delivery_compile.user_decision_findings(body, ["AUTH-01"]), [])
        self.assertTrue(delivery_compile.user_decision_findings(body.replace("| queued |", "| choice |"),
                                                                ["AUTH-01"]))


class ValidatorTests(unittest.TestCase):
    """tools/validate.py rejects every malformed autopilot policy."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        plugin = self.root / "plugins" / "software-engineering-team"
        for relative in ("skill-content/autopilot/data/autopilot-policy.json",
                         "skill-content/autopilot/scripts/autopilot.py",
                         "scripts/delivery_compile.py", "scripts/requirement_compile.py"):
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

    def test_the_shipped_policy_is_clean(self):
        self.assertEqual(self.messages(), [])

    def test_malformed_policies_are_rejected(self):
        def duplicate(key):
            return lambda value: value[key].append(dict(value[key][0]))

        maximum = POLICY["max_duration_hours"]

        def only_allowed(value):
            for entry in value["classes"]:
                entry["default"] = "allowed" if entry["default"] == "never" else entry["default"]

        cases = (
            (duplicate("classes"), "duplicate class 'choice'"),
            (duplicate("goal_kinds"), "duplicate goal kind 'delivery'"),
            (lambda value: value["classes"][0].update(default="sometimes"),
             "class 'choice' has unknown default 'sometimes'"),
            (only_allowed, "the never set is empty"),
            (lambda value: value.update(default_duration_hours=maximum + 1),
             f"default_duration_hours {maximum + 1} is above max_duration_hours {maximum}"),
            (lambda value: value.update(default_goal_cap_hours=maximum + 1),
             f"default_goal_cap_hours {maximum + 1} is above max_duration_hours {maximum}"),
            (lambda value: value["goal_kinds"][0]["end"].update(terminal_statuses=[]),
             "readable goal kind 'delivery' declares no distinct terminal statuses"),
            (lambda value: value["goal_kinds"][1]["end"].pop("terminal_statuses"),
             "goal kind 'requirement' end is none or holds exactly a compiler"),
            (lambda value: value["goal_kinds"][0]["end"].update(compiler="scripts/ghost.py"),
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
        for module, rejected in (("delivery_compile", False), ("requests", True)):
            with self.subTest(module=module):
                (script / "probe.py").write_text(f"import {module}\n", encoding="utf-8")
                findings: list = []
                validate.check_stdlib_only(validate.build_tree(self.root), findings)
                flagged = [finding for finding in findings if finding.path.endswith("probe.py")]
                self.assertEqual(bool(flagged), rejected, flagged)


if __name__ == "__main__":
    unittest.main()
