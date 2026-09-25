"""Gate 3 tests for the offline Delivery knowledge model."""

from __future__ import annotations

import contextlib
import io
import json
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
import design_system_compile  # noqa: E402
import experience_application_check  # noqa: E402
import operation_compile  # noqa: E402
import requirement_compile  # noqa: E402
import requirement_route  # noqa: E402
import stage_package  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import CRITERION, make_approved_backlog  # noqa: E402


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

    def tearDown(self):
        self.temporary.cleanup()

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


class ScopeHandoffBindingTests(unittest.TestCase):
    """Scope approval is the handoff that refuses non-current upstream bindings."""

    STORY = "backlog/epics/delivery-fixture/stories/auth-01/story.md"
    TEST_PLAN = "backlog/epics/delivery-fixture/stories/auth-01/test-plan.md"
    EVIDENCE = "[[solution-design/decisions/fixture-api|Fixture API]]"
    TECHNICAL = {"work_kind": "technical", "experience_refs": [], "related_to": [EVIDENCE]}
    CHECKOUT_REF = "checkout:SCR-001@r1"
    REMEDY = ("bind it before handoff through a manual-mode backlog revision whose input_bindings "
              "pin it, or through a Requirement whose Experience stage binds it")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.docs = self.root / "workspace" / "docs"
        (self.docs / "maps").mkdir(parents=True)
        make_approved_backlog(self.docs)
        # A space note makes the fixture's analysis folder a Business Analysis receipt.
        (self.docs / "business-analysis/delivery/space.md").write_text(
            "---\ntype: space\ntitle: Delivery\nstatus: approved\n---\n\n# Delivery\n", encoding="utf-8")
        dod = type("Args", (), {"docs": str(self.docs), "title": "Project", "file": None})
        with contextlib.redirect_stdout(io.StringIO()):
            delivery_compile.init_dod(dod)
            delivery_compile.approve_dod(dod)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "gc.auto", "0"], check=True)
        self.commit("approved backlog")

    def commit(self, message: str) -> None:
        for args in (["add", "-A"], ["commit", "-q", "-m", message]):
            subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

    def edit(self, relative: str, **changes) -> None:
        path = self.docs / relative
        props, body = backlog_compile.parse_front_matter(path)
        props.update(changes)
        path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")

    def reseal_backlog(self) -> None:
        """Stamp the approved backlog again after a test edits its authored notes."""
        record, _findings = backlog_compile.collect(self.docs, historical_inputs=True)
        paths = backlog_compile.package_paths(record, self.docs)
        for path in paths:
            props, body = backlog_compile.parse_front_matter(path)
            props["source_hash"] = backlog_compile.digest(path)
            path.write_text(backlog_compile.front_matter(props, body), encoding="utf-8")
        self.edit("backlog/backlog.md", package_hash=backlog_compile.package_digest(self.docs, paths))

    def requirement(self, identifier: str, slug: str, applicable: tuple[str, ...] = ()) -> Path:
        """Approve one Requirement whose stages outside *applicable* are not_applicable."""
        path = requirement_compile.create_requirement(
            self.docs, slug, f"Account change {identifier}", "feature", "normal", identifier, [])
        props, body = requirement_compile.split_note(path)
        for old, new in (
                ("TODO: state the requested change and who needs it.", "Customers need one bounded account change."),
                ("TODO: state the observable outcome and acceptance boundary.", "The account result is observable."),
                ("TODO: define included and excluded behavior.", "Registration only; provisioning is excluded."),
                ("TODO: record evidence, constraints and urgency rationale.", "The approved account boundary applies.")):
            body = body.replace(old, new)
        for stage in requirement_compile.STAGES:
            body = body.replace(
                f"| {stage} | required |  | TODO: explain why this stage must change. |",
                f"| {stage} | {'required' if stage in applicable else 'not_applicable'} |  | "
                f"The {stage} impact was reviewed for this change. |")
        path.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
        requirement_compile.approve_requirement(path)
        return path

    def requirement_mode(self, root: Path, **story) -> None:
        """Rebind the approved backlog in requirement mode to *root* and edit AUTH-01."""
        self.edit("backlog/backlog.md", legacy_contract=None, planning_mode="requirement",
                  requirement_ref=requirement_compile.requirement_id(root), revision=2)
        self.edit(self.STORY, **story)
        if story.get("work_kind") == "technical":
            plan = self.docs / self.TEST_PLAN
            plan.write_text(plan.read_text(encoding="utf-8").replace(
                f"  - {CRITERION}\n", f"  - {CRITERION}\n  - {self.EVIDENCE}\n"), encoding="utf-8")
        self.reseal_backlog()
        self.commit("requirement-mode backlog")

    def publish_application(self) -> tuple[str, str]:
        """Approve the next revision of an empty Experience application.

        The fixture's placeholder checkout folder is not a living Experience
        package, and the application compiles every package, so it goes first.
        """
        root = self.docs / "experience-design"
        shutil.rmtree(root / "experiences", ignore_errors=True)
        ledger, findings = experience_application_check.verified_application_ledger(root)
        self.assertEqual(findings, [])
        state = root / "_generated/open-application-revision.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"opened_revision": len(ledger) + 1}), encoding="utf-8")
        registry, findings = experience_application_check.compile_application(root)
        self.assertEqual(findings, [])
        state.unlink()
        experience_application_check.write_registry_and_ledger(root, registry)
        return f"application@r{registry['application_revision']}", registry["application_hash"]

    def legacy_receipts(self) -> list[tuple[str, str, str]]:
        """Approve the fixture's Design System and return the three pre-Experience receipts."""
        self.edit("design-system/MASTER.md",
                  baseline_hash=design_system_compile.baseline_hash(self.docs / "design-system"))
        self.commit("approved design system")
        receipts = []
        for stage, ref in (("business-analysis", "business-analysis/delivery/space"),
                           ("solution-design", "solution-design/landscape"),
                           ("design-system", "design-system/MASTER")):
            receipt, errors = stage_package.verify(self.docs, stage, ref, require_committed=True)
            self.assertEqual(errors, [])
            receipts.append((stage, ref, receipt["package_hash"]))
        return receipts

    def experience_refs_resolve(self):
        """Accept Story Experience refs, which the fixture keeps no living package for.

        Resolving a Story ref inside its process package is Backlog Planning's own
        check; scope approval only reads which application the backlog binds.
        """
        return mock.patch.object(backlog_compile, "validate_experience_ref")

    def propose(self, *, legacy_manual_inputs: bool = False) -> None:
        """Create the local DLV-001 proposal for AUTH-01.

        A new proposal refuses legacy-readonly manual inputs, which is all this
        fixture's analysis, solution and design packages are, so a manual-mode
        proposal is created through the historical read that scope approval uses.
        """
        args = type("Args", (), {"docs": str(self.docs), "id": None, "slug": "auth", "goal": "Authenticate",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        read = backlog_compile.planning_package_findings
        historical = mock.patch.object(
            backlog_compile, "planning_package_findings",
            side_effect=lambda docs, props, path, allow_historical=False: read(docs, props, path, allow_historical=True))
        with (historical if legacy_manual_inputs else contextlib.nullcontext()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery_compile.init_delivery(args), 0)

    def approve_scope(self) -> tuple[int, list[str]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery_compile.approve_scope(type("Args", (), {"docs": str(self.docs), "delivery": "DLV-001"}))
        return code, json.loads(output.getvalue()).get("errors", [])

    def stale_requirement_selection(self) -> Path:
        """Select AUTH-01, which implements REQ-001, then revise the analysis REQ-001 bound."""
        implemented = self.requirement("REQ-001", "account-access", ("business-analysis",))
        requirement_compile.bind_stage(implemented, "business-analysis", "business-analysis/delivery/space")
        root = self.requirement("REQ-002", "pin-acquisition")
        self.requirement_mode(root, origin_mode="manual",
                              implements=[f"[[requirements/{implemented.stem}|REQ-001]]"], **self.TECHNICAL)
        self.assertEqual(requirement_route.route(self.docs, "REQ-001")["action"], "backlog")
        self.propose()
        space = self.docs / "business-analysis/delivery/space.md"
        space.write_text(space.read_text(encoding="utf-8") + "\nThe space gains a revised boundary.\n",
                         encoding="utf-8")
        self.commit("revise the analysis package")
        return implemented

    def test_scope_refuses_a_story_whose_requirement_has_a_stale_stage_result(self):
        self.stale_requirement_selection()
        self.assertEqual(self.approve_scope(), (1, [
            "AUTH-01 implements REQ-001, which does not route to backlog: stage business-analysis, "
            "action repair, reason: business-analysis/delivery/space package hash is stale or does not "
            "match expected hash; rebind it through the Requirement entry, /requirement REQ-001, before handoff",
        ]))
        props, _body = delivery_compile.split_note(delivery_compile.find_delivery(self.docs, "DLV-001") / "delivery.md")
        self.assertEqual(props["status"], "scope_proposed")

    def test_scope_accepts_the_story_once_its_requirement_is_rebound(self):
        implemented = self.stale_requirement_selection()
        self.assertEqual(self.approve_scope()[0], 1)
        requirement_compile.bind_stage(implemented, "business-analysis", "business-analysis/delivery/space")
        self.commit("rebind REQ-001")
        self.assertEqual(self.approve_scope(), (0, []))

    def test_scope_refuses_experience_refs_when_the_root_requirement_marks_experience_not_applicable(self):
        application, _hash = self.publish_application()
        root = self.requirement("REQ-002", "pin-acquisition")
        with self.experience_refs_resolve():
            self.requirement_mode(root, origin_mode="manual", experience_refs=[self.CHECKOUT_REF])
            self.propose()
            self.assertEqual(self.approve_scope(), (1, [
                f"AUTH-01 cites experience_refs, but the backlog does not bind the globally current "
                f"{application}: root Requirement REQ-002 marks experience-design not_applicable; {self.REMEDY}",
            ]))

    def test_scope_refuses_a_root_requirement_that_binds_an_earlier_application(self):
        receipts = self.legacy_receipts()
        earlier, _hash = self.publish_application()
        root = self.requirement("REQ-002", "account-screens", requirement_compile.STAGES)
        self.commit("upstream packages")
        for stage, ref, _digest in receipts:
            requirement_compile.bind_stage(root, stage, ref)
        requirement_compile.bind_stage(root, "experience-design", earlier)
        with self.experience_refs_resolve():
            self.requirement_mode(root, origin_mode="manual", experience_refs=[self.CHECKOUT_REF])
            self.propose()
            current, _hash = self.publish_application()
            self.commit("application-only revision")
            self.assertEqual(self.approve_scope(), (1, [
                f"AUTH-01 cites experience_refs, but the backlog does not bind the globally current {current}: "
                f"root Requirement REQ-002's Experience Stage Results are not the current application with its "
                f"exact process receipts; experience-design receipt must use its canonical result_ref, got "
                f"{earlier}; {self.REMEDY}",
            ]))

    def manual_mode(self) -> str:
        """Rebind the approved backlog in manual mode to the current receipts; return the application."""
        receipts = self.legacy_receipts()
        application, application_hash = self.publish_application()
        bindings = [f"{stage}|{ref}|{digest}" for stage, ref, digest in receipts]
        bindings.append(f"experience-design|{application}|{application_hash}")
        self.edit("backlog/backlog.md", legacy_contract=None, planning_mode="manual", revision=2,
                  input_bindings=sorted(bindings))
        self.edit(self.STORY, origin_mode="manual", experience_refs=[self.CHECKOUT_REF])
        self.reseal_backlog()
        self.commit("manual-mode backlog")
        return application

    def test_scope_accepts_experience_refs_when_manual_bindings_pin_the_current_application(self):
        with self.experience_refs_resolve():
            self.manual_mode()
            self.propose(legacy_manual_inputs=True)
            self.assertEqual(self.approve_scope(), (0, []))

    def test_scope_refuses_manual_bindings_that_pin_an_earlier_application(self):
        with self.experience_refs_resolve():
            earlier = self.manual_mode()
            self.propose(legacy_manual_inputs=True)
            current, _hash = self.publish_application()
            self.commit("application-only revision")
            self.assertEqual(self.approve_scope(), (1, [
                f"AUTH-01 cites experience_refs, but the backlog does not bind the globally current {current}: "
                f"the manual-mode input_bindings are not the current application with its exact process "
                f"receipts; experience-design receipt must use its canonical result_ref, got {earlier}; "
                f"{self.REMEDY}",
            ]))

    def requirement_mode_with_bindings(self) -> tuple[str, str]:
        """Pin the current receipts in a requirement-mode backlog whose root marks Experience not_applicable."""
        receipts = self.legacy_receipts()
        application, application_hash = self.publish_application()
        bindings = [f"{stage}|{ref}|{digest}" for stage, ref, digest in receipts]
        bindings.append(f"experience-design|{application}|{application_hash}")
        self.edit("backlog/backlog.md", input_bindings=sorted(bindings))
        root = self.requirement("REQ-002", "pin-acquisition")
        self.requirement_mode(root, origin_mode="manual", experience_refs=[self.CHECKOUT_REF])
        return application, application_hash

    def test_scope_accepts_requirement_mode_bindings_that_pin_the_current_application(self):
        with self.experience_refs_resolve():
            self.requirement_mode_with_bindings()
            self.propose()
            self.assertEqual(self.approve_scope(), (0, []))

    def test_scope_refuses_requirement_mode_bindings_that_pin_an_earlier_application(self):
        with self.experience_refs_resolve():
            earlier, _hash = self.requirement_mode_with_bindings()
            self.propose()
            current, _hash = self.publish_application()
            self.commit("application-only revision")
            self.assertEqual(self.approve_scope(), (1, [
                f"AUTH-01 cites experience_refs, but the backlog does not bind the globally current {current}: "
                f"the requirement-mode input_bindings are not the current application with its exact process "
                f"receipts; experience-design receipt must use its canonical result_ref, got {earlier}; "
                f"{self.REMEDY}",
            ]))

    def test_scope_accepts_a_selection_without_experience_refs_on_current_requirements(self):
        root = self.requirement("REQ-002", "pin-acquisition")
        self.requirement_mode(root, origin_mode="requirement", introduced_in_revision=2,
                              implements=[f"[[requirements/{root.stem}|REQ-002]]"], **self.TECHNICAL)
        self.propose()
        self.assertEqual(self.approve_scope(), (0, []))


if __name__ == "__main__":
    unittest.main()
