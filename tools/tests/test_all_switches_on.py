"""One all-on run beside the all-default golden: every process switch at its
non-default value at once, story_size_budget with owner-set limits.

Each switch is tested on its own elsewhere. This run proves the values compose:
every shipped task binds exactly the switch references of the switches its
entry's flows own, an approved backlog checks and derives an epic review
manifest under them, and one Delivery runs through its Review under them.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import backlog_compile  # noqa: E402
import backlog_review_inputs  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import operation_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

LIMITS = {"acceptance_criteria": 12, "test_scenarios": 20}
OWNER_GATES = "deliver/references/switch-owner_gates-two_fixed_gates.md"
LANES = "deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
PRE_HANDOFF = "deliver/references/switch-pre_handoff_regression-touched_suites.md"
OWN_TARGETS = "deliver/references/switch-own_target_reuse-spot_run.md"
GATE_FIRST = "qa-verification/references/switch-qa_gate_order-gate_first.md"
BUNDLE = "execution_planning-single_source_bundle.md"
REVIEW = ("challenge-review/references/switch-mechanical_pass_tier-mechanical.md",
          "challenge-review/references/switch-review_loop-blocking_delta.md",
          "challenge-review/references/switch-review_panels-lens_panel.md")
PLANNING = ("execution-plan/references/switch-delivery_path-light_when_eligible.md",
            "execution-plan/references/switch-" + BUNDLE,
            "execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md")
BOUNDED = "backlog-plan/references/switch-review_manifest_scope-bounded.md"
# The backlog-plan switch references every backlog-plan task binds.
BACKLOG = (BOUNDED,
           "backlog-plan/references/switch-epic_review_cadence-overlap_calibration.md",
           "backlog-plan/references/switch-review_scope_record-both_scopes.md",
           "backlog-plan/references/switch-remediation_writers-per_epic.md")
SPLIT = "product-planning/references/switch-story_size_budget-propose_split.md"
# The switch references each shipped task binds, by entry and role. A task
# binds a reference of a switch that owns one of its entry's flows, from a
# skill it selects or, for owner_gates, from any skill.
EXPECTED = {
    **{f"{entry}:{role}": [] for entry, role in (
        ("business-analysis", "analysis-challenger"), ("business-analysis", "business-analyst"),
        ("business-analysis", "domain-expert"), ("demo", "ux-designer"),
        ("design-system", "ux-designer"), ("experience-design", "experience-reviewer"),
        ("experience-design", "ux-designer"), ("issue-report", None),
        ("organize-docs", "business-analyst"), ("requirement", "business-analyst"),
        ("setup", "delivery-coordinator"), ("sketch", "ux-designer"),
        ("solution-design", "domain-expert"), ("solution-design", "solution-architect"))},
    "backlog-plan:product-owner": [*BACKLOG, SPLIT],
    "backlog-plan:backlog-reviewer": sorted([*BACKLOG, SPLIT, *REVIEW]),
    "backlog-plan:business-analyst": list(BACKLOG),
    "backlog-plan:qa-engineer": list(BACKLOG),
    **{f"configure:{role}": ["configure/references/switch-" + BUNDLE, OWNER_GATES]
       for role in ("delivery-coordinator", "devops-engineer", "qa-engineer")},
    **{f"deliver:{role}": [LANES, OWNER_GATES, PRE_HANDOFF, OWN_TARGETS]
       for role in ("backend-developer", "delivery-coordinator", "devops-engineer",
                    "frontend-developer")},
    "deliver:qa-engineer": [LANES, OWNER_GATES, PRE_HANDOFF, OWN_TARGETS, GATE_FIRST],
    "deliver:code-reviewer": ["code-review/references/switch-code_review_panel-beside_official.md",
                              "code-review/references/switch-review_loop-blocking_delta.md",
                              LANES, OWNER_GATES, PRE_HANDOFF, OWN_TARGETS],
    "deliver:software-architect": [LANES, OWNER_GATES, PRE_HANDOFF, OWN_TARGETS,
                                   "software-architecture/references/switch-" + BUNDLE],
    "delivery-plan:delivery-coordinator": [
        OWNER_GATES, "delivery-plan/references/switch-delivery_path-light_when_eligible.md"],
    "delivery-plan:product-owner": [
        OWNER_GATES, "delivery-plan/references/switch-delivery_path-light_when_eligible.md", SPLIT],
    "design-system:design-system-reviewer": list(REVIEW[1:]),
    **{f"execution-plan:{role}": [OWNER_GATES, *PLANNING]
       for role in ("delivery-coordinator", "devops-engineer", "qa-engineer")},
    "execution-plan:software-architect": [OWNER_GATES, *PLANNING,
                                          "software-architecture/references/switch-" + BUNDLE],
    "solution-design:solution-reviewer": list(REVIEW),
}
WORKFLOW = ("on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: make test\n")


def quiet(call, *args) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


class AllSwitchesOnTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.project = Path(temporary.name).resolve()
        self.docs = self.project / "workspace/docs"
        init_repository(self.project, initial_branch="main")
        for key, value in (("user.email", "test@example.com"), ("user.name", "Test"),
                           ("core.autocrlf", "false")):
            self.git("config", key, value)
        (self.docs / "maps").mkdir(parents=True)
        self.registry = process_policy.load_registry()
        self.values = {switch: next(value for value in spec["values"] if value != spec["default"])
                       for switch, spec in self.registry.items()}
        self.policy("init")
        for switch, value in self.values.items():
            self.policy("set", "--switch", switch, "--value", value)
        for parameter, limit in LIMITS.items():
            self.policy("set", "--switch", "story_size_budget", "--parameter", parameter,
                        "--value", str(limit))
        self.policy("approve")
        self.commit("Approve a Process Policy with every switch on")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)

    def commit(self, message: str) -> None:
        self.git("add", "--all")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def policy(self, *argv: str) -> dict:
        code, output = quiet(process_policy.main, [argv[0], "--docs", str(self.docs), *argv[1:]])
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def test_the_policy_selects_every_non_default_value(self):
        # A switch with three values would need a run per value; every switch has two.
        self.assertTrue(all(len(spec["values"]) == 2 for spec in self.registry.values()))
        for switch, value in self.values.items():
            with self.subTest(switch=switch):
                self.assertEqual(self.policy("value", "--switch", switch)["value"], value)
        self.assertEqual(self.policy("value", "--switch", "story_size_budget")["parameters"],
                         LIMITS)

    def test_every_shipped_task_binds_the_references_of_the_switches_its_flows_own(self):
        bound = {}
        for entry, route in sorted(task_inputs.catalog()["entries"].items()):
            for role in route["roles"] or [None]:
                result = task_inputs.manifest(
                    entry=entry, role=role, mode="review",
                    project=self.project if route["project_state"] else None)
                bound[f"{entry}:{role}"] = sorted(
                    path.removeprefix("skill-content/") for path in result["required_reads"]
                    if "/references/switch-" in path)
        self.maxDiff = None
        self.assertEqual(bound, {key: sorted(value) for key, value in EXPECTED.items()})
        # Every switch reference the package ships reaches at least one task.
        shipped = {path.relative_to(ROOT / "plugins/software-engineering-team/skill-content")
                   .as_posix() for path in (ROOT / "plugins/software-engineering-team/skill-content")
                   .glob("*/references/switch-*.md")}
        self.assertEqual(shipped, {path for paths in bound.values() for path in paths})

    def test_the_backlog_checks_and_derives_an_epic_review_manifest_with_every_switch_on(self):
        make_approved_backlog(self.docs)
        code, output = quiet(backlog_compile.main,
                             ["check", "--docs", str(self.docs), "--json", "--approved"])
        result = json.loads(output)
        self.assertEqual((code, result["errors"]), (0, []), result)
        self.assertEqual(result["story_size"]["limits"], LIMITS)
        manifest = backlog_review_inputs.manifest(self.docs, epic="EP-001")
        # The manifest names the panel and the bounded scope it was derived
        # under and carries the panel's compiler facts and the story measures.
        self.assertEqual((manifest["review_panels"], manifest["review_manifest_scope"]),
                         ("lens_panel", "bounded"))
        self.assertEqual(sorted(manifest["check"]), ["counts", "relation_audit", "review_note",
                                                     "source_errors", "stories", "story_size"])
        self.assertEqual(backlog_review_inputs.manifest(
            self.docs, epic="EP-001", expected_hash=manifest["source_hash"]), manifest)

    def test_a_delivery_runs_through_its_review_with_every_switch_on(self):
        (self.project / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        governance = type("Args", (), {"docs": str(self.docs), "max_parallel": 1})
        for call in (delivery_governance.init, delivery_governance.approve):
            self.assertEqual(quiet(call, governance)[0], 0)
        make_approved_backlog(self.docs)
        workflow = self.project / ".github/workflows/tests.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(WORKFLOW, encoding="utf-8")
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        for call in (delivery_compile.init_dod, delivery_compile.approve_dod):
            self.assertEqual(quiet(call, dod)[0], 0)
        contract = type("Args", (), {"docs": str(self.docs), "kind": "verification",
                                     "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"]})
        self.assertEqual(quiet(operation_compile.init, contract)[0], 0)
        path = operation_compile.contract_path(self.docs, "verification")
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(quiet(operation_compile.approve, contract)[0], 0)
        self.commit("Approve the project sources")
        scope = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": "Users sign in",
                                  "target_branch": "main", "story": ["AUTH-01"]})
        plan = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        review = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001",
                                   "reviewed_commit": "1" * 40,
                                   "reviewed_integration_commit": "2" * 40})
        steps = [("init-delivery", delivery_compile.init_delivery, scope),
                 ("check", delivery_compile.check_delivery, plan)]
        for name, call, args in steps:
            code, output = quiet(call, args)
            self.assertEqual(code, 0, f"{name}: {output}")
        root = delivery_compile.find_delivery(self.docs, "DLV-001")
        item = root / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        # Under parallel_lanes_v1 a one-lane Item also declares its lane scope.
        props["lane_scopes"] = ["backend_developer:src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        steps = [("approve-scope", delivery_compile.approve_scope, plan),
                 ("approve-execution", delivery_compile.approve_execution, plan),
                 ("check", delivery_compile.check_delivery, plan),
                 ("status", delivery_compile.status, plan)]
        for name, call, args in steps:
            code, output = quiet(call, args)
            self.assertEqual(code, 0, f"{name}: {output}")
        # The Delivery pinned the policy, so it reads every value it set.
        pinned = delivery_compile.split_note(root / "delivery.md")[0]
        self.assertEqual({key: pinned[key] for key in process_policy.PIN_FIELDS},
                         process_policy.approved_snapshot(self.docs)[0])
        props, body = delivery_compile.split_note(item)
        props["status"] = "integrated"
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.commit("Plan DLV-001")
        for name, call, args in (("approve-review", delivery_compile.approve_review, review),
                                 ("check", delivery_compile.check_delivery, plan),
                                 ("render", delivery_compile.render, plan)):
            code, output = quiet(call, args)
            self.assertEqual(code, 0, f"{name}: {output}")
        self.assertEqual(delivery_compile.split_note(root / "delivery.md")[0]["status"], "review")
        # From the Review on the Delivery reads the values it pinned, whatever
        # the policy sets for the next Delivery. A switch no Delivery flow owns
        # is no part of the pin, so it reads the current policy.
        self.policy("begin-revision")
        for switch in self.values:
            self.policy("set", "--switch", switch, "--default")
        self.policy("approve")
        registry = process_policy.load_registry()
        pinned = process_policy.delivery_switches(registry)
        self.assertTrue(set(self.values) - pinned)
        for switch, value in self.values.items():
            with self.subTest(switch=switch):
                self.assertEqual(delivery_compile.delivery_switch_value(self.docs, "DLV-001", switch),
                                 value if switch in pinned else registry[switch]["default"])


if __name__ == "__main__":
    unittest.main()
