"""Gate 3 tests for the offline Delivery knowledge model."""

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
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import architecture_compile  # noqa: E402
import backlog_compile  # noqa: E402
import operation_compile  # noqa: E402
import stage_package  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402


WORKFLOW_JOBS = "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make test\n"
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}


class DeliveryCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.root / "workspace" / "config.json").write_text(
            json.dumps({
                "schema_version": 2,
                "team_id": "software-engineering-team",
                "output_language": "English",
                "terminology_language": "English",
            }), encoding="utf-8"
        )
        make_approved_backlog(self.docs)
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "tests.yml").write_text("on:\n  pull_request:\n" + WORKFLOW_JOBS, encoding="utf-8")
        # A checkout of its own keeps the workflow lookup inside this fixture.
        self.git("init", "-q", "-b", "main")
        self.commit_workflows()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args, cwd=None):
        subprocess.run(["git", "-C", str(cwd or self.root), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_IDENTITY})

    def commit_workflows(self, *paths):
        """Commit the fixture's workflow directories, as a project commits its CI."""
        self.git("add", "--all", "--", *(paths or (".github",)))
        self.git("commit", "-q", "--allow-empty", "-m", "workflows")

    def approve_dod(self):
        args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        self.assertEqual(delivery_compile.approve_dod(args), 0)

    def assert_delivery_vault_contract(self):
        self.maxDiff = None
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        vault = vault_check.build_vault(self.docs, policy)
        findings = []
        for check in (vault_check.check_frontmatter_props,
                      vault_check.check_nav_footer,
                      vault_check.check_wikilink_resolution,
                      vault_check.check_relation_contract):
            check(vault, findings)
        self.assertEqual(
            [finding for finding in findings if finding.path.startswith("delivery/")],
            [],
        )
        vault_check.materialize_payload(self.docs, policy, vault_check.DEFAULT_PAYLOAD)
        vault_check.reconcile_payload_fragment(self.docs, policy, "delivery")
        types = json.loads((self.docs / ".obsidian/types.json").read_text())["types"]
        for note in vault.notes.values():
            if note.rel.startswith("delivery/"):
                for key in note.fm:
                    self.assertEqual(types.get(key), policy["property_types"][key], key)

    def approve_verification_contract(self):
        """Create the smallest current Solution and Operation handoff chain."""
        decisions = self.docs / "solution-design" / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        decision = decisions / "fixture-api.md"
        ref = "solution-design/decisions/fixture-api"
        if not decision.exists():
            decision = decisions / "api.md"
            ref = "solution-design/decisions/api"
            decision.write_text(
                "---\n"
                "type: decision\nstatus: accepted\n"
                "decision_kind: technology-selection\n"
                "applies_to:\n  - api\n"
                "selected_technology: python-fastapi\n"
                "method_skills:\n  - python-fastapi\n"
                "---\n\n# API\n",
                encoding="utf-8",
            )
            landscape = self.docs / "solution-design" / "landscape.md"
            landscape.write_text("---\ntype: landscape\nstatus: approved\npackage_status: draft\n---\n\n# Landscape\n", encoding="utf-8")
            digest = stage_package.tree_hash(
                self.docs / "solution-design",
                {"package_hash", "package_status", "package_approved_at_utc"},
            )
            landscape.write_text(
                f"---\ntype: landscape\nstatus: approved\npackage_status: approved\npackage_hash: {digest}\n---\n\n# Landscape\n",
                encoding="utf-8",
            )
        args = type("Args", (), {
            "docs": str(self.docs), "kind": "verification", "constrained_by": [ref],
        })
        self.assertEqual(operation_compile.init(args), 0)
        path = self.docs / "operation" / "verification-contract.md"
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(operation_compile.approve(args), 0)

    def test_map_links_only_existing_global_documents_without_orphaning_them(self):
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        map_path = self.docs / "maps/delivery.md"
        home = self.docs / policy["home_file"]
        home.write_text("# Home\n\n[[maps/delivery|Delivery]]\n", encoding="utf-8")
        globals = ((delivery_governance.path_for(self.docs), "Governance"),
                   (self.docs / "delivery/definition-of-done.md", "Definition of Done"))

        def check_map(expected):
            before = {path: path.read_bytes() for path, _title in globals if path.is_file()}
            delivery_compile.render_map(self.docs)
            text = map_path.read_text(encoding="utf-8")
            for path, title in globals:
                link = delivery_compile.link(path.relative_to(self.docs).as_posix(), title)
                self.assertEqual(link in text, path in expected)
            vault = vault_check.build_vault(self.docs, policy)
            findings = []
            vault_check.check_wikilink_resolution(vault, findings)
            vault_check.check_orphans(vault, findings)
            vault_check.check_moc_coverage(vault, findings)
            selected = {"maps/delivery.md", *(path.relative_to(self.docs).as_posix() for path in expected)}
            self.assertEqual([finding for finding in findings if finding.path in selected], [])
            for path in expected:
                self.assertIn("maps/delivery.md", vault.inbound[path.relative_to(self.docs).as_posix()])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            delivery_compile.render_map(self.docs)
            self.assertEqual(map_path.read_text(encoding="utf-8"), text)

        check_map(set())
        self.assertEqual(delivery_governance.init(type("Args", (), {"docs": str(self.docs), "max_parallel": 1})), 0)
        check_map({globals[0][0]})
        self.approve_dod()
        check_map({path for path, _title in globals})
        globals[0][0].unlink()
        check_map({globals[1][0]})
        globals[1][0].unlink()
        check_map(set())

    def init_auth_delivery(self):
        self.approve_dod()
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                 "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)

    def test_map_render_is_byte_stable_and_ends_with_one_newline(self):
        """The map ends with one newline whether or not it lists a Delivery."""
        map_path = self.docs / "maps/delivery.md"

        def render_twice() -> str:
            delivery_compile.render_map(self.docs)
            first = map_path.read_bytes()
            delivery_compile.render_map(self.docs)
            self.assertEqual(map_path.read_bytes(), first)
            # Text mode folds a native CRLF ending, so a doubled ending shows on every OS.
            text = map_path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), text[-60:])
            return text

        self.assertNotIn("|DLV-001]]", render_twice())
        self.init_auth_delivery()
        self.assertIn("|DLV-001]]", render_twice())

    def test_relation_render_keeps_the_seeded_and_the_rendered_map(self):
        """vault_check render-relations ends every authored note with exactly one
        newline, so the governance seed and the renderer must already end that way."""
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        map_path = self.docs / "maps/delivery.md"

        def render_relations() -> str:
            before = map_path.read_bytes()
            self.assertEqual(vault_check.cmd_render_relations(type("Args", (), {"vault": self.docs}), policy), 0)
            self.assertEqual(map_path.read_bytes(), before)
            return map_path.read_text(encoding="utf-8")

        self.assertEqual(delivery_governance.init(type("Args", (), {"docs": str(self.docs), "max_parallel": 1})), 0)
        template = SCRIPTS.parent / "templates" / "vault" / "maps" / "delivery.md"
        # The seed is written in text mode, so compare text, not native line endings.
        self.assertEqual(render_relations(), template.read_text(encoding="utf-8"))
        self.init_auth_delivery()
        self.assertIn("|DLV-001]]", render_relations())

    def test_dod_bootstrap_approval_and_revision_keep_one_path(self):
        args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        self.assertEqual(delivery_compile.check_dod_cmd(args), 0)
        self.assertEqual(delivery_compile.approve_dod(args), 0)
        path = self.docs / "delivery" / "definition-of-done.md"
        before = delivery_compile.split_note(path)[0]["source_hash"]
        self.assertEqual(delivery_compile.begin_dod_revision(args), 0)
        props, _ = delivery_compile.split_note(path)
        self.assertEqual(props["status"], "draft")
        self.assertEqual(props["revision"], 2)
        self.assertNotEqual(props.get("source_hash"), before)

    def test_scope_producer_obeys_vault_schema_links_and_navigation(self):
        self.approve_dod()
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        self.assert_delivery_vault_contract()

    def test_backlog_source_validation_cache_is_scoped_to_one_read(self):
        self.approve_dod()
        original = backlog_compile.collect
        seen = []

        def read(*args, **kwargs):
            seen.append((stage_package._CANDIDATE_SESSION_STACK[-1],
                         backlog_compile._EXPERIENCE_APPLICATION_CACHE_STACK[-1]))
            return original(*args, **kwargs)

        with mock.patch.object(backlog_compile, "collect", side_effect=read):
            for _ in range(2):
                _sources, _snapshot, errors = delivery_compile.approved_backlog_sources(self.docs, ["AUTH-01"])
                self.assertEqual(errors, [])
        self.assertEqual(len(seen), 2)
        for candidate, experience in seen:
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(experience)
        self.assertIsNot(seen[0][0], seen[1][0])
        self.assertIsNot(seen[0][1], seen[1][1])

    def approved_execution_with_revised_contract(self):
        """Approve one Item, then approve a second revision of its contract."""
        self.approve_verification_contract()
        dod_args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod_args)
        delivery_compile.approve_dod(dod_args)
        init_args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": None,
                                      "goal": "SAML authentication", "outcome": "Users sign in",
                                      "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init_args), 0)
        plan_args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(plan_args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-saml-authentication"
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        bound, _ = delivery_compile.split_note(item)
        first = bound["verification_contract_hash"]

        contract_args = type("Args", (), {"docs": str(self.docs), "kind": "verification",
                                          "constrained_by": None, "json": False})
        self.assertEqual(operation_compile.revise(contract_args), 0)
        contract = self.docs / "operation" / "verification-contract.md"
        contract_props, contract_body = operation_compile.parse(contract)
        contract_props["test_command"] = "make test --tiered"
        operation_compile.atomic_text(
            contract, operation_compile.render(contract_props, contract_body))
        self.assertEqual(operation_compile.approve(contract_args), 0)
        second = operation_compile.parse(contract)[0]["source_hash"]
        self.assertNotEqual(second, first)
        return root, item, plan_args, first, second

    def test_a_terminal_item_releases_its_path_claim_to_a_later_item(self):
        """A closed Item records what it wrote; it does not reserve it forever."""
        root, item, plan_args, _first, _second = self.approved_execution_with_revised_contract()
        props, body = delivery_compile.split_note(item)
        approved, _snapshot, errors = delivery_compile.approved_backlog_sources(
            self.docs, [props["story_id"]])
        self.assertEqual(errors, [])
        sources = dict(approved)

        successor = root / "items" / "auth-02" / "item.md"
        successor.parent.mkdir(parents=True, exist_ok=True)
        later = dict(props, story_id="AUTH-02", status="in_scope")
        later["path_claims"] = ["src/auth.py"]
        later["execution_after"] = [props["story_id"]]
        later["depends_on"] = [props["story_id"]]
        delivery_compile.atomic_text(successor,
                                     delivery_compile.frontmatter(later, body))
        sources["AUTH-02"] = dict(approved[props["story_id"]],
                                  depends_on=[props["story_id"]])

        def overlaps():
            findings = delivery_compile.execution_plan_findings(root, sources, self.docs)
            return [f for f in findings if "overlaps" in f]

        # While the first Item is open, the same path may not be claimed twice.
        assert overlaps(), "an open Item must still reserve its claimed paths"

        # Once it is closed there is no writer left to protect, so the path is free.
        closed = dict(props, status="integrated")
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(closed, body))
        self.assertEqual(overlaps(), [])

    def test_terminal_item_keeps_the_contract_revision_it_was_verified_against(self):
        root, item, plan_args, first, second = self.approved_execution_with_revised_contract()
        props, body = delivery_compile.split_note(item)
        props["status"] = "integrated"
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

        # The only verb that rewrites the binding must not be gated on it.
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        rebound, _ = delivery_compile.split_note(item)
        self.assertEqual(rebound["status"], "integrated")
        self.assertEqual(rebound["verification_contract_hash"], first)
        _root, findings = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertEqual(findings, [])

    def test_reapproval_rebinds_a_sealed_item_only_when_named_for_reopen(self):
        root, item, _plan_args, first, second = self.approved_execution_with_revised_contract()
        props, body = delivery_compile.split_note(item)
        props["status"] = "integrated"
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        before, _ = delivery_compile.split_note(item)

        outside = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001", "reopen": ["AUTH-02"]})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(delivery_compile.approve_execution(outside), 1)
        self.assertIn("reopen names a Story outside this Delivery: AUTH-02", output.getvalue())
        unchanged, _ = delivery_compile.split_note(item)
        self.assertEqual(unchanged["verification_contract_hash"], first)

        named = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001", "reopen": ["AUTH-01"]})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(delivery_compile.approve_execution(named), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["rebound"], ["AUTH-01"])
        self.assertEqual(result["refreshed_sources"], [])
        rebound, _ = delivery_compile.split_note(item)
        self.assertEqual(rebound["status"], "integrated")
        self.assertEqual(rebound["verification_contract_hash"], second)
        self.assertNotEqual(rebound["item_plan_hash"], before["item_plan_hash"])
        plan_props, _ = delivery_compile.split_note(root / "execution-plan.md")
        self.assertIn(f"AUTH-01:{rebound['item_plan_hash']}", plan_props["item_plan_hashes"])
        _root, findings = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertEqual(findings, [])

    def test_reopen_flag_requires_an_integrated_item(self):
        _root, item, _plan_args, first, _second = self.approved_execution_with_revised_contract()
        named = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001", "reopen": ["AUTH-01"]})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(delivery_compile.approve_execution(named), 1)
        self.assertIn("reopen requires an integrated Item: AUTH-01", output.getvalue())
        unchanged, _ = delivery_compile.split_note(item)
        self.assertEqual(unchanged["verification_contract_hash"], first)

    def test_reapproval_refreshes_stale_story_and_backlog_pins(self):
        root, item, plan_args, first, _second = self.approved_execution_with_revised_contract()
        props, body = delivery_compile.split_note(item)
        props["status"] = "integrated"
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

        # A later approved backlog revision changes the Story the sealed Item was planned from.
        story = self.docs / props["story_path"]
        story_props, story_body = backlog_compile.parse_front_matter(story)
        revised_body = story_body.replace(
            "Preserve the approved API boundary and avoid delivery-state metadata.",
            "Preserve the approved API boundary, cover the session scenario and avoid delivery-state metadata.")
        self.assertNotEqual(revised_body, story_body)
        story.write_text(backlog_compile.front_matter(story_props, revised_body), encoding="utf-8")
        story_props["source_hash"] = backlog_compile.digest(story)
        story.write_text(backlog_compile.front_matter(story_props, revised_body), encoding="utf-8")
        record, errors = backlog_compile.collect(self.docs)
        self.assertEqual(errors, [])
        backlog = self.docs / "backlog" / "backlog.md"
        backlog_props, backlog_body = backlog_compile.parse_front_matter(backlog)
        backlog_props["package_hash"] = backlog_compile.package_digest(
            self.docs, backlog_compile.package_paths(record, self.docs))
        backlog.write_text(backlog_compile.front_matter(backlog_props, backlog_body), encoding="utf-8")
        _root, stale = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertTrue(any("story_source_hash is stale" in finding for finding in stale), stale)
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog", stale)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["refreshed_sources"], ["AUTH-01"])
        self.assertEqual(result["refreshed_delivery_pins"], ["backlog_package_hash"])
        self.assertEqual(result["rebound"], [])
        refreshed, _ = delivery_compile.split_note(item)
        self.assertEqual(refreshed["status"], "integrated")
        self.assertEqual(refreshed["story_source_hash"], story_props["source_hash"])
        self.assertEqual(refreshed["verification_contract_hash"], first)
        delivery_props, _ = delivery_compile.split_note(root / "delivery.md")
        self.assertEqual(delivery_props["backlog_package_hash"], backlog_props["package_hash"])
        _root, findings = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertEqual(findings, [])

    def test_open_item_rebinds_to_the_new_revision_and_still_reports_staleness(self):
        root, item, plan_args, first, second = self.approved_execution_with_revised_contract()
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        rebound, body = delivery_compile.split_note(item)
        self.assertEqual(rebound["status"], "in_scope")
        self.assertEqual(rebound["verification_contract_hash"], second)
        _root, findings = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertEqual(findings, [])

        rebound["verification_contract_hash"] = "sha256:" + "0" * 64
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(rebound, body))
        _root, stale = delivery_compile.delivery_findings(self.docs, "DLV-001")
        self.assertTrue(any("Verification Contract binding" in finding for finding in stale), stale)

    def test_scope_then_execution_creates_exact_item_evidence_files(self):
        self.approve_verification_contract()
        dod_args = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod_args)
        delivery_compile.approve_dod(dod_args)
        init_args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": None,
                                       "goal": "SAML authentication", "outcome": "Users sign in",
                                       "target_branch": "main", "story": ["AUTH-01"],
                                       })
        self.assertEqual(delivery_compile.init_delivery(init_args), 0)
        plan_args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(plan_args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-saml-authentication"
        delivery_props, _ = delivery_compile.split_note(root / "delivery.md")
        self.assertEqual(
            delivery_props["title"], "Delivery scope for SAML authentication"
        )
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        self.assertEqual(props["title"], "Implementation work for AUTH-01")
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(plan_args), 0)
        self.assertTrue((root / "execution-plan.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "code-review.md").exists())
        self.assertTrue((root / "items" / "auth-01" / "verification.md").exists())
        plan_props, _ = delivery_compile.split_note(root / "execution-plan.md")
        review_props, _ = delivery_compile.split_note(
            root / "items" / "auth-01" / "code-review.md"
        )
        verification_props, _ = delivery_compile.split_note(
            root / "items" / "auth-01" / "verification.md"
        )
        self.assertEqual(
            plan_props["title"], "Execution approach for SAML authentication"
        )
        self.assertEqual(
            review_props["title"], "Implementation review for AUTH-01"
        )
        self.assertEqual(
            verification_props["title"], "Verification evidence for AUTH-01"
        )
        self.assertEqual(delivery_compile.check_delivery(plan_args), 0)
        self.assert_delivery_vault_contract()

    def scope_ready_for_execution(self):
        """Return approval arguments for one scope-approved Delivery with a claimed Item."""
        self.approve_verification_contract()
        self.approve_dod()
        init_args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                      "goal": "Authenticate", "outcome": None,
                                      "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init_args), 0)
        plan_args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(plan_args), 0)
        item = delivery_compile.find_delivery(self.docs, "DLV-001") / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        return plan_args

    def approve_execution_result(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery_compile.approve_execution(args)
        return code, json.loads(output.getvalue())

    def delivery_bytes(self):
        root = delivery_compile.find_delivery(self.docs, "DLV-001")
        return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def test_execution_approval_refuses_without_workflows_until_render_ci_adds_one(self):
        plan_args = self.scope_ready_for_execution()
        shutil.rmtree(self.root / ".github")
        self.commit_workflows()
        before = self.delivery_bytes()
        code, result = self.approve_execution_result(plan_args)
        self.assertEqual(code, 1)
        [error] = result["errors"]
        self.assertIn("triggered by pull_request or pull_request_target", error)
        self.assertIn("none is committed in HEAD.", error)
        self.assertIn("operation_compile.py render-ci", error)
        self.assertIn("skill-content/setup/references/ci-bootstrap.md", error)
        self.assertTrue(error.endswith("then commit it"), error)
        self.assertEqual(self.delivery_bytes(), before)

        render = type("Args", (), {"docs": str(self.docs), "template": None, "include_environment": False,
                                   "output": str(self.root / ".github" / "workflows" / "tests.yml")})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(operation_compile.render_ci(render), 0)
        # render-ci only writes the file; the Delivery PR carries what is committed.
        self.assertEqual(self.approve_execution_result(plan_args)[0], 1)
        self.commit_workflows()
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)

    def test_execution_approval_reads_committed_workflows_not_the_working_tree(self):
        plan_args = self.scope_ready_for_execution()
        workflows = self.root / ".github" / "workflows"
        (workflows / "tests.yml").write_text("on: push\n" + WORKFLOW_JOBS, encoding="utf-8")
        self.commit_workflows()
        for state in ("untracked", "staged", "modified"):
            with self.subTest(state=state):
                if state == "untracked":
                    (workflows / "pull-request.yml").write_text("on: pull_request\n" + WORKFLOW_JOBS, encoding="utf-8")
                elif state == "staged":
                    self.git("add", "--", ".github")
                else:
                    self.git("rm", "-q", "--cached", "--", ".github/workflows/pull-request.yml")
                    (workflows / "pull-request.yml").unlink()
                    (workflows / "tests.yml").write_text("on: pull_request\n" + WORKFLOW_JOBS, encoding="utf-8")
                code, result = self.approve_execution_result(plan_args)
                self.assertEqual(code, 1)
                self.assertTrue(any("none is committed in HEAD" in error for error in result["errors"]), result)
        self.commit_workflows()
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)
        # The committed trigger still counts while the working tree edits it away.
        (workflows / "tests.yml").write_text("on: push\n" + WORKFLOW_JOBS, encoding="utf-8")
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)

    def test_execution_approval_refuses_workflows_that_pull_requests_do_not_trigger(self):
        plan_args = self.scope_ready_for_execution()
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        for trigger in ("on: push\n", "on: [push, workflow_dispatch]\n",
                        "on:\n  push:\n    branches: [main]\n",
                        "on:\n  push:\n  # pull_request:\n",
                        "on:\n  workflow_dispatch:\n    inputs:\n      pull_request:\n        type: string\n"):
            with self.subTest(trigger=trigger):
                workflow.write_text(trigger + WORKFLOW_JOBS, encoding="utf-8")
                self.commit_workflows()
                code, result = self.approve_execution_result(plan_args)
                self.assertEqual(code, 1)
                self.assertTrue(any("render-ci" in error for error in result["errors"]), result)

    def test_execution_approval_evaluates_pull_request_activity_types(self):
        plan_args = self.scope_ready_for_execution()
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        # This repository's release workflow uses the first form; it runs only when a PR closes.
        # Opening and reopening check earlier heads than the one that merge-pr reads.
        for trigger, expected in (("on:\n  pull_request_target:\n    types: [closed]\n", 1),
                                  ("on:\n  pull_request_target:\n    types: [opened, reopened]\n", 1),
                                  ("on:\n  pull_request_target:\n    types: [closed, synchronize]\n", 0)):
            with self.subTest(trigger=trigger):
                workflow.write_text(trigger + WORKFLOW_JOBS, encoding="utf-8")
                self.commit_workflows()
                self.assertEqual(self.approve_execution_result(plan_args)[0], expected)
        forms = {
            "inline list of other types": ("on:\n  pull_request:\n    types: [closed, labeled]\n", set()),
            "inline list with a counted type": ("on:\n  pull_request:\n    types: [labeled, synchronize]\n", {"pull_request"}),
            "quoted inline list": ("on:\n  pull_request:\n    types: [\"closed\", 'edited']\n", set()),
            "scalar of another type": ("on:\n  pull_request:\n    types: closed\n", set()),
            "counted scalar": ("on:\n  pull_request:\n    types: synchronize\n", {"pull_request"}),
            "block list of other types": ("on:\n  pull_request:\n    types:\n      - closed\n      - labeled\n", set()),
            "block list with a counted type": ("on:\n  pull_request:\n    types:\n      - labeled\n      - synchronize\n", {"pull_request"}),
            "block list of earlier heads": ("on:\n  pull_request:\n    types:\n      - opened\n      - reopened\n", set()),
            "compact block list": ("on:\n  pull_request:\n    types:\n    - closed\n    branches: [main]\n", set()),
            "types after a filter": ("on:\n  pull_request:\n    branches: [main]\n    types: [closed]\n", set()),
            "flow mapping of other types": ("on:\n  pull_request: {types: [closed], branches: [main]}\n", set()),
            "flow mapping without types": ("on:\n  pull_request: {branches: [main]}\n", {"pull_request"}),
            "one event of two counts": ("on:\n  pull_request_target:\n    types: [closed]\n  pull_request:\n", {"pull_request"}),
        }
        for name, (trigger, expected) in forms.items():
            with self.subTest(form=name):
                self.assertEqual(delivery_compile.pull_request_triggers(trigger + WORKFLOW_JOBS), expected)

    def publish(self):
        """Give the fixture a remote whose main carries the committed workflow."""
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", "main")
        return remote

    def test_execution_approval_reads_the_target_remote_tracking_ref_instead_of_head(self):
        plan_args = self.scope_ready_for_execution()
        remote = self.publish()
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        # A removal that is not pushed leaves the workflow on the target the PR merges into.
        shutil.rmtree(self.root / ".github")
        self.commit_workflows()
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)
        self.git("push", "-q", "origin", "main")
        code, result = self.approve_execution_result(plan_args)
        self.assertEqual(code, 1)
        [error] = result["errors"]
        self.assertIn("none is committed in refs/remotes/origin/main.", error)
        self.assertTrue(error.endswith("commit it, push it to main on origin and fetch"), error)
        # A commit that is not pushed is not on the target yet.
        workflow.parent.mkdir(parents=True)
        workflow.write_text("on: pull_request\n" + WORKFLOW_JOBS, encoding="utf-8")
        self.commit_workflows()
        self.assertEqual(self.approve_execution_result(plan_args)[0], 1)
        self.git("reset", "-q", "--hard", "origin/main")
        # A workflow merged on the provider, here pushed from another clone, counts after a fetch.
        clone = self.root / "clone"
        subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
        (clone / ".github" / "workflows").mkdir(parents=True)
        (clone / ".github" / "workflows" / "tests.yml").write_text("on: pull_request\n" + WORKFLOW_JOBS, encoding="utf-8")
        self.git("add", "--all", "--", ".github", cwd=clone)
        self.git("commit", "-q", "-m", "Add the pull request workflow", cwd=clone)
        self.git("push", "-q", "origin", "main", cwd=clone)
        self.assertEqual(self.approve_execution_result(plan_args)[0], 1)
        self.git("fetch", "-q", "origin")
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)

    def test_execution_approval_reads_pull_request_triggers_on_the_integration_ref(self):
        plan_args = self.scope_ready_for_execution()
        self.publish()
        shutil.rmtree(self.root / ".github")
        self.commit_workflows()
        self.git("push", "-q", "origin", "main")
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        # Integration carries a workflow the target lacks, as when an integrated Item added CI.
        # The PR merge commit holds it for pull_request; pull_request_target reads the default branch.
        for trigger, expected in (("on: pull_request_target\n", 1), ("on: pull_request\n", 0)):
            with self.subTest(trigger=trigger):
                workflow.parent.mkdir(parents=True, exist_ok=True)
                workflow.write_text(trigger + WORKFLOW_JOBS, encoding="utf-8")
                self.commit_workflows()
                self.git("push", "-q", "--force", "origin", "HEAD:refs/heads/agentrof/deliveries/dlv-001")
                self.git("reset", "-q", "--hard", "origin/main")
                code, result = self.approve_execution_result(plan_args)
                self.assertEqual(code, expected)
                if expected:
                    self.assertTrue(any(
                        "none is committed in refs/remotes/origin/main or "
                        "refs/remotes/origin/agentrof/deliveries/dlv-001." in error
                        for error in result["errors"]), result)

    def test_execution_approval_reads_the_working_tree_only_outside_a_git_checkout(self):
        if any((parent / ".git").exists() for parent in self.root.parents):
            self.skipTest("the temporary directory lies inside a Git checkout")
        plan_args = self.scope_ready_for_execution()
        shutil.rmtree(self.root / ".git")
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)
        (self.root / ".github" / "workflows" / "tests.yml").write_text("on: push\n" + WORKFLOW_JOBS, encoding="utf-8")
        code, result = self.approve_execution_result(plan_args)
        self.assertEqual(code, 1)
        self.assertTrue(any("outside a Git checkout" in error for error in result["errors"]), result)

    def test_pull_request_triggers_read_each_trigger_form(self):
        forms = {
            "scalar": "on: {event}\n",
            "inline list": "on: [push, {event}]\n",
            "quoted inline list": "on: [ \"push\", '{event}' ]\n",
            "mapping": "on:\n  push:\n    branches: [main]\n  {event}:\n    branches: [main]\n",
            "quoted keys": "\"on\":\n  '{event}':\n",
            "trailing comment": "on: {event}  # every change\n",
            "mapping comments": "on:  # triggers\n  push:  # pushes\n  {event}:  # reviews\n",
            "block list": "on:\n  - push\n  - {event}\n",
            "compact block list": "on:\n- push\n- {event}\n",
        }
        # The same form naming pull_request_review must be refused, which proves the form is parsed.
        for name, form in forms.items():
            for event, expected in (("pull_request", {"pull_request"}),
                                    ("pull_request_target", {"pull_request_target"}),
                                    ("pull_request_review", set())):
                with self.subTest(form=name, event=event):
                    self.assertEqual(delivery_compile.pull_request_triggers(
                        form.format(event=event) + WORKFLOW_JOBS), expected)

    def test_execution_approval_reads_workflows_at_the_git_checkout_root(self):
        # GitHub reads workflows only at the checkout root, above a project in a subdirectory.
        product = self.root / "product"
        product.mkdir()
        (self.root / "workspace").rename(product / "workspace")
        self.docs = product / "workspace" / "docs"
        plan_args = self.scope_ready_for_execution()
        (self.root / ".github").rename(product / ".github")
        self.commit_workflows(".github", "product/.github")
        self.assertEqual(self.approve_execution_result(plan_args)[0], 1)
        (product / ".github").rename(self.root / ".github")
        self.commit_workflows(".github", "product/.github")
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)

    def test_execution_reapproval_rechecks_the_pull_request_workflow(self):
        plan_args = self.scope_ready_for_execution()
        self.assertEqual(self.approve_execution_result(plan_args)[0], 0)
        shutil.rmtree(self.root / ".github")
        self.commit_workflows()
        before = self.delivery_bytes()
        code, result = self.approve_execution_result(plan_args)
        self.assertEqual(code, 1)
        self.assertTrue(any("render-ci" in error for error in result["errors"]), result)
        self.assertEqual(self.delivery_bytes(), before)

    def execution_topology_fixture(self):
        self.approve_verification_contract()
        self.approve_dod()
        args = type("Args", (), {
            "docs": str(self.docs), "id": None, "slug": "auth", "goal": "Authenticate",
            "outcome": None, "target_branch": "main", "story": ["AUTH-01"],
        })
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        root = delivery_compile.find_delivery(self.docs, "DLV-001")
        item = root / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(item)
        props.update({
            "architecture_impact": "required", "architecture_components": ["api-tools"],
            "architecture_record_kinds": ["runtime-view"],
            "architecture_reason": "Declare the application runtime and shared development harness.",
            "path_claims": ["workspace/apps/api-tools"],
        })
        sources, _snapshot, findings = delivery_compile.approved_backlog_sources(self.docs, ["AUTH-01"])
        self.assertEqual(findings, [])
        props["role_sequence"] = delivery_compile.execution_roles(sources["AUTH-01"], True)
        components = {
            "api": {"sourcing": "build", "code_path": "workspace/apps/api"},
            "api-tools": {"sourcing": "build", "code_path": "workspace/apps/api-tools"},
            "database": {"sourcing": "managed"},
        }
        return root, item, props, body, sources, components

    def test_architect_role_moves_first_once_and_preserves_other_source_roles(self):
        root, item, props, body, sources, components = self.execution_topology_fixture()
        backlog_before = {path: path.read_bytes() for path in (self.docs / "backlog").rglob("*") if path.is_file()}
        for supporters in ([], ["software_architect"],
                           ["frontend_developer", "software_architect", "devops_engineer"]):
            with self.subTest(supporters=supporters):
                source = {**sources["AUTH-01"], "supporting_roles": supporters}
                selected = {"AUTH-01": source}
                before = json.dumps(selected, sort_keys=True)
                expected = ["software_architect", source["owner_role"],
                            *(role for role in supporters if role != "software_architect"),
                            "code_reviewer", "qa_engineer"]
                self.assertEqual(delivery_compile.execution_roles(source, True), expected)
                self.assertEqual(delivery_compile.execution_roles(source),
                                 [source["owner_role"], *supporters, "code_reviewer", "qa_engineer"])
                props["role_sequence"] = expected
                delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                with mock.patch.object(architecture_compile, "solution_components", return_value=components):
                    self.assertEqual(delivery_compile.execution_plan_findings(root, selected, self.docs), [])
                self.assertEqual(json.dumps(selected, sort_keys=True), before)
        self.assertEqual({path: path.read_bytes() for path in (self.docs / "backlog").rglob("*") if path.is_file()}, backlog_before)

    def test_execution_roles_reject_wrong_order_unknown_missing_and_duplicate_roles(self):
        root, item, props, body, sources, components = self.execution_topology_fixture()
        sources["AUTH-01"]["supporting_roles"] = ["software_architect", "devops_engineer"]
        expected = ["software_architect", sources["AUTH-01"]["owner_role"],
                    "devops_engineer", "code_reviewer", "qa_engineer"]
        invalid = [
            [expected[1], expected[0], *expected[2:]],
            [*expected[:2], "unknown_role", *expected[2:]],
            [*expected[:2], *expected[3:]],
            [*expected[:2], "software_architect", *expected[2:]],
            [*expected[:-2], "qa_engineer", "code_reviewer"],
        ]
        with mock.patch.object(architecture_compile, "solution_components", return_value=components):
            for roles in invalid:
                with self.subTest(roles=roles):
                    props["role_sequence"] = roles
                    delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                    findings = delivery_compile.execution_plan_findings(root, sources, self.docs)
                    self.assertTrue(any("role_sequence must be" in finding for finding in findings), findings)
                    if len(roles) != len(set(roles)):
                        self.assertTrue(any("duplicate roles" in finding for finding in findings), findings)

    def test_execution_approval_accepts_shared_paths_without_rebinding_approved_scope(self):
        root, item, props, body, _sources, components = self.execution_topology_fixture()
        args = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(args), 0)
        scope_hash = delivery_compile.split_note(root / "delivery.md")[0]["scope_hash"]
        backlog_before = {path: path.read_bytes() for path in (self.docs / "backlog").rglob("*") if path.is_file()}
        props["path_claims"] = ["workspace/apps/api-tools", "tests/platform_runtime", "dev", "Makefile"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        with mock.patch.object(architecture_compile, "solution_components", return_value=components):
            self.assertEqual(delivery_compile.approve_execution(args), 0)
        approved, _ = delivery_compile.split_note(root / "delivery.md")
        self.assertEqual(approved["status"], "execution_approved")
        self.assertEqual(approved["scope_hash"], scope_hash)
        self.assertEqual({path: path.read_bytes() for path in (self.docs / "backlog").rglob("*") if path.is_file()}, backlog_before)
        self.assertIn("tests/platform_runtime", (root / "execution-plan.md").read_text(encoding="utf-8"))

    def test_crosscutting_claims_preserve_unselected_component_and_normalization_guards(self):
        root, item, props, body, sources, components = self.execution_topology_fixture()
        with mock.patch.object(architecture_compile, "solution_components", return_value=components):
            for selected, paths in (
                    (["api-tools"], ["workspace/apps/api-tools", "tests/platform_runtime", "Makefile", "dev", "workspace/environment"]),
                    (["database"], ["tests/integration", "workspace/environment"]),
                    (["api", "api-tools"], ["workspace/apps"])):
                with self.subTest(selected=selected, paths=paths):
                    props["architecture_components"] = selected
                    props["path_claims"] = paths
                    delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                    self.assertEqual(delivery_compile.execution_plan_findings(root, sources, self.docs), [])
            for selected, path in ((["api-tools"], "workspace/apps/api/main.py"),
                                   (["api-tools"], "workspace/apps"),
                                   (["database"], "workspace/apps/api-tools")):
                with self.subTest(selected=selected, forbidden=path):
                    props["architecture_components"] = selected
                    props["path_claims"] = [path]
                    delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                    findings = delivery_compile.execution_plan_findings(root, sources, self.docs)
                    self.assertTrue(any("unselected built component" in finding for finding in findings), findings)
            props["architecture_components"] = ["api-tools"]
            for path in (".", "..", "/tmp/source", "../source", "dev/../source", "dev//source", "dev/./source", "dev\\source", "C:/source"):
                with self.subTest(unsafe=path):
                    props["path_claims"] = [path]
                    delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                    findings = delivery_compile.execution_plan_findings(root, sources, self.docs)
                    self.assertTrue(any("not normalized" in finding for finding in findings), findings)
            for path in ("", "dev/\x00source", "dev/\nsource"):
                self.assertFalse(delivery_compile._is_normalized_claim(path))

    def test_item_path_claims_reject_exact_and_ancestor_overlap(self):
        root, item, props, body, sources, components = self.execution_topology_fixture()
        second = root / "items/auth-02/item.md"
        sources["AUTH-02"] = {**sources["AUTH-01"], "story_id": "AUTH-02"}
        with mock.patch.object(architecture_compile, "solution_components", return_value=components):
            for first_path, second_path, collision in (
                    ("dev", "dev", True), ("dev", "dev/run.py", True),
                    ("dev/run.py", "dev", True), ("dev/runtime", "dev/runtime-tools", False)):
                with self.subTest(first=first_path, second=second_path):
                    props["path_claims"] = [first_path]
                    delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
                    delivery_compile.atomic_text(second, delivery_compile.frontmatter(
                        {**props, "story_id": "AUTH-02", "path_claims": [second_path]}, body))
                    findings = delivery_compile.execution_plan_findings(root, sources, self.docs)
                    if collision:
                        self.assertTrue(any("owned by both" in finding for finding in findings), findings)
                    else:
                        self.assertEqual(findings, [])

    def test_no_timebox_or_runtime_coordination_fields_are_generated(self):
        self.approve_dod()
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "small-change",
                                  "goal": "Small change", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        delivery_compile.init_delivery(args)
        props, _ = delivery_compile.split_note(
            self.docs / "delivery" / "deliveries" / "dlv-001-small-change" / "delivery.md"
        )
        for forbidden in ("duration", "estimate", "velocity", "slot", "worktree", "assignee"):
            self.assertNotIn(forbidden, props)

    def test_scope_resolves_exact_approved_story_and_test_plan_hashes(self):
        self.approve_dod()
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        root = self.docs / "delivery" / "deliveries" / "dlv-001-auth"
        item_props, _ = delivery_compile.split_note(root / "items" / "auth-01" / "item.md")
        source_story = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        source_test = self.docs / "backlog/epics/delivery-fixture/stories/auth-01/test-plan.md"
        import backlog_compile
        self.assertEqual(item_props["story_source_hash"], backlog_compile.digest(source_story))
        self.assertEqual(item_props["test_plan_source_hash"], backlog_compile.digest(source_test))
        self.assertNotEqual(item_props["story_source_hash"], "pending")
        source_story.write_text(source_story.read_text(encoding="utf-8") + "\nChanged after scope proposal.\n", encoding="utf-8")
        scope = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 1)

    def test_existing_delivery_uses_historical_backlog_inputs_but_new_scope_is_strict(self):
        self.approve_dod()
        original_collect = delivery_compile.backlog_compile.collect
        calls: list[bool] = []
        application_advanced = False

        def collect_with_application_revision(docs, *, historical_inputs=False):
            calls.append(historical_inputs)
            record, findings = original_collect(
                docs, historical_inputs=historical_inputs,
            )
            if application_advanced and not historical_inputs:
                findings = [*findings, "pinned application receipt is not current"]
            return record, findings

        first = type("Args", (), {
            "docs": str(self.docs), "id": None, "slug": "auth",
            "goal": "Authenticate", "outcome": None,
            "target_branch": "main", "story": ["AUTH-01"],
        })
        with mock.patch.object(
            delivery_compile.backlog_compile,
            "collect",
            side_effect=collect_with_application_revision,
        ):
            self.assertEqual(delivery_compile.init_delivery(first), 0)
            self.assertFalse(calls[-1])

            # This models an application-only r1 -> r2 change: the approved
            # backlog package and selected Story/Test Plan bytes are unchanged,
            # but its Experience input is now historical.
            application_advanced = True
            existing = type("Args", (), {
                "docs": str(self.docs), "delivery": "DLV-001",
            })
            self.assertEqual(delivery_compile.check_delivery(existing), 0)
            self.assertTrue(calls[-1])

            second = type("Args", (), {
                "docs": str(self.docs), "id": None, "slug": "auth-next",
                "goal": "Authenticate next", "outcome": None,
                "target_branch": "main", "story": ["AUTH-01"],
            })
            self.assertEqual(delivery_compile.init_delivery(second), 2)
            self.assertFalse(calls[-1])

    def test_review_rejects_non_git_or_missing_review_baselines(self):
        self.approve_dod()
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                   "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                   "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        review = type("Args", (), {
            "docs": str(self.docs), "delivery": "DLV-001",
            "reviewed_commit": "not-a-git-oid", "reviewed_integration_commit": "none",
        })
        self.assertEqual(delivery_compile.approve_review(review), 2)

    def test_review_approval_stamps_the_delivery_status_mirror(self):
        self.approve_dod()
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                   "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                   "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        review = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001",
                                     "reviewed_commit": "a" * 40, "reviewed_integration_commit": "b" * 40})
        self.assertEqual(delivery_compile.approve_review(review), 0)
        props, body = delivery_compile.split_note(delivery_compile.find_delivery(self.docs, "DLV-001") / "delivery.md")
        self.assertEqual(props["status"], "review")
        self.assertEqual(set(props["tags"]), {"doc/delivery", "status/review"})
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))

    def test_review_approval_keeps_the_authored_sections(self):
        self.approve_dod()
        init = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                   "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                   "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        root = delivery_compile.find_delivery(self.docs, "DLV-001")
        review_path = root / "delivery-review.md"
        review = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001",
                                     "reviewed_commit": "a" * 40, "reviewed_integration_commit": "b" * 40})
        navigation = delivery_compile.link(str((root / "delivery.md").relative_to(self.docs)), "DLV-001")
        # Without an authored draft the approval writes the template it always wrote.
        self.assertEqual(delivery_compile.approve_review(review), 0)
        props, template = delivery_compile.split_note(review_path)
        self.assertEqual(template.rstrip(), delivery_compile.body_for("delivery-review", props["title"], {
            "Goal Outcome": "Authenticate", "Verdict": "Approved for PR handoff.", "Navigation": navigation}).rstrip())
        authored = {"Scope Disposition": "AUTH-01 delivered as planned.",
                    "Deviations": "The owner added session expiry on 2026-01-01.",
                    "Lessons and Follow-up": "Rotate the fixture keys.",
                    "Verdict": "Approved for PR handoff with one follow-up."}
        draft = delivery_compile.body_for("delivery-review", "Draft review", {
            **authored, "Findings": delivery_compile.SECTION_PLACEHOLDER, "Navigation": "[[elsewhere|Elsewhere]]"})
        review_path.write_text(delivery_compile.frontmatter({"type": "delivery-review", "status": "draft"}, draft),
                               encoding="utf-8")
        self.assertEqual(delivery_compile.approve_review(review), 0)
        props, body = delivery_compile.split_note(review_path)
        sections = delivery_compile.section_bodies(body)
        for title, text in authored.items():
            self.assertEqual(sections[title], text)
        self.assertEqual(sections["Goal Outcome"], "Authenticate")
        self.assertEqual(sections["Findings"], delivery_compile.SECTION_PLACEHOLDER)
        self.assertIn(navigation, sections["Navigation"])
        self.assertNotIn("Elsewhere", sections["Navigation"])
        self.assertEqual(props["status"], "approved")
        self.assertEqual(props["approval_hash"], delivery_compile.content_hash(
            props, body, exclude=delivery_compile.MUTABLE | {"approval_hash"}))

    def test_scope_rejects_unknown_story_and_execution_rejects_unclaimed_topology(self):
        self.approve_dod()
        unknown = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "unknown",
                                     "goal": "Unknown", "outcome": None, "target_branch": "main",
                                     "story": ["UNKNOWN-01"]})
        self.assertEqual(delivery_compile.init_delivery(unknown), 2)
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": None, "target_branch": "main",
                                  "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(args), 0)
        scope = type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        self.assertEqual(delivery_compile.approve_execution(scope), 1)


if __name__ == "__main__":
    unittest.main()
