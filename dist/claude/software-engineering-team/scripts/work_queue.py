"""Shared, reconstructible loop work for one repository on one machine.

Callers check their armed grant before enqueueing, claiming or executing work.
Automatic local retrospective observations use a separate observer and never
consume a grant's execution capacity or dispatch its work.
This module performs no external work and confers no authority. GitHub issues
and task records remain problem truth. The cache lives inside Git's common
directory so linked worktrees share it; separate machines are not coordinated.

Keys identify immutable work, including its relevant source revision. Reusing a
key with a different payload is refused. Expired leases and unknown outcomes
hold their capacity until an explicit reconciliation observes the external
result. Only a known failed attempt can be retried automatically. Deadlines
are UNIX seconds or timezone-aware ISO 8601 strings.
Terminal work moves to per-fingerprint receipts when the hot queue fills.
Receipts retain deduplication across later loops and must not be discarded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import atomic_file
import file_lock

KINDS = frozenset({"detect", "resolve", "retro", "plugin_repair"})
AUTOMATIC_RETRO_ORIGIN = "automatic-retro"
MAX_BYTES = 64 * 1024
MAX_JOBS = 4096
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("identifiers need 1-256 letters, digits, dots, underscores, colons or hyphens")
    return value


def _object(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("payload and evidence must be JSON objects")
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("payload or evidence exceeds 64 KiB")
    return json.loads(encoded)


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _deadline(value) -> float:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        value = parsed.timestamp()
    return _number(value, "deadline")


def _directory(root: Path, *, timeout: float = 10) -> Path:
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=root, capture_output=True, text=True, check=True, timeout=timeout,
    ).stdout.strip()
    return atomic_file.real_directory(Path(common), Path("agentrof/runtime/work-queue"))


@contextmanager
def _state(root: Path, directory: Path | None = None):
    directory = directory or _directory(root)
    path = directory / "state.json"
    lock_path = directory / "state.lock"
    if lock_path.is_symlink():
        raise ValueError("queue lock must not be a symbolic link")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("queue lock must be a regular file")
        if not file_lock.acquire(descriptor, timeout=5):
            raise TimeoutError("work queue is busy")
        try:
            if path.is_symlink():
                raise ValueError("queue state must not be a symbolic link")
            if path.exists() and not path.is_file():
                raise ValueError("queue state must be a regular file")
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "jobs": []}
            if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("jobs"), list):
                raise ValueError("unsupported work queue state")
            before = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
            yield state
            after = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if before != after:
                atomic_file.replace_bytes(path, (after + "\n").encode("utf-8"), mode=0o600)
        finally:
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def _event(job: dict, action: str, now: float, **details) -> None:
    job["events"].append({"id": uuid.uuid4().hex, "action": action, "at": now, **details})


def _expire(state: dict, now: float) -> None:
    for job in state["jobs"]:
        if not automatic_observation(job) and job["state"] == "claimed" and now >= job["lease_expires_at"]:
            job["state"] = "needs_reconciliation"
            _event(job, "lease_expired", now, lease_token=job["lease_token"])


def _job(state: dict, job_id: str) -> dict:
    _identifier(job_id)
    for job in state["jobs"]:
        if job["id"] == job_id:
            return job
    raise ValueError("unknown work queue job")


def automatic_observation(job: dict) -> bool:
    return job["kind"] == "retro" and job["payload"].get("origin") == AUTOMATIC_RETRO_ORIGIN


def enqueue(root: Path, kind: str, key: str, payload: dict, *, enqueued_at: float | None = None) -> dict:
    """Return the existing immutable fingerprint or enqueue it once."""
    if kind not in KINDS:
        raise ValueError("unsupported work kind")
    _identifier(key)
    payload = _object(payload)
    if enqueued_at is not None:
        enqueued_at = _number(enqueued_at, "enqueued_at")
    job_id = hashlib.sha256(f"{kind}\n{key}".encode("utf-8")).hexdigest()
    directory = _directory(root)
    with _state(root, directory) as state:
        existing = next((job for job in state["jobs"] if job["id"] == job_id), None)
        receipts = atomic_file.real_directory(directory, Path("receipts"))
        receipt = receipts / f"{job_id}.json"
        if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
            raise ValueError("queue receipts must be regular files")
        if existing is None and receipt.exists():
            existing = json.loads(receipt.read_text(encoding="utf-8"))
            if (not isinstance(existing, dict) or existing.get("id") != job_id
                    or existing.get("kind") != kind or existing.get("key") != key
                    or existing.get("state") not in {"completed", "failed"}):
                raise ValueError("invalid archived queue receipt")
        if existing is not None:
            if existing["payload"] != payload:
                raise ValueError("the fingerprint already identifies a different payload")
            return copy.deepcopy(existing)
        if len(state["jobs"]) >= MAX_JOBS:
            for retired in state["jobs"]:
                if retired["state"] not in {"completed", "failed"}:
                    continue
                archived = receipts / f"{_identifier(retired['id'])}.json"
                if archived.is_symlink() or (archived.exists() and not archived.is_file()):
                    raise ValueError("queue receipts must be regular files")
                if archived.exists():
                    if json.loads(archived.read_text(encoding="utf-8")) != retired:
                        raise ValueError("archived queue receipt differs from terminal work")
                else:
                    # Persist deduplication truth before dropping the hot record.
                    atomic_file.replace_bytes(archived, (json.dumps(retired, sort_keys=True,
                                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"), mode=0o600)
            state["jobs"] = [job for job in state["jobs"] if job["state"] not in {"completed", "failed"}]
        if len(state["jobs"]) >= MAX_JOBS:
            raise ValueError("work queue is full of active or unresolved work; complete or reconcile it before enqueueing")
        job = {"id": job_id, "kind": kind, "key": key, "payload": payload,
               "state": "pending", "attempts": 0, "events": []}
        _event(job, "enqueued", time.time() if enqueued_at is None else enqueued_at)
        state["jobs"].append(job)
        return copy.deepcopy(job)


def claim(root: Path, owner: str, allowed_kinds, max_active: int, deadline,
          lease_seconds: float, max_attempts: int, *, scope: str | None = None, shared_kinds=()) -> dict | None:
    """Claim eligible work within a grant, sharing capacity across every grant."""
    _identifier(owner)
    if scope is not None:
        _identifier(scope)
    allowed = set(allowed_kinds)
    shared = set(shared_kinds)
    if not shared <= {"retro"}:
        raise ValueError("only retrospective observations may be shared across grants")
    if not allowed or not allowed <= KINDS:
        raise ValueError("allowed_kinds must name supported work kinds")
    for value in (max_active, max_attempts):
        if type(value) is not int or value < 1:
            raise ValueError("max_active and max_attempts must be positive integers")
    deadline = _deadline(deadline)
    lease_seconds = _number(lease_seconds, "lease_seconds")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if time.time() >= deadline:
        return None
    with _state(root) as state:
        now = time.time()
        _expire(state, now)
        active = sum(job["state"] in {"claimed", "needs_reconciliation"} and not automatic_observation(job)
                     for job in state["jobs"])
        if now >= deadline or active >= max_active:
            return None
        for job in state["jobs"]:
            if job["state"] != "pending" or job["kind"] not in allowed or automatic_observation(job):
                continue
            if scope is not None and job["payload"].get("grant") != scope:
                if job["kind"] not in shared or job["payload"].get("grant") is not None:
                    continue
            resource = job["payload"].get("resource_key")
            if resource and any(other["state"] in {"claimed", "needs_reconciliation"}
                                and other["payload"].get("resource_key") == resource for other in state["jobs"]):
                continue
            job["max_attempts"] = min(job.get("max_attempts", max_attempts), max_attempts)
            if job["attempts"] >= job["max_attempts"]:
                job["state"] = "failed"
                _event(job, "attempts_exhausted", now)
                continue
            job.update(state="claimed", owner=owner, lease_token=uuid.uuid4().hex,
                       lease_expires_at=min(now + lease_seconds, deadline), attempts=job["attempts"] + 1)
            _event(job, "claimed", now, owner=owner, lease_token=job["lease_token"], attempt=job["attempts"])
            return copy.deepcopy(job)
    return None


def finish(root: Path, job_id: str, lease_token: str, outcome: str, evidence: dict) -> dict:
    """Acknowledge success, a known safe-to-retry failure, or an unknown result.

    A failed request with uncertain external effects must use 'unknown'. The
    fencing token controls queue acknowledgment; it cannot stop an old worker.
    """
    _identifier(lease_token)
    if outcome not in {"completed", "failed", "unknown"}:
        raise ValueError("outcome must be completed, failed or unknown")
    evidence = _object(evidence)
    receipt = {"lease_token": lease_token, "outcome": outcome, "evidence": evidence}
    expired = False
    with _state(root) as state:
        job = _job(state, job_id)
        if job.get("lease_token") != lease_token:
            raise ValueError("stale lease token")
        if job.get("last_finish") == receipt:
            return copy.deepcopy(job)
        now = time.time()
        _expire(state, now)
        expired = job["state"] != "claimed"
        if not expired:
            job["state"] = {"completed": "completed", "failed": "pending", "unknown": "needs_reconciliation"}[outcome]
            if outcome == "failed" and job["attempts"] >= job["max_attempts"]:
                job["state"] = "failed"
            job["last_finish"] = receipt
            _event(job, "finished", now, **receipt)
        result = copy.deepcopy(job)
    if expired:
        raise ValueError("lease is no longer active; observe the external outcome and reconcile")
    return result


def reconcile(root: Path, job_id: str, outcome: str, evidence: dict) -> dict:
    """Resolve held work only after a caller has observed the external result.

    'retry' requires evidence that retrying is safe, including stopping an old
    worker when needed. It never extends the job's original attempt budget.
    """
    if outcome not in {"completed", "failed", "retry"}:
        raise ValueError("reconciliation outcome must be completed, failed or retry")
    evidence = _object(evidence)
    if not evidence:
        raise ValueError("reconciliation requires external outcome evidence")
    with _state(root) as state:
        _expire(state, time.time())
        job = _job(state, job_id)
        if job["state"] != "needs_reconciliation":
            raise ValueError("only held work can be reconciled")
        job["state"] = outcome
        if outcome == "retry":
            job["state"] = "pending" if job["attempts"] < job["max_attempts"] else "failed"
        job["lease_token"] = None
        _event(job, "reconciled", time.time(), outcome=outcome, evidence=evidence)
        return copy.deepcopy(job)


def status(root: Path) -> dict:
    """Return the hot queue, marking expired work as held rather than retryable."""
    with _state(root) as state:
        _expire(state, time.time())
        return copy.deepcopy(state)
