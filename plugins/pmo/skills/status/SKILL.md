---
name: status
description: Entry point for the Project Management Office status view. Invoked by the user as a slash skill; reports the data directory, database health, registered projects and every active work order with its step state. Can launch the read-only web dashboard.
disable-model-invocation: true
---

# Status

Diagnostic and progress view over the central PMO database.

## When to Use

- The user asks where things stand: active work orders, current steps, recent activity.
- The user wants the visual dashboard opened in the browser.
- The user suspects the operations backbone is broken and wants a health check.
- A team plugin reported that the PMO CLI is missing or outdated.

## Procedure

1. Resolve the CLI. Prefer the synced launcher, fall back to this plugin's copy:
   - `PMO="${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py"`; if that file does not exist, use `${CLAUDE_PLUGIN_ROOT}/scripts/pmo_cli.py` and run its `sync-launcher` subcommand once so the launcher exists next time.
2. Health: run the CLI's `version` and `init-db` subcommands (idempotent; init-db creates the data directory and schema when absent). Report the printed database path.
3. Projects: run `project list`. If the current directory has a `workspace/config.json` with a `project_key`, treat that project as the focus.
4. For the focus project (or each project when none is in focus), run `resume-info --project-key <key>` and report: active work orders with story, status, current step, steps done, review and qa rounds, plus the recent event tail.
5. Backlog snapshot on request: `item list --project-key <key> --kind story` grouped by status.
6. Dashboard on request ("open the dashboard", "show me the board"): run the CLI's `dashboard` subcommand in the background (`--no-browser` off by default opens the browser). It serves a read-only localhost view over the same database; it never writes.
7. Never write to the database from this skill beyond `init-db` and `sync-launcher`; this is a read-only view. State mutations belong to the owning team flows.

## Failure Reporting

- CLI missing at both locations: tell the user the pmo plugin files are absent and to reinstall the plugin from the marketplace.
- Schema version newer than the CLI: report the exact message and advise updating the pmo plugin.
