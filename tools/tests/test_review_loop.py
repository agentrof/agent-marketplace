"""Review loop: switch `review_loop` keeps every review loop as released at
`current` and, at `blocking_delta`, lets only critical and major findings start
a round, tracks minor findings as follow-ups and scopes a re-review to the open
blocking findings, the changed text and its dependency context."""

from __future__ import annotations

import contextlib
import functools
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import operation_compile
import process_policy
import task_inputs
from git_fixture import init_repository

REGISTRY = "skill-content/configure/data/process-switches.json"
DOCUMENT = "skill-content/challenge-review/references/switch-review_loop-blocking_delta.md"
CODE = "skill-content/code-review/references/switch-review_loop-blocking_delta.md"
PANEL = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
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
# step: (entry, calibration reader role, added skills, the Operation contract kind)
CALIBRATION_READERS = {
    "backlog": ("backlog-plan", "backlog-reviewer", [], None),
    "solution_design": ("solution-design", "solution-reviewer", [], None),
    "design_system": ("design-system", "design-system-reviewer", [], None),
    "operation_verification": ("configure", "devops-engineer", ["challenge-review"], "verification"),
    "operation_environment": ("configure", "qa-engineer", ["challenge-review"], "environment"),
    "code_review": ("deliver", "code-reviewer", [], None),
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


def commit_project(root: Path) -> None:
    """Make the project a committed Git worktree, as a full task manifest needs."""
    init_repository(root)
    git(root, "config", "core.autocrlf", "false")
    write(root, ".gitignore", ".agentrof/\n")
    write(root, "brief.md", "Accepted intent.\n")
    commit(root)


def instruction_reads(project: Path, entry: str, role: str, skills: list[str], catalog: dict) -> set[str]:
    """The required reads of a task, as task_inputs.manifest derives them from the project's
    Process Policy, without the Git reads of a full manifest."""
    route = catalog["entries"][entry]
    chosen, _policy_inputs = task_inputs.switch_choices(project, route, task_inputs.PACKAGE)
    return task_inputs.instruction_reads(
        catalog, task_inputs.PACKAGE, route, role,
        task_inputs.task_skills(catalog, entry, role, skills, route), chosen)[2]


def switch_reads(project: Path, entry: str, role: str, skills: list[str], catalog: dict) -> set[str]:
    return {path for path in instruction_reads(project, entry, role, skills, catalog)
            if "/references/switch-" in path}


class ReviewLoopReferenceTests(unittest.TestCase):
    def test_calibration_runs_on_the_claiming_reviewers_own_tier(self):
        # The only calibration evidence comes from a judge on the strongest tier;
        # a lower calibration tier needs its own data first (#326, 30 Sep 2026).
        calibration = flat(DOCUMENT).split("## Calibration", 1)[1]
        self.assertNotRegex(calibration, r"`[a-z-]+-(?:lens|mechanical)`")
        switch = json.loads(read(REGISTRY))["switches"]["review_loop"]
        self.assertNotIn("agent_variants", switch)
        # An Operation contract or bundle claim is calibrated by the contract's counterpart (rr-seams-10).
        reader = ("of the claiming reviewer's role, or for an Operation contract or bundle claim the"
                  " counterpart of the contract it concerns, on that role's own tier")
        self.assertIn(f"one fresh calibration reader {reader}, confirms it", switch["values"][1]["tradeoffs"])
        orchestration = " ".join((ROOT / "docs/orchestration.md").read_text(encoding="utf-8").split())
        self.assertIn("It runs as the claiming reviewer's role, or for an Operation contract or bundle claim"
                      " the counterpart of the contract it concerns, on that role's own tier, never as a"
                      " `-lens` or a `-mechanical` variant", orchestration)
        # Every generated variant runs on `low`, a tier no calibration reader takes.
        for step, (_entry, role, _skills, _kind) in CALIBRATION_READERS.items():
            with self.subTest(step=step):
                tier = re.search(r"(?m)^reasoning: (\S+)$", read(f"agents/{role}.md")).group(1)
                self.assertNotIn(tier, {"low", "lens", "mechanical"})

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


class ReviewLoopTaskInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.docs = self.root / "workspace" / "docs"

    def test_review_tasks_bind_the_loop_only_at_blocking_delta_with_either_panel_value(self):
        catalog = task_inputs.catalog()
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
            for task in REVIEW_TASKS:
                expected = set()
                if loop == "blocking_delta":
                    expected.add(task[4])
                if panels == "lens_panel" and "challenge-review" in (
                        task[3] + catalog["required_role_skills"][task[1]]):
                    expected.add(PANEL)
                with self.subTest(panels=panels, loop=loop, task=task[:3]):
                    self.assertEqual(switch_reads(self.root, task[0], task[1], task[3], catalog), expected)

    @integration
    def test_a_derived_review_task_manifest_binds_the_loop_and_the_panel(self):
        """The one Git-backed derivation of a review task at blocking_delta and lens_panel."""
        commit_project(self.root)
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "set", "--switch", "review_panels", "--value", "lens_panel")
        policy(self.docs, "approve")
        result = task_inputs.manifest(entry="backlog-plan", role="product-owner", mode="revise",
                                      project=self.root, skills=["challenge-review"])
        self.assertEqual(bound(result), {DOCUMENT, PANEL})

    def writers(self, entry: str, kind: str | None) -> set[str]:
        """The step's writer roles as the package declares them, never as this test lists them."""
        catalog = task_inputs.catalog()
        read_only = set(catalog["read_only_roles"]) | set(catalog["read_only_entry_roles"].get(entry, []))
        if kind is not None:
            return {operation_compile.WRITER_ROLES[kind].replace("_", "-")}
        return {role for role in catalog["entries"][entry]["roles"] if role not in read_only}

    def test_the_calibration_reader_is_a_fresh_read_only_non_writer(self):
        catalog = task_inputs.catalog()
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "approve")
        for panels in ("single_reader", "lens_panel"):
            if panels == "lens_panel":
                for step in (("begin-revision",),
                             ("set", "--switch", "review_panels", "--value", "lens_panel"),
                             ("approve",)):
                    policy(self.docs, *step)
            for step, (entry, role, skills, kind) in CALIBRATION_READERS.items():
                route = catalog["entries"][entry]
                read_only = task_inputs.read_only_task(catalog, entry, role, "review")
                with self.subTest(panels=panels, step=step):
                    self.assertTrue(read_only)
                    scope = task_inputs.write_scope(self.root, set(), role, route, read_only, None,
                                                    task_inputs.PACKAGE)
                    self.assertEqual(scope["allowed_write_area"], [])
                    self.assertFalse(scope["writer_authority"])
                    # The claiming reviewer's own role, bound to its own agent file.
                    required = instruction_reads(self.root, entry, role, skills, catalog)
                    self.assertIn(f"agents/{role}.md", required)
                    self.assertIn(CODE if step == "code_review" else DOCUMENT, required)
                    # Never a writer: each writer's task of the step writes, and none is the
                    # calibration reader's.
                    writers = self.writers(entry, kind)
                    self.assertTrue(writers)
                    for writer in writers:
                        self.assertFalse(task_inputs.read_only_task(catalog, entry, writer, "revise"))
                        self.assertNotEqual(writer, role)

    @integration
    def test_a_derived_calibration_task_binds_the_claims_its_claimant_never_binds(self):
        """The one Git-backed derivation of a calibration reader beside its claimant."""
        commit_project(self.root)
        claims = write(self.root, ".agentrof/agent-marketplace/.runtime/review-loop/claims.md",
                       "| id | severity | evidence |\n|---|---|---|\n| F-3 | major | Scope repeats. |\n")
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "approve")
        entry, role, skills, _kind = CALIBRATION_READERS["code_review"]
        derive = functools.partial(task_inputs.manifest, entry=entry, project=self.root, skills=skills)
        calibration = derive(role=role, mode="review", findings=claims)
        claimant = derive(role=role, mode="review")
        self.assertEqual(calibration["write_boundary"], "read_only")
        self.assertEqual(calibration["write_scope"]["allowed_write_area"], [])
        self.assertFalse(calibration["write_scope"]["writer_authority"])
        # Fresh: a task of its own that binds the claims record, which the
        # claiming reader's task never does.
        self.assertIsNone(claimant["open_findings"])
        self.assertEqual(calibration["open_findings"], claims)
        self.assertIn(claims, {record["path"] for record in calibration["project_inputs"]})
        self.assertNotEqual(calibration["source_hash"], claimant["source_hash"])
        self.assertEqual(calibration["role"], claimant["role"])
        self.assertIn(f"agents/{role}.md", calibration["required_reads"])
        self.assertIn(CODE, calibration["required_reads"])
        task = derive(role="backend-developer", mode="revise")
        self.assertEqual(task["write_boundary"], "named_owner_only")
        self.assertNotEqual(task["role"], calibration["role"])

    @integration
    def test_re_review_task_binds_only_the_findings_the_diff_and_the_context(self):
        docs = "workspace/docs/"
        # step: (entry, rerun reader role, added skills, the changed note, its
        # dependency context, a note of the step outside that context)
        steps = {
            "operation_verification": (
                "configure", "devops-engineer", ["challenge-review"],
                "operation/verification-contract.md", ["solution-design/decisions/api.md"],
                "operation/environment-contract.md"),
            "backlog": (
                "backlog-plan", "backlog-reviewer", [],
                "backlog/epics/identity/stories/sign-in/story.md",
                ["backlog/epics/identity/epic.md",
                 "backlog/epics/identity/stories/sign-in/test-plan.md",
                 "backlog/epics/identity/reviews/round-1-epic-review.md"],
                "backlog/epics/billing/stories/invoice/story.md"),
            "solution_design": (
                "solution-design", "solution-reviewer", [],
                "solution-design/decisions/api-decision.md",
                ["solution-design/landscape.md", "solution-design/engagements/api.md"],
                "solution-design/components/web/component.md"),
            "design_system": (
                "design-system", "design-system-reviewer", [],
                "design-system/MASTER.md", ["design-system/pages/sign-in.md"],
                "design-system/pages/billing.md"),
        }
        commit_project(self.root)
        for _entry, _role, _skills, changed, context, other in steps.values():
            for path in (changed, *context, other):
                write(self.root, docs + path, f"---\ntype: note\n---\n\n# {Path(path).stem}\n")
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", "review_loop", "--value", "blocking_delta")
        policy(self.docs, "approve")
        reviewed = commit(self.root)
        for step, (entry, role, skills, changed, context, other) in steps.items():
            with self.subTest(step=step):
                write(self.root, docs + changed, "---\ntype: note\n---\n\n# Fixed\n\nThe fix.\n")
                findings = write(self.root, f".agentrof/agent-marketplace/.runtime/review-loop/{step}.md",
                                 "| id | severity | evidence |\n|---|---|---|\n"
                                 "| F-1 | major | The rule is unstated. |\n")
                inputs = [docs + path for path in (changed, *context)]
                result = task_inputs.manifest(entry=entry, role=role, mode="review",
                                              project=self.root, skills=skills, findings=findings,
                                              base=reviewed, inputs=inputs)
                bound = {record["path"] for record in result["project_inputs"]}
                self.assertEqual(bound, {*inputs, findings,
                                         "workspace/docs/delivery/process-policy.md"})
                self.assertNotIn(docs + other, bound)
                self.assertIsNone(result["backlog_scope"])
                self.assertEqual(result["changed_paths"], [docs + changed])
                self.assertEqual((result["base"], result["open_findings"]), (reviewed, findings))
                self.assertEqual(result["write_boundary"], "read_only")
                self.assertIn(DOCUMENT, result["required_reads"])
                # The task's own freshness check binds exactly these inputs.
                self.assertEqual(task_inputs.manifest(
                    entry=entry, role=role, mode="review", project=self.root, skills=skills,
                    findings=findings, base=reviewed, inputs=inputs,
                    expected_hash=result["source_hash"])["source_hash"], result["source_hash"])
                # The task's freshness covers the whole canonical source inventory, so a
                # change outside its inputs stales it as a change inside them does.
                for moved in (context[0], other):
                    write(self.root, docs + moved, "---\ntype: note\n---\n\n# Moved\n")
                    with self.assertRaisesRegex(ValueError, "task inputs are stale"):
                        task_inputs.manifest(entry=entry, role=role, mode="review",
                                             project=self.root, skills=skills, findings=findings,
                                             base=reviewed, inputs=inputs,
                                             expected_hash=result["source_hash"])
                    git(self.root, "checkout", "--", docs + moved)
                git(self.root, "checkout", "--", docs + changed)


class PackageCalibrationRecordTests(unittest.TestCase):
    """At blocking_delta a Solution Design review keeps its calibration rows in the
    engagement it read and a Design System review in MASTER.md, where each
    package's compiler validates them and its package hash binds them."""

    CITE = "[[solution-design/engagements/api\\|API engagement]]"
    ROW = (f"| SR-1 | major | minor | {CITE} The Options table prices both stacks at the stated"
           " scale, so the decision and its exit path stay the same. |")
    HEADER = "| finding | claimed_severity | calibrated_severity | reason |"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.docs = Path(temporary.name).resolve() / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        self.tree = self.docs / "solution-design"
        write(self.docs, "solution-design/landscape.md",
              "---\ntype: landscape\ntitle: Landscape\n---\n\n# Landscape\n\n## Summary\n\nOne"
              " engagement.\n\n## Current\n\nNothing built yet.\n\n## Target\n\n## Transition\n\n"
              "## Components\n")
        self.engagement = self.tree / "engagements/api.md"
        self.engagement_text = ("---\ntype: engagement\ntitle: API\n---\n\n# API\n\n## Summary\n\n"
                                "Status: open\n\n## Framing\n\nThe API stack.\n\n## Options\n\n"
                                "Two stacks.\n\n## Verdict\n\nThe managed stack.\n")
        self.design = self.docs / "design-system"
        self.master = self.design / "MASTER.md"
        self.master_text = ("---\ntype: design_master\nstatus: draft\nrevision: 1\ncontract_version: 3\n"
                            "tags:\n  - status/draft\n---\n\n# Design Master\n\n## Navigation\n\n"
                            "[[maps/design-system|Design System]]\n")
        self.loop("blocking_delta")

    def loop(self, value: str) -> None:
        first = "begin-revision" if process_policy.path_for(self.docs).exists() else "init"
        for step in ((first,), ("set", "--switch", "review_loop", "--value", value), ("approve",)):
            policy(self.docs, *step)

    def calibrate(self, path: Path, text: str, *rows: str, before: str) -> None:
        section = "\n".join(["## Severity Calibration", "", self.HEADER, "|---|---|---|---|", *rows, "", ""])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.replace(before, section + before, 1) if rows else text, encoding="utf-8")

    def solution_check(self) -> tuple[int, str]:
        import landscape_check

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            code = landscape_check.main(["check", "--tree", str(self.tree)])
        return code, errors.getvalue()

    def test_a_solution_engagement_records_and_binds_its_calibration_rows(self):
        import landscape_check

        self.calibrate(self.engagement, self.engagement_text, self.ROW, before="## Verdict")
        self.assertEqual(self.solution_check(), (0, ""))
        digest = landscape_check.package_hash(self.tree)
        self.calibrate(self.engagement, self.engagement_text,
                       self.ROW.replace("| minor |", "| invalid |"), before="## Verdict")
        self.assertEqual(self.solution_check()[0], 0)
        self.assertNotEqual(landscape_check.package_hash(self.tree), digest)
        label = "solution-design/engagements/api.md severity calibration 1"
        cases = {
            f"{label} reason must cite a vault note": self.ROW.replace(self.CITE + " ", ""),
            f"{label} claimed_severity must be critical or major": self.ROW.replace("| major |", "| minor |"),
            f"{label} calibrated_severity must confirm major or be minor or invalid":
                self.ROW.replace("| minor |", "| critical |"),
            f"{label} targets missing note: solution-design/engagements/web":
                self.ROW.replace("engagements/api", "engagements/web"),
        }
        for expected, row in cases.items():
            with self.subTest(expected=expected):
                self.calibrate(self.engagement, self.engagement_text, row, before="## Verdict")
                code, errors = self.solution_check()
                self.assertEqual(code, 1)
                self.assertIn(f"  - {expected}\n", errors)
        # At current a section of that name is the engagement's own text.
        self.loop("current")
        self.calibrate(self.engagement, self.engagement_text, "| a | b | c | d |", before="## Verdict")
        self.assertEqual(self.solution_check(), (0, ""))

    def test_the_design_system_master_records_and_binds_its_calibration_rows(self):
        import design_system_compile

        row = self.ROW.replace(self.CITE, "[[design-system/MASTER\\|Design Master]]")
        write(self.docs, "design-system/MASTER.md", self.master_text)
        self.calibrate(self.master, self.master_text, row, before="## Navigation")
        self.assertEqual(design_system_compile.calibration_findings(self.design), [])
        digest = design_system_compile.baseline_hash(self.design)
        self.calibrate(self.master, self.master_text, row.replace("| minor |", "| invalid |"),
                       before="## Navigation")
        self.assertNotEqual(design_system_compile.baseline_hash(self.design), digest)
        label = "design-system/MASTER.md severity calibration 1"
        self.calibrate(self.master, self.master_text, row.replace("| major |", "| minor |"),
                       before="## Navigation")
        expected = f"{label} claimed_severity must be critical or major"
        self.assertEqual(design_system_compile.calibration_findings(self.design), [expected])
        # The check and the approval read every semantic finding, this one included.
        self.assertIn(expected, design_system_compile.semantic_findings(self.design))
        self.loop("current")
        self.assertEqual(design_system_compile.calibration_findings(self.design), [])


if __name__ == "__main__":
    unittest.main()
