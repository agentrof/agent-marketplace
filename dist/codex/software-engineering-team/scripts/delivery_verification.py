#!/usr/bin/env python3
"""Coordinate independent, source-bound Item verification in disposable runtime."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import functools
import fnmatch
import json
import math
import os
import platform
import re
import secrets
import socket
from shlex import quote as shlex_quote

import shlex
import shutil
import stat
import time
import threading
from pathlib import Path, PurePosixPath
import subprocess
import shutil
import sys
import tempfile
import uuid

import atomic_file
import backlog_compile
from backlog_compile import meaningful_text, scenario_blocks, scenario_fields
import delivery_compile as delivery
import file_lock
import operation_compile
import process_policy
import step_timing

POLICY_PATH = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/delivery-verification-policy.json"
ROLES = ("code_reviewer", "qa_engineer")
# How often one wait call checks the Item's locks; a local check costs no model call.
WAIT_POLL_SECONDS = 0.5
# At review_loop blocking_delta a fresh code reviewer registers its rulings on
# the claims of a code review in this mode, apart from the claiming result.
CALIBRATION_MODE = "calibration"
# At pre_handoff_regression touched_suites the coordinator runs the suites of the
# earlier stories a candidate touches before the freeze, which needs that run.
PRE_HANDOFF_SWITCH = "pre_handoff_regression"
# At long_lived, QA reuses a running product environment unless the change needs a fresh one.
RUNTIME_LIFECYCLE, LONG_LIVED, FRESH = "runtime_lifecycle", "long_lived", "fresh"
TOUCHED_SUITES = "touched_suites"
PRE_HANDOFF_HOLDER = "delivery_coordinator"
PRE_HANDOFF_RUN = "regression-run"
PRE_HANDOFF_SELECTION = "scratch/pre-handoff-selection.json"
# What a pre-handoff run must share with the selection derived for the candidate.
PRE_HANDOFF_IDENTITY = ("candidate_tree", "kind", "command", "workdir", "affected_test_ids")
# What the sessions carry of every pre-handoff run, until approve-item-evidence records it.
PRE_HANDOFF_HISTORY_FIELDS = ("evidence_hash", "candidate_tree", "kind", "exit_code", "candidate_intact",
                              "duration_seconds", "earlier_stories")
# At own_target_reuse spot_run QA's final test run also takes the Item's own Test
# Plan targets from the accepted pre-handoff run, but for the spot-run targets QA
# names, which the approved test command runs with every target the run did not cover.
# At accept_record it takes every own target from that run and spot-runs none.
OWN_TARGET_SWITCH = "own_target_reuse"
# At live_case_reuse delta_since_green a live run may satisfy the groups the delta
# since their last green run does not select with that run's passed cases.
LIVE_REUSE_SWITCH, DELTA_SINCE_GREEN = "live_case_reuse", "delta_since_green"
LIVE_BASE = "live-base.json"
# At pass_carry_over per_item the runner keeps every pass record the approved test or live command writes, failed
# passes included, in the Item's carry-over record and names that record to the command's next pass.
CARRY_OVER_SWITCH, PER_ITEM = "pass_carry_over", "per_item"
CARRY_OVER_RECORD, CARRY_OVER_PASS = "carry-over-record.json", "scratch/carry-over-pass.json"
CARRY_OVER_OWNER = ("project_key", "delivery_id", "item_id")
LIVE_BASE_FIELDS = ("product_commit", "candidate_hash", "map_hash", "evidence_hash", "live_group", "command", "workdir",
                    *("environment_variables", "environment_hash", "python", "platform", "git"),
                    "exit_code", "candidate_intact", "output_file", "output_sha256", "completed_at")
SPOT_RUN = "spot_run"
ACCEPT_RECORD = "accept_record"
# At test_group_report refuse_missing_groups a test run also reads the group
# report its approved command writes, where the Verification Contract declares one.
GROUP_REPORT_SWITCH = "test_group_report"
REFUSE_MISSING_GROUPS = "refuse_missing_groups"
GROUP_STATUSES = ("passed", "failed", "not_collected")
GROUP_COUNTS = ("passed", "failed", "skipped")
MISSING_GROUP = "missing"
# At level_change_map assertion_map an Item that rewrites the automation target of
# a scenario whose Test Plan level changed since its story integrated records,
# per scenario, the assertions that prove its Then before and after the change;
# freeze refuses an incomplete or unanchored map and the code reviewer reads the
# pairs the check flags (#440).
LEVEL_CHANGE_SWITCH = "level_change_map"
ASSERTION_MAP = "assertion_map"
ASSERTION_MAP_FILE = "assertion-map.json"
ASSERTION_KINDS_PATH = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/assertion-kinds.json"
ASSERTION_FIELDS = ("path", "code", "kind", "expected")
# At test_engines partitioned QA's final test run runs the partitions the
# Verification Contract declares in parallel, one private clone each, over its
# isolated test engines, longest first by the durations the Item's runtime keeps.
ENGINE_SWITCH = "test_engines"
PARTITIONED = "partitioned"
PARTITION_DURATIONS = "partition-durations.json"
# What a mutation run records, at once and passed, when its scope holds no file.
EMPTY_MUTATION_SCOPE = "scope empty, not run"
# At process switch code_review_panel beside_official a lens panel reads the
# frozen candidate beside the official code reviewer, and merge-panel
# registers the one code review result from both.
CODE_REVIEW_PANEL = "code_review_panel"
BESIDE_OFFICIAL = "beside_official"
PANEL_LENS_MODE = "panel_lens"
PANEL_DATA_PATH = Path(__file__).resolve().parents[1] / "skill-content/code-review/data/code-review-panel.json"
# At code_review_panel risk_scoped a lens joins the official reviewer only when
# the frozen change fires its trigger, by the owner's lens-trigger parameters.
RISK_SCOPED = "risk_scoped"
PANEL_VALUES = (BESIDE_OFFICIAL, RISK_SCOPED)
LENS_TRIGGERS_PATH = Path(__file__).resolve().parents[1] / "skill-content/code-review/data/lens-triggers.json"
PANEL_STEP = "code_review"
OFFICIAL = "official"
DUPLICATE = "duplicate"
PANEL_PASS_RE = re.compile(r"^P([1-9][0-9]*)-")
# The blocking severities of the code-review skill's Severity Definitions, highest first.
REVIEW_SEVERITY_ORDER = ("critical", "major")
# A run identity covers the variables that can change a command's result, never
# the shell that ran it: these, the ones the approved Verification Contract names
# in command_variables, and every variable of the prefixed namespaces that the
# command's environment holds. PWD, OLDPWD, SHLVL and session variables stay out,
# so evidence recorded in one reader's shell binds in every other one (#356). It
# keeps a hash of their values keyed with IDENTITY_KEY, which never leaves the
# Item's verification runtime, since QA's result copies that hash into a tracked
# record and a hash of a few guessable values would check a guess of a credential.
COMMAND_VARIABLES = ("HOME", "LANG", "PATH", "TZ")
COMMAND_VARIABLE_PREFIXES = ("AGENTROF_", "LC_")
# The runner sets these for every command it runs. The Delivery and Item ids
# come from the frozen candidate, so a reader whose shell exports nothing runs
# an approved command that reads them like the coordinator's shell does.
RUNNER_VARIABLES = ("AGENTROF_DELIVERY_ID", "AGENTROF_ITEM_ID", "AGENTROF_MUTATION_FILES",
                    "AGENTROF_VERIFICATION_SCRATCH")
# The runner's per-run inputs: an identity binds the data each carries, so it
# names neither the variable nor its path. A partitioned test run hands each
# partition its own file and engine, which its record names, never the identity.
PARTITION_VARIABLES = ("AGENTROF_TEST_PARTITION", "AGENTROF_TEST_ENGINE")
SELECTION_VARIABLES = ("AGENTROF_LIVE_BASE", "AGENTROF_LIVE_BASE_COMMIT", "AGENTROF_LIVE_RESUME", "AGENTROF_LIVE_TESTS", "AGENTROF_DIAGNOSTIC_TESTS", "AGENTROF_REUSED_TESTS", "AGENTROF_CARRY_OVER_RECORD", *PARTITION_VARIABLES)
# At touched_suites QA's final test run names here the earlier-story targets the
# accepted pre-handoff run covered, for the approved test command to skip (#354).
REUSED_TESTS = "reused-tests.json"
# What every final run of one session shares, and what an identity records of its host.
ENVIRONMENT_FIELDS = ("environment_variables", "environment_hash", "python", "platform", "git")
IDENTITY_KEY = "identity.key"
IDENTITY_KEY_BYTES = 32
# A runner before #356 hashed the whole process environment and checked it against the checker's.
LEGACY_ENVIRONMENT = ("carries the whole-environment hash of an earlier runner, which binds the shell that ran"
                      " it; freeze the candidate again with freeze --fresh and rerun both readers")


RUN_KINDS = ("test", "mutation", "dependency_audit", "diagnostic_test", "live_test")


def policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def keyed_digest(key: bytes, value) -> str:
    return "hmac-sha256:" + hmac.new(key, canonical(value), hashlib.sha256).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip()
                           or "cannot inspect verification candidate")
    # NUL-separated Git paths preserve carriage returns, newlines and leading
    # spaces. Text-mode pipes and strip() both change valid POSIX file names.
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\n")


@functools.lru_cache(maxsize=128)
def runtime_anchor(root: Path) -> Path:
    if (root / ".git").is_file():
        from delivery_git import main_worktree
        return main_worktree(root)
    return root


def session_path(root: Path) -> Path:
    anchor = runtime_anchor(root.resolve())
    name = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
    return anchor / ".agentrof/agent-marketplace/.runtime/verification" / name / "session.json"


def safe_runtime_path(root: Path, path: Path, *, file_only: bool = False) -> Path:
    """Reject aliases before accessing runtime, including native Windows junctions."""
    anchor = runtime_anchor(root.resolve()).resolve()
    if path != anchor and anchor not in path.parents:
        raise RuntimeError("verification runtime must remain inside its main worktree")
    for parent in (path, *path.parents):
        if parent == anchor:
            break
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("verification runtime must not contain symlinks or junctions")
        if parent == path and file_only and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise RuntimeError("verification runtime files must be regular and unshared")
    return path


def safe_runtime(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root), file_only=True)


@contextlib.contextmanager
def locked(root: Path):
    path = safe_runtime(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = safe_runtime_path(root, path.with_suffix(".lock"), file_only=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.lock(fd)
        yield
    finally:
        file_lock.unlock(fd)
        os.close(fd)


def command_active(root: Path) -> bool:
    path = safe_runtime_path(root, safe_runtime(root).with_name("commands.lock"), file_only=True)
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if file_lock.try_lock(fd):
            file_lock.unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def command_owner_path(root: Path) -> Path:
    return safe_runtime_path(root, safe_runtime(root).with_name("command-owner.json"), file_only=True)


@contextlib.contextmanager
def command_lock(root: Path, role: str, command: str):
    """Hold the verification command lock while *role* runs *command*, with its owner record beside it.

    The record names the role, so a reader's registration waits only for that
    reader's own command (#355). As with the environment lock, the operating
    system ends the lock with its holder, and a record is read only while the
    lock is held.
    """
    path = safe_runtime_path(root, safe_runtime(root).with_name("commands.lock"), file_only=True)
    owner = command_owner_path(root)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not file_lock.try_lock(fd):
            raise RuntimeError("another verification command is still running; `wait` returns once it has exited")
        try:
            atomic_file.replace_text(owner, json.dumps(
                {"role": role, "command": command, "pid": os.getpid(),
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                indent=2, sort_keys=True) + "\n")
            yield
        finally:
            with contextlib.suppress(OSError):
                owner.unlink()
            file_lock.unlock(fd)
    finally:
        os.close(fd)


def command_holder(root: Path) -> dict | None:
    """The owner record of the running verification command, {} before it records one, or None when none runs."""
    if not command_active(root):
        return None
    return environment_owner(command_owner_path(root)) or {}


def recorded_command_owner(owner: dict) -> bool:
    return (all(isinstance(owner.get(key), str) for key in ("role", "command", "started_at"))
            and type(owner.get("pid")) is int)


def wait_for_own_command(root: Path, role: str, action: str) -> None:
    """Refuse *action* of a reader in *role* while its own verification command runs.

    A reader settles only once its own command has exited, so its evidence
    cannot change after its verdict. Another role's command holds no other
    reader: the code reviewer runs none and registers while QA's runs. A
    command that has not recorded its owner yet holds every reader.
    `wait --role` returns once the same check passes.
    """
    holder = wait_holder(root, role)
    if holder is None:
        return
    if not recorded_command_owner(holder):
        raise RuntimeError("wait for a verification command that has not recorded its owner yet to exit"
                           f" before {action}; `wait --role {role}` returns once it has")
    raise RuntimeError(f"wait for {holder['role']}'s verification command `{holder['command']}` in process"
                       f" {holder['pid']} since {holder['started_at']} to exit before {action};"
                       f" `wait --role {role}` returns once it has")


def wait_holder(root: Path, role: str | None) -> dict | None:
    """What a wait by *role* still waits for, or None once nothing does.

    For a reader role it is that reader's own running verification command, or
    a command that has not recorded its owner yet, which holds every reader:
    what wait_for_own_command refuses. Without a role it is any holder of the
    Item's environment lock or verification command lock, which every command,
    freeze and guarded write of the Item refuses.
    """
    if role is None:
        environment = environment_holder(root)
        if environment is not None:
            return {"lock": "environment", **environment}
    command = command_holder(root)
    if command is None or (role is not None and recorded_command_owner(command) and command["role"] != role):
        return None
    return {"lock": "verification_command", **command}


def wait_for_release(root: Path, role: str | None = None, seconds: float | None = None) -> dict:
    """Block until nothing the caller waits for holds the Item, and at most the policy's wait bound.

    The call returns as soon as the holder releases its lock, so a waiting role
    acts on the command's exit rather than on an interval of its own. It never
    blocks longer than wait_bound_seconds, which stays under the hosts' prompt
    cache lifetime: a role that calls wait again at once makes each model call
    of its wait while its cached context is warm, where one long sleep makes the
    host write that whole context into its cache again.
    """
    root = root.resolve()
    bound = policy()["wait_bound_seconds"]
    limit = bound if seconds is None else seconds
    if role is not None and role not in ROLES:
        raise RuntimeError("wait takes no role or a reader role: " + ", ".join(ROLES))
    if not 0 < limit <= bound:
        raise RuntimeError(f"one wait blocks for more than 0 and at most {bound} seconds, so the next model call"
                           " finds the prompt cache warm; call wait again to wait longer")
    started = time.monotonic()
    while True:
        holder = wait_holder(root, role)
        if holder is None and role in {None, "qa_engineer"}:
            session = read_session(root, required=False)
            pending = pending_owner_step(session or {})
            if pending is not None:
                holder = {"lock": "owner_step", **pending}
        elapsed = time.monotonic() - started
        if holder is None or elapsed >= limit:
            break
        time.sleep(min(WAIT_POLL_SECONDS, limit - elapsed))
    value = {"role": role, "released": holder is None, "waited_seconds": round(elapsed, 3),
             "bound_seconds": bound}
    if holder is not None:
        value.update(holder=holder, next="call wait again now, as a tool call of its own")
    progress = owner_step_progress(read_session(root, required=False) or {})
    if progress:
        value["owner_steps"] = progress
    return value


def environment_lock_paths(root: Path) -> tuple[Path, Path]:
    """The Item's environment lock and the owner record beside it, in the Item's runtime state."""
    directory = session_path(root).parent
    return (safe_runtime_path(root, directory / "environment.lock", file_only=True),
            safe_runtime_path(root, directory / "environment-owner.json", file_only=True))


def environment_owner(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def environment_holder(root: Path) -> dict | None:
    """The owner record of the process holding the Item's environment lock, or None when none does."""
    lock, owner = environment_lock_paths(root)
    if not lock.exists():
        return None
    descriptor = os.open(lock, os.O_RDWR)
    try:
        if file_lock.try_lock(descriptor):
            file_lock.unlock(descriptor)
            return None
    finally:
        os.close(descriptor)
    return environment_owner(owner) or {}


def describe_environment_holder(owner: dict) -> str:
    if not all(isinstance(owner.get(key), str) for key in ("holder", "command", "started_at")) \
            or type(owner.get("pid")) is not int:
        return "a process that has not recorded its owner yet"
    return (f"{owner['holder']}, running `{owner['command']}` in process {owner['pid']}"
            f" since {owner['started_at']}")


def environment_busy(owner: dict) -> RuntimeError:
    """The refusal of a command that finds the Item's environment lock held, naming its holder."""
    return RuntimeError("DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by "
                        + describe_environment_holder(owner) + "; run this after it finishes")


@contextlib.contextmanager
def environment_lock(root: Path, holder: str, command: str):
    """Hold the Item's environment lock while one environment verb or verification command runs.

    The lock refuses at once while another process holds it and names that
    holder from its owner record. As with every package lock, the operating
    system ends the lock with the process holding it, so a holder that died
    leaves only its owner record: the next holder replaces that record and
    yields it as the interrupted holder. Neither a record's age nor its process
    id frees a lock.
    """
    lock, owner = environment_lock_paths(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not file_lock.try_lock(descriptor):
            raise environment_busy(environment_owner(owner) or {})
        try:
            interrupted = environment_owner(owner)
            atomic_file.replace_text(owner, json.dumps(
                {"holder": holder, "command": command, "pid": os.getpid(),
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                indent=2, sort_keys=True) + "\n")
            yield interrupted
        finally:
            with contextlib.suppress(OSError):
                owner.unlink()
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def read_session(root: Path, *, required: bool = True) -> dict | None:
    path = safe_runtime(root)
    if not path.exists():
        if required:
            raise RuntimeError("verification session is missing; freeze and rerun both roles")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("schema_version") != 1 or value.get("digest") != digest(
                {key: item for key, item in value.items() if key != "digest"})
                or set(value["workers"]) != set(ROLES)
                or any(worker.get("state") not in {"running", "settled", "cancelled"}
                       for worker in value["workers"].values())):
            raise ValueError("invalid schema, workers or digest")
        return value
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"verification session is invalid: {exc}") from exc


def write_session(root: Path, value: dict) -> None:
    value = {key: item for key, item in value.items() if key != "digest"}
    value["digest"] = digest(value)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    # The guard copy is what the integrity check restores a changed session record from.
    atomic_file.replace_text(session_guard_path(root), text)
    atomic_file.replace_text(safe_runtime(root), text)
    sync_protection(root, value)


def session_guard_path(root: Path) -> Path:
    return safe_runtime_path(root, safe_runtime(root).with_name("session.guard"), file_only=True)


def protection_path(root: Path) -> Path:
    return safe_runtime_path(root, safe_runtime(root).with_name("protection.json"), file_only=True)


WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def read_protection(root: Path) -> dict:
    try:
        value = json.loads(protection_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise RuntimeError(f"verification protection record is invalid: {exc}") from exc
    return value if isinstance(value, dict) else {}


def git_metadata_dir(root: Path) -> Path:
    """The Git directory of a checkout, following a linked worktree's .git file."""
    metadata = root / ".git"
    with contextlib.suppress(OSError, IndexError):
        if metadata.is_file():
            metadata = root / metadata.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
    return metadata


def protection_targets(root: Path, value: dict) -> list[Path]:
    """Files kept read-only while readers run: tracked candidate files, HEAD and index, the session record.

    A worktree without an index protects no tracked file here. Windows keeps the
    session record writable, because it refuses to replace a read-only file and the
    runner replaces that record at every step; the integrity check still covers it.
    """
    reports = set(value.get("candidate", {}).get("report_paths", []))
    files = []
    metadata = git_metadata_dir(root)
    if (metadata / "index").is_file():
        files = [root / name for name in git(root, "ls-files", "-z").split("\0") if name and name not in reports]
        files += [metadata / "HEAD", metadata / "index"]
    if os.name != "nt":
        files += [session_path(root), session_guard_path(root)]
    return files


def protected_generation(record: dict, path: Path) -> list[int]:
    """The file's generation, with the runner's own mode changes mapped back to the frozen one."""
    generation = source_file_generation(path)
    row = record.get("files", {}).get(str(path))
    return row["frozen"] if row and row.get("frozen") and generation in row["aliases"] else generation


def apply_protection(root: Path, value: dict) -> None:
    """Make the protected files read-only, recording their exact modes first."""
    previous = read_protection(root)
    held = previous.get("state") in {"applying", "applied"}
    record = {"state": "applying", "session_id": value.get("session_id"), "files": {}}
    for path in protection_targets(root, value):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode):
            old = previous.get("files", {}).get(str(path), {})
            mode = old["mode"] if held and "mode" in old else stat.S_IMODE(metadata.st_mode)
            record["files"][str(path)] = {"mode": mode, "frozen": protected_generation(previous, path),
                                          "aliases": []}
    atomic_file.replace_text(protection_path(root), json.dumps(record, sort_keys=True) + "\n")
    for name, row in record["files"].items():
        with contextlib.suppress(FileNotFoundError):
            os.chmod(name, row["mode"] & ~WRITE_BITS)
            row["aliases"].append(source_file_generation(Path(name)))
    record["state"] = "applied"
    atomic_file.replace_text(protection_path(root), json.dumps(record, sort_keys=True) + "\n")


def restore_protection(root: Path) -> None:
    """Give the protected files back their recorded modes; a changed file keeps its change visible."""
    record = read_protection(root)
    if record.get("state") not in {"applying", "applied"}:
        return
    for name, row in record["files"].items():
        with contextlib.suppress(FileNotFoundError):
            unchanged = source_file_generation(Path(name)) in row["aliases"]
            os.chmod(name, row["mode"])
            row["aliases"] = [*row["aliases"], source_file_generation(Path(name))] if unchanged else []
    record["state"] = "restored"
    atomic_file.replace_text(protection_path(root), json.dumps(record, sort_keys=True) + "\n")


def sync_protection(root: Path, value: dict) -> None:
    """Protect while a reader runs and restore once none does, from the session state just written."""
    running = any(worker.get("state") == "running" for worker in value.get("workers", {}).values())
    record = read_protection(root)
    if running and not (record.get("state") == "applied" and record.get("session_id") == value.get("session_id")):
        apply_protection(root, value)
    elif not running:
        restore_protection(root)


def candidate_drift(root: Path, current: dict) -> list[str]:
    """What differs from the frozen candidate: HEAD, a tracked file's generation or the index."""
    drift = [] if git(root, "rev-parse", "HEAD") == current["product_commit"] else ["HEAD"]
    reports = set(current.get("report_paths", []))
    expected = current.get("source_observations", {})
    record = read_protection(root)
    names = {name for name in git(root, "ls-files", "-z").split("\0") if name and name not in reports}
    for name in sorted(names | set(expected)):
        try:
            generation = protected_generation(record, root / name)
        except FileNotFoundError:
            generation = None
        # A file this check already restored keeps a new generation, so the candidate stays changed.
        restored = record.get("files", {}).get(str(root / name), {}).get("restored")
        if generation != expected.get(name) and generation != restored:
            drift.append(name)
    staged = git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", current["product_commit"])
    drift.extend(name for name in staged.split("\0") if name and name not in reports and name not in drift)
    return drift


def restore_candidate(root: Path, current: dict, drift: list[str]) -> None:
    """Put HEAD, the index and the changed tracked files back to the frozen commit, read-only again."""
    commit = current["product_commit"]
    if "HEAD" in drift:
        try:
            git(root, "update-ref", git(root, "symbolic-ref", "-q", "HEAD"), commit)
        except RuntimeError:
            git(root, "update-ref", "--no-deref", "HEAD", commit)
    specs = [":(literal)" + name for name in drift if name != "HEAD"]
    if not specs:
        return
    git(root, "reset", "-q", commit, "--", *specs)
    kept = [name for name in git(root, "ls-tree", "-r", "--name-only", "-z", commit, "--", *specs).split("\0") if name]
    if kept:
        git(root, "checkout", commit, "--", *(":(literal)" + name for name in kept))
    record = read_protection(root)
    if record.get("state") == "applied":
        for name in kept:
            row = record["files"].get(str(root / name))
            if row:
                os.chmod(root / name, row["mode"] & ~WRITE_BITS)
                row["restored"] = source_file_generation(root / name)
        atomic_file.replace_text(protection_path(root), json.dumps(record, sort_keys=True) + "\n")


def check_integrity(root: Path) -> dict | None:
    """While readers run, restore a changed session record or candidate and mark the pass not intact.

    The finding is recorded in the session as integrity_findings with
    candidate_intact false, so no reader result passes and validate refuses.
    """
    root = Path(root).resolve()
    path = session_path(root)
    if not (root / ".git").exists() or not path.is_file():
        return None
    with locked(root):
        guard = session_guard_path(root)
        saved = guard.read_bytes() if guard.is_file() else path.read_bytes()
        try:
            value = json.loads(saved)
        except ValueError:
            return None
        if not any(worker.get("state") == "running" for worker in value.get("workers", {}).values()):
            return None
        changed = []
        if path.read_bytes() != saved:
            atomic_file.replace_bytes(path, saved)
            changed.append("session record")
        current = value.get("candidate")
        if current:
            drift = candidate_drift(root, current)
            if drift:
                restore_candidate(root, current, drift)
                changed.extend(drift)
        if not changed:
            return None
        shown = ", ".join(changed[:20]) + (f" and {len(changed) - 20} more" if len(changed) > 20 else "")
        finding = {"id": "CANDIDATE_MUTATED", "paths": changed, "detected_at": time.time(),
                   "summary": f"a command changed {shown} while readers ran; restored from the frozen candidate"
                              " and session record, and this pass is marked candidate_intact false: cancel"
                              " both readers and freeze again"}
        value = read_session(root)
        value.setdefault("integrity_findings", []).append(finding)
        value["candidate_intact"] = False
        write_session(root, value)
        return finding


def require_intact(value: dict) -> None:
    if value.get("candidate_intact") is False:
        raise RuntimeError("candidate_intact is false: " + "; ".join(
            finding["summary"] for finding in value.get("integrity_findings", [])))


def guard_write(worktree: Path, paths: list[Path] | None = None) -> None:
    """Refuse writes while either reader or a verification command is active; only isolated scratch is writable.

    The refusal names what holds the Item: its running readers, or else the
    command that holds the verification command lock, which with no reader
    running is a pre-handoff regression run before the freeze. That run holds
    the lock only while it derives its selection and clones the candidate.
    """
    root = Path(worktree).resolve()
    if not (root / ".git").exists():
        return
    value = read_session(root, required=False)
    readers = bool(value) and any(worker["state"] == "running" for worker in value["workers"].values())
    if not readers:
        # Readers that settled in a process that ended before its restore leave the modes to restore here.
        restore_protection(root)
    if not readers and not command_active(root):
        return
    scratch = session_path(root).parent / "scratch"
    if paths and all(scratch in (path if path.is_absolute() else root / path).resolve().parents
                     for path in paths):
        return
    if readers and paths and not protected_paths(root, paths):
        return
    if not readers:
        # Each command holds the environment lock around the command lock, so
        # a command with no environment holder left has finished.
        holder = environment_holder(root)
        if holder is None and not command_active(root):
            return
        wait = ("it has derived its selection and cloned the candidate"
                if (holder or {}).get("command") == PRE_HANDOFF_RUN else "it finishes")
        raise RuntimeError("DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by "
                           + describe_environment_holder(holder or {})
                           + f"; writes to the Item worktree wait until {wait}")
    raise RuntimeError("DELIVERY_VERIFICATION_READERS_ACTIVE: settle or confirm cancellation of both readers before"
                       " writing the frozen candidate's tracked files, its Git index, HEAD or refs, or the"
                       " verification session record. Untracked, ignored and runtime files of the Item worktree and"
                       " the session scratch stay writable; the bound"
                       " `<python> [-B] <bound delivery_verification.py> --worktree <worktree> <verb> ...` runs"
                       " verification commands, including `environment --verb <name>` for a QA verb the"
                       " Verification Contract declares")


def protected_paths(worktree: Path, paths: list[Path]) -> list[Path]:
    """The paths among *paths* whose write could change the frozen candidate or its session records.

    They are the Item worktree's Git metadata and tracked files, a directory that holds one, and
    the verification session directory outside its scratch. A worktree whose tracked files cannot
    be listed protects every path inside it.
    """
    root = Path(worktree).resolve()
    session = session_path(root).parent
    found = []
    for raw in paths:
        path = raw if raw.is_absolute() else root / raw
        for candidate in {Path(os.path.normpath(path)), path.resolve()}:
            if candidate.is_relative_to(session / "scratch"):
                continue
            if (candidate.is_relative_to(session) or session.is_relative_to(candidate)
                    or root.is_relative_to(candidate) or tracked_path(root, candidate)):
                found.append(raw)
                break
    return found


def tracked_path(root: Path, path: Path) -> bool:
    if not path.is_relative_to(root):
        return False
    relative = path.relative_to(root).as_posix()
    if relative.casefold().split("/")[0] == ".git":
        return True
    if not (git_metadata_dir(root) / "index").is_file():
        return True
    try:
        # icase: a case-insensitive file system writes a tracked file through any spelling.
        return bool(git(root, "ls-files", "-z", "--", ":(literal,icase)" + relative))
    except RuntimeError:
        return True


def command_form(root: Path) -> str:
    """The literal invocation a reader's Bash call starts with while readers are active; a verb follows it."""
    arguments = [sys.executable, "-B", str(Path(__file__).resolve()), "--worktree", str(root.resolve())]
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def instruction_identity() -> str:
    base = Path(__file__).resolve().parents[1]
    files = [["constitution.md", hashlib.sha256((base / "constitution.md").read_bytes()).hexdigest()]]
    for folder in ("agents", "skill-content", "flows", "scripts", "templates"):
        for path in sorted((base / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append([path.relative_to(base).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return digest(files)


def mutation_scope(root: Path, changed: list[str]) -> list[str]:
    rules = policy()["mutation_scope"]
    contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    include = contract.get(rules["additional_include_property"], [])
    if not isinstance(include, list) or any(not isinstance(path, str) or not delivery._is_normalized_claim(path) for path in include):
        raise RuntimeError("mutation_include_paths must name normalized approved repository paths")
    files = []
    for name in changed:
        path = PurePosixPath(name)
        if any(name.startswith(prefix) for prefix in rules["excluded_prefixes"]):
            continue
        if not (root / name).is_file() or (root / name).is_symlink():
            continue
        explicit = any(path == PurePosixPath(value) or PurePosixPath(value) in path.parents for value in include)
        non_code = (path.suffix.lower() in rules["non_code_suffixes"]
                    or any(part in rules["test_path_segments"] for part in path.parts[:-1])
                    or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in rules["test_name_patterns"]))
        if explicit or not non_code:
            files.append(name)
    return files


def source_file_generation(path: Path) -> list[int]:
    """Observe reverted writes with inode change time, not Windows creation time."""
    metadata = path.lstat()
    changed = metadata.st_ctime_ns
    if os.name == "nt" and stat.S_ISREG(metadata.st_mode):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]

        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        with path.open("rb") as stream:
            info = BasicInfo()
            if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.change <= 0:
                raise RuntimeError("filesystem does not expose a trustworthy source change timestamp")
            changed = info.change
    return [metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mode, metadata.st_mtime_ns, changed]


def candidate(root: Path, delivery_id: str, story: str, *, allow_evidence: bool = False) -> dict:
    from delivery_git import require_visible_item_index, worktree_pending_paths
    root = root.resolve()
    head, dirty = delivery.item_worktree_head(root)
    require_visible_item_index(root)
    docs = delivery.docs_root(root)
    directory = delivery.find_delivery(docs, delivery_id)
    item = directory / "items" / delivery.id_slug(story) / "item.md" if directory else None
    if item is None or not item.is_file():
        raise RuntimeError("Delivery Item not found")
    props, _ = delivery.split_note(item)
    if props.get("status") != "active" or not props.get("item_plan_hash"):
        raise RuntimeError("verification requires an active, approved Item")
    if delivery.verification_schedule(props) != "parallel_snapshot_v1":
        raise RuntimeError("verification sessions require parallel_snapshot_v1 in the approved Item plan")
    report_paths = (item.parent / "code-review.md", item.parent / "verification.md")
    report_errors = delivery.item_evidence_file_findings(root, head, report_paths)
    if report_errors:
        raise RuntimeError("; ".join(report_errors))
    reports = {path.relative_to(root).as_posix() for path in report_paths}
    if dirty and (not allow_evidence or not worktree_pending_paths(root, root).issubset(reports)):
        raise RuntimeError("commit or remove non-evidence changes before verifying the Item candidate")
    paths = [item, directory / "execution-plan.md", directory / "delivery.md"]
    for key in ("story_path", "test_plan_path"):
        paths.append(docs / props[key])
    delivery_props, _ = delivery.split_note(directory / "delivery.md")
    paths.append(docs / delivery_props["definition_of_done_path"])
    for key in ("verification_contract_ref", "environment_contract_ref"):
        if props.get(key):
            paths.append(docs / (props[key] + ".md"))
    paths.extend(sorted((docs / "system-architecture").rglob("*.md")))
    paths.extend(sorted(root.glob("*.md")))
    if (root / "workspace/config.json").is_file():
        paths.append(root / "workspace/config.json")
    identities = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"verification input is not a regular file: {path}")
        identities[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    base = props.get("integration_base_commit")
    if not isinstance(base, str) or not delivery.GIT_OID_RE.fullmatch(base):
        raise RuntimeError("Item has no exact integration base for verification")
    # Both sides of a rename belong to the Item change. Deleted files remain in
    # the manifest, while mutation tools receive only surviving regular files.
    changed = sorted(set(part for part in git(root, "diff", "--name-only", "--no-renames", "-z", base, head).split("\0") if part))
    code = mutation_scope(root, changed)
    observations = {}
    protection = read_protection(root)
    for name in git(root, "ls-files", "-z").split("\0"):
        if name and name not in reports:
            observations[name] = protected_generation(protection, root / name)
    value = {"schema_version": 1, "delivery": delivery_id, "story": story,
             "source_observations": observations,
             "product_commit": head, "integration_base_commit": base,
             "item_plan_hash": props["item_plan_hash"], "inputs": identities,
             "instruction_identity": instruction_identity(), "runtime_required": props.get("runtime_required", False),
             "changed_files": changed, "mutation_files": code, "report_paths": sorted(reports)}
    if delivery.delivery_switch_value(docs, delivery_id, LEVEL_CHANGE_SWITCH) == ASSERTION_MAP:
        value["assertion_map"] = assertion_map_check(root, value)
    value["candidate_hash"] = digest(value)
    return value


def require_published_claims(root: Path, current: dict, remote: str = "origin") -> None:
    """At provisional_claims during_plan_revision, refuse to freeze or start a reader on a candidate
    that changes a product path the published Item record does not claim, so no reader starts on
    work that push-item would refuse. The claims come from the Item record of the Integration
    commit the Item converged on, never the worktree's own record, which its writer can edit.
    That commit must be one the Item has taken, as push-item checks it against the remote Item
    ref and Integration. Other candidate reads, such as a regression run, still work on a
    provisional commit."""
    import delivery_git
    docs = delivery.docs_root(root)
    delivery_id, story = current["delivery"], current["story"]
    if delivery.delivery_switch_value(docs, delivery_id, delivery_git.PROVISIONAL_SWITCH) \
            != delivery_git.PROVISIONAL_VALUE:
        return
    item = delivery.find_delivery(docs, delivery_id) / "items" / delivery.id_slug(story) / "item.md"
    refs = delivery_git.canonical_refs(delivery_id, story)
    try:
        tips = delivery_git.remote_ref_oids(root, remote, [refs["item"], refs["integration"]])
        if not tips[refs["item"]] or not tips[refs["integration"]]:
            raise RuntimeError(f"{remote} holds no Item ref of {story} or no Integration of {delivery_id}")
        before = delivery_git.split_remote_note(
            root, delivery_git.require_commit(root, remote, refs["item"], tips[refs["item"]]),
            item.relative_to(root).as_posix(), delivery.split_note)[0]
        integration = delivery_git.require_commit(root, remote, refs["integration"], tips[refs["integration"]])
    except RuntimeError as exc:
        raise RuntimeError(f"DELIVERY_PUBLISHED_CLAIMS_UNREADABLE: the published Item record cannot be read"
                           f" from {remote}: {exc}") from exc
    delivery_git.converged_integration(root, before, {"integration_base_commit": current["integration_base_commit"]},
                                       current["product_commit"], integration)
    try:
        published = delivery_git.split_remote_note(root, current["integration_base_commit"],
                                                   item.relative_to(root).as_posix(), delivery.split_note)[0]
    except RuntimeError as exc:
        raise RuntimeError("the Item record that the Item's integration base publishes cannot be read: "
                           f"{exc}") from exc
    refuse_unpublished_paths(root, delivery_id, story, published.get("path_claims"), current["changed_files"],
                             remote)


def refuse_unpublished_paths(root: Path, delivery_id: str, story: str, path_claims, changed: list[str],
                             remote: str = "origin") -> None:
    """Refuse changed product paths that *path_claims* does not cover, naming a provisional claim's
    paths as pending or orphaned."""
    import delivery_git
    outside = delivery_git.paths_outside_claims(
        {path for path in changed if not path.startswith("workspace/docs/")}, path_claims)
    if outside:
        raise RuntimeError(delivery_git.provisional_path_refusal(root, remote, delivery_id, story, outside)
                           or "DELIVERY_PATH_CLAIM_EXCEEDED: the Item's product change lies outside its path"
                              " claims: " + ", ".join(outside))



def repair_review_manifest(root: Path, delivery_id: str, story: str, *, previous=None, current=None) -> dict:
    """Bind a read-only repair review to the last pass, current commit and source inputs."""
    if delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, "repair_review") != "before_freeze":
        return {"required": False, "reason": "repair_review is after_freeze"}
    previous = previous if previous is not None else read_session(root, required=False)
    if previous is None:
        return {"required": False, "reason": "first verification candidate"}
    current = current if current is not None else candidate(root, delivery_id, story, allow_evidence=True)
    old = previous["candidate"]
    if (old["delivery"], old["story"]) != (delivery_id, story):
        raise RuntimeError("repair review belongs to another Item")
    if current == old:
        return {"required": False, "reason": "candidate is unchanged"}
    delta = sorted(set(filter(None, git(root, "diff", "--name-only", "--no-renames", "-z",
                                        old["product_commit"], current["product_commit"]).split("\0"))))
    findings = list(previous.get("unresolved_findings", []))
    for role, worker in previous["workers"].items():
        findings.extend({**finding, "role": role} for finding in worker.get("result", {}).get("findings", []))
    claims = {(finding.get("role"), finding["id"]): finding for finding in findings}
    result = {"required": True, "schema_version": 1, "source_session_id": previous["session_id"],
              "candidate_hash": current["candidate_hash"], "base_commit": old["product_commit"],
              "head_commit": current["product_commit"], "repair_delta": delta,
              "source_inputs": current["inputs"], "instruction_identity": current["instruction_identity"],
              "findings_to_check": [claims[key] for key in sorted(claims)],
              "reader_role": "code-reviewer", "lens": "correctness-contract",
              "full_read": sorted(set(delta) | set(current["inputs"])),
              "instructions": "Read the committed repair diff against the previous findings and approved contracts. "
                              "Check preserved invariants, removed checks, boundary values and whether a repair test "
                              "merely endorses incorrect behavior. Do not edit the candidate or run the verification suite."}
    result["manifest_hash"] = digest(result)
    result["finding_prefix"] = "RR-" + result["manifest_hash"].removeprefix("sha256:")[:12] + "-"
    return result


def repair_review_path(root: Path) -> Path:
    return raw_output_path(root, "scratch/repair-review.json")


def check_repair_review(manifest: dict, result: dict) -> None:
    if (result.get("manifest_hash") != manifest["manifest_hash"]
            or result.get("candidate_hash") != manifest["candidate_hash"]
            or result.get("source_session_id") != manifest["source_session_id"]):
        raise RuntimeError("repair review does not bind the current repair and previous pass")
    if (result.get("role") != "code_reviewer" or result.get("mode") != "repair_review"
            or result.get("verdict") not in {"passed", "failed"}
            or not isinstance(result.get("reviewer"), str) or not result["reviewer"].strip()
            or not isinstance(result.get("report"), str) or not result["report"].strip()):
        raise RuntimeError("repair review requires its independent reader, mode, verdict and report")
    findings = result.get("findings")
    check_findings(findings)
    for finding in findings:
        if (finding["status"] != "open" or not finding["id"].startswith(manifest["finding_prefix"])
                or finding.get("file") not in manifest["full_read"]
                or not isinstance(finding.get("description"), str) or not finding["description"].strip()):
            raise RuntimeError("repair findings require new prefixed IDs, open status, a reviewed file and description")
    if result["verdict"] == "passed" and any(blocking(finding) for finding in findings):
        raise RuntimeError("a repair review with blocking findings cannot pass")


def register_repair_review(root: Path, delivery_id: str, story: str, result: dict) -> dict:
    with locked(root):
        manifest = repair_review_manifest(root, delivery_id, story)
        if not manifest["required"]:
            raise RuntimeError("this candidate needs no pre-freeze repair review")
        check_repair_review(manifest, result)
        record = {"manifest_hash": manifest["manifest_hash"], "result": result, "completed_at": time.time()}
        record["evidence_hash"] = digest(record)
        atomic_file.replace_text(repair_review_path(root), json.dumps(record, indent=2) + "\n")
        return record


def require_repair_review(root: Path, previous: dict | None, current: dict) -> dict | None:
    manifest = repair_review_manifest(root, current["delivery"], current["story"], previous=previous, current=current)
    if not manifest["required"]:
        return None
    try:
        record = json.loads(repair_review_path(root).read_text(encoding="utf-8"))
        if (record["evidence_hash"] != digest({key: value for key, value in record.items() if key != "evidence_hash"})
                or record["manifest_hash"] != manifest["manifest_hash"] or not fresh_record(record)):
            raise ValueError("stale or changed record")
        check_repair_review(manifest, record["result"])
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("a current repair-review-result is required before freezing this candidate") from None
    if record["result"]["verdict"] != "passed" or any(blocking(finding) for finding in record["result"]["findings"]):
        raise RuntimeError("repair review blocks the freeze; repair its findings and review the new diff")
    return record


def review_baseline(previous: dict | None) -> dict | None:
    """The last candidate a code reviewer settled on, the baseline of the next repair review.

    A session whose code reviewer was cancelled reviewed nothing, so it passes
    its own baseline forward instead of becoming one.
    """
    if not previous:
        return None
    if previous.get("workers", {}).get("code_reviewer", {}).get("state") == "settled":
        return previous.get("candidate")
    return previous.get("previous_candidate")


def freeze(root: Path, delivery_id: str, story: str, *, fresh: bool = False, remote: str = "origin") -> dict:
    started = time.monotonic()
    root = root.resolve()
    with locked(root):
        pairing_errors = operation_compile.pairing_findings(delivery.docs_root(root))
        if pairing_errors:
            raise RuntimeError("; ".join(pairing_errors))
        if command_active(root):
            raise RuntimeError("verification command is still running")
        previous = read_session(root, required=False)
        if previous and runtime_needs_cleanup(previous):
            raise RuntimeError("tear the environment down before replacing its verification session")
        if previous and any(worker["state"] == "running" for worker in previous["workers"].values()):
            raise RuntimeError("settle or cancel the existing readers before freezing a new candidate")
        current = candidate(root, delivery_id, story, allow_evidence=True)
        repair_review = require_repair_review(root, previous, current)
        live_repair = require_live_repairs(root, previous, current)
        rehearsals = require_rehearsals(root, delivery_id, story)
        if current.get("assertion_map", {}).get("problems"):
            raise RuntimeError("assertion map: " + "; ".join(current["assertion_map"]["problems"]))
        require_published_claims(root, current, remote)
        # At touched_suites the readers start only on a candidate whose touched
        # earlier suites and own Test Plan targets passed before the freeze.
        pre_handoff = None
        if pre_handoff_regression(root, delivery_id) == TOUCHED_SUITES:
            # The latest run decides, and a run holds the environment lock until it is recorded.
            holder = environment_holder(root)
            if holder is not None:
                raise environment_busy(holder)
            pre_handoff = accepted_pre_handoff(root, delivery_id, story, current)
        if previous and not fresh and previous.get("candidate") == current:
            try:
                validate(root, delivery_id, story)
            except RuntimeError:
                pass
            else:
                return {**previous, "reused": True}
        previous_results = previous.get("workers", {}) if previous else {}
        retained = {(finding["role"], finding["id"]): finding
                    for finding in (previous.get("unresolved_findings", []) if previous else [])}
        for role, worker in previous_results.items():
            result = worker.get("result", {})
            findings = result.get("findings", [])
            # Calibration rulings carry into the next session, so no claim is ruled twice.
            if (role == "code_reviewer" and result.get("calibration")
                    and review_loop(root, delivery_id) == "blocking_delta"):
                findings = calibrated_findings(result)
            for finding in findings:
                key = (role, finding["id"])
                if finding.get("status") == "resolved":
                    retained.pop(key, None)
                else:
                    retained[key] = {**finding, "role": role}
        if repair_review is not None:
            for finding in repair_review["result"]["findings"]:
                retained[("code_reviewer", finding["id"])] = {**finding, "role": "code_reviewer"}
        findings = [retained[key] for key in sorted(retained)]
        value = {"schema_version": 1, "session_id": uuid.uuid4().hex, "candidate": current,
                 "previous_candidate": review_baseline(previous),
                 "unresolved_findings": findings,
                 "workers": {role: {"state": "running", "started_at": time.time()} for role in ROLES},
                 "metrics": {"freeze_seconds": time.monotonic() - started, "command_cache_hits": 0,
                             "command_seconds": 0.0, "model_seconds": None, "model_tokens": None}, "raw_evidence": {}}
        repair_history = list(previous.get("repair_review_history", [])) if previous else []
        if previous and previous.get("repair_review"):
            prior = previous["repair_review"]
            if all(row["evidence_hash"] != prior["evidence_hash"] for row in repair_history):
                repair_history.append(prior)
        if repair_history:
            value["repair_review_history"] = repair_history
        if repair_review is not None:
            value["repair_review"] = repair_review
        if live_repair is not None:
            value["live_repair"] = live_repair
        if rehearsals is not None:
            value["verification_rehearsals"] = rehearsals
        controls = finding_control_history(previous)
        if controls:
            value["finding_control_bindings"] = controls
        if pre_handoff is not None:
            value["pre_handoff"] = pre_handoff
            value["pre_handoff_history"] = pre_handoff_history(root, previous, delivery_id, story)
            value["metrics"]["pre_handoff_seconds"] = pre_handoff["duration_seconds"]
        history = panel_history(previous)
        if history:
            value["panel_history"] = history
        fresh_history = fresh_runtime_history(previous)
        if fresh_history:
            value["fresh_runtime_history"] = fresh_history
        write_session(root, value)
        scope = session_path(root).parent / "mutation-files.json"
        atomic_file.replace_text(scope, json.dumps({"candidate_hash": current["candidate_hash"],
                                                   "scope": "whole_changed_files", "files": current["mutation_files"]}, indent=2) + "\n")
        return value



# Switch repair_pass at scoped_repair: a repair of fixable findings below critical
# is settled by its pre-freeze repair review and the touched-suites run, without
# a new full reader pass over unchanged scope.
REPAIR_PASS, SCOPED_REPAIR = "repair_pass", "scoped_repair"


def is_test_path(name: str) -> bool:
    rules = policy()["mutation_scope"]
    path = PurePosixPath(name)
    return (any(part in rules["test_path_segments"] for part in path.parts[:-1])
            or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in rules["test_name_patterns"]))


def scoped_repair_refusal(root: Path, delivery_id: str, story: str, previous: dict | None,
                          current: dict) -> str | None:
    """Why the repaired candidate needs a full pass, or None when a scoped repair may settle it."""
    docs = delivery.docs_root(root)
    value = lambda name: delivery.delivery_switch_value(docs, delivery_id, name)
    if value(REPAIR_PASS) != SCOPED_REPAIR:
        return "scoped-repair runs at switch repair_pass scoped_repair"
    if value("repair_review") != "before_freeze":
        return "scoped_repair needs repair_review before_freeze, whose reader reviews the repair diff"
    if pre_handoff_regression(root, delivery_id) != TOUCHED_SUITES:
        return "scoped_repair needs pre_handoff_regression touched_suites, which runs the touched suites"
    if previous is None or previous.get("candidate") == current:
        return "scoped_repair settles a repaired candidate of an earlier verification pass"
    old = previous["candidate"]
    if (old["delivery"], old["story"]) != (delivery_id, story):
        return "the earlier verification pass belongs to another Item"
    if any(old.get(key) != current.get(key) for key in ("inputs", "instruction_identity")):
        return "changed source or instruction bindings need a full verification pass"
    if current["runtime_required"]:
        # shortcut: a runtime Item always takes a full pass; upgrade when live evidence can be scoped.
        return "a runtime_required Item needs a full pass with fresh runtime evidence"
    results = []
    for role in ROLES:
        worker = previous["workers"][role]
        result = worker.get("result", {})
        if worker.get("state") != "settled" or result.get("mode") not in policy()["final_modes"][role]:
            return f"the earlier pass has no settled final {role} result"
        results.append(result)
    if all(result.get("verdict") == "passed" for result in results):
        return "the earlier pass passed; there is nothing to repair"
    findings = [finding for result in results for finding in result.get("findings", [])]
    findings += previous.get("unresolved_findings", [])
    if any(critical(finding) for finding in findings if finding.get("status") != "resolved"):
        return "a critical finding needs a full verification pass"
    delta = sorted(set(filter(None, git(root, "diff", "--name-only", "--no-renames", "-z",
                                        old["product_commit"], current["product_commit"]).split("\0"))))
    derived = derive_regression(root, delivery_id, story, current)
    if derived["kind"] != "test":
        selected = derived["affected_test_ids"]
        missed = [name for name in delta if is_test_path(name)
                  and not any(identifier == name or identifier.startswith(name + "::") for identifier in selected)]
        if missed:
            return "the touched-suites run selects no test of " + ", ".join(missed)
    return None


def scoped_repair(root: Path, delivery_id: str, story: str, remote: str = "origin") -> dict:
    """Freeze a repaired candidate and settle both readers from its repair review and touched-suites run."""
    root = root.resolve()
    previous = read_session(root, required=False)
    current = candidate(root, delivery_id, story, allow_evidence=True)
    refusal = scoped_repair_refusal(root, delivery_id, story, previous, current)
    if refusal:
        raise RuntimeError(f"DELIVERY_SCOPED_REPAIR_REFUSED: {refusal}")
    frozen = freeze(root, delivery_id, story, fresh=True, remote=remote)
    with locked(root):
        value = read_session(root)
        if value["session_id"] != frozen["session_id"] or not value.get("repair_review") or not value.get("pre_handoff"):
            raise RuntimeError("DELIVERY_SCOPED_REPAIR_REFUSED: the frozen session lacks its repair review"
                               " or touched-suites run")
        record = {"source_session_id": previous["session_id"], "candidate_hash": current["candidate_hash"],
                  "repair_review_evidence_hash": value["repair_review"]["evidence_hash"],
                  "pre_handoff_evidence_hash": value["pre_handoff"]["evidence_hash"]}
        record["evidence_hash"] = digest(record)
        value["scoped_repair"] = record
        evidence = (f"Scoped repair: repair review {record['repair_review_evidence_hash']} passed on the"
                    f" repair diff and the touched-suites run {record['pre_handoff_evidence_hash']} passed;"
                    f" unchanged scope keeps the results of session {previous['session_id']}.")
        for role in ROLES:
            earlier = previous["workers"][role]["result"]
            findings = [{**finding, "status": "resolved"} if blocking(finding) and finding["status"] == "open"
                        else finding for finding in earlier.get("findings", [])]
            if role == "code_reviewer":
                findings += value["repair_review"]["result"]["findings"]
                checks = {name: {"passed": True, "evidence": evidence} for name in policy()["review_checks"]}
            else:
                checks = {"scoped_regression": {"passed": True, "evidence": evidence,
                                                "raw_evidence_hash": record["pre_handoff_evidence_hash"]}}
            result = {"candidate_hash": current["candidate_hash"], "session_id": value["session_id"],
                      "role": role, "mode": SCOPED_REPAIR, "verdict": "passed", "checks": checks,
                      "findings": findings, "scoped_repair_hash": record["evidence_hash"],
                      "report": f"{evidence}\n\nEarlier {role} report:\n\n{earlier['report']}"}
            result["result_hash"] = digest(result)
            value["workers"][role] = {"state": "settled", "result": result, "completed_at": time.time(),
                                      "reader_elapsed_seconds": 0.0}
        write_session(root, value)
        return value


def scoped_repair_problem(value: dict, result: dict) -> str | None:
    """Why a scoped_repair result does not stand on its session's repair review and touched-suites run."""
    record = value.get("scoped_repair") or {}
    if (record.get("evidence_hash") != digest({key: item for key, item in record.items() if key != "evidence_hash"})
            or result.get("scoped_repair_hash") != record.get("evidence_hash")
            or record.get("candidate_hash") != value["candidate"]["candidate_hash"]
            or record.get("repair_review_evidence_hash") != (value.get("repair_review") or {}).get("evidence_hash")
            or record.get("pre_handoff_evidence_hash") != (value.get("pre_handoff") or {}).get("evidence_hash")):
        return "scoped repair result does not bind this session's repair review and touched-suites run"
    return None


def prepare_repair(root: Path) -> dict:
    """Create an independent repair checkout without changing the frozen Item."""
    root = root.resolve()
    with locked(root):
        session = read_session(root)
        current = require_current(root, session, allow_evidence=True)
        if delivery.delivery_switch_value(delivery.docs_root(root), current["delivery"],
                                          "lane_isolation") != "scratch_clone":
            raise RuntimeError("overlapping repair requires lane_isolation scratch_clone")
        if not any(worker["state"] == "running" for worker in session["workers"].values()):
            raise RuntimeError("overlapping repair requires a reader still running")
        findings = {(finding.get("role", "code_reviewer"), finding["id"]): finding
                    for finding in session.get("unresolved_findings", []) if finding.get("status") == "open"}
        for role, worker in session["workers"].items():
            for finding in worker.get("result", {}).get("findings", []):
                if finding.get("status") == "open":
                    findings[(role, finding["id"])] = {**finding, "role": role}
        if not findings:
            raise RuntimeError("overlapping repair requires a registered open finding")
        existing = session.get("repair_workspace")
        if existing is not None:
            path = Path(existing["path"])
            if (path.is_symlink() or not path.is_dir() or root == path.resolve()
                    or root in path.resolve().parents
                    or existing["candidate_hash"] != current["candidate_hash"]):
                raise RuntimeError("recorded repair workspace is missing, unsafe or bound to another candidate")
            temporary = Path(tempfile.gettempdir()).resolve()
            if path.parent.parent.resolve() != temporary or not path.parent.name.startswith("agentrof-repair-"):
                raise RuntimeError("recorded repair workspace is outside its private directory")
            for component in (path.parent, path, path / ".git"):
                metadata = component.lstat()
                if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    raise RuntimeError("recorded repair workspace contains an alias")
            if (Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve()
                    or git(path, "branch", "--show-current") != existing["branch"]
                    or git(path, "remote", "get-url", "origin") != str(root)
                    or (path / ".git/objects/info/alternates").exists()
                    or subprocess.run(["git", "-C", str(path), "merge-base", "--is-ancestor",
                                       existing["base_commit"], "HEAD"], capture_output=True).returncode):
                raise RuntimeError("recorded repair workspace repository identity changed")
            existing["findings"] = [findings[key] for key in sorted(findings)]
            write_session(root, session)
            return {**existing, "reused": True}
        parent = Path(tempfile.mkdtemp(prefix="agentrof-repair-"))
        checkout = parent / "checkout"
        try:
            clone_private_checkout(root, checkout, current["product_commit"])
            branch = "repair/" + session["session_id"][:12]
            git(checkout, "checkout", "-b", branch)
            require_current(root, session, allow_evidence=True)
        except BaseException:
            shutil.rmtree(parent)
            raise
        record = {"path": str(checkout), "branch": branch, "session_id": session["session_id"],
                  "candidate_hash": current["candidate_hash"], "base_commit": current["product_commit"],
                  "findings": [findings[key] for key in sorted(findings)]}
        session["repair_workspace"] = record
        write_session(root, session)
        return {**record, "reused": False}


def require_current(root: Path, value: dict, *, allow_evidence: bool = False) -> dict:
    bound = value["candidate"]
    current = candidate(root, bound["delivery"], bound["story"], allow_evidence=allow_evidence)
    if current != bound:
        raise RuntimeError("verification candidate or source bindings changed; freeze and rerun both roles")
    return current


def required_checks(root: Path, current: dict, role: str) -> list[str]:
    if role == "code_reviewer":
        return policy()["review_checks"]
    checks = list(policy()["qa_checks"])
    docs = delivery.docs_root(root)
    props, _ = delivery.split_note(docs / "operation/verification-contract.md")
    if props.get("mutation_disposition") == "required":
        checks.append("mutation_whole_changed_files")
    if props.get("dependency_audit_disposition") == "required":
        checks.append("dependency_audit")
    if current["runtime_required"]:
        checks.append("fresh_runtime")
    return checks


def raw_output_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (not delivery._is_normalized_claim(relative) or ":" in relative
            or any(part.rstrip(". ") != part for part in parts)
            or not parts or parts[0] != "scratch" or len(parts) != 2
            or PurePosixPath(relative).is_absolute() or any(part in {".", ".."} for part in parts)):
        raise RuntimeError("raw verification output must be an isolated scratch file")
    path = session_path(root).parent / relative
    return safe_runtime_path(root, path, file_only=True)


def command_environment(root: Path, current: dict, *, diagnostic: bool = False) -> dict:
    environment = dict(os.environ)
    environment["AGENTROF_DELIVERY_ID"] = current["delivery"]
    environment["AGENTROF_ITEM_ID"] = current["story"]
    environment["AGENTROF_MUTATION_FILES"] = str(session_path(root).parent / "mutation-files.json")
    for name in SELECTION_VARIABLES:
        environment.pop(name, None)
    if diagnostic:
        environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(session_path(root).parent / "diagnostic-tests.json")
    environment["AGENTROF_VERIFICATION_SCRATCH"] = str(session_path(root).parent / "scratch")
    return environment


def verification_contract(root: Path) -> dict:
    return delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")[0]


def contract_variables(contract: dict) -> list[str]:
    """The environment variables the approved Verification Contract declares for its commands.

    The contract check refuses the same lists, so a hand-edited contract
    refuses here before any command runs.
    """
    names = contract.get("command_variables", [])
    problem = operation_compile.command_variable_problem(names)
    if problem:
        raise RuntimeError(problem)
    return names


def variable_value(environment: dict, name: str) -> str | None:
    """A variable's value, or None while it is unset; Windows matches its names without case."""
    if name in environment:
        return environment[name]
    if os.name == "nt":
        return next((value for key, value in environment.items() if key.upper() == name.upper()), None)
    return None


def identity_key(root: Path) -> bytes:
    """The random key of the Item's verification runtime that keys every run identity's environment hash.

    The key never leaves the runtime, where every identity is compared, so a
    hash copied into tracked evidence checks no guess of a value. The caller
    holds the session lock, so one key is created once. A runtime that lost its
    key starts a new one, and no identity hashed with the old key matches a new
    one, so nothing recorded before is reused.
    """
    path = safe_runtime_path(root, session_path(root).parent / IDENTITY_KEY, file_only=True)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = b""
    if len(key) != IDENTITY_KEY_BYTES:
        key = secrets.token_bytes(IDENTITY_KEY_BYTES)
        atomic_file.replace_bytes(path, key, mode=0o600)
    return key


def environment_identity(root: Path, environment: dict, contract: dict | None = None) -> dict:
    """The identity of a command's environment: the declared variables it covers and the host.

    It names every variable it covers, an unset one too, and keeps only a hash
    of their values keyed with the runtime's identity key, so no value is
    written and no guess of one can be checked against the hash. The caller
    holds the session lock.
    """
    names = set(COMMAND_VARIABLES) | set(contract_variables(verification_contract(root) if contract is None
                                                             else contract))
    names |= {name for name in environment if name.startswith(COMMAND_VARIABLE_PREFIXES)}
    values = {name: variable_value(environment, name) for name in sorted(names - set(SELECTION_VARIABLES))}
    return {"environment_variables": sorted(values), "environment_hash": keyed_digest(identity_key(root), values),
            "python": sys.version, "platform": platform.platform(), "git": git(root, "--version")}


def environment_problem(identity: object, contract: dict) -> str | None:
    """Why a recorded environment identity is incomplete or inconsistent with the approved contract, or None.

    It is checked as recorded, never against the environment of the process that checks it.
    """
    if not isinstance(identity, dict):
        return "records no environment identity"
    names = identity.get("environment_variables")
    if names is None and "environment_hash" in identity:
        return LEGACY_ENVIRONMENT
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or names != sorted(set(names))):
        return "must name each variable it covers once, in order"
    declared = {*COMMAND_VARIABLES, *contract_variables(contract), *RUNNER_VARIABLES}
    missing = sorted(declared - set(names))
    if missing:
        return "does not cover " + ", ".join(missing)
    stray = [name for name in names if name not in declared
             and (name in SELECTION_VARIABLES or not name.startswith(COMMAND_VARIABLE_PREFIXES))]
    if stray:
        return "covers " + ", ".join(stray) + ", which no declaration names"
    if not isinstance(identity.get("environment_hash"), str) \
            or not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", identity["environment_hash"]):
        return "records no environment hash"
    host = [key for key in ("python", "platform", "git")
            if not isinstance(identity.get(key), str) or not identity[key].strip()]
    if host:
        return "records no " + ", ".join(host)
    return None


def literal_test_id(identifier) -> bool:
    """Whether a test id passes as selection data: nonempty, unpadded, no option prefix or control character."""
    return (isinstance(identifier, str) and bool(identifier) and identifier == identifier.strip()
            and not identifier.startswith("-")
            and not any(ord(character) < 32 or ord(character) == 127 for character in identifier))


FINDING_DIAGNOSTIC_SCOPE = "finding_diagnostic"


def test_id_in_file(identifier: str, path: str) -> bool:
    """Whether a test id names a test in *path*, as a path::name or a dotted module.name id."""
    module = PurePosixPath(path).with_suffix("").as_posix().replace("/", ".")
    return identifier.startswith(path + "::") or bool(module) and identifier.startswith(module + ".")


def finding_control_history(session: dict | None) -> list[dict]:
    """Carry successful finding bindings across passes until ordinary backlog absorption."""
    if not session:
        return []
    rows = list(session.get("finding_control_bindings", []))
    known = {digest(row) for row in rows}
    for raw in session.get("raw_evidence", {}).values():
        if raw.get("exit_code") != 0 or raw.get("candidate_intact") is not True or raw.get("selection_intact") is not True:
            continue
        for binding in raw.get("diagnostic_selection", {}).get("finding_controls", []):
            row = {**binding, "candidate_hash": raw["identity"]["candidate_hash"], "evidence_hash": raw["evidence_hash"]}
            key = digest(row)
            if key not in known:
                rows.append(row)
                known.add(key)
    return rows


def finding_controls(entries, current: dict, findings: list[dict]) -> list[dict]:
    """The negative-control tests open findings prescribe, checked as diagnostic data.

    Each entry names an open inherited finding and test ids the finding lists
    in negative_control_test_ids; each id lies in a test file the Item itself
    changed. Such a run is finding-scoped diagnostic evidence only: it never
    serves an acceptance check, so no Test Plan scenario has to bind the ids.
    """
    if not isinstance(entries, list):
        raise RuntimeError("finding_controls must be a list of {finding_id, test_ids}")
    open_findings = {finding["id"]: finding for finding in findings
                     if isinstance(finding, dict) and finding.get("status") != "resolved"}
    changed = set(current["changed_files"])
    seen = set()
    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"finding_id", "test_ids"}:
            raise RuntimeError("finding_controls entries declare only finding_id and test_ids")
        finding = open_findings.get(entry["finding_id"])
        if finding is None:
            raise RuntimeError(f"finding_controls names {entry['finding_id']!r}, which is no open inherited finding")
        identifiers = entry["test_ids"]
        if (not isinstance(identifiers, list) or not identifiers
                or any(not literal_test_id(identifier) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise RuntimeError("finding control test IDs must be unique nonempty literal identifiers")
        prescribed = set(finding.get("negative_control_test_ids") or [])
        stray = sorted(set(identifiers) - prescribed)
        if stray:
            raise RuntimeError(f"finding {finding['id']} prescribes no negative control " + ", ".join(stray))
        outside = sorted(identifier for identifier in identifiers
                         if not any(test_id_in_file(identifier, path) for path in changed))
        if outside:
            raise RuntimeError("finding control tests must lie in test files the Item changed: " + ", ".join(outside))
        if finding["id"] in seen:
            raise RuntimeError(f"finding_controls names {finding['id']} more than once")
        seen.add(finding["id"])
        rows.append({"finding_id": finding["id"], "test_ids": sorted(identifiers)})
    return sorted(rows, key=lambda row: row["finding_id"])


def diagnostic_selection(root: Path, path: Path, current: dict, findings: list[dict] | None = None) -> dict:
    """Treat focused test identifiers as data, never command arguments or code."""
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(session_path(root).parent).as_posix()
    except ValueError as exc:
        raise RuntimeError("diagnostic selection must be a regular file inside verification scratch") from exc
    if not path.is_file():
        raise RuntimeError("diagnostic selection must be a regular file inside verification scratch")
    path = raw_output_path(root, relative)
    value = json.loads(path.read_text(encoding="utf-8"))
    fields = {"schema_version", "candidate_hash", "failed_test_ids", "affected_test_ids"}
    if (not isinstance(value, dict) or set(value) - {"finding_controls"} != fields
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("candidate_hash") != current["candidate_hash"]):
        raise RuntimeError("diagnostic selection must bind this candidate and declare only schema_version, candidate_hash, failed_test_ids and affected_test_ids, with optional finding_controls")
    for name in ("failed_test_ids", "affected_test_ids"):
        identifiers = value[name]
        if (not isinstance(identifiers, list) or any(not literal_test_id(identifier) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise RuntimeError("diagnostic test IDs must be unique nonempty literal identifiers, without option prefixes or control characters")
        value[name] = sorted(identifiers)
    controls = finding_controls(value.pop("finding_controls", []), current, findings or [])
    control_ids = sorted({identifier for row in controls for identifier in row["test_ids"]})
    if controls:
        value.update(finding_controls=controls, finding_control_test_ids=control_ids,
                     evidence_scope=FINDING_DIAGNOSTIC_SCOPE)
    value["selected_test_ids"] = sorted(set(value["failed_test_ids"]) | set(value["affected_test_ids"]) | set(control_ids))
    if not value["selected_test_ids"]:
        raise RuntimeError("diagnostic selection must include at least one failed or affected test ID")
    return value


def clone_private_checkout(root: Path, execution_root: Path, commit: str) -> None:
    """Check *commit* out in a new clone of *root* with its own objects, index and files.

    Git for Windows creates no file whose absolute path reaches MAX_PATH, 260
    characters, unless core.longpaths is set, yet its checkout exits 0. A
    clone deep in the verification scratch would then lack a tracked file the
    project holds and never count as intact, so the clone sets core.longpaths
    in its own config. Git on other hosts ignores the key.
    """
    git(root, "clone", "--config", "core.longpaths=true", "--no-local", "--no-hardlinks", "--no-checkout",
        "--", str(root), str(execution_root))
    git(execution_root, "checkout", "--detach", commit)


# The one private runtime checkout of an Item, kept across verification sessions.
RUNTIME_CHECKOUT = "runtime"


def runtime_checkout_name(scratch: Path, delivery_id: str, story: str) -> str:
    """The kept runtime checkout's folder name, distinct per Item.

    A project environment may name its instance after the checkout folder, as
    the docker-compose skill's entry point does, so Items running at once need
    distinct names. A checkout an older package kept under the shared name
    keeps it, so its running instance is still torn down.
    """
    if (scratch / RUNTIME_CHECKOUT).exists():
        return RUNTIME_CHECKOUT
    return "-".join([RUNTIME_CHECKOUT, *re.findall(r"[a-z0-9]+", f"{delivery_id}-{story}".lower())])


def repin_private_checkout(root: Path, execution_root: Path, commit: str) -> None:
    """Recreate private repository state while preserving the environment's checkout pathname."""
    safe_runtime_path(root, execution_root)
    def remove_readonly(function, path, _error):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        function(path)
    shutil.rmtree(execution_root, onerror=remove_readonly)
    clone_private_checkout(root, execution_root, commit)


# How many paths a checkout difference names before it counts the rest.
CHECKOUT_DIFFERENCE_LIMIT = 5


def checkout_difference(execution_root: Path) -> str:
    """Name the tracked paths where a private checkout differs from its HEAD, or return "" if none does.

    A path the checkout lacks is named missing, any other changed: Git can
    leave a tracked file out of a checkout that exits 0, and the clone then
    differs from its commit before any command ran. At most
    CHECKOUT_DIFFERENCE_LIMIT paths are named, then how many more differ.
    """
    fields = git(execution_root, "diff", "--name-status", "--no-renames", "-z", "HEAD").split("\0")
    named = [("missing " if status == "D" else "changed ") + path for status, path in zip(fields[::2], fields[1::2])]
    more = len(named) - CHECKOUT_DIFFERENCE_LIMIT
    return ", ".join(named[:CHECKOUT_DIFFERENCE_LIMIT]) + (f" and {more} more" if more > 0 else "")


def host_guard(root: Path) -> dict:
    """The Verification Contract's opt-in host guard: keep the host awake, and the reachability probe target."""
    try:
        contract, _body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    except (OSError, ValueError, RuntimeError):
        return {"keep_awake": False, "probe": None}
    return {"keep_awake": contract.get(operation_compile.HOST_KEEP_AWAKE) == operation_compile.INHIBIT_SLEEP,
            "probe": contract.get(operation_compile.REACHABILITY_PROBE)}


@contextlib.contextmanager
def host_awake(enabled: bool):
    """Hold an idle-sleep inhibitor bound to this process while a command runs.

    The yielded record names the inhibitor, or carries keep_awake_warning when
    the host has none or it exits early; the command runs either way.
    """
    state: dict = {}
    if not enabled:
        yield state
        return
    pid = str(os.getpid())
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        argv = ["caffeinate", "-i", "-w", pid]
    elif shutil.which("systemd-inhibit") and shutil.which("tail"):
        argv = ["systemd-inhibit", "--what=sleep:idle", "--who=agent-marketplace", "--why=verification command",
                "--mode=block", "tail", f"--pid={pid}", "-f", "/dev/null"]
    else:
        state["keep_awake_warning"] = "neither caffeinate nor systemd-inhibit exists; the host may sleep during the command"
        yield state
        return
    try:
        inhibitor = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
    except OSError as exc:
        state["keep_awake_warning"] = f"{argv[0]} could not start: {exc}"
        yield state
        return
    state["keep_awake"] = argv[0]
    try:
        yield state
    finally:
        if inhibitor.poll() is not None:
            del state["keep_awake"]
            state["keep_awake_warning"] = f"{argv[0]} exited with {inhibitor.returncode} before the command ended"
        else:
            inhibitor.terminate()
            try:
                inhibitor.wait(5)
            except subprocess.TimeoutExpired:
                inhibitor.kill()


def private_checkout_run(root: Path, scratch: Path, commit: str, workdir: str, command: str, environment: dict,
                         *, isolate_search_paths: bool = False,
                         cloned=None, live_output: Path | None = None) -> tuple[subprocess.CompletedProcess, bool, str, dict, dict]:
    """Run an approved command verbatim in a private clone of one exact commit.

    Returns the completed command, whether the clone still holds that commit
    unchanged, the checkout difference that names where it does not, with
    *isolate_search_paths* the interpreter search path entries dropped because
    they resolve outside the clone, and the environment the command ran in.
    *cloned*, when given, is called once the clone holds the commit, before
    the command runs.
    """
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=scratch) as temporary:
        # A distinct folder name per run keeps an environment named after it apart from other runs.
        execution_root = Path(temporary) / ("checkout-" + Path(temporary).name.removeprefix("candidate-").lower())
        # A private repository gives mutation tools their own index, objects and
        # files; no transient mutant can enter the independent reviewer's view.
        clone_private_checkout(root, execution_root, commit)
        execution_directory = (execution_root / workdir).resolve()
        if execution_directory != execution_root.resolve() and execution_root.resolve() not in execution_directory.parents:
            raise RuntimeError("verification workdir escapes its isolated checkout")
        if cloned is not None:
            cloned()
        dropped: dict[str, list[str]] = {}
        if isolate_search_paths:
            environment, dropped = lane_command_environment(execution_root.resolve(), execution_directory, environment)
        with host_awake(host_guard(root)["keep_awake"]) as awake:
            if live_output is None:
                completed = subprocess.run(command, cwd=execution_directory, env=environment, shell=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            else:
                with live_output.open("xb") as output:
                    completed = subprocess.run(command, cwd=execution_directory, env=environment, shell=True,
                                               stdout=output, stderr=subprocess.STDOUT, check=False)
                completed.stdout = live_output.read_bytes()
        if awake:
            # The tuple callers unpack stays as it is; the run's record reads this attribute.
            completed.host_awake = awake
        from delivery_git import require_visible_item_index
        difference = ""
        try:
            require_visible_item_index(execution_root)
            intact = (git(execution_root, "rev-parse", "HEAD") == commit
                      and not git(execution_root, "diff", "--name-only", "HEAD"))
            difference = "" if intact else checkout_difference(execution_root)
        except RuntimeError:
            intact = False
    return completed, intact, difference, dropped, environment



LIVE_PROGRESS_INTERVAL = 1.0
LIVE_PROGRESS_STALE = 30.0
# With step_timing at recorded for the Delivery, the runner times its own QA
# steps in the Delivery's timing run under the QA keys of step-budgets.json.
TIMING_SWITCHES = {"step_timing", "step_budgets", "step_baseline"}


def timing_values(root: Path, delivery_id: str) -> dict | None:
    try:
        values = process_policy.delivery_values(delivery.docs_root(root), delivery_id,
                                                switches=TIMING_SWITCHES)["values"]
    except (ValueError, OSError, RuntimeError, KeyError):
        return None
    return values if step_timing.recording(values) else None


def timing_start(root: Path, values: dict | None, delivery_id: str, budget: str, name: str,
                 watch: Path | None = None) -> tuple | None:
    """Start a QA step span; timing never changes or stops a verification command."""
    if values is None:
        return None
    anchor = runtime_anchor(root.resolve()).resolve()
    step = budget + ":" + (re.sub(r"[^A-Za-z0-9._/:-]", "_", name)[:100] or "_")
    try:
        relative = watch.resolve().relative_to(anchor).as_posix() if watch is not None else None
        return anchor, values, delivery_id, step_timing.start(
            anchor, values, run=delivery_id, step=step, budget=budget, watch=relative)["span"]
    except (step_timing.Refused, OSError, ValueError):
        return None


def timing_end(handle: tuple | None) -> dict | None:
    """End a QA step span and return its overrun, if any."""
    if handle is None:
        return None
    anchor, values, run, span = handle
    try:
        return step_timing.end(anchor, values, run=run, span=span).get("overrun")
    except (step_timing.Refused, OSError, ValueError):
        return None
# A sleep-counting clock passing the awake clock by this much between two heartbeats is a host sleep.
SLEEP_GAP_SECONDS = 30.0
PROBE_INTERVAL = 30.0
PROBE_TIMEOUT = 5.0
INTERRUPTION_KINDS = ("host_sleep", "network_loss")


def host_clocks() -> tuple[float, float]:
    """A clock that counts host sleep and one that does not, in seconds.

    Linux has boot time and monotonic time; on macOS the monotonic clock
    counts sleep and the uptime clock does not. Elsewhere wall time and
    monotonic time stand in, which a wall clock adjustment can also move.
    """
    for counting, awake in (("CLOCK_BOOTTIME", "CLOCK_MONOTONIC"), ("CLOCK_MONOTONIC", "CLOCK_UPTIME_RAW")):
        if hasattr(time, counting) and hasattr(time, awake):
            return time.clock_gettime(getattr(time, counting)), time.clock_gettime(getattr(time, awake))
    return time.time(), time.monotonic()


def reachable(target: str) -> bool:
    """Whether a DNS lookup and a TCP connection to host:port succeed within the probe timeout."""
    host, _, port = target.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=PROBE_TIMEOUT):
            return True
    except (OSError, ValueError):
        return False


def live_progress_path(root: Path, record_key: str) -> Path:
    name = hashlib.sha256(record_key.encode("utf-8")).hexdigest()[:20]
    return raw_output_path(root, f"scratch/progress-{name}.json")


@contextlib.contextmanager
def live_progress(root: Path, session_id: str, current: dict, record_key: str, group: str):
    """Heartbeat and adapter observations are status only, never passing evidence."""
    progress = live_progress_path(root, record_key)
    output = raw_output_path(root, "scratch/live-output-" + uuid.uuid4().hex + ".log")
    adapter = raw_output_path(root, "scratch/live-progress.json")
    if adapter.exists():
        adapter.unlink()
    started = time.time()
    stopped = threading.Event()
    values = timing_values(root, current["delivery"])
    step = {"test_id": None, "span": None}

    def step_seen(test_id):
        if test_id != step["test_id"]:
            timing_end(step["span"])
            step.update(test_id=test_id, span=None if test_id is None else timing_start(
                root, values, current["delivery"], "live_step", test_id, progress))

    guard = host_guard(root)
    interruptions: list[dict] = []
    clocks = {"last": host_clocks()}

    def observe_sleep():
        counting, awake = host_clocks()
        gap = (counting - clocks["last"][0]) - (awake - clocks["last"][1])
        clocks["last"] = (counting, awake)
        if gap > SLEEP_GAP_SECONDS:
            interruptions.append({"kind": "host_sleep", "detected_at": time.time(), "seconds": round(gap, 1)})

    def probe():
        while not stopped.wait(PROBE_INTERVAL):
            ok = reachable(guard["probe"])
            open_loss = next((row for row in interruptions if row["kind"] == "network_loss"
                              and "restored_at" not in row), None)
            if not ok and open_loss is None:
                interruptions.append({"kind": "network_loss", "detected_at": time.time(), "target": guard["probe"]})
            elif ok and open_loss is not None:
                open_loss["restored_at"] = time.time()

    def update(state="running"):
        observed = {}
        if adapter.is_file() and adapter.stat().st_size <= 65536:
            try:
                value = json.loads(safe_runtime_path(root, adapter, file_only=True).read_text(encoding="utf-8"))
                for key in ("cases_done", "cases_total", "failures"):
                    if type(value.get(key)) is int and value[key] >= 0:
                        observed[key] = value[key]
                if (isinstance(value.get("test_id"), str) and len(value["test_id"]) <= 512
                        and value["test_id"].isprintable()):
                    observed["test_id"] = value["test_id"]
            except (OSError, ValueError, RuntimeError, AttributeError):
                pass
        now = time.time()
        payload = {"session_id": session_id, "candidate_hash": current["candidate_hash"],
                   "record_key": record_key, "live_group": group, "state": state,
                   "started_at": started, "updated_at": now, "elapsed_seconds": now - started,
                   "output_path": str(output), "output_bytes": output.stat().st_size if output.exists() else 0,
                   "adapter_observation": observed}
        if guard["keep_awake"]:
            observe_sleep()
        if interruptions:
            payload["interruptions"] = [dict(row) for row in interruptions]
        atomic_file.replace_text(safe_runtime_path(root, progress, file_only=True),
                                 json.dumps(payload, sort_keys=True) + "\n")
        if values is not None:
            step_seen(None if state == "finished" else observed.get("test_id"))

    def heartbeat():
        while not stopped.wait(LIVE_PROGRESS_INTERVAL):
            update()

    update()
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    prober = threading.Thread(target=probe, daemon=True) if guard["probe"] else None
    if prober is not None:
        prober.start()
    try:
        yield output
    finally:
        stopped.set()
        worker.join()
        # A probe stuck in a DNS lookup never delays the run's record.
        update("finished")


def live_progress_records(root: Path, session: dict) -> dict:
    result = {}
    scratch = session_path(root).parent / "scratch"
    for path in scratch.glob("progress-*.json"):
        try:
            path = safe_runtime_path(root, path, file_only=True)
            if path.stat().st_size > 65536:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                continue
            if (value.get("session_id") != session["session_id"]
                    or value.get("candidate_hash") != session["candidate"]["candidate_hash"]):
                continue
            if (value.get("state") not in {"running", "finished"}
                    or not isinstance(value.get("record_key"), str)
                    or not isinstance(value.get("updated_at"), (int, float))
                    or not math.isfinite(value["updated_at"])):
                continue
            if value.get("state") == "running" and time.time() - value["updated_at"] > LIVE_PROGRESS_STALE:
                value["state"] = "stale"
            result[value["record_key"]] = value
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            continue
    return result


def run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None,
              spot_file: Path | None = None, group: str | None = None, resume: bool = False) -> dict:
    root = root.resolve()
    read_session(root)
    with environment_lock(root, "qa_engineer", "run --kind " + kind), \
            command_lock(root, "qa_engineer", "run --kind " + kind):
        return _run_check(root, kind, fresh=fresh, selection_file=selection_file, spot_file=spot_file, group=group, resume=resume)



def valid_live_checkpoint(value) -> bool:
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("failure") != "infrastructure_preflight"
            or value.get("preflight") not in {"dns", "tls", "registry"}
            or type(value.get("failed_cases")) is not int or value["failed_cases"] != 0
            or type(value.get("started_cases_in_phase")) is not int or value["started_cases_in_phase"] != 0):
        return False
    completed, pending = value.get("completed_phases"), value.get("pending_phases")
    if not isinstance(completed, dict) or not isinstance(pending, list) or not pending:
        return False
    if any(not isinstance(name, str) or not operation_compile.LIVE_GROUP_RE.fullmatch(name)
           for name in [*completed, *pending]):
        return False
    evidence = value.get("phase_evidence", {})
    if not isinstance(evidence, dict) or set(evidence) != set(completed):
        return False
    if any(not isinstance(row, dict) or set(row) != {"file", "sha256"}
           or not isinstance(row["file"], str) or row["sha256"] != completed[name]
           for name, row in evidence.items()):
        return False
    return (len(pending) == len(set(pending)) and not set(completed).intersection(pending)
            and all(isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                    for value in completed.values()))


def checkpoint_evidence_current(root: Path, value: dict) -> bool:
    try:
        for row in value.get("phase_evidence", {}).values():
            path = raw_output_path(root, row["file"])
            if not row["file"].startswith("scratch/") or not path.is_file() or path.stat().st_size > 1024 * 1024:
                return False
            if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def live_checkpoint(root: Path) -> dict | None:
    path = raw_output_path(root, "scratch/live-checkpoint.json")
    try:
        if not path.is_file() or path.stat().st_size > 65536:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if valid_live_checkpoint(value) and checkpoint_evidence_current(root, value) else None
    except (OSError, ValueError, TypeError):
        return None


def live_interruptions(root: Path, record_key: str, exit_code: int) -> dict:
    """The host interruptions a live run's progress saw: advisory on every run, interrupted_by only on a failure."""
    try:
        shown = json.loads(live_progress_path(root, record_key).read_text(encoding="utf-8"))
        seen = sorted({row["kind"] for row in shown.get("interruptions", [])
                       if isinstance(row, dict) and row.get("kind") in INTERRUPTION_KINDS})
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    if not seen:
        return {}
    return {"interruptions_seen": seen, **({"interrupted_by": seen} if exit_code != 0 else {})}


def resumable_live_run(record: dict | None, current: dict) -> bool:
    """A preflight-only failure, or a run the host interrupted, with the command's valid checkpoint."""
    return bool(record and (record.get("exit_code") == 2 or record.get("interrupted_by"))
                and record.get("candidate_intact") is True
                and record.get("identity", {}).get("candidate_hash") == current["candidate_hash"]
                and record.get("resume_count", 0) == 0 and valid_live_checkpoint(record.get("live_checkpoint"))
                and fresh_record(record)
                and record.get("evidence_hash") == digest({k: v for k, v in record.items() if k != "evidence_hash"}))



def live_case_report(root: Path) -> dict:
    path = raw_output_path(root, "scratch/live-case-report.json")
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("groups"), dict):
        raise RuntimeError("live case report requires schema_version 1 and groups")
    groups = value["groups"]
    for group, cases in groups.items():
        if (not isinstance(group, str) or not operation_compile.LIVE_GROUP_RE.fullmatch(group)
                or not isinstance(cases, dict)
                or any(not literal_test_id(key) or not isinstance(status, str) or status not in {"passed", "failed"}
                       for key, status in cases.items())):
            raise RuntimeError("live case report requires literal test ids and passed or failed outcomes")
    return groups


def failed_live_cases(root: Path, result: dict) -> dict:
    cases = result.get("failed_live_cases", {})
    if not cases:
        return {}
    contract = verification_contract(root)
    if (result.get("role") != "qa_engineer" or result.get("verdict") != "failed"
            or not isinstance(cases, dict) or not contract.get("live_diagnostic_command")):
        raise RuntimeError("failed_live_cases requires failed QA and an approved live_diagnostic_command")
    for group, targets in cases.items():
        if (group not in contract.get("live_groups", []) or not isinstance(targets, list) or not targets
                or any(not isinstance(target, str) or not literal_test_id(target) for target in targets)
                or len(targets) != len(set(targets))):
            raise RuntimeError("failed_live_cases must name declared groups and unique literal test ids")
    session = read_session(root)
    for group, targets in cases.items():
        runs = [raw for raw in session.get("raw_evidence", {}).values()
                if raw.get("identity", {}).get("candidate_hash") == result.get("candidate_hash")
                and group in raw.get("live_case_report", {})]
        if not any(all(raw["live_case_report"][group].get(target) == "failed" for target in targets) for raw in runs):
            raise RuntimeError("failed_live_cases must cite failed cases in recorded live run evidence")
    return {group: sorted(targets) for group, targets in sorted(cases.items())}


def live_repair_record_path(root: Path) -> Path:
    return raw_output_path(root, "scratch/live-repair.json")


def require_live_repairs(root: Path, previous: dict | None, current: dict) -> dict | None:
    if not previous:
        return None
    cases = failed_live_cases(root, previous["workers"].get("qa_engineer", {}).get("result", {}))
    if not cases:
        return None
    try:
        record = json.loads(live_repair_record_path(root).read_text(encoding="utf-8"))
        if (record["source_session_id"] != previous["session_id"]
                or record["candidate_hash"] != current["candidate_hash"] or record["cases"] != cases
                or record["evidence_hash"] != digest({k: v for k, v in record.items() if k != "evidence_hash"})
                or not fresh_record(record) or set(record["runs"]) != set(cases)):
            raise ValueError("repair record binding differs")
        for group, run in record["runs"].items():
            output = raw_output_path(root, run["output_file"])
            if (run["exit_code"] != 0 or run["candidate_intact"] is not True
                    or run.get("cases") != {target: "passed" for target in cases[group]}
                    or run.get("command") != verification_contract(root)["live_diagnostic_command"].replace(operation_compile.LIVE_GROUP_PLACEHOLDER, group)
                    or hashlib.sha256(output.read_bytes()).hexdigest() != run["output_sha256"]):
                raise ValueError("a failed case has no passing intact repair run")
        return record
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("previously failed live cases need a passing repair-live run on this candidate") from None


def repair_live(root: Path, delivery_id: str, story: str) -> dict:
    root = root.resolve()
    guard_write(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "repair-live"), command_lock(root, PRE_HANDOFF_HOLDER, "repair-live"):
        previous = read_session(root)
        current = candidate(root, delivery_id, story, allow_evidence=True)
        cases = failed_live_cases(root, previous["workers"].get("qa_engineer", {}).get("result", {}))
        if not cases:
            raise RuntimeError("no registered failed live cases need a repair run")
        contract = verification_contract(root)
        record = {"source_session_id": previous["session_id"], "candidate_hash": current["candidate_hash"],
                  "cases": cases, "runs": {}}
        for group, targets in cases.items():
            environment = command_environment(root, current)
            selection = {"candidate_hash": current["candidate_hash"], "group": group, "selected_test_ids": targets}
            selector = raw_output_path(root, "scratch/live-repair-selection.json")
            data = json.dumps(selection, sort_keys=True) + "\n"
            atomic_file.replace_text(selector, data)
            generation = source_file_generation(selector)
            report_path = raw_output_path(root, "scratch/live-case-report.json")
            report_path.unlink(missing_ok=True)
            environment["AGENTROF_LIVE_TESTS"] = str(selector)
            declared = environment_identity(root, environment, contract)
            command = contract["live_diagnostic_command"].replace(operation_compile.LIVE_GROUP_PLACEHOLDER, group)
            completed, intact, difference, _dropped, _ran = private_checkout_run(
                root, selector.parent, current["product_commit"], contract.get("live_test_workdir", "."),
                command, environment)
            reported = live_case_report(root).get(group, {})
            intact = (intact and selector.read_text(encoding="utf-8") == data
                      and source_file_generation(selector) == generation
                      and reported == {target: "passed" for target in targets})
            output_name = "scratch/live-repair-" + uuid.uuid4().hex + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
            record["runs"][group] = {"exit_code": completed.returncode, "candidate_intact": intact,
                                     "command": command, "cases": reported, **declared,
                                     "output_file": output_name,
                                     "output_sha256": hashlib.sha256(completed.stdout).hexdigest()}
        if candidate(root, delivery_id, story, allow_evidence=True)["candidate_hash"] != current["candidate_hash"]:
            raise RuntimeError("repair candidate changed during the live run")
        record["completed_at"] = time.time()
        record["evidence_hash"] = digest(record)
        atomic_file.replace_text(live_repair_record_path(root), json.dumps(record, indent=2) + "\n")
        return {**record, "exit_code": 0 if all(run["exit_code"] == 0 and run["candidate_intact"]
                                               for run in record["runs"].values()) else 1}


def live_groups_selected(group: str) -> list[str]:
    """The live group names one run selects: a comma-separated list, as --group gives it."""
    return [name.strip() for name in group.split(",")]


def live_case_reuse(root: Path, delivery_id: str) -> bool:
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, LIVE_REUSE_SWITCH) == DELTA_SINCE_GREEN


def live_base_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / LIVE_BASE, file_only=True)


def read_live_base(root: Path) -> dict:
    """Each live group's last passing run in this Item worktree, kept across verification sessions."""
    try:
        value = json.loads(live_base_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    groups = value.get("groups") if isinstance(value, dict) and value.get("schema_version") == 1 else None
    return groups if isinstance(groups, dict) else {}


def record_live_base(root: Path, record: dict, product_commit: str, candidate_hash: str | None,
                     contract: dict) -> None:
    """Keep a passing intact live run as the base of every group it ran; a run that did not pass drops them.

    A group that failed after its last green run is never reused from that run.
    """
    groups = read_live_base(root)
    identity = record["identity"]
    passed = record["exit_code"] == 0 and record["candidate_intact"] is True
    cases = record.get("live_case_report", {})
    for name in live_groups_selected(identity["live_group"]):
        groups.pop(name, None)
        if passed:
            groups[name] = {"product_commit": product_commit, "candidate_hash": candidate_hash,
                            "map_hash": digest(operation_compile.live_impact_map(contract)),
                            **{key: identity[key] for key in ("live_group", "command", "workdir", *ENVIRONMENT_FIELDS)},
                            **{key: record[key] for key in ("evidence_hash", "exit_code", "candidate_intact",
                                                            "output_file", "output_sha256", "completed_at")},
                            "cases": cases.get(name, {})}
    atomic_file.replace_text(live_base_path(root), json.dumps({"schema_version": 1, "groups": groups},
                                                              indent=2, sort_keys=True) + "\n")


def pass_carry_over(root: Path, delivery_id: str) -> bool:
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, CARRY_OVER_SWITCH) == PER_ITEM


def carry_over_record_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / CARRY_OVER_RECORD, file_only=True)


def valid_carry_over_pass(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False
    if any(not isinstance(value.get(key), str) or not value[key].strip()
           for key in (*CARRY_OVER_OWNER, "run_id", "product_commit", "candidate_hash")):
        return False
    if not re.fullmatch(r"[0-9a-f]{40,64}", value["product_commit"]):
        return False
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["candidate_hash"]):
        return False
    for name in ("carried", "fresh"):
        if not isinstance(value.get(name), list):
            return False
        for case in value[name]:
            if (not isinstance(case, dict)
                    or any(not isinstance(case.get(key), str) or not case[key].strip()
                           for key in ("case_id", "outcome", "input_digest"))
                    or not isinstance(case.get("input_paths"), list)
                    or any(not isinstance(path, str) or not delivery._is_normalized_claim(path)
                           for path in case["input_paths"])
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", case["input_digest"])):
                return False
    identities = [case["case_id"] for name in ("carried", "fresh") for case in value[name]]
    return len(set(identities)) == len(identities)


def read_carry_over_record(root: Path, current: dict | None = None) -> dict | None:
    """The Item's carry-over record, kept across verification sessions, or None."""
    try:
        value = json.loads(carry_over_record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("passes"), list)
            or any(not isinstance(value.get(key), str) or not value[key].strip() for key in CARRY_OVER_OWNER)
            or not isinstance(value.get("candidate_hash"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["candidate_hash"])
            or any(not valid_carry_over_pass(entry)
                   or any(entry[key] != value[key] for key in CARRY_OVER_OWNER) for entry in value["passes"])
            or current is not None and (value["delivery_id"], value["item_id"]) != (current["delivery"], current["story"])):
        return None
    return value


def keep_carry_over_pass(root: Path, current: dict) -> dict | None:
    """Add the pass record this command wrote to the Item's record, whatever the pass's verdict.

    The pass record lists each case with its outcome, input paths and their
    digest at its product commit; the project's reader carries only a passed
    case. A record of another Item, or none, adds nothing.
    """
    try:
        entry = json.loads(raw_output_path(root, CARRY_OVER_PASS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(entry, dict)
            or entry.get("product_commit") != current["product_commit"]
            or entry.get("candidate_hash") is not None and entry.get("candidate_hash") != current["candidate_hash"]
            or (entry.get("delivery_id"), entry.get("item_id")) != (current["delivery"], current["story"])):
        return None
    entry["candidate_hash"] = current["candidate_hash"]
    if not valid_carry_over_pass(entry):
        return None
    record = read_carry_over_record(root, current)
    if record is None or any(record.get(key) != entry[key] for key in CARRY_OVER_OWNER):
        record = {"schema_version": 1, **{key: entry[key] for key in CARRY_OVER_OWNER}, "passes": []}
    record["candidate_hash"] = current["candidate_hash"]
    record["passes"] = [row for row in record["passes"] if row.get("run_id") != entry["run_id"]] + [entry]
    atomic_file.replace_text(carry_over_record_path(root), json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def commit_delta(root: Path, base: str, commit: str) -> tuple[bool, list[str] | None]:
    """Whether *base* is an ancestor of *commit*, and the paths that differ between them, or None when unreadable."""
    ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, commit],
                              capture_output=True).returncode == 0
    try:
        return ancestor, sorted(part for part in git(root, "diff", "--name-only", "--no-renames", "-z", base, commit)
                                .split("\0") if part)
    except RuntimeError:
        return ancestor, None


def live_base_problem(entry: object, group: str, contract: dict, *, ancestor: bool, changed: list[str] | None,
                      environment: dict | None = None) -> str | None:
    """Why *group* cannot take its cases from its base run *entry* on a candidate *changed* paths away, or None.

    The base passed intact, is as fresh as final evidence must be, ran the
    approved command for its groups under the approved impact map (and, with
    *environment*, in that declared environment) on an ancestor of the
    candidate, and no changed path matches a catch-all glob or a glob of the group.
    """
    if not isinstance(entry, dict) or any(key not in entry for key in LIVE_BASE_FIELDS):
        return f"live group {group} has no recorded passing base run"
    if entry["exit_code"] != 0 or entry["candidate_intact"] is not True:
        return f"the base run of live group {group} did not pass intact"
    if not fresh_record(entry):
        return f"the base run of live group {group} expired"
    impact = operation_compile.live_impact_map(contract)
    if entry["map_hash"] != digest(impact):
        return f"the approved {operation_compile.LIVE_GROUP_PATHS} changed since the base run of live group {group}"
    try:
        command = live_test_command(contract, entry["live_group"])
    except RuntimeError:
        command = None
    if (group not in live_groups_selected(str(entry["live_group"])) or entry["command"] != command
            or entry["workdir"] != contract.get("live_test_workdir", ".")):
        return f"the approved live command changed since the base run of live group {group}"
    if environment is not None and {key: entry[key] for key in ENVIRONMENT_FIELDS} != environment:
        return f"the base run of live group {group} ran in another declared environment"
    if not ancestor:
        return f"the base commit of live group {group} is no ancestor of the frozen candidate"
    if changed is None:
        return f"the paths changed since the base run of live group {group} cannot be read"
    if not impact.get(group):
        return f"live group {group} declares no {operation_compile.LIVE_GROUP_PATHS}, so no delta proves it unchanged"
    for path in changed:
        if any(fnmatch.fnmatchcase(path, glob) for glob in impact.get(operation_compile.LIVE_ALL_GROUPS, [])):
            return f"{path} changed since the base run of live group {group} and matches a catch-all live path"
        if any(fnmatch.fnmatchcase(path, glob) for glob in impact[group]):
            return f"{path} changed since the base run of live group {group} and matches one of its live paths"
    return None


def live_reuse_plan(root: Path, current: dict, contract: dict, environment: dict | None = None) -> dict:
    """Which declared live groups the delta since their base run selects, and which may reuse that run."""
    base, deltas, groups = read_live_base(root), {}, {}
    for group in contract.get("live_groups") or []:
        entry = base.get(group)
        commit = entry.get("product_commit") if isinstance(entry, dict) else None
        if isinstance(commit, str) and commit not in deltas:
            deltas[commit] = commit_delta(root, commit, current["product_commit"])
        ancestor, changed = deltas.get(commit, (False, None))
        problem = live_base_problem(entry, group, contract, ancestor=ancestor, changed=changed, environment=environment)
        groups[group] = ({"reusable": True, "base_commit": commit, "base_candidate_hash": entry["candidate_hash"],
                          "base_evidence_hash": entry["evidence_hash"], "changed_paths": changed}
                         if problem is None else {"reusable": False, "reason": problem,
                                                  **({"base_commit": commit} if commit else {})})
    commits = sorted({row["base_commit"] for row in groups.values() if row.get("base_commit")})
    return {"switch": f"{LIVE_REUSE_SWITCH} {DELTA_SINCE_GREEN}", "groups": groups,
            "selected_groups": [group for group, row in groups.items() if not row["reusable"]],
            "reusable_groups": [group for group, row in groups.items() if row["reusable"]],
            "base_commit": commits[0] if len(commits) == 1 else None,
            "rule": "Run the selected groups; a run records the reusable groups it does not run as reused_live_base"
                    " with their base runs' passed cases, and run --fresh reuses none."}


def live_base_run(root: Path, delivery_id: str, story: str, commit: str, group: str) -> dict:
    """Run the approved live command on an ancestor of the Item's candidate, such as a nightly build, as a base.

    It runs outside any reader, holds the environment like repair-live, and a
    passing intact run becomes the base of each group it ran, as a pass's own
    run would; one that does not pass drops their bases.
    """
    root = root.resolve()
    guard_write(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "live-base"), command_lock(root, PRE_HANDOFF_HOLDER, "live-base"):
        if not live_case_reuse(root, delivery_id):
            raise RuntimeError(f"live-base serves only process switch {LIVE_REUSE_SWITCH} {DELTA_SINCE_GREEN}")
        current = candidate(root, delivery_id, story, allow_evidence=True)
        commit = git(root, "rev-parse", "--verify", commit + "^{commit}")
        if not commit_delta(root, commit, current["product_commit"])[0]:
            raise RuntimeError("live-base records only an ancestor of the Item's candidate")
        contract = verification_contract(root)
        command = live_test_command(contract, group)
        group = live_record_group(contract, group)
        workdir = str(contract.get("live_test_workdir", "."))
        scratch = session_path(root).parent / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        environment = command_environment(root, current)
        declared = environment_identity(root, environment, contract)
        raw_output_path(root, "scratch/live-case-report.json").unlink(missing_ok=True)
        started = time.monotonic()
        completed, intact, difference, _dropped, _ran = private_checkout_run(
            root, safe_runtime_path(root, scratch), commit, workdir, command, environment)
        output_name = "scratch/live-base-" + uuid.uuid4().hex + ".log"
        atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
        record = {"identity": {"base_commit": commit, "kind": "live_test", "command": command, "workdir": workdir,
                               **declared, "execution_isolation": "private_clone_v1", "live_group": group},
                  "exit_code": completed.returncode, "candidate_intact": intact, "output_file": output_name,
                  "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                  "duration_seconds": time.monotonic() - started, "completed_at": time.time(),
                  "live_case_report": live_case_report(root)}
        if difference:
            record["checkout_difference"] = difference
        record["evidence_hash"] = digest(record)
        record_live_base(root, record, commit, None, contract)
        return record


def live_reuse_problem(root: Path, session: dict, raw: dict, contract: dict) -> str | None:
    """Why the base runs a live record reuses do not bind the frozen candidate any more, or None."""
    if not live_case_reuse(root, session["candidate"]["delivery"]):
        return f"reuses a live base run, which only process switch {LIVE_REUSE_SWITCH} {DELTA_SINCE_GREEN} allows"
    identity, reused = raw["identity"], raw.get("reused_live_base")
    ran = live_groups_selected(identity.get("live_group", ""))
    if (not isinstance(reused, dict) or not reused or not isinstance(identity["reused_live_base"], dict)
            or set(reused) != set(identity["reused_live_base"])
            or any(not isinstance(entry, dict) or identity["reused_live_base"][group] != entry.get("evidence_hash")
                   or group in ran or group not in (contract.get("live_groups") or [])
                   for group, entry in reused.items())
            or raw.get("evidence_hash") != digest({key: value for key, value in raw.items() if key != "evidence_hash"})):
        return "names reused live groups apart from their base runs"
    environment = {key: identity.get(key) for key in ENVIRONMENT_FIELDS}
    for group, entry in sorted(reused.items()):
        try:
            output = raw_output_path(root, str(entry.get("output_file")))
            intact = (output.is_file() and not output.is_symlink()
                      and hashlib.sha256(output.read_bytes()).hexdigest() == entry.get("output_sha256"))
        except (OSError, RuntimeError):
            intact = False
        if not intact:
            return f"reuses a base run of live group {group} whose output is missing or changed"
        commit = entry.get("product_commit")
        ancestor, changed = (commit_delta(root, commit, session["candidate"]["product_commit"])
                             if isinstance(commit, str) else (False, None))
        problem = live_base_problem(entry, group, contract, ancestor=ancestor, changed=changed, environment=environment)
        if problem:
            return "cannot reuse its base: " + problem
    return None


def qa_preflight(root: Path, delivery_id: str) -> bool:
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, "qa_preflight") == "lifecycle_first"


def rehearsal_status(root: Path, delivery_id: str, story: str) -> dict:
    """List untried approved targets without starting a verification session."""
    if not qa_preflight(root, delivery_id):
        return {"required": False, "steps": []}
    current = candidate(root, delivery_id, story, allow_evidence=True)
    text = git(root, "show", current["product_commit"] + ":workspace/docs/operation/verification-contract.md")
    contract, body = operation_compile.parse_text(text, Path("verification-contract.md"))
    if (not current["runtime_required"] and not contract.get("live_groups")
            and operation_compile.VERIFICATION_REHEARSALS not in backlog_compile.headings(body)):
        return {"required": False, "steps": []}
    rows, problems = operation_compile.verification_rehearsals(contract, body)
    problems.extend(operation_compile.live_test_problems(contract))
    if problems or not rows:
        raise RuntimeError("qa_preflight needs valid Verification Rehearsals: " + "; ".join(problems or ["no steps"]))
    if (contract.get("status") != "approved" or not contract.get("approved_at_utc")
            or contract.get("source_hash") != operation_compile.receipt_hash(contract, body)):
        raise RuntimeError("rehearsals and owner waivers require the current approved Verification Contract")
    if current["runtime_required"] and not any(row["phase"] == "lifecycle" for row in rows):
        raise RuntimeError("runtime rehearsal plan must include its lifecycle checks")
    path = raw_output_path(root, "scratch/rehearsals.json")
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.stat().st_size <= 2 * 1024 * 1024 else {}
    except (FileNotFoundError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    environment = command_environment(root, current)
    environment["AGENTROF_LIVE_TESTS"] = str(raw_output_path(root, "scratch/rehearsal-selection.json"))
    declared = environment_identity(root, environment, contract)
    steps = []
    for row in rows:
        command = contract["live_diagnostic_command"].replace(operation_compile.LIVE_GROUP_PLACEHOLDER, row["group"])
        identity = {"candidate_hash": current["candidate_hash"], "step": row, "command": command,
                    "workdir": contract.get("live_test_workdir", "."), **declared}
        entry = {**row, "identity": identity, "state": "pending"}
        if row["waiver"] != "-":
            entry.update(state="waived", approval_hash=contract["source_hash"])
        else:
            record = saved.get(row["step"], {})
            try:
                output = raw_output_path(root, record["output_file"])
                passed = (record["identity"] == identity and record["exit_code"] == 0
                          and record["candidate_intact"] is True and fresh_record(record)
                          and record["cases"] == {row["target"]: "passed"}
                          and record["evidence_hash"] == digest({k: v for k, v in record.items() if k != "evidence_hash"})
                          and hashlib.sha256(output.read_bytes()).hexdigest() == record["output_sha256"])
            except (KeyError, TypeError, OSError, ValueError):
                passed = False
            if passed:
                entry.update(state="passed", evidence_hash=record["evidence_hash"])
        steps.append(entry)
    return {"required": True, "candidate_hash": current["candidate_hash"], "steps": steps}


def require_rehearsals(root: Path, delivery_id: str, story: str) -> dict | None:
    status = rehearsal_status(root, delivery_id, story)
    if not status["required"]:
        return None
    pending = [row["step"] for row in status["steps"] if row["state"] == "pending"]
    if pending:
        raise RuntimeError("untried verification steps need rehearse or an approved owner waiver: " + ", ".join(pending))
    return status


def rehearse(root: Path, delivery_id: str, story: str) -> dict:
    """Run only unproven short targets through the approved live diagnostic command."""
    root = root.resolve()
    guard_write(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "rehearse"), command_lock(root, PRE_HANDOFF_HOLDER, "rehearse"):
        current = candidate(root, delivery_id, story, allow_evidence=True)
        require_repair_review(root, read_session(root, required=False), current)
        status = rehearsal_status(root, delivery_id, story)
        if not status["required"]:
            raise RuntimeError("rehearse requires qa_preflight lifecycle_first")
        saved_path = raw_output_path(root, "scratch/rehearsals.json")
        try:
            saved = json.loads(saved_path.read_text(encoding="utf-8")) if saved_path.stat().st_size <= 2 * 1024 * 1024 else {}
        except (FileNotFoundError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        timing = timing_values(root, delivery_id)
        overruns: list[dict] = []

        def timed(value: dict) -> dict:
            return {**value, "step_overruns": overruns} if overruns else value

        for row in status["steps"]:
            if row["state"] != "pending":
                continue
            selection = raw_output_path(root, "scratch/rehearsal-selection.json")
            data = json.dumps({"candidate_hash": current["candidate_hash"], "group": row["group"],
                               "selected_test_ids": [row["target"]]}, sort_keys=True) + "\n"
            atomic_file.replace_text(selection, data)
            generation = source_file_generation(selection)
            raw_output_path(root, "scratch/live-case-report.json").unlink(missing_ok=True)
            environment = command_environment(root, current)
            environment["AGENTROF_LIVE_TESTS"] = str(selection)
            identity = row["identity"]
            timer = timing_start(root, timing, delivery_id, "qa_lifecycle_gate", row["step"])
            try:
                result, intact, _difference, _dropped, _ran = private_checkout_run(
                    root, selection.parent, current["product_commit"], identity["workdir"], identity["command"], environment)
            finally:
                late = timing_end(timer)
                if late is not None:
                    overruns.append(late)
            cases = live_case_report(root).get(row["group"], {})
            intact = (intact and selection.read_text(encoding="utf-8") == data
                      and source_file_generation(selection) == generation)
            output_name = "scratch/rehearsal-" + uuid.uuid4().hex + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), result.stdout)
            record = {"identity": identity, "exit_code": result.returncode, "candidate_intact": intact,
                      "cases": cases, "completed_at": time.time(), "output_file": output_name,
                      "output_sha256": hashlib.sha256(result.stdout).hexdigest()}
            record["evidence_hash"] = digest(record)
            saved[row["step"]] = record
            atomic_file.replace_text(saved_path, json.dumps(saved, indent=2) + "\n")
            if result.returncode != 0 or not intact or cases != {row["target"]: "passed"}:
                return timed({**rehearsal_status(root, delivery_id, story), "exit_code": 1,
                              "failed_step": row["step"]})
        if candidate(root, delivery_id, story, allow_evidence=True)["candidate_hash"] != current["candidate_hash"]:
            raise RuntimeError("rehearsal candidate changed while its targets ran")
        final = rehearsal_status(root, delivery_id, story)
        return timed({**final, "exit_code": int(any(row["state"] == "pending" for row in final["steps"]))})


def live_test_command(contract: dict, group: str) -> str:
    """The approved live test command for the declared groups *group* names, comma-separated.

    One group substitutes its name for {group}. Several groups run as one live
    run only when the contract declares live_group_run combined; {group} then
    receives their names comma-separated, once, in the contract's order.
    """
    if "live_test_command" not in contract or operation_compile.live_test_problems(contract):
        raise RuntimeError("the Verification Contract declares no valid live_test_command, live_test_workdir"
                           " and live_groups")
    selected = live_groups_selected(group)
    for name in selected:
        if name not in contract["live_groups"]:
            raise RuntimeError(f"live group {name!r} is not declared in live_groups: {', '.join(contract['live_groups'])}")
    if not selected or len(selected) != len(set(selected)):
        raise RuntimeError("run --kind live_test --group names each declared group once")
    if len(selected) > 1 and contract.get(operation_compile.LIVE_GROUP_RUN) != "combined":
        raise RuntimeError(f"one live run of several groups needs {operation_compile.LIVE_GROUP_RUN}: combined in the"
                           " Verification Contract; run one group per call")
    names = ",".join(name for name in contract["live_groups"] if name in selected)
    return contract["live_test_command"].replace(operation_compile.LIVE_GROUP_PLACEHOLDER, names)


def live_record_group(contract: dict, group: str) -> str:
    """The canonical group key of a live run: its selected names in the contract's order."""
    selected = set(live_groups_selected(group))
    return ",".join(name for name in contract.get("live_groups") or [] if name in selected) or group


def _run_check(root: Path, kind: str, *, fresh: bool = False, selection_file: Path | None = None,
               spot_file: Path | None = None, group: str | None = None, resume: bool = False) -> dict:
    """Run an approved command verbatim with a file-based mutation scope binding."""
    root = root.resolve()
    if kind not in RUN_KINDS:
        raise RuntimeError("unsupported verification command kind")
    if (kind == "live_test") != (group is not None):
        raise RuntimeError("run --kind live_test requires --group; other kinds take no --group")
    if resume and (kind != "live_test" or fresh):
        raise RuntimeError("resume is only for a live preflight attempt on the unchanged candidate")
    # Each live group keeps its own evidence beside the final commands' records.
    record_key = kind if group is None else f"{kind}:{group}"
    if (kind == "diagnostic_test") != (selection_file is not None):
        raise RuntimeError("diagnostic_test requires --selection-file; final commands do not accept a focused selection")
    if spot_file is not None and kind != "test":
        raise RuntimeError("only run --kind test takes --spot-run-file")
    if spot_file is not None and fresh:
        raise RuntimeError("run --fresh reuses nothing, so it takes no --spot-run-file")
    if selection_file is not None:
        selection_file = Path(selection_file)
        if not selection_file.is_absolute():
            selection_file = root / selection_file
    with locked(root):
        session = read_session(root)
        current = require_current(root, session, allow_evidence=True)
        require_owner_step_progress(root, session)
        if session["workers"]["qa_engineer"]["state"] != "running":
            raise RuntimeError("verification commands require the active QA reader")
        spot = None
        own_value = own_target_reuse(root, current["delivery"]) if kind == "test" else None
        if spot_file is not None:
            if own_value != SPOT_RUN:
                raise RuntimeError(f"run --spot-run-file serves only process switch {OWN_TARGET_SWITCH} {SPOT_RUN};"
                                   f" {current['delivery']} runs it at {own_value}")
            spot = spot_run_selection(root, Path(spot_file), current)
        elif own_value == ACCEPT_RECORD and not fresh:
            # No own target is kept for the command: the accepted run supplies them all.
            spot = []
        contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
        command = live_test_command(contract, group) if group is not None else contract.get(kind + "_command")
        if group is not None:
            group = live_record_group(contract, group)
            record_key = f"{kind}:{group}"
            require_lifecycle_ready(root, session)
        prior = session["raw_evidence"].get(record_key) if resume else None
        if resume:
            if (not resumable_live_run(prior, current) or "live_resume_command" not in contract
                    or not checkpoint_evidence_current(root, prior["live_checkpoint"])):
                raise RuntimeError("live resume requires one fresh preflight-only failed measurement and an approved resume command")
            command = contract["live_resume_command"].replace(operation_compile.LIVE_GROUP_PLACEHOLDER, group)
        if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
            raise RuntimeError("approved verification command is missing or contains unresolved parameters")
        partitions = (partition_declaration(root, current["delivery"], contract, contract_body)
                      if kind == "test" else None)
        if partitions is not None:
            command = partitions["command"]
        workdir = str(contract.get(kind + "_workdir", "."))
        directory = (root / workdir).resolve()
        if directory != root and root not in directory.parents:
            raise RuntimeError("verification command workdir must remain inside the Item worktree")
        environment = command_environment(root, current, diagnostic=kind == "diagnostic_test")
        inherited = session.get("unresolved_findings", [])
        selection = (diagnostic_selection(root, selection_file, current, inherited)
                     if selection_file is not None else None)
        selection_bytes = None
        if selection is not None:
            input_generation = source_file_generation(selection_file)
            selection_bytes = (json.dumps(selection, indent=2, sort_keys=True) + "\n").encode("utf-8")
            selector = safe_runtime_path(root, Path(environment["AGENTROF_DIAGNOSTIC_TESTS"]), file_only=True)
            atomic_file.replace_bytes(selector, selection_bytes)
            selector_generation = source_file_generation(selector)
        scope = {"candidate_hash": current["candidate_hash"], "scope": "whole_changed_files", "files": current["mutation_files"]}
        atomic_file.replace_text(Path(environment["AGENTROF_MUTATION_FILES"]), json.dumps(scope, indent=2) + "\n")
        scratch = safe_runtime_path(root, Path(environment["AGENTROF_VERIFICATION_SCRATCH"]))
        scratch.mkdir(exist_ok=True)
        declared = environment_identity(root, environment, contract)
        resume_input = None
        if resume:
            if any(prior["identity"].get(key) != declared[key] for key in ENVIRONMENT_FIELDS):
                raise RuntimeError("live resume requires the original command environment")
            resume_path = raw_output_path(root, "scratch/live-resume.json")
            resume_input = {"candidate_hash": current["candidate_hash"], "group": group,
                            "prior_evidence_hash": prior["evidence_hash"], **prior["live_checkpoint"]}
            atomic_file.replace_text(resume_path, json.dumps(resume_input, sort_keys=True) + "\n")
            resume_generation = source_file_generation(resume_path)
            environment["AGENTROF_LIVE_RESUME"] = str(resume_path)
        checkpoint_path = raw_output_path(root, "scratch/live-checkpoint.json") if group is not None else None
        if checkpoint_path is not None and checkpoint_path.exists():
            checkpoint_path.unlink()
        if kind == "mutation" and not current["mutation_files"]:
            return record_empty_mutation(root, session, current, command, workdir, declared)
        live_reuse = live_reuse_on = None
        if group is not None and live_case_reuse(root, current["delivery"]):
            live_reuse_on = True
            plan = live_reuse_plan(root, current, contract, declared)
            ran = live_groups_selected(group)
            base = read_live_base(root)
            # run --fresh and a resumed attempt reuse nothing.
            live_reuse = ({name: base[name] for name in plan["reusable_groups"] if name not in ran}
                          if not fresh and not resume else {})
            base_file = raw_output_path(root, "scratch/live-base-selection.json")
            atomic_file.replace_text(base_file, json.dumps(
                {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                 "base_commits": {name: row["base_commit"] for name, row in plan["groups"].items()
                                  if row.get("base_commit")},
                 "selected_groups": plan["selected_groups"], "reused_groups": sorted(live_reuse)},
                indent=2, sort_keys=True) + "\n")
            environment["AGENTROF_LIVE_BASE"] = str(base_file)
            if plan["base_commit"]:
                environment["AGENTROF_LIVE_BASE_COMMIT"] = plan["base_commit"]
        carry_over = kind in {"test", "live_test"} and pass_carry_over(root, current["delivery"])
        if carry_over:
            kept = read_carry_over_record(root, current)
            if kept is not None:
                # The command copies the candidate it runs on from the record into its pass record.
                kept["candidate_hash"] = current["candidate_hash"]
                atomic_file.replace_text(carry_over_record_path(root), json.dumps(kept, indent=2, sort_keys=True) + "\n")
            else:
                carry_over_record_path(root).unlink(missing_ok=True)
            environment["AGENTROF_CARRY_OVER_RECORD"] = str(carry_over_record_path(root))
        reuse, refusal = (pre_handoff_reuse(root, session, current, declared, fresh=fresh, spot=spot)
                          if kind == "test" else (None, None))
        own_note = None
        # Without a spot-run file the run says so even when it reuses nothing else; with one that
        # reuses nothing, its pre_handoff_reuse refusal says why.
        if (kind == "test" and (reuse is None and spot is None or reuse is not None and "own_targets" not in reuse)
                and own_value == SPOT_RUN):
            own_note = ("no --spot-run-file names the own targets QA runs itself, so the run reuses none of the"
                        " Item's own targets" if spot is None else
                        "the spot-run targets, with every own target that is, prefixes or lies under one of them,"
                        " cover every own target, so the run reuses none of them")
        elif kind == "test" and own_value == ACCEPT_RECORD and (reuse is None or "own_targets" not in reuse):
            own_note = ("no accepted pre-handoff run binds the frozen candidate for the Item's own targets, so the"
                        " approved test command runs every one of them itself")
        identity = {"candidate_hash": current["candidate_hash"], "kind": kind, "command": command,
                    "workdir": workdir, **declared, "execution_isolation": "private_clone_v1"}
        if selection is not None:
            identity["diagnostic_selection_hash"] = digest(selection)
        if group is not None:
            identity["live_group"] = group
        if resume_input is not None:
            identity["live_resume_hash"] = digest(resume_input)
        if reuse is not None:
            identity["reused_pre_handoff"] = reuse
        if carry_over and kept is not None:
            identity["carry_over_record"] = digest(kept)
        if live_reuse:
            identity["reused_live_base"] = {name: entry["evidence_hash"] for name, entry in sorted(live_reuse.items())}
        groups = (group_report_declaration(root, current["delivery"], contract)
                  if kind in {"test", "diagnostic_test"} else None)
        if groups is not None:
            identity["test_group_report"] = groups
        if partitions is not None:
            identity["test_partitions"] = {key: partitions[key] for key in
                                           ("partitions", "test_engines", "shared_profiles")}
        key = digest(identity)
        old = session["raw_evidence"].get(record_key)
        if (not fresh and old and old.get("identity") == identity and old.get("exit_code") == 0 and old.get("candidate_intact") is True
                and fresh_record(old)):
            output = raw_output_path(root, old["output_file"])
            if output.is_file() and not output.is_symlink() and hashlib.sha256(output.read_bytes()).hexdigest() == old["output_sha256"]:
                session["metrics"]["command_cache_hits"] += 1
                write_session(root, session)
                return {**old, "reused": True, **({"own_target_reuse": own_note} if own_note else {})}
        if reuse is not None:
            reused = safe_runtime_path(root, session_path(root).parent / REUSED_TESTS, file_only=True)
            reused_bytes = (json.dumps(
                {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                 "pre_handoff_evidence_hash": reuse["evidence_hash"], "reused_test_ids": reuse["test_ids"]},
                indent=2, sort_keys=True) + "\n").encode("utf-8")
            atomic_file.replace_bytes(reused, reused_bytes)
            reused_generation = source_file_generation(reused)
            environment["AGENTROF_REUSED_TESTS"] = str(reused)
        if group is not None:
            raw_output_path(root, "scratch/live-case-report.json").unlink(missing_ok=True)
        if carry_over:
            raw_output_path(root, CARRY_OVER_PASS).unlink(missing_ok=True)
        session["raw_evidence"].pop(record_key, None)
        write_session(root, session)
        session_id = session["session_id"]
        if groups is not None and partitions is None:
            clear_group_report(scratch, groups)
    started = time.monotonic()
    partition_records = None
    step_overrun = None
    accepted_full = (own_value == ACCEPT_RECORD and reuse is not None and not fresh
                     and session["pre_handoff"].get("kind") == "test"
                     and session["pre_handoff"].get("command") == command
                     and session["pre_handoff"].get("workdir") == workdir
                     # A group-reported run takes its group report from the accepted run, which must show
                     # every group the contract declares now passed.
                     and (groups is None or group_report_problem(
                         {"identity": {"test_group_report": session["pre_handoff"].get("test_group_report")},
                          "test_groups": session["pre_handoff"].get("test_groups")}, groups) is None)
                     and partitions is None)
    if accepted_full:
        completed = subprocess.CompletedProcess(command, 0,
            ("Accepted full-suite pre-handoff evidence " + reuse["evidence_hash"] + "\n"
             + "".join("REUSED " + target + "\n" for target in reuse["test_ids"])).encode("utf-8"))
        intact, difference = True, ""
        group_report = (None if groups is None else
                        {"test_groups": session["pre_handoff"]["test_groups"], "missing_test_groups": []})
    elif partitions is not None:
        partition_records = run_partitions(root, scratch, current, workdir, partitions, environment,
                                           reuse["test_ids"] if reuse is not None else [], groups)
        intact, difference = all(record["candidate_intact"] is True for record in partition_records), ""
        completed = subprocess.CompletedProcess(command, 0 if all(record["passed"] for record in partition_records)
                                                else 1, b"".join(
            f"== partition {record['partition']} on engine {record['engine']}: exit {record['exit_code']}"
            f" ==\n".encode("utf-8") + record["output"] for record in partition_records))
        group_report = None if groups is None else {
            "test_groups": {group: entry for record in partition_records
                            for group, entry in record.get("test_groups", {}).items()},
            "missing_test_groups": sorted(group for record in partition_records
                                          for group in record.get("missing_test_groups", []))}
    else:
        timer = (timing_start(root, timing_values(root, current["delivery"]), current["delivery"], "live_run",
                              group, live_progress_path(root, record_key)) if group is not None else None)
        try:
            with contextlib.ExitStack() as progress:
                live_output = (progress.enter_context(live_progress(root, session_id, current, record_key, group))
                               if group is not None else None)
                completed, intact, difference, _dropped, _ran = private_checkout_run(
                    root, scratch, current["product_commit"], workdir, command, environment,
                    **({"live_output": live_output} if live_output is not None else {}))
        finally:
            step_overrun = timing_end(timer)
        group_report = read_group_report(scratch, groups) if groups is not None else None
    if group_report is not None and group_report["missing_test_groups"]:
        intact = False
    selection_intact = None
    if selection is not None:
        try:
            selector = safe_runtime_path(root, Path(environment["AGENTROF_DIAGNOSTIC_TESTS"]), file_only=True)
            selection_intact = (diagnostic_selection(root, selection_file, current, inherited) == selection
                                and selector.read_bytes() == selection_bytes
                                and source_file_generation(selection_file) == input_generation
                                and source_file_generation(selector) == selector_generation)
        except (RuntimeError, ValueError, OSError):
            selection_intact = False
    elif reuse is not None:
        # A command that changed the reuse list it received skipped suites the reused run never covered.
        try:
            selection_intact = (safe_runtime_path(root, reused, file_only=True).read_bytes() == reused_bytes
                                and source_file_generation(reused) == reused_generation)
        except (RuntimeError, ValueError, OSError):
            selection_intact = False
    if resume_input is not None:
        try:
            selection_intact = (json.loads(resume_path.read_text(encoding="utf-8")) == resume_input
                                and source_file_generation(resume_path) == resume_generation
                                and checkpoint_evidence_current(root, resume_input))
        except (OSError, ValueError):
            selection_intact = False
    if selection_intact is not None:
        intact = intact and selection_intact
    interruptions = live_interruptions(root, record_key, completed.returncode) if group is not None else {}
    checkpoint = (live_checkpoint(root)
                  if group is not None and (completed.returncode == 2 or interruptions.get("interrupted_by"))
                  else None)
    with locked(root):
        session = read_session(root)
        if session["session_id"] != session_id:
            raise RuntimeError("verification session changed while the command ran")
        require_current(root, session, allow_evidence=True)
        output_prefix = f"scratch/{record_key.replace(':', '-')}-{key.removeprefix('sha256:')}-{uuid.uuid4().hex}"
        output_name = output_prefix + ".log"
        output = raw_output_path(root, output_name)
        atomic_file.replace_bytes(output, completed.stdout)
        record = {"identity": identity, "exit_code": completed.returncode, "candidate_intact": intact,
                  "output_file": output_name, "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                  "duration_seconds": time.monotonic() - started, "completed_at": time.time()}
        if group is not None:
            record["live_case_report"] = live_case_report(root)
        if live_reuse:
            record["reused_live_base"] = live_reuse
        if checkpoint is not None:
            record["live_checkpoint"] = checkpoint
        record.update(interruptions)
        if getattr(completed, "host_awake", None):
            record["host_awake"] = completed.host_awake
        if resume:
            record["resume_count"] = 1
            record["prior_attempt"] = prior
            record["resume_of"] = prior["evidence_hash"]
        if accepted_full:
            record["execution_mode"] = "accepted_pre_handoff"
        if difference:
            record["checkout_difference"] = difference
        if selection is not None:
            record["diagnostic_selection"] = selection
            if "evidence_scope" in selection:
                record["evidence_scope"] = selection["evidence_scope"]
        if selection_intact is not None:
            record["selection_intact"] = selection_intact
        if group_report is not None:
            record.update(group_report)
        if partition_records is not None:
            record["partitions"] = []
            for index, entry in enumerate(partition_records):
                name = f"{output_prefix}-partition-{index}.log"
                atomic_file.replace_bytes(raw_output_path(root, name), entry["output"])
                record["partitions"].append({**{field: value for field, value in entry.items()
                                                if field not in {"output", "test_groups", "missing_test_groups"}},
                                             "output_file": name,
                                             "output_sha256": hashlib.sha256(entry["output"]).hexdigest()})
        record["evidence_hash"] = digest(record)
        session["raw_evidence"][record_key] = record
        if live_reuse_on:
            record_live_base(root, record, current["product_commit"], current["candidate_hash"], contract)
        if carry_over and intact is True:
            keep_carry_over_pass(root, current)
        if record.get("diagnostic_selection", {}).get("finding_controls"):
            session["finding_control_bindings"] = finding_control_history(session)
        session["metrics"]["command_seconds"] += record["duration_seconds"]
        write_session(root, session)
        return {**record, "reused": False, **({"pre_handoff_reuse": refusal} if refusal else {}),
                **({"own_target_reuse": own_note} if own_note else {}),
                **({"step_overrun": step_overrun} if step_overrun else {})}


def record_empty_mutation(root: Path, session: dict, current: dict, command: str, workdir: str,
                          declared: dict) -> dict:
    """Record a mutation run whose scope holds no file as passed without launching the command.

    With no compiler-selected file there is no mutant to generate, so the
    record says so, binds the approved command and environment as a run would,
    and takes no time.
    """
    identity = {"candidate_hash": current["candidate_hash"], "kind": "mutation", "command": command,
                "workdir": workdir, **declared, "execution_isolation": "private_clone_v1"}
    output = (EMPTY_MUTATION_SCOPE + "\n").encode("utf-8")
    output_name = f"scratch/mutation-{digest(identity).removeprefix('sha256:')}-{uuid.uuid4().hex}.log"
    atomic_file.replace_bytes(raw_output_path(root, output_name), output)
    record = {"identity": identity, "exit_code": 0, "candidate_intact": True, "not_run": EMPTY_MUTATION_SCOPE,
              "output_file": output_name, "output_sha256": hashlib.sha256(output).hexdigest(),
              "duration_seconds": 0, "completed_at": time.time()}
    record["evidence_hash"] = digest(record)
    session["raw_evidence"]["mutation"] = record
    write_session(root, session)
    return {**record, "reused": False}


def own_target_reuse(root: Path, delivery_id: str) -> str:
    """The own_target_reuse value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, OWN_TARGET_SWITCH)


def group_report_declaration(root: Path, delivery_id: str, contract: dict) -> dict | None:
    """The test groups and the group report path a test run checks, or None.

    Only a Verification Contract that declares test_groups and
    test_group_report under a Delivery that runs test_group_report at
    refuse_missing_groups yields them; a contract without the fields never
    reads the Process Policy, so every run without them is as released.
    """
    if "test_groups" not in contract and "test_group_report" not in contract:
        return None
    if delivery.delivery_switch_value(delivery.docs_root(root), delivery_id,
                                      GROUP_REPORT_SWITCH) != REFUSE_MISSING_GROUPS:
        return None
    problems = operation_compile.test_group_problems(contract)
    if problems:
        raise RuntimeError("; ".join(problems))
    return {"test_groups": list(contract["test_groups"]), "test_group_report": contract["test_group_report"]}


def group_report_path(scratch: Path, declaration: dict) -> Path:
    path = scratch / declaration["test_group_report"]
    if scratch.resolve() not in path.resolve().parents:
        raise RuntimeError("test_group_report escapes the verification scratch")
    return path


def clear_group_report(scratch: Path, declaration: dict) -> None:
    """Remove an earlier run's group report, so only the command about to run can write one."""
    path = group_report_path(scratch, declaration)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise RuntimeError(f"test_group_report {declaration['test_group_report']} is no regular file")


def read_group_report(scratch: Path, declaration: dict) -> dict:
    """Each declared group's status and case counts, as the command's group report names them.

    A group the report lacks, or names without a declared status and
    non-negative case counts, is missing; so is every group when the report is
    absent or no report of the declared shape.
    """
    path = group_report_path(scratch, declaration)
    groups, problem = {}, None
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("the command wrote no group report")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"schema_version", "groups"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["groups"], dict)):
            raise ValueError("the group report holds no schema_version 1 and groups")
        groups = value["groups"]
    except (OSError, ValueError) as exc:
        problem = str(exc)
    statuses = {}
    for group in declaration["test_groups"]:
        entry = groups.get(group)
        if (isinstance(entry, dict) and set(entry) == {"status", *GROUP_COUNTS}
                and entry["status"] in GROUP_STATUSES
                and all(type(entry[count]) is int and entry[count] >= 0 for count in GROUP_COUNTS)):
            statuses[group] = {key: entry[key] for key in ("status", *GROUP_COUNTS)}
        else:
            statuses[group] = {"status": MISSING_GROUP}
    report = {"test_groups": statuses,
              "missing_test_groups": [group for group, entry in statuses.items() if entry["status"] == MISSING_GROUP]}
    if problem:
        report["test_group_report_problem"] = problem
    return report


def group_report_problem(raw: dict, declaration: dict) -> str | None:
    """Why a final test run's recorded group report does not show every declared group passed, or None."""
    if raw.get("identity", {}).get("test_group_report") != declaration:
        return "does not check the test groups and group report the approved Verification Contract declares"
    recorded = raw.get("test_groups")
    if not isinstance(recorded, dict) or set(recorded) != set(declaration["test_groups"]):
        return "does not record every declared test group"
    unpassed = {group: entry.get("status") if isinstance(entry, dict) else None
                for group, entry in sorted(recorded.items())
                if not isinstance(entry, dict) or entry.get("status") != "passed"}
    if unpassed:
        return "records test groups that did not pass: " + ", ".join(
            f"{group} {status}" for group, status in unpassed.items())
    return None


def partition_declaration(root: Path, delivery_id: str, contract: dict, body: str) -> dict | None:
    """The partition plan, command and engines QA's final test run runs under, or None.

    Only a Verification Contract that declares test_partition_command under a
    Delivery that runs test_engines at partitioned yields them; a contract
    without it never reads the Process Policy. The plan must place every
    declared group in exactly one partition, and the Environment Contract must
    provision every engine.
    """
    if "test_partition_command" not in contract:
        return None
    docs = delivery.docs_root(root)
    if delivery.delivery_switch_value(docs, delivery_id, ENGINE_SWITCH) != PARTITIONED:
        return None
    plan, problems = operation_compile.test_partition_plan(contract, body)
    if problems or plan is None:
        raise RuntimeError("; ".join(problems) or "the Verification Contract declares no test partition plan")
    path = docs / "operation/environment-contract.md"
    provisioned = delivery.split_note(path)[0].get("test_engines") if path.is_file() else None
    missing = [engine for engine in contract["test_engines"]
               if not isinstance(provisioned, list) or engine not in provisioned]
    if missing:
        raise RuntimeError("the Environment Contract provisions no test engine " + ", ".join(missing))
    command = contract["test_partition_command"]
    if "{{" in command or "}}" in command:
        raise RuntimeError("approved test partition command contains unresolved parameters")
    return {"command": command, "partitions": plan, "test_engines": list(contract["test_engines"]),
            "shared_profiles": sorted(contract.get("shared_profiles", []))}


def partition_durations_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / PARTITION_DURATIONS, file_only=True)


def partition_durations(root: Path) -> dict:
    try:
        value = json.loads(partition_durations_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: seconds for name, seconds in value.items()
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)} if isinstance(value, dict) else {}


def partition_order(root: Path, plan: list[dict]) -> list[dict]:
    """The partitions longest first by their last recorded duration; one never recorded comes first."""
    durations = partition_durations(root)
    return sorted(plan, key=lambda entry: -durations.get(entry["partition"], float("inf")))


def run_partitions(root: Path, scratch: Path, current: dict, workdir: str, partitions: dict, environment: dict,
                   reused: list[str], groups: dict | None) -> list[dict]:
    """Run every partition in its own private clone, in parallel over the declared test engines.

    A partition of an exclusive profile takes an engine alone; partitions of a
    shared profile may share one with each other. Never more partitions run at
    once than there are engines. A partition that fails, cannot start or
    misses a group never stops another. Each record names its partition,
    engine, exit code, intactness, duration and output, in plan order.
    """
    import threading

    engines, shared = partitions["test_engines"], set(partitions["shared_profiles"])
    load = {engine: {"exclusive": False, "count": 0} for engine in engines}
    pending, results, condition = partition_order(root, partitions["partitions"]), {}, threading.Condition()
    directory = safe_runtime_path(root, session_path(root).parent / "partitions")
    directory.mkdir(exist_ok=True)

    def engine_for(entry: dict) -> str | None:
        if sum(state["count"] for state in load.values()) >= len(engines):
            return None
        if entry["profile"] in shared:
            free = [engine for engine in engines if not load[engine]["exclusive"]]
            return min(free, key=lambda engine: load[engine]["count"]) if free else None
        return next((engine for engine in engines if load[engine]["count"] == 0), None)

    def run(entry: dict, engine: str, index: int) -> None:
        record = {"partition": entry["partition"], "engine": engine, "groups": entry["groups"],
                  "profile": entry["profile"]}
        started = time.monotonic()
        try:
            own = safe_runtime_path(root, scratch / "partitions" / entry["partition"])
            own.mkdir(parents=True, exist_ok=True)
            selection = safe_runtime_path(root, directory / f"{index}.json", file_only=True)
            data = (json.dumps({"schema_version": 1, "candidate_hash": current["candidate_hash"],
                                "partition": entry["partition"], "groups": entry["groups"], "engine": engine,
                                "reused_test_ids": reused}, indent=2, sort_keys=True) + "\n").encode("utf-8")
            atomic_file.replace_bytes(selection, data)
            generation = source_file_generation(selection)
            own_groups = (None if groups is None else
                          {**groups, "test_groups": [group for group in groups["test_groups"]
                                                     if group in entry["groups"]]})
            if own_groups is not None:
                clear_group_report(own, own_groups)
            completed, intact, difference, _dropped, _ran = private_checkout_run(
                root, own, current["product_commit"], workdir, partitions["command"],
                {**environment, "AGENTROF_TEST_PARTITION": str(selection), "AGENTROF_TEST_ENGINE": engine,
                 "AGENTROF_VERIFICATION_SCRATCH": str(own)})
            try:
                intact = intact and selection.read_bytes() == data and source_file_generation(selection) == generation
            except OSError:
                intact = False
            record.update(exit_code=completed.returncode, output=completed.stdout)
            if difference:
                record["checkout_difference"] = difference
            if own_groups is not None:
                report = read_group_report(own, own_groups)
                record.update(report)
                intact = intact and not report["missing_test_groups"]
            record["candidate_intact"] = intact
        except Exception as exc:  # noqa: BLE001 - any failure is this partition's result, never another's
            record.update(exit_code=None, candidate_intact=False, output=f"{exc}\n".encode("utf-8"),
                          error=f"the partition did not start or finish: {exc}")
        record["duration_seconds"] = time.monotonic() - started
        record["passed"] = (record["exit_code"] == 0 and record["candidate_intact"] is True
                            and all(group.get("status") == "passed"
                                    for group in record.get("test_groups", {}).values()))
        with condition:
            results[entry["partition"]] = record
            load[engine]["count"] -= 1
            load[engine]["exclusive"] = False
            condition.notify_all()

    threads = []
    with condition:
        while pending:
            chosen = next(((entry, engine) for entry in pending
                           for engine in [engine_for(entry)] if engine is not None), None)
            if chosen is None:
                condition.wait()
                continue
            entry, engine = chosen
            pending.remove(entry)
            load[engine]["count"] += 1
            load[engine]["exclusive"] = entry["profile"] not in shared
            thread = threading.Thread(target=run, args=(entry, engine, partitions["partitions"].index(entry)),
                                      daemon=True)
            threads.append(thread)
            thread.start()
    for thread in threads:
        thread.join()
    durations = partition_durations(root)
    durations.update({partition: record["duration_seconds"] for partition, record in results.items()})
    atomic_file.replace_text(partition_durations_path(root), json.dumps(durations, indent=2, sort_keys=True) + "\n")
    return [results[entry["partition"]] for entry in partitions["partitions"]]


def partition_problem(root: Path, raw: dict, partitions: dict) -> str | None:
    """Why a final test run's record is no complete, passing run of the declared partition plan, or None."""
    if raw.get("identity", {}).get("test_partitions") != {key: partitions[key] for key in (
            "partitions", "test_engines", "shared_profiles")}:
        return "does not run the test partition plan the approved Verification Contract declares"
    records = raw.get("partitions")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        return "records no partition results"
    names = [record.get("partition") for record in records]
    twice = sorted({str(name) for name in names if names.count(name) > 1})
    if twice:
        return "holds partition " + ", ".join(twice) + " more than once"
    declared = [entry["partition"] for entry in partitions["partitions"]]
    missing = [name for name in declared if name not in names]
    if missing:
        return "lacks partition " + ", ".join(missing)
    stray = sorted(str(name) for name in names if name not in declared)
    if stray:
        return "holds partition " + ", ".join(stray) + ", which the plan does not declare"
    failed = [record["partition"] for record in records
              if record.get("passed") is not True or record.get("exit_code") != 0
              or record.get("candidate_intact") is not True]
    if failed:
        return "holds failed or not intact partition " + ", ".join(failed)
    for record in records:
        try:
            output = raw_output_path(root, str(record.get("output_file")))
        except RuntimeError:
            return f"names no raw output of partition {record['partition']}"
        if (not output.is_file() or output.is_symlink()
                or hashlib.sha256(output.read_bytes()).hexdigest() != record.get("output_sha256")):
            return f"partition {record['partition']} raw command output is missing or changed"
    return None


def own_plan_targets(root: Path, current: dict) -> list[str]:
    """The automation targets of the Item's own Test Plan, in plan order."""
    plan = str(item_record(root, current["delivery"], current["story"]).get("test_plan_path") or "")
    return plan_automation_targets(delivery.docs_root(root), plan, f"{current['story']} of {current['delivery']}")


def spot_run_selection(root: Path, path: Path, current: dict) -> list[str]:
    """The own Test Plan targets QA's final test run runs itself at own_target_reuse spot_run, read as data.

    The file lies in the verification scratch, binds the frozen candidate and
    names at least one automation target of the Item's own Test Plan, each
    once; no command receives it, so its ids are bound by value in the run's
    identity.
    """
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(session_path(root).parent).as_posix()
    except ValueError as exc:
        raise RuntimeError("spot-run selection must be a regular file inside verification scratch") from exc
    if not path.is_file():
        raise RuntimeError("spot-run selection must be a regular file inside verification scratch")
    value = json.loads(raw_output_path(root, relative).read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or set(value) != {"schema_version", "candidate_hash", "spot_test_ids"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("candidate_hash") != current["candidate_hash"]):
        raise RuntimeError("spot-run selection must bind this candidate and declare only schema_version,"
                           " candidate_hash and spot_test_ids")
    identifiers = value["spot_test_ids"]
    if (not isinstance(identifiers, list) or not identifiers
            or any(not literal_test_id(identifier) for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise RuntimeError("spot_test_ids must name at least one unique literal test id, without option prefixes"
                           " or control characters")
    stray = sorted(set(identifiers) - set(own_plan_targets(root, current)))
    if stray:
        raise RuntimeError("spot_test_ids must be automation targets of the Item's own Test Plan; "
                           + ", ".join(stray) + (" is" if len(stray) == 1 else " are") + " not")
    return sorted(identifiers)


def kept_own_targets(own: list[str], spot: list[str]) -> list[str]:
    """The own targets the approved test command must run when QA spot-runs *spot*.

    They are the spot-run targets and every own target that is, prefixes or
    lies under one of them or under such a target in turn: a command that skips
    a reused id by node id prefix would otherwise skip part of a target it
    must run.
    """
    kept = set(spot)
    grown = True
    while grown:
        grown = False
        for target in own:
            if target not in kept and overlaps(target, sorted(kept)):
                kept.add(target)
                grown = True
    return sorted(kept)


def pre_handoff_reuse(root: Path, session: dict, current: dict, environment: dict,
                      *, fresh: bool = False, spot: list[str] | None = None) -> tuple[dict | None, str | None]:
    """The targets QA's final test run takes from the accepted pre-handoff run, or why it takes none.

    At pre_handoff_regression touched_suites the run the freeze accepted covers
    the targets of the earlier stories when it passed intact on the frozen
    tree, with the selection and the approved command derived for the frozen
    candidate, in the declared environment of QA's run, and is as fresh as
    final evidence must be. The Item's own Test Plan targets stay QA's to run,
    with every earlier target that overlaps one, and a *fresh* run reuses
    nothing. With *spot*, the own targets QA names at own_target_reuse
    spot_run, the run also covers every other own target, and only those
    *spot* keeps with kept_own_targets stay QA's, with every target that
    overlaps one; at accept_record *spot* is empty and the run covers every
    own target. At any other value it returns (None, None), or with *spot*
    why it reuses nothing.
    """
    value = pre_handoff_regression(root, current["delivery"])
    if value != TOUCHED_SUITES:
        return None, (f"own targets are reused only from an accepted pre-handoff run, which"
                      f" {PRE_HANDOFF_SWITCH} {value} never runs" if spot is not None else None)
    if fresh:
        return None, "run --fresh runs every suite itself"
    receipt = session.get("pre_handoff")
    if not isinstance(receipt, dict):
        return None, "the frozen session holds no accepted pre-handoff run"
    if receipt.get("exit_code") != 0 or receipt.get("candidate_intact") is not True:
        return None, "the accepted pre-handoff run did not pass intact"
    if not fresh_record(receipt):
        return None, "the accepted pre-handoff run expired"
    try:
        derived = derive_regression(root, current["delivery"], current["story"], current)
    except RuntimeError as exc:
        return None, f"the frozen candidate's selection cannot be derived again: {exc}"
    for label, keys in (("candidate tree", ("candidate_tree",)), ("selection", ("affected_test_ids",)),
                        ("approved command", ("kind", "command", "workdir"))):
        if any(receipt.get(key) != derived[key] for key in keys):
            return None, f"the pre-handoff run's {label} differs from the frozen candidate's"
    if {key: receipt.get(key) for key in ENVIRONMENT_FIELDS} != environment:
        return None, "the pre-handoff run's declared environment differs from this run's"
    own = derived["own_targets"]
    kept = own if spot is None else kept_own_targets(own, spot)
    stories = [{"delivery": entry["delivery"], "story": entry["story"], "test_ids": test_ids}
               for entry in derived["earlier_stories"]
               for test_ids in [sorted(test for test in set(entry["automation_targets"]) if not overlaps(test, kept))]
               if test_ids]
    reused_own = sorted(set(own) - set(kept))
    test_ids = sorted({test for entry in stories for test in entry["test_ids"]} | set(reused_own))
    if not test_ids:
        return None, ("the pre-handoff run covered no earlier story's target beyond the Item's own" if spot is None
                      else "the pre-handoff run covered no target beyond the own targets QA's run keeps")
    reuse = {"evidence_hash": receipt["evidence_hash"], "earlier_stories": stories, "test_ids": test_ids}
    if reused_own:
        reuse["own_targets"] = {"test_ids": reused_own, "spot_test_ids": sorted(spot)}
    return reuse, None


def overlaps(identifier: str, targets: list[str]) -> bool:
    """Whether a test id is, prefixes or lies under one of *targets*, compared as text.

    A command that skips the reused ids by node id prefix, as pytest's
    --deselect does with no boundary, skips every target the id prefixes, so
    an id that overlaps an own target the command must run is never reused.
    """
    return any(target.startswith(identifier) or identifier.startswith(target) for target in targets)


def reuse_problem(root: Path, session: dict, identity: dict, contract: dict) -> str | None:
    """Why the pre-handoff reuse a final test run records does not bind its frozen session, or None.

    It is checked as recorded: the reused run is the frozen session's accepted
    run, passed intact on the frozen tree, as fresh as final evidence must be
    now, of the approved command and in the declared environment of the final
    run, and the reused test ids are earlier story targets that run selected,
    none that is, prefixes or lies under one of the Item's own. A reuse of own
    targets is valid only at own_target_reuse spot_run, with its spot-run
    targets and its reused own targets disjoint automation targets of the
    Item's own Test Plan, and no reused id overlaps a target kept_own_targets
    keeps for the command instead, or at accept_record, with every own target
    reused and none spot-run.
    """
    reuse, receipt = identity["reused_pre_handoff"], session.get("pre_handoff")
    if (not isinstance(reuse, dict) or not isinstance(receipt, dict)
            or reuse.get("evidence_hash") != receipt.get("evidence_hash")):
        return "reuses a pre-handoff run the frozen session does not hold"
    stories, test_ids = reuse.get("earlier_stories"), reuse.get("test_ids")
    own_reuse = reuse.get("own_targets")
    if own_reuse is not None and (
            not isinstance(own_reuse, dict) or set(own_reuse) != {"test_ids", "spot_test_ids"}
            or any(not isinstance(own_reuse[key], list) or (key == "test_ids" and not own_reuse[key])
                   or any(not isinstance(test, str) for test in own_reuse[key])
                   or own_reuse[key] != sorted(set(own_reuse[key])) for key in own_reuse)):
        return "names its reused own targets apart from its spot-run targets"
    reused_own = own_reuse["test_ids"] if own_reuse is not None else []
    if (not isinstance(stories, list) or (not stories and own_reuse is None)
            or not isinstance(test_ids, list) or not test_ids
            or any(not isinstance(entry, dict) or not isinstance(entry.get("test_ids"), list) or not entry["test_ids"]
                   for entry in stories)
            or test_ids != sorted({test for entry in stories for test in entry["test_ids"]} | set(reused_own))):
        return "names its reused test ids apart from their earlier stories"
    current = session["candidate"]
    if (receipt.get("exit_code") != 0 or receipt.get("candidate_intact") is not True
            or receipt.get("candidate_tree") != git(root, "rev-parse", current["product_commit"] + "^{tree}")):
        return "reuses a pre-handoff run that did not pass intact on the frozen tree"
    if not fresh_record(receipt):
        return "reuses a pre-handoff run that expired"
    kind = str(receipt.get("kind"))
    if (kind not in {"test", "diagnostic_test"} or receipt.get("command") != contract.get(kind + "_command")
            or receipt.get("workdir") != str(contract.get(kind + "_workdir", "."))):
        return "reuses a pre-handoff run of another command than the approved one"
    if not set(test_ids) <= set(receipt.get("affected_test_ids") or []):
        return "reuses test ids the pre-handoff run did not select"
    own = own_plan_targets(root, current)
    if own_reuse is not None:
        value = own_target_reuse(root, current["delivery"])
        if value not in {SPOT_RUN, ACCEPT_RECORD}:
            return (f"reuses the Item's own Test Plan targets, which only process switch {OWN_TARGET_SWITCH}"
                    f" {SPOT_RUN} or {ACCEPT_RECORD} allows; {current['delivery']} runs it at {value}")
        spot = own_reuse["spot_test_ids"]
        if value == SPOT_RUN and not spot:
            return "names its reused own targets apart from its spot-run targets"
        if value == ACCEPT_RECORD and (spot or reused_own != sorted(set(own))):
            return (f"at {OWN_TARGET_SWITCH} {ACCEPT_RECORD} reuses every own Test Plan target of the Item and"
                    " spot-runs none, which this reuse does not")
        if not set(spot) | set(reused_own) <= set(own) or set(spot) & set(reused_own):
            return ("names spot-run targets or reused own targets that are no automation targets of the Item's own"
                    " Test Plan, or one target as both")
        if any(overlaps(test, kept_own_targets(own, spot)) for test in test_ids):
            return ("reuses a test id that is, prefixes or lies under one of the Item's own Test Plan targets that"
                    " QA's final test run spot-runs or keeps with them")
    elif any(overlaps(test, own) for test in test_ids):
        return ("reuses a test id that is, prefixes or lies under one of the Item's own Test Plan targets,"
                " which QA runs itself")
    if {key: receipt.get(key) for key in ENVIRONMENT_FIELDS} != {key: identity[key] for key in ENVIRONMENT_FIELDS}:
        return "reuses a pre-handoff run of another declared environment"
    return None


def fresh_record(record: dict) -> bool:
    """Whether a recorded command completed no longer ago than raw evidence stays fresh."""
    completed = record.get("completed_at", 0)
    return (isinstance(completed, (int, float))
            and 0 <= time.time() - completed <= policy()["raw_evidence_max_age_seconds"])


def require_raw_evidence(root: Path, session: dict, checks: dict) -> dict | None:
    """Check QA's final command evidence as recorded and return the environment it shares.

    Each record must be complete, run the approved command and stay fresh, and
    every final record of the session shares the environment identity of its
    full_test_suite evidence, the first check QA runs. No record is compared
    with the environment of the process that checks it, so evidence recorded
    in a reader's shell is approved from any other.
    """
    kinds = {"full_test_suite": "test", "mutation_whole_changed_files": "mutation", "dependency_audit": "dependency_audit"}
    contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    shared: tuple[str, dict] | None = None
    for check in required_checks(root, session["candidate"], "qa_engineer"):
        if check not in kinds:
            continue
        raw = session["raw_evidence"].get(kinds[check], {})
        if (raw.get("exit_code") != 0 or raw.get("candidate_intact") is not True or checks[check].get("raw_evidence_hash") != raw.get("evidence_hash")
                or raw.get("evidence_hash") != digest({key: item for key, item in raw.items() if key != "evidence_hash"})
                or raw.get("identity", {}).get("candidate_hash") != session["candidate"]["candidate_hash"]):
            raise RuntimeError(f"{check} requires successful same-candidate command evidence from run")
        identity = raw["identity"]
        problem = environment_problem(identity, contract)
        if problem:
            raise RuntimeError(f"{check} evidence {problem}")
        partitions = (partition_declaration(root, session["candidate"]["delivery"], contract, contract_body)
                      if check == "full_test_suite" else None)
        approved = partitions["command"] if partitions is not None else contract.get(kinds[check] + "_command")
        if (identity.get("kind") != kinds[check] or identity.get("command") != approved
                or identity.get("workdir") != contract.get(kinds[check] + "_workdir", ".")
                or identity.get("execution_isolation") != "private_clone_v1"):
            raise RuntimeError(f"{check} evidence does not run the approved command in its approved workdir")
        if not fresh_record(raw):
            raise RuntimeError(f"{check} evidence expired")
        groups = (group_report_declaration(root, session["candidate"]["delivery"], contract)
                  if check == "full_test_suite" else None)
        if groups is not None:
            problem = group_report_problem(raw, groups)
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        if partitions is not None:
            problem = partition_problem(root, raw, partitions)
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        if "reused_pre_handoff" in identity:
            problem = reuse_problem(root, session, identity, contract) if check == "full_test_suite" \
                else "reuses a pre-handoff run, which only the full test suite does"
            if problem:
                raise RuntimeError(f"{check} evidence {problem}")
        environment = {key: identity[key] for key in ENVIRONMENT_FIELDS}
        if shared is None:
            shared = (check, environment)
        elif environment != shared[1]:
            raise RuntimeError(f"{check} evidence ran in another environment than {shared[0]} evidence")
        if check == "full_test_suite" and checks[check].get("environment") != identity["environment_hash"]:
            raise RuntimeError("full suite environment must bind the recorded execution environment")
        output = raw_output_path(root, raw["output_file"])
        if (not output.is_file() or output.is_symlink()
                or hashlib.sha256(output.read_bytes()).hexdigest() != raw.get("output_sha256")):
            raise RuntimeError(f"{check} raw command output is missing or changed")
    for key, raw in sorted(session["raw_evidence"].items()):
        if raw.get("identity", {}).get("kind") == "live_test" and "reused_live_base" in raw["identity"]:
            problem = live_reuse_problem(root, session, raw, contract)
            if problem:
                raise RuntimeError(f"{key} evidence {problem}")
    return shared[1] if shared else None


def review_loop(root: Path, delivery_id: str) -> str:
    """The review_loop value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, delivery.REVIEW_LOOP)


def code_review_panel(root: Path, delivery_id: str) -> str:
    """The code_review_panel value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, CODE_REVIEW_PANEL)


def panel_assignments() -> list[dict]:
    """The code review panel's lens assignments, each with its key, its lens ids and their focus."""
    step = json.loads(PANEL_DATA_PATH.read_text(encoding="utf-8"))["review_steps"][PANEL_STEP]
    focus = {lens["id"]: lens["focus"] for lens in step["lenses"]}
    return [{"key": assignment[0], "lens": list(assignment), "focus": {lens: focus[lens] for lens in assignment}}
            for assignment in step["default_panel"]]


def changed_lines(root: Path, current: dict) -> int:
    """Lines added plus lines removed in the frozen change's product and test paths against its base.

    The vault under workspace/docs is not code a lens reads, and a binary file counts nothing.
    """
    total = 0
    for row in git(root, "diff", "--numstat", "--no-renames", "-z", current["integration_base_commit"],
                   current["product_commit"]).split("\0"):
        added, removed, path = (row.split("\t", 2) + ["", ""])[:3]
        if added.isdigit() and removed.isdigit() and not path.startswith("workspace/docs/"):
            total += int(added) + int(removed)
    return total


def lens_selection(root: Path, current: dict, assignments: list[dict],
                   previous: dict | None = None) -> tuple[list[dict], dict]:
    """The lens assignments the frozen change triggers at risk_scoped, and why each lens joined or not.

    Each lens joins by the trigger lens-triggers.json declares for it, read
    against the owner's parameter in force; a lens with no declared trigger
    always joins. An assignment joins when any of its lenses does.
    """
    data = json.loads(LENS_TRIGGERS_PATH.read_text(encoding="utf-8"))
    state = process_policy.delivery_values(delivery.docs_root(root), current["delivery"],
                                           switches={CODE_REVIEW_PANEL})
    parameters = state["values"][CODE_REVIEW_PANEL].get("parameters", {})
    measured = current
    if previous and all(previous.get(key) == current.get(key)
                        for key in ("delivery", "story", "inputs", "instruction_identity")):
        ancestor, paths = commit_delta(root, previous["product_commit"], current["product_commit"])
        if ancestor and paths is not None:
            measured = {**current, "integration_base_commit": previous["product_commit"], "changed_files": paths}
    lines = changed_lines(root, measured)
    lenses = {}
    for lens in sorted({lens for assignment in assignments for lens in assignment["lens"]}):
        declared = next((entry for entry in data["lens_triggers"] if entry["lens"] == lens), None)
        if declared is None:
            lenses[lens] = {"joined": True, "trigger": None}
            continue
        threshold = parameters.get(declared["parameter"])
        if threshold is None:
            raise RuntimeError(f"code_review_panel {RISK_SCOPED} needs parameter {declared['parameter']!r}"
                               " set in the Process Policy")
        if declared["trigger"] == "changed_path_matches":
            matched = sorted(path for path in measured["changed_files"]
                             if any(fnmatch.fnmatchcase(path, glob) for glob in threshold))
            lenses[lens] = {"joined": bool(matched), "trigger": declared["trigger"], "matched_paths": matched}
        elif declared["trigger"] == "changed_lines_above":
            lenses[lens] = {"joined": lines > threshold, "trigger": declared["trigger"], "threshold": threshold}
        else:
            raise RuntimeError(f"lens {lens!r} declares unknown trigger {declared['trigger']!r}")
    chosen = [assignment for assignment in assignments if any(lenses[lens]["joined"] for lens in assignment["lens"])]
    return chosen, {"changed_lines": lines, "lenses": lenses}


def panel_prefix(panel: dict, assignment: dict) -> str:
    """The id prefix of a lens assignment's findings in one panel pass."""
    return f"P{panel['pass']}-{assignment['key']}-"


def code_review_panel_state(root: Path, value: dict) -> dict | None:
    """The session's code review panel at code_review_panel beside_official, else None.

    A session that registered no panel result yet gets the panel it would
    start: its pass follows every earlier pass and every panel id the session
    carries, so a lens finding id never repeats an earlier one. The Process
    Policy is part of the frozen candidate, so the value holds for the session.
    """
    selected = code_review_panel(root, value["candidate"]["delivery"])
    if selected not in PANEL_VALUES:
        return None
    if isinstance(value.get("panel"), dict):
        return value["panel"]
    assignments, selection = panel_assignments(), None
    if selected == RISK_SCOPED:
        # With no lens triggered the official reviewer reads alone and registers through result.
        assignments, selection = lens_selection(root, value["candidate"], assignments,
                                                 value.get("previous_candidate"))
        if not assignments:
            return None
    passes = [record.get("pass", 0) for record in value.get("panel_history", [])]
    passes += [int(match.group(1)) for finding in value.get("unresolved_findings", [])
               for match in [PANEL_PASS_RE.match(str(finding.get("id", "")))] if match]
    panel = {"pass": max(passes, default=0) + 1, "assignments": assignments, "members": {}}
    if selection is not None:
        panel["lens_selection"] = selection
    return panel


def panel_history(session: dict | None) -> list[dict]:
    """The panel records of every code review panel pass of the Item that settled so far."""
    if not session:
        return []
    history = list(session.get("panel_history", []))
    worker = session["workers"]["code_reviewer"]
    record = worker.get("result", {}).get("panel")
    if worker["state"] == "settled" and isinstance(record, dict):
        history.append(record)
    return history


def complete_members(panel: dict) -> dict:
    """The panel's registered results, once the official result and every lens result are in."""
    members = panel["members"]
    missing = [key for key in [OFFICIAL, *(assignment["key"] for assignment in panel["assignments"])]
               if key not in members]
    if missing:
        raise RuntimeError("register the official result and every lens result with panel-result first;"
                           " missing: " + ", ".join(missing))
    return members


def panel_claims(panel: dict, value: dict, loop: str) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """The claims a code review panel's calibration rules, as returned, and the
    findings each lens claim may be ruled a duplicate of, with their severity.

    Every open critical or major finding of a lens result is a claim and, at
    review_loop blocking_delta, every open critical or major claim of the
    official result that no earlier calibration ruled. A lens claim duplicates
    an open critical or major official finding or another lens claim.
    """
    members = complete_members(panel)
    official = members[OFFICIAL]["result"]["findings"]
    lens_claims = [finding for assignment in panel["assignments"]
                   for finding in members[assignment["key"]]["result"]["findings"]
                   if finding["status"] == "open" and blocking(finding)]
    claims = list(lens_claims)
    if loop == "blocking_delta":
        ruled = {finding["id"] for finding in value.get("unresolved_findings", [])
                 if finding["role"] == "code_reviewer" and "calibrated_severity" in finding}
        claims += open_claims(official, ruled)
    targets = {finding["id"]: finding["severity"] for finding in [*official, *lens_claims]
               if finding["status"] == "open" and blocking(finding)}
    return (sorted(claims, key=lambda claim: claim["id"]),
            {claim["id"]: {identifier: severity for identifier, severity in targets.items()
                           if identifier != claim["id"]} for claim in lens_claims})


def item_record(root: Path, delivery_id: str, story: str) -> dict:
    directory = delivery.find_delivery(delivery.docs_root(root), delivery_id)
    if directory is None:
        raise RuntimeError("Delivery Item not found")
    return delivery.split_note(directory / "items" / delivery.id_slug(story) / "item.md")[0]


def nonblocking(finding: dict) -> bool:
    return finding["severity"].casefold() in {value.casefold() for value in policy()["nonblocking_severities"]}


def blocking(finding: dict) -> bool:
    return finding["severity"].casefold() in {value.casefold() for value in policy()["blocking_severities"]}


# Switch finding_disposition at follow_up_issue: only a critical finding blocks an
# Item; every open finding below it is a complete follow-up the coordinator files.
FINDING_DISPOSITION, FOLLOW_UP_ISSUE = "finding_disposition", "follow_up_issue"


def critical(finding: dict) -> bool:
    return finding["severity"].casefold() in {value.casefold() for value in policy()["critical_severities"]}


def follow_up_disposition(root: Path, delivery_id: str) -> bool:
    """Whether findings below critical are follow-ups: at follow_up_issue, and always for a single verifier."""
    docs = delivery.docs_root(root)
    return (delivery.delivery_switch_value(docs, delivery_id, FINDING_DISPOSITION) == FOLLOW_UP_ISSUE
            or delivery.delivery_switch_value(docs, delivery_id, ITEM_VERIFICATION) == SINGLE_VERIFIER)


# Switch item_verification at single_verifier: one fresh verifier reads the frozen
# candidate, runs QA's approved commands and registers both results in one pass.
ITEM_VERIFICATION, SINGLE_VERIFIER = "item_verification", "single_verifier"
SINGLE_VERIFIER_FIELDS = ("candidate_hash", "session_id", "verifier", "verdict", "report",
                          "review_checks", "qa_checks", "findings")


def register_single_verifier(root: Path, payload: dict) -> dict:
    """Split one verifier's result into the code review and QA results and settle both readers together.

    Both results pass every check a separate reader's result passes, so
    evidence approval and integration read them unchanged. Nothing is written
    unless both are accepted.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "qa_engineer", "settling its reader")
        value = read_session(root)
        current = value["candidate"]
        docs = delivery.docs_root(root)
        if delivery.delivery_switch_value(docs, current["delivery"], ITEM_VERIFICATION) != SINGLE_VERIFIER:
            raise RuntimeError("single-verifier-result runs at switch item_verification single_verifier")
        if code_review_panel(root, current["delivery"]) != "single_reader" or review_loop(root, current["delivery"]) == "blocking_delta":
            raise RuntimeError("item_verification single_verifier needs code_review_panel single_reader and a"
                               " review_loop other than blocking_delta: one verifier is no panel and no calibration")
        if not isinstance(payload, dict) or set(payload) - {*SINGLE_VERIFIER_FIELDS, "blocked_commands"} \
                or any(key not in payload for key in SINGLE_VERIFIER_FIELDS):
            raise RuntimeError("a single verifier result holds exactly: " + ", ".join(SINGLE_VERIFIER_FIELDS))
        if not isinstance(payload["verifier"], str) or not payload["verifier"].strip():
            raise RuntimeError("a single verifier result names its native reader identity in verifier")
        findings = payload["findings"] if isinstance(payload["findings"], list) else None
        if findings is None or any(not isinstance(finding, dict) or finding.get("role", "code_reviewer") not in ROLES
                                   for finding in findings):
            raise RuntimeError("each single verifier finding names role code_reviewer or qa_engineer")
        common = {key: payload[key] for key in ("candidate_hash", "session_id", "verdict", "report", "verifier")}
        if "blocked_commands" in payload:
            common["blocked_commands"] = payload["blocked_commands"]
        split = lambda role: [{key: item for key, item in finding.items() if key != "role"}
                              for finding in findings if finding.get("role", "code_reviewer") == role]
        results = [{**common, "role": "code_reviewer",
                    "mode": "review_repair" if value.get("previous_candidate") else "review_initial",
                    "checks": payload["review_checks"], "findings": split("code_reviewer")},
                   {**common, "role": "qa_engineer", "mode": "qa_final",
                    "checks": payload["qa_checks"], "findings": split("qa_engineer")}]
        if payload["verdict"] == "passed":
            require_intact(value)
        for result in results:
            check_result(root, value, result)
        for result in results:
            value = settle_result(root, value, result)
        return value


def gate_severities(root: Path, delivery_id: str) -> set[str]:
    """The severities that keep a result from passing: critical alone at follow_up_issue."""
    key = "critical_severities" if follow_up_disposition(root, delivery_id) else "blocking_severities"
    return {value.casefold() for value in policy()[key]}


def disposition_follow_ups(results: list[dict], item: dict) -> list[dict]:
    """Every open finding below critical of the final results, each a complete follow-up.

    The owner role is one of the Item's implementation roles, who fixes it
    later; QA's findings carry it as the code reviewer's do.
    """
    findings = [{**finding, "role": result["role"]} for result in results
                for finding in (calibrated_findings(result) if result["role"] == "code_reviewer"
                                else result.get("findings", []))
                if finding.get("status") == "open" and not critical(finding)]
    roles = follow_up_roles(item)
    problems = []
    for finding in findings:
        for key in ("file", "description"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                problems.append(f"{finding['id']} needs its {key}")
        if finding.get("owner_role") not in roles:
            problems.append(f"{finding['id']} owner_role must be one of: {', '.join(roles)}")
        if not isinstance(finding.get("revisit_trigger"), str) or not meaningful_text(finding["revisit_trigger"]):
            problems.append(f"{finding['id']} needs a concrete revisit_trigger")
    if problems:
        raise RuntimeError("finding_disposition follow_up_issue: every finding below critical is a follow-up;"
                           " " + "; ".join(problems))
    return sorted(findings, key=lambda finding: (finding["role"], finding["id"]))


def follow_up_issues(root: Path, delivery_id: str, story: str) -> dict:
    """The project issue each follow-up of the validated Item becomes, for the coordinator to file."""
    if not follow_up_disposition(root, delivery_id):
        raise RuntimeError("follow-up-issues runs at switch finding_disposition follow_up_issue")
    session = validate(root, delivery_id, story)
    results = [session["workers"][role]["result"] for role in ROLES]
    issues = [{"finding": finding["id"], "role": finding["role"], "severity": finding["severity"],
               "title": f"{story} follow-up {finding['id']}: "
                        + finding["description"].strip().splitlines()[0][:120],
               "body": "\n".join([f"Found by the {finding['role']} on candidate"
                                  f" {session['candidate']['candidate_hash']} of {delivery_id}.", "",
                                  f"File: {finding['file']}", f"Severity: {finding['severity']}",
                                  f"Owner role: {finding['owner_role']}",
                                  f"Revisit trigger: {finding['revisit_trigger']}", "",
                                  finding["description"].strip(), "",
                                  f"Verification: {finding['verification']}"])}
              for finding in disposition_follow_ups(results, item_record(root, delivery_id, story))]
    return {"delivery": delivery_id, "story": story, "issues": issues}


def calibrated_findings(result: dict) -> list[dict]:
    """A code review result's findings with its calibration rows applied.

    A confirmed claim keeps its severity, a claim calibrated minor becomes a
    follow-up with the row's owner role and trigger, and a claim calibrated
    invalid is closed by the row's cited evidence. Each keeps the severity it
    was claimed at and records its ruling, so no later session rules it again.
    A merged panel finding already records the severity it was claimed at.
    """
    rows = {row["finding"]: row for row in result.get("calibration", [])}
    findings = []
    for finding in result.get("findings", []):
        row = rows.get(finding["id"])
        if row is None:
            findings.append(finding)
            continue
        ruling = row["calibrated_severity"].casefold()
        ruled = {**finding, "claimed_severity": finding.get("claimed_severity", finding["severity"]),
                 "calibrated_severity": ruling}
        if ruling == "invalid":
            ruled["status"] = "resolved"
        elif ruling == "minor":
            ruled.update(severity="minor", owner_role=row.get("owner_role"),
                         revisit_trigger=row.get("revisit_trigger"))
        findings.append(ruled)
    return findings


def calibrated_verdict(result: dict) -> str:
    """A failed code review whose every calibrated claim is minor or invalid passes."""
    if result.get("verdict") != "failed" or not result.get("calibration"):
        return str(result.get("verdict"))
    return "failed" if any(finding["status"] == "open" and blocking(finding)
                           for finding in calibrated_findings(result)) else "passed"


def candidate_line_count(root: Path, commit: str, path: str) -> int | None:
    """The number of lines ``path`` holds in the frozen candidate, or None without that file."""
    completed = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob",
                                f"{commit}:{path}"], capture_output=True, check=False)
    if completed.returncode:
        return None
    data = completed.stdout
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def cites_candidate(root: Path, current: dict, reason: str) -> bool:
    """Whether a calibration reason cites a line of the frozen candidate as path:line."""
    for path, line in re.findall(r"(?<![\w./-])([\w.-]+(?:/[\w.-]+)*):([1-9][0-9]*)\b", reason):
        if delivery._is_normalized_claim(path):
            count = candidate_line_count(root, current["product_commit"], path)
            if count is not None and int(line) <= count:
                return True
    return False


def at_least_as_severe(severity: str, claimed: str) -> bool:
    """Whether a blocking severity is the claimed one or above it on the code review scale.

    Only the code review scale orders severities; any other declared severity
    is at least as severe as itself alone.
    """
    severity, claimed = severity.casefold(), claimed.casefold()
    return severity == claimed or (severity in REVIEW_SEVERITY_ORDER and claimed in REVIEW_SEVERITY_ORDER
                                   and REVIEW_SEVERITY_ORDER.index(severity) < REVIEW_SEVERITY_ORDER.index(claimed))


def calibration_problems(root: Path, current: dict, result: dict, ruled: set[str],
                         roles: list[str], duplicates: dict[str, dict[str, str]] | None = None) -> list[str]:
    """One row per open critical or major claim that no earlier calibration ruled.

    *duplicates* maps each lens claim of a code review panel to the findings it
    may be ruled a duplicate of, with their severity; only those claims take
    that ruling, and only toward a finding at least as severe, since no ruling
    lowers a claim to another blocking severity.
    """
    rows = result.get("calibration", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("finding"), str)
                                         for row in rows):
        return ["calibration must list one row object per claim"]
    claims = {finding["id"]: finding for finding in result.get("findings", [])
              if finding["status"] == "open" and blocking(finding) and finding["id"] not in ruled}
    listed = [row["finding"] for row in rows]
    if sorted(listed) != sorted(claims):
        return ["calibration must hold exactly one row for each open critical or major claim no earlier"
                f" calibration ruled: {', '.join(sorted(claims)) or 'none'}"]
    by_claim = {row["finding"]: row for row in rows}
    problems = []
    for row in rows:
        claim = claims[row["finding"]]
        label, severity = row["finding"], claim["severity"].casefold()
        claimed, ruling = row.get("claimed_severity"), row.get("calibrated_severity")
        targets = (duplicates or {}).get(label)
        if not isinstance(claimed, str) or claimed.casefold() != severity:
            problems.append(f"{label} calibration must record the claimed severity {claim['severity']}")
        if targets is None and (not isinstance(ruling, str) or ruling.casefold() not in {severity, "minor", "invalid"}):
            problems.append(f"{label} calibrated_severity must confirm {claim['severity']} or be minor or invalid")
        elif targets is not None and (not isinstance(ruling, str)
                                      or ruling.casefold() not in {severity, "minor", "invalid", DUPLICATE}):
            problems.append(f"{label} calibrated_severity must confirm {claim['severity']} or be minor, invalid"
                            " or duplicate")
        if targets is not None and isinstance(ruling, str) and ruling.casefold() == DUPLICATE:
            target = row.get("duplicate_of")
            target_row = by_claim.get(target, {}) if isinstance(target, str) else {}
            chained = str(target_row.get("calibrated_severity", "")).casefold() == DUPLICATE
            if not isinstance(target, str) or target not in targets or chained:
                problems.append(f"{label} duplicate_of must name an open critical or major official finding or"
                                " another lens claim not ruled duplicate")
            elif not at_least_as_severe(targets[target], claim["severity"]):
                problems.append(f"{label} duplicate_of must name a finding at least as severe as the claim,"
                                f" {claim['severity']}; {target} is {targets[target]}")
        elif "duplicate_of" in row:
            problems.append(f"{label} names duplicate_of without a duplicate ruling")
        reason = row.get("reason")
        if not isinstance(reason, str) or not meaningful_text(reason) or not cites_candidate(root, current, reason):
            problems.append(f"{label} calibration reason must cite the candidate text as path:line,"
                            " a line the frozen candidate holds")
        if isinstance(ruling, str) and ruling.casefold() == "minor" and (
                row.get("owner_role") not in roles or not isinstance(row.get("revisit_trigger"), str)
                or not meaningful_text(row["revisit_trigger"])):
            problems.append(f"{label} calibrated minor needs an owner_role of {', '.join(roles)}"
                            " and a concrete revisit_trigger")
    return problems


def open_claims(findings: list[dict], ruled: set[str]) -> list[dict]:
    """The open critical or major claims that no earlier calibration ruled, by id."""
    return sorted((finding for finding in findings
                   if finding["status"] == "open" and blocking(finding) and finding["id"] not in ruled),
                  key=lambda finding: finding["id"])


def check_calibration(root: Path, value: dict, current: dict, loop: str, panel: dict | None, result: dict) -> None:
    """Refuse a calibration reader's result that register_calibration may not register."""
    if loop != "blocking_delta" and panel is None:
        raise RuntimeError("severity calibration runs only at review_loop blocking_delta or"
                           " code_review_panel beside_official")
    if result.get("role") != "code_reviewer" or result.get("mode") != CALIBRATION_MODE:
        raise RuntimeError(f"a calibration result names role code_reviewer and mode {CALIBRATION_MODE}")
    if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
        raise RuntimeError("calibration does not bind this candidate and reader session")
    if value["workers"]["code_reviewer"]["state"] != "running":
        raise RuntimeError("calibrate the claims before the claiming code review result is registered")
    if value.get("calibration"):
        raise RuntimeError("this session's claims are already calibrated; each claim is ruled once")
    if not isinstance(result.get("report"), str) or not result["report"].strip():
        raise RuntimeError("calibration requires its independent report")
    claims = result.get("claims")
    if (not isinstance(claims, list) or not claims
            or any(not isinstance(claim, dict) or not isinstance(claim.get("id"), str)
                   or not isinstance(claim.get("severity"), str) or claim.get("status") != "open"
                   or not blocking(claim) for claim in claims)
            or len({claim["id"] for claim in claims}) != len(claims)):
        raise RuntimeError("calibration claims must list each open critical or major claim once, as returned")
    ruled = sorted({finding["id"] for finding in value.get("unresolved_findings", [])
                    if finding["role"] == "code_reviewer" and "calibrated_severity" in finding}
                   & {claim["id"] for claim in claims})
    if ruled:
        raise RuntimeError(f"an earlier calibration already ruled {', '.join(ruled)}")
    duplicates = None
    if panel is not None:
        expected, duplicates = panel_claims(panel, value, loop)
        if sorted(claims, key=lambda claim: claim["id"]) != expected:
            raise RuntimeError("calibration claims must be exactly the open critical or major claims to rule,"
                               " as returned: " + (", ".join(claim["id"] for claim in expected) or "none"))
    item = item_record(root, current["delivery"], current["story"])
    problems = calibration_problems(root, current, {"findings": claims, "calibration": result.get("calibration")},
                                    set(), follow_up_roles(item), duplicates)
    if problems:
        raise RuntimeError("severity calibration is incomplete: " + "; ".join(problems))


def register_calibration(root: Path, result: dict) -> dict:
    """Register a calibration reader's rulings as a result of their own.

    At review_loop blocking_delta a fresh code reviewer, never the reviewer that
    returned the claims, rules each open critical or major claim before the
    claiming result is registered, so the implementation writer stays idle.
    Its rows bind the exact claims it ruled, and they reach the claiming
    result only through this registration: register_result refuses a claiming
    result that carries rows of its own. At code_review_panel beside_official
    it rules, once every panel result is registered, exactly the claims
    panel_claims names, also at review_loop current.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "registering a calibration")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        loop = review_loop(root, current["delivery"])
        panel = code_review_panel_state(root, value)
        check_calibration(root, value, current, loop, panel, result)
        stored = dict(result)
        stored["result_hash"] = digest(result)
        value["calibration"] = {"state": "settled", "result": stored, "completed_at": time.time()}
        write_session(root, value)
        return value


def follow_up_roles(item: dict) -> list[str]:
    """The Item's implementation roles, which own its code review follow-ups."""
    roles = item.get("role_sequence")
    return [role for role in roles if role not in ROLES] if isinstance(roles, list) else []


def follow_up_problems(findings: list, roles: list[str]) -> list[str]:
    """Every open non-blocking code review finding is a tracked follow-up."""
    problems = []
    for finding in findings:
        if finding.get("status") != "open" or not nonblocking(finding):
            continue
        identifier = finding["id"]
        for key in ("file", "description"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                problems.append(f"{identifier} needs its {key}")
        if finding.get("owner_role") not in roles:
            problems.append(f"{identifier} owner_role must be one of: {', '.join(roles)}")
        trigger = finding.get("revisit_trigger")
        if not isinstance(trigger, str) or not meaningful_text(trigger):
            problems.append(f"{identifier} needs a concrete revisit_trigger")
    return problems


def open_follow_ups(result: dict, item: dict) -> list[dict]:
    """The open non-blocking findings of a code review result, each a complete follow-up."""
    findings = calibrated_findings(result)
    problems = follow_up_problems(findings, follow_up_roles(item))
    if problems:
        raise RuntimeError("code review follow-ups are incomplete: " + "; ".join(problems))
    return sorted((finding for finding in findings
                   if finding.get("status") == "open" and nonblocking(finding)),
                  key=lambda finding: finding["id"])


def check_findings(findings: object) -> None:
    """Every finding has a unique stable id, a declared severity, a verification and a status."""
    if (not isinstance(findings, list) or any(not isinstance(finding, dict)
            or not all(isinstance(finding.get(key), str) and finding[key].strip()
                       for key in ("id", "severity", "verification"))
            or finding.get("status") not in {"open", "resolved"} for finding in findings)
            or len({finding["id"] for finding in findings}) != len(findings)):
        raise RuntimeError("findings require unique stable IDs, severity, verification and open/resolved status")
    for finding in findings:
        controls = finding.get("negative_control_test_ids")
        if controls is not None and (not isinstance(controls, list) or not controls
                                     or any(not literal_test_id(identifier) for identifier in controls)
                                     or len(set(controls)) != len(controls)):
            raise RuntimeError(f"finding {finding['id']} negative_control_test_ids must list unique literal test IDs")
    allowed_severities = {value.casefold() for value in policy()["blocking_severities"] + policy()["nonblocking_severities"]}
    if any(finding["severity"].casefold() not in allowed_severities for finding in findings):
        raise RuntimeError("finding severity is not declared in the verification policy")


def check_result(root: Path, value: dict, result: dict) -> tuple[dict, dict, dict]:
    """Check a reader's result against its session before its reader settles.

    Returns the candidate the result binds, the role's inherited findings and
    the result's findings by id. settle_result adds the review_loop checks.
    """
    if result.get("role") == "qa_engineer":
        if result.get("verdict") != "cancelled":
            require_owner_step_progress(root, value, final=result.get("verdict") == "passed"
                                        and result.get("mode") == "qa_final")
        if runtime_needs_cleanup(value):
            raise RuntimeError("tear the environment down before settling the QA reader")
    current = value["candidate"] if result.get("verdict") == "cancelled" else require_current(root, value, allow_evidence=True)
    role, mode, verdict = (result.get(key) for key in ("role", "mode", "verdict"))
    if role not in ROLES or mode not in policy()["role_modes"][role]:
        raise RuntimeError("unsupported verification role or mode")
    if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
        raise RuntimeError("result does not bind this candidate and reader session")
    if value["workers"][role]["state"] != "running":
        raise RuntimeError("reader already settled; freeze a new session to replace its result")
    if verdict not in {"passed", "failed", "cancelled"}:
        raise RuntimeError("verification verdict must be passed, failed or cancelled")
    if not isinstance(result.get("report"), str) or not result["report"].strip():
        raise RuntimeError("verification result requires its independent report")
    if verdict == "cancelled":
        if result.get("cancellation_confirmed") is not True:
            raise RuntimeError("reader cancellation must be confirmed before reopening writes")
    elif verdict == "passed" and mode != "qa_diagnostic":
        checks = result.get("checks", {})
        for check in required_checks(root, current, role):
            evidence = checks.get(check, {}) if isinstance(checks, dict) else {}
            if (not isinstance(evidence, dict) or evidence.get("passed") is not True
                    or not isinstance(evidence.get("evidence"), str) or not evidence["evidence"].strip()):
                raise RuntimeError(f"final {role} result requires passing {check} evidence")
            if check == "mutation_whole_changed_files" and evidence.get("files") != current["mutation_files"]:
                raise RuntimeError("mutation evidence must cover every compiler-selected changed file")
        if role == "qa_engineer":
            test = checks["full_test_suite"]
            contract, contract_body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
            partitions = partition_declaration(root, current["delivery"], contract, contract_body)
            approved = partitions["command"] if partitions is not None else contract.get("test_command")
            if test.get("command") != approved or test.get("exit_code") != 0:
                raise RuntimeError("full suite evidence must identify the approved test command and successful exit")
            if not isinstance(test.get("environment"), str) or not test["environment"].strip():
                raise RuntimeError("full suite evidence requires the verification environment identity")
            environment = require_raw_evidence(root, value, checks)
            if current["runtime_required"]:
                require_runtime_evidence(root, value, checks["fresh_runtime"], environment)
    failed_live_cases(root, result)
    if check_blocked_commands(result) and verdict == "passed" and mode != "qa_diagnostic":
        raise RuntimeError(f"a final passed result cannot leave a {BLOCKED_BY_PERMISSION} command unrun;"
                           " settle as a diagnostic or failed result, and resume once the owner allows it")
    findings = result.get("findings", [])
    check_findings(findings)
    inherited_open = [finding for finding in value.get("unresolved_findings", [])
                      if finding["role"] == role and blocking(finding)]
    # The switch is read only when a finding it could let pass is present.
    gated = (verdict == "passed" and mode != "qa_diagnostic"
             and (any(finding["status"] == "open" for finding in findings) or inherited_open))
    blocking_severities = ({severity.casefold() for severity in policy()["blocking_severities"]}
                           if not gated else gate_severities(root, current["delivery"]))
    if verdict == "passed" and any(finding["severity"].casefold() in blocking_severities
                                   and finding["status"] == "open" for finding in findings):
        raise RuntimeError("passing verification cannot retain an open blocking finding")
    inherited = {finding["id"]: finding for finding in value.get("unresolved_findings", []) if finding["role"] == role}
    dispositions = {finding["id"]: finding for finding in findings}
    if any(finding["severity"].casefold() != inherited[identifier]["severity"].casefold()
           for identifier, finding in dispositions.items() if identifier in inherited):
        raise RuntimeError("inherited finding severity must be preserved")
    if verdict == "passed" and mode != "qa_diagnostic" and (
            not inherited.keys() <= dispositions.keys()
            or any(finding["severity"].casefold() in blocking_severities
                   and dispositions[identifier]["status"] != "resolved"
                   for identifier, finding in inherited.items())):
        raise RuntimeError("final result must explicitly disposition every inherited finding and resolve blocking findings")
    if gated and follow_up_disposition(root, current["delivery"]):
        disposition_follow_ups([result], item_record(root, current["delivery"], current["story"]))
    return current, inherited, dispositions


def register_result(root: Path, result: dict) -> dict:
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, str(result.get("role")), "settling its reader")
        value = read_session(root)
        if result.get("verdict") == "passed":
            require_intact(value)
        return settle_result(root, value, result)


def settle_result(root: Path, value: dict, result: dict, *, merged: bool = False) -> dict:
    """Check a reader's result and settle its reader with it.

    At code_review_panel beside_official a code review settles only with the
    result merge_panel builds, *merged*, whose calibration merge_panel matched
    to the claims as returned; a confirmed cancellation settles as before.
    """
    current, inherited, dispositions = check_result(root, value, result)
    role, verdict = result["role"], result["verdict"]
    findings = result.get("findings", [])
    if (role == "code_reviewer" and verdict != "cancelled" and not merged
            and code_review_panel_state(root, value) is not None):
        raise RuntimeError(f"at code_review_panel {code_review_panel(root, current['delivery'])} the code review"
                           " settles through merge-panel: register the official result and every lens result"
                           " with panel-result")
    if (role == "code_reviewer" and verdict != "cancelled"
            and review_loop(root, current["delivery"]) == "blocking_delta"):
        item = item_record(root, current["delivery"], current["story"])
        if not merged:
            if "calibration" in result:
                raise RuntimeError("calibration rows come only from the calibration reader's own result,"
                                   " registered with calibrate; the claiming result carries none")
            ruled = {identifier for identifier, finding in inherited.items() if "calibrated_severity" in finding}
            claims = open_claims(findings, ruled)
            registered = (value.get("calibration") or {}).get("result")
            if claims or registered:
                if registered is None or sorted(registered["claims"], key=lambda claim: claim["id"]) != claims:
                    raise RuntimeError("register the calibration reader's result with calibrate for exactly the"
                                       " open critical or major claims no earlier calibration ruled, as returned: "
                                       + (", ".join(claim["id"] for claim in claims) or "none"))
                result = {**result, "calibration": registered["calibration"],
                          "calibration_result_hash": registered["result_hash"]}
            problems = calibration_problems(root, current, result, ruled, follow_up_roles(item))
            if problems:
                raise RuntimeError("severity calibration is incomplete: " + "; ".join(problems))
        if calibrated_verdict(result) != verdict:
            checks = result.get("checks", {})
            for check in required_checks(root, current, role):
                evidence = checks.get(check, {}) if isinstance(checks, dict) else {}
                if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence"), str) \
                        or not evidence["evidence"].strip():
                    raise RuntimeError(f"a calibrated {role} pass requires {check} evidence")
            if not inherited.keys() <= dispositions.keys():
                raise RuntimeError("final result must explicitly disposition every inherited finding"
                                   " and resolve blocking findings")
        # Refuses an open minor finding that is not a complete follow-up.
        open_follow_ups(result, item)
    if role == "qa_engineer" and verdict == "cancelled":
        for row in value.get("owner_steps", []):
            if row["state"] == "awaiting_owner_step":
                row.update(state="cancelled", completed_at=time.time())
    stored = dict(result)
    if role == "qa_engineer" and value.get("owner_steps"):
        stored["owner_steps"] = owner_step_progress(value)
    stored["result_hash"] = digest(stored)
    value["workers"][role] = {"state": "cancelled" if verdict == "cancelled" else "settled", "result": stored,
                              "completed_at": time.time(),
                              "reader_elapsed_seconds": max(0.0, time.time() - value["workers"][role].get("started_at", time.time()))}
    write_session(root, value)
    return value


def check_panel_result(root: Path, value: dict, current: dict, panel: dict, result: dict) -> str:
    """Refuse a code review panel result that register_panel_result may not register; return its member key."""
    if result.get("role") != "code_reviewer":
        raise RuntimeError("a code review panel result names role code_reviewer")
    if result.get("candidate_hash") != current["candidate_hash"] or result.get("session_id") != value["session_id"]:
        raise RuntimeError("result does not bind this candidate and reader session")
    if value["workers"]["code_reviewer"]["state"] != "running":
        raise RuntimeError("the code review already settled; freeze a new session to replace it")
    mode = result.get("mode")
    if mode != PANEL_LENS_MODE and mode not in policy()["role_modes"]["code_reviewer"]:
        raise RuntimeError("a code review panel result names mode review_initial or review_repair for the"
                           " official reviewer, or panel_lens for a lens reader")
    if result.get("verdict") not in {"passed", "failed"}:
        raise RuntimeError("a code review panel result's verdict is passed or failed")
    members = panel["members"]
    inherited = {finding["id"] for finding in value.get("unresolved_findings", [])
                 if finding["role"] == "code_reviewer"}
    if mode == PANEL_LENS_MODE:
        assignment = next((assignment for assignment in panel["assignments"]
                           if assignment["lens"] == result.get("lens")), None)
        if assignment is None:
            raise RuntimeError("lens must name one assignment of the code review panel: "
                               + "; ".join(", ".join(item["lens"]) for item in panel["assignments"]))
        key = assignment["key"]
        if key in members:
            raise RuntimeError(f"lens assignment {key} is already registered")
        if not isinstance(result.get("report"), str) or not result["report"].strip():
            raise RuntimeError("verification result requires its independent report")
        findings = result.get("findings", [])
        check_findings(findings)
        if result["verdict"] == "passed" and any(blocking(finding) for finding in findings):
            raise RuntimeError("passing verification cannot retain an open blocking finding")
        prefix = panel_prefix(panel, assignment)
        for finding in findings:
            identifier = finding["id"]
            if finding["status"] != "open":
                raise RuntimeError(f"{identifier} must be a new open finding")
            if not identifier.startswith(prefix):
                raise RuntimeError(f"{identifier} must start with its assignment's id_prefix {prefix}")
            if not all(isinstance(finding.get(field), str) and finding[field].strip()
                       for field in ("file", "description")):
                raise RuntimeError(f"{identifier} needs its file and description")
        held = inherited | {finding["id"] for member in members.values()
                            for finding in member["result"].get("findings", [])}
    else:
        if OFFICIAL in members:
            raise RuntimeError("the official code review result is already registered")
        check_result(root, value, result)
        if "calibration" in result:
            raise RuntimeError("calibration rows come only from the calibration reader's own result,"
                               " registered with calibrate; the claiming result carries none")
        if review_loop(root, current["delivery"]) == "blocking_delta":
            problems = follow_up_problems(result.get("findings", []), follow_up_roles(
                item_record(root, current["delivery"], current["story"])))
            if problems:
                raise RuntimeError("code review follow-ups are incomplete: " + "; ".join(problems))
        key, findings = OFFICIAL, result.get("findings", [])
        held = {finding["id"] for member in members.values() for finding in member["result"].get("findings", [])}
    for finding in findings:
        if finding["id"] in held:
            raise RuntimeError(f"{finding['id']} is already held by another result of this pass or an"
                               " earlier cycle")
    return key


def register_panel_result(root: Path, result: dict) -> dict:
    """Register one result of a code review panel pass at code_review_panel beside_official.

    The official reviewer's result is checked as register_result checks it. A
    lens reader's result names its assignment and holds only new, open
    findings under the assignment's id prefix. Each records when it returned;
    neither settles the code review, so the implementation writer stays idle
    until merge_panel does.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "registering a panel result")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        panel = code_review_panel_state(root, value)
        if panel is None:
            raise RuntimeError("panel-result registers a code review panel result only at code_review_panel"
                               " beside_official")
        key = check_panel_result(root, value, current, panel, result)
        members = panel["members"]
        stored = dict(result)
        stored["result_hash"] = digest(result)
        completed = time.time()
        started = value["workers"]["code_reviewer"].get("started_at", completed)
        value["panel"] = {**panel, "members": {**members, key: {
            "result": stored, "completed_at": completed, "elapsed_seconds": max(0.0, completed - started)}}}
        write_session(root, value)
        return value


def merged_panel_result(root: Path, value: dict, current: dict, panel: dict) -> dict:
    """The one code review result merge_panel settles, or the refusal of an incomplete panel pass."""
    worker = value["workers"]["code_reviewer"]
    if worker["state"] != "running":
        raise RuntimeError("the code review already settled; freeze a new session to replace it")
    claims, _targets = panel_claims(panel, value, review_loop(root, current["delivery"]))
    registered = (value.get("calibration") or {}).get("result")
    if (claims or registered) and (registered is None or sorted(
            registered["claims"], key=lambda claim: claim["id"]) != claims):
        raise RuntimeError("register the calibration reader's result with calibrate for exactly the open"
                           " critical or major claims to rule, as returned: "
                           + (", ".join(claim["id"] for claim in claims) or "none"))
    rows = {row["finding"]: row for row in registered["calibration"]} if registered else {}
    members = panel["members"]
    official = members[OFFICIAL]["result"]
    carried = {finding["id"]: finding for finding in value.get("unresolved_findings", [])
               if finding["role"] == "code_reviewer" and finding.get("source") == "panel"}
    findings = [{**finding, "source": "panel", "lens": carried[finding["id"]]["lens"]}
                if finding["id"] in carried else {**finding, "source": OFFICIAL}
                for finding in official["findings"]]
    gating = {source: sorted(finding["id"] for finding in findings if finding["source"] == source
                             and finding["status"] == "open" and blocking(finding))
              for source in (OFFICIAL, "panel")}
    rulings: dict = {"confirmed": [], "minor": [], "invalid": [], DUPLICATE: {}}
    claims = []
    for assignment in panel["assignments"]:
        for finding in members[assignment["key"]]["result"]["findings"]:
            if not blocking(finding):
                continue
            row = rows[finding["id"]]
            ruling = row["calibrated_severity"].casefold()
            claim = {"finding": finding["id"], "lens": assignment["lens"], "severity": finding["severity"],
                     "ruling": ruling if ruling in {DUPLICATE, "invalid", "minor"} else "confirmed",
                     "file": finding["file"], "description": finding["description"], "reason": row["reason"]}
            claims.append(claim)
            if ruling == DUPLICATE:
                claim["duplicate_of"] = row["duplicate_of"]
                rulings[DUPLICATE][finding["id"]] = row["duplicate_of"]
                continue
            if ruling == "invalid":
                rulings["invalid"].append(finding["id"])
                continue
            entry = {**finding, "source": "panel", "lens": assignment["lens"],
                     "claimed_severity": finding["severity"], "calibrated_severity": ruling}
            if ruling == "minor":
                follow_up = {"owner_role": row.get("owner_role"), "revisit_trigger": row.get("revisit_trigger")}
                entry.update(severity="minor", **follow_up)
                claim.update(follow_up)
                rulings["minor"].append(finding["id"])
            else:
                rulings["confirmed"].append(finding["id"])
            findings.append(entry)
    official_seconds = members[OFFICIAL]["elapsed_seconds"]
    combined = max(0.0, time.time() - worker.get("started_at", time.time()))
    record = {"pass": panel["pass"], "mode": official["mode"],
              "official_result_hash": official["result_hash"],
              "lens_result_hashes": {assignment["key"]: members[assignment["key"]]["result"]["result_hash"]
                                     for assignment in panel["assignments"]},
              "official_seconds": round(official_seconds, 1),
              "panel_seconds": round(max(members[assignment["key"]]["elapsed_seconds"]
                                         for assignment in panel["assignments"]), 1),
              "combined_seconds": round(combined, 1),
              "combined_ratio": round(combined / official_seconds, 2) if official_seconds > 0 else None,
              "official_blocking": gating[OFFICIAL], "carried_panel_blocking": gating["panel"],
              "confirmed": sorted(rulings["confirmed"]), "minor": sorted(rulings["minor"]),
              "invalid": sorted(rulings["invalid"]), DUPLICATE: dict(sorted(rulings[DUPLICATE].items())),
              "claims": sorted(claims, key=lambda claim: claim["finding"])}
    result = {key: item for key, item in official.items() if key not in {"result_hash", "findings", "verdict"}}
    result.update(verdict="failed" if official["verdict"] == "failed" or rulings["confirmed"] else "passed",
                  findings=findings, panel=record)
    if registered:
        result.update(calibration=registered["calibration"], calibration_result_hash=registered["result_hash"])
    return result


def merge_panel(root: Path) -> dict:
    """Register the one code review result of a code review panel pass and settle the code review.

    Its findings are the official result's, with source official, except that
    a panel finding of an earlier pass the official result re-lists keeps its
    source and lens, and each lens claim that calibration confirmed or lowered
    to minor, with source panel, its lens and its claimed and calibrated
    severity. A claim ruled invalid or duplicate and a lens finding that does
    not block stay out; the calibration rows on the result keep every ruling.
    The result keeps the official report, mode and checks, fails when the
    official result fails or a lens claim is confirmed, and settles through
    every check register_result applies. Its panel record keeps the combined
    step's wall clock from the freeze to this merge against the official
    reviewer's, the open blocking findings of the official result by source,
    and every lens claim with its text and ruling, which no later session
    holds otherwise.
    """
    root = root.resolve()
    with locked(root):
        wait_for_own_command(root, "code_reviewer", "merging the code review panel")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        panel = code_review_panel_state(root, value)
        if panel is None:
            raise RuntimeError("merge-panel settles a code review only at code_review_panel beside_official")
        result = merged_panel_result(root, value, current, panel)
        return settle_result(root, value, result, merged=True)


def declared_owner_steps(root: Path) -> list[dict]:
    props, body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    steps, errors = operation_compile.owner_steps(body)
    if steps and (props.get("status") != "approved"
                  or props.get("source_hash") != operation_compile.receipt_hash(props, body)):
        errors.append("Owner Steps requires an unchanged approved Verification Contract")
    if errors:
        raise RuntimeError("; ".join(errors))
    return steps


def owner_step_progress(value: dict) -> list[dict]:
    return [{**row, "state": "timed_out" if row["state"] == "awaiting_owner_step"
             and time.time() >= row["deadline"] else row["state"]}
            for row in value.get("owner_steps", [])]


def pending_owner_step(value: dict) -> dict | None:
    return next((row for row in owner_step_progress(value) if row["state"] == "awaiting_owner_step"), None)


def require_owner_step_progress(root: Path, value: dict, *, final: bool = False) -> None:
    pending = pending_owner_step(value)
    if pending is not None:
        raise RuntimeError(f"awaiting_owner_step {pending['step']}: keep this QA reader and frozen candidate open;"
                           " the owner must attach the command output before QA continues")
    if final:
        passed = {row["step"] for row in owner_step_progress(value) if row["state"] == "passed"}
        missing = [row["step"] for row in declared_owner_steps(root) if row["step"] not in passed]
        if missing:
            raise RuntimeError("final QA requires passing owner steps: " + ", ".join(missing))


def await_owner_step(root: Path, step: str) -> dict:
    """Keep QA running at a declared handoff without executing the owner's command."""
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the active verification command before an owner handoff")
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        if value["workers"]["qa_engineer"]["state"] != "running":
            raise RuntimeError("an owner handoff requires the active QA reader")
        plan = declared_owner_steps(root)
        completed = value.get("owner_steps", [])
        pending = pending_owner_step(value)
        if pending is not None:
            if pending["step"] != step:
                raise RuntimeError("another owner step is already pending: " + pending["step"])
            return {"session_id": value["session_id"], "candidate_hash": current["candidate_hash"], **pending}
        if any(row["state"] != "passed" for row in owner_step_progress(value)):
            raise RuntimeError("the owner step failed or timed out; record failed QA before retrying")
        if len(completed) >= len(plan) or plan[len(completed)]["step"] != step:
            raise RuntimeError("await-owner-step must name the next declared owner step")
        row = {**plan[len(completed)], "state": "awaiting_owner_step", "started_at": time.time()}
        row["deadline"] = row["started_at"] + row["timeout_seconds"]
        value.setdefault("owner_steps", []).append(row)
        write_session(root, value)
        return {"session_id": value["session_id"], "candidate_hash": current["candidate_hash"], **row}


def record_owner_step(root: Path, result: dict) -> dict:
    """Attach explicitly confirmed owner output; the runner never executes that command."""
    root = root.resolve()
    with locked(root):
        value = read_session(root)
        current = require_current(root, value, allow_evidence=True)
        if value["workers"]["qa_engineer"]["state"] != "running" or command_active(root):
            raise RuntimeError("owner output requires the waiting QA reader with no command running")
        keys = {"session_id", "candidate_hash", "step", "command", "exit_code", "output", "owner_confirmed"}
        if (not isinstance(result, dict) or set(result) != keys or result["owner_confirmed"] is not True
                or type(result["exit_code"]) is not int or not isinstance(result["output"], str)):
            raise RuntimeError("owner output requires the exact receipt fields and explicit owner confirmation")
        pending = pending_owner_step(value)
        if pending is None:
            previous = next((row for row in value.get("owner_steps", [])
                             if row.get("result", {}).get("evidence_hash") == digest(result)), None)
            if previous is not None:
                return {"session_id": value["session_id"], "candidate_hash": current["candidate_hash"], **previous}
            raise RuntimeError("no owner step is waiting, or its deadline expired")
        if (result["session_id"] != value["session_id"] or result["candidate_hash"] != current["candidate_hash"]
                or result["step"] != pending["step"] or result["command"] != pending["command"]):
            raise RuntimeError("owner output does not bind the waiting step, command and frozen candidate")
        passed = result["exit_code"] == 0 and pending["expected_output"] in result["output"]
        completed_at = time.time()
        if completed_at >= pending["deadline"]:
            raise RuntimeError("owner step deadline expired before its output was accepted")
        pending.update(state="passed" if passed else "failed", completed_at=completed_at,
                       result={**result, "evidence_hash": digest(result)})
        value["owner_steps"][-1] = pending
        write_session(root, value)
        return {"session_id": value["session_id"], "candidate_hash": current["candidate_hash"], **pending}


def resume_qa(root: Path) -> dict:
    """Continue QA on unchanged sources without repeating the independent review."""
    root = root.resolve()
    with locked(root):
        if command_active(root):
            raise RuntimeError("wait for the verification command to exit before resuming QA;"
                               " `wait` returns once it has")
        value = read_session(root)
        require_current(root, value, allow_evidence=True)
        worker = value["workers"]["qa_engineer"]
        result = worker.get("result", {})
        failed_final = result.get("mode") == "qa_final" and result.get("verdict") == "failed"
        if worker["state"] != "settled" or not (result.get("mode") == "qa_diagnostic" or failed_final):
            raise RuntimeError("only a settled QA diagnostic or failed final QA can resume on the same candidate")
        if runtime_needs_cleanup(value):
            raise RuntimeError("tear the environment down before resuming QA")
        retained = {(finding["role"], finding["id"]): finding for finding in value.get("unresolved_findings", [])}
        for finding in result.get("findings", []):
            if finding["status"] == "open":
                retained[("qa_engineer", finding["id"])] = {**finding, "role": "qa_engineer"}
        value["unresolved_findings"] = [retained[key] for key in sorted(retained)]
        value.setdefault("qa_attempts", []).append(worker)
        if value.get("owner_steps"):
            value.setdefault("owner_step_attempts", []).append(owner_step_progress(value))
            value.pop("owner_steps")
        runtime = value.get("runtime")
        failed_runtime = runtime and (runtime.get("interrupted_commands")
                                      or any(event.get("exit_code") != 0 or event.get("candidate_intact") is not True
                                             for event in runtime.get("events", [])))
        permission_only = bool(result.get("blocked_commands")) and not failed_runtime
        if runtime and ((failed_final and not permission_only) or failed_runtime):
            # Preserve failure evidence and its files. A new attempt receives a
            # different checkout and output namespace and cannot inherit events.
            value.setdefault("runtime_attempts", []).append(runtime)
            value.pop("runtime")
        value["workers"]["qa_engineer"] = {"state": "running", "started_at": time.time(), "resumed_from": result}
        if permission_only:
            value["workers"]["qa_engineer"]["resume_commands"] = [row["command"] for row in result["blocked_commands"]]
        write_session(root, value)
        return value


def validate(root: Path, delivery_id: str, story: str) -> dict:
    root = root.resolve()
    if command_active(root):
        raise RuntimeError("verification command is still running")
    value = read_session(root)
    require_intact(value)
    current = require_current(root, value, allow_evidence=True)
    if current["delivery"] != delivery_id or current["story"] != story:
        raise RuntimeError("verification session belongs to another Item")
    for role in ROLES:
        worker = value["workers"][role]
        result = worker.get("result", {})
        verdict = result.get("verdict")
        if (role == "code_reviewer" and verdict == "failed" and result.get("calibration")
                and review_loop(root, delivery_id) == "blocking_delta"):
            verdict = calibrated_verdict(result)
        if (worker["state"] != "settled" or verdict != "passed"
                or result.get("mode") not in policy()["final_modes"][role]
                or result.get("candidate_hash") != current["candidate_hash"]
                or result.get("session_id") != value["session_id"]
                or result.get("result_hash") != digest({key: item for key, item in result.items() if key != "result_hash"})):
            raise RuntimeError(f"same-candidate final passed {role} result is required")
        if result.get("mode") == SCOPED_REPAIR:
            problem = scoped_repair_problem(value, result)
            if problem:
                raise RuntimeError(problem)
            continue
        if role == "qa_engineer":
            require_owner_step_progress(root, value, final=True)
            environment = require_raw_evidence(root, value, result["checks"])
            if current["runtime_required"]:
                require_runtime_evidence(root, value, result["checks"]["fresh_runtime"], environment)
    return value


def runtime_needs_cleanup(session: dict) -> bool:
    state = session.get("runtime", {})
    if state.get("lifecycle") == LONG_LIVED:
        # A long-lived instance stays up between sessions; only an interrupted command needs teardown.
        return bool(state.get("pending"))
    return bool(state.get("active") or state.get("cleanup_required") or state.get("pending"))


def runtime_lifecycle_policy(root: Path, delivery_id: str) -> tuple[str, list[str]]:
    """The runtime_lifecycle value a Delivery runs under and the owner's environment_paths globs."""
    state = process_policy.delivery_values(delivery.docs_root(root), delivery_id,
                                           switches={RUNTIME_LIFECYCLE})["values"][RUNTIME_LIFECYCLE]
    return state["value"], state.get("parameters", {}).get("environment_paths") or []


def fresh_runtime_problem(session: dict, evidence: dict | None = None) -> bool:
    """Whether the session's recorded runtime events miss the fresh down/up/seed/logs/down shape."""
    state = session.get("runtime", {})
    events = state.get("events", [])
    attempt_id = state.get("attempt_id")
    verbs = [event["verb"] for event in events if event.get("exit_code") == 0 and event["verb"] in ENVIRONMENT_VERBS]
    return bool(not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
                or runtime_needs_cleanup(session) or state.get("interrupted_commands")
                or state.get("lifecycle", FRESH) != FRESH
                or state.get("active") is not False or not verbs or verbs[0] != "down" or verbs[-1] != "down"
                or any(verb not in verbs for verb in ("up", "seed", "logs"))
                or (evidence is not None
                    and evidence.get("event_hashes") != [event["evidence_hash"] for event in events]))


def fresh_runtime_history(session: dict | None) -> list[dict]:
    """The Item's candidates whose sessions recorded a complete fresh runtime lifecycle, oldest first."""
    if not session:
        return []
    history = list(session.get("fresh_runtime_history", []))
    events = session.get("runtime", {}).get("events", [])
    # A complete, intact fresh lifecycle proves the environment code even when QA failed on product behaviour.
    if (events and not fresh_runtime_problem(session)
            and all(event.get("exit_code") == 0 and event.get("candidate_intact") is True for event in events)):
        record = {"candidate_hash": session["candidate"]["candidate_hash"],
                  "product_commit": session["candidate"]["product_commit"],
                  "event_hashes": [event["evidence_hash"] for event in events]}
        if record not in history:
            history.append(record)
    return history


def runtime_lifecycle(root: Path, session: dict) -> dict:
    """The runtime lifecycle QA's environment evidence of the session's candidate must follow, and why.

    At runtime_lifecycle long_lived it is long_lived only when an earlier
    candidate of the Item holds fresh runtime evidence and holds every path of
    the owner's environment_paths globs as this candidate does. Otherwise the
    fresh down/up path applies: fresh_evidence_missing or environment_code_changed.
    """
    value, globs = runtime_lifecycle_policy(root, session["candidate"]["delivery"])
    if value != LONG_LIVED:
        return {"mode": FRESH, "reason": "switch"}
    if not globs:
        raise RuntimeError(f"{RUNTIME_LIFECYCLE} {LONG_LIVED} needs parameter 'environment_paths'"
                           " set in the Process Policy")
    commit = session["candidate"]["product_commit"]
    changed = None
    for record in reversed(session.get("fresh_runtime_history", [])):
        try:
            paths = git(root, "diff", "--name-only", "--no-renames", "-z", record["product_commit"], commit).split("\0")
        except RuntimeError:
            continue
        matched = sorted(path for path in paths if path and any(fnmatch.fnmatchcase(path, glob) for glob in globs))
        if not matched:
            return {"mode": LONG_LIVED, "reason": "fresh_evidence_held", "fresh_candidate": record["candidate_hash"]}
        changed = changed if changed is not None else matched
    if changed is not None:
        return {"mode": FRESH, "reason": "environment_code_changed", "matched_paths": changed}
    return {"mode": FRESH, "reason": "fresh_evidence_missing"}


def runtime_environment_identity(root: Path, current: dict) -> dict:
    return environment_identity(root, command_environment(root, current))


ENVIRONMENT_VERBS = operation_compile.ENVIRONMENT_VERBS


def declared_qa_verbs(root: Path) -> dict[str, dict]:
    """The QA Verbs an unchanged approved Verification Contract declares, by verb."""
    props, body = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    verbs, errors = operation_compile.qa_verbs(body)
    if verbs and (props.get("status") != "approved"
                  or props.get("source_hash") != operation_compile.receipt_hash(props, body)):
        errors.append("QA Verbs requires an unchanged approved Verification Contract")
    if errors:
        raise RuntimeError("; ".join(errors))
    return {row["verb"]: row for row in verbs}


def run_environment(root: Path, verb: str, value: str | None = None) -> dict:
    """Invoke only the approved environment contract, or a declared QA verb, in a persistent private clone."""
    root = root.resolve()
    declared = None
    if verb not in ENVIRONMENT_VERBS:
        declared = declared_qa_verbs(root).get(verb)
        if declared is None:
            raise RuntimeError("unsupported environment verb; declare it in the Verification Contract's QA Verbs")
    with environment_lock(root, "qa_engineer", "environment --verb " + verb), \
            command_lock(root, "qa_engineer", "environment --verb " + verb):
        with locked(root):
            session = read_session(root)
            if verb != "down":
                require_owner_step_progress(root, session)
            current = session["candidate"] if verb == "down" else require_current(root, session, allow_evidence=True)
            if not current["runtime_required"] or session["workers"]["qa_engineer"]["state"] != "running":
                raise RuntimeError("environment commands require an active runtime QA reader")
            contract, _, error = delivery.parse_frontmatter(git(root, "show", current["product_commit"] + ":workspace/docs/operation/environment-contract.md"))
            if error:
                raise RuntimeError("frozen environment contract cannot be parsed")
            command = contract.get("env_command", "")
            if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
                raise RuntimeError("approved environment command is missing or unresolved")
            if verb in {"seed", "url"}:
                allowed = contract.get("scenarios" if verb == "seed" else "service_catalog", [])
                if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value)
                        or value not in allowed):
                    raise RuntimeError("environment argument must be an exact approved scenario or service identifier")
            elif value is not None:
                raise RuntimeError("this environment verb does not accept a value")
            if declared:
                command, contract = declared["command"], {"env_workdir": declared["workdir"]}
            state = session.get("runtime", {"attempt_id": uuid.uuid4().hex, "events": [], "active": False})
            if state.get("pending") and verb != "down":
                raise RuntimeError("interrupted runtime command requires teardown before another command")
            if state.get("attempt_id") and not re.fullmatch(r"[0-9a-f]{32}", str(state["attempt_id"])):
                raise RuntimeError("runtime attempt identity is invalid")
            if (not state["events"] and not state.get("pending")
                    and runtime_lifecycle(root, session)["mode"] == LONG_LIVED):
                state["lifecycle"] = LONG_LIVED
            long_lived = state.get("lifecycle") == LONG_LIVED
            successful = [event["verb"] for event in state["events"]
                          if event["exit_code"] == 0 and event["verb"] in ENVIRONMENT_VERBS]
            if verb == "up" and not long_lived and (not successful or successful[-1] != "down"):
                raise RuntimeError("fresh runtime requires a successful down before up")
            if verb in {"seed", "logs", "url"} and not state["active"]:
                raise RuntimeError("runtime must be up before seed, logs or url")
            # Every attempt of this Item runs in one kept checkout path, because
            # a project environment may bind an Item instance to the checkout
            # that created it. Each attempt's first command re-pins it to that
            # attempt's candidate as a fresh clone would hold it; raw output
            # stays per attempt. A session started by an older package keeps
            # its own per-attempt path until its teardown.
            scratch = session_path(root).parent / "scratch"
            first_command = not state["events"] and not state.get("pending")
            legacy = scratch / ("runtime-" + state.get("attempt_id", session["session_id"]))
            kept = "attempt_id" in state and (first_command or not legacy.exists())
            name = runtime_checkout_name(scratch, current["delivery"], current["story"])
            execution_root = safe_runtime_path(root, scratch / name if kept else legacy)
            if not execution_root.exists():
                execution_root.parent.mkdir(parents=True, exist_ok=True)
                clone_private_checkout(root, execution_root, current["product_commit"])
            elif kept and first_command and not execution_root.is_symlink():
                repin_private_checkout(root, execution_root, current["product_commit"])
            if execution_root.is_symlink() or (verb != "down" and git(execution_root, "rev-parse", "HEAD") != current["product_commit"]):
                raise RuntimeError("runtime checkout no longer binds the frozen candidate")
            workdir = (execution_root / str(contract.get("env_workdir", "."))).resolve()
            if workdir != execution_root.resolve() and execution_root.resolve() not in workdir.parents:
                raise RuntimeError("environment workdir escapes its isolated candidate")
            from delivery_git import require_visible_item_index
            difference = ""
            try:
                require_visible_item_index(execution_root)
                before_intact = not git(execution_root, "diff", "--name-only", "HEAD")
                difference = "" if before_intact else checkout_difference(execution_root)
            except RuntimeError:
                before_intact = False
            if not before_intact and verb != "down":
                raise RuntimeError("runtime checkout changed" + (f" ({difference})" if difference else "")
                                   + "; only teardown is allowed before a new verification session")
            bound_session = session["session_id"]
            environment_identity = runtime_environment_identity(root, current)
            # Persist before launching: a failed/interrupted up may still leave
            # real services behind, so only a successful down permits settlement.
            if state.get("pending"):
                state.setdefault("interrupted_commands", []).append(state["pending"])
            # A declared QA verb reads results; it never brings services up, so
            # running one after down leaves no teardown owed. Its pending entry
            # still demands teardown if it is interrupted.
            if not declared:
                state["cleanup_required"] = True
            state["pending"] = {"verb": verb, "value": value, "started_at": time.time(),
                                "candidate_hash": current["candidate_hash"],
                                "environment_identity": environment_identity}
            session["runtime"] = state
            write_session(root, session)
        # Only fixed verbs and declared identifier tokens enter this argument
        # suffix; project command bytes remain the approved contract's bytes.
        if not declared:
            command = command + " " + verb + (" " + value if value else "")
        started = time.monotonic()
        with host_awake(host_guard(root)["keep_awake"]) as awake:
            completed = subprocess.run(command, cwd=workdir, env=command_environment(root, current), shell=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        with locked(root):
            session = read_session(root)
            if session["session_id"] != bound_session:
                raise RuntimeError("runtime reader session changed while command ran")
            if verb != "down":
                require_current(root, session, allow_evidence=True)
            state = session["runtime"]
            output_name = "scratch/runtime-" + state.get("attempt_id", session["session_id"]) + "-" + str(len(state["events"])) + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
            difference = ""
            try:
                require_visible_item_index(execution_root)
                intact = (before_intact and git(execution_root, "rev-parse", "HEAD") == current["product_commit"]
                          and not git(execution_root, "diff", "--name-only", "HEAD"))
                difference = "" if intact else checkout_difference(execution_root)
            except RuntimeError:
                intact = False
            event = {"candidate_intact": intact, "verb": verb, "value": value, "command": command, "exit_code": completed.returncode,
                     "output_file": output_name, "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                     "duration_seconds": time.monotonic() - started, "candidate_hash": current["candidate_hash"],
                     "environment_identity": environment_identity, "completed_at": time.time(),
                     "attempt_id": state.get("attempt_id")}
            if state.get("lifecycle") == LONG_LIVED:
                event["lifecycle"] = LONG_LIVED
            if difference:
                event["checkout_difference"] = difference
            if awake:
                event["host_awake"] = awake
            event["evidence_hash"] = digest(event)
            state["events"].append(event)
            state.pop("pending", None)
            if completed.returncode == 0 and verb in {"up", "down"}:
                state["active"] = verb == "up"
                state["cleanup_required"] = verb != "down"
            session["runtime"] = state
            session["metrics"]["command_seconds"] += event["duration_seconds"]
            write_session(root, session)
            return {**event, "output": completed.stdout.decode("utf-8", errors="replace")}


# The interpreter search paths a lane command never inherits from outside the Item worktree.
LANE_SEARCH_PATH_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "NODE_PATH")


def lane_command_environment(root: Path, directory: Path,
                             base: dict | None = None) -> tuple[dict, dict[str, list[str]]]:
    """Return the inherited environment, or *base*, without search path entries outside *root*.

    An entry is kept when it resolves inside the worktree, a relative entry
    against the command's working directory and a link through its target. A
    variable left with no entry is unset. The second value lists the dropped
    entries of each variable.
    """
    environment = dict(os.environ if base is None else base)
    dropped: dict[str, list[str]] = {}
    for name in LANE_SEARCH_PATH_VARIABLES:
        if name not in environment:
            continue
        kept: list[str] = []
        for entry in environment[name].split(os.pathsep):
            resolved = (directory / entry).resolve()
            if resolved == root or root in resolved.parents:
                kept.append(entry)
            else:
                dropped.setdefault(name, []).append(entry)
        if kept:
            environment[name] = os.pathsep.join(kept)
        else:
            del environment[name]
    return environment, dropped


def lane_run(root: Path, delivery_id: str, story: str, role: str, kind: str,
             verb: str | None = None, value: str | None = None) -> dict:
    """Run the approved full test command or an approved environment verb for one lane.

    Parallel lanes share the Item worktree and its one environment, so the
    coordinator runs these commands here, in that worktree and before the
    candidate freeze, and each takes the Item's environment lock.
    """
    root = root.resolve()
    item = item_record(root, delivery_id, story)
    if item.get("status") != "active" or not item.get("item_plan_hash"):
        raise RuntimeError("lane commands require an active, approved Item")
    if delivery.implementation_schedule(item) != "parallel_lanes_v1":
        raise RuntimeError("lane commands serve only an Item whose approved plan runs parallel_lanes_v1")
    if role not in delivery.lane_roles(item):
        raise RuntimeError(f"{role} is not a lane of {story}")
    guard_write(root)
    kinds = {"test": ("verification", "test_command", "test_workdir"),
             "environment": ("environment", "env_command", "env_workdir")}
    if kind not in kinds:
        raise RuntimeError("unsupported lane command kind")
    contract_kind, command_key, workdir_key = kinds[kind]
    relative = f"workspace/docs/operation/{contract_kind}-contract.md"
    try:
        contract, _, error = delivery.parse_frontmatter(git(root, "show", "HEAD:" + relative))
    except RuntimeError as exc:
        raise RuntimeError(f"the Item worktree's HEAD holds no {relative}") from exc
    if error:
        raise RuntimeError(f"{relative} cannot be parsed")
    command = contract.get(command_key)
    if not isinstance(command, str) or not command.strip() or "{{" in command or "}}" in command:
        raise RuntimeError("approved command is missing or contains unresolved parameters")
    if kind == "test":
        if verb is not None or value is not None:
            raise RuntimeError("the test command takes no verb or value")
        label = "test"
    else:
        if verb not in {"down", "up", "seed", "logs", "url"}:
            raise RuntimeError("unsupported environment verb")
        if verb in {"seed", "url"}:
            allowed = contract.get("scenarios" if verb == "seed" else "service_catalog", [])
            if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value)
                    or value not in allowed):
                raise RuntimeError("environment argument must be an exact approved scenario or service identifier")
        elif value is not None:
            raise RuntimeError("this environment verb does not accept a value")
        label = "environment --verb " + verb + (" " + value if value else "")
        # Only fixed verbs and approved identifiers join the approved command bytes.
        command = command + " " + verb + (" " + value if value else "")
    directory = (root / str(contract.get(workdir_key, "."))).resolve()
    if directory != root and root not in directory.parents:
        raise RuntimeError("lane command workdir must remain inside the Item worktree")
    output = safe_runtime_path(root, session_path(root).parent / "lanes" / f"{role}-{uuid.uuid4().hex}.log",
                               file_only=True)
    environment, dropped = lane_command_environment(root, directory)
    # Only the run that writes a selection file names it; an inherited one would skip suites unseen.
    for name in SELECTION_VARIABLES:
        environment.pop(name, None)
    with environment_lock(root, role, label) as interrupted:
        output.parent.mkdir(exist_ok=True)
        started = time.monotonic()
        completed = subprocess.run(command, cwd=directory, env=environment, shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        duration = time.monotonic() - started
        atomic_file.replace_bytes(output, completed.stdout)
    return {"delivery": delivery_id, "story": story, "holder": role, "kind": kind, "command": command,
            "exit_code": completed.returncode, "output_file": str(output), "duration_seconds": duration,
            "interrupted_holder": interrupted, "dropped_search_paths": dropped}


def pre_handoff_regression(root: Path, delivery_id: str) -> str:
    """The pre_handoff_regression value the Delivery runs under, as its pinned policy sets it."""
    return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, PRE_HANDOFF_SWITCH)


def require_touched_suites(root: Path, delivery_id: str) -> None:
    value = pre_handoff_regression(root, delivery_id)
    if value != TOUCHED_SUITES:
        raise RuntimeError(f"pre-handoff regression runs only at process switch {PRE_HANDOFF_SWITCH}"
                           f" {TOUCHED_SUITES}; {delivery_id} runs it at {value}")


def automation_targets(body: str) -> list[str]:
    """The automation targets of a Test Plan body's automation-required scenarios, in plan order."""
    targets = []
    for _scenario, block in scenario_blocks(body):
        fields, _duplicates = scenario_fields(block)
        target = fields.get("automation_target", "").strip()
        if fields.get("automation", "").strip().casefold() == "required" and target:
            targets.append(target)
    return targets


def scenario_records(body: str) -> dict[str, dict]:
    """Each scenario of a Test Plan body by id, with its automation, target, level and Then."""
    records = {}
    for identifier, block in scenario_blocks(body):
        fields, _duplicates = scenario_fields(block)
        records[identifier] = {key: fields.get(field, "").strip() for key, field in (
            ("automation", "automation"), ("automation_target", "automation_target"), ("level", "level"),
            ("then", "Then"))}
    return records


def plan_automation_targets(docs: Path, relative: str, label: str) -> list[str]:
    """The automation targets of a Test Plan's automation-required scenarios, in plan order."""
    if not relative:
        raise RuntimeError(f"{label} records no Test Plan; its suite cannot be selected")
    path = docs / relative
    if not delivery._is_normalized_claim(relative) or path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} records Test Plan {relative}, which the candidate does not hold;"
                           " its suite cannot be selected")
    return automation_targets(delivery.split_note(path)[1])


def integrated_plan(root: Path, commit: str, plans: list[str], hashes: set[str]) -> dict | None:
    """The newest revision of a story's Test Plans whose backlog digest is one of *hashes*, or None.

    An Item records the digest of the Test Plan revision it integrated as
    test_plan_source_hash. The candidate's own file is the newest revision;
    after it come the revisions the candidate's history wrote, newest first.
    The result names the plan, its digest, the commit that holds it and its
    automation targets.
    """
    docs = delivery.docs_root(root)

    def integrated(relative: str, text: str, holder: str) -> dict | None:
        value = backlog_compile.digest_text(text)
        if value not in hashes:
            return None
        body = backlog_compile.parse_front_matter_text(text)[1]
        return {"test_plan": relative, "source_hash": value, "commit": holder,
                "targets": automation_targets(body), "scenarios": scenario_records(body),
                "scenario_metadata": {identifier: scenario_fields(block)[0]
                                      for identifier, block in scenario_blocks(body)}}

    for relative in plans:
        path = docs / relative
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() and not path.is_symlink() else None
        except (OSError, ValueError):
            text = None
        revision = integrated(relative, text, commit) if text is not None else None
        if revision is not None:
            return revision
    names = {(docs / relative).relative_to(root).as_posix(): relative for relative in plans}
    history = git(root, "--literal-pathspecs", "log", "--full-history", "--topo-order", "--format=%H",
                  commit, "--", *names)
    for holder in history.split():
        for name, relative in names.items():
            shown = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob",
                                    f"{holder}:{name}"], capture_output=True, check=False)
            if shown.returncode:
                continue
            try:
                revision = integrated(relative, shown.stdout.decode("utf-8"), holder)
            except UnicodeDecodeError:
                continue
            if revision is not None:
                return revision
    return None


def integrated_items(root: Path, delivery_id: str):
    """Yield each integrated Item of a merged Delivery or of *delivery_id* as (delivery, current, path, record).

    The current Delivery's integrated Items are no earlier stories, but a story
    one of them integrated again takes the revision it integrated. A record
    that cannot be read and a merge state Git cannot decide refuse, so no
    integrated Item is dropped silently.
    """
    docs = delivery.docs_root(root)
    for directory in delivery.delivery_dirs(docs):
        record = directory / "delivery.md"
        if not record.is_file():
            continue
        try:
            props, _ = delivery.split_note(record)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{record.relative_to(root).as_posix()} cannot be read, so the suites it"
                               f" merged cannot be selected: {exc}") from exc
        identifier = str(props.get("id", ""))
        current_delivery = identifier == delivery_id
        if not current_delivery:
            # An unreadable Delivery Review record is an unknown merge state as well.
            status, unknown = delivery.delivery_state(directory, props)
            if unknown is not None:
                raise RuntimeError(f"whether {identifier} merged decides which earlier suites the candidate"
                                   f" touches: {unknown}")
            if status != "merged":
                continue
        for item_path in sorted(directory.glob("items/*/item.md")):
            try:
                item, _ = delivery.split_note(item_path)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"{item_path.relative_to(root).as_posix()} cannot be read, so its suite"
                                   f" cannot be selected: {exc}") from exc
            if item.get("status") == "integrated":
                yield identifier, current_delivery, item_path, item


def add_integration(integrations: dict[str, dict], item: dict) -> None:
    """Add the Test Plan path and revision digest an integrated Item recorded to its story's integrations."""
    plan = str(item.get("test_plan_path") or "")
    recorded = item.get("test_plan_source_hash")
    if plan and delivery._is_normalized_claim(plan) and isinstance(recorded, str) and recorded:
        integration = integrations.setdefault(str(item.get("story_id", "")), {"plans": [], "hashes": set()})
        if plan not in integration["plans"]:
            integration["plans"].append(plan)
        integration["hashes"].add(recorded)


def converted_scenarios(root: Path, delivery_id: str, current: dict) -> list[dict]:
    """The scenarios the Item converts: automation-required, their target in a file the Item changes, and
    their Test Plan level changed since the newest revision an integrated Item of their story bound.

    A scenario no integration bound yet, or one whose level is unchanged, is
    no conversion. A story whose integrated revision neither the candidate nor
    its history holds refuses, so no conversion is dropped silently.
    """
    docs = delivery.docs_root(root)
    changed = set(current["changed_files"])
    integrations: dict[str, dict] = {}
    for _identifier, _current, _path, item in integrated_items(root, delivery_id):
        add_integration(integrations, item)
    converted = []
    for story, integration in sorted(integrations.items()):
        rewritten = {}
        for relative in integration["plans"]:
            path = docs / relative
            if path.is_symlink() or not path.is_file():
                continue
            for identifier, record in scenario_records(delivery.split_note(path)[1]).items():
                target = record["automation_target"]
                if (record["automation"].casefold() == "required" and target
                        and target.partition("::")[0] in changed):
                    rewritten.setdefault(identifier, (relative, record))
        if not rewritten:
            continue
        revision = integrated_plan(root, current["product_commit"], integration["plans"], integration["hashes"])
        if revision is None:
            raise RuntimeError(f"{story} integrated a Test Plan revision neither the candidate nor its history"
                               f" holds, so whether {', '.join(sorted(rewritten))} changed level cannot be decided")
        for identifier, (relative, record) in sorted(rewritten.items()):
            before = revision["scenarios"].get(identifier)
            if before is None or before["level"] == record["level"]:
                continue
            converted.append({"story": story, "scenario": identifier,
                              "test_plan": (docs / relative).relative_to(root).as_posix(), "then": record["then"],
                              "before": {"level": before["level"] or None,
                                         "automation_target": before["automation_target"]},
                              "after": {"level": record["level"] or None,
                                        "automation_target": record["automation_target"]}})
    return converted


def assertion_kinds() -> dict:
    return json.loads(ASSERTION_KINDS_PATH.read_text(encoding="utf-8"))["kinds"]


def assertion_map_path(root: Path) -> Path:
    return session_path(root).parent / ASSERTION_MAP_FILE


def anchored(root: Path, commit: str, path: str, code: str) -> bool:
    """Whether a line of *path* at *commit*, stripped, is *code*, stripped."""
    shown = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"],
                           capture_output=True, check=False)
    if shown.returncode:
        return False
    try:
        text = shown.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return code.strip() in {line.strip() for line in text.splitlines()}


def assertion_problems(root: Path, commit: str, side: str, identifier: str, entries: object,
                       kinds: dict) -> list[str]:
    """Why the *side* assertions of one map entry are no anchored, typed assertions, or nothing."""
    where = "integration base" if side == "before" else "candidate"
    if not isinstance(entries, list) or not entries:
        return [f"{identifier} names no {side} assertion"]
    problems = []
    for number, entry in enumerate(entries, 1):
        label = f"{identifier} {side} assertion {number}"
        if not isinstance(entry, dict) or set(entry) != set(ASSERTION_FIELDS):
            problems.append(f"{label} holds other fields than {', '.join(ASSERTION_FIELDS)}")
            continue
        path, code, kind, expected = (entry[field] for field in ASSERTION_FIELDS)
        if not isinstance(path, str) or not delivery._is_normalized_claim(path):
            problems.append(f"{label} names no normalized repository path")
        elif not isinstance(code, str) or not code.strip() or "\n" in code or "\r" in code:
            problems.append(f"{label} names no single line of code")
        elif kind not in kinds:
            problems.append(f"{label} names kind {kind!r}, none of {', '.join(kinds)}")
        elif expected is not None and (not isinstance(expected, str) or not expected.strip()
                                       or expected not in code):
            problems.append(f"{label} names an expected value its code does not hold; name null for none")
        elif not anchored(root, commit, path, code):
            problems.append(f"{label} is no line of {path} in the {where}")
    return problems


def assertion_map_check(root: Path, current: dict) -> dict:
    """Check the Item's assertion map against the scenarios it converts.

    Every converted scenario needs one entry whose then is its Then, with at
    least one before assertion that is a line of the integration base and one
    after assertion that is a line of the candidate; anything else is a
    problem freeze refuses. A complete entry is flagged when every after
    assertion is of a weak kind while a before one was not, or when an
    expected value of a before assertion is no after assertion's.
    """
    converted = converted_scenarios(root, current["delivery"], current)
    path = safe_runtime_path(root, assertion_map_path(root))
    result = {"converted_scenarios": converted, "map_hash": None, "problems": [], "flagged": []}
    if not path.exists():
        if converted:
            result["problems"].append("no assertion map names the assertions of the converted scenarios "
                                      + ", ".join(entry["scenario"] for entry in converted))
        return result
    raw = safe_runtime_path(root, path, file_only=True).read_bytes()
    result["map_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scenarios"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["scenarios"], dict)):
        result["problems"].append("the assertion map holds no schema_version 1 and scenarios")
        return result
    kinds = assertion_kinds()
    scenarios = {entry["scenario"]: entry for entry in converted}
    for identifier in sorted(set(value["scenarios"]) - set(scenarios)):
        result["problems"].append(f"{identifier} is no scenario the Item converts")
    for identifier, scenario in sorted(scenarios.items()):
        entry = value["scenarios"].get(identifier)
        if not isinstance(entry, dict) or set(entry) != {"then", "before", "after"}:
            result["problems"].append(f"{identifier} has no map entry of then, before and after")
            continue
        problems = [] if entry["then"] == scenario["then"] else [
            f"{identifier} maps another then than its Test Plan's Then: {scenario['then']}"]
        problems += assertion_problems(root, current["integration_base_commit"], "before", identifier,
                                       entry["before"], kinds)
        problems += assertion_problems(root, current["product_commit"], "after", identifier, entry["after"], kinds)
        if problems:
            result["problems"].extend(problems)
            continue
        flags = []
        before, after = entry["before"], entry["after"]
        if (any(not kinds[item["kind"]]["weak"] for item in before)
                and all(kinds[item["kind"]]["weak"] for item in after)):
            flags.append("weakened_kind")
        lost = sorted({item["expected"] for item in before if item["expected"] is not None}
                      - {item["expected"] for item in after if item["expected"] is not None})
        if lost:
            flags.append("expected_value_lost")
        if flags:
            result["flagged"].append({"story": scenario["story"], "scenario": identifier, "then": scenario["then"],
                                      "flags": flags, "lost_expected_values": lost, "before": before,
                                      "after": after})
    return result


def assertion_map_status(root: Path, delivery_id: str, story: str) -> dict:
    """The converted scenarios of the committed candidate, its assertion map's check and a template to fill."""
    root = root.resolve()
    value = delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, LEVEL_CHANGE_SWITCH)
    if value != ASSERTION_MAP:
        raise RuntimeError(f"the assertion map is checked only at process switch {LEVEL_CHANGE_SWITCH}"
                           f" {ASSERTION_MAP}; {delivery_id} runs it at {value}")
    check = candidate(root, delivery_id, story, allow_evidence=True)["assertion_map"]
    return {**check, "assertion_map_file": str(assertion_map_path(root)),
            "kinds": {kind: spec["summary"] for kind, spec in assertion_kinds().items()},
            "template": {"schema_version": 1, "scenarios": {
                entry["scenario"]: {"then": entry["then"], "before": [], "after": []}
                for entry in check["converted_scenarios"]}}}



def rebind_unit_selection(root: Path, delivery_id: str) -> dict:
    commit = git(root, "rev-parse", "HEAD")
    integrations = {}
    for _identifier, _current, _path, item in integrated_items(root, delivery_id):
        add_integration(integrations, item)
    targets = set()
    revisions = []
    for story, value in sorted(integrations.items()):
        closed = integrated_plan(root, commit, value["plans"], value["hashes"])
        if closed is None:
            raise RuntimeError("rebound unit selection requires the closed plan")
        _current, receipt = current_approved_regression_plan(root, commit, story, closed)
        for row in receipt["scenario_delta"].values():
            before, after = row.get("before") or {}, row.get("after") or {}
            target = after.get("automation_target")
            if (after.get("level") == "unit" and after.get("automation", "").lower() == "required"
                    and target and target != before.get("automation_target")):
                if not literal_test_id(target):
                    raise RuntimeError("rebound unit target must be a literal test id")
                targets.add(target)
        revisions.append({"story": story, "current_source_hash": receipt["current_source_hash"]})
    contract = verification_contract(root)
    kind = "diagnostic_test" if contract.get("diagnostic_test_command") else "test"
    return {"delivery": delivery_id, "candidate_tree": git(root, "rev-parse", "HEAD^{tree}"),
            "selected_test_ids": sorted(targets), "revisions": revisions, "kind": kind,
            "command": contract[kind + "_command"], "workdir": contract.get(kind + "_workdir", ".")}


def rebind_unit_record(root: Path, delivery_id: str) -> Path:
    if not re.fullmatch(r"DLV-[0-9]+", delivery_id):
        raise RuntimeError("invalid Delivery id")
    return safe_runtime_path(root, session_path(root).parent / (delivery_id + "-rebound-units.json"), file_only=True)


def rebind_unit_run(root: Path, delivery_id: str) -> dict:
    root = root.resolve()
    guard_write(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "rebind-unit-run"), command_lock(root, PRE_HANDOFF_HOLDER, "rebind-unit-run"):
        selection = rebind_unit_selection(root, delivery_id)
        if not selection["selected_test_ids"]:
            return {"exit_code": 0, "candidate_intact": True, "needed": False}
        path = rebind_unit_record(root, delivery_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # This Delivery-wide run has no single Item identity.
        environment = command_environment(root, {"delivery": delivery_id, "story": ""},
                                          diagnostic=selection["kind"] == "diagnostic_test")
        data = {"schema_version": 1, "candidate_hash": digest(selection), "failed_test_ids": [],
                "affected_test_ids": selection["selected_test_ids"], "selected_test_ids": selection["selected_test_ids"]}
        selector = raw_output_path(root, "scratch/rebound-unit-selection.json")
        selector.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.replace_text(selector, json.dumps(data, sort_keys=True))
        generation = source_file_generation(selector)
        if selection["kind"] == "diagnostic_test":
            environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(selector)
        completed, intact, _difference, _dropped, _ran = private_checkout_run(root, selector.parent,
            git(root, "rev-parse", "HEAD"), selection["workdir"], selection["command"], environment)
        intact = (intact and rebind_unit_selection(root, delivery_id) == selection
                  and source_file_generation(selector) == generation)
        output = raw_output_path(root, "scratch/rebound-units-" + uuid.uuid4().hex + ".log")
        atomic_file.replace_bytes(output, completed.stdout)
        record = {"selection": selection, "exit_code": completed.returncode, "candidate_intact": intact,
                  "output_file": output.relative_to(session_path(root).parent).as_posix(),
                  "output_sha256": hashlib.sha256(completed.stdout).hexdigest(), "completed_at": time.time()}
        record["evidence_hash"] = digest(record)
        atomic_file.replace_text(path, json.dumps(record, sort_keys=True))
        return record


def rebind_unit_refusals(root: Path, delivery_id: str) -> list[str]:
    try:
        import process_policy
        values, _pin = process_policy.effective_values(delivery.docs_root(root))
        if values.get("regression_plan_revision", {}).get("value", "integrated") != "current_approved":
            return []
        selection = rebind_unit_selection(root, delivery_id)
        if not selection["selected_test_ids"]:
            return []
        record = json.loads(rebind_unit_record(root, delivery_id).read_text(encoding="utf-8"))
        output = raw_output_path(root, record["output_file"])
        if (record["selection"] != selection or record["exit_code"] != 0 or record["candidate_intact"] is not True
                or record["evidence_hash"] != digest({k: v for k, v in record.items() if k != "evidence_hash"})
                or hashlib.sha256(output.read_bytes()).hexdigest() != record["output_sha256"] or not fresh_record(record)):
            raise ValueError("no current green evidence")
        return []
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        return ["Newly rebound unit targets need a passing rebind-unit-run before plan approval: " + str(exc)]


def current_approved_regression_plan(root: Path, commit: str, story: str, closed: dict) -> tuple[dict, dict]:
    """Read one story from the complete approved backlog at the candidate commit."""
    docs = delivery.docs_root(root)
    sources = backlog_compile.approved_history_sources(root, docs, commit)
    if sources is None:
        raise RuntimeError("current regression plans require a complete committed backlog approval")
    matches = []
    for path, raw in sources.items():
        if path.name != "test-plan.md":
            continue
        text = raw.decode("utf-8")
        props, body = backlog_compile.parse_front_matter_text(text)
        if props.get("story_id") == story:
            matches.append((path, text, body))
    if len(matches) != 1:
        raise RuntimeError(f"{story} requires exactly one current approved Test Plan; found {len(matches)}")
    path, text, body = matches[0]
    before = closed.get("scenario_metadata", closed["scenarios"])
    after = {identifier: scenario_fields(block)[0] for identifier, block in scenario_blocks(body)}
    delta = {identifier: {"before": before.get(identifier), "after": after.get(identifier)}
             for identifier in sorted(set(before) | set(after))
             if before.get(identifier) != after.get(identifier)}
    metadata = after
    revision = {"test_plan": path.relative_to(docs).as_posix(),
                "source_hash": backlog_compile.digest_text(text), "commit": commit,
                "targets": automation_targets(body), "scenarios": scenario_records(body)}
    receipt = {"closed_commit": closed["commit"], "closed_source_hash": closed["source_hash"],
               "current_commit": commit, "current_source_hash": revision["source_hash"],
               "scenario_delta": delta, "scenario_metadata": metadata}
    return revision, receipt


def derive_regression(root: Path, delivery_id: str, story: str, current: dict) -> dict:
    """Derive the pre-handoff regression selection of an Item's exact candidate.

    An earlier story is one whose Item is integrated in a Delivery that the
    candidate holds as merged and whose path claims hold or lie under a path
    the candidate changes against its integration base. The selection is the
    automation targets of the automation-required scenarios in those stories'
    Test Plans and in the Item's own, as affected_test_ids of the approved
    diagnostic adapter; without that adapter, or with no target, the full
    approved test command runs.

    An earlier story's targets come from the newest Test Plan revision that an
    integrated Item the candidate holds, of a merged Delivery or of the current
    one, records as its test_plan_source_hash: the candidate's own Test Plan
    when it is that revision, else that revision from the candidate's history.
    A backlog revision no integration bound yet changes no selection. A record
    that cannot be read, a merge state Git cannot decide and an integrated
    revision neither the candidate nor its history holds refuse, so no suite
    is dropped silently.
    """
    docs = delivery.docs_root(root)
    changed = current["changed_files"]
    touched_items = []
    integrations: dict[str, dict] = {}
    for identifier, current_delivery, item_path, item in integrated_items(root, delivery_id):
        earlier_story = str(item.get("story_id", ""))
        plan = str(item.get("test_plan_path") or "")
        recorded = item.get("test_plan_source_hash")
        add_integration(integrations, item)
        claims = item.get("path_claims")
        if current_delivery or not isinstance(claims, list):
            continue
        touched = sorted({claim for claim in claims if isinstance(claim, str)
                          and any(delivery._claims_overlap(claim, path) for path in changed)})
        if not touched:
            continue
        label = f"{earlier_story} of {identifier}"
        if not plan:
            raise RuntimeError(f"{label} records no Test Plan; its suite cannot be selected")
        if not delivery._is_normalized_claim(plan):
            raise RuntimeError(f"{label} records Test Plan {plan}, which is no normalized docs path; its suite"
                               " cannot be selected")
        if not isinstance(recorded, str) or not recorded:
            raise RuntimeError(f"{label} records no test_plan_source_hash, so the Test Plan revision it"
                               " integrated cannot be selected")
        touched_items.append({"delivery": identifier, "story": earlier_story, "label": label,
                              "item": item_path.relative_to(root).as_posix(), "plan": plan,
                              "recorded": recorded, "touched": touched})
    revisions: dict[str, dict | None] = {}
    earlier = []
    for entry in touched_items:
        if entry["story"] not in revisions:
            integration = integrations[entry["story"]]
            revisions[entry["story"]] = integrated_plan(root, current["product_commit"], integration["plans"],
                                                        integration["hashes"])
        revision = revisions[entry["story"]]
        if revision is None:
            raise RuntimeError(f"{entry['label']} integrated Test Plan {entry['plan']} at {entry['recorded']},"
                               " a revision neither the candidate nor its history holds; its suite cannot be"
                               " selected")
        plan_revision = None
        if delivery.delivery_switch_value(docs, delivery_id, "regression_plan_revision") == "current_approved":
            revision, plan_revision = current_approved_regression_plan(
                root, current["product_commit"], entry["story"], revision)
        earlier.append({"delivery": entry["delivery"], "story": entry["story"], "item": entry["item"],
                        "test_plan": (docs / revision["test_plan"]).relative_to(root).as_posix(),
                        "test_plan_source_hash": revision["source_hash"], "test_plan_commit": revision["commit"],
                        "touched_claims": entry["touched"],
                        "touched_paths": sorted({path for path in changed for claim in entry["touched"]
                                                 if delivery._claims_overlap(claim, path)}),
                        "automation_targets": sorted(set(revision["targets"])),
                        **({"plan_revision": plan_revision} if plan_revision is not None else {})})
    earlier.sort(key=lambda entry: (entry["delivery"], entry["story"]))
    plan = str(item_record(root, delivery_id, story).get("test_plan_path") or "")
    own = sorted(set(plan_automation_targets(docs, plan, f"{story} of {delivery_id}")))
    for label, targets in [*((entry["story"], entry["automation_targets"]) for entry in earlier), (story, own)]:
        for target in targets:
            if not literal_test_id(target):
                raise RuntimeError(f"automation target {target} of {label} is no literal test id: it is padded,"
                                   " starts with '-' or holds a control character")
    selected = sorted(set(own).union(*(entry["automation_targets"] for entry in earlier)))
    contract, _ = delivery.split_note(docs / "operation/verification-contract.md")

    def approved(kind: str) -> str | None:
        command = contract.get(kind + "_command")
        resolved = isinstance(command, str) and command.strip() and "{{" not in command and "}}" not in command
        return command if resolved else None

    accept_full = own_target_reuse(root, delivery_id) == ACCEPT_RECORD
    kind = "diagnostic_test" if selected and approved("diagnostic_test") and not accept_full else "test"
    command = approved(kind)
    if command is None:
        raise RuntimeError("approved verification command is missing or contains unresolved parameters")
    live_plan = None
    if contract.get("live_environment_plan") == "effect_ordered_v1":
        rows = []
        for path in sorted(docs.rglob("test-plan.md")):
            for identifier, block in scenario_blocks(delivery.split_note(path)[1]):
                fields = scenario_fields(block)[0]
                if fields.get("automation_target", "").strip() in selected:
                    rows.append({"id": identifier, "fields": fields})
        live_plan = backlog_compile.live_environment_plan(rows)
    return {**({"live_environment_plan": live_plan} if live_plan is not None else {}),
            "delivery": delivery_id, "story": story, "candidate_hash": current["candidate_hash"],
            "product_commit": current["product_commit"],
            "candidate_tree": git(root, "rev-parse", current["product_commit"] + "^{tree}"),
            "earlier_stories": earlier, "own_targets": own, "affected_test_ids": selected,
            "kind": kind, "command": command, "workdir": str(contract.get(kind + "_workdir", "."))}


def write_pre_handoff_selection(root: Path, derived: dict) -> Path | None:
    """Write the derived selection in the diagnostic adapter's input schema, which QA's run reads too.

    Unchanged bytes are left in place, so deriving the selection again never
    invalidates a QA diagnostic that reads the same file.
    """
    if not derived["affected_test_ids"]:
        return None
    path = raw_output_path(root, PRE_HANDOFF_SELECTION)
    text = json.dumps({**({"live_environment_plan": derived["live_environment_plan"]}
                         if "live_environment_plan" in derived else {}),
                       "schema_version": 1, "candidate_hash": derived["candidate_hash"], "failed_test_ids": [],
                       "affected_test_ids": derived["affected_test_ids"]}, indent=2, sort_keys=True) + "\n"
    if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.replace_text(path, text)
    return path


def regression_selection(root: Path, delivery_id: str, story: str) -> dict:
    """Derive and write the pre-handoff regression selection of the Item's committed candidate.

    It holds the Item's environment lock meanwhile, which a pre-handoff run
    and QA's diagnostic hold while their command runs, so it never rewrites the
    selection a running command reads.
    """
    root = root.resolve()
    require_touched_suites(root, delivery_id)
    with environment_lock(root, PRE_HANDOFF_HOLDER, "regression-selection") as interrupted:
        derived = derive_regression(root, delivery_id, story, candidate(root, delivery_id, story, allow_evidence=True))
        path = write_pre_handoff_selection(root, derived)
    return {**derived, "selection_file": str(path) if path else None, "interrupted_holder": interrupted}


def pre_handoff_record_path(root: Path) -> Path:
    return safe_runtime_path(root, session_path(root).parent / "pre-handoff.json", file_only=True)


def pre_handoff_runs(root: Path) -> list[dict]:
    """The pre-handoff regression runs recorded in this Item worktree's runtime, oldest first."""
    path = pre_handoff_record_path(root)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        runs = value["runs"]
        if (value.get("schema_version") != 1 or not isinstance(runs, list)
                or any(not isinstance(run, dict) or run.get("evidence_hash") != digest(
                    {key: item for key, item in run.items() if key != "evidence_hash"}) for run in runs)):
            raise ValueError("invalid schema or evidence hash")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"pre-handoff run record is invalid: {exc}; move {path} aside and run"
                           " regression-run again") from exc
    return runs


def regression_run(root: Path, delivery_id: str, story: str) -> dict:
    """Run the pre-handoff regression selection on the Item's exact committed candidate.

    The coordinator runs it before the freeze, under the Item's environment
    lock for the whole run, in a private clone of the candidate commit without
    interpreter search paths that resolve outside that clone: the approved
    diagnostic adapter with the selection in AGENTROF_DIAGNOSTIC_TESTS, or the
    full approved test command without one. Only deriving the selection and
    cloning the candidate read the Item worktree, so the verification command
    lock, which guarded writes to it wait for, is held for those alone. The run
    and its result are recorded against the tree it cloned, so a commit made
    while the command runs is a new candidate that this run does not bind.
    """
    root = root.resolve()
    require_touched_suites(root, delivery_id)
    guard_write(root)
    pre_handoff_runs(root)
    with environment_lock(root, PRE_HANDOFF_HOLDER, PRE_HANDOFF_RUN) as interrupted, \
            contextlib.ExitStack() as reading:
        reading.enter_context(command_lock(root, PRE_HANDOFF_HOLDER, PRE_HANDOFF_RUN))
        current = candidate(root, delivery_id, story, allow_evidence=True)
        derived = derive_regression(root, delivery_id, story, current)
        # Read while the run still holds the worktree, which may change once its command runs,
        # and checked now, so a malformed contract costs no run of the selection.
        contract = verification_contract(root)
        contract_variables(contract)
        shared = write_pre_handoff_selection(root, derived)
        environment = command_environment(root, current, diagnostic=derived["kind"] == "diagnostic_test")
        selections: dict[Path, tuple[bytes, list[int]]] = {}
        if derived["kind"] == "diagnostic_test":
            selector = safe_runtime_path(root, session_path(root).parent / "pre-handoff-tests.json", file_only=True)
            atomic_file.replace_text(selector, json.dumps(
                {"schema_version": 1, "candidate_hash": current["candidate_hash"], "failed_test_ids": [],
                 "affected_test_ids": derived["affected_test_ids"],
                 "selected_test_ids": derived["affected_test_ids"],
                 **({"regression_plans": [{"story": entry["story"], **entry["plan_revision"]}
                                           for entry in derived["earlier_stories"] if "plan_revision" in entry]}
                    if any("plan_revision" in entry for entry in derived["earlier_stories"]) else {})},
                indent=2, sort_keys=True) + "\n")
            environment["AGENTROF_DIAGNOSTIC_TESTS"] = str(selector)
            # As in QA's diagnostic run, neither the selection the command
            # receives nor the one QA's diagnostic reads may change while it runs.
            selections = {path: (path.read_bytes(), source_file_generation(path)) for path in (selector, shared)}
        scratch = safe_runtime_path(root, Path(environment["AGENTROF_VERIFICATION_SCRATCH"]))
        scratch.mkdir(parents=True, exist_ok=True)
        groups = group_report_declaration(root, delivery_id, contract)
        if groups is not None:
            clear_group_report(scratch, groups)
        started = time.monotonic()
        completed, intact, difference, dropped, ran = private_checkout_run(
            root, scratch, current["product_commit"], derived["workdir"], derived["command"], environment,
            isolate_search_paths=True, cloned=reading.close)
        duration = time.monotonic() - started
        group_report = read_group_report(scratch, groups) if groups is not None else None
        if group_report is not None and group_report["missing_test_groups"]:
            intact = False
        selection_intact = None
        if selections:
            try:
                selection_intact = all(
                    safe_runtime_path(root, path, file_only=True).read_bytes() == data
                    and source_file_generation(path) == generation for path, (data, generation) in selections.items())
            except (RuntimeError, ValueError, OSError):
                selection_intact = False
            intact = intact and selection_intact
        with locked(root):
            runs = pre_handoff_runs(root)
            output_name = "scratch/pre-handoff-" + uuid.uuid4().hex + ".log"
            atomic_file.replace_bytes(raw_output_path(root, output_name), completed.stdout)
            record = {**{key: derived[key] for key in ("delivery", "story", "product_commit", *PRE_HANDOFF_IDENTITY)},
                      "earlier_stories": [{"delivery": entry["delivery"], "story": entry["story"],
                                           **({"plan_revision": entry["plan_revision"]}
                                              if "plan_revision" in entry else {})}
                                          for entry in derived["earlier_stories"]],
                      "exit_code": completed.returncode, "candidate_intact": intact, "output_file": output_name,
                      "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                      "duration_seconds": duration, "completed_at": time.time(), "dropped_search_paths": dropped,
                      **environment_identity(root, ran, contract)}
            if difference:
                record["checkout_difference"] = difference
            if selection_intact is not None:
                record["selection_intact"] = selection_intact
            if group_report is not None:
                record.update(group_report, test_group_report=groups)
            record["evidence_hash"] = digest(record)
            atomic_file.replace_text(pre_handoff_record_path(root), json.dumps(
                {"schema_version": 1, "runs": [*runs, record]}, indent=2, sort_keys=True) + "\n")
    return {**record, "output_path": str(raw_output_path(root, output_name)), "interrupted_holder": interrupted}


def accepted_pre_handoff(root: Path, delivery_id: str, story: str, current: dict) -> dict:
    """The passing pre-handoff run that lets the exact candidate freeze, or the refusal that names why none does.

    The latest run on the candidate's tree with the selection and command
    derived for it decides: a new commit, a changed selection or a changed
    approved command needs a new run. The session keeps it with the bindings
    and the declared environment QA's final test run compares to reuse it.
    """
    derived = derive_regression(root, delivery_id, story, current)
    identity = {key: derived[key] for key in PRE_HANDOFF_IDENTITY}
    runs = [run for run in pre_handoff_runs(root) if (run.get("delivery"), run.get("story")) == (delivery_id, story)
            and {key: run.get(key) for key in PRE_HANDOFF_IDENTITY} == identity]
    tree = derived["candidate_tree"]
    if not runs:
        raise RuntimeError(f"DELIVERY_PRE_HANDOFF_MISSING: no pre-handoff regression run binds candidate tree {tree}"
                           f" with the selection and command derived for it; run regression-run --delivery"
                           f" {delivery_id} --story {story}, repair what it finds and commit, then freeze")
    latest = runs[-1]
    if latest.get("exit_code") != 0 or latest.get("candidate_intact") is not True:
        changed = ("" if latest.get("candidate_intact") is True
                   else " and changed the selection it ran" if latest.get("selection_intact") is False
                   else " and its group report lacks " + ", ".join(latest["missing_test_groups"])
                   if latest.get("missing_test_groups")
                   else f" and changed its checkout ({latest['checkout_difference']})" if latest.get("checkout_difference")
                   else " and changed its checkout")
        raise RuntimeError(f"DELIVERY_PRE_HANDOFF_MISSING: the latest pre-handoff regression run on candidate tree"
                           f" {tree} exited {latest.get('exit_code')}{changed}; repair what it found and commit,"
                           " then run regression-run on the new candidate, or run it again once a cause outside"
                           " the candidate is fixed")
    receipt = {key: latest[key] for key in ("evidence_hash", *PRE_HANDOFF_IDENTITY, "earlier_stories", "exit_code",
                                            "candidate_intact", "duration_seconds", "completed_at")}
    # A run recorded before runs named their declared environment has none, so QA reuses no such run.
    receipt.update({key: latest[key] for key in ENVIRONMENT_FIELDS if key in latest})
    receipt.update({key: latest[key] for key in ("test_group_report", "test_groups") if key in latest})
    return receipt


def pre_handoff_history(root: Path, session: dict | None, delivery_id: str, story: str) -> list[dict]:
    """Every pre-handoff regression run of the Item so far, oldest first.

    It is the history the session carries, then each run of the Item's runtime
    record that the history does not hold yet, by evidence hash. Each freeze
    carries it into the next session and approve-item-evidence records it, so
    a freeze of the same tree never counts a run twice, and a runtime record
    moved aside loses no run a freeze already carried.
    """
    history = list(session.get("pre_handoff_history", [])) if session else []
    held = {run.get("evidence_hash") for run in history}
    for run in pre_handoff_runs(root):
        if (run.get("delivery"), run.get("story")) == (delivery_id, story) and run["evidence_hash"] not in held:
            history.append({key: run.get(key) for key in PRE_HANDOFF_HISTORY_FIELDS})
            held.add(run["evidence_hash"])
    return history


def runtime_event_environment(root: Path, session: dict, event: dict, contract: dict) -> dict:
    path = raw_output_path(root, event["output_file"])
    if (event.get("exit_code") != 0 or event.get("candidate_intact") is not True
            or event.get("candidate_hash") != session["candidate"]["candidate_hash"]
            or event.get("attempt_id") != session.get("runtime", {}).get("attempt_id")
            or not fresh_record(event)
            or event.get("evidence_hash") != digest({key: value for key, value in event.items() if key != "evidence_hash"})
            or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != event["output_sha256"]):
        raise RuntimeError("runtime command evidence is failed, stale or missing")
    identity = event.get("environment_identity")
    problem = environment_problem(identity, contract)
    if problem:
        raise RuntimeError(f"runtime command evidence {problem}")
    return {key: identity[key] for key in ENVIRONMENT_FIELDS}


def require_lifecycle_ready(root: Path, session: dict) -> None:
    current = session["candidate"]
    if not current["runtime_required"] or not qa_preflight(root, current["delivery"]):
        return
    state = session.get("runtime", {})
    events = state.get("events", [])
    verbs = [event.get("verb") for event in events if event.get("verb") in ENVIRONMENT_VERBS]
    up = max((i for i, verb in enumerate(verbs) if verb == "up"), default=-1)
    long_lived = (state.get("lifecycle") == LONG_LIVED if events
                  else runtime_lifecycle(root, session)["mode"] == LONG_LIVED)
    if (state.get("active") is not True or state.get("pending") or state.get("interrupted_commands")
            or up < (0 if long_lived else 1) or (not long_lived and verbs[up - 1] != "down")
            or "seed" not in verbs[up + 1:]
            or "logs" not in verbs[up + 1:] or verbs.index("logs", up + 1) < verbs.index("seed", up + 1)):
        raise RuntimeError("live groups require successful lifecycle up/seed/logs on the long-lived instance first"
                           if long_lived else "live groups require successful lifecycle down/up/seed/logs first")
    contract = verification_contract(root)
    expected = runtime_environment_identity(root, current)
    if any(runtime_event_environment(root, session, event, contract) != expected for event in events):
        raise RuntimeError("lifecycle preflight ran in another environment")


def require_runtime_evidence(root: Path, session: dict, evidence: dict, environment: dict | None = None) -> None:
    """Check the fresh runtime events as recorded: every event of the attempt shares one complete
    environment identity, the one *environment* names when QA's final command evidence gives it, and
    none is compared with the environment of the process that checks it."""
    state = session.get("runtime", {})
    events = state.get("events", [])
    attempt_id = state.get("attempt_id")
    verbs = [event["verb"] for event in events if event.get("exit_code") == 0 and event["verb"] in ENVIRONMENT_VERBS]
    required = runtime_lifecycle(root, session)
    recorded = {event.get("lifecycle", FRESH) for event in events} | {state.get("lifecycle", FRESH)}
    if recorded != {required["mode"]}:
        raise RuntimeError(f"runtime evidence follows the {'/'.join(sorted(recorded))} lifecycle but this candidate"
                           f" requires {required['mode']} ({required['reason']})")
    if required["mode"] == LONG_LIVED:
        up = max((i for i, verb in enumerate(verbs) if verb == "up"), default=-1)
        if (not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
                or runtime_needs_cleanup(session) or state.get("interrupted_commands") or up < 0
                or "seed" not in verbs[up + 1:] or "logs" not in verbs[verbs.index("seed", up + 1):]
                or evidence.get("event_hashes") != [event["evidence_hash"] for event in events]):
            raise RuntimeError("long-lived runtime evidence requires recorded up/seed/logs on the running instance"
                               " and exact event hashes")
    elif fresh_runtime_problem(session, evidence):
        raise RuntimeError("fresh runtime evidence requires recorded down/up/seed/logs/down and exact event hashes")
    contract = verification_contract(root)
    shared = None
    for event in events:
        identity = runtime_event_environment(root, session, event, contract)
        if shared is None:
            shared = identity
        elif identity != shared:
            raise RuntimeError("runtime command evidence ran in more than one environment")
    if environment is not None and shared is not None and shared != environment:
        raise RuntimeError("runtime command evidence ran in another environment than full_test_suite evidence")
    if qa_preflight(root, session["candidate"]["delivery"]):
        live = [record["completed_at"] for record in session.get("raw_evidence", {}).values()
                if record.get("identity", {}).get("kind") == "live_test"]
        if live and not any(event["verb"] == "logs" and event["completed_at"] >= max(live) for event in events):
            raise RuntimeError("runtime evidence requires a final log check after the live groups")


def inspect_instruction(root: Path, relative: str) -> dict:
    current = require_current(root, read_session(root), allow_evidence=True)
    if not delivery._is_normalized_claim(relative):
        raise RuntimeError("instruction path must be normalized and package-relative")
    path = PurePosixPath(relative)
    if relative != "constitution.md" and path.parts[0] not in {"agents", "skill-content", "flows", "scripts", "templates"}:
        raise RuntimeError("instruction path is outside the bound package instruction roots")
    base = Path(__file__).resolve().parents[1]
    file = base / relative
    if any(parent.is_symlink() for parent in (file, *file.parents) if parent != base and base in parent.parents):
        raise RuntimeError("instruction reads cannot follow symlinks")
    if not file.is_file():
        raise RuntimeError("instruction file is missing")
    return {"candidate_hash": current["candidate_hash"], "instruction_identity": current["instruction_identity"],
            "path": relative, "content": file.read_text(encoding="utf-8")}


def inspect_candidate(root: Path, relative: str, *, base: bool = False) -> dict:
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if not delivery._is_normalized_claim(relative):
        raise RuntimeError("inspect path must be normalized and repository-relative")
    commit = current["integration_base_commit"] if base else current["product_commit"]
    completed = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "show", f"{commit}:{relative}"],
                               capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError("path is not present in the selected candidate tree")
    import base64
    try:
        content, encoding = completed.stdout.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(completed.stdout).decode("ascii"), "base64"
    return {"candidate_hash": current["candidate_hash"], "commit": commit, "path": relative,
            "encoding": encoding, "content": content}


def inspect_context(root: Path, payload: dict, *, reason: str | None = None,
                    refs: list[str] | None = None) -> dict:
    """Batch source units from the frozen Git candidate, never live text."""
    import context_catalog
    import context_history
    import project_context
    current = require_current(root, read_session(root), allow_evidence=True)
    if payload.get("candidate_hash") != current["candidate_hash"]:
        raise RuntimeError("context manifest belongs to another verification candidate")
    if payload.get("product_commit", current["product_commit"]) != current["product_commit"]:
        raise RuntimeError("context manifest names another candidate commit")
    plan = payload.get("project_reading")
    if "project_reading_file" in payload:
        reference = payload["project_reading_file"]
        try:
            checksum = reference.get("sha256", "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
                raise ValueError("invalid reading attachment digest")
            path = safe_runtime_path(root, session_path(root).parent / "manifest" /
                                     ("project-reading-" + checksum[7:] + ".json"), file_only=True)
            plan = json.loads(path.read_text(encoding="utf-8"))
            if reference.get("path") != str(path) or digest(plan) != reference.get("sha256"):
                raise ValueError("context attachment differs")
        except (OSError, ValueError, TypeError, AttributeError):
            raise RuntimeError("project reading attachment changed or is unavailable") from None
    if not isinstance(plan, dict) or "request" not in plan:
        raise RuntimeError("resolve context or use frozen inspect reads before continuing")
    def addressed(index):
        project_context.validate_plan(root, index, plan)
        if reason is not None:
            return project_context.expand_context(root, index, plan, reason=reason, refs=refs, persist_state=False)
        if refs:
            raise RuntimeError("expanding frozen context requires a reason")
        return {row["unit_id"]: dict(index["catalog"]["units"].get(row["unit_id"], row)) for row in plan["must_read"]}
    addresses = project_context.with_index(root, addressed, no_cache=True)
    if reason is not None:
        return {"candidate_hash": current["candidate_hash"], "product_commit": current["product_commit"],
                "project_reading": addresses}
    request = project_context.request_data(root, plan)
    manual = []
    for obligation in plan.get("manual_reads", []):
        relative = obligation["path"]
        if not delivery._is_normalized_claim(relative):
            raise RuntimeError("manual frozen context path must be normalized and repository-relative")
        raw = context_history.git_source(root, relative, current["product_commit"])
        if context_catalog.digest(raw) != obligation["source_hash"] or len(raw) != obligation["source_bytes"]:
            raise RuntimeError("manual context source differs from the frozen candidate")
        manual.append({**obligation, "candidate_hash": current["candidate_hash"],
            "product_commit": current["product_commit"], "disposition": "requires_frozen_inspect",
            "next_action": "When the task needs this source, read it from the bound candidate with delivery_verification.py inspect --path <path>."})
    units = {}
    for row in plan["must_read"]:
        unit = addresses[row["unit_id"]]
        unit.setdefault("git_revision", current["product_commit"])
        units[unit["unit_id"]] = unit
    result = context_catalog.read_units(root / "workspace/docs", {"units": units},
        list(units), request["budget"]["max_source_bytes"])
    result.update(candidate_hash=current["candidate_hash"], plan_status=plan["status"],
                  coverage=plan["coverage"])
    if "continuation" in plan:
        result["continuation"] = plan["continuation"]
    if manual:
        result["manual_reads"] = manual
        result["status"] = "needs_manual_read"
    if not units and not manual and plan["status"] != "ready":
        result["status"] = plan["status"]
    return result


def candidate_diff(root: Path, paths: list[str]) -> dict:
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if any(not delivery._is_normalized_claim(path) for path in paths):
        raise RuntimeError("diff paths must be normalized and repository-relative")
    return {"candidate_hash": current["candidate_hash"], "diff": git(root, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv",
            current["integration_base_commit"], current["product_commit"], "--", *paths)}


def validate_evidence(item: dict, review: dict, verification: dict) -> None:
    """Check durable final-mode bindings after the product becomes evidence's parent."""
    if delivery.verification_schedule(item) == "sequential_v1":
        return
    candidate_hash = review.get("verification_candidate_hash")
    if (not isinstance(candidate_hash, str) or not candidate_hash.startswith("sha256:")
            or verification.get("verification_candidate_hash") != candidate_hash):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: independent reports must bind the same verification candidate")
    for role, record in (("code_reviewer", review), ("qa_engineer", verification)):
        if (record.get("verification_mode") not in policy()["final_modes"][role]
                or not str(record.get("verification_result_hash", "")).startswith("sha256:")):
            raise RuntimeError(f"DELIVERY_ITEM_NOT_READY: final {role} verification result is missing")


CLOSURE_SWITCH, CLOSURE_VALUE = "review_scope", "impact_closure"
# The Item notes its closure starts from; every other bound record stays a full read.
ITEM_RECORD_KEYS = ("story_path", "test_plan_path")
CONTRACT_KEYS = ("verification_contract_ref", "environment_contract_ref")
# The bound inputs that grow with the package rather than with the Item.
PACKAGE_SCOPED = "system-architecture/"


def review_scope(root: Path, delivery_id: str) -> str:
    """The review_scope value the Delivery runs under.

    ``full`` while no registry declares it, and where the policy cannot be
    read, so a reader whose policy is unreadable reads everything as released.
    """
    try:
        return delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, CLOSURE_SWITCH)
    except (KeyError, ValueError):
        return "full"


def closure_read(root: Path, current: dict) -> tuple[list[str], dict]:
    """Narrow a reader's full read to the impact closure of the Item change.

    The closure starts from the Item's Story, Test Plan and contracts and every
    vault note the change touched. An architecture note outside the closure is
    listed with the hash the candidate already binds instead of being read;
    the Item's own records, the project root files and every product file the
    change touched stay full reads.
    """
    import task_inputs

    docs = delivery.docs_root(root)
    prefix = docs.relative_to(root).as_posix() + "/"
    item = next(path for path in current["inputs"] if path.endswith("/item.md"))
    props, _ = delivery.split_note(root / item)
    seeds = {prefix + props[key] for key in ITEM_RECORD_KEYS}
    seeds |= {prefix + props[key] + ".md" for key in CONTRACT_KEYS if props.get(key)}
    # Every vault file the Item changed since its integration base starts the
    # closure, notes and data alike; a deleted note seeds it with what it named.
    changed_vault = {path for path in current["changed_files"]
                     if path.startswith(prefix) and task_inputs.canonical_source(path)}
    seeds |= changed_vault
    deleted = task_inputs.deleted_texts(root, current.get("integration_base_commit"),
                                        changed_vault)
    try:
        scope = task_inputs.impact_closure(docs, seeds, prefix, deleted)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    reach = set(scope["closure"]) | set(scope["graph_gaps"]) | seeds
    reads = {path for path in reach if (root / path).is_file()}
    # Only a note the closure proves unchanged since its approval stays unread.
    proven = {row["path"] for row in scope["proven_unchanged"]}
    unread = sorted(path for path in current["inputs"]
                    if path.startswith(prefix + PACKAGE_SCOPED) and path not in reads
                    and path in proven)
    scope["proven_unchanged"] = [row for row in scope["proven_unchanged"] if row["path"] not in reads]
    scope.update(read="closure", seeds=sorted(seeds),
                 unread_inputs=[{"path": path, "sha256": current["inputs"][path]} for path in unread])
    full = (set(current["inputs"]) - set(unread)) | set(current["changed_files"]) | reads
    return sorted(full), {"scope": scope, "views": task_inputs.vault_views(docs)}


FINDING_SUMMARY_FIELDS = ("id", "role", "severity", "calibrated_severity", "status", "file")


def finding_summary(finding: dict) -> dict:
    """One line per inherited finding; the full record stays in the manifest's findings file."""
    value = {key: finding[key] for key in FINDING_SUMMARY_FIELDS if key in finding}
    text = str(finding.get("title") or finding.get("description") or finding.get("verification") or "")
    line = text.strip().splitlines()[0] if text.strip() else ""
    value["title"] = line if len(line) <= 160 else line[:157] + "..."
    return value


def reader_attachment(root: Path, name: str, data, use: str) -> dict:
    """Write bulky manifest data beside the session and return its path and hash.

    The CLI binds staleness through the frozen session, never through this copy,
    so a reader opens the file only when it needs the full data."""
    path = safe_runtime_path(root, session_path(root).parent / "manifest" / name, file_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_file.replace_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": digest(data), "entries": len(data), "use": use}


def manifest(root: Path, delivery_id: str, story: str, role: str, mode: str, remote: str = "origin") -> dict:
    if role not in ROLES or mode not in policy()["role_modes"][role]:
        raise RuntimeError("unsupported verification role or mode")
    value = read_session(root)
    current = require_current(root, value, allow_evidence=True)
    if (current["delivery"], current["story"]) != (delivery_id, story):
        raise RuntimeError("verification session belongs to another Item")
    require_published_claims(root, current, remote)
    previous = value.get("previous_candidate")
    delta = list(current["changed_files"])
    scope_expanded = bool(previous and (previous.get("inputs") != current["inputs"]
                                       or previous.get("instruction_identity") != current["instruction_identity"]))
    if previous and not scope_expanded and mode in {"review_repair", "qa_diagnostic"}:
        old = previous.get("product_commit", "")
        try:
            delta = sorted(set(part for part in git(root, "diff", "--name-only", "--no-renames", "-z", old, current["product_commit"]).split("\0") if part))
        except RuntimeError:
            scope_expanded = True
    contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    checks = {name: {"passed": False, "evidence": "Independent assessment of this gate"}
              for name in required_checks(root, current, role)} if mode != "qa_diagnostic" else {}
    for name, command_kind in (("full_test_suite", "test"), ("mutation_whole_changed_files", "mutation"),
                               ("dependency_audit", "dependency_audit")):
        if name in checks:
            checks[name]["raw_evidence_hash"] = "Copy evidence_hash from run --kind " + command_kind
    if "full_test_suite" in checks:
        checks["full_test_suite"].update(command=contract["test_command"], exit_code=0,
                                         environment="Copy identity.environment_hash from run --kind test")
    if "mutation_whole_changed_files" in checks:
        checks["mutation_whole_changed_files"]["files"] = current["mutation_files"]
    if "fresh_runtime" in checks:
        checks["fresh_runtime"]["event_hashes"] = []
    unresolved = value.get("unresolved_findings", [])
    bound = {key: item for key, item in current.items() if key != "source_observations"}
    result = {"schema_version": 1, "role": role, "mode": mode, "session_id": value["session_id"],
              **bound, "required_checks": list(checks),
              "source_observations_file": reader_attachment(
                  root, "source-observations.json", current["source_observations"],
                  "The CLI checks this snapshot itself; readers never open it."),
              "full_read": sorted(set(current["inputs"]) | set(current["changed_files"])),
              # A delta equal to the whole change names it instead of repeating every path.
              "repair_delta": "changed_files" if delta == current["changed_files"] else delta,
              "scope_expanded": scope_expanded,
              "unresolved_findings": [finding_summary(finding) for finding in unresolved],
              "unresolved_findings_file": reader_attachment(
                  root, "unresolved-findings.json", unresolved,
                  "Full records of the one-line unresolved_findings; read only to carry a finding forward."),
              "read_interface": {"inspect": "inspect --path <repository-relative-path> [--base]", "diff": "diff [--path <repository-relative-path>]"},
              "review_passes": policy()["review_checks"], "allowed_writes": [str(session_path(root).parent / "scratch")],
              "mutation_scope_file": str(session_path(root).parent / "mutation-files.json"),
              "command_form": command_form(root) + " <verb> ...",
              "result_interface": {"candidate_hash": current["candidate_hash"], "session_id": value["session_id"],
                                   "role": role, "mode": mode, "verdict": "passed|failed|cancelled",
                                   "report": "Independent findings and conclusion", "checks": checks, "findings": [],
                                   "blocked_commands": []},
              "diagnostic_interface": {"available": bool(contract.get("diagnostic_test_command")),
                                       "command": "run --kind diagnostic_test --selection-file <scratch-selection.json>",
                                       "selection": {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                                                     "failed_test_ids": [], "affected_test_ids": []},
                                       "finding_controls": "Optional [{finding_id, test_ids}]: run the negative"
                                                           " controls an open finding lists in"
                                                           " negative_control_test_ids, from test files the Item"
                                                           " changed; recorded as finding_diagnostic evidence only",
                                       "environment_variable": "AGENTROF_DIAGNOSTIC_TESTS",
                                       "terminal_evidence": False},
              "live_test_interface": {"groups": list(contract.get("live_groups") or []),
                                      "command": "run --kind live_test --group <group>",
                                      "evidence": "Cite evidence_hash from status --run live_test:<group> in the check"
                                                  " the Test Plan names; live groups never join required_checks"}
              if contract.get(operation_compile.LIVE_GROUP_RUN) != "combined" else
              {"groups": list(contract.get("live_groups") or []), "run": "combined",
               "command": "run --kind live_test --group <group>,<group>,... once, with every group the pass selects",
               "evidence": "Cite evidence_hash from status --run live_test:<groups>, the selected groups"
                           " comma-separated in live_groups order; live groups never join required_checks"},
              "execution_note": "Commands run in private clones containing tracked files only. Approved commands must provision dependencies or use a fixed external environment; ignored dependencies are never copied. The clone is not an operating-system sandbox for trusted commands with absolute paths.",
              "next_transition": "Register independent result; owner writes reports only after both readers settle"}
    if role == "qa_engineer" and value["workers"][role].get("resume_commands"):
        result["resume_commands"] = value["workers"][role]["resume_commands"]
    if role == "qa_engineer" and (steps := declared_owner_steps(root)):
        result["owner_step_interface"] = {
            "steps": steps, "progress": owner_step_progress(value),
            "await": "await-owner-step --step <step>",
            "owner_result": "owner-step-result --file <owner-output.json> (owner's external terminal only)",
            "result_fields": {"session_id": value["session_id"], "candidate_hash": current["candidate_hash"],
                              "step": "Declared step id", "command": "Exact declared command", "exit_code": 0,
                              "output": "Owner's actual command output", "owner_confirmed": True},
            "rule": "Keep the reader open while awaiting_owner_step; resume at the declared instruction after"
                    " owner output passes. Never run the owner's command or confirm output on their behalf."}

    if finding_control_history(value):
        result["finding_control_bindings"] = finding_control_history(value)
    if review_scope(root, delivery_id) == CLOSURE_VALUE:
        result["full_read"], closure = closure_read(root, current)
        result.update({CLOSURE_SWITCH: CLOSURE_VALUE, "vault_views": closure["views"],
                       CLOSURE_VALUE: closure["scope"]})
    import project_context
    seeds = {path for path in current["inputs"] if path.endswith("/item.md")}
    result["project_reading"] = project_context.task_context(root, entry="deliver",
        role=role.replace("_", "-"), mode="review", paths=seeds | set(current["changed_files"]), no_cache=True,
        change_base=current["integration_base_commit"])
    result["read_interface"]["context"] = "inspect-context --plan <saved verification manifest>"
    result["read_interface"]["expand_context"] = "expand-context --plan <saved context manifest> --reason <reason> [--ref <reference>]"
    result["context_guidance"] = ("Start with project_reading and batch-read with inspect-context; "
        "Required review scope and verification gates remain mandatory; suggested reads are bounded advice. Expand optional context only when needed. "
        "If context is insufficient, use frozen inspect/diff on your initiative or parent direction "
        "and report context_findings with sources, impact, recovery and proposed fix to the parent. "
        "Only the parent offers an anonymous issue through issue-report after exact-payload user approval.")
    live_plan = (live_reuse_plan(root, current, contract)
                 if contract.get("live_groups") and live_case_reuse(root, delivery_id) else None)
    if live_plan is not None:
        result["live_reuse"] = live_plan
        live_rows = ([row for row in carried_findings(root, value, current, scope_expanded, live_plan)
                      if row.get("live_group")] if role == "qa_engineer" else [])
        if live_rows:
            result["carried_findings"] = live_rows
    if role == "code_reviewer":
        # The code reviewer runs no command: it reads the freeze record and QA's results instead.
        del result["diagnostic_interface"]
        result["verification_evidence"] = reviewer_evidence(root, value)
        if review_loop(root, delivery_id) == "blocking_delta" and mode == "review_repair":
            result["carried_findings"] = [row for row in carried_findings(root, value, current, scope_expanded, live_plan)
                                          if row.get("role", "code_reviewer") == "code_reviewer"]
    panel = code_review_panel_state(root, value) if role == "code_reviewer" else None
    if panel is None and role == "code_reviewer" and code_review_panel(root, current["delivery"]) == RISK_SCOPED:
        result["lens_selection"] = lens_selection(root, current, panel_assignments(), previous)[1]
    if panel is not None:
        # Every reader of the panel pass receives this same manifest and one assignment.
        result["code_review_panel"] = {
            "pass": panel["pass"],
            "assignments": [{"lens": assignment["lens"], "focus": assignment["focus"],
                             "id_prefix": panel_prefix(panel, assignment)} for assignment in panel["assignments"]],
            "lens_result_interface": {"candidate_hash": current["candidate_hash"], "session_id": value["session_id"],
                                      "role": role, "mode": PANEL_LENS_MODE, "lens": "The assignment's lens ids",
                                      "verdict": "passed|failed", "report": "Independent findings through the lens",
                                      "findings": []},
            "registration": "Every reader registers its own result with panel-result --file <result.json>;"
                            " merge-panel then registers the one code review result"}
        if "lens_selection" in panel:
            result["code_review_panel"]["lens_selection"] = panel["lens_selection"]
    budget = json.loads(project_context.POLICY.read_text(encoding="utf-8"))["reader_manifest_bytes"]
    if len(json.dumps(result, indent=2).encode("utf-8")) > budget:
        reading = result["project_reading"]
        result["project_reading_file"] = reader_attachment(
            root, "project-reading-" + digest(reading)[7:] + ".json", reading,
            "The frozen inspect-context command loads this bound reading plan.")
        result["project_reading"] = {key: reading[key] for key in ("status", "coverage") if key in reading}
    return result


LAUNCH_PROMPTS_PATH = Path(__file__).resolve().parents[1] / "templates/reader-launch-prompts.json"


def launch_prompt(template: list[str], fields: dict) -> str:
    """A reader launch prompt: its template lines with every placeholder filled; a missing field refuses."""
    try:
        return "\n".join(line.format_map(fields) for line in template) + "\n"
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"reader launch prompt template names an unknown placeholder: {exc}") from exc


def start_pass(root: Path, delivery_id: str, story: str, *, review_mode: str | None = None,
               qa_mode: str = "qa_final", fresh: bool = False, remote: str = "origin") -> dict:
    """Start one verification pass of an Item: pre-handoff regression when the Delivery runs it, freeze,
    both readers' manifests and their launch prompts. The first failing step stops the chain."""
    root = root.resolve()
    steps: list[dict] = []

    def stopped(step: str, error: str) -> dict:
        steps.append({"step": step, "ok": False, "error": error})
        return {"ok": False, "failed_step": step, "errors": [f"{step}: {error}"], "steps": steps}

    pairing_errors = operation_compile.pairing_findings(delivery.docs_root(root))
    if pairing_errors:
        return stopped("contract-pairing", "; ".join(pairing_errors))
    try:
        if delivery.delivery_switch_value(delivery.docs_root(root), delivery_id, "repair_review") == "before_freeze":
            previous = read_session(root, required=False)
            if previous is not None:
                current = candidate(root, delivery_id, story, allow_evidence=True)
                require_repair_review(root, previous, current)
                steps.append({"step": "repair-review", "ok": True})
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        return stopped("repair-review", str(exc))
    try:
        rehearsals = require_rehearsals(root, delivery_id, story)
        if rehearsals is not None:
            steps.append({"step": "rehearsals", "ok": True, "steps": rehearsals["steps"]})
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        return stopped("rehearsals", str(exc))
    try:
        regression = pre_handoff_regression(root, delivery_id)
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        return stopped("regression-selection", str(exc))
    if regression == TOUCHED_SUITES:
        try:
            selection = regression_selection(root, delivery_id, story)
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            return stopped("regression-selection", str(exc))
        steps.append({"step": "regression-selection", "ok": True, "selection_file": selection["selection_file"]})
        try:
            ran = regression_run(root, delivery_id, story)
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            return stopped("regression-run", str(exc))
        if ran.get("exit_code") != 0 or ran.get("candidate_intact") is False:
            return stopped("regression-run", f"exit code {ran.get('exit_code')}, candidate intact"
                                             f" {ran.get('candidate_intact')}; read {ran.get('output_file')}")
        steps.append({"step": "regression-run", "ok": True, "duration_seconds": ran.get("duration_seconds")})
    else:
        steps.extend({"step": step, "ok": True, "skipped": f"{PRE_HANDOFF_SWITCH} is {regression}"}
                     for step in ("regression-selection", "regression-run"))
    try:
        frozen = freeze(root, delivery_id, story, fresh=fresh, remote=remote)
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        return stopped("freeze", str(exc))
    steps.append({"step": "freeze", "ok": True, "reused": bool(frozen.get("reused"))})
    modes = {"code_reviewer": review_mode or ("review_repair" if frozen.get("previous_candidate") else "review_initial"),
             "qa_engineer": qa_mode}
    directory = safe_runtime_path(root, session_path(root).parent / "pass")
    directory.mkdir(parents=True, exist_ok=True)
    manifests: dict[str, dict] = {}
    paths: dict[str, str] = {}
    for role, mode in modes.items():
        try:
            value = manifest(root, delivery_id, story, role, mode, remote)
            path = safe_runtime_path(root, directory / f"manifest-{role}.json", file_only=True)
            atomic_file.replace_text(path, json.dumps(value, indent=2) + "\n")
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            return stopped(f"manifest {role}", str(exc))
        manifests[role], paths[role] = value, str(path)
        steps.append({"step": f"manifest {role}", "ok": True, "mode": mode, "path": str(path)})
    try:
        templates = json.loads(LAUNCH_PROMPTS_PATH.read_text(encoding="utf-8"))["prompts"]
        panel = manifests["code_reviewer"].get(CODE_REVIEW_PANEL)
        common = {"delivery": delivery_id, "story": story, "session_id": frozen["session_id"],
                  "candidate_hash": frozen["candidate"]["candidate_hash"],
                  "constitution": str(Path(__file__).resolve().parents[1] / "constitution.md"),
                  "runner": " ".join(subprocess.list2cmdline([part]) if os.name == "nt" else shlex_quote(part)
                                     for part in (sys.executable, str(Path(__file__).resolve()),
                                                  "--worktree", str(root))),
                  "scratch": str(session_path(root).parent / "scratch")}
        readers = [("code_reviewer", "code_reviewer", {"agent": "code-reviewer", "mode": modes["code_reviewer"],
                                                       "register": "panel-result" if panel else "result"}),
                   ("qa_engineer", "qa_engineer", {"agent": "qa-engineer", "mode": modes["qa_engineer"],
                                                   "register": "result"})]
        for assignment in (panel or {}).get("assignments", []):
            readers.append((f"code_reviewer_lens-{assignment['id_prefix'].rstrip('-')}", "code_reviewer_lens",
                            {"agent": "code-reviewer-lens", "mode": PANEL_LENS_MODE, "register": "panel-result",
                             "lens": ", ".join(assignment["lens"]), "id_prefix": assignment["id_prefix"]}))
        prompts = {}
        for name, template, fields in readers:
            role = "code_reviewer" if template != "qa_engineer" else "qa_engineer"
            text = launch_prompt(templates[template], {**common, **fields, "manifest": paths[role]})
            path = safe_runtime_path(root, directory / f"launch-{name}.md", file_only=True)
            atomic_file.replace_text(path, text)
            prompts[name] = {"path": str(path), "text": text}
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        return stopped("launch prompts", str(exc))
    steps.append({"step": "launch prompts", "ok": True, "readers": sorted(prompts)})
    value = {"ok": True, "session_id": frozen["session_id"], "candidate_hash": frozen["candidate"]["candidate_hash"],
             "steps": steps, "manifests": paths, "prompts": prompts}
    plan = manifests["qa_engineer"].get("live_reuse")
    if plan is not None:
        value["live_reuse"] = {key: plan[key] for key in ("base_commit", "selected_groups", "reusable_groups")}
    return value



REVIEWER_COMMAND_RULE = ("The code reviewer starts no test, diagnostic or other verification command and no"
                         " regression-run; it reads the frozen candidate's recorded runs with status instead.")
# A finding names a repository path as a token with a slash or a file extension, and a
# symbol as an identifier inside backticks.
NAMED_PATH_RE = re.compile(r"(?<![\w.-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+|(?:[\w.-]+/)+[\w.-]+)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def reviewer_evidence(root: Path, session: dict) -> dict:
    """What the code reviewer reads in place of a test run: the freeze record and QA's recorded runs."""
    pre_handoff = session.get("pre_handoff")
    return {"rule": REVIEWER_COMMAND_RULE,
            "pre_handoff_run": run_summary(root, pre_handoff) if isinstance(pre_handoff, dict) else None,
            "qa_runs": {kind: run_summary(root, record)
                        for kind, record in sorted(session.get("raw_evidence", {}).items())},
            "read": "status --summary; status --run pre_handoff|test|mutation|dependency_audit|diagnostic_test"}


def finding_names(finding: dict) -> tuple[list[str], list[str]]:
    """The repository paths and the code symbols a finding names, read as data from its text fields."""
    anchor = str(finding.get("file") or "")
    paths = {anchor.rsplit(":", 1)[0] if re.search(r":\d+(?:-\d+)?$", anchor) else anchor} - {""}
    symbols = set()
    for key in ("file", "description", "impact", "fix", "verification"):
        text = str(finding.get(key) or "")
        paths.update(match.rstrip(".") for match in NAMED_PATH_RE.findall(text))
        for quoted in BACKTICK_RE.findall(text):
            if not NAMED_PATH_RE.fullmatch(quoted.strip()):
                symbols.update(IDENTIFIER_RE.findall(quoted))
    return sorted(paths), sorted(symbols)


def carried_findings(root: Path, session: dict, current: dict, scope_expanded: bool,
                     live_plan: dict | None = None) -> list[dict]:
    """Which unresolved code review findings a repair review re-examines and which it carries.

    At review_loop blocking_delta a carried finding is re-examined only when a
    file or symbol it names changed between the previous candidate and this
    one; otherwise its status is carried from the record. Without a previous
    candidate, or when the bound inputs or instructions changed, every one is
    re-examined. With *live_plan*, at live_case_reuse delta_since_green, a
    finding of either reader that names its live_group joins: while that group
    reuses its base run and no file the finding names changed since that base,
    it may close on the reused evidence.
    """
    findings = [finding for finding in session.get("unresolved_findings", [])
                if finding.get("role") == "code_reviewer" or (live_plan and finding.get("live_group"))]
    previous = session.get("previous_candidate") or {}
    changed_files: set[str] = set()
    known_files: set[str] = set()
    changed_lines: list[str] = []
    whole = scope_expanded or not previous.get("product_commit")
    if not whole and findings:
        old, new = previous["product_commit"], current["product_commit"]
        try:
            known_files = {part for commit in (old, new)
                           for part in git(root, "ls-tree", "-r", "--name-only", "-z", commit).split("\0") if part}
            changed_files = {part for part in git(root, "diff", "--name-only", "--no-renames", "-z", old, new)
                             .split("\0") if part}
            changed_lines = [line[1:] for line in git(root, "diff", "--no-renames", "--unified=0", old, new)
                             .splitlines() if line[:1] in {"+", "-"} and line[:3] not in {"+++", "---"}]
        except RuntimeError:
            whole = True
    rows = []
    for finding in findings:
        paths, symbols = finding_names(finding)
        touched = sorted(path for path in paths if path in changed_files
                         or any(changed.startswith(path.rstrip("/") + "/") for changed in changed_files))
        moved = sorted(symbol for symbol in symbols
                       if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", line)
                              for line in changed_lines))
        anchored = any(path in known_files or any(name.startswith(path.rstrip("/") + "/") for name in known_files)
                       for path in paths)
        reexamine = whole or not anchored or bool(touched or moved)
        row = {"id": finding["id"], "severity": finding.get("severity"), "status": finding.get("status"),
               "named_files": paths, "named_symbols": symbols, "changed_files": touched,
               "changed_symbols": moved, "action": "reexamine" if reexamine else "carry"}
        group = finding.get("live_group") if live_plan else None
        if group:
            base = live_plan["groups"].get(group, {})
            since = base.get("changed_paths") or []
            row.update(live_group=group, role=finding.get("role"))
            if base.get("reusable") and not any(path == changed or changed.startswith(path.rstrip("/") + "/")
                                                for path in paths for changed in since):
                row.update(action="close_on_reused_live", live_base_evidence_hash=base["base_evidence_hash"])
        rows.append(row)
    return rows


RUN_SUMMARY_FIELDS = ("live_group", "resume_count", "resume_of", "exit_code", "candidate_intact", "selection_intact", "evidence_scope", "reused_pre_handoff", "reused_live_base", "duration_seconds",
                      "completed_at", "evidence_hash", "environment_hash", "checkout_difference", "earlier_stories",
                      "output_file")


def run_summary(root: Path, record: dict) -> dict:
    """One run's outcome without the identity and bindings that make the whole session large."""
    fields = {**record.get("identity", {}), **record}
    value = {key: fields[key] for key in RUN_SUMMARY_FIELDS if key in fields}
    if "reused_pre_handoff" in value:
        value["reused_pre_handoff"] = value["reused_pre_handoff"].get("evidence_hash")
    if "reused_live_base" in value:
        value["reused_live_base"] = {group: entry.get("evidence_hash") if isinstance(entry, dict) else entry
                                     for group, entry in value["reused_live_base"].items()}
    if "output_file" in value:
        value["output_path"] = str(raw_output_path(root, value["output_file"]))
    return value


BLOCKED_BY_PERMISSION = "blocked_by_permission"


def cli_form(root: Path, *verb: str) -> str:
    """One literal invocation of this packaged CLI, as a host permission rule matches it."""
    arguments = [sys.executable, "-B", str(Path(__file__).resolve()), "--worktree", str(root.resolve()), *verb]
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def permission_preflight(root: Path, delivery_id: str, story: str) -> dict:
    """The exact commands the QA reader runs for this Item, for the owner to allow before the first QA round.

    Permission rules belong to the owner, so the package only names the
    command forms and the host rules that admit them; it never edits them.
    """
    root = root.resolve()
    contract, _ = delivery.split_note(delivery.docs_root(root) / "operation/verification-contract.md")
    item = item_record(root, delivery_id, story)
    forms = [("full_test_suite", cli_form(root, "run", "--kind", "test"))]
    if contract.get("diagnostic_test_command"):
        forms.append(("diagnostic_test", cli_form(root, "run", "--kind", "diagnostic_test", "--selection-file")
                      + " <scratch-selection.json>"))
    if contract.get("mutation_disposition") == "required":
        forms.append(("mutation", cli_form(root, "run", "--kind", "mutation")))
    if contract.get("dependency_audit_disposition") == "required":
        forms.append(("dependency_audit", cli_form(root, "run", "--kind", "dependency_audit")))
    groups = contract.get("live_groups")
    for group in groups if isinstance(groups, list) else []:
        forms.append((f"live_test:{group}", cli_form(root, "run", "--kind", "live_test", "--group", str(group))))
    if item.get("runtime_required"):
        for verb in ("up", "seed", "logs", "url", "down", *declared_qa_verbs(root)):
            forms.append((f"environment:{verb}", cli_form(root, "environment", "--verb", verb)))
    if qa_preflight(root, delivery_id):
        forms.append(("rehearse", cli_form(root, "rehearse", "--delivery", delivery_id, "--story", story)))
    extra = contract.get("live_permission_commands", [])
    if not isinstance(extra, list) or any(not isinstance(command, str) or not command.strip() for command in extra):
        raise RuntimeError("live_permission_commands must list exact nonempty commands")
    forms.extend((f"environment_permission:{number}", command) for number, command in enumerate(extra, 1))
    plan = delivery.docs_root(root) / str(item.get("test_plan_path", ""))
    changing = []
    if plan.is_file():
        for identifier, block in scenario_blocks(delivery.split_note(plan)[1]):
            fields = scenario_fields(block)[0]
            effect = fields.get("environment_effect", "changes")
            if fields.get("level") == "live" and effect != "reads":
                changing.append({"scenario": identifier, "environment_effect": effect,
                                 "target": fields.get("automation_target")})
    prefix = cli_form(root)
    return {"delivery": delivery_id, "story": story,
            "environment_changing_scenarios": changing,
            "probe": {"scope": "command_validation_only", "host_permission_prediction": False,
                      "command": prefix + " permission-probe --kind <kind> [--group <group>]",
                      "reason": "Host permission classifiers expose no portable no-effect authorization API; confirm the exact allow rules with the owner."},
            "commands": [{"purpose": purpose, "command": command} for purpose, command in forms],
            "allow_rules": {"command_prefixes": [f"{prefix} run", f"{prefix} environment"],
                            "exact_commands": [command for _, command in forms]},
            "next_action": ("Before the first QA round, show the owner these commands and ask them to allow them"
                            " once in the host's permission settings, as a prefix rule per command_prefixes entry"
                            " or one rule per exact command; the package never edits those settings."
                            " A QA reader whose command the host refuses records it in blocked_commands with"
                            f" outcome {BLOCKED_BY_PERMISSION}; once the owner allows it, resume-qa reruns it on"
                            " the same candidate.")}


def permission_probe(root: Path, kind: str, group: str | None = None) -> dict:
    contract = verification_contract(root)
    if kind == "live_test":
        if group is None:
            raise RuntimeError("live permission probe requires a group")
        command = live_test_command(contract, group)
    else:
        if group is not None:
            raise RuntimeError("only live permission probes take a group")
        command = contract.get(kind + "_command")
        if not isinstance(command, str) or not command.strip():
            raise RuntimeError("permission probe requires an approved command")
    return {"effect": "none", "command": command, "command_valid": True,
            "host_permission": "unknown", "owner_allow_rule_required": True}


def check_blocked_commands(result: dict) -> list[dict]:
    """A reader's commands the host refused, each named as blocked_by_permission rather than left unrun."""
    blocked = result.get("blocked_commands", [])
    if (not isinstance(blocked, list) or any(
            not isinstance(row, dict) or set(row) != {"command", "outcome", "reason"}
            or row["outcome"] != BLOCKED_BY_PERMISSION
            or not all(isinstance(row[key], str) and row[key].strip() for key in ("command", "reason"))
            for row in blocked)):
        raise RuntimeError("blocked_commands entries declare only command, reason and outcome"
                           f" {BLOCKED_BY_PERMISSION}")
    return blocked


def status_summary(root: Path, run: str | None = None) -> dict:
    """The session's identity, readers and run outcomes; `run` narrows the runs to one kind."""
    session = read_session(root)
    runs = dict(session["raw_evidence"])
    progress = live_progress_records(root, session)
    for key, value in progress.items():
        if key not in runs:
            runs[key] = value
    if "pre_handoff" in session:
        runs["pre_handoff"] = session["pre_handoff"]
    if run is not None:
        if run not in runs:
            raise RuntimeError(f"no {run} run is recorded in verification session {session['session_id']}")
        runs = {run: runs[run]}
    current = session["candidate"]
    result = {"session_id": session["session_id"], "delivery": current["delivery"], "story": current["story"],
            "candidate_hash": current["candidate_hash"], "product_commit": current["product_commit"],
            "workers": {role: worker["state"] for role, worker in session["workers"].items()},
            "unresolved_findings": len(session.get("unresolved_findings", [])),
            BLOCKED_BY_PERMISSION: [{**row, "role": role} for role, worker in session["workers"].items()
                                    for row in worker.get("result", {}).get("blocked_commands", [])],
            "metrics": session["metrics"], "runs": {
                kind: record if kind in progress and "identity" not in record else run_summary(root, record)
                for kind, record in runs.items()}}
    if session.get("owner_steps"):
        result["owner_steps"] = owner_step_progress(session)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "manifest", "validate", "regression-selection", "regression-run", "assertion-map",
                 "repair-live", "repair-review-manifest", "repair-review-result", "permission-preflight",
                 "rehearse", "rehearsal-status", "live-base", "follow-up-issues", "scoped-repair"):
        cmd = subs.add_parser(name)
        cmd.add_argument("--delivery", required=True)
        cmd.add_argument("--story", required=True)
    subs.choices["repair-review-result"].add_argument("--file", required=True)
    subs.choices["live-base"].add_argument("--commit", required=True,
                                           help="an ancestor of the Item's candidate, such as a nightly build")
    subs.choices["live-base"].add_argument("--group", required=True,
                                           help="the live groups to run, comma-separated as run --kind live_test takes them")
    rebind = subs.add_parser("rebind-unit-run")
    rebind.add_argument("--delivery", required=True)
    subs.choices["freeze"].add_argument("--fresh", action="store_true")
    for name in ("freeze", "manifest"):
        subs.choices[name].add_argument("--remote", default="origin",
                                        help="the Delivery's Git remote, whose Integration holds its provisional claims")
    for name in ("manifest",):
        subs.choices[name].add_argument("--role", choices=ROLES, required=True)
        subs.choices[name].add_argument("--mode", required=True)
    start = subs.add_parser("start-pass")
    start.add_argument("--delivery", required=True)
    start.add_argument("--story", required=True)
    start.add_argument("--review-mode", choices=("review_initial", "review_repair"))
    start.add_argument("--qa-mode", choices=("qa_final", "qa_diagnostic"), default="qa_final")
    start.add_argument("--fresh", action="store_true")
    start.add_argument("--remote", default="origin")
    result = subs.add_parser("result")
    result.add_argument("--file", required=True)
    subs.add_parser("single-verifier-result").add_argument("--file", required=True)
    owner_wait = subs.add_parser("await-owner-step")
    owner_wait.add_argument("--step", required=True)
    owner_result = subs.add_parser("owner-step-result")
    owner_result.add_argument("--file", required=True)
    calibrate = subs.add_parser("calibrate")
    calibrate.add_argument("--file", required=True)
    panel_result = subs.add_parser("panel-result")
    panel_result.add_argument("--file", required=True)
    probe = subs.add_parser("permission-probe")
    probe.add_argument("--kind", choices=RUN_KINDS, required=True)
    probe.add_argument("--group")
    subs.add_parser("merge-panel")
    run = subs.add_parser("run")
    run.add_argument("--kind", choices=RUN_KINDS, required=True)
    run.add_argument("--group", action="append",
                     help="with --kind live_test: a group of the contract's live_groups; repeat it or separate"
                          " names with commas for one combined run where the contract declares live_group_run"
                          " combined")
    run.add_argument("--selection-file", type=Path)
    run.add_argument("--spot-run-file", type=Path)
    run.add_argument("--fresh", action="store_true")
    run.add_argument("--resume", action="store_true")
    environment = subs.add_parser("environment")
    environment.add_argument("--verb", required=True,
                             help="down, up, seed, logs, url or a QA verb the Verification Contract declares")
    environment.add_argument("--value")
    lane = subs.add_parser("lane-run")
    lane.add_argument("--delivery", required=True)
    lane.add_argument("--story", required=True)
    lane.add_argument("--role", required=True)
    lane.add_argument("--kind", choices=("test", "environment"), required=True)
    lane.add_argument("--verb", choices=("down", "up", "seed", "logs", "url"))
    lane.add_argument("--value")
    inspect = subs.add_parser("inspect")
    selection = inspect.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path")
    selection.add_argument("--instruction")
    inspect.add_argument("--base", action="store_true")
    diff = subs.add_parser("diff")
    diff.add_argument("--path", action="append", default=[])
    for verb in ("inspect-context", "expand-context"):
        context = subs.add_parser(verb)
        context.add_argument("--plan", required=True)
        if verb == "expand-context":
            context.add_argument("--reason", required=True)
            context.add_argument("--ref", action="append")
    subs.add_parser("resume-qa")
    subs.add_parser("prepare-repair")
    status = subs.add_parser("status")
    status.add_argument("--summary", action="store_true")
    status.add_argument("--run", help="one recorded run: " + ", ".join(RUN_KINDS[:-1]) + ", live_test:<group> or pre_handoff")
    wait = subs.add_parser("wait")
    wait.add_argument("--role", choices=ROLES)
    wait.add_argument("--seconds", type=float)
    args = parser.parse_args(argv)
    root = Path(args.worktree).resolve()
    try:
        if args.command == "start-pass":
            value = start_pass(root, args.delivery, args.story, review_mode=args.review_mode, qa_mode=args.qa_mode,
                               fresh=args.fresh, remote=args.remote)
            print(json.dumps(value, indent=2))
            return 0 if value["ok"] else 1
        if args.command == "freeze":
            value = freeze(root, args.delivery, args.story, fresh=args.fresh, remote=args.remote)
        elif args.command == "inspect-context":
            value = inspect_context(root, json.loads(Path(args.plan).read_text(encoding="utf-8")))
        elif args.command == "expand-context":
            value = inspect_context(root, json.loads(Path(args.plan).read_text(encoding="utf-8")),
                                    reason=args.reason, refs=args.ref)
        elif args.command == "result":
            value = register_result(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "single-verifier-result":
            value = register_single_verifier(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "await-owner-step":
            value = await_owner_step(root, args.step)
        elif args.command == "owner-step-result":
            value = record_owner_step(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "calibrate":
            value = register_calibration(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "panel-result":
            value = register_panel_result(root, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "merge-panel":
            value = merge_panel(root)
        elif args.command == "validate":
            value = validate(root, args.delivery, args.story)
        elif args.command == "scoped-repair":
            value = scoped_repair(root, args.delivery, args.story)
        elif args.command == "follow-up-issues":
            value = follow_up_issues(root, args.delivery, args.story)
        elif args.command == "environment":
            value = run_environment(root, args.verb, args.value)
        elif args.command == "lane-run":
            value = lane_run(root, args.delivery, args.story, args.role, args.kind, args.verb, args.value)
        elif args.command == "rebind-unit-run":
            value = rebind_unit_run(root, args.delivery)
        elif args.command == "regression-selection":
            value = regression_selection(root, args.delivery, args.story)
        elif args.command == "regression-run":
            value = regression_run(root, args.delivery, args.story)
        elif args.command == "repair-review-manifest":
            value = repair_review_manifest(root, args.delivery, args.story)
        elif args.command == "repair-review-result":
            value = register_repair_review(root, args.delivery, args.story, json.loads(Path(args.file).read_text(encoding="utf-8")))
        elif args.command == "repair-live":
            value = repair_live(root, args.delivery, args.story)
        elif args.command == "live-base":
            value = live_base_run(root, args.delivery, args.story, args.commit, args.group)
        elif args.command == "rehearse":
            value = rehearse(root, args.delivery, args.story)
        elif args.command == "rehearsal-status":
            value = rehearsal_status(root, args.delivery, args.story)
        elif args.command == "permission-probe":
            value = permission_probe(root, args.kind, args.group)
        elif args.command == "permission-preflight":
            value = permission_preflight(root, args.delivery, args.story)
        elif args.command == "assertion-map":
            value = assertion_map_status(root, args.delivery, args.story)
        elif args.command == "inspect":
            value = inspect_instruction(root, args.instruction) if args.instruction else inspect_candidate(root, args.path, base=args.base)
        elif args.command == "diff":
            value = candidate_diff(root, args.path)
        elif args.command == "resume-qa":
            value = resume_qa(root)
        elif args.command == "prepare-repair":
            value = prepare_repair(root)
        elif args.command == "wait":
            value = wait_for_release(root, args.role, args.seconds)
        elif args.command == "run":
            value = run_check(root, args.kind, fresh=args.fresh, selection_file=args.selection_file,
                              spot_file=args.spot_run_file, group=",".join(args.group) if args.group else None, resume=args.resume)
        elif args.command == "manifest":
            value = manifest(root, args.delivery, args.story, args.role, args.mode, args.remote)
        elif args.summary or args.run:
            value = status_summary(root, args.run)
        else:
            value = read_session(root)
        print(json.dumps({"ok": True, **value}, indent=2))
        if args.command == "owner-step-result":
            return 0 if value["state"] == "passed" else 1
        return 0 if args.command not in {"run", "environment", "lane-run", "regression-run", "repair-live", "live-base", "rehearse", "rebind-unit-run"} or (value["exit_code"] == 0 and value.get("candidate_intact", True)) else 1
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
