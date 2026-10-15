"""Switch review_rounds at single_pass: a draft round after a draft round of the
same policy pin is refused unless that predecessor returned a critical finding."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import backlog_compile as backlog  # noqa: E402

PIN = {"process_policy_path": "process/process-policy.md", "process_policy_revision": 2}


def review(number: int, *, severity: str | None = "major", pin: dict = PIN, **props) -> dict:
    body = "## Verdict\n\nchanges_requested\n"
    if severity:
        body = ("## Returned Findings\n\n| finding | severity | description |\n|---|---|---|\n"
                f"| F-1 | {severity} | A finding. |\n\n" + body)
    return {"round": number, "path": f"backlog/reviews/round-{number}-backlog-review.md",
            "props": {"status": "draft", **pin, **props}, "body": body}


class SinglePassRoundTests(unittest.TestCase):
    def findings(self, reviews, loop="single_pass"):
        returned = lambda docs, body, path: ({"F-1": body.split("| F-1 | ")[1].split(" |")[0]}, [])
        with mock.patch.object(backlog, "review_loop_value", return_value=loop), \
                mock.patch.object(backlog, "returned_findings", side_effect=returned):
            return backlog.single_pass_round_findings(Path("/docs"), reviews)

    def test_a_second_round_after_no_critical_is_refused(self):
        self.assertEqual(self.findings([review(1), review(2, severity=None)]), [
            "backlog/reviews/round-2-backlog-review.md opens round 2 after"
            " backlog/reviews/round-1-backlog-review.md, which returned no critical finding; "
            + backlog.SINGLE_PASS_RULE])

    def test_allowed_rounds(self):
        self.assertEqual(self.findings([review(1, severity="critical"), review(2)]), [])
        self.assertEqual(self.findings([review(1), review(2)], loop="current"), [])
        self.assertEqual(self.findings([review(1, pin={}), review(2)]), [])
        self.assertEqual(self.findings([review(1, status="approved"), review(2)]), [])
        self.assertEqual(self.findings([review(1, severity=None), review(2)]), [])
        self.assertEqual(self.findings([review(1)]), [])


if __name__ == "__main__":
    unittest.main()
