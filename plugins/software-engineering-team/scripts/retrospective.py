#!/usr/bin/env python3
"""Bounded automatic local observation, separate from granted remediation.

Events and reports stay in the common Git queue. A detached observer reads local
receipts and host usage metadata only. It cannot run tests, models or publish.
An OS lock owns collection. A launch reservation bounds processes and closes
the enqueue/shutdown race. Durable inbox writes never wait for the queue lock.
The next event or session resumes interrupted work.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import atomic_file
import file_lock
import project_paths
import work_queue

POLICY = Path(__file__).resolve().parent.parent / "skill-content/retrospective/data/automatic-policy.json"
EVENT_KEYS = frozenset({"kind", "operation", "task", "head", "receipt_path", "receipt_sha256",
                        "session_id", "transcript_path", "host", "tool_use_id", "status", "seconds",
                        "error_category", "turn_id", "agent_id", "tool", "duration_ms", "exit_code",
                        "transcript_status", "source_id", "requested_at", "approved_at", "merged_at", "ready_at"})
RECEIPT_KEYS = ("task", "head", "status", "ok", "started_at", "finished_at", "selection", "proof_state",
                "source_delivery", "application_source", "packet_sha256", "binding_sha256", "host")
METHOD_KEYS = ("load_source", "loaded_at", "skill_sha256", "recorded_at", "completion_source",
               "evidence_sha256", "source_path", "content_sha256", "source_scope")
NATIVE_FAILURE_LIMIT = 20


def policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _lock(directory: Path) -> int:
    path = directory / "observer.lock"
    if path.is_symlink():
        raise ValueError("observer lock cannot be a symbolic link")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("observer lock must be a regular file")
    return descriptor


def _pending(state: dict) -> list[dict]:
    return [job for job in state["jobs"] if work_queue.automatic_observation(job)
            and job["state"] in {"pending", "claimed", "needs_reconciliation"}]


def _publish(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{uuid.uuid4().hex}.pending")
    try:
        atomic_file.replace_bytes(temporary, data, mode=0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                # The observer may have consumed the duplicate after EEXIST.
                os.link(temporary, path.with_name(f"{path.stem}-{uuid.uuid4().hex}.json"))
            else:
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("inbox event must be a regular file")
    finally:
        temporary.unlink(missing_ok=True)


def _has_work(directory: Path) -> bool:
    if next((directory / "inbox").glob("*.json"), None):
        return True
    state_path = directory / "state.json"
    if state_path.is_symlink():
        raise ValueError("queue state cannot be a symbolic link")
    if not state_path.is_file():
        return False
    if state_path.stat().st_size > policy()["receipt_max_bytes"]:
        return True
    return bool(_pending(json.loads(state_path.read_text(encoding="utf-8"))))


def _import_inbox(root: Path, directory: Path, limit: int, *, deadline: float | None = None) -> None:
    for path in sorted((directory / "inbox").glob("*.json"))[:limit]:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if path.is_symlink() or path.stat().st_size > work_queue.MAX_BYTES + 1024:
            raise ValueError("invalid retrospective inbox event")
        item = json.loads(path.read_text(encoding="utf-8"))
        work_queue.enqueue(root, "retro", item["key"], item["payload"], enqueued_at=item["enqueued_at"])
        path.unlink()


def _snapshot(root: Path, event: dict, limit: int) -> dict:
    if not event.get("receipt_path"):
        return {}
    path = (root / event["receipt_path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > limit:
        return {"receipt_state": "unavailable"}
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        return {"receipt_state": "over_budget"}
    digest = hashlib.sha256(data).hexdigest()
    if event.get("receipt_sha256") and digest != event["receipt_sha256"]:
        return {"receipt_state": "changed", "observed_sha256": digest}
    record = json.loads(data)
    if not isinstance(record, dict):
        return {"receipt_state": "unsupported"}
    snapshot = {key: record[key] for key in RECEIPT_KEYS if key in record}
    snapshot.update(receipt_state="observed", receipt_sha256=digest)
    snapshot["commands"] = [{key: row[key] for key in ("exit_code", "seconds", "queue_seconds", "timed_out",
                                                      "started_at", "finished_at") if key in row}
                            for row in record.get("commands", []) if isinstance(row, dict)]
    snapshot["methods"] = [{"name": name, **{key: value[key] for key in METHOD_KEYS if key in value}}
                           for name, value in record.items() if isinstance(value, dict) and "load_source" in value]
    for method in snapshot["methods"]:
        action = record[method["name"]].get("action", {})
        if isinstance(action, dict):
            method["action"] = {key: action[key] for key in ("head", "ok", "exit_code", "graph_sha256",
                              "stdout_sha256", "stderr_sha256", "seconds") if key in action}
    result = record.get("result", {})
    if isinstance(result, dict):
        snapshot["session_id"] = result.get("session_id")
        snapshot["usage"] = result.get("usage")
        report = result.get("report", {})
        if isinstance(report, dict):
            snapshot["finding_counts"] = dict(Counter(row.get("severity", "unknown")
                for row in report.get("findings", []) if isinstance(row, dict)))
            snapshot["assessed_sources"] = [row.get("source_id") for row in report.get("checks", [])
                                             if isinstance(row, dict)]
        snapshot["observed_model"] = result.get("observed_model")
        snapshot["observed_effort"] = result.get("observed_effort")
    requested = record.get("requested", {})
    if isinstance(requested, dict):
        snapshot["requested"] = {key: requested[key] for key in ("model", "effort", "profile", "access", "agent") if key in requested}
    return snapshot


def _task_snapshots(root: Path, event: dict, limit: int) -> dict:
    task = event.get("task", "")
    if not isinstance(task, str) or not re.fullmatch(r"TK-[0-9]{3,}", task):
        return {}
    runtime = project_paths.runtime_root(root)
    paths = {"proof": runtime / "proof" / f"{task}.json", "methods": runtime / "review" / f"{task}.methods.json"}
    snapshots = {}
    for name, path in paths.items():
        if path.is_file():
            try:
                snapshots[name] = _snapshot(root, {"receipt_path": str(path)}, limit)
            except (OSError, ValueError):
                snapshots[name] = {"receipt_state": "unavailable"}
    heads = {event.get("head"), snapshots.get("proof", {}).get("head")}
    snapshots["reviews"] = []
    for head in sorted(value for value in heads if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)):
        path = runtime / "review" / f"{head}.native-review.json"
        if path.is_file():
            try:
                snapshots["reviews"].append(_snapshot(root, {"receipt_path": str(path)}, limit))
            except (OSError, ValueError):
                snapshots["reviews"].append({"receipt_state": "unavailable"})
    return snapshots


def emit(root: Path, event: dict) -> dict:
    """Persist a sanitized event before requesting collection, without waiting for it."""
    try:
        if set(event) - EVENT_KEYS or event.get("kind") not in {"completion", "failure", "session"}:
            raise ValueError("unsupported retrospective event")
        if not isinstance(event.get("operation"), str) or not event["operation"]:
            raise ValueError("retrospective event needs an operation")
        settings = policy()
        received_at = time.time()
        refresh_after = None
        if event.get("host") and event.get("session_id") and all(event.get(key) == value for key, value in {
                "kind": "session", "operation": "PostToolUse", "tool": "Bash", "status": "unknown"}.items()):
            # One immutable refresh intent scans after the window's final command.
            bucket = int(received_at // settings["refresh_seconds"])
            event = {key: event[key] for key in ("kind", "host", "operation", "tool", "status",
                     "session_id", "agent_id") if key in event}
            event["source_id"] = f"refresh:{bucket}"
            refresh_after = (bucket + 1) * settings["refresh_seconds"]
        try:
            snapshot = _snapshot(root, event, settings["receipt_max_bytes"])
        except (OSError, ValueError) as error:
            snapshot = {"receipt_state": "unavailable", "error_category": type(error).__name__}
        payload = {"origin": work_queue.AUTOMATIC_RETRO_ORIGIN, "worktree": str(root.resolve()),
                   "event": copy.deepcopy(event), "snapshot": snapshot}
        payload["task_evidence"] = _task_snapshots(root, event, settings["receipt_max_bytes"])
        if refresh_after is not None:
            payload["refresh_after"] = refresh_after
        if event.get("transcript_path") and refresh_after is None:
            try:
                metadata = Path(event["transcript_path"]).stat()
                payload["transcript_revision"] = {"size": metadata.st_size, "mtime_ns": metadata.st_mtime_ns}
            except OSError:
                payload["transcript_revision"] = None
        identity = hashlib.sha256(encoded(payload)).hexdigest()
        # Queue enqueued-at is the compiler timestamp, outside the immutable payload.
        payload = work_queue._object(payload)
        directory = work_queue._directory(root, timeout=0.2)
        inbox = atomic_file.real_directory(directory, Path("inbox"))
        key = f"observation:{identity}"
        _publish(inbox / f"{identity}.json", encoded({"key": key, "payload": payload, "enqueued_at": received_at}))
        event_id = hashlib.sha256(f"retro\n{key}".encode("utf-8")).hexdigest()
        return {"queued": True, "event_id": event_id, **resume(root, directory=directory)}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return {"queued": False, "observer_started": False, "error_category": type(error).__name__}


def resume(root: Path, *, directory: Path | None = None) -> dict:
    """Start at most one observer, recovering a dead worker on the next trigger."""
    descriptor = None
    token = None
    try:
        directory = directory or work_queue._directory(root, timeout=0.2)
        descriptor = _lock(directory)
        if not file_lock.try_lock(descriptor):
            return {"observer_started": False, "status": "running"}
        if not _has_work(directory):
            os.close(descriptor)
            descriptor = None
            if next((directory / "inbox").glob("*.json"), None):
                return resume(root, directory=directory)
            return {"observer_started": False, "status": "idle"}
        reservation = directory / "observer.json"
        if reservation.is_symlink():
            raise ValueError("observer reservation cannot be a symbolic link")
        current = json.loads(reservation.read_text(encoding="utf-8")) if reservation.is_file() else {}
        if current.get("launch_until", 0) > time.time():
            return {"observer_started": False, "status": "starting"}
        token = uuid.uuid4().hex
        atomic_file.replace_bytes(reservation, encoded({"token": token, "launch_until": time.time() + policy()["launch_seconds"]}), mode=0o600)
        options = {"start_new_session": True} if os.name == "posix" else {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        worktrees = subprocess.run(["git", "--git-dir", str(directory.parents[2]), "worktree", "list", "--porcelain"],
                                  capture_output=True, text=True, check=True, timeout=0.2).stdout
        anchor = next((Path(line[9:]) for line in worktrees.splitlines()
                       if line.startswith("worktree ") and Path(line[9:]).is_dir()), root.resolve())
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--project", str(anchor),
                                    "--token", token], cwd=anchor, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, **options)
        threading.Thread(target=process.wait, daemon=True).start()
        return {"observer_started": True, "status": "starting", "pid": process.pid}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if token and directory:
            (directory / "observer.json").unlink(missing_ok=True)
        return {"observer_started": False, "error_category": type(error).__name__}
    finally:
        if descriptor is not None:
            os.close(descriptor)


def elapsed(start, end):
    try:
        seconds = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                   datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        return seconds if seconds >= 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def remember_session(sessions: dict, key: tuple, values: dict, at: float) -> None:
    row = sessions.setdefault(key, {})
    newest = at >= row.get("last_observed_at", 0)
    for name, value in values.items():
        if newest or name not in row:
            row[name] = value
    row["last_observed_at"] = max(at, row.get("last_observed_at", 0))


def task_records(rows: list[dict]) -> dict:
    """One task identity per shared Git queue, retaining every source checkout."""
    tasks = {}
    for previous in sorted(rows, key=lambda row: row.get("last_observed_at", 0)):
        row = tasks.setdefault(previous["task"], {"task": previous["task"],
            "worktree": previous["worktree"], "worktrees": [], "receipts": {}, "methods": {}})
        row["worktrees"] = sorted(set(row["worktrees"]) | set(previous.get("worktrees", [])) | {previous["worktree"]})
        row["receipts"].update(previous.get("receipts", {}))
        row["methods"].update(previous.get("methods", {}))
        row.update({key: value for key, value in previous.items()
                    if key not in {"worktree", "worktrees", "receipts", "methods"} and value is not None})
    return tasks


def tracking(directory: Path, jobs: list[dict]) -> dict:
    registry = directory / "tracking.json"
    if registry.is_symlink():
        raise ValueError("session registry cannot be a symbolic link")
    previous = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else {}
    tasks = task_records(previous.get("tasks", []))
    sessions = {(row["host"], row["session_id"], row.get("agent_id", "")): row for row in previous.get("sessions", [])}
    for job in jobs:
        payload, at = job["payload"], job["events"][0]["at"]
        event, worktree = payload["event"], payload["worktree"]
        if event.get("session_id") and event.get("host"):
            key = event["host"], event["session_id"], event.get("agent_id", "")
            values = {"host": key[0], "session_id": key[1], "agent_id": key[2], "worktree": worktree}
            if event.get("transcript_path"):
                values["path"] = event["transcript_path"]
            remember_session(sessions, key, values, at)
        task = event.get("task")
        if not task:
            continue
        row = tasks.setdefault(task, {"task": task, "worktree": worktree, "receipts": {}, "methods": {}})
        row["worktrees"] = sorted(set(row.get("worktrees", [])) | {row["worktree"], worktree})
        newest = at >= row.get("last_observed_at", 0)
        if newest:
            row.update(last_observed_at=at, last_operation=event["operation"])
        for key in ("requested_at", "approved_at", "merged_at", "ready_at", "head"):
            if event.get(key) is not None and (newest or key not in row):
                row[key] = event[key]
        evidence = payload.get("task_evidence", {})
        receipts = [payload.get("snapshot", {}), evidence.get("proof", {}), evidence.get("methods", {}), *evidence.get("reviews", [])]
        for receipt in receipts:
            if receipt.get("receipt_sha256"):
                row["receipts"][receipt["receipt_sha256"]] = receipt
            for method in receipt.get("methods", []):
                if newest or method["name"] not in row["methods"]:
                    row["methods"][method["name"]] = method
            if receipt.get("session_id") and receipt.get("host"):
                key = receipt["host"], receipt["session_id"], ""
                requested = receipt.get("requested", {})
                remember_session(sessions, key, {"host": key[0], "session_id": key[1], "worktree": worktree,
                    "task": task, "role": requested.get("agent", "reviewer"), "model": requested.get("model"),
                    "effort": requested.get("effort"), "packet_sha256": receipt.get("packet_sha256"),
                    "usage": receipt.get("usage"), "finished_at": receipt.get("finished_at")}, at)
    settings = policy()
    recent_tasks = sorted(tasks.values(), key=lambda row: row["last_observed_at"], reverse=True)[:settings["recent_tasks"]]
    recent_sessions = sorted(sessions.values(), key=lambda row: row.get("last_observed_at", 0), reverse=True)[:settings["recent_sessions"]]
    deliveries = [{"name": name, "task": row["task"], "source_sha256": method.get("skill_sha256"),
                   "content_sha256": method.get("content_sha256") or
                       (method.get("skill_sha256") if method.get("load_source") == "method_tool" else None)}
                  for row in recent_tasks for name, method in row["methods"].items()]
    for session in recent_sessions:
        if not session.get("packet_sha256"):
            session["deliveries"] = deliveries
    result = {"tasks": recent_tasks, "sessions": recent_sessions,
              "scope": {"recent_tasks": settings["recent_tasks"], "recent_sessions": settings["recent_sessions"],
                        "omitted_tasks": max(0, len(tasks) - len(recent_tasks)),
                        "omitted_sessions": max(0, len(sessions) - len(recent_sessions))}}
    atomic_file.replace_bytes(registry, encoded(result), mode=0o600)
    return result


def collect(root: Path, jobs: list[dict]) -> dict:
    """Summarize local facts. Reported application is distinct from observed delivery."""
    events = [job["payload"]["event"] for job in jobs]
    snapshots = [job["payload"].get("snapshot", {}) for job in jobs]
    directory = atomic_file.real_directory(work_queue._directory(root), Path("observations"))
    retained = tracking(directory, jobs)
    observed_events = []
    usage = {"coverage": {"state": "unavailable"}, "usage": None, "deliveries": []}
    try:
        import session_cost
        observed = session_cost.observe(root, expected_sessions=retained["sessions"])
        usage = {key: observed.get(key) for key in ("coverage", "usage", "deliveries")}
        observed_events = observed.get("events", [])
    except (ImportError, AttributeError, OSError, ValueError, RuntimeError) as error:
        usage["coverage"]["error_category"] = type(error).__name__
    failures = [{key: event[key] for key in ("lane", "event_id", "at", "role", "task", "tool", "status",
                                           "exit_code", "seconds") if key in event}
                for event in observed_events if event.get("kind") == "failure"
                and event.get("observation_source") == "native_item_completed"]
    native_failures = {"source": "native_item_completed", "total": len(failures),
                       "omitted": max(0, len(failures) - NATIVE_FAILURE_LIMIT),
                       "records": failures[-NATIVE_FAILURE_LIMIT:]}
    counts = dict(Counter(event["kind"] for event in events))
    commands = [command for snapshot in snapshots for command in snapshot.get("commands", [])]
    duration = sum(row.get("seconds", 0) for row in commands if isinstance(row.get("seconds", 0), (int, float)))
    wait = sum(row.get("queue_seconds", 0) for row in commands if isinstance(row.get("queue_seconds", 0), (int, float)))
    proposals = []
    if counts.get("failure"):
        proposals.append({"basis": "observed_failure_events", "count": counts["failure"],
                          "action": "Inspect the recorded failed operation before proposing a scoped repair."})
    if failures:
        proposals.append({"basis": "observed_native_command_failures", "count": len(failures),
                          "action": "Inspect the identified native command failures before proposing a scoped repair."})
    if wait:
        proposals.append({"basis": "observed_proof_queue_seconds", "seconds": wait,
                          "action": "Compare shared proof admission with task overlap before changing capacity."})
    if (usage.get("coverage") or {}).get("state", (usage.get("coverage") or {}).get("usage_state")) != "observed":
        proposals.append({"basis": "incomplete_usage_coverage",
                          "action": "Inspect the listed missing or limited session sources before comparing cost."})
    unavailable = sum(snapshot.get("receipt_state") in {"unavailable", "changed", "over_budget", "unsupported"}
                      for snapshot in snapshots)
    if unavailable:
        proposals.append({"basis": "unavailable_receipt_snapshots", "count": unavailable,
                          "action": "Inspect the event receipt identity before drawing conclusions about the attempt."})
    operation_seconds = {}
    for event in events:
        seconds = event.get("seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
            operation_seconds[event["operation"]] = operation_seconds.get(event["operation"], 0) + seconds
    task_metrics = []
    for task in retained["tasks"]:
        records = list(task["receipts"].values())
        proof = [command for record in records for command in record.get("commands", [])]
        review_seconds = [elapsed(record.get("started_at"), record.get("finished_at"))
                          for record in records if record.get("packet_sha256")]
        calls = [event for event in observed_events if event.get("kind") == "call" and event.get("task") == task["task"]]
        tokens = {key: sum(event.get("tokens", {}).get(key, 0) for event in calls)
                  for key in ("input", "cache_read", "cache_write", "output")}
        task_metrics.append({key: value for key, value in task.items() if key not in {"receipts", "methods"}} | {
            "request_to_ready_seconds": elapsed(task.get("requested_at"), task.get("ready_at")),
            "request_to_merge_seconds": elapsed(task.get("requested_at"), task.get("merged_at")),
            "proof_seconds": sum(item.get("seconds", 0) for item in proof) if proof else None,
            "review_seconds": sum(value for value in review_seconds if value is not None)
                if any(value is not None for value in review_seconds) else None,
            "methods": list(task["methods"].values()), "usage": {
                "uncached_input": tokens["input"], "cached_input": tokens["cache_read"],
                "cache_write": tokens["cache_write"], "output": tokens["output"],
                "input_including_cache": tokens["input"] + tokens["cache_read"] + tokens["cache_write"]} if calls else None,
            "usage_scope": "explicit_task_attribution_only"})
    source_counts = dict(Counter(row.get("state", "unknown") for row in usage.get("deliveries") or []))
    if source_counts.get("unknown"):
        proposals.append({"basis": "source_delivery_unverified", "count": source_counts["unknown"],
                          "action": "Inspect the named missing source or transcript before claiming procedure delivery."})
    return {"schema_version": 1, "created_at": now(), "event_ids": [job["id"] for job in jobs],
            "event_counts": counts, "tasks": sorted({event["task"] for event in events if event.get("task")}),
            "event_scope": "current_batch", "source_coverage": source_counts,
            "task_metrics": task_metrics, "tracking_scope": retained["scope"],
            "native_failures": native_failures,
            "events": [{"event_id": job["id"], "at": job["events"][0]["at"], **job["payload"]} for job in jobs],
            "proof_seconds": duration if commands else None, "proof_queue_seconds": wait if commands else None,
            "operation_wall_seconds": operation_seconds,
            "observations": snapshots, "proposals": proposals, **usage,
            "limits": ["Local observations do not execute repairs, tests or publication.",
                       "Source access and reported application do not prove semantic compliance."]}


def run(root: Path, token: str | None = None) -> dict:
    directory = work_queue._directory(root)
    descriptor = _lock(directory)
    if not file_lock.acquire(descriptor, timeout=5):
        os.close(descriptor)
        return {"status": "running"}
    settings = policy()
    token = token or uuid.uuid4().hex
    completed = 0
    pending_after = False
    deadline = time.monotonic() + settings["run_seconds"]
    try:
        reservation = directory / "observer.json"
        current = json.loads(reservation.read_text(encoding="utf-8")) if reservation.is_file() else {}
        if current.get("token") and current["token"] != token:
            return {"status": "superseded"}
        atomic_file.replace_bytes(reservation, encoded({"token": token, "pid": os.getpid(), "launch_until": 0}), mode=0o600)
        _import_inbox(root, directory, settings["batch_size"], deadline=deadline)
        with work_queue._state(root, directory) as state:
            for job in _pending(state):
                if job["state"] != "pending":
                    job["state"] = "pending" if job["attempts"] < settings["max_attempts"] else "failed"
                    work_queue._event(job, "observer_recovered", time.time(), observer=token)
        while time.monotonic() < deadline:
            _import_inbox(root, directory, settings["batch_size"], deadline=deadline)
            with work_queue._state(root, directory) as state:
                pending = _pending(state)
                ready_at = time.time()
                selected = [job for job in pending
                            if job["payload"].get("refresh_after", 0) <= ready_at][:settings["batch_size"]]
                for job in selected:
                    job.update(state="claimed", owner=token, attempts=job["attempts"] + 1,
                               lease_token=token, max_attempts=settings["max_attempts"],
                               lease_expires_at=time.time() + settings["run_seconds"])
                    work_queue._event(job, "observer_claimed", time.time(), observer=token)
                batch = copy.deepcopy(selected)
            if not batch:
                if not pending:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                refresh_at = min(job["payload"].get("refresh_after", 0) for job in pending)
                time.sleep(min(1, max(0, refresh_at - time.time()), remaining))
                continue
            try:
                report = collect(root, batch)
                reports = atomic_file.real_directory(directory, Path("observations"))
                identity = hashlib.sha256(encoded(report["event_ids"])).hexdigest()
                path = reports / f"{identity}.json"
                data = encoded(report)
                atomic_file.replace_bytes(path, data, mode=0o600)
                atomic_file.replace_bytes(reports / "latest.json", data, mode=0o600)
                text = f"Automatic retrospective observed {len(batch)} events across {len(report['tasks'])} tasks.\n"
                text += f"Completion events {report['event_counts'].get('completion', 0)}, failures {report['event_counts'].get('failure', 0)}.\n"
                text += f"Observed native command failures {report['native_failures']['total']}, omitted records {report['native_failures']['omitted']}.\n"
                text += "Usage coverage " + str((report.get("coverage") or {}).get("state", "unavailable")) + ".\n"
                usage = report.get("usage")
                if usage:
                    text += f"Observed input including cache {usage['input_including_cache']}, output {usage['output']}.\n"
                text += f"Source delivery observed {report['source_coverage'].get('observed', 0)}, unverified {report['source_coverage'].get('unknown', 0)}.\n"
                for task in report["task_metrics"]:
                    duration = task["request_to_ready_seconds"]
                    measured = f"{duration:g} seconds" if duration is not None else "unknown"
                    text += f"{task['task']} request to ready {measured}.\n"
                atomic_file.replace_bytes(reports / "latest.txt", text.encode("utf-8"), mode=0o600)
                outcome = {"report": str(path), "sha256": hashlib.sha256(data).hexdigest()}
            except (OSError, ValueError, RuntimeError) as error:
                outcome = {"error_category": type(error).__name__}
            with work_queue._state(root, directory) as state:
                for item in batch:
                    job = work_queue._job(state, item["id"])
                    job["state"] = "completed" if "report" in outcome else (
                        "pending" if job["attempts"] < settings["max_attempts"] else "failed")
                    job["observation"] = outcome
                    work_queue._event(job, "observer_finished", time.time(), observer=token, **outcome)
                    completed += job["state"] == "completed"
    finally:
        # A producer writes its inbox before trying this lock. Recheck after
        # release so an arrival during the final drain starts a successor.
        try:
            with work_queue._state(root, directory) as state:
                pending_after = bool(_pending(state))
            reservation = directory / "observer.json"
            if reservation.is_file() and json.loads(reservation.read_text(encoding="utf-8")).get("token") == token:
                reservation.unlink()
        finally:
            file_lock.unlock(descriptor)
            os.close(descriptor)
    successor = resume(root) if pending_after or next((directory / "inbox").glob("*.json"), None) else {"observer_started": False}
    return {"status": "collected", "completed": completed, **successor}


def report(root: Path) -> dict:
    directory = work_queue._directory(root)
    path = directory / "observations/latest.json"
    if path.is_symlink():
        raise ValueError("retrospective report cannot be a symbolic link")
    latest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    with work_queue._state(root, directory) as state:
        states = dict(Counter(job["state"] for job in state["jobs"] if work_queue.automatic_observation(job)))
    return {"report_path": str(path), "latest": latest, "queue": states,
            "inbox_pending": sum(1 for _ in (directory / "inbox").glob("*.json"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("observe", "report"), default="observe")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--token")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "report":
        result = report(args.project.resolve())
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            text = Path(result["report_path"]).with_name("latest.txt")
            print(text.read_text(encoding="utf-8") if text.is_file() else "Automatic observations are pending.")
            print("Queue " + json.dumps(result["queue"], sort_keys=True))
    else:
        run(args.project.resolve(), args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
