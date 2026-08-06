"""Add program, release, experience and approved backlog planning state."""

from datetime import datetime, timezone
import json


DDL = r"""
CREATE TABLE IF NOT EXISTS programs (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), program_key TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','baselined','complete','cancelled')), baseline_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, program_key));
CREATE TABLE IF NOT EXISTS releases (id INTEGER PRIMARY KEY, program_id INTEGER NOT NULL REFERENCES programs(id), release_key TEXT NOT NULL, title TEXT NOT NULL, sequence INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','baselined','active','complete','cancelled')), experience_registry_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(program_id, release_key), UNIQUE(program_id, sequence));
CREATE TABLE IF NOT EXISTS work_item_releases (item_id INTEGER PRIMARY KEY REFERENCES work_items(id), release_id INTEGER NOT NULL REFERENCES releases(id), provenance TEXT NOT NULL DEFAULT 'approved_plan');
CREATE TABLE IF NOT EXISTS readiness_items (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES work_items(id), statement TEXT NOT NULL, satisfied INTEGER NOT NULL DEFAULT 0, UNIQUE(item_id, statement));
CREATE TABLE IF NOT EXISTS work_item_refs (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES work_items(id), ref_kind TEXT NOT NULL CHECK (ref_kind IN ('requirement','solution','budget','ux')), ref_value TEXT NOT NULL, UNIQUE(item_id, ref_kind, ref_value));
CREATE TABLE IF NOT EXISTS work_item_owners (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES work_items(id), role TEXT NOT NULL, relationship TEXT NOT NULL CHECK (relationship IN ('owner','supporting')), UNIQUE(item_id, role, relationship));
CREATE TABLE IF NOT EXISTS work_item_shares (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), left_item_id INTEGER NOT NULL REFERENCES work_items(id), right_item_id INTEGER NOT NULL REFERENCES work_items(id), subject TEXT NOT NULL, UNIQUE(left_item_id, right_item_id, subject));
CREATE TABLE IF NOT EXISTS experience_runs (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), run_key TEXT NOT NULL, program_key TEXT NOT NULL, release_key TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released')), created_at TEXT NOT NULL, released_at TEXT NOT NULL DEFAULT '', UNIQUE(project_id, run_key));
CREATE TABLE IF NOT EXISTS experience_node_claims (run_id INTEGER NOT NULL REFERENCES experience_runs(id), node_ref TEXT NOT NULL, claimed_at TEXT NOT NULL, PRIMARY KEY(run_id, node_ref));
CREATE TABLE IF NOT EXISTS experience_gates (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES experience_runs(id), gate_name TEXT NOT NULL, decision TEXT NOT NULL, revision_hash TEXT NOT NULL, decided_by TEXT NOT NULL DEFAULT 'owner', decided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backlog_plans (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), plan_key TEXT NOT NULL, program_key TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('baseline','replan','feature')), status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','verified','applied','abandoned')), plan_json TEXT NOT NULL, draft_hash TEXT NOT NULL, compiler_hash TEXT NOT NULL DEFAULT '', approved_hash TEXT NOT NULL DEFAULT '', gate_revision INTEGER NOT NULL DEFAULT 0, session_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, plan_key));
CREATE TABLE IF NOT EXISTS backlog_plan_revisions (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES backlog_plans(id), revision INTEGER NOT NULL, plan_hash TEXT NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(plan_id, revision), UNIQUE(plan_id, plan_hash));
CREATE TABLE IF NOT EXISTS planning_findings (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES backlog_plans(id), external_id TEXT NOT NULL, finding_kind TEXT NOT NULL DEFAULT 'semantic' CHECK (finding_kind IN ('mechanical','semantic')), severity TEXT NOT NULL CHECK (severity IN ('blocker','non-blocking')), summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','rejected','accepted-risk')), reason TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', revisit TEXT NOT NULL DEFAULT '', review_rounds INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(plan_id, external_id));
CREATE TABLE IF NOT EXISTS planning_gates (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES backlog_plans(id), gate_name TEXT NOT NULL, decision TEXT NOT NULL, plan_hash TEXT NOT NULL, revision INTEGER NOT NULL, decided_by TEXT NOT NULL DEFAULT 'owner', decided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS id_reservations (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES backlog_plans(id), prefix TEXT NOT NULL, first_value INTEGER NOT NULL, last_value INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(plan_id, prefix, first_value));
CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(program_id, status);
CREATE INDEX IF NOT EXISTS idx_backlog_plans_status ON backlog_plans(project_id, status);
CREATE INDEX IF NOT EXISTS idx_experience_runs_status ON experience_runs(project_id, status);
"""


def migrate(connection, context):
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    migrated = 0
    for project in connection.execute("SELECT id, project_key FROM projects ORDER BY id").fetchall():
        project_id = project[0]
        stories = connection.execute("SELECT id, external_id, status FROM work_items WHERE project_id = ? AND kind = 'story' ORDER BY id", (project_id,)).fetchall()
        if not stories:
            continue
        terminal = all(row[2] in {"done", "deferred"} for row in stories)
        program_status = "complete" if terminal else "draft"
        release_status = "complete" if terminal else "draft"
        connection.execute("INSERT INTO programs (project_id, program_key, title, status, created_at, updated_at) VALUES (?, 'PRG-LEGACY', 'Legacy backlog', ?, ?, ?)", (project_id, program_status, stamp, stamp))
        program_id = connection.execute("SELECT id FROM programs WHERE project_id = ? AND program_key = 'PRG-LEGACY'", (project_id,)).fetchone()[0]
        connection.execute("INSERT INTO releases (program_id, release_key, title, sequence, status, created_at, updated_at) VALUES (?, 'REL-LEGACY', 'Legacy release', 1, ?, ?, ?)", (program_id, release_status, stamp, stamp))
        release_id = connection.execute("SELECT id FROM releases WHERE program_id = ? AND release_key = 'REL-LEGACY'", (program_id,)).fetchone()[0]
        for story in stories:
            connection.execute("INSERT INTO work_item_releases (item_id, release_id, provenance) VALUES (?, ?, 'migrated_unverified')", (story[0], release_id))
        connection.execute("UPDATE story_criteria SET criterion_id = 'legacy:' || criterion_id WHERE project_id = ? AND criterion_id NOT LIKE 'legacy:%'", (project_id,))
        if not terminal:
            payload = {"mode": "baseline", "program_id": "PRG-LEGACY", "releases": [{"release_id": "REL-LEGACY"}], "stories": [row[1] for row in stories], "provenance": "migrated_unverified"}
            plan_json = json.dumps(payload, sort_keys=True)
            connection.execute("INSERT INTO backlog_plans (project_id, plan_key, program_key, mode, plan_json, draft_hash, created_at, updated_at) VALUES (?, 'legacy-adoption', 'PRG-LEGACY', 'baseline', ?, 'migrated_unverified', ?, ?)", (project_id, plan_json, stamp, stamp))
            plan_id = connection.execute("SELECT id FROM backlog_plans WHERE project_id = ? AND plan_key = 'legacy-adoption'", (project_id,)).fetchone()[0]
            connection.execute("INSERT INTO backlog_plan_revisions (plan_id, revision, plan_hash, plan_json, created_at) VALUES (?, 1, 'migrated_unverified', ?, ?)", (plan_id, plan_json, stamp))
            for story in stories:
                if story[2] != "in_development":
                    continue
                active = connection.execute("SELECT 1 FROM work_orders WHERE story_id = ? AND status IN ('running','waiting_gate')", (story[0],)).fetchone()
                if active is None:
                    connection.execute("INSERT INTO planning_findings (plan_id, external_id, severity, summary, created_at, updated_at) VALUES (?, ?, 'blocker', ?, ?, ?)", (plan_id, "LEGACY-" + story[1], "Legacy in-development story has no active work order", stamp, stamp))
        migrated += 1
    return {"legacy_projects": migrated}
