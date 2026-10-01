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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import delivery_compile  # noqa: E402
import fixtures  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "owner_gates"
REGISTRY = "skill-content/configure/data/process-switches.json"
CLASSES = "skill-content/deliver/data/owner-decision-classes.json"
REFERENCE = "skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md"
FLOWS = ("delivery-execution", "delivery-governance", "delivery-planning",
         "execution-planning", "operation")
HEADER = ("| id | class | question | options | recommendation | status | answer | blocks |"
          " wait_minutes |\n|---|---|---|---|---|---|---|---|---|\n")
ROW = ("| D-01 | queued | Which cache root do the runs share? | One root per checkout; one root"
       " per Item | One root per checkout | answered | One root per checkout. | VC revision 6 | 12 |\n")
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


class OwnerGatesRegistryTests(unittest.TestCase):
    def test_switch_ships_at_per_step_under_the_owner_promotion_rule(self):
        switch = json.loads(read(REGISTRY))["switches"][SWITCH]
        self.assertEqual(switch["issue"], 329)
        self.assertEqual([value["id"] for value in switch["values"]], ["per_step", "two_fixed_gates"])
        self.assertEqual(switch["default"], "per_step")
        self.assertEqual(switch["flows"], list(FLOWS))
        self.assertEqual(switch["value_data"], {"two_fixed_gates": [CLASSES]})
        # The owner's rule of 30 Sep 2026: at least 3 Deliveries, as #329 states no other unit.
        self.assertEqual(switch["promotion"]["unit"], "At least 3 Deliveries run with two_fixed_gates.")
        for term in ("At most 1 stop outside the two gates per Delivery, and only for an at-once"
                     " class", "at most 15 min per Delivery",
                     "zero dependent tasks run before their question was answered",
                     "the owner's approval"):
            with self.subTest(term=term):
                self.assertIn(term, switch["promotion"]["threshold"])
        for term in ("owner stops outside gates A and B", "critical-path owner wait",
                     "time from queueing to answer", "minutes dependent tasks waited",
                     "early gates forced", "idle hours that began with an open question",
                     "status questions from the owner",
                     "every User Decisions row is answered before the work that depends on it"):
            with self.subTest(term=term):
                self.assertIn(term, switch["metric"])


class OwnerDecisionClassTests(unittest.TestCase):
    def test_the_at_once_classes_are_the_issue_classes_beside_the_architect_clause(self):
        data = json.loads(read(CLASSES))
        self.assertEqual([entry["id"] for entry in data["classes"]],
                         ["rule_exception", "scope_or_grant_change",
                          "credentials_spending_or_irreversible_action"])
        self.assertTrue(all(entry["description"].strip() for entry in data["classes"]))
        [clause] = data["agent_clauses"]
        self.assertEqual((clause["id"], clause["agent"]), ("architect_escalation", "software-architect"))
        # The clause is kept as the agent file states it, under both values.
        role = " ".join(read("agents/software-architect.md").split())
        self.assertIn(clause["clause"], role)
        for term in ("owner_gates", "two_fixed_gates", "gate A"):
            self.assertNotIn(term, role)


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

    def test_shipped_classes_are_clean(self):
        self.assertEqual(self.messages(lambda data: None), [])

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
    def test_reference_applies_only_at_two_fixed_gates_and_is_never_linked(self):
        text = flat(REFERENCE)
        self.assertIn("process switch `owner_gates` at `two_fixed_gates`", text)
        self.assertIn("A task binds this file only when the project's Process Policy selects that"
                      " value", text)
        self.assertIn("at the default, `per_step`,", text)
        for skill in TEAM.glob("skill-content/*/SKILL.md"):
            self.assertNotIn("switch-owner_gates", skill.read_text(encoding="utf-8"))

    def test_every_owning_flow_anchors_the_switch_and_names_the_reference(self):
        for flow in FLOWS:
            with self.subTest(flow=flow):
                text = flat(f"flows/{flow}.md")
                self.assertIn("Switch `owner_gates`: at `two_fixed_gates`", text)
                self.assertIn(REFERENCE, text)

    def test_the_gates_batch_questions_and_never_decide_by_default(self):
        text = flat(REFERENCE)
        for rule in (
            "The switch changes when a question is asked, never who decides it",
            "Nothing is decided by default",
            "the run never proceeds on a guess",
            "present gate A as one choice gate: the Delivery scope, the execution plan with its"
            " topology, claims, role sequence and schedules, every Operation revision and"
            " Governance change the plan needs, the decision log so far and every queued question",
            "Group the questions in calls of at most four, with the recommended option first and"
            " the tradeoffs in the option descriptions",
            "Gate A's approval is the go for Item start",
            "apply it with `delivery_git.py apply-governance` before any Item starts",
            "present gate B as one choice gate: the Delivery Review, its follow-ups, the decision"
            " log since gate A and the merge",
            "add a `pending` row to the Delivery's `User Decisions` table and continue",
            "a task that depends on it waits, and only that task waits",
            "When every remaining task depends on pending questions, ask the queued questions at"
            " once as an early gate",
            "Only the owner's answer closes a question",
            "No approved document changes between the gates unless an `answered` row names it",
            "Ask a decision of these classes at once and name its class in the question",
            "class `architect_escalation`, which stays exactly as that file states it",
            "never mark a row `answered` without the owner's answer",
            "record the owner's answers verbatim and the wait minutes",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_setup_configure_backlog_and_requirement_gates_are_unchanged(self):
        self.assertIn("Setup, `/configure` outside a Delivery's plan, backlog approval and"
                      " Requirement-flow gates keep their own gates", flat(REFERENCE))
        for relative in ("flows/requirement.md", "flows/backlog-planning.md",
                         "flows/business-analysis.md", "skill-content/setup/SKILL.md",
                         "skill-content/configure/SKILL.md", "skill-content/backlog-plan/SKILL.md",
                         "skill-content/requirement/SKILL.md"):
            with self.subTest(path=relative):
                self.assertNotIn("owner_gates", read(relative))

    def test_host_contracts_group_the_gate_questions(self):
        for host, surface in (("claude", "AskUserQuestion"), ("codex", "request_user_input")):
            text = " ".join((ROOT / "platforms" / host / "software-engineering-team"
                             / "host-contract.md").read_text(encoding="utf-8").split())
            with self.subTest(host=host):
                self.assertIn("Under switch `owner_gates` at `two_fixed_gates`, ask the owner inside"
                              " a Delivery only at gate A, gate B, an early gate or for an at-once"
                              " class", text)
                self.assertIn(f"through `{surface}` in calls of at most four questions, with the"
                              " recommended option first", text)

    def test_docs_describe_both_values(self):
        for doc in ("docs/orchestration.md", "docs/requirement-delivery-protocol.md"):
            text = " ".join((ROOT / doc).read_text(encoding="utf-8").split())
            with self.subTest(doc=doc):
                self.assertIn("process switch `owner_gates`", text.replace("Process", "process"))
                self.assertIn("`per_step`", text)
                self.assertIn("`two_fixed_gates`", text)
                self.assertIn("owner-decision-classes.json", text)


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
        init_repository(self.root)
        (self.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        for args in (("add", "-A"), ("commit", "-qm", "Fixture")):
            subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                            *args], check=True, capture_output=True)
        self.docs = self.root / "workspace" / "docs"

    def bound(self, entry: str, role: str) -> set[str]:
        result = task_inputs.manifest(entry=entry, role=role, mode="review", project=self.root)
        paths = set(result["required_reads"]) | {item["path"] for item in result["instructions"]}
        return paths & {REFERENCE, CLASSES}

    def test_every_owning_flow_task_binds_the_gates_and_only_at_two_fixed_gates(self):
        self.assertEqual({entry for entry, _role in self.OWNING},
                         {"configure", "deliver", "delivery-plan", "execution-plan"})
        self.assertEqual(len(self.OWNING), 16)
        for state in ("no policy", "per_step", "two_fixed_gates"):
            if state != "no policy":
                choose(self.docs, state)
            for (entry, role), binds in self.TASKS.items():
                with self.subTest(state=state, entry=entry, role=role):
                    wanted = {REFERENCE, CLASSES} if binds and state == "two_fixed_gates" else set()
                    self.assertEqual(self.bound(entry, role), wanted)


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
        init_repository(self.root, initial_branch="main")
        for args in (("add", "--all"), ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                           env={**os.environ, **GIT_IDENTITY})
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(quiet(delivery_compile.init_dod, dod)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_dod, dod)[0], 0)
        self.plan = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})

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
        pending = ROW.replace("| answered | One root per checkout. |", "| pending |  |")
        self.decisions(path, HEADER + ROW + pending.replace("D-01", "D-02"))
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
            (ROW.replace("| One root per checkout. |", "|  |"), "row 1 is answered but records no answer"),
            (ROW.replace("| answered |", "| pending |"), "row 1 is pending but records an answer"),
            (ROW.replace("| 12 |", "| about 12 |"), "row 1 wait_minutes must be a whole number"),
            (ROW.replace("| Which cache root do the runs share? |", "|  |"), "row 1 states no question"),
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

    def test_a_policy_changed_after_the_pin_leaves_the_log_unchecked(self):
        choose(self.docs, "two_fixed_gates")
        path = self.init()
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)
        props, body = delivery_compile.split_note(path)
        props["status"] = "active"
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))
        self.decisions(path, HEADER + ROW + ROW)
        self.assertTrue(self.findings())
        # A later policy for the next Delivery never strands this one on its log.
        choose(self.docs, "per_step")
        self.assertEqual(self.findings(), [])
        policy(self.docs, "begin-revision")
        self.assertEqual(self.findings(), [])

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
