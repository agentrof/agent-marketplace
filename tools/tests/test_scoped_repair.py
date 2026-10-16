"""Switch repair_pass at scoped_repair: a repair of findings below critical is
settled by its pre-freeze repair review and touched-suites run, with no new full
reader pass; anything else is refused and keeps the full pass."""
from __future__ import annotations

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
import test_repair_review as repair
from tools.tests.levels import integration

SWITCHES = {'repair_pass': 'scoped_repair', 'repair_review': 'before_freeze'}


class ScopedRepairBindingTests(unittest.TestCase):
    def test_changed_source_or_instruction_binding_requires_full_review(self):
        old = {'delivery': 'DLV-001', 'story': 'AUTH-01', 'inputs': {'story': 'old'},
               'instruction_identity': 'old', 'product_commit': 'a'}
        with mock.patch.object(delivery, 'delivery_switch_value', side_effect=lambda docs, identifier, name: SWITCHES[name]), \
                mock.patch.object(verification, 'pre_handoff_regression', return_value=verification.TOUCHED_SUITES):
            for changed in ({'inputs': {'story': 'new'}}, {'instruction_identity': 'new'}):
                self.assertIn('changed source or instruction bindings', verification.scoped_repair_refusal(
                    ROOT, 'DLV-001', 'AUTH-01', {'candidate': old}, {**old, **changed}))


@integration
class ScopedRepairTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result
    review = repair.RepairReviewTests.review

    def enable(self, **values):
        chosen = {**SWITCHES, **values}
        original = delivery.delivery_switch_value
        for target, value in ((delivery, mock.patch.object(delivery, 'delivery_switch_value', side_effect=lambda d, i, n:
                                  chosen[n] if n in chosen else original(d, i, n))),
                              (verification, mock.patch.object(verification, 'pre_handoff_regression',
                                                               return_value=verification.TOUCHED_SUITES)),
                              (verification, mock.patch.object(verification, 'derive_regression',
                                                               return_value={'kind': 'test', 'affected_test_ids': []})),
                              (verification, mock.patch.object(verification, 'accepted_pre_handoff',
                                                               return_value={'evidence_hash': 'sha256:touched',
                                                                             'exit_code': 0, 'duration_seconds': 1}))):
            value.start()
            self.addCleanup(value.stop)

    def failed_pass(self, severity='major'):
        self.freeze()
        failure = self.result(verdict='failed')
        failure['findings'] = [{'id': 'CR-1', 'severity': severity, 'status': 'open',
                                'verification': 'Reject the boundary value.'}]
        verification.register_result(self.root, failure)
        verification.register_result(self.root, self.result('qa_engineer', 'qa_final'))
        self.write('src/product.py', 'value = 3\n')
        self.commit()
        manifest = verification.repair_review_manifest(self.root, 'DLV-001', 'AUTH-01')
        verification.register_repair_review(self.root, 'DLV-001', 'AUTH-01', self.review(manifest))

    def test_a_reviewed_repair_settles_both_readers_and_validates(self):
        self.enable()
        self.failed_pass()
        value = verification.scoped_repair(self.root, 'DLV-001', 'AUTH-01')
        for role in verification.ROLES:
            self.assertEqual(value['workers'][role]['result']['mode'], 'scoped_repair')
        self.assertEqual(value['workers']['code_reviewer']['result']['findings'][0]['status'], 'resolved')
        validated = verification.validate(self.root, 'DLV-001', 'AUTH-01')
        self.assertEqual(validated['scoped_repair']['pre_handoff_evidence_hash'], 'sha256:touched')
        # A changed record no longer binds the results.
        validated['scoped_repair']['pre_handoff_evidence_hash'] = 'sha256:other'
        verification.write_session(self.root, validated)
        with self.assertRaisesRegex(RuntimeError, 'does not bind'):
            verification.validate(self.root, 'DLV-001', 'AUTH-01')

    def test_refusals_keep_the_full_pass(self):
        for values, severity, message in (({'repair_pass': 'full_pass'}, 'major', 'runs at switch repair_pass'),
                                          ({'repair_review': 'after_freeze'}, 'major', 'repair_review before_freeze'),
                                          ({}, 'critical', 'critical finding needs a full')):
            with self.subTest(message=message):
                self.setUp()
                self.enable(**values)
                if values.get('repair_review') == 'after_freeze':
                    self.freeze()
                    verification.register_result(self.root, self.result(verdict='failed'))
                    verification.register_result(self.root, self.result('qa_engineer', 'qa_final'))
                    self.write('src/product.py', 'value = 3\n'); self.commit()
                else:
                    self.failed_pass(severity)
                before = verification.read_session(self.root)['session_id']
                with self.assertRaisesRegex(RuntimeError, message):
                    verification.scoped_repair(self.root, 'DLV-001', 'AUTH-01')
                self.assertEqual(verification.read_session(self.root)['session_id'], before)


if __name__ == '__main__':
    unittest.main()
