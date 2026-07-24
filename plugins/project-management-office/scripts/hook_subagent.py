#!/usr/bin/env python3
"""SubagentStart/SubagentStop hook: record team-agent work as task activity.

Called with a phase argument (start|stop). Filters to agents belonging to a
registered team plugin, resolves the project from the session's cwd, and
reconciles timestamps into the task row through the CLI. Mechanics only:
which step and round the work belongs to is the orchestrator's record."""

from __future__ import annotations

import sys

import hook_common


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase not in ("start", "stop"):
        hook_common.log(f"subagent hook called with bad phase: {phase!r}")
        return 0
    payload = hook_common.normalize_payload(hook_common.read_payload())
    agent_type = str(payload.get("agent_type", ""))
    role = hook_common.team_role(agent_type)
    if role is None:
        return 0
    resolved = hook_common.resolve_project(payload.get("cwd", ""))
    if resolved is None:
        return 0
    project_key, project_root = resolved
    hook_common.run_cli([
        "task", "touch",
        "--project-key", project_key,
        "--role", role,
        "--phase", phase,
        "--worktree", project_root,
        "--session-id", str(payload.get("session_id", "")),
        "--agent", agent_type.split(":", 1)[-1],
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
