"""Delivery path: switch `delivery_path` keeps every Delivery on the standard
two-step, two-gate path at `standard` and, at `light_when_eligible`, plans a
Delivery that the compiler finds eligible in one step with one owner gate,
without skipping a check the standard path runs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import backlog_fixture  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_governance  # noqa: E402
import operation_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "delivery_path"
PLAN_REFERENCE = "skill-content/delivery-plan/references/switch-delivery_path-light_when_eligible.md"
TOPOLOGY_REFERENCE = "skill-content/execution-plan/references/switch-delivery_path-light_when_eligible.md"
DECISION = "[[solution-design/decisions/fixture-api|Fixture API]]"
LIGHT = {SWITCH: "light_when_eligible", "story_size_budget": "propose_split"}
LIMITS = {"acceptance_criteria": 5}
REASON = "The session check stays inside the approved authentication module and its interface."
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
DELIVERY = "DLV-001"


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_IDENTITY}).stdout.strip()


def policy(docs: Path, *argv: str) -> None:
    code, output = quiet(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output)


def set_policy(docs: Path, switches: dict, limits: dict | None = None) -> None:
    """Approve a Process Policy revision that sets exactly these switches and story size limits."""
    policy(docs, "begin-revision" if process_policy.path_for(docs).exists() else "init")
    for switch in process_policy.load_registry():
        policy(docs, "set", "--switch", switch, "--default")
    for switch, value in switches.items():
        policy(docs, "set", "--switch", switch, "--value", value)
    for name, limit in (limits or {}).items():
        policy(docs, "set", "--switch", "story_size_budget", "--parameter", name, "--value", str(limit))
    policy(docs, "approve")


def contract_args(docs: Path, kind: str = "verification"):
    return type("Args", (), {"docs": str(docs), "kind": kind, "constrained_by": [DECISION]})


def approve_verification_contract(docs: Path) -> None:
    args = contract_args(docs)
    if not operation_compile.contract_path(docs, "verification").exists():
        quiet(operation_compile.init, args)
    path = operation_compile.contract_path(docs, "verification")
    props, body = operation_compile.parse(path)
    props["test_command"] = "make test"
    operation_compile.atomic_text(path, operation_compile.render(props, body))
    quiet(operation_compile.approve, args)


def story_author(roles: tuple[str, ...] = (), criteria: int = 1, fields: dict | None = None):
    """Author each fixture Story with extra supporting roles, acceptance criteria and front-matter fields."""
    original = backlog_fixture._author_story

    def author(story: Path, test_plan: Path, story_id: str) -> None:
        original(story, test_plan, story_id)
        props, body = backlog_compile.parse_front_matter(story)
        props.update(fields or {})
        if roles:
            props["supporting_roles"] = list(roles)
            owner = "- backend_developer: Implement the validated account boundary and API integration."
            body = body.replace(owner, owner + "".join(f"\n- {role}: Keep the session boundary intact."
                                                     for role in roles))
        criterion = "- [ ] Every cited criterion has an observable passing result."
        body = body.replace(criterion, criterion + "".join(
            f"\n- [ ] Rejected request {number} leaves no session." for number in range(2, criteria + 1)))
        story.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
    return author


def build_project(root: Path, stories: tuple[str, ...] = ("AUTH-01",), *, roles: tuple[str, ...] = (),
                  criteria: int = 1, fields: dict | None = None, remote: bool = False) -> Path:
    """Commit one approved backlog, Definition of Done and Verification Contract, and push them when asked."""
    init_repository(root, initial_branch="main")
    # The coordinator's own commits read the repository's identity.
    git(root, "config", "user.email", GIT_IDENTITY["GIT_AUTHOR_EMAIL"])
    git(root, "config", "user.name", GIT_IDENTITY["GIT_AUTHOR_NAME"])
    docs = root / "workspace" / "docs"
    (docs / "maps").mkdir(parents=True)
    (root / "workspace" / "config.json").write_text(json.dumps({
        "schema_version": 2, "team_id": "software-engineering-team",
        "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
    workflow = root / ".github" / "workflows" / "tests.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on:\n  pull_request:\n", encoding="utf-8")
    quiet(delivery_governance.init, type("Args", (), {"docs": str(docs), "max_parallel": 1}))
    quiet(delivery_governance.approve, type("Args", (), {"docs": str(docs)}))
    with mock.patch.object(backlog_fixture, "_author_story", story_author(roles, criteria, fields)):
        backlog_fixture.make_approved_backlog(docs, *stories)
    dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
    for call in (delivery_compile.init_dod, delivery_compile.approve_dod):
        if quiet(call, dod)[0]:
            raise AssertionError("fixture Definition of Done was refused")
    approve_verification_contract(docs)
    commit(root, "approved sources")
    if remote:
        bare = root / "remote.git"
        init_repository(bare, bare=True)
        git(root, "remote", "add", "origin", str(bare))
        git(root, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    return docs


def commit(root: Path, message: str) -> None:
    git(root, "add", "--all")
    git(root, "commit", "-qm", message)


def init_args(docs: Path, stories=("AUTH-01",)):
    return type("Args", (), {"docs": str(docs), "id": None, "slug": "auth", "goal": "Authenticate",
                             "outcome": None, "target_branch": "main", "story": list(stories)})


def plan_args(docs: Path):
    return type("Args", (), {"docs": str(docs), "delivery": DELIVERY, "remote": "origin"})


def item_path(docs: Path, story: str = "AUTH-01") -> Path:
    return delivery_compile.find_delivery(docs, DELIVERY) / "items" / story.lower() / "item.md"


def author_topology(docs: Path, story: str = "AUTH-01", **fields) -> None:
    """Write what the Software Architect's topology-only pass writes on the Item."""
    path = item_path(docs, story)
    props, body = delivery_compile.split_note(path)
    props.update({"path_claims": ["src/auth.py"], "contract_claims": ["auth:session"],
                  "architecture_reason": REASON, **fields})
    delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))


def user_decisions(docs: Path) -> str:
    path = delivery_compile.find_delivery(docs, DELIVERY) / "delivery.md"
    return delivery_compile.section_bodies(delivery_compile.split_note(path)[1])["User Decisions"]


def run(call, args) -> tuple[int, dict]:
    code, output = quiet(call, args)
    return code, json.loads(output)


def failed(report: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for failure in report["failed"]:
        found.setdefault(failure["condition"], []).append(failure["finding"])
    return found


@contextlib.contextmanager
def lost_push_response():
    """Let the next atomic push land on the remote while its response is lost on the way back."""
    real_run = subprocess.run
    lost: list = []

    def landed_without_response(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if (not lost and isinstance(command, list) and command[:3] == ["git", "push", "--atomic"]
                and result.returncode == 0):
            lost.append(command)
            return subprocess.CompletedProcess(command, 128, "", "fatal: the remote end hung up unexpectedly")
        return result

    with mock.patch.object(subprocess, "run", side_effect=landed_without_response):
        yield lost


class DeliveryPathInstructionTests(unittest.TestCase):
    def test_the_reference_names_every_condition_the_compiler_checks(self):
        text = read(PLAN_REFERENCE)
        for condition in delivery_compile.LIGHT_PATH_CONDITIONS:
            with self.subTest(condition=condition):
                self.assertIn(f"- `{condition}`:", text)

    def test_skills_and_the_architect_role_are_unchanged(self):
        for relative in ("skill-content/delivery-plan/SKILL.md", "skill-content/execution-plan/SKILL.md",
                         "agents/software-architect.md", "agents/delivery-coordinator.md"):
            with self.subTest(path=relative):
                self.assertNotIn("delivery_path", read(relative))
                self.assertNotIn("light path", read(relative))


class DeliveryPathTaskInputTests(unittest.TestCase):
    # (entry, role): the light-path references the task binds at light_when_eligible.
    TASKS = {("delivery-plan", "delivery-coordinator"): {PLAN_REFERENCE},
             ("delivery-plan", "product-owner"): {PLAN_REFERENCE},
             ("execution-plan", "software-architect"): {TOPOLOGY_REFERENCE},
             ("execution-plan", "delivery-coordinator"): {TOPOLOGY_REFERENCE},
             ("deliver", "software-architect"): set(), ("deliver", "delivery-coordinator"): set(),
             ("backlog-plan", "product-owner"): set(), ("configure", "delivery-coordinator"): set()}

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.root = Path(self.temporary.name).resolve()
        init_repository(self.root)
        (self.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        commit(self.root, "fixture")
        self.docs = self.root / "workspace" / "docs"

    def bound(self, entry: str, role: str) -> set[str]:
        result = task_inputs.manifest(entry=entry, role=role, mode="review", project=self.root)
        paths = set(result["required_reads"]) | {item["path"] for item in result["instructions"]}
        return paths & {PLAN_REFERENCE, TOPOLOGY_REFERENCE}

    def test_only_planning_tasks_bind_the_light_path_and_only_at_its_value(self):
        for state in ("no policy", "standard", "light_when_eligible"):
            if state != "no policy":
                set_policy(self.docs, {SWITCH: state})
            for (entry, role), references in self.TASKS.items():
                with self.subTest(state=state, entry=entry, role=role):
                    wanted = references if state == "light_when_eligible" else set()
                    self.assertEqual(self.bound(entry, role), wanted)


class LightPathCompilerTests(unittest.TestCase):
    """The compiler decides eligibility from the records and records the path the Delivery took."""

    def project(self, stories=("AUTH-01",), **options) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        return build_project(Path(temporary.name).resolve(), stories, **options)

    def propose(self, docs: Path, stories=("AUTH-01",)) -> dict:
        code, result = run(delivery_compile.init_delivery, init_args(docs, stories))
        self.assertEqual(code, 0, result)
        return result

    def check(self, docs: Path) -> tuple[int, dict]:
        return run(delivery_compile.light_path_check, plan_args(docs))

    def check_proposal(self, docs: Path) -> tuple[int, dict]:
        """Check the proposal as it stands, then take back the fallback a failed check records.

        Each case of an eligibility test checks the same proposal; the recorded
        fallback has tests of its own.
        """
        path = delivery_compile.find_delivery(docs, DELIVERY) / "delivery.md"
        before = path.read_bytes()
        try:
            return self.check(docs)
        finally:
            path.write_bytes(before)

    def test_the_standard_value_leaves_every_output_and_record_alone(self):
        for state in ("no policy", "standard"):
            with self.subTest(state=state):
                docs = self.project()
                if state == "standard":
                    set_policy(docs, {"review_panels": "lens_panel"})
                self.assertNotIn(SWITCH, self.propose(docs))
                author_topology(docs)
                code, scope = run(delivery_compile.approve_scope, plan_args(docs))
                self.assertEqual((code, set(scope)), (0, {"ok", "id", "scope_hash"}))
                code, execution = run(delivery_compile.approve_execution, plan_args(docs))
                self.assertEqual(code, 0, execution)
                self.assertNotIn(SWITCH, execution)
                self.assertNotIn("Delivery path:", user_decisions(docs))
                code, refused = self.check(docs)
                self.assertEqual(code, 1)
                self.assertEqual(refused["errors"], [f"{DELIVERY} runs switch delivery_path at standard; only"
                                                     " light_when_eligible plans a Delivery on the light path"])

    def test_the_light_path_proposal_writes_the_standard_records(self):
        docs = self.project()
        written = []
        for switches in ({"story_size_budget": "propose_split"}, LIGHT):
            set_policy(docs, switches, LIMITS)
            self.assertEqual(SWITCH in self.propose(docs), switches == LIGHT)
            root = delivery_compile.find_delivery(docs, DELIVERY)
            written.append({path.relative_to(docs).as_posix(): path.read_bytes()
                            for path in sorted(root.rglob("*")) if path.is_file()})
            written[-1]["maps/delivery.md"] = (docs / "maps" / "delivery.md").read_bytes()
            shutil.rmtree(root)
        self.assertEqual(written[0], written[1])

    def test_init_reports_an_eligible_selection_with_its_topology_pending(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        report = self.propose(docs)[SWITCH]
        receipt = operation_compile.check_contract(docs, "verification")[0]
        self.assertEqual(report, {"value": "light_when_eligible", "eligible": True, "failed": [],
                                  "pending": ["architecture_not_applicable"],
                                  "receipts": [{"kind": "verification", "revision": 1,
                                                "source_hash": receipt["source_hash"]}]})

    def test_the_architects_task_derives_the_topology_pass_on_the_proposal(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        project = docs.parent.parent
        manifest = task_inputs.manifest(entry="execution-plan", role="software-architect", mode="create",
                                        project=project, delivery=DELIVERY,
                                        inputs=[item_path(docs).relative_to(project).as_posix()])
        self.assertIn(TOPOLOGY_REFERENCE, manifest["required_reads"])
        self.assertNotIn(PLAN_REFERENCE, manifest["required_reads"])
        self.assertIn("workspace/docs/delivery/process-policy.md",
                      [record["path"] for record in manifest["project_inputs"]])

    def test_two_stories_are_not_eligible(self):
        docs = self.project(("AUTH-01", "AUTH-02"))
        set_policy(docs, LIGHT, LIMITS)
        report = self.propose(docs, ("AUTH-01", "AUTH-02"))[SWITCH]
        self.assertFalse(report["eligible"])
        self.assertEqual(failed(report), {"single_story": [
            "the selection holds 2 Stories, and the light path plans exactly one"]})

    def test_an_architect_role_is_not_eligible(self):
        docs = self.project(roles=("software_architect",))
        set_policy(docs, LIGHT, LIMITS)
        report = self.propose(docs)[SWITCH]
        self.assertEqual(failed(report), {"no_architect_role": [
            "AUTH-01 lists software_architect among its roles"]})

    def test_a_story_classified_with_operation_impact_is_not_eligible(self):
        # The approved Story's classification decides it before any Operation draft exists (#348).
        reason = "Delivering the story needs a queue service the Environment Contract lacks."
        finding = "AUTH-01 classifies operation_impact required, and the light path revises no Operation contract"
        for impact in ("required", "not_applicable"):
            with self.subTest(impact=impact):
                docs = self.project(fields={"operation_impact": impact, "operation_reason": reason})
                set_policy(docs, LIGHT, LIMITS)
                report = self.propose(docs)[SWITCH]
                author_topology(docs)
                code, checked = self.check_proposal(docs)
                if impact == "required":
                    self.assertFalse(report["eligible"])
                    self.assertEqual(failed(report), {"no_operation_impact": [finding]})
                    self.assertEqual((code, checked["path"], failed(checked)),
                                     (1, "standard", {"no_operation_impact": [finding]}))
                else:
                    self.assertTrue(report["eligible"], report)
                    self.assertEqual((code, checked["path"], checked["failed"]), (0, "light", []),
                                     checked)

    def test_architecture_impact_needs_the_architects_own_reason(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        placeholder = ("AUTH-01 declares no architecture impact without the Software Architect's reason,"
                       " since the reason init writes is a placeholder")
        none = {"architecture_impact": "not_applicable", "architecture_components": [],
                "architecture_record_kinds": []}
        for fields, finding in (
            ({"architecture_impact": "required", "architecture_components": ["api"],
              "architecture_record_kinds": ["decision"]}, "AUTH-01 declares architecture_impact required"),
            ({**none, "architecture_reason": delivery_compile.NO_ARCHITECTURE_REASON}, placeholder),
            ({**none, "architecture_reason": " "}, placeholder),
        ):
            with self.subTest(finding=finding, fields=fields):
                author_topology(docs, **fields)
                code, report = self.check_proposal(docs)
                self.assertEqual(code, 1)
                self.assertEqual(failed(report), {"architecture_not_applicable": [finding]})
                self.assertEqual(report["path"], "standard")
        author_topology(docs, **none)
        code, report = self.check(docs)
        self.assertEqual((code, report["path"], report["failed"], report["plan_findings"]),
                         (0, "light", [], []), report)

    def test_the_light_gate_reports_the_findings_check_plan_reports(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs, path_claims=["../src/auth.py"])
        code, report = self.check(docs)
        checked_code, checked = run(delivery_compile.check_plan, type("Args", (), {
            "docs": str(docs), "delivery": DELIVERY, "reopen": [], "remote": "origin"}))
        self.assertEqual((code, checked_code, report["failed"]), (1, 1, []))
        self.assertEqual(report["plan_findings"], checked["errors"])
        self.assertIn("AUTH-01 path_claim is not normalized: ../src/auth.py", checked["errors"])

    def test_an_open_or_not_current_operation_contract_is_not_eligible(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        contract = operation_compile.contract_path(docs, "verification")
        approved = contract.read_bytes()
        quiet(operation_compile.revise, contract_args(docs))
        self.assertEqual(failed(self.check(docs)[1]), {"operation_contracts_unchanged": [
            "Verification Contract revision 2 is open"]})
        contract.write_bytes(approved.replace(b"Fill the declared", b"Fill every declared"))
        findings = failed(self.check(docs)[1])["operation_contracts_unchanged"]
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].startswith("the Verification Contract is not approved and current:"
                                               " approved contract source_hash is stale"), findings)
        contract.unlink()
        self.assertEqual(failed(self.check(docs)[1])["operation_contracts_unchanged"],
                         ["the Verification Contract is missing"])
        contract.write_bytes(approved)
        author_topology(docs, runtime_required=True)
        self.assertEqual(failed(self.check(docs)[1]), {"operation_contracts_unchanged": [
            "the Environment Contract is missing"]})
        author_topology(docs, runtime_required=False)
        quiet(operation_compile.init, contract_args(docs, "environment"))
        self.assertEqual(failed(self.check(docs)[1]), {"operation_contracts_unchanged": [
            "Environment Contract revision 1 is open"]})

    def test_an_unmet_waits_for_dependency_is_not_eligible(self):
        docs = self.project(("AUTH-01", "AUTH-02"))
        set_policy(docs, LIGHT, LIMITS)
        self.assertTrue(self.propose(docs)[SWITCH]["eligible"])
        author_topology(docs, waits_for=["AUTH-02"])
        code, report = self.check_proposal(docs)
        self.assertEqual(code, 1)
        self.assertEqual(failed(report), {"dependencies_met": [
            "AUTH-02 is recorded integrated by no Delivery that the target branch holds merged"]})
        # A Delivery the target holds merged that records the Story integrated meets it.
        with mock.patch.object(delivery_git, "merged_story_owners",
                               return_value={"AUTH-02": "DLV-009"}) as merged:
            code, report = self.check(docs)
        self.assertEqual((code, report["failed"]), (0, []))
        self.assertEqual(merged.call_args[0][1:], ("HEAD", ["AUTH-02"]))

    def test_every_approved_dependency_is_waited_for(self):
        sources = {"AUTH-02": {"depends_on": ["AUTH-01", "AUTH-03"]}}
        items = {"AUTH-02": ({"waits_for": ["AUTH-04", "AUTH-01"]}, "")}
        self.assertEqual(delivery_compile.waited_for_stories(sources, None), ["AUTH-01", "AUTH-03"])
        self.assertEqual(delivery_compile.waited_for_stories(sources, items), ["AUTH-01", "AUTH-03", "AUTH-04"])
        self.assertEqual(delivery_compile.waited_for_stories({**sources, "AUTH-01": {"depends_on": []}}, None),
                         ["AUTH-03"])

    def test_only_the_owners_size_limits_make_a_story_small(self):
        docs = self.project(criteria=2)
        set_policy(docs, {SWITCH: "light_when_eligible"})
        self.assertEqual(failed(self.propose(docs)[SWITCH]), {"within_story_size_budget": [
            "switch story_size_budget sets no size limit, and without an owner-set limit no Story counts"
            " as small"]})
        set_policy(docs, LIGHT, {"acceptance_criteria": 1, "test_scenarios": 3})
        author_topology(docs)
        self.assertEqual(failed(self.check_proposal(docs)[1]), {"within_story_size_budget": [
            "AUTH-01 acceptance_criteria is 2, over its limit of 1"]})
        set_policy(docs, LIGHT, {"acceptance_criteria": 2})
        self.assertEqual(self.check(docs)[1]["failed"], [])

    def test_scope_approval_binds_the_light_path_record(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        code, gate = self.check(docs)
        self.assertEqual(code, 0, gate)
        code, scope = run(delivery_compile.approve_scope, plan_args(docs))
        self.assertEqual(code, 0, scope)
        line = (f"Delivery path: light. approve-scope approved the scope with the Item topology"
                f" {gate['topology_hash']} and the reused contract receipts verification revision 1"
                f" {gate['receipts'][0]['source_hash']}.")
        self.assertEqual(scope[SWITCH], {"path": "light", "failed": [], "line": line})
        self.assertEqual(user_decisions(docs), line + "\n\nLocal scope proposal; awaiting scope approval.")
        props, body = delivery_compile.split_note(delivery_compile.find_delivery(docs, DELIVERY) / "delivery.md")
        self.assertEqual(props["scope_hash"], delivery_compile.content_hash(
            props, body, exclude=delivery_compile.MUTABLE | {"scope_hash"}))
        self.assertEqual(props["process_policy_source_hash"],
                         process_policy.approved_snapshot(docs)[0]["process_policy_source_hash"])
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]), (0, {"path": "light", "failed": [], "line": line}))
        self.assertEqual(user_decisions(docs), line + "\n\nLocal scope proposal; awaiting scope approval.")

    def test_an_ineligible_delivery_records_the_standard_path_for_good(self):
        docs = self.project(("AUTH-01", "AUTH-02"))
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs, ("AUTH-01", "AUTH-02"))
        code, scope = run(delivery_compile.approve_scope, plan_args(docs))
        self.assertEqual(code, 0, scope)
        self.assertEqual(scope[SWITCH]["path"], "standard")
        self.assertEqual([failure["condition"] for failure in scope[SWITCH]["failed"]],
                         ["single_story", "architecture_not_applicable", "architecture_not_applicable"])
        line = scope[SWITCH]["line"]
        self.assertTrue(line.startswith("Delivery path: standard. approve-scope recorded that the light path"
                                        " does not hold: single_story: the selection holds 2 Stories, and"
                                        " the light path plans exactly one; architecture_not_applicable:"), line)
        self.assertEqual(user_decisions(docs).split("\n\n")[0], line)
        # The standard path plans both Items; execution approval keeps the record.
        for story, claim in (("AUTH-01", "src/auth.py"), ("AUTH-02", "src/session.py")):
            author_topology(docs, story, path_claims=[claim], contract_claims=[f"{story.lower()}:api"])
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]), (0, {"path": "standard", "failed": [], "line": line}))
        code, report = self.check(docs)
        self.assertEqual((code, report["path"], report["recorded"]), (1, "standard", "standard"))

    def test_execution_approval_refuses_a_plan_the_light_gate_did_not_show(self):
        """A topology changed after the light path's one gate is a plan its owner never saw: execution
        approval and check-plan refuse it and name /execution-plan with its gate, and it is approved
        only once light-path-check recorded the fallback (#328)."""
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        author_topology(docs, path_claims=["src/auth.py", "src/session.py"])
        root = delivery_compile.find_delivery(docs, DELIVERY)
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        refusal = [f"{DELIVERY} left the light path after its one owner gate: topology_unchanged: the Item"
                   " topology differs from the one scope approval recorded; the owner has not seen this plan,"
                   " so run light-path-check, which records the fallback, and take the plan to the owner gate"
                   f" of /execution-plan {DELIVERY}"]
        code, refused = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, refused["errors"]), (1, refusal))
        self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
        code, checked = run(delivery_compile.check_plan, type("Args", (), {
            "docs": str(docs), "delivery": DELIVERY, "reopen": [], "remote": "origin"}))
        self.assertEqual((code, checked["errors"]), (1, refusal))
        # The first failed check records the fallback, and the standard path approves the plan.
        code, report = self.check(docs)
        line = ("Delivery path: standard. light-path-check recorded that the Delivery left the light path:"
                " topology_unchanged: the Item topology differs from the one scope approval recorded.")
        self.assertEqual((code, failed(report), report["recorded"], report["fallback"]), (1, {
            "topology_unchanged": ["the Item topology differs from the one scope approval recorded"]},
            "standard", line))
        self.assertEqual(report["plan_findings"], [])
        self.assertEqual(user_decisions(docs).split("\n\n")[0], line)
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]), (0, {"path": "standard", "failed": [], "line": line}))
        self.assertEqual(user_decisions(docs).split("\n\n")[0], line)

    def test_a_transient_failure_ends_the_light_path_for_good(self):
        """A failure that clears again leaves the fallback in place, so execution approval records the
        standard path the Delivery took and a later check never returns it to the light path (#328)."""
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        contract = operation_compile.contract_path(docs, "verification")
        approved = contract.read_bytes()
        quiet(operation_compile.revise, contract_args(docs))
        code, report = self.check(docs)
        line = ("Delivery path: standard. light-path-check recorded that the Delivery left the light path:"
                " operation_contracts_unchanged: Verification Contract revision 2 is open.")
        self.assertEqual((code, report["path"], report["recorded"], report["fallback"]),
                         (1, "standard", "standard", line))
        # The parallel session discards its draft, and every condition holds again.
        contract.write_bytes(approved)
        code, report = self.check(docs)
        self.assertEqual((code, report["eligible"], report["failed"], report["path"], report["recorded"]),
                         (1, True, [], "standard", "standard"))
        self.assertNotIn("fallback", report)
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]), (0, {"path": "standard", "failed": [], "line": line}))
        self.assertEqual(user_decisions(docs).split("\n\n")[0], line)

    def test_a_refused_step_ends_the_light_path(self):
        """Any refused step of the light sequence ends the light path, whatever its remedy: the host names
        it with --refused, the Delivery records it, and the gate's approval no longer drives the plan (#328)."""
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        self.assertEqual(self.check(docs)[0], 0)
        code, output = quiet(delivery_compile.main, ["--docs", str(docs), "light-path-check", "--delivery",
                                                    DELIVERY, "--refused", "reserve-delivery"])
        line = ("Delivery path: standard. light-path-check recorded that the Delivery left the light path:"
                " delivery_path: reserve-delivery was refused.")
        self.assertEqual((code, json.loads(output)), (1, {
            "ok": False, "delivery": DELIVERY, "status": "scope_approved", "path": "standard",
            "recorded": "standard", "fallback": line}))
        self.assertEqual(user_decisions(docs).split("\n\n")[0], line)
        code, report = self.check(docs)
        self.assertEqual((code, report["eligible"], report["path"], report["recorded"]),
                         (1, True, "standard", "standard"))
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]["line"]), (0, line))
        # Only the light sequence's own steps are named.
        with self.assertRaises(SystemExit) as exited, contextlib.redirect_stderr(io.StringIO()):
            delivery_compile.main(["--docs", str(docs), "light-path-check", "--delivery", DELIVERY,
                                   "--refused", "start-item"])
        self.assertEqual(exited.exception.code, 2)

    def test_execution_approval_records_standard_for_a_scope_approved_without_the_light_path(self):
        docs = self.project()
        set_policy(docs, {"story_size_budget": "propose_split"}, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        set_policy(docs, LIGHT, LIMITS)
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual(code, 0, execution)
        self.assertEqual(execution[SWITCH]["failed"], [
            {"condition": SWITCH, "finding": "scope approval recorded no light path"}])
        # A policy that leaves the light path after the record turns it standard at the re-pin.
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        set_policy(docs, {"story_size_budget": "propose_split"}, LIMITS)
        code, execution = run(delivery_compile.approve_execution, plan_args(docs))
        self.assertEqual((code, execution[SWITCH]["failed"]), (0, [
            {"condition": SWITCH, "finding": "the Process Policy that approve-execution pins sets it to standard"}]))

    def test_gate_a_holds_the_light_plan_and_keeps_its_decision_log(self):
        docs = self.project()
        set_policy(docs, {**LIGHT, "owner_gates": "two_fixed_gates"}, LIMITS)
        self.propose(docs)
        path = delivery_compile.find_delivery(docs, DELIVERY) / "delivery.md"
        header = ("| id | class | question | options | recommendation | status | answer | blocks |"
                  " wait_minutes |\n|---|---|---|---|---|---|---|---|---|")
        row = ("| D-01 | queued | Which session store does the Item reuse? | The current store; a new store"
               " | The current store | answered | The current store. | AUTH-01 | 3 |")
        props, body = delivery_compile.split_note(path)
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(
            props, delivery_compile.replace_section(body, "User Decisions", header + "\n" + row)))
        author_topology(docs)
        self.assertEqual(self.check(docs)[0], 0)
        for approve in (delivery_compile.approve_scope, delivery_compile.approve_execution):
            with self.subTest(approve=approve.__name__):
                code, result = run(approve, plan_args(docs))
                self.assertEqual((code, result[SWITCH]["path"]), (0, "light"), result)
                line, table = user_decisions(docs).split("\n\n")
                self.assertTrue(line.startswith("Delivery path: light."), line)
                self.assertEqual(table, header + "\n" + row)
                self.assertEqual(delivery_compile.delivery_findings(docs, DELIVERY)[1], [])

    def test_light_path_check_refuses_outside_the_light_path(self):
        docs = self.project()
        set_policy(docs, LIGHT, LIMITS)
        self.propose(docs)
        author_topology(docs)
        self.assertEqual(run(delivery_compile.approve_scope, plan_args(docs))[0], 0)
        commit(docs.parents[1], "Approve the scope")
        # A policy that keeps the light path leaves it to the first execution
        # approval, which pins that policy; one that ends it is drift.
        set_policy(docs, {**LIGHT, "review_panels": "lens_panel"}, LIMITS)
        self.assertEqual(self.check(docs)[0], 0)
        set_policy(docs, {**LIGHT, SWITCH: "standard"}, LIMITS)
        code, refused = self.check(docs)
        self.assertEqual(code, 1)
        self.assertIn(f"Delivery runs switch {SWITCH} at light_when_eligible under its pinned Process"
                      " Policy revision 1, but the approved revision 3 sets standard",
                      refused["errors"][0])
        # The refused check ends the light path the Delivery was on.
        self.assertEqual(refused["fallback"], "Delivery path: standard. light-path-check recorded that the"
                         f" Delivery left the light path: delivery_path: {refused['errors'][0]}.")
        self.assertEqual(user_decisions(docs).split("\n\n")[0], refused["fallback"])
        path = delivery_compile.find_delivery(docs, DELIVERY) / "delivery.md"
        props, body = delivery_compile.split_note(path)
        props["status"] = "review"
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))
        code, refused = self.check(docs)
        self.assertEqual((code, refused["errors"]), (1, [
            f"{DELIVERY} is review; the light path ends once its Items are claimed"]))
        code, refused = run(delivery_compile.light_path_check, type("Args", (), {
            "docs": str(docs), "delivery": "DLV-404", "remote": "origin"}))
        self.assertEqual((code, refused["errors"]), (1, ["Delivery not found"]))


class LightPathRemoteTests(unittest.TestCase):
    """An eligible Delivery runs on a fixture remote from its proposal to its claims."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.root = Path(self.temporary.name).resolve()
        self.docs = build_project(self.root, remote=True)
        set_policy(self.docs, LIGHT, LIMITS)
        commit(self.root, "process policy")
        git(self.root, "push", "-q")
        self.integration = delivery_git.canonical_refs(DELIVERY)["integration"]

    def light_check(self) -> dict:
        code, report = run(delivery_compile.light_path_check, plan_args(self.docs))
        self.assertEqual((code, report["path"], report["failed"], report["plan_findings"]),
                         (0, "light", [], []), report)
        return report

    def plan_on_the_light_path(self) -> dict:
        """The proposal, the topology-only pass and the check before the one owner gate."""
        code, proposal = run(delivery_compile.init_delivery, init_args(self.docs))
        self.assertEqual((code, proposal[SWITCH]["eligible"]), (0, True), proposal)
        author_topology(self.docs)
        return self.light_check()

    def published(self, oid: str, relative: str) -> str:
        return git(self.root, "show", f"{oid}:workspace/docs/delivery/deliveries/dlv-001-auth/{relative}")

    def test_an_eligible_delivery_runs_to_its_claims_without_a_second_gate(self):
        gate = self.plan_on_the_light_path()
        self.light_check()
        code, scope = run(delivery_compile.approve_scope, plan_args(self.docs))
        self.assertEqual((code, scope[SWITCH]["path"]), (0, "light"), scope)
        self.light_check()
        # A lost response is resolved from the refetched refs, not by retrying or asking again.
        with lost_push_response() as lost:
            with self.assertRaisesRegex(RuntimeError, "^DELIVERY_TRANSACTION_UNCERTAIN: "):
                delivery_git.reserve_delivery(self.root, DELIVERY)
        self.assertEqual(len(lost), 1)
        reserved = delivery_git.remote_oid(self.root, "origin", self.integration)
        self.assertIn("Agentrof-Record: delivery-reservation-v1", git(self.root, "cat-file", "-p", reserved))
        with self.assertRaisesRegex(RuntimeError, "^DELIVERY_REF_COLLISION: "):
            delivery_git.reserve_delivery(self.root, DELIVERY)
        self.light_check()
        code, execution = run(delivery_compile.approve_execution, plan_args(self.docs))
        self.assertEqual((code, execution[SWITCH]["path"]), (0, "light"), execution)
        self.light_check()
        with lost_push_response():
            with self.assertRaisesRegex(RuntimeError, "^DELIVERY_TRANSACTION_UNCERTAIN: "):
                delivery_git.publish_execution_plan(self.root, DELIVERY)
        published = delivery_git.remote_oid(self.root, "origin", self.integration)
        self.assertIn("Agentrof-Record: execution-plan-published-v1", git(self.root, "cat-file", "-p", published))
        self.light_check()
        claims = delivery_git.claim_items(self.root, DELIVERY)
        self.assertEqual(claims["claims"], ["AUTH-01"])
        # The published Delivery names the path it ran and the policy it ran under.
        delivery = self.published(claims["integration"], "delivery.md")
        self.assertIn(f"Delivery path: light. approve-scope approved the scope with the Item topology"
                      f" {gate['topology_hash']} and the reused contract receipts verification revision 1"
                      f" {gate['receipts'][0]['source_hash']}.", delivery)
        self.assertIn("process_policy_source_hash: "
                      + process_policy.approved_snapshot(self.docs)[0]["process_policy_source_hash"], delivery)
        self.assertIn("plan_hash: " + execution["plan_hash"], self.published(claims["integration"],
                                                                             "execution-plan.md"))
        self.assertEqual(run(delivery_compile.check_delivery, plan_args(self.docs))[0], 0)

    def test_a_fallback_after_reservation_keeps_its_approvals_and_records_standard(self):
        self.plan_on_the_light_path()
        code, scope = run(delivery_compile.approve_scope, plan_args(self.docs))
        self.assertEqual(code, 0, scope)
        self.light_check()
        delivery_git.reserve_delivery(self.root, DELIVERY)
        reserved = delivery_git.remote_oid(self.root, "origin", self.integration)
        quiet(operation_compile.revise, contract_args(self.docs))
        code, report = run(delivery_compile.light_path_check, plan_args(self.docs))
        line = ("Delivery path: standard. light-path-check recorded that the Delivery left the light path:"
                " operation_contracts_unchanged: Verification Contract revision 2 is open.")
        self.assertEqual((code, report["path"], report["recorded"], failed(report)["operation_contracts_unchanged"],
                          report["fallback"]),
                         (1, "standard", "standard", ["Verification Contract revision 2 is open"], line))
        # The scope approval and the reservation stay; the standard path continues from there.
        props, _body = delivery_compile.split_note(delivery_compile.find_delivery(self.docs, DELIVERY) / "delivery.md")
        self.assertEqual((props["status"], props["scope_hash"]), ("scope_approved", scope["scope_hash"]))
        self.assertEqual(delivery_git.remote_oid(self.root, "origin", self.integration), reserved)
        approve_verification_contract(self.docs)
        code, execution = run(delivery_compile.approve_execution, plan_args(self.docs))
        self.assertEqual(code, 0, execution)
        self.assertEqual(execution[SWITCH], {"path": "standard", "failed": [], "line": line})
        delivery_git.publish_execution_plan(self.root, DELIVERY)
        self.assertEqual(delivery_git.claim_items(self.root, DELIVERY)["claims"], ["AUTH-01"])
        code, report = run(delivery_compile.light_path_check, plan_args(self.docs))
        self.assertEqual((code, report["path"], report["recorded"]), (1, "standard", "standard"))


if __name__ == "__main__":
    unittest.main()
