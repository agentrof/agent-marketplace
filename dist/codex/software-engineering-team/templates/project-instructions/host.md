# Load user context

Before any work, read these files in this order and follow them. Skip a file
that does not exist.

- `{{user_context}}`

## Codex host

- Run mutating Agent Marketplace workflows in Code or Default mode, never Plan
  mode.
- Spawn the matching project-scoped custom agent by its bare canonical role id.
  Dispatch independent read-only roles in parallel; never run overlapping
  writers in parallel.
- Use `request_user_input` only at declared project decision gates.
