# Orchestration

The user starts an entry skill. The entry reads its canonical flow, checks the
project-local workspace and delegates read-only challenges to role agents when
needed. Writers are serialized, except in the two process-switch cases below:
the parallel lanes of `implementation_schedule` and the parallel contract
drafts of `execution_planning`. Independent readers may run in parallel.

Every delegated review has three explicit boundaries: the orchestrator names
the complete input file set and expected output shape before invocation; it
waits for all readers in that review layer; then the owning persona alone
triages findings and writes canonical files. Reviewer replies are transient
inputs, not project state. A later review layer starts only after the prior
layer's writes and deterministic checks are green. Each host uses its native
agent invocation and wait mechanism without changing these semantics.

Backlog, Solution Design, Design System and Operation contract reviews run as
process switch `review_panels` selects in the project's Process Policy.
`single_reader`, the default, keeps one fresh reviewer per step. `lens_panel`
runs review panels, defined in
`challenge-review/references/switch-review_panels-lens_panel.md`: fresh
read-only lens readers, one lens assignment each, read the same inputs in
parallel, and the read-only reviewer roles run as their `-lens` variants. The
owning persona merges findings that share a root cause, keeping the highest
returned severity, and the panel approves only when no lens has an open
critical or major finding and the owning compiler checks are green. A panel
changes who reads, never the review loop: at `review_loop` `current` it keeps
the step's own loop and a re-review reruns the whole panel, and at
`blocking_delta` a re-review reruns only the assignments whose blocking
findings were fixed or disproved, plus one changed-text check. A panel
replaces the step's single reviewer and never stacks on top of it: the
Solution primary reviewer existed only to stop two full panels from stacking
(#293). Delivery code review and QA, and the Experience attestation, keep one
reader per role because their machine interfaces accept one result per role;
they join through a later merge step.

The writer side of those backlog, Solution Design and Operation contract
reviews runs as process switch `mechanical_pass_tier` selects. `role_tier`,
the default, keeps every writer pass on its role's own tier. `mechanical`,
defined in
`challenge-review/references/switch-mechanical_pass_tier-mechanical.md`,
gives a pass whose findings each name their exact fix to the owning writer's
`-mechanical` variant and runs render, stamp and check steps as direct entry
commands. Triage, authoring, design, code repairs and every review, re-check
or calibration keep their roles and tiers.

Process switch `review_loop` decides how a review loop treats the findings a
review returns, with either value of `review_panels`. At `current`, the
default, every step keeps its own loop. At `blocking_delta`, only a critical or
major finding starts a round in the backlog, Solution Design, Design System and
Operation contract reviews and in Delivery code review. A minor finding is
fixed only in a writer pass that already carries a blocking fix; otherwise it
becomes a follow-up with an owner role and a revisit trigger: in the compiler-
validated `Accepted Minor Findings` table of a backlog review note or an
Operation contract, at the approval gate of Solution Design and Design System,
and for code review in result fields that `approve-item-evidence` copies into
the Item's code review record and `approve-review` lists in the Delivery
Review. A re-review reads only the open blocking findings, the changed text and
its dependency context. Before a critical or major claim gates, one fresh,
read-only calibration reader, neither the writer nor the claiming reader,
confirms it, lowers it to minor or rules it invalid, citing the text. It runs
as the claiming reviewer's role on that role's own tier, never as a `-lens`
variant. In Delivery code review it registers its rulings as a result of its
own, and `delivery_verification.py result` refuses rulings that the claiming
result carries itself. Only confirmed claims gate, and each claim is ruled
once. Every ruling is kept where a compiler reads it: a backlog review note and
an Operation contract keep a review record of the returned findings with their
ids and severities, the rulings and the id of each accepted minor finding, so
no finding that stays critical or major is accepted as minor; a Solution Design
engagement, `MASTER.md` and the code review record keep their rulings too, and
the approval gate shows them. A claim on an Operation contract is calibrated by
the counterpart of the contract the claim concerns, the DevOps Engineer for the
Verification Contract and the QA Engineer for the Environment Contract, never
by that contract's writer. The instructions live in the `challenge-review` and
`code-review` references `switch-review_loop-blocking_delta.md`.

What a backlog epic reviewer reads is process switch `review_manifest_scope`.
At `transitive`, the default, every note the epic's manifest includes expands
its own links, so the read set follows the vault. At `bounded`, links are
followed only from the epics, stories and test plans of the epic and its
dependency closure; each note they link to or cite is read with its
front-matter relations one hop further, and nothing beyond is expanded. The
root review and writer manifests keep the transitive read set, and a reviewer
that needs evidence outside its manifest reports it and is rerun with it.
`backlog-plan/references/switch-review_manifest_scope-bounded.md` defines the
read set.

A Delivery Item's implementation writers run as process switch
`implementation_schedule` selects. `sequential_v1`, the default, runs them one
after another in their approved order. For an Item whose approved plan
declares it, `parallel_lanes_v1` is one of the two exceptions to serialized
writers, beside the parallel contract drafts of `single_source_bundle`: after
the Software Architect runs alone, roles whose approved lane scopes are
disjoint write at the same time in the Item's one worktree, and a lane that
consumes a declared seam starts as soon as its own producers finish. Lanes
make no Git writes, the coordinator runs intent-to-add for the new files they
report, environment verbs and verification commands hold the Item's
environment lock one at a time, vault writes stay serial, and the coordinator
alone commits, once, before the candidate freeze.
`execution-plan/references/switch-implementation_schedule-parallel_lanes_v1.md`
and `deliver/references/switch-implementation_schedule-parallel_lanes_v1.md`
define the lanes.

Backlog planning measures story size as process switch `story_size_budget`
selects. At `off`, the default, nothing is measured or shown. At
`propose_split`, `backlog_compile.py check --json` reports each story's
measures, derived from the story and its Test Plan, against the limits the
owner sets in the Process Policy. Before the first epic review the Product
Owner proposes a split for each story over budget, and the owner accepts it or
keeps the story with a compiler-validated `Size Exceptions` row in the epic
review note. The budget is advisory: it never fails a check, blocks an
approval or rewrites a criterion, and a split moves criteria and scenarios
verbatim. Review manifests carry the measures as given facts, and
`/delivery-plan` shows them read-only.
`product-planning/references/switch-story_size_budget-propose_split.md`
defines the steps.

Execution planning writes and reviews the facts a plan needs as process switch
`execution_planning` selects. `per_document`, the default, revises and reviews
each Operation contract on its own through the Operation flow.
`single_source_bundle` writes every execution-planning fact once, in the
document, section or front-matter keys, and writer role that
`execution-plan/data/fact-ownership.json` names, and every other document
links it: the architect's planning output is a
transient handoff to the contract writers, and the revised contracts, the Item
records and their Stories and Test Plans take one review layer over one
hash-checked bundle manifest, which also binds the owner rulings in the
Delivery's `User Decisions`. Each revised contract's counterpart reads the
whole bundle in parallel, or review panel `execution_bundle` does at
`review_panels` `lens_panel`, and the bundle gets one verdict. Writer
ownership, architecture inside the active Item, pinned-only publication and the
non-runtime Environment rule do not change.
`execution-plan/references/switch-execution_planning-single_source_bundle.md`
defines the steps.

When the owner answers a Delivery's questions is process switch `owner_gates`.
At `per_step`, the default, each question is asked when it comes up. At
`two_fixed_gates` the owner decides in two choice gates: gate A presents the
scope, the execution plan, every Operation or Governance change it needs, the
decision log and the queued questions, and its approval authorizes the plan's
writes through Item start; gate B presents the Delivery Review, its follow-ups,
the decision log since gate A and the merge. Between them a question is queued
as a `pending` row of the Delivery's `User Decisions` table and only the tasks
that depend on it wait; when every remaining task does, the queue is asked as an
early gate. The Software Architect's escalation clause and the classes in
`deliver/data/owner-decision-classes.json` are still asked at once, nothing is
decided by default, and every gate groups its questions in calls of at most
four, recommended option first.
`deliver/references/switch-owner_gates-two_fixed_gates.md` defines the gates.

How many planning steps and owner gates a Delivery takes is process switch
`delivery_path`. At `standard`, the default, `/delivery-plan` plans the scope
and `/execution-plan DLV-###` the execution, each with its own gate. At
`light_when_eligible`, a Delivery the compiler finds eligible, one small Story
with no architecture or Operation impact, gets a topology-only pass of the
Software Architect inside `/delivery-plan` and one owner gate; the compiler
approvals and the Git coordinator verbs then run in order with their checks
unchanged, and `delivery_compile.py light-path-check` repeats the eligibility
check before each of them. `delivery-plan/references/switch-delivery_path-light_when_eligible.md`
and `execution-plan/references/switch-delivery_path-light_when_eligible.md`
define the path.

Requirement Flow is a linear, user-gated sequence. Each required stage commits
its approved documents before the next stage begins. The backlog compiler is
the only machine that derives backlog indexes. All durable changes are
ordinary Git changes in the project workspace.

Host adapters preserve semantics: Claude uses namespaced agents and
`AskUserQuestion`; Codex uses project-local agents and `request_user_input`.
Neither host requires another plugin.

`task_inputs.py` derives the delegated task's full read list, conditional
references, source identities, role boundary and repair obligations from the
canonical task-input policy and the owning compiler's selected project inputs.
The entry checks its source hash again before persisting results. The manifest
is disposable stdout, not project state or approval authority; required full
reads remain mandatory. External issue reporting never creates a project
manifest. Catalog validation requires every role, skill and flow to be mapped.

An approved Delivery Item may select `parallel_snapshot_v1`: implementation
finishes before Code Review and QA read the same frozen candidate concurrently.
Both must settle before the owner writes. The verification compiler binds
independent final results and raw command evidence to that candidate; diagnostic
QA cannot approve it. Missing schedule preserves the legacy sequential behavior.
The evidence child commit preserves the existing exact product-parent proof.

## Autopilot

The `/autopilot` entry gives the orchestrating session a grant the user arms
before an absence, such as a night or a weekend. While it is active the session
presents no question. A question of an allowed class takes its recommended
option: the session applies it, records it with `autopilot.py record` and
writes it where the flow records the user's answer, marked with the grant id.
Any other question is queued with `autopilot.py queue`, and the session
continues the work that does not depend on it and stops only when every
remaining task waits on a queued question. Roles are unaffected: they never
ask the user and never read the grant.

Classes are data in `skill-content/autopilot/data/autopilot-policy.json`.
`choice`, `approval_gate` and `merge` are allowed by default. `release` and
`phase_start` are excluded by default, and a grant may allow them.
`credentials`, `security_settings`, `spending`, `destructive` and
`scope_or_rule`, which holds every decision a flow asks at once, are never
delegated, and `on` refuses a grant that allows one. A grant ends at a time,
at a goal or at whichever comes first. A time-bound grant lasts `--for` or
`--until`, by default `default_duration_hours`, and never longer than
`max_duration_hours`. A goal-bound grant ends when the goal's owning compiler
reads it as terminal, or at its cap: `default_goal_cap_hours` unless the grant
gives a time. Goal kind `delivery` ends when `delivery_compile.py` reads the
Delivery as `merged` or `cancelled`, `requirement` when `requirement_compile.py`
reads the Requirement as incorporated into the approved backlog,
`resolved_no_change`, `superseded` or `withdrawn`, and `text` only through
`complete`, `off` or its cap. The session's own judgement never ends a
readable goal; `complete` ends any grant and records whether the compiler
agreed.

Only the user arms a grant. Each host's user-prompt hook records the `on`
command the user typed as a short-lived arming record; `on` refuses without
one and takes the grant's options only from it, so no agent, file, issue or
tool output can start, extend or widen a grant. A built package that cannot
show its arming hook fails closed: `on` refuses and no grant counts. Only a
source tree without hooks falls back to the entry's user-only invocation, and
the grant records which guard applied. A grant armed under another guard,
longer than `max_duration_hours` or holding a never class is inactive, and
`check` and `status` name why. A grant governs only the session, on the host,
whose user typed it: the arming hook records the session id, the question hook
denies only that session, and `on`, `record` and `queue` refuse a session whose
host or session variable differs, so a parallel session of either host asks as
usual. A pre-tool hook denies the host question tool while a grant is
active and states the procedure; a host whose question tool cannot be hooked
relies on the instructions, and `status` reports which guards run. The hooks
are workflow-integrity controls, not an operating-system sandbox: a process
that writes the runtime files directly has the user's filesystem authority.

Autopilot is an entry, not a process switch. A switch is a project rule that a
Delivery pins at scope approval, and changing it inside a pinned Delivery is
drift. A grant is personal and temporary: it turns on at night and off in the
morning in the middle of any flow, without changing a pinned policy or an
approved document. It lives only in ignored runtime state under
`.agentrof/agent-marketplace/.runtime/autopilot/`, and only its decisions reach
tracked documents. With no grant every compiler output and every task binding
stays the same: the entry delegates no task, so the task-input policy declares
it under `session_entries`, outside the task routes. In a Delivery's
`User Decisions` table under `owner_gates` at `two_fixed_gates`, an autopilot
decision is an `answered` row of class `queued` whose answer carries the
choice and the grant id, which `delivery_compile.py check` accepts as it
stands.
