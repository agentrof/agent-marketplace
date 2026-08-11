"""Add audited abandonment to Experience runs without deleting claims."""


def migrate(connection, context):
    connection.execute("ALTER TABLE experience_gates RENAME TO experience_gates_v5")
    connection.execute(
        "ALTER TABLE experience_node_claims RENAME TO experience_node_claims_v5"
    )
    connection.execute("ALTER TABLE experience_runs RENAME TO experience_runs_v5")
    connection.execute(
        "CREATE TABLE experience_runs ("
        "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),"
        "run_key TEXT NOT NULL, program_key TEXT NOT NULL,"
        "release_key TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',"
        "status TEXT NOT NULL DEFAULT 'active'"
        " CHECK (status IN ('active','released','abandoned')),"
        "created_at TEXT NOT NULL, released_at TEXT NOT NULL DEFAULT '',"
        "abandoned_at TEXT NOT NULL DEFAULT '', abandon_reason TEXT NOT NULL DEFAULT '',"
        "UNIQUE(project_id, run_key))"
    )
    connection.execute(
        "CREATE TABLE experience_node_claims ("
        "run_id INTEGER NOT NULL REFERENCES experience_runs(id),"
        "node_ref TEXT NOT NULL, claimed_at TEXT NOT NULL, PRIMARY KEY(run_id,node_ref))"
    )
    connection.execute(
        "CREATE TABLE experience_gates ("
        "id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES experience_runs(id),"
        "gate_name TEXT NOT NULL, decision TEXT NOT NULL, revision_hash TEXT NOT NULL,"
        "decided_by TEXT NOT NULL DEFAULT 'owner', decided_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO experience_runs"
        " (id,project_id,run_key,program_key,release_key,session_id,status,created_at,released_at)"
        " SELECT id,project_id,run_key,program_key,release_key,session_id,status,created_at,released_at"
        " FROM experience_runs_v5"
    )
    connection.execute(
        "INSERT INTO experience_node_claims SELECT * FROM experience_node_claims_v5"
    )
    connection.execute("INSERT INTO experience_gates SELECT * FROM experience_gates_v5")
    connection.execute("DROP TABLE experience_gates_v5")
    connection.execute("DROP TABLE experience_node_claims_v5")
    connection.execute("DROP TABLE experience_runs_v5")
    connection.execute(
        "CREATE INDEX idx_experience_runs_status ON experience_runs(project_id,status)"
    )
    return {"experience_runs_preserved": connection.execute(
        "SELECT COUNT(*) FROM experience_runs"
    ).fetchone()[0]}
