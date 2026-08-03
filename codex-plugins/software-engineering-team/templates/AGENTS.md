# Agentrof Software Engineering Team

Read and follow `{{workspace}}/memory/me.md` before team work.

## Host rules

- Run mutating Agentrof workflows in Codex Code or Default mode, never Plan mode.
- Use the project-scoped `software-engineering-team-*` custom agents for the matching roles. Open the PMO task before spawning and close it only after validating the role output.
- Dispatch independent read-only roles in parallel; avoid overlapping parallel writers.
- Ask one concise, option-preserving question at each owner gate and resume from PMO/workspace state on the next turn.
- Treat `{{workspace}}/config.json` and generated vault views as machine-managed.

## Workspace

- `{{workspace}}/config.json`: project declaration, changed only through the configure entry.
- `{{workspace}}/docs/`: governed knowledge vault.
- `{{workspace}}/apps/`: application code.
- `{{workspace}}/environment/`: runnable containerized environment.
- `{{workspace}}/work-orders/`: gitignored work-order snapshots; durable delivery state lives in PMO.

Start work through the Agentrof entry skills, not free-form state changes.
