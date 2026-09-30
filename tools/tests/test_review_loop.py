"""Review loop: switch `review_loop` keeps every review loop as released at
`current` and, at `blocking_delta`, lets only critical and major findings start
a round, tracks minor findings as follow-ups and scopes a re-review to the open
blocking findings, the changed text and its dependency context."""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import process_policy
import task_inputs
from git_fixture import init_repository

REGISTRY = "skill-content/configure/data/process-switches.json"
DOCUMENT = "skill-content/challenge-review/references/switch-review_loop-blocking_delta.md"
CODE = "skill-content/code-review/references/switch-review_loop-blocking_delta.md"
PANEL = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
# flow: the switch reference its review step follows at blocking_delta.
ANCHORS = {
    "backlog-planning": DOCUMENT,
    "delivery-execution": CODE,
    "design-system": DOCUMENT,
    "operation": DOCUMENT,
    "solution-design": DOCUMENT,
}
# (entry, role, mode, added skills, the reference the task binds at blocking_delta)
REVIEW_TASKS = (
    ("configure", "devops-engineer", "review", ["challenge-review"], DOCUMENT),
    ("configure", "qa-engineer", "review", ["challenge-review"], DOCUMENT),
    ("configure", "qa-engineer", "revise", ["challenge-review"], DOCUMENT),
    ("design-system", "design-system-reviewer", "review", [], DOCUMENT),
    ("design-system", "ux-designer", "revise", ["challenge-review"], DOCUMENT),
    ("backlog-plan", "backlog-reviewer", "review", [], DOCUMENT),
    ("backlog-plan", "product-owner", "revise", ["challenge-review"], DOCUMENT),
    ("solution-design", "solution-reviewer", "review", [], DOCUMENT),
    ("solution-design", "solution-architect", "revise", ["challenge-review"], DOCUMENT),
    ("deliver", "code-reviewer", "review", [], CODE),
)
# step: (entry, calibration reader role, added skills, the step's writer roles)
CALIBRATION_READERS = {
    "backlog": ("backlog-plan", "backlog-reviewer", [], {"product-owner"}),
    "solution_design": ("solution-design", "solution-reviewer", [], {"solution-architect"}),
    "design_system": ("design-system", "design-system-reviewer", [], {"ux-designer"}),
    "operation_verification": ("configure", "devops-engineer", ["challenge-review"], {"qa-engineer"}),
    "operation_environment": ("configure", "qa-engineer", ["challenge-review"], {"devops-engineer"}),
    "code_review": ("deliver", "code-reviewer", [],
                    {"software-architect", "backend-developer", "frontend-developer", "devops-engineer"}),
}


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def flat(relative: str) -> str:
    return " ".join(read(relative).split())


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(root: Path) -> str:
    git(root, "add", "-A")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
    return git(root, "rev-parse", "HEAD")


def write(root: Path, relative: str, text: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def policy(docs: Path, *argv: str) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main([argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output.getvalue())


def bound(result: dict) -> set[str]:
    return {path for path in result["required_reads"] if "/references/switch-" in path}


class ReviewLoopRegistryTests(unittest.TestCase):
    def test_switch_ships_at_current_under_the_issue_flip_rule(self):
        switch = json.loads(read(REGISTRY))["switches"]["review_loop"]
        self.assertEqual(switch["issue"], 326)
        self.assertEqual([value["id"] for value in switch["values"]], ["current", "blocking_delta"])
        self.assertEqual(switch["default"], "current")
        self.assertEqual(switch["flows"], sorted(ANCHORS))
        self.assertNotIn("agent_variants", switch)
        # #326 states its own unit, so the owner's 3-Delivery default does not apply.
        self.assertEqual(switch["promotion"]["unit"],
                         "At least 5 reviewed documents across at least 2 flows: a contract"
                         " revision, an epic or root review, a Solution or Design System"
                         " review, or an Item code review.")
        for term in ("Median review-loop minutes at most 50% of the matching baseline",
                     "traced to a skipped minor round",
                     "an owner role and revisit trigger on every follow-up",
                     "the owner's approval"):
            with self.subTest(term=term):
                self.assertIn(term, switch["promotion"]["threshold"])
        for term in ("review-loop minutes", "rounds",
                     "minor findings fixed in a blocking pass against those recorded as follow-ups",
                     "calibration counts, claimed, confirmed, downgraded and invalid, with calibration"
                     " minutes"):
            with self.subTest(term=term):
                self.assertIn(term, switch["metric"])
        for term in ("traced to a skipped minor round or a calibration downgrade",
                     "the shadow judge confirming every downgrade"):
            with self.subTest(term=term):
                self.assertIn(term, switch["promotion"]["threshold"])


class ReviewLoopReferenceTests(unittest.TestCase):
    def test_references_apply_only_at_blocking_delta_and_are_never_linked(self):
        for reference, skill in ((DOCUMENT, "challenge-review"), (CODE, "code-review")):
            text = flat(reference)
            with self.subTest(reference=reference):
                self.assertIn("process switch `review_loop` at `blocking_delta`", text)
                self.assertIn("A task binds this file only when the project's Process Policy"
                              " selects that value", text)
                self.assertIn("at the default, `current`,", text)
                self.assertNotIn("switch-review_loop", read(f"skill-content/{skill}/SKILL.md"))

    def test_every_owning_flow_anchors_its_review_step(self):
        for flow, reference in ANCHORS.items():
            with self.subTest(flow=flow):
                text = flat(f"flows/{flow}.md")
                self.assertIn("Switch `review_loop`: at `blocking_delta`", text)
                self.assertIn(reference, text)

    def test_document_loop_states_severity_for_single_readers_and_panels(self):
        text = flat(DOCUMENT)
        for rule in (
            "to the step's single reviewer at `single_reader` and to every lens reader of the"
            " step's panel at `lens_panel`",
            "Only a critical or major finding blocks",
            "Design System and Operation contract reviews use this table",
            "the backlog Review findings section of"
            " `product-planning/references/structured-records.md`",
            "the Solution Severity table",
            "Imprecision that could mislead a careful reader is major, never minor",
            "The writer never lowers a returned severity",
            "adds `--skill challenge-review` to its task",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        for row in ("| `critical` | yes |", "| `major` | yes |", "| `minor` | no |"):
            with self.subTest(row=row):
                self.assertIn(row, text)

    def test_a_minor_finding_never_starts_a_round_and_becomes_a_follow_up(self):
        text = flat(DOCUMENT)
        for rule in (
            "A minor finding never blocks and never starts a round",
            "Fix it only in a writer pass that already carries a blocking fix",
            "the review note's `Accepted Minor Findings` table",
            "Solution Design: shown with its acceptance reason at the approval gate",
            "Design System: shown at the approval gate with its reason, the owner role"
            " `ux_designer` and its revisit trigger",
            "the contract's optional `Accepted Minor Findings` section",
            "| finding | owner_role | reason | revisit_trigger |",
            "`owner_role` is `qa_engineer` or `devops_engineer`",
            "`operation_compile.py` validates every row whenever the section is present",
            "Recording the section is not a change to the reviewed text and starts no re-review",
            "A critical or major finding never enters an `Accepted Minor Findings` section",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_re_review_reads_only_blocking_findings_changed_text_and_context(self):
        text = flat(DOCUMENT)
        for rule in (
            "commit the candidate before its first review",
            "the open critical and major findings, as returned, with their evidence",
            "the changed text: the diff between the reviewed revision and the fixed one",
            "its dependency context",
            "`--findings <record>`, `--base <reviewed commit>` and one `--input` per changed"
            " path and dependency-context note, and no other document of the step",
            "A backlog re-review keeps its regenerated manifest",
            "it never re-audits unchanged text",
            "at `single_reader` one fresh reviewer of the step's reader role",
            "at `lens_panel` the assignments that returned a blocking finding",
            "no clean extra round runs once no critical or major finding is open",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_code_review_minors_are_tracked_on_the_record(self):
        text = flat(CODE)
        for rule in (
            "including the System Architecture records that an Architecture Item's code review"
            " checks",
            "A MINOR finding never blocks and never starts a review cycle",
            "`owner_role`: the implementation role of this Item that follows it up",
            "`revisit_trigger`: the event that reopens it",
            "`delivery_verification.py result` refuses a code review result",
            "`approve-item-evidence` copies the open non-blocking findings into the"
            " `Deviations and Follow-ups` section of the Item's code review record",
            "`approve-review` lists the follow-ups of every integrated Item in the Delivery"
            " Review's `Lessons and Follow-up`",
            "the correctness, conformance and security passes all mandatory",
            "QA keeps its own blocking severities",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_document_calibration_confirms_a_claim_before_it_gates(self):
        text = flat(DOCUMENT)
        for rule in (
            "Before a verdict with an open critical or major finding becomes a gate, one fresh,"
            " read-only calibration reader checks every critical and major claim of the review",
            "Calibration is skipped when no critical or major finding is open",
            "a new critical or major finding of a re-review gets its own calibration before it gates",
            "The calibration reader is neither the writer nor a reader that returned a finding of"
            " the review",
            "Spawn a fresh instance of the claiming reviewer's role on that role's own tier:"
            " `backlog-reviewer`, `solution-reviewer` or `design-system-reviewer`",
            "`devops-engineer` for the Verification Contract and `qa-engineer` for the Environment"
            " Contract",
            "Never spawn a `-lens` variant for calibration, also when a lens panel returned the"
            " claims: a lower calibration tier needs its own data first",
            "adding `--findings <record of the claims>`",
            "Never pass the writer's triage or interpretation, another reply or the conversation",
            "`finding`, `claimed_severity`, `calibrated_severity` and `reason`",
            "calibration never raises a severity",
            "A row that lowers or invalidates a claim without citing the text is refused: that claim"
            " keeps its claimed severity",
            "Only confirmed critical and major findings keep the verdict at `changes_requested`",
            "The writer never changes a returned or calibrated severity",
            "`Severity Calibration` section placed before `Verdict`",
            "they show the rows with the verdict at the approval gate",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_code_review_calibration_is_enforced_at_registration(self):
        text = flat(CODE)
        for rule in (
            "one fresh, read-only calibration reader checks every such claim that no earlier"
            " calibration ruled",
            "Calibration is skipped when the result has none",
            "Spawn a fresh `code-reviewer` on its own tier, neither an implementation writer nor the"
            " reviewer that returned the claims",
            "The claiming reviewer's result is not registered yet, so the implementation writer"
            " stays idle",
            "`reason` cites the candidate as `path:line`",
            "Credentials or secrets that reach a client artifact or a log stay critical",
            "Attach the rows unchanged as the result's `calibration` list",
            "Only confirmed claims gate",
            "Neither the claiming reviewer nor the writer changes a severity",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_calibration_runs_on_the_claiming_reviewers_own_tier(self):
        # The only calibration evidence comes from a judge on the strongest tier;
        # a lower calibration tier needs its own data first (#326, 30 Sep 2026).
        calibration = flat(DOCUMENT).split("## Calibration", 1)[1]
        self.assertNotRegex(calibration, r"`[a-z-]+-lens`")
        switch = json.loads(read(REGISTRY))["switches"]["review_loop"]
        self.assertNotIn("agent_variants", switch)
        self.assertIn("one fresh calibration reader of the claiming reviewer's role, on that"
                      " role's own tier, confirms it", switch["values"][1]["tradeoffs"])
        orchestration = " ".join((ROOT / "docs/orchestration.md").read_text(encoding="utf-8").split())
        self.assertIn("It runs as the claiming reviewer's role on that role's own tier, never as a"
                      " `-lens` variant", orchestration)
        for step, (_entry, role, _skills, _writers) in CALIBRATION_READERS.items():
            with self.subTest(step=step):
                tier = re.search(r"(?m)^reasoning: (\S+)$", read(f"agents/{role}.md")).group(1)
                self.assertNotIn(tier, {"lens", "mechanical"})

    def test_only_review_panels_start_the_lens_variants(self):
        variants = json.loads(read(REGISTRY))["switches"]["review_panels"]["agent_variants"]
        self.assertEqual(variants["lens_panel"]["agents"],
                         ["backlog-reviewer", "design-system-reviewer", "solution-reviewer"])
        self.assertEqual(variants["lens_panel"]["description"],
                         "Lens-tier reader variant for review panels.")
        verbs = {"claude": "spawn", "codex": "start"}
        for host, verb in verbs.items():
            contract = " ".join((ROOT / "platforms" / host / "software-engineering-team"
                                 / "host-contract.md").read_text(encoding="utf-8").split())
            with self.subTest(host=host):
                self.assertIn(f"only review panels under switch `review_panels` at `lens_panel`"
                              f" {verb} them, and the reviewers themselves keep their own tier",
                              contract)
                self.assertNotIn("calibration reader", contract)

    def test_default_path_instructions_name_no_review_loop_value(self):
        for path in TEAM.rglob("*.md"):
            relative = path.relative_to(TEAM).as_posix()
            if path.name.startswith("switch-") or relative.startswith("flows/"):
                continue
            with self.subTest(path=relative):
                self.assertNotIn("blocking_delta", path.read_text(encoding="utf-8"))

    def test_qa_blocking_severities_are_unchanged(self):
        policy_data = json.loads(read("skill-content/deliver/data/delivery-verification-policy.json"))
        self.assertEqual(policy_data["blocking_severities"],
                         ["critical", "major", "high", "medium", "P0", "P1", "P2"])
        self.assertEqual(policy_data["nonblocking_severities"], ["minor", "low", "info", "P3"])

    def test_docs_describe_both_values(self):
        orchestration = " ".join((ROOT / "docs/orchestration.md").read_text(encoding="utf-8").split())
        architecture = " ".join((ROOT / "docs/architecture.md").read_text(encoding="utf-8").split())
        invariant = architecture.split(" 14. ", 1)[1].split(" 15. ", 1)[0]
        for text in (orchestration, invariant):
            with self.subTest(text=text[:40]):
                self.assertIn("Process switch `review_loop`", text)
                self.assertIn("`current`", text)
                self.assertIn("`blocking_delta`", text)
                self.assertIn("re-review reads only the open blocking findings, the changed"
                              " text and its dependency context", text)
                self.assertIn("one fresh, read-only calibration reader", text)


class ReviewLoopTaskInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        init_repository(self.root)
        git(self.root, "config", "core.autocrlf", "false")
        write(self.root, ".gitignore", ".agentrof/\n")
        write(self.root, "brief.md", "Accepted intent.\n")
        commit(self.root)
        self.docs = self.root / "workspace" / "docs"

    def manifests(self) -> dict:
        return {task[:3]: task_inputs.manifest(entry=task[0], role=task[1], mode=task[2],
                                               project=self.root, skills=task[3])
                for task in REVIEW_TASKS}

    def test_review_tasks_bind_the_loop_only_at_blocking_delta_with_either_panel_value(self):
        states = (
            ("single_reader", "current", ()),
            ("single_reader", "blocking_delta", ("review_loop", "--value", "blocking_delta")),
            ("lens_panel", "blocking_delta", ("review_panels", "--value", "lens_panel")),
            ("lens_panel", "current", ("review_loop", "--default")),
        )
        for panels, loop, change in states:
            if change:
                policy(self.docs, "begin-revision" if process_policy.path_for(self.docs).exists() else "init")
                policy(self.docs, "set", "--switch", *change)
                policy(self.docs, "approve")
            manifests = self.manifests()
            for task in REVIEW_TASKS:
                expected = set()
                if loop == "blocking_delta":
                    expected.add(task[4])
                if panels == "lens_panel" and "challenge-review" in (
                        task[3] + task_inputs.catalog()["required_role_skills"][task[1]]):
                    expected.add(PANEL)
                with self.subTest(panels=panels, loop=loop, task=task[:3]):
                    self.assertEqual(bound(manifests[task[:3]]), expected)

    def test_the_calibration_reader_is_a_fresh_read_only_non_writer(self):
        claims = write(self.root, ".agentrof/agent-marketplace/.runtime/review-loop/claims.md",
                       "| id | severity | evidence |\n|---|---|---|\n| F-3 | major | Scope repeats. |\n")
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "approve")
        for panels in ("single_reader", "lens_panel"):
            if panels == "lens_panel":
                for step in (("begin-revision",),
                             ("set", "--switch", "review_panels", "--value", "lens_panel"),
                             ("approve",)):
                    policy(self.docs, *step)
            for step, (entry, role, skills, writers) in CALIBRATION_READERS.items():
                result = task_inputs.manifest(entry=entry, role=role, mode="review",
                                              project=self.root, skills=skills, findings=claims)
                with self.subTest(panels=panels, step=step):
                    self.assertNotIn(role, writers)
                    self.assertEqual(result["write_boundary"], "read_only")
                    self.assertEqual(result["write_scope"]["allowed_write_area"], [])
                    self.assertEqual(result["open_findings"], claims)
                    self.assertIn(CODE if step == "code_review" else DOCUMENT,
                                  result["required_reads"])

    def test_re_review_task_binds_only_the_findings_the_diff_and_the_context(self):
        contract = write(self.root, "workspace/docs/operation/verification-contract.md",
                         "---\ntype: verification-contract\n---\n\n# Contract\n\nRun make test.\n")
        decision = write(self.root, "workspace/docs/solution-design/decisions/api.md",
                         "---\ntype: decision\n---\n\n# API\n")
        other = write(self.root, "workspace/docs/operation/environment-contract.md",
                      "---\ntype: environment-contract\n---\n\n# Environment\n")
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "approve")
        reviewed = commit(self.root)
        write(self.root, contract, "---\ntype: verification-contract\n---\n\n# Contract\n\n"
                                   "Run make test from the repository root.\n")
        findings = write(self.root, ".agentrof/agent-marketplace/.runtime/review-loop/open.md",
                         "| id | severity | evidence |\n|---|---|---|\n"
                         "| OP-1 | major | The test workdir is unstated. |\n")
        result = task_inputs.manifest(entry="configure", role="devops-engineer", mode="review",
                                      project=self.root, skills=["challenge-review"],
                                      findings=findings, base=reviewed, inputs=[contract, decision])
        self.assertEqual({record["path"] for record in result["project_inputs"]},
                         {contract, decision, findings, "workspace/docs/delivery/process-policy.md"})
        self.assertNotIn(other, {record["path"] for record in result["project_inputs"]})
        self.assertEqual(result["changed_paths"], [contract])
        self.assertEqual((result["base"], result["open_findings"]), (reviewed, findings))
        self.assertEqual(result["write_boundary"], "read_only")
        self.assertIn(DOCUMENT, result["required_reads"])


if __name__ == "__main__":
    unittest.main()
