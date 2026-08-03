# Codex Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode, stop before mutation and ask the user to switch modes.
- Treat `AGENTROF_HOOKS_ACTIVE` in session context as the hook trust sentinel. If it is absent, stop and ask the user to review and trust both Agentrof plugins through `/hooks`, then start a new task.
- Resolve the PMO launcher before work. If the PMO plugin is absent, stop with `codex plugin add project-management-office@agent-marketplace`.
- When the canonical workflow says `Task` or “spawn”, use the matching project-scoped custom agent from `.codex/agents/`. Open the PMO task before spawning, wait for every required agent, then close the PMO task after validating its output.
- Dispatch independent roles in parallel and wait for all of them before synthesis. Never allow parallel writers to edit overlapping files.
- When the canonical workflow asks through `AskUserQuestion`, end the current turn with one concise question and the same options. Resume from PMO/workspace state after the user's next message.
- Successful completion means the same durable state, gates, artifacts, and PR outcome as Claude Code; host-specific UI is not part of the contract.
