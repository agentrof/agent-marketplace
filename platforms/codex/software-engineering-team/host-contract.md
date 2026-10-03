# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode,
  stop before mutation and ask the user to switch modes.
- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start, and reports the
  project's role files in `.codex/agents/` whose stamp is not the installed
  plugin's. It never registers global state or blocks project work.
  `vault_hook.py` protects only compiler-owned fields and immediately checks
  changed vault documents.
- Every hook runs through `hook_launcher.py`, which first checks the plugin's
  Python runtime floor. Below it the launcher denies only a write to a path the
  vault hook governs and a command that runs one of the plugin's scripts; a raw
  shell write to a governed file goes unchecked, since the plugin's own flows
  are halted. When the session context reports
  `AGENT_MARKETPLACE_PYTHON: unsupported`, stop the entry and tell the user the
  reason and the fix that context gives.
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
  recommendation and tradeoffs. `request_user_input` takes at most three
  questions per call and two to three options per question, and Codex adds a
  free-form `Other` option itself.
- Under switch `owner_gates` at `two_fixed_gates`, ask the owner inside a
  Delivery only at gate A, gate B, an early gate or for an at-once class, and
  queue every other question in the Delivery's `User Decisions`. Present each
  gate through `request_user_input` in calls of at most three questions, with
  the recommended option first and the tradeoffs in the option descriptions.
- When the canonical workflow says `spawn`, use the matching project-scoped
  custom agent from `.codex/agents/` and wait for every required agent before
  synthesis. Never run overlapping writers concurrently.
- Under switch `review_panels` at `lens_panel`, run a review panel's lens
  readers in parallel: start every reader of the panel before waiting on any
  of them, then wait for all of them before triage.
- Under switch `code_review_panel` at `beside_official`, run the code review
  panel beside the official reviewer: start the official `code-reviewer`,
  every `code-reviewer-lens` reader of the panel and QA before waiting on any
  of them, then wait for all of them before the calibration and `merge-panel`.
- Under switch `implementation_schedule` at `parallel_lanes_v1`, writers
  run at the same time only when their approved lane scopes are disjoint.
  Start every lane that waits for no producer before waiting on any of them,
  start each consumer lane as soon as every producer it waits for has
  finished, and wait for every lane before the coordinator's commit.
- Under switch `execution_planning` at `single_source_bundle`, start every
  reader of an execution-plan bundle before waiting on any of them, then wait
  for all of them before triage.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots, local project memory and the role files it
  renders into `.codex/agents/`, each with its generated header. The last line
  of that header stamps the package version and source agent the file was
  rendered from and the digest of the model and effort the project config
  resolves for the role. When `team_guard.py` reports at session start that a
  stamp does not match the installed plugin or the project's
  `workspace/config.json`, as after a plugin update or a config change, such
  as a pull, that setup has not rendered yet, those roles still run the role
  definition, model and effort they were rendered with: the hook tells the
  user once per session to run setup, which renders them again, and the next
  start of a role reads its new file.
- Role agents use the package's `auto` execution profile: each of the
  three role tiers runs one model of the package's model catalog, named by
  its exact model ID, at the tier's reasoning effort, and every role file
  carries both `model` and `model_reasoning_effort`. The high, medium and low
  tiers all run `gpt-6.1-sol` at effort `xhigh`; the catalog keeps
  `gpt-6-luna`, which no tier runs. Every build also ships the `-lens`
  variants of the read-only document reviewers on the `low` tier,
  `gpt-6.1-sol` at effort `xhigh`; only review panels under switch
  `review_panels` at `lens_panel` start them, and the reviewers themselves
  keep their own tier.
  Every build also ships `code-reviewer-lens` on the same tier; only the code
  review panel under switch `code_review_panel` at `beside_official` starts
  it, and `code-reviewer` keeps its own tier.
  The pinned models need Codex 0.159.1 or later, the first release whose
  bundled model catalog lists `gpt-6.1-sol`; `gpt-6-luna` is bundled from
  0.157.0.
  To make every role
  follow the parent session's model, run `<absolute-python>
  <absolute-package-scripts>/generate_codex_project.py apply --project-root
  <root> --scope local --execution-profile inherit`, which omits `model` from
  every role file and keeps `model_reasoning_effort`; `--execution-profile
  auto` restores the pins and checks them again. The managed agent files
  record the choice and later refreshes keep it.
- A project sets a tier's model and effort and a role's tier through
  `/configure models`, which records only overrides in
  `workspace/config.json`: `tier_models`, per host and tier a `model`, an
  exact model ID of this host's model list or `session`, and an `effort`,
  either one optional, and `role_tiers`, which moves any role, the generated
  variants included, between the high, medium and low tiers on every host.
  Setup writes neither key. A role's tier comes from `role_tiers`, else the
  package; the tier's model and effort come from `tier_models`, else the
  package's `auto` profile, and a missing key keeps the package value.
  `session` renders the role file without a `model` key, so the role runs on
  the parent session's model at the tier's `model_reasoning_effort`. Setup
  and refresh render each role's resolved model and effort into its role
  file under the `auto` profile, keep its effort under `inherit` and a model
  fallback, and report each role's tier, model, effort and source;
  `project_config.py tiers` prints the config-level effective map. Generated
  role files are never edited by hand: `/configure models` renders them
  again. A role file's `model` and `model_reasoning_effort` take precedence
  over a spawn request and the `[agents]` defaults; `ultra` is refused for
  every tier, because a role at `ultra` starts subagents of its own.
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
  - On Codex the binary is the nearest `codex` process above setup, since
    Codex exports no variable that names it, else the target of the
    `apply_patch` alias on PATH, `CODEX_CLI_PATH` when the environment
    carries it, `codex` on PATH or the ChatGPT app's bundled CLI, and its
    version must equal `CODEX_VERSION` when that is set. The list is the
    account catalog Codex caches in `models_cache.json` in its home when the
    cache comes from this binary's version and is at most 24 hours old,
    otherwise the output of `codex debug models`, which reflects no account
    when it can only have printed the catalog bundled with the binary:
    inside the command sandbox, with a `CODEX_SANDBOX` variable set, or when
    it equals `codex debug models --bundled`. The role files of an
    `unavailable` model omit `model` and keep `model_reasoning_effort`.
    Codex has no hook event for a failed role, so the run-time warning comes
    from the coordinator rule below.
- When a role's spawn ends in `Agent errored: ...` and the error names its
  pinned model as unknown, unsupported, not found or not supported with this
  account, such as `Model not found <model>` or `The '<model>' model is not
  supported when using Codex with a ChatGPT account.`, tell the user which
  role, which model and why. Any other error, such as a rate limit whose text
  names the model, moves no role: report it as it is. Start it again only
  when the failed run changed nothing: its task manifest's `write_boundary`
  is `read_only`, as a reader's is, or the `task_inputs.py` invocation that
  derived that manifest, run again with `--expected-hash <source_hash>`,
  still passes, since the manifest binds the content of its inputs and of
  every modified or new source it covers; otherwise stop and report, and
  record no fallback.
  To start it again, run `<absolute-python>
  <absolute-package-scripts>/generate_codex_project.py apply --project-root
  <root> --scope local --inherit-model <model>`, which renders every role
  pinned to that model the same way and records it, and start the same
  `agent_type` again: Codex reads a role file each time it starts the role,
  so the new file applies without a restart. If it errors again, run that
  command with `--restore-model <model>` in place of `--inherit-model
  <model>`, which renders those roles with their pin again and drops the
  record, then stop and report both errors.
- Every build also ships the `-mechanical` variants of the document writers
  `product-owner`, `qa-engineer`, `devops-engineer` and `solution-architect`
  on the `low` tier, `gpt-6.1-sol` at effort `xhigh`. Only switch
  `mechanical_pass_tier` at `mechanical` starts them, for a pass that applies
  the fixes a review names; the writers themselves keep their own tier, and
  no review, re-check or calibration runs on a variant. Every variant runs
  its writer's own model and effort, so it changes only the fresh context of
  the pass. These values are placeholders until the variants' frozen-task A/B
  sets them. For that A/B, apply `--execution-profile inherit` in a scratch
  copy of the project, set the candidate model as the session's `model`, and
  set a candidate effort as `model_reasoning_effort` in that copy's
  `-mechanical` role files.
- Under switch `delivery_path` at `light_when_eligible`, an eligible Delivery
  is planned inside `/delivery-plan` with one owner gate, presented through
  `request_user_input`, and handed over to `/deliver DLV-###`; `/execution-plan
  DLV-###` stays for a plan revision and for a Delivery that left the light
  path, so the public entries do not change.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.

## Autopilot

- `$software-engineering-team:autopilot` is the user-invoked autopilot entry.
  Picking it from the skill menu inserts
  `$software-engineering-team:autopilot on`, a prompt that arms as it stands
  or with options such as `--for 9h` added.
  Only the user arms a grant: the plugin's `UserPromptSubmit` hook records a
  prompt that starts with that mention and an `on` command as a short-lived
  arming record, never a subagent's prompt, and `autopilot.py on`, run
  without options, refuses without that record and takes the grant's options
  only from it. Codex runs each plugin hook only after the user trusts it in
  `/hooks`: both the `UserPromptSubmit` arming hook and the `PreToolUse`
  question hook need that trust, again after an update changes them. Until
  the arming hook is trusted `on` refuses; until the question hook is, nothing
  denies `request_user_input`, and `status` still shows both as declared.
  Never start, extend or widen a grant, and never retry a refused `on` with
  options of your own. `off` and `complete`
  may end a grant at any time. `autopilot.py` is the packaged
  `skill-content/autopilot/scripts/autopilot.py`.
- The guard stops an agent that runs the packaged script, not a process that
  writes the runtime files with the user's filesystem authority. `vault_hook.py`
  narrows the gap: it denies `apply_patch` into
  `.agentrof/agent-marketplace/.runtime/autopilot/` and puts back what a shell
  command adds to the grant or the arming record there, while a command may
  still end the grant or delete the files. A command that overlaps a run of
  `autopilot.py` can undo that run's write, so run `autopilot.py` on its own,
  never in a parallel batch.
- A grant governs only the Codex session whose user typed it: the hook
  records its session id, the question hook denies only that session, and
  `on`, `record` and `queue` refuse when `CODEX_THREAD_ID` differs, as it does
  in a subagent thread. Every other session, and every Claude Code session on
  the same checkout, asks as usual; `status` and the denial name the bound
  session.
- While a grant is active, the plugin's `PreToolUse` hook on
  `request_user_input` denies the call and states this procedure. Once the
  user started a grant or a question is denied that way, run
  `autopilot.py check` before every choice gate. While it exits 0, present no
  question; when it exits 1, ask the user as usual:
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
- When `check` reports the grant inactive, expired or completed, ask through
  `request_user_input` again, queued questions first. Roles never ask the user
  and never read the grant.
- Inside a Delivery that keeps a `User Decisions` table, write an autopilot
  decision as an `answered` row whose answer states the choice and is marked
  with the grant id: `blocks` names the Items that waited by Story id,
  `wait_minutes` records how long they waited, and an approval between the
  gates names its document as `<document> revision N` in the answer. Write
  every queued question there too, as a `pending` row of class `queued` or its
  at-once class with the Items it holds in `blocks`, so the Delivery's
  refusals apply.
- The question matcher covers `request_user_input` and
  `request_user_input_async`. In Codex 0.159.x, `send_user_message_async`,
  which persistent-mode instructions name, is an older model-catalog name that
  only switches on `request_user_input_async`; no tool of that name runs.
  `send_message_to_user_async` sends the user a message without waiting for an
  answer and is not guarded: never ask a question through it under a grant.
