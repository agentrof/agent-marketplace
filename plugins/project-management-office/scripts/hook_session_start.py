#!/usr/bin/env python3
"""SessionStart hook: bootstrap the data directory, sync the CLI launcher,
inject the current UTC time (every artifact timestamp comes from the clock,
never from the model's memory), and inject resume context when the
session's project has an active work order."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import hook_common
import pmo_cli


def clock_line() -> str:
    return (
        f"Current UTC time at session start: {pmo_cli.now()}. Timestamps"
        " written into durable artifacts come from the clock, never from"
        " memory: run the PMO CLI 'now' verb (--date for YYYY-MM-DD,"
        " --compact for work-order key prefixes) at write time, or use the"
        " owning stamp verb where one exists."
    )


def resume_lines(payload) -> list[str]:
    resolved = hook_common.resolve_project(payload.get("cwd", ""))
    if resolved is None:
        return []
    project_key, project_root = resolved
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = pmo_cli.main(["resume-info", "--project-key", project_key, "--json"])
    if code != 0:
        return []
    info = json.loads(out.getvalue())
    orders = info.get("active_work_orders", [])
    if not orders:
        return []
    # Partition by worktree: this session may only resume the order that
    # lives HERE; orders in other worktrees are parallel lanes.
    here = str(Path(project_root).resolve())
    local = [o for o in orders if o.get("worktree") == here]
    elsewhere = [o for o in orders if o.get("worktree") != here]

    def describe(order):
        story = f", story {order['story']}" if order.get("story") else ""
        return (
            f"- work order '{order['work_order_key']}': {order['status']}"
            f" at step {order['current_step']}{story} (review rounds"
            f" {order['review_rounds']}, qa rounds {order['qa_rounds']})"
        )

    lines = ["PMO resume context for this project:"]
    if local:
        lines.append("Active in THIS worktree; before starting new work,"
                     " surface it to the user and offer to resume:")
        lines.extend(describe(o) for o in local)
        lines.append(
            "To resume, invoke the owning team plugin's entry skill (for"
            " software-engineering-team: the request entry); its pre-flight routes back"
            " into the work order's flow at the recorded step. Do NOT"
            " continue the work free-form: every state change goes through"
            " the PMO CLI."
        )
    if elsewhere:
        lines.append(
            "Active in OTHER worktrees (parallel lanes); do not resume or"
            " modify them from this session, the delivery-lanes entry on the"
            " primary checkout coordinates them:"
        )
        lines.extend(describe(o) + f" (worktree {o.get('worktree')})"
                     for o in elsewhere)
    lines.append(
        "Details: resume-info --project-key " + project_key
        + " via the PMO CLI launcher in the data directory."
    )
    return lines


def main() -> int:
    payload = hook_common.read_payload()
    try:
        hook_common.run_cli(["init-db"])
        hook_common.run_cli(["sync-launcher"])
    except Exception as exc:
        hook_common.log(f"session_start bootstrap failed: {exc}")

    lines = [clock_line()]
    try:
        lines.extend(resume_lines(payload))
    except Exception as exc:
        hook_common.log(f"session_start resume-info failed: {exc}")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
