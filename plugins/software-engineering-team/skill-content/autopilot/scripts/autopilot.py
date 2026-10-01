#!/usr/bin/env python3
"""User-armed autopilot grant for the orchestrating session.

While a grant is active the session takes the recommended option at every
question of an allowed class, records each decision and queues every other
question. Classes, goal kinds and durations are data in the entry's
``data/autopilot-policy.json``. The grant is ignored runtime state under
``<git-root>/.agentrof/agent-marketplace/.runtime/autopilot/``; only the
decisions it takes reach tracked documents.

Only the user arms a grant. When the installed package declares the
user-prompt hook, ``hook user-prompt`` records the entry command the user
typed as a short-lived arming record, and ``on`` refuses without it and takes
the grant's options from it alone. Without that hook ``on`` reads its options
on its own command line, and the grant records that the entry's user-only
invocation was the guard. ``hook pre-question`` denies the host question tool
while a grant is active. A hook with nothing to do, or one that fails, prints
nothing and exits 0.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ENTRY = Path(__file__).resolve().parents[1]
PACKAGE = ENTRY.parents[1]
SCRIPTS = PACKAGE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

POLICY = ENTRY / "data" / "autopilot-policy.json"
HOOKS = PACKAGE / "hooks" / "hooks.json"
# The build writes this provenance file into every host package it makes.
MANIFEST = PACKAGE / ".agent-marketplace-package.json"
GRANT = "grant.json"
ARMING = "arming.json"
LEDGER = "ledger.jsonl"
LOCK = "autopilot.lock"
DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$")
CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# An arming record from a clock a little ahead of this one is still fresh.
ARMING_SKEW = timedelta(seconds=60)
GRANT_STATES = ("active", "expired", "completed", "revoked", "replaced")


class Refusal(Exception):
    """A verb refused; the message names what is wrong."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refusal(message)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no offset: {value}")
    return parsed.astimezone(timezone.utc)


def span(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def load_policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def hook_commands() -> list[str] | None:
    """Every command line the installed package declares for its hooks, None when unreadable.

    A source tree has no hooks file and declares none. A built package always
    ships one, so a missing or torn file there reads as None.
    """
    try:
        data = json.loads(HOOKS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None if MANIFEST.is_file() else []
    except (OSError, ValueError):
        return None
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            command = node.get("command")
            if isinstance(command, str):
                arguments = node.get("args")
                extra = " ".join(str(item) for item in arguments) if isinstance(arguments, list) else ""
                found.append(f"{command} {extra}".strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


def declares_hook(verb: str) -> bool:
    return any(f"autopilot.py hook {verb}" in command for command in hook_commands() or [])


def package_binding() -> dict:
    """The host and the session variable the package's user-prompt hook declares."""
    for command in hook_commands() or []:
        if "autopilot.py hook user-prompt" in command:
            try:
                tokens = shlex.split(command)
            except ValueError:
                return {}
            values = dict(zip(tokens, tokens[1:]))
            return {"host": values.get("--host"), "session_env": values.get("--session-env")}
    return {}


def binding_problem(host: str | None, session: str | None, *, typed: bool = False) -> str | None:
    """Why the running session is not the one a grant, or a typed command, belongs to.

    A grant serves the session and host whose user typed it. The running
    package names its host and the variable that holds its session id; a
    variable that is not set compares nothing.
    """
    owner = "this arming record was typed in" if typed else "bound to"
    binding = package_binding()
    running = binding.get("host")
    if host and running and host != running:
        return f"{owner} a {host} session; this package runs {running}"
    variable = binding.get("session_env")
    here = os.environ.get(variable) if variable else None
    if session and here and here != session:
        return f"{owner} {host} session {session}; this session is {here}"
    return None


def bound_line(grant: dict) -> str:
    session = grant.get("armed_by", {}).get("session_id")
    return f"{grant.get('host')} session {session}" if session else "every session (no session bound)"


def package_guard() -> tuple[str | None, str]:
    """How this package arms a grant, or None and why it can arm none.

    A built package arms only through its declared user-prompt hook; one that
    cannot show that hook fails closed. Only a source tree without hooks falls
    back to the entry's user-only invocation.
    """
    commands = hook_commands()
    if commands is None:
        problem = "hooks/hooks.json is missing" if not HOOKS.exists() \
            else "hooks/hooks.json cannot be read"
        return None, f"{problem} in this built package"
    if any("autopilot.py hook user-prompt" in command for command in commands):
        return "user_prompt_hook", ""
    if MANIFEST.is_file():
        return None, "this built package declares no arming hook in hooks/hooks.json"
    return "user_only_entry", ""


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


def main_checkout(project: Path) -> Path:
    import delivery_git

    return delivery_git.main_worktree(project)


def state_dir(project: Path) -> Path:
    """The autopilot runtime directory of the checkout's main worktree."""
    import delivery_git

    runtime = delivery_git.runtime_root(main_checkout(project))
    directory = runtime / "autopilot"
    for part in (runtime.parents[1], runtime.parent, runtime, directory):
        if part.is_symlink():
            raise Refusal(f"the autopilot runtime must not pass through a symbolic link: {part}")
    return directory


def private_dir(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def write_private(path: Path, value: dict) -> None:
    import atomic_file

    private_dir(path.parent)
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    os.chmod(path, 0o600)
    atomic_file.replace_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def append_private(path: Path, value: dict) -> None:
    private_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def read_json(path: Path) -> dict | None:
    """The file's object, None when it is missing; ValueError when unreadable."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not an object")
    return value


@contextlib.contextmanager
def locked(directory: Path):
    import file_lock

    private_dir(directory)
    lock = directory / LOCK
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(lock, 0o600)
        file_lock.lock(descriptor)
        try:
            yield
        finally:
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def ledger(directory: Path, grant: str | None = None) -> list[dict]:
    path = directory / LEDGER
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (grant is None or event.get("grant") == grant):
            events.append(event)
    return events


def log(directory: Path, now: datetime, grant: dict, event: str, **fields) -> None:
    append_private(directory / LEDGER, {"time": stamp(now), "grant": grant["id"], "event": event, **fields})


def inactive_reason(grant: dict | None, now: datetime) -> str | None:
    """Why a grant gives no authority here, or None while it is active.

    Besides its state and time, a grant this package could not have armed is
    inactive: one armed under another guard, one longer than the maximum or
    one that holds a never class.
    """
    if not grant:
        return "no grant"
    if not isinstance(grant, dict):
        return "grant.json is not an object"
    problem = grant_problem(grant)
    if problem:
        return problem
    if grant["state"] != "active":
        return f"{grant['state']} at {grant.get('ended_at')}"
    expires = parse_stamp(grant["expires_at"])
    if now >= expires:
        return f"expired at {grant['expires_at']}"
    guard, problem = package_guard()
    if guard is None:
        return f"{problem}, so it honours no grant"
    armed = grant["armed_by"].get("guard")
    if armed != guard:
        return f"armed by {armed}, but this package arms through {guard}"
    policy = load_policy()
    length = expires - parse_stamp(grant["granted_at"])
    if length > timedelta(hours=policy["max_duration_hours"]):
        return (f"runs {span(length)} from granted_at, longer than the"
                f" {policy['max_duration_hours']} h maximum")
    never = {entry["id"] for entry in policy["classes"] if entry["default"] == "never"}
    held = [name for name in grant["classes"] if name in never]
    if held:
        return f"holds never class {held[0]!r}"
    return None


def is_active(grant: dict | None, now: datetime) -> bool:
    return inactive_reason(grant, now) is None


def end_grant(directory: Path, now: datetime, grant: dict, state: str, ended_at: datetime,
              **fields) -> dict:
    grant.update(state=state, ended_at=stamp(ended_at), **fields)
    write_private(directory / GRANT, grant)
    log(directory, now, grant, state, **fields)
    return grant


def grant_problem(grant: dict) -> str | None:
    """What keeps a grant record from being read, or None when its fields hold."""
    for key in ("id", "state", "granted_at", "expires_at"):
        if not isinstance(grant.get(key), str) or not grant[key]:
            return f"grant.json has no {key}"
    if grant["state"] not in GRANT_STATES:
        return f"grant.json state {grant['state']!r} is unknown"
    try:
        parse_stamp(grant["granted_at"])
        parse_stamp(grant["expires_at"])
    except (TypeError, ValueError) as exc:
        return f"grant.json holds an unreadable time: {exc}"
    classes = grant.get("classes")
    if not isinstance(classes, list) or not all(isinstance(name, str) for name in classes):
        return "grant.json classes is not a list of class ids"
    if not isinstance(grant.get("armed_by"), dict):
        return "grant.json armed_by is not an object"
    return None


def move_aside(path: Path, now: datetime) -> Path:
    stem = f"{path.name}.broken-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
    target, number = path.with_name(stem), 1
    while target.exists():
        number += 1
        target = path.with_name(f"{stem}-{number}")
    os.replace(path, target)
    os.chmod(target, 0o600)
    return target


def current(directory: Path, now: datetime) -> dict | None:
    """The latest grant, marked expired once its time has passed. Call under the lock.

    A grant that cannot be read, or lacks a field, is moved aside as
    grant.json.broken-<time> and logged, so the verbs go on and a new grant
    can start.
    """
    path = directory / GRANT
    try:
        grant = read_json(path)
        problem = None if grant is None else grant_problem(grant)
    except ValueError as exc:
        grant, problem = None, str(exc)
    if problem:
        moved = move_aside(path, now)
        append_private(directory / LEDGER, {
            "time": stamp(now), "grant": grant.get("id") if isinstance(grant, dict) else None,
            "event": "broken", "moved_to": moved.name, "problem": problem})
        print(f"autopilot: moved an unreadable grant aside to {moved.name} ({problem})",
              file=sys.stderr)
        return None
    if grant is not None and grant["state"] == "active" \
            and now >= parse_stamp(grant["expires_at"]):
        end_grant(directory, now, grant, "expired", parse_stamp(grant["expires_at"]))
    return grant


# ---------------------------------------------------------------------------
# Goals: a readable goal's end is read by its owning compiler
# ---------------------------------------------------------------------------


def read_delivery_goal(docs: Path, target: str) -> dict:
    import delivery_compile

    root = delivery_compile.find_delivery(docs, target)
    if root is None:
        return {"found": False, "error": f"finds no Delivery {target}"}
    props, _body = delivery_compile.split_note(root / "delivery.md")
    state, unknown = delivery_compile.delivery_state(root, props)
    return {"found": True, "state": str(state), "error": unknown}


def read_requirement_goal(docs: Path, target: str) -> dict:
    import requirement_compile

    for path in requirement_compile.requirement_paths(docs):
        if requirement_compile.requirement_id(path) != target:
            continue
        payload = requirement_compile.status_requirement(path)
        state = str(payload.get("status"))
        # Planning ends when the approved Requirement is in the approved backlog.
        if state == "approved" and payload.get("incorporated"):
            state = "incorporated"
        return {"found": True, "state": state, "error": None}
    return {"found": False, "error": f"finds no Requirement {target}"}


GOAL_READERS = {
    "scripts/delivery_compile.py": read_delivery_goal,
    "scripts/requirement_compile.py": read_requirement_goal,
}


def read_goal(project: Path, goal: dict) -> dict | None:
    """The goal's state as its owning compiler reads it, None for an unreadable kind."""
    end = goal.get("end")
    if not isinstance(end, dict):
        return None
    reader = GOAL_READERS.get(str(end.get("compiler")))
    if reader is None:
        return {"found": False, "state": None, "error": f"no reader for {end.get('compiler')}"}
    try:
        result = reader(main_checkout(project) / "workspace" / "docs", str(goal["target"]))
    except Exception as exc:
        # A read that fails is no terminal state: the grant keeps running to its cap.
        result = {"found": False, "error": f"cannot read it: {exc or type(exc).__name__}"}
    result.setdefault("state", None)
    result["terminal"] = bool(result.get("found")) and result["state"] in end.get("terminal_statuses", [])
    return result


# ---------------------------------------------------------------------------
# Grant terms
# ---------------------------------------------------------------------------


def option_parser() -> Parser:
    parser = Parser(prog="autopilot on", add_help=False)
    parser.add_argument("--for", dest="duration")
    parser.add_argument("--until")
    parser.add_argument("--goal")
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--deny", action="append", default=[])
    return parser


def names(values: list[str]) -> list[str]:
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


def duration(text: str) -> timedelta:
    match = DURATION_RE.match(text.strip().lower())
    if not text.strip() or match is None:
        raise Refusal(f"--for {text!r} is not a duration such as 9h, 90m, 1h30m or 2d")
    days, hours, minutes = (int(part or 0) for part in match.groups())
    value = timedelta(days=days, hours=hours, minutes=minutes)
    if value <= timedelta(0):
        raise Refusal("--for must be longer than zero")
    return value


def until(text: str, now: datetime) -> datetime:
    clock = CLOCK_RE.match(text.strip())
    if clock:
        local = now.astimezone()
        moment = local.replace(hour=int(clock.group(1)), minute=int(clock.group(2)),
                               second=0, microsecond=0)
        if moment <= local:
            moment += timedelta(days=1)
        return moment.astimezone(timezone.utc)
    try:
        # A time without an offset is the local wall clock.
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise Refusal(f"--until {text!r} is not a time such as 07:00 or 2026-10-02T07:00+03:00") from None


def grant_classes(policy: dict, allow: list[str], deny: list[str]) -> list[str]:
    declared = {entry["id"]: entry["default"] for entry in policy["classes"]}
    for name in [*allow, *deny]:
        if name not in declared:
            raise Refusal(f"unknown class {name!r}; declared classes: {', '.join(declared)}")
    for name in allow:
        if declared[name] == "never":
            raise Refusal(f"class {name!r} is never delegated; no grant can allow it")
    both = sorted(set(allow) & set(deny))
    if both:
        raise Refusal(f"class {both[0]!r} is both allowed and denied")
    defaults = {name for name, default in declared.items() if default == "allowed"}
    return [name for name in declared if name in (defaults | set(allow)) - set(deny)]


def grant_goal(policy: dict, text: str, project: Path) -> dict:
    kinds = {entry["id"]: entry for entry in policy["goal_kinds"]}
    kind, separator, target = text.partition(":")
    kind, target = kind.strip(), target.strip()
    if kind not in kinds:
        raise Refusal(f"unknown goal kind {kind!r}; declared kinds: {', '.join(kinds)}")
    if not separator or not target:
        raise Refusal(f"goal {text!r} names no target; write {kind}:<target>")
    end = kinds[kind]["end"]
    if isinstance(end, dict):
        target = target.upper()
    goal = {"kind": kind, "target": target, "end": end}
    read = read_goal(project, goal)
    if read is not None:
        if not read["found"]:
            raise Refusal(f"goal {kind}:{target}: the owning compiler {end['compiler']} {read['error']}")
        if read["terminal"]:
            raise Refusal(f"goal {kind}:{target} is already reached: {end['compiler']} reads"
                          f" {read['state']}")
    return goal


def grant_terms(policy: dict, options: argparse.Namespace, now: datetime, project: Path) -> dict:
    maximum = timedelta(hours=policy["max_duration_hours"])
    if options.duration and options.until:
        raise Refusal("give --for or --until, not both")
    goal = grant_goal(policy, options.goal, project) if options.goal else None
    if options.duration:
        length = duration(options.duration)
        if length > maximum:
            raise Refusal(f"--for {options.duration} is above the {policy['max_duration_hours']} h"
                          " maximum")
        expires = now + length
    elif options.until:
        expires = until(options.until, now)
        if expires <= now:
            raise Refusal(f"--until {options.until} is in the past")
        if expires - now > maximum:
            raise Refusal(f"--until {options.until} is more than the"
                          f" {policy['max_duration_hours']} h maximum away")
    else:
        hours = policy["default_goal_cap_hours"] if goal else policy["default_duration_hours"]
        expires = now + timedelta(hours=hours)
    return {"expires_at": stamp(expires), "goal": goal,
            "classes": grant_classes(policy, names(options.allow), names(options.deny))}


def options_given(options: argparse.Namespace) -> bool:
    return bool(options.duration or options.until or options.goal or options.allow or options.deny)


def fresh_arming(directory: Path, policy: dict, now: datetime) -> dict:
    try:
        arming = read_json(directory / ARMING)
    except ValueError:
        arming = None
    try:
        armed_at = parse_stamp(arming["armed_at"]) if arming else None
    except (KeyError, TypeError, ValueError):
        armed_at = None
    ttl = timedelta(minutes=policy["arming_ttl_minutes"])
    if armed_at is None or not now - ttl <= armed_at <= now + ARMING_SKEW \
            or not isinstance(arming.get("arguments"), str):
        raise Refusal(
            "no fresh arming record from your own typed entry command; only the user starts,"
            " extends or widens a grant. Type the autopilot entry with `on` and its options"
            " yourself. If the host skipped the package hooks, enable or trust them first.")
    return arming


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def command_line() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))}"


def goal_line(goal: dict | None, read: dict | None = None) -> str:
    if not goal:
        return "goal: none (time-bound)"
    text = f"goal: {goal['kind']} {goal['target']}"
    if not isinstance(goal.get("end"), dict):
        return text + ", read by no compiler; it ends through complete, off or the cap"
    end = goal["end"]
    text += f", ends when {end['compiler']} reads {' or '.join(end['terminal_statuses'])}"
    if read is not None:
        state = read.get("state") if read.get("found") else "unknown"
        text += f"; current state: {state}"
        if read.get("error"):
            text += f" ({read['error']})"
    return text


def class_descriptions(grant: dict) -> list[tuple[str, str]]:
    described = {entry["id"]: entry["description"] for entry in load_policy()["classes"]}
    return [(name, described.get(name, "not declared by this package"))
            for name in grant.get("classes", [])]


def summary(grant: dict, now: datetime, read: dict | None = None, *,
            describe: bool = False) -> list[str]:
    lines = []
    if grant.get("state") == "active":
        expires = parse_stamp(grant["expires_at"])
        lines.append(f"autopilot: active {grant['id']} until {grant['expires_at']}"
                     f" ({span(expires - now)} left)")
    else:
        lines.append(f"autopilot: {grant.get('state')} {grant.get('id')} at {grant.get('ended_at')}")
    lines.append(goal_line(grant.get("goal"), read))
    if describe:
        lines.append("allowed classes:" if grant.get("classes") else "allowed classes: none")
        lines.extend(f"- {name}: {text}" for name, text in class_descriptions(grant))
    else:
        lines.append("allowed classes: " + (", ".join(grant.get("classes", [])) or "none"))
    return lines


def entry_classes(entry: dict) -> list[str]:
    """The classes a ledger entry names; an older entry holds one class."""
    classes = entry.get("classes")
    return list(classes) if isinstance(classes, list) else [str(entry.get("class"))]


def report_payload(directory: Path, grant: dict) -> dict:
    entries = [event for event in ledger(directory, grant["id"])
               if event.get("event") in ("decision", "queued")]
    return {"grant": grant, "entries": entries,
            "counts": {"decisions": sum(event["event"] == "decision" for event in entries),
                       "queued": sum(event["event"] == "queued" for event in entries)}}


def report_lines(payload: dict) -> list[str]:
    grant = payload["grant"]
    lines = [f"Autopilot report {grant['id']} ({grant.get('state')})",
             f"granted {grant.get('granted_at')}, expires {grant.get('expires_at')}"
             + (f", ended {grant['ended_at']}" if grant.get("ended_at") else ""),
             goal_line(grant.get("goal")),
             "allowed classes: " + (", ".join(grant.get("classes", [])) or "none"),
             f"armed by: {grant.get('armed_by', {}).get('guard')}"]
    completion = grant.get("completion")
    if completion:
        lines.append(f"completed by {completion.get('by')}: {completion.get('evidence') or ''}"
                     f" (goal state {completion.get('goal_state')},"
                     f" compiler agreed: {completion.get('compiler_agreed')})")
    for number, entry in enumerate(payload["entries"], 1):
        options = "; ".join(entry.get("options", []))
        classes = ", ".join(entry_classes(entry))
        if entry["event"] == "decision":
            lines.append(f"{number}. {entry['time']} decision [{classes}]"
                         f" {entry['question']} -> {entry['choice']}")
            lines.append(f"   options: {options or 'none offered'}; reason: {entry['reason']};"
                         f" written to: {entry['target']}")
        else:
            lines.append(f"{number}. {entry['time']} queued [{classes}]"
                         f" {entry['question']} -> recommended: {entry['recommendation']}")
            lines.append(f"   options: {options or 'none offered'}"
                         + (f"; blocks: {entry['blocks']}" if entry.get("blocks") else ""))
    counts = payload["counts"]
    lines.append(f"decisions: {counts['decisions']}, queued: {counts['queued']}")
    return lines


def guards() -> dict:
    guard, problem = package_guard()
    return {"arming": guard or f"none ({problem})",
            "question_guard": "hook" if declares_hook("pre-question") else "instructions"}


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def cmd_on(args: argparse.Namespace, now: datetime) -> int:
    project = Path(args.project_root)
    policy = load_policy()
    directory = state_dir(project)
    guard, problem = package_guard()
    if guard is None:
        raise Refusal(f"{problem}; on refuses until the package is repaired or reinstalled")
    if guard == "user_prompt_hook":
        if options_given(args):
            raise Refusal("this host arms a grant from the entry command you type; run `on`"
                          " without options and it takes them from your typed command")
        # Refused before any write: no arming, no grant, no runtime file.
        typed = fresh_arming(directory, policy, now)
        problem = binding_problem(typed.get("host"), typed.get("session_id"), typed=True)
        if problem:
            raise Refusal(problem)
        with locked(directory):
            # The grant state is read first, so a failure leaves the typed arming in place.
            current(directory, now)
            arming = fresh_arming(directory, policy, now)
            (directory / ARMING).unlink()
        try:
            tokens = shlex.split(arming["arguments"])
        except ValueError as exc:
            raise Refusal(f"your typed command cannot be read: {exc}") from None
        if tokens[:1] != ["on"]:
            raise Refusal("the arming record holds no `on` command")
        options = option_parser().parse_args(tokens[1:])
        armed_by = {"guard": "user_prompt_hook", "arguments": arming["arguments"],
                    "armed_at": arming["armed_at"], "session_id": arming.get("session_id"),
                    "event": arming.get("event")}
        host = arming.get("host")
    else:
        options = args
        typed = [f"--{key} {shlex.quote(value)}" for key, value in (
            ("for", args.duration), ("until", args.until), ("goal", args.goal)) if value]
        typed += [f"--allow {value}" for value in args.allow]
        typed += [f"--deny {value}" for value in args.deny]
        armed_by = {"guard": "user_only_entry", "arguments": " ".join(["on", *typed])}
        host = args.host
    terms = grant_terms(policy, options, now, project)
    with locked(directory):
        previous = current(directory, now)
        grant = {"schema_version": 1,
                 "id": f"AP-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}",
                 "host": host, "state": "active", "granted_at": stamp(now), **terms,
                 "armed_by": armed_by, "replaces": None}
        reason = inactive_reason(previous, now)
        if previous and reason is None:
            grant["replaces"] = previous["id"]
            end_grant(directory, now, previous, "replaced", now, replaced_by=grant["id"])
        elif previous and previous.get("state") == "active":
            # A grant this package could not have armed is set aside, never chained.
            log(directory, now, previous, "set_aside", reason=reason)
        write_private(directory / GRANT, grant)
        log(directory, now, grant, "granted", expires_at=grant["expires_at"], goal=grant["goal"],
            classes=grant["classes"], armed_by=armed_by, replaces=grant["replaces"])
    lines = summary(grant, now)
    if grant["replaces"]:
        lines.append(f"replaces: {grant['replaces']}")
    lines.append(f"armed by: {armed_by['guard']} with `{armed_by['arguments']}`")
    print("\n".join(lines))
    return 0


def settle(directory: Path, project: Path, now: datetime) -> tuple[dict | None, dict | None]:
    """The latest grant after its end conditions are applied, and its goal read.

    A grant past its time is expired; a readable goal its compiler reads as
    terminal completes it. A project without autopilot state gets none.
    """
    if not (directory / GRANT).is_file():
        return None, None
    with locked(directory):
        return settle_locked(directory, project, now)


def settle_locked(directory: Path, project: Path, now: datetime) -> tuple[dict | None, dict | None]:
    """settle for a caller that holds the lock."""
    grant = current(directory, now)
    if not grant or not grant.get("goal") or inactive_reason(grant, now) is not None:
        return grant, None
    read = read_goal(project, grant["goal"])
    if read is not None and read["terminal"]:
        end_grant(directory, now, grant, "completed", now, completion={
            "by": "compiler", "goal_state": read["state"], "read_at": stamp(now),
            "compiler": grant["goal"]["end"]["compiler"]})
    return grant, read


def ended_line(grant: dict) -> str:
    line = f"autopilot: {grant.get('state')} {grant.get('id')} at {grant.get('ended_at')}"
    completion = grant.get("completion") or {}
    if completion.get("by") == "compiler":
        line += (f"; {completion['compiler']} read {grant['goal']['kind']}"
                 f" {grant['goal']['target']} as {completion['goal_state']}")
    return line


def cmd_check(args: argparse.Namespace, now: datetime) -> int:
    project = Path(args.project_root)
    grant, read = settle(state_dir(project), project, now)
    if grant is None:
        print("autopilot: inactive")
        return 1
    if grant.get("state") != "active":
        print(ended_line(grant))
        return 1
    reason = inactive_reason(grant, now)
    if reason:
        print(f"autopilot: inactive {grant['id']}: {reason}")
        return 1
    problem = binding_problem(grant.get("host"), grant["armed_by"].get("session_id"))
    if problem:
        print(f"autopilot: active {grant['id']} is {problem}; this session asks as usual")
        return 1
    print("\n".join(summary(grant, now, read, describe=True)))
    return 0


def cmd_status(args: argparse.Namespace, now: datetime) -> int:
    project = Path(args.project_root)
    directory = state_dir(project)
    coverage = guards()
    grant, read = settle(directory, project, now)
    counts = report_payload(directory, grant)["counts"] if grant else {"decisions": 0, "queued": 0}
    reason = inactive_reason(grant, now)
    active = reason is None
    problem = binding_problem(grant.get("host"), grant["armed_by"].get("session_id")) \
        if active else None
    if args.json:
        result = {"active": active, "grant": grant, "goal_read": read, "counts": counts,
                  "remaining_minutes": int((parse_stamp(grant["expires_at"]) - now).total_seconds()
                                           // 60) if active else 0,
                  "inactive_reason": reason, "bound_to": bound_line(grant) if grant else None,
                  "binding_problem": problem, **coverage}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if grant is None:
        lines = ["autopilot: inactive"]
    elif active:
        lines = summary(grant, now, read)
    elif grant.get("state") != "active":
        lines = [ended_line(grant)]
    else:
        lines = [f"autopilot: inactive {grant['id']}: {reason}"]
    if grant:
        lines.append(f"bound to: {bound_line(grant)}"
                     + (f" ({problem}; this session asks as usual)" if problem else ""))
        lines.append(f"decisions: {counts['decisions']}, queued: {counts['queued']}")
    lines.append(f"arming: {coverage['arming']}; question guard: {coverage['question_guard']}")
    print("\n".join(lines))
    return 0


def require_state(directory: Path, what: str) -> None:
    """Refuse a verb that needs a grant before it creates any runtime file."""
    if not (directory / GRANT).is_file():
        raise Refusal(what)


def active_grant(directory: Path, now: datetime, project: Path | None = None) -> dict:
    """The grant that governs this session, or a refusal that names why there is none.

    Given the project, the grant's end conditions, its goal included, are
    applied first, so a decision is never recorded after the goal is reached.
    """
    if project is None:
        grant = current(directory, now)
    else:
        grant, _read = settle_locked(directory, project, now)
    if grant and grant.get("state") != "active":
        raise Refusal(f"no active grant: {ended_line(grant).removeprefix('autopilot: ')}")
    reason = inactive_reason(grant, now)
    if reason:
        raise Refusal("no active grant" + (f"; {grant['id']} is inactive: {reason}" if grant else ""))
    problem = binding_problem(grant.get("host"), grant["armed_by"].get("session_id"))
    if problem:
        raise Refusal(f"grant {grant['id']} is {problem}")
    return grant


def declared_classes(policy: dict, names: list[str]) -> list[str]:
    declared = [entry["id"] for entry in policy["classes"]]
    for name in names:
        if name not in declared:
            raise Refusal(f"unknown class {name!r}; declared classes: {', '.join(declared)}")
    return list(dict.fromkeys(names))


def split_options(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(";") if item.strip()]


def cmd_record(args: argparse.Namespace, now: datetime) -> int:
    policy = load_policy()
    directory = state_dir(Path(args.project_root))
    options = split_options(args.options)
    require_state(directory, "no active grant")
    with locked(directory):
        grant = active_grant(directory, now, Path(args.project_root))
        # A decision is taken only when every class its recommended option touches is allowed.
        names = declared_classes(policy, args.class_)
        defaults = {entry["id"]: entry["default"] for entry in policy["classes"]}
        for name in names:
            if defaults[name] == "never":
                raise Refusal(f"class {name!r} is never delegated; queue the question")
            if name not in grant["classes"]:
                raise Refusal(f"class {name!r} is not allowed by grant {grant['id']};"
                              " queue the question")
        if options and args.choice not in options:
            raise Refusal("the choice must be one of the options")
        log(directory, now, grant, "decision", classes=names, question=args.question,
            options=options, choice=args.choice, reason=args.reason, target=args.target)
        number = sum(event.get("event") == "decision" for event in ledger(directory, grant["id"]))
    print(f"autopilot: recorded decision {number} under {grant['id']}; write it to {args.target}"
          f" marked {grant['id']}")
    return 0


def cmd_queue(args: argparse.Namespace, now: datetime) -> int:
    policy = load_policy()
    directory = state_dir(Path(args.project_root))
    options = split_options(args.options)
    require_state(directory, "no active grant")
    with locked(directory):
        grant = active_grant(directory, now, Path(args.project_root))
        names = declared_classes(policy, args.class_)
        if options and args.recommendation not in options:
            raise Refusal("the recommendation must be one of the options")
        log(directory, now, grant, "queued", classes=names, question=args.question,
            options=options, recommendation=args.recommendation, blocks=args.blocks or "")
        number = sum(event.get("event") == "queued" for event in ledger(directory, grant["id"]))
    doubt = all(name in grant["classes"] for name in names)
    print(f"autopilot: queued question {number} under {grant['id']}"
          + (", in doubt, though every class it names is allowed" if doubt else "")
          + "; continue the work that does not depend on it")
    return 0


def cmd_off(args: argparse.Namespace, now: datetime) -> int:
    directory = state_dir(Path(args.project_root))
    require_state(directory, "no grant to revoke")
    with locked(directory):
        grant = current(directory, now)
        if grant is None:
            raise Refusal("no grant to revoke")
        if grant.get("state") == "active":
            end_grant(directory, now, grant, "revoked", now)
    print("\n".join(report_lines(report_payload(directory, grant))))
    return 0


def cmd_complete(args: argparse.Namespace, now: datetime) -> int:
    directory = state_dir(Path(args.project_root))
    require_state(directory, "no active grant")
    with locked(directory):
        grant = active_grant(directory, now)
        completion = {"by": "complete", "evidence": args.evidence, "goal_state": None,
                      "compiler_agreed": None}
        read = read_goal(Path(args.project_root), grant["goal"]) if grant.get("goal") else None
        if read is not None:
            completion.update(goal_state=read["state"] if read["found"] else None,
                              compiler_agreed=read["terminal"], compiler=grant["goal"]["end"]["compiler"])
        end_grant(directory, now, grant, "completed", now, completion=completion)
    print("\n".join(report_lines(report_payload(directory, grant))))
    return 0


def cmd_report(args: argparse.Namespace, now: datetime) -> int:
    directory = state_dir(Path(args.project_root))
    require_state(directory, "no grant to report")
    with locked(directory):
        grant = current(directory, now)
    if args.grant and (grant is None or grant.get("id") != args.grant):
        granted = [event for event in ledger(directory, args.grant) if event.get("event") == "granted"]
        if not granted:
            raise Refusal(f"no grant {args.grant}")
        grant = {"id": args.grant, "state": "ended", "granted_at": granted[0]["time"],
                 "expires_at": granted[0].get("expires_at"), "goal": granted[0].get("goal"),
                 "classes": granted[0].get("classes", []), "armed_by": granted[0].get("armed_by", {})}
    if grant is None:
        raise Refusal("no grant to report")
    payload = report_payload(directory, grant)
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json
          else "\n".join(report_lines(payload)))
    return 0


# ---------------------------------------------------------------------------
# Hooks: fast, silent and never a reason for a session to fail
# ---------------------------------------------------------------------------


def typed_arguments(payload: dict, entry: str) -> str | None:
    """The arguments of the entry command the user typed, None for any other prompt."""
    name = payload.get("command_name")
    if isinstance(name, str):
        if name != entry or payload.get("expansion_type", "slash_command") != "slash_command":
            return None
        arguments = payload.get("command_args")
        if isinstance(arguments, str):
            return arguments.strip()
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return None
    first = prompt.lstrip().split("\n", 1)[0].rstrip()
    for token in (f"/{entry}", f"${entry}"):
        if first == token or first.startswith((token + " ", token + "\t")):
            return first[len(token):].strip()
    linked = re.match(r"\[\$" + re.escape(entry) + r"\]\([^)]*\)(?:\s+(.*))?$", first)
    return (linked.group(1) or "").strip() if linked else None


def hook_user_prompt(options: dict, payload: dict, now: datetime) -> None:
    entry = options.get("--entry")
    # A subagent's prompt is written by an agent, never typed by the user, and
    # a grant needs the session it binds to.
    session = payload.get("session_id")
    if not entry or payload.get("agent_id") or not isinstance(session, str) or not session:
        return
    arguments = typed_arguments(payload, entry)
    if arguments is None or arguments.split()[:1] != ["on"]:
        return
    directory = state_dir(Path(payload.get("cwd") or os.getcwd()))
    write_private(directory / ARMING, {
        "schema_version": 1, "armed_at": stamp(now), "host": options.get("--host"),
        "session_id": payload.get("session_id"), "event": payload.get("hook_event_name"),
        "entry": entry, "arguments": arguments})


def denial(grant: dict, now: datetime) -> str:
    command = command_line()
    classes = "; ".join(f"{name} ({text})" for name, text in class_descriptions(grant)) or "none"
    return (
        f"Autopilot grant {grant['id']}, armed in {bound_line(grant)}, is active until"
        f" {grant['expires_at']} ({span(parse_stamp(grant['expires_at']) - now)} left) and"
        f" allows: {classes}."
        f" Do not ask the user. Run `{command} check` first; if it exits 1, ask the user as"
        " usual. Classify the question by every effect of its recommended option: any never"
        " effect makes it never, an excluded effect the grant does not allow queues it, and"
        " doubt queues it. If every class it touches is allowed, take the recommended option"
        " (answer an open question with the recommendation you would offer), first run"
        f" `{command} record` with --class once per class it touches, --question, --options,"
        " --choice, --reason and --target, then apply it and write the decision into the"
        " governing document where the flow records the user's answer, marked"
        f" {grant['id']}. Otherwise run `{command} queue` with --class, --question, --options"
        " and --recommendation, and continue the work that does not depend on it; stop only"
        " when every remaining task waits on a queued question, then end with the queued list.")


def hook_pre_question(options: dict, payload: dict, now: datetime) -> None:
    directory = state_dir(Path(payload.get("cwd") or os.getcwd()))
    grant = read_json(directory / GRANT)
    if not is_active(grant, now):
        return
    # Another session, or a session of another host, is never governed by this grant.
    session = grant["armed_by"].get("session_id")
    if session and payload.get("session_id") != session:
        return
    host = options.get("--host")
    if host and grant.get("host") and host != grant["host"]:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": denial(grant, now)}}))


def run_hook(argv: list[str], stdin, now: datetime) -> int:
    try:
        verb, rest = argv[0], argv[1:]
        options = dict(zip(rest[::2], rest[1::2]))
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        if verb == "user-prompt":
            hook_user_prompt(options, payload, now)
        elif verb == "pre-question":
            hook_pre_question(options, payload, now)
    except Exception:
        # An internal error allows the prompt or the question: the session asks as usual.
        pass
    return 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    root.add_argument("--project-root", default=".")
    verbs = root.add_subparsers(dest="verb", required=True)
    on = verbs.add_parser("on", parents=[option_parser()],
                          help="start or replace a grant")
    on.add_argument("--host")
    on.set_defaults(handler=cmd_on)
    for name, handler in (("off", cmd_off), ("check", cmd_check)):
        verbs.add_parser(name).set_defaults(handler=handler)
    for name, handler in (("status", cmd_status), ("report", cmd_report)):
        verb = verbs.add_parser(name)
        verb.add_argument("--json", action="store_true")
        verb.set_defaults(handler=handler)
    verbs.choices["report"].add_argument("--grant")
    record = verbs.add_parser("record")
    record.add_argument("--choice", required=True)
    record.add_argument("--reason", required=True)
    record.add_argument("--target", required=True)
    record.set_defaults(handler=cmd_record)
    queue = verbs.add_parser("queue")
    queue.add_argument("--recommendation", required=True)
    queue.add_argument("--blocks")
    queue.set_defaults(handler=cmd_queue)
    for verb in (record, queue):
        verb.add_argument("--class", dest="class_", action="append", required=True,
                          help="a class the question touches; give it once per class")
        verb.add_argument("--question", required=True)
        verb.add_argument("--options")
    complete = verbs.add_parser("complete")
    complete.add_argument("--evidence", required=True)
    complete.set_defaults(handler=cmd_complete)
    return root


def main(argv: list[str] | None = None, *, now: datetime | None = None, stdin=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    moment = now or datetime.now(timezone.utc)
    if arguments[:1] == ["hook"]:
        return run_hook(arguments[1:] or [""], stdin or sys.stdin, moment)
    args = parser().parse_args(arguments)
    try:
        return args.handler(args, moment)
    except Refusal as exc:
        print(f"autopilot: refused: {exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"autopilot: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
