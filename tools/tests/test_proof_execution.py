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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/software-engineering-team/scripts'))
import task_lane
from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration


@integration
class ProofExecutionTests(unittest.TestCase):
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
