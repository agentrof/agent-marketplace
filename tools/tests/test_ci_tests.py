"""Coverage, isolation and failure accounting for the CI test planner."""
from __future__ import annotations

import copy
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import ci_tests
from tools.tests import git_fixture


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
                       "always_groups": ["fixture"],
                       "full_paths": ["tools/*", "plugins/*/*.md"],
                       "generated_paths": ["dist/*"], "generated_sources": ["plugins/*"],
                       "rules": [{"paths": ["README.md"], "groups": ["fixture"]},
                                 {"paths": ["plugins/compiler.py", "dist/*"], "groups": ["fixture"]}],
                       "module_seconds": {}, "default_seconds": 1.0,
                       "lanes": {"local": {"os": runner, "python": ".".join(platform.python_version().split(".")[:2]),
                                           "shards": 2, "groups": ["all"]}}}
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

    def test_shards_balance_durations_deterministically_and_preserve_exact_inventory(self):
        ids = ["a", "b", "c", "d", "e"]
        durations = dict(zip(ids, [8, 7, 3, 2, 1]))
        shards = ci_tests.balanced_shards(ids, 2, durations, self.policy)
        self.assertEqual(shards, ci_tests.balanced_shards(list(reversed(ids)), 2, durations, self.policy))
        self.assertEqual(sorted(item for shard in shards for item in shard), ids)
        totals = [sum(durations[item] for item in shard) for shard in shards]
        self.assertEqual(sorted(totals), [10, 11])
        with self.assertRaises(ci_tests.CIError):
            ci_tests.balanced_shards(ids, 2, {"a": float("nan")}, self.policy)

    def test_selected_vault_hook_requires_exactly_one_apple_worker(self):
        (self.root / "tools/tests/test_vault_hook.py").write_text(
            "import unittest\nclass Tests(unittest.TestCase):\n    def test_native(self): pass\n",
            encoding="utf-8")
        with self.assertRaisesRegex(ci_tests.CIError, "require an Apple launcher lane"):
            self.plan()
        self.policy["apple_launcher_lane"] = "local"
        self.policy["lanes"]["local"]["os"] = "macos-latest"
        self.save_policy()
        plan = self.plan()
        owner = [row for row in plan["matrix"]["include"] if row["apple_launcher"]]
        self.assertEqual([(row["lane"], row["shard"]) for row in owner], [("local", 0)])
        self.assertTrue(plan["apple_launcher"])
        for mutation in ("missing", "duplicate", "wrong-lane", "disabled", "nonboolean"):
            with self.subTest(mutation=mutation):
                altered = copy.deepcopy(plan)
                if mutation == "missing":
                    altered["matrix"]["include"][0]["apple_launcher"] = False
                elif mutation == "duplicate":
                    altered["matrix"]["include"][1]["apple_launcher"] = True
                elif mutation == "wrong-lane":
                    altered["apple_launcher_lane"] = "unselected"
                elif mutation == "nonboolean":
                    altered["matrix"]["include"][0]["apple_launcher"] = 1
                else:
                    altered["apple_launcher"] = False
                    altered["apple_launcher_lane"] = None
                    for row in altered["matrix"]["include"]:
                        row["apple_launcher"] = False
                self.rehash(altered)
                with self.assertRaises(ci_tests.CIError):
                    ci_tests.validate_plan(altered, self.root)
        self.policy["lanes"]["local"]["os"] = "ubuntu-latest"
        self.save_policy()
        with self.assertRaisesRegex(ci_tests.CIError, "require an Apple launcher lane"):
            self.plan()

    def test_unselected_apple_lane_cannot_drop_native_check(self):
        (self.root / "tools/tests/test_vault_hook.py").write_text(
            "import unittest\nclass Tests(unittest.TestCase):\n    def test_native(self): pass\n",
            encoding="utf-8")
        self.policy["apple_launcher_lane"] = "support"
        self.policy["groups"]["empty"] = {"tests": []}
        self.policy["lanes"]["support"] = {"os": "macos-latest", "python": "3.9", "shards": 1,
                                               "groups": ["empty"]}
        self.save_policy()
        with self.assertRaisesRegex(ci_tests.CIError, "exactly one selected shard"):
            self.plan()

    def test_partition_rejects_missing_duplicate_and_unknown_ids_even_after_rehash(self):
        original = self.plan()
        for change in ("missing", "duplicate", "unknown"):
            with self.subTest(change=change):
                plan = copy.deepcopy(original)
                shards = plan["lanes"]["local"]["shards"]
                if change == "missing":
                    shards[0].pop()
                elif change == "duplicate":
                    shards[1].append(shards[0][0])
                else:
                    shards[0][0] = "tools.tests.unknown.Tests.test_unknown"
                self.rehash(plan)
                with self.assertRaises(ci_tests.CIError):
                    ci_tests.validate_plan(plan, self.root)

    def test_selection_cannot_drop_a_lane_or_test_after_rehash(self):
        plan = self.plan()
        plan["selected_ids"].pop()
        self.rehash(plan)
        with self.assertRaises(ci_tests.CIError):
            ci_tests.validate_plan(plan, self.root)
        plan = self.plan()
        plan["lanes"] = {}
        plan["has_tests"] = False
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

    def test_impact_selection_preserves_nul_delimited_renames_and_unknown_fallback(self):
        path = self.root / "README.md"
        path.write_text("docs")
        self.commit()
        base = self.git("rev-parse", "HEAD")
        renamed = self.root / ("docs renamed.md" if os.name == "nt" else "docs\nrenamed.md")
        path.rename(renamed)
        self.commit()
        self.assertEqual(ci_tests.changed_paths(self.root, base, "HEAD"), ["README.md", renamed.name])
        with mock.patch.object(ci_tests, "git", return_value=b"README.md\0docs\nrenamed.md\0"):
            self.assertEqual(ci_tests.changed_paths(self.root, base, "HEAD"), ["README.md", "docs\nrenamed.md"])
        self.assertEqual(self.plan("impact", base=base)["mode"], "full")

    def test_unknown_shared_and_plugin_markdown_force_full_coverage(self):
        for path in ("new-runtime.py", "tools/ci_tests.py", "plugins/team/flows/deliver.md"):
            with self.subTest(path=path):
                selected, mode, _reason = ci_tests.select_ids("impact", [path], self.policy, self.ids)
                self.assertEqual(mode, "full")
                self.assertEqual(selected, self.ids)
        selected, mode, _reason = ci_tests.select_ids("impact", ["README.md"], self.policy, self.ids)
        self.assertEqual(mode, "impact")
        self.assertEqual(selected, self.ids)

    def test_generated_only_change_falls_back_but_canonical_pair_uses_mapping(self):
        self.assertEqual(ci_tests.select_ids("impact", ["dist/codex/file.py"], self.policy, self.ids)[1], "full")
        paths = ["plugins/compiler.py", "dist/codex/file.py"]
        self.assertEqual(ci_tests.select_ids("impact", paths, self.policy, self.ids)[1], "impact")

    def test_dynamic_discovery_and_unsupported_inheritance_fail_closed(self):
        for text in ("def load_tests(loader, tests, pattern): return tests\n",
                     "class Tests(Other):\n    def test_hidden(self): pass\n",
                     "import unittest\nif True:\n    class Tests(unittest.TestCase):\n        def test_hidden(self): pass\n",
                     "# Empty or dynamically populated test module\n"):
            self.test_file.write_text(text)
            with self.assertRaises(ci_tests.CIError):
                ci_tests.inventory(self.root)

    def test_reuse_emits_no_lanes_and_explicit_no_tests(self):
        plan = self.plan("reuse")
        self.assertFalse(plan["has_tests"])
        self.assertEqual(plan["lanes"], {})
        self.assertEqual(plan["selected_ids"], [])
        self.assertEqual(ci_tests.verify_reports(plan, [])["durations"], {})

    def test_an_unknown_or_retired_mode_is_refused_not_narrowed(self):
        # A release is a tag of a validated commit; no release test profile exists.
        for mode in ("release", "prepare", ""):
            with self.subTest(mode=mode), self.assertRaisesRegex(ci_tests.CIError, "unknown plan mode"):
                ci_tests.select_ids(mode, [], self.policy, self.ids)

    def test_reports_require_each_shard_once_with_exact_source_and_test_ids(self):
        plan = self.plan()
        reports = self.reports(plan)
        self.assertEqual(set(ci_tests.verify_reports(plan, reports)["durations"]["local"]), set(self.ids))
        for change in ("missing", "duplicate", "extra", "source", "plan", "tests", "running", "cancelled", "failure"):
            with self.subTest(change=change):
                altered = copy.deepcopy(reports)
                if change == "missing": altered.pop()
                elif change == "duplicate": altered.append(copy.deepcopy(altered[0]))
                elif change == "extra": altered[0]["shard"] = 999
                elif change == "source": altered[0]["source_tree"] = "other"
                elif change == "plan": altered[0]["plan_hash"] = "other"
                elif change == "tests": altered[0]["tests"].append(copy.deepcopy(altered[0]["tests"][0]))
                elif change == "failure": altered[0]["tests"][0]["outcome"] = "failure"
                else: altered[0]["status"] = change
                with self.assertRaises(ci_tests.CIError): ci_tests.verify_reports(plan, altered)

    def test_reports_reject_wrong_runtime_and_accept_directory_input(self):
        plan = self.plan()
        reports = self.reports(plan)
        reports[0]["runtime"]["python_version"] = "0.0.0"
        with self.assertRaises(ci_tests.CIError): ci_tests.verify_reports(plan, reports)
        directory = self.root / "reports"
        for index, report in enumerate(self.reports(plan)):
            ci_tests.write_json(directory / str(index) / "report.json", report)
        self.assertEqual(set(ci_tests.verify_reports(plan, directory)["durations"]["local"]), set(self.ids))

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
        header = "import os, pathlib, subprocess, tempfile, unittest\nclass Tests(unittest.TestCase):\n"
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

    def test_known_runtime_and_regression_changes_select_dependency_closure(self):
        policy = ci_tests.policy_at(ci_tests.ROOT)
        ids, _hash = ci_tests.inventory(ci_tests.ROOT)
        paths = ["plugins/software-engineering-team/scripts/delivery_git.py",
                 "tools/tests/test_delivery_git.py",
                 ".changes/delivery-regression.json",
                 "dist/codex/software-engineering-team/scripts/delivery_git.py",
                 "dist/claude/software-engineering-team/scripts/delivery_git.py"]
        selected, mode, _reason = ci_tests.select_ids("impact", paths, policy, ids, ci_tests.ROOT)
        self.assertEqual(mode, "impact")
        self.assertLess(len(selected), len(ids))
        self.assertTrue({test_id for test_id in ids if ".test_delivery_git." in test_id} <= set(selected))
        self.assertTrue({test_id for test_id in ids if ".test_delivery_compile." in test_id} <= set(selected))
        unknown = paths + ["tools/tests/test_new_unknown.py"]
        self.assertEqual(ci_tests.select_ids("impact", unknown, policy, ids, ci_tests.ROOT)[1], "full")

    def test_python_dependency_closure_follows_test_helpers_and_literal_cli_paths(self):
        (self.root / "plugins/scripts").mkdir(parents=True)
        (self.root / "plugins/scripts/producer.py").write_text("value = 1\n")
        (self.root / "plugins/scripts/consumer.py").write_text("import producer\n")
        (self.root / "tools/tests/helper.py").write_text("import consumer\n")
        self.test_file.write_text("import unittest\nimport helper\nclass Tests(unittest.TestCase):\n"
                                  "    def test_alpha(self): pass\n")
        selected = ci_tests.dependency_tests(self.root, ["plugins/scripts/producer.py"], self.ids)
        self.assertEqual(selected, set(self.ids))
        self.test_file.write_text("import unittest\nSCRIPT = 'producer.py'\nclass Tests(unittest.TestCase):\n"
                                  "    def test_alpha(self): pass\n")
        self.assertEqual(ci_tests.dependency_tests(self.root, ["plugins/scripts/producer.py"], self.ids), set(self.ids))

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
        replanned = ci_tests.make_plan(self.root, timings=timings)
        ci_tests.validate_plan(replanned, self.root)

    def test_mandatory_native_regressions_cannot_pass_as_skipped(self):
        self.policy["lanes"]["local"]["required_tests"] = [self.ids[-1]]
        self.save_policy()
        plan = self.plan()
        reports = self.reports(plan)
        for report in reports:
            for test in report["tests"]:
                if test["id"] == self.ids[-1]:
                    test["outcome"] = "skipped"
        with self.assertRaisesRegex(ci_tests.CIError, "mandatory native"):
            ci_tests.verify_reports(plan, reports)
        shard = next(index for index, ids in enumerate(plan["lanes"]["local"]["shards"]) if self.ids[-1] in ids)
        with mock.patch("sys.stderr", io.StringIO()):
            path = self.root / "report.json"
            self.assertEqual(ci_tests.run_shard(self.root, plan, "local", shard, path), 1)
        self.assertEqual(ci_tests.read_json(path)["status"], "failed")

    def test_skipped_subtests_are_recorded_and_cannot_hide_mandatory_assertions(self):
        for mandatory, failure_first in ((False, False), (True, False), (False, True)):
            with self.subTest(mandatory=mandatory, failure_first=failure_first):
                sys.modules.pop(self.module, None)
                body = "import unittest\nclass Tests(unittest.TestCase):\n    def test_alpha(self):\n"
                if failure_first:
                    body += "        with self.subTest(case='failed'): self.fail('failure before skip')\n"
                body += "        with self.subTest(case='native assertion'): self.skipTest('native assertion unavailable')\n"
                self.test_file.write_text(body)
                self.policy["lanes"]["local"]["required_tests"] = [self.ids[0]] if mandatory else []
                self.save_policy()
                plan = self.plan()
                path = self.root / "report.json"
                with mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(ci_tests.run_shard(self.root, plan, "local", 0, path),
                                     1 if mandatory or failure_first else 0)
                report = ci_tests.read_json(path)
                row = report["tests"][0]
                self.assertEqual(row["outcome"], "failure" if failure_first else "skipped")
                self.assertEqual(row["skipped_subtests"][0]["reason"], "native assertion unavailable")
                if failure_first:
                    self.assertIn("failure before skip", row["detail"])
                if mandatory or failure_first:
                    self.assertEqual(report["status"], "failed")
                    with self.assertRaises(ci_tests.CIError):
                        ci_tests.verify_reports(plan, [report])
                else:
                    self.assertEqual(ci_tests.verify_reports(plan, [report])["policy_hash"], plan["policy_hash"])

    def test_repository_windows_policy_keeps_native_regressions_on_both_python_versions(self):
        policy = ci_tests.policy_at(ci_tests.ROOT)
        ids, _hash = ci_tests.inventory(ci_tests.ROOT)
        required = {"tools.tests.test_ba_compile.EnterReviewTests.test_windows_junction_space_ancestor_is_rejected",
                    "tools.tests.test_delivery_git.DeliveryGitTests.test_receipt_lock_is_released_when_its_holder_dies"}
        for name in ("windows-current", "windows-minimum"):
            lane = policy["lanes"][name]
            self.assertEqual(lane["shards"], 8)
            self.assertTrue(required <= set(ci_tests.group_ids(lane["groups"], policy, ids)))

    def test_fixture_weights_keep_every_test_and_expose_estimates(self):
        ids = ["tools.tests.a.Tests.test_one", "tools.tests.a.Tests.test_two",
               "tools.tests.b.Tests.test_one", "tools.tests.b.Tests.test_two"]
        policy = dict(self.policy, fixture_startup_seconds={"tools.tests.a": 50, "tools.tests.b": 30})
        shards = ci_tests.balanced_shards(ids, 2, {}, policy)
        self.assertEqual(sorted(item for shard in shards for item in shard), ids)
        self.assertTrue(all(shards))
        self.assertGreater(ci_tests.estimated_seconds(shards[0], {}, policy), len(shards[0]))
        with self.assertRaises(ci_tests.CIError):
            ci_tests.fixture_startup("tools.tests.a", dict(policy, fixture_startup_seconds={"tools.tests.a": -1}))

    def test_unmapped_inventory_expands_impact_and_repository_mapping_is_complete(self):
        policy = ci_tests.policy_at(ci_tests.ROOT)
        ids, _ = ci_tests.inventory(ci_tests.ROOT)
        self.assertEqual(set(policy["known_test_modules"]), {ci_tests.module_of(test_id) for test_id in ids})
        incomplete = copy.deepcopy(policy)
        incomplete["known_test_modules"].pop()
        selected, mode, reason = ci_tests.select_ids("impact", ["README.md"], incomplete, ids)
        self.assertEqual(mode, "full")
        self.assertEqual(selected, ids)
        self.assertIn("mapping is incomplete", reason)

    def test_acceleration_contracts_have_impact_and_native_windows_coverage(self):
        policy = ci_tests.policy_at(ci_tests.ROOT)
        ids, _ = ci_tests.inventory(ci_tests.ROOT)
        modules = {"tools.tests.test_delivery_verification", "tools.tests.test_performance_contracts",
                   "tools.tests.test_task_inputs", "tools.tests.test_ci_local"}
        native = ci_tests.group_ids(["windows"], policy, ids)
        self.assertTrue({test_id for test_id in ids if ci_tests.module_of(test_id) in modules}.issubset(native))
        selected, _mode, _reason = ci_tests.select_ids("impact",
            ["plugins/software-engineering-team/scripts/delivery_git.py"], policy, ids, root=ci_tests.ROOT)
        self.assertTrue({test_id for test_id in ids if ci_tests.module_of(test_id) in modules}.issubset(selected))

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
        self.assertEqual(fixture.phase_totals.call_count, 2)

    def test_fixture_measurement_sums_distinct_module_stores_once_each(self):
        qualified, flat = mock.Mock(), mock.Mock()
        qualified.phase_totals.return_value = {"seed_build": 1.5, "seed_copy": .2}
        flat.phase_totals.return_value = {"seed_build": 2.0, "seed_validate": .1}
        with mock.patch.dict(sys.modules, {"tools.tests.fixture_cache": qualified, "fixture_cache": flat}):
            self.assertEqual(ci_tests.fixture_totals(),
                             {"seed_build": 3.5, "seed_copy": .2, "seed_validate": .1})
        qualified.phase_totals.assert_called_once_with()
        flat.phase_totals.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
