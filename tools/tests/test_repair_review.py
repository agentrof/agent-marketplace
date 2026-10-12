"""Pre-freeze repair review binds findings and contracts without starting a pass."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/software-engineering-team/scripts'))
sys.path.insert(0, str(ROOT / 'tools/tests'))
import delivery_compile as delivery
import delivery_verification as verification
import test_delivery_verification as base
from tools.tests.levels import integration


@integration
class RepairReviewTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    settle = base.VerificationTests.settle

    def enable(self):
        original = delivery.delivery_switch_value
        patch = mock.patch.object(delivery, 'delivery_switch_value', side_effect=lambda docs, identifier, name:
            'before_freeze' if name == 'repair_review' else original(docs, identifier, name))
        patch.start()
        self.addCleanup(patch.stop)

    def begin_repair(self):
        self.enable()
        self.assertFalse(verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')['required'])
        self.freeze()
        failure = self.result(verdict='failed')
        failure['findings'] = [{'id': 'CR-1', 'severity': 'major', 'status': 'open',
                                'verification': 'Preserve the recorded result comparison.'}]
        verification.register_result(self.root, failure)
        verification.register_result(self.root, self.result('qa_engineer', 'qa_diagnostic', 'failed'))
        self.write('src/product.py', 'value = 3\n')
        self.commit()
        return verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')

    def review(self, manifest, *, severity=None):
        result = {key: manifest[key] for key in ('manifest_hash', 'candidate_hash', 'source_session_id')}
        result.update(role='code_reviewer', mode='repair_review', reviewer='independent-fixture-reader',
                      verdict='failed' if severity in ('critical', 'major') else 'passed',
                      report='Compared the repair against the original invariant and approved contract.', findings=[])
        if severity:
            result['findings'] = [{'id': manifest['finding_prefix'] + '1', 'severity': severity,
                'status': 'open', 'file': 'src/product.py', 'description': 'The repair removed a required comparison.',
                'verification': 'Retain comparison coverage for both the legacy and normal cases.'}]
        return result

    @integration
    def test_missing_or_blocking_review_preserves_the_old_session_and_minor_findings_carry(self):
        manifest = self.begin_repair()
        old = verification.read_session(self.root)['session_id']
        self.assertEqual(manifest['repair_delta'], ['src/product.py'])
        self.assertEqual(manifest['findings_to_check'][0]['id'], 'CR-1')
        with self.assertRaisesRegex(RuntimeError, 'repair-review-result'):
            self.freeze()
        self.assertEqual(verification.read_session(self.root)['session_id'], old)
        result = self.review(manifest, severity='major')
        verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', result)
        with self.assertRaisesRegex(RuntimeError, 'blocks the freeze'):
            self.freeze()
        passing = self.review(manifest, severity='minor')
        record = verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', passing)
        frozen = self.freeze()
        self.assertNotEqual(frozen['session_id'], old)
        self.assertEqual(frozen['repair_review']['evidence_hash'], record['evidence_hash'])
        self.assertIn(passing['findings'][0]['id'], [row['id'] for row in frozen['unresolved_findings']])
        self.assertFalse(verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')['required'])

    @integration
    def test_candidate_source_and_finding_changes_invalidate_the_review(self):
        manifest = self.begin_repair()
        result = self.review(manifest)
        bad = {**result, 'manifest_hash': 'sha256:wrong'}
        with self.assertRaisesRegex(RuntimeError, 'does not bind'):
            verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', bad)
        bad = self.review(manifest, severity='major'); bad['verdict'] = 'passed'
        with self.assertRaisesRegex(RuntimeError, 'cannot pass'):
            verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', bad)
        bad = self.review(manifest, severity='minor'); bad['findings'][0]['file'] = 'unreviewed.py'
        with self.assertRaisesRegex(RuntimeError, 'reviewed file'):
            verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', bad)
        verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', result)
        path = verification.repair_review_path(self.root)
        saved = path.read_text()
        changed = json.loads(saved); changed['result']['report'] = 'tampered'
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(RuntimeError, 'current repair-review-result'):
            self.freeze()
        path.write_text(saved)
        previous = verification.read_session(self.root)
        previous['workers']['code_reviewer']['result']['findings'][0]['verification'] = 'Additional contract case'
        verification.write_session(self.root, previous)
        with self.assertRaisesRegex(RuntimeError, 'current repair-review-result'):
            self.freeze()
        next_manifest = verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')
        verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', self.review(next_manifest))
        self.write('src/product.py', 'value = 4\n'); self.commit()
        with self.assertRaisesRegex(RuntimeError, 'current repair-review-result'):
            self.freeze()
        with self.assertRaisesRegex(RuntimeError, 'does not bind'):
            verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', result)

    @integration
    def test_start_pass_refuses_unreviewed_repairs_before_running_regression(self):
        manifest = self.begin_repair()
        previous = verification.read_session(self.root)['session_id']
        for result in (None, self.review(manifest, severity='major')):
            if result is not None:
                verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', result)
            with mock.patch.object(verification, 'pre_handoff_regression', return_value=verification.TOUCHED_SUITES), \
                 mock.patch.object(verification, 'regression_selection') as selection, \
                 mock.patch.object(verification, 'regression_run') as run, \
                 mock.patch.object(verification, 'freeze') as freeze:
                rejected = verification.start_pass(self.root, 'DLV-001', 'AUTH-01', fresh=True)
                self.assertFalse(rejected['ok'])
                self.assertEqual(rejected['failed_step'], 'repair-review')
                selection.assert_not_called()
                run.assert_not_called()
                freeze.assert_not_called()
            self.assertEqual(verification.read_session(self.root)['session_id'], previous)
        verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', self.review(manifest))
        with mock.patch.object(verification, 'pre_handoff_regression', return_value=verification.TOUCHED_SUITES), \
             mock.patch.object(verification, 'regression_selection', side_effect=RuntimeError('after review')) as selection:
            accepted = verification.start_pass(self.root, 'DLV-001', 'AUTH-01')
            self.assertEqual(accepted['failed_step'], 'regression-selection')
            selection.assert_called_once()

    @integration
    def test_default_keeps_freeze_behavior_and_disabled_review_writes_nothing(self):
        self.freeze(); self.settle()
        self.write('src/product.py', 'value = 4\n'); self.commit()
        manifest = verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')
        self.assertFalse(manifest['required'])
        with self.assertRaisesRegex(RuntimeError, 'needs no pre-freeze'):
            verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', {})
        self.assertFalse(verification.repair_review_path(self.root).exists())
        frozen = self.freeze()
        self.assertNotIn('repair_review', frozen)


if __name__ == '__main__':
    unittest.main()
