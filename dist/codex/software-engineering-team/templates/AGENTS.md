# Software Engineering Team

Read and follow `{{workspace}}/memory/me.md` before team work.

## Host rules

- Run mutating Agent Marketplace workflows in Codex Code or Default mode, never Plan mode.
- Use the matching project-scoped custom agent by its bare canonical role id. Open the PMO task before spawning and close it only after validating the role output.
- Dispatch independent read-only roles in parallel; avoid overlapping parallel writers.
- Use `request_user_input` at each canonical owner choice gate, preserving the
  options, recommendation and tradeoffs. Do not use it for ordinary dialogue.
- Treat `{{workspace}}/config.json` and generated vault views as machine-managed.

## Workspace

- `{{workspace}}/config.json`: project declaration, changed only through the configure entry.
- `{{workspace}}/docs/`: governed knowledge vault.
- `{{workspace}}/docs/experience-design/`: approved program and release
  experience graphs; sketches are not baselines.
- `{{workspace}}/apps/`: application code.
- `{{workspace}}/environment/`: runnable containerized environment.
- `.agentrof/agent-marketplace/.runtime/work-orders/`: gitignored work-order
  snapshots owned by this worktree; durable delivery state lives in PMO.
- `.agentrof/agent-marketplace/.runtime/plan/`: gitignored plan drafts.

Start work through the Agent Marketplace entry skills, not free-form state changes.
For greenfield use setup, business-analysis, solution-design, design-system,
experience-design and backlog-plan, then stop before explicit delivery.
