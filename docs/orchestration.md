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
dependency context. The instructions live in the `challenge-review` and
`code-review` references `switch-review_loop-blocking_delta.md`.

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
