"""Guards on the coverage-audit script's matching semantics."""

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "plugins" / "software-engineering-team" / "skill-content"
          / "qa-verification" / "scripts" / "scenario_report.py")

spec = importlib.util.spec_from_file_location("scenario_report", SCRIPT)
scenario_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario_report)


JUNIT_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="{count}">
{cases}
</testsuite>
"""


def junit(cases: list[str]) -> str:
    rendered = "\n".join(
        f'  <testcase classname="tests.t" name="{name}" time="0.1"/>' for name in cases
    )
    return JUNIT_TEMPLATE.format(count=len(cases), cases=rendered)


class ScenarioReportMatching(unittest.TestCase):
    def run_report(self, brief: str, junit_xml: str):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "brief.md"
            j = Path(tmp) / "results.xml"
            b.write_text(brief, encoding="utf-8")
            j.write_text(junit_xml, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = scenario_report.main(["--brief", str(b), "--junit", str(j)])
            return code, out.getvalue()

    def test_short_id_does_not_match_longer_id(self):
        """AC-1 untested + AC-10 tested must yield AC-1 NO-TEST, not a false PASS."""
        brief = "\n".join(f"- AC-{n}: criterion {n}." for n in range(1, 11))
        cases = [f"test_thing_{n}[AC-{n}]" for n in range(2, 11)]  # AC-1 deliberately untested
        code, out = self.run_report(brief, junit(cases))
        self.assertEqual(code, 1)
        ac1_row = next(line for line in out.splitlines() if line.startswith("| AC-1 "))
        self.assertIn("NO-TEST", ac1_row)
        ac10_row = next(line for line in out.splitlines() if line.startswith("| AC-10"))
        self.assertIn("PASS", ac10_row)

    def test_boundary_match_still_maps_bracketed_and_property_tags(self):
        brief = "- BR-001: rule one.\n- BR-002: rule two.\n"
        xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="2">
  <testcase classname="tests.t" name="test_one[BR-001]" time="0.1"/>
  <testcase classname="tests.t" name="test_two" time="0.1">
    <properties><property name="scenario" value="BR-002"/></properties>
  </testcase>
</testsuite>
"""
        code, out = self.run_report(brief, xml)
        self.assertEqual(code, 0)
        self.assertIn('"verdict": "PASS"', out)

    def test_canonical_qualified_rule_criterion_and_story_scenario_ids(self):
        brief = (
            "- [[business-analysis/erp/acceptance|erp:AC-INV-001]]\n"
            "- [[business-analysis/erp/rules|erp:BR-INV-002]]\n"
            "## ST-007-TS-003\n"
        )
        xml = junit([
            "test_receipt[erp:AC-INV-001]",
            "test_stock_rule[ERP:BR-INV-002]",
            "test_boundary[ST-007-TS-003]",
        ])
        code, out = self.run_report(brief, xml)
        self.assertEqual(code, 0, out)
        self.assertIn("| ERP:AC-INV-001", out)
        self.assertIn("| ERP:BR-INV-002", out)
        self.assertIn("| ST-007-TS-003", out)

    def test_qualified_identity_does_not_create_a_second_bare_row(self):
        code, out = self.run_report(
            "[[business-analysis/erp/acceptance|erp:AC-INV-001]]\n",
            junit(["test_receipt[erp:AC-INV-001]"]),
        )
        self.assertEqual(code, 0, out)
        rows = [line for line in out.splitlines() if line.startswith("| ERP:")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("| AC-INV-001", out)

    def test_qualified_test_tag_does_not_satisfy_an_unqualified_identity(self):
        code, out = self.run_report(
            "- AC-INV-001\n",
            junit(["test_receipt[erp:AC-INV-001]"]),
        )
        self.assertEqual(code, 1, out)
        self.assertIn("| AC-INV-001", out)
        self.assertIn("NO-TEST", out)

    def test_scenario_identity_uses_the_backlog_story_id_grammar(self):
        code, out = self.run_report(
            "## AUTH-01-TS-003\n",
            junit(["test_authorization[AUTH-01-TS-003]"]),
        )
        self.assertEqual(code, 0, out)
        self.assertIn("| AUTH-01-TS-003", out)

    def test_determinism(self):
        brief = "- BR-001: rule.\n- AC-001: criterion.\n"
        xml = junit(["test_a[BR-001]"])
        first = self.run_report(brief, xml)
        second = self.run_report(brief, xml)
        self.assertEqual(first, second)

    def test_json_out_matches_coverage_import_shape(self):
        import json

        brief = "- AC-001: criterion.\n- AC-002: other.\n"
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "brief.md"
            j = Path(tmp) / "results.xml"
            o = Path(tmp) / "coverage.json"
            b.write_text(brief, encoding="utf-8")
            j.write_text(junit(["test_one[AC-001]"]), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                scenario_report.main([
                    "--brief", str(b), "--junit", str(j), "--json-out", str(o),
                ])
            data = json.loads(o.read_text(encoding="utf-8"))
            rows = {r["id"]: r["result"] for r in data["rows"]}
            self.assertEqual(rows, {"AC-001": "PASS", "AC-002": "NO-TEST"})
            self.assertEqual(data["summary"]["no_test"], 1)


if __name__ == "__main__":
    unittest.main()
