"""Owner gates: switch `owner_gates` keeps asking each question when it comes
up at `per_step` and, at `two_fixed_gates`, batches the owner's decisions into
gate A and gate B, queues every other question in `User Decisions` and never
decides anything by default."""

from __future__ import annotations

import contextlib
import io
import json
import os
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
sys.path.insert(0, str(ROOT / "tools"))
import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import fixtures  # noqa: E402
import operation_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "owner_gates"
REGISTRY = "skill-content/configure/data/process-switches.json"
CLASSES = "skill-content/deliver/data/owner-decision-classes.json"
REFERENCE = "skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md"
HEADER = ("| id | class | question | options | recommendation | status | answer | blocks |"
          " wait_minutes |\n|---|---|---|---|---|---|---|---|---|\n")
ANSWER = "One root per checkout, in Verification Contract revision 6."
ROW = ("| D-01 | queued | Which cache root do the runs share? | One root per checkout; one root"
       f" per Item | One root per checkout | answered | {ANSWER} | AUTH-01 | 12 |\n")
PENDING = ROW.replace(f"| answered | {ANSWER} |", "| pending |  |")
DECISION = "[[solution-design/decisions/fixture-api|Fixture API]]"


def ruling(identifier: str, document: str) -> str:
    """An answered row whose answer names the document the owner let change between the gates."""
    return (f"| {identifier} | queued | May {document} change? | Change it; keep it | Change it | answered |"
            f" Change it and approve {document}. | AUTH-01 | 4 |\n")
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def flat(relative: str) -> str:
    return " ".join(read(relative).split())


def quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


def policy(docs: Path, *argv: str) -> None:
    code, output = quiet(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output)


def choose(docs: Path, value: str) -> None:
    policy(docs, "begin-revision" if process_policy.path_for(docs).exists() else "init")
    policy(docs, "set", "--switch", SWITCH, "--value", value)
    policy(docs, "approve")


class OwnerDecisionClassValidatorTests(unittest.TestCase):
    """tools/validate.py rejects an empty or duplicate at-once class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        fixtures.make_valid_root(cls.root)
        cls.path = cls.root / "plugins/software-engineering-team" / CLASSES
        cls.original = cls.path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def messages(self, mutate) -> list[str]:
        data = json.loads(self.original)
        mutate(data)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            findings: list = []
            validate.check_owner_decision_classes(validate.build_tree(self.root), findings)
            return [finding.message for finding in findings]
        finally:
            self.path.write_text(self.original, encoding="utf-8")

    def test_empty_duplicate_and_unanchored_classes_are_rejected(self):
        cases = (
            (lambda data: data.update(classes=[]), "classes must declare at least one at-once class"),
            (lambda data: data["classes"][0].update(description=" "), "empty class"),
            (lambda data: data["classes"][1].update(id=""), "empty class"),
            (lambda data: data["classes"].append(dict(data["classes"][0])),
             "duplicate class 'rule_exception'"),
            (lambda data: data["classes"][0].update(id="architect_escalation"),
             "duplicate class 'architect_escalation'"),
            (lambda data: data["classes"][0].update(id="queued"),
             "class id 'queued' must be lowercase snake_case other than 'queued'"),
            (lambda data: data["agent_clauses"][0].update(agent="release-manager"),
             "names unknown agent 'release-manager'"),
            (lambda data: data["agent_clauses"][0].update(clause="Decides alone"),
             "agent clause 'architect_escalation' is not in agents/software-architect.md"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                messages = self.messages(mutate)
                self.assertTrue(any(fragment in message for message in messages), messages)


class OwnerGatesReferenceTests(unittest.TestCase):
    def test_setup_configure_backlog_and_requirement_gates_are_unchanged(self):
        self.assertIn("Setup, `/configure` outside a Delivery's plan, backlog approval and"
                      " Requirement-flow gates keep their own gates", flat(REFERENCE))
        for relative in ("flows/requirement.md", "flows/backlog-planning.md",
                         "flows/business-analysis.md", "skill-content/setup/SKILL.md",
                         "skill-content/configure/SKILL.md", "skill-content/backlog-plan/SKILL.md",
                         "skill-content/requirement/SKILL.md"):
            with self.subTest(path=relative):
                self.assertNotIn("owner_gates", read(relative))


def bound_instructions(project: Path, entry: str, role: str | None, catalog: dict) -> set[str]:
    """The required reads and hashed instructions a task binds, as task_inputs.manifest derives
    them from the project's Process Policy, without the Git reads of a full manifest."""
    route = catalog["entries"][entry]
    chosen, _policy_inputs = task_inputs.switch_choices(project, route, task_inputs.PACKAGE)
    _registry, _chosen, required, _conditional, hashed = task_inputs.instruction_reads(
        catalog, task_inputs.PACKAGE, route, role,
        task_inputs.task_skills(catalog, entry, role, None, route), chosen)
    return required | hashed


class OwnerGatesTaskInputTests(unittest.TestCase):
    # (entry, role): whether the task binds the gate instructions at two_fixed_gates.
    # Every role of every entry that runs one of the switch's owning flows binds
    # them, planning, Operation and Governance included; no other task does.
    OWNING = sorted({(entry, role)
                     for entry, route in task_inputs.catalog()["entries"].items()
                     if set(route["flows"]) & set(json.loads((TEAM / REGISTRY).read_text(
                         encoding="utf-8"))["switches"]["owner_gates"]["flows"])
                     for role in route["roles"]})
    TASKS = {**dict.fromkeys(OWNING, True),
             ("setup", "delivery-coordinator"): False, ("backlog-plan", "product-owner"): False,
             ("requirement", "business-analyst"): False,
             ("business-analysis", "business-analyst"): False,
             ("solution-design", "solution-architect"): False,
             ("experience-design", "ux-designer"): False}

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.docs = self.root / "workspace" / "docs"

    def bound(self, entry: str, role: str, catalog: dict) -> set[str]:
        return bound_instructions(self.root, entry, role, catalog) & {REFERENCE, CLASSES}

    def test_every_owning_flow_task_binds_the_gates_and_only_at_two_fixed_gates(self):
        self.assertEqual({entry for entry, _role in self.OWNING},
                         {"configure", "deliver", "delivery-plan", "execution-plan"})
        self.assertEqual(len(self.OWNING), 16)
        catalog = task_inputs.catalog()
        for state in ("no policy", "per_step", "two_fixed_gates"):
            if state != "no policy":
                choose(self.docs, state)
            for (entry, role), binds in self.TASKS.items():
                with self.subTest(state=state, entry=entry, role=role):
                    wanted = {REFERENCE, CLASSES} if binds and state == "two_fixed_gates" else set()
                    self.assertEqual(self.bound(entry, role, catalog), wanted)

    @integration
    def test_a_derived_task_manifest_binds_the_gates_from_the_committed_project(self):
        """The one Git-backed derivation: manifest reads the same policy through the project root."""
        init_repository(self.root)
        (self.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        for args in (("add", "-A"), ("commit", "-qm", "Fixture")):
            subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                            *args], check=True, capture_output=True)
        choose(self.docs, "two_fixed_gates")
        for (entry, role), wanted in ((("deliver", "delivery-coordinator"), {REFERENCE, CLASSES}),
                                      (("setup", "delivery-coordinator"), set())):
            with self.subTest(entry=entry, role=role):
                result = task_inputs.manifest(entry=entry, role=role, mode="review", project=self.root)
                paths = set(result["required_reads"]) | {item["path"] for item in result["instructions"]}
                self.assertEqual(paths & {REFERENCE, CLASSES}, wanted)


@integration
class DecisionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, self.temporary)
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace" / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        make_approved_backlog(self.docs)
        # Execution approval needs a committed workflow that the Delivery PR runs.
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("on:\n  pull_request:\n", encoding="utf-8")
        init_repository(self.root, initial_branch="main")
        for args in (("add", "--all"), ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                           env={**os.environ, **GIT_IDENTITY})
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(quiet(delivery_compile.init_dod, dod)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_dod, dod)[0], 0)
        self.plan = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.review = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001",
                                        "reviewed_commit": "1" * 40, "reviewed_integration_commit": "2" * 40})

    def init(self) -> Path:
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(quiet(delivery_compile.init_delivery, init)[0], 0)
        return delivery_compile.find_delivery(self.docs, "DLV-001") / "delivery.md"

    def decisions(self, path: Path, table: str) -> None:
        props, body = delivery_compile.split_note(path)
        body = delivery_compile.replace_section(body, "User Decisions", table)
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))

    def findings(self) -> list[str]:
        return [error for error in delivery_compile.delivery_findings(self.docs, "DLV-001")[1]
                if "User Decisions" in error]

    def contract(self, kind: str = "verification"):
        return type("Args", (), {"docs": str(self.docs), "kind": kind, "constrained_by": [DECISION]})

    def plan_the_item(self) -> None:
        """Approve the Verification Contract and give the Item the claims execution approval needs."""
        path = operation_compile.contract_path(self.docs, "verification")
        quiet(operation_compile.init, self.contract())
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(quiet(operation_compile.approve, self.contract())[0], 0)
        item = delivery_compile.find_delivery(self.docs, "DLV-001") / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props.update(path_claims=["src/auth.py"], contract_claims=["auth:session"])
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

    def refused(self, call, args) -> list[str]:
        code, output = quiet(call, args)
        self.assertEqual(code, 1, output)
        return json.loads(output)["errors"]

    def test_per_step_keeps_the_free_text_log_unchecked(self):
        path = self.init()
        self.assertIn("Local scope proposal; awaiting scope approval.",
                      delivery_compile.section_bodies(delivery_compile.split_note(path)[1])["User Decisions"])
        self.decisions(path, "| id | question |\n|---|---|\n| 1 | ? |")
        self.assertEqual(self.findings(), [])
        choose(self.docs, "per_step")
        self.assertEqual(self.findings(), [])

    def test_init_writes_the_table_and_check_validates_every_row(self):
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        section = delivery_compile.section_bodies(delivery_compile.split_note(path)[1])["User Decisions"]
        self.assertEqual(section + "\n", HEADER)
        self.assertEqual(self.findings(), [])
        self.decisions(path, HEADER + ROW)
        self.assertEqual(self.findings(), [])
        self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 0)
        self.decisions(path, HEADER + ROW + PENDING.replace("D-01", "D-02").replace("| 12 |", "|  |"))
        self.assertEqual(self.findings(), [])
        cases = (
            (ROW + ROW, "row 2 repeats id D-01; every id is unique"),
            (ROW.replace("D-01", "D-1"), "row 1 id must be D- and at least two digits"),
            (ROW.replace("| queued |", "| convenience |"), "row 1 class must be queued or an"
             " at-once class: architect_escalation, credentials_spending_or_irreversible_action,"
             " rule_exception, scope_or_grant_change"),
            (ROW.replace("One root per checkout; one root per Item", "One root per checkout"),
             "row 1 needs at least two options separated by semicolons"),
            (ROW.replace("| One root per checkout | answered", "| A shared root | answered"),
             "row 1 recommendation must be one of its options"),
            (ROW.replace("| answered |", "| deferred |"), "row 1 status must be pending or answered"),
            (ROW.replace(f"| {ANSWER} |", "|  |"), "row 1 is answered but records no answer"),
            (ROW.replace("| answered |", "| pending |"), "row 1 is pending but records an answer"),
            (ROW.replace("| 12 |", "| about 12 |"), "row 1 wait_minutes must be a whole number"),
            (ROW.replace("| Which cache root do the runs share? |", "|  |"), "row 1 states no question"),
            # A row names the Items that wait for it, so the verbs that start them can hold them.
            (ROW.replace("| AUTH-01 |", "| Verification Contract revision 6 |"),
             "row 1 blocks must name Items of this Delivery by Story id, separated by semicolons, not"
             " Verification Contract revision 6"),
            (ROW.replace("| AUTH-01 |", "| AUTH-01; AUTH-09 |"),
             "row 1 blocks must name Items of this Delivery by Story id, separated by semicolons, not AUTH-09"),
            (ROW.replace("| 12 |", "|  |"),
             "row 1 is answered after AUTH-01 waited for it, so it records that wait in wait_minutes"),
        )
        for rows, fragment in cases:
            with self.subTest(fragment=fragment):
                self.decisions(path, HEADER + rows)
                findings = self.findings()
                self.assertTrue(any(fragment in finding for finding in findings), findings)
                self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 1)
        self.decisions(path, "Rulings are listed below.\n\n- D-01: one root per checkout.")
        self.assertIn("delivery.md User Decisions must be a Markdown table", self.findings())

    def test_the_delivery_records_the_value_through_its_pin(self):
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        self.decisions(path, HEADER + ROW)
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        props, _body = delivery_compile.split_note(path)
        self.assertEqual(props["process_policy_source_hash"],
                         process_policy.approved_snapshot(self.docs)[0]["process_policy_source_hash"])
        self.assertEqual(delivery_compile.delivery_switch_value(self.docs, "DLV-001", SWITCH),
                         "two_fixed_gates")
        # A malformed log refuses the gate A writes that run the Delivery checks.
        self.decisions(path, HEADER + ROW + ROW)
        self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 1)

    def test_a_policy_changed_after_the_pin_keeps_the_log_checked(self):
        """At review, the gate B window, a policy set for the next Delivery drifts from the pin, and the
        pin is no longer compared there. The table the section holds is still checked (#329)."""
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        self.decisions(path, HEADER + ROW)
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_review, self.review)[0], 0)
        self.assertEqual(delivery_compile.split_note(path)[0]["status"], "review")
        answered_without_answer = ROW.replace(f"| {ANSWER} |", "|  |")
        for step in ("the pinned policy", "a policy for the next Delivery", "its draft revision"):
            with self.subTest(step=step):
                if step == "a policy for the next Delivery":
                    choose(self.docs, "per_step")
                elif step == "its draft revision":
                    policy(self.docs, "begin-revision")
                self.decisions(path, HEADER + answered_without_answer)
                self.assertEqual(self.findings(), ["delivery.md User Decisions row 1 is answered but records no answer"])
                self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 1)
                self.decisions(path, HEADER + ROW)
                self.assertEqual(self.findings(), [])

    def test_the_gates_ask_every_queued_question_before_their_writes(self):
        """Gate A and gate B each ask every queued question, so approve-scope and approve-review refuse
        while a row is pending and name it, and check-plan reports the pending rows (#329)."""
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        self.plan_the_item()
        self.decisions(path, HEADER + PENDING + PENDING.replace("D-01", "D-02"))
        before = path.read_bytes()
        checked = json.loads(quiet(delivery_compile.check_plan, type("Args", (), {
            "docs": str(self.docs), "delivery": "DLV-001", "reopen": [], "remote": "origin"}))[1])
        self.assertEqual((checked["ok"], checked["pending_decisions"]), (True, ["D-01", "D-02"]))
        self.assertEqual(self.refused(delivery_compile.approve_scope, self.plan), [
            "User Decisions rows D-01, D-02 are pending; gate A asks every queued question, so record the"
            " owner's answers before approve-scope"])
        self.assertEqual(path.read_bytes(), before)
        self.decisions(path, HEADER + ROW + PENDING.replace("D-01", "D-02"))
        self.assertEqual(self.refused(delivery_compile.approve_scope, self.plan), [
            "User Decisions row D-02 is pending; gate A asks every queued question, so record the owner's"
            " answers before approve-scope"])
        self.decisions(path, HEADER + ROW)
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_execution, self.plan)[0], 0)
        # A question queued between the gates holds gate B's approval of the Review.
        self.decisions(path, HEADER + ROW + PENDING.replace("D-01", "D-03"))
        before = path.read_bytes()
        self.assertEqual(self.refused(delivery_compile.approve_review, self.review), [
            "User Decisions row D-03 is pending; gate B asks every queued question, so record the owner's"
            " answers before approve-review"])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((path.parent / "delivery-review.md").exists())
        self.decisions(path, HEADER + ROW + ROW.replace("D-01", "D-03"))
        self.assertEqual(quiet(delivery_compile.approve_review, self.review)[0], 0)

    def test_per_step_asks_each_question_when_it_comes_up(self):
        """Without the decision log no gate write reads User Decisions."""
        choose(self.docs, "per_step")
        path = self.init()
        self.plan_the_item()
        self.decisions(path, "D-01 is still open: which cache root do the runs share?")
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_execution, self.plan)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_review, self.review)[0], 0)

    def test_no_approved_document_changes_between_the_gates_without_a_ruling(self):
        """Between gate A and gate B an Operation contract, the Delivery Governance and the execution plan
        change only once an answered row names them in its answer; gate A's own approvals need none (#329)."""
        governance = type("Args", (), {"docs": str(self.docs), "max_parallel": 1})
        quiet(delivery_governance.init, governance)
        self.assertEqual(quiet(delivery_governance.approve, governance)[0], 0)
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        self.plan_the_item()
        self.decisions(path, HEADER + ROW)
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_execution, self.plan)[0], 0)

        def between(document: str) -> str:
            return (f"DLV-001 is between its two owner gates, where {document} is approved only once an"
                    " answered User Decisions row names it in its answer; queue the question and record the"
                    f" owner's answer naming {document} first")

        contract = operation_compile.contract_path(self.docs, "verification")
        quiet(operation_compile.revise, self.contract())
        draft = contract.read_bytes()
        with self.assertRaises(ValueError) as refused:
            operation_compile.approve(self.contract())
        self.assertEqual(str(refused.exception), between("Verification Contract revision 2"))
        self.assertEqual(contract.read_bytes(), draft)
        # Naming another revision is no ruling on this one.
        self.decisions(path, HEADER + ROW + ruling("D-02", "Verification Contract revision 21"))
        with self.assertRaises(ValueError):
            operation_compile.approve(self.contract())
        self.decisions(path, HEADER + ROW + ruling("D-02", "Verification Contract revision 2"))
        self.assertEqual(quiet(operation_compile.approve, self.contract())[0], 0)

        governance_path = delivery_governance.path_for(self.docs)
        quiet(delivery_governance.begin_revision, governance)
        draft = governance_path.read_bytes()
        with self.assertRaises(ValueError) as refused:
            delivery_governance.approve(governance)
        self.assertEqual(str(refused.exception), between("Delivery Governance revision 2"))
        self.assertEqual(governance_path.read_bytes(), draft)
        self.decisions(path, HEADER + ROW + ruling("D-02", "Verification Contract revision 2")
                       + ruling("D-03", "Delivery Governance revision 2"))
        self.assertEqual(quiet(delivery_governance.approve, governance)[0], 0)

        # The Items bind the new contract only through a new execution approval, the second one.
        plan = delivery_compile.find_delivery(self.docs, "DLV-001") / "execution-plan.md"
        approved = plan.read_bytes()
        self.assertEqual(self.refused(delivery_compile.approve_execution, self.plan),
                         [between("execution plan approval 2")])
        self.assertEqual(plan.read_bytes(), approved)
        checked = json.loads(quiet(delivery_compile.check_plan, type("Args", (), {
            "docs": str(self.docs), "delivery": "DLV-001", "reopen": [], "remote": "origin"}))[1])
        self.assertEqual(checked["errors"], [between("execution plan approval 2")])
        self.decisions(path, HEADER + ROW + ruling("D-02", "Verification Contract revision 2")
                       + ruling("D-03", "Delivery Governance revision 2")
                       + ruling("D-04", "execution plan approval 2"))
        self.assertEqual(quiet(delivery_compile.approve_execution, self.plan)[0], 0)
        self.assertEqual(self.refused(delivery_compile.approve_execution, self.plan),
                         [between("execution plan approval 3")])

    def test_a_policy_with_an_unknown_value_is_refused(self):
        choose(self.docs, "two_fixed_gates")
        self.init()
        path = process_policy.path_for(self.docs)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "`two_fixed_gates`", "`one_fixed_gate`"), encoding="utf-8")
        code, output = quiet(delivery_compile.approve_scope, self.plan)
        self.assertEqual(code, 1)
        self.assertIn("switch 'owner_gates' has no value 'one_fixed_gate'", output)


if __name__ == "__main__":
    unittest.main()
