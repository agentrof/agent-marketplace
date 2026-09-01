# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode,
  stop before mutation and ask the user to switch modes.
- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start. It never
  registers global state or blocks project work. `vault_hook.py` protects only
  compiler-owned fields and immediately checks changed vault documents.
- One Software Engineering Team owns a project. There is no shared project
  state service or cross-project work key.
- Resolve every canonical "packaged script" reference relative to the installed
  plugin root. Invoke a machine-owned writer by passing that absolute script
  path to the active hook runtime's exact absolute Python executable. Bare
  interpreter names receive no pre-authorized writer grant. For backward
  compatibility, direct bare-Python `init` and `render-application` commands
  may retain their exact command-specific output only when PATH resolves to the
  trusted hook runtime and the owning compiler validates the final application;
  otherwise the hook restores it. Environment indirection and direct shebang
  execution are guard-only. No shared dispatcher or second plugin is involved.
- Native Windows lifecycle writer preservation is not claimed until Codex
  supplies an attested shell-family contract. Shared hook logic is portable to
  Windows and fails closed there; real lifecycle parity remains a host gate.
- Vault hooks are workflow-integrity controls for host-dispatched tool effects,
  not a same-user operating-system sandbox. A process deliberately targeting
  hook scratch or recovery files has the user's filesystem authority; host
  sandboxing and OS permissions remain the security boundary.
- A canonical entry with `project_scope: external` does not require project
  setup, workspace configuration or a Git repository.
- Use `request_user_input` only at declared choice gates, preserving options,
  recommendation and tradeoffs.
- When the canonical workflow says `spawn`, use the matching project-scoped
  custom agent from `.codex/agents/` and wait for every required agent before
  synthesis. Never run overlapping writers concurrently.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots and local project memory.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.
