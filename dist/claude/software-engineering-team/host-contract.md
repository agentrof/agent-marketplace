# Host Contract

- One Software Engineering Team owns one project. Resolve scripts beneath `${CLAUDE_PLUGIN_ROOT}`.
  External entries require no project setup. `team_guard.py` reports the
  Python/scripts binding and stale managed launch definitions. A Python
  `unsupported` marker blocks entry with its stated fix.
- Present genuine decision gates through `AskUserQuestion`, at most four
  questions with two to four choices each and the recommendation first.
- Resolve each native worker through `model_class.py resolve --profile
  <light|work|review|critical> --access <read|write> --host claude`. Prefer the
  current project identity `software-engineering-team-<agent-id>`. Use the
  plugin identity `software-engineering-team:<agent-id>` only after confirming
  its settings match the request. A stale or unavailable project definition
  must not silently discard project overrides. Refresh or report pending.
- Allocate isolated write workspaces before launch. The parent owns all
  delegation and waits for required work. Workers cannot launch other agents.
- Launch definitions pin full model IDs and effort. Pass no spawn-time model
  alias, which would override the pin. The models need Claude Code 2.1.293 or
  later. Record requested and host-observed model/effort separately. `/tasks`
  can expose observations. Missing observations remain unverified.
- Read profiles allow only `Read`, `Grep` and `Glob`. Confirm the host loaded
  that definition before accepting independent review. Worker profiles retain
  task-scoped authoring and preview-browser capability. `omitClaudeMd: true`
  keeps the inherited instruction load bounded. The worker launch hook supplies
  current project rules and applicable methods from `SWE_WORKER_BRIEF`. The
  parent includes current user restrictions that are not yet in project files.
- The main-session default is `claude-sonnet-5-5` at `medium`. This is a launch
  recommendation and changes no global configuration. `/configure models`
  manages project `class_models` overrides for the four compute profiles.
  An explicit `session` override uses `inherit` and retains profile effort.
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and `CLAUDE_CODE_EFFORT_LEVEL` can override
  requested settings. Check their effect through host observations.
- Setup and refresh run the generated project check. Setup owns stamped project launch definitions and generated instruction
  roots, preserves authored files and reports collisions. Replaced owned
  definitions participate in its rollback-capable transaction. Restart when
  the host has not loaded a new or changed definition.
- An unavailable model keeps its pin. After a failed launch with no changes,
  allow one retry using a separately resolved, authorized fallback profile for
  that attempt. Record the first failure and changed request. Never rewrite
  shared project settings for a single retry.

- With a compatible workspace config, stale launch source, model or renderer
  needs only `generate_claude_project.py apply --scope local`, followed by
  `generate_claude_project.py check --scope local`, from this package's scripts.
  This refresh changes owned launch files and preserves project documents.
  Use full setup for missing bootstrap or a reviewed config migration. Confirm
  host reload before relying on a refreshed launch identity.

## Local candidate loading

Build every host distribution from canonical source, then start the test
session with `claude --plugin-dir <candidate-root>/dist/claude/software-engineering-team`.
Rebuild after edits and run `/reload-plugins`, or start a fresh test session.
Use `/plugin` to inspect discovered components and load errors. Confirm the
actual package path, package digest, loaded skill and hook binding before
accepting a regression result. Confirm any project launch projections match
the candidate, since their model settings can override the plugin identity.
Keep versioned release caches immutable. Restore the intended stable package
selection after the candidate test. Managed settings may restrict local
loading, so an unavailable candidate leaves validation pending.
[Claude local plugin development](https://code.claude.com/docs/en/plugins/create)

## Autopilot

`/software-engineering-team:autopilot` is the entry; its skill holds the
procedure. Claude facts: the `UserPromptExpansion` hook records the user's
typed `on` as the arming record; the `PreToolUse` hook on `AskUserQuestion`
denies questions only in the session whose `CLAUDE_CODE_SESSION_ID` armed the
grant; `autopilot_guard.py` denies `Write` and `Edit` into
`.agentrof/agent-marketplace/.runtime/autopilot/`; `--recover-transport` uses
the native `asyncRewake` watcher.
