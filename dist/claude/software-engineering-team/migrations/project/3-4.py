"""Describe direct authoring and project-local runtime contract version 4.

The host-neutral upgrade engine performs the verified directory migration.
This runner declares the resulting stable project contract.
"""


def migrate(project, context):
    return {
        "runtime_root": ".agentrof/agent-marketplace/.runtime",
        "plan_runtime": ".agentrof/agent-marketplace/.runtime/plan",
        "work_order_runtime": (
            ".agentrof/agent-marketplace/.runtime/work-orders"
        ),
        "design_system_authoring": "workspace/docs/design-system",
        "experience_authoring": "workspace/docs/experience-design",
    }
