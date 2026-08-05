#!/usr/bin/env python3
"""Project Management Office CLI: the single writer of the central database.

Every team plugin records its process state here: projects, epics, stories,
machine-generated tasks, work orders with step state and gates, findings,
coverage, budgets and the quality ledger. Nothing else ever writes the
database; agents and hooks go through this CLI, and every mutation appends
an audit event.

The database lives in the Agent Marketplace product directory. Stdlib only.

Exit codes: 0 success, 1 rule violation, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import marketplace_paths
import upgrade_core

PMO_VERSION = "0.0.1"
SCHEMA_VERSION = 4
DB_NAME = "pmo.db"

WO_STATUSES = {"running", "waiting_gate", "blocked", "escalated", "complete"}
ACTIVE_WO_STATUSES = ("running", "waiting_gate")
STEP_STATUSES = {"pending", "in_progress", "done", "blocked", "escalated"}
STEP_IDS = ["0", "1", "2", "3", "4", "5"]
EPIC_STATUSES = {"open", "done"}
STORY_STATUSES = {"planned", "ready", "in_development", "done", "deferred"}
TASK_STATUSES = {"open", "in_progress", "done", "blocked"}
STATUSES_BY_KIND = {"epic": EPIC_STATUSES, "story": STORY_STATUSES, "task": TASK_STATUSES}
FINDING_SOURCES = {"review", "qa", "design_qa"}
FINDING_STATUSES = {"open", "fixed", "waived"}
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
# Issue candidates: marketplace defect/improvement notes captured during a
# run, filed to the Agent Marketplace repository only on explicit owner
# approval. Unlike findings they are not bound to a work order.
ISSUE_KINDS = {"defect", "improvement"}
ISSUE_STATUSES = {"candidate", "filed", "dismissed"}
BUDGET_VERDICTS = {"verified", "unverified"}
COVERAGE_VERDICTS = {"pass", "fail", "no_test"}
DOD_STATUSES = {"pending", "verified", "failed"}
ATTEMPT_OUTCOMES = {"running", "done", "blocked", "failed"}
STORY_REQUIRED_FIELDS = ["title", "scope", "excludes", "dor", "dod", "epic"]

# Work-item priority: the token before the first ':' must be one of these;
# free text after the colon carries the reason ("high: unblocks WP-04").
PRIORITIES = ("critical", "high", "medium", "low")
PRIORITY_RANK = {tier: rank for rank, tier in enumerate(PRIORITIES)}

DDL = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  project_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  project_uuid TEXT NOT NULL DEFAULT '',
  repository_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY,
  plugin_name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_teams (
  project_id INTEGER NOT NULL REFERENCES projects(id),
  team_id INTEGER NOT NULL REFERENCES teams(id),
  UNIQUE (project_id, team_id)
);
CREATE TABLE IF NOT EXISTS work_items (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  kind TEXT NOT NULL CHECK (kind IN ('epic', 'story', 'task')),
  external_id TEXT NOT NULL,
  parent_id INTEGER REFERENCES work_items(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  item_type TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  excludes TEXT NOT NULL DEFAULT '',
  dor TEXT NOT NULL DEFAULT '',
  dod TEXT NOT NULL DEFAULT '',
  deployed_verified INTEGER NOT NULL DEFAULT 0,
  work_order_id INTEGER REFERENCES work_orders(id),
  role TEXT NOT NULL DEFAULT '',
  step_id TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, external_id)
);
CREATE TABLE IF NOT EXISTS work_item_deps (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  item_id INTEGER NOT NULL REFERENCES work_items(id),
  depends_on_id INTEGER NOT NULL REFERENCES work_items(id),
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (item_id, depends_on_id),
  CHECK (item_id != depends_on_id)
);
CREATE TABLE IF NOT EXISTS dod_items (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES work_items(id),
  statement TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'verified', 'failed')),
  verified_at TEXT NOT NULL DEFAULT '',
  failure_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (item_id, statement)
);
CREATE TABLE IF NOT EXISTS task_attempts (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES work_items(id),
  attempt INTEGER NOT NULL,
  agent_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT 'running'
    CHECK (outcome IN ('running', 'done', 'blocked', 'failed')),
  failure_reason TEXT NOT NULL DEFAULT '',
  cost_usd REAL,
  source TEXT NOT NULL DEFAULT 'hook',
  UNIQUE (task_id, attempt)
);
CREATE TABLE IF NOT EXISTS story_criteria (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  criterion_id TEXT NOT NULL,
  story_id INTEGER REFERENCES work_items(id),
  disposition TEXT NOT NULL CHECK (disposition IN ('covered', 'deferred')),
  reason TEXT NOT NULL DEFAULT '',
  UNIQUE (project_id, criterion_id)
);
CREATE TABLE IF NOT EXISTS open_questions (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  question TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  story_id INTEGER REFERENCES work_items(id),
  work_order_key TEXT NOT NULL UNIQUE,
  request TEXT NOT NULL,
  status TEXT NOT NULL CHECK
    (status IN ('running', 'waiting_gate', 'blocked', 'escalated', 'complete')),
  current_step TEXT NOT NULL DEFAULT '0',
  review_rounds INTEGER NOT NULL DEFAULT 0,
  qa_rounds INTEGER NOT NULL DEFAULT 0,
  worktree_path TEXT NOT NULL,
  bindings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_order_steps (
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK
    (status IN ('pending', 'in_progress', 'done', 'blocked', 'escalated')),
  artifact_path TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE (work_order_id, step_id)
);
CREATE TABLE IF NOT EXISTS gates (
  id INTEGER PRIMARY KEY,
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  name TEXT NOT NULL,
  decision TEXT NOT NULL,
  decided_by TEXT NOT NULL DEFAULT 'owner',
  decided_at TEXT NOT NULL,
  UNIQUE (work_order_id, name)
);
CREATE TABLE IF NOT EXISTS ownership (
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  role TEXT NOT NULL,
  path_prefix TEXT NOT NULL,
  UNIQUE (work_order_id, role, path_prefix)
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  story_id INTEGER REFERENCES work_items(id),
  external_id TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('review', 'qa', 'design_qa')),
  severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
  summary TEXT NOT NULL,
  repro TEXT NOT NULL DEFAULT '',
  expected_actual TEXT NOT NULL DEFAULT '',
  traced_requirement TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'waived')),
  opened_round INTEGER NOT NULL DEFAULT 0,
  closed_round INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, external_id)
);
CREATE TABLE IF NOT EXISTS coverage (
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  requirement_id TEXT NOT NULL,
  test_names TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'no_test')),
  recorded_at TEXT NOT NULL,
  UNIQUE (work_order_id, requirement_id)
);
CREATE TABLE IF NOT EXISTS budgets (
  work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
  budget_id TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (verdict IN ('verified', 'unverified')),
  reason TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  UNIQUE (work_order_id, budget_id)
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  story_id INTEGER REFERENCES work_items(id),
  work_order_id INTEGER REFERENCES work_orders(id),
  checkpoint_at TEXT NOT NULL,
  finding_counts_json TEXT NOT NULL,
  review_rounds INTEGER NOT NULL,
  qa_rounds INTEGER NOT NULL,
  escaped_defect INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  project_id INTEGER,
  work_order_id INTEGER,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS issue_candidates (
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  work_order_id INTEGER REFERENCES work_orders(id),
  external_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('defect', 'improvement')),
  status TEXT NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'filed', 'dismissed')),
  issue_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
  migration_id TEXT PRIMARY KEY,
  from_version INTEGER NOT NULL,
  to_version INTEGER NOT NULL,
  checksum TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_uuid
  ON projects(project_uuid) WHERE project_uuid != '';
CREATE INDEX IF NOT EXISTS idx_work_item_deps_reverse ON work_item_deps(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_task_attempts_task ON task_attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id);
CREATE INDEX IF NOT EXISTS idx_work_items_pks ON work_items(project_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_findings_wo_status ON findings(work_order_id, status);
CREATE INDEX IF NOT EXISTS idx_issue_candidates_status ON issue_candidates(status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg: str, code: int = 1) -> int:
    print(f"pmo: {msg}", file=sys.stderr)
    return code


def data_dir() -> Path:
    return marketplace_paths.marketplace_home()


def db_path() -> Path:
    return data_dir() / DB_NAME


def connect(*, allow_upgrade_schema: bool = False) -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.create_function(
        "agent_marketplace_writer_epoch", 0, lambda: upgrade_core.WRITER_EPOCH,
        deterministic=True,
    )
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    if not allow_upgrade_schema and version not in (0, SCHEMA_VERSION):
        con.close()
        raise Rule(
            f"AGENT_MARKETPLACE_UPGRADE_REQUIRED: database schema {version} must be"
            f" upgraded to {SCHEMA_VERSION}; run the Agent Marketplace Upgrade entry"
        )
    return con


class Rule(Exception):
    """A rule violation: reported on stderr, exit 1."""


def content_fingerprint(con: sqlite3.Connection) -> str:
    """Deterministic hash of the full database content (every table except
    meta, rows ordered by rowid). The database is small by design, so a
    full-content hash is affordable and catches foreign UPDATEs that row
    counts and max ids would miss."""
    import hashlib
    digest = hashlib.sha256()
    tables = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name != 'meta' ORDER BY name")]
    for table in tables:
        digest.update(table.encode("utf-8"))
        for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def _integrity_stamp(con: sqlite3.Connection) -> None:
    """Update the tamper tripwire inside the current transaction: the
    generation counter and the content fingerprint move together with
    every sanctioned mutation. A foreign writer changes content without
    them, which `verify` detects at the next gate."""
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key = 'generation'").fetchone()
        generation = int(row["value"]) + 1 if row else 1
        fingerprint = content_fingerprint(con)
        for key, value in (("generation", str(generation)),
                           ("fingerprint", fingerprint),
                           ("stamped_at", now())):
            con.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    except sqlite3.OperationalError:
        pass  # init-db has not created the schema yet


def verify_integrity(con: sqlite3.Connection) -> str | None:
    """None when the content matches the recorded fingerprint; otherwise a
    human-readable problem statement. An uninitialized database is not an
    integrity finding; command validation routes it to init-db."""
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key = 'fingerprint'").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    actual = content_fingerprint(con)
    if actual == row["value"]:
        return None
    return (
        "database content does not match the recorded integrity"
        " fingerprint: a writer other than the PMO CLI has touched the"
        " database since the last sanctioned mutation"
    )


def mutate(con: sqlite3.Connection):
    """Context manager: BEGIN IMMEDIATE, commit on success, rollback on
    error. Every successful commit re-stamps the integrity tripwire."""
    class _Tx:
        def __enter__(self):
            con.execute("BEGIN IMMEDIATE")
            return con

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                _integrity_stamp(con)
                con.execute("COMMIT")
            else:
                con.execute("ROLLBACK")
            return False
    return _Tx()


def record(con, action: str, project_id=None, work_order_id=None,
           actor="orchestrator", payload=None) -> None:
    con.execute(
        "INSERT INTO events (ts, project_id, work_order_id, actor, action, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (now(), project_id, work_order_id, actor, action,
         json.dumps(payload or {}, sort_keys=True)),
    )


def get_project(con, key: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM projects WHERE project_key = ?", (key,)).fetchone()
    if row is None:
        raise Rule(f"no project registered with key '{key}'")
    return row


def get_work_order(con, wo_key: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM work_orders WHERE work_order_key = ?", (wo_key,)
    ).fetchone()
    if row is None:
        raise Rule(f"no work order with key '{wo_key}'")
    return row


def get_item(con, project_id: int, external_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM work_items WHERE project_id = ? AND external_id = ?",
        (project_id, external_id),
    ).fetchone()


def active_work_orders(con, project_id: int) -> list[sqlite3.Row]:
    marks = ", ".join("?" for _ in ACTIVE_WO_STATUSES)
    return con.execute(
        f"SELECT * FROM work_orders WHERE project_id = ? AND status IN ({marks})",
        (project_id, *ACTIVE_WO_STATUSES),
    ).fetchall()


def norm_path(path: str) -> str:
    """Canonicalize a worktree path so symlinked variants compare equal."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def is_snake(role: str) -> bool:
    return bool(role) and role == role.lower() and role.replace("_", "").isalnum()


def prefixes_overlap(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)


def next_external_id(con, project_id: int, prefix: str) -> str:
    rows = con.execute(
        "SELECT external_id FROM work_items WHERE project_id = ? AND external_id LIKE ?",
        (project_id, f"{prefix}-%"),
    ).fetchall()
    highest = 0
    for row in rows:
        tail = row["external_id"].rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1:03d}"


def next_finding_id(con, project_id: int) -> str:
    rows = con.execute(
        "SELECT external_id FROM findings WHERE project_id = ?", (project_id,)
    ).fetchall()
    highest = 0
    for row in rows:
        tail = row["external_id"].rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"F-{highest + 1:03d}"


def next_issue_candidate_id(con) -> str:
    """Mint IC-NNN globally: issue candidates may be project-less, so the
    sequence is not scoped by project the way findings are."""
    rows = con.execute("SELECT external_id FROM issue_candidates").fetchall()
    highest = 0
    for row in rows:
        tail = row["external_id"].rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"IC-{highest + 1:03d}"


def priority_tier(value: str) -> str:
    """The validated token of a priority value: lowercased text before the
    first ':'. 'high: unblocks WP-04' -> 'high'."""
    return value.split(":", 1)[0].strip().lower()


def check_priority(value: str, context: str) -> None:
    if value and priority_tier(value) not in PRIORITIES:
        raise Rule(
            f"{context}: priority tier '{priority_tier(value)}' not in"
            f" {'/'.join(PRIORITIES)}; use 'tier: reason'"
        )


def priority_rank(value: str) -> int:
    return PRIORITY_RANK.get(priority_tier(value), len(PRIORITIES))


# ---------------------------------------------------------------------------
# Worktree binding (mechanical session-scope guards)
# ---------------------------------------------------------------------------


def cwd_inside(worktree_path: str) -> bool:
    cwd = norm_path(os.getcwd())
    root = norm_path(worktree_path)
    return cwd == root or cwd.startswith(root + os.sep)


def require_cwd_inside(order) -> None:
    """Mid-flight mutations belong to the session working in the order's
    claimed worktree; any other session is refused mechanically."""
    if not cwd_inside(order["worktree_path"]):
        raise Rule(
            f"work order '{order['work_order_key']}' belongs to worktree"
            f" {order['worktree_path']}; run this from inside it"
            " (work-order release is the recovery verb from elsewhere)"
        )


def in_linked_worktree() -> bool:
    """True when the current directory sits in a linked git worktree, not the
    primary checkout. A non-git directory binds nothing (permissive)."""
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode != 0:
            return False
        lines = probe.stdout.splitlines()
        if len(lines) < 2:
            return False
        git_dir, common_dir = (str(Path(p).resolve()) for p in lines[:2])
        return git_dir != common_dir
    except Exception:
        return False


def require_main_line(order) -> None:
    """Closing writes (checkpoint, complete) run on the primary checkout: the
    solo session or the integrator, never a lane's linked worktree."""
    if in_linked_worktree():
        raise Rule(
            f"closing writes for work order '{order['work_order_key']}' run"
            " on the primary checkout (main line), not inside a lane worktree"
        )


def revalidate_claims(con, order) -> None:
    """Reactivating a parked work order re-runs the init claim checks against
    the CURRENT active set; anything taken meanwhile refuses by name."""
    mine = con.execute(
        "SELECT role, path_prefix FROM ownership WHERE work_order_id = ?",
        (order["id"],),
    ).fetchall()
    for other in active_work_orders(con, order["project_id"]):
        if other["id"] == order["id"]:
            continue
        if other["worktree_path"] == order["worktree_path"]:
            raise Rule(
                f"cannot reactivate: active work order"
                f" '{other['work_order_key']}' now holds worktree"
                f" {order['worktree_path']}"
            )
        if order["story_id"] is not None and other["story_id"] == order["story_id"]:
            raise Rule(
                "cannot reactivate: the story is now claimed by active work"
                f" order '{other['work_order_key']}'"
            )
        for row in con.execute(
            "SELECT role, path_prefix FROM ownership WHERE work_order_id = ?",
            (other["id"],),
        ):
            for own in mine:
                if prefixes_overlap(own["path_prefix"], row["path_prefix"]):
                    raise Rule(
                        f"cannot reactivate: ownership {own['role']}:"
                        f"{own['path_prefix']} overlaps {row['role']}:"
                        f"{row['path_prefix']} held by active work order"
                        f" '{other['work_order_key']}'"
                    )


# ---------------------------------------------------------------------------
# Dependency graph helpers (shared with the dashboard)
# ---------------------------------------------------------------------------


def topo_order(nodes: dict[str, str], edges: dict[str, set[str]]):
    """Deterministic Kahn sort, one node at a time: of everything currently
    unblocked, the highest priority tier goes next (external_id breaks ties),
    then the frontier is re-evaluated. nodes maps external_id -> priority
    value; edges maps external_id -> the ids it depends on. Returns
    (order, cycles); cycles lists the ids left unresolvable."""
    pending = {nid: {d for d in deps if d in nodes}
               for nid, deps in ((n, edges.get(n, set())) for n in nodes)}
    blockers: dict[str, set[str]] = {nid: set() for nid in nodes}
    for nid, deps in pending.items():
        for dep in deps:
            blockers[dep].add(nid)
    heap = [(priority_rank(nodes[nid]), nid)
            for nid, deps in pending.items() if not deps]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        _, nid = heapq.heappop(heap)
        order.append(nid)
        del pending[nid]
        for waiter in blockers[nid]:
            deps = pending.get(waiter)
            if deps is not None:
                deps.discard(nid)
                if not deps:
                    heapq.heappush(heap, (priority_rank(nodes[waiter]), waiter))
    return order, sorted(pending)


def dep_cycle_path(con, item_id: int, depends_on_id: int):
    """The external-id path that adding item -> depends_on would close into a
    cycle, or None. Walks existing edges transitively from depends_on."""
    parents = {depends_on_id: None}
    frontier = [depends_on_id]
    while frontier:
        current = frontier.pop()
        if current == item_id:
            path = []
            node = current
            while node is not None:
                path.append(node)
                node = parents[node]
            ids = list(reversed(path))
            marks = ", ".join("?" for _ in ids)
            titles = {
                row["id"]: row["external_id"]
                for row in con.execute(
                    f"SELECT id, external_id FROM work_items WHERE id IN ({marks})",
                    ids,
                )
            }
            return [titles[i] for i in ids]
        for row in con.execute(
            "SELECT depends_on_id FROM work_item_deps WHERE item_id = ?",
            (current,),
        ):
            nxt = row["depends_on_id"]
            if nxt not in parents:
                parents[nxt] = current
                frontier.append(nxt)
    return None


def project_dep_graph(con, project_id: int, kind: str):
    """(nodes, edges) over one kind's items for topo_order."""
    nodes = {
        row["external_id"]: row["priority"]
        for row in con.execute(
            "SELECT external_id, priority FROM work_items"
            " WHERE project_id = ? AND kind = ?",
            (project_id, kind),
        )
    }
    edges: dict[str, set[str]] = {}
    for row in con.execute(
        "SELECT a.external_id AS item, b.external_id AS dep"
        " FROM work_item_deps d"
        " JOIN work_items a ON a.id = d.item_id"
        " JOIN work_items b ON b.id = d.depends_on_id"
        " WHERE d.project_id = ? AND a.kind = ? AND b.kind = ?",
        (project_id, kind, kind),
    ):
        edges.setdefault(row["item"], set()).add(row["dep"])
    return nodes, edges


# ---------------------------------------------------------------------------
# Attempt helpers (task dispatch history)
# ---------------------------------------------------------------------------


def close_running_attempts(con, task_id: int, outcome: str, stamp: str,
                           failure_reason: str = "", cost_usd=None) -> int:
    """Close every attempt still marked running; returns how many closed."""
    open_rows = con.execute(
        "SELECT id FROM task_attempts WHERE task_id = ? AND outcome = 'running'",
        (task_id,),
    ).fetchall()
    for row in open_rows:
        con.execute(
            "UPDATE task_attempts SET outcome = ?, finished_at = ?,"
            " failure_reason = ?, cost_usd = COALESCE(?, cost_usd) WHERE id = ?",
            (outcome, stamp, failure_reason, cost_usd, row["id"]),
        )
    return len(open_rows)


def open_attempt(con, task_id: int, role: str, agent_name: str,
                 session_id: str, stamp: str, source: str = "hook") -> int:
    """Insert the next attempt row for a task; returns the attempt number."""
    highest = con.execute(
        "SELECT COALESCE(MAX(attempt), 0) AS n FROM task_attempts WHERE task_id = ?",
        (task_id,),
    ).fetchone()["n"]
    attempt = highest + 1
    con.execute(
        "INSERT INTO task_attempts (task_id, attempt, agent_name, role,"
        " session_id, started_at, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, attempt, agent_name, role, session_id, stamp, source),
    )
    return attempt


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init_db(args) -> int:
    con = connect(allow_upgrade_schema=True)
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        con.close()
        raise Rule(
            f"AGENT_MARKETPLACE_UPGRADE_REQUIRED: database schema {version} must be"
            f" upgraded to {SCHEMA_VERSION}; run the Agent Marketplace Upgrade entry"
        )
    con.executescript(DDL)  # commits itself; keep it outside the transaction
    with mutate(con):
        if con.execute("PRAGMA user_version").fetchone()[0] == 0:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('writer_epoch', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(upgrade_core.WRITER_EPOCH),),
        )
        record(con, "init_db", payload={"schema_version": SCHEMA_VERSION})
    upgrade_core.install_writer_guards(con)
    print(f"pmo: database ready at {db_path()} (schema {SCHEMA_VERSION})")
    return 0


def cmd_project_register(args) -> int:
    con = connect()
    with mutate(con):
        existing = con.execute(
            "SELECT * FROM projects WHERE project_key = ?", (args.key,)
        ).fetchone()
        if existing is None:
            con.execute(
                "INSERT INTO projects (project_key, name, created_at) VALUES (?, ?, ?)",
                (args.key, args.name or args.key, now()),
            )
            project = get_project(con, args.key)
            record(con, "project_registered", project_id=project["id"],
                   payload={"project_key": args.key})
        else:
            project = existing
        if args.team:
            con.execute(
                "INSERT OR IGNORE INTO teams (plugin_name, display_name) VALUES (?, ?)",
                (args.team, args.team.replace("-", " ").title()),
            )
            team = con.execute(
                "SELECT * FROM teams WHERE plugin_name = ?", (args.team,)
            ).fetchone()
            con.execute(
                "INSERT OR IGNORE INTO project_teams (project_id, team_id) VALUES (?, ?)",
                (project["id"], team["id"]),
            )
    if args.stamp_config:
        config_path = Path(args.stamp_config)
        config = {}
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        config["project_key"] = args.key
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if args.project_root:
        if not args.team:
            raise Rule("project contract initialization requires --team")
        try:
            state = upgrade_core.initialize_project_contract(
                data_dir(), args.project_root, args.team, args.workspace,
            )
        except upgrade_core.UpgradeError as exc:
            raise Rule(str(exc)) from exc
        state["project_key"] = args.key
        state_path = Path(args.project_root).resolve() / upgrade_core.STATE_RELATIVE
        upgrade_core.atomic_json(state_path, state, 0o644)
        with mutate(con):
            con.execute(
                "UPDATE projects SET project_uuid = ?, repository_fingerprint = ?"
                " WHERE id = ?",
                (state["project_id"], state.get("repository_fingerprint", ""),
                 project["id"]),
            )
            record(
                con, "project_contract_initialized", project_id=project["id"],
                payload={"project_uuid": state["project_id"],
                         "contract_version": state["contract_version"]},
            )
    print(f"pmo: project '{args.key}' registered")
    return 0


def cmd_project_list(args) -> int:
    con = connect()
    rows = con.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"pmo: {row['project_key']}  {row['name']}")
        if not rows:
            print("pmo: no projects registered")
    return 0


def check_key_date_prefix(work_order_key: str) -> None:
    """Reject a date-shaped key prefix that is not the current UTC date.

    Keys shaped <yyyymmdd>-... carry the mint date; the value must
    come off the clock (now --compact), never out of the model's
    memory. Yesterday is accepted so a key minted just before UTC
    midnight still initializes. Keys without a date-shaped prefix pass:
    the convention belongs to the team plugins, not to this backbone."""
    key = work_order_key
    if len(key) < 9 or not key[:8].isdigit() or key[8] != "-":
        return
    today = date.fromisoformat(now()[:10])
    allowed = {today.strftime("%Y%m%d"),
               (today - timedelta(days=1)).strftime("%Y%m%d")}
    if key[:8] not in allowed:
        raise Rule(
            f"work-order key date prefix '{key[:8]}' is not the current"
            f" UTC date ({today.strftime('%Y%m%d')}); timestamps come off"
            " the clock, never out of memory; mint the prefix with:"
            " pmo_cli.py now --compact"
        )


def cmd_wo_init(args) -> int:
    check_key_date_prefix(args.work_order_key)
    brief_source = Path(args.brief) if args.brief else None
    if brief_source is not None and not brief_source.is_dir():
        raise Rule("--brief must name an analysis-space directory")
    con = connect()
    worktree = norm_path(args.worktree)
    with mutate(con):
        project = get_project(con, args.project_key)
        for order in active_work_orders(con, project["id"]):
            if order["worktree_path"] == worktree:
                raise Rule(
                    f"active work order '{order['work_order_key']}' already holds"
                    f" worktree {worktree}; resume it or release it first"
                )
        story_id = None
        if args.story:
            story = get_item(con, project["id"], args.story)
            if story is None or story["kind"] != "story":
                raise Rule(f"story '{args.story}' is not in the backlog; import it first")
            claimed = con.execute(
                "SELECT work_order_key FROM work_orders WHERE story_id = ?"
                " AND status IN (?, ?)",
                (story["id"], *ACTIVE_WO_STATUSES),
            ).fetchone()
            if claimed:
                raise Rule(
                    f"story '{args.story}' is already claimed by active work order"
                    f" '{claimed['work_order_key']}'"
                )
            story_id = story["id"]
            con.execute(
                "UPDATE work_items SET status = 'in_development', updated_at = ?"
                " WHERE id = ?",
                (now(), story_id),
            )
        con.execute(
            "INSERT INTO work_orders (project_id, story_id, work_order_key, request,"
            " status, current_step, worktree_path, bindings_json, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, 'running', '0', ?, ?, ?, ?)",
            (project["id"], story_id, args.work_order_key, args.request, worktree,
             args.bindings or "{}", now(), now()),
        )
        order = get_work_order(con, args.work_order_key)
        if story_id is not None:
            # The claimed story keeps a pointer to the work order delivering
            # it (tasks already carry one); readers link the two directly.
            con.execute(
                "UPDATE work_items SET work_order_id = ?, updated_at = ?"
                " WHERE id = ?",
                (order["id"], now(), story_id),
            )
        for step_id in STEP_IDS:
            status = "in_progress" if step_id == "0" else "pending"
            con.execute(
                "INSERT INTO work_order_steps (work_order_id, step_id, status)"
                " VALUES (?, ?, ?)",
                (order["id"], step_id, status),
            )
        record(con, "work_order_initialized", project_id=project["id"],
               work_order_id=order["id"],
               payload={"work_order_key": args.work_order_key,
                        "story": args.story or "", "worktree": worktree})
    if args.order_dir:
        order_dir = Path(args.order_dir)
        order_dir.mkdir(parents=True, exist_ok=True)
        for src, dest in ((args.constitution, "constitution.md"),
                          (args.config, "config.snapshot.json")):
            if not src:
                continue
            source = Path(src)
            if source.is_file():
                shutil.copyfile(src, order_dir / dest)
        if brief_source is not None:
            dest_dir = order_dir / "brief-snapshot"
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(brief_source, dest_dir)
    print(f"pmo: work order '{args.work_order_key}' initialized (step 0 in progress)")
    return 0


def cmd_wo_set_step(args) -> int:
    if args.step not in STEP_IDS:
        return fail(f"unknown step: {args.step}", 2)
    if args.status not in STEP_STATUSES:
        return fail(f"step status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        if args.status == "in_progress":
            for prior in STEP_IDS[: STEP_IDS.index(args.step)]:
                row = con.execute(
                    "SELECT status FROM work_order_steps WHERE work_order_id = ?"
                    " AND step_id = ?",
                    (order["id"], prior),
                ).fetchone()
                if row["status"] != "done":
                    raise Rule(
                        f"transition guard: step {prior} is not done;"
                        f" step {args.step} cannot start"
                    )
            con.execute(
                "UPDATE work_orders SET current_step = ?, updated_at = ? WHERE id = ?",
                (args.step, now(), order["id"]),
            )
        con.execute(
            "UPDATE work_order_steps SET status = ?,"
            " artifact_path = CASE WHEN ? = '' THEN artifact_path ELSE ? END,"
            " attempts = attempts + ? WHERE work_order_id = ? AND step_id = ?",
            (args.status, args.artifact, args.artifact,
             1 if args.bump_attempts else 0, order["id"], args.step),
        )
        con.execute("UPDATE work_orders SET updated_at = ? WHERE id = ?",
                    (now(), order["id"]))
        record(con, "step_changed", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"step": args.step, "status": args.status,
                        "artifact": args.artifact})
    print(f"pmo: step {args.step} -> {args.status}")
    return 0


def cmd_wo_record_gate(args) -> int:
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        con.execute(
            "INSERT INTO gates (work_order_id, name, decision, decided_by, decided_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (work_order_id, name) DO UPDATE SET"
            " decision = excluded.decision, decided_by = excluded.decided_by,"
            " decided_at = excluded.decided_at",
            (order["id"], args.gate, args.decision, args.decided_by, now()),
        )
        con.execute("UPDATE work_orders SET updated_at = ? WHERE id = ?",
                    (now(), order["id"]))
        record(con, "gate_recorded", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"gate": args.gate, "decision": args.decision})
    print(f"pmo: gate {args.gate} -> {args.decision}")
    return 0


def cmd_wo_bump(args) -> int:
    if args.counter not in ("review", "qa"):
        return fail(f"unknown counter: {args.counter}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        column = "review_rounds" if args.counter == "review" else "qa_rounds"
        con.execute(
            f"UPDATE work_orders SET {column} = {column} + 1, updated_at = ?"
            " WHERE id = ?",
            (now(), order["id"]),
        )
        value = con.execute(
            f"SELECT {column} AS v FROM work_orders WHERE id = ?", (order["id"],)
        ).fetchone()["v"]
        record(con, "round_bumped", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"counter": args.counter, "value": value})
    print(f"pmo: {args.counter} rounds = {value}")
    return 0


def cmd_wo_set_ownership(args) -> int:
    try:
        ownership = json.loads(args.ownership)
    except json.JSONDecodeError as exc:
        return fail(f"ownership is not valid JSON: {exc}", 2)
    if not isinstance(ownership, dict):
        return fail("ownership must be a JSON object of role -> path list", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        flat: list[tuple[str, str]] = []
        for role, paths in ownership.items():
            if not is_snake(role):
                raise Rule(f"ownership key must be a snake_case role name: {role}")
            for path in paths:
                flat.append((role, path))
        for i, (role_a, path_a) in enumerate(flat):
            for role_b, path_b in flat[i + 1:]:
                if role_a != role_b and prefixes_overlap(path_a, path_b):
                    raise Rule(
                        f"ownership overlap inside the work order: {role_a}:{path_a}"
                        f" overlaps {role_b}:{path_b}"
                    )
        for other in active_work_orders(con, order["project_id"]):
            if other["id"] == order["id"]:
                continue
            for row in con.execute(
                "SELECT role, path_prefix FROM ownership WHERE work_order_id = ?",
                (other["id"],),
            ):
                for role, path in flat:
                    if prefixes_overlap(path, row["path_prefix"]):
                        raise Rule(
                            f"ownership overlap across work orders: {role}:{path}"
                            f" overlaps {row['role']}:{row['path_prefix']} held by"
                            f" active work order '{other['work_order_key']}'"
                        )
        con.execute("DELETE FROM ownership WHERE work_order_id = ?", (order["id"],))
        for role, path in flat:
            con.execute(
                "INSERT INTO ownership (work_order_id, role, path_prefix)"
                " VALUES (?, ?, ?)",
                (order["id"], role, path),
            )
        con.execute("UPDATE work_orders SET updated_at = ? WHERE id = ?",
                    (now(), order["id"]))
        record(con, "ownership_set", project_id=order["project_id"],
               work_order_id=order["id"], payload={"ownership": ownership})
    print("pmo: ownership map stored")
    return 0


def cmd_wo_set_status(args) -> int:
    if args.status not in WO_STATUSES:
        return fail(f"work order status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        if args.status == "complete":
            require_main_line(order)
        else:
            require_cwd_inside(order)
        if (args.status in ACTIVE_WO_STATUSES
                and order["status"] not in ACTIVE_WO_STATUSES):
            revalidate_claims(con, order)
        if args.status == "complete":
            not_done = [
                row["step_id"]
                for row in con.execute(
                    "SELECT step_id FROM work_order_steps WHERE work_order_id = ?"
                    " AND status != 'done' ORDER BY step_id",
                    (order["id"],),
                )
            ]
            if not_done:
                raise Rule(
                    f"complete guard: steps not done: {', '.join(not_done)}"
                )
            open_findings = [
                row["external_id"]
                for row in con.execute(
                    "SELECT external_id FROM findings WHERE work_order_id = ?"
                    " AND status = 'open' ORDER BY external_id",
                    (order["id"],),
                )
            ]
            if open_findings:
                raise Rule(
                    "complete guard: open findings remain:"
                    f" {', '.join(open_findings)}"
                )
            if order["story_id"] is not None:
                covered = con.execute(
                    "SELECT COUNT(*) FROM coverage WHERE work_order_id = ?",
                    (order["id"],),
                ).fetchone()[0]
                if covered == 0:
                    raise Rule(
                        "complete guard: story work order has no coverage rows;"
                        " import the QA coverage (coverage import) first"
                    )
                ledgered = con.execute(
                    "SELECT COUNT(*) FROM ledger WHERE work_order_id = ?",
                    (order["id"],),
                ).fetchone()[0]
                if ledgered == 0:
                    raise Rule(
                        "complete guard: story work order has no ledger line;"
                        " run the checkpoint subcommand first"
                    )
        con.execute(
            "UPDATE work_orders SET status = ?, updated_at = ? WHERE id = ?",
            (args.status, now(), order["id"]),
        )
        record(con, "work_order_status_changed", project_id=order["project_id"],
               work_order_id=order["id"], payload={"status": args.status})
    print(f"pmo: work order status -> {args.status}")
    return 0


def cmd_wo_release(args) -> int:
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        if order["status"] in ACTIVE_WO_STATUSES:
            con.execute(
                "UPDATE work_orders SET status = 'blocked', updated_at = ?"
                " WHERE id = ?",
                (now(), order["id"]),
            )
            record(con, "work_order_released", project_id=order["project_id"],
                   work_order_id=order["id"],
                   payload={"previous_status": order["status"]})
            print(f"pmo: work order '{args.work_order_key}' released"
                  " (status blocked; claims freed)")
        else:
            print(f"pmo: work order '{args.work_order_key}' is not active;"
                  " nothing to release")
    return 0


def cmd_wo_validate(args) -> int:
    con = connect()
    order = get_work_order(con, args.work_order_key)
    problems: list[str] = []
    integrity = verify_integrity(con)
    if integrity:
        problems.append(integrity)
    if order["status"] not in WO_STATUSES:
        problems.append(f"work order status not in enum: {order['status']}")
    steps = {
        row["step_id"]: row
        for row in con.execute(
            "SELECT * FROM work_order_steps WHERE work_order_id = ?", (order["id"],)
        )
    }
    for step_id in STEP_IDS:
        if step_id not in steps:
            problems.append(f"missing step row: {step_id}")
        elif steps[step_id]["status"] not in STEP_STATUSES:
            problems.append(f"step {step_id} status not in enum")
    if order["status"] == "complete":
        for step_id, row in steps.items():
            if row["status"] != "done":
                problems.append(f"work order complete but step {step_id} is not done")
    for row in con.execute(
        "SELECT DISTINCT role FROM ownership WHERE work_order_id = ?", (order["id"],)
    ):
        if not is_snake(row["role"]):
            problems.append(f"ownership role not snake_case: {row['role']}")
    if problems:
        for problem in problems:
            print(f"pmo: INVALID: {problem}", file=sys.stderr)
        return 1
    print("pmo: work order state is valid")
    return 0


def cmd_resume_info(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    orders = active_work_orders(con, project["id"])
    info = {"project_key": args.project_key, "active_work_orders": [],
            "recent_events": []}
    integrity = verify_integrity(con)
    if integrity:
        info["integrity_warning"] = integrity
        print(f"pmo: WARNING: {integrity}", file=sys.stderr)
    for order in orders:
        steps = con.execute(
            "SELECT step_id, status, attempts FROM work_order_steps"
            " WHERE work_order_id = ? ORDER BY step_id",
            (order["id"],),
        ).fetchall()
        gates = con.execute(
            "SELECT name, decision FROM gates WHERE work_order_id = ? ORDER BY name",
            (order["id"],),
        ).fetchall()
        story = None
        if order["story_id"]:
            row = con.execute(
                "SELECT external_id, title FROM work_items WHERE id = ?",
                (order["story_id"],),
            ).fetchone()
            story = f"{row['external_id']} {row['title']}"
        ownership: dict[str, list[str]] = {}
        for row in con.execute(
            "SELECT role, path_prefix FROM ownership WHERE work_order_id = ?"
            " ORDER BY role, path_prefix",
            (order["id"],),
        ):
            ownership.setdefault(row["role"], []).append(row["path_prefix"])
        info["active_work_orders"].append({
            "work_order_key": order["work_order_key"],
            "status": order["status"],
            "current_step": order["current_step"],
            "story": story,
            "worktree": order["worktree_path"],
            "steps": {s["step_id"]: s["status"] for s in steps},
            "gates": {g["name"]: g["decision"] for g in gates},
            "ownership": ownership,
            "review_rounds": order["review_rounds"],
            "qa_rounds": order["qa_rounds"],
        })
    for row in con.execute(
        "SELECT ts, actor, action, payload_json FROM events WHERE project_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (project["id"], args.events),
    ):
        info["recent_events"].append(
            {"ts": row["ts"], "actor": row["actor"], "action": row["action"],
             "payload": json.loads(row["payload_json"])}
        )
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0
    if not orders:
        print(f"pmo: no active work order for project '{args.project_key}'")
        return 0
    for order in info["active_work_orders"]:
        story = f" story {order['story']}" if order["story"] else ""
        print(
            f"pmo: active work order '{order['work_order_key']}' status"
            f" {order['status']} at step {order['current_step']}{story}"
            f" (worktree {order['worktree']})"
        )
        done = [s for s, status in sorted(order["steps"].items()) if status == "done"]
        print(f"pmo:   steps done: {', '.join(done) or 'none'};"
              f" review rounds {order['review_rounds']},"
              f" qa rounds {order['qa_rounds']}")
    return 0


def load_import_file(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rule(f"cannot read import file: {exc}")
    if not isinstance(data, dict):
        raise Rule("import file must be a JSON object")
    return data


def normalize_deps(raw) -> list[dict]:
    """Accept ["WP-01", ...] or [{"item": "WP-01", "reason": "..."}, ...]."""
    deps = []
    for entry in raw or []:
        if isinstance(entry, str):
            deps.append({"item": entry, "reason": ""})
        elif isinstance(entry, dict) and entry.get("item"):
            deps.append({"item": entry["item"], "reason": entry.get("reason", "")})
        else:
            raise Rule(f"malformed depends_on entry: {entry!r}")
    return deps


def replace_item_deps(con, project_id: int, item_row, deps: list[dict]) -> None:
    """Replace an item's authored dependency edges with the given list."""
    con.execute("DELETE FROM work_item_deps WHERE item_id = ?", (item_row["id"],))
    for dep in deps:
        target = get_item(con, project_id, dep["item"])
        if target is None:
            raise Rule(
                f"{item_row['external_id']}: depends_on target"
                f" '{dep['item']}' not found"
            )
        if target["kind"] != item_row["kind"]:
            raise Rule(
                f"{item_row['external_id']}: dependency crosses kinds"
                f" ({item_row['kind']} -> {target['kind']}); same kind only"
            )
        if target["id"] == item_row["id"]:
            raise Rule(f"{item_row['external_id']}: depends on itself")
        con.execute(
            "INSERT INTO work_item_deps (project_id, item_id, depends_on_id,"
            " reason, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (item_id, depends_on_id) DO UPDATE SET"
            " reason = excluded.reason",
            (project_id, item_row["id"], target["id"], dep["reason"], now()),
        )


def sync_dod_items(con, item_row, statements: list) -> None:
    """Reconcile authored DoD statements: keep verified/failed rows (they are
    evidence), drop pending rows no longer authored, add the new ones."""
    incoming = [str(s).strip() for s in statements if str(s).strip()]
    query = "DELETE FROM dod_items WHERE item_id = ? AND status = 'pending'"
    params: list = [item_row["id"]]
    if incoming:
        marks = ", ".join("?" for _ in incoming)
        query += f" AND statement NOT IN ({marks})"
        params += incoming
    con.execute(query, params)
    for statement in incoming:
        con.execute(
            "INSERT INTO dod_items (item_id, statement, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (item_id, statement) DO NOTHING",
            (item_row["id"], statement, now(), now()),
        )


def cmd_item_import(args) -> int:
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        data = load_import_file(args.json_file)
        epics = data.get("epics", [])
        stories = data.get("stories", [])
        problems: list[str] = []
        epic_ids = {e.get("external_id", "") for e in epics}
        for epic in epics:
            if not epic.get("external_id") or not epic.get("title"):
                problems.append(f"epic missing external_id or title: {epic}")
        for story in stories:
            sid = story.get("external_id", "(missing id)")
            for field in STORY_REQUIRED_FIELDS:
                if not str(story.get(field, "")).strip():
                    problems.append(f"story {sid}: required field '{field}' is empty")
            epic_ref = story.get("epic", "")
            if epic_ref and epic_ref not in epic_ids:
                existing = get_item(con, project["id"], epic_ref)
                if existing is None or existing["kind"] != "epic":
                    problems.append(f"story {sid}: epic '{epic_ref}' not found")
        if problems:
            raise Rule("import rejected:\n  " + "\n  ".join(problems))
        for epic in epics:
            status = epic.get("status", "open")
            if status not in EPIC_STATUSES:
                raise Rule(f"epic {epic['external_id']}: status '{status}' not in enum")
            existing = get_item(con, project["id"], epic["external_id"])
            if existing is None:
                con.execute(
                    "INSERT INTO work_items (project_id, kind, external_id, title,"
                    " status, scope, created_at, updated_at)"
                    " VALUES (?, 'epic', ?, ?, ?, ?, ?, ?)",
                    (project["id"], epic["external_id"], epic["title"], status,
                     epic.get("goal", ""), now(), now()),
                )
            else:
                con.execute(
                    "UPDATE work_items SET title = ?, status = ?, scope = ?,"
                    " updated_at = ? WHERE id = ?",
                    (epic["title"], status, epic.get("goal", ""), now(),
                     existing["id"]),
                )
        for story in stories:
            status = story.get("status", "planned")
            if status not in STORY_STATUSES:
                raise Rule(
                    f"story {story['external_id']}: status '{status}' not in enum"
                )
            check_priority(story.get("priority", ""),
                           f"story {story['external_id']}")
            parent = get_item(con, project["id"], story["epic"])
            existing = get_item(con, project["id"], story["external_id"])
            values = (
                story["title"], status, story.get("type", ""),
                story.get("priority", ""),
                story["scope"], story["excludes"], story["dor"], story["dod"],
                parent["id"] if parent else None,
            )
            if existing is None:
                con.execute(
                    "INSERT INTO work_items (project_id, kind, external_id, title,"
                    " status, item_type, priority, scope, excludes,"
                    " dor, dod, parent_id, created_at, updated_at)"
                    " VALUES (?, 'story', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project["id"], story["external_id"], *values, now(), now()),
                )
            else:
                con.execute(
                    "UPDATE work_items SET title = ?, status = ?, item_type = ?,"
                    " priority = ?, scope = ?, excludes = ?,"
                    " dor = ?, dod = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                    (*values, now(), existing["id"]),
                )
        # Structured dependencies and DoD items, after every story exists so
        # forward references inside one import resolve.
        for story in stories:
            row = get_item(con, project["id"], story["external_id"])
            deps = normalize_deps(story.get("depends_on"))
            replace_item_deps(con, project["id"], row, deps)
            if "dod_items" in story:
                sync_dod_items(con, row, story.get("dod_items") or [])
        nodes, edges = project_dep_graph(con, project["id"], "story")
        _, cycles = topo_order(nodes, edges)
        if cycles:
            raise Rule(
                "import rejected: dependency cycle among stories:"
                f" {', '.join(cycles)}"
            )
        for criterion in data.get("criteria", []):
            cid = criterion.get("criterion_id", "")
            if not cid:
                raise Rule(f"criterion missing criterion_id: {criterion}")
            disposition = criterion.get("disposition", "covered")
            story_ref = criterion.get("story", "")
            story_row = get_item(con, project["id"], story_ref) if story_ref else None
            if disposition == "covered" and story_row is None:
                raise Rule(f"criterion {cid}: covered but story '{story_ref}' not found")
            con.execute(
                "INSERT INTO story_criteria (project_id, criterion_id, story_id,"
                " disposition, reason) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, criterion_id) DO UPDATE SET"
                " story_id = excluded.story_id, disposition = excluded.disposition,"
                " reason = excluded.reason",
                (project["id"], cid, story_row["id"] if story_row else None,
                 disposition, criterion.get("reason", "")),
            )
        for question in data.get("open_questions", []):
            existing = con.execute(
                "SELECT id FROM open_questions WHERE project_id = ? AND question = ?",
                (project["id"], question),
            ).fetchone()
            if existing is None:
                con.execute(
                    "INSERT INTO open_questions (project_id, question, created_at)"
                    " VALUES (?, ?, ?)",
                    (project["id"], question, now()),
                )
        record(con, "backlog_imported", project_id=project["id"],
               payload={"epics": len(epics), "stories": len(stories)})
    print(f"pmo: imported {len(epics)} epic(s), {len(stories)} story(ies)")
    return 0


ITEM_UPDATE_FIELDS = {
    "status": "status",
    "priority": "priority",
    "title": "title",
    "scope": "scope",
    "excludes": "excludes",
    "dor": "dor",
    "dod": "dod",
    "type": "item_type",
}


def cmd_item_update(args) -> int:
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        item = get_item(con, project["id"], args.external_id)
        if item is None:
            raise Rule(f"no work item '{args.external_id}' in project")
        updates: dict[str, str] = {}
        for flag, column in ITEM_UPDATE_FIELDS.items():
            value = getattr(args, flag.replace("-", "_"), None)
            if value is not None:
                updates[column] = value
        if "status" in updates and updates["status"] not in STATUSES_BY_KIND[item["kind"]]:
            raise Rule(
                f"status '{updates['status']}' not in {item['kind']} enum"
            )
        if "priority" in updates:
            check_priority(updates["priority"], args.external_id)
        if args.deployed_verified is not None:
            updates["deployed_verified"] = 1 if args.deployed_verified == "true" else 0
        if not updates:
            raise Rule("nothing to update; pass at least one field flag")
        assignments = ", ".join(f"{col} = ?" for col in updates)
        con.execute(
            f"UPDATE work_items SET {assignments}, updated_at = ? WHERE id = ?",
            (*updates.values(), now(), item["id"]),
        )
        record(con, "item_updated", project_id=project["id"],
               payload={"external_id": args.external_id, "fields": sorted(updates)})
    print(f"pmo: {args.external_id} updated")
    return 0


def cmd_item_list(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    query = "SELECT * FROM work_items WHERE project_id = ?"
    params: list = [project["id"]]
    if args.kind:
        query += " AND kind = ?"
        params.append(args.kind)
    if args.status:
        query += " AND status = ?"
        params.append(args.status)
    query += " ORDER BY kind, external_id"
    rows = con.execute(query, params).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"pmo: {row['kind']:5} {row['external_id']:8} {row['status']:15}"
                  f" {row['title']}")
        if not rows:
            print("pmo: no matching work items")
    return 0


def cmd_item_add_dep(args) -> int:
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        item = get_item(con, project["id"], args.item)
        target = get_item(con, project["id"], args.depends_on)
        if item is None:
            raise Rule(f"no work item '{args.item}' in project")
        if target is None:
            raise Rule(f"no work item '{args.depends_on}' in project")
        if item["kind"] == "epic" or target["kind"] == "epic":
            raise Rule("epics never carry dependency edges; they are groupings")
        if item["kind"] != target["kind"]:
            raise Rule(
                f"dependency crosses kinds ({item['kind']} -> {target['kind']});"
                " same kind only"
            )
        if item["id"] == target["id"]:
            raise Rule(f"'{args.item}' cannot depend on itself")
        cycle = dep_cycle_path(con, item["id"], target["id"])
        if cycle:
            raise Rule(
                "dependency rejected: it closes a cycle"
                f" {args.item} -> {' -> '.join(cycle)}"
            )
        con.execute(
            "INSERT INTO work_item_deps (project_id, item_id, depends_on_id,"
            " reason, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (item_id, depends_on_id) DO UPDATE SET"
            " reason = excluded.reason",
            (project["id"], item["id"], target["id"], args.reason, now()),
        )
        record(con, "dep_added", project_id=project["id"],
               payload={"item": args.item, "depends_on": args.depends_on,
                        "reason": args.reason})
    print(f"pmo: {args.item} depends on {args.depends_on}")
    return 0


def cmd_item_remove_dep(args) -> int:
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        item = get_item(con, project["id"], args.item)
        target = get_item(con, project["id"], args.depends_on)
        if item is None or target is None:
            raise Rule("both items must exist in the project")
        gone = con.execute(
            "DELETE FROM work_item_deps WHERE item_id = ? AND depends_on_id = ?",
            (item["id"], target["id"]),
        ).rowcount
        if not gone:
            raise Rule(f"no dependency {args.item} -> {args.depends_on} recorded")
        record(con, "dep_removed", project_id=project["id"],
               payload={"item": args.item, "depends_on": args.depends_on})
    print(f"pmo: dependency {args.item} -> {args.depends_on} removed")
    return 0


def cmd_item_list_deps(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    query = (
        "SELECT a.external_id AS item, b.external_id AS depends_on, d.reason"
        " FROM work_item_deps d"
        " JOIN work_items a ON a.id = d.item_id"
        " JOIN work_items b ON b.id = d.depends_on_id"
        " WHERE d.project_id = ?"
    )
    params: list = [project["id"]]
    if args.item:
        query += " AND (a.external_id = ? OR b.external_id = ?)"
        params += [args.item, args.item]
    query += " ORDER BY a.external_id, b.external_id"
    rows = [dict(r) for r in con.execute(query, params)]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            reason = f" ({row['reason']})" if row["reason"] else ""
            print(f"pmo: {row['item']} -> {row['depends_on']}{reason}")
        if not rows:
            print("pmo: no dependencies recorded")
    return 0


def cmd_item_add_dod(args) -> int:
    statement = args.statement.strip()
    if not statement:
        return fail("statement must not be empty", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        item = get_item(con, project["id"], args.item)
        if item is None:
            raise Rule(f"no work item '{args.item}' in project")
        con.execute(
            "INSERT INTO dod_items (item_id, statement, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (item_id, statement) DO NOTHING",
            (item["id"], statement, now(), now()),
        )
        row = con.execute(
            "SELECT id FROM dod_items WHERE item_id = ? AND statement = ?",
            (item["id"], statement),
        ).fetchone()
        record(con, "dod_added", project_id=project["id"],
               payload={"item": args.item, "dod_id": row["id"]})
    print(f"pmo: dod item {row['id']} on {args.item}")
    return 0


def cmd_item_set_dod(args) -> int:
    if args.status not in DOD_STATUSES:
        return fail(f"dod status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        row = con.execute(
            "SELECT d.*, w.external_id, w.project_id FROM dod_items d"
            " JOIN work_items w ON w.id = d.item_id WHERE d.id = ?",
            (args.dod_id,),
        ).fetchone()
        if row is None or row["project_id"] != project["id"]:
            raise Rule(f"no dod item {args.dod_id} in project")
        if args.status == "failed" and not args.failure_reason:
            raise Rule("failed requires --failure-reason")
        verified_at = now() if args.status == "verified" else ""
        failure = args.failure_reason if args.status == "failed" else ""
        con.execute(
            "UPDATE dod_items SET status = ?, verified_at = ?, failure_reason = ?,"
            " updated_at = ? WHERE id = ?",
            (args.status, verified_at, failure, now(), args.dod_id),
        )
        record(con, "dod_updated", project_id=project["id"],
               payload={"item": row["external_id"], "dod_id": args.dod_id,
                        "status": args.status})
    print(f"pmo: dod item {args.dod_id} -> {args.status}")
    return 0


def cmd_item_list_dod(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    item = get_item(con, project["id"], args.item)
    if item is None:
        return fail(f"no work item '{args.item}' in project")
    rows = [dict(r) for r in con.execute(
        "SELECT id, statement, status, verified_at, failure_reason"
        " FROM dod_items WHERE item_id = ? ORDER BY id",
        (item["id"],),
    )]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            mark = {"pending": " ", "verified": "x", "failed": "!"}[row["status"]]
            print(f"pmo: [{mark}] {row['id']:4} {row['statement']}")
        if not rows:
            print("pmo: no dod items recorded")
    return 0


def cmd_item_order(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    nodes, edges = project_dep_graph(con, project["id"], args.kind)
    order, cycles = topo_order(nodes, edges)
    if args.json:
        print(json.dumps({"order": order, "cycles": cycles},
                         indent=2, sort_keys=True))
    else:
        for pos, external_id in enumerate(order, 1):
            print(f"pmo: {pos:3}. {external_id}")
        for external_id in cycles:
            print(f"pmo: CYCLE {external_id}", file=sys.stderr)
        if not order and not cycles:
            print(f"pmo: no {args.kind} items")
    return 0


def cmd_item_ready(args) -> int:
    """The dispatch surface: which stories may start now, and why the rest
    cannot. Derived entirely from existing rows; never stored."""
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    nodes, edges = project_dep_graph(con, project["id"], "story")
    order_list, cycles = topo_order(nodes, edges)
    topo_pos = {eid: i + 1 for i, eid in enumerate(order_list)}
    stories = {
        row["external_id"]: row
        for row in con.execute(
            "SELECT * FROM work_items WHERE project_id = ? AND kind = 'story'",
            (project["id"],),
        )
    }
    claims = {}
    for active in active_work_orders(con, project["id"]):
        if active["story_id"] is not None:
            claims[active["story_id"]] = active
    dep_rows = con.execute(
        "SELECT a.external_id AS item, b.external_id AS dep, b.status AS dep_status,"
        " d.reason FROM work_item_deps d"
        " JOIN work_items a ON a.id = d.item_id"
        " JOIN work_items b ON b.id = d.depends_on_id"
        " WHERE d.project_id = ? AND a.kind = 'story' AND b.kind = 'story'"
        " ORDER BY a.external_id, b.external_id",
        (project["id"],),
    ).fetchall()
    unmet = {}
    for row in dep_rows:
        if row["dep_status"] != "done":
            unmet.setdefault(row["item"], []).append(
                {"item": row["dep"], "status": row["dep_status"],
                 "reason": row["reason"]})
    result = {"project_key": args.project_key, "ready": [], "blocked": [],
              "claimed": [], "stale_in_development": [], "cycles": cycles}
    for eid in sorted(stories, key=lambda e: topo_pos.get(e, 10 ** 6)):
        story = stories[eid]
        active = claims.get(story["id"])
        if active is not None:
            result["claimed"].append({
                "external_id": eid,
                "work_order_key": active["work_order_key"],
                "worktree": active["worktree_path"],
            })
            continue
        if story["status"] == "in_development":
            result["stale_in_development"].append(eid)
            continue
        if story["status"] not in ("planned", "ready"):
            continue
        if eid in unmet:
            result["blocked"].append({"external_id": eid,
                                      "blocked_by": unmet[eid]})
            continue
        if eid in cycles:
            continue
        result["ready"].append({
            "external_id": eid, "title": story["title"],
            "priority": story["priority"],
            "topo_position": topo_pos.get(eid),
        })
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    for entry in result["ready"]:
        print(f"pmo: READY   {entry['external_id']:8} {entry['title']}")
    for entry in result["blocked"]:
        holders = ", ".join(b["item"] for b in entry["blocked_by"])
        print(f"pmo: BLOCKED {entry['external_id']:8} waits on {holders}")
    for entry in result["claimed"]:
        print(f"pmo: CLAIMED {entry['external_id']:8} by {entry['work_order_key']}")
    for eid in result["stale_in_development"]:
        print(f"pmo: STALE   {eid:8} in_development with no active work order")
    if not any((result["ready"], result["blocked"], result["claimed"],
                result["stale_in_development"])):
        print("pmo: no stories in the backlog")
    return 0


def cmd_task_open(args) -> int:
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        external_id = next_external_id(con, order["project_id"], "T")
        con.execute(
            "INSERT INTO work_items (project_id, kind, external_id, parent_id,"
            " title, status, work_order_id, role, step_id, created_at, updated_at)"
            " VALUES (?, 'task', ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
            (order["project_id"], external_id, order["story_id"], args.title,
             order["id"], args.role, args.step, now(), now()),
        )
        record(con, "task_opened", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"task": external_id, "role": args.role, "step": args.step})
    print(f"pmo: task {external_id} opened ({args.role}, step {args.step})")
    return 0


def find_task(con, order, role: str, step: str | None) -> sqlite3.Row | None:
    query = ("SELECT * FROM work_items WHERE work_order_id = ? AND kind = 'task'"
             " AND role = ?")
    params: list = [order["id"], role]
    if step:
        query += " AND step_id = ?"
        params.append(step)
    query += " ORDER BY id DESC LIMIT 1"
    return con.execute(query, params).fetchone()


def cmd_task_close(args) -> int:
    if args.outcome not in ("done", "blocked"):
        return fail(f"outcome must be done or blocked, got: {args.outcome}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        task = find_task(con, order, args.role, args.step)
        if task is None:
            raise Rule(
                f"no task for role '{args.role}' in work order"
                f" '{args.work_order_key}'"
            )
        finished = task["finished_at"] or now()
        con.execute(
            "UPDATE work_items SET status = ?, finished_at = ?, updated_at = ?"
            " WHERE id = ?",
            (args.outcome, finished, now(), task["id"]),
        )
        closed = close_running_attempts(con, task["id"], args.outcome, now())
        if closed == 0:
            # No hook ever opened an attempt (hooks disabled or not
            # trusted): the CLI is the guarantee, so synthesize one
            # spanning the task's own stamps, honestly labeled.
            existing = con.execute(
                "SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = ?",
                (task["id"],),
            ).fetchone()["n"]
            if existing == 0:
                started = task["started_at"] or task["created_at"]
                attempt = open_attempt(con, task["id"], args.role, "",
                                       "", started, source="cli_inferred")
                con.execute(
                    "UPDATE task_attempts SET outcome = ?, finished_at = ?"
                    " WHERE task_id = ? AND attempt = ?",
                    (args.outcome, finished, task["id"], attempt),
                )
                record(con, "attempt_synthesized",
                       project_id=order["project_id"],
                       work_order_id=order["id"],
                       payload={"task": task["external_id"],
                                "source": "cli_inferred"})
        record(con, "task_closed", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"task": task["external_id"], "outcome": args.outcome})
    print(f"pmo: task {task['external_id']} -> {args.outcome}")
    return 0


def cmd_task_touch(args) -> int:
    if args.phase not in ("start", "stop"):
        return fail(f"phase must be start or stop, got: {args.phase}", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        orders = active_work_orders(con, project["id"])
        if args.worktree:
            wanted = norm_path(args.worktree)
            orders = [o for o in orders if o["worktree_path"] == wanted]
        if not orders:
            record(con, "task_touch_unmatched", project_id=project["id"],
                   actor="hook", payload={"role": args.role, "phase": args.phase})
            print("pmo: no active work order matched; touch recorded as an event only")
            return 0
        order = orders[0]
        task = find_task(con, order, args.role, None)
        stamp = now()
        if task is None:
            if args.phase == "stop":
                record(con, "task_touch_unmatched", project_id=project["id"],
                       work_order_id=order["id"], actor="hook",
                       payload={"role": args.role, "phase": "stop"})
                print("pmo: no task row for role; touch recorded as an event only")
                return 0
            external_id = next_external_id(con, order["project_id"], "T")
            con.execute(
                "INSERT INTO work_items (project_id, kind, external_id, parent_id,"
                " title, status, work_order_id, role, step_id, started_at,"
                " created_at, updated_at) VALUES (?, 'task', ?, ?, ?,"
                " 'in_progress', ?, ?, ?, ?, ?, ?)",
                (order["project_id"], external_id, order["story_id"],
                 f"auto-recorded {args.role} work", order["id"], args.role,
                 order["current_step"], stamp, stamp, stamp),
            )
            task_id = con.execute(
                "SELECT id FROM work_items WHERE project_id = ? AND external_id = ?",
                (order["project_id"], external_id),
            ).fetchone()["id"]
            attempt = open_attempt(con, task_id, args.role, args.agent,
                                   args.session_id, stamp)
            record(con, "task_started", project_id=order["project_id"],
                   work_order_id=order["id"], actor="hook",
                   payload={"task": external_id, "role": args.role})
            record(con, "attempt_started", project_id=order["project_id"],
                   work_order_id=order["id"], actor="hook",
                   payload={"task": external_id, "attempt": attempt,
                            "agent": args.agent})
            print(f"pmo: task {external_id} auto-opened and started")
            return 0
        if args.phase == "start":
            started = task["started_at"] or stamp
            status = "in_progress" if task["status"] == "open" else task["status"]
            con.execute(
                "UPDATE work_items SET started_at = ?, status = ?, updated_at = ?"
                " WHERE id = ?",
                (started, status, stamp, task["id"]),
            )
            superseded = close_running_attempts(
                con, task["id"], "failed", stamp,
                failure_reason="superseded by new dispatch",
            )
            attempt = open_attempt(con, task["id"], args.role, args.agent,
                                   args.session_id, stamp)
            record(con, "task_started", project_id=order["project_id"],
                   work_order_id=order["id"], actor="hook",
                   payload={"task": task["external_id"], "role": args.role})
            record(con, "attempt_started", project_id=order["project_id"],
                   work_order_id=order["id"], actor="hook",
                   payload={"task": task["external_id"], "attempt": attempt,
                            "agent": args.agent, "superseded": superseded})
        else:
            con.execute(
                "UPDATE work_items SET finished_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, task["id"]),
            )
            closed = close_running_attempts(con, task["id"], "done", stamp,
                                            cost_usd=args.cost_usd)
            if closed:
                record(con, "attempt_finished", project_id=order["project_id"],
                       work_order_id=order["id"], actor="hook",
                       payload={"task": task["external_id"]})
            else:
                record(con, "attempt_unmatched", project_id=order["project_id"],
                       work_order_id=order["id"], actor="hook",
                       payload={"task": task["external_id"], "phase": "stop"})
            record(con, "task_finished", project_id=order["project_id"],
                   work_order_id=order["id"], actor="hook",
                   payload={"task": task["external_id"], "role": args.role})
    print(f"pmo: task {task['external_id']} touched ({args.phase})")
    return 0


def cmd_finding_open(args) -> int:
    if args.source not in FINDING_SOURCES:
        return fail(f"source not in enum: {args.source}", 2)
    if args.severity not in FINDING_SEVERITIES:
        return fail(f"severity not in enum: {args.severity}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        external_id = next_finding_id(con, order["project_id"])
        con.execute(
            "INSERT INTO findings (project_id, work_order_id, story_id, external_id,"
            " source, severity, summary, repro, expected_actual,"
            " traced_requirement, opened_round, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order["project_id"], order["id"], order["story_id"], external_id,
             args.source, args.severity, args.summary, args.repro,
             args.expected_actual, args.traced, args.round, now(), now()),
        )
        record(con, "finding_opened", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"finding": external_id, "source": args.source,
                        "severity": args.severity})
    print(f"pmo: finding {external_id} opened ({args.source}, {args.severity})")
    return 0


def cmd_finding_update(args) -> int:
    if args.status not in FINDING_STATUSES:
        return fail(f"status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        finding = con.execute(
            "SELECT * FROM findings WHERE project_id = ? AND external_id = ?",
            (order["project_id"], args.finding),
        ).fetchone()
        if finding is None:
            raise Rule(f"no finding '{args.finding}' in this project")
        con.execute(
            "UPDATE findings SET status = ?, closed_round = ?, updated_at = ?"
            " WHERE id = ?",
            (args.status,
             args.round if args.status in ("fixed", "waived") else None,
             now(), finding["id"]),
        )
        record(con, "finding_updated", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"finding": args.finding, "status": args.status})
    print(f"pmo: finding {args.finding} -> {args.status}")
    return 0


def cmd_finding_list(args) -> int:
    con = connect()
    try:
        order = get_work_order(con, args.work_order_key)
    except Rule as exc:
        return fail(str(exc))
    query = "SELECT * FROM findings WHERE work_order_id = ?"
    params: list = [order["id"]]
    if args.status:
        query += " AND status = ?"
        params.append(args.status)
    query += " ORDER BY external_id"
    rows = con.execute(query, params).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"pmo: {row['external_id']} [{row['severity']}/{row['source']}]"
                  f" {row['status']:6} {row['summary']}")
        if not rows:
            print("pmo: no findings recorded")
    return 0


def cmd_issue_open(args) -> int:
    if args.kind not in ISSUE_KINDS:
        return fail(f"kind not in enum: {args.kind}", 2)
    con = connect()
    with mutate(con):
        project_id = None
        work_order_id = None
        if args.work_order_key:
            order = get_work_order(con, args.work_order_key)
            work_order_id = order["id"]
            project_id = order["project_id"]
        elif args.project_key:
            project_id = get_project(con, args.project_key)["id"]
        external_id = next_issue_candidate_id(con)
        con.execute(
            "INSERT INTO issue_candidates (project_id, work_order_id,"
            " external_id, title, body, evidence, kind, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, work_order_id, external_id, args.title, args.body,
             args.evidence, args.kind, now(), now()),
        )
        record(con, "issue_candidate_opened", project_id=project_id,
               work_order_id=work_order_id,
               payload={"candidate": external_id, "kind": args.kind})
    print(f"pmo: issue candidate {external_id} opened ({args.kind})")
    return 0


def cmd_issue_update(args) -> int:
    if args.status is not None and args.status not in ("candidate", "dismissed"):
        return fail(f"status not settable here: {args.status}"
                    " (filed is set by 'issue file')", 2)
    con = connect()
    with mutate(con):
        row = con.execute(
            "SELECT * FROM issue_candidates WHERE external_id = ?",
            (args.issue,),
        ).fetchone()
        if row is None:
            raise Rule(f"no issue candidate '{args.issue}'")
        updates: list[str] = []
        params: list = []
        for name, value in (("title", args.title), ("body", args.body),
                            ("evidence", args.evidence),
                            ("status", args.status)):
            if value is not None:
                updates.append(name)
                params.append(value)
        if not updates:
            return fail("nothing to update: pass a field or --status", 2)
        assignments = ", ".join(f"{name} = ?" for name in updates)
        params.append(now())
        params.append(row["id"])
        con.execute(
            f"UPDATE issue_candidates SET {assignments}, updated_at = ?"
            " WHERE id = ?", params)
        record(con, "issue_candidate_updated", project_id=row["project_id"],
               work_order_id=row["work_order_id"],
               payload={"candidate": args.issue, "fields": updates})
    print(f"pmo: issue candidate {args.issue} updated ({', '.join(updates)})")
    return 0


def cmd_issue_list(args) -> int:
    con = connect()
    query = "SELECT * FROM issue_candidates"
    params: list = []
    if args.status:
        query += " WHERE status = ?"
        params.append(args.status)
    query += " ORDER BY external_id"
    rows = con.execute(query, params).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"pmo: {row['external_id']} [{row['kind']}]"
                  f" {row['status']:9} {row['title']}")
        if not rows:
            print("pmo: no issue candidates recorded")
    return 0


def cmd_issue_file(args) -> int:
    con = connect()
    with mutate(con):
        row = con.execute(
            "SELECT * FROM issue_candidates WHERE external_id = ?",
            (args.issue,),
        ).fetchone()
        if row is None:
            raise Rule(f"no issue candidate '{args.issue}'")
        if row["status"] == "filed":
            return fail(f"issue candidate {args.issue} is already filed:"
                        f" {row['issue_url']}", 2)
        con.execute(
            "UPDATE issue_candidates SET status = 'filed', issue_url = ?,"
            " updated_at = ? WHERE id = ?",
            (args.url, now(), row["id"]),
        )
        record(con, "issue_candidate_filed", project_id=row["project_id"],
               work_order_id=row["work_order_id"],
               payload={"candidate": args.issue, "issue_url": args.url})
    print(f"pmo: issue candidate {args.issue} filed -> {args.url}")
    return 0


def cmd_coverage_import(args) -> int:
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        data = load_import_file(args.json_file)
        rows = data.get("rows", [])
        if not rows:
            raise Rule("coverage file has no rows")
        con.execute("DELETE FROM coverage WHERE work_order_id = ?", (order["id"],))
        for row in rows:
            verdict = str(row.get("result", "")).lower().replace("-", "_")
            if verdict not in COVERAGE_VERDICTS:
                raise Rule(f"coverage row has unknown result: {row}")
            con.execute(
                "INSERT INTO coverage (work_order_id, requirement_id, test_names,"
                " verdict, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (order["id"], row.get("id", ""), "; ".join(row.get("tests", [])),
                 verdict, now()),
            )
        record(con, "coverage_imported", project_id=order["project_id"],
               work_order_id=order["id"], payload={"rows": len(rows)})
    print(f"pmo: coverage imported ({len(rows)} requirement rows)")
    return 0


def cmd_coverage_list(args) -> int:
    """Read surface for the criterion coverage map: story_criteria joined
    to the covering stories. The database is the source of truth; no
    rendered view sits between a reader and these rows."""
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    query = ("SELECT c.criterion_id, w.external_id AS story, c.disposition,"
             " c.reason FROM story_criteria c"
             " LEFT JOIN work_items w ON w.id = c.story_id"
             " WHERE c.project_id = ?")
    params: list = [project["id"]]
    if args.story:
        query += " AND w.external_id = ?"
        params.append(args.story)
    query += " ORDER BY c.criterion_id"
    rows = [dict(r) for r in con.execute(query, params)]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"pmo: {row['criterion_id']:10} {row['story'] or '-':8}"
                  f" {row['disposition']:8} {row['reason'] or '-'}")
        if not rows:
            print("pmo: no coverage rows recorded")
    return 0


def cmd_ledger_list(args) -> int:
    """Read surface for the quality ledger: one row per checkpoint, the
    finding counts decoded from their stored JSON."""
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    rows = [dict(r) for r in con.execute(
        "SELECT l.checkpoint_at, w.external_id AS story, o.work_order_key,"
        " l.review_rounds, l.qa_rounds, l.escaped_defect,"
        " l.finding_counts_json FROM ledger l"
        " LEFT JOIN work_items w ON w.id = l.story_id"
        " LEFT JOIN work_orders o ON o.id = l.work_order_id"
        " WHERE l.project_id = ? ORDER BY l.checkpoint_at, l.id",
        (project["id"],))]
    if args.tail:
        rows = rows[-args.tail:]
    for row in rows:
        row["finding_counts"] = json.loads(row.pop("finding_counts_json"))
        row["escaped_defect"] = bool(row["escaped_defect"])
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            counts = ", ".join(f"{k}: {v}" for k, v in
                               sorted(row["finding_counts"].items())) or "none"
            escaped = "YES" if row["escaped_defect"] else "no"
            print(f"pmo: {row['checkpoint_at']} {row['story'] or '-':8}"
                  f" review {row['review_rounds']} qa {row['qa_rounds']}"
                  f" findings {counts} escaped {escaped}")
        if not rows:
            print("pmo: no ledger lines recorded")
    return 0


def cmd_budget_set(args) -> int:
    if args.verdict not in BUDGET_VERDICTS:
        return fail(f"verdict not in enum: {args.verdict}", 2)
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_cwd_inside(order)
        con.execute(
            "INSERT INTO budgets (work_order_id, budget_id, description, verdict,"
            " reason, recorded_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (work_order_id, budget_id) DO UPDATE SET"
            " description = excluded.description, verdict = excluded.verdict,"
            " reason = excluded.reason, recorded_at = excluded.recorded_at",
            (order["id"], args.budget_id, args.description, args.verdict,
             args.reason, now()),
        )
        record(con, "budget_recorded", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"budget": args.budget_id, "verdict": args.verdict})
    print(f"pmo: budget {args.budget_id} -> {args.verdict}")
    return 0


def cmd_ledger_checkpoint(args) -> int:
    con = connect()
    with mutate(con):
        order = get_work_order(con, args.work_order_key)
        require_main_line(order)
        counts: dict[str, int] = {}
        for row in con.execute(
            "SELECT source, severity, COUNT(*) AS n FROM findings"
            " WHERE work_order_id = ?"
            " GROUP BY source, severity ORDER BY source, severity",
            (order["id"],),
        ):
            counts[f"{row['source']}_{row['severity']}"] = row["n"]
        con.execute(
            "INSERT INTO ledger (project_id, story_id, work_order_id, checkpoint_at,"
            " finding_counts_json, review_rounds, qa_rounds, escaped_defect)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order["project_id"], order["story_id"], order["id"], now(),
             json.dumps(counts, sort_keys=True), order["review_rounds"],
             order["qa_rounds"], 1 if args.escaped_defect else 0),
        )
        record(con, "ledger_checkpoint", project_id=order["project_id"],
               work_order_id=order["id"],
               payload={"escaped_defect": bool(args.escaped_defect)})
    print("pmo: ledger checkpoint appended")
    return 0


def cmd_event_append(args) -> int:
    try:
        payload = json.loads(args.payload) if args.payload else {}
    except json.JSONDecodeError as exc:
        return fail(f"payload is not valid JSON: {exc}", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        work_order_id = None
        if args.work_order_key:
            work_order_id = get_work_order(con, args.work_order_key)["id"]
        record(con, args.action, project_id=project["id"],
               work_order_id=work_order_id, actor=args.actor, payload=payload)
    print(f"pmo: event '{args.action}' appended")
    return 0


def cmd_dump(args) -> int:
    con = connect()
    try:
        schema_version = con.execute("PRAGMA user_version").fetchone()[0]
        output = (
            f"PRAGMA user_version = {schema_version};\n"
            + "\n".join(con.iterdump())
            + "\n"
        )
    finally:
        con.close()
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent,
        prefix=f".{destination.name}.", delete=False,
    ) as handle:
        handle.write(output)
        candidate = Path(handle.name)
    try:
        os.replace(candidate, destination)
    except OSError:
        if candidate.exists():
            candidate.unlink()
        raise
    print(f"pmo: database dumped to {args.out}")
    return 0


def cmd_load(args) -> int:
    destination = db_path()
    if destination.is_file() and not args.force:
        return fail("database already exists; pass --force to overwrite", 1)
    try:
        script = Path(args.infile).read_text(encoding="utf-8")
    except OSError as exc:
        return fail(f"cannot read database dump: {exc}", 2)
    data_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=data_dir(), prefix=".agent-marketplace-load.", suffix=".db", delete=False,
    ) as handle:
        candidate = Path(handle.name)
    con = None
    try:
        con = sqlite3.connect(candidate)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=OFF")
        con.executescript(script)
        con.execute("PRAGMA foreign_keys=ON")
        foreign_key_problem = con.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_problem is not None:
            raise Rule(
                "database dump failed foreign_key_check: "
                + ", ".join(str(value) for value in foreign_key_problem)
            )
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise Rule(f"database dump failed integrity_check: {integrity}")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise Rule(
                f"database dump schema {version} does not match CLI schema"
                f" {SCHEMA_VERSION}"
            )
        problem = verify_integrity(con)
        if problem:
            raise Rule(f"database dump integrity stamp is invalid: {problem}")
        con.commit()
        con.close()
        con = None
        if destination.is_file():
            old = sqlite3.connect(destination)
            try:
                old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                old.close()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(destination) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        os.replace(candidate, destination)
    except (OSError, sqlite3.DatabaseError, Rule) as exc:
        if con is not None:
            con.close()
        if candidate.exists():
            candidate.unlink()
        return fail(f"database dump rejected; existing database preserved: {exc}", 1)
    print(f"pmo: database loaded from {args.infile}")
    return 0


def cmd_verify(args) -> int:
    """The integrity tripwire: compare database content against the
    fingerprint the last sanctioned mutation recorded. Foreign writes
    (anything that is not this CLI) are detected here, at gate time and
    at resume, which is the detect-after guarantee when hooks were not
    active to deny before write."""
    con = connect()
    problem = verify_integrity(con)
    if args.json:
        print(json.dumps({"ok": problem is None,
                          "problem": problem or ""}, sort_keys=True))
    if problem:
        if not args.json:
            print(f"pmo: INVALID: {problem}", file=sys.stderr)
        return 1
    if not args.json:
        print("pmo: database integrity verified")
    return 0


def cmd_session_reconcile(args) -> int:
    """Infer the dangling-session audit event when no session-end hook
    recorded one: at the next session start, an active work order in
    this worktree whose newest event is not already the dangling marker
    gets one, honestly labeled as inferred. Idempotent per session
    boundary (the dedup rule): reconcile directly after a recorded
    session end appends nothing."""
    con = connect()
    project = get_project(con, args.project_key)
    orders = active_work_orders(con, project["id"])
    if args.worktree:
        wanted = norm_path(args.worktree)
        orders = [o for o in orders if o["worktree_path"] == wanted]
    appended = 0
    with mutate(con):
        for order in orders:
            newest = con.execute(
                "SELECT action FROM events WHERE work_order_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (order["id"],),
            ).fetchone()
            if newest and newest["action"] == "session_ended_with_active_work_order":
                continue
            record(con, "session_ended_with_active_work_order",
                   project_id=project["id"], work_order_id=order["id"],
                   actor="hook",
                   payload={"reason": "inferred_at_next_start",
                            "current_step": order["current_step"]})
            appended += 1
    print(f"pmo: session reconcile appended {appended} event(s)")
    return 0


def cmd_ensure(args) -> int:
    """One idempotent bootstrap: initialize the database, sync the
    launcher and report integrity. Entry pre-flights call this, so hooks
    are accelerators and never the only path to a working backbone.
    Bootstrap must not brick a session: an integrity problem is reported
    loudly but exits 0; gates fail on it via work-order validate."""
    sync_args = argparse.Namespace(force=False)
    cmd_sync_launcher(sync_args)
    version = upgrade_core.database_version(db_path())
    if version not in (0, SCHEMA_VERSION):
        return fail(
            f"AGENT_MARKETPLACE_UPGRADE_REQUIRED: database schema {version} must be"
            f" upgraded to {SCHEMA_VERSION}; run the Agent Marketplace Upgrade entry"
        )
    code = cmd_init_db(args)
    if code != 0:
        return code
    con = connect()
    problem = verify_integrity(con)
    if problem:
        print(f"pmo: WARNING: {problem}", file=sys.stderr)
    print("pmo: ensure complete")
    return 0


def dashboard_assets() -> tuple[Path, Path]:
    """(module path, index.html path) next to this script. Works from both the
    plugin layout (scripts/ + dashboard/) and the synced layout (bin/ +
    dashboard/), because both put index.html at ../dashboard/index.html."""
    here = Path(__file__).resolve().parent
    return here / "pmo_dashboard.py", here.parent / "dashboard" / "index.html"


def cmd_sync_launcher(args) -> int:
    bin_dir = data_dir() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "pmo_cli.py"
    version_file = bin_dir / "VERSION"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    if current == PMO_VERSION and target.is_file() and not args.force:
        print(f"pmo: launcher already at version {PMO_VERSION}")
        return 0
    source = Path(__file__).resolve()
    if source != target:
        shutil.copyfile(source, target)
    module_src, index_src = dashboard_assets()
    if module_src.is_file() and module_src != bin_dir / "pmo_dashboard.py":
        shutil.copyfile(module_src, bin_dir / "pmo_dashboard.py")
    # The dispatcher travels with the launcher: every "$RUN" invocation
    # in shipped content resolves through it.
    for extra in (
        "marketplace_run.py", "marketplace_paths.py", "file_issue.py", "upgrade_core.py",
        "upgrade_guidance.json",
    ):
        extra_src = source.parent / extra
        extra_dst = bin_dir / extra
        if extra_src.is_file() and extra_src != extra_dst:
            shutil.copyfile(extra_src, extra_dst)
    if index_src.is_file():
        index_dst = data_dir() / "dashboard" / "index.html"
        if index_src != index_dst:
            index_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(index_src, index_dst)
    version_file.write_text(PMO_VERSION + "\n", encoding="utf-8")
    print(f"pmo: launcher synced to {target} (version {PMO_VERSION})")
    return 0


def cmd_dashboard(args) -> int:
    module_path, index_path = dashboard_assets()
    if not module_path.is_file() or not index_path.is_file():
        return fail(
            "dashboard files missing next to this CLI; reinstall the"
            " project-management-office plugin or re-run sync-launcher from"
            " the plugin copy"
        )
    sys.path.insert(0, str(module_path.parent))
    import pmo_dashboard
    return pmo_dashboard.serve(args.host, args.port,
                               open_browser=not args.no_browser)


def cmd_version(args) -> int:
    print(PMO_VERSION)
    return 0


def cmd_now(args) -> int:
    """The one exposed clock: every timestamp in a durable artifact is
    pasted from this output, never typed from memory. Bare stdout so the
    value can be used verbatim; no database access so it works before
    init-db."""
    full = now()
    if args.date:
        print(full[:10])
    elif args.compact:
        print(full[:10].replace("-", ""))
    else:
        print(full)
    return 0


def upgrade_project_root(args) -> str:
    return str(Path(args.project_root).resolve()) if args.project_root else ""


def cmd_upgrade_status(args) -> int:
    try:
        result = upgrade_core.status(
            data_dir(), db_path(), SCHEMA_VERSION,
            upgrade_project_root(args) or None,
            exclude_session_id=args.exclude_session_id,
        )
    except upgrade_core.UpgradeError as exc:
        raise Rule(str(exc)) from exc
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"pmo: {result['status']}")
        for value in result["reasons"]:
            print(f"pmo: reason: {value}")
        for value in result["blockers"]:
            print(f"pmo: blocker: {value}")
    return 0 if result["status"] in {
        upgrade_core.STATUS_CURRENT, upgrade_core.STATUS_READY,
        upgrade_core.STATUS_RESTART, upgrade_core.STATUS_PROJECT_PR,
    } else 1


def cmd_upgrade_plan(args) -> int:
    try:
        result = upgrade_core.plan(
            data_dir(), db_path(), SCHEMA_VERSION,
            upgrade_project_root(args) or None,
        )
    except upgrade_core.UpgradeError as exc:
        raise Rule(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_upgrade_apply(args) -> int:
    try:
        result = upgrade_core.apply(
            data_dir(), db_path(), SCHEMA_VERSION, args.plan_id,
        )
    except upgrade_core.UpgradeError as exc:
        raise Rule(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_upgrade_recover(args) -> int:
    try:
        result = upgrade_core.recover(
            data_dir(), db_path(), SCHEMA_VERSION, args.run_id,
        )
    except upgrade_core.UpgradeError as exc:
        raise Rule(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_upgrade_session_release(args) -> int:
    try:
        result = upgrade_core.release_session(
            data_dir(), args.session_id, args.confirm_closed,
        )
    except upgrade_core.UpgradeError as exc:
        raise Rule(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Management Office CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("version")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("now")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--date", action="store_true")
    group.add_argument("--compact", action="store_true")
    p.set_defaults(func=cmd_now)

    p = sub.add_parser("sync-launcher")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_sync_launcher)

    p = sub.add_parser("verify")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("session-reconcile")
    p.add_argument("--project-key", required=True)
    p.add_argument("--worktree", default="")
    p.set_defaults(func=cmd_session_reconcile)

    p = sub.add_parser("ensure")
    p.set_defaults(func=cmd_ensure)

    p = sub.add_parser("dashboard")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_dashboard)

    upgrade = sub.add_parser("upgrade").add_subparsers(
        dest="subcommand", required=True
    )
    p = upgrade.add_parser("status")
    p.add_argument("--project-root", default="")
    p.add_argument("--exclude-session-id", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_upgrade_status)
    p = upgrade.add_parser("plan")
    p.add_argument("--project-root", default="")
    p.set_defaults(func=cmd_upgrade_plan)
    p = upgrade.add_parser("apply")
    p.add_argument("--plan-id", required=True)
    p.set_defaults(func=cmd_upgrade_apply)
    p = upgrade.add_parser("recover")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_upgrade_recover)
    p = upgrade.add_parser("session-release")
    p.add_argument("--session-id", required=True)
    p.add_argument("--confirm-closed", action="store_true")
    p.set_defaults(func=cmd_upgrade_session_release)

    project = sub.add_parser("project").add_subparsers(dest="subcommand", required=True)
    p = project.add_parser("register")
    p.add_argument("--key", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--team", default="")
    p.add_argument("--stamp-config", default="")
    p.add_argument("--project-root", default="")
    p.add_argument("--workspace", default="workspace")
    p.set_defaults(func=cmd_project_register)
    p = project.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_project_list)

    p = sub.add_parser("resume-info")
    p.add_argument("--project-key", required=True)
    p.add_argument("--events", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resume_info)

    wo = sub.add_parser("work-order").add_subparsers(dest="subcommand", required=True)
    p = wo.add_parser("init")
    p.add_argument("--project-key", required=True)
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--story", default="")
    p.add_argument("--bindings", default="")
    p.add_argument("--order-dir", default="")
    p.add_argument("--constitution", default="")
    p.add_argument("--brief", default="")
    p.add_argument("--config", default="")
    p.set_defaults(func=cmd_wo_init)
    p = wo.add_parser("set-step")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--artifact", default="")
    p.add_argument("--bump-attempts", action="store_true")
    p.set_defaults(func=cmd_wo_set_step)
    p = wo.add_parser("record-gate")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--decided-by", default="owner")
    p.set_defaults(func=cmd_wo_record_gate)
    p = wo.add_parser("bump")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--counter", required=True)
    p.set_defaults(func=cmd_wo_bump)
    p = wo.add_parser("set-ownership")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--ownership", required=True,
                   help="JSON object: snake_case role -> path prefix list")
    p.set_defaults(func=cmd_wo_set_ownership)
    p = wo.add_parser("set-status")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--status", required=True)
    p.set_defaults(func=cmd_wo_set_status)
    p = wo.add_parser("release")
    p.add_argument("--work-order-key", required=True)
    p.set_defaults(func=cmd_wo_release)
    p = wo.add_parser("validate")
    p.add_argument("--work-order-key", required=True)
    p.set_defaults(func=cmd_wo_validate)

    item = sub.add_parser("item").add_subparsers(dest="subcommand", required=True)
    p = item.add_parser("import")
    p.add_argument("--project-key", required=True)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_item_import)
    p = item.add_parser("update")
    p.add_argument("--project-key", required=True)
    p.add_argument("--external-id", required=True)
    for flag in ITEM_UPDATE_FIELDS:
        p.add_argument(f"--{flag}", default=None)
    p.add_argument("--deployed-verified", default=None, choices=["true", "false"])
    p.set_defaults(func=cmd_item_update)
    p = item.add_parser("list")
    p.add_argument("--project-key", required=True)
    p.add_argument("--kind", default="", choices=["", "epic", "story", "task"])
    p.add_argument("--status", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_item_list)
    p = item.add_parser("add-dep")
    p.add_argument("--project-key", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--depends-on", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_item_add_dep)
    p = item.add_parser("remove-dep")
    p.add_argument("--project-key", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--depends-on", required=True)
    p.set_defaults(func=cmd_item_remove_dep)
    p = item.add_parser("list-deps")
    p.add_argument("--project-key", required=True)
    p.add_argument("--item", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_item_list_deps)
    p = item.add_parser("add-dod")
    p.add_argument("--project-key", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--statement", required=True)
    p.set_defaults(func=cmd_item_add_dod)
    p = item.add_parser("set-dod")
    p.add_argument("--project-key", required=True)
    p.add_argument("--dod-id", type=int, required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--failure-reason", default="")
    p.set_defaults(func=cmd_item_set_dod)
    p = item.add_parser("list-dod")
    p.add_argument("--project-key", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_item_list_dod)
    p = item.add_parser("order")
    p.add_argument("--project-key", required=True)
    p.add_argument("--kind", default="story", choices=["story", "task"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_item_order)
    p = item.add_parser("ready")
    p.add_argument("--project-key", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_item_ready)

    task = sub.add_parser("task").add_subparsers(dest="subcommand", required=True)
    p = task.add_parser("open")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--title", required=True)
    p.set_defaults(func=cmd_task_open)
    p = task.add_parser("close")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--step", default="")
    p.add_argument("--outcome", required=True)
    p.set_defaults(func=cmd_task_close)
    p = task.add_parser("touch")
    p.add_argument("--project-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--worktree", default="")
    p.add_argument("--session-id", default="")
    p.add_argument("--agent", default="")
    p.add_argument("--cost-usd", type=float, default=None)
    p.set_defaults(func=cmd_task_touch)

    finding = sub.add_parser("finding").add_subparsers(dest="subcommand", required=True)
    p = finding.add_parser("open")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--severity", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--repro", default="")
    p.add_argument("--expected-actual", default="")
    p.add_argument("--traced", default="")
    p.add_argument("--round", type=int, default=0)
    p.set_defaults(func=cmd_finding_open)
    p = finding.add_parser("update")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--finding", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--round", type=int, default=None)
    p.set_defaults(func=cmd_finding_update)
    p = finding.add_parser("list")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--status", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_finding_list)

    issue = sub.add_parser("issue").add_subparsers(dest="subcommand", required=True)
    p = issue.add_parser("open")
    p.add_argument("--title", required=True)
    p.add_argument("--kind", required=True, choices=sorted(ISSUE_KINDS))
    p.add_argument("--body", default="")
    p.add_argument("--evidence", default="")
    p.add_argument("--project-key", default="")
    p.add_argument("--work-order-key", default="")
    p.set_defaults(func=cmd_issue_open)
    p = issue.add_parser("update")
    p.add_argument("--issue", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--body", default=None)
    p.add_argument("--evidence", default=None)
    p.add_argument("--status", default=None, choices=["candidate", "dismissed"])
    p.set_defaults(func=cmd_issue_update)
    p = issue.add_parser("list")
    p.add_argument("--status", default="", choices=["", *sorted(ISSUE_STATUSES)])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_issue_list)
    p = issue.add_parser("file")
    p.add_argument("--issue", required=True)
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_issue_file)

    coverage = sub.add_parser("coverage").add_subparsers(dest="subcommand", required=True)
    p = coverage.add_parser("import")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_coverage_import)
    p = coverage.add_parser("list")
    p.add_argument("--project-key", required=True)
    p.add_argument("--story", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_coverage_list)

    budget = sub.add_parser("budget").add_subparsers(dest="subcommand", required=True)
    p = budget.add_parser("set")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--budget-id", required=True)
    p.add_argument("--verdict", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_budget_set)

    event = sub.add_parser("event").add_subparsers(dest="subcommand", required=True)
    p = event.add_parser("append")
    p.add_argument("--project-key", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--actor", default="orchestrator")
    p.add_argument("--work-order-key", default="")
    p.add_argument("--payload", default="")
    p.set_defaults(func=cmd_event_append)

    ledger = sub.add_parser("ledger").add_subparsers(dest="subcommand", required=True)
    p = ledger.add_parser("checkpoint")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--escaped-defect", action="store_true")
    p.set_defaults(func=cmd_ledger_checkpoint)
    p = ledger.add_parser("list")
    p.add_argument("--project-key", required=True)
    p.add_argument("--tail", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ledger_list)

    # The merge-checkpoint verb: one deterministic call, the ledger line.
    # The database is the source of truth; no rendered views to refresh.
    p = sub.add_parser("checkpoint")
    p.add_argument("--work-order-key", required=True)
    p.add_argument("--escaped-defect", action="store_true")
    p.set_defaults(func=cmd_ledger_checkpoint)

    p = sub.add_parser("dump")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_dump)
    p = sub.add_parser("load")
    p.add_argument("--infile", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_load)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Rule as exc:
        return fail(str(exc))
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return fail("database not initialized; run: pmo_cli.py init-db", 1)
        return fail(f"database error: {exc}", 1)


if __name__ == "__main__":
    raise SystemExit(main())
