"""Bounded recovery of explicitly registered host sessions after transport errors."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import atomic_file

CHILDREN = {}
PROMPT = "Resume the interrupted assigned work from its current state. Preserve its scope, approvals and verification requirements."


def read_state(directory):
    path = directory / "recovery.json"
    if path.is_symlink():
        raise ValueError("recovery state cannot be a symlink")
    return json.loads(path.read_text()) if path.exists() else {"lanes": {}}


def save(directory, state):
    atomic_file.replace_text(directory / "recovery.json", json.dumps(state, sort_keys=True) + "\n")


def register(directory, project, grant, session, transcript, role, pid):
    uuid.UUID(session)
    if type(pid) is not int or pid <= 0:
        raise ValueError("register the host process id")
    path = Path(transcript).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
        raise ValueError("register a regular host transcript, without symlinks")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    identities = {str(record.get(key)) for record in records if isinstance(record, dict)
                  for key in ("session_id", "sessionId", "thread_id") if record.get(key)}
    identities.update(str(record["payload"].get("id")) for record in records if isinstance(record, dict)
                      and record.get("type") == "session_meta" and isinstance(record.get("payload"), dict))
    if session not in identities:
        raise ValueError("transcript does not identify the registered host session")
    for record in records:
        payload = record.get("payload", record) if isinstance(record, dict) else {}
        if isinstance(payload, dict) and payload.get("cwd") and Path(payload["cwd"]).resolve() != project.resolve():
            raise ValueError("transcript belongs to another workspace")
    state = read_state(directory)
    old = state["lanes"].get(session)
    entry = {"grant": grant["id"], "project": str(project.resolve()), "transcript": str(path),
             "role": role, "source_pid": pid, "attempts": 0}
    if old and any(old.get(key) != entry[key] for key in ("grant", "project", "transcript", "role", "source_pid")):
        raise ValueError("registered recovery session cannot be rebound")
    state["lanes"][session] = old or entry
    save(directory, state)
    return state["lanes"][session]


def terminal_error(path, codes):
    if path.is_symlink() or not path.is_file():
        return None
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 1048576))
        lines = [line for line in stream.read().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        event = json.loads(lines[-1])
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict):
        return None
    payload = event.get("payload", event)
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type", event.get("type"))
    if kind not in {"error", "turn.failed"} and not (kind == "result" and payload.get("is_error") is True):
        return None
    text = json.dumps(payload.get("error", payload.get("errors", payload.get("message", ""))))
    if not any(re.search(r"(?<![A-Za-z])" + re.escape(code) + r"(?![A-Za-z])", text, re.I) for code in codes):
        return None
    return {"fingerprint": hashlib.sha256(lines[-1]).hexdigest(), "time_ns": path.stat().st_mtime_ns}


def wrote_after(project, time_ns, transcript):
    listed = subprocess.run(["git", "-C", str(project), "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"], capture_output=True, check=True).stdout.split(b"\0")
    for raw in listed:
        if not raw:
            continue
        path = project / os.fsdecode(raw)
        if path.absolute() == transcript.absolute():
            continue
        if path.exists() and path.stat().st_mtime_ns > time_ns:
            return True
    head = subprocess.run(["git", "-C", str(project), "symbolic-ref", "-q", "HEAD"],
                          capture_output=True, text=True, check=False).stdout.strip()
    for ref in ["HEAD", *([head] if head else [])]:
        raw = subprocess.run(["git", "-C", str(project), "rev-parse", "--git-path", ref],
                             capture_output=True, text=True, check=True).stdout.strip()
        path = Path(raw) if Path(raw).is_absolute() else project / raw
        if path.exists() and path.stat().st_mtime_ns > time_ns:
            return True
    return False



def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def heal(directory, project, grant, policy, launch=None):
    import transport_recovery_host as host
    state = read_state(directory)
    outcomes = []
    for session, lane in state["lanes"].items():
        if lane.get("grant") != grant["id"] or lane.get("project") != str(project.resolve()):
            continue
        if process_alive(lane["source_pid"]):
            continue
        child = CHILDREN.get(session)
        if child is not None and child.poll() is None:
            continue
        if child is not None:
            CHILDREN.pop(session)
        transcript = Path(lane.get("last_output", lane["transcript"]))
        error = terminal_error(transcript, policy["transport_error_codes"])
        if error is None or error["fingerprint"] == lane.get("last_error"):
            continue
        if lane["attempts"] >= policy["transport_max_attempts"]:
            lane["last_error"] = error["fingerprint"]
            outcomes.append({"session": session, "state": "needs_owner", "reason": "transport retries exhausted"})
            continue
        if wrote_after(project, error["time_ns"], transcript):
            lane["last_error"] = error["fingerprint"]
            outcomes.append({"session": session, "state": "needs_owner", "reason": "workspace changed after failure"})
            continue
        command = host.resume_command(session, PROMPT)
        output = directory / ("recovery-" + session + "-" + str(lane["attempts"] + 1) + ".jsonl")
        if output.exists() or output.is_symlink():
            raise ValueError("recovery output already exists")
        with output.open("xb") as stream:
            child = (launch or subprocess.Popen)(command, cwd=project, stdout=stream, stderr=subprocess.STDOUT,
                                                 **host.spawn_options())
        CHILDREN[session] = child
        lane.update(attempts=lane["attempts"] + 1, last_error=error["fingerprint"], last_output=str(output))
        outcomes.append({"session": session, "state": "resumed", "attempt": lane["attempts"]})
    save(directory, state)
    return outcomes


def start(script, project, grant, directory):
    import transport_recovery_host as host
    host.resume_command(grant.get("armed_by", {}).get("session_id") or "capability-check", PROMPT)
    output = directory / "recovery-watch.log"
    with output.open("ab") as stream:
        return subprocess.Popen([os.fspath(__import__("sys").executable), os.fspath(script),
                                 "--project-root", str(project), "watch-recovery", "--grant", grant["id"]],
                                stdout=stream, stderr=subprocess.STDOUT, **host.spawn_options()).pid
