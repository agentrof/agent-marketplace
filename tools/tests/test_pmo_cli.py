"""Unit tests for the PMO plugin's central-database CLI."""

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
CLI_PATH = REPO / "plugins" / "project-management-office" / "scripts" / "pmo_cli.py"

spec = importlib.util.spec_from_file_location("pmo_cli", CLI_PATH)
pmo_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pmo_cli)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = pmo_cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


BACKLOG = {
    "epics": [
        {"external_id": "EP-01", "title": "Account management", "goal": "Users manage accounts."}
    ],
    "stories": [
        {
            "external_id": "WP-01", "epic": "EP-01", "title": "Password reset flow",
            "type": "feature", "priority": "critical: walking skeleton",
            "scope": "Request, email token, set new password.",
            "excludes": "Two-factor reset.",
            "dor": "Brief BR-001..BR-004 accepted.",
            "dod": "All ACs pass; review and qa green.",
            "dod_items": [
                "A member with a valid token sets a new password and signs in with it.",
                "An expired token is rejected with the documented message.",
            ],
        },
        {
            "external_id": "WP-02", "epic": "EP-01", "title": "Profile editing",
            "type": "feature", "priority": "high: closes the account loop",
            "depends_on": [{"item": "WP-01",
                            "reason": "editing needs the authenticated session WP-01 ships"}],
            "scope": "Edit display name and avatar.",
            "excludes": "Email change.",
            "dor": "Brief BR-005 accepted.",
            "dod": "All ACs pass.",
        },
    ],
    "criteria": [
        {"criterion_id": "AC-001", "story": "WP-01", "disposition": "covered"},
        {"criterion_id": "AC-002", "disposition": "deferred", "reason": "Out of v1."},
    ],
    "open_questions": ["Which mail provider?"],
}


COMMAND_CONTRACTS = {
    "init-db": "test_init_db_idempotent",
    "version": "test_version_reports_product_version",
    "now": "test_now_prints_iso_utc",
    "sync-launcher": "test_sync_launcher_is_idempotent",
    "verify": "test_verify_fresh_database",
    "session-reconcile": "test_session_reconcile_is_idempotent",
    "ensure": "test_ensure_bootstraps_launcher",
    "dashboard": "test_dashboard_command_delegates_to_read_only_server",
    "upgrade status": "test_upgrade_status_current_without_project",
    "upgrade prepare-branch": "test_upgrade_prepare_branch_delegates",
    "upgrade plan": "test_upgrade_status_current_without_project",
    "upgrade apply": "test_upgrade_status_current_without_project",
    "upgrade recover": "test_upgrade_status_current_without_project",
    "project register": "test_project_register_and_list",
    "project list": "test_project_register_and_list",
    "project environment-status": "test_project_environment_status_and_attach",
    "project attach": "test_project_environment_status_and_attach",
    "project activate-vault": "test_project_activate_vault_updates_canonical_contract",
    "project classify-origin": "test_project_origin_classification_is_guarded",
    "program list": "test_program_experience_backlog_lifecycle",
    "program show": "test_program_experience_backlog_lifecycle",
    "program status": "test_program_experience_backlog_lifecycle",
    "program baseline": "test_program_experience_backlog_lifecycle",
    "program complete": "test_program_experience_backlog_lifecycle",
    "program cancel": "test_program_experience_backlog_lifecycle",
    "release list": "test_program_experience_backlog_lifecycle",
    "release show": "test_program_experience_backlog_lifecycle",
    "release activate": "test_program_experience_backlog_lifecycle",
    "release refresh-ready": "test_program_experience_backlog_lifecycle",
    "release complete": "test_program_experience_backlog_lifecycle",
    "release cancel": "test_program_experience_backlog_lifecycle",
    "experience-run init": "test_program_experience_backlog_lifecycle",
    "experience-run status": "test_program_experience_backlog_lifecycle",
    "experience-run record-gate": "test_program_experience_backlog_lifecycle",
    "experience-run release": "test_program_experience_backlog_lifecycle",
    "experience-run abandon": "test_experience_run_abandon_preserves_audit_state",
    "experience run abandon": "test_experience_run_abandon_preserves_audit_state",
    "backlog-plan init": "test_program_experience_backlog_lifecycle",
    "backlog-plan status": "test_program_experience_backlog_lifecycle",
    "backlog-plan reserve-ids": "test_program_experience_backlog_lifecycle",
    "backlog-plan record-finding": "test_program_experience_backlog_lifecycle",
    "backlog-plan resolve-finding": "test_program_experience_backlog_lifecycle",
    "backlog-plan record-gate": "test_program_experience_backlog_lifecycle",
    "backlog-plan verify": "test_program_experience_backlog_lifecycle",
    "backlog-plan apply": "test_program_experience_backlog_lifecycle",
    "backlog-plan abandon": "test_program_experience_backlog_lifecycle",
    "resume-info": "test_resume_info_reports_work_order_shape",
    "work-order init": "test_story_claim_marks_in_development",
    "work-order set-step": "test_transition_guard",
    "work-order record-gate": "test_every_mutation_writes_an_event",
    "work-order bump": "test_every_mutation_writes_an_event",
    "work-order set-ownership": "test_ownership_snake_case_and_cross_order_overlap",
    "work-order set-status": "test_complete_guard_full_chain",
    "work-order release": "test_release_frees_worktree_and_claim",
    "work-order checkpoint-reconcile": "test_reconcile_checkpoint_contract",
    "work-order resume-reconcile": "test_reconcile_checkpoint_contract",
    "work-order validate": "test_work_order_validate",
    "item import": "test_import_green_and_upsert",
    "item update": "test_priority_enum_on_update",
    "item list": "test_story_claim_marks_in_development",
    "item add-dep": "test_add_remove_dep_and_cycle_guard",
    "item remove-dep": "test_add_remove_dep_and_cycle_guard",
    "item list-deps": "test_import_materializes_structured_deps",
    "item add-dod": "test_dod_transitions",
    "item set-dod": "test_dod_transitions",
    "item list-dod": "test_dod_transitions",
    "item order": "test_item_order_deterministic_with_priority_tiebreak",
    "item ready": "test_item_ready_dep_gating",
    "task open": "test_task_open_touch_close",
    "task close": "test_task_open_touch_close",
    "task touch": "test_task_open_touch_close",
    "finding open": "test_finding_lifecycle",
    "finding update": "test_finding_lifecycle",
    "finding list": "test_finding_lifecycle",
    "issue open": "test_issue_open_mints_global_id_without_project",
    "issue update": "test_issue_update_fields_and_dismiss",
    "issue list": "test_issue_list_filters_by_status_json",
    "issue file": "test_issue_file_records_url_and_event",
    "coverage import": "test_coverage_import_and_replace",
    "coverage list": "test_coverage_list_reads_story_criteria",
    "budget set": "test_budget_and_ledger",
    "event append": "test_event_append_contract",
    "ledger checkpoint": "test_budget_and_ledger",
    "ledger list": "test_ledger_list_decodes_finding_counts",
    "checkpoint": "test_complete_guard_full_chain",
    "dump": "test_dump_load_round_trip_into_fresh_home",
    "load": "test_dump_load_round_trip_into_fresh_home",
}


def parser_leaf_commands(parser, prefix=()) -> list[str]:
    leaves: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                leaves.extend(parser_leaf_commands(child, (*prefix, name)))
    return leaves or [" ".join(prefix)]


class MarketplacePathContractTests(unittest.TestCase):
    def test_team_owner_resolution_is_contract_first_and_fail_closed(self):
        resolver = pmo_cli.marketplace_paths.team_from_config
        cases = (
            ({"agent_marketplace": {"team_id": "nested"},
              "team_id": "legacy"}, "nested"),
            ({"team_id": "legacy"}, "legacy"),
            ({"managed_by": "legacy plugin; change only through the configure entry"},
             "legacy"),
            ({"managed_by": "legacy"}, "legacy"),
            ({"agent_marketplace": {}, "team_id": "legacy"}, ""),
        )
        for config, expected in cases:
            with self.subTest(config=config):
                self.assertEqual(resolver(config), expected)

    def test_environment_precedence(self):
        resolver = pmo_cli.marketplace_paths
        cases = (
            ({}, "/users/example/.agentrof", "/users/example/.agentrof/agent-marketplace"),
            ({"AGENTROF_HOME": "/vendor"}, "/vendor", "/vendor/agent-marketplace"),
            ({"AGENT_MARKETPLACE_HOME": "/product"},
             "/users/example/.agentrof", "/product"),
            ({"AGENTROF_HOME": "/vendor", "AGENT_MARKETPLACE_HOME": "/product"},
             "/vendor", "/product"),
        )
        for environment, expected_vendor, expected_marketplace in cases:
            with self.subTest(environment=environment):
                self.assertEqual(
                    resolver.vendor_home(environment, "/users/example"),
                    Path(expected_vendor),
                )
                self.assertEqual(
                    resolver.marketplace_home(environment, "/users/example"),
                    Path(expected_marketplace),
                )

    def test_vendor_override_keeps_all_runtime_files_in_product_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            vendor = Path(tmp) / "vendor"
            with mock.patch.dict(
                os.environ,
                {"AGENTROF_HOME": str(vendor), "AGENT_MARKETPLACE_HOME": ""},
            ):
                code, _, err = run(["init-db"])
                self.assertEqual(code, 0, err)
                product = vendor / "agent-marketplace"
                self.assertEqual(pmo_cli.data_dir(), product)
                self.assertTrue((product / "pmo.db").is_file())
                self.assertFalse((vendor / "pmo.db").exists())

class PmoCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("AGENT_MARKETPLACE_HOME")
        os.environ["AGENT_MARKETPLACE_HOME"] = str(Path(self.tmp.name) / "agentrof")
        # The worktree-binding guard compares the caller's cwd against each
        # order's claimed worktree, so tests run from real directories.
        self.wt_main = Path(self.tmp.name) / "wt-main"
        self.wt_two = Path(self.tmp.name) / "wt-two"
        self.wt_main.mkdir()
        self.wt_two.mkdir()
        for root in (self.wt_main, self.wt_two):
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.com"],
                cwd=root, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Tests"], cwd=root, check=True,
            )
            config_path = root / "workspace" / "config.json"
            config_path.parent.mkdir()
            config = {"project_key": "shop", "project_origin": "existing"}
            contract = {
                "schema_version": 1, "contract_version": 5,
                "project_id": "test-project-id", "team_id": "software-engineering-team",
                "workspace": "workspace", "repository_fingerprint": "test",
                "delivery": {"requires_pull_request": False, "target_branch": "master"},
                "marketplace_release": "0.1.0", "source_channel": "stable",
                "source_ref": "v0.1.0", "source_commit": "test",
                "components": {}, "managed_surfaces": {}, "vault": {},
                "upgrade_provenance": {},
            }
            pmo_cli.upgrade_core.write_project_contract(
                config_path, config, contract
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
        self._old_cwd = os.getcwd()
        os.chdir(self.wt_main)
        code, _, err = run(["init-db"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["project", "register", "--key", "shop",
                            "--name", "Shop", "--team", "software-engineering-team"])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        os.chdir(self._old_cwd)
        if self._old_home is None:
            os.environ.pop("AGENT_MARKETPLACE_HOME", None)
        else:
            os.environ["AGENT_MARKETPLACE_HOME"] = self._old_home
        self.tmp.cleanup()

    def db(self):
        con = sqlite3.connect(Path(os.environ["AGENT_MARKETPLACE_HOME"]) / "pmo.db")
        con.row_factory = sqlite3.Row
        return con

    def import_backlog(self, data=None):
        path = Path(self.tmp.name) / "backlog.json"
        path.write_text(json.dumps(data or BACKLOG), encoding="utf-8")
        return run(["item", "import", "--project-key", "shop",
                    "--json-file", str(path)])

    def init_wo(self, wo_key="wo1", worktree=None, story="WP-01"):
        worktree = worktree or str(self.wt_main)
        argv = ["work-order", "init", "--project-key", "shop",
                "--work-order-key", wo_key,
                "--request", "build it", "--worktree", worktree]
        if story:
            argv += ["--story", story]
        return run(argv)

    def test_program_experience_backlog_lifecycle(self):
        code, _, err = run([
            "experience-run", "init", "--project-key", "shop",
            "--run-key", "ux-1", "--program", "PRG-001",
            "--release", "REL-001", "--node", "marketplace",
        ])
        self.assertEqual(code, 0, err)

        code, out, err = run([
            "experience-run", "status", "--project-key", "shop",
            "--run-key", "ux-1",
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["status"], "active")
        for gate in ("space:marketplace", "release", "program"):
            code, _, err = run([
                "experience-run", "record-gate", "--run-key", "ux-1",
                "--gate", gate, "--decision", "approved",
                "--revision-hash", "sha256:experience",
            ])
            self.assertEqual(code, 0, err)
        code, _, err = run(["experience-run", "release", "--run-key", "ux-1"])
        self.assertEqual(code, 0, err)

        plan = {
            "mode": "baseline",
            "program_id": "PRG-001",
            "title": "Marketplace",
            "releases": [{
                "release_id": "REL-001", "title": "First release",
                "experience_registry_hash": "sha256:experience",
                "experience_registry": "registry.json",
            }],
            "epics": [{"external_id": "EP-01", "title": "Core",
                       "goal": "Customers browse the core catalog."}],
            "stories": [{
                "external_id": "WP-01", "epic": "EP-01",
                "release_id": "REL-001", "title": "Browse catalog",
                "type": "feature", "priority": "high: walking skeleton",
                "scope": "Browse approved catalog.", "excludes": "Checkout.",
                "dor": ["Approved inputs are linked."],
                "dod": ["Catalog is verified."],
                "criteria": ["marketplace:AC-CAT-001"],
                "solution_refs": ["SD-001"], "budget_refs": ["BUD-001"],
                "ux_refs": ["PRG-001:SCR-001@r1"], "ui": True,
                "delivery_owners": {"owner": "frontend_developer", "supporting": []},
            }],
            "shares": [],
            "gates": {"reviewer": "approved", "domains": ["marketplace"],
                      "program": "approved"},
            "findings": [],
        }
        path = Path(self.tmp.name) / "plan.json"
        (Path(self.tmp.name) / "registry.json").write_text(json.dumps({
            "program_id": "PRG-001", "release_id": "REL-001",
            "registry_hash": "sha256:experience",
            "records": [{"id": "SCR-001", "revision": 1}],
        }), encoding="utf-8")
        path.write_text(json.dumps(plan), encoding="utf-8")
        expected_hash = pmo_cli.backlog_plan_hash(plan)
        code, out, err = run([
            "backlog-plan", "init", "--project-key", "shop",
            "--plan-key", "plan-1", "--program", "PRG-001",
            "--mode", "baseline", "--plan-file", str(path),
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), expected_hash)
        code, out, err = run([
            "backlog-plan", "reserve-ids", "--plan-key", "plan-1",
            "--prefix", "WP", "--count", "2",
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), ["WP-001", "WP-002"])
        code, _, err = run([
            "backlog-plan", "record-finding", "--plan-key", "plan-1",
            "--finding", "BF-001", "--severity", "non-blocking",
            "--summary", "Watch capacity",
        ])
        self.assertEqual(code, 0, err)
        code, _, err = run([
            "backlog-plan", "resolve-finding", "--plan-key", "plan-1",
            "--finding", "BF-001", "--status", "accepted-risk",
            "--reason", "Bounded", "--owner", "product-owner",
            "--revisit", "before activation",
        ])
        self.assertEqual(code, 0, err)
        revision = 0
        for gate in ("reviewer", "domain:marketplace", "reconciliation", "program"):
            code, out, err = run([
                "backlog-plan", "record-gate", "--plan-key", "plan-1",
                "--gate", gate, "--decision", "approved",
                "--plan-hash", expected_hash,
            ])
            self.assertEqual(code, 0, err)
            revision = int(out.strip())
        code, _, err = run([
            "backlog-plan", "verify", "--plan-key", "plan-1",
            "--plan-file", str(path), "--compiler",
            str(REPO / "plugins" / "software-engineering-team" / "scripts"
                / "backlog_compile.py"),
        ])
        self.assertEqual(code, 0, err)
        code, _, err = run([
            "backlog-plan", "apply", "--plan-key", "plan-1",
            "--plan-file", str(path), "--approved-hash", expected_hash,
            "--gate-revision", str(revision),
        ])
        self.assertEqual(code, 0, err)
        code, out, err = run(["backlog-plan", "status", "--plan-key", "plan-1"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "applied")
        with self.db() as con:
            self.assertEqual(
                con.execute("SELECT status FROM programs WHERE program_key = 'PRG-001'").fetchone()[0],
                "baselined",
            )
            self.assertEqual(
                con.execute("SELECT role FROM work_item_owners WHERE relationship = 'owner'").fetchone()[0],
                "frontend_developer",
            )

        code, _, err = run([
            "program", "baseline", "--project-key", "shop",
            "--program", "PRG-001", "--baseline-hash", expected_hash,
        ])
        self.assertEqual(code, 0, err)
        for command in ("list",):
            code, _, err = run(["program", command, "--project-key", "shop"])
            self.assertEqual(code, 0, err)
        for command in ("show", "status"):
            code, _, err = run(["program", command, "--project-key", "shop",
                                "--program", "PRG-001"])
            self.assertEqual(code, 0, err)
        code, _, err = run(["release", "list", "--project-key", "shop",
                            "--program", "PRG-001"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["release", "activate", "--project-key", "shop",
                            "--program", "PRG-001", "--release", "REL-001"])
        self.assertEqual(code, 0, err)
        code, out, err = run(["release", "refresh-ready", "--project-key", "shop",
                              "--program", "PRG-001", "--release", "REL-001"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["ready"], ["WP-01"])
        code, _, err = run(["item", "update", "--project-key", "shop",
                            "--external-id", "WP-01", "--status", "in_development"])
        self.assertEqual(code, 0, err)
        protected_change = json.loads(json.dumps(plan))
        protected_change["stories"][0]["ux_refs"] = ["PRG-001:SCR-999@r1"]
        with pmo_cli.connect() as con:
            project = pmo_cli.get_project(con, "shop")
            plan_row = pmo_cli.get_backlog_plan(con, "plan-1")
            with self.assertRaisesRegex(pmo_cli.Rule, "protected story contract"):
                with pmo_cli.mutate(con):
                    pmo_cli.sync_plan_items(con, project, plan_row, protected_change)
        code, _, err = run(["release", "show", "--project-key", "shop",
                            "--program", "PRG-001", "--release", "REL-001"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["item", "update", "--project-key", "shop",
                            "--external-id", "WP-01", "--status", "done"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["release", "complete", "--project-key", "shop",
                            "--program", "PRG-001", "--release", "REL-001"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["program", "complete", "--project-key", "shop",
                            "--program", "PRG-001"])
        self.assertEqual(code, 0, err)

        abandoned = dict(plan)
        abandoned["mode"] = "replan"
        abandoned["program_id"] = "PRG-002"
        abandoned_path = Path(self.tmp.name) / "abandoned.json"
        abandoned_path.write_text(json.dumps(abandoned), encoding="utf-8")
        code, _, err = run([
            "backlog-plan", "init", "--project-key", "shop",
            "--plan-key", "plan-2", "--program", "PRG-002",
            "--mode", "replan", "--plan-file", str(abandoned_path),
        ])
        self.assertEqual(code, 0, err)
        code, _, err = run(["backlog-plan", "abandon", "--plan-key", "plan-2",
                            "--reason", "superseded"])
        self.assertEqual(code, 0, err)

    def test_experience_run_abandon_preserves_audit_state(self):
        for run_key, command in (
            ("ux-abandon-legacy", ["experience-run", "abandon"]),
            ("ux-abandon-canonical", ["experience", "run", "abandon"]),
        ):
            code, _, err = run([
                "experience-run", "init", "--project-key", "shop",
                "--run-key", run_key, "--program", "PRG-001",
                "--node", "marketplace",
            ])
            self.assertEqual(code, 0, err)
            code, _, err = run([
                *command, "--run-key", run_key,
                "--reason", "environment reconciliation",
            ])
            self.assertEqual(code, 0, err)
        with self.db() as con:
            rows = con.execute(
                "SELECT id, run_key, status, abandoned_at, abandon_reason"
                " FROM experience_runs ORDER BY run_key"
            ).fetchall()
            self.assertEqual([row["status"] for row in rows],
                             ["abandoned", "abandoned"])
            self.assertTrue(all(row["abandoned_at"] for row in rows))
            self.assertTrue(all(row["abandon_reason"] for row in rows))
            claim_count = con.execute(
                "SELECT COUNT(*) FROM experience_node_claims"
            ).fetchone()[0]
            audit_count = con.execute(
                "SELECT COUNT(*) FROM events"
                " WHERE action = 'experience_run_abandoned'"
            ).fetchone()[0]
            self.assertEqual(claim_count, 2)
            self.assertEqual(audit_count, 2)

    def test_project_origin_classification_is_guarded(self):
        workspace = self.wt_main / "workspace"
        config_path = workspace / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["project_origin"] = "unclassified"
        config["user_setting"] = "preserved"
        contract = config["agent_marketplace"]
        pmo_cli.upgrade_core.write_project_contract(config_path, config, contract)

        code, out, err = run([
            "project", "classify-origin", "--project-key", "shop",
            "--project-root", str(self.wt_main), "--origin", "existing",
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("project_origin=existing", out)
        classified = json.loads(config_path.read_text(encoding="utf-8"))
        contract = classified["agent_marketplace"]
        self.assertEqual(classified["project_origin"], "existing")
        self.assertEqual(classified["user_setting"], "preserved")
        self.assertEqual(
            contract["contract_sha256"],
            pmo_cli.upgrade_core.contract_sha256(contract),
        )

        code, _, err = self.import_backlog()
        self.assertEqual(code, 0, err)
        code, _, err = run([
            "project", "classify-origin", "--project-key", "shop",
            "--project-root", str(self.wt_main), "--origin", "greenfield",
        ])
        self.assertEqual(code, 1)
        self.assertIn("immutable", err)

    def test_backlog_plan_revisions_and_finding_round_guards(self):
        path = Path(self.tmp.name) / "revision-plan.json"
        plan = {"mode": "baseline", "program_id": "PRG-009", "stories": []}
        path.write_text(json.dumps(plan), encoding="utf-8")
        code, first_hash, err = run([
            "backlog-plan", "init", "--project-key", "shop",
            "--plan-key", "revision-plan", "--program", "PRG-009",
            "--mode", "baseline", "--plan-file", str(path),
            "--session-id", "session-a",
        ])
        self.assertEqual(code, 0, err)
        plan["title"] = "Revised"
        path.write_text(json.dumps(plan), encoding="utf-8")
        code, second_hash, err = run([
            "backlog-plan", "init", "--project-key", "shop",
            "--plan-key", "revision-plan", "--program", "PRG-009",
            "--mode", "baseline", "--plan-file", str(path),
            "--session-id", "session-a",
        ])
        self.assertEqual(code, 0, err)
        self.assertNotEqual(first_hash, second_hash)
        with self.db() as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM backlog_plan_revisions").fetchone()[0],
                2,
            )

        code, _, err = run([
            "backlog-plan", "record-finding", "--plan-key", "revision-plan",
            "--finding", "MECH-1", "--kind", "mechanical",
            "--severity", "blocker", "--summary", "Compiler failure",
        ])
        self.assertEqual(code, 0, err)
        code, _, err = run([
            "backlog-plan", "resolve-finding", "--plan-key", "revision-plan",
            "--finding", "MECH-1", "--status", "rejected",
            "--reason", "Not applicable",
        ])
        self.assertEqual(code, 1)
        self.assertIn("mechanical findings", err)

        for round_number in range(1, 5):
            code, _, err = run([
                "backlog-plan", "record-finding", "--plan-key", "revision-plan",
                "--finding", "SEM-1", "--kind", "semantic",
                "--severity", "blocker", "--summary", f"Round {round_number}",
            ])
            self.assertEqual(code, 0 if round_number <= 3 else 1)
        self.assertIn("three review rounds", err)

    # -- clock ------------------------------------------------------------------

    def test_public_command_contract_registry_is_complete(self):
        leaves = set(parser_leaf_commands(pmo_cli.build_parser()))
        self.assertEqual(leaves, set(COMMAND_CONTRACTS))
        for command, method in COMMAND_CONTRACTS.items():
            with self.subTest(command=command, method=method):
                self.assertTrue(hasattr(type(self), method), method)

    def test_version_reports_product_version(self):
        code, out, err = run(["version"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), pmo_cli.PMO_VERSION)

    def test_upgrade_status_current_without_project(self):
        code, out, err = run(["upgrade", "status", "--json"])
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertEqual(result["status"], "AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED")
        self.assertTrue(any("REQUIRED_COMPONENT_MISSING" in value
                            for value in result["blockers"]))

    def test_upgrade_prepare_branch_delegates(self):
        expected = {
            "status": "AGENT_MARKETPLACE_UPGRADE_BRANCH_PREPARED",
            "upgrade_branch": "agent-marketplace/upgrade-20260811T104530Z",
        }
        with mock.patch.object(
            pmo_cli.upgrade_core, "prepare_branch", return_value=expected
        ) as prepare:
            code, out, err = run([
                "upgrade", "prepare-branch", "--project-root", str(self.wt_main),
            ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), expected)
        self.assertEqual(prepare.call_args.args[-1], str(self.wt_main.resolve()))

    def test_removed_upgrade_session_interfaces_are_rejected(self):
        for command in (
            ["upgrade", "status", "--exclude-session-id", "old"],
            ["upgrade", "session-release", "--session-id", "old"],
        ):
            with self.subTest(command=command):
                code, _, _ = run(command)
                self.assertEqual(code, 2)

    def test_now_prints_iso_utc(self):
        from datetime import datetime
        code, out, err = run(["now"])
        self.assertEqual(code, 0, err)
        value = out.strip()
        self.assertTrue(value.endswith("+00:00"), value)
        datetime.fromisoformat(value)  # parseable or it raises

    def test_now_date_and_compact_shapes(self):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        code, out, _ = run(["now", "--date"])
        self.assertEqual((code, out.strip()), (0, today.isoformat()))
        code, out, _ = run(["now", "--compact"])
        self.assertEqual((code, out.strip()), (0, today.strftime("%Y%m%d")))
        code, out, _ = run(["now", "--compact-time"])
        self.assertRegex(out.strip(), r"^[0-9]{8}T[0-9]{6}Z$")

    def test_now_flags_are_exclusive(self):
        code, _, _ = run(["now", "--date", "--compact"])
        self.assertEqual(code, 2)
        code, _, _ = run(["now", "--compact", "--compact-time"])
        self.assertEqual(code, 2)

    def test_wo_init_rejects_wrong_date_prefix(self):
        from datetime import datetime, timedelta, timezone
        self.import_backlog()
        today = datetime.now(timezone.utc).date()
        for prefix in ((today - timedelta(days=2)).strftime("%Y%m%d"),
                       (today + timedelta(days=1)).strftime("%Y%m%d"),
                       "99999999"):
            code, _, err = self.init_wo(wo_key=f"{prefix}-password-reset")
            self.assertEqual(code, 1, err)
            self.assertIn("now --compact", err)

    def test_wo_init_accepts_current_dated_and_undated_keys(self):
        from datetime import datetime, timedelta, timezone
        self.import_backlog()
        today = datetime.now(timezone.utc).date()
        code, _, err = self.init_wo(
            wo_key=f"{today.strftime('%Y%m%d')}-password-reset")
        self.assertEqual(code, 0, err)
        yesterday = (today - timedelta(days=1)).strftime("%Y%m%d")
        code, _, err = self.init_wo(wo_key=f"{yesterday}-midnight-lane",
                                    worktree=str(self.wt_two), story="")
        self.assertEqual(code, 0, err)

    # -- database and project --------------------------------------------------

    def test_sync_launcher_is_idempotent(self):
        code, _, err = run(["sync-launcher"])
        self.assertEqual(code, 0, err)
        first = (Path(os.environ["AGENT_MARKETPLACE_HOME"]) / "bin" / "pmo_cli.py")
        self.assertTrue(first.is_file())
        before = first.read_bytes()
        code, out, err = run(["sync-launcher"])
        self.assertEqual(code, 0, err)
        self.assertIn("already at version", out)
        self.assertEqual(first.read_bytes(), before)

    def test_verify_fresh_database(self):
        code, out, err = run(["verify", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"ok": True, "problem": ""})

    def test_ensure_bootstraps_launcher(self):
        code, out, err = run(["ensure"])
        self.assertEqual(code, 0, err)
        self.assertIn("ensure complete", out)
        self.assertTrue((Path(os.environ["AGENT_MARKETPLACE_HOME"]) / "bin"
                         / "pmo_cli.py").is_file())

    def test_session_reconcile_is_idempotent(self):
        self.import_backlog()
        self.init_wo()
        argv = ["session-reconcile", "--project-key", "shop",
                "--worktree", str(self.wt_main)]
        code, out, err = run(argv)
        self.assertEqual(code, 0, err)
        self.assertIn("appended 1", out)
        code, out, err = run(argv)
        self.assertEqual(code, 0, err)
        self.assertIn("appended 0", out)

    def test_dashboard_command_delegates_to_read_only_server(self):
        module = Path(self.tmp.name) / "pmo_dashboard.py"
        index = Path(self.tmp.name) / "index.html"
        module.write_text("# fixture\n", encoding="utf-8")
        index.write_text("<html></html>\n", encoding="utf-8")
        fake = SimpleNamespace(serve=mock.Mock(return_value=0))
        with mock.patch.object(
                pmo_cli, "dashboard_assets", return_value=(module, index)), \
                mock.patch.dict(sys.modules, {"pmo_dashboard": fake}):
            code, _, err = run([
                "dashboard", "--host", "127.0.0.1", "--port", "9191",
                "--no-browser",
            ])
        self.assertEqual(code, 0, err)
        fake.serve.assert_called_once_with(
            "127.0.0.1", 9191, open_browser=False
        )

    def test_project_register_and_list(self):
        code, _, err = run([
            "project", "register", "--key", "second", "--name", "Second",
        ])
        self.assertEqual(code, 0, err)
        code, out, err = run(["project", "list", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            [row["project_key"] for row in json.loads(out)],
            ["second", "shop"],
        )

    def test_project_environment_status_and_attach(self):
        current = {
            "status": pmo_cli.upgrade_core.STATUS_CURRENT,
            "reasons": [], "blockers": [], "active_work": [],
            "contract_sha256": "sha256:current",
        }
        reconcile = {
            "status": pmo_cli.upgrade_core.STATUS_RECONCILE,
            "reasons": ["LOCAL_PROJECT_ATTACH_REQUIRED:shop"],
            "blockers": [], "active_work": [],
            "contract_sha256": "sha256:current",
        }
        with mock.patch.object(
            pmo_cli.upgrade_core, "environment_status", return_value=current
        ):
            code, out, err = run([
                "project", "environment-status", "--project-root",
                str(self.wt_main), "--json",
            ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], current["status"])

        config = json.loads(
            (self.wt_main / "workspace" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        contract = config["agent_marketplace"]
        with pmo_cli.connect() as con:
            with pmo_cli.mutate(con):
                con.execute(
                    "UPDATE projects SET project_uuid = ?,"
                    " repository_fingerprint = ? WHERE project_key = 'shop'",
                    (contract["project_id"],
                     contract["repository_fingerprint"]),
                )
        with mock.patch.object(
            pmo_cli.upgrade_core, "environment_status",
            side_effect=[reconcile, current],
        ), mock.patch.object(
            pmo_cli.upgrade_core, "normalize_registry",
            return_value={"plugins": {
                "software-engineering-team": {"hosts": {}}
            }},
        ):
            code, out, err = run([
                "project", "attach", "--project-root", str(self.wt_main),
                "--workspace", "workspace", "--json",
            ])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["mutation_performed"])
        with self.db() as con:
            event = con.execute(
                "SELECT 1 FROM events"
                " WHERE action = 'project_environment_attached'"
            ).fetchone()
        self.assertTrue(event)

    def test_project_activate_vault_updates_canonical_contract(self):
        config_path = self.wt_main / "workspace" / "config.json"
        code, _, err = run([
            "project", "activate-vault", "--project-root", str(self.wt_main),
            "--workspace", "workspace", "--plan-hash", "sha256:adoption",
            "--policy-version", "2",
        ])
        self.assertEqual(code, 0, err)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        contract = config["agent_marketplace"]
        self.assertEqual(contract["vault"], {
            "root": "workspace/docs", "policy_version": 2,
            "status": "active", "adoption_plan_hash": "sha256:adoption",
        })
        self.assertEqual(
            contract["contract_sha256"],
            pmo_cli.upgrade_core.contract_sha256(contract),
        )
        with self.db() as con:
            event = con.execute(
                "SELECT 1 FROM events WHERE action = 'vault_adoption_activated'"
            ).fetchone()
        self.assertTrue(event)

    def test_init_db_idempotent(self):
        code1, _, _ = run(["init-db"])
        code2, _, _ = run(["init-db"])
        self.assertEqual((code1, code2), (0, 0))

    def test_uninitialized_db_fails_cleanly(self):
        os.environ["AGENT_MARKETPLACE_HOME"] = str(Path(self.tmp.name) / "fresh")
        code, _, err = run(["resume-info", "--project-key", "shop"])
        self.assertEqual(code, 1)
        self.assertIn("init-db", err)

    def test_fresh_db_schema(self):
        con = self.db()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],
                         pmo_cli.SCHEMA_VERSION)
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in ("work_orders", "work_order_steps", "work_item_deps",
                      "dod_items", "task_attempts", "issue_candidates"):
            self.assertIn(table, tables)
        self.assertNotIn("runs", tables)

    def test_refuses_noncurrent_schema(self):
        con = self.db()
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        code, _, err = run(["init-db"])
        self.assertEqual(code, 1)
        self.assertIn("AGENT_MARKETPLACE_UPGRADE_REQUIRED", err)

    def test_dump_load_round_trip_into_fresh_home(self):
        dump = Path(self.tmp.name) / "backup.sql"
        code, _, err = run(["dump", "--out", str(dump)])
        self.assertEqual(code, 0, err)
        original_home = os.environ["AGENT_MARKETPLACE_HOME"]
        try:
            os.environ["AGENT_MARKETPLACE_HOME"] = str(Path(self.tmp.name) / "restored")
            code, _, err = run(["load", "--infile", str(dump)])
            self.assertEqual(code, 0, err)
            code, out, err = run(["project", "list", "--json"])
            self.assertEqual(code, 0, err)
            self.assertEqual([row["project_key"] for row in json.loads(out)], ["shop"])
            code, _, err = run(["verify"])
            self.assertEqual(code, 0, err)
        finally:
            os.environ["AGENT_MARKETPLACE_HOME"] = original_home

    def test_dump_replace_failure_removes_temporary_file(self):
        dump = Path(self.tmp.name) / "backup.sql"
        with mock.patch.object(pmo_cli.os, "replace", side_effect=OSError("full")):
            with self.assertRaisesRegex(OSError, "full"):
                run(["dump", "--out", str(dump)])
        self.assertFalse(dump.exists())
        self.assertEqual(list(dump.parent.glob(f".{dump.name}.*")), [])

    def test_load_without_force_preserves_existing_database(self):
        dump = Path(self.tmp.name) / "backup.sql"
        run(["dump", "--out", str(dump)])
        code, _, err = run(["load", "--infile", str(dump)])
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        code, out, _ = run(["project", "list", "--json"])
        self.assertEqual([row["project_key"] for row in json.loads(out)], ["shop"])

    def test_force_load_invalid_sql_preserves_existing_database(self):
        bad = Path(self.tmp.name) / "bad.sql"
        bad.write_text("this is not SQL;", encoding="utf-8")
        code, _, err = run(["load", "--infile", str(bad), "--force"])
        self.assertEqual(code, 1)
        self.assertIn("existing database preserved", err)
        code, out, _ = run(["project", "list", "--json"])
        self.assertEqual([row["project_key"] for row in json.loads(out)], ["shop"])

    def test_force_load_wrong_schema_preserves_existing_database(self):
        bad = Path(self.tmp.name) / "wrong-schema.sql"
        bad.write_text("PRAGMA user_version = 99;\n", encoding="utf-8")
        code, _, err = run(["load", "--infile", str(bad), "--force"])
        self.assertEqual(code, 1)
        self.assertIn("schema 99", err)
        code, out, _ = run(["project", "list", "--json"])
        self.assertEqual([row["project_key"] for row in json.loads(out)], ["shop"])

    def test_force_load_rejects_tampered_integrity_stamp(self):
        dump = Path(self.tmp.name) / "backup.sql"
        run(["dump", "--out", str(dump)])
        text = dump.read_text(encoding="utf-8")
        self.assertIn("Shop", text)
        dump.write_text(text.replace("Shop", "Tampered", 1), encoding="utf-8")
        code, _, err = run(["load", "--infile", str(dump), "--force"])
        self.assertEqual(code, 1)
        self.assertIn("integrity stamp is invalid", err)
        code, out, _ = run(["project", "list", "--json"])
        self.assertEqual(json.loads(out)[0]["name"], "Shop")

    def test_force_load_valid_dump_atomically_replaces_database(self):
        dump = Path(self.tmp.name) / "backup.sql"
        run(["dump", "--out", str(dump)])
        run(["project", "register", "--key", "temporary"])
        code, _, err = run(["load", "--infile", str(dump), "--force"])
        self.assertEqual(code, 0, err)
        code, out, _ = run(["project", "list", "--json"])
        self.assertEqual([row["project_key"] for row in json.loads(out)], ["shop"])

    def test_load_missing_dump_is_clean_input_error(self):
        missing = Path(self.tmp.name) / "missing.sql"
        fresh_home = str(Path(self.tmp.name) / "empty-home")
        original_home = os.environ["AGENT_MARKETPLACE_HOME"]
        try:
            os.environ["AGENT_MARKETPLACE_HOME"] = fresh_home
            code, _, err = run(["load", "--infile", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("cannot read database dump", err)
            self.assertFalse(Path(fresh_home, pmo_cli.DB_NAME).exists())
        finally:
            os.environ["AGENT_MARKETPLACE_HOME"] = original_home

    # -- issue candidates ------------------------------------------------------

    def test_issue_open_mints_global_id_without_project(self):
        code, out, err = run(["issue", "open", "--title", "gh missing",
                              "--kind", "defect", "--evidence", "session A"])
        self.assertEqual(code, 0, err)
        self.assertIn("IC-001", out)
        code, out, _ = run(["issue", "open", "--title", "capture idea",
                            "--kind", "improvement"])
        self.assertIn("IC-002", out)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertIsNone(row["project_id"])
        self.assertIsNone(row["work_order_id"])
        self.assertEqual(row["status"], "candidate")

    def test_issue_id_sequence_is_global_not_project_scoped(self):
        run(["issue", "open", "--title", "a", "--kind", "defect"])
        code, out, err = run(["issue", "open", "--title", "b", "--kind",
                              "improvement", "--project-key", "shop"])
        self.assertEqual(code, 0, err)
        self.assertIn("IC-002", out)  # shared sequence across project-less/bound
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-002'").fetchone()
        project = self.db().execute(
            "SELECT id FROM projects WHERE project_key='shop'").fetchone()
        self.assertEqual(row["project_id"], project["id"])

    def test_issue_open_binds_work_order(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["issue", "open", "--title", "wo defect",
                            "--kind", "defect", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        order = self.db().execute(
            "SELECT id, project_id FROM work_orders WHERE work_order_key='wo1'"
        ).fetchone()
        self.assertEqual(row["work_order_id"], order["id"])
        self.assertEqual(row["project_id"], order["project_id"])

    def test_issue_open_rejects_bad_kind(self):
        code, _, _ = run(["issue", "open", "--title", "x", "--kind", "bug"])
        self.assertEqual(code, 2)

    def test_issue_update_fields_and_dismiss(self):
        run(["issue", "open", "--title", "candidate", "--kind", "defect"])
        code, out, err = run(["issue", "update", "--issue", "IC-001",
                              "--title", "new", "--status", "dismissed"])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertEqual(row["title"], "new")
        self.assertEqual(row["status"], "dismissed")

    def test_issue_update_cannot_set_filed(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        code, _, _ = run(["issue", "update", "--issue", "IC-001",
                          "--status", "filed"])
        self.assertEqual(code, 2)  # filed is set only by 'issue file'

    def test_issue_update_needs_a_field(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        code, _, err = run(["issue", "update", "--issue", "IC-001"])
        self.assertEqual(code, 2)
        self.assertIn("nothing to update", err)

    def test_issue_file_records_url_and_event(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        url = "https://github.com/agentrof/agent-marketplace/issues/9"
        code, _, err = run(["issue", "file", "--issue", "IC-001", "--url", url])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT * FROM issue_candidates WHERE external_id='IC-001'").fetchone()
        self.assertEqual(row["status"], "filed")
        self.assertEqual(row["issue_url"], url)
        event = self.db().execute(
            "SELECT 1 FROM events WHERE action='issue_candidate_filed'").fetchone()
        self.assertTrue(event)

    def test_issue_file_refuses_double_file(self):
        run(["issue", "open", "--title", "x", "--kind", "defect"])
        run(["issue", "file", "--issue", "IC-001", "--url", "https://a"])
        code, _, err = run(["issue", "file", "--issue", "IC-001",
                            "--url", "https://b"])
        self.assertEqual(code, 2)
        self.assertIn("already filed", err)

    def test_issue_list_filters_by_status_json(self):
        run(["issue", "open", "--title", "a", "--kind", "defect"])
        run(["issue", "open", "--title", "b", "--kind", "improvement"])
        run(["issue", "update", "--issue", "IC-002", "--status", "dismissed"])
        code, out, err = run(["issue", "list", "--status", "candidate", "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["external_id"] for r in rows], ["IC-001"])

    # -- backlog import ------------------------------------------------------

    def test_import_green_and_upsert(self):
        code, out, err = self.import_backlog()
        self.assertEqual(code, 0, err)
        code, _, _ = self.import_backlog()  # idempotent re-import
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "story", "--json"])
        stories = json.loads(out)
        self.assertEqual([s["external_id"] for s in stories], ["WP-01", "WP-02"])

    def test_import_rejects_empty_required_fields(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["dod"] = "  "
        bad["stories"][1]["scope"] = ""
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("WP-01: required field 'dod' is empty", err)
        self.assertIn("WP-02: required field 'scope' is empty", err)

    def test_import_rejects_unknown_epic(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["epic"] = "EP-99"
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("epic 'EP-99' not found", err)

    def test_import_rejects_bad_priority(self):
        bad = json.loads(json.dumps(BACKLOG))
        bad["stories"][0]["priority"] = "P1"
        code, _, err = self.import_backlog(bad)
        self.assertEqual(code, 1)
        self.assertIn("priority tier", err)

    def test_import_materializes_structured_deps(self):
        self.import_backlog()
        code, out, _ = run(["item", "list-deps", "--project-key", "shop", "--json"])
        self.assertEqual(code, 0)
        deps = json.loads(out)
        self.assertEqual(deps, [{
            "item": "WP-02", "depends_on": "WP-01",
            "reason": "editing needs the authenticated session WP-01 ships",
        }])

    def test_import_rejects_dependency_cycle(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][0]["depends_on"] = [{"item": "WP-02", "reason": "loop"}]
        code, _, err = self.import_backlog(data)
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)

    def test_import_syncs_dod_items(self):
        self.import_backlog()
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-01", "--json"])
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "pending" for r in rows))
        # verify one, then re-import with a changed pending statement:
        # the verified row survives, the stale pending row is replaced
        run(["item", "set-dod", "--project-key", "shop",
             "--dod-id", str(rows[0]["id"]), "--status", "verified"])
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][0]["dod_items"] = [
            "A member with a valid token sets a new password and signs in with it.",
            "A reused token is rejected and the event is logged.",
        ]
        self.import_backlog(data)
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-01", "--json"])
        rows = json.loads(out)
        statements = {r["statement"]: r["status"] for r in rows}
        self.assertEqual(statements[
            "A member with a valid token sets a new password and signs in with it."
        ], "verified")
        self.assertIn("A reused token is rejected and the event is logged.",
                      statements)
        self.assertNotIn("An expired token is rejected with the documented message.",
                         statements)

    # -- dependencies and ordering --------------------------------------------

    def test_add_remove_dep_and_cycle_guard(self):
        self.import_backlog()
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "WP-02",
                            "--reason", "closes the loop"])
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)
        self.assertIn("WP-01", err)
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "WP-01"])
        self.assertEqual(code, 1)
        self.assertIn("itself", err)
        code, _, err = run(["item", "add-dep", "--project-key", "shop",
                            "--item", "WP-01", "--depends-on", "EP-01"])
        self.assertEqual(code, 1)
        self.assertIn("epic", err.lower())
        code, _, _ = run(["item", "remove-dep", "--project-key", "shop",
                          "--item", "WP-02", "--depends-on", "WP-01"])
        self.assertEqual(code, 0)
        code, _, err = run(["item", "remove-dep", "--project-key", "shop",
                            "--item", "WP-02", "--depends-on", "WP-01"])
        self.assertEqual(code, 1)

    def test_item_order_deterministic_with_priority_tiebreak(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"].append({
            "external_id": "WP-03", "epic": "EP-01", "title": "Avatar upload",
            "type": "feature", "priority": "low: cosmetic tail",
            "scope": "s", "excludes": "x",
            "dor": "d", "dod": "d",
        })
        self.import_backlog(data)
        code, out, _ = run(["item", "order", "--project-key", "shop",
                            "--kind", "story", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        # WP-01 (critical) before WP-03 (low); WP-02 only after its dep
        self.assertEqual(result["order"], ["WP-01", "WP-02", "WP-03"])
        self.assertEqual(result["cycles"], [])

    # -- dod items -------------------------------------------------------------

    def test_dod_transitions(self):
        self.import_backlog()
        code, out, _ = run(["item", "add-dod", "--project-key", "shop",
                            "--item", "WP-02",
                            "--statement", "The avatar renders on the profile page."])
        self.assertEqual(code, 0)
        dod_id = out.strip().split()[3]
        code, _, err = run(["item", "set-dod", "--project-key", "shop",
                            "--dod-id", dod_id, "--status", "failed"])
        self.assertEqual(code, 1)
        self.assertIn("failure-reason", err)
        code, _, _ = run(["item", "set-dod", "--project-key", "shop",
                          "--dod-id", dod_id, "--status", "failed",
                          "--failure-reason", "renders a broken image link"])
        self.assertEqual(code, 0)
        code, _, _ = run(["item", "set-dod", "--project-key", "shop",
                          "--dod-id", dod_id, "--status", "verified"])
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list-dod", "--project-key", "shop",
                            "--item", "WP-02", "--json"])
        row = json.loads(out)[0]
        self.assertEqual(row["status"], "verified")
        self.assertTrue(row["verified_at"])
        self.assertEqual(row["failure_reason"], "")

    # -- priority --------------------------------------------------------------

    def test_priority_enum_on_update(self):
        self.import_backlog()
        code, _, err = run(["item", "update", "--project-key", "shop",
                            "--external-id", "WP-01", "--priority", "P1"])
        self.assertEqual(code, 1)
        self.assertIn("priority tier", err)
        code, _, _ = run(["item", "update", "--project-key", "shop",
                          "--external-id", "WP-01",
                          "--priority", "medium: demoted after the checkpoint"])
        self.assertEqual(code, 0)

    # -- work-order lifecycle guards -------------------------------------------

    def test_wo_init_snapshots_directory_brief(self):
        """An analysis-space brief snapshots as a whole tree."""
        self.import_backlog()
        space = Path(self.tmp.name) / "erp"
        (space / "rules").mkdir(parents=True)
        (space / "_generated").mkdir()
        (space / "space.md").write_text("# ERP\n", encoding="utf-8")
        (space / "rules" / "core.md").write_text("| BR-ERP-001 |\n", encoding="utf-8")
        (space / "_generated" / "registry.json").write_text("{}", encoding="utf-8")
        order_dir = Path(self.tmp.name) / "orders" / "wo1"
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "wo1", "--request", "build it",
                            "--worktree", str(self.wt_main), "--story", "WP-01",
                            "--order-dir", str(order_dir), "--brief", str(space)])
        self.assertEqual(code, 0, err)
        snap = order_dir / "brief-snapshot"
        self.assertTrue((snap / "space.md").is_file())
        self.assertTrue((snap / "rules" / "core.md").is_file())
        self.assertTrue((snap / "_generated" / "registry.json").is_file())

    def test_wo_init_rejects_file_brief(self):
        self.import_backlog()
        brief = Path(self.tmp.name) / "brief.md"
        brief.write_text("# Brief\n", encoding="utf-8")
        order_dir = Path(self.tmp.name) / "orders" / "wo-file"
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "wo-file", "--request", "build it",
                            "--worktree", str(self.wt_main), "--story", "WP-01",
                            "--order-dir", str(order_dir), "--brief", str(brief)])
        self.assertEqual(code, 1)
        self.assertIn("analysis-space directory", err)
        self.assertFalse(order_dir.exists())

    def test_same_worktree_refused(self):
        self.import_backlog()
        self.assertEqual(self.init_wo()[0], 0)
        code, _, err = self.init_wo(wo_key="wo2", story="WP-02")
        self.assertEqual(code, 1)
        self.assertIn("already holds worktree", err)

    def test_same_story_refused_disjoint_allowed(self):
        self.import_backlog()
        self.assertEqual(self.init_wo()[0], 0)
        code, _, err = self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-01")
        self.assertEqual(code, 1)
        self.assertIn("already claimed", err)
        code, _, err = self.init_wo(wo_key="wo3", worktree=str(self.wt_two), story="WP-02")
        self.assertEqual(code, 0, err)

    def test_story_claim_marks_in_development(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "story", "--status", "in_development",
                            "--json"])
        story = json.loads(out)[0]
        self.assertEqual(story["external_id"], "WP-01")
        # the claimed story points at the work order delivering it
        con = self.db()
        order = con.execute("SELECT id FROM work_orders WHERE"
                            " work_order_key = 'wo1'").fetchone()
        self.assertEqual(story["work_order_id"], order["id"])

    def test_unknown_story_refused(self):
        code, _, err = self.init_wo(story="WP-99")
        self.assertEqual(code, 1)
        self.assertIn("not in the backlog", err)

    def test_transition_guard(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-step", "--work-order-key", "wo1",
                            "--step", "2", "--status", "in_progress"])
        self.assertEqual(code, 1)
        self.assertIn("transition guard", err)

    def test_complete_guard_full_chain(self):
        """Story work orders: steps, findings, coverage and ledger all guard
        completion."""
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("steps not done", err)
        for step in ("0", "1", "2", "3", "4", "5"):
            run(["work-order", "set-step", "--work-order-key", "wo1",
                 "--step", step, "--status", "in_progress"])
            run(["work-order", "set-step", "--work-order-key", "wo1",
                 "--step", step, "--status", "done"])
        run(["finding", "open", "--work-order-key", "wo1", "--source", "review",
             "--severity", "high", "--summary", "broken thing"])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("open findings remain", err)
        run(["finding", "update", "--work-order-key", "wo1", "--finding", "F-001",
             "--status", "fixed", "--round", "1"])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("no coverage rows", err)
        run(["coverage", "import", "--work-order-key", "wo1",
             "--json-file", self.coverage_file()])
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 1)
        self.assertIn("no ledger line", err)
        code, _, err = run(["checkpoint", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["work-order", "set-status", "--work-order-key", "wo1",
                            "--status", "complete"])
        self.assertEqual(code, 0, err)

    def test_storyless_work_order_completes_without_coverage(self):
        self.import_backlog()
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "atomic1",
                            "--request", "small fix",
                            "--worktree", str(self.wt_main)])
        self.assertEqual(code, 0, err)
        for step in ("0", "1", "2", "3", "4", "5"):
            run(["work-order", "set-step", "--work-order-key", "atomic1",
                 "--step", step, "--status", "in_progress"])
            run(["work-order", "set-step", "--work-order-key", "atomic1",
                 "--step", step, "--status", "done"])
        code, _, err = run(["work-order", "set-status",
                            "--work-order-key", "atomic1",
                            "--status", "complete"])
        self.assertEqual(code, 0, err)

    def test_release_frees_worktree_and_claim(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["work-order", "release", "--work-order-key", "wo1"])
        self.assertEqual(code, 0)
        code, _, err = self.init_wo(wo_key="wo2")
        self.assertEqual(code, 0, err)

    def test_reconcile_checkpoint_contract(self):
        code, _, err = self.init_wo(wo_key="reconcile1", story="")
        self.assertEqual(code, 0, err)
        for step in ("0", "1", "2", "3", "4"):
            code, _, err = run([
                "work-order", "set-step", "--work-order-key", "reconcile1",
                "--step", step, "--status", "in_progress",
            ])
            self.assertEqual(code, 0, err)
            code, _, err = run([
                "work-order", "set-step", "--work-order-key", "reconcile1",
                "--step", step, "--status", "done",
            ])
            self.assertEqual(code, 0, err)
        code, _, err = run([
            "work-order", "checkpoint-reconcile",
            "--work-order-key", "reconcile1",
        ])
        self.assertEqual(code, 0, err)
        with self.db() as con:
            order = con.execute(
                "SELECT status, bindings_json FROM work_orders"
                " WHERE work_order_key = 'reconcile1'"
            ).fetchone()
        checkpoint = json.loads(
            order["bindings_json"]
        )["agent_marketplace"]["reconcile"]
        self.assertEqual(order["status"], "blocked")
        self.assertTrue(checkpoint["reservation"])

        current = {
            "status": pmo_cli.upgrade_core.STATUS_CURRENT,
            "reasons": [], "blockers": [], "active_work": [],
        }
        with mock.patch.object(
            pmo_cli.upgrade_core, "environment_status", return_value=current
        ):
            code, _, err = run([
                "work-order", "resume-reconcile",
                "--work-order-key", "reconcile1",
            ])
        self.assertEqual(code, 0, err)
        with self.db() as con:
            order = con.execute(
                "SELECT status, current_step, bindings_json FROM work_orders"
                " WHERE work_order_key = 'reconcile1'"
            ).fetchone()
            steps = {
                row["step_id"]: row["status"] for row in con.execute(
                    "SELECT step_id, status FROM work_order_steps"
                    " WHERE work_order_id = (SELECT id FROM work_orders"
                    " WHERE work_order_key = 'reconcile1')"
                )
            }
        reconcile = json.loads(
            order["bindings_json"]
        )["agent_marketplace"]["reconcile"]
        self.assertEqual((order["status"], order["current_step"]),
                         ("running", "4"))
        self.assertEqual((steps["4"], steps["5"]),
                         ("in_progress", "pending"))
        self.assertFalse(reconcile["reservation"])

    # -- ownership -----------------------------------------------------------

    def test_ownership_snake_case_and_cross_order_overlap(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo1",
                            "--ownership", '{"backend-dev": ["a/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("snake_case", err)
        code, _, _ = run(["work-order", "set-ownership",
                          "--work-order-key", "wo1",
                          "--ownership",
                          '{"backend_developer": ["apps/backend/"]}'])
        self.assertEqual(code, 0)
        self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-02")
        os.chdir(self.wt_two)  # wo2's mutations belong to its own worktree
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo2",
                            "--ownership",
                            '{"frontend_developer": ["apps/backend/app/"]}'])
        self.assertEqual(code, 1)
        self.assertIn("across work orders", err)
        code, _, err = run(["work-order", "set-ownership",
                            "--work-order-key", "wo2",
                            "--ownership",
                            '{"frontend_developer": ["apps/frontend/"]}'])
        self.assertEqual(code, 0, err)

    # -- worktree binding and reactivation (mechanical guards) -----------------

    def test_midflight_verbs_bound_to_worktree(self):
        """Mid-flight mutations are refused from outside the order's claimed
        worktree, naming the owning path; the same call passes from inside."""
        self.import_backlog()
        self.init_wo()  # claimed by wt_main
        os.chdir(self.wt_two)
        for argv in (
            ["work-order", "set-step", "--work-order-key", "wo1",
             "--step", "0", "--status", "done"],
            ["work-order", "record-gate", "--work-order-key", "wo1",
             "--gate", "g", "--decision", "approve"],
            ["finding", "open", "--work-order-key", "wo1",
             "--source", "review", "--severity", "low", "--summary", "x"],
            ["task", "open", "--work-order-key", "wo1",
             "--role", "qa_engineer", "--step", "1", "--title", "t"],
        ):
            code, _, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("belongs to worktree", err)
            self.assertIn(str(self.wt_main.name), err)
        os.chdir(self.wt_main)
        code, _, err = run(["work-order", "set-step", "--work-order-key", "wo1",
                            "--step", "0", "--status", "done"])
        self.assertEqual(code, 0, err)

    def test_release_works_from_anywhere(self):
        self.import_backlog()
        self.init_wo()
        os.chdir(self.wt_two)  # the recovery verb stays unrestricted
        code, _, err = run(["work-order", "release", "--work-order-key", "wo1"])
        self.assertEqual(code, 0, err)

    def test_reactivation_revalidates_claims(self):
        """set-status back to an active status re-runs the init claim checks:
        a story or worktree taken meanwhile refuses by name."""
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        # another lane claims the same story while wo1 is parked
        self.init_wo(wo_key="wo2", worktree=str(self.wt_two), story="WP-01")
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 1)
        self.assertIn("cannot reactivate", err)
        self.assertIn("wo2", err)
        # free the story again: reactivation now succeeds
        os.chdir(self.wt_two)
        run(["work-order", "release", "--work-order-key", "wo2"])
        os.chdir(self.wt_main)
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 0, err)

    def test_reactivation_refuses_taken_worktree(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        # a storyless order grabs the SAME worktree while wo1 is parked
        code, _, err = run(["work-order", "init", "--project-key", "shop",
                            "--work-order-key", "squatter",
                            "--request", "atomic fix",
                            "--worktree", str(self.wt_main)])
        self.assertEqual(code, 0, err)
        code, _, err = run(["work-order", "set-status", "--work-order-key",
                            "wo1", "--status", "running"])
        self.assertEqual(code, 1)
        self.assertIn("now holds worktree", err)

    # -- item ready (dispatch surface) -----------------------------------------

    def ready(self):
        code, out, err = run(["item", "ready", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_item_ready_dep_gating(self):
        self.import_backlog()
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], ["WP-01"])
        self.assertEqual(result["blocked"][0]["external_id"], "WP-02")
        blocker = result["blocked"][0]["blocked_by"][0]
        self.assertEqual(blocker["item"], "WP-01")
        self.assertIn("authenticated session", blocker["reason"])
        run(["item", "update", "--project-key", "shop",
             "--external-id", "WP-01", "--status", "done"])
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], ["WP-02"])
        self.assertEqual(result["blocked"], [])

    def test_item_ready_excludes_claimed(self):
        self.import_backlog()
        self.init_wo()  # claims WP-01
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]], [])
        claimed = result["claimed"][0]
        self.assertEqual(claimed["external_id"], "WP-01")
        self.assertEqual(claimed["work_order_key"], "wo1")
        self.assertIn("wt-main", claimed["worktree"])

    def test_item_ready_stale_in_development(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "release", "--work-order-key", "wo1"])
        result = self.ready()  # story stayed in_development, claim freed
        self.assertEqual(result["stale_in_development"], ["WP-01"])

    def test_item_ready_orders_by_topo_priority(self):
        data = json.loads(json.dumps(BACKLOG))
        data["stories"][1]["depends_on"] = []
        data["stories"].append({
            "external_id": "WP-03", "epic": "EP-01", "title": "Avatar upload",
            "type": "feature", "priority": "low: cosmetic tail",
            "scope": "s", "excludes": "x",
            "dor": "d", "dod": "d",
        })
        self.import_backlog(data)
        result = self.ready()
        self.assertEqual([r["external_id"] for r in result["ready"]],
                         ["WP-01", "WP-02", "WP-03"])  # critical, high, low
        self.assertEqual([r["topo_position"] for r in result["ready"]],
                         [1, 2, 3])

    def test_resume_info_carries_ownership_map(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "set-ownership", "--work-order-key", "wo1",
             "--ownership", '{"backend_developer": ["apps/backend/"]}'])
        code, out, _ = run(["resume-info", "--project-key", "shop", "--json"])
        info = json.loads(out)
        self.assertEqual(info["active_work_orders"][0]["ownership"],
                         {"backend_developer": ["apps/backend/"]})

    # -- tasks and attempts ----------------------------------------------------

    def test_task_open_touch_close(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["task", "open", "--work-order-key", "wo1",
                          "--role", "backend_developer", "--step", "2",
                          "--title", "implement slice"])
        self.assertEqual(code, 0)
        code, _, _ = run(["task", "touch", "--project-key", "shop",
                          "--role", "backend_developer", "--phase", "start"])
        self.assertEqual(code, 0)
        code, out, _ = run(["item", "list", "--project-key", "shop",
                            "--kind", "task", "--json"])
        task = json.loads(out)[0]
        self.assertEqual(task["status"], "in_progress")
        self.assertTrue(task["started_at"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop"])
        code, _, _ = run(["task", "close", "--work-order-key", "wo1",
                          "--role", "backend_developer", "--outcome", "done"])
        self.assertEqual(code, 0)
        _, out, _ = run(["item", "list", "--project-key", "shop",
                         "--kind", "task", "--json"])
        task = json.loads(out)[0]
        self.assertEqual(task["status"], "done")
        self.assertTrue(task["finished_at"])

    def test_task_touch_without_open_task_auto_creates(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["task", "touch", "--project-key", "shop",
                            "--role", "qa_engineer", "--phase", "start"])
        self.assertEqual(code, 0)
        self.assertIn("auto-opened", out)

    def test_concurrent_task_starts_commit_without_loss(self):
        self.import_backlog()
        self.init_wo()
        processes = []
        for index in range(12):
            processes.append(subprocess.Popen(
                [sys.executable, str(CLI_PATH), "task", "touch",
                 "--project-key", "shop", "--role", f"worker_{index}",
                 "--phase", "start", "--session-id", f"session-{index}"],
                cwd=self.wt_main,
                env={**os.environ},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))
        for process in processes:
            _, err = process.communicate(timeout=40)
            self.assertEqual(process.returncode, 0, err)
        code, out, err = run([
            "item", "list", "--project-key", "shop", "--kind", "task", "--json"
        ])
        self.assertEqual(code, 0, err)
        tasks = json.loads(out)
        self.assertEqual({task["role"] for task in tasks}, {
            f"worker_{index}" for index in range(12)
        })
        code, _, err = run(["verify"])
        self.assertEqual(code, 0, err)

    def test_attempt_lifecycle(self):
        """Every dispatch is one attempt row: start opens, stop closes with
        cost, a second start supersedes the dangling first."""
        self.import_backlog()
        self.init_wo()
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s1", "--agent", "backend-developer"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s2", "--agent", "backend-developer"])
        run(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop",
             "--session-id", "s2", "--cost-usd", "1.25"])
        con = self.db()
        attempts = [dict(r) for r in con.execute(
            "SELECT * FROM task_attempts ORDER BY attempt")]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["outcome"], "failed")
        self.assertIn("superseded", attempts[0]["failure_reason"])
        self.assertEqual(attempts[0]["session_id"], "s1")
        self.assertEqual(attempts[1]["outcome"], "done")
        self.assertEqual(attempts[1]["cost_usd"], 1.25)
        self.assertEqual(attempts[1]["agent_name"],
                         "backend-developer")
        actions = []
        for row in con.execute("SELECT action FROM events ORDER BY id"):
            actions.append(row["action"])
        self.assertIn("attempt_started", actions)
        self.assertIn("attempt_finished", actions)

    def test_task_close_closes_running_attempt(self):
        self.import_backlog()
        self.init_wo()
        run(["task", "touch", "--project-key", "shop",
             "--role", "qa_engineer", "--phase", "start", "--session-id", "s1"])
        run(["task", "close", "--work-order-key", "wo1",
             "--role", "qa_engineer", "--outcome", "blocked"])
        con = self.db()
        attempt = con.execute("SELECT * FROM task_attempts").fetchone()
        self.assertEqual(attempt["outcome"], "blocked")
        self.assertTrue(attempt["finished_at"])

    def test_finding_lifecycle(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run([
            "finding", "open", "--work-order-key", "wo1",
            "--source", "review", "--severity", "high",
            "--summary", "contract mismatch",
        ])
        self.assertEqual(code, 0, err)
        code, out, err = run([
            "finding", "list", "--work-order-key", "wo1", "--json",
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["status"], "open")
        code, _, err = run([
            "finding", "update", "--work-order-key", "wo1",
            "--finding", "F-001", "--status", "fixed", "--round", "1",
        ])
        self.assertEqual(code, 0, err)
        code, out, err = run([
            "finding", "list", "--work-order-key", "wo1",
            "--status", "fixed", "--json",
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["closed_round"], 1)

    def test_event_append_contract(self):
        code, _, err = run([
            "event", "append", "--project-key", "shop",
            "--action", "quality_probe", "--actor", "test",
            "--payload", '{"gate":"green"}',
        ])
        self.assertEqual(code, 0, err)
        row = self.db().execute(
            "SELECT actor, action, payload_json FROM events"
            " WHERE action = 'quality_probe'"
        ).fetchone()
        self.assertEqual(row["actor"], "test")
        self.assertEqual(json.loads(row["payload_json"]), {"gate": "green"})

    # -- coverage, budgets, ledger --------------------------------------------

    def coverage_file(self):
        path = Path(self.tmp.name) / "coverage.json"
        path.write_text(json.dumps({"rows": [
            {"id": "AC-001", "result": "PASS", "tests": ["test_reset[AC-001]"]},
            {"id": "AC-002", "result": "NO-TEST", "tests": []},
        ]}), encoding="utf-8")
        return str(path)

    def test_coverage_list_reads_story_criteria(self):
        self.import_backlog()
        code, out, err = run(["coverage", "list", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["criterion_id"] for r in rows],
                         ["AC-001", "AC-002"])
        self.assertEqual(rows[0]["story"], "WP-01")
        self.assertEqual(rows[0]["disposition"], "covered")
        self.assertEqual(rows[1]["story"], None)
        self.assertEqual(rows[1]["disposition"], "deferred")
        self.assertEqual(rows[1]["reason"], "Out of v1.")
        code, out, _ = run(["coverage", "list", "--project-key", "shop",
                            "--story", "WP-01", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual([r["criterion_id"] for r in json.loads(out)],
                         ["AC-001"])

    def test_ledger_list_decodes_finding_counts(self):
        self.import_backlog()
        self.init_wo()
        run(["finding", "open", "--work-order-key", "wo1", "--source", "qa",
             "--severity", "low", "--summary", "cosmetic"])
        run(["ledger", "checkpoint", "--work-order-key", "wo1",
             "--escaped-defect"])
        run(["ledger", "checkpoint", "--work-order-key", "wo1"])
        code, out, err = run(["ledger", "list", "--project-key", "shop",
                              "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["story"], "WP-01")
        self.assertEqual(rows[0]["work_order_key"], "wo1")
        self.assertEqual(rows[0]["finding_counts"], {"qa_low": 1})
        self.assertTrue(rows[0]["escaped_defect"])
        self.assertNotIn("finding_counts_json", rows[0])
        code, out, _ = run(["ledger", "list", "--project-key", "shop",
                            "--tail", "1", "--json"])
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["escaped_defect"])

    def test_coverage_import_and_replace(self):
        self.import_backlog()
        self.init_wo()
        code, _, err = run(["coverage", "import", "--work-order-key", "wo1",
                            "--json-file", self.coverage_file()])
        self.assertEqual(code, 0, err)
        code, _, _ = run(["coverage", "import", "--work-order-key", "wo1",
                          "--json-file", self.coverage_file()])
        self.assertEqual(code, 0)  # re-import replaces, does not duplicate

    def test_budget_and_ledger(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["budget", "set", "--work-order-key", "wo1",
                          "--budget-id", "BR-010", "--verdict", "unverified",
                          "--reason", "load-only budget; no rig in scope"])
        self.assertEqual(code, 0)
        run(["finding", "open", "--work-order-key", "wo1", "--source", "qa",
             "--severity", "low", "--summary", "cosmetic"])
        code, _, _ = run(["ledger", "checkpoint", "--work-order-key", "wo1",
                          "--escaped-defect"])
        self.assertEqual(code, 0)
        code, out, _ = run(["ledger", "list", "--project-key", "shop",
                            "--json"])
        self.assertEqual(code, 0)
        row = json.loads(out)[0]
        self.assertEqual(row["finding_counts"], {"qa_low": 1})
        self.assertTrue(row["escaped_defect"])

    # -- audit trail ----------------------------------------------------------

    def test_every_mutation_writes_an_event(self):
        self.import_backlog()
        self.init_wo()
        run(["work-order", "set-step", "--work-order-key", "wo1", "--step", "0",
             "--status", "done"])
        run(["work-order", "record-gate", "--work-order-key", "wo1",
             "--gate", "design_gate", "--decision", "approved"])
        run(["work-order", "bump", "--work-order-key", "wo1",
             "--counter", "review"])
        code, out, _ = run(["resume-info", "--project-key", "shop",
                            "--events", "50", "--json"])
        self.assertEqual(code, 0)
        actions = [e["action"] for e in json.loads(out)["recent_events"]]
        for expected in ("backlog_imported", "work_order_initialized",
                         "step_changed", "gate_recorded", "round_bumped"):
            self.assertIn(expected, actions)

    def test_resume_info_reports_work_order_shape(self):
        self.import_backlog()
        self.init_wo()
        code, out, _ = run(["resume-info", "--project-key", "shop", "--json"])
        self.assertEqual(code, 0)
        info = json.loads(out)
        self.assertEqual(len(info["active_work_orders"]), 1)
        active = info["active_work_orders"][0]
        self.assertEqual(active["current_step"], "0")
        self.assertIn("WP-01", active["story"])

    def test_work_order_validate(self):
        self.import_backlog()
        self.init_wo()
        code, _, _ = run(["work-order", "validate", "--work-order-key", "wo1"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
