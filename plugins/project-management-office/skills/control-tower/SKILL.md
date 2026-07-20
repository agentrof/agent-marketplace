---
name: control-tower
description: Starts Control Tower, the read-only web dashboard over the central database, as a background process and replies with the clickable running URL. The remote-control of the operations backbone.
disable-model-invocation: true
---

# Launch Control Tower

Run it, get a link. Everything the operations backbone tracks (projects,
work orders, gates, findings, coverage, budgets, quality ledger, audit
events, the team catalog) is viewed in Control Tower; this entry only
starts it and hands back the URL.

## When to Use

- The user wants to see where things stand: Control Tower is the answer.
- The user asks to open, start or link the dashboard.
- A team plugin reported the CLI launcher missing or outdated (this
  entry bootstraps it as a side effect).

## Procedure

1. Resolve the CLI: prefer the synced launcher
   `PMO="${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py"`. If that file
   does not exist, find this plugin's installed copy of scripts/pmo_cli.py
   and run its `ensure` subcommand once (it syncs the launcher): on Claude
   Code the install root is listed in installed_plugins.json inside the
   user-level Claude plugins directory; on Codex it is the newest version
   directory of this plugin inside the user-level Codex plugin cache; on
   Cursor the session-start hook syncs the launcher, so starting a new
   session is the path. Then use `"$PMO"` and run the idempotent `ensure`.
2. Detect the surface: HARNESS=$("$RUN" harness), with the dispatcher
   RUN="${AGENTROF_HOME:-$HOME/.agentrof}/bin/agentrof_run.py" synced
   beside the launcher.
3. On claude_code (or unknown): start the server as a BACKGROUND
   process: `"$PMO" dashboard --no-browser`. The FIRST stdout line
   carries the running URL. If the default port is taken, retry once
   with `--port 0` and read the bound URL from that first line instead.
   Reply with the clickable link, exactly this shape: "Control Tower is
   running: http://127.0.0.1:<port>/". Add one line: the server keeps
   running in the background and refreshes itself; stop it by killing
   the background process.
4. On cursor or codex: the default sandbox denies binding localhost, so
   do NOT start the server from this session. Print the exact command
   for the user's OWN terminal, `"$PMO" dashboard --no-browser` with
   the resolved launcher path substituted, name the URL shape to expect
   from its first output line, and name the opt-in alternative (network
   access with local binding enabled in the harness's sandbox
   configuration) for users who want it launched in-session.
5. Never write to the database beyond `ensure`, `init-db` and
   `sync-launcher`; Control Tower itself is read-only by construction.

## Failure Reporting

- CLI missing at both locations: the project-management-office plugin
  files are absent; reinstall it from the marketplace.
- Schema version newer than the CLI: report the exact message and advise
  updating the project-management-office plugin.
