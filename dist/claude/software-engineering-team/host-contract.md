# Host Contract

- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start, and reports the
  project's rendered role agents whose stamp is not the installed plugin's. It
  never registers global state or blocks project work. `vault_hook.py`
  protects only compiler-owned fields and immediately checks changed vault
  documents.
- Every hook runs through `hook_launcher.py`, which first checks the plugin's
  Python runtime floor. Below it the launcher denies only a write to a path the
  vault hook governs and a command that runs one of the plugin's scripts; a raw
  shell write to a governed file goes unchecked, since the plugin's own flows
  are halted. When the session context reports
  `AGENT_MARKETPLACE_PYTHON: unsupported`, stop the entry and tell the user the
  reason and the fix that context gives.
- One Software Engineering Team owns a project. There is no shared project
  state service or cross-project work key.
- Resolve every canonical "packaged script" reference beneath
  `${CLAUDE_PLUGIN_ROOT}`. Invoke a machine-owned writer by passing that
  absolute script path to the active hook runtime's exact absolute Python
  executable. Bare interpreter names receive no pre-authorized writer grant.
  For backward compatibility, direct bare-Python `init` and
  `render-application` commands may retain their exact command-specific output
  only when PATH resolves to the trusted hook runtime and the owning compiler
  validates the final application; otherwise the hook restores it. Environment
  indirection and direct shebang execution are guard-only. No shared dispatcher
  or second plugin is involved.
- Native PowerShell lifecycle writes are guard-only because Claude's payload
  does not attest a PowerShell grammar/runtime identity. Use a supported
  Bash-compatible tool contract for machine-owned writers. Shared hook logic
  is portable to Windows, but native Windows writer preservation is not a
  real-host Claude claim.
- Bring an approved source handoff into the local checkout with Git itself,
  never a pull or a wrapped script: fetch it first, then run one direct Bash
  call from the checkout root with Git's absolute path, `<git> merge --no-edit
  <source>`, or, when `HEAD` already holds that source, `<git>
  restore --source=<source> --worktree -- workspace/docs/experience-design`.
  `<source>` is a committed source holding the current remote target. The
  vault hook attests only these two forms, against the open project Fence and
  its target; another merge, pull or checkout that changes compiler-owned
  Experience state is restored, and the hook's message names the form to run.
- Vault hooks are workflow-integrity controls for host-dispatched tool effects,
  not a same-user operating-system sandbox. A process deliberately targeting
  hook scratch or recovery files has the user's filesystem authority; host
  sandboxing and OS permissions remain the security boundary.
- A canonical entry with `project_scope: external` does not require project
  setup, workspace configuration or a Git repository.
- Present canonical choice gates through `AskUserQuestion`, preserving options,
  recommendation and tradeoffs. `AskUserQuestion` takes at most four questions
  per call and two to four options per question, and adds a free-form `Other`
  option itself.
- Under switch `owner_gates` at `two_fixed_gates`, ask the owner inside a
  Delivery only at gate A, gate B, an early gate or for an at-once class, and
  queue every other question in the Delivery's `User Decisions`. Present each
  gate through `AskUserQuestion` in calls of at most four questions, with the
  recommended option first and the tradeoffs in the option descriptions.
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
- Under switch `review_panels` at `lens_panel`, run a review panel's lens
  readers in parallel: spawn every reader of the panel in one message, then
  wait for all of them before triage.
- Under switch `code_review_panel` at `beside_official`, run the code review
  panel beside the official reviewer: spawn the official `code-reviewer`,
  every `code-reviewer-lens` reader of the panel and QA in one message, then
  wait for all of them before the calibration and `merge-panel`.
- Under switch `implementation_schedule` at `parallel_lanes_v1`, writers
  run at the same time only when their approved lane scopes are disjoint.
  Spawn every lane that waits for no producer in one message, spawn each
  consumer lane as soon as every producer it waits for has finished, and wait
  for every lane before the coordinator's commit.
- Under switch `execution_planning` at `single_source_bundle`, start every
  reader of an execution-plan bundle together: spawn them in one message, then
  wait for all of them before triage.
- Under switch `reader_waves` at `all_at_once`, spawn every reader of a
  review or recheck wave in one message, then wait for all of them before
  triage; a
  finished Claude Code subagent holds no thread, so there is nothing to close
  first. Every wave's progress message names the wave size and how many of
  its readers run at once.
- Under switch `review_scope` at `impact_closure`, run the owning
  compiler's structural check the flow names before spawning any reader, so
  no reader is spent on a package the compiler refuses, and give each reader
  its impact closure in full and every approved, unchanged note outside it as
  its hash-bound summary.
- Under switch `review_fanout` at `per_unit`, spawn one reader per changed
  unit of a review in one message, wait for all of them, then spawn the one
  aggregator with their findings and the compilers' cross-unit facts, and
  wait for it before triage.
- Under switch `review_levels` at `concurrent_when_independent`, spawn the
  readers of every review level the flow declares independent in one
  message, then wait for all of them before triage; approval still waits for
  every level.
- Under switch `context_pack` at `role_digest`, give each spawned role the
  pack `context_pack.py build` returns for its entry, role and mode in place
  of the full required reads.
- Claude Code's prompt cache keeps a role's context for five minutes from the
  start of the model call that last used it, and a model call after a longer
  pause writes the whole context into the cache again, at more than ten times
  what a read costs. So a role that waits inside its turn never blocks one
  tool call longer than the Delivery runner's 240-second `wait` bound: it runs
  `delivery_verification.py wait` in the foreground with a Bash `timeout` of
  300000, starts a verification command that can run longer with
  `run_in_background`, and never waits through `sleep`, a polling loop or a
  longer foreground timeout.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots, local project memory and the role agents it
  renders into `.claude/agents/`: the top-level
  `software-engineering-team-<agent-id>.md` files that carry its generated
  header. It leaves every other file there and lists under `kept` one that
  carries the header, such as a user's copy of a rendered role.
- Role agents use the package's `auto` execution profile: each of the
  three role tiers runs one model of the package's model catalog, named by
  its exact model ID, at the tier's `effort`. The high tier runs
  `claude-opus-5-5` at effort `xhigh`, the medium tier `claude-opus-5-5` at
  effort `medium` and the low tier `claude-sonnet-5-5` at effort `high`; the
  catalog keeps `claude-haiku-4-5-20251001`, which no tier runs. A role
  without an `effort` would follow the session's level, `max` included.
  A pinned ID does not follow the main conversation: unlike a family alias
  such as `opus`, it keeps its version and context window when the main
  conversation runs a newer model or a `[1m]` variant of that family. Every
  build also ships the `-lens` variants of the read-only document reviewers
  on the `low` tier, `claude-sonnet-5-5` at effort `high`; only
  review panels under switch `review_panels` at `lens_panel` spawn them, and
  the reviewers themselves keep their own tier. Every build also ships
  `code-reviewer-lens` on the same tier; only the code review panel under
  switch `code_review_panel` at `beside_official` spawns it, and
  `code-reviewer` keeps its own tier. The package cannot switch the profile
  per user.
  The pinned models need Claude Code 2.1.284 or later: Sonnet 5.5 from
  2.1.284, Opus 5.5 from 2.1.280 and Haiku 4.5 from 2.1.74, the first version
  that honours a full model ID in agent frontmatter. To run
  every role on the main conversation's model for good, for example when the
  provider never offers a pinned model, the user sets
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` in the `env` block of the project's
  settings, or of their user settings for every project (Claude Code 2.1.257
  or later). A role's `effort` overrides the session level but not
  `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the session and every
  subagent. Both settings reach every subagent in the session, not only this
  team.
- A project sets a tier's model and effort and a role's tier through
  `/configure models`, which records only overrides in
  `workspace/config.json`: `tier_models`, per host and tier a `model`, an
  exact model ID of this host's model list or `session`, and an `effort`,
  either one optional, and `role_tiers`, which moves any role, the generated
  variants included, between the high, medium and low tiers on every host.
  Setup writes neither key. A role's tier comes from `role_tiers`, else the
  package; the tier's model and effort come from `tier_models`, else the
  package's `auto` profile, and a missing key keeps the package value.
  `session` renders `model: inherit`, which runs the role on the main
  conversation's model at the tier's `effort`. Setup and refresh render
  every role, its `-lens` and `-mechanical` variants included, into
  `.claude/agents/software-engineering-team-<agent-id>.md` with its resolved
  `model` and `effort` and report each role's tier, model, effort and
  source; `project_config.py tiers` prints the config-level effective map.
  Generated role files are never edited by hand: `/configure models` renders
  them again. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` still overrides that
  `model`, `CLAUDE_CODE_EFFORT_LEVEL` still overrides that `effort`, and a
  `maxEffortLevel` setting, the lowest across settings files, or an
  organization effort limit caps it. `/tasks` names each running role's
  model and the effort its definition sets. The first render creates
  `.claude/agents/`, which Claude Code loads only when a session starts, so
  setup asks for a restart.
- A role whose pinned model cannot run falls back to this session's model
  with a visible warning, by one strategy on both hosts:
  - Setup and refresh read this host's own model list from the binary that
    runs this session, without a model request, and judge each model a role
    is pinned to, the project's `tier_models` included: `available` when the
    list holds it and reflects the signed-in account,
    `unavailable` when the list does not hold it, since this binary cannot
    run it, and `unverified` when no list could be read or the list reflects
    no account. Every probe has a time limit, and one that fails gives
    `unverified`, never an error that stops setup.
  - The roles of an `unavailable` model run on this session's model at their
    own effort, and setup prints a warning that names the model, its tiers
    and its roles; every setup or refresh judges the model again. An
    `unverified` model keeps its pin with a note, and the run-time rule
    covers it.
  - At run time a role whose pinned model fails gets a warning that never
    blocks work and at most one start again on this session's model, only
    when the failed run changed nothing.
  - On Claude Code the binary is `CLAUDE_CODE_EXECPATH` or the executable of
    `CLAUDE_PID`, else `claude` on PATH when its version meets the highest
    `min_cli_version` of the catalog models the tiers run. The list is the
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
    the package tier map when the project holds no such file; without a pin
    both hooks stay silent.
    Neither hook fires for a spawn that passes `model` or under
    `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, so a model the user chose never
    triggers them and a re-spawn never repeats.
- Every build also ships the `-mechanical` variants of the document writers
  `product-owner`, `qa-engineer`, `devops-engineer` and `solution-architect`
  on the `low` tier, `claude-sonnet-5-5` at effort `high`. Only
  switch `mechanical_pass_tier` at `mechanical` spawns them, for a pass that
  applies the fixes a review names; the writers themselves keep their own
  tier, and no review, re-check or calibration runs on a variant. A variant
  runs its pass in a fresh context at that setting. Every variant moves its
  writer from Opus to Sonnet, the smaller model; its effort `high` is above
  the `medium` of `product-owner`, `qa-engineer` and `devops-engineer` and
  below the `xhigh` of `solution-architect`. These values are placeholders
  until the variants' frozen-task A/B sets them.
- Under switch `delivery_path` at `light_when_eligible`, an eligible Delivery
  is planned inside `/delivery-plan` with one owner gate, presented through
  `AskUserQuestion`, and handed over to `/deliver DLV-###`; `/execution-plan
  DLV-###` stays for a plan revision and for a Delivery that left the light
  path, so the public entries do not change.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.

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
  writes the runtime files with the user's filesystem authority. `vault_hook.py`
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
    queues it, and doubt queues it. `check` and the denial list each allowed
    class with its description.
  - For a question whose every class is allowed, take the recommended option,
    or for an open question the recommendation you would offer, run
    `autopilot.py record` first, then apply it and write the decision into the
    governing document where the flow records the user's answer, marked with
    the grant id. Give `record` `--class` once per class it touches; it
    refuses one that is not allowed, and refuses once the grant has ended, its
    goal included; then ask as usual.
  - Otherwise run `autopilot.py queue` and continue the work that does not
    depend on it. An at-once owner decision and the Software
    Architect's escalation clause are never taken: queue them as
    `scope_or_rule` or as the never class they touch.
  - Stop only when every remaining task waits on a queued question, then end
    with the queued list of `autopilot.py report`.
- A grant, broad delegation or time pressure authorizes running the
  prescribed flow and never waives its evidence, integration, PR record or
  closure. The `merge` class takes only the flow's own `merge-pr` on the
  recorded PR head, never a provider or Git merge of another head, and a
  Delivery is complete only when `closure-audit` returns `closed`. A quality
  exception is taken only at its explicit flow gate, never under a grant
  alone.
- When `check` reports the grant inactive, expired or completed, ask through
  `AskUserQuestion` again, queued questions first. Roles never ask the user
  and never read the grant.
- Inside a Delivery that keeps a `User Decisions` table, write an autopilot
  decision as an `answered` row whose answer states the choice and is marked
  with the grant id: `blocks` names the Items that waited by Story id,
  `wait_minutes` records how long they waited, and an approval between the
  gates names its document as `<document> revision N` in the answer. Write
  every queued question there too, as a `pending` row of class `queued` or its
  at-once class with the Items it holds in `blocks`, so the Delivery's
  refusals apply.
