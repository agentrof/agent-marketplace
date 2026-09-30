"""Execution planning: switch `execution_planning` keeps every contract and the
Item topology on their own flows at `per_document` and, at
`single_source_bundle`, writes each execution-planning fact once where it is
owned and reviews the revised contracts with the Item topology in one bundle."""

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
import operation_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import validate  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "execution_planning"
REGISTRY = "skill-content/configure/data/process-switches.json"
OWNERSHIP = "skill-content/execution-plan/data/fact-ownership.json"
# The bundle reader judges through the execution_bundle lenses this data declares.
PANELS = "skill-content/challenge-review/data/review-panels.json"
PLAN = "skill-content/execution-plan/references/switch-execution_planning-single_source_bundle.md"
CONTRACTS = "skill-content/configure/references/switch-execution_planning-single_source_bundle.md"
ARCHITECT = "skill-content/software-architecture/references/switch-execution_planning-single_source_bundle.md"
PANEL = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
# flow: the switch references its anchor names.
ANCHORS = {"execution-planning": (PLAN, ARCHITECT), "operation": (CONTRACTS,),
           "delivery-execution": (ARCHITECT,)}
# (entry, role, mode, added skills): the switch files the task binds at single_source_bundle.
TASKS = {
    ("configure", "qa-engineer", "revise", ("challenge-review",)):
        {CONTRACTS, OWNERSHIP, PANELS},
    ("configure", "devops-engineer", "review", ("challenge-review",)):
        {CONTRACTS, OWNERSHIP, PANELS},
    ("configure", "delivery-coordinator", "revise", ()): {CONTRACTS, OWNERSHIP, PANELS},
    ("execution-plan", "software-architect", "create", ()): {PLAN, ARCHITECT, OWNERSHIP, PANELS},
    ("execution-plan", "delivery-coordinator", "create", ()): {PLAN, OWNERSHIP, PANELS},
    ("deliver", "software-architect", "create", ()): {ARCHITECT, OWNERSHIP, PANELS},
    ("deliver", "backend-developer", "create", ()): set(),
    ("backlog-plan", "product-owner", "revise", ()): set(),
}
SWITCH_FILES = {PLAN, CONTRACTS, ARCHITECT, OWNERSHIP, PANELS}
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


class ExecutionPlanningRegistryTests(unittest.TestCase):
    def test_switch_ships_at_per_document_under_the_issue_promotion_rule(self):
        switch = json.loads(read(REGISTRY))["switches"][SWITCH]
        self.assertEqual(switch["issue"], 324)
        self.assertEqual([value["id"] for value in switch["values"]],
                         ["per_document", "single_source_bundle"])
        self.assertEqual(switch["default"], "per_document")
        self.assertEqual(switch["flows"], sorted(ANCHORS))
        self.assertEqual(switch["value_data"], {"single_source_bundle": [OWNERSHIP, PANELS]})
        self.assertNotIn("agent_variants", switch)
        # #324 names its own unit, so the owner's plain 3-Delivery default is refined.
        self.assertEqual(switch["promotion"]["unit"],
                         "At least 3 Deliveries that revise at least one Operation contract or"
                         " declare architecture impact, run with single_source_bundle.")
        for term in ("One review layer per plan", "at most 5 serial model passes",
                     "a restated share under 5%", "at most 60% of the 342 min measured baseline",
                     "zero escaped valid critical or major findings", "the owner's approval"):
            with self.subTest(term=term):
                self.assertIn(term, switch["promotion"]["threshold"])
        for term in ("execution-planning wall time from plan start to publication",
                     "serial model passes", "review layers and review-loop minutes",
                     "distinct 8-word sequences", "owner rulings recorded once with an id",
                     "plan revisions caused by contract errors", "DELIVERY_OPERATION_UNCARRIED"):
            with self.subTest(term=term):
                self.assertIn(term, switch["metric"])

    def test_the_bundle_panel_joins_the_review_panels_switch(self):
        switches = json.loads(read(REGISTRY))["switches"]
        self.assertIn("execution-planning", switches["review_panels"]["flows"])
        step = json.loads(read("skill-content/challenge-review/data/review-panels.json"))[
            "review_steps"]["execution_bundle"]
        self.assertEqual(step["reader_role"], "devops-engineer")
        self.assertEqual([lens["id"] for lens in step["lenses"]],
                         ["command-safety", "boundary-fit", "criteria-to-contract-coverage",
                          "topology-and-claims", "single-source"])


class FactOwnershipTests(unittest.TestCase):
    def test_every_fact_class_has_the_one_owner_the_issue_names(self):
        data = json.loads(read(OWNERSHIP))
        owners = {name: (data["documents"][spec["owner"]["document"]]["type"],
                         spec["owner"]["section"], spec["owner"]["writer"])
                  for name, spec in data["fact_classes"].items()}
        self.assertEqual(owners, {
            "verification_semantics": ("verification_contract", "Contract", "qa_engineer"),
            "runtime_topology": ("environment_contract", "Contract", "devops_engineer"),
            "structural_decisions": ("decision", "Decision", "software_architect"),
            "item_topology": ("delivery_item", "front matter", "software_architect"),
            "owner_rulings": ("delivery", "User Decisions", "delivery_coordinator"),
        })
        for term in ("Item's context", "test suites", "evidence and coverage rules",
                     "cache locations", "diagnostic test adapter"):
            self.assertIn(term, data["fact_classes"]["verification_semantics"]["facts"])

    def test_the_contract_writers_stay_the_ones_the_operation_flow_names(self):
        text = flat("flows/operation.md")
        self.assertIn("`qa-engineer` is the only Verification Contract writer; `devops-engineer`"
                      " is the only Environment Contract writer.", text)


class FactOwnershipValidatorTests(unittest.TestCase):
    """tools/validate.py rejects a fact class without exactly one named owner."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        fixtures.make_valid_root(cls.root)
        cls.path = cls.root / "plugins/software-engineering-team" / OWNERSHIP
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
            validate.check_fact_ownership(validate.build_tree(self.root), findings)
            return [finding.message for finding in findings]
        finally:
            self.path.write_text(self.original, encoding="utf-8")

    def test_shipped_data_is_clean(self):
        self.assertEqual(self.messages(lambda data: None), [])

    def test_no_owner_two_owners_unknown_or_unnamed_writer_are_rejected(self):
        def owner(name, **changes):
            return lambda data: data["fact_classes"][name]["owner"].update(changes)

        cases = (
            (lambda data: data["fact_classes"]["runtime_topology"].pop("owner"),
             "fact class 'runtime_topology': has no owner"),
            (lambda data: data["fact_classes"]["runtime_topology"].update(owner={}),
             "fact class 'runtime_topology': has no owner"),
            (lambda data: data["fact_classes"]["runtime_topology"]["owner"].pop("section"),
             "fact class 'runtime_topology': has no owner"),
            (lambda data: data["fact_classes"]["runtime_topology"].update(owner=[
                data["fact_classes"]["runtime_topology"]["owner"],
                data["fact_classes"]["verification_semantics"]["owner"]]),
             "fact class 'runtime_topology': has two owners"),
            (owner("runtime_topology", writer=["devops_engineer", "qa_engineer"]),
             "fact class 'runtime_topology': has two owners"),
            (owner("runtime_topology", writer="release_manager"),
             "fact class 'runtime_topology': names unknown writer role 'release_manager'"),
            (owner("runtime_topology", writer="qa_engineer"),
             "fact class 'runtime_topology': flow 'operation' does not name `qa-engineer` as the"
             " Environment Contract writer"),
            (owner("owner_rulings", writer="software_architect"),
             "flow 'execution-planning' does not name `software-architect` as the User Decisions"
             " writer"),
            (owner("item_topology", document="ghost"),
             "fact class 'item_topology': names undeclared document 'ghost'"),
            (lambda data: data["documents"]["item_topology"].update(type="ghost_type"),
             "document 'item_topology': unknown vault document type 'ghost_type'"),
            (lambda data: data.update(fact_classes={}),
             "fact_classes must declare at least one fact class"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                messages = self.messages(mutate)
                self.assertTrue(any(fragment in message for message in messages), messages)

    def test_duplicate_fact_class_is_rejected(self):
        duplicated = self.original.replace('"fact_classes": {', '"fact_classes": {\n'
                                           '    "owner_rulings": {"facts": "x"},', 1)
        self.path.write_text(duplicated, encoding="utf-8")
        try:
            findings: list = []
            validate.check_fact_ownership(validate.build_tree(self.root), findings)
        finally:
            self.path.write_text(self.original, encoding="utf-8")
        self.assertTrue(any("duplicate keys ['owner_rulings']" in finding.message
                            for finding in findings), findings)


class ExecutionPlanningReferenceTests(unittest.TestCase):
    def test_references_apply_only_at_single_source_bundle_and_are_never_linked(self):
        for reference in (PLAN, CONTRACTS, ARCHITECT):
            with self.subTest(reference=reference):
                text = flat(reference)
                self.assertIn("process switch `execution_planning` at `single_source_bundle`", text)
                self.assertIn("A task binds this file only when the project's Process Policy"
                              " selects that value", text)
                self.assertIn("`per_document`", text)
        for skill in TEAM.glob("skill-content/*/SKILL.md"):
            self.assertNotIn("switch-execution_planning", skill.read_text(encoding="utf-8"))
            self.assertNotIn("fact-ownership", skill.read_text(encoding="utf-8"))

    def test_every_owning_flow_anchors_the_switch_and_names_its_references(self):
        for flow, references in ANCHORS.items():
            text = flat(f"flows/{flow}.md")
            with self.subTest(flow=flow):
                self.assertIn("Switch `execution_planning`: at `single_source_bundle`", text)
                for reference in references:
                    self.assertIn(reference, text)
        text = flat("flows/execution-planning.md")
        self.assertIn("review panel `execution_bundle`", text)
        self.assertIn("Switch `review_panels`: at `lens_panel`", text)
        self.assertIn(PANEL, text)

    def test_the_four_deliberate_rules_stay(self):
        plan = flat(PLAN)
        for rule in ("the QA Engineer writes the Verification Contract and the DevOps Engineer the"
                     " Environment Contract",
                     "An architecture record exists only inside an active Item",
                     "Publication carries only the contracts an Item pins",
                     "A non-runtime Item never binds the Environment Contract"):
            with self.subTest(rule=rule):
                self.assertIn(rule, plan)
        # The compilers keep them: architecture belongs to the active Item and a
        # non-runtime Item cannot bind the Environment Contract.
        self.assertIn("A record is legal only while an active Delivery Item owns its delta",
                      " ".join(read("scripts/architecture_compile.py").split()))
        self.assertIn("non-runtime Item must not bind an Environment Contract",
                      read("scripts/delivery_compile.py"))

    def test_the_bundle_steps_follow_the_issue(self):
        plan = flat(PLAN)
        for rule in (
            "creates no planning document",
            ".agentrof/agent-marketplace/.runtime/<dlv-id>/execution-handoff.md",
            "--input <handoff>",
            "delivery_compile.py bundle-manifest --delivery DLV-###",
            "Start every reader together on the same manifest",
            "No separate counterpart review runs for a contract inside the bundle",
            "--expected-hash <source_hash>",
            "A restatement of a fact outside its owning section is a finding that names the"
            " owning section; one that contradicts the owner is critical",
            "The bundle gets one verdict",
            "`operation_compile.py check --kind <kind> --json` for every revised contract and"
            " `delivery_compile.py check --delivery DLV-###` are green",
            "run `delivery_git.py refresh-target` before `publish-execution-plan`, in the same step",
            "The bundle loop follows switch `review_loop`",
            "Record each owner ruling once, in the Delivery's `User Decisions` section",
            "cites the id and never restates the ruling",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, plan)

    def test_the_architect_links_owning_sections_and_its_role_file_is_unchanged(self):
        architect = flat(ARCHITECT)
        for rule in ("no vault output for its definitions",
                     "A decision record carries the decision, the alternatives weighed and the"
                     " rationale",
                     "link the owning contract section instead of restating it"):
            with self.subTest(rule=rule):
                self.assertIn(rule, architect)
        role = read("agents/software-architect.md")
        for term in ("execution_planning", "single_source_bundle", "fact-ownership"):
            self.assertNotIn(term, role)
        self.assertIn("Escalates and halts, never guesses", role)

    def test_the_panel_keeps_each_revised_contract_with_its_counterpart(self):
        panel = flat(PANEL)
        self.assertIn("run once for each contract the bundle revises, as that contract's"
                      " counterpart", panel)
        self.assertIn("`qa-engineer` for the Environment Contract", panel)

    def test_host_contracts_start_every_bundle_reader_together(self):
        for host, rule in (("claude", "spawn them in one message"),
                           ("codex", "start every reader of an execution-plan bundle before"
                                     " waiting on any of them")):
            text = " ".join((ROOT / "platforms" / host / "software-engineering-team"
                             / "host-contract.md").read_text(encoding="utf-8").split())
            with self.subTest(host=host):
                self.assertIn("Under switch `execution_planning` at `single_source_bundle`", text)
                self.assertIn(rule, text)

    def test_docs_describe_both_values(self):
        for doc in ("docs/orchestration.md", "docs/requirement-delivery-protocol.md"):
            text = " ".join((ROOT / doc).read_text(encoding="utf-8").split())
            with self.subTest(doc=doc):
                self.assertIn("process switch `execution_planning`", text.replace("Process", "process"))
                self.assertIn("`per_document`", text)
                self.assertIn("`single_source_bundle`", text)
                self.assertIn("fact-ownership.json", text)


class ExecutionPlanningTaskInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        init_repository(self.root)
        (self.root / ".gitignore").write_text(".agentrof/\n", encoding="utf-8")
        (self.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        for args in (("add", "-A"), ("commit", "-qm", "Fixture")):
            subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                            *args], check=True, capture_output=True)
        self.docs = self.root / "workspace" / "docs"

    def bound(self, task) -> tuple[set, set, dict]:
        entry, role, mode, skills = task
        result = task_inputs.manifest(entry=entry, role=role, mode=mode, project=self.root,
                                      skills=list(skills))
        instructions = {item["path"] for item in result["instructions"]}
        return set(result["required_reads"]) & SWITCH_FILES, instructions & SWITCH_FILES, result

    def test_tasks_bind_the_switch_files_only_at_single_source_bundle(self):
        for state in ("no policy", "per_document", "single_source_bundle"):
            if state != "no policy":
                choose(self.docs, state)
            for task, expected in TASKS.items():
                with self.subTest(state=state, task=task[:3]):
                    reads, instructions, _result = self.bound(task)
                    wanted = expected if state == "single_source_bundle" else set()
                    self.assertEqual(reads, wanted)
                    self.assertEqual(instructions, wanted)

    def test_the_architect_declares_no_vault_output_and_writers_bind_its_handoff(self):
        choose(self.docs, "single_source_bundle")
        _reads, _instructions, architect = self.bound(
            ("execution-plan", "software-architect", "create", ()))
        self.assertEqual(architect["write_scope"]["allowed_write_area"], [])
        handoff = self.root / ".agentrof/agent-marketplace/.runtime/dlv-001/execution-handoff.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("# Handoff\n\nverification_semantics: ...\n", encoding="utf-8")
        relative = handoff.relative_to(self.root).as_posix()
        for writer in ("qa-engineer", "devops-engineer"):
            with self.subTest(writer=writer):
                result = task_inputs.manifest(entry="configure", role=writer, mode="revise",
                                              project=self.root, skills=["challenge-review"],
                                              inputs=[relative])
                self.assertIn(relative, [record["path"] for record in result["project_inputs"]])
                self.assertIn(OWNERSHIP, result["required_reads"])

    def test_a_policy_with_an_unknown_value_is_refused(self):
        choose(self.docs, "single_source_bundle")
        path = process_policy.path_for(self.docs)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "`single_source_bundle`", "`single_source_everything`"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "has no value 'single_source_everything'"):
            self.bound(("configure", "qa-engineer", "revise", ("challenge-review",)))


class BundleManifestTests(unittest.TestCase):
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
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "tests.yml").write_text("on:\n  pull_request:\njobs:\n  test:\n"
                                             "    runs-on: ubuntu-latest\n", encoding="utf-8")
        init_repository(self.root, initial_branch="main")
        for args in (("add", "--all"), ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                           env={**os.environ, **GIT_IDENTITY})
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(quiet(delivery_compile.init_dod, dod)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_dod, dod)[0], 0)
        self.contract = type("Args", (), {"docs": str(self.docs), "kind": "verification",
                                          "constrained_by": ["solution-design/decisions/fixture-api"]})
        self.assertEqual(quiet(operation_compile.init, self.contract)[0], 0)
        path = operation_compile.contract_path(self.docs, "verification")
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(quiet(operation_compile.approve, self.contract)[0], 0)

    def scope(self) -> None:
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(quiet(delivery_compile.init_delivery, init)[0], 0)
        plan = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(quiet(delivery_compile.approve_scope, plan)[0], 0)

    def run_bundle(self, expected_hash=None) -> tuple[int, dict]:
        code, output = quiet(delivery_compile.main, [
            "--docs", str(self.docs), "bundle-manifest", "--delivery", "DLV-001",
            *(["--expected-hash", expected_hash] if expected_hash else [])])
        return code, json.loads(output)

    def item(self, **changes) -> Path:
        item = delivery_compile.find_delivery(self.docs, "DLV-001") / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(item)
        props.update(changes)
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        return item

    def test_only_single_source_bundle_has_a_bundle(self):
        self.scope()
        code, result = self.run_bundle()
        self.assertEqual(code, 1)
        self.assertIn("DLV-001 runs switch execution_planning at per_document", result["errors"][0])
        # A policy that keeps the value agrees with the missing pin; one that
        # selects the bundle later is drift until the plan pins it again.
        choose(self.docs, "per_document")
        self.assertIn("DLV-001 runs switch execution_planning at per_document",
                      self.run_bundle()[1]["errors"][0])
        choose(self.docs, "single_source_bundle")
        self.assertIn("Delivery runs switch execution_planning at per_document under no Process"
                      " Policy, as it pinned none, but the approved revision 2 sets"
                      " single_source_bundle", self.run_bundle()[1]["errors"][0])

    def test_a_policy_with_an_unknown_value_refuses_the_bundle(self):
        choose(self.docs, "single_source_bundle")
        self.scope()
        path = process_policy.path_for(self.docs)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "`single_source_bundle`", "`single_source_everything`"), encoding="utf-8")
        code, result = self.run_bundle()
        self.assertEqual(code, 1)
        self.assertIn("has no value 'single_source_everything'", result["errors"][0])

    def test_the_manifest_lists_the_bundle_and_names_each_counterpart(self):
        choose(self.docs, "single_source_bundle")
        self.scope()
        self.item(path_claims=["src/auth.py"], contract_claims=["auth:session"])
        code, result = self.run_bundle()
        self.assertEqual(code, 0, result)
        self.assertEqual([(contract["kind"], contract["revised"], contract["pinned"])
                          for contract in result["contracts"]], [("verification", False, True)])
        # A plan that revises no contract has no bundle review.
        self.assertEqual(result["readers"], [])
        self.assertEqual([record["story"] for record in result["items"]], ["AUTH-01"])
        self.assertEqual([record["path"].rsplit("/", 1)[1] for record in result["sources"]],
                         ["story.md", "test-plan.md"])
        self.assertEqual([record["path"] for record in result["data"]], [OWNERSHIP, PANELS])
        self.assertTrue(all(record["sha256"].startswith("sha256:") for record in
                            (*result["contracts"], *result["items"], *result["sources"],
                             *result["data"])))
        self.assertEqual(result["inputs"], sorted(
            f"workspace/docs/{record['path']}"
            for record in (*result["contracts"], *result["items"], *result["sources"])))

        self.assertEqual(quiet(operation_compile.revise, self.contract)[0], 0)
        environment = type("Args", (), {"docs": str(self.docs), "kind": "environment",
                                        "constrained_by": ["solution-design/decisions/fixture-api"]})
        self.assertEqual(quiet(operation_compile.init, environment)[0], 0)
        result = self.run_bundle()[1]
        self.assertEqual([(contract["kind"], contract["revised"], contract["pinned"],
                           contract["counterpart"]) for contract in result["contracts"]],
                         [("verification", True, True, "devops_engineer"),
                          ("environment", True, False, "qa_engineer")])
        self.assertEqual(result["readers"], ["devops_engineer", "qa_engineer"])
        self.assertEqual(result["unpinned_revisions"], ["operation/environment-contract.md"])
        self.item(runtime_required=True)
        self.assertEqual(self.run_bundle()[1]["unpinned_revisions"], [])

    def test_a_changed_input_makes_the_manifest_stale(self):
        choose(self.docs, "single_source_bundle")
        self.scope()
        first = self.run_bundle()[1]
        self.assertEqual(self.run_bundle(first["source_hash"])[0], 0)
        self.item(path_claims=["src/auth.py"])
        code, result = self.run_bundle(first["source_hash"])
        self.assertEqual(code, 1)
        self.assertIn("bundle manifest is stale", result["errors"][0])

    def test_the_bundle_belongs_to_execution_planning(self):
        choose(self.docs, "single_source_bundle")
        self.scope()
        props, body = delivery_compile.split_note(
            delivery_compile.find_delivery(self.docs, "DLV-001") / "delivery.md")
        # The Delivery names the value it ran under through its pinned policy.
        self.assertEqual(props["process_policy_source_hash"],
                         process_policy.approved_snapshot(self.docs)[0]["process_policy_source_hash"])
        self.assertEqual(delivery_compile.delivery_switch_value(self.docs, "DLV-001", SWITCH),
                         "single_source_bundle")
        props["status"] = "active"
        delivery_compile.atomic_text(
            delivery_compile.find_delivery(self.docs, "DLV-001") / "delivery.md",
            delivery_compile.frontmatter(props, body))
        code, result = self.run_bundle()
        self.assertEqual(code, 1)
        self.assertIn("DLV-001 is active; its bundle is reviewed during execution planning",
                      result["errors"][0])


if __name__ == "__main__":
    unittest.main()
