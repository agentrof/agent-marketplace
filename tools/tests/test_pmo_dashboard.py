"""Unit tests for the PMO dashboard server: endpoint shapes over real HTTP,
read-only enforcement, catalog scan, and failure-mode responses."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "pmo" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cli(argv, env):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "pmo_cli.py"), *argv],
        capture_output=True, text=True, env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


class PmoDashboardTests(unittest.TestCase):
    """One seeded database and one live server for the whole class; tests
    that need a broken database swap AGENTROF_HOME and restore it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.home = str(root / "agentrof")
        cls._old_home = os.environ.get("AGENTROF_HOME")
        cls._old_plugins = os.environ.get("AGENTROF_PLUGINS_DIR")
        os.environ["AGENTROF_HOME"] = cls.home
        env = {"AGENTROF_HOME": cls.home}

        code, _, err = cli(["init-db"], env)
        assert code == 0, err
        cli(["project", "register", "--key", "shop", "--name", "Shop",
             "--team", "software-team"], env)
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
             "--worktree", os.getcwd(), "--story", "WP-01"], env)
        cli(["work-order", "set-step", "--work-order-key", "wo1",
             "--step", "0", "--status", "done"], env)
        cli(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "start",
             "--session-id", "s1",
             "--agent", "software-team-backend-developer"], env)
        cli(["task", "touch", "--project-key", "shop",
             "--role", "backend_developer", "--phase", "stop",
             "--cost-usd", "0.42"], env)
        cli(["finding", "open", "--work-order-key", "wo1", "--source", "review",
             "--severity", "high", "--summary", "bug one"], env)
        cli(["ledger", "checkpoint", "--work-order-key", "wo1"], env)

        # a fake Claude plugin registry for the catalog scan
        plugins = root / "plugins"
        install = plugins / "cache" / "agent-marketplace" / "software-team" / "9.9.9"
        (install / ".claude-plugin").mkdir(parents=True)
        (install / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": "software-team", "version": "9.9.9",
             "description": "The software team.", "dependencies": ["pmo"]}))
        (install / "agents").mkdir()
        (install / "agents" / "software-team-backend-developer.md").write_text(
            "---\nname: software-team-backend-developer\n"
            "description: Builds backends.\nmodel: sonnet\n---\n# X\n")
        (install / "skills" / "product-planning").mkdir(parents=True)
        (install / "skills" / "product-planning" / "SKILL.md").write_text(
            "---\nname: product-planning\ndescription: Planning knowledge.\n"
            "user-invocable: false\n---\n# X\n")
        (plugins / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "software-team@agent-marketplace": [
                    {"scope": "project", "installPath": str(install),
                     "version": "9.9.9",
                     "lastUpdated": "2026-07-12T00:00:00Z"}],
                "unrelated@other": [
                    {"scope": "user", "installPath": str(root),
                     "version": "1.0.0",
                     "lastUpdated": "2026-07-11T00:00:00Z"}],
            },
        }))
        os.environ["AGENTROF_PLUGINS_DIR"] = str(plugins)

        cls.dashboard = load("pmo_dashboard")
        cls.server = cls.dashboard.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for key, value in (("AGENTROF_HOME", cls._old_home),
                           ("AGENTROF_PLUGINS_DIR", cls._old_plugins)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}") as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read()
                if "json" in content_type:
                    return response.status, json.loads(body)
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if body.strip().startswith(b"{"):
                return exc.code, json.loads(body)
            return exc.code, body

    # -- endpoint shapes -------------------------------------------------------

    def test_head(self):
        status, head = self.get("/api/head")
        self.assertEqual(status, 200)
        self.assertTrue(head["db_present"])
        self.assertGreater(head["head_id"], 0)
        self.assertEqual(head["schema_version"], 2)

    def test_overview(self):
        status, overview = self.get("/api/overview")
        self.assertEqual(status, 200)
        project = overview["projects"][0]
        self.assertEqual(project["project_key"], "shop")
        self.assertEqual(project["teams"], ["software-team"])
        self.assertEqual(project["open_findings"], 1)
        self.assertEqual(project["item_counts"]["story"]["in_development"], 1)
        active = project["active_work_orders"][0]
        self.assertEqual(active["work_order_key"], "wo1")
        self.assertFalse(active["dangling"])

    def test_catalog_scans_team_plugins_only(self):
        status, catalog = self.get("/api/catalog")
        self.assertEqual(status, 200)
        teams = {team["plugin_name"]: team for team in catalog["teams"]}
        self.assertIn("software-team", teams)
        self.assertNotIn("unrelated", teams)
        team = teams["software-team"]
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

    # -- static and hardening --------------------------------------------------

    def test_index_served_at_root(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            body = response.read().decode("utf-8", errors="replace")
        self.assertIn("<title>Agentrof PMO</title>", body)
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
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/head",
                method=method, data=b"{}")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request)
            self.assertEqual(ctx.exception.code, 405, method)

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

    def test_missing_db_and_newer_schema_responses(self):
        try:
            os.environ["AGENTROF_HOME"] = self.tmp.name + "/nowhere"
            status, head = self.get("/api/head")
            self.assertEqual(status, 200)
            self.assertEqual(head, {"db_present": False, "head_id": 0,
                                    "schema_version": None})
            status, body = self.get("/api/overview")
            self.assertEqual(status, 503)
            self.assertEqual(body["error"], "db_missing")
        finally:
            os.environ["AGENTROF_HOME"] = self.home
        connection = sqlite3.connect(Path(self.home) / "agentrof.db")
        try:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        finally:
            connection.close()
        try:
            status, body = self.get("/api/overview")
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "schema_newer")
            status, head = self.get("/api/head")
            self.assertEqual(status, 200)
            self.assertEqual(head["schema_version"], 99)
        finally:
            connection = sqlite3.connect(Path(self.home) / "agentrof.db")
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
            connection.close()


if __name__ == "__main__":
    unittest.main()
