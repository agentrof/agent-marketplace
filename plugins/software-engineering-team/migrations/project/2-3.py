"""Describe the team project contract 2 to 3 migration.

The host-neutral upgrade engine owns the transaction. This step records the
single-vault policy, adoption gate and new transient Design System work area.
It never moves unknown vault content or invents approvals.
"""


def migrate(project, context):
    return {
        "vault_root": "workspace/docs",
        "vault_policy": 5,
        "vault_status": project.get("vault_status", "pending"),
        "vault_adoption": "exact-plan-hash-required",
        "design_system_work": "workspace/design-system-work",
        "relation_projection": "typed-outgoing-generated-inverse",
    }
