#!/usr/bin/env python3
"""Control Tower: the read-only localhost dashboard over the central database.

Serves one self-contained HTML page plus a JSON API. The single-writer rule
is enforced mechanically, not by instruction: every request opens the SQLite
file with mode=ro (writes raise OperationalError) and the router exposes GET
routes only. Launched through the CLI: pmo_cli.py dashboard [--port ...].

Master data is hybrid by design: the catalog of team plugins, their agents,
skills and commands is scanned live from supported host install records;
usage facts (projects, work orders, tasks, attempts, findings) are read out
of the database. Stdlib only.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmo_cli

EVENT_LIMIT_MAX = 500
SESSION_ENDED_ACTION = "session_ended_with_active_work_order"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.body = {"error": code, "message": message, **extra}


def find_index_html() -> Path | None:
    path = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
    return path if path.is_file() else None


def open_ro() -> sqlite3.Connection:
    """A fresh read-only connection per request: cannot write by construction,
    never crosses threads, never holds the WAL long enough to pin it."""
    if not pmo_cli.db_path().is_file():
        raise ApiError(503, "db_missing",
                       "the PMO database does not exist yet; run a session"
                       " in a managed project first")
    uri = pmo_cli.db_path().resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as exc:
        raise ApiError(503, "db_unavailable", f"cannot open the database: {exc}")
    con.row_factory = sqlite3.Row
    con.create_function(
        "agent_marketplace_writer_epoch", 0,
        lambda: pmo_cli.upgrade_core.WRITER_EPOCH,
        deterministic=True,
    )
    return con


def check_schema(con: sqlite3.Connection) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != pmo_cli.SCHEMA_VERSION:
        raise ApiError(409, "schema_mismatch",
                       "the database schema does not match this dashboard;"
                       " start with a database created by this plugin version",
                       db_schema=version, cli_schema=pmo_cli.SCHEMA_VERSION)
    return version


def rows(con, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params)]


def one_param(params: dict, name: str) -> str:
    value = params.get(name, [""])[0].strip()
    if not value:
        raise ApiError(400, "missing_param", f"query parameter '{name}' is required")
    return value


# ---------------------------------------------------------------------------
# Catalog scan (filesystem master data)
# ---------------------------------------------------------------------------


def plugins_dir() -> Path:
    override = os.environ.get("AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR", "").strip()
    return Path(override) if override else Path.home() / ".claude" / "plugins"


def codex_plugins_dir() -> Path:
    override = os.environ.get("AGENT_MARKETPLACE_CODEX_PLUGINS_DIR", "").strip()
    return Path(override) if override else Path.home() / ".codex" / "plugins" / "cache"


def frontmatter(path: Path) -> dict:
    """The top-level 'key: value' string pairs of a markdown frontmatter
    block. Not a YAML parser; enough for name/description/model lines."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if line[:1] in (" ", "\t", "#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("'\"")
    return meta


def scan_install(install_path: Path) -> dict:
    manifest = {}
    manifest_path = next((path for path in (
        install_path / ".codex-plugin" / "plugin.json",
        install_path / ".claude-plugin" / "plugin.json",
    ) if path.is_file()), None)
    if manifest_path is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    agents = []
    for agent_file in sorted((install_path / "agents").glob("*.md")):
        meta = frontmatter(agent_file)
        agents.append({"name": meta.get("name", agent_file.stem),
                       "description": meta.get("description", ""),
                       "model": meta.get("model", "")})
    skills = []
    configured_skills = str(manifest.get("skills", "./skills/")).strip()
    skills_root = install_path / configured_skills.removeprefix("./")
    try:
        skills_root.resolve().relative_to(install_path.resolve())
    except ValueError:
        skills_root = install_path / "skills"
    for skill_file in sorted(skills_root.glob("*/SKILL.md")):
        meta = frontmatter(skill_file)
        skills.append({"name": meta.get("name", skill_file.parent.name),
                       "description": meta.get("description", ""),
                       "user_invocable": meta.get("user-invocable", "") != "false"})
    commands = [{"name": f.stem}
                for f in sorted((install_path / "commands").glob("*.md"))]
    flows = [{"name": f.stem}
             for f in sorted((install_path / "flows").glob("*.md"))]
    return {"manifest": manifest, "agents": agents, "skills": skills,
            "commands": commands, "flows": flows}


def scan_codex_cache(teams: dict, errors: list) -> None:
    """Discover Agent Marketplace installs before their SessionStart hook registers."""
    cache = codex_plugins_dir()
    if not cache.is_dir():
        return
    for manifest_path in sorted(cache.rglob(".codex-plugin/plugin.json")):
        install_path = manifest_path.parent.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"Codex manifest unreadable: {manifest_path}")
            continue
        if (manifest.get("author") or {}).get("name") != "Agentrof":
            continue
        plugin_name = manifest.get("name", install_path.name)
        detail = scan_install(install_path)
        is_backbone = plugin_name == "project-management-office"
        team = teams.setdefault(plugin_name, {
            "plugin_name": plugin_name,
            "marketplace": "agent-marketplace",
            "display_name": plugin_name.replace("-", " ").title(),
            "description": manifest.get("description", ""),
            "kind": "backbone" if is_backbone else "team",
            "installed": True,
            "in_use": False,
            "installs": [],
            "agents": detail["agents"],
            "skills": detail["skills"],
            "commands": detail["commands"],
            "flows": detail["flows"],
        })
        record = {
            "version": manifest.get("version", ""),
            "scope": "codex",
            "project_path": "",
            "last_updated": "",
        }
        if record not in team["installs"]:
            team["installs"].append(record)


def scan_plugin_roots(teams: dict, errors: list) -> None:
    """Local install records: the plugin_roots registry that hooks and
    the setup entry maintain (the same file the marketplace_run dispatcher
    resolves from). Registry membership itself is the team marker: only
    this marketplace's own plugins register there."""
    registry_file = pmo_cli.data_dir() / "plugin_roots.json"
    if not registry_file.is_file():
        return
    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"plugin_roots.json unreadable: {registry_file}")
        return
    for plugin_name, entry in sorted((registry.get("plugins") or {}).items()):
        hosts = entry.get("hosts") if isinstance(entry, dict) else None
        records = hosts.items() if isinstance(hosts, dict) else [("local", entry)]
        for host, host_entry in sorted(records):
            if not isinstance(host_entry, dict):
                errors.append(f"{plugin_name}/{host}: invalid registry entry")
                continue
            install_path = Path(host_entry.get("root", ""))
            if not install_path.is_dir():
                errors.append(
                    f"{plugin_name}/{host}: registered root missing: {install_path}")
                continue
            detail = scan_install(install_path)
            manifest = detail["manifest"]
            is_backbone = plugin_name == "project-management-office"
            team = teams.setdefault(plugin_name, {
                "plugin_name": plugin_name,
                "marketplace": "",
                "display_name": plugin_name.replace("-", " ").title(),
                "description": manifest.get("description", ""),
                "kind": "backbone" if is_backbone else "team",
                "installed": True,
                "in_use": False,
                "installs": [],
                "agents": detail["agents"],
                "skills": detail["skills"],
                "commands": detail["commands"],
                "flows": detail["flows"],
            })
            record = {
                "version": host_entry.get("version", ""),
                "scope": str(host),
                "project_path": "",
                "last_updated": host_entry.get("registered_at", ""),
            }
            if record not in team["installs"]:
                team["installs"].append(record)


def scan_catalog() -> dict:
    """Agent Marketplace plugins from Claude, Codex, and the shared root registry."""
    registry_path = plugins_dir() / "installed_plugins.json"
    registry = {}
    if registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            registry = {}
    teams: dict[str, dict] = {}
    errors: list[str] = []
    for key, records in (registry.get("plugins") or {}).items():
        if not isinstance(records, list):
            continue
        plugin_name = key.split("@", 1)[0]
        marketplace = key.split("@", 1)[-1] if "@" in key else ""
        for entry in sorted(records, key=lambda r: r.get("lastUpdated", ""),
                            reverse=True):
            install_path = Path(entry.get("installPath", ""))
            if not install_path.is_dir():
                errors.append(f"{key}: install path missing: {install_path}")
                continue
            detail = scan_install(install_path)
            manifest = detail["manifest"]
            is_backbone = (manifest.get("name") == "project-management-office"
                           or plugin_name == "project-management-office")
            is_team = "project-management-office" in (
                manifest.get("dependencies") or [])
            if not (is_team or is_backbone):
                break  # not ours; skip the plugin entirely
            team = teams.setdefault(plugin_name, {
                "plugin_name": plugin_name,
                "marketplace": marketplace,
                "display_name": plugin_name.replace("-", " ").title(),
                "description": manifest.get("description", ""),
                "kind": "backbone" if is_backbone else "team",
                "installed": True,
                "in_use": False,
                "installs": [],
                "agents": detail["agents"],
                "skills": detail["skills"],
                "commands": detail["commands"],
                "flows": detail["flows"],
            })
            team["installs"].append({
                "version": entry.get("version", ""),
                "scope": entry.get("scope", ""),
                "project_path": entry.get("projectPath", ""),
                "last_updated": entry.get("lastUpdated", ""),
            })
    scan_codex_cache(teams, errors)
    scan_plugin_roots(teams, errors)
    return {"teams": teams, "errors": errors}


# ---------------------------------------------------------------------------
# API endpoints: pure functions (query params) -> JSON-able dict
# ---------------------------------------------------------------------------


def api_head(params) -> dict:
    base = {"db_path": str(pmo_cli.db_path()),
            "cli_version": pmo_cli.PMO_VERSION}
    if not pmo_cli.db_path().is_file():
        return {**base, "head_id": 0, "db_present": False,
                "schema_version": None}
    con = open_ro()
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        head = 0
        if version == pmo_cli.SCHEMA_VERSION:
            head = con.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events"
            ).fetchone()[0]
        return {**base, "head_id": head, "db_present": True,
                "schema_version": version}
    finally:
        con.close()


def wo_summary(con, order: dict) -> dict:
    steps = rows(con,
                 "SELECT step_id, status FROM work_order_steps"
                 " WHERE work_order_id = ? ORDER BY step_id", (order["id"],))
    story = None
    if order["story_id"]:
        row = con.execute("SELECT external_id, title FROM work_items WHERE id = ?",
                          (order["story_id"],)).fetchone()
        if row:
            story = {"external_id": row["external_id"], "title": row["title"]}
    last = con.execute(
        "SELECT action FROM events WHERE work_order_id = ? ORDER BY id DESC LIMIT 1",
        (order["id"],),
    ).fetchone()
    dangling = (order["status"] in pmo_cli.ACTIVE_WO_STATUSES and last is not None
                and last["action"] == SESSION_ENDED_ACTION)
    return {
        "work_order_key": order["work_order_key"],
        "status": order["status"],
        "current_step": order["current_step"],
        "story": story,
        "worktree_path": order["worktree_path"],
        "review_rounds": order["review_rounds"],
        "qa_rounds": order["qa_rounds"],
        "steps_total": len(steps),
        "steps_done": sum(1 for s in steps if s["status"] == "done"),
        "steps": steps,
        "dangling": dangling,
        "updated_at": order["updated_at"],
    }


def api_overview(params) -> dict:
    con = open_ro()
    try:
        check_schema(con)
        projects = []
        for project in rows(con, "SELECT * FROM projects ORDER BY project_key"):
            counts: dict[str, dict[str, int]] = {}
            for row in rows(con,
                            "SELECT kind, status, COUNT(*) AS n FROM work_items"
                            " WHERE project_id = ? GROUP BY kind, status",
                            (project["id"],)):
                counts.setdefault(row["kind"], {})[row["status"]] = row["n"]
            teams = [r["plugin_name"] for r in rows(
                con,
                "SELECT t.plugin_name FROM project_teams pt"
                " JOIN teams t ON t.id = pt.team_id WHERE pt.project_id = ?"
                " ORDER BY t.plugin_name",
                (project["id"],))]
            active = [wo_summary(con, o) for o in rows(
                con,
                "SELECT * FROM work_orders WHERE project_id = ?"
                " AND status IN (?, ?) ORDER BY work_order_key",
                (project["id"], *pmo_cli.ACTIVE_WO_STATUSES))]
            wo_counts = {r["status"]: r["n"] for r in rows(
                con,
                "SELECT status, COUNT(*) AS n FROM work_orders"
                " WHERE project_id = ? GROUP BY status",
                (project["id"],))}
            last_event = con.execute(
                "SELECT ts FROM events WHERE project_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (project["id"],),
            ).fetchone()
            open_findings = con.execute(
                "SELECT COUNT(*) FROM findings WHERE project_id = ?"
                " AND status = 'open'",
                (project["id"],),
            ).fetchone()[0]
            projects.append({
                "project_key": project["project_key"],
                "name": project["name"],
                "teams": teams,
                "item_counts": counts,
                "work_order_counts": wo_counts,
                "active_work_orders": active,
                "open_findings": open_findings,
                "last_event_ts": last_event["ts"] if last_event else None,
            })
        head = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        open_candidates = rows(
            con,
            "SELECT * FROM issue_candidates WHERE status = 'candidate'"
            " ORDER BY external_id")
        return {"projects": projects, "head_id": head,
                "open_candidates": len(open_candidates),
                "issue_candidates": open_candidates}
    finally:
        con.close()


def api_catalog(params) -> dict:
    catalog = scan_catalog()
    teams = catalog["teams"]
    db_teams: list[dict] = []
    if pmo_cli.db_path().is_file():
        con = open_ro()
        try:
            check_schema(con)
            db_teams = rows(con,
                            "SELECT t.plugin_name, t.display_name,"
                            " COUNT(pt.project_id) AS project_count FROM teams t"
                            " LEFT JOIN project_teams pt ON pt.team_id = t.id"
                            " GROUP BY t.id ORDER BY t.plugin_name")
        finally:
            con.close()
    for row in db_teams:
        team = teams.get(row["plugin_name"])
        if team is None:
            teams[row["plugin_name"]] = {
                "plugin_name": row["plugin_name"],
                "marketplace": "",
                "display_name": row["display_name"],
                "description": "",
                "kind": "team",
                "installed": False,
                "in_use": True,
                "installs": [],
                "agents": [], "skills": [], "commands": [], "flows": [],
                "project_count": row["project_count"],
            }
        else:
            team["in_use"] = True
            team["display_name"] = row["display_name"]
            team["project_count"] = row["project_count"]
    ordered = sorted(teams.values(),
                     key=lambda t: (t["kind"] != "backbone", t["plugin_name"]))
    return {"teams": ordered, "errors": catalog["errors"]}


def api_project(params) -> dict:
    key = one_param(params, "key")
    con = open_ro()
    try:
        check_schema(con)
        project = con.execute("SELECT * FROM projects WHERE project_key = ?",
                              (key,)).fetchone()
        if project is None:
            raise ApiError(404, "unknown_project", f"no project '{key}'")
        items = rows(con,
                     "SELECT * FROM work_items WHERE project_id = ?"
                     " ORDER BY external_id", (project["id"],))
        dod_by_item: dict[str, list] = {}
        for row in rows(con,
                        "SELECT d.id, d.statement, d.status, d.verified_at,"
                        " d.failure_reason, w.external_id FROM dod_items d"
                        " JOIN work_items w ON w.id = d.item_id"
                        " WHERE w.project_id = ? ORDER BY d.id",
                        (project["id"],)):
            dod_by_item.setdefault(row.pop("external_id"), []).append(row)
        deps = rows(con,
                    "SELECT a.external_id AS item, b.external_id AS depends_on,"
                    " d.reason FROM work_item_deps d"
                    " JOIN work_items a ON a.id = d.item_id"
                    " JOIN work_items b ON b.id = d.depends_on_id"
                    " WHERE d.project_id = ?"
                    " ORDER BY a.external_id, b.external_id",
                    (project["id"],))
        wo_keys = {r["id"]: r["work_order_key"] for r in rows(
            con, "SELECT id, work_order_key FROM work_orders WHERE project_id = ?",
            (project["id"],))}
        by_id = {item["id"]: item for item in items}
        epics, ungrouped_stories, atomic_tasks = [], [], []
        for item in items:  # first pass: annotate and seed children lists
            item["dod_items"] = dod_by_item.get(item["external_id"], [])
            item["work_order_key"] = wo_keys.get(item["work_order_id"])
            if item["kind"] == "epic":
                item["stories"] = []
            elif item["kind"] == "story":
                item["tasks"] = []
        for item in items:  # second pass: attach to parents
            parent = by_id.get(item["parent_id"])
            if item["kind"] == "epic":
                epics.append(item)
            elif item["kind"] == "story":
                if parent is not None and parent["kind"] == "epic":
                    parent["stories"].append(item)
                else:
                    ungrouped_stories.append(item)
            else:
                if parent is not None and parent["kind"] == "story":
                    parent["tasks"].append(item)
                else:
                    atomic_tasks.append(item)
        story_nodes, story_edges = pmo_cli.project_dep_graph(con, project["id"], "story")
        story_order, story_cycles = pmo_cli.topo_order(story_nodes, story_edges)
        task_nodes, task_edges = pmo_cli.project_dep_graph(con, project["id"], "task")
        task_order, task_cycles = pmo_cli.topo_order(task_nodes, task_edges)
        criteria = rows(con,
                        "SELECT c.criterion_id, c.disposition, c.reason,"
                        " w.external_id AS story FROM story_criteria c"
                        " LEFT JOIN work_items w ON w.id = c.story_id"
                        " WHERE c.project_id = ? ORDER BY c.criterion_id",
                        (project["id"],))
        questions = rows(con,
                         "SELECT question, status FROM open_questions"
                         " WHERE project_id = ? ORDER BY id", (project["id"],))
        return {
            "project": {"project_key": project["project_key"],
                        "name": project["name"],
                        "created_at": project["created_at"]},
            "epics": epics,
            "ungrouped_stories": ungrouped_stories,
            "atomic_tasks": atomic_tasks,
            "deps": deps,
            "order": {
                "stories": {"order": story_order, "cycles": story_cycles},
                "tasks": {"order": task_order, "cycles": task_cycles},
            },
            "criteria": criteria,
            "open_questions": questions,
        }
    finally:
        con.close()


def api_work_orders(params) -> dict:
    con = open_ro()
    try:
        check_schema(con)
        query = ("SELECT w.*, p.project_key FROM work_orders w"
                 " JOIN projects p ON p.id = w.project_id")
        args: tuple = ()
        project_key = params.get("project_key", [""])[0].strip()
        if project_key:
            query += " WHERE p.project_key = ?"
            args = (project_key,)
        query += " ORDER BY w.updated_at DESC, w.id DESC"
        orders = []
        for order in rows(con, query, args):
            summary = wo_summary(con, order)
            summary["project_key"] = order["project_key"]
            summary["request"] = order["request"]
            summary["created_at"] = order["created_at"]
            orders.append(summary)
        return {"work_orders": orders}
    finally:
        con.close()


def api_work_order(params) -> dict:
    key = one_param(params, "key")
    con = open_ro()
    try:
        check_schema(con)
        order = con.execute(
            "SELECT w.*, p.project_key FROM work_orders w"
            " JOIN projects p ON p.id = w.project_id WHERE w.work_order_key = ?",
            (key,),
        ).fetchone()
        if order is None:
            raise ApiError(404, "unknown_work_order", f"no work order '{key}'")
        detail = wo_summary(con, order)
        detail["project_key"] = order["project_key"]
        detail["request"] = order["request"]
        detail["bindings"] = json.loads(order["bindings_json"] or "{}")
        detail["created_at"] = order["created_at"]
        detail["steps"] = rows(con,
                               "SELECT step_id, status, artifact_path, attempts"
                               " FROM work_order_steps WHERE work_order_id = ?"
                               " ORDER BY step_id", (order["id"],))
        detail["gates"] = rows(con,
                               "SELECT name, decision, decided_by, decided_at"
                               " FROM gates WHERE work_order_id = ?"
                               " ORDER BY decided_at, name", (order["id"],))
        detail["ownership"] = rows(con,
                                   "SELECT role, path_prefix FROM ownership"
                                   " WHERE work_order_id = ?"
                                   " ORDER BY role, path_prefix", (order["id"],))
        detail["findings"] = rows(con,
                                  "SELECT external_id, source, severity, summary,"
                                  " repro, expected_actual, traced_requirement,"
                                  " status, opened_round, closed_round, created_at"
                                  " FROM findings WHERE work_order_id = ?"
                                  " ORDER BY external_id", (order["id"],))
        detail["coverage"] = rows(con,
                                  "SELECT requirement_id, test_names, verdict,"
                                  " recorded_at FROM coverage"
                                  " WHERE work_order_id = ?"
                                  " ORDER BY requirement_id", (order["id"],))
        detail["budgets"] = rows(con,
                                 "SELECT budget_id, description, verdict, reason,"
                                 " recorded_at FROM budgets WHERE work_order_id = ?"
                                 " ORDER BY budget_id", (order["id"],))
        tasks = rows(con,
                     "SELECT id, external_id, title, status, role, step_id,"
                     " started_at, finished_at FROM work_items"
                     " WHERE work_order_id = ? AND kind = 'task' ORDER BY id",
                     (order["id"],))
        for task in tasks:
            task["attempts"] = rows(con,
                                    "SELECT attempt, agent_name, role, session_id,"
                                    " started_at, finished_at, outcome,"
                                    " failure_reason, cost_usd FROM task_attempts"
                                    " WHERE task_id = ? ORDER BY attempt",
                                    (task.pop("id"),))
        detail["tasks"] = tasks
        detail["events"] = rows(con,
                                "SELECT id, ts, actor, action, payload_json"
                                " FROM events WHERE work_order_id = ?"
                                " ORDER BY id DESC LIMIT 100", (order["id"],))
        for event in detail["events"]:
            event["payload"] = json.loads(event.pop("payload_json") or "{}")
        return detail
    finally:
        con.close()


def api_ledger(params) -> dict:
    project_key = one_param(params, "project_key")
    con = open_ro()
    try:
        check_schema(con)
        project = con.execute("SELECT * FROM projects WHERE project_key = ?",
                              (project_key,)).fetchone()
        if project is None:
            raise ApiError(404, "unknown_project", f"no project '{project_key}'")
        entries = rows(con,
                       "SELECT l.checkpoint_at, l.review_rounds, l.qa_rounds,"
                       " l.escaped_defect, l.finding_counts_json,"
                       " w.external_id AS story, o.work_order_key FROM ledger l"
                       " LEFT JOIN work_items w ON w.id = l.story_id"
                       " LEFT JOIN work_orders o ON o.id = l.work_order_id"
                       " WHERE l.project_id = ? ORDER BY l.checkpoint_at, l.id",
                       (project["id"],))
        for entry in entries:
            entry["finding_counts"] = json.loads(entry.pop("finding_counts_json"))
            entry["escaped_defect"] = bool(entry["escaped_defect"])
        return {"rows": entries}
    finally:
        con.close()


def api_events(params) -> dict:
    con = open_ro()
    try:
        check_schema(con)
        since_id = int(params.get("since_id", ["0"])[0] or 0)
        limit = min(int(params.get("limit", ["100"])[0] or 100), EVENT_LIMIT_MAX)
        query = ("SELECT e.id, e.ts, e.actor, e.action, e.payload_json,"
                 " p.project_key, o.work_order_key FROM events e"
                 " LEFT JOIN projects p ON p.id = e.project_id"
                 " LEFT JOIN work_orders o ON o.id = e.work_order_id"
                 " WHERE e.id > ?")
        args: list = [since_id]
        project_key = params.get("project_key", [""])[0].strip()
        if project_key:
            query += " AND p.project_key = ?"
            args.append(project_key)
        query += " ORDER BY e.id LIMIT ?"
        args.append(limit)
        events = rows(con, query, args)
        for event in events:
            event["payload"] = json.loads(event.pop("payload_json") or "{}")
        head = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        return {"events": events, "head_id": head}
    finally:
        con.close()


def api_issue_candidates(params) -> dict:
    con = open_ro()
    try:
        check_schema(con)
        status = params.get("status", [""])[0].strip()
        query = "SELECT * FROM issue_candidates"
        args: list = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        query += " ORDER BY external_id"
        candidates = rows(con, query, args)
        head = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        return {"issue_candidates": candidates, "head_id": head}
    finally:
        con.close()


ROUTES = {
    "/api/head": api_head,
    "/api/overview": api_overview,
    "/api/catalog": api_catalog,
    "/api/project": api_project,
    "/api/work_orders": api_work_orders,
    "/api/work_order": api_work_order,
    "/api/ledger": api_ledger,
    "/api/events": api_events,
    "/api/issue_candidates": api_issue_candidates,
}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def json_response(obj: dict, status: int = 200) -> tuple[int, bytes, str]:
    return (
        status,
        json.dumps(obj, sort_keys=True).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def dispatch_request(method: str, target: str) -> tuple[int, bytes, str]:
    """Resolve one request without opening a socket.

    The HTTP handler is deliberately a thin transport adapter around this
    function so route, method, error and response contracts stay hermetic in
    the default test gate.
    """
    if method != "GET":
        return json_response({
            "error": "method_not_allowed",
            "message": "this dashboard is read-only",
        }, 405)
    parsed = urlparse(target)
    path = parsed.path.rstrip("/") or "/"
    if path in ("/", "/index.html"):
        index = find_index_html()
        if index is None:
            return json_response({
                "error": "no_index",
                "message": "dashboard index.html is missing; reinstall the"
                           " project-management-office plugin",
            }, 500)
        return 200, index.read_bytes(), "text/html; charset=utf-8"
    if path == "/favicon.ico":
        return 204, b"", "image/x-icon"
    endpoint = ROUTES.get(path)
    if endpoint is None:
        return json_response({"error": "not_found", "message": path}, 404)
    try:
        return json_response(endpoint(parse_qs(parsed.query)))
    except ApiError as exc:
        return json_response(exc.body, exc.status)
    except (ValueError, KeyError) as exc:
        return json_response({"error": "bad_request", "message": str(exc)}, 400)
    except sqlite3.OperationalError as exc:
        return json_response({"error": "db_unavailable", "message": str(exc)}, 503)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pmo-dashboard"

    def log_message(self, fmt, *log_args):  # silence per-request noise
        pass

    def _write(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser gave up on this poll; nothing to salvage

    def send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, sort_keys=True).encode("utf-8")
        self._write(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        self._write(*dispatch_request("GET", self.path))

    def _reject(self) -> None:
        self._write(*dispatch_request(self.command, self.path))

    do_POST = _reject
    do_PUT = _reject
    do_DELETE = _reject
    do_PATCH = _reject


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8787,
          open_browser: bool = True) -> int:
    try:
        server = make_server(host, port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"pmo: port {port} is in use; pass --port to pick another",
                  file=sys.stderr)
            return 1
        raise
    bound_port = server.server_address[1]
    url = f"http://{host}:{bound_port}/"
    if host not in ("127.0.0.1", "localhost"):
        print(f"pmo: WARNING: binding {host} exposes the dashboard beyond this"
              " machine and it has no authentication", file=sys.stderr)
    print(f"pmo: Control Tower running on {url} (read-only; Ctrl+C stops it)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Control Tower: the read-only dashboard over the central database.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    return serve(args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
