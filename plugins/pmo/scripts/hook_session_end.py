#!/usr/bin/env python3
"""SessionEnd hook: flag sessions that end while a work order is still
active, so resume-info and the dashboard can surface dangling work."""

from __future__ import annotations

import json
import sys

import hook_common
import pmo_cli


def main() -> int:
    payload = hook_common.read_payload()
    resolved = hook_common.resolve_project(payload.get("cwd", ""))
    if resolved is None:
        return 0
    project_key, project_root = resolved
    try:
        import contextlib
        import io
        from pathlib import Path
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = pmo_cli.main(["resume-info", "--project-key", project_key, "--json"])
        if code != 0:
            return 0
        orders = json.loads(out.getvalue()).get("active_work_orders", [])
        # Only THIS session's worktree goes dangling; lanes in other worktrees
        # have their own sessions and must not be flagged by this one.
        here = str(Path(project_root).resolve())
        orders = [o for o in orders if o.get("worktree") == here]
        for order in orders:
            hook_common.run_cli([
                "event", "append",
                "--project-key", project_key,
                "--action", "session_ended_with_active_work_order",
                "--actor", "hook",
                "--work-order-key", order["work_order_key"],
                "--payload", json.dumps({
                    "reason": payload.get("reason", ""),
                    "current_step": order.get("current_step", ""),
                }),
            ])
    except Exception as exc:
        hook_common.log(f"session_end failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
