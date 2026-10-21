# Host Contract

- One Software Engineering Team owns a project. Packaged scripts resolve
  beneath `${CLAUDE_PLUGIN_ROOT}`. An entry with `project_scope: external`
  needs no setup, workspace config or Git repository.
- At session start `team_guard.py` reports the absolute Python and scripts
  binding and any rendered role whose stamp no longer matches the installed
  plugin or `workspace/config.json`; it never blocks. Every hook runs through
  `hook_launcher.py`; when the context reports `AGENT_MARKETPLACE_PYTHON:
  unsupported`, stop the entry and give the user the stated fix.
- Choice gates use `AskUserQuestion`: at most four questions per call, two to
  four options each, recommendation first; it adds `Other` itself.
- `spawn` uses the project's rendered `software-engineering-team-<agent-id>`
  when the session lists it, else the plugin's
  `software-engineering-team:<agent-id>`; while `team_guard.py` reports a stale
  stamp, use the plugin identity for every role. On any other fallback tell the
  user once why project model settings are off: no rendered file means setup
  has not run; a file that is not listed needs a restart, or a managed
  `strictPluginOnlyCustomization` blocks project agents. Wait for every
  required agent; never run overlapping writers.
- A background agent's completion notice reaches only the top session. A role
  running as a subagent spawns its readers in the foreground, a whole wave in
  one message; background only work that writes a file, then wait with
  `await_outputs.py --file <output>` in the foreground (Bash `timeout`
  300000), repeating while pending. Never end a turn to wait for a notice.
- Setup and refresh regenerate the host projection, run the
  generated project check and preserve authored files. The generator owns only the instruction
  roots, local memory and the header-stamped
  `.claude/agents/software-engineering-team-<agent-id>.md` files; other files
  stay, and a header-stamped user copy is listed under `kept`. The first render
  creates `.claude/agents/`, which loads only at session start, so setup asks
  for a restart.
- Workhorse model: `deliver` runs in a fresh session per task started with
  `claude --model sonnet` (or `/model` in the app); a skill's `model:` does
  not switch the session model.
- Models: every spawn passes the `model` alias and `effort` that
  `model_class.py resolve` returns; the pinned models need Claude Code 2.1.293
  or later. `/configure models` records
  `class_models` and `role_classes` overrides and renders the role files
  again; never edit them by hand. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` runs
  every role on the main model and `CLAUDE_CODE_EFFORT_LEVEL` pins every
  effort, for every subagent in the session. Rendered roles set
  `omitClaudeMd: true` and the `disallowedTools` of
  `platforms/claude/agent-tools.json`.
- A role whose pinned model cannot run falls back to this session's model
  with a visible warning. When an Agent result or completion message names its
  pinned model as unavailable, refused or unsupported (`model_not_found`,
  `There's an issue with the selected model`, `is not available on your ...
  deployment`, `is not available with the ... plan`, `don't have access to the
  model`, `does not support this model`), tell the user which role, model and
  why. Spawn it again once, same `subagent_type` with `model` set to this
  session's family alias, only when the failed run changed nothing: its
  manifest's `write_boundary` is `read_only`, or its `task_inputs.py`
  invocation still passes with `--expected-hash <source_hash>`. Otherwise, or
  if that spawn fails too, stop and report. The `model_fallback.py` hooks on
  `Agent` add this warning; they stay silent for a spawn that passes `model`.

## Autopilot

`/software-engineering-team:autopilot` is the entry; its skill holds the
procedure. Claude facts: the `UserPromptExpansion` hook records the user's
typed `on` as the arming record; the `PreToolUse` hook on `AskUserQuestion`
denies questions only in the session whose `CLAUDE_CODE_SESSION_ID` armed the
grant; `autopilot_guard.py` denies `Write` and `Edit` into
`.agentrof/agent-marketplace/.runtime/autopilot/`; `--recover-transport` uses
the native `asyncRewake` watcher.
