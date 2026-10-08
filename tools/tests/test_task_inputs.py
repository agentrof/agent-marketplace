"""Derived task inputs preserve full reads, scoped sources and role boundaries."""

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
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import backlog_compile
import process_policy
import task_inputs
from backlog_fixture import CONSTRAINT, CRITERION, DESIGN, EXPERIENCE, _author_story, make_approved_backlog
from git_fixture import init_repository, remove_temporary
from test_default_equivalence import SWITCH_DATA, SWITCH_FILES, build_task_package, build_task_project


def commit_all(root):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"], check=True, capture_output=True)


def instruction_paths(project, entry, role, skills=None, catalog=None):
    """The required reads and hashed instructions of a task, as task_inputs.manifest derives them
    from the project's Process Policy, without the Git reads of a full manifest."""
    catalog = catalog or task_inputs.catalog()
    route = catalog["entries"][entry]
    chosen, _policy_inputs = task_inputs.switch_choices(project, route, task_inputs.PACKAGE)
    _registry, _chosen, required, _conditional, hashed = task_inputs.instruction_reads(
        catalog, task_inputs.PACKAGE, route, role,
        task_inputs.task_skills(catalog, entry, role, skills, route), chosen)
    return required, hashed


def task_scope(project, entry, role, mode, inputs=(), closure=None, catalog=None, chosen=frozenset(),
               remote="origin"):
    """The write scope task_inputs.manifest derives for a task, without its Git reads."""
    catalog = catalog or task_inputs.catalog()
    paths = set(inputs) | ({"workspace/docs/" + path for path in closure["paths"]} if closure else set())
    return task_inputs.write_scope(project, paths, role, catalog["entries"][entry],
                                   task_inputs.read_only_task(catalog, entry, role, mode), closure,
                                   task_inputs.PACKAGE, chosen=chosen, remote=remote)


class TaskInputTests(unittest.TestCase):
    @integration
    def test_setup_supports_unborn_repository_but_binds_initial_files_and_commit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            init_repository(root)
            (root / "README.md").write_text("New project\n", encoding="utf-8")
            kwargs = dict(entry="setup", role="delivery-coordinator", mode="create", project=root)
            result = task_inputs.manifest(**kwargs)
            self.assertIsNone(result["head"])
            self.assertEqual(result["changed_paths"], ["README.md"])
            with self.assertRaisesRegex(ValueError, "committed Git HEAD"):
                task_inputs.manifest(entry="deliver", role="code-reviewer", mode="review", project=root)
            with self.assertRaisesRegex(ValueError, "committed Git HEAD"):
                task_inputs.manifest(**kwargs, base="HEAD")
            (root / "README.md").write_text("Changed project\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            self.commit(root)
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def make_project(self, root):
        init_repository(root)
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True, capture_output=True)
        (root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        self.commit(root)

    def commit(self, root):
        commit_all(root)

    def note(self, root, relative, kind, owner, extra=""):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: {kind}\nowner_role: {owner}\n{extra}---\n\n# Selected source\n", encoding="utf-8")
        return relative

    @integration
    def test_ba_write_scope_is_exact_owned_selected_space_and_not_read_dependencies(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            own = self.note(root, "workspace/docs/business-analysis/orders/space.md", "space", "business_analyst")
            other = self.note(root, "workspace/docs/business-analysis/payments/space.md", "space", "business_analyst")
            dependency = self.note(root, "workspace/docs/solution-design/landscape.md", "landscape", "solution_architect")
            generated = self.note(root, "workspace/docs/business-analysis/orders/_generated/catalog.md", "space", "business_analyst")
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="repair",
                          inputs=[own, dependency, generated])
            result = task_scope(root, **kwargs)
            self.assertEqual(result["allowed_write_area"],
                             [{"path": own, "coverage": "exact_file", "source": own}])
            self.assertFalse(result["writer_authority"])
            self.make_project(root)
            manifest = task_inputs.manifest(**kwargs, project=root)
            self.assertEqual(manifest["write_scope"]["allowed_write_area"], result["allowed_write_area"])
            self.assertIn("ba_compile.py", next(row["detail"] for row in manifest["next_transition_conditions"]
                                               if row["condition"] == "entry_gate"))
            unresolved = task_scope(root, **{**kwargs, "inputs": [own, other]})
            self.assertEqual(unresolved["status"], "unresolved")
            self.assertEqual(unresolved["allowed_write_area"], [])
            review = task_scope(root, **{**kwargs, "role": "analysis-challenger"})
            self.assertEqual(review["allowed_write_area"], [])

    def test_epic_owner_write_area_excludes_dependency_context_and_keeps_po_as_writer(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            rows = [
                ("backlog/backlog.md", "backlog", "product_owner"),
                ("backlog/epics/one/epic.md", "epic", "product_owner"),
                ("backlog/epics/one/stories/one/story.md", "story", "backend_developer"),
                ("backlog/epics/one/stories/one/test-plan.md", "test-plan", "qa_engineer"),
                ("backlog/epics/one/reviews/round-1-epic-review.md", "epic-review", "product_owner"),
                ("backlog/epics/two/stories/two/story.md", "story", "backend_developer"),
            ]
            for path, kind, owner in rows:
                self.note(root, "workspace/docs/" + path, kind, owner)
            closure = {"scope": rows[1][0], "primary_paths": [row[0] for row in rows[:4]],
                       "paths": [row[0] for row in rows], "review": {"path": rows[4][0]},
                       "check": {}}
            unscoped = task_scope(root, "backlog-plan", "product-owner", "revise",
                                  inputs=["workspace/docs/" + row[0] for row in rows])
            self.assertEqual(unscoped["status"], "unresolved")
            self.assertEqual(unscoped["allowed_write_area"], [])
            result = task_scope(root, "backlog-plan", "product-owner", "revise", closure=closure)
            self.assertEqual([row["path"] for row in result["allowed_write_area"]],
                             sorted("workspace/docs/" + row[0] for row in rows[1:5]))
            for role in ("business-analyst", "qa-engineer", "backlog-reviewer"):
                reader = task_scope(root, "backlog-plan", role, "revise", closure=closure)
                self.assertEqual(reader["status"], "read_only")
                self.assertEqual(reader["allowed_write_area"], [])

    @integration
    def test_product_owner_scope_covers_a_story_right_after_stub_story(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "maps").mkdir(parents=True)
            (root / "workspace/config.json").write_text(json.dumps({
                "schema_version": 2, "team_id": "software-engineering-team",
                "output_language": "English", "terminology_language": "English",
            }), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                make_approved_backlog(docs)
                self.commit(root)
                self.assertEqual(backlog_compile.stub_story(SimpleNamespace(
                    docs=str(docs), epic="delivery-fixture", slug="job-worker", id="AUTH-02",
                    title="Job worker", scope=None, work_kind="technical", criterion_ref=[],
                    experience_ref=[], evidence_ref=["[[solution-design/decisions/fixture-api|Fixture API]]"],
                    uses_design=[], constrained_by=[], implements=[])), 0)
            folder = "workspace/docs/backlog/epics/delivery-fixture/stories/job-worker/"
            kwargs = dict(entry="backlog-plan", project=root, epic="EP-001")
            writer = task_inputs.manifest(**kwargs, role="product-owner", mode="revise")
            self.assertEqual(writer["write_scope"]["status"], "resolved")
            area = [row["path"] for row in writer["write_scope"]["allowed_write_area"]]
            self.assertIn(folder + "story.md", area)
            self.assertIn(folder + "test-plan.md", area)
            self.assertTrue(writer["backlog_scope"]["check"]["scaffold_findings"])
            for role, mode in (("backlog-reviewer", "review"), ("product-owner", "review")):
                with self.assertRaisesRegex(ValueError, "untouched"):
                    task_inputs.manifest(**kwargs, role=role, mode=mode)

    @integration
    def test_item_claims_bind_one_item_and_role_without_granting_runtime_or_vault_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            item = self.note(root, "workspace/docs/delivery/deliveries/one/items/st-001/item.md",
                             "delivery-item", "backend_developer", "role_sequence:\n  - backend_developer\n  - code_reviewer\n  - qa_engineer\npath_claims:\n  - src\n  - tests\n  - workspace/docs\n  - .git\n")
            self.commit(root)
            kwargs = dict(entry="deliver", role="backend-developer", mode="repair", project=root, inputs=[item])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["write_scope"]["allowed_write_area"], [
                {"path": path, "coverage": "path_and_descendants", "source": item} for path in ("src", "tests")])
            self.assertFalse(result["write_scope"]["writer_authority"])
            self.assertIn("workspace/docs", result["write_scope"]["excluded_subtrees"])
            other_role = task_inputs.manifest(**{**kwargs, "role": "frontend-developer"})
            self.assertEqual(other_role["write_scope"]["status"], "unresolved")
            self.assertEqual(other_role["write_scope"]["allowed_write_area"], [])
            content = (root / item).read_text(encoding="utf-8")
            (root / item).write_text(content.replace("  - src", "  - ../outside"), encoding="utf-8")
            self.assertEqual(task_inputs.manifest(**kwargs)["write_scope"]["status"], "unresolved")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    @integration
    def test_item_claims_come_from_the_published_record_and_live_provisional_claims(self):
        """A checkout's Item record can be a plan revision's unapproved draft, so the implementer's scope
        takes the claims of the record the Delivery's Integration publishes. At provisional_claims
        during_plan_revision it adds the Item's live provisional paths, and nothing without one (#464)."""
        import delivery_git
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve() / "project"
            root.mkdir()
            self.make_project(root)
            package = "workspace/docs/delivery/deliveries/dlv-001-auth"
            self.note(root, package + "/delivery.md", "delivery", "delivery_coordinator", "id: DLV-001\n")
            claims = "story_id: AUTH-01\nrole_sequence:\n  - backend_developer\n  - code_reviewer\n  - qa_engineer\n"
            item = self.note(root, package + "/items/auth-01/item.md", "delivery-item", "backend_developer",
                             claims + "path_claims:\n  - src/auth.py\n")
            self.commit(root)
            remote = Path(raw).resolve() / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(remote)], check=True)
            ref = delivery_git.canonical_refs("DLV-001")["integration"]
            subprocess.run(["git", "-C", str(root), "push", "-q", "origin", "HEAD:" + ref], check=True,
                           capture_output=True)
            # The plan revision's draft adds a path no approval published.
            self.note(root, item, "delivery-item", "backend_developer",
                      claims + "path_claims:\n  - src/auth.py\n  - src/verify.py\n")
            on = {("provisional_claims", "during_plan_revision")}

            def area(chosen=frozenset()):
                scope = task_scope(root, "deliver", "backend-developer", "create", inputs=[item], chosen=chosen)
                return [target["path"] for target in scope["allowed_write_area"]], scope["constraints"]

            self.assertEqual(area(), (["src/auth.py"], area()[1]))
            self.assertEqual(area(on), area())
            live = {"story": "AUTH-01", "paths": ["src/verify.py"], "state": "live"}
            for claims_read, expected in (([live], ["src/auth.py", "src/verify.py"]),
                                          ([{**live, "state": "void"}], ["src/auth.py"]),
                                          ([{**live, "story": "OTHER-01"}], ["src/auth.py"])):
                with self.subTest(claims=claims_read), mock.patch.object(
                        delivery_git, "delivery_provisional_claims", return_value=claims_read):
                    paths, constraints = area(on)
                    self.assertEqual(paths, expected)
                    self.assertEqual(task_inputs.PROVISIONAL_CONSTRAINT in constraints, len(expected) == 2)
                    self.assertEqual(area(), (["src/auth.py"], area()[1]))
            subprocess.run(["git", "-C", str(root), "push", "-q", "origin", ":" + ref], check=True,
                           capture_output=True)
            scope = task_scope(root, "deliver", "backend-developer", "create", inputs=[item])
            self.assertEqual((scope["status"], scope["reason"]), ("unresolved", "DLV-001 has no Integration on origin"))

    def published_item_project(self, raw, remote_name="origin"):
        """A project whose Delivery remote publishes AUTH-01 claiming src/auth.py on the reservation record of
        its Integration while the checkout's draft adds src/unapproved.py, the remote-tracking ref of that
        Integration held locally."""
        import delivery_git
        root = Path(raw).resolve() / "project"
        root.mkdir()
        self.make_project(root)
        package = "workspace/docs/delivery/deliveries/dlv-001-auth"
        self.note(root, package + "/delivery.md", "delivery", "delivery_coordinator", "id: DLV-001\n")
        claims = "story_id: AUTH-01\nrole_sequence:\n  - backend_developer\n  - code_reviewer\n  - qa_engineer\n"
        item = self.note(root, package + "/items/auth-01/item.md", "delivery-item", "backend_developer",
                         claims + "path_claims:\n  - src/auth.py\n")
        self.commit(root)
        remote = Path(raw).resolve() / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", remote_name, str(remote)], check=True)
        ref = delivery_git.canonical_refs("DLV-001")["integration"]
        published = delivery_git.commit_tree(root, "HEAD", [], "Reserve Delivery DLV-001", {
            "Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": "DLV-001"})
        subprocess.run(["git", "-C", str(root), "push", "-q", remote_name, published + ":" + ref], check=True,
                       capture_output=True)
        self.note(root, item, "delivery-item", "backend_developer",
                  claims + "path_claims:\n  - src/auth.py\n  - src/unapproved.py\n")
        return root, item, remote, published

    def integration_commit(self, root, item, parent, claims, trailers):
        """A commit on *parent* whose AUTH-01 record claims *claims*; the checkout's draft stays as it was."""
        import delivery_git
        draft = (root / item).read_bytes()
        self.note(root, item, "delivery-item", "backend_developer",
                  "story_id: AUTH-01\nrole_sequence:\n  - backend_developer\npath_claims:\n"
                  + "".join(f"  - {claim}\n" for claim in claims))
        try:
            return delivery_git.commit_tree(root, parent, [item], "Publish execution plan for DLV-001", trailers)
        finally:
            (root / item).write_bytes(draft)

    @integration
    def test_item_claims_resolve_from_the_remote_first_and_offline_from_verified_local_records(self):
        """#464: the Delivery's remote is the source of truth while it answers, even when the checkout's
        tracking ref of the Integration is stale. Only offline does a local published commit stand in, the
        newest by ancestry of the tracking ref and the Item worktree's integration_base_commit, and only as a
        record commit of the Delivery's Integration line: a forged tracking ref grants nothing, and with no
        such record the scope is unresolved with its reason. The draft's added path is never granted."""
        import delivery_git
        with tempfile.TemporaryDirectory() as raw:
            root, item, remote, published = self.published_item_project(raw)
            ref = delivery_git.canonical_refs("DLV-001")["integration"]
            tracking = "refs/remotes/origin/" + delivery_git.short_refs("DLV-001")["integration"]
            record = {"Record": "execution-plan-published-v1", "Protocol": "1", "Delivery": "DLV-001"}

            def paths():
                return [target["path"] for target in scope()["allowed_write_area"]]

            def scope():
                return task_scope(root, "deliver", "backend-developer", "create", inputs=[item])

            self.assertEqual(paths(), ["src/auth.py"])
            # Another host published a revision; this checkout's tracking ref still names the older record.
            newer = self.integration_commit(root, item, published, ["src/auth.py", "src/newer.py"], record)
            subprocess.run(["git", "-C", str(root), "push", "-q", "origin", f"{newer}:{ref}"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(root), "update-ref", tracking, published], check=True)
            self.assertEqual(paths(), ["src/auth.py", "src/newer.py"])
            # Offline, a forged tracking ref whose record widens the claims is no Integration record.
            forged = self.integration_commit(root, item, newer, ["src/auth.py", "anything/else.py"], {})
            subprocess.run(["git", "-C", str(root), "update-ref", tracking, forged], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "set-url", "origin", str(remote) + "-gone"], check=True)
            offline = scope()
            self.assertEqual(offline["status"], "unresolved")
            self.assertTrue(offline["reason"].startswith(
                "the published record of the selected Item cannot be read: no published Integration commit of "
                "DLV-001 is in this checkout and origin cannot provide one: "), offline["reason"])
            # Offline, a valid record commit of the Delivery's line resolves the scope.
            subprocess.run(["git", "-C", str(root), "update-ref", tracking, published], check=True)
            self.assertEqual(paths(), ["src/auth.py"])
            # The Item worktree's record names the newer Integration commit its writer converged on,
            # which wins by ancestry over the older tracking ref.
            worktree = delivery_git.worktree_paths(root, "DLV-001", "AUTH-01")["item"]
            converged = worktree / item
            converged.parent.mkdir(parents=True)
            converged.write_text("---\ntype: delivery-item\npath_claims:\n  - src/unapproved.py\n"
                                 f"integration_base_commit: {newer}\n---\n", encoding="utf-8")
            self.assertEqual(paths(), ["src/auth.py", "src/newer.py"])
            subprocess.run(["git", "-C", str(root), "update-ref", "-d", tracking], check=True)
            self.assertEqual(paths(), ["src/auth.py", "src/newer.py"])

    @integration
    def test_item_claims_without_a_remote_read_the_item_worktree_base_never_the_draft(self):
        """#464: removing the Delivery's remote does not restore the draft's authority while the Item
        worktree names a published Integration commit: its record resolves the scope, one that is no
        record commit of the Delivery's Integration line leaves the scope unresolved with its reason, and
        only a checkout with no published commit of the Delivery keeps its own record."""
        import delivery_git
        with tempfile.TemporaryDirectory() as raw:
            root, item, _remote, published = self.published_item_project(raw)
            subprocess.run(["git", "-C", str(root), "remote", "remove", "origin"], check=True)

            def scope():
                return task_scope(root, "deliver", "backend-developer", "create", inputs=[item])

            self.assertEqual([target["path"] for target in scope()["allowed_write_area"]],
                             ["src/auth.py", "src/unapproved.py"])
            worktree = delivery_git.worktree_paths(root, "DLV-001", "AUTH-01")["item"]
            converged = worktree / item
            converged.parent.mkdir(parents=True)
            # The checkout's own commit exists but is no record commit of the Delivery's Integration line.
            own = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                                 encoding="utf-8").stdout.strip()
            for base, expected in ((published, ["src/auth.py"]), (own, None)):
                with self.subTest(base=base):
                    converged.write_text(f"---\ntype: delivery-item\nintegration_base_commit: {base}\n---\n",
                                         encoding="utf-8")
                    resolved = scope()
                    if expected is None:
                        self.assertEqual((resolved["status"], resolved["reason"]), (
                            "unresolved", "this checkout has no remote and no commit it holds for DLV-001 is a "
                                          "published Integration record of DLV-001; add the Delivery's remote"))
                    else:
                        self.assertEqual([target["path"] for target in resolved["allowed_write_area"]], expected)

    @integration
    def test_item_claims_name_the_delivery_remote_instead_of_reading_the_draft(self):
        """#464: a checkout whose only remote is not origin keeps no draft authority: the default remote
        leaves the scope unresolved and names --remote, and the Delivery's remote resolves the published
        record."""
        with tempfile.TemporaryDirectory() as raw:
            root, item, _remote, _published = self.published_item_project(raw, "upstream")
            tracking = subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(refname)",
                                       "refs/remotes/upstream"], check=True, capture_output=True,
                                      encoding="utf-8").stdout.split()
            for ref in tracking:
                subprocess.run(["git", "-C", str(root), "update-ref", "-d", ref], check=True)
            default = task_scope(root, "deliver", "backend-developer", "create", inputs=[item])
            self.assertEqual((default["status"], default["reason"]), (
                "unresolved", "no published record of the selected Item is in this checkout and it has no remote "
                              "origin; name the Delivery's remote with --remote"))
            named = task_scope(root, "deliver", "backend-developer", "create", inputs=[item], remote="upstream")
            self.assertEqual([target["path"] for target in named["allowed_write_area"]], ["src/auth.py"])

    def test_item_claims_exclude_the_authority_roots_with_their_case_folded(self):
        """#464: a claim under Workspace/docs, .Agentrof or .GIT reaches the same directory on a file system
        that folds case, so the implementer's scope leaves it out like its lower-case form."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            package = "workspace/docs/delivery/deliveries/dlv-001-auth"
            item = self.note(root, package + "/items/auth-01/item.md", "delivery-item", "backend_developer",
                             "story_id: AUTH-01\nrole_sequence:\n  - backend_developer\npath_claims:\n"
                             "  - src/auth.py\n  - Workspace/docs/x.md\n  - .Agentrof/x\n  - .GIT/hooks\n")
            scope = task_scope(root, "deliver", "backend-developer", "create", inputs=[item])
            self.assertEqual([target["path"] for target in scope["allowed_write_area"]], ["src/auth.py"])

    def test_lane_roles_bind_only_their_lane_scope_while_the_architect_keeps_every_claim(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            lanes = ("implementation_schedule: parallel_lanes_v1\nrole_sequence:\n  - software_architect\n"
                     "  - backend_developer\n  - devops_engineer\n  - code_reviewer\n  - qa_engineer\n"
                     "path_claims:\n  - deploy\n  - src/api\n  - workspace/docs\n"
                     "lane_scopes:\n  - backend_developer:src/api\n  - devops_engineer:deploy\n"
                     "lane_seams:\n")
            item = self.note(root, "workspace/docs/delivery/deliveries/one/items/st-001/item.md",
                             "delivery-item", "backend_developer", lanes)

            def scope(role):
                return task_scope(root, "deliver", role, "create", inputs=[item])

            lane_note = [constraint for constraint in scope("backend-developer")["constraints"]
                         if constraint.startswith("parallel lane:")]
            self.assertEqual(len(lane_note), 1)
            for role, paths in (("backend-developer", ["src/api"]), ("devops-engineer", ["deploy"])):
                with self.subTest(role=role):
                    self.assertEqual(scope(role)["allowed_write_area"], [
                        {"path": path, "coverage": "path_and_descendants", "source": item} for path in paths])
                    self.assertIn(lane_note[0], scope(role)["constraints"])
            # The architect runs alone before the lanes, so its area is the Item's claims.
            architect = scope("software-architect")
            self.assertEqual([area["path"] for area in architect["allowed_write_area"]], ["deploy", "src/api"])
            self.assertNotIn(lane_note[0], architect["constraints"])
            content = (root / item).read_text(encoding="utf-8")
            for broken in (content.replace("backend_developer:src/api", "backend_developer:src\\api"),
                           content.replace("backend_developer:src/api", "backend_developer:elsewhere"),
                           content.replace("parallel_lanes_v1", "parallel_lanes_v2")):
                with self.subTest(broken=broken):
                    (root / item).write_text(broken, encoding="utf-8")
                    self.assertEqual(scope("backend-developer")["status"], "unresolved")
            # Without the schedule every implementation role holds every claim, as before.
            (root / item).write_text(content.replace("implementation_schedule: parallel_lanes_v1\n", ""),
                                     encoding="utf-8")
            self.assertEqual([area["path"] for area in scope("backend-developer")["allowed_write_area"]],
                             ["deploy", "src/api"])

    def test_parallel_lane_instructions_are_bound_only_at_parallel_lanes(self):
        planning = "skill-content/execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md"
        execution = "skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            tasks = (("execution-plan", "software-architect", planning),
                     ("deliver", "backend-developer", execution),
                     ("deliver", "code-reviewer", execution),
                     ("deliver", None, execution))

            def bound(entry, role):
                required, hashed = instruction_paths(root, entry, role)
                return required | hashed

            for entry, role, reference in tasks:
                with self.subTest(entry=entry, role=role, policy=False):
                    self.assertFalse({planning, execution} & bound(entry, role))
            docs = root / "workspace/docs"
            def policy(*argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]), 0)
            policy("init")
            policy("set", "--switch", "implementation_schedule", "--value", "parallel_lanes_v1")
            policy("approve")
            for entry, role, reference in tasks:
                with self.subTest(entry=entry, role=role, policy=True):
                    self.assertEqual({planning, execution} & bound(entry, role), {reference})

    @integration
    def test_unknown_write_scope_remains_empty_with_unresolved_transition_condition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            result = task_inputs.manifest(entry="setup", role="delivery-coordinator", mode="create",
                                          project=root, inputs=["brief.md"])
            self.assertEqual(result["write_scope"]["status"], "unresolved")
            self.assertEqual(result["write_scope"]["allowed_write_area"], [])
            self.assertEqual(next(row["status"] for row in result["next_transition_conditions"]
                                  if row["condition"] == "write_scope"), "unresolved")

    def test_regular_input_rejects_lexical_and_filesystem_aliases(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "note.md").write_text("Note", encoding="utf-8")
            for name in ("./note.md", "folder/../note.md", "folder//note.md", "note.md/", "C:note.md", "note.md."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    task_inputs.regular(root, name)
            try:
                (root / "alias.md").symlink_to(root / "note.md")
            except OSError as exc:
                self.skipTest(str(exc))
            with self.assertRaisesRegex(ValueError, "symlink"):
                task_inputs.regular(root, "alias.md")

    @integration
    def test_new_incoming_canonical_source_invalidates_but_opaque_interior_does_not(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "solution-design").mkdir(parents=True)
            (docs / "solution-design/landscape.md").write_text("Landscape", encoding="utf-8")
            self.commit(root)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="review", project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            opaque = docs / "experience-design/artifacts/deep/arbitrary.json"
            opaque.parent.mkdir(parents=True)
            opaque.write_bytes(b"\xffopaque unparsed")
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"]), result)
            (docs / "solution-design/incoming.md").write_text("[[business-analysis/new-edge|Incoming]]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    @integration
    def test_an_input_task_binds_its_inputs_and_cited_notes_and_only_membership_of_the_rest(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            docs = root / "workspace/docs"
            (docs / "operation").mkdir(parents=True)
            (docs / "operation/cited.md").write_text("Cited", encoding="utf-8")
            (docs / "operation/unrelated.md").write_text("Unrelated", encoding="utf-8")
            (root / "brief.md").write_text("Reads [[operation/cited|Cited]].\n", encoding="utf-8")
            self.commit(root)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="review",
                          project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["context_membership"], {"count": 2, "source_hash":
                             task_inputs.digest(task_inputs.source_paths(root))})
            self.assertNotIn("canonical_source_paths", result)
            self.assertEqual({record["path"] for record in result["canonical_source_inventory"]},
                             {"workspace/docs/operation/cited.md"})
            # Another writer's note and a commit leave the task fresh.
            (docs / "operation/unrelated.md").write_text("Changed elsewhere", encoding="utf-8")
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])["source_hash"],
                             result["source_hash"])
            self.commit(root)
            task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            # A cited note and a new source still invalidate it.
            (docs / "operation/cited.md").write_text("Cited, changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            (docs / "operation/new.md").write_text("New", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    @integration
    def test_base_inventory_includes_dirty_and_untracked_product_sources(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            (root / "brief.md").write_text("Uncommitted change", encoding="utf-8")
            (root / "new.py").write_text("new = True\n", encoding="utf-8")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root, base="HEAD")
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["changed_paths"], ["brief.md", "new.py"])
            (root / "new.py").write_text("new = False\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            subprocess.run(["git", "-C", str(root), "update-index", "--assume-unchanged", "brief.md"], check=True)
            with self.assertRaisesRegex(ValueError, "hidden index"):
                task_inputs.manifest(**kwargs)

    @integration
    def test_head_drift_during_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            original = subprocess.run
            observations = []
            def moved(argv, **kwargs):
                result = original(argv, **kwargs)
                if argv[-3:] == ["rev-parse", "--verify", "HEAD"]:
                    observations.append(True)
                    if len(observations) > 1:
                        result.stdout = b"0" * 40 + b"\n"
                return result
            with mock.patch.object(task_inputs.subprocess, "run", side_effect=moved):
                with self.assertRaisesRegex(ValueError, "HEAD changed"):
                    task_inputs.manifest(entry="business-analysis", role="business-analyst", mode="review", project=root)

    @integration
    def test_technology_methods_require_selected_committed_accepted_decisions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            path = root / "workspace/docs/solution-design/decisions/api-decision.md"
            path.parent.mkdir(parents=True)
            text = "---\ntype: decision\nstatus: proposed\nmethod_skills:\n  - python-fastapi\n---\n\n# API\n"
            path.write_text(text, encoding="utf-8")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root,
                          skills=["python-fastapi"], inputs=[path.relative_to(root).as_posix()])
            for value in (text, text.replace("proposed", "accepted")):
                path.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "committed accepted"):
                    task_inputs.manifest(**kwargs)
            self.commit(root)
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(result["method_bindings"]["python-fastapi"], [path.relative_to(root).as_posix()])
            with self.assertRaisesRegex(ValueError, "internal skills"):
                task_inputs.manifest(entry="deliver", role="code-reviewer", mode="review", skills=["setup"])

    @integration
    def test_technology_binding_rejects_git_clean_crlf_bytes_that_differ_from_head(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_project(root)
            subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "true"], check=True, capture_output=True)
            relative = "workspace/docs/solution-design/decisions/api-decision.md"
            path = root / relative
            path.parent.mkdir(parents=True)
            committed = b"---\ntype: decision\nstatus: accepted\nmethod_skills:\n  - python-fastapi\n---\n\n# API\n"
            path.write_bytes(committed.replace(b"\n", b"\r\n"))
            self.commit(root)
            self.assertEqual(subprocess.check_output(["git", "-C", str(root), "show", "HEAD:" + relative]), committed)
            self.assertEqual(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", relative]), b"")
            kwargs = dict(entry="deliver", role="code-reviewer", mode="review", project=root,
                          skills=["python-fastapi"], inputs=[relative])
            with self.assertRaisesRegex(ValueError, "committed accepted"):
                task_inputs.manifest(**kwargs)
            path.write_bytes(committed)
            self.assertEqual(task_inputs.manifest(**kwargs)["method_bindings"], {"python-fastapi": [relative]})

    def test_catalog_covers_current_roles_skills_and_flows(self):
        policy = task_inputs.catalog()
        self.assertEqual(set(policy["role_skills"]), {p.stem for p in (task_inputs.PACKAGE / "agents").glob("*.md")})
        for entry, route in policy["entries"].items():
            for role in route["roles"] or [None]:
                result = task_inputs.manifest(entry=entry, role=role, mode="review")
                self.assertIn("constitution.md", result["required_reads"])
                self.assertFalse(result["approval_authority"])

    def test_method_skills_are_explicit_and_required_checklists_are_full_reads(self):
        result = task_inputs.manifest(entry="deliver", role="code-reviewer", mode="repair", skills=["python-fastapi"])
        self.assertIn("skill-content/code-review/references/passes.md", result["required_reads"])
        self.assertIn("skill-content/python-fastapi/references/review-checklist.md", result["required_reads"])
        backend = task_inputs.manifest(entry="deliver", role="backend-developer", mode="create")
        self.assertNotIn("skill-content/sql-database-design/SKILL.md", backend["required_reads"])
        self.assertNotIn("skill-content/nosql-database-design/SKILL.md", backend["required_reads"])
        qa = task_inputs.manifest(entry="deliver", role="qa-engineer", mode="create")
        self.assertEqual(qa["write_boundary"], "read_only")

    def test_review_panel_protocol_is_bound_only_at_lens_panel(self):
        protocol = "skill-content/challenge-review/references/switch-review_panels-lens_panel.md"
        panels = "skill-content/challenge-review/data/review-panels.json"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            catalog = task_inputs.catalog()
            tasks = (("configure", "devops-engineer", ["challenge-review"]),
                     ("configure", "qa-engineer", ["challenge-review"]),
                     ("backlog-plan", "backlog-reviewer", []),
                     ("backlog-plan", "product-owner", ["challenge-review"]))
            for entry, role, skills in tasks:
                with self.subTest(entry=entry, role=role, policy=False):
                    required, hashed = instruction_paths(root, entry, role, skills, catalog)
                    self.assertIn("skill-content/challenge-review/SKILL.md", required)
                    # The lens data is read only by the values that list it.
                    self.assertNotIn(panels, hashed)
                    self.assertNotIn(protocol, hashed)
            plain, _hashed = instruction_paths(None, "configure", "devops-engineer", None, catalog)
            self.assertNotIn("skill-content/challenge-review/SKILL.md", plain)
            docs = root / "workspace/docs"
            def policy(*argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]), 0)
            policy("init")
            policy("set", "--switch", "review_panels", "--value", "lens_panel")
            policy("approve")
            for entry, role, skills in tasks:
                with self.subTest(entry=entry, role=role, policy=True):
                    required, _hashed = instruction_paths(root, entry, role, skills, catalog)
                    self.assertIn(protocol, required)
                    self.assertIn(panels, required)
                    self.assertTrue(task_inputs.read_only_task(catalog, entry, role, "review"))

    @integration
    def test_process_policy_binds_only_the_chosen_switch_references(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            package, project = base / "package", base / "project"
            build_task_package(package, switch_files=True)
            build_task_project(project)
            docs = project / "workspace/docs"
            reader = dict(entry="fixture-entry", role="fixture-reader", mode="review",
                          project=project, package=package)
            plain = task_inputs.manifest(**reader)

            def policy(*argv):
                output = io.StringIO()
                with mock.patch.object(process_policy, "PACKAGE", package), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(process_policy.main([argv[0], "--docs", str(docs), *argv[1:]]),
                                     0, output.getvalue())

            policy("init")
            policy("set", "--switch", "fixture_mode", "--value", "fast")
            with self.assertRaisesRegex(ValueError, "Process Policy revision 1 is a draft"):
                task_inputs.manifest(**reader)
            policy("approve")
            chosen = task_inputs.manifest(**reader)
            # The value's data travels with its references as a required read.
            self.assertEqual(sorted(set(chosen["required_reads"]) - set(plain["required_reads"])),
                             sorted([*SWITCH_FILES, SWITCH_DATA]))
            self.assertEqual(chosen["conditional_reads"], plain["conditional_reads"])
            self.assertIn("workspace/docs/delivery/process-policy.md",
                          [record["path"] for record in chosen["project_inputs"]])
            self.assertTrue({*SWITCH_FILES, SWITCH_DATA}
                            <= {record["path"] for record in chosen["instructions"]})
            self.assertNotIn(SWITCH_DATA, [record["path"] for record in plain["instructions"]])

            policy("begin-revision")
            policy("set", "--switch", "fixture_mode", "--default")
            policy("approve")
            default = task_inputs.manifest(**reader)
            self.assertEqual(default["required_reads"], plain["required_reads"])
            self.assertEqual(default["instructions"], plain["instructions"])
            self.assertEqual([record["path"] for record in default["project_inputs"]],
                             ["workspace/docs/delivery/process-policy.md"])
            (docs / "delivery/process-policy.md").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "process policy cannot bind switch instructions"):
                task_inputs.manifest(**reader, expected_hash=default["source_hash"])

    @integration
    def test_a_task_inside_a_pinned_delivery_refuses_a_policy_changed_since_the_pin(self):
        import delivery_compile

        lanes = "skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            docs = root / "workspace/docs"
            (docs / "maps").mkdir(parents=True)
            make_approved_backlog(docs)
            self.make_project(root)

            def run(call, *args):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(call(*args), 0)

            def policy(*argv):
                run(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])

            dod = SimpleNamespace(docs=str(docs), title="Project", file=None)
            run(delivery_compile.init_dod, dod)
            run(delivery_compile.approve_dod, dod)
            policy("init")
            policy("approve")
            run(delivery_compile.init_delivery, SimpleNamespace(
                docs=str(docs), id=None, slug="auth", goal="Authenticate", outcome=None,
                target_branch="main", story=["AUTH-01"]))
            run(delivery_compile.approve_scope, SimpleNamespace(docs=str(docs), delivery="DLV-001"))
            self.commit(root)
            item = "workspace/docs/delivery/deliveries/dlv-001-auth/items/auth-01/item.md"
            pinned = process_policy.path_for(docs).read_bytes()

            def bound(**kwargs):
                result = task_inputs.manifest(entry="deliver", role="backend-developer",
                                              mode="create", project=root, **kwargs)
                return set(result["required_reads"])

            inside = ({"inputs": [item]}, {"delivery": "DLV-001"})
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            policy("begin-revision")
            policy("set", "--switch", "implementation_schedule", "--value", "parallel_lanes_v1")
            policy("approve")
            # The Delivery runs under the values it pinned; its tasks never bind a later one.
            drift = ("process policy cannot bind switch instructions: DLV-001: Delivery runs switch"
                     " implementation_schedule at sequential_v1 under its pinned Process Policy"
                     " revision 1, but the approved revision 2 sets parallel_lanes_v1")
            for kwargs in inside:
                with self.subTest(context=kwargs), self.assertRaisesRegex(ValueError, drift):
                    bound(**kwargs)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = task_inputs.main(["--entry", "deliver", "--role", "backend-developer",
                                         "--mode", "create", "--project-root", str(root),
                                         "--delivery", "DLV-001"])
            self.assertEqual(code, 1)
            self.assertIn("Delivery runs switch implementation_schedule at sequential_v1",
                          output.getvalue())
            # A task outside any Delivery follows the project's current policy.
            self.assertIn(lanes, bound())
            # Restoring the pinned policy, or re-pinning through a new execution
            # approval, lets the Delivery's tasks bind again.
            current = process_policy.path_for(docs).read_bytes()
            process_policy.path_for(docs).write_bytes(pinned)
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            # Without a policy every switch is at its default, as the pinned revision sets it.
            process_policy.path_for(docs).unlink()
            for kwargs in inside:
                self.assertNotIn(lanes, bound(**kwargs))
            # From the Delivery Review on the Delivery reads the revision it pinned,
            # so a policy set for the next Delivery never changes what its tasks bind.
            process_policy.path_for(docs).write_bytes(current)
            path = docs / "delivery/deliveries/dlv-001-auth/delivery.md"
            props, body = delivery_compile.split_note(path)
            props["status"] = "review"
            delivery_compile.atomic_text(path, delivery_compile.frontmatter(props, body))
            self.assertNotIn(lanes, bound(inputs=[item]))
            with self.assertRaisesRegex(ValueError, "Delivery not found: DLV-404"):
                bound(delivery="DLV-404")

    def test_a_switch_reference_binds_only_for_tasks_of_its_owning_flows(self):
        values = {"review_panels": "lens_panel", "review_loop": "blocking_delta",
                  "mechanical_pass_tier": "mechanical"}
        references = {switch: f"skill-content/challenge-review/references/switch-{switch}-{value}.md"
                      for switch, value in values.items()}
        # Every task below selects challenge-review; only the switches whose
        # owning flows its entry runs reach it.
        tasks = {("business-analysis", "analysis-challenger"): set(),
                 ("experience-design", "experience-reviewer"): set(),
                 ("backlog-plan", "backlog-reviewer"): set(references),
                 ("solution-design", "solution-reviewer"): set(references),
                 ("design-system", "design-system-reviewer"): {"review_panels", "review_loop"}}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            docs = root / "workspace/docs"

            def policy(command, *argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(process_policy.main([command, "--docs", str(docs), *argv]), 0)

            policy("init")
            for switch, value in values.items():
                policy("set", "--switch", switch, "--value", value)
            policy("approve")
            for (entry, role), owned in tasks.items():
                with self.subTest(entry=entry, role=role):
                    required, _hashed = instruction_paths(root, entry, role)
                    self.assertIn("skill-content/challenge-review/SKILL.md", required)
                    self.assertEqual({switch for switch, path in references.items()
                                      if path in required}, owned)

    @integration
    def test_changed_project_input_invalidates_manifest_without_runtime_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_repository(root)
            (root / "brief.md").write_text("Original accepted intent.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "brief.md"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "brief"], check=True)
            kwargs = dict(entry="business-analysis", role="business-analyst", mode="revise", project=root, inputs=["brief.md"])
            result = task_inputs.manifest(**kwargs)
            self.assertEqual(task_inputs.manifest(**kwargs, expected_hash=result["source_hash"]), result)
            (root / "brief.md").write_text("Changed intent.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            self.assertFalse((root / ".agentrof").exists())

    def test_catalog_rejects_orphan_role_and_missing_mandatory_reference(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "package"
            shutil.copytree(task_inputs.PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
            (package / "agents/new-role.md").write_text("# Unregistered role\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "every current role"):
                task_inputs.catalog(package)
            (package / "agents/new-role.md").unlink()
            (package / "skill-content/code-review/references/passes.md").unlink()
            with self.assertRaisesRegex(ValueError, "required input is missing"):
                task_inputs.catalog(package)

    def test_invoked_tools_and_selected_method_data_invalidate_without_becoming_reads(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "package"
            shutil.copytree(task_inputs.PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
            kwargs = dict(entry="design-system", role="ux-designer", mode="create", package=package,
                          skills=["ui-ux-design"])
            result = task_inputs.manifest(**kwargs)
            data = "skill-content/ui-ux-design/data/styles.csv"
            self.assertNotIn(data, result["required_reads"])
            self.assertIn(data, {record["path"] for record in result["instructions"]})
            with (package / data).open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])
            result = task_inputs.manifest(**kwargs)
            with (package / "scripts/ba_compile.py").open("ab") as output:
                output.write(b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                task_inputs.manifest(**kwargs, expected_hash=result["source_hash"])

    def test_external_issue_flow_refuses_project_state_and_wrong_role(self):
        result = task_inputs.manifest(entry="issue-report", role=None, mode="create")
        self.assertEqual(result["project_inputs"], [])
        with self.assertRaisesRegex(ValueError, "conversation inputs only"):
            task_inputs.manifest(entry="issue-report", role=None, mode="create", project=ROOT)
        with self.assertRaisesRegex(ValueError, "role does not belong"):
            task_inputs.manifest(entry="business-analysis", role="frontend-developer", mode="create")


@integration
class EpicTaskScopeTests(unittest.TestCase):
    """EP-001 holds ST-001; EP-002's writer still has to finish the stubs of ST-002."""

    ROLES = (("backlog-reviewer", "review"), ("product-owner", "revise"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        self.docs = self.root / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English",
        }), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            make_approved_backlog(self.docs, "ST-001")
            backlog_compile.stub_epic(SimpleNamespace(
                docs=str(self.docs), slug="second", id="EP-002", title="Second",
                goal="Deliver a separate customer outcome."))
            backlog_compile.stub_story(SimpleNamespace(
                docs=str(self.docs), epic="second", slug="st-002", id="ST-002", title=None,
                scope=None, work_kind="feature", criterion_ref=[CRITERION],
                experience_ref=[EXPERIENCE], evidence_ref=[], uses_design=[DESIGN],
                constrained_by=[CONSTRAINT]))
        init_repository(self.root)
        subprocess.run(["git", "-C", str(self.root), "config", "core.autocrlf", "false"],
                       check=True, capture_output=True)
        commit_all(self.root)

    def task(self, role, mode, epic="EP-001", **extra):
        return task_inputs.manifest(entry="backlog-plan", role=role, mode=mode,
                                    project=self.root, epic=epic, **extra)

    def story(self, slug):
        epic = "delivery-fixture" if slug == "st-001" else "second"
        return self.docs / f"backlog/epics/{epic}/stories/{slug}"

    def finish_second_story(self):
        """EP-002's writer replaces every placeholder stub-story wrote."""
        folder = self.story("st-002")
        _author_story(folder / "story.md", folder / "test-plan.md", "ST-002")

    def test_another_epics_writer_leaves_an_epic_task_fresh(self):
        tasks = {role: self.task(role, mode) for role, mode in self.ROLES}
        self.assertTrue(tasks["backlog-reviewer"]["backlog_scope"]["check"]["scaffold_findings"])
        # The root package, bare --epic, and an unscoped writer keep every source.
        whole = {"root": dict(role="product-owner", mode="revise", epic=""),
                 "unscoped": dict(role="product-owner", mode="revise", epic=None)}
        previous = {name: self.task(**kwargs) for name, kwargs in whole.items()}
        self.finish_second_story()
        aside = self.docs / "solution-design/aside.md"
        aside.write_text("---\ntype: note\n---\n\n# A source no closure reads\n", encoding="utf-8")
        outside = {self.story("st-002").relative_to(self.root).as_posix() + name
                   for name in ("/story.md", "/test-plan.md")} | {"workspace/docs/solution-design/aside.md"}
        for role, mode in self.ROLES:
            with self.subTest(role=role):
                fresh = self.task(role, mode, expected_hash=tasks[role]["source_hash"])
                closure = {"workspace/docs/" + path for path in fresh["backlog_scope"]["paths"]}
                self.assertLessEqual({record["path"] for record in fresh["canonical_source_inventory"]},
                                     closure)
                self.assertFalse(outside & {record["path"] for record in fresh["working_inputs"]})
                self.assertFalse(outside & set(fresh["changed_paths"]))
                self.assertFalse(fresh["backlog_scope"].get("check", {}).get("scaffold_findings"))
        for name, kwargs in whole.items():
            with self.subTest(scope=name):
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(**kwargs, expected_hash=previous[name]["source_hash"])
                current = self.task(**kwargs)
                self.assertEqual(current["context_inventory"], {
                    "count": len(task_inputs.source_inventory(self.root)),
                    "source_hash": task_inputs.digest(task_inputs.source_inventory(self.root))})

    def test_another_epics_partial_edit_leaves_an_epic_task_fresh(self):
        tasks = {role: self.task(role, mode) for role, mode in self.ROLES}
        # EP-002's writer replaces the coverage-row stubs and nothing else.
        plan = self.story("st-002") / "test-plan.md"
        text = plan.read_text(encoding="utf-8")
        for coverage in backlog_compile.SCENARIO_COVERAGE_CLASSES:
            text = text.replace(
                f"| {coverage} | not_applicable | - | {backlog_compile.COVERAGE_REASON_STUB} |",
                f"| {coverage} | not_applicable | - | ST-002 exposes no {coverage} behavior. |")
        plan.write_text(text, encoding="utf-8")
        unclassified = ("backlog/epics/second/stories/st-002/test-plan.md scenarios are not"
                        " classified by Coverage Classes: ST-002-TS-001")
        for role, mode in self.ROLES:
            with self.subTest(role=role):
                fresh = self.task(role, mode, expected_hash=tasks[role]["source_hash"])
                self.assertIn(unclassified, fresh["backlog_scope"]["check"]["scaffold_findings"])
        with self.assertRaisesRegex(ValueError, "not classified by Coverage Classes"):
            self.task("backlog-reviewer", "review", epic="EP-002")

    def test_a_change_inside_an_epic_tasks_closure_still_makes_it_stale(self):
        self.finish_second_story()
        commit_all(self.root)
        first = self.story("st-001") / "story.md"
        for role, mode in self.ROLES:
            with self.subTest(role=role, change="inside"):
                previous = self.task(role, mode)
                first.write_text(first.read_text(encoding="utf-8").replace(
                    "Administrative bulk operations", "Administrative batch operations"),
                    encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(role, mode, expected_hash=previous["source_hash"])
                subprocess.run(["git", "-C", str(self.root), "checkout", "--", "."],
                               check=True, capture_output=True)
        # ST-002 of EP-002 gains a dependency on ST-001: a new incoming edge
        # brings ST-002 into EP-001's closure, so every EP-001 task goes stale.
        previous = {role: self.task(role, mode) for role, mode in self.ROLES}
        path = self.story("st-002") / "story.md"
        props, body = backlog_compile.parse_front_matter(path)
        link = "[[backlog/epics/delivery-fixture/stories/st-001/story|ST-001]]"
        props["depends_on"] = [link]
        body = body.replace("## Dependencies\n\nNone.",
                            "## Dependencies\n\n- " + link + ": Supplies the account boundary.")
        path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        for role, mode in self.ROLES:
            with self.subTest(role=role, change="incoming edge"):
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.task(role, mode, expected_hash=previous[role]["source_hash"])
                current = self.task(role, mode)
                self.assertIn(path.relative_to(self.root).as_posix(),
                              {record["path"] for record in current["canonical_source_inventory"]})


@integration
class BuiltPackageTaskInputTests(unittest.TestCase):
    """A built host package derives tasks with its own scripts and agents."""

    def test_every_built_package_checks_its_catalog_and_derives_a_role_task(self):
        # The catalog reads the switch registry without importing its compiler.
        self.assertEqual(task_inputs.SWITCH_REGISTRY, process_policy.REGISTRY)
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import build_distributions as builder
        finally:
            sys.path.remove(str(ROOT / "tools"))
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "dist"
            builder.build(ROOT, output)
            for host in builder.HOSTS:
                package = output / host / "software-engineering-team"
                # The build writes each switch's agent variants beside their base agents.
                self.assertTrue((package / "agents/product-owner-mechanical.md").is_file())
                self.assertTrue((package / "agents/backlog-reviewer-lens.md").is_file())
                for argv in (["--check-catalog"],
                             ["--entry", "backlog-plan", "--role", "backlog-reviewer",
                              "--mode", "review"]):
                    with self.subTest(host=host, argv=argv):
                        result = subprocess.run(
                            [sys.executable, str(package / "scripts/task_inputs.py"), *argv],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotEqual(json.loads(result.stdout).get("ok"), False)
            # A variant is never a task role: its task is derived as its base role.
            script = output / "claude/software-engineering-team/scripts/task_inputs.py"
            refused = subprocess.run(
                [sys.executable, str(script), "--entry", "backlog-plan",
                 "--role", "product-owner-mechanical", "--mode", "revise"],
                capture_output=True, text=True, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(refused.returncode, 1)
            self.assertIn("derive its task as product-owner", json.loads(refused.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
