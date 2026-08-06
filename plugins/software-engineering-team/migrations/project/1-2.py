"""Describe the team project contract 1 to 2 migration.

The host-neutral upgrade engine owns the atomic file transaction. This runner
keeps the packaged migration catalog explicit and testable.
"""


def migrate(project, context):
    return {
        "project_origin": project.get("project_origin", "unclassified"),
        "gitignore_contract": 1,
        "preparation_identity": "preparation_check.py",
        "experience_identity": "experience-design",
        "backlog_identity": "backlog-plan",
    }
