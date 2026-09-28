#!/usr/bin/env python3
"""Plan, shard and account for repository tests without importing them to plan."""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import importlib
import json
import math
import os
import platform
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "tools/data/ci-test-policy.json"
SUCCESS_OUTCOMES = {"success", "skipped", "expected_failure"}


class CIError(ValueError):
    pass


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
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False)
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


def policy_at(root):
    policy = read_json(root / POLICY_PATH)
    if policy.get("schema_version") != 1:
        raise CIError("unsupported CI test policy")
    required = {"groups", "lanes", "always_groups", "release_groups", "full_paths", "rules", "module_seconds"}
    if not required <= set(policy):
        raise CIError("CI test policy is incomplete")
    for name, lane in policy["lanes"].items():
        if not isinstance(lane.get("shards"), int) or not 1 <= lane["shards"] <= 16:
            raise CIError(f"invalid shard count for {name}")
        if lane.get("os") not in {"ubuntu-latest", "macos-latest", "windows-latest"}:
            raise CIError(f"invalid operating system for {name}")
        if not isinstance(lane.get("python"), str) or len(lane["python"].split(".")) != 2 \
                or not all(part.isdigit() for part in lane["python"].split(".")):
            raise CIError(f"Python lane must pin major.minor: {name}")
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
    if mode == "reuse":
        return [], "reuse", "caller must verify complete prior evidence for this source"
    if mode == "release":
        return group_ids(policy["release_groups"], policy, all_ids), "release", "caller must prove trusted deterministic release replay"
    if mode == "full":
        return list(all_ids), "full", "full coverage requested"
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


def balanced_shards(ids, count, durations, policy):
    shards = [[] for _ in range(min(count, len(ids)))]
    totals = [0.0] * len(shards)
    def weight(test_id):
        value = durations.get(test_id, policy["module_seconds"].get(module_of(test_id), policy.get("default_seconds", 1.0)))
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise CIError(f"invalid test duration for {test_id}")
        return max(float(value), 0.001)
    for test_id in sorted(ids, key=lambda value: (-weight(value), value)):
        index = min(range(len(shards)), key=lambda value: (totals[value], value))
        shards[index].append(test_id)
        totals[index] += weight(test_id)
    return [sorted(shard) for shard in shards]


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
             "shards": len(lane["shards"]), "apple_launcher": name == apple_lane and index == 0}
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
        shards = balanced_shards(lane_ids, lane["shards"], timings["durations"].get(name, {}), policy)
        lanes[name] = {"os": lane["os"], "python": lane["python"], "selected_ids": lane_ids, "shards": shards,
                       "must_run_ids": sorted(set(lane_ids) & set(lane.get("required_tests", [])))}
    apple_lane = apple_launcher_lane(selected, policy)
    rows = matrix_rows(lanes, apple_lane)
    plan = {"schema_version": 1, "source_sha": source_sha,
            "source_tree": git(root, "rev-parse", source_sha + "^{tree}").decode().strip(),
            "policy_hash": digest(policy), "inventory_hash": inventory_hash,
            "requested_mode": mode, "mode": actual_mode, "base_sha": base,
            "changed_paths": paths, "selection_reason": reason, "selected_ids": selected, "lanes": lanes,
            "has_tests": bool(rows), "apple_launcher": apple_lane is not None,
            "apple_launcher_lane": apple_lane,
            "matrix": {"include": rows or [{"lane": "noop", "os": "ubuntu-latest",
            "python": "3.14", "shard": 0, "shards": 0, "apple_launcher": False}]}}
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
        if plan["requested_mode"] not in {"full", "impact", "release", "reuse"}:
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
        if set(plan["lanes"]) - set(policy["lanes"]):
            raise CIError("plan contains unknown lanes")


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


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        self.report = kwargs.pop("report")
        self.report_path = kwargs.pop("report_path")
        super().__init__(*args, **kwargs)
        self.starts = {}
        self.outcomes = {}
        self.details = {}
        self.skipped_subtests = {}

    def startTest(self, test):
        self.starts[test.id()] = time.monotonic()
        self.outcomes[test.id()] = "success"
        super().startTest(test)

    def stopTest(self, test):
        row = {"id": test.id(), "outcome": self.outcomes[test.id()],
               "seconds": round(time.monotonic() - self.starts[test.id()], 6)}
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


def run_shard(root, plan, lane_name, shard, report_path):
    validate_plan(plan, root)
    lane = plan["lanes"].get(lane_name)
    if lane is None or not isinstance(shard, int) or not 0 <= shard < len(lane["shards"]):
        raise CIError("unknown lane or shard")
    runtime = runtime_identity()
    validate_runtime(runtime, lane)
    expected = lane["shards"][shard]
    report = {"schema_version": 1, "plan_hash": plan["plan_hash"], "source_tree": plan["source_tree"],
              "lane": lane_name, "shard": shard, "status": "running", "runtime": runtime, "tests": []}
    write_json(report_path, report)
    try:
        ids, _hash = inventory(root)
        suite = load_selected(root, expected, ids)
        result = unittest.TextTestRunner(verbosity=2, resultclass=lambda *args, **kwargs:
            TimedResult(*args, report=report, report_path=report_path, **kwargs)).run(suite)
        complete = sorted(test["id"] for test in report["tests"]) == sorted(expected)
        required_ran = all(test["outcome"] == "success" for test in report["tests"]
                           if test["id"] in lane.get("must_run_ids", []))
        report["status"] = "complete" if result.wasSuccessful() and complete and required_ran else "failed"
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
    write_json(report_path, report)
    return 0 if report["status"] == "complete" else 1


def verify_reports(plan, reports):
    validate_plan(plan)
    if isinstance(reports, (str, Path)):
        reports = [read_json(path) for path in sorted(Path(reports).rglob("*.json"))]
    expected = {(name, index): ids for name, lane in plan["lanes"].items() for index, ids in enumerate(lane["shards"])}
    seen = set()
    durations = {}
    runtimes = {}
    for report in reports:
        key = (report.get("lane"), report.get("shard"))
        if key not in expected or key in seen:
            raise CIError(f"unexpected or duplicate shard report: {key}")
        seen.add(key)
        if report.get("schema_version") != 1 or report.get("plan_hash") != plan["plan_hash"] \
                or report.get("source_tree") != plan["source_tree"] or report.get("status") != "complete" or report.get("errors"):
            raise CIError(f"incomplete or mismatched shard report: {key}")
        validate_runtime(report.get("runtime", {}), plan["lanes"][key[0]])
        tests = report.get("tests", [])
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
        identity = report["runtime"]
        if key[0] in runtimes and runtimes[key[0]] != identity:
            raise CIError(f"runtime changed between shards: {key[0]}")
        runtimes[key[0]] = identity
    if seen != set(expected):
        raise CIError("missing shard reports: " + str(sorted(set(expected) - seen)))
    return {"schema_version": 1, "policy_hash": plan["policy_hash"], "durations": durations, "runtimes": runtimes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--mode", choices=("full", "impact", "release", "reuse"), default="full")
    plan_parser.add_argument("--base")
    plan_parser.add_argument("--head", default="HEAD")
    plan_parser.add_argument("--timings", type=Path)
    plan_parser.add_argument("--output", type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--lane", required=True)
    run_parser.add_argument("--shard", type=int, required=True)
    run_parser.add_argument("--report", type=Path, required=True)
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
        if args.command == "run":
            signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
            return run_shard(ROOT, read_json(args.plan), args.lane, args.shard, args.report)
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
