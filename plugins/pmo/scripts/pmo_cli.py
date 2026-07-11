#!/usr/bin/env python3
"""Project Management Office CLI: the single writer of the central database.

Every team plugin records its process state here: projects, epics, stories,
machine-generated tasks, runs with step state and gates, findings, coverage,
budgets and the quality ledger. Nothing else ever writes the database; agents
and hooks go through this CLI, and every mutation appends an audit event.

The database lives in the user-level data directory: AGENTROF_HOME when set,
otherwise .agentrof under the user's home. Stdlib only.

Exit codes: 0 success, 1 rule violation, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PMO_VERSION = "0.1.0"
SCHEMA_VERSION = 1
DB_NAME = "agentrof.db"

RUN_STATUSES = {"running", "waiting_gate", "blocked", "escalated", "complete"}
ACTIVE_RUN_STATUSES = ("running", "waiting_gate")
STEP_STATUSES = {"pending", "in_progress", "done", "blocked", "escalated"}
STEP_IDS = ["0", "1", "2", "3", "4", "5"]
EPIC_STATUSES = {"open", "done"}
STORY_STATUSES = {"planned", "ready", "in_development", "done", "deferred"}
TASK_STATUSES = {"open", "in_progress", "done", "blocked"}
STATUSES_BY_KIND = {"epic": EPIC_STATUSES, "story": STORY_STATUSES, "task": TASK_STATUSES}
FINDING_SOURCES = {"review", "qa", "design_qa"}
FINDING_STATUSES = {"open", "fixed", "waived"}
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
BUDGET_VERDICTS = {"verified", "unverified"}
COVERAGE_VERDICTS = {"pass", "fail", "no_test"}
STORY_REQUIRED_FIELDS = ["title", "scope", "excludes", "dor", "dod", "epic"]

DDL = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  project_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
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
  dependency TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  excludes TEXT NOT NULL DEFAULT '',
  dor TEXT NOT NULL DEFAULT '',
  dod TEXT NOT NULL DEFAULT '',
  deployed_verified INTEGER NOT NULL DEFAULT 0,
  run_id INTEGER REFERENCES runs(id),
  role TEXT NOT NULL DEFAULT '',
  step_id TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, external_id)
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
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  story_id INTEGER REFERENCES work_items(id),
  run_key TEXT NOT NULL UNIQUE,
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
CREATE TABLE IF NOT EXISTS run_steps (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK
    (status IN ('pending', 'in_progress', 'done', 'blocked', 'escalated')),
  artifact_path TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE (run_id, step_id)
);
CREATE TABLE IF NOT EXISTS gates (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  name TEXT NOT NULL,
  decision TEXT NOT NULL,
  decided_by TEXT NOT NULL DEFAULT 'owner',
  decided_at TEXT NOT NULL,
  UNIQUE (run_id, name)
);
CREATE TABLE IF NOT EXISTS ownership (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  role TEXT NOT NULL,
  path_prefix TEXT NOT NULL,
  UNIQUE (run_id, role, path_prefix)
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  run_id INTEGER NOT NULL REFERENCES runs(id),
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
  run_id INTEGER NOT NULL REFERENCES runs(id),
  requirement_id TEXT NOT NULL,
  test_names TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'no_test')),
  recorded_at TEXT NOT NULL,
  UNIQUE (run_id, requirement_id)
);
CREATE TABLE IF NOT EXISTS budgets (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  budget_id TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (verdict IN ('verified', 'unverified')),
  reason TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  UNIQUE (run_id, budget_id)
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  story_id INTEGER REFERENCES work_items(id),
  run_id INTEGER REFERENCES runs(id),
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
  run_id INTEGER,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg: str, code: int = 1) -> int:
    print(f"pmo: {msg}", file=sys.stderr)
    return code


def data_dir() -> Path:
    override = os.environ.get("AGENTROF_HOME", "").strip()
    return Path(override) if override else Path.home() / ".agentrof"


def db_path() -> Path:
    return data_dir() / DB_NAME


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


class Rule(Exception):
    """A rule violation: reported on stderr, exit 1."""


def mutate(con: sqlite3.Connection):
    """Context manager: BEGIN IMMEDIATE, commit on success, rollback on error."""
    class _Tx:
        def __enter__(self):
            con.execute("BEGIN IMMEDIATE")
            return con

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                con.execute("COMMIT")
            else:
                con.execute("ROLLBACK")
            return False
    return _Tx()


def record(con, action: str, project_id=None, run_id=None, actor="orchestrator", payload=None) -> None:
    con.execute(
        "INSERT INTO events (ts, project_id, run_id, actor, action, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (now(), project_id, run_id, actor, action, json.dumps(payload or {}, sort_keys=True)),
    )


def get_project(con, key: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM projects WHERE project_key = ?", (key,)).fetchone()
    if row is None:
        raise Rule(f"no project registered with key '{key}'")
    return row


def get_run(con, run_key: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM runs WHERE run_key = ?", (run_key,)).fetchone()
    if row is None:
        raise Rule(f"no run with key '{run_key}'")
    return row


def get_item(con, project_id: int, external_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM work_items WHERE project_id = ? AND external_id = ?",
        (project_id, external_id),
    ).fetchone()


def active_runs(con, project_id: int) -> list[sqlite3.Row]:
    marks = ", ".join("?" for _ in ACTIVE_RUN_STATUSES)
    return con.execute(
        f"SELECT * FROM runs WHERE project_id = ? AND status IN ({marks})",
        (project_id, *ACTIVE_RUN_STATUSES),
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init_db(args) -> int:
    con = connect()
    con.executescript(DDL)  # commits itself; keep it outside the transaction
    with mutate(con):
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version > SCHEMA_VERSION:
            raise Rule(
                f"database schema version {version} is newer than this CLI"
                f" ({SCHEMA_VERSION}); update the pmo plugin"
            )
        record(con, "init_db", payload={"schema_version": SCHEMA_VERSION})
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


def cmd_run_init(args) -> int:
    con = connect()
    worktree = norm_path(args.worktree)
    with mutate(con):
        project = get_project(con, args.project_key)
        for run in active_runs(con, project["id"]):
            if run["worktree_path"] == worktree:
                raise Rule(
                    f"active run '{run['run_key']}' already holds worktree"
                    f" {worktree}; resume it or release it first"
                )
        story_id = None
        if args.story:
            story = get_item(con, project["id"], args.story)
            if story is None or story["kind"] != "story":
                raise Rule(f"story '{args.story}' is not in the backlog; import it first")
            claimed = con.execute(
                "SELECT run_key FROM runs WHERE story_id = ? AND status IN (?, ?)",
                (story["id"], *ACTIVE_RUN_STATUSES),
            ).fetchone()
            if claimed:
                raise Rule(
                    f"story '{args.story}' is already claimed by active run"
                    f" '{claimed['run_key']}'"
                )
            story_id = story["id"]
            con.execute(
                "UPDATE work_items SET status = 'in_development', updated_at = ?"
                " WHERE id = ?",
                (now(), story_id),
            )
        con.execute(
            "INSERT INTO runs (project_id, story_id, run_key, request, status,"
            " current_step, worktree_path, bindings_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'running', '0', ?, ?, ?, ?)",
            (project["id"], story_id, args.run_key, args.request, worktree,
             args.bindings or "{}", now(), now()),
        )
        run = get_run(con, args.run_key)
        for step_id in STEP_IDS:
            status = "in_progress" if step_id == "0" else "pending"
            con.execute(
                "INSERT INTO run_steps (run_id, step_id, status) VALUES (?, ?, ?)",
                (run["id"], step_id, status),
            )
        record(con, "run_initialized", project_id=project["id"], run_id=run["id"],
               payload={"run_key": args.run_key, "story": args.story or "",
                        "worktree": worktree})
    if args.run_dir:
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        for src, dest in ((args.constitution, "constitution.md"),
                          (args.brief, "brief.snapshot.md"),
                          (args.config, "config.snapshot.json")):
            if src and Path(src).is_file():
                shutil.copyfile(src, run_dir / dest)
    print(f"pmo: run '{args.run_key}' initialized (step 0 in progress)")
    return 0


def cmd_run_set_step(args) -> int:
    if args.step not in STEP_IDS:
        return fail(f"unknown step: {args.step}", 2)
    if args.status not in STEP_STATUSES:
        return fail(f"step status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        if args.status == "in_progress":
            for prior in STEP_IDS[: STEP_IDS.index(args.step)]:
                row = con.execute(
                    "SELECT status FROM run_steps WHERE run_id = ? AND step_id = ?",
                    (run["id"], prior),
                ).fetchone()
                if row["status"] != "done":
                    raise Rule(
                        f"transition guard: step {prior} is not done;"
                        f" step {args.step} cannot start"
                    )
            con.execute(
                "UPDATE runs SET current_step = ?, updated_at = ? WHERE id = ?",
                (args.step, now(), run["id"]),
            )
        con.execute(
            "UPDATE run_steps SET status = ?,"
            " artifact_path = CASE WHEN ? = '' THEN artifact_path ELSE ? END,"
            " attempts = attempts + ? WHERE run_id = ? AND step_id = ?",
            (args.status, args.artifact, args.artifact,
             1 if args.bump_attempts else 0, run["id"], args.step),
        )
        con.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now(), run["id"]))
        record(con, "step_changed", project_id=run["project_id"], run_id=run["id"],
               payload={"step": args.step, "status": args.status,
                        "artifact": args.artifact})
    print(f"pmo: step {args.step} -> {args.status}")
    return 0


def cmd_run_record_gate(args) -> int:
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        con.execute(
            "INSERT INTO gates (run_id, name, decision, decided_by, decided_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (run_id, name) DO UPDATE SET decision = excluded.decision,"
            " decided_by = excluded.decided_by, decided_at = excluded.decided_at",
            (run["id"], args.gate, args.decision, args.decided_by, now()),
        )
        con.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now(), run["id"]))
        record(con, "gate_recorded", project_id=run["project_id"], run_id=run["id"],
               payload={"gate": args.gate, "decision": args.decision})
    print(f"pmo: gate {args.gate} -> {args.decision}")
    return 0


def cmd_run_bump(args) -> int:
    if args.counter not in ("review", "qa"):
        return fail(f"unknown counter: {args.counter}", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        column = "review_rounds" if args.counter == "review" else "qa_rounds"
        con.execute(
            f"UPDATE runs SET {column} = {column} + 1, updated_at = ? WHERE id = ?",
            (now(), run["id"]),
        )
        value = con.execute(
            f"SELECT {column} AS v FROM runs WHERE id = ?", (run["id"],)
        ).fetchone()["v"]
        record(con, "round_bumped", project_id=run["project_id"], run_id=run["id"],
               payload={"counter": args.counter, "value": value})
    print(f"pmo: {args.counter} rounds = {value}")
    return 0


def cmd_run_set_ownership(args) -> int:
    try:
        ownership = json.loads(args.ownership)
    except json.JSONDecodeError as exc:
        return fail(f"ownership is not valid JSON: {exc}", 2)
    if not isinstance(ownership, dict):
        return fail("ownership must be a JSON object of role -> path list", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
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
                        f"ownership overlap inside the run: {role_a}:{path_a}"
                        f" overlaps {role_b}:{path_b}"
                    )
        for other in active_runs(con, run["project_id"]):
            if other["id"] == run["id"]:
                continue
            for row in con.execute(
                "SELECT role, path_prefix FROM ownership WHERE run_id = ?",
                (other["id"],),
            ):
                for role, path in flat:
                    if prefixes_overlap(path, row["path_prefix"]):
                        raise Rule(
                            f"ownership overlap across runs: {role}:{path} overlaps"
                            f" {row['role']}:{row['path_prefix']} held by active run"
                            f" '{other['run_key']}'"
                        )
        con.execute("DELETE FROM ownership WHERE run_id = ?", (run["id"],))
        for role, path in flat:
            con.execute(
                "INSERT INTO ownership (run_id, role, path_prefix) VALUES (?, ?, ?)",
                (run["id"], role, path),
            )
        con.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now(), run["id"]))
        record(con, "ownership_set", project_id=run["project_id"], run_id=run["id"],
               payload={"ownership": ownership})
    print("pmo: ownership map stored")
    return 0


def cmd_run_set_status(args) -> int:
    if args.status not in RUN_STATUSES:
        return fail(f"run status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        if args.status == "complete":
            not_done = [
                row["step_id"]
                for row in con.execute(
                    "SELECT step_id FROM run_steps WHERE run_id = ?"
                    " AND status != 'done' ORDER BY step_id",
                    (run["id"],),
                )
            ]
            if not_done:
                raise Rule(f"run-complete guard: steps not done: {', '.join(not_done)}")
            open_findings = [
                row["external_id"]
                for row in con.execute(
                    "SELECT external_id FROM findings WHERE run_id = ?"
                    " AND status = 'open' ORDER BY external_id",
                    (run["id"],),
                )
            ]
            if open_findings:
                raise Rule(
                    "run-complete guard: open findings remain:"
                    f" {', '.join(open_findings)}"
                )
        con.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (args.status, now(), run["id"]),
        )
        record(con, "run_status_changed", project_id=run["project_id"],
               run_id=run["id"], payload={"status": args.status})
    print(f"pmo: run status -> {args.status}")
    return 0


def cmd_run_release(args) -> int:
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        if run["status"] in ACTIVE_RUN_STATUSES:
            con.execute(
                "UPDATE runs SET status = 'blocked', updated_at = ? WHERE id = ?",
                (now(), run["id"]),
            )
            record(con, "run_released", project_id=run["project_id"],
                   run_id=run["id"], payload={"previous_status": run["status"]})
            print(f"pmo: run '{args.run_key}' released (status blocked; claims freed)")
        else:
            print(f"pmo: run '{args.run_key}' is not active; nothing to release")
    return 0


def cmd_run_validate(args) -> int:
    con = connect()
    run = get_run(con, args.run_key)
    problems: list[str] = []
    if run["status"] not in RUN_STATUSES:
        problems.append(f"run status not in enum: {run['status']}")
    steps = {
        row["step_id"]: row
        for row in con.execute(
            "SELECT * FROM run_steps WHERE run_id = ?", (run["id"],)
        )
    }
    for step_id in STEP_IDS:
        if step_id not in steps:
            problems.append(f"missing step row: {step_id}")
        elif steps[step_id]["status"] not in STEP_STATUSES:
            problems.append(f"step {step_id} status not in enum")
    if run["status"] == "complete":
        for step_id, row in steps.items():
            if row["status"] != "done":
                problems.append(f"run complete but step {step_id} is not done")
    for row in con.execute(
        "SELECT DISTINCT role FROM ownership WHERE run_id = ?", (run["id"],)
    ):
        if not is_snake(row["role"]):
            problems.append(f"ownership role not snake_case: {row['role']}")
    if problems:
        for problem in problems:
            print(f"pmo: INVALID: {problem}", file=sys.stderr)
        return 1
    print("pmo: run state is valid")
    return 0


def cmd_resume_info(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    runs = active_runs(con, project["id"])
    info = {"project_key": args.project_key, "active_runs": [], "recent_events": []}
    for run in runs:
        steps = con.execute(
            "SELECT step_id, status, attempts FROM run_steps WHERE run_id = ?"
            " ORDER BY step_id",
            (run["id"],),
        ).fetchall()
        gates = con.execute(
            "SELECT name, decision FROM gates WHERE run_id = ? ORDER BY name",
            (run["id"],),
        ).fetchall()
        story = None
        if run["story_id"]:
            row = con.execute(
                "SELECT external_id, title FROM work_items WHERE id = ?",
                (run["story_id"],),
            ).fetchone()
            story = f"{row['external_id']} {row['title']}"
        info["active_runs"].append({
            "run_key": run["run_key"],
            "status": run["status"],
            "current_step": run["current_step"],
            "story": story,
            "worktree": run["worktree_path"],
            "steps": {s["step_id"]: s["status"] for s in steps},
            "gates": {g["name"]: g["decision"] for g in gates},
            "review_rounds": run["review_rounds"],
            "qa_rounds": run["qa_rounds"],
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
    if not runs:
        print(f"pmo: no active run for project '{args.project_key}'")
        return 0
    for run in info["active_runs"]:
        story = f" story {run['story']}" if run["story"] else ""
        print(
            f"pmo: active run '{run['run_key']}' status {run['status']}"
            f" at step {run['current_step']}{story} (worktree {run['worktree']})"
        )
        done = [s for s, status in sorted(run["steps"].items()) if status == "done"]
        print(f"pmo:   steps done: {', '.join(done) or 'none'};"
              f" review rounds {run['review_rounds']}, qa rounds {run['qa_rounds']}")
    return 0


def load_import_file(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rule(f"cannot read import file: {exc}")
    if not isinstance(data, dict):
        raise Rule("import file must be a JSON object")
    return data


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
            parent = get_item(con, project["id"], story["epic"])
            existing = get_item(con, project["id"], story["external_id"])
            values = (
                story["title"], status, story.get("type", ""),
                story.get("priority", ""), story.get("dependency", ""),
                story["scope"], story["excludes"], story["dor"], story["dod"],
                parent["id"] if parent else None,
            )
            if existing is None:
                con.execute(
                    "INSERT INTO work_items (project_id, kind, external_id, title,"
                    " status, item_type, priority, dependency, scope, excludes,"
                    " dor, dod, parent_id, created_at, updated_at)"
                    " VALUES (?, 'story', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project["id"], story["external_id"], *values, now(), now()),
                )
            else:
                con.execute(
                    "UPDATE work_items SET title = ?, status = ?, item_type = ?,"
                    " priority = ?, dependency = ?, scope = ?, excludes = ?,"
                    " dor = ?, dod = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                    (*values, now(), existing["id"]),
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
    "dependency": "dependency",
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


def cmd_task_open(args) -> int:
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        external_id = next_external_id(con, run["project_id"], "T")
        con.execute(
            "INSERT INTO work_items (project_id, kind, external_id, parent_id,"
            " title, status, run_id, role, step_id, created_at, updated_at)"
            " VALUES (?, 'task', ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
            (run["project_id"], external_id, run["story_id"], args.title,
             run["id"], args.role, args.step, now(), now()),
        )
        record(con, "task_opened", project_id=run["project_id"], run_id=run["id"],
               payload={"task": external_id, "role": args.role, "step": args.step})
    print(f"pmo: task {external_id} opened ({args.role}, step {args.step})")
    return 0


def find_task(con, run, role: str, step: str | None) -> sqlite3.Row | None:
    query = ("SELECT * FROM work_items WHERE run_id = ? AND kind = 'task'"
             " AND role = ?")
    params: list = [run["id"], role]
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
        run = get_run(con, args.run_key)
        task = find_task(con, run, args.role, args.step)
        if task is None:
            raise Rule(f"no task for role '{args.role}' in run '{args.run_key}'")
        finished = task["finished_at"] or now()
        con.execute(
            "UPDATE work_items SET status = ?, finished_at = ?, updated_at = ?"
            " WHERE id = ?",
            (args.outcome, finished, now(), task["id"]),
        )
        record(con, "task_closed", project_id=run["project_id"], run_id=run["id"],
               payload={"task": task["external_id"], "outcome": args.outcome})
    print(f"pmo: task {task['external_id']} -> {args.outcome}")
    return 0


def cmd_task_touch(args) -> int:
    if args.phase not in ("start", "stop"):
        return fail(f"phase must be start or stop, got: {args.phase}", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        runs = active_runs(con, project["id"])
        if args.worktree:
            wanted = norm_path(args.worktree)
            runs = [r for r in runs if r["worktree_path"] == wanted]
        if not runs:
            record(con, "task_touch_unmatched", project_id=project["id"],
                   actor="hook", payload={"role": args.role, "phase": args.phase})
            print("pmo: no active run matched; touch recorded as an event only")
            return 0
        run = runs[0]
        task = find_task(con, run, args.role, None)
        stamp = now()
        if task is None:
            if args.phase == "stop":
                record(con, "task_touch_unmatched", project_id=project["id"],
                       run_id=run["id"], actor="hook",
                       payload={"role": args.role, "phase": "stop"})
                print("pmo: no task row for role; touch recorded as an event only")
                return 0
            external_id = next_external_id(con, run["project_id"], "T")
            con.execute(
                "INSERT INTO work_items (project_id, kind, external_id, parent_id,"
                " title, status, run_id, role, step_id, started_at, created_at,"
                " updated_at) VALUES (?, 'task', ?, ?, ?, 'in_progress', ?, ?, ?,"
                " ?, ?, ?)",
                (run["project_id"], external_id, run["story_id"],
                 f"auto-recorded {args.role} work", run["id"], args.role,
                 run["current_step"], stamp, stamp, stamp),
            )
            record(con, "task_started", project_id=run["project_id"],
                   run_id=run["id"], actor="hook",
                   payload={"task": external_id, "role": args.role})
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
            record(con, "task_started", project_id=run["project_id"],
                   run_id=run["id"], actor="hook",
                   payload={"task": task["external_id"], "role": args.role})
        else:
            con.execute(
                "UPDATE work_items SET finished_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, task["id"]),
            )
            record(con, "task_finished", project_id=run["project_id"],
                   run_id=run["id"], actor="hook",
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
        run = get_run(con, args.run_key)
        external_id = next_finding_id(con, run["project_id"])
        con.execute(
            "INSERT INTO findings (project_id, run_id, story_id, external_id,"
            " source, severity, summary, repro, expected_actual,"
            " traced_requirement, opened_round, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["project_id"], run["id"], run["story_id"], external_id,
             args.source, args.severity, args.summary, args.repro,
             args.expected_actual, args.traced, args.round, now(), now()),
        )
        record(con, "finding_opened", project_id=run["project_id"],
               run_id=run["id"],
               payload={"finding": external_id, "source": args.source,
                        "severity": args.severity})
    print(f"pmo: finding {external_id} opened ({args.source}, {args.severity})")
    return 0


def cmd_finding_update(args) -> int:
    if args.status not in FINDING_STATUSES:
        return fail(f"status not in enum: {args.status}", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        finding = con.execute(
            "SELECT * FROM findings WHERE project_id = ? AND external_id = ?",
            (run["project_id"], args.finding),
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
        record(con, "finding_updated", project_id=run["project_id"],
               run_id=run["id"],
               payload={"finding": args.finding, "status": args.status})
    print(f"pmo: finding {args.finding} -> {args.status}")
    return 0


def cmd_finding_list(args) -> int:
    con = connect()
    try:
        run = get_run(con, args.run_key)
    except Rule as exc:
        return fail(str(exc))
    query = "SELECT * FROM findings WHERE run_id = ?"
    params: list = [run["id"]]
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


def cmd_coverage_import(args) -> int:
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        data = load_import_file(args.json_file)
        rows = data.get("rows", [])
        if not rows:
            raise Rule("coverage file has no rows")
        con.execute("DELETE FROM coverage WHERE run_id = ?", (run["id"],))
        for row in rows:
            verdict = str(row.get("result", "")).lower().replace("-", "_")
            if verdict not in COVERAGE_VERDICTS:
                raise Rule(f"coverage row has unknown result: {row}")
            con.execute(
                "INSERT INTO coverage (run_id, requirement_id, test_names,"
                " verdict, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (run["id"], row.get("id", ""), "; ".join(row.get("tests", [])),
                 verdict, now()),
            )
        record(con, "coverage_imported", project_id=run["project_id"],
               run_id=run["id"], payload={"rows": len(rows)})
    print(f"pmo: coverage imported ({len(rows)} requirement rows)")
    return 0


def cmd_budget_set(args) -> int:
    if args.verdict not in BUDGET_VERDICTS:
        return fail(f"verdict not in enum: {args.verdict}", 2)
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        con.execute(
            "INSERT INTO budgets (run_id, budget_id, description, verdict, reason,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (run_id, budget_id) DO UPDATE SET"
            " description = excluded.description, verdict = excluded.verdict,"
            " reason = excluded.reason, recorded_at = excluded.recorded_at",
            (run["id"], args.budget_id, args.description, args.verdict,
             args.reason, now()),
        )
        record(con, "budget_recorded", project_id=run["project_id"],
               run_id=run["id"],
               payload={"budget": args.budget_id, "verdict": args.verdict})
    print(f"pmo: budget {args.budget_id} -> {args.verdict}")
    return 0


def cmd_ledger_checkpoint(args) -> int:
    con = connect()
    with mutate(con):
        run = get_run(con, args.run_key)
        counts: dict[str, int] = {}
        for row in con.execute(
            "SELECT source, severity, COUNT(*) AS n FROM findings WHERE run_id = ?"
            " GROUP BY source, severity ORDER BY source, severity",
            (run["id"],),
        ):
            counts[f"{row['source']}_{row['severity']}"] = row["n"]
        con.execute(
            "INSERT INTO ledger (project_id, story_id, run_id, checkpoint_at,"
            " finding_counts_json, review_rounds, qa_rounds, escaped_defect)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run["project_id"], run["story_id"], run["id"], now(),
             json.dumps(counts, sort_keys=True), run["review_rounds"],
             run["qa_rounds"], 1 if args.escaped_defect else 0),
        )
        record(con, "ledger_checkpoint", project_id=run["project_id"],
               run_id=run["id"],
               payload={"escaped_defect": bool(args.escaped_defect)})
    print("pmo: ledger checkpoint appended")
    return 0


GENERATED_HEADER = "<!-- generated by pmo render; do not edit by hand -->"


def cmd_render_backlog(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    lines = [GENERATED_HEADER, f"# Backlog: {project['name']}", ""]
    epics = con.execute(
        "SELECT * FROM work_items WHERE project_id = ? AND kind = 'epic'"
        " ORDER BY external_id",
        (project["id"],),
    ).fetchall()
    orphan_filter = "AND parent_id IS NULL"
    groups: list[tuple[sqlite3.Row | None, str]] = [(e, "") for e in epics]
    groups.append((None, orphan_filter))
    for epic, extra in groups:
        if epic is not None:
            lines.append(f"## {epic['external_id']} {epic['title']} [{epic['status']}]")
            if epic["scope"]:
                lines.append(f"Goal: {epic['scope']}")
            stories = con.execute(
                "SELECT * FROM work_items WHERE project_id = ? AND kind = 'story'"
                " AND parent_id = ? ORDER BY external_id",
                (project["id"], epic["id"]),
            ).fetchall()
        else:
            stories = con.execute(
                "SELECT * FROM work_items WHERE project_id = ? AND kind = 'story'"
                f" {extra} ORDER BY external_id",
                (project["id"],),
            ).fetchall()
            if not stories:
                continue
            lines.append("## Ungrouped stories")
        lines.append("")
        for story in stories:
            deployed = "yes" if story["deployed_verified"] else "no"
            lines.append(f"### {story['external_id']} {story['title']}")
            lines.append(f"- Status: {story['status']}; type: {story['item_type']};"
                         f" priority: {story['priority']};"
                         f" depends: {story['dependency'] or 'none'};"
                         f" deployed verified: {deployed}")
            lines.append(f"- Scope: {story['scope']}")
            lines.append(f"- Does not include: {story['excludes']}")
            lines.append(f"- Definition of Ready: {story['dor']}")
            lines.append(f"- Definition of Done: {story['dod']}")
            lines.append("")
    criteria = con.execute(
        "SELECT c.criterion_id, c.disposition, c.reason, w.external_id AS story"
        " FROM story_criteria c LEFT JOIN work_items w ON w.id = c.story_id"
        " WHERE c.project_id = ? ORDER BY c.criterion_id",
        (project["id"],),
    ).fetchall()
    if criteria:
        lines.append("## Coverage Map")
        lines.append("")
        lines.append("| Criterion | Story | Disposition | Reason |")
        lines.append("|---|---|---|---|")
        for row in criteria:
            lines.append(f"| {row['criterion_id']} | {row['story'] or '-'} |"
                         f" {row['disposition']} | {row['reason'] or '-'} |")
        lines.append("")
    questions = con.execute(
        "SELECT question FROM open_questions WHERE project_id = ?"
        " AND status = 'open' ORDER BY id",
        (project["id"],),
    ).fetchall()
    if questions:
        lines.append("## Open Questions")
        lines.append("")
        for row in questions:
            lines.append(f"- {row['question']}")
        lines.append("")
    output = "\n".join(lines).rstrip() + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(output, encoding="utf-8")
    print(f"pmo: backlog rendered to {args.out}")
    return 0


def cmd_render_ledger(args) -> int:
    con = connect()
    try:
        project = get_project(con, args.project_key)
    except Rule as exc:
        return fail(str(exc))
    lines = [GENERATED_HEADER, f"# Quality Ledger: {project['name']}", ""]
    lines.append("| Checkpoint | Story | Review rounds | QA rounds |"
                 " Findings | Escaped defect |")
    lines.append("|---|---|---|---|---|---|")
    for row in con.execute(
        "SELECT l.*, w.external_id AS story FROM ledger l"
        " LEFT JOIN work_items w ON w.id = l.story_id"
        " WHERE l.project_id = ? ORDER BY l.checkpoint_at, l.id",
        (project["id"],),
    ):
        counts = json.loads(row["finding_counts_json"])
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
        escaped = "YES" if row["escaped_defect"] else "no"
        lines.append(f"| {row['checkpoint_at']} | {row['story'] or '-'} |"
                     f" {row['review_rounds']} | {row['qa_rounds']} |"
                     f" {summary} | {escaped} |")
    output = "\n".join(lines).rstrip() + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(output, encoding="utf-8")
    print(f"pmo: ledger rendered to {args.out}")
    return 0


def cmd_event_append(args) -> int:
    try:
        payload = json.loads(args.payload) if args.payload else {}
    except json.JSONDecodeError as exc:
        return fail(f"payload is not valid JSON: {exc}", 2)
    con = connect()
    with mutate(con):
        project = get_project(con, args.project_key)
        run_id = None
        if args.run_key:
            run_id = get_run(con, args.run_key)["id"]
        record(con, args.action, project_id=project["id"], run_id=run_id,
               actor=args.actor, payload=payload)
    print(f"pmo: event '{args.action}' appended")
    return 0


def cmd_dump(args) -> int:
    con = connect()
    output = "\n".join(con.iterdump()) + "\n"
    Path(args.out).write_text(output, encoding="utf-8")
    print(f"pmo: database dumped to {args.out}")
    return 0


def cmd_load(args) -> int:
    if db_path().is_file() and not args.force:
        return fail("database already exists; pass --force to overwrite", 1)
    if db_path().is_file():
        db_path().unlink()
    con = connect()
    script = Path(args.infile).read_text(encoding="utf-8")
    con.executescript(script)
    print(f"pmo: database loaded from {args.infile}")
    return 0


def cmd_sync_launcher(args) -> int:
    bin_dir = data_dir() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "pmo_cli.py"
    version_file = bin_dir / "VERSION"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    if current == PMO_VERSION and target.is_file() and not args.force:
        print(f"pmo: launcher already at version {PMO_VERSION}")
        return 0
    shutil.copyfile(Path(__file__).resolve(), target)
    version_file.write_text(PMO_VERSION + "\n", encoding="utf-8")
    print(f"pmo: launcher synced to {target} (version {PMO_VERSION})")
    return 0


def cmd_version(args) -> int:
    print(PMO_VERSION)
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

    p = sub.add_parser("sync-launcher")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_sync_launcher)

    project = sub.add_parser("project").add_subparsers(dest="subcommand", required=True)
    p = project.add_parser("register")
    p.add_argument("--key", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--team", default="")
    p.add_argument("--stamp-config", default="")
    p.set_defaults(func=cmd_project_register)
    p = project.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_project_list)

    p = sub.add_parser("resume-info")
    p.add_argument("--project-key", required=True)
    p.add_argument("--events", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resume_info)

    run = sub.add_parser("run").add_subparsers(dest="subcommand", required=True)
    p = run.add_parser("init")
    p.add_argument("--project-key", required=True)
    p.add_argument("--run-key", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--story", default="")
    p.add_argument("--bindings", default="")
    p.add_argument("--run-dir", default="")
    p.add_argument("--constitution", default="")
    p.add_argument("--brief", default="")
    p.add_argument("--config", default="")
    p.set_defaults(func=cmd_run_init)
    p = run.add_parser("set-step")
    p.add_argument("--run-key", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--artifact", default="")
    p.add_argument("--bump-attempts", action="store_true")
    p.set_defaults(func=cmd_run_set_step)
    p = run.add_parser("record-gate")
    p.add_argument("--run-key", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--decided-by", default="owner")
    p.set_defaults(func=cmd_run_record_gate)
    p = run.add_parser("bump")
    p.add_argument("--run-key", required=True)
    p.add_argument("--counter", required=True)
    p.set_defaults(func=cmd_run_bump)
    p = run.add_parser("set-ownership")
    p.add_argument("--run-key", required=True)
    p.add_argument("--ownership", required=True,
                   help="JSON object: snake_case role -> path prefix list")
    p.set_defaults(func=cmd_run_set_ownership)
    p = run.add_parser("set-status")
    p.add_argument("--run-key", required=True)
    p.add_argument("--status", required=True)
    p.set_defaults(func=cmd_run_set_status)
    p = run.add_parser("release")
    p.add_argument("--run-key", required=True)
    p.set_defaults(func=cmd_run_release)
    p = run.add_parser("validate")
    p.add_argument("--run-key", required=True)
    p.set_defaults(func=cmd_run_validate)

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

    task = sub.add_parser("task").add_subparsers(dest="subcommand", required=True)
    p = task.add_parser("open")
    p.add_argument("--run-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--title", required=True)
    p.set_defaults(func=cmd_task_open)
    p = task.add_parser("close")
    p.add_argument("--run-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--step", default="")
    p.add_argument("--outcome", required=True)
    p.set_defaults(func=cmd_task_close)
    p = task.add_parser("touch")
    p.add_argument("--project-key", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--worktree", default="")
    p.set_defaults(func=cmd_task_touch)

    finding = sub.add_parser("finding").add_subparsers(dest="subcommand", required=True)
    p = finding.add_parser("open")
    p.add_argument("--run-key", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--severity", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--repro", default="")
    p.add_argument("--expected-actual", default="")
    p.add_argument("--traced", default="")
    p.add_argument("--round", type=int, default=0)
    p.set_defaults(func=cmd_finding_open)
    p = finding.add_parser("update")
    p.add_argument("--run-key", required=True)
    p.add_argument("--finding", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--round", type=int, default=None)
    p.set_defaults(func=cmd_finding_update)
    p = finding.add_parser("list")
    p.add_argument("--run-key", required=True)
    p.add_argument("--status", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_finding_list)

    coverage = sub.add_parser("coverage").add_subparsers(dest="subcommand", required=True)
    p = coverage.add_parser("import")
    p.add_argument("--run-key", required=True)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_coverage_import)

    budget = sub.add_parser("budget").add_subparsers(dest="subcommand", required=True)
    p = budget.add_parser("set")
    p.add_argument("--run-key", required=True)
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
    p.add_argument("--run-key", default="")
    p.add_argument("--payload", default="")
    p.set_defaults(func=cmd_event_append)

    ledger = sub.add_parser("ledger").add_subparsers(dest="subcommand", required=True)
    p = ledger.add_parser("checkpoint")
    p.add_argument("--run-key", required=True)
    p.add_argument("--escaped-defect", action="store_true")
    p.set_defaults(func=cmd_ledger_checkpoint)

    render = sub.add_parser("render").add_subparsers(dest="subcommand", required=True)
    p = render.add_parser("backlog")
    p.add_argument("--project-key", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_render_backlog)
    p = render.add_parser("ledger")
    p.add_argument("--project-key", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_render_ledger)

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
