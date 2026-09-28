"""Keep queue latency, execution cost and classified performance claims distinct."""
import copy
import datetime as dt
import unittest

from tools import ci_performance, ci_tests


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = ci_tests.read_json(ci_performance.ROOT / ci_performance.POLICY_PATH)

    def test_queue_time_starts_after_dependencies_and_incomplete_jobs_stay_unknown(self):
        jobs = [
            {'id': 1, 'name': 'plan', 'started_at': '2026-09-28T10:00:00Z', 'completed_at': '2026-09-28T10:01:00Z'},
            {'id': 2, 'name': 'tests (windows-current, 0)', 'started_at': '2026-09-28T10:10:00Z',
             'completed_at': '2026-09-28T10:15:00Z', 'steps': [{'name': 'Run the exact selected test partition',
                 'started_at': '2026-09-28T10:11:00Z', 'completed_at': '2026-09-28T10:15:00Z'}]},
            {'id': 3, 'name': 'deterministic-check', 'completed_at': '2026-09-28T10:00:20Z'},
            {'id': 4, 'name': 'check', 'started_at': '2026-09-28T10:16:00Z'},
            {'id': 5, 'name': 'changeset', 'completed_at': '2026-09-28T10:00:20Z'},
            {'id': 6, 'name': 'release-pr-policy', 'completed_at': '2026-09-28T10:00:20Z'}]
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

    def test_comparisons_never_mix_change_classes_or_make_missing_quality_claims(self):
        now = dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc)
        samples = []
        for kind, before, after in [('documentation', 10, 5), ('runtime', 100, 95)]:
            for cohort, value in [('baseline', before), ('candidate', after)]:
                samples.extend({'cohort': cohort, 'value': value, 'metric': 'local_validation_seconds',
                    'change_class': kind, 'observed_at': now.isoformat()} for _ in range(5))
        result = ci_performance.compare(samples, self.policy, now)
        self.assertEqual([row['status'] for row in result['comparisons']], ['met', 'missed'])
        self.assertIn('required_gate_omissions', result['missing_metrics'])
        self.assertNotIn('local_validation_seconds', result['missing_metrics'])

    def test_expired_future_or_too_few_samples_do_not_establish_a_target(self):
        now = dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc)
        samples = [{'cohort': 'candidate', 'value': 0, 'metric': 'required_gate_omissions',
                    'change_class': 'runtime', 'observed_at': now.isoformat()}]
        for days in (-31, 1):
            sample = dict(samples[0], observed_at=(now + dt.timedelta(days=days)).isoformat())
            samples.append(sample)
        result = ci_performance.compare(samples, self.policy, now)
        self.assertEqual(result['comparisons'][0]['status'], 'insufficient_samples')
        self.assertEqual(result['comparisons'][0]['candidate_count'], 1)

    def test_invalid_values_and_unknown_metric_are_rejected(self):
        sample = {'cohort': 'candidate', 'value': 0, 'metric': 'local_validation_seconds',
                  'change_class': 'runtime', 'observed_at': '2026-09-28T10:00:00Z'}
        for value in (float('nan'), -1, True):
            with self.assertRaises(ci_tests.CIError):
                ci_performance.compare([dict(sample, value=value)], self.policy)
        with self.assertRaises(ci_tests.CIError):
            ci_performance.compare([dict(sample, metric='invented')], self.policy)

    def test_performance_policy_keeps_quality_and_platform_targets_at_zero(self):
        for metric in ('required_gate_omissions', 'platform_coverage_omissions', 'duplicate_same_candidate_checks'):
            self.assertEqual(self.policy['targets'][metric], {'maximum_value': 0})
        self.assertEqual(self.policy['targets']['local_validation_seconds']['minimum_reduction'], .4)
        self.assertIn('escaped_defects', self.policy['observed_metrics'])
