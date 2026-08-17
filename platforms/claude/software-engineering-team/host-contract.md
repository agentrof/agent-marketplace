# Host Contract

- `team_guard.py` only announces the installed team at session start. It never
  registers global state or blocks project work. `vault_hook.py` protects only
  compiler-owned fields and immediately checks changed vault documents.
- One Software Engineering Team owns a project. There is no shared project
  state service or cross-project work key.
- Resolve every canonical "packaged script" reference beneath
  `${CLAUDE_PLUGIN_ROOT}` and invoke the resulting file directly. No shared
  dispatcher or second plugin is involved.
- Present canonical choice gates through `AskUserQuestion`, preserving options,
  recommendation and tradeoffs.
- When the canonical workflow says `spawn`, use the
  `software-engineering-team:<agent-id>` identity and wait for every required
  agent before synthesis. Never run overlapping writers concurrently.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots and local project memory.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.
  `/delivery-lanes` and `/develop` are retired compatibility routes and must
  not create runtime state.
