"""Gate 3 tests for the offline Delivery knowledge model."""

from __future__ import annotations

import json
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
import architecture_compile  # noqa: E402
import backlog_compile  # noqa: E402
import operation_compile  # noqa: E402
import stage_package  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402


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
