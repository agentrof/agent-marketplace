# Host Contract

- The shared `team_guard.py` PreToolUse hook mechanically requires the PMO session-ready record before Write, Edit, or Bash. Keep the exact context check `AGENTROF_PMO_READY: project-management-office` as the user-facing diagnostic. If it is absent, run `claude plugin list --json` as a read-only diagnostic and stop. If PMO is missing, ask the user to run `/plugin install project-management-office@agent-marketplace`; if it is disabled, ask for `/plugin enable project-management-office@agent-marketplace`; if it is installed and enabled, ask for a Claude Code restart and PMO hook-log inspection. State that no files or project state were changed.
- One delivery team owns a project. Stop without mutation when workspace/config.json or Agentrof-owned project agents name another team.
- Map every canonical dispatcher `run` or `path` call to the registered Claude package by inserting `--host claude` immediately after the verb. This is required when both host packages are installed.
- Present every canonical choice gate through `AskUserQuestion`, preserving the options, recommendation, and tradeoffs.
- When the canonical workflow says `spawn`, use the `software-engineering-team:<agent-id>` identity. Open the PMO task before spawning, wait for every required agent, then close the task after validating its output.
- Dispatch independent read-only agents together and wait for all of them. Do not dispatch overlapping writers concurrently.
- During setup, run `"$RUN" run --host claude "$TEAM" scripts/generate_claude_project.py apply --project-root <git-root> --workspace <chosen-workspace-name>`. The generator owns only its marked block in `CLAUDE.md`, substitutes the chosen workspace name, preserves user content, and stops on an unmanaged collision.
- For delivery-lane handoffs, open a new Claude Code session in the selected worktree.
- Successful completion means the same durable state, gates, artifacts, and PR outcome as every supported host; host-specific UI is not part of the contract.
