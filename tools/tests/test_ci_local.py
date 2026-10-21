"""Exact staged-source identity and fail-closed local result reuse."""
from __future__ import annotations

import copy
import io
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from unittest import mock

from tools import ci_local, ci_tests
from tools.tests import git_fixture


WORKER_TEMP_FIXTURE = """import os, tempfile, unittest
from pathlib import Path
class Example(unittest.TestCase):
    def check_temporary(self, expected_worker):
        temporary_root = Path(tempfile.gettempdir()).resolve()
        checkout = Path.cwd().resolve()
        self.assertNotIn(checkout, (temporary_root, *temporary_root.parents))
        self.assertEqual(temporary_root.parent.name, expected_worker)
        self.assertTrue(temporary_root.name.startswith("unit-"), temporary_root)
        self.assertEqual(temporary_root.parent, Path(os.environ["TEMP"]).resolve())
        self.assertEqual(os.environ["TMPDIR"], os.environ["TMP"])
        self.assertFalse(any((parent / ".git").exists()
                             for parent in (temporary_root, *temporary_root.parents)),
                         "unit temporary directory has a Git ancestor")
    def test_one(self): self.check_temporary("0")
    def test_two(self): self.check_temporary("1")
"""


class LocalValidationTests(unittest.TestCase):
    def setUp(self):
        processors = mock.patch.object(ci_local.os, 'cpu_count', return_value=2)
        processors.start()
        self.addCleanup(processors.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(git_fixture.remove_temporary, self.temporary)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'tools/tests').mkdir(parents=True)
        (self.root / 'tools/data').mkdir()
        (self.root / 'tools/__init__.py').touch()
        (self.root / 'tools/tests/__init__.py').touch()
        (self.root / '.gitignore').write_text('.agentrof/\n__pycache__/\n')
        self.test_path = self.root / 'tools/tests/test_example.py'
        self.test_path.write_text('import unittest\nclass Example(unittest.TestCase):\n'
                                  '    def test_one(self): pass\n    def test_two(self): pass\n')
        for name in ('ci_local.py', 'ci_tests.py'):
            shutil.copyfile(ci_tests.ROOT / 'tools' / name, self.root / 'tools' / name)
        self.source = self.root / 'source.py'
        self.source.write_text('value = 1\n')
        (self.root / 'static.py').write_text('')
        (self.root / 'Makefile').write_text('static-check:\n\t$(PY) static.py\n')
        self.policy = {'schema_version': 1, 'groups': {}, 'lanes': {}, 'default_seconds': 1, 'python': '3.14'}
        ci_tests.write_json(self.root / ci_tests.POLICY_PATH, self.policy)
        ci_tests.write_json(self.root / ci_local.POLICY_PATH, {'schema_version': 1, 'max_age_seconds': 86400,
            'default_workers': 2, 'max_workers': 4, 'budget_estimated_seconds': 0, 'direct_tools': [],
            'environment_names': ['PATH', 'LANG'], 'environment_prefixes': ['PYTHON', 'GIT_'],
            'environment_ignored': ['GIT_EDITOR'], 'git_configuration_ignored': ['branch.*', 'remote.*.fetch'],
            'ignored_cache_paths': ['.agentrof/*', '**/__pycache__/*']})
        git_fixture.init_repository(self.root)
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.email', 'local@example.invalid')
        self.git('config', 'user.name', 'Local fixture')
        self.git('add', '--all')
        self.git('commit', '-qm', 'base')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def git(self, *args):
        return ci_tests.git(self.root, *args).decode().strip()

    def reports(self, plan):
        return [{'schema_version': 1, 'plan_hash': plan['plan_hash'], 'shard': n,
                 'runtime': plan['environment']['runtime'], 'status': 'complete',
                 'tests': [{'id': test, 'outcome': 'success', 'seconds': 0.1} for test in shard]}
                for n, shard in enumerate(plan['shards'])]

    def run_check(self, **kwargs):
        with mock.patch('sys.stdout', io.StringIO()):
            return ci_local.check(self.root, **kwargs)

    @integration
    def test_prior_branch_commits_and_staged_rename_deletion_are_in_the_candidate(self):
        self.source.write_text('value = 2\n')
        self.git('add', '--all')
        self.git('commit', '-qm', 'prior branch change')
        unusual = 'renamed ü.md' if os.name == 'nt' else 'renamed\tnew\nü.md'
        self.source.rename(self.root / unusual)
        self.git('add', '--all')
        plan = ci_local.make_plan(self.root)
        self.assertEqual(plan['candidate']['changed_paths'], sorted(['source.py', unusual]))
        self.assertNotEqual(plan['candidate']['base'], plan['candidate']['head'])

    @integration
    def test_missing_target_runs_full_without_fetching(self):
        plan = ci_local.make_plan(self.root, target='no-such-ref')
        self.assertEqual(plan['mode'], 'full')
        self.assertIsNone(plan['candidate']['base'])

    @integration
    def test_partial_staging_untracked_merge_flags_and_same_stat_edits_are_rejected(self):
        self.source.write_text('value = 2\n')
        with self.assertRaisesRegex(ci_tests.CIError, 'bytes differ'):
            ci_local.make_plan(self.root)
        self.git('add', '--all')
        timestamp = self.source.stat()
        self.source.write_text('value = 3\n')
        os.utime(self.source, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        with self.assertRaisesRegex(ci_tests.CIError, 'bytes differ'):
            ci_local.make_plan(self.root)
        self.git('add', '--all')
        for flag in ('--assume-unchanged', '--skip-worktree'):
            self.git('update-index', flag, 'source.py')
            with self.assertRaisesRegex(ci_tests.CIError, 'flags'):
                ci_local.make_plan(self.root)
            self.git('update-index', '--no-' + flag[2:], 'source.py')
        (self.root / 'untracked.txt').write_text('new source')
        with self.assertRaisesRegex(ci_tests.CIError, 'untracked'):
            ci_local.make_plan(self.root)

    @integration
    def test_a_failing_static_check_fails_the_attempt_after_the_workers_it_ran_beside(self):
        (self.root / 'static.py').write_text('import sys\nprint("static finding")\nsys.exit(3)\n')
        self.git('add', '--all')
        output = io.StringIO()
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)) as worker, \
                mock.patch('sys.stdout', output), self.assertRaises(subprocess.CalledProcessError) as raised:
            ci_local.check(self.root)
        self.assertNotEqual(raised.exception.returncode, 0)
        self.assertEqual(worker.call_count, 1)
        self.assertIn('static finding', output.getvalue())
        self.assertEqual(ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')['status'], 'failed')

    @integration
    def test_reuse_is_exact_fresh_static_and_does_not_extend_expiry(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)) as worker:
            first = self.run_check(full=True)
            second = self.run_check(full=True)
            self.assertEqual(worker.call_count, 1)
            self.assertTrue(second['reused_tests'])
            self.assertEqual(second['finished_at'], first['finished_at'])
            self.assertEqual(self.run_check(verify_only=True), second)
            self.run_check(fresh=True, full=True)
            self.assertEqual(worker.call_count, 2)
        plan = ci_local.make_plan(self.root, full=True)
        self.assertFalse(ci_local.reusable(first, plan, 86400, now=first['finished_at'] + 86401))
        changed = copy.deepcopy(first)
        changed['reports'][0]['tests'][0]['outcome'] = 'failure'
        self.assertFalse(ci_local.reusable(changed, plan, 86400))

    @integration
    def test_failed_rerun_invalidates_older_success_even_for_same_source(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
        with mock.patch.object(ci_local, 'execute_workers', side_effect=ci_tests.CIError('failed')):
            with self.assertRaises(ci_tests.CIError):
                self.run_check(fresh=True)
        with self.assertRaisesRegex(ci_tests.CIError, 'no current'):
            self.run_check(verify_only=True)

    @integration
    def test_real_workers_have_distinct_temporary_directories_and_account_for_all_tests(self):
        # The worker's unit fixture checks its layout in process; Git startup
        # is forbidden throughout a local unit worker, including class fixtures.
        self.test_path.write_text(WORKER_TEMP_FIXTURE)
        self.git('add', '--all')
        receipt = self.run_check()
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(sum(len(report['tests']) for report in receipt['reports']), 2)

    @integration
    def test_worker_temp_parent_rejects_candidate_and_other_git_ancestry(self):
        inside = self.root / '.agentrof/tmp'
        inside.mkdir(parents=True)
        with mock.patch.object(ci_local.tempfile, 'gettempdir', return_value=str(inside)):
            with self.assertRaisesRegex(ci_tests.CIError, 'outside the candidate'):
                ci_local.worker_temp_parent(self.root)
        with tempfile.TemporaryDirectory() as raw:
            other = Path(raw).resolve()
            git_fixture.init_repository(other)
            nested = other / 'nested/tmp'
            nested.mkdir(parents=True)
            with mock.patch.object(ci_local.tempfile, 'gettempdir', return_value=str(nested)):
                with self.assertRaisesRegex(ci_tests.CIError, 'Git checkout ancestor'):
                    ci_local.worker_temp_parent(self.root)

    @integration
    def test_prewarm_failure_invalidates_receipt_and_never_starts_workers(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
        original = subprocess.Popen
        started_workers = []
        def start(command, *args, **kwargs):
            if isinstance(command, list) and len(command) > 2 and command[2] == 'worker':
                started_workers.append(command)
            return original(command, *args, **kwargs)
        with mock.patch.object(ci_local, 'prewarm_stdlib', side_effect=subprocess.CalledProcessError(1, 'prewarm')), \
                mock.patch.object(ci_local.subprocess, 'Popen', side_effect=start):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_check(fresh=True)
        self.assertEqual(started_workers, [])
        self.assertEqual(ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')['status'], 'failed')

    @integration
    def test_edit_then_restore_during_workers_invalidates_attempt_and_previous_receipt(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
        original = self.source.read_bytes()
        metadata = self.source.stat()
        def restore_after_write(_root, plan, _cache):
            self.source.write_bytes(b'changed while tests ran\n')
            self.source.write_bytes(original)
            os.utime(self.source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            return self.reports(plan)
        with mock.patch.object(ci_local, 'execute_workers', side_effect=restore_after_write):
            with self.assertRaisesRegex(ci_tests.CIError, 'was written'):
                self.run_check(fresh=True)
        self.assertEqual(ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')['status'], 'failed')

    @integration
    def test_reused_test_receipt_still_runs_static_checks(self):
        (self.root / 'static.py').write_text('from pathlib import Path\np = Path(".agentrof/static-count")\n'
            'p.parent.mkdir(exist_ok=True)\np.write_text(str(int(p.read_text()) + 1) if p.exists() else "1")\n')
        self.git('add', '--all')
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
            self.run_check()
        self.assertEqual((self.root / '.agentrof/static-count').read_text(), '2')

    @integration
    def test_ignored_source_is_rejected_but_declared_runtime_cache_is_allowed(self):
        (self.root / '.gitignore').write_text('.agentrof/\nignored-source.py\n')
        self.git('add', '--all')
        (self.root / '.agentrof').mkdir()
        (self.root / '.agentrof/cache').write_text('cache')
        ci_local.make_plan(self.root)
        (self.root / 'ignored-source.py').write_text('source')
        with self.assertRaisesRegex(ci_tests.CIError, 'ignored source'):
            ci_local.make_plan(self.root)

    @integration
    def test_another_worktree_creating_and_deleting_branches_during_check_keeps_the_receipt(self):
        self.git('branch', 'packed')
        self.git('pack-refs', '--all')
        other = Path(self.temporary.name).parent / (Path(self.temporary.name).name + '-other')
        self.addCleanup(shutil.rmtree, other, True)
        original = subprocess.Popen
        def parallel_branch(command, *args, **kwargs):
            if 'static-check' in command:
                ci_tests.git(self.root, 'worktree', 'add', '-q', '-b', 'parallel', str(other), 'HEAD')
                ci_tests.git(self.root, 'config', 'branch.parallel.remote', 'origin')
                ci_tests.git(self.root, 'config', '--add', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
                ci_tests.git(self.root, 'branch', '-D', 'packed')
            return original(command, *args, **kwargs)
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)), \
                mock.patch.object(ci_local.subprocess, 'Popen', side_effect=parallel_branch):
            receipt = self.run_check()
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(self.run_check(verify_only=True), receipt)
        self.git('config', 'core.autocrlf', 'true')
        with self.assertRaisesRegex(ci_tests.CIError, 'Git configuration'):
            self.run_check(verify_only=True)

    def changed_fixture(self, budget=0):
        modules = {'test_helper': 'from tools import helper\n', 'test_uses_helper': 'from tools import helper\n',
                   'test_names_input': 'INPUT = "data.json"\n'}
        for name, header in modules.items():
            (self.root / f'tools/tests/{name}.py').write_text(
                header + 'import unittest\nclass Case(unittest.TestCase):\n    def test_it(self): pass\n')
        (self.root / 'tools/helper.py').write_text('value = 1\n')
        (self.root / 'data.json').write_text('{}\n')
        local = ci_tests.read_json(self.root / ci_local.POLICY_PATH)
        local['budget_estimated_seconds'] = budget
        ci_tests.write_json(self.root / ci_local.POLICY_PATH, local)
        self.git('add', '--all')
        self.git('commit', '-qm', 'changed fixture')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def selected_modules(self):
        plan = ci_local.make_plan(self.root)
        self.assertEqual(plan['mode'], 'changed')
        return sorted({ci_tests.module_of(test_id) for test_id in plan['selected_ids']}), plan

    @integration
    def test_changed_selection_runs_the_changes_own_tests_and_leaves_the_rest_to_ci(self):
        self.changed_fixture()
        # A changed module selects its own test module, not every module that imports it.
        (self.root / 'tools/helper.py').write_text('value = 2\n')
        self.git('add', '--all')
        self.assertEqual(self.selected_modules()[0], ['tools.tests.test_helper'])
        # A changed test module runs, and so does a test that names a changed input.
        (self.root / 'tools/tests/test_uses_helper.py').write_text(
            'import unittest\nclass Case(unittest.TestCase):\n    def test_it(self): self.assertTrue(True)\n')
        (self.root / 'data.json').write_text('{"changed": true}\n')
        self.git('add', '--all')
        self.assertEqual(self.selected_modules()[0], ['tools.tests.test_helper', 'tools.tests.test_names_input',
                                                      'tools.tests.test_uses_helper'])

    @integration
    def test_changed_selection_stops_at_the_budget_in_its_order(self):
        self.changed_fixture(budget=2)
        (self.root / 'tools/helper.py').write_text('value = 2\n')
        (self.root / 'tools/tests/test_uses_helper.py').write_text(
            'import unittest\nclass Case(unittest.TestCase):\n    def test_it(self): self.assertTrue(True)\n')
        (self.root / 'data.json').write_text('{"changed": true}\n')
        self.git('add', '--all')
        modules, plan = self.selected_modules()
        # Changed tests come first, then tests naming a changed input; the own test module waits for CI.
        self.assertEqual(modules, ['tools.tests.test_names_input', 'tools.tests.test_uses_helper'])
        self.assertIn('1 more left to pull request CI past the local budget', plan['selection_reason'])

    @integration
    def test_the_local_gate_runs_unit_tests_and_leaves_integration_tests_to_ci(self):
        self.changed_fixture(budget=100)
        (self.root / 'tools/tests/test_uses_helper.py').write_text(
            'import unittest\nfrom tools.tests.levels import integration\n'
            'class Case(unittest.TestCase):\n    def test_it(self): self.assertTrue(True)\n'
            '    @integration\n    def test_git(self): pass\n'
            '@integration\nclass Live(unittest.TestCase):\n    def test_live(self): pass\n')
        self.git('add', '--all')
        modules, plan = self.selected_modules()
        self.assertEqual(plan['selected_ids'], ['tools.tests.test_uses_helper.Case.test_it'])
        self.assertIn('2 integration tests left to pull request CI', plan['selection_reason'])
        full = ci_local.make_plan(self.root, full=True)
        self.assertNotIn('tools.tests.test_uses_helper.Case.test_git', full['selected_ids'])
        self.assertNotIn('tools.tests.test_uses_helper.Live.test_live', full['selected_ids'])
        self.assertIn('tools.tests.test_uses_helper.Case.test_it', full['selected_ids'])
        # A change whose own tests are all integration tests leaves the gate to its static checks.
        (self.root / 'tools/tests/test_uses_helper.py').write_text(
            'import unittest\nfrom tools.tests.levels import integration\n'
            '@integration\nclass Live(unittest.TestCase):\n    def test_live(self): self.assertTrue(True)\n')
        self.git('add', '--all')
        _modules, plan = self.selected_modules()
        self.assertEqual((plan['selected_ids'], plan['shards']), ([], []))
        ci_local.validate_plan(plan)
        self.assertEqual(ci_local.verify_reports(plan, []), [])

    def write_module(self, name, text):
        path = self.root / f'tools/tests/{name}.py'
        path.write_text(text)
        return path

    def commit_base(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'base')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def selected_ids(self):
        self.git('add', '--all')
        return ci_local.make_plan(self.root)['selected_ids']

    @integration
    def test_a_changed_test_method_selects_that_method_and_other_code_its_module(self):
        self.changed_fixture()
        module = ('import unittest\nclass Case(unittest.TestCase):\n    def setUp(self):\n        self.value = 1\n'
                  '    def test_first(self):\n        self.assertEqual(self.value, 1)\n'
                  '    def test_second(self):\n        self.assertTrue(True)\n')
        path = self.write_module('test_methods', module)
        self.commit_base()
        path.write_text(module.replace('self.assertTrue(True)', 'self.assertFalse(False)'))
        self.assertEqual(self.selected_ids(), ['tools.tests.test_methods.Case.test_second'])
        path.write_text(module + '    def test_third(self):\n        pass\n')
        self.assertEqual(self.selected_ids(), ['tools.tests.test_methods.Case.test_third'])
        path.write_text(module.replace('self.value = 1', 'self.value = 1  # shared'))
        self.assertEqual(self.selected_ids(), ['tools.tests.test_methods.Case.test_first',
                                               'tools.tests.test_methods.Case.test_second'])

    @integration
    def test_a_named_input_selects_the_methods_that_name_it_or_the_module_that_does(self):
        self.changed_fixture()
        self.write_module('test_inside', 'import unittest\nclass Case(unittest.TestCase):\n'
                          '    def test_reads(self):\n        self.assertTrue("data.json")\n'
                          '    def test_other(self):\n        pass\n')
        self.commit_base()
        (self.root / 'data.json').write_text('{"changed": true}\n')
        selected = self.selected_ids()
        self.assertIn('tools.tests.test_inside.Case.test_reads', selected)
        self.assertNotIn('tools.tests.test_inside.Case.test_other', selected)
        # test_names_input names it at module level, so its whole module runs.
        self.assertIn('tools.tests.test_names_input.Case.test_it', selected)

    @integration
    def test_own_module_tests_that_name_a_changed_function_run_first(self):
        self.changed_fixture(budget=1)
        source = 'def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n'
        (self.root / 'tools/helper.py').write_text(source)
        self.write_module('test_helper', 'import unittest\nfrom tools import helper\n'
                          'class Case(unittest.TestCase):\n'
                          '    def test_alpha(self):\n        self.assertEqual(helper.alpha(), 1)\n'
                          '    def test_beta(self):\n        self.assertEqual(helper.beta(), 2)\n')
        self.commit_base()
        (self.root / 'tools/helper.py').write_text(source.replace('return 2', 'return 2  # changed'))
        plan_ids = self.selected_ids()
        self.assertEqual(plan_ids, ['tools.tests.test_helper.Case.test_beta'])

    @integration
    def test_a_changed_test_helper_selects_the_modules_that_import_it(self):
        self.changed_fixture()
        support = self.root / 'tools/tests/support.py'
        support.write_text('VALUE = 1\n')
        self.write_module('test_uses_support', 'import unittest\nfrom tools.tests import support\n'
                          'class Case(unittest.TestCase):\n    def test_it(self):\n'
                          '        self.assertEqual(support.VALUE, 1)\n')
        self.commit_base()
        support.write_text('VALUE = 1  # changed\n')
        self.assertEqual(self.selected_ids(), ['tools.tests.test_uses_support.Case.test_it'])

    @integration
    def test_a_failed_run_names_its_failing_tests_last_and_keeps_the_worker_logs(self):
        self.test_path.write_text('import unittest\nclass Example(unittest.TestCase):\n'
                                  '    def test_one(self): self.fail("broken on purpose")\n'
                                  '    def test_two(self): pass\n')
        self.git('add', '--all')
        output = io.StringIO()
        with mock.patch('sys.stdout', output), self.assertRaisesRegex(ci_tests.CIError, 'worker logs kept at'):
            ci_local.check(self.root)
        lines = output.getvalue().strip().splitlines()
        self.assertIn('tools.tests.test_example.Example.test_one', lines[-2])
        kept = Path(lines[-1].removeprefix('ci-local: worker logs kept at '))
        self.assertEqual(kept, self.root / ci_local.CACHE_PATH / 'last-failure')
        self.assertTrue(any('broken on purpose' in path.read_text() for path in kept.glob('*/output.log')))
        # The next passing run removes them.
        self.test_path.write_text('import unittest\nclass Example(unittest.TestCase):\n'
                                  '    def test_one(self): pass\n    def test_two(self): pass\n')
        self.git('add', '--all')
        self.run_check()
        self.assertFalse(kept.exists())

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('git') == '/usr/bin/git'
                         and shutil.which('xcrun'), 'needs the macOS git trampoline')
    @integration
    def test_the_direct_git_keeps_its_templates_and_helpers(self):
        local = ci_tests.read_json(self.root / ci_local.POLICY_PATH)
        local['direct_tools'] = ['git']
        ci_tests.write_json(self.root / ci_local.POLICY_PATH, local)
        with tempfile.TemporaryDirectory() as raw:
            git = ci_local.direct_tool_directory(self.root, Path(raw)) / 'git'
            repository = Path(raw) / 'repository'
            created = subprocess.run([str(git), 'init', '-q', str(repository)], capture_output=True, text=True)
            self.assertEqual((created.returncode, created.stderr), (0, ''))
            self.assertTrue((repository / '.git/info/exclude').is_file())
            helpers = Path(subprocess.run([str(git), '--exec-path'], capture_output=True, text=True,
                                          check=True).stdout.strip())
            self.assertTrue((helpers / 'git-upload-pack').exists())

    @integration
    def test_a_requested_full_suite_is_what_verify_checks(self):
        self.changed_fixture()
        self.source.write_text('value = 2\n')
        self.git('add', '--all')
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            receipt = self.run_check(full=True)
        self.assertTrue(receipt['full'])
        self.assertEqual(receipt['selected_count'], len(ci_tests.inventory(self.root)[0]))
        self.assertEqual(self.run_check(verify_only=True), receipt)

    @integration
    def test_verify_without_jobs_takes_the_worker_count_check_used(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            receipt = self.run_check(jobs=1, full=True)
        self.assertEqual((receipt['jobs'], receipt['worker_count']), (1, 1))
        self.assertEqual(self.run_check(verify_only=True), receipt)
        with self.assertRaisesRegex(ci_tests.CIError, 'no current'):
            self.run_check(verify_only=True, jobs=2)

    @integration
    def test_verify_ignores_host_session_variables_and_names_a_changed_bound_one(self):
        with mock.patch.dict(os.environ, {'PYTHONPATH': 'first-secret-path'}):
            with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
                receipt = self.run_check()
            session = {'CLAUDE_CODE_SESSION_ID': 'another-session', 'CODEX_THREAD_ID': 'another-thread',
                       'GIT_EDITOR': 'another-editor', 'TMPDIR': os.environ.get('TMPDIR', tempfile.gettempdir())}
            with mock.patch.dict(os.environ, session):
                self.assertEqual(self.run_check(verify_only=True), receipt)
        self.assertNotIn('first-secret-path', (self.root / ci_local.CACHE_PATH / 'latest.json').read_text())
        with mock.patch.dict(os.environ, {'PYTHONPATH': 'second-secret-path'}):
            with self.assertRaises(ci_tests.CIError) as raised:
                self.run_check(verify_only=True)
        self.assertIn('changed: PYTHONPATH)', str(raised.exception))
        self.assertNotIn('secret-path', str(raised.exception))

class LocalValidationUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'tools/data').mkdir(parents=True)
        ci_tests.write_json(self.root / ci_local.POLICY_PATH, {'schema_version': 1, 'max_age_seconds': 86400,
            'default_workers': 2, 'max_workers': 4, 'budget_estimated_seconds': 0, 'direct_tools': ['git'],
            'environment_names': [], 'environment_prefixes': [], 'environment_ignored': [],
            'git_configuration_ignored': []})

    def plan(self):
        plan = {'schema_version': 1, 'authority': 'local_only', 'environment': {'runtime': {'python': '3'}},
                'selected_ids': ['a', 'b', 'c'], 'shards': [['a', 'b'], ['c']]}
        plan['plan_hash'] = ci_tests.digest(plan)
        return plan

    def reports(self, plan):
        return [{'schema_version': 1, 'plan_hash': plan['plan_hash'], 'shard': n,
                 'runtime': plan['environment']['runtime'], 'status': 'complete',
                 'tests': [{'id': test, 'outcome': 'success', 'seconds': 0.1} for test in shard]}
                for n, shard in enumerate(plan['shards'])]

    def test_worker_accounting_rejects_missing_duplicate_partial_failed_and_nonfinite_reports(self):
        plan = self.plan()
        self.assertEqual(len(ci_local.verify_reports(plan, self.reports(plan))), 2)
        for change in ('missing', 'duplicate', 'partial', 'wrong_runtime', 'failure', 'nan', 'error', 'fixture'):
            with self.subTest(change=change):
                reports = self.reports(plan)
                if change == 'missing': reports.pop()
                elif change == 'duplicate': reports[-1] = copy.deepcopy(reports[0])
                elif change == 'partial': reports[0]['tests'] = []
                elif change == 'wrong_runtime': reports[0]['runtime'] = {}
                elif change == 'failure': reports[0]['tests'][0]['outcome'] = 'failure'
                elif change == 'nan': reports[0]['tests'][0]['seconds'] = float('nan')
                elif change == 'error': reports[0]['error'] = 'worker exception'
                else: reports[0]['tests'][0]['fixture_seconds'] = {'seed': -1}
                with self.assertRaises(ci_tests.CIError):
                    ci_local.verify_reports(plan, reports)

    def test_workers_are_balanced_with_the_full_suite_lanes_measured_estimates(self):
        heavy, ids = 'test_one', ['test_one', 'test_three', 'test_two']
        policy = {'default_seconds': 1, 'lanes': {'full': {'os': 'ubuntu-latest', 'groups': ['all']}},
                  'test_seconds': {'ubuntu-latest': {heavy: 50.0}}}
        weights = ci_local.estimate_weights(policy, 'macos-latest')
        self.assertEqual(weights, {heavy: 50.0})
        self.assertEqual(ci_tests.balanced_shards(ids, 2, weights, policy), [[heavy], ['test_three', 'test_two']])

    def test_macos_workers_call_the_tool_its_xcrun_trampoline_resolves(self):
        real_prefix = self.root / 'developer/usr'
        real = real_prefix / 'bin/git'
        for folder in ('bin', 'libexec/git-core', 'share/git-core/templates'):
            (real_prefix / folder).mkdir(parents=True)
        real.write_text('#!/bin/sh\n')
        real.chmod(0o755)
        resolved = subprocess.CompletedProcess(['xcrun'], 0, stdout=str(real) + '\n', stderr='')
        directory = self.root / 'scratch'
        with mock.patch.object(ci_local.subprocess, 'run', return_value=resolved) as xcrun:
            for system, found, expected in (('Linux', '/usr/bin/git', None),
                                            ('Darwin', '/opt/homebrew/bin/git', None),
                                            ('Darwin', '/usr/bin/git', directory / 'direct-tools/bin')):
                with self.subTest(system=system, found=found), \
                        mock.patch.object(ci_tests.platform, 'system', return_value=system), \
                        mock.patch.object(ci_local.shutil, 'which', return_value=found):
                    self.assertEqual(ci_local.direct_tool_directory(self.root, directory), expected)
            self.assertEqual((directory / 'direct-tools/bin/git').readlink(), real)
            # Helpers and templates resolve beside the bin directory, as they do beside the real one.
            for sibling in ('libexec', 'share'):
                self.assertEqual((directory / 'direct-tools' / sibling).readlink(), real_prefix / sibling)
            xcrun.assert_called_once_with(['xcrun', '--find', 'git'], capture_output=True, text=True, check=False)

    def test_every_variable_the_source_reads_is_bound_or_named_as_unbound(self):
        # A variable the tools read changes what the tests do, so the receipt binds it.
        # Workers set their own scratch roots and drop CLAUDE_PID; Make's level is orchestration.
        unbound = {'TMPDIR', 'TMP', 'TEMP', 'CLAUDE_PID', 'MAKELEVEL'}
        policy = ci_local.policy_at(ci_tests.ROOT)
        pattern = re.compile(r'''(?:environ(?:\.get|\.pop|\.setdefault)?\(|environ\[|getenv\()\s*["']([A-Za-z_0-9]+)["']''')
        read = set()
        for directory in ('tools', 'plugins', 'platforms'):
            for path in (ci_tests.ROOT / directory).rglob('*.py'):
                if 'tests' not in path.relative_to(ci_tests.ROOT).parts[:2]:
                    read.update(pattern.findall(path.read_text(encoding='utf-8')))
        self.assertIn('CLAUDE_CODE_EXECPATH', read)
        with mock.patch.dict(os.environ, {name: 'value' for name in read}, clear=True):
            bound = ci_local.bound_environment(policy)
        self.assertEqual(sorted(read - set(bound) - unbound), [])


if __name__ == '__main__':
    unittest.main()
