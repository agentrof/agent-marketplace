# Host Contract

- Run mutations in Code or Default mode within the user's current authority.
  One Software Engineering Team owns one project. Resolve scripts from the selected plugin root.
- `team_guard.py` reports the Python/scripts binding and stale managed launch
  definitions. A Python `unsupported` marker blocks entry with its stated fix.
- Present genuine decision gates through `request_user_input`, at most three
  questions with two to three choices each and the recommendation first.
- Resolve each native worker through `model_class.py resolve --profile
  <light|work|review|critical> --access <read|write> --host codex`. Select its
  generated identity from `.codex/agents/`. Allocate isolated write workspaces
  before launch. Use the flow's `SWE_WORKER_BRIEF` format. The launch hook
  supplies current project rules and applicable methods. Include current user
  restrictions not yet recorded in project files. The parent owns orchestration
  and waits for required work.
- A launch file's model and effort override spawn-time settings. Pass no model
  override. The package pins need Codex 0.159.1 or later. Record requested and
  host-observed model/effort separately, leaving absent observations unverified.
- Custom agent files inherit the parent's permission boundary. A `tools` label
  or `sandbox_mode` inside a custom file cannot establish read-only execution.
  Run an independent reviewer only through a verified read-only boundary.
  If the host cannot provide one, keep independent review pending.
- The main-session default is `gpt-6.1-sol` at `medium`. This is a launch
  recommendation and changes no global configuration. `/configure models`
  manages project `class_models` overrides for the four compute profiles.
  An explicit `session` override follows the parent's model while retaining
  the selected profile's effort. `ultra` remains refused by package policy.
- Setup and refresh run the generated project check. Setup owns stamped files in `.codex/agents/` and generated instruction roots.
  It preserves authored files and reports collisions. Retired owned definitions
  are removed in the same rollback-capable transaction as new definitions.
  Instruction changes require a new session. Verify launch-file reload from
  the host before relying on changed settings.
- An unavailable model keeps its pin. After a failed launch with no changes,
  allow one retry using a separately resolved, authorized fallback profile for
  that attempt. Record the first failure and changed request. Never rewrite
  shared project settings to make a single retry inherit the session model.

- With a compatible workspace config, stale launch source, model or renderer
  needs only `generate_codex_project.py apply --scope local`, followed by
  `generate_codex_project.py check --scope local`, from this package's scripts.
  This refresh changes owned launch files and preserves project documents.
  Use full setup for missing bootstrap or a reviewed config migration. Confirm
  host reload before relying on a refreshed launch identity.

## Initial setup and project migration

After the setup entry's core apply/check, run `generate_codex_project.py`
with `inspect`, `apply` and `check`, each using `--project-root <root>
--scope all --seed-user-files`. Resolve any reported ownership choices before
apply. The full scope refreshes portable instruction roots and this host's local
launch files. Seeding creates missing user files and preserves existing content.
A local-only refresh does not update tracked instructions or seed memory.
Use the other host's generator when its local launch definitions also need refresh.
Reload the host and complete its required hook trust flow before relying on the
new launch guard.

## Graphify invocation policy

Under explicit user-wide Graphify authorization, run
`setup_project.py companions configure --companion graphify --host codex
--explicit-only --project-root <root>`. This adds
`policy.allow_implicit_invocation: false` to installed Graphify skill metadata
under the user's skill directories. It suppresses automatic Graphify selection
across projects while preserving explicit invocation, the CLI and the team's
bounded companion entry. Existing unsupported metadata is preserved for review.
The command reports exact paths and before/after hashes. Start a fresh session
and verify its catalog. Ordinary setup, refresh and package updates preserve
this user-wide policy.

## Local candidate loading

Build every host distribution from canonical source. In an isolated development
marketplace root, copy the generated Codex package and create
`.agents/plugins/marketplace.json` with a unique development marketplace name.
Use a `./`-prefixed `source.path` inside that root, retain the plugin component
name, and declare the installation/authentication policy and category.
Register with `codex plugin marketplace add <candidate-root>`, then install with
`codex plugin add software-engineering-team@<development-marketplace> --json`.
[Local marketplaces](https://developers.openai.com/plugins/build/plugins),
[Plugin commands](https://learn.chatgpt.com/docs/developer-commands)

Select only the intended instance in the test project's plugin settings.
Update the source directory after each rebuild and restart the desktop app.
Verify `codex plugin list --json`, the installed path and package digest, the
loaded skill, hook binding and project launch projections before accepting
the regression result. Local installs use a cached copy. Keep versioned
release caches immutable and restore the intended stable selection afterward.
If managed policy prevents isolation or the host has not loaded the candidate,
leave validation pending.
[Local loading and project selection](https://developers.openai.com/plugins/build/plugins)

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
