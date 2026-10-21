# Host Contract

- Run state-changing entries only in Code or Default mode; in Plan mode stop
  before mutation and ask the user to switch.
- One Software Engineering Team owns a project. Packaged scripts resolve
  relative to the installed plugin root. An entry with `project_scope:
  external` needs no setup, workspace config or Git repository.
- At session start `team_guard.py` reports the absolute Python and scripts
  binding and any role file in `.codex/agents/` whose stamp no longer matches
  the installed plugin or `workspace/config.json`; it never blocks. Every hook
  runs through `hook_launcher.py`; when the context reports
  `AGENT_MARKETPLACE_PYTHON: unsupported`, stop the entry and give the user
  the stated fix.
- Choice gates use `request_user_input`, only at declared gates: at most three
  questions per call, two to three options each, recommendation first; Codex
  adds `Other` itself.
- `spawn` uses the matching project-scoped custom agent from `.codex/agents/`.
  Wait for every required agent; never run overlapping writers.
- Setup and refresh regenerate the host projection, run the
  generated project check and preserve authored files. The generator owns only the instruction
  roots, local memory and the header-stamped role files in `.codex/agents/`. A
  stale stamp means the role still runs what it was rendered with until setup
  renders it again; the next start reads the new file.
- Workhorse model: `deliver` runs in a fresh session per task started with
  `codex -m gpt-6.1-sol -c model_reasoning_effort=medium`.
- Models: a role file's `model` and `model_reasoning_effort` win over a spawn
  request, so the spawn passes no model; the pinned models need Codex 0.159.1
  or later. `/configure models` records
  `class_models` and `role_classes` overrides and renders the role files
  again; never edit them by hand; `ultra` is refused. `<absolute-python>
  <absolute-package-scripts>/generate_codex_project.py apply --project-root
  <root> --scope local --execution-profile inherit` omits `model` from every
  role file and keeps `model_reasoning_effort`; `--execution-profile auto`
  restores the pins.
- A spawn whose pinned model errors as unknown or unsupported (`Agent
  errored: ...`) is started once more only if it changed nothing, after the
  command above with `--execution-profile inherit`; tell the user which role
  and model, and stop if it fails again.

## Autopilot

`$software-engineering-team:autopilot` is the entry; its skill holds the
procedure. Codex facts: the `UserPromptSubmit` hook records the user's typed
`on` as the arming record, and both it and the `PreToolUse` question hook work
only after the user trusts them in `/hooks`, again after an update. The
question hook denies `request_user_input` and `request_user_input_async` only
in the thread whose `CODEX_THREAD_ID` armed the grant;
`send_message_to_user_async` is not guarded, so never ask through it under a
grant. `autopilot_guard.py` denies `apply_patch` into
`.agentrof/agent-marketplace/.runtime/autopilot/`. `--recover-transport` binds
a native heartbeat with the app's automation tool and `native-bind`.
