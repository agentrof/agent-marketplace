#!/usr/bin/env python3
"""Plan, shard and account for repository tests without importing them to plan."""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import importlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "tools/data/ci-test-policy.json"
SUCCESS_OUTCOMES = {"success", "skipped", "expected_failure"}
MAX_WORKERS = 16
PROGRESS_SECONDS = 30
RUNNERS = {"ubuntu-latest", "macos-latest", "windows-latest"}
ESTIMATE_MINIMUM_SECONDS = 5.0
INTERPRETER_KEYS = {"source", "package", "version", "sha512"}
NO_INTERPRETER = {"interpreter_package": "", "interpreter_version": "", "interpreter_sha512": ""}


class CIError(ValueError):
    pass


# A project generator or host_models.py finds Claude Code and Codex through
# the environment, which a session that runs the suite points at its own
# binaries; a test pins fakes of its own (fixtures.isolated_hosts). The
# runner points the environment at these tripwires instead, and a test during
# which one ran fails. Each prints a version, so the search stops there.
TRIPWIRE_CODEX_VERSION = "0.0.0-tripwire"
TRIPWIRE_HOSTS = {"claude": ("CLAUDE_CODE_EXECPATH", "0.0.0 (Claude Code)"),
                  "codex": ("CODEX_CLI_PATH", f"codex-cli {TRIPWIRE_CODEX_VERSION}")}
TRIPWIRE_PROGRAM = """import json, sys
host, args = sys.argv[1], sys.argv[2:]
with open(LOG, "a", encoding="utf-8") as log:
    log.write(json.dumps([host, *args]) + "\\n")
if args == ["--version"]:
    print(VERSIONS[host])
    sys.exit(0)
sys.exit(1)
"""


class HostTripwire:
    """Tripwire `claude` and `codex` binaries, and the host calls they record."""

    def __init__(self, directory):
        directory = Path(directory)
        self.log = directory / "calls.jsonl"
        program = directory / "tripwire.py"
        versions = {host: version for host, (_name, version) in TRIPWIRE_HOSTS.items()}
        program.write_text(f"LOG = {str(self.log)!r}\nVERSIONS = {versions!r}\n" + TRIPWIRE_PROGRAM,
                           encoding="utf-8")
        (directory / "codex-home").mkdir()
        self.environment = {"CODEX_VERSION": TRIPWIRE_CODEX_VERSION,
                            "CODEX_HOME": str(directory / "codex-home")}
        for host, (name, _version) in TRIPWIRE_HOSTS.items():
            if os.name == "nt":
                path = directory / f"{host}.cmd"
                path.write_text(f'@"{sys.executable}" "{program}" {host} %*\n', encoding="utf-8")
            else:
                path = directory / host
                path.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                                f"{shlex.quote(str(program))} {host} \"$@\"\n", encoding="utf-8")
                path.chmod(0o755)
            self.environment[name] = str(path)
        self.seen = 0

    def calls(self):
        try:
            lines = self.log.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [" ".join(json.loads(line)) for line in lines if line.strip()]

    def reached(self):
        """The host calls recorded since the last look."""
        calls = self.calls()
        new, self.seen = calls[self.seen:], len(calls)
        return new


@contextlib.contextmanager
def host_tripwire():
    """Point this process's environment at tripwire host binaries, then restore it."""
    with tempfile.TemporaryDirectory(prefix="agentrof-host-tripwire-") as directory:
        tripwire = HostTripwire(directory)
        saved = {name: os.environ.get(name) for name in (*tripwire.environment, "CLAUDE_PID")}
        os.environ.pop("CLAUDE_PID", None)
        os.environ.update(tripwire.environment)
        try:
            yield tripwire
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def host_calls_text(calls):
    return ("reached a host binary through the runner's environment instead of a fake it pins"
            " (fixtures.isolated_hosts): " + "; ".join(calls))


def run_guarded(load, report, report_path):
    """Load and run a suite with tripwire host binaries in the environment.

    Return the result and the host calls that no test was running for, which
    a class or module fixture made after the last test.
    """
    with host_tripwire() as tripwire:
        suite = load()
        result = unittest.TextTestRunner(verbosity=2, resultclass=lambda *args, **kwargs:
            TimedResult(*args, report=report, report_path=report_path, tripwire=tripwire,
                        **kwargs)).run(suite)
        return result, tripwire.reached()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CIError(f"cannot read JSON {path}: {error}") from error


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def git(root, *arguments):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise CIError(result.stderr.decode("utf-8", "replace").strip() or "Git operation failed")
    return result.stdout


def changed_paths(root, base, head):
    # --no-renames includes both sides of a rename, including the deleted path.
    if not base:
        raise CIError("impact selection requires --base")
    raw = git(root, "diff", "--name-only", "--no-renames", "-z", base, head, "--")
    return sorted(set(os.fsdecode(path) for path in raw.split(b"\0") if path))


def inventory(root):
    """Accept the repository's explicit unittest classes; reject dynamic discovery."""
    found = []
    sources = {}
    for path in sorted((root / "tools/tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        module = "tools.tests." + path.stem
        sources[path.relative_to(root).as_posix()] = hashlib.sha256(text.encode()).hexdigest()
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            raise CIError(f"invalid test source: {path}: {error}") from error
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "load_tests"
               for node in ast.walk(tree)):
            raise CIError(f"dynamic load_tests is not supported: {path}")
        before = len(found)
        top_classes = {id(node) for node in tree.body if isinstance(node, ast.ClassDef)}
        for nested in ast.walk(tree):
            if isinstance(nested, ast.ClassDef) and id(nested) not in top_classes and any(
                    isinstance(base, ast.Attribute) and base.attr == "TestCase" for base in nested.bases):
                raise CIError(f"conditional or nested TestCase is not supported: {module}")
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and item.name.startswith("test")]
            direct_case = any(isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                              and base.value.id == "unittest" and base.attr == "TestCase" for base in node.bases)
            if methods and (not direct_case or len(node.bases) != 1):
                raise CIError(f"test inheritance requires explicit inventory support: {module}.{node.name}")
            if direct_case:
                nested = [item for item in ast.walk(node) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and item.name.startswith("test")]
                if len(nested) != len(methods):
                    raise CIError(f"conditional or nested tests are not supported: {module}.{node.name}")
                found.extend(f"{module}.{node.name}.{method}" for method in methods)
        if len(found) == before:
            raise CIError(f"test module has no statically discoverable tests: {module}")
    if not found or len(found) != len(set(found)):
        raise CIError("test inventory is empty or contains duplicate IDs")
    return sorted(found), digest(sources)


def module_of(test_id):
    return test_id.rsplit(".", 2)[0]


def matches(value, patterns):
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def valid_interpreter(value, lane):
    """A python.org NuGet build for Windows: exact version of the lane, SHA-512 bound."""
    return (isinstance(value, dict) and set(value) == INTERPRETER_KEYS and value["source"] == "nuget"
            and lane.get("os") == "windows-latest"
            and isinstance(value["package"], str) and re.fullmatch(r"[a-z0-9][a-z0-9.-]*", value["package"]) is not None
            and isinstance(value["version"], str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["version"]) is not None
            and value["version"].rsplit(".", 1)[0] == lane.get("python")
            and isinstance(value["sha512"], str) and re.fullmatch(r"[0-9a-f]{128}", value["sha512"]) is not None)


def policy_at(root):
    policy = read_json(root / POLICY_PATH)
    if policy.get("schema_version") != 1:
        raise CIError("unsupported CI test policy")
    required = {"groups", "lanes", "always_groups", "full_paths", "rules", "module_seconds"}
    if not required <= set(policy):
        raise CIError("CI test policy is incomplete")
    for name, lane in policy["lanes"].items():
        if not isinstance(lane.get("shards"), int) or not 1 <= lane["shards"] <= 16:
            raise CIError(f"invalid shard count for {name}")
        if type(lane.get("workers", 1)) is not int or not 1 <= lane.get("workers", 1) <= MAX_WORKERS:
            raise CIError(f"invalid worker count for {name}")
        if lane.get("os") not in RUNNERS:
            raise CIError(f"invalid operating system for {name}")
        if not isinstance(lane.get("python"), str) or len(lane["python"].split(".")) != 2 \
                or not all(part.isdigit() for part in lane["python"].split(".")):
            raise CIError(f"Python lane must pin major.minor: {name}")
        if "interpreter" in lane and not valid_interpreter(lane["interpreter"], lane):
            raise CIError(f"invalid pinned interpreter for {name}")
    estimates = policy.get("test_seconds", {})
    if not isinstance(estimates, dict) or not set(estimates) <= RUNNERS or any(
            not isinstance(values, dict) or any(
                not isinstance(test_id, str) or type(seconds) not in {int, float}
                or not math.isfinite(seconds) or not 0 < seconds <= 600 for test_id, seconds in values.items())
            for values in estimates.values()):
        raise CIError("invalid per-test duration estimates")
    return policy


def group_ids(names, policy, all_ids):
    result = set()
    pending = list(names)
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        if name not in policy["groups"]:
            raise CIError(f"unknown test group: {name}")
        group = policy["groups"][name]
        pending.extend(group.get("depends_on", []))
        for pattern in group.get("tests", []):
            selected = [test_id for test_id in all_ids if fnmatch.fnmatchcase(test_id, pattern)]
            if not selected:
                raise CIError(f"test selector matches no test: {pattern}")
            result.update(selected)
    return sorted(result)


def dependency_tests(root, paths, all_ids):
    """Follow local imports and literal script paths, conservatively by module stem."""
    affected = {Path(path).stem for path in paths if path.endswith(".py")}
    graph = {}
    for directory in ("tools", "plugins", "platforms"):
        for path in (root / directory).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeError) as error:
                raise CIError(f"cannot inspect Python dependency: {path}") from error
            dependencies = graph.setdefault(path.stem, set())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    dependencies.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        dependencies.add(node.module.rsplit(".", 1)[-1])
                    dependencies.update(alias.name for alias in node.names)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".py"):
                    dependencies.add(Path(node.value).stem)
    previous = set()
    while previous != affected:
        previous = set(affected)
        affected.update(name for name, dependencies in graph.items() if dependencies & previous)
    return {test_id for test_id in all_ids if module_of(test_id).rsplit(".", 1)[-1] in affected}


def select_ids(mode, paths, policy, all_ids, root=None):
    if mode not in {"full", "impact", "reuse"}:
        raise CIError(f"unknown plan mode: {mode}")
    if mode == "reuse":
        return [], "reuse", "caller must verify complete prior evidence for this source"
    if mode == "full":
        return list(all_ids), "full", "full coverage requested"
    if "known_test_modules" in policy:
        missing = sorted({module_of(test_id) for test_id in all_ids} - set(policy["known_test_modules"]))
        if missing:
            return list(all_ids), "full", "test inventory mapping is incomplete: " + ", ".join(missing)
    if any(matches(path, policy.get("generated_paths", [])) for path in paths) and not any(
            matches(path, policy.get("generated_sources", [])) for path in paths):
        return list(all_ids), "full", "generated distribution changed without its canonical source"
    groups = set(policy["always_groups"])
    direct = set()
    for path in paths:
        if matches(path, policy["full_paths"]):
            return list(all_ids), "full", f"shared or policy input changed: {path}"
        if path.startswith("tools/tests/test_") and path.endswith(".py"):
            module = "tools.tests." + Path(path).stem
            if module not in policy.get("known_test_modules", []):
                return list(all_ids), "full", f"new or unmapped test module changed: {path}"
            own = {test_id for test_id in all_ids if module_of(test_id) == module}
            if not own:
                return list(all_ids), "full", f"test module was deleted: {path}"
            direct.update(own)
            for name in policy.get("impact_groups", []):
                if any(matches(test_id, policy["groups"][name].get("tests", [])) for test_id in own):
                    groups.add(name)
            continue
        rules = [rule for rule in policy["rules"] if matches(path, rule["paths"])]
        if not rules:
            return list(all_ids), "full", f"unmapped input changed: {path}"
        for rule in rules:
            groups.update(rule["groups"])
    if root is not None:
        direct.update(dependency_tests(root, paths, all_ids))
    return sorted(set(group_ids(groups, policy, all_ids)) | direct), "impact", "explicit impact groups and Python dependency closure: " + ", ".join(sorted(groups))


def test_weight(test_id, durations, policy):
    value = durations.get(test_id, policy["module_seconds"].get(module_of(test_id), policy.get("default_seconds", 1.0)))
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise CIError(f"invalid test duration for {test_id}")
    return max(float(value), 0.001)


def fixture_startup(module, policy):
    value = policy.get("fixture_startup_seconds", {}).get(module, 0.0)
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise CIError(f"invalid fixture startup estimate: {module}")
    return float(value)


def estimated_seconds(ids, durations, policy):
    return round(sum(test_weight(test_id, durations, policy) for test_id in ids) +
                 sum(fixture_startup(module, policy) for module in {module_of(test_id) for test_id in ids}), 6)


def balanced_shards(ids, count, durations, policy):
    shards = [[] for _ in range(min(count, len(ids)))]
    totals = [0.0] * len(shards)
    modules = [set() for _ in shards]
    for position, test_id in enumerate(sorted(ids, key=lambda value: (-test_weight(value, durations, policy), value))):
        module = module_of(test_id)
        startup = fixture_startup(module, policy)
        # Seed every worker before considering fixture affinity; never create an empty shard.
        index = position if position < len(shards) else min(range(len(shards)), key=lambda value:
            (totals[value] + (0 if module in modules[value] else startup), value))
        shards[index].append(test_id)
        totals[index] += test_weight(test_id, durations, policy) + (0 if module in modules[index] else startup)
        modules[index].add(module)
    return [sorted(shard) for shard in shards]


def worker_partition(ids, shard_count, workers, durations, policy):
    """Balance one lane over its shard jobs and their worker processes.

    A process pays each module's fixture startup once, so the balance runs over
    every process. Shards then take whole processes round robin, which never
    leaves a shard or a process empty.
    """
    processes = balanced_shards(ids, shard_count * workers, durations, policy)
    groups = [[] for _ in range(min(shard_count, len(ids)))]
    for index, tests in enumerate(processes):
        groups[index % len(groups)].append(tests)
    return groups


def assignments(groups):
    """Each shard's sorted IDs and the worker that runs each of them."""
    shards, owners = [], []
    for group in groups:
        worker_of = {test_id: index for index, tests in enumerate(group) for test_id in tests}
        shard = sorted(worker_of)
        shards.append(shard)
        owners.append([worker_of[test_id] for test_id in shard])
    return shards, owners


def interpreter_fields(lane):
    """Matrix fields naming a pinned interpreter package; empty selects setup-python."""
    value = lane.get("interpreter")
    if value is None:
        return dict(NO_INTERPRETER)
    return {"interpreter_package": value["package"], "interpreter_version": value["version"],
            "interpreter_sha512": value["sha512"]}


def apple_launcher_lane(selected_ids, policy):
    if not any(test_id.startswith("tools.tests.test_vault_hook.") for test_id in selected_ids):
        return None
    name = policy.get("apple_launcher_lane")
    lane = policy["lanes"].get(name)
    if lane is None or lane["os"] != "macos-latest":
        raise CIError("selected vault-hook tests require an Apple launcher lane")
    return name


def matrix_rows(lanes, apple_lane):
    rows = [{"lane": name, "os": lane["os"], "python": lane["python"], "shard": index,
             "shards": len(lane["shards"]), "apple_launcher": name == apple_lane and index == 0,
             **interpreter_fields(lane)}
            for name, lane in lanes.items() for index in range(len(lane["shards"]))]
    if apple_lane is not None and sum(row["apple_launcher"] for row in rows) != 1:
        raise CIError("Apple launcher check must belong to exactly one selected shard")
    return rows


def make_plan(root, mode="full", base=None, head="HEAD", timings=None):
    policy = policy_at(root)
    ids, inventory_hash = inventory(root)
    source_sha = git(root, "rev-parse", head + "^{commit}").decode().strip()
    current = git(root, "rev-parse", "HEAD^{commit}").decode().strip()
    if source_sha != current:
        raise CIError("plan source must be the checked out commit")
    paths = changed_paths(root, base, source_sha) if mode == "impact" else []
    selected, actual_mode, reason = select_ids(mode, paths, policy, ids, root)
    timings = timings or {"schema_version": 1, "durations": {}}
    if timings.get("schema_version") != 1 or not isinstance(timings.get("durations"), dict):
        raise CIError("invalid timing history")
    lanes = {}
    for name, lane in sorted(policy["lanes"].items()):
        permitted = ids if lane.get("groups") == ["all"] else group_ids(lane["groups"], policy, ids)
        lane_ids = sorted(set(selected) & set(permitted))
        if not lane_ids:
            continue
        measured = timings["durations"].get(name, {})
        # Measured history wins; the policy's per-test estimates cover a plan
        # whose policy changed, since history is bound to the exact policy.
        durations = {**policy.get("test_seconds", {}).get(lane["os"], {}), **measured}
        groups = worker_partition(lane_ids, lane["shards"], lane.get("workers", 1), durations, policy)
        shards, owners = assignments(groups)
        worker_seconds = [[estimated_seconds(tests, durations, policy) for tests in group] for group in groups]
        lanes[name] = {"os": lane["os"], "python": lane["python"], "selected_ids": lane_ids, "shards": shards,
                       "workers": lane.get("workers", 1), "worker_assignments": owners,
                       "must_run_ids": sorted(set(lane_ids) & set(lane.get("required_tests", []))),
                       "estimated_shard_seconds": [max(seconds) for seconds in worker_seconds],
                       "estimated_worker_seconds": worker_seconds,
                       "measured_weights": len(set(lane_ids) & set(measured))}
        if "interpreter" in lane:
            lanes[name]["interpreter"] = lane["interpreter"]
    apple_lane = apple_launcher_lane(selected, policy)
    rows = matrix_rows(lanes, apple_lane)
    plan = {"schema_version": 1, "source_sha": source_sha,
            "source_tree": git(root, "rev-parse", source_sha + "^{tree}").decode().strip(),
            "policy_hash": digest(policy), "inventory_hash": inventory_hash,
            "requested_mode": mode, "mode": actual_mode, "base_sha": base,
            "changed_paths": paths, "selection_reason": reason, "selected_ids": selected, "lanes": lanes,
            "timing_provenance": {"sources": timings.get("sources", []),
                                  "fallback_reasons": timings.get("fallback_reasons", ["no history supplied"])},
            "has_tests": bool(rows), "apple_launcher": apple_lane is not None,
            "apple_launcher_lane": apple_lane,
            "matrix": {"include": rows or [{"lane": "noop", "os": "ubuntu-latest",
            "python": "3.14", "shard": 0, "shards": 0, "apple_launcher": False, **NO_INTERPRETER}]}}
    plan["plan_hash"] = digest(plan)
    validate_plan(plan, root)
    return plan


def validate_plan(plan, root=None):
    if plan.get("schema_version") != 1 or plan.get("plan_hash") != digest({key: value for key, value in plan.items() if key != "plan_hash"}):
        raise CIError("plan digest or schema is invalid")
    all_selected = plan.get("selected_ids", [])
    if all_selected != sorted(set(all_selected)):
        raise CIError("plan selection contains duplicate or unsorted IDs")
    if bool(plan.get("lanes")) != plan.get("has_tests"):
        raise CIError("plan matrix disagrees with selected lanes")
    apple_lane = plan.get("apple_launcher_lane")
    if plan.get("apple_launcher") is not (apple_lane is not None):
        raise CIError("Apple launcher selection differs from its lane")
    for name, lane in plan["lanes"].items():
        expected = lane["selected_ids"]
        flattened = [test_id for shard in lane["shards"] for test_id in shard]
        if not set(lane.get("must_run_ids", [])) <= set(expected):
            raise CIError(f"required native test is not selected: {name}")
        if not expected or expected != sorted(set(expected)) or sorted(flattened) != expected \
                or any(not shard for shard in lane["shards"]):
            raise CIError(f"shards are not a complete disjoint partition: {name}")
        if not set(expected) <= set(all_selected):
            raise CIError(f"lane contains an unselected test: {name}")
        validate_execution(name, lane)
    rows = matrix_rows(plan["lanes"], apple_lane)
    if any(not isinstance(row.get("apple_launcher"), bool) for row in plan["matrix"]["include"]):
        raise CIError("matrix Apple launcher flags must be boolean")
    if rows and sorted(rows, key=lambda row: (row["lane"], row["shard"])) != sorted(plan["matrix"]["include"], key=lambda row: (row["lane"], row["shard"])):
        raise CIError("matrix does not match the lane partitions")
    if root is not None:
        policy = policy_at(root)
        ids, inventory_hash = inventory(root)
        if digest(policy) != plan["policy_hash"] or inventory_hash != plan["inventory_hash"]:
            raise CIError("policy or inventory changed after planning")
        if git(root, "rev-parse", "HEAD^{tree}").decode().strip() != plan["source_tree"]:
            raise CIError("plan belongs to another source tree")
        if plan["requested_mode"] not in {"full", "impact", "reuse"}:
            raise CIError("unknown plan mode")
        paths = changed_paths(root, plan["base_sha"], plan["source_sha"]) if plan["requested_mode"] == "impact" else []
        expected, mode, reason = select_ids(plan["requested_mode"], paths, policy, ids, root)
        if expected != all_selected or mode != plan["mode"] or paths != plan["changed_paths"] or reason != plan["selection_reason"]:
            raise CIError("plan selection does not match the declared inputs")
        if apple_lane != apple_launcher_lane(all_selected, policy):
            raise CIError("Apple launcher lane differs from policy")
        for name, config in policy["lanes"].items():
            permitted = ids if config["groups"] == ["all"] else group_ids(config["groups"], policy, ids)
            expected = sorted(set(all_selected) & set(permitted))
            lane = plan["lanes"].get(name)
            if not expected:
                if lane is not None:
                    raise CIError(f"unexpected empty lane: {name}")
                continue
            if lane is None or lane["selected_ids"] != expected or lane["os"] != config["os"] \
                    or lane["python"] != config["python"] or len(lane["shards"]) != min(config["shards"], len(expected)) \
                    or lane.get("must_run_ids", []) != sorted(set(expected) & set(config.get("required_tests", []))):
                raise CIError(f"lane coverage differs from policy: {name}")
            if lane["workers"] != config.get("workers", 1) or lane.get("interpreter") != config.get("interpreter"):
                raise CIError(f"lane execution differs from policy: {name}")
        if set(plan["lanes"]) - set(policy["lanes"]):
            raise CIError("plan contains unknown lanes")


def validate_execution(name, lane):
    """Every shard splits into contiguous, non-empty worker processes."""
    workers, owners = lane.get("workers"), lane.get("worker_assignments")
    seconds = lane.get("estimated_worker_seconds")
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS or not isinstance(owners, list) \
            or not isinstance(seconds, list) or len(owners) != len(lane["shards"]) or len(seconds) != len(owners):
        raise CIError(f"worker partition is invalid: {name}")
    for shard, owner, estimates in zip(lane["shards"], owners, seconds):
        used = set(owner) if isinstance(owner, list) else set()
        if not isinstance(owner, list) or len(owner) != len(shard) or any(type(index) is not int for index in owner) \
                or sorted(used) != list(range(len(used))) or len(used) > workers \
                or not isinstance(estimates, list) or len(estimates) != len(used) \
                or any(type(value) not in {int, float} or not math.isfinite(value) or value < 0 for value in estimates):
            raise CIError(f"worker partition is invalid: {name}")
    if "interpreter" in lane and not valid_interpreter(lane["interpreter"], lane):
        raise CIError(f"pinned interpreter is invalid: {name}")


def available_cpus():
    """CPUs this process may use; affinity or a container quota can lower the count."""
    counter = getattr(os, "process_cpu_count", None)
    if counter is not None:
        return max(1, counter() or 1)
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


def worker_groups(lane, shard, limit=None):
    """Each process's tests; fewer CPUs than planned workers fold workers together.

    A group lists its tests in ID order, so every module and class stays
    contiguous and its fixtures start once per process.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise CIError("worker count must be positive")
    tests, owners = lane["shards"][shard], lane["worker_assignments"][shard]
    count = min(max(owners) + 1, available_cpus() if limit is None else limit)
    groups = [[] for _ in range(count)]
    for test_id, owner in zip(tests, owners):
        groups[owner % count].append(test_id)
    return groups


def path_alias(path, metadata=None):
    try:
        metadata = metadata if metadata is not None else path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def worker_temp_parent(root):
    parent = Path(tempfile.gettempdir()).resolve()
    candidate_root = root.resolve()
    if parent == candidate_root or candidate_root in parent.parents:
        raise CIError("worker temporary directory must be outside the candidate checkout; set TMPDIR/TMP/TEMP to an external directory")
    # Non-Git fixtures must remain non-Git when Git searches their ancestors.
    # Another checkout or linked worktree is just as unsafe as this candidate.
    if any((directory / ".git").exists() or path_alias(directory / ".git")
           for directory in (parent, *parent.parents)):
        raise CIError("worker temporary directory has a Git checkout ancestor")
    probe = subprocess.run(["git", "--no-replace-objects", "-C", str(parent), "rev-parse", "--git-dir"],
                           capture_output=True, check=False)
    if probe.returncode == 0:
        raise CIError("worker temporary directory belongs to a Git repository")
    return parent


def runtime_identity():
    return {"python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
            "os": platform.system(), "os_release": platform.release(), "machine": platform.machine(),
            "git_version": subprocess.check_output(["git", "--version"], text=True, encoding="utf-8").strip()}


def validate_runtime(runtime, lane):
    systems = {"ubuntu-latest": "Linux", "macos-latest": "Darwin", "windows-latest": "Windows"}
    if runtime.get("os") != systems[lane["os"]] or ".".join(runtime.get("python_version", "").split(".")[:2]) != lane["python"]:
        raise CIError("runtime does not match the planned operating system and Python version")
    for key in ("python_implementation", "os_release", "machine", "git_version"):
        if not isinstance(runtime.get(key), str) or not runtime[key]:
            raise CIError(f"missing runtime identity: {key}")


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def load_selected(root, selected, all_ids):
    sys.path.insert(0, str(root))
    loader = unittest.TestLoader()
    for module_name in sorted({module_of(test_id) for test_id in selected}):
        module = importlib.import_module(module_name)
        loaded = [test.id() for test in flatten(loader.loadTestsFromModule(module))]
        expected = [test_id for test_id in all_ids if module_of(test_id) == module_name]
        if sorted(loaded) != expected or loader.errors:
            raise CIError(f"runtime discovery differs from static inventory: {module_name}")
    suite = loader.loadTestsFromNames(selected)
    if sorted(test.id() for test in flatten(suite)) != sorted(selected) or loader.errors:
        raise CIError("selected test IDs could not be loaded exactly")
    return suite


def fixture_totals():
    totals = {}
    seen = set()
    for name in ("tools.tests.fixture_cache", "fixture_cache"):
        module = sys.modules.get(name)
        if module is None or id(module) in seen or not hasattr(module, "phase_totals"):
            continue
        seen.add(id(module))
        for phase, value in module.phase_totals().items():
            totals[phase] = totals.get(phase, 0.0) + value
    return totals


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        self.report = kwargs.pop("report")
        self.report_path = kwargs.pop("report_path")
        self.tripwire = kwargs.pop("tripwire", None)
        super().__init__(*args, **kwargs)
        self.starts = {}
        self.outcomes = {}
        self.details = {}
        self.skipped_subtests = {}
        self.fixture_starts = {}

    def startTest(self, test):
        self.starts[test.id()] = time.monotonic()
        self.fixture_starts[test.id()] = fixture_totals()
        self.outcomes[test.id()] = "success"
        super().startTest(test)

    def stopTest(self, test):
        # Calls from a class or module setup count for the test that follows it.
        reached = self.tripwire.reached() if self.tripwire is not None else []
        if reached:
            try:
                raise AssertionError("the test " + host_calls_text(reached))
            except AssertionError:
                self.addFailure(test, sys.exc_info())
        row = {"id": test.id(), "outcome": self.outcomes[test.id()],
               "seconds": round(time.monotonic() - self.starts[test.id()], 6)}
        phases = fixture_totals()
        row["fixture_seconds"] = {key: round(max(0.0, value - self.fixture_starts[test.id()].get(key, 0.0)), 6)
                                  for key, value in phases.items()}
        if test.id() in self.details:
            row["detail"] = self.details[test.id()]
        if test.id() in self.skipped_subtests:
            row["skipped_subtests"] = self.skipped_subtests[test.id()]
        self.report["tests"].append(row)
        write_json(self.report_path, self.report)
        super().stopTest(test)

    def addFailure(self, test, err):
        self.outcomes[test.id()] = "failure"
        self.details[test.id()] = self._exc_info_to_string(err, test)
        super().addFailure(test, err)

    def addError(self, test, err):
        self.outcomes[test.id()] = "error"
        self.details[test.id()] = self._exc_info_to_string(err, test)
        if test.id() not in self.starts:
            self.report.setdefault("errors", []).append({"id": test.id(), "detail": self.details[test.id()]})
        super().addError(test, err)

    def addSkip(self, test, reason):
        parent = getattr(test, "test_case", test)
        parent_id = parent.id()
        if test is not parent:
            self.skipped_subtests.setdefault(parent_id, []).append({"id": test.id(), "reason": reason})
        if self.outcomes.get(parent_id) == "success":
            self.outcomes[parent_id] = "skipped"
            self.details[parent_id] = reason
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.outcomes[test.id()] = "expected_failure"
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.outcomes[test.id()] = "unexpected_success"
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.outcomes[test.id()] = "failure"
            self.details[test.id()] = self._exc_info_to_string(err, test)
        super().addSubTest(test, subtest, err)


def planned_lane(plan, lane_name, shard):
    lane = plan["lanes"].get(lane_name)
    if lane is None or type(shard) is not int or not 0 <= shard < len(lane["shards"]):
        raise CIError("unknown lane or shard")
    return lane


def run_shard(root, plan, lane_name, shard, report_path, workers=None):
    """Run one shard in-process, or across worker processes when CPUs allow."""
    validate_plan(plan, root)
    lane = planned_lane(plan, lane_name, shard)
    groups = worker_groups(lane, shard, workers)
    runtime = runtime_identity()
    validate_runtime(runtime, lane)
    expected = lane["shards"][shard]
    report = {"schema_version": 1, "plan_hash": plan["plan_hash"], "source_tree": plan["source_tree"],
              "lane": lane_name, "shard": shard, "status": "running", "runtime": runtime, "tests": []}
    write_json(report_path, report)
    started = time.monotonic()
    try:
        if len(groups) == 1:
            ids, _hash = inventory(root)
            result, unattributed = run_guarded(lambda: load_selected(root, expected, ids),
                                               report, report_path)
            passed = result.wasSuccessful()
            problems = ["a class or module fixture " + host_calls_text(unattributed)] if unattributed else []
            report["workers"] = [{"worker": 0, "tests": len(report["tests"]),
                                  "status": "complete" if passed and not problems else "failed",
                                  "wall_seconds": round(time.monotonic() - started, 6)}]
        else:
            passed, problems = run_workers(root, plan, lane_name, shard, groups, report, runtime)
        complete = sorted(test["id"] for test in report["tests"]) == sorted(expected)
        required_ran = all(test["outcome"] == "success" for test in report["tests"]
                           if test["id"] in lane.get("must_run_ids", []))
        report["status"] = "complete" if passed and complete and required_ran and not problems else "failed"
        if problems:
            report["error"] = "; ".join(problems)
        if not required_ran:
            report["error"] = "mandatory native regression was skipped or did not pass"
        if not complete:
            report["error"] = "one or more planned tests did not finish"
    except (KeyboardInterrupt, SystemExit):
        report["status"] = "cancelled"
        write_json(report_path, report)
        raise
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        write_json(report_path, report)
        raise
    report["wall_seconds"] = round(time.monotonic() - started, 6)
    write_json(report_path, report)
    return 0 if report["status"] == "complete" else 1


def relay(stream, prefix, lock):
    """Copy a worker's output line by line, tagged, without decoding it."""
    for line in iter(stream.readline, b""):
        with lock:
            sys.stdout.flush()
            target = getattr(sys.stdout, "buffer", None)
            if target is None:
                sys.stdout.write(prefix + line.decode("utf-8", "replace"))
            else:
                target.write(prefix.encode() + line)
                target.flush()
    stream.close()


def say(lock, text):
    with lock:
        print(text, flush=True)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_workers(root, plan, lane_name, shard, groups, report, runtime):
    """Run each group in its own interpreter, then merge the exact accounting.

    Every worker gets its own TMPDIR, TMP and TEMP outside any Git checkout and
    applies its own host tripwire; its report stays outside the shard report.
    """
    lock = threading.Lock()
    # A short prefix keeps each worker's scratch path shorter than the default
    # Windows TEMP, so no fixture path gains characters toward the 260 limit.
    directory = Path(tempfile.mkdtemp(prefix="ciw-", dir=worker_temp_parent(root)))
    plan_path = directory / "plan.json"
    write_json(plan_path, plan)
    workers = []
    started = time.monotonic()
    try:
        for index in range(len(groups)):
            scratch = directory / str(index)
            scratch.mkdir()
            environment = {**os.environ, "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
                           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "worker", "--root", str(root),
                 "--plan", str(plan_path), "--plan-hash", plan["plan_hash"], "--lane", lane_name,
                 "--shard", str(shard), "--worker", str(index), "--workers", str(len(groups)),
                 "--report", str(scratch / "report.json")],
                cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            thread = threading.Thread(target=relay, args=(process.stdout, f"[worker {index}] ", lock), daemon=True)
            thread.start()
            workers.append((process, thread, scratch))
        say(lock, f"ci-tests: {lane_name} shard {shard}: {sum(map(len, groups))} tests in {len(groups)} workers")
        next_update = started + PROGRESS_SECONDS
        while any(process.poll() is None for process, _thread, _scratch in workers):
            if time.monotonic() >= next_update:
                finished = sum(process.poll() is not None for process, _thread, _scratch in workers)
                say(lock, f"ci-tests: {finished}/{len(workers)} workers finished after "
                          f"{time.monotonic() - started:.0f}s")
                next_update += PROGRESS_SECONDS
            time.sleep(0.1)
        for _process, thread, _scratch in workers:
            thread.join()
        return merge_workers(plan, lane_name, shard, groups, report, runtime,
                             [process.returncode for process, _thread, _scratch in workers],
                             [scratch / "report.json" for _process, _thread, scratch in workers])
    except BaseException:
        for process, _thread, scratch in workers:
            stop(process)
        merge_workers(plan, lane_name, shard, groups, report, runtime,
                      [process.returncode for process, _thread, _scratch in workers],
                      [scratch / "report.json" for _process, _thread, scratch in workers])
        raise
    finally:
        for process, thread, _scratch in workers:
            stop(process)
            thread.join(timeout=10)
        shutil.rmtree(directory, ignore_errors=True)


def merge_workers(plan, lane_name, shard, groups, report, runtime, codes, paths):
    """Fold worker reports into the shard report; any doubt fails the shard."""
    rows, errors, problems, summary = [], [], [], []
    passed = True
    for index, (tests, code, path) in enumerate(zip(groups, codes, paths)):
        try:
            value = read_json(path)
        except CIError:
            value = None
        allowed = set(tests)
        if not isinstance(value, dict) or value.get("plan_hash") != plan["plan_hash"] \
                or value.get("lane") != lane_name or value.get("shard") != shard or value.get("worker") != index \
                or value.get("runtime") != runtime or not isinstance(value.get("tests"), list) \
                or not isinstance(value.get("errors", []), list) \
                or any(not isinstance(row, dict) or row.get("id") not in allowed for row in value["tests"]):
            passed = False
            problems.append(f"worker {index} left no valid report (exit {code})")
            summary.append({"worker": index, "tests": 0, "status": "missing", "wall_seconds": None})
            continue
        rows.extend(value["tests"])
        errors.extend(value.get("errors", []))
        if code != 0 or value.get("status") != "complete":
            passed = False
            problems.append(f"worker {index} {value.get('status')} (exit {code})"
                            + (f": {value['error']}" if value.get("error") else ""))
        summary.append({"worker": index, "tests": len(value["tests"]), "status": value.get("status"),
                        "wall_seconds": value.get("wall_seconds")})
    if errors:
        report["errors"] = errors
    report["tests"] = sorted(rows, key=lambda row: row["id"])
    report["workers"] = summary
    return passed, problems


def run_worker(root, plan, plan_hash, lane_name, shard, worker, workers, report_path):
    """One worker process of a shard: exactly its planned group, under its own tripwire."""
    validate_plan(plan)
    if plan["plan_hash"] != plan_hash:
        raise CIError("worker plan differs from its shard's plan")
    lane = planned_lane(plan, lane_name, shard)
    runtime = runtime_identity()
    validate_runtime(runtime, lane)
    groups = worker_groups(lane, shard, workers)
    if len(groups) != workers or type(worker) is not int or not 0 <= worker < workers:
        raise CIError("unknown worker")
    expected = groups[worker]
    report = {"schema_version": 1, "plan_hash": plan["plan_hash"], "source_tree": plan["source_tree"],
              "lane": lane_name, "shard": shard, "worker": worker, "status": "running", "runtime": runtime,
              "tests": []}
    write_json(report_path, report)
    started = time.monotonic()
    try:
        ids, identity = inventory(root)
        if identity != plan["inventory_hash"]:
            raise CIError("test inventory changed after planning")
        result, unattributed = run_guarded(lambda: load_selected(root, expected, ids), report, report_path)
        complete = sorted(test["id"] for test in report["tests"]) == sorted(expected)
        report["status"] = "complete" if result.wasSuccessful() and complete and not unattributed else "failed"
        if unattributed:
            report["error"] = "a class or module fixture " + host_calls_text(unattributed)
        if not complete:
            report["error"] = "one or more planned tests did not finish"
    except (KeyboardInterrupt, SystemExit):
        report["status"] = "cancelled"
        write_json(report_path, report)
        raise
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        write_json(report_path, report)
        raise
    report["wall_seconds"] = round(time.monotonic() - started, 6)
    write_json(report_path, report)
    return 0 if report["status"] == "complete" else 1


def validate_measurements(report):
    wall = report.get("wall_seconds")
    if wall is not None and (type(wall) not in {float, int} or not math.isfinite(wall) or wall < 0):
        raise CIError("invalid worker wall duration")
    for test in report.get("tests", []):
        phases = test.get("fixture_seconds", {})
        if not isinstance(phases, dict) or any(
                not isinstance(name, str) or type(value) not in {int, float}
                or not math.isfinite(value) or value < 0 for name, value in phases.items()):
            raise CIError("invalid fixture phase duration")
    workers = report.get("workers", [])
    if not isinstance(workers, list) or any(
            not isinstance(row, dict) or type(row.get("worker")) is not int or type(row.get("tests")) is not int
            or row["tests"] < 0 or not isinstance(row.get("status"), str)
            or (row.get("wall_seconds") is not None and (type(row["wall_seconds"]) not in {int, float}
                                                       or not math.isfinite(row["wall_seconds"])
                                                       or row["wall_seconds"] < 0))
            for row in workers):
        raise CIError("invalid worker measurement")


def verify_reports(plan, reports):
    validate_plan(plan)
    if isinstance(reports, (str, Path)):
        reports = [read_json(path) for path in sorted(Path(reports).rglob("*.json"))]
    expected = {(name, index): ids for name, lane in plan["lanes"].items() for index, ids in enumerate(lane["shards"])}
    seen = set()
    durations = {}
    runtimes = {}
    measurements = []
    for report in reports:
        if not isinstance(report, dict):
            raise CIError("invalid shard report shape")
        key = (report.get("lane"), report.get("shard"))
        if key not in expected or key in seen:
            raise CIError(f"unexpected or duplicate shard report: {key}")
        seen.add(key)
        if report.get("schema_version") != 1 or report.get("plan_hash") != plan["plan_hash"] \
                or report.get("source_tree") != plan["source_tree"] or report.get("status") != "complete" \
                or report.get("errors") or report.get("error"):
            raise CIError(f"incomplete or mismatched shard report: {key}")
        validate_runtime(report.get("runtime", {}), plan["lanes"][key[0]])
        tests = report.get("tests", [])
        if not isinstance(tests, list) or any(not isinstance(test, dict) for test in tests):
            raise CIError("invalid shard test accounting shape")
        if sorted(test.get("id", "") for test in tests) != sorted(expected[key]):
            raise CIError(f"test IDs do not exactly cover the shard: {key}")
        for test in tests:
            seconds = test.get("seconds")
            if test.get("outcome") not in SUCCESS_OUTCOMES or isinstance(seconds, bool) \
                    or not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds < 0:
                raise CIError(f"invalid test outcome or duration: {test.get('id')}")
            if test["id"] in plan["lanes"][key[0]].get("must_run_ids", []) and test["outcome"] != "success":
                raise CIError(f"mandatory native regression did not pass: {test['id']}")
            durations.setdefault(key[0], {})[test["id"]] = seconds
        validate_measurements(report)
        measurements.append({"lane": key[0], "shard": key[1],
            "wall_seconds": report.get("wall_seconds"),
            "test_seconds": round(sum(test["seconds"] for test in tests), 6),
            "fixture_seconds": {phase: round(sum(test.get("fixture_seconds", {}).get(phase, 0.0) for test in tests), 6)
                for phase in {phase for test in tests for phase in test.get("fixture_seconds", {})}},
            "estimated_seconds": plan["lanes"][key[0]].get("estimated_shard_seconds", [None] * len(expected))[key[1]],
            "worker_wall_seconds": [row.get("wall_seconds") for row in report.get("workers", [])],
            "estimated_worker_seconds": plan["lanes"][key[0]].get("estimated_worker_seconds",
                                                                  [None] * len(expected))[key[1]]})
        identity = report["runtime"]
        if key[0] in runtimes and runtimes[key[0]] != identity:
            raise CIError(f"runtime changed between shards: {key[0]}")
        runtimes[key[0]] = identity
    if seen != set(expected):
        raise CIError("missing shard reports: " + str(sorted(set(expected) - seen)))
    return {"schema_version": 1, "policy_hash": plan["policy_hash"], "durations": durations, "runtimes": runtimes, "measurements": measurements}


def refresh_estimates(root, payload, minimum=ESTIMATE_MINIMUM_SECONDS):
    """Rewrite the policy's per-test estimates from verified durations.

    Only tests that took at least ``minimum`` seconds are kept, per runner
    operating system and at the slowest of its lanes; every other test weighs
    the policy default, which keeps the table short.
    """
    policy = read_json(root / POLICY_PATH)
    validated = policy_at(root)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 \
            or not isinstance(payload.get("durations"), dict):
        raise CIError("invalid durations artifact")
    if type(minimum) not in {int, float} or not math.isfinite(minimum) or minimum < 0.1:
        raise CIError("the estimate threshold must be at least 0.1 seconds")
    estimates = {}
    for lane, values in payload["durations"].items():
        if lane not in validated["lanes"] or not isinstance(values, dict):
            continue
        target = estimates.setdefault(validated["lanes"][lane]["os"], {})
        for test_id, seconds in values.items():
            if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 0 <= seconds <= 600:
                raise CIError(f"invalid duration for {test_id}")
            if seconds >= minimum:
                target[test_id] = max(target.get(test_id, 0.0), round(seconds, 1))
    policy["test_seconds"] = {system: dict(sorted(values.items())) for system, values in sorted(estimates.items())}
    path = root / POLICY_PATH
    path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    policy_at(root)
    return {system: len(values) for system, values in policy["test_seconds"].items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--mode", choices=("full", "impact", "reuse"), default="full")
    plan_parser.add_argument("--base")
    plan_parser.add_argument("--head", default="HEAD")
    plan_parser.add_argument("--timings", type=Path)
    plan_parser.add_argument("--output", type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--lane", required=True)
    run_parser.add_argument("--shard", type=int, required=True)
    run_parser.add_argument("--report", type=Path, required=True)
    run_parser.add_argument("--workers", type=int,
                            help="at most this many worker processes; default: the planned count, bounded by CPUs")
    worker_parser = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker_parser.add_argument("--root", type=Path, default=ROOT)
    worker_parser.add_argument("--plan", type=Path, required=True)
    worker_parser.add_argument("--plan-hash", required=True)
    worker_parser.add_argument("--lane", required=True)
    worker_parser.add_argument("--shard", type=int, required=True)
    worker_parser.add_argument("--worker", type=int, required=True)
    worker_parser.add_argument("--workers", type=int, required=True)
    worker_parser.add_argument("--report", type=Path, required=True)
    estimates_parser = sub.add_parser("estimates", help="refresh the policy's per-test estimates from durations")
    estimates_parser.add_argument("--durations", type=Path, required=True)
    estimates_parser.add_argument("--minimum", type=float, default=ESTIMATE_MINIMUM_SECONDS)
    for name in ("verify-reports", "verify"):
        verify_parser = sub.add_parser(name)
        verify_parser.add_argument("--plan", type=Path, required=True)
        verify_parser.add_argument("--reports", type=Path, required=True)
        verify_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        if args.command == "plan":
            plan = make_plan(ROOT, args.mode, args.base, args.head, read_json(args.timings) if args.timings else None)
            write_json(args.output, plan)
            print(json.dumps({"matrix": plan["matrix"], "has_tests": plan["has_tests"], "mode": plan["mode"],
                              "plan_hash": plan["plan_hash"], "selection_reason": plan["selection_reason"]}))
            return 0
        if args.command == "estimates":
            print(json.dumps(refresh_estimates(ROOT, read_json(args.durations), args.minimum), sort_keys=True))
            return 0
        if args.command in {"run", "worker"}:
            signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
        if args.command == "run":
            return run_shard(ROOT, read_json(args.plan), args.lane, args.shard, args.report, args.workers)
        if args.command == "worker":
            return run_worker(args.root.resolve(), read_json(args.plan), args.plan_hash, args.lane, args.shard,
                              args.worker, args.workers, args.report)
        plan = read_json(args.plan)
        validate_plan(plan, ROOT)
        result = verify_reports(plan, [read_json(path) for path in sorted(args.reports.rglob("*.json"))])
        if args.output:
            write_json(args.output, result)
        print("ci-tests: all planned tests and shards accounted for")
        return 0
    except (CIError, OSError, KeyError, TypeError) as error:
        print(f"ci-tests: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ci-tests: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
