"""A Delivery runs under the process switch values it pinned.

Until its Review a new execution approval can re-pin it, so an approved policy
that changes a value the Delivery reads is refused there, and a planning task
binds the approved policy only where the next execution approval pins it. From
the Review on, the pinned revision's own values are read back, from Git when
the current policy moved on.
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import architecture_compile  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_governance  # noqa: E402
import operation_compile  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

DELIVERY = "DLV-001"
ITEM = "workspace/docs/delivery/deliveries/dlv-001-auth/items/auth-01/item.md"
CODE_REVIEW_LOOP = "skill-content/code-review/references/switch-review_loop-blocking_delta.md"
PLAN_LANES = "skill-content/execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md"
DELIVER_LANES = "skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
WORKFLOW = ("on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: make test\n")
LANE_COMPONENTS = {"api": {"sourcing": "build", "code_path": "workspace/apps/api"}}
LANES = ["backend_developer:workspace/apps/api", "devops_engineer:deploy",
         "frontend_developer:workspace/apps/web"]


def quiet(call, *args) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


class DeliveryPolicyPinTests(unittest.TestCase):
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
        (self.project / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        governance = type("Args", (), {"docs": str(self.docs), "max_parallel": 1})
        self.assertEqual(quiet(delivery_governance.init, governance)[0], 0)
        self.assertEqual(quiet(delivery_governance.approve, governance)[0], 0)
        make_approved_backlog(self.docs)
        workflow = self.project / ".github/workflows/tests.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(WORKFLOW, encoding="utf-8")
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(quiet(delivery_compile.init_dod, dod)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_dod, dod)[0], 0)
        contract = type("Args", (), {"docs": str(self.docs), "kind": "verification",
                                     "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"]})
        self.assertEqual(quiet(operation_compile.init, contract)[0], 0)
        path = operation_compile.contract_path(self.docs, "verification")
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(quiet(operation_compile.approve, contract)[0], 0)
        self.commit("Approve the project sources")
        remote = self.project / "remote.git"
        init_repository(remote, bare=True)
        with (self.project / ".git/info/exclude").open("a", encoding="utf-8") as exclude:
            exclude.write("remote.git/\n")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                       check=True)
        self.plan = type("Args", (), {"docs": str(self.docs), "delivery": DELIVERY})

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.project), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def policy(self, *argv: str) -> dict:
        code, output = quiet(process_policy.main, [argv[0], "--docs", str(self.docs), *argv[1:]])
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def revise_policy(self, **values: str) -> dict:
        self.policy("begin-revision" if process_policy.path_for(self.docs).exists() else "init")
        for switch, value in values.items():
            if value == "default":
                self.policy("set", "--switch", switch, "--default")
            else:
                self.policy("set", "--switch", switch, "--value", value)
        self.policy("approve")
        return process_policy.approved_snapshot(self.docs)[0]

    def value(self, switch: str) -> tuple[int, dict]:
        code, output = quiet(process_policy.main, ["value", "--docs", str(self.docs), "--switch",
                                                   switch, "--delivery", DELIVERY])
        return code, json.loads(output)

    def findings(self) -> list[str]:
        return delivery_compile.delivery_findings(self.docs, DELIVERY)[1]

    def delivery_note(self) -> Path:
        return delivery_compile.find_delivery(self.docs, DELIVERY) / "delivery.md"

    def set_status(self, path: Path, status: str) -> None:
        props, body = delivery_compile.split_note(path)
        props["status"] = status
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))

    def propose_and_approve_scope(self) -> None:
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(quiet(delivery_compile.init_delivery, init)[0], 0)
        self.assertEqual(quiet(delivery_compile.approve_scope, self.plan)[0], 0)

    def approve_execution(self, **item: object) -> dict:
        path = self.project / ITEM
        props, body = delivery_compile.split_note(path)
        props.update({"path_claims": ["src/auth.py"], "contract_claims": ["auth:session"], **item})
        delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))
        code, output = quiet(delivery_compile.approve_execution, self.plan)
        result = json.loads(output)
        self.assertEqual(code, 0, result)
        return result

    def task(self, entry: str, role: str, mode: str = "create", **kwargs) -> set[str]:
        return set(task_inputs.manifest(entry=entry, role=role, mode=mode, project=self.project,
                                        **kwargs)["required_reads"])

    def test_a_delivery_past_its_review_reads_the_revision_it_pinned(self):
        self.revise_policy(review_loop="blocking_delta")
        pinned_commit = self.commit("Approve Process Policy revision 1")
        self.propose_and_approve_scope()
        self.approve_execution()
        self.set_status(self.delivery_note(), "review")
        self.commit("Review DLV-001")
        # The owner sets the policy back for the next Delivery.
        self.revise_policy(review_loop="default")
        self.commit("Approve Process Policy revision 2")
        code, result = self.value("review_loop")
        self.assertEqual(code, 0, result)
        self.assertEqual((result["value"], result["policy"]["process_policy_revision"]),
                         ("blocking_delta", 1))
        self.assertEqual(result.get("pinned_revision_commit"), pinned_commit)
        self.assertEqual(delivery_compile.delivery_switch_value(self.docs, DELIVERY, "review_loop"),
                         "blocking_delta")
        self.assertIn(CODE_REVIEW_LOOP, self.task("deliver", "code-reviewer", "review",
                                                  delivery=DELIVERY))
        # Outside the Delivery the approved policy is in force.
        self.assertNotIn(CODE_REVIEW_LOOP, self.task("deliver", "code-reviewer", "review"))
        # An Item reopened after the Review runs under the same pin.
        self.set_status(self.project / ITEM, "active")
        self.assertIn(CODE_REVIEW_LOOP, self.task("deliver", "code-reviewer", "review",
                                                  inputs=[ITEM]))
        # A pin that neither the policy nor its Git history holds is refused.
        props, body = delivery_compile.split_note(self.delivery_note())
        props["process_policy_source_hash"] = "sha256:" + "0" * 64
        delivery_compile.atomic_text(self.delivery_note(), delivery_compile.frontmatter(props, body))
        code, result = self.value("review_loop")
        self.assertEqual(code, 1)
        self.assertIn("is neither the current policy nor an approved file in the Git history",
                      result["errors"][0])
        self.assertIn("fetch the history that holds it", result["errors"][0])
        with self.assertRaisesRegex(ValueError, "neither the current policy nor an approved file"):
            self.task("deliver", "code-reviewer", "review", delivery=DELIVERY)

    def test_a_malformed_git_batch_reads_as_no_pinned_revision(self):
        self.revise_policy(review_loop="blocking_delta")
        self.commit("Approve Process Policy revision 1")
        pin = process_policy.approved_snapshot(self.docs)[0]
        found = process_policy.history_revision(self.docs, pin)
        self.assertIsNotNone(found)
        real = subprocess.run

        def truncated(command, *args, **kwargs):
            result = real(command, *args, **kwargs)
            if "cat-file" in command:
                result = subprocess.CompletedProcess(result.args, 0, result.stdout[:20], b"")
            return result

        with mock.patch.object(process_policy.subprocess, "run", side_effect=truncated):
            self.assertIsNone(process_policy.history_revision(self.docs, pin))

    def test_a_policy_at_every_default_is_no_drift_for_a_delivery_that_pinned_none(self):
        # HAN's DLV-002 was execution-approved before any Process Policy existed.
        self.propose_and_approve_scope()
        self.approve_execution()
        props, _body = delivery_compile.split_note(self.delivery_note())
        self.assertFalse(set(process_policy.PIN_FIELDS) & set(props))
        self.revise_policy()
        self.assertEqual(self.findings(), [])
        self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 0)
        self.assertEqual(self.value("review_loop")[1]["value"], "current")
        self.assertNotIn(DELIVER_LANES, self.task("deliver", "backend-developer", delivery=DELIVERY))
        # A switch that no Delivery flow owns changes nothing the Delivery runs.
        self.revise_policy(review_manifest_scope="bounded")
        self.assertEqual(self.findings(), [])
        # One that a Delivery flow owns is drift until an execution approval pins it.
        self.revise_policy(review_loop="blocking_delta")
        self.assertEqual(self.findings(), [
            "Delivery runs switch review_loop at current under no Process Policy, as it pinned"
            " none, but the approved revision 3 sets blocking_delta; to run it under the approved"
            " policy, revise its execution plan in order: begin-plan-revision, the execution-plan"
            " tasks, which bind that policy while the barrier is held, and the Item revisions"
            " they make, approve-execution, which pins it, publish-execution-plan,"
            " finish-plan-revision; to keep the pinned values instead, approve a Process Policy"
            " revision that sets them back through /configure process"])
        self.assertEqual(self.value("review_loop")[0], 1)
        with self.assertRaisesRegex(ValueError, "Delivery runs switch review_loop at current"):
            self.task("deliver", "backend-developer", delivery=DELIVERY)
        # Setting the value back is the other way out.
        self.revise_policy(review_loop="default")
        self.assertEqual(self.findings(), [])

    def first_policy(self, *argv: tuple[str, ...]) -> None:
        """Approve the project's first Process Policy with the given set commands."""
        process_policy.path_for(self.docs).unlink(missing_ok=True)
        self.policy("init")
        for step in argv:
            self.policy(*step)
        self.policy("approve")

    def test_an_execution_approved_delivery_compares_only_the_switches_its_execution_flow_reads(self):
        """Once execution is approved only delivery-execution runs until a plan revision, whose
        approval pins the planning switches anew, so a first policy that sets only review_panels
        or story_size_budget leaves a Delivery that pinned none running (rr-switches-06)."""
        planning = {
            "review_panels": [("set", "--switch", "review_panels", "--value", "lens_panel")],
            "story_size_budget": [("set", "--switch", "story_size_budget", "--value", "propose_split"),
                                  ("set", "--switch", "story_size_budget", "--parameter",
                                   "acceptance_criteria", "--value", "12")]}
        self.propose_and_approve_scope()
        # A scope-approved Delivery still runs its planning flows, so every Delivery switch counts.
        for switch, commands in planning.items():
            with self.subTest(status="scope_approved", switch=switch):
                self.first_policy(*commands)
                self.assertTrue(any(finding.startswith(f"Delivery runs switch {switch} at")
                                    for finding in self.findings()), self.findings())
        process_policy.path_for(self.docs).unlink()
        self.approve_execution()
        for switch, commands in planning.items():
            with self.subTest(status="execution_approved", switch=switch):
                self.first_policy(*commands)
                self.assertEqual(self.findings(), [])
                self.assertEqual(quiet(delivery_compile.check_delivery, self.plan)[0], 0)
        # A switch the execution flow reads is still drift until an execution approval pins it.
        self.first_policy(("set", "--switch", "review_loop", "--value", "blocking_delta"))
        self.assertTrue(self.findings()[0].startswith("Delivery runs switch review_loop at current"))
        # The protocol and /configure process name the set the registry derives.
        names = sorted(process_policy.delivery_switches(process_policy.load_registry(),
                                                        status="execution_approved"))
        listed = ", ".join(f"`{name}`" for name in names[:-1]) + f" and `{names[-1]}`."
        for relative in ("docs/requirement-delivery-protocol.md",
                         "plugins/software-engineering-team/skill-content/configure/references/process-policy.md"):
            with self.subTest(doc=relative):
                text = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
                self.assertIn("flow owns: " + listed, text)

    def test_a_switch_no_delivery_flow_owns_is_read_from_the_current_policy(self):
        """The pin covers only the switches a Delivery flow owns. mechanical_pass_tier belongs to
        backlog, Operation and Solution Design work, so a /configure task inside a running
        Delivery reads it from the current policy instead of demanding a plan revision
        (rr-seams-07)."""
        self.revise_policy(review_loop="blocking_delta")
        self.propose_and_approve_scope()
        self.approve_execution()
        self.commit("Pin Process Policy revision 1")
        self.revise_policy(mechanical_pass_tier="mechanical")
        self.assertEqual(self.findings(), [])
        code, result = self.value("mechanical_pass_tier")
        self.assertEqual((code, result["value"]), (0, "mechanical"), result)
        self.task("configure", "qa-engineer", "revise", delivery=DELIVERY)
        self.task("deliver", "backend-developer", delivery=DELIVERY)
        # From the Review on a Delivery switch reads the pinned revision, the other the current policy.
        self.set_status(self.delivery_note(), "review")
        self.revise_policy(review_loop="default")
        self.assertEqual(self.value("review_loop")[1]["value"], "blocking_delta")
        code, result = self.value("mechanical_pass_tier")
        self.assertEqual((code, result["value"]), (0, "mechanical"), result)
        self.revise_policy(mechanical_pass_tier="default")
        self.assertEqual(self.value("mechanical_pass_tier")[1]["value"], "role_tier")

    @contextlib.contextmanager
    def lane_story(self):
        """Give AUTH-01 two more implementation roles, so its Item can run lanes."""
        original = delivery_compile.approved_backlog_sources

        def with_roles(docs, story_ids, **kwargs):
            sources, snapshot, errors = original(docs, story_ids, **kwargs)
            for source in sources.values():
                source["supporting_roles"] = ["devops_engineer", "frontend_developer"]
            return sources, snapshot, errors

        with mock.patch.object(delivery_compile, "approved_backlog_sources", with_roles), \
                mock.patch.object(architecture_compile, "solution_components",
                                  return_value=LANE_COMPONENTS):
            yield

    def test_a_running_delivery_moves_from_parallel_lanes_to_sequential_in_a_plan_revision(self):
        lanes = {"architecture_impact": "required", "architecture_components": ["api"],
                 "architecture_record_kinds": ["interface-contract"],
                 "architecture_reason": "Fix the API seam before the lanes start.",
                 "role_sequence": ["software_architect", "backend_developer", "devops_engineer",
                                   "frontend_developer", "code_reviewer", "qa_engineer"],
                 "path_claims": ["deploy", "workspace/apps/api", "workspace/apps/web"],
                 "contract_claims": ["auth:session"], "implementation_schedule": "parallel_lanes_v1",
                 "lane_scopes": LANES,
                 "lane_seams": ["backend_developer -> frontend_developer via IFC-001"]}
        with self.lane_story():
            self.revise_policy(implementation_schedule="parallel_lanes_v1")
            self.commit("Approve Process Policy revision 1")
            self.propose_and_approve_scope()
            self.commit("Approve the DLV-001 scope")
            delivery_git.reserve_delivery(self.project, DELIVERY)
            self.approve_execution(**lanes)
            self.commit("Approve the DLV-001 execution plan")
            delivery_git.publish_execution_plan(self.project, DELIVERY)
            architect = dict(entry="execution-plan", role="software-architect", mode="revise",
                             inputs=[ITEM])
            self.assertIn(PLAN_LANES, self.task(**architect))
            # The owner turns lanes off while DLV-001 runs.
            self.revise_policy(implementation_schedule="default")
            self.commit("Approve Process Policy revision 2")
            self.assertTrue(self.findings()[0].startswith(
                "Delivery runs switch implementation_schedule at parallel_lanes_v1 under its pinned"
                " Process Policy revision 1, but the approved revision 2 sets sequential_v1; to run"
                " it under the approved policy, revise its execution plan in order:"
                " begin-plan-revision,"), self.findings())
            # Without the barrier no task of the Delivery binds either policy.
            for task in (architect, dict(entry="deliver", role="backend-developer", inputs=[ITEM])):
                with self.subTest(task=task["entry"]), self.assertRaisesRegex(
                        ValueError, "begin-plan-revision, the execution-plan tasks"):
                    self.task(**task)
            delivery_git.begin_plan_revision(self.project, DELIVERY)
            # Inside the barrier the planning task binds the approved policy the
            # revision's approval pins; the Items still wait for that approval.
            self.assertNotIn(PLAN_LANES, self.task(**architect))
            with self.assertRaisesRegex(ValueError, "Delivery runs switch implementation_schedule"):
                self.task("deliver", "backend-developer", inputs=[ITEM])
            refreshed = self.approve_execution(**{**lanes, "implementation_schedule": "sequential_v1",
                                                  "lane_scopes": [], "lane_seams": []})
            self.assertEqual(refreshed["refreshed_delivery_pins"],
                             ["process_policy_revision", "process_policy_source_hash"])
            self.assertEqual(self.findings(), [])
            self.commit("Approve the revised DLV-001 execution plan")
            delivery_git.publish_execution_plan(self.project, DELIVERY)
            delivery_git.finish_plan_revision(self.project, DELIVERY)
            self.assertNotIn(DELIVER_LANES, self.task("deliver", "backend-developer", inputs=[ITEM]))
            self.assertNotIn(PLAN_LANES, self.task(**architect))


if __name__ == "__main__":
    unittest.main()
