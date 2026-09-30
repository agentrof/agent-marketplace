"""Process switch story_size_budget: story size measures against owner-set
limits, and a split proposal for a story over budget.

At `off`, the default, nothing is measured or shown, so every compiler output
and every bound instruction stays as released. At `propose_split`, the backlog
compiler reports each story's measures, a review manifest carries them as
given facts and the Delivery proposal shows them read-only; a story over
budget never fails a check or blocks an approval.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import backlog_compile  # noqa: E402
import backlog_fixture  # noqa: E402
import backlog_review_inputs  # noqa: E402
import delivery_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
from backlog_fixture import CONSTRAINT, CRITERION, DESIGN, EXPERIENCE  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "story_size_budget"
REGISTRY = "skill-content/configure/data/process-switches.json"
MEASURES = "skill-content/product-planning/data/story-size-measures.json"
REFERENCE = "skill-content/product-planning/references/switch-story_size_budget-propose_split.md"
OWNING_FLOWS = ("backlog-planning", "delivery-planning")
EPIC = "backlog/epics/delivery-fixture"
SECOND = ("[[business-analysis/delivery/domains/identity/acceptance/"
          "delivery-acceptance|delivery:AC-DEL-002]]")
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        code = call(*args)
    return code, output.getvalue()


def measured(story: dict) -> dict:
    return {name: backlog_compile.STORY_SIZE_DERIVATIONS[spec["derivation"]](story)
            for name, spec in backlog_compile.story_size_measures().items()}


class RegistryAndInstructionTests(unittest.TestCase):
    def test_switch_is_off_by_default_with_owner_set_limits_and_its_promotion_rule(self):
        spec = json.loads(read(REGISTRY))["switches"][SWITCH]
        self.assertEqual(spec["issue"], 325)
        self.assertEqual([value["id"] for value in spec["values"]], ["off", "propose_split"])
        self.assertEqual(spec["default"], "off")
        self.assertEqual(sorted(spec["flows"]), sorted(OWNING_FLOWS))
        # The package sets no limit: the declaration names ids and a type, never a value.
        self.assertEqual(spec["parameters"], {
            "summary": spec["parameters"]["summary"], "values": ["propose_split"],
            "declared_by": {"path": MEASURES, "key": "measures"},
            "type": "positive_integer", "min_count": 1})
        # Only the propose_split instructions and the compiler read the measures.
        self.assertEqual(spec["value_data"], {"propose_split": [MEASURES]})
        self.assertIn("never over budget", spec["parameters"]["summary"])
        self.assertEqual(spec["promotion"]["unit"], "At least 3 backlog revisions run with"
                         " propose_split and 3 Deliveries that contain stories planned under it.")
        for fragment in ("accepts at least half of the split proposals",
                         "median cycle time at most half", "loses, merges or rewords a criterion",
                         "dependency cycle", "verifiable only after a sibling lands"):
            self.assertIn(fragment, spec["promotion"]["threshold"])
        self.assertIn("never fails a check, blocks an approval or rewrites a criterion",
                      spec["values"][1]["tradeoffs"])

    def test_a_promotion_in_the_documented_form_loads_and_validates(self):
        # A promotion keeps off as the default and ships package limits (#325).
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            for relative in (REGISTRY, MEASURES):
                (package / relative).parent.mkdir(parents=True, exist_ok=True)
                (package / relative).write_bytes((TEAM / relative).read_bytes())
            registry = json.loads(read(REGISTRY))
            spec = registry["switches"][SWITCH]
            spec["parameters"]["package_limits"] = {"acceptance_criteria": 12, "test_scenarios": 20}
            (package / REGISTRY).write_text(json.dumps(registry), encoding="utf-8")
            declared = process_policy.load_registry(package)[SWITCH]
            self.assertEqual((declared["default"], declared["parameters"]["package_limits"]),
                             ("off", {"acceptance_criteria": 12, "test_scenarios": 20}))
            ids = [value["id"] for value in spec["values"]]
            self.assertEqual(validate.parameter_problems(f"switch {SWITCH!r}", spec["parameters"],
                                                         ids, spec["default"], package), [])

    def test_the_measures_are_the_policy_parameters_and_the_compiler_derives_each(self):
        measures = backlog_compile.story_size_measures()
        self.assertEqual(sorted(measures), ["acceptance_criteria", "contract_deltas",
                                            "implementation_roles", "test_scenarios"])
        declared = process_policy.load_registry()[SWITCH]["parameters"]
        self.assertEqual(sorted(declared["ids"]), sorted(measures))
        derivations = validate.implemented_derivations(read("scripts/backlog_compile.py"))
        self.assertEqual(derivations, set(backlog_compile.STORY_SIZE_DERIVATIONS))
        self.assertEqual({spec["derivation"] for spec in measures.values()}, derivations)

    def test_each_owning_flow_anchors_the_switch_and_names_the_reference(self):
        for flow in OWNING_FLOWS:
            with self.subTest(flow=flow):
                text = flat(read(f"flows/{flow}.md"))
                self.assertIn(f"Switch `{SWITCH}`: at `propose_split`", text)
                self.assertIn(REFERENCE, text)
        for path in sorted((TEAM / "flows").glob("*.md")):
            if path.stem not in OWNING_FLOWS:
                with self.subTest(flow=path.stem):
                    self.assertNotIn(SWITCH, path.read_text(encoding="utf-8"))

    def test_the_default_path_keeps_its_instructions(self):
        # The size rule, the no-estimate rule and the role files keep their text.
        for relative in ("skill-content/product-planning/SKILL.md",
                         "skill-content/product-planning/references/flow-metrics.md",
                         "skill-content/product-planning/references/slicing-patterns.md",
                         "skill-content/product-planning/references/structured-records.md",
                         "skill-content/backlog-plan/SKILL.md",
                         "skill-content/delivery-plan/SKILL.md",
                         "agents/product-owner.md", "agents/backlog-reviewer.md"):
            with self.subTest(path=relative):
                text = read(relative)
                self.assertNotIn(SWITCH, text)
                self.assertNotIn("propose_split", text)
                self.assertNotIn("Size Exceptions", text)
        self.assertIn("DON'T add an estimate field, points, or sizing numbers to any artifact",
                      read("skill-content/product-planning/references/flow-metrics.md"))

    def test_the_reference_defines_an_advisory_verbatim_split(self):
        text = flat(read(REFERENCE))
        for rule in (
                "These are the instructions of process switch `story_size_budget` at"
                " `propose_split`",
                "A task binds this file only when the project's Process Policy selects that"
                " value; at the default, `off`, nothing is measured or shown",
                "without adding a field to any story or capping anything",
                "The package sets none, and a measure without a limit is reported but never"
                " over budget",
                "it never fails `backlog_compile.py check`, never blocks a review or an approval"
                " and never rewrites a criterion",
                "The one-review-unit rule and the no-estimate rule of"
                " `references/flow-metrics.md` stand as written",
                "A limit bounds a review unit, never time or effort",
                "never recount them by hand",
                "before any epic review manifest is derived",
                "`references/slicing-patterns.md`",
                "Too-Big and Too-Small tests",
                "Never merge, reword, drop or compress a criterion or a scenario to fit a limit",
                "Ask the owner one choice-gate question per over-budget story",
                "Create each new story with `backlog_compile.py stub-story`",
                "byte for byte",
                "only its heading changes to the new story's `<story-id>-TS-###`",
                "every moved criterion is covered by exactly one of the two stories",
                "No part may be verifiable only after a sibling lands",
                "`Size Exceptions` table of its epic's current review note",
                "The compiler validates every row and rejects a repeated story and measure",
                "The review manifest's `check.story_size` block carries the measures",
                "never raises a finding for a count alone",
                "Slicing evidence names the limits its review ran under; the note records the"
                " Process Policy's path, revision and source hash of the round",
                "the budget never changes the selection or the scope decision"):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        for skill in sorted(TEAM.glob("skill-content/*/SKILL.md")):
            self.assertNotIn("switch-story_size_budget", skill.read_text(encoding="utf-8"))


def story(body: str = "", scenarios: int = 0, owner: str = "backend_developer",
          supporting: tuple[str, ...] = ()) -> dict:
    props = {"owner_role": owner}
    if supporting:
        props["supporting_roles"] = list(supporting)
    return {"props": props, "body": body,
            "scenario_ids": [f"ST-001-TS-{number:03d}" for number in range(1, scenarios + 1)]}


STORY_BODY = """# Story

## Scope

- [ ] Not a criterion: a checklist-like line in Scope.

## Acceptance

{acceptance}

## Dependencies

None.

## Delivery Notes

- [ ] Not a criterion either.

<!-- sec: nav -->
- [ ] A navigation line is never a criterion.
- [[maps/backlog|Backlog map]]
"""


class MeasureTests(unittest.TestCase):
    def test_acceptance_criteria_count_exactly_the_checklist_of_the_acceptance_section(self):
        cases = (
            ("The story keeps its criteria in its Test Plan scenarios.", 0),
            ("- [ ] One observable result.", 1),
            ("- [ ] First result.\n- [x] Second result.\n* [ ] Third result.\n"
             "  - [ ] A nested criterion.\n- [X] Fifth result.\n"
             "\n```text\n- [ ] Inside a code block.\n```\n"
             "- Not a checklist line.\n-[ ] Not a checklist line either.", 5),
        )
        for acceptance, expected in cases:
            with self.subTest(expected=expected):
                value = story(STORY_BODY.format(acceptance=acceptance))
                self.assertEqual(measured(value)["acceptance_criteria"], expected)
        # The last section reads only up to the navigation marker.
        last = "# Story\n\n## Acceptance\n\n- [ ] One.\n\n<!-- sec: nav -->\n- [ ] Two.\n"
        self.assertEqual(measured(story(last))["acceptance_criteria"], 1)

    def test_a_measure_whose_derivation_is_not_a_name_is_refused_not_a_crash(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "measures.json"
            path.write_text(json.dumps({"schema_version": 1, "measures": {
                "acceptance_criteria": {"summary": "Criteria.",
                                        "derivation": ["acceptance_checklist_lines"]}}}),
                encoding="utf-8")
            with mock.patch.object(backlog_compile, "STORY_SIZE_MEASURES_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "acceptance_criteria"):
                    backlog_compile.story_size_measures()

    def test_roles_and_contract_deltas_follow_the_role_fields(self):
        cases = (
            ((), (1, 0)),
            (("frontend_developer",), (2, 0)),
            (("software_architect", "ux_designer"), (3, 1)),
            (("frontend_developer", "devops_engineer", "software_architect"), (4, 1)),
        )
        for supporting, (roles, deltas) in cases:
            with self.subTest(supporting=supporting):
                values = measured(story(supporting=supporting))
                self.assertEqual((values["implementation_roles"], values["contract_deltas"]),
                                 (roles, deltas))
        # A repeated role is one role.
        repeated = story(supporting=("backend_developer",))
        self.assertEqual(measured(repeated)["implementation_roles"], 1)


def approved_backlog(fixture: "Project") -> None:
    backlog_fixture.make_approved_backlog(fixture.docs, "ST-001", "ST-002")


class Project:
    """A project with a backlog, by default an approved legacy one with two
    stories, and the Process Policy CLI over it."""

    def __init__(self, test: unittest.TestCase, build=approved_backlog) -> None:
        self.test = test
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace" / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        build(self)

    def policy(self, *argv: str) -> dict:
        code, output = quiet(process_policy.main, [argv[0], "--docs", str(self.docs), *argv[1:]])
        result = json.loads(output)
        self.test.assertEqual(code, 0, result)
        return result

    def choose(self, *values: tuple[str, str], limits: dict | None = None) -> None:
        """Approve a policy revision that sets exactly these values and limits."""
        if process_policy.path_for(self.docs).exists():
            self.policy("begin-revision")
            for switch in process_policy.table_rows(
                    process_policy.parse(process_policy.path_for(self.docs))[1])[0]:
                self.policy("set", "--switch", switch, "--default")
        else:
            self.policy("init")
        for switch, value in values:
            self.policy("set", "--switch", switch, "--value", value)
        for parameter, limit in (limits or {}).items():
            self.policy("set", "--switch", SWITCH, "--parameter", parameter, "--value", str(limit))
        self.policy("approve")

    def check(self, *flags: str) -> tuple[int, str]:
        return quiet(backlog_compile.main, ["check", "--docs", str(self.docs), "--json", *flags])

    def story_path(self, number: int) -> Path:
        return self.docs / f"{EPIC}/stories/st-{number:03d}/story.md"

    def add_criteria(self, number: int, *lines: str) -> None:
        path = self.story_path(number)
        text = path.read_text(encoding="utf-8")
        anchor = "- [ ] Every cited criterion has an observable passing result.\n"
        path.write_text(text.replace(anchor, anchor + "".join(f"{line}\n" for line in lines), 1),
                        encoding="utf-8")

    def commit(self) -> None:
        init_repository(self.root, initial_branch="main")
        for args in (("config", "core.autocrlf", "false"), ("add", "--all"),
                     ("commit", "-q", "-m", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True,
                           capture_output=True, env={**os.environ, **GIT_IDENTITY})


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Project(self)

    def test_without_a_policy_or_at_off_the_check_output_is_unchanged(self):
        released = self.fx.check()
        self.assertEqual(json.loads(released[1])["ok"], True)
        self.assertNotIn("story_size", released[1])
        self.fx.choose(("review_panels", "lens_panel"))
        self.assertEqual(self.fx.check(), released)
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 5})
        self.assertIn("story_size", json.loads(self.fx.check()[1]))
        self.fx.choose()
        self.assertEqual(self.fx.check(), released)
        # At the default the compiler derives no measure, so its measure reader never runs.
        with mock.patch.object(backlog_compile, "STORY_SIZE_MEASURES_PATH",
                               self.fx.root / "missing.json"):
            self.assertEqual(self.fx.check(), released)

    def test_propose_split_reports_every_story_and_never_fails_the_check(self):
        self.fx.add_criteria(1, "- [ ] A second observable result.",
                             "- [ ] A third observable result.")
        self.fx.choose((SWITCH, "propose_split"),
                       limits={"acceptance_criteria": 2, "test_scenarios": 5})
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["ok"], result["errors"]), (0, True, []))
        self.assertEqual(result["story_size"], {
            "switch": SWITCH, "value": "propose_split",
            "limits": {"acceptance_criteria": 2, "test_scenarios": 5},
            "over_budget_stories": ["ST-001"],
            "stories": {
                "ST-001": {"measures": {
                    "acceptance_criteria": {"value": 3, "limit": 2},
                    "contract_deltas": {"value": 0},
                    "implementation_roles": {"value": 1},
                    "test_scenarios": {"value": 1, "limit": 5}},
                    "over_budget": ["acceptance_criteria"], "size_exceptions": []},
                "ST-002": {"measures": {
                    "acceptance_criteria": {"value": 1, "limit": 2},
                    "contract_deltas": {"value": 0},
                    "implementation_roles": {"value": 1},
                    "test_scenarios": {"value": 1, "limit": 5}},
                    "over_budget": [], "size_exceptions": []}}})

    def test_the_policy_refuses_propose_split_without_valid_limits(self):
        def refused(*argv: str) -> str:
            code, output = quiet(process_policy.main,
                                 [argv[0], "--docs", str(self.fx.docs), *argv[1:]])
            self.assertEqual(code, 1, output)
            return json.loads(output)["errors"][0]

        self.fx.policy("init")
        self.fx.policy("set", "--switch", SWITCH, "--value", "propose_split")
        self.assertIn("switch 'story_size_budget' at 'propose_split' needs at least 1 of its"
                      " parameters ['acceptance_criteria', 'contract_deltas',"
                      " 'implementation_roles', 'test_scenarios']", refused("approve"))
        for value in ("0", "-1", "12.5", "twelve"):
            with self.subTest(value=value):
                self.assertIn("must be a positive integer", refused(
                    "set", "--switch", SWITCH, "--parameter", "acceptance_criteria",
                    "--value", value))
        self.assertIn("switch 'story_size_budget' has no parameter 'story_points'", refused(
            "set", "--switch", SWITCH, "--parameter", "story_points", "--value", "8"))
        self.fx.policy("set", "--switch", SWITCH, "--parameter", "test_scenarios", "--value", "30")
        self.assertEqual(self.fx.policy("approve")["revision"], 1)

    def test_a_draft_policy_is_refused_never_read(self):
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 5})
        self.fx.policy("begin-revision")
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertTrue(any("Process Policy revision 2 is a draft" in error
                            for error in result["errors"]), result["errors"])
        self.assertNotIn("story_size", result)
        with self.assertRaisesRegex(backlog_review_inputs.InputError, "is a draft"):
            backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")

    def test_size_exceptions_are_validated_and_reported_while_the_budget_is_on(self):
        review = self.fx.docs / f"{EPIC}/reviews/round-1-epic-review.md"
        props, body = backlog_compile.parse_front_matter(review)
        link = f"[[{EPIC}/stories/st-001/story\\|ST-001]]"
        reason = "The criteria share one acceptance boundary that one review pass covers."
        rows = [
            f"| {link} | acceptance_criteria | {reason} |",
            f"| {link} | ghost_measure | {reason} |",
            f"| [[{EPIC}/stories/st-002/story\\|ST-001]] | test_scenarios | {reason} |",
            f"| [[{EPIC}/stories/st-009/story\\|ST-009]] | test_scenarios | {reason} |",
            f"| [[{EPIC}/epic\\|EP-001]] | test_scenarios | {reason} |",
            f"| {link} | test_scenarios | TBD |",
            f"| {link} | acceptance_criteria | {reason} |",
        ]
        table = ("\n## Size Exceptions\n\n| story | measure | reason |\n|---|---|---|\n"
                 + "\n".join(rows) + "\n")
        body = body.replace("\n## Findings", table + "\n## Findings", 1)
        review.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        released = self.fx.check()
        # At the default the table is neither read nor checked.
        self.assertEqual(json.loads(released[1])["errors"], [])
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 5})
        code, output = self.fx.check()
        result = json.loads(output)
        path = f"{EPIC}/reviews/round-1-epic-review.md"
        expected = [
            f"{path} size exception 2 names undeclared measure ghost_measure; the measures are"
            " acceptance_criteria, contract_deltas, implementation_roles, test_scenarios",
            f"{path} size exception 3 story alias must be ST-002",
            f"{path} size exception 4 targets missing note: {EPIC}/stories/st-009/story",
            f"{path} size exception 5 must link a story of EP-001: [[{EPIC}/epic\\|EP-001]]",
            f"{path} size exception 6 needs a concrete reason",
            f"{path} repeats the size exception of ST-001 for acceptance_criteria",
        ]
        self.assertEqual((code, sorted(result["errors"])), (1, sorted(expected)))
        self.assertEqual(result["story_size"]["stories"]["ST-001"]["size_exceptions"],
                         ["acceptance_criteria", "test_scenarios"])
        self.assertEqual(result["story_size"]["stories"]["ST-002"]["size_exceptions"],
                         ["test_scenarios"])

    def test_the_test_scenario_measure_counts_definitions_not_prose_citations(self):
        plan = self.fx.docs / f"{EPIC}/stories/st-001/test-plan.md"
        text = plan.read_text(encoding="utf-8")
        block = text[text.index("## ST-001-TS-001"):text.index("<!-- sec: nav -->")].rstrip()
        second = (block.replace("ST-001-TS-001", "ST-001-TS-002")
                  .replace("an eligible customer supplies valid account details",
                           "the account state that ST-005-TS-003 leaves; this scenario"
                           " supersedes ST-005-TS-004 and ST-019-TS-001"))
        text = text.replace(block, block + "\n\n" + second + "\n\nRegression context names"
                            " ST-031-TS-007 and ST-031-TS-008 only in prose.")
        text = text.replace("| empty | covered | ST-001-TS-001 |",
                            "| empty | covered | ST-001-TS-001, ST-001-TS-002 |")
        plan.write_text(text, encoding="utf-8")
        record, _errors = backlog_compile.collect(self.fx.docs)
        found = next(item for item in record["stories"] if item["id"] == "ST-001")
        self.assertIn("ST-005-TS-003", found["test_body"])
        self.assertEqual(found["scenario_ids"], ["ST-001-TS-001", "ST-001-TS-002"])
        self.assertEqual(measured(found)["test_scenarios"], 2)

    def test_stub_story_adds_no_size_field_under_the_budget(self):
        def stubbed(slug: str, identity: str) -> dict:
            args = SimpleNamespace(
                docs=str(self.fx.docs), epic="delivery-fixture", slug=slug, id=identity, title=None,
                scope=None, work_kind="feature", criterion_ref=[CRITERION],
                experience_ref=[EXPERIENCE], evidence_ref=[], uses_design=[DESIGN],
                constrained_by=[CONSTRAINT], implements=[])
            self.assertEqual(quiet(backlog_compile.stub_story, args)[0], 0)
            return backlog_compile.parse_front_matter(
                self.fx.docs / f"{EPIC}/stories/{slug}/story.md")[0]

        released = stubbed("released", "ST-003")
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 5})
        budgeted = stubbed("budgeted", "ST-004")
        self.assertEqual(list(budgeted), list(released))
        policy = json.loads(read("skill-content/obsidian-vault/data/vault-policy.json"))
        self.assertFalse([key for key in policy["property_types"]
                          if re.search(r"size|estimate|budget|point", key)])


def over_budget_before_approval(fixture: Project) -> None:
    """Author both stories over the policy's limit before the backlog is approved."""
    fixture.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 1})
    original = backlog_fixture._author_story

    def grown(story_path, test_plan, story_id):
        original(story_path, test_plan, story_id)
        text = story_path.read_text(encoding="utf-8")
        anchor = "- [ ] Every cited criterion has an observable passing result.\n"
        story_path.write_text(text.replace(anchor, anchor + "- [ ] A second result.\n"),
                              encoding="utf-8")

    with mock.patch.object(backlog_fixture, "_author_story", side_effect=grown):
        backlog_fixture.make_approved_backlog(fixture.docs, "ST-001", "ST-002")


class ApprovalTests(unittest.TestCase):
    """A story over budget never blocks approval, at any limit."""

    def setUp(self) -> None:
        self.fx = Project(self, build=over_budget_before_approval)

    def test_an_over_budget_backlog_approves_and_checks_approved(self):
        code, output = self.fx.check("--approved")
        result = json.loads(output)
        self.assertEqual((code, result["ok"]), (0, True), result["errors"])
        self.assertEqual(result["story_size"]["over_budget_stories"], ["ST-001", "ST-002"])


def next_round(review: Path, body: str | None = None) -> Path:
    """Write a draft next round of a review as the Product Owner does, by hand."""
    props, text = backlog_compile.parse_front_matter(review)
    number = int(props["round"]) + 1
    props = backlog_compile.without_policy_pin(props)
    props["round"] = number
    props["aliases"] = [props["aliases"][0].rsplit("-", 1)[0] + f"-{number:03d}"]
    backlog_compile.status_tag(props, "draft")
    for key in ("approved_at_utc", "source_hash"):
        props.pop(key, None)
    path = review.with_name(review.name.replace(f"round-{number - 1}-", f"round-{number}-"))
    path.write_text(backlog_compile.front_matter(props, text if body is None else body),
                    encoding="utf-8")
    return path


class SizeExceptionApprovalTests(unittest.TestCase):
    """While the budget is on, approval seals only Size Exceptions check accepts."""

    def setUp(self) -> None:
        self.fx = Project(self)

    def revise(self, *rows: str) -> Path:
        """Open revision 2 with new root and epic review rounds; the epic round keeps rows."""
        root = self.fx.docs / "backlog/backlog.md"
        props, body = backlog_compile.parse_front_matter(root)
        props = backlog_compile.without_policy_pin(props)
        props["revision"] = 2
        backlog_compile.status_tag(props, "draft")
        for key in ("approved_at_utc", "source_hash", "package_hash"):
            props.pop(key, None)
        root.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        next_round(self.fx.docs / "backlog/reviews/round-1-backlog-review.md")
        epic = self.fx.docs / f"{EPIC}/reviews/round-1-epic-review.md"
        body = backlog_compile.parse_front_matter(epic)[1]
        table = ("\n## Size Exceptions\n\n| story | measure | reason |\n|---|---|---|\n"
                 + "\n".join(rows) + "\n")
        return next_round(epic, body.replace("\n## Findings", table + "\n## Findings", 1))

    def approve(self) -> tuple[int, dict]:
        code, output = quiet(backlog_compile.approve, SimpleNamespace(docs=str(self.fx.docs)))
        return code, json.loads(output)

    def test_approval_refuses_a_size_exception_check_rejects_before_any_write(self):
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 1})
        self.fx.commit()
        link = f"[[{EPIC}/stories/st-001/story\\|ST-001]]"
        review = self.revise(f"| {link} | story_points | TBD |")
        path = review.relative_to(self.fx.docs).as_posix()
        before = backlog_compile.snapshot_tree(self.fx.docs)
        code, result = self.approve()
        self.assertEqual(code, 1, result)
        for finding in (f"{path} size exception 1 names undeclared measure story_points; the"
                        " measures are acceptance_criteria, contract_deltas,"
                        " implementation_roles, test_scenarios",
                        f"{path} size exception 1 needs a concrete reason"):
            self.assertIn(finding, result["errors"])
        self.assertEqual(backlog_compile.snapshot_tree(self.fx.docs), before)
        # A row check accepts approves, and the approved backlog checks clean.
        text = review.read_text(encoding="utf-8").replace(
            "| story_points | TBD |",
            "| acceptance_criteria | The two results share one boundary one review covers. |")
        review.write_text(text, encoding="utf-8")
        code, result = self.approve()
        self.assertEqual(code, 0, result)
        code, output = self.fx.check("--approved")
        self.assertEqual((code, json.loads(output)["errors"]), (0, []))

    def test_at_the_default_approval_never_reads_the_table(self):
        self.fx.choose()
        self.fx.commit()
        link = f"[[{EPIC}/stories/st-001/story\\|ST-001]]"
        self.revise(f"| {link} | story_points | TBD |")
        code, result = self.approve()
        self.assertEqual(code, 0, result)


def two_epic_backlog(fixture: Project) -> None:
    """Two epics: ST-001 and ST-002 in EP-001, ST-003 in EP-002."""
    docs = fixture.docs
    backlog_fixture.make_approved_backlog(docs, "ST-001", "ST-002", "ST-003")
    quiet(backlog_compile.stub_epic, SimpleNamespace(
        docs=str(docs), slug="second", id="EP-002", title="Second",
        goal="Deliver a separate customer outcome."))
    old, new = f"{EPIC}/stories/st-003", "backlog/epics/second/stories/st-003"
    (docs / old).rename(docs / new)
    for path in docs.rglob("*.md"):
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    path = docs / new / "story.md"
    props, body = backlog_compile.parse_front_matter(path)
    props["derives_from"] = ["[[backlog/epics/second/epic|EP-002]]"]
    path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
    record, _errors = backlog_compile.collect(docs)
    root = backlog_compile.latest(record["backlog_reviews"])
    root["props"]["related_to"] = [f"[[{item['path'][:-3]}|{item['id']}]]"
                                   for item in record["epics"]]
    (docs / root["path"]).write_text(
        backlog_compile.front_matter(root["props"], root["body"]), encoding="utf-8")
    sections = backlog_compile.backlog_contract()["required_epic_review_sections"]
    for epic in record["epics"]:
        review = backlog_compile.latest(epic["reviews"])
        props = review["props"]
        props.update(verdict="approved", dependency_refs=[], scenario_refs=[
            scenario for item in epic["stories"] for scenario in item["scenario_ids"]])
        props["verifies"] = [f"[[{path[:-3]}|{item['id']}]]" for item in epic["stories"]
                             for path in (item["path"], item["test_plan"])]
        (docs / review["path"]).write_text(backlog_compile.front_matter(
            props, backlog_fixture._complete_review_body(props["title"], sections)),
            encoding="utf-8")
    fixture.test.assertEqual(backlog_compile.collect(docs)[1], [])


class ReviewManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Project(self, build=two_epic_backlog)

    def test_the_manifest_carries_the_measures_of_its_scope_as_given_facts(self):
        plain = backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")
        self.assertNotIn("check", plain)
        # A switch the manifest never reads adds no measures.
        self.fx.choose(("mechanical_pass_tier", "mechanical"))
        self.assertEqual(backlog_review_inputs.manifest(self.fx.docs, epic="EP-001"), plain)
        self.fx.choose((SWITCH, "propose_split"), limits={"implementation_roles": 1})
        epic = backlog_review_inputs.manifest(self.fx.docs, epic="EP-001")
        # At single_reader the measures are the whole check block.
        self.assertEqual(sorted(epic["check"]), ["story_size"])
        self.assertEqual(sorted(epic["check"]["story_size"]["stories"]), ["ST-001", "ST-002"])
        self.assertEqual(epic["check"]["story_size"]["limits"], {"implementation_roles": 1})
        root = backlog_review_inputs.manifest(self.fx.docs)
        self.assertEqual(sorted(root["check"]["story_size"]["stories"]),
                         ["ST-001", "ST-002", "ST-003"])
        self.assertEqual(backlog_review_inputs.manifest(
            self.fx.docs, epic="EP-001", expected_hash=epic["source_hash"]), epic)
        # A changed limit is a changed input: the manifest goes stale.
        self.fx.choose((SWITCH, "propose_split"), limits={"implementation_roles": 2})
        with self.assertRaisesRegex(backlog_review_inputs.InputError, "stale"):
            backlog_review_inputs.manifest(self.fx.docs, epic="EP-001",
                                           expected_hash=epic["source_hash"])


class SplitTests(unittest.TestCase):
    """An accepted split moves criteria and scenarios verbatim into a stubbed story."""

    def setUp(self) -> None:
        self.fx = Project(self)

    MOVED_LINE = "- [ ] A registered account receives its second identity factor."

    def grow(self) -> str:
        """Give ST-001 a second criterion with its own acceptance line and scenario."""
        registry = self.fx.docs / "business-analysis/delivery/_generated/registry.json"
        data = json.loads(registry.read_text(encoding="utf-8"))
        data["ids"]["AC-DEL-002"] = dict(data["ids"]["AC-DEL-001"])
        registry.write_text(json.dumps(data), encoding="utf-8")
        path = self.fx.story_path(1)
        props, body = backlog_compile.parse_front_matter(path)
        props["criterion_refs"] = [CRITERION, SECOND]
        path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        self.fx.add_criteria(1, self.MOVED_LINE)
        plan = self.fx.docs / f"{EPIC}/stories/st-001/test-plan.md"
        text = plan.read_text(encoding="utf-8")
        block = text[text.index("## ST-001-TS-001"):text.index("<!-- sec: nav -->")].rstrip()
        moved = (block.replace("ST-001-TS-001", "ST-001-TS-002").replace(CRITERION, SECOND)
                 .replace("the customer submits the account request",
                          "the customer confirms the second identity factor"))
        text = text.replace(block, block + "\n\n" + moved)
        text = text.replace("| empty | covered | ST-001-TS-001 |",
                            "| empty | covered | ST-001-TS-001, ST-001-TS-002 |")
        plan.write_text(text, encoding="utf-8")
        self.refresh_review()
        return moved.split("\n", 1)[1]

    def refresh_review(self) -> None:
        record, _errors = backlog_compile.collect(self.fx.docs)
        epic = record["epics"][0]
        review = self.fx.docs / backlog_compile.latest(epic["reviews"])["path"]
        props, body = backlog_compile.parse_front_matter(review)
        props["verifies"] = [f"[[{path[:-3]}|{item['id']}]]" for item in epic["stories"]
                             for path in (item["path"], item["test_plan"])]
        props["scenario_refs"] = [scenario for item in epic["stories"]
                                  for scenario in item["scenario_ids"]]
        review.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")

    def split(self, implements: list[str]) -> None:
        """Apply the accepted split of AC-DEL-002 from ST-001 into ST-003."""
        args = SimpleNamespace(
            docs=str(self.fx.docs), epic="delivery-fixture", slug="st-003", id="ST-003",
            title="Second identity factor", scope="Confirm the second identity factor.",
            work_kind="feature", criterion_ref=[SECOND], experience_ref=[EXPERIENCE],
            evidence_ref=[], uses_design=[DESIGN], constrained_by=[CONSTRAINT],
            implements=implements)
        self.assertEqual(quiet(backlog_compile.stub_story, args)[0], 0)
        source = self.fx.story_path(1)
        text = source.read_text(encoding="utf-8").replace(self.MOVED_LINE + "\n", "", 1)
        props, body = backlog_compile.parse_front_matter_text(text)
        props["criterion_refs"] = [CRITERION]
        source.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        source_plan = self.fx.docs / f"{EPIC}/stories/st-001/test-plan.md"
        text = source_plan.read_text(encoding="utf-8")
        start = text.index("## ST-001-TS-002")
        moved_block = text[start:text.index("<!-- sec: nav -->")].rstrip()
        text = text.replace("\n\n" + moved_block, "", 1).replace(
            "| empty | covered | ST-001-TS-001, ST-001-TS-002 |",
            "| empty | covered | ST-001-TS-001 |")
        source_plan.write_text(text, encoding="utf-8")
        target = self.fx.story_path(3)
        target_plan = target.with_name("test-plan.md")
        text = target.read_text(encoding="utf-8")
        for old, new in {
                "Describe the observable user or business value.":
                    "Customers protect their account with a second identity factor.",
                "List behavior deliberately excluded from this story.":
                    "Account registration stays in its own story.",
                "- backend_developer: Own implementation and integration.":
                    "- backend_developer: Implement the second-factor confirmation.",
                "- [ ] Map every cited criterion to an observable result.": self.MOVED_LINE,
                "Record delivery constraints without execution state.":
                    "Deliver beside the account registration boundary.",
                "priority_reason: Required for the epic outcome.":
                    "priority_reason: The second factor completes the approved account outcome.",
        }.items():
            text = text.replace(old, new)
        target.write_text(text, encoding="utf-8")
        text = target_plan.read_text(encoding="utf-8")
        stub = text[text.index("## ST-003-TS-001"):text.index("<!-- sec: nav -->")].rstrip()
        text = text.replace(stub, moved_block.replace("## ST-001-TS-002", "## ST-003-TS-001", 1))
        for coverage in backlog_compile.SCENARIO_COVERAGE_CLASSES:
            text = text.replace(
                f"| {coverage} | not_applicable | - | {backlog_compile.COVERAGE_REASON_STUB} |",
                f"| {coverage} | covered | ST-003-TS-001 | |" if coverage == "empty" else
                f"| {coverage} | not_applicable | - | The factor confirmation has no"
                f" {coverage} path. |")
        target_plan.write_text(text, encoding="utf-8")
        self.refresh_review()

    def assert_split(self, moved_body: str) -> None:
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["errors"]), (0, []))
        size = result["story_size"]
        self.assertEqual(size["over_budget_stories"], [])
        self.assertEqual(size["stories"]["ST-001"]["measures"]["acceptance_criteria"]["value"], 1)
        self.assertEqual(size["stories"]["ST-003"]["measures"]["acceptance_criteria"]["value"], 1)
        record, _errors = backlog_compile.collect(self.fx.docs)
        covering = [item["id"] for item in record["stories"] if SECOND in item["criteria"]]
        self.assertEqual(covering, ["ST-003"])
        self.assertIn(self.MOVED_LINE + "\n", self.fx.story_path(3).read_text(encoding="utf-8"))
        self.assertNotIn(self.MOVED_LINE, self.fx.story_path(1).read_text(encoding="utf-8"))
        # The scenario moved byte for byte; only its heading took the new story's id.
        plan = self.fx.story_path(3).with_name("test-plan.md").read_text(encoding="utf-8")
        self.assertIn("## ST-003-TS-001\n" + moved_body, plan)

    def over_budget(self) -> str:
        moved_body = self.grow()
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 1})
        code, output = self.fx.check()
        result = json.loads(output)
        self.assertEqual((code, result["errors"]), (0, []))
        self.assertEqual(result["story_size"]["over_budget_stories"], ["ST-001"])
        return moved_body

    def test_a_split_in_a_legacy_backlog_moves_criteria_and_scenarios_verbatim(self):
        moved_body = self.over_budget()
        self.split([])
        props = backlog_compile.parse_front_matter(self.fx.story_path(3))[0]
        self.assertNotIn("origin_mode", props)
        self.assert_split(moved_body)

    def test_a_split_in_a_requirement_mode_backlog_moves_criteria_and_scenarios_verbatim(self):
        requirement = self.fx.docs / "requirements/req-001-account-access.md"
        requirement.parent.mkdir(parents=True)
        requirement.write_text("---\ntype: requirement\nid: REQ-001\ntitle: Account access\n"
                               "status: approved\n---\n\n# Account access\n", encoding="utf-8")
        link = "[[requirements/req-001-account-access|REQ-001]]"
        backlog = self.fx.docs / "backlog/backlog.md"
        props, body = backlog_compile.parse_front_matter(backlog)
        props.pop("legacy_contract", None)
        props.update(planning_mode="requirement", requirement_ref="REQ-001")
        backlog.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        for number in (1, 2):
            path = self.fx.story_path(number)
            props, body = backlog_compile.parse_front_matter(path)
            props.update(origin_mode="requirement", implements=[link])
            path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        # Upstream receipts are out of scope here; the backlog rules are real.
        with mock.patch.object(backlog_compile, "planning_package_findings",
                               return_value=("requirement", [], [])), \
                mock.patch.object(backlog_compile, "validate_experience_ref", return_value=None):
            moved_body = self.over_budget()
            self.split([])
            props = backlog_compile.parse_front_matter(self.fx.story_path(3))[0]
            self.assertEqual((props["origin_mode"], props["implements"]), ("requirement", [link]))
            self.assert_split(moved_body)


def committed_backlog_with_dod(fixture: Project) -> None:
    """The approved backlog, a workflow, a commit and an approved Definition of Done."""
    approved_backlog(fixture)
    workflows = fixture.root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "tests.yml").write_text(
        "on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: make test\n", encoding="utf-8")
    fixture.commit()
    dod = SimpleNamespace(docs=str(fixture.docs), title="Project", file=None)
    fixture.test.assertEqual(quiet(delivery_compile.init_dod, dod)[0], 0)
    fixture.test.assertEqual(quiet(delivery_compile.approve_dod, dod)[0], 0)


class DeliveryProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Project(self, build=committed_backlog_with_dod)

    def propose(self, slug: str) -> dict:
        args = SimpleNamespace(docs=str(self.fx.docs), id=None, slug=slug, goal="Authenticate",
                               outcome="Users sign in", target_branch="main", story=["ST-001"])
        code, output = quiet(delivery_compile.init_delivery, args)
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def test_init_shows_the_selected_stories_measures_read_only(self):
        released = self.propose("released")
        self.assertEqual(sorted(released), ["id", "ok", "path", "slug", "stories"])
        self.fx.choose((SWITCH, "propose_split"),
                       limits={"acceptance_criteria": 1, "implementation_roles": 1})
        result = self.propose("budgeted")
        self.assertEqual(result["stories"], ["ST-001"])
        self.assertEqual(result["story_size"], {
            "switch": SWITCH, "value": "propose_split",
            "limits": {"acceptance_criteria": 1, "implementation_roles": 1},
            "over_budget_stories": [],
            "stories": {"ST-001": {"measures": {
                "acceptance_criteria": {"value": 1, "limit": 1},
                "contract_deltas": {"value": 0},
                "implementation_roles": {"value": 1, "limit": 1},
                "test_scenarios": {"value": 1}},
                "over_budget": [], "size_exceptions": []}}})
        # The display writes nothing: the Items carry exactly the released fields.
        items = sorted(self.fx.docs.glob("delivery/deliveries/*/items/st-001/item.md"))
        self.assertEqual(len(items), 2)
        first, second = (delivery_compile.split_note(path)[0] for path in items)
        self.assertEqual(sorted(first), sorted(second))


def committed_brief(fixture: Project) -> None:
    (fixture.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
    fixture.commit()


class TaskBindingTests(unittest.TestCase):
    """Only an approved policy at `propose_split` binds the reference and the
    measures it reads."""

    TASKS = (("backlog-plan", "product-owner", "revise"),
             ("backlog-plan", "backlog-reviewer", "review"),
             ("delivery-plan", "product-owner", "revise"))

    def setUp(self) -> None:
        self.fx = Project(self, build=committed_brief)

    def manifests(self) -> dict:
        return {task: task_inputs.manifest(entry=task[0], role=task[1], mode=task[2],
                                           project=self.fx.root)
                for task in self.TASKS}

    def test_the_reference_and_the_measures_bind_only_at_propose_split(self):
        plain = self.manifests()
        for task, result in plain.items():
            with self.subTest(task=task, policy="missing"):
                for path in (REFERENCE, MEASURES):
                    self.assertNotIn(path, result["required_reads"])
                    self.assertNotIn(path, [item["path"] for item in result["instructions"]])
        self.fx.choose((SWITCH, "propose_split"), limits={"acceptance_criteria": 12})
        for task, result in self.manifests().items():
            with self.subTest(task=task, policy="propose_split"):
                self.assertEqual(sorted(set(result["required_reads"])
                                        - set(plain[task]["required_reads"])),
                                 sorted([MEASURES, REFERENCE]))
        self.fx.choose()
        for task, result in self.manifests().items():
            with self.subTest(task=task, policy="off"):
                self.assertEqual(result["required_reads"], plain[task]["required_reads"])
                self.assertEqual(result["instructions"], plain[task]["instructions"])


if __name__ == "__main__":
    unittest.main()
