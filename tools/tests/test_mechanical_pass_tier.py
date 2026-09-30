"""Process switch mechanical_pass_tier: writer fix passes on the mechanical tier.

At `role_tier`, the default, nothing a task binds changes. At `mechanical`,
the owning writer's `-mechanical` variant applies the fixes a review names and
compiler steps run without a role, while every review, re-check and
calibration keeps its tier.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
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


def flat(text: str) -> str:
    return " ".join(text.split())


class RegistryTests(unittest.TestCase):
    def test_switch_defaults_to_the_role_tier_under_its_promotion_rule(self):
        spec = switch()
        self.assertEqual([value["id"] for value in spec["values"]], ["role_tier", "mechanical"])
        self.assertEqual(spec["default"], "role_tier")
        self.assertEqual(spec["issue"], 330)
        self.assertEqual(sorted(spec["flows"]), sorted(set(WRITERS.values())))
        self.assertIn("frozen-task A/B", spec["promotion"]["unit"])
        self.assertIn("at least 3 Deliveries", spec["promotion"]["unit"])
        for fragment in ("at most 50%", "rework rate no higher than role_tier's",
                         "zero accepted unrelated edits", "owner's approval"):
            self.assertIn(fragment, spec["promotion"]["threshold"])
        for fragment in ("wall time", "output tokens", "rework", "unrelated edit"):
            self.assertIn(fragment, spec["metric"])

    def test_the_mechanical_value_declares_one_variant_per_writer(self):
        self.assertEqual(switch()["agent_variants"], {"mechanical": {
            "suffix": "mechanical", "tier": "mechanical",
            "description": "Mechanical-tier variant for passes that apply only the fixes a review"
                           " verdict names.",
            "agents": sorted(WRITERS)}})

    def test_every_host_maps_the_mechanical_tier(self):
        models = json.loads((ROOT / "tools/data/models.json").read_text(encoding="utf-8"))
        self.assertIn("mechanical", models["reasoning_levels"])
        self.assertIn("mechanical", build_distributions.CANONICAL_REASONING_LEVELS)
        self.assertIn("mechanical", validate.AGENT_REASONING_ENUM)
        tables = {
            host: json.loads(build_distributions.execution_profile_path(ROOT, host)
                             .read_text(encoding="utf-8"))["profiles"]["auto"]
            for host in ("claude", "codex")
        }
        # Starting values: the frozen-task A/B sets them before a project opts in.
        self.assertEqual(tables["claude"]["mechanical"], {"class": "strong", "effort": "high"})
        self.assertEqual(tables["codex"]["mechanical"], {"class": "fast", "effort": "high"})

    def test_every_statement_of_the_tier_says_what_a_variant_changes_per_host(self):
        # On Claude three writers already run the mechanical tier's class, so
        # their variant changes only the effort (#330); the texts must say so.
        tables = {host: json.loads(build_distributions.execution_profile_path(ROOT, host)
                                   .read_text(encoding="utf-8"))["profiles"]["auto"]
                  for host in ("claude", "codex")}
        tiers = {agent: build_distributions.parse_frontmatter(TEAM / "agents" / f"{agent}.md")[0]
                 ["reasoning"] for agent in WRITERS}
        same_class = {host: {agent for agent, tier in tiers.items()
                             if table[tier].get("class") == table["mechanical"]["class"]}
                      for host, table in tables.items()}
        self.assertEqual(same_class, {"claude": {"product-owner", "qa-engineer", "devops-engineer"},
                                      "codex": set()})
        spec = switch()
        mechanical = next(value for value in spec["values"] if value["id"] == "mechanical")
        for text in (mechanical["tradeoffs"], spec["agent_variants"]["mechanical"]["description"],
                     read(REFERENCE)):
            self.assertNotIn("lower mechanical tier", flat(text))
            self.assertNotIn("Lower-tier", text)
            self.assertNotIn("on a lower tier", flat(text))
        for fragment in ("The host contract states per host what a variant changes against its"
                         " writer's own tier", "only a fixed effort that is lower only when the"
                         " session runs above it", "placeholders until the frozen-task A/B sets"
                         " them"):
            self.assertIn(fragment, mechanical["tradeoffs"])
        contracts = {host: flat((ROOT / "platforms" / host / "software-engineering-team"
                                 / "host-contract.md").read_text(encoding="utf-8"))
                     .split("Every build also ships the `-mechanical` variants", 1)[1]
                     .split(" - ", 1)[0] for host in tables}
        authoring = flat((ROOT / "docs/authoring.md").read_text(encoding="utf-8")
                         .split("## Mechanical passes", 1)[1].split("\n## ", 1)[0])
        for text in (contracts["claude"], authoring):
            self.assertIn("`product-owner`, `qa-engineer` and `devops-engineer` already run Sonnet"
                          " at the session's effort, so their variant is lower only when the"
                          " session runs above effort `high`", text)
            self.assertIn("a lower model only for `solution-architect`", text)
        for text in (contracts["codex"], authoring):
            self.assertIn("from Sol to Luna", text)
        for text in (*contracts.values(), authoring):
            self.assertIn("placeholders until the tier's frozen-task A/B sets them", text)


class InstructionTests(unittest.TestCase):
    def test_each_owning_flow_anchors_the_switch_and_names_the_reference(self):
        for flow in sorted(set(WRITERS.values())):
            with self.subTest(flow=flow):
                text = flat(read(f"flows/{flow}.md"))
                self.assertIn(f"Switch `{SWITCH}`: at `mechanical`", text)
                self.assertIn(REFERENCE, text)
        for flow in sorted({path.stem for path in (TEAM / "flows").glob("*.md")}
                           - set(WRITERS.values())):
            with self.subTest(flow=flow):
                self.assertNotIn(SWITCH, read(f"flows/{flow}.md"))

    def test_the_reference_lists_exactly_the_declared_writer_variants(self):
        text = read(REFERENCE)
        rows = re.findall(r"^\| `([a-z-]+)` \| `([a-z-]+)` \|", text, re.M)
        self.assertEqual(rows, [(agent, f"{agent}-mechanical") for agent in (
            "product-owner", "qa-engineer", "devops-engineer", "solution-architect")])
        self.assertEqual(sorted(agent for agent, _ in rows), switch()["agent_variants"]["mechanical"]["agents"])
        kinds = re.findall(r"^\| `([a-z_]+)` \|", text, re.M)
        self.assertEqual(kinds[:4], ["apply_findings", "render", "stamp", "check"])

    def test_the_reference_keeps_triage_design_and_reviews_off_the_mechanical_path(self):
        text = flat(read(REFERENCE))
        for fragment in (
                "Never mechanical: authoring or rewriting text no finding dictates, design or a"
                " choice between alternatives, triage of findings, code and architecture repairs,"
                " and every review, re-check or calibration.",
                "A variant never reads for a review, re-check or calibration",
                "Otherwise the base writer runs the whole pass as the flow describes.",
                "It never changes a severity, never disputes a finding and never edits text no"
                " finding names.",
                "The flow's re-check is unchanged",
                "Every gate before a stamp stays, including the reader barrier, the"
                " `--expected-hash` recheck and the owner's approval.",
                "`--mode revise` and `--skill challenge-review`",
                "add `--pass-kind apply_findings`, `--findings <record>` and one `--input` per"
                " document the findings change",
                "`task_inputs.py` refuses the kind for a review, re-check, calibration, triage or"
                " repair task"):
            self.assertIn(fragment, text)

    def test_only_the_switch_reference_names_a_variant(self):
        named = {}
        for path in sorted(TEAM.rglob("*.md")):
            for base in VARIANT_RE.findall(path.read_text(encoding="utf-8")):
                named.setdefault(path.relative_to(TEAM).as_posix(), set()).add(base)
        self.assertEqual(named, {REFERENCE: set(WRITERS)})
        for skill in sorted(TEAM.glob("skill-content/*/SKILL.md")):
            self.assertNotIn("switch-mechanical_pass_tier", skill.read_text(encoding="utf-8"))


class ReviewTierContractTests(unittest.TestCase):
    """Every review, re-check and calibration keeps its tier under both values."""

    def test_no_canonical_role_moves_to_the_mechanical_tier(self):
        for path in sorted((TEAM / "agents").glob("*.md")):
            with self.subTest(agent=path.stem):
                fields = build_distributions.parse_frontmatter(path)[0]
                self.assertNotEqual(fields["reasoning"], "mechanical")

    def test_every_variant_is_a_writer_and_never_a_reader(self):
        policy = json.loads(read("templates/task-input-policy.json"))
        panels = json.loads(read("skill-content/challenge-review/data/review-panels.json"))
        read_only = set(policy["read_only_roles"])
        readers = {step["reader_role"] for step in panels["review_steps"].values()}
        for agent in switch()["agent_variants"]["mechanical"]["agents"]:
            with self.subTest(agent=agent):
                fields = build_distributions.parse_frontmatter(TEAM / "agents" / f"{agent}.md")[0]
                self.assertNotIn("tools", fields)
                self.assertNotIn(agent, read_only)
        # The Operation counterparts read the other contract as their base role.
        self.assertEqual(readers & set(WRITERS), {"devops-engineer", "qa-engineer"})
        reference = flat(read(REFERENCE))
        self.assertIn("the Operation counterpart that reviews the other contract runs as its"
                      " base role", reference)
        lens = read("skill-content/challenge-review/references/switch-review_panels-lens_panel.md")
        self.assertNotIn("mechanical", lens)


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

    def edit_json(self, relative: str, mutate) -> list:
        path = self.root / relative
        original = path.read_bytes()
        value = json.loads(original)
        mutate(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        try:
            return validate.run(self.root)
        finally:
            path.write_bytes(original)

    def variants(self, mutate) -> list:
        return self.edit_json(f"plugins/{fixtures.PLUGIN}/{REGISTRY}",
                              lambda data: mutate(data["switches"][SWITCH]))

    def test_the_shipped_switch_is_clean(self):
        self.assertEqual(validate.run(self.root), [])

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

    def test_the_switch_must_declare_its_writer_variants(self):
        for mutate in (lambda spec: spec.pop("agent_variants"),
                       lambda spec: spec["agent_variants"].pop("mechanical")):
            with self.subTest(mutate=mutate):
                findings = self.variants(mutate)
                self.assertTrue(any(
                    finding.check == "process_switches"
                    and "must declare the 'mechanical' agent variants" in finding.message
                    for finding in findings), findings)

    def test_a_host_table_must_map_the_tier_in_its_vocabulary(self):
        cases = (
            ("claude", lambda auto: auto.pop("mechanical")),
            ("codex", lambda auto: auto.pop("mechanical")),
            ("claude", lambda auto: auto.update(mechanical={"class": "strong", "effort": "extreme"})),
            ("claude", lambda auto: auto.update(mechanical={"class": "gpt-5"})),
            ("claude", lambda auto: auto.update(mechanical={"model": "claude-sonnet-5-5"})),
            ("codex", lambda auto: auto.update(mechanical={"class": "fast", "effort": "turbo"})),
            ("codex", lambda auto: auto.update(mechanical={"class": "fast", "effort": "ultra"})),
        )
        for host, mutate in cases:
            with self.subTest(host=host, mutate=mutate):
                findings = self.edit_json(f"platforms/{host}/execution-profiles.json",
                                          lambda table: mutate(table["profiles"]["auto"]))
                self.assertIn((f"platforms/{host}/execution-profiles.json", "execution_profiles"),
                              {(finding.path, finding.check) for finding in findings})
        findings = self.edit_json("tools/data/models.json",
                                  lambda models: models["reasoning_levels"].remove("mechanical"))
        self.assertIn(("tools/data/models.json", "model_config_shape"),
                      {(finding.path, finding.check) for finding in findings})


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
