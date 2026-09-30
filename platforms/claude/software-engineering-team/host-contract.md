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
  overlap only when their approved lane scopes intersect. Spawn every lane
  that waits for no producer in one message, spawn each consumer lane as soon
  as every producer it waits for has finished, and wait for every lane before
  the coordinator's commit.
- Under switch `execution_planning` at `single_source_bundle`, start every
  reader of an execution-plan bundle together: spawn them in one message, then
  wait for all of them before triage.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots and local project memory.
- Role agents use the package's `auto` execution profile: each role tier
  runs one class of the package's model catalog, pinned to an exact model ID,
  and, when the tier sets one, an `effort`. A pinned ID does not follow the
  main conversation: unlike a family alias such as `opus`, it keeps its
  version and context window when the main conversation runs a newer model
  or a `[1m]` variant of that family. Every build also ships the `-lens`
  variants of the read-only document reviewers on the `lens` tier, the
  `strong` class (Sonnet) at effort `high`; only review panels under switch
  `review_panels` at `lens_panel` spawn them, and the reviewers themselves
  keep their own tier. The package cannot switch the profile per user. To run
  every role on the main conversation's model, for example when this Claude
  Code version, the provider or the organization's model allowlist does not
  offer a pinned model, the user sets `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` in
  the `env` block of the project's settings, or of their user settings for
  every project (Claude Code 2.1.257 or later). A role's `effort` overrides
  the session level but not `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level
  for the session and every subagent. Both settings reach every subagent in
  the session, not only this team.
- Every build also ships the `-mechanical` variants of the document writers
  `product-owner`, `qa-engineer`, `devops-engineer` and `solution-architect`
  on the `mechanical` tier, the `strong` class (Sonnet) at effort `high`.
  Only switch `mechanical_pass_tier` at `mechanical` spawns them, for a pass
  that applies the fixes a review names; the writers themselves keep their
  own tier, and no review, re-check or calibration runs on a variant.
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
- While a grant is active, the plugin's `PreToolUse` hook on `AskUserQuestion`
  denies the call and states this procedure. Once the user started a grant or
  a question is denied that way, run `autopilot.py check` before every choice
  gate. While it exits 0, present no question:
  - For a question of an allowed class, take the recommended option, or for
    an open question the recommendation you would offer, apply it, run
    `autopilot.py record`, and write the decision into the governing document
    where the flow records the user's answer, marked with the grant id.
  - For any other class, run `autopilot.py queue` and continue the work that
    does not depend on it. An at-once owner decision and the Software
    Architect's escalation clause are never taken: queue them as
    `scope_or_rule` or as the never class they touch.
  - Stop only when every remaining task waits on a queued question, then end
    with the queued list of `autopilot.py report`.
- When `check` reports the grant inactive, expired or completed, ask through
  `AskUserQuestion` again, queued questions first. Roles never ask the user
  and never read the grant.
