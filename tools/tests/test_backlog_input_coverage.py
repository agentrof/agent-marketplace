"""The manual input coverage view reports receipt currency and citing stories."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tests import backlog_fixture

compiler = backlog_fixture.backlog_compile
stage_package = backlog_fixture.stage_package


def digest(fill: str) -> str:
    return "sha256:" + fill * 64


class InputPackageCoverageViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.docs = Path(self.temporary.name).resolve() / "workspace/docs"
        backlog_fixture.make_approved_backlog(self.docs)

    def render_manual(self, bindings, verify):
        record, errors = compiler.collect(self.docs)
        self.assertEqual(errors, [])
        record["backlog"]["planning_mode"] = "manual"
        record["backlog"]["props"]["input_bindings"] = bindings
        with mock.patch.object(stage_package, "verify", side_effect=verify):
            compiler.render(record, self.docs)
        return (self.docs / "backlog/_generated/input-package-coverage.md").read_text(encoding="utf-8")

    def test_rows_report_receipt_currency_and_every_citing_story(self):
        # The fixture story cites delivery criteria, the design master, the
        # landscape and checkout:SCR-001@r1.
        bindings = [
            f"business-analysis|business-analysis/delivery/space|{digest('1')}",
            f"design-system|design-system/MASTER|{digest('2')}",
            f"experience-design|application@r3|{digest('3')}",
            f"experience-design|checkout@r2|{digest('4')}",
            f"solution-design|solution-design/landscape|{digest('5')}",
        ]

        def verify(docs, stage, reference, expected_hash, **options):
            self.assertTrue(options.get("require_strict_current"))
            self.assertTrue(options.get("require_committed"))
            if reference == "checkout@r2":
                return None, ["checkout@r2 is not the current process receipt"]
            return {"result_ref": reference, "package_hash": expected_hash}, []

        view = self.render_manual(bindings, verify)
        self.assertIn("| package reference | stage | receipt | status | story links |", view)
        self.assertIn(f"| business-analysis/delivery/space | business-analysis | {digest('1')} | current | 1 |", view)
        self.assertIn(f"| design-system/MASTER | design-system | {digest('2')} | current | 1 |", view)
        self.assertIn(f"| application@r3 | experience-design | {digest('3')} | current | 1 |", view)
        self.assertIn(
            f"| checkout@r2 | experience-design | {digest('4')} | "
            "not current: checkout@r2 is not the current process receipt | 1 |",
            view,
        )
        self.assertIn(f"| solution-design/landscape | solution-design | {digest('5')} | current | 1 |", view)
        self.assertNotIn("unknown", view)

    def test_a_package_no_story_cites_reports_zero_links(self):
        view = self.render_manual(
            [f"experience-design|billing@r1|{digest('6')}"],
            lambda docs, stage, reference, expected_hash, **options: ({"result_ref": reference}, []),
        )
        self.assertIn(f"| billing@r1 | experience-design | {digest('6')} | current | 0 |", view)


if __name__ == "__main__":
    unittest.main()
