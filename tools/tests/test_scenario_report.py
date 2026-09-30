"""Guards on the coverage-audit script's matching semantics."""

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "plugins" / "software-engineering-team" / "skill-content"
          / "qa-verification" / "scripts" / "scenario_report.py")
sys.path.insert(0, str(REPO / "plugins" / "software-engineering-team" / "scripts"))

import backlog_compile

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


def scenario(identity: str, refs: list[str], given: str = "the stock is known") -> str:
    lines = [f"## {identity}", "", "- category: happy-path", "- target: component",
             "- automation: required",
             f"- automation_target: tests/test_stock.py::test_{identity.lower().replace('-', '_')}",
             "- source_refs:"]
    lines += [f"  - {ref}" for ref in refs]
    lines += [f"- Given: {given}", "- When: the clerk books the receipt",
              "- Then: the stock changes by the booked quantity", ""]
    return "\n".join(lines)


def plan_text(story: str, *scenarios: str) -> str:
    return (f"---\ntype: test-plan\ntitle: Test plan for {story}\naliases:\n"
            f"  - {story}-TP\n---\n# Test plan for {story}\n\n## Coverage Classes\n\n"
            "| class | disposition | scenario_refs | reason |\n|---|---|---|---|\n"
            f"| boundary | covered | {story}-TS-001 | The receipt boundary. |\n\n"
            + "\n".join(scenarios)
            + "\n<!-- sec: nav -->\n- [[maps/backlog|Backlog map]]\n")


ACCEPTANCE = "[[business-analysis/erp/domains/stock/acceptance/stock-acceptance|erp:AC-INV-{}]]"
RULES = "[[business-analysis/erp/domains/stock/rules/stock-rules|erp:BR-INV-{}]]"
DECISION = "[[solution-design/decisions/stock-api|SD-004]]"

# ST-007 cites ST-005's scenario and an extra rule only in prose.
OWN_PLAN = plan_text(
    "ST-007",
    scenario("ST-007-TS-001", [ACCEPTANCE.format("001"), DECISION],
             "the stock state that ST-005-TS-003 leaves and erp:BR-INV-009 governs"),
    scenario("ST-007-TS-002", [RULES.format("002")],
             "a zero quantity; this scenario supersedes ST-005-TS-003"),
)
REGRESSION_PLAN = plan_text(
    "ST-005",
    scenario("ST-005-TS-003", [ACCEPTANCE.format("002"), ACCEPTANCE.format("001")]),
    scenario("ST-005-TS-004", [ACCEPTANCE.format("003")]),
)


def matrix_ids(out: str) -> list[str]:
    return [line.split("|")[1].strip() for line in out.splitlines()
            if line.startswith("| ") and not line.startswith("| Id ")]


class ScenarioReportPlans(unittest.TestCase):
    def run_files(self, files: dict, *args: str):
        """Run the script on ``files`` written to a directory; names map to paths."""
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                (Path(tmp) / name).write_text(content, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = scenario_report.main(
                    [str(Path(tmp) / arg) if arg in files else arg for arg in args])
            return code, out.getvalue(), err.getvalue()

    def test_a_plan_audits_the_scenarios_it_defines_not_the_ids_it_cites(self):
        files = {"plan.md": OWN_PLAN, "results.xml": junit([
            "test_receipt[ST-007-TS-001]", "test_zero[ST-007-TS-002]",
            "test_accept[erp:AC-INV-001]", "test_rule[erp:BR-INV-002]"])}
        code, out, err = self.run_files(files, "--plan", "plan.md", "--junit", "results.xml")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(matrix_ids(out), [
            "ST-007-TS-001", "ERP:AC-INV-001", "ST-007-TS-002", "ERP:BR-INV-002"])
        # An explicit brief keeps today's reading of every id in its text.
        code, out, err = self.run_files(files, "--brief", "plan.md", "--junit", "results.xml")
        self.assertEqual(code, 1, out + err)
        rows = {line.split("|")[1].strip(): line for line in out.splitlines()
                if line.startswith("| ")}
        self.assertIn("NO-TEST", rows["ST-005-TS-003"])
        self.assertIn("NO-TEST", rows["ERP:BR-INV-009"])

    def test_a_defined_scenario_without_a_test_stays_a_no_test_row(self):
        code, out, err = self.run_files(
            {"plan.md": OWN_PLAN, "results.xml": junit([
                "test_receipt[ST-007-TS-001]", "test_accept[erp:AC-INV-001]",
                "test_rule[erp:BR-INV-002]"])},
            "--plan", "plan.md", "--junit", "results.xml")
        self.assertEqual(code, 1, out + err)
        row = next(line for line in out.splitlines() if line.startswith("| ST-007-TS-002"))
        self.assertIn("NO-TEST", row)
        self.assertIn('"no_test": 1', out)

    def test_a_superseded_regression_scenario_leaves_with_the_ids_only_it_cites(self):
        code, out, err = self.run_files(
            {"own.md": OWN_PLAN, "dependency.md": REGRESSION_PLAN, "results.xml": junit([
                "test_receipt[ST-007-TS-001]", "test_zero[ST-007-TS-002]",
                "test_accept[erp:AC-INV-001]", "test_rule[erp:BR-INV-002]",
                "test_regression[ST-005-TS-004]", "test_other[erp:AC-INV-003]"])},
            "--plan", "own.md", "dependency.md", "--superseded", "ST-005-TS-003",
            "--junit", "results.xml")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(matrix_ids(out), [
            "ST-007-TS-001", "ERP:AC-INV-001", "ST-007-TS-002", "ERP:BR-INV-002",
            "ST-005-TS-004", "ERP:AC-INV-003"])

    def test_plan_inputs_that_cannot_be_audited_are_refused(self):
        files = {"own.md": OWN_PLAN, "story.md": "# Receipt\n\nCites ST-007-TS-001.\n",
                 "results.xml": junit(["test_receipt[ST-007-TS-001]"])}
        for args, message in (
                (("--plan", "own.md", "--superseded", "ST-005-TS-003"),
                 "superseded ids are not scenarios the plans define: ST-005-TS-003"),
                (("--plan", "own.md", "--superseded", "ST-007-TS-001", "ST-007-TS-002"),
                 "the plans define no scenario that is not superseded"),
                (("--plan", "own.md", "story.md"), "story.md defines no story scenario")):
            code, out, err = self.run_files(files, *args, "--junit", "results.xml")
            self.assertEqual(code, 2, out + err)
            self.assertIn(message, err)
            self.assertEqual(out, "")
        for args in (("--brief", "own.md", "--superseded", "ST-007-TS-001"),
                     ("--plan", "own.md", "--brief", "own.md")):
            with self.assertRaises(SystemExit) as refused:
                self.run_files(files, *args, "--junit", "results.xml")
            self.assertEqual(refused.exception.code, 2)

    def test_the_plan_grammar_matches_the_backlog_compiler(self):
        def compiler_view(text):
            _props, body = backlog_compile.parse_front_matter_text(text)
            view = []
            for identity, block in backlog_compile.scenario_blocks(body):
                fields, _duplicates = backlog_compile.scenario_fields(block)
                links, _clean = backlog_compile.source_ref_values(fields.get("source_refs", ""))
                aliases = [backlog_compile.split_wikilink(link)[2] for link in links]
                view.append((identity, [alias for alias in aliases
                                        if backlog_compile.BA_ID_RE.fullmatch(alias)]))
            return view

        plans = [
            OWN_PLAN, REGRESSION_PLAN, OWN_PLAN.replace("\n", "\r\n"),
            "## ST-001-TS-001\n- source_refs: [[a/b\\|erp:AC-INV-001]], [[a/c#^x|erp:BR-INV-002]]\n",
            "## ST-001-TS-001\n- source_refs:\n  - [[a|erp:AC-INV-001]]\n- Given: x\n"
            "  - [[b|erp:AC-INV-009]]\n- source_refs: [[c|erp:BR-INV-003]]\n  - [[d|erp:AC-INV-004]]\n",
            "## ST-1-TS-1\n## st-001-ts-001\n## ST-001-TS-0001\n### ST-001-TS-002\n"
            "##  ST-001-TS-003   \n- source_refs: [[a|ERP:AC-INV-001]], [[b|erp:ac-inv-002]],"
            " [[c|erp:AC-INV-01]], [[d|AC-INV-005]], [[e]], erp:AC-INV-006\n",
            "---\nno closing fence\n## ST-002-TS-001\n- source_refs: [[a|erp:AC-INV-001]]\n",
            "---\n---\n## ST-002-TS-001\n- source_refs:\n    - [[a|x:BR-ABCD-1234]]\n\t- [[b|x:BR-AB-001]]\n",
        ]
        for text in plans:
            with self.subTest(text=text[:40]):
                self.assertEqual(scenario_report.plan_scenarios(text), compiler_view(text))
        self.assertEqual(scenario_report.plan_scenarios(plans[4]),
                         [("ST-001-TS-001", ["erp:BR-INV-003", "erp:AC-INV-004"])])


if __name__ == "__main__":
    unittest.main()
