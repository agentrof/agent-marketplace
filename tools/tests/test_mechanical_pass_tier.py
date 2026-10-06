"""Process switch mechanical_pass_tier: writer fix passes on the writers' variants.

At `role_tier`, the default, nothing a task binds changes. At `mechanical`,
the owning writer's `-mechanical` variant, on the `low` tier like every
generated variant, applies the fixes a review names and compiler steps run
without a role, while every review, re-check and calibration keeps its tier.
"""

from __future__ import annotations

import contextlib
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
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import fixtures  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository  # noqa: E402

SWITCH = "mechanical_pass_tier"
REGISTRY = "skill-content/configure/data/process-switches.json"
REFERENCE = "skill-content/challenge-review/references/switch-mechanical_pass_tier-mechanical.md"
WRITERS = {
    "devops-engineer": "operation",
    "product-owner": "backlog-planning",
    "qa-engineer": "operation",
    "solution-architect": "solution-design",
}
# Writer tasks that apply fixes, derived as their flows derive the writer's.
WRITER_TASKS = (
    ("configure", "qa-engineer"),
    ("configure", "devops-engineer"),
    ("backlog-plan", "product-owner"),
    ("solution-design", "solution-architect"),
)
VARIANT_RE = re.compile(r"\b([a-z]+(?:-[a-z]+)*)-mechanical\b")


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def switch() -> dict:
    return json.loads(read(REGISTRY))["switches"][SWITCH]


class InstructionTests(unittest.TestCase):
    def test_the_reference_lists_exactly_the_declared_writer_variants(self):
        text = read(REFERENCE)
        rows = re.findall(r"^\| `([a-z-]+)` \| `([a-z-]+)` \|", text, re.M)
        self.assertEqual(rows, [(agent, f"{agent}-mechanical") for agent in (
            "product-owner", "qa-engineer", "devops-engineer", "solution-architect")])
        self.assertEqual(sorted(agent for agent, _ in rows), switch()["agent_variants"]["mechanical"]["agents"])
        kinds = re.findall(r"^\| `([a-z_]+)` \|", text, re.M)
        self.assertEqual(kinds[:4], ["apply_findings", "render", "stamp", "check"])

    def test_only_the_switch_reference_names_a_variant(self):
        named = {}
        for path in sorted(TEAM.rglob("*.md")):
            for base in VARIANT_RE.findall(path.read_text(encoding="utf-8")):
                named.setdefault(path.relative_to(TEAM).as_posix(), set()).add(base)
        self.assertEqual(named, {REFERENCE: set(WRITERS)})
        for skill in sorted(TEAM.glob("skill-content/*/SKILL.md")):
            self.assertNotIn("switch-mechanical_pass_tier", skill.read_text(encoding="utf-8"))


class CodexTierTests(unittest.TestCase):
    """On Codex a variant keeps its writer's model and effort until the
    variants' frozen-task A/B sets a lower one (#404), and every fix pass
    starts the variant and records the model and effort that ran it."""

    def test_every_codex_variant_runs_its_writers_model_and_effort(self):
        profile = json.loads((ROOT / "platforms/codex/execution-profiles.json").read_text(
            encoding="utf-8"))["profiles"]["auto"]
        agents = ROOT / "dist/codex/software-engineering-team/agents"

        def setting(name: str) -> tuple[str, str]:
            text = (agents / f"{name}.md").read_text(encoding="utf-8")
            return (re.search(r"^model: (.+)$", text, re.M)[1],
                    re.search(r"^model_reasoning_effort: (.+)$", text, re.M)[1])

        self.assertEqual(profile["low"], {"model": "gpt-6.1-sol", "effort": "xhigh"})
        self.assertEqual(setting("product-owner-mechanical"),
                         (profile["low"]["model"], profile["low"]["effort"]))
        for writer in WRITERS:
            with self.subTest(writer=writer):
                self.assertEqual(setting(f"{writer}-mechanical"), setting(writer))
        contract = " ".join((ROOT / "platforms/codex/software-engineering-team/host-contract.md")
                            .read_text(encoding="utf-8").split())
        self.assertIn("a lower low-tier effort waits for that A/B (#404)", contract)

    def test_a_resumed_writer_pass_is_recorded_on_its_own_tier(self):
        text = " ".join(read(REFERENCE).split())
        self.assertIn("whether it ran on the `-mechanical` variant or as the resumed base writer,"
                      " the model and effort that ran it", text)
        self.assertIn("never counts as a variant pass", text)
        contract = " ".join((ROOT / "platforms/codex/software-engineering-team/host-contract.md")
                            .read_text(encoding="utf-8").split())
        self.assertIn("never resume the base writer for one", contract)


class ValidatorTests(unittest.TestCase):
    """The validator refuses a mechanical variant on a reader, a missing
    variant declaration and a host table that omits or mis-maps the tier."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "valid"
        fixtures.make_valid_root(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def edit_json(self, relative: str, mutate, check: str) -> list:
        path = self.root / relative
        original = path.read_bytes()
        value = json.loads(original)
        mutate(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        try:
            return fixtures.validator_findings(self.root, check)
        finally:
            path.write_bytes(original)

    def variants(self, mutate, check: str = "process_switches") -> list:
        return self.edit_json(f"plugins/{fixtures.PLUGIN}/{REGISTRY}",
                              lambda data: mutate(data["switches"][SWITCH]), check)

    def test_a_reader_never_gets_a_mechanical_variant(self):
        for reader in ("backlog-reviewer", "code-reviewer", "analysis-challenger"):
            with self.subTest(reader=reader):
                findings = self.variants(lambda spec: spec["agent_variants"]["mechanical"]
                                         ["agents"].append(reader))
                self.assertIn((
                    "process_switches",
                    f"'mechanical' agent variant '{reader}' is a read-only reviewer or challenger;"
                    " its reviews, re-checks and calibrations keep their tier"),
                    {(finding.check, finding.message) for finding in findings})

    def test_every_variant_a_switch_reference_names_is_declared(self):
        # The reference routes an Environment Contract fix pass to
        # devops-engineer-mechanical; a build that stops shipping it must fail.
        findings = self.variants(lambda spec: spec["agent_variants"]["mechanical"]["agents"]
                                 .remove("devops-engineer"), "switch_variant_references")
        self.assertIn((
            f"plugins/{fixtures.PLUGIN}/{REFERENCE}", "switch_variant_references",
            "switch reference names agent variant 'devops-engineer-mechanical', which switch"
            " 'mechanical_pass_tier' at 'mechanical' does not declare"),
            {(finding.path, finding.check, finding.message) for finding in findings})
        lens = self.edit_json(f"plugins/{fixtures.PLUGIN}/{REGISTRY}", lambda data: data[
            "switches"]["review_panels"]["agent_variants"]["lens_panel"].update(suffix="panel"),
            "switch_variant_references")
        self.assertTrue(any(
            finding.check == "switch_variant_references"
            and "'solution-reviewer-lens', which switch 'review_panels' at 'lens_panel'"
            in finding.message for finding in lens), lens)

    def test_the_switch_must_declare_its_writer_variants(self):
        for mutate in (lambda spec: spec.pop("agent_variants"),
                       lambda spec: spec["agent_variants"].pop("mechanical")):
            with self.subTest(mutate=mutate):
                findings = self.variants(mutate)
                self.assertTrue(any(
                    finding.check == "process_switches"
                    and "must declare the 'mechanical' agent variants" in finding.message
                    for finding in findings), findings)


@integration
class TaskBindingTests(unittest.TestCase):
    """Only an approved policy at `mechanical` binds the reference."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name).resolve()
        init_repository(self.project)
        subprocess.run(["git", "-C", str(self.project), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        (self.project / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)
        self.docs = self.project / "workspace" / "docs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def policy(self, *argv: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = process_policy.main([argv[0], "--docs", str(self.docs), *argv[1:]])
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())

    def manifests(self) -> dict:
        return {(entry, role): task_inputs.manifest(
                    entry=entry, role=role, mode="revise", project=self.project,
                    skills=["challenge-review"])
                for entry, role in WRITER_TASKS}

    @staticmethod
    def reference_reads(result: dict) -> list:
        return [path for path in result["required_reads"] if "switch-mechanical_pass_tier" in path]

    def test_the_reference_binds_only_at_the_mechanical_value(self):
        plain = self.manifests()
        for task, result in plain.items():
            with self.subTest(task=task, policy="missing"):
                self.assertEqual(self.reference_reads(result), [])
                self.assertNotIn(REFERENCE, [item["path"] for item in result["instructions"]])
        self.assertEqual(self.policy("value", "--switch", SWITCH)["value"], "role_tier")

        self.policy("init")
        self.policy("set", "--switch", SWITCH, "--value", "mechanical")
        self.policy("approve")
        self.assertEqual(self.policy("value", "--switch", SWITCH)["value"], "mechanical")
        for task, result in self.manifests().items():
            with self.subTest(task=task, policy="mechanical"):
                self.assertEqual(self.reference_reads(result), [REFERENCE])
                self.assertEqual(result["write_boundary"], "named_owner_only")
                self.assertEqual(sorted(set(result["required_reads"]) - set(plain[task]["required_reads"])),
                                 [REFERENCE])

        self.policy("begin-revision")
        self.policy("set", "--switch", SWITCH, "--default")
        self.policy("approve")
        for task, result in self.manifests().items():
            with self.subTest(task=task, policy="role_tier"):
                self.assertEqual(result["required_reads"], plain[task]["required_reads"])
                self.assertEqual(result["instructions"], plain[task]["instructions"])


@integration
class PassKindTests(unittest.TestCase):
    """The task input policy declares the pass kinds, and task_inputs.py keeps
    every review, re-check, calibration, triage and code repair off them."""

    CONTRACT = "workspace/docs/operation/verification-contract.md"
    FINDINGS = "workspace/findings.json"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        init_repository(self.project)
        subprocess.run(["git", "-C", str(self.project), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        self.docs = self.project / "workspace" / "docs"
        (self.project / self.CONTRACT).parent.mkdir(parents=True)
        (self.project / self.CONTRACT).write_text("---\ntype: verification-contract\n---\n\n# VC\n",
                                                  encoding="utf-8")
        self.write_findings([{"id": "OP-1", "severity": "minor", "anchor": "Scope",
                              "repair": "Replace 'the tests' with 'make test'."}])
        self.policy("init")
        self.policy("set", "--switch", SWITCH, "--value", "mechanical")
        self.policy("approve")
        self.commit()

    def policy(self, command: str, *argv: str) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(process_policy.main([command, "--docs", str(self.docs), *argv]), 0)

    def commit(self) -> None:
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Fixture"], check=True, capture_output=True)

    def write_findings(self, findings) -> None:
        (self.project / self.FINDINGS).write_text(json.dumps({"findings": findings}), encoding="utf-8")

    def task(self, **changes) -> dict:
        kwargs = {"entry": "configure", "role": "qa-engineer", "mode": "revise",
                  "project": self.project, "skills": ["challenge-review"],
                  "inputs": [self.CONTRACT], "findings": self.FINDINGS,
                  "pass_kind": "apply_findings", **changes}
        return task_inputs.manifest(**kwargs)

    def test_the_policy_declares_the_pass_kinds_for_the_mechanical_value(self):
        policy = task_inputs.catalog()
        self.assertEqual(sorted(policy["pass_kinds"]), ["apply_findings", "check", "render", "stamp"])
        apply = policy["pass_kinds"]["apply_findings"]
        self.assertEqual((apply["switch"], apply["value"], apply["modes"]),
                         (SWITCH, "mechanical", ["revise"]))
        # The writers whose documents apply_findings changes are the switch's variants.
        self.assertEqual(sorted(apply["documents"]), sorted(WRITERS))
        for kind in ("render", "stamp", "check"):
            self.assertEqual(policy["pass_kinds"][kind]["runs_as"], "entry_command")

    def test_apply_findings_writes_only_the_owning_writers_document(self):
        result = self.task()
        self.assertEqual(result["pass_kind"], "apply_findings")
        self.assertEqual(result["open_findings"], self.FINDINGS)
        self.assertIn(self.FINDINGS, [record["path"] for record in result["project_inputs"]])
        scope = result["write_scope"]
        self.assertEqual(scope["status"], "resolved")
        self.assertEqual(scope["allowed_write_area"], [
            {"path": self.CONTRACT, "coverage": "exact_file", "source": self.CONTRACT}])
        self.assertEqual(scope["source_records"], [self.CONTRACT])
        # Without a pass kind the writer task reads as before.
        plain = self.task(pass_kind=None)
        self.assertNotIn("pass_kind", plain)
        self.assertEqual(plain["write_scope"]["status"], "unresolved")

    def test_a_mechanical_kind_never_serves_a_review_triage_or_repair(self):
        refused = {
            "a review, re-check or calibration": dict(mode="review"),
            "a read-only reviewer": dict(entry="backlog-plan", role="backlog-reviewer",
                                         inputs=["brief.md"]),
            "a code repair": dict(entry="deliver", role="backend-developer", mode="repair",
                                  skills=[]),
            "a repair pass": dict(mode="repair"),
            "a writer without a variant": dict(role="delivery-coordinator"),
            "the owning writer's document": dict(inputs=["brief.md"]),
            "the verdict's findings": dict(findings=None),
            "an entry command": dict(pass_kind="render"),
            "an unknown kind": dict(pass_kind="rewrite"),
        }
        (self.project / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        self.commit()
        for case, changes in refused.items():
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "pass kind"):
                self.task(**changes)
        # A finding without its exact repair needs the base writer's triage.
        self.write_findings([{"id": "OP-1", "severity": "minor", "anchor": "Scope", "repair": ""}])
        with self.assertRaisesRegex(ValueError, "OP-1 names no exact repair"):
            self.task()
        self.write_findings([{"id": "OP-1", "severity": "minor", "anchor": "Scope",
                              "repair": "Replace 'the tests' with 'make test'."}])
        # At role_tier no pass is mechanical.
        self.policy("begin-revision")
        self.policy("set", "--switch", SWITCH, "--default")
        self.policy("approve")
        with self.assertRaisesRegex(ValueError, "runs only at switch mechanical_pass_tier mechanical"):
            self.task()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = task_inputs.main([
                "--entry", "configure", "--role", "qa-engineer", "--mode", "revise",
                "--project-root", str(self.project), "--skill", "challenge-review",
                "--input", self.CONTRACT, "--findings", self.FINDINGS,
                "--pass-kind", "apply_findings"])
        self.assertEqual(code, 1)
        self.assertIn("runs only at switch mechanical_pass_tier mechanical", output.getvalue())


if __name__ == "__main__":
    unittest.main()
