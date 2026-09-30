# Orchestration

The user starts an entry skill. The entry reads its canonical flow, checks the
project-local workspace and delegates read-only challenges to role agents when
needed. Writers are serialized; independent readers may run in parallel.

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
critical or major finding and the owning compiler checks are green. A
re-review reruns only the assignments whose blocking findings were fixed or
disproved, plus one changed-text check. A panel replaces the step's single
reviewer and never stacks on top of it: the Solution primary reviewer existed
only to stop two full panels from stacking (#293). Delivery code review and
QA, and the Experience attestation, keep one reader per role because their
machine interfaces accept one result per role; they join through a later
merge step.

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
Operation contract reviews and in Delivery code review. A minor finding is fixed
only in a writer pass that already carries a blocking fix; otherwise it becomes
a follow-up with an owner role and a revisit trigger: in the compiler-validated
`Accepted Minor Findings` table of a backlog review note or an Operation
contract, at the approval gate of Solution Design and Design System, and for
code review in result fields that `approve-item-evidence` copies into the
Item's code review record and `approve-review` lists in the Delivery Review. A
re-review reads only the open blocking findings, the changed text and its
dependency context. Before a critical or major claim gates, one fresh,
read-only calibration reader, neither the writer nor the claiming reader,
confirms it, lowers it to minor or rules it invalid, citing the text. It runs
as the claiming reviewer's role on that role's own tier, never as a `-lens`
variant. Only confirmed claims gate, each claim is ruled once, and the review
note, the code review record or the approval gate keeps every ruling. The
instructions live in the `challenge-review` and `code-review` references
`switch-review_loop-blocking_delta.md`.

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
after another in their approved order. `parallel_lanes_v1` is the one exception
to serialized writers, for an Item whose approved plan declares it: after the
Software Architect runs alone, roles whose approved lane scopes are disjoint
write at the same time in the Item's one worktree, ordered only by declared
seams. Lanes make no Git writes except intent-to-add for their own new files,
environment verbs and vault writes stay serial, and the coordinator alone
commits, once, before the candidate freeze.
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
document, section and writer role that `execution-plan/data/fact-ownership.json`
names, and every other document links it: the architect's planning output is a
transient handoff to the contract writers, and the revised contracts, the Item
records and their Stories and Test Plans take one review layer over one
hash-checked bundle manifest. Each revised contract's counterpart reads the
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
