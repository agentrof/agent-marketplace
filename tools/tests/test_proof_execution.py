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


class ProcessCleanupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_wait_process_group_requires_disappearance_within_its_deadline(self):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        with mock.patch.object(task_lane.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(task_lane.time, "sleep", side_effect=sleep), \
                mock.patch.object(task_lane.os, "killpg", side_effect=[None, None, ProcessLookupError()]):
            self.assertTrue(task_lane.wait_process_group(12345, 1))
        self.assertAlmostEqual(clock[0], 0.1)
        clock[0] = 0.0
        with mock.patch.object(task_lane.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(task_lane.time, "sleep", side_effect=sleep), \
                mock.patch.object(task_lane.os, "killpg"):
            self.assertFalse(task_lane.wait_process_group(12345, 0.1))
        self.assertAlmostEqual(clock[0], 0.1)
        with mock.patch.object(task_lane.os, "killpg", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                task_lane.wait_process_group(12345, 1)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_stop_proof_verifies_group_disappearance_after_reaping_the_leader(self):
        process = mock.Mock(pid=12345)
        with mock.patch.object(task_lane.os, "killpg") as kill, \
                mock.patch.object(task_lane.time, "monotonic", side_effect=[100, 102, 104]), \
                mock.patch.object(task_lane, "wait_process_group", return_value=False) as wait:
            with self.assertRaisesRegex(task_lane.Refused, "cleanup is unconfirmed"):
                task_lane.stop_proof(process)
        kill.assert_called_once_with(12345, task_lane.signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=8)
        wait.assert_called_once_with(12345, 6)
        process.reset_mock()
        with mock.patch.object(task_lane.os, "killpg"), \
                mock.patch.object(task_lane, "wait_process_group", return_value=True) as wait:
            task_lane.stop_proof(process)
        process.wait.assert_called_once()
        wait.assert_called_once()

    def test_windows_cleanup_keeps_taskkill_and_waits_for_the_leader(self):
        process = mock.Mock(pid=12345)
        with mock.patch.object(task_lane.os, "name", "nt"), \
                mock.patch.object(task_lane.subprocess, "run", return_value=mock.Mock(returncode=0)) as stop, \
                mock.patch.object(task_lane.time, "monotonic", side_effect=[100, 102]), \
                mock.patch.object(task_lane, "wait_process_group") as group:
            task_lane.stop_proof(process)
        stop.assert_called_once_with(["taskkill", "/PID", "12345", "/T", "/F"],
                                     capture_output=True, timeout=10, check=False)
        process.wait.assert_called_once_with(timeout=8)
        group.assert_not_called()
        process.reset_mock()
        process.poll.return_value = None
        with mock.patch.object(task_lane.os, "name", "nt"), \
                mock.patch.object(task_lane.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaisesRegex(task_lane.Refused, "cleanup is unconfirmed"):
                task_lane.stop_proof(process)
        process.wait.assert_not_called()

    def test_original_wait_error_survives_failed_cleanup_and_blocks_retry(self):
        for error in (subprocess.TimeoutExpired("proof", 1), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                blocked = root / "blocked.json"
                process = mock.Mock(pid=12345)
                process.wait.side_effect = error
                with mock.patch.object(task_lane.subprocess, "Popen", return_value=process), \
                        mock.patch.object(task_lane, "stop_proof", side_effect=task_lane.Refused("cleanup unconfirmed")), \
                        self.assertRaises(type(error)) as caught:
                    task_lane.run_proof_commands(root, ["synthetic"], admission=(blocked, 0))
                self.assertIs(caught.exception, error)
                self.assertIsInstance(error.__cause__, task_lane.Refused)
                self.assertEqual(json.loads(blocked.read_text())["reason"], "cleanup_unconfirmed")


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
                # Model the leftover group independently of the OS's orphan reaping timing.
                process = mock.Mock(pid=12345, returncode=0)
                with mock.patch.object(task_lane.subprocess, 'Popen', return_value=process), \
                        mock.patch.object(task_lane, 'run', return_value=str(root / '.git')), \
                        mock.patch.object(task_lane.os, 'killpg', side_effect=[None, None, ProcessLookupError()]) as kill:
                    result = task_lane.run_proof_commands(root, [command + ' & exit 0'])
                self.assertEqual(kill.call_args_list, [mock.call(12345, 0),
                                                      mock.call(12345, task_lane.signal.SIGKILL), mock.call(12345, 0)])
                self.assertEqual(process.wait.call_count, 2)
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
