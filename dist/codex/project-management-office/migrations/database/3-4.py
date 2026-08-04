"""Add stable project identity and the exactly-once migration ledger."""


def column_names(connection, table):
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection, context):
    if "project_uuid" not in column_names(connection, "projects"):
        connection.execute(
            "ALTER TABLE projects ADD COLUMN project_uuid TEXT NOT NULL DEFAULT ''"
        )
    if "repository_fingerprint" not in column_names(connection, "projects"):
        connection.execute(
            "ALTER TABLE projects ADD COLUMN repository_fingerprint"
            " TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_uuid"
        " ON projects(project_uuid) WHERE project_uuid != ''"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " migration_id TEXT PRIMARY KEY, from_version INTEGER NOT NULL,"
        " to_version INTEGER NOT NULL, checksum TEXT NOT NULL,"
        " plugin_version TEXT NOT NULL, started_at TEXT NOT NULL,"
        " finished_at TEXT NOT NULL, source_fingerprint TEXT NOT NULL,"
        " result_json TEXT NOT NULL DEFAULT '{}')"
    )
    project = context.get("project", {})
    if context.get("project_id") and project.get("project_key"):
        connection.execute(
            "UPDATE projects SET project_uuid = ?, repository_fingerprint = ?"
            " WHERE project_key = ? AND project_uuid = ''",
            (context["project_id"], project.get("repository_fingerprint", ""),
             project["project_key"]),
        )
    return {"projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]}
