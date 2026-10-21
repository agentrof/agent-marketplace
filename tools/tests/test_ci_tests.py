"""Coverage, isolation and failure accounting for the CI test planner."""
from __future__ import annotations

import copy
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
from unittest import mock

from tools import ci_tests
from tools.tests import git_fixture


HOST_PROCESS_FIXTURE_HEADER = """import os, pathlib, subprocess, tempfile, unittest
def integration(target):
    target._test_level = "integration"
    return target
@integration
class Tests(unittest.TestCase):
"""


@integration
class CITestPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(git_fixture.remove_temporary, self.temporary)
        self.root = Path(self.temporary.name)
        old_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), old_path))
        tests = self.root / "tools/tests"
        tests.mkdir(parents=True)
        self.module = "tools.tests.test_ci_fixture_" + self.root.name.replace("-", "_")
        self.test_file = tests / (self.module.rsplit(".", 1)[-1] + ".py")
        self.test_file.write_text("import unittest\nclass Tests(unittest.TestCase):\n"
                                  "    def test_alpha(self): pass\n"
                                  "    def test_beta(self): pass\n"
                                  "    @unittest.skip('intentional')\n"
                                  "    def test_skip(self): pass\n", encoding="utf-8")
        self.ids = [self.module + ".Tests.test_" + name for name in ("alpha", "beta", "skip")]
        runner = {"Linux": "ubuntu-latest", "Darwin": "macos-latest", "Windows": "windows-latest"}[platform.system()]
        self.policy = {"schema_version": 1, "groups": {"fixture": {"tests": [self.module + ".*"]}},
                       "generated_paths": ["dist/*"], "default_seconds": 1.0,
                       "python": ".".join(platform.python_version().split(".")[:2]),
                       "lanes": {"local": {"os": runner, "shards": 2, "groups": ["all"]}}}
        self.save_policy()
        git_fixture.init_repository(self.root)
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI tests")
        self.commit()
        self.addCleanup(sys.modules.pop, self.module, None)

    def git(self, *args):
        return ci_tests.git(self.root, *args).decode().strip()

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "fixture")

    def save_policy(self):
        path = self.root / ci_tests.POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.policy), encoding="utf-8")

    def plan(self, mode="full", **kwargs):
        return ci_tests.make_plan(self.root, mode, **kwargs)

    def reports(self, plan):
        return [{"schema_version": 1, "plan_hash": plan["plan_hash"], "source_tree": plan["source_tree"],
                 "lane": lane, "shard": index, "status": "complete", "runtime": ci_tests.runtime_identity(),
                 "tests": [{"id": name, "outcome": "success", "seconds": 1.0} for name in ids]}
                for lane, value in plan["lanes"].items() for index, ids in enumerate(value["shards"])]

    def rehash(self, plan):
        plan["plan_hash"] = ci_tests.digest({key: value for key, value in plan.items() if key != "plan_hash"})

    def test_planning_never_imports_test_modules_and_retains_skipped_tests(self):
        with mock.patch.object(ci_tests.importlib, "import_module", side_effect=AssertionError("imported")):
            plan = self.plan()
        self.assertEqual(plan["selected_ids"], self.ids)
        ci_tests.validate_plan(plan, self.root)

    def test_a_plan_cannot_drop_a_test_or_lane_even_after_rehash(self):
        original = self.plan()
        for change in ("missing", "selection", "lane"):
            with self.subTest(change=change):
                plan = copy.deepcopy(original)
                if change == "missing":
                    plan["lanes"]["local"]["shards"][0].pop()
                elif change == "selection":
                    plan["selected_ids"].pop()
                else:
                    plan["lanes"], plan["has_tests"] = {}, False
                self.rehash(plan)
                with self.assertRaises(ci_tests.CIError):
                    ci_tests.validate_plan(plan, self.root)

    def test_policy_inventory_and_tree_changes_invalidate_a_plan(self):
        plan = self.plan()
        self.test_file.write_text(self.test_file.read_text() + "\n# changed\n")
        with self.assertRaisesRegex(ci_tests.CIError, "inventory changed"):
            ci_tests.validate_plan(plan, self.root)
        plan = self.plan()
        self.policy["default_seconds"] = 2
        self.save_policy()
        with self.assertRaisesRegex(ci_tests.CIError, "policy or inventory"):
            ci_tests.validate_plan(plan, self.root)
        plan = self.plan()
        self.commit()
        with self.assertRaisesRegex(ci_tests.CIError, "another source tree"):
            ci_tests.validate_plan(plan, self.root)

    def test_reuse_emits_no_lanes_and_explicit_no_tests(self):
        plan = self.plan("reuse")
        self.assertFalse(plan["has_tests"])
        self.assertEqual(plan["lanes"], {})
        self.assertEqual(plan["selected_ids"], [])
        self.assertEqual(ci_tests.verify_reports(plan, [])["durations"], {})

    def test_reports_require_each_shard_once_with_exact_source_and_test_ids(self):
        plan = self.plan()
        reports = self.reports(plan)
        self.assertEqual(set(ci_tests.verify_reports(plan, reports)["durations"]["local"]), set(self.ids))
        kernels = copy.deepcopy(reports)
        kernels[-1]["runtime"]["os_release"] = "another kernel"
        self.assertEqual(set(ci_tests.verify_reports(plan, kernels)["durations"]["local"]), set(self.ids))
        for change in ("missing", "duplicate", "plan", "tests", "failure"):
            with self.subTest(change=change):
                altered = copy.deepcopy(reports)
                if change == "missing": altered.pop()
                elif change == "duplicate": altered.append(copy.deepcopy(altered[0]))
                elif change == "plan": altered[0]["plan_hash"] = "other"
                elif change == "tests": altered[0]["tests"].append(copy.deepcopy(altered[0]["tests"][0]))
                else: altered[0]["tests"][0]["outcome"] = "failure"
                with self.assertRaises(ci_tests.CIError): ci_tests.verify_reports(plan, altered)

    def test_a_shard_on_another_patch_release_than_planned_is_refused_by_name(self):
        release = platform.python_version()
        plan = self.plan(python_release=release)
        reports = self.reports(plan)
        other = release.rsplit(".", 1)[0] + "." + str(int(release.rsplit(".", 1)[1]) + 1)
        reports[-1]["runtime"]["python_version"] = other
        with self.assertRaisesRegex(ci_tests.CIError, f"runtime runs Python {re.escape(other)}, not the planned"
                                                      f" release {re.escape(release)}"):
            ci_tests.verify_reports(plan, reports)
        with mock.patch.object(ci_tests, "runtime_identity",
                               return_value=dict(ci_tests.runtime_identity(), python_version=other)), \
                self.assertRaisesRegex(ci_tests.CIError, "not the planned release"):
            ci_tests.run_shard(self.root, plan, "local", 0, self.root / "report.json")

    def test_selected_shard_runs_only_its_tests_and_writes_durable_report(self):
        plan = self.plan()
        path = self.root / "report.json"
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path), 0)
        report = ci_tests.read_json(path)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(sorted(test["id"] for test in report["tests"]), plan["lanes"]["local"]["shards"][0])

    def test_import_side_effect_cannot_add_a_test_without_accounting(self):
        self.test_file.write_text(self.test_file.read_text() + "\nsetattr(Tests, 'test_dynamic', lambda self: None)\n")
        plan = self.plan()
        path = self.root / "report.json"
        with self.assertRaisesRegex(ci_tests.CIError, "runtime discovery"):
            ci_tests.run_shard(self.root, plan, "local", 0, path)
        self.assertEqual(ci_tests.read_json(path)["status"], "failed")

    def test_interrupted_shard_never_leaves_complete_evidence(self):
        plan = self.plan()
        path = self.root / "report.json"
        with mock.patch.object(ci_tests, "load_selected", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                ci_tests.run_shard(self.root, plan, "local", 0, path)
        self.assertEqual(ci_tests.read_json(path)["status"], "cancelled")

    def test_subtest_failure_and_class_setup_failure_do_not_pass(self):
        cases = ["import unittest\nclass Tests(unittest.TestCase):\n"
                 "    def test_alpha(self):\n        with self.subTest(value=1): self.fail('broken')\n",
                 "import unittest\nclass Tests(unittest.TestCase):\n"
                 "    @classmethod\n    def setUpClass(cls): raise RuntimeError('broken')\n"
                 "    def test_alpha(self): pass\n"]
        for text in cases:
            with self.subTest(text=text):
                sys.modules.pop(self.module, None)
                self.test_file.write_text(text)
                plan = self.plan()
                path = self.root / "report.json"
                with mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path), 1)
                self.assertEqual(ci_tests.read_json(path)["status"], "failed")

    @unittest.skipIf(os.name != "posix", "the stand-in host binaries are POSIX shell scripts")
    def test_a_test_that_reaches_a_host_binary_through_the_runner_environment_fails(self):
        # The session that runs the suite names its own Claude Code and Codex
        # binaries; a project generator follows those names unless the test
        # pins fakes of its own.
        own = Path(tempfile.mkdtemp(prefix="ci-own-hosts-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(own, ignore_errors=True))
        log = own / "calls.log"
        for name in ("claude", "codex"):
            (own / name).write_text(f"#!/bin/sh\necho {name} \"$@\" >> {log}\nexit 0\n",
                                    encoding="utf-8")
            (own / name).chmod(0o755)
        probe = ("    def test_alpha(self):\n"
                 "        subprocess.run([os.environ['CLAUDE_CODE_EXECPATH'], '--version'],"
                 " capture_output=True)\n"
                 "        subprocess.run([os.environ['CODEX_CLI_PATH'], 'debug', 'models'],"
                 " capture_output=True)\n")
        pinned = ("    def test_beta(self):\n"
                  "        with tempfile.TemporaryDirectory() as raw:\n"
                  "            fake = pathlib.Path(raw) / 'claude'\n"
                  "            fake.write_text('#!/bin/sh\\necho 9.9.9\\n')\n"
                  "            fake.chmod(0o755)\n"
                  "            env = dict(os.environ, CLAUDE_CODE_EXECPATH=str(fake))\n"
                  "            subprocess.run([env['CLAUDE_CODE_EXECPATH'], '--version'], env=env,"
                  " capture_output=True, check=True)\n")
        header = HOST_PROCESS_FIXTURE_HEADER
        teardown = ("    @classmethod\n    def tearDownClass(cls):\n"
                    "        subprocess.run([os.environ['CLAUDE_CODE_EXECPATH'], '--version'],"
                    " capture_output=True)\n")
        developer = {"CLAUDE_CODE_EXECPATH": str(own / "claude"),
                     "CODEX_CLI_PATH": str(own / "codex"), "CLAUDE_PID": "4242"}
        self.policy["lanes"]["local"]["shards"] = 1
        self.save_policy()
        for name, text in (("a test", header + probe + pinned),
                           ("a class fixture", header + teardown + "    def test_alpha(self): pass\n")):
            with self.subTest(case=name), mock.patch.dict(os.environ, developer):
                sys.modules.pop(self.module, None)
                self.test_file.write_text(text, encoding="utf-8")
                plan = self.plan()
                path = self.root / "report.json"
                with mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path), 1)
                report = ci_tests.read_json(path)
                self.assertEqual(report["status"], "failed")
                rows = {row["id"].rsplit(".", 1)[-1]: row for row in report["tests"]}
                if name == "a test":
                    self.assertEqual(rows["test_alpha"]["outcome"], "failure")
                    detail = rows["test_alpha"]["detail"]
                    self.assertIn("claude --version", detail)
                    self.assertIn("codex debug models", detail)
                    self.assertIn("fixtures.isolated_hosts", detail)
                    self.assertEqual(rows["test_beta"]["outcome"], "success")
                else:
                    self.assertEqual(rows["test_alpha"]["outcome"], "success")
                    self.assertIn("claude --version", report["error"])
                # The developer's binaries never ran, and the runner left the
                # environment as it found it.
                self.assertFalse(log.exists())
                for key, value in developer.items():
                    self.assertEqual(os.environ.get(key), value)

    def test_independent_workers_cover_a_plan_and_merge_real_duration_reports(self):
        runner = self.root / "tools/ci_tests.py"
        runner.write_text(Path(ci_tests.__file__).read_text(), encoding="utf-8")
        plan = self.plan()
        plan_path = self.root / "plan.json"
        ci_tests.write_json(plan_path, plan)
        directory = self.root / "reports"
        workers = []
        for shard in range(len(plan["lanes"]["local"]["shards"])):
            workers.append(subprocess.Popen([
                sys.executable, str(runner), "run", "--plan", str(plan_path), "--lane", "local",
                "--shard", str(shard), "--report", str(directory / str(shard) / "report.json")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}))
        try:
            for worker in workers:
                output, errors = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, output + errors)
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait()
        timings = ci_tests.verify_reports(plan, directory)
        self.assertEqual(set(timings["durations"]["local"]), set(self.ids))
        self.assertEqual(timings["policy_hash"], plan["policy_hash"])

    def test_skipped_subtests_are_recorded_and_cannot_hide_a_failure(self):
        for failure_first in (False, True):
            with self.subTest(failure_first=failure_first):
                sys.modules.pop(self.module, None)
                body = "import unittest\nclass Tests(unittest.TestCase):\n    def test_alpha(self):\n"
                if failure_first:
                    body += "        with self.subTest(case='failed'): self.fail('failure before skip')\n"
                body += "        with self.subTest(case='native assertion'): self.skipTest('native assertion unavailable')\n"
                self.test_file.write_text(body)
                plan = self.plan()
                path = self.root / "report.json"
                with mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path),
                                     1 if failure_first else 0)
                report = ci_tests.read_json(path)
                row = report["tests"][0]
                self.assertEqual(row["outcome"], "failure" if failure_first else "skipped")
                self.assertEqual(row["skipped_subtests"][0]["reason"], "native assertion unavailable")
                if failure_first:
                    self.assertIn("failure before skip", row["detail"])
                if failure_first:
                    self.assertEqual(report["status"], "failed")
                    with self.assertRaises(ci_tests.CIError):
                        ci_tests.verify_reports(plan, [report])
                else:
                    self.assertEqual(ci_tests.verify_reports(plan, [report])["policy_hash"], plan["policy_hash"])

    def parallel_fixture(self, bodies=None, workers=3):
        """Six tests that each record the process, scratch and tripwire they ran under."""
        records = Path(tempfile.mkdtemp(prefix="ci-worker-records-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(records, ignore_errors=True))
        names = "abcdef"
        bodies = bodies or {}
        text = ("import json, os, sys, tempfile, unittest\nfrom pathlib import Path\n"
                f"RECORDS = Path({str(records)!r})\n"
                "def record(name):\n"
                "    (RECORDS / name).write_text(json.dumps({'pid': os.getpid(), 'temp': tempfile.gettempdir(),\n"
                "        'environment': [os.environ.get(key) for key in ('TMPDIR', 'TMP', 'TEMP')],\n"
                "        'claude': os.environ.get('CLAUDE_CODE_EXECPATH')}), encoding='utf-8')\n"
                "def integration(case):\n    return case\n"
                "@integration\nclass Tests(unittest.TestCase):\n")
        for name in names:
            text += f"    def test_{name}(self):\n        record({name!r})\n"
            text += "".join(f"        {line}\n" for line in bodies.get(name, []))
        self.test_file.write_text(text, encoding="utf-8")
        self.ids = sorted(f"{self.module}.Tests.test_{name}" for name in names)
        self.policy["lanes"]["local"].update(shards=1, workers=workers)
        self.save_policy()
        return records

    def worker_of(self, plan, test_id):
        lane = plan["lanes"]["local"]
        return lane["worker_assignments"][0][lane["shards"][0].index(test_id)]

    def test_parallel_shard_runs_each_test_once_in_isolated_workers(self):
        records = self.parallel_fixture()
        plan = self.plan()
        lane = plan["lanes"]["local"]
        self.assertEqual((lane["workers"], sorted(set(lane["worker_assignments"][0]))), (3, [0, 1, 2]))
        path = self.root / "report.json"
        with mock.patch("sys.stdout", io.StringIO()) as output:
            self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path, workers=3), 0)
        self.assertIn("[worker 2] ", output.getvalue())
        report = ci_tests.read_json(path)
        self.assertEqual(report["status"], "complete")
        self.assertEqual([row["id"] for row in report["tests"]], self.ids)
        self.assertEqual([row["worker"] for row in report["workers"]], [0, 1, 2])
        self.assertEqual([row["tests"] for row in report["workers"]],
                         [lane["worker_assignments"][0].count(index) for index in range(3)])
        self.assertEqual(set(ci_tests.verify_reports(plan, [report])["durations"]["local"]), set(self.ids))
        seen = {name: json.loads((records / name).read_text(encoding="utf-8")) for name in "abcdef"}
        by_worker = {}
        for name, value in seen.items():
            by_worker.setdefault(self.worker_of(plan, f"{self.module}.Tests.test_{name}"), []).append(value)
        self.assertEqual(sorted(by_worker), [0, 1, 2])
        temps, tripwires = set(), set()
        for values in by_worker.values():
            self.assertEqual(len({value["pid"] for value in values}), 1)
            self.assertEqual(len({value["temp"] for value in values}), 1)
            value = values[0]
            self.assertEqual(value["environment"], [value["temp"]] * 3)
            self.assertNotIn(self.root.resolve(), Path(value["temp"]).resolve().parents)
            self.assertIn(Path(value["temp"]).resolve(), Path(value["claude"]).resolve().parents)
            temps.add(value["temp"])
            tripwires.add(value["claude"])
        self.assertEqual((len(temps), len(tripwires)), (3, 3))
        self.assertFalse(any(Path(temp).exists() for temp in temps))

    def test_a_single_process_shard_holds_an_unmarked_test_to_the_unit_boundary(self):
        self.test_file.write_text(
            "import subprocess, unittest\n"
            "def integration(case):\n    return case\n"
            "class Tests(unittest.TestCase):\n"
            "    def test_unit(self):\n"
            "        subprocess.run(['git', '--version'], capture_output=True)\n"
            "    @integration\n"
            "    def test_marked(self):\n"
            "        subprocess.run(['git', '--version'], capture_output=True, check=True)\n", encoding="utf-8")
        self.ids = sorted(f"{self.module}.Tests.test_{name}" for name in ("marked", "unit"))
        self.policy["lanes"]["local"].update(shards=1, workers=1)
        self.save_policy()
        plan = self.plan()
        self.assertEqual(ci_tests.worker_groups(plan["lanes"]["local"], 0, 1), [self.ids])
        path = self.root / "report.json"
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path, workers=1), 1)
        rows = {row["id"].rsplit(".", 1)[-1]: row for row in ci_tests.read_json(path)["tests"]}
        self.assertEqual((rows["test_marked"]["outcome"], rows["test_unit"]["outcome"]), ("success", "failure"))
        self.assertIn("unit test started a process: git; keep it in process or mark it @integration",
                      rows["test_unit"]["detail"])

    def test_a_failed_or_crashed_worker_fails_the_shard(self):
        for case in ("failure", "crash"):
            with self.subTest(case=case):
                sys.modules.pop(self.module, None)
                body = ["self.fail('broken')"] if case == "failure" else ["os._exit(3)"]
                self.parallel_fixture({"f": body})
                plan = self.plan()
                broken = self.worker_of(plan, f"{self.module}.Tests.test_f")
                path = self.root / "report.json"
                with mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path, workers=3), 1)
                report = ci_tests.read_json(path)
                self.assertEqual(report["status"], "failed")
                rows = {row["id"].rsplit(".", 1)[-1]: row for row in report["tests"]}
                if case == "failure":
                    self.assertEqual(report["error"], f"worker {broken} failed (exit 1)")
                    self.assertEqual(rows["test_f"]["outcome"], "failure")
                    self.assertEqual(len(rows), 6)
                else:
                    self.assertNotIn("test_f", rows)
                    self.assertEqual(report["error"], "one or more planned tests did not finish")
                    self.assertNotEqual(report["workers"][broken]["status"], "complete")
                self.assertEqual(sum(row["status"] == "complete" for row in report["workers"]), 2)
                with self.assertRaises(ci_tests.CIError):
                    ci_tests.verify_reports(plan, [report])

    def test_policy_estimates_outweigh_defaults(self):
        runner = self.policy["lanes"]["local"]["os"]
        self.policy["test_seconds"] = {runner: {self.ids[0]: 40.0}}
        self.policy["lanes"]["local"]["workers"] = 1
        self.save_policy()
        estimated = self.plan()["lanes"]["local"]
        self.assertEqual(sorted(estimated["estimated_shard_seconds"]), [2.0, 40.0])


class CITestPlannerUnitTests(unittest.TestCase):
    POLICY = {"schema_version": 1, "groups": {}, "default_seconds": 1.0, "python": "3.12",
              "lanes": {"local": {"os": "ubuntu-latest", "shards": 2, "groups": ["all"]}}}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = copy.deepcopy(self.POLICY)
        self.save_policy()

    def save_policy(self):
        path = self.root / ci_tests.POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.policy), encoding="utf-8")

    def test_dynamic_discovery_and_unsupported_inheritance_fail_closed(self):
        test_file = self.root / "tools/tests/test_dynamic.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        for text in ("def load_tests(loader, tests, pattern): return tests\n",
                     "class Tests(Other):\n    def test_hidden(self): pass\n",
                     "import unittest\nif True:\n    class Tests(unittest.TestCase):\n        def test_hidden(self): pass\n",
                     "# Empty or dynamically populated test module\n"):
            with self.subTest(text=text):
                test_file.write_text(text, encoding="utf-8")
                with self.assertRaises(ci_tests.CIError):
                    ci_tests.inventory(self.root)

    def test_fewer_cpus_fold_planned_workers_in_a_fixed_order(self):
        lane = {"shards": [["t0", "t1", "t2", "t3", "t4"]], "worker_assignments": [[0, 1, 2, 3, 0]]}
        self.assertEqual(ci_tests.worker_groups(lane, 0, 2), [["t0", "t2", "t4"], ["t1", "t3"]])
        self.assertEqual(ci_tests.worker_groups(lane, 0, 8), [["t0", "t4"], ["t1"], ["t2"], ["t3"]])
        with mock.patch.object(ci_tests, "available_cpus", return_value=1):
            self.assertEqual(ci_tests.worker_groups(lane, 0), [["t0", "t1", "t2", "t3", "t4"]])

    def test_worker_partition_balances_processes_and_never_leaves_one_empty(self):
        ids = [f"tools.tests.test_{module}.Tests.test_{index}" for module in "ab" for index in range(5)]
        durations = {ids[0]: 9.0, ids[5]: 8.0}
        groups = ci_tests.worker_partition(ids, 2, 3, durations, self.policy)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(1 <= len(group) <= 3 and all(group) for group in groups))
        self.assertEqual(sorted(test for group in groups for tests in group for test in tests), ids)
        self.assertEqual(groups, ci_tests.worker_partition(list(reversed(ids)), 2, 3, durations, self.policy))
        heavy = [tests for group in groups for tests in group if ids[0] in tests or ids[5] in tests]
        self.assertEqual([len(tests) for tests in heavy], [1, 1])
        self.assertEqual(ci_tests.worker_partition(ids[:2], 4, 3, {}, self.policy), [[[ids[0]]], [[ids[1]]]])

    def test_tampered_worker_partitions_and_reports_are_refused(self):
        with self.assertRaisesRegex(ci_tests.CIError, "worker partition is invalid"):
            ci_tests.validate_execution("local", {"shards": [["a", "b"]], "workers": 2,
                                                  "worker_assignments": [[0, 2]],
                                                  "estimated_worker_seconds": [[1.0, 1.0]]})
        plan, runtime, groups = {"plan_hash": "p"}, {"python_version": "3.12.0"}, [["a"], ["b"]]
        for change in ("foreign", "missing"):
            with self.subTest(change=change):
                paths = []
                for index, tests in enumerate(groups):
                    value = {"plan_hash": "p", "lane": "local", "shard": 0, "worker": index, "runtime": runtime,
                             "status": "complete", "tests": [{"id": test, "outcome": "success"} for test in tests]}
                    if index == 1 and change == "foreign":
                        value["tests"].append({"id": "a", "outcome": "success"})
                    path = self.root / f"{change}-{index}.json"
                    if not (index == 1 and change == "missing"):
                        ci_tests.write_json(path, value)
                    paths.append(path)
                report = {}
                passed, problems = ci_tests.merge_workers(plan, "local", 0, groups, report, runtime, [0, 0], paths)
                self.assertFalse(passed)
                self.assertEqual(problems, ["worker 1 left no valid report (exit 0)"])
                self.assertEqual(report["workers"][1]["status"], "missing")

    def test_policy_worker_counts_are_bounded(self):
        self.policy["lanes"]["local"]["workers"] = 17
        self.save_policy()
        with self.assertRaisesRegex(ci_tests.CIError, "worker count"):
            ci_tests.policy_at(self.root)

    def test_estimates_keep_the_slowest_lane_per_system_above_the_threshold(self):
        ids = [f"tools.tests.test_a.Tests.test_{name}" for name in "abc"]
        self.policy["lanes"]["other"] = dict(self.policy["lanes"]["local"])
        self.save_policy()
        payload = {"schema_version": 1, "durations": {
            "local": {ids[0]: 6.04, ids[1]: 4.9, ids[2]: 7.0},
            "other": {ids[0]: 8.26, ids[2]: 2.0},
            "retired": {ids[1]: 50.0}}}
        self.assertEqual(ci_tests.refresh_estimates(self.root, payload), {"ubuntu-latest": 2})
        written = ci_tests.read_json(self.root / ci_tests.POLICY_PATH)
        self.assertEqual(written["test_seconds"], {"ubuntu-latest": {ids[0]: 8.3, ids[2]: 7.0}})
        self.assertEqual({key: value for key, value in written.items() if key != "test_seconds"}, self.policy)
        with self.assertRaises(ci_tests.CIError):
            ci_tests.refresh_estimates(self.root, {"schema_version": 1, "durations": {"local": {ids[0]: -1}}})
        self.policy["test_seconds"] = {"ubuntu-latest": {ids[0]: float("inf")}}
        self.save_policy()
        with self.assertRaisesRegex(ci_tests.CIError, "estimates"):
            ci_tests.policy_at(self.root)

    def test_fixture_measurement_is_a_delta_for_each_test(self):
        fixture = mock.Mock()
        fixture.phase_totals.side_effect = [{"seed_build": 3.0}, {"seed_build": 4.5, "seed_copy": .2}]
        report = {"tests": []}
        runner = ci_tests.TimedResult(unittest.runner._WritelnDecorator(io.StringIO()), True, 2,
                                     report=report, report_path=self.root / "fixture-report.json")
        test = unittest.FunctionTestCase(lambda: None)
        with mock.patch.dict(sys.modules, {"tools.tests.fixture_cache": fixture, "fixture_cache": fixture}):
            runner.startTest(test)
            runner.stopTest(test)
        self.assertEqual(report["tests"][0]["fixture_seconds"], {"seed_build": 1.5, "seed_copy": .2})

    def test_fixture_measurement_sums_distinct_module_stores_once_each(self):
        qualified, flat = mock.Mock(), mock.Mock()
        qualified.phase_totals.return_value = {"seed_build": 1.5, "seed_copy": .2}
        flat.phase_totals.return_value = {"seed_build": 2.0, "seed_validate": .1}
        with mock.patch.dict(sys.modules, {"tools.tests.fixture_cache": qualified, "fixture_cache": flat}):
            self.assertEqual(ci_tests.fixture_totals(),
                             {"seed_build": 3.5, "seed_copy": .2, "seed_validate": .1})


class TestLevelTests(unittest.TestCase):
    PROBE = ci_tests.ROOT / "tools/tests/unit-boundary-probe"

    def test_integration_marks_a_test_or_every_test_of_its_class(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tools/tests").mkdir(parents=True)
            (root / "tools/tests/test_marked.py").write_text(
                "import unittest\nfrom tools.tests import levels\nfrom tools.tests.levels import integration\n"
                "class Mixed(unittest.TestCase):\n    @integration\n    def test_git(self): pass\n"
                "    def test_rule(self): pass\n"
                "@integration\nclass Whole(unittest.TestCase):\n    def test_a(self): pass\n"
                "    def helper(self): pass\n"
                "class Qualified(unittest.TestCase):\n    @levels.integration\n    @unittest.skip('x')\n"
                "    def test_b(self): pass\n", encoding="utf-8")
            module = "tools.tests.test_marked."
            self.assertEqual(ci_tests.integration_ids(root),
                             {module + "Mixed.test_git", module + "Whole.test_a", module + "Qualified.test_b"})

    def remove_probe(self):
        if self.PROBE.is_dir():
            self.PROBE.rmdir()
        elif self.PROBE.exists() or self.PROBE.is_symlink():
            self.PROBE.unlink()

    def run_under_boundary(self, cases, marked=()):
        """Run *cases* as one TestCase through the worker's guarded runner; each test's report row."""
        if self.PROBE.exists() or self.PROBE.is_symlink():
            self.remove_probe()
        self.addCleanup(lambda: self.remove_probe() if self.PROBE.exists() or self.PROBE.is_symlink() else None)
        suite_class = type("BoundaryCases", (unittest.TestCase,), dict(cases))
        prefix = f"{suite_class.__module__}.{suite_class.__qualname__}."
        unaudited = tuple(getattr(os, name, None) for name in ci_tests.UnitBoundary.UNAUDITED)
        with tempfile.TemporaryDirectory() as raw, mock.patch("sys.stderr", io.StringIO()):
            report = {"tests": []}
            self.outside_temporary = Path(raw) / "beside-the-test"
            ci_tests.run_guarded(lambda: unittest.TestLoader().loadTestsFromTestCase(suite_class), report,
                                 Path(raw) / "report.json", {prefix + name for name in marked})
            self.assertFalse(self.outside_temporary.exists(), "a guarded test wrote beside its own directory")
        # The runner puts every replaced call back once the suite ends.
        self.assertEqual(tuple(getattr(os, name, None) for name in ci_tests.UnitBoundary.UNAUDITED), unaudited)
        written = self.PROBE.exists() or self.PROBE.is_symlink()
        if written:
            self.remove_probe()
        self.assertFalse(written, "a guarded test wrote outside its temporary directory")
        return {row["id"][len(prefix):]: row for row in report["tests"]}

    def test_a_unit_test_that_starts_a_process_or_writes_outside_its_temporary_directory_fails(self):
        import threading
        probe = self.PROBE
        released = threading.Event()
        self.addCleanup(released.set)

        def writes_inside(_case):
            with tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / "kept.txt"
                path.write_text("inside\n", encoding="utf-8")
                os.replace(path, Path(raw) / "moved.txt")

        def catches_its_write(_case):
            try:
                probe.write_text("outside\n", encoding="utf-8")
            except AssertionError:
                pass

        def by_descriptor(_case):
            descriptor = os.open(probe.parent, os.O_RDONLY)
            try:
                os.mkdir(probe.name, dir_fd=descriptor)
            finally:
                os.close(descriptor)

        def in_a_thread(_case):
            thread = threading.Thread(target=lambda: subprocess.run(["git", "--version"], capture_output=True))
            thread.start()
            thread.join()

        cases = {
            "test_starts_git": lambda _case: subprocess.run(["git", "--version"], capture_output=True),
            "test_starts_in_a_thread": in_a_thread,
            "test_leaves_a_thread_running": lambda _case: threading.Thread(target=released.wait, name="lingering",
                                                                           daemon=True).start(),
            "test_writes_outside": lambda _case: open(probe, "w", encoding="utf-8").close(),
            "test_catches_its_write": catches_its_write,
            "test_makes_a_directory_by_descriptor": by_descriptor,
            "test_writes_beside_its_own_directory": lambda _case: self.outside_temporary.write_text("x"),
            "test_writes_inside": writes_inside,
            "test_reads_the_repository": lambda _case: (ci_tests.ROOT / "README.md").read_bytes(),
        }
        rows = self.run_under_boundary(cases)
        released.set()
        passing = {"test_writes_inside", "test_reads_the_repository"}
        self.assertEqual({name: row["outcome"] for name, row in rows.items()},
                         {name: "success" if name in passing else "failure" for name in cases})
        self.assertIn("started a process", rows["test_starts_in_a_thread"]["detail"])
        self.assertIn("left a thread running", rows["test_leaves_a_thread_running"]["detail"])


if __name__ == "__main__":
    unittest.main()
