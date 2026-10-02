#!/usr/bin/env python3
"""Verify an exact staged candidate locally; receipts never authorize remote CI."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import ci_tests as tests

path_alias = tests.path_alias
worker_temp_parent = tests.worker_temp_parent
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "tools/data/ci-local-policy.json"
CACHE_PATH = ".agentrof/agent-marketplace/.runtime/ci-local"
ORCHESTRATION_ENV = {"_", "SHLVL", "PWD", "OLDPWD", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMOUT", "MAKE_TERMERR"}
STDLIB_PREWARM = (
    "import argparse, ast, collections, compileall, ctypes, dataclasses, datetime, difflib, "
    "fnmatch, functools, glob, hashlib, importlib, inspect, io, json, pathlib, platform, "
    "re, shutil, signal, site, socket, stat, subprocess, tempfile, threading, time, "
    "traceback, typing, unittest, urllib.request, uuid, xml.etree.ElementTree, zipfile"
)


def policy_at(root):
    value = tests.read_json(root / POLICY_PATH)
    if value.get("schema_version") != 1 or type(value.get("max_age_seconds")) is not int \
            or not 1 <= value["max_age_seconds"] <= 86400:
        raise tests.CIError("invalid local receipt policy")
    if any(type(value.get(key)) is not int for key in ("default_workers", "max_workers")) or \
            not 1 <= value.get("default_workers", 0) <= value.get("max_workers", 0) <= 4:
        raise tests.CIError("invalid local worker policy")
    commands = value.get("static_commands")
    if not isinstance(commands, list) or not commands or any(
            not isinstance(command, list) or not command or
            any(not isinstance(arg, str) or not arg for arg in command) for command in commands):
        raise tests.CIError("invalid local static commands")
    return value


def index_entries(root):
    if tests.git(root, "ls-files", "--unmerged", "-z"):
        raise tests.CIError("resolve the merge before local validation")
    flags = tests.git(root, "ls-files", "-v", "-z").split(b"\0")
    if any(row and row[:1] != b"H" for row in flags):
        raise tests.CIError("remove assume-unchanged/skip-worktree flags before local validation")
    if tests.git(root, "ls-files", "--others", "--exclude-standard", "-z"):
        raise tests.CIError("stage or remove untracked source files before local validation")
    allowed = policy_at(root).get("ignored_cache_paths", [])
    ignored = tests.git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    for raw in ignored.split(b"\0"):
        if raw and not tests.matches(os.fsdecode(raw), allowed):
            raise tests.CIError("untracked ignored source is outside the cache policy: " + os.fsdecode(raw))
    result = []
    for record in tests.git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        if stage != "0" or mode not in {"100644", "100755"}:
            raise tests.CIError("unsupported index entry: " + os.fsdecode(name))
        result.append((os.fsdecode(name), mode, oid))
    return result


def candidate(root, target, expected=None):
    """Read every tracked byte, bypassing Git's stat cache and index flags."""
    entries = index_entries(root)
    # SHA-1 and SHA-256 repositories use their native Git blob identity.
    algorithm = tests.git(root, "rev-parse", "--show-object-format").decode().strip()
    for name, mode, blob_oid in entries:
        path = root / name
        try:
            metadata = path.lstat()
            if path_alias(path, metadata) or any(path_alias(root / parent) for parent in path.relative_to(root).parents if parent != Path(".")):
                raise tests.CIError("unsafe tracked file ancestor: " + name)
            if not stat.S_ISREG(metadata.st_mode):
                raise tests.CIError("unsafe tracked file type: " + name)
            if os.name != "nt" and bool(metadata.st_mode & stat.S_IXUSR) != (mode == "100755"):
                raise tests.CIError("index/worktree executable mode differs: " + name)
            content = path.read_bytes()
        except OSError as error:
            raise tests.CIError("tracked file is missing or unreadable: " + name) from error
        actual = hashlib.new(algorithm, b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if actual != blob_oid:
            raise tests.CIError("index/worktree bytes differ; stage the complete candidate: " + name)
    head = tests.git(root, "rev-parse", "HEAD^{commit}").decode().strip()
    index_hash = tests.digest(entries)
    if expected is not None:
        if index_hash != expected["index_hash"]:
            raise tests.CIError("index changed during local validation")
        tree = expected["tree"]
    else:
        tree = tests.git(root, "write-tree").decode().strip()
        if index_entries(root) != entries:
            raise tests.CIError("index changed while identifying the candidate")
    try:
        base = tests.git(root, "merge-base", head, target).decode().strip()
        paths = tests.changed_paths(root, base, tree)
    except tests.CIError:
        base, paths = None, []
    return {"head": head, "tree": tree, "base": base, "target": target,
            "changed_paths": paths, "index_hash": tests.digest(entries)}


def execution_environment(root):
    return {**{key: value for key, value in os.environ.items() if key not in ORCHESTRATION_ENV},
            "PWD": str(root), "PYTHONDONTWRITEBYTECODE": "1"}


def environment_identity(root):
    # Only digests leave this function. Never record credential values in a receipt.
    environment = {key: value for key, value in os.environ.items()
                   if key not in ORCHESTRATION_ENV}
    configuration = tests.git(root, "config", "--null", "--list", "--show-origin")
    return {"runtime": tests.runtime_identity(), "python_executable": str(Path(sys.executable).resolve()),
            "environment_hash": tests.digest(environment),
            "git_configuration_hash": hashlib.sha256(configuration).hexdigest()}


def file_generation(path):
    """Observe writes separately from the content-based receipt identity."""
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    changed = metadata.st_ctime_ns
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        with path.open("rb") as stream:
            info = BasicInfo()
            if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.change <= 0:
                raise tests.CIError("filesystem does not expose a trustworthy file change timestamp")
            changed = info.change
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mode,
            metadata.st_mtime_ns, changed)


def generation_token(root):
    paths = {root / name for name, _mode, _oid in index_entries(root)}
    git_files = ["index", "HEAD", "logs/HEAD", "config", "packed-refs"]
    branch = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "symbolic-ref", "--quiet", "HEAD"],
                            capture_output=True, check=False)
    if branch.returncode == 0:
        git_files.append(branch.stdout.decode("utf-8").strip())
    for name in git_files:
        path = Path(os.fsdecode(tests.git(root, "rev-parse", "--git-path", name)).strip())
        paths.add(path if path.is_absolute() else root / path)
    return tests.digest([(str(path), file_generation(path)) for path in sorted(paths)])


def assert_generation(root, expected):
    if generation_token(root) != expected:
        raise tests.CIError("source or Git state was written during local validation; restored bytes do not preserve this attempt")


def make_plan(root, target="origin/main", jobs=None):
    local_policy = policy_at(root)
    source = candidate(root, target)
    policy = tests.policy_at(root)
    ids, inventory_hash = tests.inventory(root)
    selected, mode, reason = tests.select_ids("impact" if source["base"] else "full",
                                               source["changed_paths"], policy, ids, root)
    if source["base"] is None:
        reason = "target merge-base unavailable; full local suite required"
    jobs = local_policy["default_workers"] if jobs is None else jobs
    if type(jobs) is not int or not 1 <= jobs <= local_policy["max_workers"]:
        raise tests.CIError("worker count is outside local policy")
    jobs = min(jobs, os.cpu_count() or 1)
    runner_os = {"Linux": "ubuntu-latest", "Darwin": "macos-latest", "Windows": "windows-latest"}.get(tests.platform.system())
    must_run = sorted(set(selected) & {test_id for lane in policy["lanes"].values()
                      if lane["os"] == runner_os for test_id in lane.get("required_tests", [])})
    # The full-suite lane's measured estimates cover every module; this
    # system's own estimates refine the tests its lanes measured.
    estimates = policy.get("test_seconds", {})
    weights = {test_id: seconds for lane in policy["lanes"].values() if lane["groups"] == ["all"]
               for test_id, seconds in estimates.get(lane["os"], {}).items()}
    weights.update(estimates.get(runner_os, {}))
    plan = {"schema_version": 1, "authority": "local_only", "candidate": source,
            "must_run_ids": must_run,
            "environment": environment_identity(root), "policy_hash": tests.digest(policy),
            "local_policy_hash": tests.digest(local_policy), "inventory_hash": inventory_hash,
            "selected_ids": selected, "mode": mode, "selection_reason": reason,
            "shards": tests.balanced_shards(selected, jobs, weights, policy),
            "static_commands": local_policy["static_commands"]}
    plan["plan_hash"] = tests.digest(plan)
    return plan


def validate_plan(plan):
    if plan.get("schema_version") != 1 or plan.get("authority") != "local_only" or \
            plan.get("plan_hash") != tests.digest({k: v for k, v in plan.items() if k != "plan_hash"}):
        raise tests.CIError("local plan identity is invalid")
    ids = plan.get("selected_ids", [])
    if not ids or ids != sorted(set(ids)) or sorted(item for shard in plan["shards"] for item in shard) != ids \
            or any(not shard for shard in plan["shards"]):
        raise tests.CIError("local shards do not exactly partition the selection")


def assert_current(root, plan, environment=True):
    validate_plan(plan)
    if candidate(root, plan["candidate"]["target"], plan["candidate"]) != plan["candidate"]:
        raise tests.CIError("candidate changed during local validation")
    if environment and environment_identity(root) != plan["environment"]:
        raise tests.CIError("validation environment changed")


def verify_reports(plan, reports):
    validate_plan(plan)
    if not isinstance(reports, list) or len(reports) != len(plan["shards"]):
        raise tests.CIError("missing or extra local worker report")
    seen = set()
    for report in reports:
        if not isinstance(report, dict):
            raise tests.CIError("invalid local report shape")
        shard = report.get("shard")
        if type(shard) is not int or shard in seen or not 0 <= shard < len(plan["shards"]):
            raise tests.CIError("duplicate or unexpected local worker report")
        seen.add(shard)
        if report.get("schema_version") != 1 or report.get("plan_hash") != plan["plan_hash"] \
                or report.get("status") != "complete" or report.get("errors") or report.get("error") \
                or report.get("runtime") != plan["environment"]["runtime"]:
            raise tests.CIError("failed, incomplete or mismatched local worker")
        rows = report.get("tests", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or \
                sorted(row.get("id", "") for row in rows) != plan["shards"][shard]:
            raise tests.CIError("local worker test accounting differs")
        for row in rows:
            seconds = row.get("seconds")
            if row.get("outcome") not in tests.SUCCESS_OUTCOMES or type(seconds) not in {int, float} \
                    or not tests.math.isfinite(seconds) or seconds < 0:
                raise tests.CIError("invalid local test outcome")
            if row["id"] in plan.get("must_run_ids", []) and row["outcome"] != "success":
                raise tests.CIError("mandatory native regression did not pass")
        tests.validate_measurements(report)
    return reports


def run_worker(root, plan, shard, path):
    assert_current(root, plan, environment=False)
    if type(shard) is not int or not 0 <= shard < len(plan["shards"]):
        raise tests.CIError("invalid local shard")
    report = {"schema_version": 1, "plan_hash": plan["plan_hash"], "shard": shard,
              "runtime": tests.runtime_identity(), "status": "running", "tests": []}
    tests.write_json(path, report)
    started = time.monotonic()
    try:
        ids, identity = tests.inventory(root)
        if identity != plan["inventory_hash"]:
            raise tests.CIError("test inventory changed")
        result, unattributed = tests.run_guarded(
            lambda: tests.load_selected(root, plan["shards"][shard], ids), report, path)
        report["status"] = "complete" if result.wasSuccessful() and not unattributed else "failed"
        if unattributed:
            report["error"] = "a class or module fixture " + tests.host_calls_text(unattributed)
        assert_current(root, plan, environment=False)
        if sorted(row["id"] for row in report["tests"]) != plan["shards"][shard]:
            report["status"] = "failed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report["wall_seconds"] = round(time.monotonic() - started, 6)
        tests.write_json(path, report)
    return 0 if report["status"] == "complete" else 1


def safe_cache(root):
    cache = root / CACHE_PATH
    for path in [cache, *cache.parents]:
        if path == root:
            break
        if path_alias(path):
            raise tests.CIError("local cache cannot contain symlink or junction ancestors")
    cache.mkdir(parents=True, exist_ok=True)
    return cache


@contextmanager
def receipt_lock(cache):
    path = cache / "lock"
    if path_alias(path) or path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise tests.CIError("unsafe local receipt lock")
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise tests.CIError("another local validation owns this checkout; wait for it to finish") from error
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def reusable(receipt, plan, max_age, now=None):
    now = time.time() if now is None else now
    if not isinstance(receipt, dict) or receipt.get("status") != "complete" \
            or receipt.get("plan_hash") != plan["plan_hash"] or receipt.get("authority") != "local_only":
        return False
    finished = receipt.get("finished_at")
    if type(finished) not in {int, float} or not 0 <= now - finished <= max_age:
        return False
    if receipt.get("receipt_hash") != tests.digest({k: v for k, v in receipt.items() if k != "receipt_hash"}):
        return False
    try:
        verify_reports(plan, receipt["reports"])
        return receipt.get("static_passed") is True
    except (tests.CIError, KeyError, TypeError):
        return False


def read_receipt(path):
    try:
        if path_alias(path) or path.exists() and not path.is_file():
            return {}
        return tests.read_json(path)
    except tests.CIError:
        return {}


def cache_identity(cache):
    if path_alias(cache):
        raise tests.CIError("stdlib cache cannot contain path aliases")
    files = []
    for directory, folders, names in os.walk(cache, followlinks=False):
        for name in folders + names:
            path = Path(directory) / name
            if path_alias(path):
                raise tests.CIError("stdlib cache cannot contain path aliases")
        for name in names:
            path = Path(directory) / name
            if not path.is_file() or path.suffix != ".pyc":
                raise tests.CIError("stdlib cache contains an unexpected file")
            files.append((path.relative_to(cache).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return tests.digest(sorted(files))


def prewarm_stdlib(directory):
    cache = directory / "stdlib-cache"
    cache.mkdir()
    optimization = ["-" + "O" * sys.flags.optimize] if sys.flags.optimize else []
    # Isolated, site-disabled startup cannot resolve project/PYTHONPATH modules.
    # Only this trusted child writes bytecode; test processes consume it read-only.
    subprocess.run([sys.executable, "-I", "-S", *optimization, "-X", "pycache_prefix=" + str(cache),
                    "-c", STDLIB_PREWARM], cwd=directory, check=True, capture_output=True)
    return cache, cache_identity(cache)


def execute_workers(root, plan, cache):
    workers = []
    with tempfile.TemporaryDirectory(prefix="agentrof-ci-workers-", dir=worker_temp_parent(root)) as temporary:
        directory = Path(temporary)
        plan_path = directory / "plan.json"
        tests.write_json(plan_path, plan)
        stdlib_cache, stdlib_identity = prewarm_stdlib(directory)
        try:
            for index in range(len(plan["shards"])):
                scratch = directory / str(index)
                scratch.mkdir()
                output = (scratch / "output.log").open("wb")
                environment = {**execution_environment(root),
                               "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
                               "PYTHONPYCACHEPREFIX": str(stdlib_cache)}
                process = subprocess.Popen([sys.executable, str(root / "tools/ci_local.py"), "worker",
                    "--plan", str(plan_path), "--shard", str(index), "--report", str(scratch / "report.json")],
                    cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT)
                workers.append((process, output, scratch))
            started = time.monotonic()
            next_update = started + 30
            while any(process.poll() is None for process, _output, _scratch in workers):
                if time.monotonic() >= next_update:
                    finished = sum(process.poll() is not None for process, _output, _scratch in workers)
                    print(f"ci-local: {finished}/{len(workers)} workers finished after {time.monotonic() - started:.0f}s", flush=True)
                    next_update = time.monotonic() + 30
                time.sleep(.1)
            codes = [process.returncode for process, _output, _scratch in workers]
            for process, output, scratch in workers:
                output.close()
                print((scratch / "output.log").read_text(encoding="utf-8", errors="replace"), end="")
            reports = [tests.read_json(scratch / "report.json") for _process, _output, scratch in workers]
            if cache_identity(stdlib_cache) != stdlib_identity:
                raise tests.CIError("read-only stdlib cache changed during local validation")
            if any(codes):
                raise tests.CIError("one or more local test workers failed")
            return verify_reports(plan, reports)
        finally:
            for process, output, _scratch in workers:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                output.close()


def check(root, target="origin/main", jobs=None, fresh=False, verify_only=False):
    cache = safe_cache(root)
    with receipt_lock(cache):
        latest = cache / "latest.json"
        # Verify also records invalidation if candidate inspection itself fails.
        previous = read_receipt(latest)
        attempt = {"schema_version": 1, "authority": "local_only", "status": "running", "started_at": time.time()}
        try:
            plan = make_plan(root, target, jobs)
            generation = generation_token(root)
            policy = policy_at(root)
            valid = reusable(previous, plan, policy["max_age_seconds"])
            if verify_only:
                if not valid:
                    raise tests.CIError("no current successful local receipt; run make check-local")
                assert_current(root, plan)
                assert_generation(root, generation)
                print("ci-local: exact staged candidate has a current local receipt")
                return previous
            attempt["plan_hash"] = plan["plan_hash"]
            tests.write_json(latest, attempt)
            started = time.monotonic()
            for command in plan["static_commands"]:
                subprocess.run([sys.executable, *command], cwd=root, check=True,
                               env=execution_environment(root))
            static_seconds = time.monotonic() - started
            assert_current(root, plan)
            assert_generation(root, generation)
            run_started = time.monotonic()
            reports = previous["reports"] if valid and not fresh else execute_workers(root, plan, cache)
            assert_current(root, plan)
            assert_generation(root, generation)
            verify_reports(plan, reports)
            attempt.update(status="complete", finished_at=time.time(), static_passed=True, reports=reports,
                           reused_tests=bool(valid and not fresh), selection_reason=plan["selection_reason"],
                           selected_count=len(plan["selected_ids"]), worker_count=len(plan["shards"]),
                           static_seconds=round(static_seconds, 6), test_wall_seconds=round(time.monotonic() - run_started, 6))
            # Reuse does not extend the original tests' expiry.
            if valid and not fresh:
                attempt["finished_at"] = previous["finished_at"]
            attempt["receipt_hash"] = tests.digest(attempt)
            tests.write_json(latest, attempt)
            print(f"ci-local: {len(plan['selected_ids'])} tests; {len(plan['shards'])} workers; "
                  f"{'reused exact local results' if attempt['reused_tests'] else 'fresh results'}; {plan['selection_reason']}")
            return attempt
        except BaseException:
            attempt.update(status="failed", finished_at=time.time())
            tests.write_json(latest, attempt)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--staged", action="store_true", required=True)
        command.add_argument("--target", default="origin/main")
        command.add_argument("--jobs", type=int)
        if name == "check":
            command.add_argument("--fresh", action="store_true")
    worker = commands.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--plan", type=Path, required=True)
    worker.add_argument("--shard", type=int, required=True)
    worker.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.command == "worker":
            return run_worker(ROOT, tests.read_json(args.plan), args.shard, args.report)
        check(ROOT, args.target, args.jobs, getattr(args, "fresh", False), args.command == "verify")
        return 0
    except (tests.CIError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"ci-local: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ci-local: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
