# Host Contract

- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start, and reports the
  project's rendered role agents whose stamp is not the installed plugin's. It
  never registers global state or blocks project work.
- Every hook runs through `hook_launcher.py`, which first checks the plugin's
  Python runtime floor. Below it the launcher reports the
  unsupported runtime at session start and blocks the autopilot entry prompt;
  every other hook passes. When the session context reports
  `AGENT_MARKETPLACE_PYTHON: unsupported`, stop the entry and tell the user the
  reason and the fix that context gives.
- One Software Engineering Team owns a project. Resolve every "packaged
  script" reference beneath `${CLAUDE_PLUGIN_ROOT}`.
- A canonical entry with `project_scope: external` does not require project
  setup, workspace configuration or a Git repository.
- Present canonical choice gates through `AskUserQuestion`, preserving options,
  recommendation and tradeoffs. `AskUserQuestion` takes at most four questions
  per call and two to four options per question, and adds a free-form `Other`
  option itself.
- When the canonical workflow says `spawn`, use the project's rendered
  `software-engineering-team-<agent-id>` identity when the session's
  available agents list it, since it runs the project's model settings, and
  otherwise the plugin's `software-engineering-team:<agent-id>` identity,
  which runs the package defaults. Each rendered file's header stamps the
  package version and source agent it was rendered from and the digest of
  the model and effort it renders. When `team_guard.py` reports at session
  start that a stamp does not match the installed plugin or the project's
  `workspace/config.json`, as after a plugin update or a config change that
  setup or a package refresh has not rendered yet, spawn the plugin's
  `software-engineering-team:<agent-id>` identity for every role until setup
  or a package refresh renders them again, since a rendered file still runs
  the role definition, model and effort it was rendered with; the hook tells
  the user once per session to refresh. On any
  other fallback, tell the user once per session why the project's model
  settings are not in effect: without
  `.claude/agents/software-engineering-team-<agent-id>.md`, setup has not
  rendered it yet; with the file, the session started before setup created
  `.claude/agents/`, so Claude Code needs a restart, or, when a restart does
  not help, a managed `strictPluginOnlyCustomization` setting keeps project
  agents from loading. Either way, wait for every required agent before
  synthesis. Never run overlapping writers concurrently.
- A background agent's completion notice reaches the top session, never the
  subagent that spawned it. So a role running as a subagent that spawns
  readers spawns them in the foreground, every reader of a wave in one
  message, which returns when all of them return; it spawns with
  `run_in_background` only work that writes its result to a file, and then
  waits with `await_outputs.py --file <output> ...` in the foreground with a
  Bash `timeout` of 300000, calling it again while it returns pending. A
  role never ends its turn to wait for a background notice.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored project files. The generator owns
  only portable instruction roots, local project memory and the role agents it
  renders into `.claude/agents/`: the top-level
  `software-engineering-team-<agent-id>.md` files that carry its generated
  header. It leaves every other file there and lists under `kept` one that
  carries the header, such as a user's copy of a rendered role.
- Role agents use the package's `auto` execution profile by purpose class.
  Each class slot runs one model of the package's model catalog, named by its
  exact model ID, at the slot's `effort`; a class that escalates has a second
  slot, `<class>_escalated`, for a task of size `l`, rigor `high` or a risk
  flag. `mechanical` runs `claude-haiku-5-5` at `high`; `routine_authoring`
  and `routine_review` `claude-sonnet-5-5` at `high`; `complex_authoring`
  `claude-sonnet-5-5` at `xhigh`; `critical_decision` `claude-opus-5-5` at
  `high`; `security_review` `claude-opus-5-5` at `xhigh` and `orchestration`
  `claude-sonnet-5-5` at `medium`, the last two never escalating. Each role
  file carries its own class's pin. Every spawn passes the `model` family
  alias and the `effort` that `model_class.py resolve` returns, which
  override the file's pins for that spawn; a per-spawn `effort` needs Claude
  Code 2.1.292 or later. The `haiku` alias resolves to Haiku 4.5 on Bedrock,
  Vertex and Foundry. A role without an `effort` would follow the session's
  level, `max` included. The package cannot switch the profile per user.
  The pinned models need Claude Code 2.1.293 or later: Haiku 5.5 from
  2.1.293, Sonnet 5.5 from 2.1.284, Opus 5.5 from 2.1.280 and Haiku 4.5,
  which no class runs, from 2.1.74. To run
  every role on the main conversation's model for good, for example when the
  provider never offers a pinned model, the user sets
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` in the `env` block of the project's
  settings, or of their user settings for every project (Claude Code 2.1.257
  or later). A role's `effort` overrides the session level but not
  `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the session and every
  subagent. Both settings reach every subagent in the session, not only this
  team.
- Rendered role agents set `omitClaudeMd: true`, so a role starts without the
  user's and the project's `CLAUDE.md` chain (Claude Code 2.1.271 or later);
  the constitution in its prompt carries the rules every role needs. Their
  `disallowedTools` remove the artifact, workflow and scheduling tools and
  every MCP server (`mcp__*`); `frontend-developer` and `qa-engineer` keep
  the browser servers and lose only the desktop, terminal and visualization
  ones. The list is
  `platforms/claude/agent-tools.json`.
- A project sets a class slot's model and effort and a role's class through
  `/configure models`, which records only overrides in
  `workspace/config.json`: `class_models`, per host and slot a `model`, an
  exact model ID of this host's model list or `session`, and an `effort`,
  either one optional, and `role_classes`, which moves any role to another
  class on every host. Setup writes
  neither key and drops the retired `tier_models` and `role_tiers`. A role's
  class comes from `role_classes`, else the package; the slot's model and
  effort come from `class_models`, else the package's `auto` profile, and a
  missing key keeps the package value. `session` renders `model: inherit`,
  which runs the role on the main conversation's model at the slot's
  `effort`, and `model_class.py resolve` then passes no `model`. Setup and
  refresh render every role into `.claude/agents/software-engineering-team-<agent-id>.md`
  with its resolved `model` and `effort` and report each role's class,
  model, effort and source; `project_config.py classes` prints the
  config-level effective map. Generated role files are never edited by
  hand: `/configure models` renders them again.
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` still overrides that `model`,
  `CLAUDE_CODE_EFFORT_LEVEL` still overrides that `effort`, and a
  `maxEffortLevel` setting, the lowest across settings files, or an
  organization effort limit caps it. `/tasks` names each running role's
  model and the effort its definition sets. The first render creates
  `.claude/agents/`, which Claude Code loads only when a session starts, so
  setup asks for a restart.
- A role whose pinned model cannot run falls back to this session's model
  with a visible warning, by one strategy on both hosts:
  - Setup and refresh read this host's own model list from the binary that
    runs this session, without a model request, and judge each model a role
    is pinned to, the project's `class_models` included: `available` when the
    list holds it and reflects the signed-in account,
    `unavailable` when the list does not hold it, since this binary cannot
    run it, and `unverified` when no list could be read or the list reflects
    no account. Every probe has a time limit, and one that fails gives
    `unverified`, never an error that stops setup.
  - The roles of an `unavailable` model run on this session's model at their
    own effort, and setup prints a warning that names the model, its class slots
    and its roles; every setup or refresh judges the model again. An
    `unverified` model keeps its pin with a note, and the run-time rule
    covers it.
  - At run time a role whose pinned model fails gets a warning that never
    blocks work and at most one start again on this session's model, only
    when the failed run changed nothing.
  - On Claude Code the binary is `CLAUDE_CODE_EXECPATH` or the executable of
    `CLAUDE_PID`, else `claude` on PATH when its version meets the highest
    `min_cli_version` of the catalog models the class slots run. The list is the
    `models` of its reply to the `initialize` control request, and it
    reflects the account only when the reply's `account.tokenSource` names a
    claude.ai login. The request runs with every hook off and loads no MCP
    server, `--strict-mcp-config` without `--mcp-config`, so it starts none
    of the project's `.mcp.json` servers, which an interactive session asks
    the user to approve first, and no user or plugin server. Claude Code
    refuses that flag while an organization's managed MCP config exists, so
    there every pin stays `unverified`. A rendered role on this session's
    model carries `model: inherit` and keeps its `effort`. Claude Code
    reports a failed role to hooks, so the plugin's hooks below add the
    run-time warning.
  - An organization model policy (`availableModels`, `deniedModels` or an
    Enterprise restriction) that blocks a pinned ID already runs the role on
    the main conversation's model, and an interactive session shows Claude
    Code's warning naming both models.
  - When a role's Agent result or completion message names its pinned model
    as unavailable or refused in Claude Code's wording, error type
    `model_not_found`, `There's an issue with the selected model`, `is not
    available on your ... deployment`, `is not available with the ... plan`
    or `don't have access to the model`, or as too new for this Claude Code,
    `does not support this model`, tell the user which role, which model and
    why. Spawn it again only when the failed run changed nothing: its task
    manifest's `write_boundary` is `read_only`, as a reader's is, or the
    `task_inputs.py` invocation that derived that manifest, run again with
    `--expected-hash <source_hash>`, still passes, since the manifest binds
    the content of its inputs and of every modified or new source it covers;
    otherwise stop and report. That spawn names the same `subagent_type`
    with `model` set to the family alias of this session's model, such as
    `opus` or `sonnet`, which runs it on this session's exact model. If it
    fails too, stop and report both errors.
  - The plugin's `PostToolUseFailure` hook on `Agent` adds a warning for the
    user and that instruction to a foreground failure; a background role
    reports its failure only in its completion message. Its `PostToolUse` hook
    warns when a role started on another model than its pin, and that run
    stands. A rendered role's pin is the `model` of its rendered file, or of
    the package class map when the project holds no such file; without a pin
    both hooks stay silent.
    Neither hook fires for a spawn that passes `model` or under
    `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, so a model the user chose never
    triggers them and a re-spawn never repeats; since every resolved spawn
    passes `model`, the hooks cover only a spawn made without the resolver.

## Autopilot

- `/software-engineering-team:autopilot` is the user-invoked autopilot entry.
  Only the user arms a grant: the plugin's `UserPromptExpansion` hook records
  an `on` command the user typed as a short-lived arming record, and
  `autopilot.py on`, run without options, refuses without that record and
  takes the grant's options only from it. Never start, extend or widen a
  grant, and never retry a refused `on` with options of your own. `off` and
  `complete` may end a grant at any time. `autopilot.py` is the packaged
  `skill-content/autopilot/scripts/autopilot.py`.
- The guard stops an agent that runs the packaged script, not a process that
  writes the runtime files with the user's filesystem authority. `autopilot_guard.py`
  narrows the gap: it denies `Write` and `Edit` into
  `.agentrof/agent-marketplace/.runtime/autopilot/` and puts back what a `Bash`
  command adds to the grant or the arming record there, while a command may
  still end the grant or delete the files. It keeps a grant or arming record
  whose bytes `autopilot.py` or its prompt hook marked as their latest write,
  so a long command that overlaps an `on` no longer undoes it.
- A grant governs only the Claude Code session whose user typed it: the hook
  records its session id, the question hook denies only that session, and
  `on`, `record` and `queue` refuse when `CLAUDE_CODE_SESSION_ID` differs.
  Every other session, and every Codex session on the same checkout, asks as
  usual; `status` and the denial name the bound session.
- While a grant is active, the plugin's `PreToolUse` hook on `AskUserQuestion`
  denies the call and states this procedure. Once the user started a grant or
  a question is denied that way, run `autopilot.py check` before every choice
  gate. While it exits 0, present no question; when it exits 1, ask the user
  as usual:
  - Classify the question by every effect of its recommended option: any
    never effect makes it never, an excluded effect the grant does not allow
    queues it, and doubt queues it with `--hold "<reason>"`. `check` and the denial list each allowed
    class with its description.
  - For a question whose every class is allowed, take the recommended option,
    or for an open question the recommendation you would offer, run
    `autopilot.py record` first, then apply it and write the decision into the
    governing document where the flow records the user's answer, marked with
    the grant id. Give `record` `--class` once per class it touches; it
    refuses one that is not allowed, and refuses once the grant has ended, its
    goal included; then ask as usual.
  - Otherwise run `autopilot.py queue` and continue the work that does not
    depend on it. If it returns `decided`, write the logged decision into the
    governing document and apply it. Keep independent excluded effects in
    separate questions so they do not hold an allowed decision. An at-once owner decision and the Software
    Architect's escalation clause are never taken: queue them as
    `scope_or_rule` or as the never class they touch.
  - Stop only when every remaining task waits on a queued question, then end
    with the queued list of `autopilot.py report`.
- An entry that ends by naming the next entry asks no question, and without
  a grant it still stops there. Under a grant, running that next entry is a
  question of class `phase_start`: `autopilot.py record` refuses it and
  `queue` keeps it queued, so stop there as before.
- A grant never waives a flow's evidence, integration or PR record. The
  `merge` class takes only the flow's own `merge` step on the recorded PR
  head, never a provider or Git merge of another head.
- When `check` reports the grant inactive, expired or completed, ask through
  `AskUserQuestion` again, queued questions first. Roles never ask the user
  and never read the grant.

When all remaining work waits on queued owner decisions, call the packaged
`autopilot.py waiting` before ending the turn. It submits one desktop notice
per queue. Report an unavailable notification backend; never claim delivery
when the command failed. The decision still belongs to the owner.

When the user arms `--recover-transport`, the packaged native watcher uses
`asyncRewake` to continue the existing main conversation or registered subagent
after a host-marked transient API failure. No separate SDK or service is installed.
Only an active grant, unchanged workspace and remaining retry allowance can wake
the conversation. CLI resume remains for separately registered exited processes;
never use it to create a replacement for a native agent.
