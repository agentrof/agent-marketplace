"""Switch finding_disposition at follow_up_issue: only a critical finding blocks an
Item; every finding below it is a complete follow-up that becomes one project issue."""
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
from tools.tests.levels import integration


@integration
class FindingDispositionTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result

    def choose(self, value):
        original = delivery.delivery_switch_value
        patch = mock.patch.object(delivery, 'delivery_switch_value', side_effect=lambda docs, identifier, name:
            value if name == 'finding_disposition' else original(docs, identifier, name))
        patch.start()
        self.addCleanup(patch.stop)

    def finding(self, severity='major', **fields):
        return {'id': 'CR-1', 'severity': severity, 'status': 'open', 'file': 'src/product.py',
                'description': 'The boundary value is not rejected.', 'verification': 'Reject the boundary value.',
                'owner_role': 'backend_developer', 'revisit_trigger': 'The next change to the input validation.',
                **fields}

    def test_a_major_finding_passes_as_a_follow_up_and_becomes_an_issue(self):
        self.choose('follow_up_issue')
        self.freeze()
        review = self.result()
        review['findings'] = [self.finding()]
        verification.register_result(self.root, review)
        verification.register_result(self.root, self.result('qa_engineer', 'qa_final'))
        issues = verification.follow_up_issues(self.root, 'DLV-001', 'AUTH-01')['issues']
        self.assertEqual([(issue['finding'], issue['role']) for issue in issues], [('CR-1', 'code_reviewer')])
        self.assertTrue(issues[0]['title'].startswith('AUTH-01 follow-up CR-1: The boundary value'))
        self.assertIn('Revisit trigger: The next change to the input validation.', issues[0]['body'])

    def test_critical_and_incomplete_follow_ups_still_refuse(self):
        self.choose('follow_up_issue')
        self.freeze()
        for finding, message in ((self.finding('critical'), 'open blocking finding'),
                                 (self.finding(owner_role='qa_engineer'), 'owner_role must be one of'),
                                 (self.finding('minor', revisit_trigger=''), 'concrete revisit_trigger')):
            review = self.result()
            review['findings'] = [finding]
            with self.subTest(severity=finding['severity']), self.assertRaisesRegex(RuntimeError, message):
                verification.register_result(self.root, review)

    def test_next_pass_keeps_a_major_finding_blocking(self):
        self.choose('next_pass')
        self.freeze()
        review = self.result()
        review['findings'] = [self.finding()]
        with self.assertRaisesRegex(RuntimeError, 'open blocking finding'):
            verification.register_result(self.root, review)
        with self.assertRaisesRegex(RuntimeError, 'finding_disposition follow_up_issue'):
            verification.follow_up_issues(self.root, 'DLV-001', 'AUTH-01')


if __name__ == '__main__':
    unittest.main()
