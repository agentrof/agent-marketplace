"""Switch item_verification at single_verifier: one verifier's result settles the
code review and QA readers together, through every check a separate reader's
result passes, and only a critical finding blocks."""
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
class SingleVerifierTests(unittest.TestCase):
    setUp = base.VerificationTests.setUp
    write = base.VerificationTests.write
    note = base.VerificationTests.note
    commit = base.VerificationTests.commit
    freeze = base.VerificationTests.freeze
    result = base.VerificationTests.result

    def choose(self, **values):
        original = delivery.delivery_switch_value
        patch = mock.patch.object(delivery, 'delivery_switch_value', side_effect=lambda docs, identifier, name:
            values[name] if name in values else original(docs, identifier, name))
        patch.start()
        self.addCleanup(patch.stop)

    def payload(self, *findings, verdict='passed'):
        review, qa = self.result(), self.result('qa_engineer', 'qa_final')
        return {'candidate_hash': review['candidate_hash'], 'session_id': review['session_id'],
                'verifier': 'fixture-verifier', 'verdict': verdict, 'report': 'One verifier read and ran the candidate.',
                'review_checks': review['checks'], 'qa_checks': qa['checks'], 'findings': list(findings)}

    def finding(self, severity='major', role='code_reviewer'):
        return {'id': 'SV-1', 'role': role, 'severity': severity, 'status': 'open', 'file': 'src/product.py',
                'description': 'The boundary value is not rejected.', 'verification': 'Reject the boundary value.',
                'owner_role': 'backend_developer', 'revisit_trigger': 'The next change to the input validation.'}

    def test_one_result_settles_both_readers_and_validates(self):
        self.choose(item_verification='single_verifier')
        self.freeze()
        value = verification.register_single_verifier(self.root, self.payload(self.finding(role='qa_engineer')))
        self.assertEqual({role: worker['state'] for role, worker in value['workers'].items()},
                         {'code_reviewer': 'settled', 'qa_engineer': 'settled'})
        self.assertEqual(value['workers']['qa_engineer']['result']['findings'][0]['id'], 'SV-1')
        verification.validate(self.root, 'DLV-001', 'AUTH-01')
        issues = verification.follow_up_issues(self.root, 'DLV-001', 'AUTH-01')['issues']
        self.assertEqual([(issue['finding'], issue['role']) for issue in issues], [('SV-1', 'qa_engineer')])

    def test_a_critical_finding_or_another_switch_settles_nothing(self):
        for values, payload, message in (
                ({'item_verification': 'single_verifier'}, lambda: self.payload(self.finding('critical')),
                 'open blocking finding'),
                ({'item_verification': 'separate'}, lambda: self.payload(), 'runs at switch item_verification'),
                ({'item_verification': 'single_verifier', 'code_review_panel': 'beside_official'},
                 lambda: self.payload(), 'needs code_review_panel single_reader')):
            with self.subTest(message=message):
                self.setUp()
                self.choose(**values)
                self.freeze()
                with self.assertRaisesRegex(RuntimeError, message):
                    verification.register_single_verifier(self.root, payload())
                self.assertEqual({worker['state'] for worker in verification.read_session(self.root)['workers'].values()},
                                 {'running'})


if __name__ == '__main__':
    unittest.main()
