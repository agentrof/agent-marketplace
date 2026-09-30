# Host Contract

- Run state-changing entries only in Codex Code or Default mode. In Plan mode,
  stop before mutation and ask the user to switch modes.
- `team_guard.py` announces the installed team and the exact absolute Python
  and scripts-directory invocation binding at session start. It never
  registers global state or blocks project work. `vault_hook.py` protects only
  compiler-owned fields and immediately checks changed vault documents.
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
  recommendation and tradeoffs.
- Under switch `owner_gates` at `two_fixed_gates`, ask the owner inside a
  Delivery only at gate A, gate B, an early gate or for an at-once class, and
  queue every other question in the Delivery's `User Decisions`. Present each
  gate through `request_user_input` in calls of at most four questions, with
  the recommended option first and the tradeoffs in the option descriptions.
- When the canonical workflow says `spawn`, use the matching project-scoped
  custom agent from `.codex/agents/` and wait for every required agent before
  synthesis. Never run overlapping writers concurrently.
- Under switch `review_panels` at `lens_panel`, run a review panel's lens
  readers in parallel: start every reader of the panel before waiting on any
  of them, then wait for all of them before triage.
- Under switch `implementation_schedule` at `parallel_lanes_v1`, writers
  overlap only when their approved lane scopes intersect. Start every lane of
  an Item phase before waiting on any of them, then wait for all of them
  before the next phase.
- Under switch `execution_planning` at `single_source_bundle`, start every
  reader of an execution-plan bundle before waiting on any of them, then wait
  for all of them before triage.
- During setup or a package refresh, regenerate the host projection, run the
  generated project check and preserve authored vault files. The generator owns
  only portable instruction roots and local project memory.
- Role agents use the package's `auto` execution profile: each role tier
  runs one class of the package's model catalog, pinned to an exact model, at
  the tier's reasoning effort, and every role file carries both `model` and
  `model_reasoning_effort`. Every build also ships the `-lens` variants of the
  read-only document reviewers on the `lens` tier, the `strong` class (Sol)
  at effort `high`; only review panels under switch `review_panels` at
  `lens_panel` start them, and the reviewers themselves keep their own tier.
  When the account, the workspace or this Codex version cannot use a pinned
  model, or the user wants every role to follow the parent session, run
  `<absolute-python> <absolute-package-scripts>/generate_codex_project.py apply
  --project-root <root> --scope local --execution-profile inherit`, which
  omits both keys; `--execution-profile auto` restores the default. The
  managed agent files record the choice and later refreshes keep it.
- Every build also ships the `-mechanical` variants of the document writers
  `product-owner`, `qa-engineer`, `devops-engineer` and `solution-architect`
  on the `mechanical` tier, the `fast` class (Luna) at effort `high`. Only
  switch `mechanical_pass_tier` at `mechanical` starts them, for a pass that
  applies the fixes a review names; the writers themselves keep their own
  tier, and no review, re-check or calibration runs on a variant. For the
  tier's frozen-task A/B, apply `--execution-profile inherit` in a scratch
  copy of the project and set the candidate model and effort as the
  session's `model` and `model_reasoning_effort`.
- Under switch `delivery_path` at `light_when_eligible`, an eligible Delivery
  is planned inside `/delivery-plan` with one owner gate, presented through
  `request_user_input`, and handed over to `/deliver DLV-###`; `/execution-plan
  DLV-###` stays for a plan revision and for a Delivery that left the light
  path, so the public entries do not change.
- Delivery execution is available only through the exact public entries
  `/delivery-plan`, `/execution-plan DLV-###` and `/deliver DLV-###`.
