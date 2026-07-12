#!/usr/bin/env python3
"""SessionStart hook: bootstrap the data directory, sync the CLI launcher,
and inject resume context when the session's project has an active run."""

from __future__ import annotations

import json
import sys

import hook_common
import pmo_cli


def main() -> int:
    payload = hook_common.read_payload()
    try:
        hook_common.run_cli(["init-db"])
        hook_common.run_cli(["sync-launcher"])
    except Exception as exc:
        hook_common.log(f"session_start bootstrap failed: {exc}")

    resolved = hook_common.resolve_project(payload.get("cwd", ""))
    if resolved is None:
        return 0
    project_key, _ = resolved
    try:
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = pmo_cli.main(["resume-info", "--project-key", project_key, "--json"])
        if code != 0:
            return 0
        info = json.loads(out.getvalue())
        runs = info.get("active_runs", [])
        if not runs:
            return 0
        lines = [
            "PMO resume context: this project has active team run(s) in the",
            "central database. Before starting new work, surface this to the",
            "user and offer to resume.",
        ]
        for run in runs:
            story = f", story {run['story']}" if run.get("story") else ""
            lines.append(
                f"- run '{run['run_key']}': {run['status']} at step"
                f" {run['current_step']}{story} (review rounds"
                f" {run['review_rounds']}, qa rounds {run['qa_rounds']})"
            )
        lines.append(
            "To resume, invoke the owning team plugin's entry skill (for"
            " software-team: the request entry); its pre-flight routes back"
            " into the run's flow at the recorded step. Do NOT continue the"
            " work free-form: every state change goes through the PMO CLI."
        )
        lines.append(
            "Details: resume-info --project-key " + project_key
            + " via the PMO CLI launcher in the data directory."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n".join(lines),
            }
        }))
    except Exception as exc:
        hook_common.log(f"session_start resume-info failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
