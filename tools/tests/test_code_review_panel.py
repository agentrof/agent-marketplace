"""Code review panel: switch `code_review_panel` keeps Delivery code review on the
official code reviewer alone at `single_reader` and, at `beside_official`, runs a
lens panel beside it on the same frozen candidate and inputs. Each panel claim
is calibrated before it gates, and `merge-panel` registers the one code review
result: the official findings and the validated panel findings, each with its
source, with the combined step's wall clock against the official reviewer's."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import delivery_compile as delivery  # noqa: E402
import delivery_verification as verification  # noqa: E402
import fixtures  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

REGISTRY = "skill-content/configure/data/process-switches.json"
REFERENCE = "skill-content/code-review/references/switch-code_review_panel-beside_official.md"
DATA = "skill-content/code-review/data/code-review-panel.json"
# The lens split of one project's measured shadow panel, kept so later passes compare with it.
LENSES = ["correctness-contract", "security-isolation", "conformance-tests"]
PREFIX = {lens: f"P1-{lens}-" for lens in LENSES}


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def run_policy(docs: Path, command: str, *argv: str) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main([command, "--docs", str(docs), *argv])
    if code:
        raise AssertionError(output.getvalue())


def policy(docs: Path, **values: str) -> None:
    """Approve a Process Policy that sets each given switch value."""
    run_policy(docs, "init")
    for name, value in values.items():
        run_policy(docs, "set", "--switch", name, "--value", value)
    run_policy(docs, "approve")


class CodeReviewPanelRegistryTests(unittest.TestCase):
    def test_the_panel_data_declares_the_measured_lens_split(self):
        data = json.loads(read(DATA))
        self.assertEqual(set(data), {"schema_version", "review_steps"})
        self.assertEqual(list(data["review_steps"]), ["code_review"])
        step = data["review_steps"]["code_review"]
        self.assertEqual(step["reader_role"], "code-reviewer")
        self.assertEqual([lens["id"] for lens in step["lenses"]], LENSES)
        self.assertEqual(step["default_panel"], [[lens] for lens in LENSES])
        self.assertTrue(all(lens["focus"].strip() for lens in step["lenses"]))

    def test_the_official_reviewer_and_every_calibration_reader_keep_their_tier(self):
        self.assertRegex(read("agents/code-reviewer.md"), r"(?m)^reasoning: high$")
        # Only the review panel switches ship lens variants; review_loop ships none.
        switches = json.loads(read(REGISTRY))["switches"]
        self.assertNotIn("agent_variants", switches["review_loop"])
        self.assertNotIn("code-reviewer", switches["review_panels"]["agent_variants"]["lens_panel"]["agents"])


class CodeReviewPanelReferenceTests(unittest.TestCase):
    def test_default_path_instructions_name_no_panel_value(self):
        for path in TEAM.rglob("*.md"):
            relative = path.relative_to(TEAM).as_posix()
            if path.name.startswith("switch-") or relative.startswith("flows/"):
                continue
            with self.subTest(path=relative):
                self.assertNotIn("beside_official", path.read_text(encoding="utf-8"))


class CodeReviewPanelTaskInputTests(unittest.TestCase):
    def test_code_review_tasks_bind_the_panel_only_at_beside_official(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        catalog = task_inputs.catalog()
        route = catalog["entries"]["deliver"]

        def bound(role: str) -> dict:
            """The task's required reads and hashed instructions, as task_inputs.manifest derives
            them from the project's Process Policy, without the Git reads of a full manifest."""
            chosen, _policy_inputs = task_inputs.switch_choices(root, route, task_inputs.PACKAGE)
            _registry, _chosen, required, _conditional, hashed = task_inputs.instruction_reads(
                catalog, task_inputs.PACKAGE, route, role,
                task_inputs.task_skills(catalog, "deliver", role, None, route), chosen)
            return {"reads": required, "hashed": hashed}

        reviewer, writer = bound("code-reviewer"), bound("backend-developer")
        self.assertIn("skill-content/code-review/SKILL.md", reviewer["reads"])
        for paths in (*reviewer.values(), *writer.values()):
            self.assertFalse({REFERENCE, DATA} & paths)
        policy(root / "workspace/docs", code_review_panel="beside_official")
        reviewer, writer = bound("code-reviewer"), bound("backend-developer")
        self.assertLessEqual({REFERENCE, DATA}, reviewer["reads"])
        self.assertFalse({REFERENCE, DATA} & writer["reads"])


class CodeReviewPanelValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixtures.make_valid_root(self.root)
        self.plugin = self.root / "plugins" / fixtures.PLUGIN
        self.originals = {path: (self.plugin / path).read_bytes() for path in (DATA, REGISTRY)}

    def messages(self) -> list[str]:
        return [finding.message
                for finding in fixtures.validator_findings(self.root, "code_review_panel")]

    def edit(self, relative: str, mutate) -> None:
        value = json.loads(self.originals[relative])
        mutate(value)
        (self.plugin / relative).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_shape_variant_and_binding_errors_are_rejected(self):
        variant = lambda value: value["switches"]["code_review_panel"]["agent_variants"]["beside_official"]  # noqa: E731
        cases = (
            (DATA, lambda value: value["review_steps"]["code_review"].update(lenses=[]),
             "review step 'code_review': empty panel, lenses declares no lens"),
            (DATA, lambda value: value["review_steps"]["code_review"]["default_panel"].pop(),
             "lens 'conformance-tests' is in no default_panel assignment"),
            (DATA, lambda value: value["review_steps"]["code_review"]["lenses"][0].update(id="Bad_Id"),
             "lens id 'Bad_Id' must be kebab-case"),
            (DATA, lambda value: value["review_steps"].update(qa=value["review_steps"]["code_review"]),
             "must declare exactly the review step 'code_review'"),
            (DATA, lambda value: value["review_steps"]["code_review"].update(reader_role="qa-engineer"),
             "reader 'qa-engineer' of the code review panel has no 'beside_official' agent variant"),
            (REGISTRY, lambda value: variant(value).update(agents=["qa-engineer"]),
             "'beside_official' agent variant 'qa-engineer' reads no code review panel step"),
            (REGISTRY, lambda value: value["switches"]["code_review_panel"].pop("value_data"),
             f"switch 'code_review_panel' must bind {DATA} as 'beside_official' value data"),
        )
        for relative, mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                for path, original in self.originals.items():
                    (self.plugin / path).write_bytes(original)
                self.edit(relative, mutate)
                messages = self.messages()
                self.assertTrue(any(fragment in message for message in messages), messages)
        (self.plugin / DATA).write_text('{"schema_version": 1, "review_steps": {}, "review_steps": {}}\n',
                                        encoding="utf-8")
        self.assertTrue(any("not valid unique-key JSON" in message for message in self.messages()))


@integration
class CodeReviewPanelMachineTests(unittest.TestCase):
    """One parallel-snapshot Item, as test_delivery_verification builds it."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        init_repository(self.root, initial_branch="main")
        self.write(".gitignore", ".agentrof/\n")
        self.write("src/product.py", "value = 1\nlimit = 2\nname = 3\n")
        self.commit()
        base = verification.git(self.root, "rev-parse", "HEAD")
        self.directory = "workspace/docs/delivery/deliveries/dlv-001-sample"
        self.note(self.directory + "/items/auth-01/item.md", {
            "type": "delivery-item", "title": "Authentication", "status": "active", "story_id": "AUTH-01",
            "story_path": "backlog/story.md", "test_plan_path": "backlog/test-plan.md",
            "item_plan_hash": "sha256:plan", "verification_schedule": "parallel_snapshot_v1",
            "integration_base_commit": base, "verification_contract_ref": "operation/verification-contract",
            "role_sequence": ["backend_developer", "code_reviewer", "qa_engineer"],
        })
        self.note(self.directory + "/delivery.md", {"type": "delivery", "id": "DLV-001",
                                                    "definition_of_done_path": "delivery/definition-of-done.md"})
        self.note(self.directory + "/execution-plan.md", {"type": "execution-plan", "plan_hash": "sha256:plan"})
        for name in ("backlog/story.md", "backlog/test-plan.md", "delivery/definition-of-done.md"):
            self.note("workspace/docs/" + name, {"status": "approved"})
        executable = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        self.command = executable + ' -c "print(123)"'
        self.note("workspace/docs/operation/verification-contract.md", {
            "type": "verification-contract", "status": "approved", "test_command": self.command,
            "test_workdir": ".", "mutation_disposition": "not_applicable",
            "dependency_audit_disposition": "not_applicable"})
        for name, kind in (("code-review.md", "code-review"), ("verification.md", "verification")):
            self.note(self.directory + "/items/auth-01/" + name,
                      {"type": kind, "title": name, "status": "draft", "tags": [], "item_plan_hash": "sha256:plan"})
        self.write("src/product.py", "value = 2\nlimit = 2\nname = 3\n")
        self.commit()
        identity = mock.patch.object(verification, "instruction_identity", return_value="sha256:policy")
        identity.start()
        self.addCleanup(identity.stop)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def note(self, relative, props):
        self.write(relative, delivery.frontmatter(props, "# Evidence\n\nReviewed independently.\n"))

    def commit(self):
        for args in (("add", "-A"), ("-c", "user.name=Test", "-c", "user.email=test@example.com",
                                     "commit", "-qm", "Candidate")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def policy(self, **values):
        policy(self.root / "workspace/docs", **values)
        self.commit()

    def at(self, seconds, call, *args, **kwargs):
        """Run a verification verb at a fixed wall clock, so the panel record is exact."""
        with mock.patch.object(verification.time, "time", return_value=1_000_000.0 + seconds):
            return call(*args, **kwargs)

    def freeze(self, seconds=0.0):
        return self.at(seconds, verification.freeze, self.root, "DLV-001", "AUTH-01")

    def session(self):
        return verification.read_session(self.root)

    def official(self, verdict="passed", findings=(), mode="review_initial"):
        session = self.session()
        checks = {name: {"passed": True, "evidence": "Independently verified"}
                  for name in verification.required_checks(self.root, session["candidate"], "code_reviewer")}
        return {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                "role": "code_reviewer", "mode": mode, "verdict": verdict,
                "report": "Independent code review result", "checks": checks, "findings": list(findings)}

    def lens(self, lens, findings=(), verdict=None):
        session = self.session()
        findings = list(findings)
        if verdict is None:
            verdict = "failed" if any(verification.blocking(finding) for finding in findings) else "passed"
        return {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                "role": "code_reviewer", "mode": "panel_lens", "lens": [lens], "verdict": verdict,
                "report": f"Lens {lens} read the candidate", "findings": findings}

    @staticmethod
    def finding(identifier, severity="major", line=1, **extra):
        return {"id": identifier, "severity": severity, "status": "open", "verification": "Rerun the regression",
                "file": f"src/product.py:{line}", "description": f"Defect {identifier} in the product.", **extra}

    @staticmethod
    def ruling(finding, claimed="major", ruling="major", line=1, **extra):
        row = {"finding": finding, "claimed_severity": claimed, "calibrated_severity": ruling,
               "reason": f"src/product.py:{line} shows the claim as written."}
        if ruling == "minor":
            row.update(owner_role="backend_developer", revisit_trigger="Revisit at the next change to src/product.py.")
        return {**row, **extra}

    def calibration(self, claims, rows):
        session = self.session()
        return {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                "role": "code_reviewer", "mode": "calibration", "report": "Independent calibration of the claims",
                "claims": list(claims), "calibration": list(rows)}

    def qa(self, final=True):
        session = self.session()
        result = {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                  "role": "qa_engineer", "mode": "qa_final" if final else "qa_diagnostic", "verdict": "passed",
                  "report": "Independent QA result", "checks": {}}
        if final:
            result["checks"] = {name: {"passed": True, "evidence": "Independently verified"}
                                for name in verification.required_checks(self.root, session["candidate"],
                                                                         "qa_engineer")}
            raw = verification.run_check(self.root, "test")
            result["checks"]["full_test_suite"].update(command=self.command, exit_code=0,
                                                        environment=raw["identity"]["environment_hash"],
                                                        raw_evidence_hash=raw["evidence_hash"])
        return result

    def register_panel(self, official, lenses, start=0.0):
        """Register each lens result 30 s apart from +120 s and the official result at +600 s."""
        for index, (lens, findings) in enumerate(lenses.items()):
            self.at(start + 120.0 + 30 * index, verification.register_panel_result, self.root,
                    self.lens(lens, findings))
        self.at(start + 600.0, verification.register_panel_result, self.root, official)

    def approve_evidence(self):
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-001", "story": "AUTH-01"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery.approve_item_evidence(args)
        self.assertEqual(code, 0, output.getvalue())
        return delivery.section_bodies(delivery.split_note(
            self.root / self.directory / "items/auth-01/code-review.md")[1])

    def test_single_reader_keeps_the_released_code_review(self):
        self.freeze()
        manifest = verification.manifest(self.root, "DLV-001", "AUTH-01", "code_reviewer", "review_initial")
        self.assertNotIn("code_review_panel", manifest)
        with self.assertRaisesRegex(RuntimeError, "panel-result registers a code review panel result only at"
                                                  " code_review_panel beside_official"):
            verification.register_panel_result(self.root, self.lens("security-isolation"))
        with self.assertRaisesRegex(RuntimeError, "merge-panel settles a code review only at code_review_panel"
                                                  " beside_official"):
            verification.merge_panel(self.root)
        with self.assertRaisesRegex(RuntimeError, "severity calibration runs only at review_loop blocking_delta"):
            verification.register_calibration(self.root, self.calibration(
                [self.finding("F-1")], [self.ruling("F-1")]))
        verification.register_result(self.root, self.official())
        session = self.session()
        self.assertNotIn("panel", session)
        self.assertNotIn("panel_history", session)
        self.assertNotIn("panel", session["workers"]["code_reviewer"]["result"])


    def test_result_refuses_a_code_review_that_skips_the_merge(self):
        self.policy(code_review_panel="beside_official")
        self.freeze()
        with self.assertRaisesRegex(RuntimeError, "at code_review_panel beside_official the code review settles"
                                                  " through merge-panel: register the official result and every"
                                                  " lens result with panel-result"):
            verification.register_result(self.root, self.official())
        # A confirmed cancellation still releases the write barrier.
        cancelled = {**self.official(verdict="cancelled"), "cancellation_confirmed": True}
        verification.register_result(self.root, cancelled)
        self.assertEqual(self.session()["workers"]["code_reviewer"]["state"], "cancelled")

    def test_the_manifest_gives_every_reader_the_same_inputs_and_one_assignment(self):
        self.policy(code_review_panel="beside_official")
        self.freeze()
        manifest = verification.manifest(self.root, "DLV-001", "AUTH-01", "code_reviewer", "review_initial")
        panel = manifest.pop("code_review_panel")
        self.assertEqual(panel["pass"], 1)
        focus = {lens["id"]: lens["focus"] for lens in json.loads(read(DATA))["review_steps"]["code_review"]["lenses"]}
        self.assertEqual(panel["assignments"], [{"lens": [lens], "focus": {lens: focus[lens]},
                                                 "id_prefix": PREFIX[lens]} for lens in LENSES])
        self.assertEqual(panel["lens_result_interface"]["mode"], "panel_lens")
        self.assertIn("panel-result --file", panel["registration"])
        self.assertIn("merge-panel", panel["registration"])
        # Every reader receives the official reviewer's manifest; QA's carries no panel.
        self.assertEqual(manifest["role"], "code_reviewer")
        qa = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")
        self.assertNotIn("code_review_panel", qa)

    def test_panel_result_registers_each_member_without_settling_the_review(self):
        """The session-backed case of panel-result; CodeReviewPanelRuleTests holds its refusal matrix."""
        self.policy(code_review_panel="beside_official")
        self.freeze()
        lens = "security-isolation"
        verification.register_panel_result(self.root, self.lens(lens, [self.finding(PREFIX[lens] + "01")]))
        with self.assertRaisesRegex(RuntimeError, "lens assignment security-isolation is already registered"):
            verification.register_panel_result(self.root, self.lens(lens))
        verification.register_panel_result(self.root, self.official())
        self.assertEqual(sorted(self.session()["panel"]["members"]), ["official", lens])
        # Registration never settles the code review, so the writer barrier holds.
        self.assertEqual(self.session()["workers"]["code_reviewer"]["state"], "running")
        with self.assertRaisesRegex(RuntimeError, "DELIVERY_VERIFICATION_READERS_ACTIVE"):
            verification.guard_write(self.root, [Path("src/product.py")])

    def test_calibration_waits_for_every_panel_result(self):
        """The session-backed case of calibrate; CodeReviewPanelRuleTests holds its refusal matrix."""
        self.policy(code_review_panel="beside_official")
        self.freeze()
        claim = self.finding(PREFIX["security-isolation"] + "01")
        with self.assertRaisesRegex(RuntimeError, "register the official result and every lens result with"
                                                  " panel-result first; missing: official, correctness-contract,"
                                                  " security-isolation, conformance-tests"):
            verification.register_calibration(self.root, self.calibration([claim], [self.ruling(claim["id"])]))
        self.assertNotIn("calibration", self.session())

    def test_merge_panel_settles_the_code_review_across_a_repair_cycle(self):
        """The session-backed case of merge-panel, over two frozen candidates; CodeReviewPanelRuleTests
        holds the merge's findings, record and refusals."""
        self.policy(code_review_panel="beside_official")
        self.freeze()
        confirmed = self.finding(PREFIX["security-isolation"] + "01")
        lowered = self.finding(PREFIX["security-isolation"] + "02", line=2)
        invalid = self.finding(PREFIX["correctness-contract"] + "01", "critical", line=3)
        twin = self.finding(PREFIX["correctness-contract"] + "02", line=2)
        note = self.finding(PREFIX["conformance-tests"] + "01", "minor", line=3)
        official = self.official(findings=[self.finding("F-1", "minor", line=3)])
        self.register_panel(official, {"correctness-contract": [invalid, twin],
                                       "security-isolation": [confirmed, lowered], "conformance-tests": [note]})
        claims = sorted([confirmed, lowered, invalid, twin], key=lambda finding: finding["id"])
        with self.assertRaisesRegex(RuntimeError, "register the calibration reader's result with calibrate for exactly"
                                                  " the open critical or major claims to rule, as returned: "
                                                  + ", ".join(claim["id"] for claim in claims)):
            verification.merge_panel(self.root)
        rows = [self.ruling(confirmed["id"]), self.ruling(lowered["id"], ruling="minor", line=2),
                self.ruling(invalid["id"], "critical", "invalid", line=3),
                self.ruling(twin["id"], ruling="duplicate", line=2, duplicate_of=lowered["id"])]
        self.at(630.0, verification.register_calibration, self.root, self.calibration(claims, rows))
        self.at(660.0, verification.merge_panel, self.root)
        session = self.session()
        worker = session["workers"]["code_reviewer"]
        self.assertEqual(worker["state"], "settled")
        result = worker["result"]
        self.assertEqual(result["result_hash"], verification.digest(
            {key: value for key, value in result.items() if key != "result_hash"}))
        self.assertEqual(sorted(finding["id"] for finding in result["findings"]),
                         ["F-1", confirmed["id"], lowered["id"]])
        self.assertEqual(result["calibration"], rows)
        self.assertEqual(result["panel"]["official_result_hash"],
                         session["panel"]["members"]["official"]["result"]["result_hash"])
        self.assertEqual(result["panel"]["lens_result_hashes"],
                         {lens: session["panel"]["members"][lens]["result"]["result_hash"] for lens in LENSES})
        self.assertEqual(worker["reader_elapsed_seconds"], 660.0)
        verification.register_result(self.root, self.qa(final=False))
        # A confirmed panel blocker gates the Item as an official one would.
        with self.assertRaisesRegex(RuntimeError, "same-candidate final passed code_reviewer"):
            verification.validate(self.root, "DLV-001", "AUTH-01")
        self.repair_cycle(confirmed["id"], lowered["id"])

    def repair_cycle(self, blocker, follow_up):
        self.write("src/product.py", "value = 3\nlimit = 2\nname = 3\n")
        self.commit()
        self.freeze(1000.0)
        session = self.session()
        unresolved = {finding["id"]: finding for finding in session["unresolved_findings"]}
        self.assertEqual((unresolved[blocker]["source"], unresolved[blocker]["lens"]),
                         ("panel", ["security-isolation"]))
        self.assertEqual([record["pass"] for record in session["panel_history"]], [1])
        manifest = verification.manifest(self.root, "DLV-001", "AUTH-01", "code_reviewer", "review_repair")
        self.assertEqual([assignment["id_prefix"] for assignment in manifest["code_review_panel"]["assignments"]],
                         [f"P2-{lens}-" for lens in LENSES])
        self.assertEqual(manifest["code_review_panel"]["pass"], 2)
        # The official repair review dispositions every inherited finding, the panel's included.
        carried = [{**unresolved[identifier], "status": "resolved" if identifier == blocker else "open"}
                   for identifier in sorted(unresolved)]
        carried = [{key: value for key, value in finding.items() if key != "role"} for finding in carried]
        self.register_panel(self.official(mode="review_repair", findings=carried),
                            {lens: [] for lens in LENSES}, start=1000.0)
        # With no claim to rule, the merge needs no calibration.
        self.at(1610.0, verification.merge_panel, self.root)
        verification.register_result(self.root, self.qa())
        verification.validate(self.root, "DLV-001", "AUTH-01")
        # At review_loop current the record keeps each pass's claims, also those of a replaced session.
        section = self.approve_evidence()["Implementation Evidence"]
        self.assertTrue(section.startswith("Independent code review result\n\n" + delivery.ITEM_PANEL_PASSES), section)
        trigger = "Revisit at the next change to src/product.py."
        self.assertEqual(section.split(delivery.ITEM_PANEL_PASSES, 1)[1], "\n".join([
            "", "",
            "| pass | mode | official_seconds | panel_seconds | combined_seconds | combined_ratio"
            " | official_blocking | carried_panel_blocking | confirmed | minor | invalid | duplicate |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
            f"| 1 | review_initial | 600.0 | 180.0 | 660.0 | 1.1 | none | none | {blocker} | {follow_up}"
            f" | P1-correctness-contract-01 | P1-correctness-contract-02 of {follow_up} |",
            "| 2 | review_repair | 600.0 | 180.0 | 610.0 | 1.02 | none | none | none | none | none | none |",
            "",
            delivery.ITEM_PANEL_CLAIMS,
            "",
            "| pass | finding | lens | severity | ruling | file | description | reason | owner_role"
            " | revisit_trigger |",
            "|---|---|---|---|---|---|---|---|---|---|",
            "| 1 | P1-correctness-contract-01 | correctness-contract | critical | invalid | src/product.py:3"
            " | Defect P1-correctness-contract-01 in the product. | src/product.py:3 shows the claim as written."
            " | none | none |",
            f"| 1 | P1-correctness-contract-02 | correctness-contract | major | duplicate of {follow_up}"
            " | src/product.py:2 | Defect P1-correctness-contract-02 in the product."
            " | src/product.py:2 shows the claim as written. | none | none |",
            f"| 1 | {blocker} | security-isolation | major | confirmed | src/product.py:1 | Defect {blocker} in the"
            " product. | src/product.py:1 shows the claim as written. | none | none |",
            f"| 1 | {follow_up} | security-isolation | major | minor | src/product.py:2 | Defect {follow_up} in"
            f" the product. | src/product.py:2 shows the claim as written. | backend_developer | {trigger} |"]))

    def test_the_code_review_registers_while_qa_runs_its_command(self):
        """Every code review registration verb goes on while QA's verification command holds the command lock,
        which holds back only QA's own result (#355)."""
        self.policy(code_review_panel="beside_official", review_loop="blocking_delta")
        self.freeze()
        official_claim = self.finding("F-1", line=3)
        panel_claim = self.finding(PREFIX["conformance-tests"] + "01")
        with verification.command_lock(self.root, "qa_engineer", "run --kind test"):
            self.register_panel(self.official(verdict="failed", findings=[official_claim]),
                                {"correctness-contract": [], "security-isolation": [],
                                 "conformance-tests": [panel_claim]})
            verification.register_calibration(self.root, self.calibration(
                [official_claim, panel_claim],
                [self.ruling("F-1", ruling="invalid", line=3), self.ruling(panel_claim["id"], ruling="minor")]))
            verification.merge_panel(self.root)
            self.assertEqual(self.session()["workers"]["code_reviewer"]["state"], "settled")
            with self.assertRaisesRegex(RuntimeError, "^wait for qa_engineer's verification command `run --kind"
                                                      " test` in process [0-9]+ since "):
                verification.register_result(self.root, self.qa(final=False))
        verification.register_result(self.root, self.qa())
        verification.validate(self.root, "DLV-001", "AUTH-01")

    def test_blocking_delta_records_the_official_claims_with_the_panel_claims(self):
        """The session-backed record of a blocking_delta pass; CodeReviewPanelRuleTests holds its calibration."""
        self.policy(code_review_panel="beside_official", review_loop="blocking_delta")
        self.freeze()
        official_claim = self.finding("F-1", line=3)
        panel_claim = self.finding(PREFIX["conformance-tests"] + "01")
        self.register_panel(self.official(verdict="failed", findings=[official_claim]),
                            {"correctness-contract": [], "security-isolation": [], "conformance-tests": [panel_claim]})
        rows = [self.ruling("F-1", ruling="invalid", line=3), self.ruling(panel_claim["id"], ruling="minor")]
        verification.register_calibration(self.root, self.calibration([official_claim, panel_claim], rows))
        verification.merge_panel(self.root)
        result = self.session()["workers"]["code_reviewer"]["result"]
        # The official result failed on its own claim; calibration disproved it, so the review passes.
        self.assertEqual(result["verdict"], "failed")
        self.assertEqual(verification.calibrated_verdict(result), "passed")
        self.assertEqual(result["panel"]["official_blocking"], ["F-1"])
        verification.register_result(self.root, self.qa())
        verification.validate(self.root, "DLV-001", "AUTH-01")
        sections = self.approve_evidence()
        # The panel claims reach the record at blocking_delta too, beside the follow-ups and the calibration.
        self.assertEqual(delivery.block_rows(sections["Implementation Evidence"], delivery.ITEM_PANEL_CLAIMS), [
            f"| 1 | {panel_claim['id']} | conformance-tests | major | minor | src/product.py:1 | Defect"
            f" {panel_claim['id']} in the product. | src/product.py:1 shows the claim as written."
            " | backend_developer | Revisit at the next change to src/product.py. |"])
        section = sections["Deviations and Follow-ups"]
        self.assertIn(f"| {panel_claim['id']} | minor | src/product.py:1 | Defect {panel_claim['id']} in the"
                      " product. | backend_developer | Revisit at the next change to src/product.py. |", section)
        self.assertIn(f"| {panel_claim['id']} | major | minor | src/product.py:1 shows the claim as written. |", section)
        self.assertIn("| F-1 | major | invalid | src/product.py:3 shows the claim as written. |", section)


T0 = 1_000_000.0
ROLE_SEQUENCE = ["backend_developer", "code_reviewer", "qa_engineer"]
TRIGGER = "Revisit at the next change to src/product.py."


class CodeReviewPanelRuleTests(unittest.TestCase):
    """The rules panel-result, calibrate and merge-panel apply, on an in-memory session.

    The candidate, the Process Policy, the Item record and the candidate's line
    counts are the seams the session verbs read through Git and the docs tree;
    here each is fixed, so no case starts a process.
    """

    finding = staticmethod(CodeReviewPanelMachineTests.finding)
    ruling = staticmethod(CodeReviewPanelMachineTests.ruling)

    def setUp(self):
        self.root = Path("in-memory-item")
        self.current = {"delivery": "DLV-001", "story": "AUTH-01", "candidate_hash": "sha256:candidate",
                        "product_commit": "0" * 40, "runtime_required": False}
        self.loop = "current"
        lines = {"src/product.py": 3}
        for name, seam in (("require_current", lambda root, value, allow_evidence=False: value["candidate"]),
                           ("code_review_panel", lambda root, delivery_id: verification.BESIDE_OFFICIAL),
                           ("review_loop", lambda root, delivery_id: self.loop),
                           ("item_record", lambda root, delivery_id, story: {"role_sequence": ROLE_SEQUENCE}),
                           ("candidate_line_count", lambda root, commit, path: lines.get(path))):
            patcher = mock.patch.object(verification, name, seam)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start_session(0.0)

    def start_session(self, start, unresolved=(), history=()):
        """A frozen session as freeze leaves it, with the panel its first registration starts."""
        self.start = start
        self.value = {"session_id": f"session-{start}", "candidate": self.current,
                      "unresolved_findings": list(unresolved),
                      "workers": {role: {"state": "running", "started_at": T0 + start} for role in verification.ROLES}}
        if history:
            self.value["panel_history"] = list(history)
        self.panel = verification.code_review_panel_state(self.root, self.value)

    def official(self, verdict="passed", findings=(), mode="review_initial"):
        checks = {name: {"passed": True, "evidence": "Independently verified"}
                  for name in verification.required_checks(self.root, self.current, "code_reviewer")}
        return {"candidate_hash": self.current["candidate_hash"], "session_id": self.value["session_id"],
                "role": "code_reviewer", "mode": mode, "verdict": verdict,
                "report": "Independent code review result", "checks": checks, "findings": list(findings)}

    def lens(self, lens, findings=(), verdict=None):
        findings = list(findings)
        if verdict is None:
            verdict = "failed" if any(verification.blocking(finding) for finding in findings) else "passed"
        return {"candidate_hash": self.current["candidate_hash"], "session_id": self.value["session_id"],
                "role": "code_reviewer", "mode": "panel_lens", "lens": [lens], "verdict": verdict,
                "report": f"Lens {lens} read the candidate", "findings": findings}

    def calibration(self, claims, rows):
        return {"candidate_hash": self.current["candidate_hash"], "session_id": self.value["session_id"],
                "role": "code_reviewer", "mode": "calibration", "report": "Independent calibration of the claims",
                "claims": list(claims), "calibration": list(rows)}

    def check(self, result):
        return verification.check_panel_result(self.root, self.value, self.current, self.panel, result)

    def register(self, result, elapsed):
        """Store a checked panel result as register_panel_result stores it, *elapsed* seconds after the freeze."""
        key = self.check(result)
        self.panel["members"][key] = {"result": {**result, "result_hash": verification.digest(result)},
                                      "completed_at": T0 + self.start + elapsed, "elapsed_seconds": elapsed}

    def register_panel(self, official, lenses):
        """Each lens result 30 s apart from +120 s and the official result at +600 s, as the machine tests do."""
        for index, (lens, findings) in enumerate(lenses.items()):
            self.register(self.lens(lens, findings), 120.0 + 30 * index)
        self.register(official, 600.0)

    def check_calibration(self, result):
        verification.check_calibration(self.root, self.value, self.current, self.loop, self.panel, result)

    def calibrate(self, claims, rows):
        result = self.calibration(claims, rows)
        self.check_calibration(result)
        self.value["calibration"] = {"state": "settled",
                                     "result": {**result, "result_hash": verification.digest(result)}}

    def merge(self, elapsed):
        with mock.patch.object(verification.time, "time", return_value=T0 + self.start + elapsed):
            return verification.merged_panel_result(self.root, self.value, self.current, self.panel)

    def settle(self, result):
        """The session freeze replaces after a merge: the settled code review and its carried findings."""
        stored = {**result, "result_hash": verification.digest(result)}
        unresolved = [{**finding, "role": "code_reviewer"} for finding in stored["findings"]
                      if finding["status"] != "resolved"]
        return stored, unresolved

    def test_panel_result_checks_each_member_when_it_registers(self):
        lens = "security-isolation"
        prefix = PREFIX[lens]
        refusals = (
            ("lens must name one assignment of the code review panel",
             {**self.lens(lens), "lens": ["security"]}),
            ("a code review panel result names mode review_initial or review_repair for the official"
             " reviewer, or panel_lens for a lens reader", {**self.lens(lens), "mode": "review_lens"}),
            ("a code review panel result's verdict is passed or failed",
             {**self.lens(lens), "verdict": "cancelled"}),
            (f"F-9 must start with its assignment's id_prefix {prefix}",
             self.lens(lens, [self.finding("F-9")])),
            (f"{prefix}01 must be a new open finding",
             self.lens(lens, [{**self.finding(prefix + "01"), "status": "resolved"}])),
            (f"{prefix}01 needs its file and description",
             self.lens(lens, [{key: value for key, value in self.finding(prefix + "01").items()
                               if key != "file"}])),
            ("verification result requires its independent report", {**self.lens(lens), "report": " "}),
            ("passing verification cannot retain an open blocking finding",
             self.lens(lens, [self.finding(prefix + "01")], verdict="passed")),
            ("result does not bind this candidate and reader session", {**self.lens(lens), "session_id": "other"}),
            # The official result is checked as result would check it, before the merge needs it.
            ("final code_reviewer result requires passing correctness evidence", {**self.official(), "checks": {}}),
            ("passing verification cannot retain an open blocking finding",
             self.official(findings=[self.finding("F-1")])),
            ("calibration rows come only from the calibration reader's own result",
             {**self.official(), "calibration": []}),
        )
        for message, result in refusals:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    self.check(result)
        self.assertEqual(self.panel["members"], {})
        self.register(self.lens(lens, [self.finding(prefix + "01")]), 120.0)
        with self.assertRaisesRegex(RuntimeError, "lens assignment security-isolation is already registered"):
            self.check(self.lens(lens))
        with self.assertRaisesRegex(RuntimeError, f"{prefix}01 is already held by another result of this pass or"
                                                  " an earlier cycle"):
            self.check(self.official(verdict="failed", findings=[self.finding(prefix + "01")]))
        self.register(self.official(), 600.0)
        with self.assertRaisesRegex(RuntimeError, "the official code review result is already registered"):
            self.check(self.official())

    def test_each_panel_claim_is_calibrated_before_it_gates(self):
        claim = self.finding(PREFIX["security-isolation"] + "01")
        twin = self.finding(PREFIX["correctness-contract"] + "01", line=2)
        official = self.finding("F-1", line=3)
        with self.assertRaisesRegex(RuntimeError, "register the official result and every lens result with"
                                                  " panel-result first; missing: official, correctness-contract,"
                                                  " security-isolation, conformance-tests"):
            self.check_calibration(self.calibration([claim], [self.ruling(claim["id"])]))
        self.register_panel(self.official(verdict="failed", findings=[official]),
                            {"correctness-contract": [twin], "security-isolation": [claim],
                             "conformance-tests": [self.finding(PREFIX["conformance-tests"] + "01", "minor")]})
        expected = f"{twin['id']}, {claim['id']}"
        rows = [self.ruling(twin["id"], line=2), self.ruling(claim["id"])]
        refusals = (
            # At review_loop current the official claims gate as returned and are never ruled.
            (f"calibration claims must be exactly the open critical or major claims to rule, as returned: {expected}",
             [claim, twin, official], [*rows, self.ruling("F-1", line=3)]),
            (f"calibration claims must be exactly the open critical or major claims to rule, as returned: {expected}",
             [claim, {**twin, "description": "Edited."}], rows),
            (f"{claim['id']} duplicate_of must name an open critical or major official finding or another lens"
             " claim not ruled duplicate", [twin, claim],
             [rows[0], self.ruling(claim["id"], ruling="duplicate", duplicate_of="F-404")]),
            (f"{claim['id']} duplicate_of must name an open critical or major official finding or another lens"
             " claim not ruled duplicate", [twin, claim],
             [self.ruling(twin["id"], ruling="duplicate", line=2, duplicate_of=claim["id"]),
              self.ruling(claim["id"], ruling="duplicate", duplicate_of=twin["id"])]),
            (f"{twin['id']} names duplicate_of without a duplicate ruling", [twin, claim],
             [self.ruling(twin["id"], line=2, duplicate_of="F-1"), rows[1]]),
            (f"{claim['id']} calibrated_severity must confirm major or be minor, invalid or duplicate",
             [twin, claim], [rows[0], self.ruling(claim["id"], ruling="critical")]),
            (f"{claim['id']} calibration reason must cite the candidate text as path:line",
             [twin, claim], [rows[0], self.ruling(claim["id"], line=9)]),
        )
        for message, claims, ruled in refusals:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    self.check_calibration(self.calibration(claims, ruled))
        self.check_calibration(self.calibration(
            [twin, claim], [rows[0], self.ruling(claim["id"], ruling="duplicate", duplicate_of="F-1")]))

    def test_merge_panel_unions_the_official_and_the_validated_panel_findings(self):
        confirmed = self.finding(PREFIX["security-isolation"] + "01")
        lowered = self.finding(PREFIX["security-isolation"] + "02", line=2)
        invalid = self.finding(PREFIX["correctness-contract"] + "01", "critical", line=3)
        twin = self.finding(PREFIX["correctness-contract"] + "02", line=2)
        note = self.finding(PREFIX["conformance-tests"] + "01", "minor", line=3)
        official = self.official(findings=[self.finding("F-1", "minor", line=3)])
        self.register_panel(official, {"correctness-contract": [invalid, twin],
                                       "security-isolation": [confirmed, lowered], "conformance-tests": [note]})
        claims = sorted([confirmed, lowered, invalid, twin], key=lambda finding: finding["id"])
        with self.assertRaisesRegex(RuntimeError, "register the calibration reader's result with calibrate for exactly"
                                                  " the open critical or major claims to rule, as returned: "
                                                  + ", ".join(claim["id"] for claim in claims)):
            self.merge(660.0)
        rows = [self.ruling(confirmed["id"]), self.ruling(lowered["id"], ruling="minor", line=2),
                self.ruling(invalid["id"], "critical", "invalid", line=3),
                self.ruling(twin["id"], ruling="duplicate", line=2, duplicate_of=lowered["id"])]
        self.calibrate(claims, rows)
        result = self.merge(660.0)
        self.assertEqual((result["role"], result["mode"], result["verdict"], result["report"]),
                         ("code_reviewer", "review_initial", "failed", "Independent code review result"))
        self.assertEqual(result["checks"], official["checks"])
        merged = {finding["id"]: finding for finding in result["findings"]}
        self.assertEqual(sorted(merged), ["F-1", confirmed["id"], lowered["id"]])
        self.assertEqual(merged["F-1"], {**official["findings"][0], "source": "official"})
        self.assertEqual(merged[confirmed["id"]], {**confirmed, "source": "panel", "lens": ["security-isolation"],
                                                   "claimed_severity": "major", "calibrated_severity": "major"})
        self.assertEqual(merged[lowered["id"]], {
            **lowered, "source": "panel", "lens": ["security-isolation"], "severity": "minor",
            "claimed_severity": "major", "calibrated_severity": "minor", "owner_role": "backend_developer",
            "revisit_trigger": TRIGGER})
        self.assertEqual(result["calibration"], rows)
        self.assertEqual(result["calibration_result_hash"], self.value["calibration"]["result"]["result_hash"])

        def claim(finding, lens, ruling, **extra):
            return {"finding": finding["id"], "lens": [lens], "severity": finding["severity"], "ruling": ruling,
                    "file": finding["file"], "description": finding["description"],
                    "reason": f"{finding['file']} shows the claim as written.", **extra}
        members = self.panel["members"]
        self.assertEqual(result["panel"], {
            "pass": 1, "mode": "review_initial",
            "official_result_hash": members["official"]["result"]["result_hash"],
            "lens_result_hashes": {lens: members[lens]["result"]["result_hash"] for lens in LENSES},
            "official_seconds": 600.0, "panel_seconds": 180.0, "combined_seconds": 660.0, "combined_ratio": 1.1,
            "official_blocking": [], "carried_panel_blocking": [], "confirmed": [confirmed["id"]],
            "minor": [lowered["id"]], "invalid": [invalid["id"]], "duplicate": {twin["id"]: lowered["id"]},
            # Every lens claim keeps its text and ruling, which no later session holds otherwise.
            "claims": [claim(invalid, "correctness-contract", "invalid"),
                       claim(twin, "correctness-contract", "duplicate", duplicate_of=lowered["id"]),
                       claim(confirmed, "security-isolation", "confirmed"),
                       claim(lowered, "security-isolation", "minor", owner_role="backend_developer",
                             revisit_trigger=TRIGGER)]})
        self.value["workers"]["code_reviewer"]["state"] = "settled"
        with self.assertRaisesRegex(RuntimeError, "the code review already settled"):
            self.merge(690.0)

    def test_a_repair_pass_keeps_the_source_of_a_carried_panel_finding(self):
        blocker = self.finding(PREFIX["security-isolation"] + "01")
        self.register_panel(self.official(), {"correctness-contract": [], "security-isolation": [blocker],
                                              "conformance-tests": []})
        self.calibrate([blocker], [self.ruling(blocker["id"])])
        first, unresolved = self.settle(self.merge(660.0))
        # The next pass numbers its lens ids after every earlier pass.
        self.start_session(1000.0, unresolved, [first["panel"]])
        self.assertEqual((self.panel["pass"], [verification.panel_prefix(self.panel, assignment)
                                               for assignment in self.panel["assignments"]]),
                         (2, [f"P2-{lens}-" for lens in LENSES]))
        # The official repair review dispositions every inherited finding, the panel's included.
        with self.assertRaisesRegex(RuntimeError, "final result must explicitly disposition every inherited finding"):
            self.check(self.official(mode="review_repair"))
        # The repair leaves the panel blocker open, and the official reviewer finds a defect of its own;
        # it re-lists the panel finding as a disposition, without the panel's fields.
        [inherited] = unresolved
        relisted = {key: value for key, value in inherited.items() if key not in {"role", "source", "lens"}}
        own = self.finding("F-2", line=2)
        self.register_panel(self.official(verdict="failed", mode="review_repair", findings=[relisted, own]),
                            {lens: [] for lens in LENSES})
        second, unresolved = self.settle(self.merge(610.0))
        merged = {finding["id"]: finding for finding in second["findings"]}
        self.assertEqual((merged[blocker["id"]]["source"], merged[blocker["id"]]["lens"]),
                         ("panel", ["security-isolation"]))
        self.assertEqual(merged["F-2"]["source"], "official")
        record = second["panel"]
        self.assertEqual((record["official_blocking"], record["carried_panel_blocking"], record["confirmed"]),
                         (["F-2"], [blocker["id"]], []))
        self.start_session(2000.0, unresolved, [first["panel"], second["panel"]])
        resolved = [{**{key: value for key, value in finding.items() if key != "role"}, "status": "resolved"}
                    for finding in unresolved]
        self.register_panel(self.official(mode="review_repair", findings=resolved), {lens: [] for lens in LENSES})
        third = self.merge(620.0)
        self.assertEqual({finding["id"]: finding["source"] for finding in third["findings"]},
                         {blocker["id"]: "panel", "F-2": "official"})
        body = delivery.with_panel_record(
            delivery.body_for("item", "Code review", {"Implementation Evidence": third["report"]}),
            {"panel_history": [first["panel"], second["panel"]]}, third)
        section = delivery.section_bodies(body)["Implementation Evidence"]
        self.assertEqual(delivery.block_rows(section, delivery.ITEM_PANEL_PASSES), [
            f"| 1 | review_initial | 600.0 | 180.0 | 660.0 | 1.1 | none | none | {blocker['id']} | none | none"
            " | none |",
            f"| 2 | review_repair | 600.0 | 180.0 | 610.0 | 1.02 | F-2 | {blocker['id']} | none | none | none"
            " | none |",
            "| 3 | review_repair | 600.0 | 180.0 | 620.0 | 1.03 | none | none | none | none | none | none |"])

    def test_a_duplicate_names_a_finding_at_least_as_severe_as_its_claim(self):
        official = self.finding("F-1", line=3)
        secret = self.finding(PREFIX["security-isolation"] + "01", "critical")
        twin = self.finding(PREFIX["correctness-contract"] + "01", line=2)
        self.register_panel(self.official(verdict="failed", findings=[official]),
                            {"correctness-contract": [twin], "security-isolation": [secret],
                             "conformance-tests": []})
        # A critical claim never folds into a major finding, as no ruling lowers it to major.
        for target in ("F-1", twin["id"]):
            with self.subTest(target=target):
                with self.assertRaisesRegex(RuntimeError, re.escape(
                        f"{secret['id']} duplicate_of must name a finding at least as severe as the claim,"
                        f" critical; {target} is major")):
                    self.check_calibration(self.calibration([twin, secret], [
                        self.ruling(twin["id"], line=2),
                        self.ruling(secret["id"], "critical", "duplicate", duplicate_of=target)]))
        self.calibrate([twin, secret], [
            self.ruling(twin["id"], ruling="duplicate", line=2, duplicate_of=secret["id"]),
            self.ruling(secret["id"], "critical", "critical")])
        result = self.merge(660.0)
        self.assertEqual({finding["id"]: (finding["severity"], finding["source"]) for finding in result["findings"]},
                         {"F-1": ("major", "official"), secret["id"]: ("critical", "panel")})
        self.assertEqual(result["panel"]["duplicate"], {twin["id"]: secret["id"]})

    def test_blocking_delta_calibrates_the_official_claims_with_the_panel_claims(self):
        self.loop = "blocking_delta"
        official_claim = self.finding("F-1", line=3)
        panel_claim = self.finding(PREFIX["conformance-tests"] + "01")
        self.register_panel(self.official(verdict="failed", findings=[official_claim]),
                            {"correctness-contract": [], "security-isolation": [], "conformance-tests": [panel_claim]})
        with self.assertRaisesRegex(RuntimeError, re.escape(
                "calibration claims must be exactly the open critical or major claims to rule, as returned: "
                f"F-1, {panel_claim['id']}")):
            self.check_calibration(self.calibration([panel_claim], [self.ruling(panel_claim["id"])]))
        rows = [self.ruling("F-1", ruling="invalid", line=3), self.ruling(panel_claim["id"], ruling="minor")]
        self.calibrate([official_claim, panel_claim], rows)
        result = self.merge(660.0)
        # The official result failed on its own claim; calibration disproved it, so the review passes.
        self.assertEqual(result["verdict"], "failed")
        self.assertEqual(verification.calibrated_verdict(result), "passed")
        self.assertEqual(result["panel"]["official_blocking"], ["F-1"])
        self.assertEqual(result["calibration"], rows)


if __name__ == "__main__":
    unittest.main()
