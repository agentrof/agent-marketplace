# Host Contract

- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start. It never
  registers global state or blocks project work. `vault_hook.py` protects only
  compiler-owned fields and immediately checks changed vault documents.
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
- Vault hooks are workflow-integrity controls for host-dispatched tool effects,
  not a same-user operating-system sandbox. A process deliberately targeting
  hook scratch or recovery files has the user's filesystem authority; host
  sandboxing and OS permissions remain the security boundary.
- A canonical entry with `project_scope: external` does not require project
  setup, workspace configuration or a Git repository.
- Present canonical choice gates through `AskUserQuestion`, preserving options,
  recommendation and tradeoffs.
- Under switch `owner_gates` at `two_fixed_gates`, ask the owner inside a
  Delivery only at gate A, gate B, an early gate or for an at-once class, and
  queue every other question in the Delivery's `User Decisions`. Present each
  gate through `AskUserQuestion` in calls of at most four questions, with the
  recommended option first and the tradeoffs in the option descriptions.
- When the canonical workflow says `spawn`, use the
  `software-engineering-team:<agent-id>` identity and wait for every required
  agent before synthesis. Never run overlapping writers concurrently.
- Under switch `review_panels` at `lens_panel`, run a review panel's lens
  readers in parallel: spawn every reader of the panel in one message, then
  wait for all of them before triage.
- Under switch `implementation_schedule` at `parallel_lanes_v1`, writers
  overlap only when their approved lane scopes intersect. Spawn every lane of
  an Item phase in one message, then wait for all of them before the next
  phase.
- Under switch `execution_planning` at `single_source_bundle`, start every
  reader of an execution-plan bundle together: spawn them in one message, then
  wait for all of them before triage.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots and local project memory.
- Role agents use the package's `auto` execution profile: `model` and, when
  set, `effort` per role tier. Every build also ships the `-lens` variants of
  the read-only document reviewers on the `lens` tier, `sonnet` at effort
  `high`; only review panels under switch `review_panels` at `lens_panel`
  spawn them, and the reviewers themselves keep their own tier. The package
  cannot switch the profile per user. To run every role on the main
  conversation's model, the user sets
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` in the `env` block of their settings
  (Claude Code 2.1.257 or later). A
  role's `effort` overrides the session level but not
  `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the session and every
  subagent. Both settings reach every subagent in the session, not only this
  team.
- Every build also ships the `-mechanical` variants of the document writers
  `product-owner`, `qa-engineer`, `devops-engineer` and `solution-architect`
  on the `mechanical` tier, `sonnet` at effort `high`. Only switch
  `mechanical_pass_tier` at `mechanical` spawns them, for a pass that applies
  the fixes a review names; the writers themselves keep their own tier, and no
  review, re-check or calibration runs on a variant.
- Under switch `delivery_path` at `light_when_eligible`, an eligible Delivery
  is planned inside `/delivery-plan` with one owner gate, presented through
  `AskUserQuestion`, and handed over to `/deliver DLV-###`; `/execution-plan
  DLV-###` stays for a plan revision and for a Delivery that left the light
  path, so the public entries do not change.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.
