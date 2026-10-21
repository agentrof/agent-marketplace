"""Keep queue latency, execution cost and classified performance claims distinct."""
import unittest

from tools import ci_performance, ci_tests


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = ci_tests.read_json(ci_performance.ROOT / ci_performance.POLICY_PATH)

    def test_queue_time_starts_after_dependencies_and_incomplete_jobs_stay_unknown(self):
        jobs = [
            {'id': 1, 'name': 'plan', 'started_at': '2026-09-28T10:00:00Z', 'completed_at': '2026-09-28T10:01:00Z'},
            {'id': 2, 'name': 'tests (windows, 0)', 'started_at': '2026-09-28T10:10:00Z',
             'completed_at': '2026-09-28T10:15:00Z', 'steps': [{'name': 'Run the exact selected test partition',
                 'started_at': '2026-09-28T10:11:00Z', 'completed_at': '2026-09-28T10:15:00Z'}]},
            {'id': 3, 'name': 'deterministic-check', 'completed_at': '2026-09-28T10:00:20Z'},
            {'id': 4, 'name': 'check', 'started_at': '2026-09-28T10:16:00Z'},
            {'id': 5, 'name': 'changeset', 'completed_at': '2026-09-28T10:00:20Z'}]
        result = ci_performance.job_measurements(jobs, self.policy)
        self.assertEqual(result[1]['scheduling_seconds'], 540)
        self.assertEqual(result[1]['test_step_seconds'], 240)
        self.assertEqual(result[1]['wall_seconds'], 300)
        self.assertEqual(result[3]['scheduling_seconds'], 60)
        self.assertIsNone(result[3]['wall_seconds'])
        self.assertIsNone(result[0]['scheduling_seconds'])

    def test_no_measured_test_step_is_unknown_and_naive_times_are_rejected(self):
        result = ci_performance.job_measurements([{'name': 'tests (linux, 0)', 'steps': []}], self.policy)
        self.assertIsNone(result[0]['test_step_seconds'])
        with self.assertRaisesRegex(ci_tests.CIError, 'timezone'):
            ci_performance.timestamp('2026-09-28T10:00:00')
