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
(#293). Delivery code review, QA and the Experience attestation keep one
reader per role because their machine interfaces accept one result per role;
only under process switch `code_review_panel` does Delivery code review add a
panel beside its reader, whose merge step registers that one result.

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
as the claiming reviewer's role, or for an Operation contract or bundle claim
the counterpart of the contract it concerns, on that role's own tier, never as
a `-lens` or a `-mechanical` variant. In Delivery code review it registers its
rulings as a result of its
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

Process switch `review_rounds` caps the readers and rounds of backlog,
Solution Design, Design System and Operation contract reviews. At `current`,
the default, they follow `review_panels` and `review_loop`. At `single_pass`,
defined in `challenge-review/references/switch-review_rounds-single_pass.md`,
one reader of the step's reader role reads, never a lens panel; no
calibration reader runs, so the reader's severity stands; only a critical
finding starts a writer pass, and one re-review of the changed text closes
it. Minor and major findings become follow-ups, which `backlog_compile.py`
and `operation_compile.py` accept in `Accepted Minor Findings`, and a critical
finding still open after the one re-review goes to the owner's approval gate.
Delivery code review keeps its `review_loop` value.

Process switch `code_review_panel` decides who reads a Delivery Item's frozen
candidate in code review. At `single_reader`, the default, the official code
reviewer alone does. At `beside_official`, one fresh `code-reviewer-lens` per
lens assignment of `code-review/data/code-review-panel.json` reads the same
candidate and inputs in parallel with the official reviewer, which reviews as
before, and every reader registers its result through
`delivery_verification.py panel-result`. One fresh code reviewer on its own
tier then calibrates every critical or major panel claim, confirming it,
lowering it to minor, ruling it invalid or ruling it a duplicate of a finding
that already carries the defect, and at `review_loop` `blocking_delta` the
official claims too. `merge-panel` registers the one code review result the
machine interface accepts: the official findings and the validated panel
findings, each with its source, and a panel record of every lens claim with
its ruling and of the combined step's wall clock against the official
reviewer's, which `approve-item-evidence` keeps for every pass in the Item's
code review record.
`code-review/references/switch-code_review_panel-beside_official.md` defines
the steps.

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

Process switch `review_scope_record` collects the data that scope's default is
chosen on. At `off`, the default, nothing is measured. At `both_scopes`, an
epic reader's manifest also derives the other scope's read set and carries
both sizes, flags a transitive read set over the owner's
`transitive_source_bytes` budget so the flow offers `bounded` before any
reader starts, and `backlog_review_inputs.py --scope-findings` reports the
review's blocking findings that cite a note outside the bounded read set; the
coordinator appends both to the JSON Lines record
`<workspace>/measurements/review-scope.jsonl`, which the backlog revision
commits, and the command refuses a record inside the vault or one Git ignores.
`backlog-plan/references/switch-review_scope_record-both_scopes.md` defines
the record.

Who applies the epic reviews' findings is process switch
`remediation_writers`. At `single_writer`, the default, one Product Owner
writer reads the union of every epic's review scope and applies them all. At
`per_epic`, each epic with findings gets its own Product Owner writer, in
parallel, whose `--epic` task reads that epic's review scope at the
`review_manifest_scope` value in force and writes only that epic's notes;
afterwards one cross-epic writer, derived without `--epic`, reads and writes
only the notes the cross-epic findings name.
`backlog-plan/references/switch-remediation_writers-per_epic.md` defines the
writers.

What the root reviewer of a backlog revision reads in full is process switch
`root_review_scope`. At `full`, the default, it reads the complete package. At
`revision_delta`, its manifest names in full only the stories the revision
changed or added, their neighbours one dependency edge away, their test
plans, every epic and the review notes, and gives every other story as a
hash-bound summary in the compiler's whole-backlog graph with every
dependency edge and the root review's compiler facts. A first backlog, a delta
over the owner's `max_delta_share_percent` and a reader's stated request read
the whole package.
`backlog-plan/references/switch-root_review_scope-revision_delta.md` defines
the read set.

Who writes a backlog remediation pass's bookkeeping is process switch
`remediation_bookkeeping`. At `writer`, the default, the Product Owner writer
copies the rechecks' closure rows, reruns the expected-hash checks and writes
the preservation evidence itself. At `compiler`, the writer does the content
fixes and runs `backlog_compile.py record-rechecks` once: from the readers'
closure tables and the pinned candidate it writes each review note's
`Recheck Closures` table, the expected manifest hash of every touched scope
and a preservation report, and `--verify` is the writer's check.
`backlog-plan/references/switch-remediation_bookkeeping-compiler.md` defines
the command.

When epic reviews queue behind the host's agent limit, process switch
`epic_review_cadence` sets when the next one starts. At `wait_per_panel`, the
default, the coordinator waits for the previous review's calibration and its
own record of that review. At `overlap_calibration`, the next review starts as
soon as the previous review's last reader returns, a calibration reader starts
for each reader's claims as soon as that reader returns, the coordinator
writes only a fixed claim record at the boundary, and no writer action starts
until every review and every calibration has returned.
`backlog-plan/references/switch-epic_review_cadence-overlap_calibration.md`
defines the cadence.

Whether a business rule that computes a number must show how is process
switch `calculation_examples`. At `off`, the default, a calculation rule
follows the space standard alone. At `required`, each such rule carries its
formula or an AC with a worked example, inputs, parameters, expected output
and the output after one parameter change; the analysis challenger reports a
missing one as a major finding, and a backlog test plan takes its expected
value from it and never invents one, as
`requirements-analysis/references/switch-calculation_examples-required.md`
defines.

How many owner gates a decision that changes approved analysis documents
takes is process switch `source_decision_gate`. At `two_gates`, the default,
the owner picks a direction, and the drafted and reviewed change is approved
as exact content in a second gate. At `one_gate_when_drafted`, when the
recommended option needs no owner input, the change is drafted and reviewed
first and one gate shows it as "approve this exact change" with its content
hash from `ba_compile.py content-hash`, which `approve-package
--expected-content-hash` then holds the package to; any other answer falls
back to two gates, as
`business-analysis/references/switch-source_decision_gate-one_gate_when_drafted.md`
defines.

Which owner gate approves the Experience rebinds a source approval makes
necessary is process switch `dependent_rebind_gate`. At `separate`, the
default, binding refresh finds the stale Experience package after the source
approval and opens its own scope gate. At `with_source`,
`experience_compile.py source-impact` lists the stale packages before the
source gate and classes each as a `mechanical` rebind, when none of its notes
cites a changed source row or document, or a `semantic` one; the source gate
approves the mechanical rebinds with the source, and a semantic one keeps its
own gate, as
`business-analysis/references/switch-dependent_rebind_gate-with_source.md`
defines.

What the final snapshot review of a source-only Experience rebind reads is
process switch `rebind_review_scope`. At `full`, the default, it reads the
whole package and prototype tree. At `source_delta`, when `source-impact`
reports `source_rebind_only` and no note citing a changed source row or
document, the reviewer reads the source delta, the package notes and the
attested hashes, not the prototype tree, and still writes the full
attestation; any authored change or cited claim takes the full review, as
`experience-modeling/references/switch-rebind_review_scope-source_delta.md`
defines.

How the readers of one review or recheck wave start is process switch
`reader_waves`. At `as_slots_free`, the default, readers start as the host
lets them. At `all_at_once`, the coordinator closes every finished worker,
the writer between its passes included, and starts every reader of the wave
before waiting on any of them, the largest inputs first when the host's
thread cap is short, and each wave's progress message names its size and its
readers running at once, as
`challenge-review/references/switch-reader_waves-all_at_once.md` defines.
Each host contract states how: Codex counts every open spawned thread against
its cap, while a finished Claude Code subagent holds none.

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

What an Item's candidate has passed when Code Review and QA start is process
switch `pre_handoff_regression`. At `off`, the default, the implementation
hands it over after its own Item's tests. At `touched_suites`, the coordinator
first runs the automated suites of the earlier stories whose integrated Items
in merged Deliveries claim a path the change touches, with the Item's own Test
Plan targets, through the approved diagnostic adapter or else the full approved
test command, under the Item's environment lock, and repairs every failure
before the freeze, which refuses until that run passed on the exact candidate.
QA's first gate run reports every failing group where the approved command
allows it. `deliver/references/switch-pre_handoff_regression-touched_suites.md`
defines the run. At process switch `own_target_reuse` `spot_run`, QA's final
test run also takes the Item's own Test Plan targets from that run, but for the
ones QA names to spot-run itself, as
`deliver/references/switch-own_target_reuse-spot_run.md` defines.

When QA starts its first test command of a round is process switch
`qa_gate_order`. At `plan_first`, the default, QA plans and maps every check
first. At `gate_first`, QA starts the command in the background and plans,
maps and drafts its result while the command runs, as
`qa-verification/references/switch-qa_gate_order-gate_first.md` defines.

Whether a test run knows its groups is process switch `test_group_report`. At
`off`, the default, the runner records a test command's exit code and output
only. At `refuse_missing_groups`, where the Verification Contract declares
`test_groups` and `test_group_report`, QA's test runs and the pre-handoff run
read the group report the approved command writes, record each declared
group's status, record a run whose report lacks a group not intact, and
evidence approval refuses a final test run with a group that did not pass, as
`deliver/references/switch-test_group_report-refuse_missing_groups.md`
defines.

How QA's final test run uses the test environment is process switch
`test_engines`. At `single`, the default, it runs the approved test command
once under the Item's environment lock. At `partitioned`, where the
Verification Contract declares a partition command, its engines and a `Test
Partitions` table and the Environment Contract provisions the engines,
`run --kind test` runs every partition in its own private clone, in parallel
over the engines, longest first, and merges them into one record whose exit
code is 0 only when every partition passed intact, as
`deliver/references/switch-test_engines-partitioned.md` defines.

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

What a Test Plan scenario costs to run is process switch `test_cost_budget`.
A scenario may state `rows`, the table rows its automation target runs, and
`row_split`, `serial`, `sharded` or `grouped`, which `backlog_compile.py check`
validates at every value. At `off`, the default, nothing is flagged. At
`flag_serial_rows`, `check --json`, the review manifests and
`delivery_compile.py init` list each automation-required scenario whose rows
exceed the owner's `serial_rows` limit while its split is `serial` or absent,
and QA proposes a split before the epic review, or the owner keeps the scenario
serial with a recorded reason. The flag is advisory, as
`product-planning/references/switch-test_cost_budget-flag_serial_rows.md`
defines.

At what level a Test Plan scenario has to run is process switch `test_levels`.
A scenario may state `level`, `unit`, `fixture` or `live`, which
`backlog_compile.py check` validates at every value, and a `level_reason` for
a `fixture` or `live` one. At `off`, the default, nothing is listed. At
`declared`, QA proves a decision rule at `unit` level over every combination
of its inputs, adds one `fixture` scenario per entry point and decision
family, and keeps `live` for engine or operating-system behaviour;
`check --json` and the review manifests list each automation-required
scenario that states no level and each `fixture` or `live` one that states no
reason, the `test_cost_budget` list names each flagged scenario's level, and
the backlog reviewer asks of every `live` scenario whether its assertion is a
decision. The list is advisory, as
`product-planning/references/switch-test_levels-declared.md` defines.

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
decided by default, and every gate groups its questions in host calls no larger
than the per-call bound the host contract names, four questions on Claude Code
and three on Codex, recommended option first.
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

Host adapters preserve semantics: Claude uses project-local agents, else the
plugin's namespaced ones, and `AskUserQuestion`; Codex uses project-local
agents and `request_user_input`.
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
option: the session records it with `autopilot.py record` first, then applies
it and writes it where the flow records the user's answer, marked with the
grant id. `record` and `queue` apply the grant's end conditions first, its goal
included, and refuse once it has ended.
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
agreed. A goal whose state its compiler cannot read, such as a Delivery whose
merge state Git cannot decide or whose Review record cannot be read, is never
terminal: `on` refuses it, and `check` and `status` show it as unknown with
the reason instead of the tracked status.

Only the user arms a grant. Each host's user-prompt hook records the `on`
command the user typed as a short-lived arming record; `on` refuses without
one and takes the grant's options only from it, so no agent, file, issue or
tool output can start, extend or widen a grant through the packaged script. A
built package that cannot
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
relies on the instructions, and `status` reports which guards the package
declares, since a host can skip a declared hook it has not enabled or
trusted. The guard
stops an agent that runs the packaged script, not a process that writes the
runtime files with the user's filesystem authority: the hooks are
workflow-integrity controls, not an operating-system sandbox. `vault_hook.py`
narrows the gap. It denies a tool write into the autopilot runtime directory
and puts back what a shell command adds to the grant or the arming record,
while a command may still end the grant or delete the files; a process outside
the host's tool calls, or a command that overlaps a run of `autopilot.py`,
escapes or undoes that check.

Autopilot is an entry, not a process switch. A switch is a project rule that a
Delivery pins at scope approval, and changing it inside a pinned Delivery is
drift. A grant is personal and temporary: it turns on at night and off in the
morning in the middle of any flow, without changing a pinned policy or an
approved document. It lives only in ignored runtime state under
`.agentrof/agent-marketplace/.runtime/autopilot/`, and only its decisions reach
tracked documents. With no grant every compiler output and every task binding
stays the same: the entry delegates no task, so the task-input policy declares
it under `session_entries`, outside the task routes.

Under `owner_gates` at `two_fixed_gates`, the grant is the owner's advance
answer for its allowed classes, as the switch reference states. In the
Delivery's `User Decisions` table an autopilot decision is an `answered` row
whose answer carries the choice and the grant id; `blocks` names the Items that
waited by Story id, `wait_minutes` records their wait, and an approval between
the gates names its document as `<document> revision N` in the answer. Every
question autopilot queues is also a `pending` row, of class `queued` or its
at-once class, with the Items it holds in `blocks`, so gate A, gate B and the
Item starts refuse while it is open. `delivery_compile.py check` accepts both
rows.
