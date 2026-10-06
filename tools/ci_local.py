#!/usr/bin/env python3
"""Verify an exact staged candidate locally; receipts never authorize remote CI."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
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
    for key in ("environment_names", "environment_prefixes", "environment_ignored", "git_configuration_ignored"):
        names = value.get(key)
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            raise tests.CIError("invalid local environment binding: " + key)
    if value.get("test_selection") not in {"changed", "impact"}:
        raise tests.CIError("invalid local test selection")
    if type(value.get("budget_estimated_seconds")) is not int or value["budget_estimated_seconds"] < 0:
        raise tests.CIError("invalid local test budget")
    direct = value.get("direct_tools")
    if not isinstance(direct, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in direct):
        raise tests.CIError("invalid local direct tools")
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


def identity_key(root):
    """A random key of this checkout's cache, so a recorded digest confirms no guess of a variable's value."""
    cache = safe_cache(root)
    path = cache / "identity-key"
    if path_alias(path) or path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise tests.CIError("unsafe local identity key")
    if not path.exists():
        descriptor, name = tempfile.mkstemp(prefix="identity-key.", dir=cache)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(os.urandom(32))
            try:
                os.link(name, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(name)
    key = path.read_bytes()
    if len(key) != 32:
        raise tests.CIError("local identity key is invalid; remove it and run check again")
    return key


def bound_environment(policy):
    """The variables that can change what the tests do; host session ids and scratch roots are not among them."""
    names, prefixes = set(policy["environment_names"]), tuple(policy["environment_prefixes"])
    return {key: value for key, value in os.environ.items()
            if key not in ORCHESTRATION_ENV and key not in policy["environment_ignored"]
            and (key in names or key.startswith(prefixes))}


def environment_identity(root):
    # Only keyed digests leave this function. Never record credential values in a receipt.
    key = identity_key(root)
    environment = {name: hmac.new(key, (name + "\0" + value).encode("utf-8", "surrogateescape"),
                                  hashlib.sha256).hexdigest()
                   for name, value in bound_environment(policy_at(root)).items()}
    return {"runtime": tests.runtime_identity(), "python_executable": str(Path(sys.executable).resolve()),
            "environment": environment,
            "git_configuration_hash": hashlib.sha256(git_configuration(root)).hexdigest()}


def git_configuration(root):
    """The configuration that can change test behaviour, without the entries other worktrees rewrite."""
    ignored = policy_at(root)["git_configuration_ignored"]
    raw = tests.git(root, "config", "--null", "--list", "--show-origin").split(b"\0")
    kept = []
    # Each entry is its origin, then its key and value split by the first newline.
    for origin, entry in zip(raw[0::2], raw[1::2]):
        key = entry.split(b"\n", 1)[0].decode("utf-8", "surrogateescape")
        if not tests.matches(key, ignored):
            kept.append(origin + b"\0" + entry + b"\0")
    return b"".join(kept)


def environment_difference(receipt, plan):
    """What alone separates a receipt from this plan: the changed variable names, never their values."""
    recorded = receipt.get("environment") if isinstance(receipt, dict) else None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("environment"), dict):
        return None
    same = {**{key: value for key, value in plan.items() if key != "plan_hash"}, "environment": recorded}
    if receipt.get("plan_hash") != tests.digest(same):
        return None
    current = plan["environment"]
    names = sorted(name for name in set(recorded["environment"]) | set(current["environment"])
                   if recorded["environment"].get(name) != current["environment"].get(name))
    labels = {"runtime": "Python, Git or OS runtime", "python_executable": "Python executable",
              "git_configuration_hash": "Git configuration"}
    return names + [label for key, label in labels.items() if recorded.get(key) != current.get(key)]


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
    # The shared config and packed-refs change when another worktree creates, deletes or fetches a
    # branch; their test-relevant content is compared through the environment and candidate instead.
    git_files = ["index", "HEAD", "logs/HEAD"]
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


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
TESTS_DIRECTORY = "tools/tests/"


def changed_lines(root, source, path, text):
    """The non-blank lines of the candidate's version of a file that differ from the base.

    A deletion counts as the lines around it. Blank lines are left out, so the
    blank line a diff takes between two functions does not count as code
    outside them, and a change of blank lines alone changes nothing.
    """
    raw = tests.git(root, "diff", "--no-ext-diff", "--no-color", "--unified=0", source["base"], source["tree"],
                    "--", path).decode("utf-8", "replace")
    lines = text.splitlines()
    changed = set()
    for start, count in HUNK.findall(raw):
        low = int(start)
        high = low + int(count or 1) - 1 if count != "0" else low + 1
        changed.update(number for number in range(low, high + 1)
                       if 0 < number <= len(lines) and lines[number - 1].strip())
    return sorted(changed)


def candidate_text(root, source, path):
    try:
        return tests.git(root, "show", f"{source['tree']}:{path}").decode("utf-8")
    except (tests.CIError, UnicodeDecodeError):
        return None


def definitions(text):
    """Each top-level function and class and each method: (first line, last line, class, function), or None."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    def first(node):
        return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((first(node), node.end_lineno, None, node.name))
        elif isinstance(node, ast.ClassDef):
            found.append((first(node), node.end_lineno, node.name, None))
            found.extend((first(item), item.end_lineno, node.name, item.name) for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return found


def enclosing_functions(lines, found):
    """The (class, function) each line lies in, or None when one lies outside every function."""
    names = set()
    for line in lines:
        enclosing = [item for item in found if item[3] is not None and item[0] <= line <= item[1]]
        if not enclosing:
            return None
        names.add(max(enclosing, key=lambda item: item[0])[2:])
    return names


def module_path(module):
    return module.replace(".", "/") + ".py"


def method_ids(module, methods, all_ids):
    # An inherited test runs under each class that has it, so the method name selects it everywhere in the module.
    return [test_id for test_id in all_ids if tests.module_of(test_id) == module and test_id.rsplit(".", 1)[-1] in methods]


def module_ids(module, all_ids):
    return [test_id for test_id in all_ids if tests.module_of(test_id) == module]


def changed_test_ids(root, source, path, module, all_ids):
    """The changed test methods of a changed test module, or the whole module when other code changed."""
    text = candidate_text(root, source, path)
    found = definitions(text) if text is not None else None
    names = enclosing_functions(changed_lines(root, source, path, text), found) if found is not None else None
    if names is None or any(not name.startswith("test") for _cls, name in names):
        return module_ids(module, all_ids), True
    return method_ids(module, {name for _cls, name in names}, all_ids), False


def naming_ids(root, paths, policy, all_ids):
    """Tests that name a changed non-Python input: by file name, or by folder and name when the name repeats.

    Only the test methods that name it run, unless the module names it outside them.
    """
    tracked = [PurePosixPath(os.fsdecode(raw)) for raw in tests.git(root, "ls-files", "-z").split(b"\0") if raw]
    counts = {}
    for path in tracked:
        counts[path.name] = counts.get(path.name, 0) + 1
    needles = set()
    for path in map(PurePosixPath, paths):
        if path.suffix == ".py" or tests.matches(path.as_posix(), policy.get("generated_paths", [])):
            continue
        needles.add(path.name if counts.get(path.name, 0) <= 1 or len(path.parts) < 2 else "/".join(path.parts[-2:]))
    selected = []
    for module in sorted({tests.module_of(test_id) for test_id in all_ids}) if needles else []:
        text = (root / module_path(module)).read_text(encoding="utf-8")
        lines = [number for number, line in enumerate(text.splitlines(), 1) if any(needle in line for needle in needles)]
        if not lines:
            continue
        found = definitions(text)
        names = enclosing_functions(lines, found) if found is not None else None
        if names is None or any(not name.startswith("test") for _cls, name in names):
            selected += module_ids(module, all_ids)
        else:
            selected += method_ids(module, {name for _cls, name in names}, all_ids)
    return selected


def own_test_ids(root, source, path, module, all_ids):
    """The tests of a changed module's own test module, those that name a changed definition first."""
    text = candidate_text(root, source, path)
    found = definitions(text) if text is not None else None
    names = enclosing_functions(changed_lines(root, source, path, text), found) if found is not None else None
    every = module_ids(module, all_ids)
    if names is None:
        return [], every
    if not names:
        return [], []
    words = re.compile(r"\b(?:" + "|".join(sorted({re.escape(name) for pair in names for name in pair if name})) + r")\b")
    test_text = (root / module_path(module)).read_text(encoding="utf-8").splitlines()
    test_found = definitions("\n".join(test_text)) or []
    naming = {name for start, end, _cls, name in test_found if name and name.startswith("test")
              and words.search("\n".join(test_text[start - 1:end]))}
    first = method_ids(module, naming, all_ids)
    return first, [test_id for test_id in every if test_id not in set(first)]


def estimate_weights(policy, runner_os):
    """Per-test seconds: the full-suite lanes' measured estimates, refined by this system's own."""
    estimates = policy.get("test_seconds", {})
    weights = {test_id: seconds for lane in policy["lanes"].values() if lane["groups"] == ["all"]
               for test_id, seconds in estimates.get(lane["os"], {}).items()}
    weights.update(estimates.get(runner_os, {}))
    return weights


def changed_ids(root, source, policy, all_ids, weights, budget, integration=frozenset()):
    """The change's own unit tests, most specific first, within the local budget.

    In order: the changed test methods (a whole changed test module when code
    outside its test methods changed); the test methods that name a changed
    non-Python input; in the own test module of each changed Python module,
    the tests that name a changed function or class, then the test modules
    that import a changed test helper, then the rest of each own test module.
    Pull request CI runs everything; a test marked ``@integration``, or one
    that would take the estimate past the budget, is left to it.
    """
    # A generated copy is checked by the distribution sync; its canonical source selects the tests.
    real = [path for path in source["changed_paths"] if not tests.matches(path, policy.get("generated_paths", []))]
    stems = {tests.module_of(test_id).rsplit(".", 1)[-1]: tests.module_of(test_id) for test_id in all_ids}
    python = [path for path in real if path.endswith(".py") and candidate_text(root, source, path) is not None]
    changed_tests, own_first, own_rest, helpers = [], [], [], set()
    for path in python:
        stem = PurePosixPath(path).stem
        if path.startswith(TESTS_DIRECTORY) and stem in stems:
            ids, whole = changed_test_ids(root, source, path, stems[stem], all_ids)
            changed_tests += ids
            if whole:
                helpers.add(stem)
        elif path.startswith(TESTS_DIRECTORY):
            helpers.add(stem)
        if not path.startswith(TESTS_DIRECTORY) and "test_" + stem in stems:
            first, rest = own_test_ids(root, source, path, stems["test_" + stem], all_ids)
            own_first += first
            own_rest += rest
    importers = []
    if helpers:
        graph = tests.import_graph(root)
        modules = sorted({module for stem, module in stems.items() if graph.get(stem, set()) & helpers})
        importers = [test_id for module in modules for test_id in module_ids(module, all_ids)]
    tiers = [("changed tests", changed_tests), ("tests naming a changed input", naming_ids(root, real, policy, all_ids)),
             ("tests naming a changed definition", own_first), ("importers of changed test code", importers),
             ("other own-module tests", own_rest)]
    selected, seen, spent, deferred, left, counts = [], set(), 0.0, 0, 0, []
    for label, tier in tiers:
        taken = 0
        for test_id in tier:
            if test_id in seen:
                continue
            seen.add(test_id)
            if test_id in integration:
                left += 1
                continue
            cost = tests.test_weight(test_id, weights, policy)
            if budget and spent + cost > budget:
                deferred += 1
                continue
            selected.append(test_id)
            spent += cost
            taken += 1
        if taken:
            counts.append(f"{label} {taken}")
    reason = f"{len(selected)} unit tests of the change: " + (", ".join(counts) or "none")
    if left:
        reason += f"; {left} integration tests left to pull request CI"
    if deferred:
        reason += f"; {deferred} more left to pull request CI past the local budget"
    return sorted(selected), "changed", reason


def make_plan(root, target="origin/main", jobs=None, full=False):
    local_policy = policy_at(root)
    source = candidate(root, target)
    policy = tests.policy_at(root)
    ids, inventory_hash = tests.inventory(root)
    runner_os = {"Linux": "ubuntu-latest", "Darwin": "macos-latest", "Windows": "windows-latest"}.get(tests.platform.system())
    weights = estimate_weights(policy, runner_os)
    # Integration tests start processes or write outside their temporary directory; only CI runs them.
    integration = tests.integration_ids(root)
    units = [test_id for test_id in ids if test_id not in integration]
    if full:
        selected, mode, reason = units, "full", "every unit test requested"
    elif source["base"] is None:
        selected, mode, reason = units, "full", "target merge-base unavailable; every unit test required"
    elif local_policy["test_selection"] == "changed":
        # Pull request CI runs every test on Linux; this gate gives the change's own unit tests before the push.
        selected, mode, reason = changed_ids(root, source, policy, ids, weights,
                                             local_policy["budget_estimated_seconds"], integration)
    else:
        selected, mode, reason = tests.select_ids("impact", source["changed_paths"], policy, ids, root)
        left = len([test_id for test_id in selected if test_id in integration])
        selected = [test_id for test_id in selected if test_id not in integration]
        if left:
            reason += f"; {left} integration tests left to pull request CI"
    jobs = local_policy["default_workers"] if jobs is None else jobs
    if type(jobs) is not int or not 1 <= jobs <= local_policy["max_workers"]:
        raise tests.CIError("worker count is outside local policy")
    jobs = min(jobs, os.cpu_count() or 1)
    # A mandatory native regression marked @integration runs only in CI, so only a selected unit one binds here.
    must_run = sorted(set(selected) & {test_id for lane in policy["lanes"].values()
                      if lane["os"] == runner_os for test_id in lane.get("required_tests", [])})
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
    if ids != sorted(set(ids)) or sorted(item for shard in plan["shards"] for item in shard) != ids \
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
            lambda: tests.load_selected(root, plan["shards"][shard], ids), report, path, tests.integration_ids(root), unit_only=True)
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


FAILURE_LINE = re.compile(r"^(FAIL|ERROR): \S+ \(([^)]+)\)")
SEPARATOR = "-" * 70


def failure_summary(logs):
    """Each failing or erroring test in the worker logs, with the last line of its traceback."""
    lines = []
    for shard, path in enumerate(logs):
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        for block in text.split("=" * 70):
            parts = block.strip("\n").split(SEPARATOR)
            match = FAILURE_LINE.match(parts[0])
            if match is None:
                continue
            body = [line.strip() for line in (parts[1] if len(parts) > 1 else "").splitlines() if line.strip()]
            message = body[-1][:160] if body else ""
            lines.append(f"shard {shard}: {match.group(1)} {match.group(2)}" + (f": {message}" if message else ""))
    return lines


def keep_failure_logs(cache, scratches):
    """Replace the kept logs of the last failed run with this run's worker logs and reports."""
    kept = cache / "last-failure"
    shutil.rmtree(kept, ignore_errors=True)
    for shard, scratch in enumerate(scratches):
        target = kept / str(shard)
        target.mkdir(parents=True)
        for name in ("output.log", "report.json"):
            if (scratch / name).is_file():
                shutil.copyfile(scratch / name, target / name)
    return kept


def direct_tool_directory(root, directory):
    """On macOS, a bin directory with the tools whose /usr/bin entry is an xcrun trampoline.

    The trampoline resolves the developer directory on every call, which in
    measured runs tripled the cost of a git call; workers call the tool it
    resolves to. Such a tool finds its helpers and templates beside its own
    bin directory, so that directory's siblings link to those of the tool's
    real prefix. Other systems, and tools found elsewhere first, are unchanged.
    """
    if tests.platform.system() != "Darwin":
        return None
    prefix = directory / "direct-tools"
    linked = prefix / "bin"
    for name in policy_at(root)["direct_tools"]:
        found = shutil.which(name)
        if found is None or Path(found).parent != Path("/usr/bin"):
            continue
        resolved = subprocess.run(["xcrun", "--find", name], capture_output=True, text=True, check=False)
        target = Path(resolved.stdout.strip())
        if resolved.returncode or not target.is_absolute() or target.parent.name != "bin" \
                or not os.access(target, os.X_OK):
            continue
        siblings = {entry.name: entry for entry in target.parent.parent.iterdir() if entry.name != "bin"}
        # A sibling another tool's prefix already claims would send this tool to the wrong helpers.
        if any((prefix / sibling).is_symlink() and (prefix / sibling).readlink() != entry
               for sibling, entry in siblings.items()):
            continue
        linked.mkdir(parents=True, exist_ok=True)
        for sibling, entry in siblings.items():
            if not (prefix / sibling).is_symlink():
                (prefix / sibling).symlink_to(entry)
        (linked / name).symlink_to(target)
    return linked if linked.is_dir() else None


def execute_workers(root, plan, cache):
    workers = []
    with tempfile.TemporaryDirectory(prefix="agentrof-ci-workers-", dir=worker_temp_parent(root)) as temporary:
        directory = Path(temporary)
        plan_path = directory / "plan.json"
        tests.write_json(plan_path, plan)
        stdlib_cache, stdlib_identity = prewarm_stdlib(directory)
        tools_bin = direct_tool_directory(root, directory)
        try:
            for index in range(len(plan["shards"])):
                scratch = directory / str(index)
                scratch.mkdir()
                output = (scratch / "output.log").open("wb")
                environment = {**execution_environment(root),
                               "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
                               "PYTHONPYCACHEPREFIX": str(stdlib_cache)}
                if tools_bin is not None:
                    environment["PATH"] = str(tools_bin) + os.pathsep + environment.get("PATH", "")
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
            if any(codes):
                # The summary comes last and the logs outlive this directory, so a cut output still names the failure.
                scratches = [scratch for _process, _output, scratch in workers]
                kept = keep_failure_logs(cache, scratches)
                failures = failure_summary([scratch / "output.log" for scratch in scratches])
                print(f"ci-local: {len(failures)} failing tests" + (":" if failures else "; see the worker logs"))
                for line in failures:
                    print("  " + line)
                print(f"ci-local: worker logs kept at {kept}")
                raise tests.CIError(f"one or more local test workers failed; worker logs kept at {kept}")
            reports = [tests.read_json(scratch / "report.json") for _process, _output, scratch in workers]
            if cache_identity(stdlib_cache) != stdlib_identity:
                raise tests.CIError("read-only stdlib cache changed during local validation")
            shutil.rmtree(cache / "last-failure", ignore_errors=True)
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


def check(root, target="origin/main", jobs=None, fresh=False, verify_only=False, full=False):
    cache = safe_cache(root)
    with receipt_lock(cache):
        latest = cache / "latest.json"
        # Verify also records invalidation if candidate inspection itself fails.
        previous = read_receipt(latest)
        attempt = {"schema_version": 1, "authority": "local_only", "status": "running", "started_at": time.time()}
        try:
            policy = policy_at(root)
            if verify_only and jobs is None and isinstance(previous, dict) and type(previous.get("jobs")) is int:
                # The worker count partitions the receipt's tests; verify takes the one check used.
                jobs = previous["jobs"]
                print(f"ci-local: verifying with the receipt's {jobs} workers")
            if verify_only and isinstance(previous, dict) and previous.get("full") is True:
                full = True
                print("ci-local: verifying the receipt's full local suite")
            jobs = policy["default_workers"] if jobs is None else jobs
            plan = make_plan(root, target, jobs, full)
            generation = generation_token(root)
            valid = reusable(previous, plan, policy["max_age_seconds"])
            if verify_only:
                if not valid:
                    changed = environment_difference(previous, plan)
                    if changed:
                        raise tests.CIError("no current successful local receipt: the receipt for this candidate"
                                            " was made in another validation environment (changed: "
                                            + ", ".join(changed) + "); run make check-local here")
                    raise tests.CIError("no current successful local receipt; run make check-local")
                assert_current(root, plan)
                assert_generation(root, generation)
                print("ci-local: exact staged candidate has a current local receipt")
                return previous
            attempt.update(plan_hash=plan["plan_hash"], environment=plan["environment"], jobs=jobs, full=full)
            tests.write_json(latest, attempt)
            started = time.monotonic()
            # Static checks only read the candidate, so they run beside the test workers.
            static = [subprocess.Popen([sys.executable, *command], cwd=root, env=execution_environment(root),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for command in plan["static_commands"]]
            run_started = time.monotonic()
            try:
                reports = previous["reports"] if valid and not fresh else execute_workers(root, plan, cache)
            finally:
                outputs = [process.communicate()[0] for process in static]
            static_seconds = time.monotonic() - started
            for command, process, output in zip(plan["static_commands"], static, outputs):
                print(output.decode("utf-8", "replace"), end="")
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, [sys.executable, *command])
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
            command.add_argument("--full", action="store_true",
                                 help="run every test, for changes whose host-specific behavior CI cannot cover")
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
        check(ROOT, args.target, args.jobs, getattr(args, "fresh", False), args.command == "verify",
              getattr(args, "full", False))
        return 0
    except (tests.CIError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"ci-local: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ci-local: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
