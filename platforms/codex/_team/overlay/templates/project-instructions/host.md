# Load user context

Before any work, read these files in this order and follow them. Skip a file
that does not exist. Their loading is best-effort; mechanically required rules
stay in this generated file or in hooks and validators.

- `{{user_context}}`

## Codex host

- Run mutating Agent Marketplace workflows in Code or Default mode, never Plan
  mode.
- Use the matching project-scoped custom agent by its bare canonical role id.
  Open the standalone team task before spawning and close it after validating the role
  output.
- Dispatch independent read-only roles in parallel. Do not run overlapping
  parallel writers.
- Use `request_user_input` only at declared project decision gates.
