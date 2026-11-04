"""Bounded shared proof execution keeps failures visible."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/software-engineering-team/scripts'))
import task_lane
from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration


@integration
class ProofExecutionTests(unittest.TestCase):
    def test_prove_reuses_only_unchanged_success_and_serializes_duplicate_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            init_repository(root, initial_branch='main')
            (root / '.gitignore').write_text('.agentrof/\n', encoding='utf-8')
            (root / 'workspace').mkdir()
            runtime = root / '.agentrof'
            runtime.mkdir()
            dependency = runtime / 'dependency'
            dependency.write_text('generation-one', encoding='utf-8')
            counter = runtime / 'runs'
            (root / 'check.py').write_text(
                "from pathlib import Path\nimport time\n"
                "counter = Path('.agentrof/runs')\n"
                "counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')\n"
                "time.sleep(0.05)\n", encoding='utf-8')
            argv = [sys.executable, 'check.py']
            command = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
            config = {'local_proof': [command], 'proof_reuse': {'identity_command': [
                sys.executable, '-c', "from pathlib import Path; print(Path('.agentrof/dependency').read_text())"]}}
            config_path = root / 'workspace/config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')

            def commit():
                task_lane.run('git', 'add', '.', cwd=root)
                task_lane.run('git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                              'commit', '-qm', 'fixture', cwd=root)

            args = Namespace(task='TK-001', base='main', full=False, deferred=False)
            commit()
            started = time.monotonic()
            results = []
            with ThreadPoolExecutor(max_workers=2) as workers:
                requests = [workers.submit(task_lane.cmd_prove, args, root) for _ in range(2)]
                for request in requests:
                    try:
                        results.append(request.result())
                    except task_lane.Refused as error:
                        self.assertIn('shared proof capacity is busy', str(error))
            if len(results) == 1:
                results.append(task_lane.cmd_prove(args, root))
            self.assertEqual(sorted(record['reused'] for record in results), [False, True])
            first = next(record for record in results if not record['reused'])
            self.assertTrue(first['ok'])
            self.assertEqual(counter.read_text(), '1')
            self.assertEqual(results[0]['finished_at'], results[1]['finished_at'])
            self.assertEqual(results[0]['commands'], results[1]['commands'])
            self.assertEqual(task_lane.proof_problems(root, args.task)[0], [])
            print(f'proof reuse fixture: duplicate requests {time.monotonic() - started:.3f}s, one execution')

            dependency.write_text('generation-two', encoding='utf-8')
            self.assertTrue(task_lane.proof_problems(root, args.task)[0])
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])
            self.assertEqual(counter.read_text(), '2')
            with mock.patch.dict(os.environ, {'PROOF_TEST_IDENTITY': 'private-fixture-value'}):
                changed = task_lane.cmd_prove(args, root)
                self.assertFalse(changed['reused'])
                self.assertNotIn('private-fixture-value', json.dumps(changed))
                self.assertTrue(task_lane.cmd_prove(args, root)['reused'])

            with (root / 'check.py').open('a', encoding='utf-8') as stream:
                stream.write('# changed test\n')
            commit()
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])
            path = task_lane.proof_path(root, args.task)
            legacy = json.loads(path.read_text())
            legacy.pop('proof_inputs')
            path.write_text(json.dumps(legacy), encoding='utf-8')
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])

            dependency.write_text('generation-three', encoding='utf-8')
            with mock.patch.object(task_lane, 'run_proof_commands', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    task_lane.cmd_prove(args, root)
            self.assertEqual(json.loads(path.read_text())['proof_state'], 'failed')
            self.assertTrue(task_lane.proof_problems(root, args.task)[0])
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])

            observed = json.loads(path.read_text())
            path.write_text(json.dumps({**observed, 'ok': False, 'proof_state': 'failed'}))
            self.assertTrue(task_lane.cached_proof_problems(
                root, observed, [command], base='main',
                scope={'task': args.task, 'selection': 'full'}, path=path))
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])

            dependency.write_text('generation-four', encoding='utf-8')
            execute = task_lane.run_proof_commands

            def mutate_during_proof(*arguments, **keywords):
                dependency.write_text('generation-five', encoding='utf-8')
                return execute(*arguments, **keywords)

            with mock.patch.object(task_lane, 'run_proof_commands', side_effect=mutate_during_proof):
                drifted = task_lane.cmd_prove(args, root)
            self.assertFalse(drifted['ok'])
            self.assertFalse(drifted['inputs_stable'])
            self.assertFalse(task_lane.cmd_prove(args, root)['reused'])

            config.pop('proof_reuse')
            config_path.write_text(json.dumps(config), encoding='utf-8')
            commit()
            for _ in range(2):
                fresh = task_lane.cmd_prove(args, root)
                self.assertTrue(fresh['ok'])
                self.assertFalse(fresh['reused'])
                self.assertIsNone(fresh['proof_inputs'])
            self.assertEqual(task_lane.proof_problems(root, args.task)[0], [])

    def test_proof_is_bounded_serial_and_preserves_the_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            result = task_lane.run_proof_commands(root, ['echo first', 'exit 7', 'echo skipped'])
            self.assertEqual([row['exit_code'] for row in result], [0, 7])
            self.assertTrue(all('queue_seconds' in row for row in result))
            policy = root / 'policy.json'
            policy.write_text(json.dumps({'proof_timeout_seconds': 0.05}))
            arguments = [sys.executable, '-c', 'import time; time.sleep(10)']
            command = subprocess.list2cmdline(arguments) if os.name == 'nt' else shlex.join(arguments)
            started = time.monotonic()
            with mock.patch.object(task_lane, 'TARGETS_FILE', policy):
                result = task_lane.run_proof_commands(root, [command])
            self.assertEqual(result[0]['exit_code'], 124)
            self.assertTrue(result[0]['timed_out'])
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(task_lane.run_proof_commands(root, ['echo recovered'])[0]['exit_code'], 0)

            if os.name == 'posix':
                result = task_lane.run_proof_commands(root, [command + ' & exit 0'])
                self.assertEqual(result[0]['exit_code'], 125)
                self.assertTrue(result[0]['leftover_children'])
                self.assertEqual(task_lane.run_proof_commands(root, ['echo cleaned'])[0]['exit_code'], 0)

            process = mock.Mock(pid=12345)
            process.wait.side_effect = [KeyboardInterrupt(), None]
            with mock.patch.object(task_lane.subprocess, 'Popen', return_value=process), \
                    mock.patch.object(task_lane, 'run', return_value=str(root / '.git')), \
                    mock.patch.object(task_lane, 'stop_proof') as stop:
                with self.assertRaises(KeyboardInterrupt):
                    task_lane.run_proof_commands(root, ['echo interrupted'])
                stop.assert_called_once_with(process)
