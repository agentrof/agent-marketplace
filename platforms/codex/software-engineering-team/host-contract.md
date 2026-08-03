# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode, stop before mutation and ask the user to switch modes.
- Treat `AGENTROF_HOOKS_ACTIVE` in session context as the hook trust sentinel. If it is absent, stop and ask the user to review and trust both Agentrof plugins through `/hooks`, then start a new task.
- Resolve the PMO launcher before work. If the PMO plugin is absent, stop with `codex plugin add project-management-office@agent-marketplace`.
- When the canonical workflow says `spawn`, use the matching project-scoped custom agent from `.codex/agents/`. Open the PMO task before spawning, wait for every required agent, then close the PMO task after validating its output.
- Dispatch independent roles in parallel and wait for all of them before synthesis. Never allow parallel writers to edit overlapping files.
- When the canonical workflow reaches a choice gate, end the current turn with one concise question and the same options. Resume from PMO/workspace state after the user's next message.
- During setup, require `AGENTROF_HOOKS_ACTIVE` before any write. If absent, ask the user to inspect and trust both plugins with `/hooks`, then start a new task.
- During setup, stop with `codex plugin add project-management-office@agent-marketplace` if PMO is absent. After common templates exist, run `"$RUN" run "$TEAM" scripts/generate_codex_project.py --project-root <git-root> --workspace <chosen-workspace-name>`. The generator owns only its marked `AGENTS.md` block and Agentrof-marked `.codex/agents/*.toml`; stop on an unmanaged collision. Tell the user to start a fresh task/session so the agents load.
- For delivery-lane handoffs, open a new App task on the worktree or an interactive CLI session in that worktree.
- Successful completion means the same durable state, gates, artifacts, and PR outcome as every supported host; host-specific UI is not part of the contract.
