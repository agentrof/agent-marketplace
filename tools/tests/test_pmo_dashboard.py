"""Hermetic PMO dashboard contracts: routes, read-only enforcement,
catalog scan, HTTP adapter headers, and failure-mode responses."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "dist" / "claude" / "project-management-office" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cli(argv, env, cwd=None):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "pmo_cli.py"), *argv],
        capture_output=True, text=True, cwd=cwd,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


class PmoDashboardTests(unittest.TestCase):
    """One seeded database for the whole class. Request dispatch stays
    in-process so the default test gate never requires a network socket."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.home = str(root / "agentrof")
        cls._old_home = os.environ.get("AGENT_MARKETPLACE_HOME")
        cls._old_plugins = os.environ.get("AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR")
        os.environ["AGENT_MARKETPLACE_HOME"] = cls.home
        env = {"AGENT_MARKETPLACE_HOME": cls.home}
        project_root = root / "project"
        (project_root / "workspace").mkdir(parents=True)
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "dashboard@example.test"],
            ["git", "config", "user.name", "Dashboard Tests"],
        ):
            subprocess.run(command, cwd=project_root, check=True)
        contract = {
            "schema_version": 1, "contract_version": 5,
            "project_id": "dashboard-project",
            "team_id": "software-engineering-team",
            "workspace": "workspace", "repository_fingerprint": "test",
            "delivery": {"requires_pull_request": False,
                         "target_branch": "master"},
            "marketplace_release": "0.1.0", "source_channel": "stable",
            "source_ref": "v0.1.0", "source_commit": "test",
            "components": {}, "managed_surfaces": {}, "vault": {},
            "upgrade_provenance": {},
        }
        contract["contract_sha256"] = hashlib.sha256(json.dumps(
            contract, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        (project_root / "workspace" / "config.json").write_text(json.dumps({
            "project_key": "shop", "agent_marketplace": contract,
        }), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project_root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "baseline"],
            cwd=project_root, check=True,
        )

        code, _, err = cli(["init-db"], env)
        assert code == 0, err
        cli(["project", "register", "--key", "shop", "--name", "Shop",
             "--team", "software-engineering-team"], env)
        backlog = root / "backlog.json"
        backlog.write_text(json.dumps({
            "epics": [{"external_id": "EP-01", "title": "Core", "goal": "g"}],
            "stories": [
                {"external_id": "WP-01", "epic": "EP-01", "title": "Base",
                 "priority": "critical: walking skeleton", "scope": "s",
                 "excludes": "x", "dor": "d", "dod": "d",
                 "dod_items": ["The submit endpoint returns 201."]},
                {"external_id": "WP-02", "epic": "EP-01", "title": "Next",
                 "priority": "high: closes the loop", "scope": "s",
                 "excludes": "x", "dor": "d", "dod": "d",
                 "depends_on": [{"item": "WP-01", "reason": "state model"}]},
            ],
            "criteria": [{"criterion_id": "AC-001", "story": "WP-01",
                          "disposition": "covered"}],
            "open_questions": ["Mail provider?"],
        }), encoding="utf-8")
        cli(["item", "import", "--project-key", "shop",
             "--json-file", str(backlog)], env)
        # the worktree-binding guard compares each caller's cwd to the claimed
        # worktree, so the seed claims the test runner's own directory
        cli(["work-order", "init", "--project-key", "shop",
             "--work-order-key", "wo1", "--request", "build",
             "--worktree", str(project_root), "--story", "WP-01"], env,
            cwd=project_root)
        cli(["work-order", "set-step", "--work-order-key", "wo1",
             "--step", "0", "--status", "done"], env, cwd=project_root)
        cli(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s1",
             "--agent", "backend-developer"], env, cwd=project_root)
        cli(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop",
             "--cost-usd", "0.42"], env, cwd=project_root)
        cli(["finding", "open", "--work-order-key", "wo1", "--source", "review",
             "--severity", "high", "--summary", "bug one"], env,
            cwd=project_root)
        cli(["ledger", "checkpoint", "--work-order-key", "wo1"], env,
            cwd=project_root)

        # a fake Claude plugin registry for the catalog scan
        plugins = root / "plugins"
        install = plugins / "cache" / "agent-marketplace" / "software-engineering-team" / "9.9.9"
        (install / ".claude-plugin").mkdir(parents=True)
        (install / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": "software-engineering-team", "version": "9.9.9",
             "description": "The software team.", "dependencies": ["project-management-office"]}))
        (install / "agents").mkdir()
        (install / "agents" / "backend-developer.md").write_text(
            "---\nname: backend-developer\n"
            "description: Builds backends.\nmodel: sonnet\n---\n# X\n")
        (install / "skills" / "product-planning").mkdir(parents=True)
        (install / "skills" / "product-planning" / "SKILL.md").write_text(
            "---\nname: product-planning\ndescription: Planning knowledge.\n"
            "user-invocable: false\n---\n# X\n")
        (plugins / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "software-engineering-team@agent-marketplace": [
                    {"scope": "project", "installPath": str(install),
                     "version": "9.9.9",
                     "lastUpdated": "2026-07-12T00:00:00Z"}],
                "unrelated@other": [
                    {"scope": "user", "installPath": str(root),
                     "version": "1.0.0",
                     "lastUpdated": "2026-07-11T00:00:00Z"}],
            },
        }))
        os.environ["AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR"] = str(plugins)

        cls.dashboard = load("pmo_dashboard")

    @classmethod
    def tearDownClass(cls):
        for key, value in (("AGENT_MARKETPLACE_HOME", cls._old_home),
                           ("AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR", cls._old_plugins)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.tmp.cleanup()

    def get(self, path):
        status, body, content_type = self.dashboard.dispatch_request("GET", path)
        if "json" in content_type:
            return status, json.loads(body)
        return status, body

    # -- endpoint shapes -------------------------------------------------------

    def test_head(self):
        status, head = self.get("/api/head")
        self.assertEqual(status, 200)
        self.assertTrue(head["db_present"])
        self.assertGreater(head["head_id"], 0)
        self.assertEqual(head["schema_version"],
                         self.dashboard.pmo_cli.SCHEMA_VERSION)
        # status-skill parity: the head carries the system facts
        self.assertEqual(head["db_path"], str(Path(self.home) / "pmo.db"))
        self.assertTrue(head["cli_version"])

    def test_overview(self):
        status, overview = self.get("/api/overview")
        self.assertEqual(status, 200)
        project = overview["projects"][0]
        self.assertEqual(project["project_key"], "shop")
        self.assertEqual(project["teams"], ["software-engineering-team"])
        self.assertEqual(project["open_findings"], 1)
        self.assertEqual(project["item_counts"]["story"]["in_development"], 1)
        active = project["active_work_orders"][0]
        self.assertEqual(active["work_order_key"], "wo1")
        self.assertFalse(active["dangling"])

    def test_dangling_flag_follows_last_event(self):
        """A session-ended event as the order's LAST event flips dangling on;
        any later activity flips it back off."""
        env = {"AGENT_MARKETPLACE_HOME": self.home}
        cli(["event", "append", "--project-key", "shop",
             "--action", "session_ended_with_active_work_order",
             "--actor", "hook", "--work-order-key", "wo1"], env)
        status, overview = self.get("/api/overview")
        self.assertEqual(status, 200)
        self.assertTrue(overview["projects"][0]["active_work_orders"][0]["dangling"])
        cli(["event", "append", "--project-key", "shop",
             "--action", "lane_resumed", "--actor", "hook",
             "--work-order-key", "wo1"], env)
        status, overview = self.get("/api/overview")
        self.assertFalse(overview["projects"][0]["active_work_orders"][0]["dangling"])

    def test_catalog_scans_team_plugins_only(self):
        status, catalog = self.get("/api/catalog")
        self.assertEqual(status, 200)
        teams = {team["plugin_name"]: team for team in catalog["teams"]}
        self.assertIn("software-engineering-team", teams)
        self.assertNotIn("unrelated", teams)
        team = teams["software-engineering-team"]
        self.assertTrue(team["in_use"])
        self.assertTrue(team["installed"])
        self.assertEqual(team["agents"][0]["model"], "sonnet")
        self.assertEqual(team["skills"][0]["name"], "product-planning")
        self.assertFalse(team["skills"][0]["user_invocable"])
        self.assertEqual(team["installs"][0]["version"], "9.9.9")

    def test_project_tree_deps_dod_order(self):
        status, project = self.get("/api/project?key=shop")
        self.assertEqual(status, 200)
        story = project["epics"][0]["stories"][0]
        self.assertEqual(story["external_id"], "WP-01")
        self.assertEqual(story["dod_items"][0]["statement"],
                         "The submit endpoint returns 201.")
        self.assertEqual(story["tasks"][0]["external_id"], "T-001")
        self.assertEqual(project["deps"], [{
            "item": "WP-02", "depends_on": "WP-01", "reason": "state model"}])
        self.assertEqual(project["order"]["stories"]["order"],
                         ["WP-01", "WP-02"])
        self.assertEqual(project["criteria"][0]["criterion_id"], "AC-001")
        status, _ = self.get("/api/project?key=ghost")
        self.assertEqual(status, 404)
        status, _ = self.get("/api/project")
        self.assertEqual(status, 400)

    def test_work_orders_and_detail(self):
        status, orders = self.get("/api/work_orders?project_key=shop")
        self.assertEqual(status, 200)
        summary = orders["work_orders"][0]
        self.assertEqual(summary["steps_done"], 1)
        self.assertEqual(summary["steps_total"], 6)
        status, detail = self.get("/api/work_order?key=wo1")
        self.assertEqual(status, 200)
        self.assertEqual(detail["findings"][0]["external_id"], "F-001")
        attempt = detail["tasks"][0]["attempts"][0]
        self.assertEqual(attempt["cost_usd"], 0.42)
        self.assertEqual(attempt["session_id"], "s1")
        self.assertTrue(any(e["action"] == "attempt_finished"
                            for e in detail["events"]))

    def test_ledger(self):
        status, ledger = self.get("/api/ledger?project_key=shop")
        self.assertEqual(status, 200)
        self.assertEqual(ledger["rows"][0]["finding_counts"], {"review_high": 1})

    def test_events_cursor(self):
        status, page = self.get("/api/events?since_id=0&project_key=shop&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(len(page["events"]), 5)
        last_id = page["events"][-1]["id"]
        status, rest = self.get(f"/api/events?since_id={last_id}")
        self.assertTrue(all(e["id"] > last_id for e in rest["events"]))

    def test_issue_candidates_endpoint(self):
        env = {"AGENT_MARKETPLACE_HOME": self.home}
        code, _, err = cli(["issue", "open", "--title", "dash candidate",
                            "--kind", "improvement"], env)
        self.assertEqual(code, 0, err)
        status, data = self.get("/api/issue_candidates")
        self.assertEqual(status, 200)
        self.assertIn("IC-001",
                      [c["external_id"] for c in data["issue_candidates"]])
        status, filtered = self.get("/api/issue_candidates?status=candidate")
        self.assertEqual(status, 200)
        self.assertTrue(all(c["status"] == "candidate"
                            for c in filtered["issue_candidates"]))

    def test_overview_reports_open_candidates(self):
        env = {"AGENT_MARKETPLACE_HOME": self.home}
        cli(["issue", "open", "--title", "shown in overview",
             "--kind", "defect"], env)
        status, data = self.get("/api/overview")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["open_candidates"], 1)
        self.assertIn("shown in overview",
                      [c["title"] for c in data["issue_candidates"]])

    def test_dashboard_reads_remain_available_during_concurrent_writes(self):
        env = {"AGENT_MARKETPLACE_HOME": self.home}

        def append_event(index):
            return cli([
                "event", "append", "--project-key", "shop",
                "--action", f"concurrency_probe_{index}", "--actor", "test",
            ], env)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(append_event, index) for index in range(24)]
            reads = 0
            while True:
                status, body = self.get("/api/overview")
                self.assertEqual(status, 200, body)
                self.assertIn("projects", body)
                reads += 1
                if all(future.done() for future in futures):
                    break
            results = [future.result() for future in futures]

        self.assertGreater(reads, 0)
        for code, _, err in results:
            self.assertEqual(code, 0, err)

    # -- static and hardening --------------------------------------------------

    def test_index_served_at_root(self):
        status, raw = self.get("/")
        self.assertEqual(status, 200)
        body = raw.decode("utf-8", errors="replace")
        self.assertIn("<title>Control Tower</title>", body)
        # served self-contained: no external runtime URLs in src/href
        import re
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', body)
        self.assertEqual(external, [])

    def test_unknown_and_traversal_paths_404(self):
        for path in ("/api/nope", "/../../etc/passwd", "/etc/passwd",
                     "/..%2f..%2fetc%2fpasswd"):
            status, _ = self.get(path)
            self.assertEqual(status, 404, path)

    def test_non_get_methods_405(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, body, content_type = self.dashboard.dispatch_request(
                method, "/api/head")
            self.assertEqual(status, 405, method)
            self.assertIn("json", content_type)
            self.assertEqual(json.loads(body)["error"], "method_not_allowed")

    def test_http_adapter_sets_no_store_and_exact_length(self):
        handler = self.dashboard.Handler.__new__(self.dashboard.Handler)
        headers = {}
        statuses = []
        handler.wfile = io.BytesIO()
        handler.send_response = statuses.append
        handler.send_header = headers.__setitem__
        handler.end_headers = lambda: None
        handler._write(200, b"payload", "text/plain")
        self.assertEqual(statuses, [200])
        self.assertEqual(headers["Content-Length"], "7")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(handler.wfile.getvalue(), b"payload")

    def test_every_registered_route_has_a_success_contract(self):
        cases = {
            "/api/head": ("/api/head", {"db_present", "head_id"}),
            "/api/overview": ("/api/overview", {"projects"}),
            "/api/catalog": ("/api/catalog", {"teams"}),
            "/api/project": ("/api/project?key=shop", {"project", "epics"}),
            "/api/work_orders": ("/api/work_orders?project_key=shop", {"work_orders"}),
            "/api/work_order": ("/api/work_order?key=wo1", {"work_order_key", "tasks"}),
            "/api/ledger": ("/api/ledger?project_key=shop", {"rows"}),
            "/api/events": ("/api/events?since_id=0", {"events", "head_id"}),
            "/api/issue_candidates": ("/api/issue_candidates", {"issue_candidates"}),
        }
        self.assertEqual(set(cases), set(self.dashboard.ROUTES))
        for route, (target, expected_keys) in cases.items():
            with self.subTest(route=route):
                status, body = self.get(target)
                self.assertEqual(status, 200, body)
                self.assertTrue(expected_keys <= set(body), body)

    def test_read_only_connection_rejects_writes(self):
        connection = self.dashboard.open_ro()
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                connection.execute(
                    "INSERT INTO projects (project_key, name, created_at)"
                    " VALUES ('x', 'x', 'x')")
            self.assertIn("readonly", str(ctx.exception))
        finally:
            connection.close()

    def test_missing_db_and_noncurrent_schema_responses(self):
        try:
            os.environ["AGENT_MARKETPLACE_HOME"] = self.tmp.name + "/nowhere"
            status, head = self.get("/api/head")
            self.assertEqual(status, 200)
            self.assertFalse(head["db_present"])
            self.assertEqual(head["head_id"], 0)
            self.assertIsNone(head["schema_version"])
            self.assertIn("nowhere", head["db_path"])
            status, body = self.get("/api/overview")
            self.assertEqual(status, 503)
            self.assertEqual(body["error"], "db_missing")
        finally:
            os.environ["AGENT_MARKETPLACE_HOME"] = self.home
        connection = sqlite3.connect(Path(self.home) / "pmo.db")
        try:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        finally:
            connection.close()
        try:
            status, body = self.get("/api/overview")
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "schema_mismatch")
            status, head = self.get("/api/head")
            self.assertEqual(status, 200)
            self.assertEqual(head["schema_version"], 99)
        finally:
            connection = sqlite3.connect(Path(self.home) / "pmo.db")
            connection.execute(f"PRAGMA user_version = {self.dashboard.pmo_cli.SCHEMA_VERSION}")
            connection.commit()
            connection.close()


if __name__ == "__main__":
    unittest.main()
