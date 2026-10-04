"""Exact staged-source identity and fail-closed local result reuse."""
from __future__ import annotations

import copy
import io
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools import ci_local, ci_tests
from tools.tests import git_fixture


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
        self.policy = {'schema_version': 1, 'groups': {'all': {'tests': ['tools.tests.test_example.*']}},
            'always_groups': ['all'], 'lanes': {},
            'full_paths': ['tools/*'], 'rules': [{'paths': ['source.py', '*.md'], 'groups': ['all']}],
            'module_seconds': {}, 'default_seconds': 1, 'python': '3.14',
            'known_test_modules': ['tools.tests.test_example']}
        ci_tests.write_json(self.root / ci_tests.POLICY_PATH, self.policy)
        ci_tests.write_json(self.root / ci_local.POLICY_PATH, {'schema_version': 1, 'max_age_seconds': 86400,
            'default_workers': 2, 'max_workers': 4, 'static_commands': [['static.py']],
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

    def test_prior_branch_commits_and_staged_rename_deletion_are_in_impact(self):
        self.source.write_text('value = 2\n')
        self.git('add', '--all')
        self.git('commit', '-qm', 'prior branch change')
        unusual = 'renamed ü.md' if os.name == 'nt' else 'renamed\tnew\nü.md'
        self.source.rename(self.root / unusual)
        self.git('add', '--all')
        plan = ci_local.make_plan(self.root)
        self.assertEqual(plan['candidate']['changed_paths'], sorted(['source.py', unusual]))
        self.assertNotEqual(plan['candidate']['base'], plan['candidate']['head'])
        self.assertEqual(plan['mode'], 'impact')

    def test_missing_target_runs_full_without_fetching(self):
        plan = ci_local.make_plan(self.root, target='no-such-ref')
        self.assertEqual(plan['mode'], 'full')
        self.assertIsNone(plan['candidate']['base'])

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

    @unittest.skipIf(os.name == 'nt', 'POSIX executable mode')
    def test_executable_mode_change_cannot_hide_behind_core_filemode_false(self):
        self.git('config', 'core.filemode', 'false')
        self.source.chmod(0o755)
        with self.assertRaisesRegex(ci_tests.CIError, 'mode differs'):
            ci_local.make_plan(self.root)

    def test_candidate_change_during_static_check_invalidates_previous_success(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
            original = subprocess.run
            def mutate_static(command, **kwargs):
                if command[-1] == 'static.py':
                    self.source.write_text('different')
                return original(command, **kwargs)
            with mock.patch.object(ci_local.subprocess, 'run', side_effect=mutate_static):
                with self.assertRaises(ci_tests.CIError):
                    self.run_check()
        receipt = ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')
        self.assertEqual(receipt['status'], 'failed')

    def test_reuse_is_exact_fresh_static_and_does_not_extend_expiry(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)) as worker:
            first = self.run_check()
            second = self.run_check()
            self.assertEqual(worker.call_count, 1)
            self.assertTrue(second['reused_tests'])
            self.assertEqual(second['finished_at'], first['finished_at'])
            self.assertEqual(self.run_check(verify_only=True), second)
            self.run_check(fresh=True)
            self.assertEqual(worker.call_count, 2)
        plan = ci_local.make_plan(self.root)
        self.assertFalse(ci_local.reusable(first, plan, 86400, now=first['finished_at'] + 86401))
        changed = copy.deepcopy(first)
        changed['reports'][0]['tests'][0]['outcome'] = 'failure'
        self.assertFalse(ci_local.reusable(changed, plan, 86400))

    def test_failed_rerun_invalidates_older_success_even_for_same_source(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
        with mock.patch.object(ci_local, 'execute_workers', side_effect=ci_tests.CIError('failed')):
            with self.assertRaises(ci_tests.CIError):
                self.run_check(fresh=True)
        with self.assertRaisesRegex(ci_tests.CIError, 'no current'):
            self.run_check(verify_only=True)

    def test_changed_environment_policy_or_inventory_invalidates_receipt(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            receipt = self.run_check()
        for change in ('environment', 'policy', 'inventory'):
            with self.subTest(change=change):
                plan = ci_local.make_plan(self.root)
                if change == 'environment':
                    with mock.patch.dict(os.environ, {'PYTHONPATH': 'different'}):
                        plan = ci_local.make_plan(self.root)
                else:
                    plan[change + '_hash'] = 'different'
                    plan['plan_hash'] = ci_tests.digest({k: v for k, v in plan.items() if k != 'plan_hash'})
                self.assertFalse(ci_local.reusable(receipt, plan, 86400))

    def test_worker_accounting_rejects_missing_duplicate_partial_wrong_runtime_and_skew(self):
        plan = ci_local.make_plan(self.root)
        for change in ('missing', 'duplicate', 'partial', 'wrong_runtime', 'failure', 'running', 'nan'):
            with self.subTest(change=change):
                reports = self.reports(plan)
                if change == 'missing': reports.pop()
                elif change == 'duplicate': reports[-1] = copy.deepcopy(reports[0])
                elif change == 'partial': reports[0]['tests'] = []
                elif change == 'wrong_runtime': reports[0]['runtime'] = {}
                elif change == 'failure': reports[0]['tests'][0]['outcome'] = 'failure'
                elif change == 'nan': reports[0]['tests'][0]['seconds'] = float('nan')
                else: reports[0]['status'] = 'running'
                with self.assertRaises(ci_tests.CIError):
                    ci_local.verify_reports(plan, reports)

    def test_real_workers_have_distinct_temporary_directories_and_account_for_all_tests(self):
        self.test_path.write_text('import os, subprocess, tempfile, unittest\nfrom pathlib import Path\n'
            'class Example(unittest.TestCase):\n'
            '    def check_temporary(self, expected_worker):\n'
            '        temporary_root = Path(tempfile.gettempdir()).resolve()\n'
            '        checkout = Path.cwd().resolve()\n'
            '        self.assertNotIn(checkout, (temporary_root, *temporary_root.parents))\n'
            '        self.assertEqual(temporary_root.name, expected_worker)\n'
            '        self.assertEqual(tempfile.gettempdir(), os.environ["TEMP"])\n'
            '        self.assertEqual(os.environ["TMPDIR"], os.environ["TMP"])\n'
            '        with tempfile.TemporaryDirectory() as raw:\n'
            '            result = subprocess.run(["git", "-C", raw, "rev-parse", "--show-toplevel"], capture_output=True)\n'
            '            self.assertNotEqual(result.returncode, 0, result.stdout)\n'
            '    def test_one(self): self.check_temporary("0")\n'
            '    def test_two(self): self.check_temporary("1")\n')
        self.git('add', '--all')
        receipt = self.run_check()
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(sum(len(report['tests']) for report in receipt['reports']), 2)

    @unittest.skipIf(os.name != 'posix', 'the stand-in host binary is a POSIX shell script')
    def test_a_worker_test_that_reaches_a_host_binary_through_the_environment_fails(self):
        # The session that runs the gate names its own Claude Code binary.
        own = Path(tempfile.mkdtemp(prefix='local-own-host-'))
        self.addCleanup(shutil.rmtree, own, True)
        (own / 'claude').write_text(f'#!/bin/sh\necho "$@" >> {own / "calls.log"}\nexit 0\n')
        (own / 'claude').chmod(0o755)
        self.test_path.write_text('import os, subprocess, unittest\nclass Example(unittest.TestCase):\n'
            '    def test_one(self):\n'
            '        subprocess.run([os.environ["CLAUDE_CODE_EXECPATH"], "--version"], capture_output=True)\n'
            '    def test_two(self): pass\n')
        self.git('add', '--all')
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_EXECPATH': str(own / 'claude')}), \
                self.assertRaisesRegex(ci_tests.CIError, 'local test workers failed'):
            self.run_check()
        self.assertEqual(ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')['status'], 'failed')
        self.assertFalse((own / 'calls.log').exists())

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
        with tempfile.TemporaryDirectory() as raw:
            bare = Path(raw).resolve()
            subprocess.run(['git', '-C', str(bare), 'init', '--bare', '-q'], check=True, capture_output=True)
            with mock.patch.object(ci_local.tempfile, 'gettempdir', return_value=str(bare)):
                with self.assertRaisesRegex(ci_tests.CIError, 'belongs to a Git repository'):
                    ci_local.worker_temp_parent(self.root)

    def test_stdlib_prewarm_ignores_project_modules_and_never_reuses_or_populates_fixture_bytecode(self):
        import py_compile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            preparation = root / 'preparation'
            preparation.mkdir()
            for name in ('argparse.py', 'sitecustomize.py'):
                (preparation / name).write_text('raise RuntimeError("untrusted module imported")\n')
            with mock.patch.dict(os.environ, {'PYTHONPATH': str(preparation), 'PYTHONDONTWRITEBYTECODE': '1'}):
                cache, before = ci_local.prewarm_stdlib(preparation)
            self.assertTrue(list(cache.rglob('argparse.*.pyc')))
            fixture = root / 'fixture'
            fixture.mkdir()
            source = fixture / 'poisoned.py'
            source.write_text('value = 1\n')
            metadata = source.stat()
            bytecode = fixture / '__pycache__' / ('poisoned.' + sys.implementation.cache_tag + '.pyc')
            bytecode.parent.mkdir()
            py_compile.compile(str(source), cfile=str(bytecode), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            poison = bytecode.read_bytes()
            source.write_text('value = 2\n')
            os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            environment = ci_local.execution_environment(self.root)
            environment.pop('PYTHONPYCACHEPREFIX', None)
            # Apple's system Python has a cache prefix even without the env var.
            control = subprocess.run([sys.executable, '-X', 'pycache_prefix=', '-c', 'import poisoned; print(poisoned.value)'],
                                     cwd=fixture, env=environment, check=True, capture_output=True, text=True)
            self.assertEqual(control.stdout.strip(), '1')
            environment['PYTHONPYCACHEPREFIX'] = str(cache)
            code = ('import os\nfrom importlib.machinery import SourceFileLoader\n'
                    'original = SourceFileLoader.get_data\n'
                    'def guarded(self, path):\n'
                    '    if os.path.normcase(os.path.abspath(path)) == ' + repr(os.path.normcase(os.path.abspath(ci_local.argparse.__file__))) + ':\n'
                    '        raise RuntimeError("stdlib source was read instead of warmed bytecode")\n'
                    '    return original(self, path)\n'
                    'SourceFileLoader.get_data = guarded\n'
                    'import argparse, poisoned\nprint(poisoned.value)\n')
            result = subprocess.run([sys.executable, '-c', code], cwd=fixture, env=environment,
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), '2')
            self.assertEqual(ci_local.cache_identity(cache), before)
            self.assertEqual(bytecode.read_bytes(), poison)
            self.assertFalse(list(cache.rglob('poisoned.*.pyc')))

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

    def test_worker_cannot_populate_shared_read_only_cache(self):
        self.test_path.write_text('import os, pathlib, unittest\nclass Example(unittest.TestCase):\n'
            '    def test_one(self):\n'
            '        (pathlib.Path(os.environ["PYTHONPYCACHEPREFIX"]) / "unexpected.pyc").write_bytes(b"changed")\n'
            '    def test_two(self): pass\n')
        self.git('add', '--all')
        with self.assertRaisesRegex(ci_tests.CIError, 'read-only stdlib cache changed'):
            self.run_check()
        self.assertEqual(ci_tests.read_json(self.root / ci_local.CACHE_PATH / 'latest.json')['status'], 'failed')


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

    def test_tracked_symlink_cannot_hide_ignored_source_content(self):
        cache = self.root / '.agentrof/cache.py'
        cache.parent.mkdir()
        cache.write_text('value = 1\n')
        self.source.unlink()
        try:
            self.source.symlink_to(cache)
        except OSError as error:
            self.skipTest(str(error))
        self.git('add', 'source.py')
        with self.assertRaisesRegex(ci_tests.CIError, 'unsupported index entry'):
            ci_local.make_plan(self.root)

    def test_cache_reparse_alias_and_lock_hardlink_are_rejected(self):
        alias = self.root / '.agentrof'
        metadata = mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with mock.patch.object(Path, 'lstat', return_value=metadata):
            self.assertTrue(ci_local.path_alias(alias))
        cache = ci_local.safe_cache(self.root)
        outside = self.root / '.agentrof/another-lock'
        outside.write_text('lock')
        try:
            os.link(outside, cache / 'lock')
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaisesRegex(ci_tests.CIError, 'unsafe local receipt lock'):
            with ci_local.receipt_lock(cache):
                self.fail('unsafe lock acquired')

    def test_json_writer_does_not_follow_predictable_temporary_alias(self):
        directory = self.root / '.agentrof'
        directory.mkdir()
        outside = directory / 'outside'
        outside.write_text('untouched')
        target = directory / 'receipt.json'
        try:
            target.with_name('receipt.json.tmp').symlink_to(outside)
        except OSError as error:
            self.skipTest(str(error))
        ci_tests.write_json(target, {'valid': True})
        self.assertEqual(outside.read_text(), 'untouched')
        self.assertEqual(ci_tests.read_json(target), {'valid': True})

    def test_worker_exception_or_nonfinite_measurements_cannot_form_a_receipt(self):
        plan = ci_local.make_plan(self.root)
        for field in ('error', 'wall_seconds', 'fixture_seconds'):
            reports = self.reports(plan)
            if field == 'error': reports[0]['error'] = 'worker exception'
            elif field == 'wall_seconds': reports[0]['wall_seconds'] = float('nan')
            else: reports[0]['tests'][0]['fixture_seconds'] = {'seed': -1}
            with self.subTest(field=field), self.assertRaises(ci_tests.CIError):
                ci_local.verify_reports(plan, reports)

    def test_workers_are_balanced_with_the_full_suite_lanes_measured_estimates(self):
        self.test_path.write_text('import unittest\nclass Example(unittest.TestCase):\n'
                                  '    def test_one(self): pass\n    def test_two(self): pass\n'
                                  '    def test_three(self): pass\n')
        self.git('add', '--all')
        heavy = 'tools.tests.test_example.Example.test_one'
        self.assertEqual(ci_local.make_plan(self.root)['shards'][0], [heavy, 'tools.tests.test_example.Example.test_two'])
        self.policy['lanes'] = {'full': {'os': 'ubuntu-latest', 'shards': 1, 'groups': ['all']}}
        self.policy['test_seconds'] = {'ubuntu-latest': {heavy: 50.0}}
        ci_tests.write_json(self.root / ci_tests.POLICY_PATH, self.policy)
        self.git('add', '--all')
        self.assertEqual(ci_local.make_plan(self.root)['shards'], [[heavy], [
            'tools.tests.test_example.Example.test_three', 'tools.tests.test_example.Example.test_two']])

    def test_worker_policy_rejects_oversubscription(self):
        for jobs in (0, 5, True):
            with self.assertRaises(ci_tests.CIError):
                ci_local.make_plan(self.root, jobs=jobs)
        with mock.patch.object(ci_local.os, 'cpu_count', return_value=1):
            self.assertEqual(len(ci_local.make_plan(self.root, jobs=4)['shards']), 1)

    def test_reused_test_receipt_still_runs_static_commands(self):
        (self.root / 'static.py').write_text('from pathlib import Path\np = Path(".agentrof/static-count")\n'
            'p.parent.mkdir(exist_ok=True)\np.write_text(str(int(p.read_text()) + 1) if p.exists() else "1")\n')
        self.git('add', '--all')
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            self.run_check()
            self.run_check()
        self.assertEqual((self.root / '.agentrof/static-count').read_text(), '2')

    def test_ignored_source_is_rejected_but_declared_runtime_cache_is_allowed(self):
        (self.root / '.gitignore').write_text('.agentrof/\nignored-source.py\n')
        self.git('add', '--all')
        (self.root / '.agentrof').mkdir()
        (self.root / '.agentrof/cache').write_text('cache')
        ci_local.make_plan(self.root)
        (self.root / 'ignored-source.py').write_text('source')
        with self.assertRaisesRegex(ci_tests.CIError, 'ignored source'):
            ci_local.make_plan(self.root)

    def test_mandatory_native_tests_cannot_be_skipped_locally(self):
        plan = ci_local.make_plan(self.root)
        plan['must_run_ids'] = [plan['selected_ids'][0]]
        plan['plan_hash'] = ci_tests.digest({k: v for k, v in plan.items() if k != 'plan_hash'})
        reports = self.reports(plan)
        reports[0]['tests'][0]['outcome'] = 'skipped'
        with self.assertRaisesRegex(ci_tests.CIError, 'mandatory native'):
            ci_local.verify_reports(plan, reports)

    def test_make_environment_is_normalized_without_mutating_the_callers_environment(self):
        with mock.patch.dict(os.environ, {'MAKELEVEL': '7', 'MAKEFLAGS': 'test', 'LOCAL_SECRET': 'do-not-record'}):
            identity = ci_local.environment_identity(self.root)
            self.assertNotIn('do-not-record', json.dumps(identity))
            self.assertNotIn('MAKEFLAGS', ci_local.execution_environment(self.root))
            self.assertEqual(os.environ['MAKELEVEL'], '7')
        with mock.patch.dict(os.environ, {'MAKELEVEL': '2', 'MAKEFLAGS': 'other', 'LOCAL_SECRET': 'do-not-record'}):
            self.assertEqual(ci_local.environment_identity(self.root), identity)

    def test_another_worktree_creating_and_deleting_branches_during_check_keeps_the_receipt(self):
        self.git('branch', 'packed')
        self.git('pack-refs', '--all')
        other = Path(self.temporary.name).parent / (Path(self.temporary.name).name + '-other')
        self.addCleanup(shutil.rmtree, other, True)
        original = subprocess.run
        def parallel_branch(command, **kwargs):
            if command[-1] == 'static.py':
                ci_tests.git(self.root, 'worktree', 'add', '-q', '-b', 'parallel', str(other), 'HEAD')
                ci_tests.git(self.root, 'config', 'branch.parallel.remote', 'origin')
                ci_tests.git(self.root, 'config', '--add', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
                ci_tests.git(self.root, 'branch', '-D', 'packed')
            return original(command, **kwargs)
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)), \
                mock.patch.object(ci_local.subprocess, 'run', side_effect=parallel_branch):
            receipt = self.run_check()
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(self.run_check(verify_only=True), receipt)
        self.git('config', 'core.autocrlf', 'true')
        with self.assertRaisesRegex(ci_tests.CIError, 'Git configuration'):
            self.run_check(verify_only=True)

    def test_verify_without_jobs_takes_the_worker_count_check_used(self):
        with mock.patch.object(ci_local, 'execute_workers', side_effect=lambda _r, p, _c: self.reports(p)):
            receipt = self.run_check(jobs=1)
        self.assertEqual((receipt['jobs'], receipt['worker_count']), (1, 1))
        self.assertEqual(self.run_check(verify_only=True), receipt)
        with self.assertRaisesRegex(ci_tests.CIError, 'no current'):
            self.run_check(verify_only=True, jobs=2)

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
