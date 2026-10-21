"""Native host recovery, using existing session wakeups and no external runtime."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

import autopilot
import autopilot_recovery
import file_lock

STATE = "native-recovery.json"
PREFIX = "AUTOPILOT_TRANSPORT_RECOVERY"


def load(directory: Path) -> dict:
    value = autopilot.read_json(directory / STATE)
    return value or {"sessions": {}, "attempts": {}}


def store(directory: Path, value: dict) -> None:
    autopilot.write_private(directory / STATE, value)


def active(project: Path, host: str, owner: str, now: datetime) -> tuple[Path, dict]:
    directory = autopilot.state_dir(project)
    if not (directory / autopilot.GRANT).is_file():
        raise autopilot.Refusal("no active native recovery grant")
    with autopilot.locked(directory):
        grant = autopilot.active_grant(directory, now, bound=False)
    if (grant.get("transport_recovery") is not True or grant.get("host") != host
            or grant.get("armed_by", {}).get("session_id") != owner):
        raise autopilot.Refusal("native recovery belongs to the user-armed host session")
    return directory, grant


def identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("native session id is missing")
    uuid.UUID(value)
    return value


def remember(payload: dict, host: str, now: datetime) -> None:
    owner = identity(payload.get("session_id"))
    project = Path(payload["cwd"]).resolve()
    directory, grant = active(project, host, owner, now)
    child = payload.get("agent_id")
    if child is not None and host == "codex":
        identity(child)
    elif child is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(child)):
        raise ValueError("native agent id is invalid")
    key = str(child or owner)
    with autopilot.locked(directory):
        state = load(directory)
        state["sessions"][key] = {"id": key, "owner": owner, "host": host,
            "project": str(project), "grant": grant["id"], "role": payload.get("agent_type") or "coordinator"}
        store(directory, state)


def transport_error(message: object, policy: dict) -> bool:
    if not isinstance(message, str):
        return False
    return any(re.search(r"(?<![A-Za-z])" + re.escape(code) + r"(?![A-Za-z])", message, re.I)
               for code in policy["transport_error_codes"]) or bool(re.search(r"(?:HTTP|status|Error:)\s*(?:502|503|504)\b", message, re.I))


def claude_failure(path: Path, policy: dict) -> dict | None:
    """Only a host-marked API error, never assistant prose, can request a retry."""
    if path.is_symlink() or not path.is_file():
        return None
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 1048576))
        lines = stream.read().splitlines()
    for raw in reversed(lines):
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "assistant":
            if event.get("isApiErrorMessage", event.get("is_api_error_message")) is not True:
                return None
            content = event.get("message", {}).get("content", [])
            text = " ".join(row.get("text", "") for row in content if isinstance(row, dict))
            if transport_error(text, policy):
                moment = event.get("timestamp")
                try:
                    stamp = int(datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
                except (AttributeError, ValueError):
                    stamp = path.stat().st_mtime_ns
                return {"fingerprint": hashlib.sha256(raw).hexdigest(), "time_ns": stamp}
            return None
        if kind == "user":
            return None
    return None


def reserve(directory: Path, grant: dict, session: str, fingerprint: str, now: datetime) -> dict:
    state = load(directory)
    key = grant["id"] + ":" + session
    attempt = state["attempts"].get(key, {"count": 0})
    if attempt.get("fingerprint") == fingerprint:
        return {"state": "already_requested"}
    if attempt["count"] >= autopilot.load_policy()["transport_max_attempts"]:
        attempt["fingerprint"] = fingerprint
        state["attempts"][key] = attempt
        store(directory, state)
        autopilot.log(directory, now, grant, "transport_recovery", session=session,
                      state="needs_owner", reason="transport retries exhausted")
        return {"state": "needs_owner", "reason": "transport retries exhausted"}
    attempt = {"count": attempt["count"] + 1, "fingerprint": fingerprint, "requested_at": autopilot.stamp(now)}
    state["attempts"][key] = attempt
    store(directory, state)
    autopilot.log(directory, now, grant, "transport_recovery", session=session, state="native_resume_requested", attempt=attempt["count"])
    return {"state": "resume", "attempt": attempt["count"]}


def issue_permit(directory: Path, grant: dict, session: str, project: Path, failed_at_ns: int, now: datetime) -> str:
    ticket = uuid.uuid4().hex
    message = PREFIX + ":" + ticket + " " + autopilot_recovery.PROMPT
    with autopilot.locked(directory):
        state = load(directory)
        state.setdefault("permits", {})[ticket] = {"thread_id": session, "grant": grant["id"],
            "owner": grant["armed_by"]["session_id"], "host": grant["host"], "project": str(project),
            "failed_at_ns": failed_at_ns, "created_at": autopilot.stamp(now), "message": message, "consumed": False}
        store(directory, state)
    return message


def watch(payload: dict, *, clock=None, sleep=None) -> int:
    """Exit 2 through asyncRewake to wake this existing Claude conversation."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or time.sleep
    owner = identity(payload.get("session_id"))
    project = Path(payload["cwd"]).resolve()
    policy = autopilot.load_policy()
    started = clock()
    waiting_for_on = "--recover-transport" in str(payload.get("command_args", ""))
    while True:
        try:
            directory, grant = active(project, "claude", owner, clock())
            if not waiting_for_on or autopilot.parse_stamp(grant["granted_at"]) >= started.replace(microsecond=0):
                break
        except (autopilot.Refusal, OSError, ValueError):
            if not waiting_for_on:
                return 0
        if (clock() - started).total_seconds() >= policy["native_arming_wait_seconds"]:
            return 0
        sleep(policy["native_poll_seconds"])
    remember(payload, "claude", clock())
    session = str(payload.get("agent_id") or owner)
    transcript = Path(payload["transcript_path"])
    if payload.get("agent_id"):
        transcript = Path(payload.get("agent_transcript_path") or transcript.with_suffix("") / "subagents" / ("agent-" + session + ".jsonl"))
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).resolve()
    resolved = transcript.resolve()
    if config not in resolved.parents:
        return 0
    for part in (transcript, *transcript.parents):
        if part.is_symlink():
            target = part.resolve()
            if target == config or config in target.parents:
                return 0
    transcript = resolved
    key = hashlib.sha256((grant["id"] + session).encode()).hexdigest()
    lock_path = directory / ("native-watch-" + key + ".lock")
    if lock_path.is_symlink():
        return 0
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    held = False
    try:
        if not file_lock.try_lock(descriptor):
            return 0
        held = True
        while (clock() - started).total_seconds() < policy["native_watch_seconds"]:
            try:
                current_dir, current = active(project, "claude", owner, clock())
            except (autopilot.Refusal, OSError, ValueError):
                return 0
            if current["id"] != grant["id"]:
                return 0
            error = claude_failure(transcript, policy)
            if error:
                sleep(policy["native_retry_delay_seconds"])
                current_dir, current = active(project, "claude", owner, clock())
                if current["id"] != grant["id"]:
                    return 0
                if claude_failure(transcript, policy) != error:
                    continue
                if autopilot_recovery.wrote_after(project, error["time_ns"], transcript):
                    return 0
                with autopilot.locked(current_dir):
                    result = reserve(current_dir, current, session, error["fingerprint"], clock())
                if result["state"] == "resume":
                    target = "Retry the interrupted assigned work in this same conversation. "
                    if session != owner:
                        message = issue_permit(current_dir, current, session, project, error["time_ns"], clock())
                        target = (f"Resume only the existing subagent with id {session} using SendMessage "
                                  f"with this exact message: {json.dumps(message)}. "
                                  "Do not perform its assignment in the coordinator. ")
                    print(f"{PREFIX}: " + target +
                          "Preserve its context, role, permissions and existing approvals. "
                          "Do not start a replacement agent or infer merge/release approval.", file=sys.stderr)
                    return 2
                if result["state"] == "needs_owner":
                    autopilot.notify_owner("Transport recovery needs attention", "The native session exhausted its recovery attempts.")
                return 0
            sleep(policy["native_poll_seconds"])
    finally:
        if held:
            file_lock.unlock(descriptor)
        os.close(descriptor)
    return 0


def enrollment(project: Path, grant: dict) -> dict | None:
    """The Codex app installs this heartbeat using its built-in tool, not a daemon."""
    if grant.get("host") != "codex" or not grant.get("transport_recovery"):
        return None
    if not grant.get("armed_by", {}).get("session_id"):
        return None
    owner = identity(grant["armed_by"]["session_id"])
    args = [sys.executable, str(Path(autopilot.__file__).resolve()), "--project-root", str(project.resolve()), "native-plan", "--grant", grant["id"]]
    command = __import__("subprocess").list2cmdline(args) if os.name == "nt" else shlex.join(args)
    prompt = (f"{PREFIX} {grant['id']}. Run {command} first. If inactive, pause this heartbeat and stop. "
        "Read only the registered sessions it lists with read_thread (turnLimit 2, includeOutputs false), save their structured snapshots "
        "in the grant's runtime directory, and rerun native-plan --snapshots <file>. "
        "Resume only its returned exact agent IDs, once each, using the host's existing-agent follow-up tool "
        "(send_input or followup_task) with the returned message, without interrupting a running agent. "
        "For an action with coordinator true, do not send a message to yourself: this heartbeat already resumed this thread; continue its pending work. "
        "The heartbeat itself continues the coordinator in its existing context; recover failed child "
        "agents with their own context, never spawn replacements. A running, completed, cancelled or "
        "non-transport failure is never retried. Preserve permissions and all existing approval boundaries. "
        "This recovery instruction grants no merge or release authority. Stay quiet when there is "
        "nothing to recover; notify only when recovery exhausts its attempts or needs the owner.")
    return {"tool": "mcp__codex_app__automation_update", "arguments": {"mode": "create", "kind": "heartbeat",
            "name": "Autopilot transport recovery", "targetThreadId": owner, "prompt": prompt,
            "rrule": f"FREQ=MINUTELY;INTERVAL={max(1, int(autopilot.load_policy()['transport_interval_seconds']) // 60)}",
            "status": "ACTIVE"}, "expires_at": grant["expires_at"], "grant": grant["id"]}


def plan(project: Path, snapshots: list[dict] | None, now: datetime, requested_grant: str) -> dict:
    directory = autopilot.state_dir(project)
    if not (directory / autopilot.GRANT).is_file():
        return {"active": False, "reason": "no grant"}
    with autopilot.locked(directory):
        grant = autopilot.active_grant(directory, now, bound=False)
    if grant["id"] != requested_grant:
        return {"active": False, "reason": "grant replaced"}
    owner = identity(grant.get("armed_by", {}).get("session_id"))
    active(project, "codex", owner, now)
    state = load(directory)
    sessions = {owner: {"id": owner, "owner": owner, "host": "codex", "project": str(project.resolve()), "grant": grant["id"]}}
    sessions.update({key: row for key, row in state["sessions"].items()
                     if row.get("grant") == grant["id"] and row.get("host") == "codex"})
    if snapshots is None:
        return {"active": True, "owner": owner, "grant": grant["id"], "inspect": sorted(sessions),
                "runtime": str(directory), "enrollment": enrollment(project, grant)}
    if not isinstance(snapshots, list):
        raise ValueError("native snapshots must be a list of structured read_thread results")
    actions, needs_owner = [], []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
            raise ValueError("native snapshots require the host's schemaVersion 1 result")
        thread = snapshot.get("thread", {})
        session = thread.get("id")
        if session not in sessions or Path(thread.get("cwd", "")).resolve() != Path(sessions[session]["project"]).resolve():
            raise ValueError("native snapshot is outside this grant's registered sessions")
        status = thread.get("status", {})
        if status.get("type") == "active" and session != owner:
            continue
        turns = snapshot.get("turns", [])
        # The scheduler's own active turn is not the failed turn it is checking.
        terminal = next((turn for turn in turns if turn.get("status") != "inProgress"), None)
        if not terminal or terminal.get("status") != "failed":
            continue
        error = terminal.get("error") or {}
        if not isinstance(error, dict) or not transport_error(error.get("message"), autopilot.load_policy()):
            continue
        stamp = terminal.get("completedAt")
        if not isinstance(stamp, (int, float)):
            continue
        if autopilot_recovery.wrote_after(Path(sessions[session]["project"]), int(stamp * 1_000_000_000), project / ".native-recovery-snapshot"):
            continue
        fingerprint = hashlib.sha256(json.dumps([terminal.get("id"), error], sort_keys=True).encode()).hexdigest()
        with autopilot.locked(directory):
            result = reserve(directory, grant, session, fingerprint, now)
        if result["state"] == "needs_owner":
            needs_owner.append({"thread_id": session, "reason": result["reason"]})
        if result["state"] == "resume":
            message = issue_permit(directory, grant, session, Path(sessions[session]["project"]), int(stamp * 1_000_000_000), now)
            actions.append({"thread_id": session, "owner": owner, "coordinator": session == owner,
                            "attempt": result["attempt"], "message": message})
    return {"active": True, "grant": grant["id"], "actions": actions, "needs_owner": needs_owner}


def supports_enrollment(grant: dict) -> bool:
    return grant.get("host") == "codex"


def dispatch_guard(payload: dict, now: datetime) -> str | None:
    data = payload.get("tool_input", {})
    if not isinstance(data, dict):
        return None
    prompt = data.get("message", data.get("prompt", data.get("content", "")))
    if not isinstance(prompt, str) or not prompt.startswith(PREFIX + ":"):
        return None
    try:
        ticket = prompt.split(" ", 1)[0].split(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{32}", ticket):
            raise ValueError("invalid recovery permit")
        directory = autopilot.state_dir(Path(payload["cwd"]))
        with autopilot.locked(directory):
            state = load(directory)
            permit = state.get("permits", {}).get(ticket)
            targets = [data[key] for key in ("id", "target", "threadId", "to", "recipient") if key in data]
            target = targets[0] if targets else None
            if any(item != target for item in targets):
                raise ValueError("conflicting native dispatch fields")
            # Claude includes a truncated legacy `content` preview beside the full `message`.
            if not permit or permit["consumed"] or target != permit["thread_id"]:
                raise ValueError("missing, consumed or mismatched recovery permit")
            if prompt != permit["message"] or data.get("interrupt") is True or data.get("model") or data.get("thinking"):
                raise ValueError("recovery message changed or would interrupt the agent")
            if payload.get("session_id") != permit["owner"]:
                raise ValueError("recovery dispatch belongs to another coordinator")
            grant = autopilot.active_grant(directory, now, bound=False)
            if (grant["id"] != permit["grant"] or not grant.get("transport_recovery")
                    or grant.get("host") != permit["host"] or grant.get("armed_by", {}).get("session_id") != permit["owner"]):
                raise ValueError("recovery grant ended or changed")
            age = (now - autopilot.parse_stamp(permit["created_at"])).total_seconds()
            if not 0 <= age <= autopilot.load_policy()["native_permit_ttl_seconds"]:
                raise ValueError("recovery permit expired")
            if autopilot_recovery.wrote_after(Path(permit["project"]), permit["failed_at_ns"], Path(permit["project"]) / ".native-recovery-snapshot"):
                raise ValueError("workspace changed after the failed turn")
            permit["consumed"] = True
            store(directory, state)
        return ""
    except (autopilot.Refusal, OSError, ValueError, KeyError, TypeError) as error:
        return "Native recovery refused: " + str(error)
