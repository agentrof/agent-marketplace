# Requirement and Delivery protocol

This document defines the current host-neutral lifecycle implemented by the
Software Engineering Team. Canonical behavior lives under
`plugins/software-engineering-team/`; supported host adapters only adapt
invocation and choice gates.

## Public entry surface

```text
/setup
/configure
/requirement
/business-analysis
/solution-design
/design-system
/experience-design
/backlog-plan
/delivery-plan
/execution-plan
/deliver
/demo
/sketch
/organize-docs
/issue-report
/autopilot
```

The public path for implementation work is:

```text
/setup -> /requirement -> applicable stages -> /backlog-plan
       -> /delivery-plan -> /execution-plan -> /deliver
```

Process switch `delivery_path` decides how many planning steps and owner gates
one Delivery takes between the backlog and `/deliver`. `standard`, the
default, is the path above. At `light_when_eligible`, a Delivery the compiler
finds eligible plans its scope and its execution inside `/delivery-plan`, and
every other Delivery keeps the standard path:

```text
standard: /delivery-plan -> scope gate -> /execution-plan DLV-### -> execution gate -> /deliver DLV-###
light:    /delivery-plan -> one gate: scope, topology, reused contract receipts -> /deliver DLV-###
```

Entry skills are the only user-facing commands. Coordinator verbs such as
`claim-items`, `start-item`, `integrate-item`, `open-pr` and `merge-pr` are
internal operations invoked by the owning entry.

`/issue-report` is the one external, stateless support entry. It previews an
Agent Marketplace GitHub issue in chat and files only the explicitly approved
payload. It does not require setup and never reads or writes Requirement,
Delivery, workspace or runtime state as workflow state.

`/autopilot` lets the user arm a bounded grant under which the orchestrating
session takes the recommended option of every question in an allowed class,
records it and queues every other question. It creates no Requirement,
Delivery or workspace state; [Orchestration](orchestration.md#autopilot)
defines its classes, goals and limits.

## Durable project truth

The only managed workspace is `workspace/`; its vault root is
`workspace/docs/`. Authored Markdown, project configuration and Git refs are
durable. The project-local `.agentrof/agent-marketplace/.runtime/` tree holds
only ignored receipts, locks, temporary indexes and worktrees. Deleting that
runtime may require a verified takeover, but it cannot change Requirement,
backlog or Delivery truth.

The tracked workflow tree is:

```text
workspace/docs/
├── requirements/req-<digits>-<slug>.md
├── business-analysis/
├── solution-design/
│   ├── landscape.md
│   ├── components/<component-id>/component.md
│   └── _generated/{component-catalog,capability-registry,topology}.json
├── system-architecture/
│   ├── architecture.md
│   ├── components/<component-id>/{component.md,modules/,interfaces/,data/,security/,runtime/,reliability/,observability/,decisions/}
│   ├── connections/
│   ├── _ledger/
│   └── _generated/
├── design-system/
├── experience-design/
│   ├── artifacts/
│   ├── _ledger/application-revisions.json
│   ├── _generated/application-registry.json
│   └── experiences/<primary-process-slug>/
│       ├── experience.md
│       ├── {journeys,flows,screens,states,transitions}/
│       ├── artifacts/
│       ├── _ledger/
│       └── _generated/
├── operation/
│   ├── verification-contract.md
│   └── environment-contract.md
├── backlog/
│   ├── backlog.md
│   ├── reviews/
│   └── epics/<epic>/stories/<story>/{story.md,test-plan.md}
└── delivery/
    ├── governance/governance.md
    ├── definition-of-done.md
    ├── process-policy.md
    └── deliveries/dlv-<digits>-<slug>/
        ├── delivery.md
        ├── execution-plan.md
        ├── delivery-review.md
        └── items/<story-id>/{item.md,code-review.md,verification.md}
```

Compiler-rendered maps and backlog views are projections, not independent
state. Front matter uses fixed English type keys; authored titles are direct
user-facing labels, while tags drive graph presentation.

## Requirement Flow

A Requirement is the single intake record for a feature, defect, technical
change or initial project change. Free text creates a local proposal. An exact
`REQ-###` resumes exactly one record. Bare invocation asks for intake unless
one eligible open Requirement can be offered without fuzzy matching.

Each Requirement records:

- normalized intent and observable outcome;
- scope and non-goals;
- evidence and constraints;
- `request_kind: feature|defect|technical`;
- `urgency: low|normal|high|critical`;
- one row for Business Analysis, Solution Design, Design System and Experience
  Design, each with `required`, `reuse` or `not_applicable`.

The Requirement approval precedes stage work. Required stages run in order,
reuse resolves to an approved current package, and not-applicable rows retain a
concrete rationale with no evidence target. A semantic edit invalidates the
Requirement approval. Stage compilers own their existing approval gates.

Experience Design completes as one aggregate handoff. Its complete
`experience-design/artifacts/` tree is an author-owned prototype workspace:
its folders, files, technologies, assets and behavior are free. The compiler
does not parse or constrain those contents. It records a safe recursive byte
inventory, artifact-tree hash and current process receipt set in the globally
current `application@rN` receipt. `application` remains reserved from process
slugs and aliases. The zero-process form is valid with an approved empty
artifact inventory.

One approved transaction covers every process create, update, rename or
retire action, prototype snapshot and its
compiler-owned open-revision and receipt state. Mutating commands serialize on
one project-scoped lock; a durable runtime journal restores the exact tracked
Experience preimage after interruption before another command proceeds.
An application-only revision leaves process receipts unchanged but still
advances the application receipt. Any approved package-set or application delta
makes the preceding application receipt non-current. Requirement Stage Results
and an existing backlog must rebind the new receipt through their normal
revision before a new handoff; Delivery Planning enforces that rebind for the
Stories it selects. An already-created nonterminal Delivery remains
bound to its exact approved backlog package and selected Story/Test Plan hashes;
an unrelated later application revision cannot invalidate those immutable
inputs. Mechanical coverage proves that selected exact refs have declared
mappings; visual fidelity and usability remain reviewer judgments.

Backlog Planning starts only when the Requirement and every applicable stage
are current. `resolved_no_change` is the only approved terminal outcome that
does not create a backlog delta. Discard, Withdraw and Supersede are explicit,
state-valid actions; a generic rejection never infers one of them.

## Backlog handoff

The backlog is one living, approved tree. Every story has one sibling test
plan, one accountable implementation role and any concrete supporting roles.
Stable criteria and rules map to Given/When/Then scenarios and required
automation targets. Epic and root reviews prove the exact child sets,
dependency direction, overlap and global coverage.

Backlog approval commits the planning package. It creates no Delivery ref,
branch, worktree, execution slot or release state. A new Delivery Planning run
consumes only approved strict-current Story, Test Plan, Requirement and
Definition of Done hashes. Once created, that Delivery verifies the same pinned
approved backlog package and selected Story/Test Plan bytes historically; it
does not silently adopt or become blocked by a later unrelated upstream
application receipt.

When the project has a Process Policy, backlog approval records the pin a
Delivery takes at scope approval, the policy's path, revision and source hash,
in `backlog.md` and in each review note it approves, so a backlog revision and
its reviews name the process switch values they ran under outside any
Delivery. The pin is a record and is never compared: a later policy revision
leaves the approved backlog current, a review approved earlier keeps its own
pin, and `begin-revision` drops the pin from the new draft until its own
approval. Without a policy nothing is recorded and the approval writes the
bytes it wrote before; a draft or invalid policy refuses the approval.

## Delivery Planning

`/delivery-plan "<goal>"` creates a disposable local proposal.
`/delivery-plan DLV-###` resumes one exact reserved Delivery. Scope approval
binds the goal, exact Story set, dependency facts, Definition of Done and
target branch. The Git coordinator then reserves the Delivery by atomically
creating its Integration ref with the project Fence lease.

When the project has a Process Policy, scope approval also pins its path,
revision and source hash in `delivery.md`, inside the scope hash, so every
Delivery names the process switch values it ran under. Without a policy
nothing is pinned and every compiler output is unchanged. A draft or invalid
policy refuses scope approval. The pin drifts like the Definition of Done's
while a new execution approval can still re-pin it, that is while the
Delivery is `scope_approved` or `execution_approved`: a policy revised,
created or removed since the pin makes `delivery_compile.py check`, and the
coordinator verbs that run it, refuse the Delivery until its execution plan is
revised and approved again, which pins the current policy and lists the
changed pin fields in `refreshed_delivery_pins`. In those phases
`refresh-target` treats the policy as a pinned input, like the Definition of
Done: it refuses with `DELIVERY_TARGET_SOURCE_VIOLATION` a target whose policy
differs from the Integration's pin, including one created or removed since.
Revise, approve and publish the execution plan first, which pins the target's
policy; the refresh then carries that policy into the Integration. From the
Delivery Review on, and for a merged or cancelled Delivery, the pin is the
record of the policy the Delivery ran under and is no longer compared, so a
policy set for the next Delivery never strands one that can no longer re-pin.
Inside a Delivery a flow reads a switch with
`process_policy.py value --switch <id> --delivery DLV-###`, which refuses a
drifted pin instead of reading the new value. Task derivation follows the same
pin: `task_inputs.py` refuses a task that names the Delivery with `--delivery`,
or reads a file of its package, while that pin has drifted, so a Delivery's
tasks never bind the switch references of a policy it did not pin. The
Delivery stays `execution_approved` while its Items run, so the pin holds
through Item execution.

Process switch `owner_gates` decides when the owner answers a Delivery's
questions. At `per_step`, the default, each is asked when it comes up and
`User Decisions` keeps free text. At `two_fixed_gates`, the scope decision joins
the execution plan and every Operation or Governance change it needs in gate A,
whose approval authorizes the plan's writes through Item start, and the
Delivery Review and the merge form gate B. Between them a question is queued in
the Delivery's `User Decisions` table unless it is of an at-once class: the
Software Architect's escalation clause or a class of
`skill-content/deliver/data/owner-decision-classes.json`. `init` writes the
table's header. From the proposal through the Review,
`delivery_compile.py check` and every verb that runs its checks refuse a row
whose id is not a unique `D-` id of at least two digits, whose class is neither
`queued` nor an at-once class, that lists fewer than two options or a
recommendation outside them, whose status is neither `pending` nor `answered`,
or that is `answered` without an answer or `pending` with one. The pinned Process Policy names the
value the Delivery ran under; a policy changed after the pin leaves the table
unchecked, and the pin checks report that drift where it blocks.

Scope approval is the handoff check for upstream bindings, and `init` runs the
same check before it renders the proposal, so a selection that cannot be handed
off is refused before the user decides on it. Every Requirement that a selected
Story `implements` must be approved and route to `backlog`; otherwise the check
names the Story, the Requirement and the router's stage, action and reason, and
the Requirement is rebound through `/requirement REQ-###` first. A superseded,
withdrawn or `resolved_no_change` Requirement cannot be rebound, so the check
names its successor when it has one and routes to a backlog revision that
re-traces the Story to a current Requirement or drops it. When a selected Story
cites `experience_refs`, the backlog must bind the globally current
`application@rN` in its compiler-owned `input_bindings`, in either planning
mode; otherwise the check names that receipt and the remedy for the backlog's
mode. A manual-mode backlog revision pins it with `--input-ref`. A
requirement-mode revision binds the root Requirement's Experience Stage
Results, rebound first through `/requirement REQ-###`, or, when that
Requirement marks Experience `not_applicable`, pins it with `--input-ref` at
`begin-revision`. A requirement-mode backlog approved before it carried
`input_bindings` is transitional: until its next revision it binds through its
root Requirement's Experience Stage Results, and binds none when that
Requirement marks Experience `not_applicable`. A backlog without a planning
mode predates application receipts and is not held to that rule.

A technical or defect Requirement may explicitly declare genuinely absent
visual input families through Backlog's `headless-v1` contract. This requires
`not_applicable` impact rows, current BA/Solution receipts, a headless
CLI/worker/scheduler topology, no relevant authored dependencies and complete
Git history without previous visual package content. The `--absent-input`
flags must be repeated and validated for every new revision. An existing
binding cannot be removed through this exception. Historical Delivery checks
retain the original approved, hash-verified committed backlog boundary even
when later work adds visual packages; new handoffs evaluate current inputs.

A Delivery is one reviewable outcome. It has no duration, estimate, cadence,
capacity or release field. Before reservation, declining or stopping leaves no
tracked file, ID, ref or provider object. After reservation, its ID,
goal-derived slug and scope hash are immutable.

Process switch `delivery_path` decides whether a small Delivery plans in one
step. At `standard`, the default, every Delivery takes the scope gate here and
the execution gate in Execution Planning. At `light_when_eligible`, `init`
reports under `delivery_path` whether the selection is eligible and names each
failed condition, and `delivery_compile.py light-path-check --delivery DLV-###`
repeats the check on the Delivery's records before each light step, with the
findings execution approval would refuse. The compiler finds a Delivery
eligible only when it selects one Story without a `software_architect` role,
whose Item declares no architecture impact with the Software Architect's own
reason, that reuses the approved current Operation contracts with no revision
open, whose dependencies a merged Delivery records integrated, and that stays
within the limits the owner set in switch `story_size_budget`; with no limit
set no Story is eligible. An eligible Delivery gets a topology-only pass inside
`/delivery-plan` and one owner gate for the scope, the Item topology and the
reused contract receipts. Then `approve-scope`, `reserve-delivery`,
`approve-execution`, `publish-execution-plan` and `claim-items` run in that
order with every check and refusal unchanged. A failed check falls back to the
standard path and keeps every approval already made; a
`DELIVERY_TRANSACTION_UNCERTAIN` from reservation or publication is resolved by
reading the refs again, not by a second gate. `approve-scope` writes one
`Delivery path:` line first in `User Decisions`, inside the scope hash: `light`
with the Item topology hash and the contract receipts it approved, or
`standard` with each failed condition. `approve-execution` keeps a light line
only while every condition holds and the plan binds that topology and those
receipts, and otherwise records `standard`. With the pinned Process Policy,
the line names the path the Delivery ran. At `owner_gates` `two_fixed_gates`
the one gate is gate A.

## Execution Planning

`/execution-plan DLV-###` writes the exact Item topology. Each Item is the
canonical owner of:

- `execution_after` and cross-Delivery dependency bindings;
- path and contract claims;
- implementation owner and supporting responsibilities;
- role sequence;
- review and verification strategy.

No owner gate shows a plan that execution approval would refuse.
`delivery_compile.py check-plan --delivery DLV-###` runs the same checks as
`approve-execution`, writes nothing and must pass before the plan gate on the
standard path and before gate A at `owner_gates` `two_fixed_gates`;
`light-path-check` reports the same findings as `plan_findings` before the
light path's one gate. In gate A, an open Operation revision that passes its
own check is listed under `pending_operation_revisions` instead of refused,
since gate A approves it before execution approval runs.

`execution-plan.md` is a compiler-rendered aggregate of those Item records.
Approval is local. `publish-execution-plan` is the only network writer for the
approved plan and creates no Item worktree or execution slot. Claims begin only
after the published plan and target baseline are verified remotely. A Story is
claimable only when no Item ref names it and no merged Delivery's package in
the Integration records it `integrated`; `claim-items` refuses either with
`DELIVERY_CLAIM_CONFLICT`, naming the Delivery that holds the Story. Item refs
are named by Story alone, so a Delivery reads an Item ref as its own only when
the tip's `Agentrof-Delivery` trailer names it. Target refresh, scope revision
and cancellation pass over another Delivery's claim and never re-issue it.

Each Item also records its implementation schedule, which process switch
`implementation_schedule` selects for new Items. An Item without the field runs
`sequential_v1`: its implementation roles write one after another in their
approved order, and an approved Item without it keeps its bytes and hashes. At
`parallel_lanes_v1`, `init` writes the schedule with empty `lane_scopes`
(`<role>:<path>`) and `lane_seams` (`<producer> -> <consumer> via <interface>`)
on every new Item. Approval then requires an implementation role besides the
Software Architect and lane scopes that are disjoint in both directions,
together equal `path_claims`, reach neither `workspace/docs`, `.git` nor
`.agentrof`, and give every implementation role except the Software Architect a
scope; seams that join two lanes, name a contract the Item claims or an
architecture record of a claimed kind, and form no cycle; and a Process Policy
that still selects the schedule. Role Sequences render the phases: the
Software Architect alone, then the lanes, each seam consumer with the producer
lanes it waits for, then Code Review and QA. A consumer lane starts as soon as
its own producers finish and waits for no other lane.
`item_plan_hash` and the plan hash cover the schedule, the scopes and the
seams. The Item keeps one worktree, Item ref, Slot and writer receipt epoch:
each lane role's task manifest bounds its write scope to its lane, lanes make
no Git writes except intent-to-add, and the coordinator alone commits the
combined change before the freeze. A schedule change follows normal execution
revision, approval and publication.

Process switch `execution_planning` decides how the facts a plan needs are
written and reviewed. At `per_document`, the default, each Operation contract
the plan needs is revised and reviewed through the Operation flow on its own.
At `single_source_bundle`, each fact is written once, in the section that
`skill-content/execution-plan/data/fact-ownership.json` names, every other
document links it, and each owner ruling gets one stable `User Decisions` id.
`delivery_compile.py bundle-manifest --delivery DLV-###` lists every contract
the plan revises or pins, every Item record with its Story and Test Plan and
the fact ownership data, each with the hash of its bytes, and names the
counterpart of every revised contract as a reader; it refuses a Delivery that
runs `per_document`. The readers start together, the manifest is recomputed
with `--expected-hash` before any finding is accepted, and the bundle replaces
each revised contract's separate counterpart review. Its one verdict is
approved only when no reader holds an open critical or major finding and the
Operation and Delivery checks are green. A revised contract that no Item pins
is recorded on the target branch and brought in with `refresh-target` before
publication, because publication still carries only pinned contracts. The
Delivery's pinned Process Policy names the value it ran under.

Execution approval pins the approved Verification Contract on every Item. An
Item marked `runtime_required: true` additionally pins the approved
Environment Contract. Contract hash drift blocks Item start, resume, reopen and
takeover; Operation remains outside Requirement and product-stage routing.
Publication carries only the pinned contracts, so an Operation revision that
no Item pins reaches the Delivery through the target branch and
`refresh-target`. `publish-execution-plan` refuses with
`DELIVERY_OPERATION_UNCARRIED` while such a revision is approved and current
and the Integration does not hold it. Publication leaves out a differing local
copy that is not approved and current, and its result names that copy as a
`not_carried` file observation.

Publication reads its leases when it runs, so they stop a concurrent publisher
but not a checkout that still holds an earlier approval. Each execution
approval therefore lists in `superseded_plan_approvals` the `source_hash` of
every earlier execution approval it revises, newest first; approval is offline
and cannot tell which of them was published, so it keeps them all. When the
Integration already holds a different published plan, `publish-execution-plan`
publishes only an approval that lists the Integration's. It publishes a pinned
Operation contract only when the Integration's copy is not approved at a later
revision or as another approval of the same revision, because a sealed Item
keeps its bindings and so the plan hash cannot show an older contract.
Otherwise it refuses with `DELIVERY_PLAN_SUPERSEDED`, names the Integration's
plan hash and contract revisions, and moves no ref: take the Delivery package
and the Operation contracts from the Integration, then revise inside
`begin-plan-revision`. The first publication and a republication of the same
plan are unchanged.

Publication never moves a sealed Item back. An Item's sealed record and its
approved review and verification records reach the Integration only through
integration or cancellation, and nothing returns them to a checkout's package.
For an Item the Integration holds integrated or cancelled, publication keeps
its review and verification records, and keeps its record unless the local
copy has the same lifecycle, base and stamp: the sealed record an approval
started from, such as one that rebinds the Item for reopen.

Closure requires successful provider checks, so execution approval also
carries the pull request check precondition that
`plugins/software-engineering-team/flows/execution-planning.md` states and
`plugins/software-engineering-team/skill-content/setup/references/ci-bootstrap.md`
defines.

## Git topology

The canonical remote refs are:

```text
refs/heads/agentrof/fence
refs/heads/agentrof/deliveries/dlv-<digits>
refs/heads/agentrof/items/<story-id-lower>
refs/heads/agentrof/slots/<three-digits>
```

Their ordinary branch names are the ref names without `refs/heads/`. Local
runtime worktrees are deterministic:

```text
.agentrof/agent-marketplace/.runtime/worktrees/dlv-<digits>/integration/
.agentrof/agent-marketplace/.runtime/worktrees/dlv-<digits>/items/<story-id-lower>/
```

The Integration branch is the Delivery's reviewed assembly branch and the
head of its single final PR. Each Item branch owns product and test changes for
one Story. Item branches merge serially into Integration after code review and
verification. The Fence and Slot refs are control refs; they have no worktree
and do not authorize product edits.

Integration and Item refs live while their Delivery is open. Once `merge-pr`
or `verify-merge` proves that the target merged the recorded PR head, it
deletes the Integration ref and the Item ref of every integrated Story in one
atomic transaction and reports each as `absent`. The target's copy of the
package records the Delivery from then on. A cancelled Story keeps its Item
ref, which keeps any other Delivery from claiming it again, and a ref that no
longer names what the merge holds stays. Apart from those, only the project
Fence stays.

## Fence and execution slots

The project Fence serializes cross-machine changes that must not race:
Delivery reservation, governed Delivery Governance handoff, source handoff, plan barriers,
upgrade and provider target mutation. Every mutation uses exact observed OIDs
and an atomic remote transaction. A lost lease changes no semantic ref.

The first reservation of a project creates the Fence on the target tip. A
later reservation takes over the Fence that earlier Deliveries left only while
the Fence is open with no barrier, source intent or target-update intent and
carries the approved Governance, and while no other Delivery's Integration ref
or Slot exists. It pushes a Fence child with a new Epoch and the target tip as
its Target together with the new Integration ref. A Delivery whose PR the
target merged cannot be reserved again. One Delivery is open at a time:
reserving beside an open Delivery needs a multi-Delivery protocol that does
not exist yet.

Approved Delivery Governance owns `max_parallel`, the hard project-wide maximum
number of simultaneously active Items. Slot refs `001..N` enforce that limit across Deliveries, hosts and
machines. Activation advances the Item and selected Slot to the same candidate
OID. Normal Item writes advance both refs together. Pause or integration
deletes the Slot under an exact lease. A Slot is coordination evidence, not a
schedule or backlog property. Activation reads that limit only while the Fence
carries the approved Governance: after a Governance revision, `start-item` and
`resume-item` refuse with `DELIVERY_FENCE_GOVERNANCE` until `apply-governance`
binds the revision to the Fence.

A protocol-1 Fence is accepted only by the dedicated quiescent migration path.
It must be open with every Slot free; `upgrade-fence-v1` writes the
protocol-2 Fence with the current approved Governance hash. No new Item
mutation is legal until that conversion succeeds. Every other coordinator verb
that reads or writes the Fence refuses a protocol-1 Fence before any ref or
provider write with `DELIVERY_PROTOCOL_UNSUPPORTED` and names
`upgrade-fence-v1`; a Fence record of neither protocol refuses with
`DELIVERY_FENCE_CORRUPT`.

## Item execution

`/deliver DLV-###` derives state from tracked files and freshly verified remote
refs. Starting an Item requires the current plan, source hashes, target,
predecessors, claims, Fence and one free Slot to pass. Each Item its
`execution_after` names must be integrated first: its remote Item tip records
`integrated` and the Integration contains that exact tip. Each Story its
`waits_for` names is met the same way against this Delivery's Integration,
which for a Story of another Delivery means that Delivery merged into the
target and this one refreshed onto it. The `Agentrof-Delivery` trailer of the
Story's Item tip names the package that records its status. Once that
Delivery merged and dropped the Item ref, its package answers instead: the
Story is met when the package in this Integration records it `integrated` and
this Integration holds the merge of that Delivery's recorded PR, and it is
still on its way when only the target holds them. A Story no
Delivery has claimed, or one its Delivery cancelled, fails closed with
`DELIVERY_DEPENDENCY_UNMET`, and the refusal names a backlog revision as the
way out. Before the atomic remote transaction, the coordinator writes an
ignored pending receipt. It promotes the receipt only after Item and Slot refs
both equal the accepted candidate, then creates the Item worktree from that
exact OID. A rejected transaction deletes the pending receipt once the
refetched Item ref is absent or still holds the tip the activation leased: the
transaction is atomic, so that proves no ref changed, whatever the Slot holds.

An active writer may push only while its receipt epoch matches the remote Item
and Slot lineage. Pause requires a clean worktree whose local head equals the
verified remote Item. A missing receipt denies local writer readiness. Explicit
takeover elects a new epoch on the existing Item and Slot refs; it never
allocates a second Slot. Reopen and takeover drop their pending receipt on the
same proof as activation, and a takeover the remote rejects that way gives back
the receipt and the worktree it replaced. When the push of a start, reopen or
takeover reports an error while the refetched Item and Slot refs both hold its
candidate, the transaction landed and only its response was lost: the verb
still promotes the receipt, and a later takeover gives the host its worktree.

Product and test changes stay on the Item branch. Before approving evidence,
the active Item worktree may contain only edits to its initialized Code Review
and Verification reports; its real `HEAD` becomes both the
reviewed and verified commit; callers cannot supply an arbitrary commit ID.
New Items approve an explicit `parallel_snapshot_v1` verification schedule.
An absent schedule preserves `sequential_v1` for legacy approved Items without
altering their bytes or hashes. Parallel Code Review and QA bind one committed
candidate and its exact plan, source and instruction identities. Both readers
must settle or confirm cancellation before the owner can write. The compiler
requires independent final results, including source-bound raw command evidence;
diagnostic QA can return findings but cannot approve. A changed candidate or
contract invalidates the result. A schedule change follows normal execution
revision, approval and publication. The evidence child described below does
not itself create a new product candidate or require another verification run.
The subsequent Item push accepts only a committed change after the active
remote Item, refuses Delivery control-file changes except the current required
Architecture Item's exact compiler stamp (delta hash and refreshed source hash), and
allows uncommitted changes solely to the generated Code Review and Verification
records. It creates an `item-evidence-v1` child whose direct parent is that
exact product/test commit, then advances both Item and Slot together.
The stamp preserves all other Item bytes and requires a current, sealed,
nonempty Architecture delta within the approved component and record-kind claims.
A target refresh leaves an Item that already carries work to its writer, who
converges it by taking the refreshed Integration. The push then also accepts
that Integration commit's Delivery controls, byte for byte, and the commit as
the Item's `integration_base_commit`, when the commit lies on the Integration's
own line after the Item's previous base and the product tip contains it. The
Item record must then hold that commit's plan-owned fields and change only its
own status, stamp and base. The push also refuses a committed product or test
path outside the Item's path claims, where a claim covers its path and every
path below it. Vault paths keep the control, Architecture and projection rules
instead, and a path the product tip holds exactly as the Item's
`integration_base_commit` does is not the Item's change.

Integration reads the Item, Code Review and Verification records from the
remote Item tip, not from the primary worktree. It accepts an Item only when
those records are approved/current and bind the exact direct product/test
parent. Each successful integration produces one merge commit and releases the
Slot atomically.

## Delivery Review and PR

After every Item is integrated, the aggregate gate runs the approved Verification Contract tests,
portable vault gate and Delivery checks on the exact Integration head. The one
Delivery Review records outcome, deviations, evidence, unfinished scope and
follow-up decisions. Its author writes them into a draft before approval, and
approval keeps what was written, filling only empty sections and the
compiler-owned navigation. Its approval binds the reviewed Integration commit
and the authored content.

The coordinator publishes that Review, elects one durable PR intent and uses
the provider adapter to create or adopt exactly one PR for the Delivery. The PR
head is the Integration branch and its base is the resolved target branch.
Provider calls are elected by crash-durable receipts and are never repeated
blindly after an ambiguous result. When a Review is published again, the next
PR-creation intent takes over the receipt the earlier intent left, unless that
receipt still guards a PR the provider does not show: a call that started
while no exact Delivery PR is visible, or a verified PR other than the exact
Delivery PR. Such a receipt refuses the intent with `DELIVERY_PR_UNCERTAIN`.
An adoption intent names the one PR it adopts. When `open-pr` stops after that
intent and before the PR record, its next run records that PR, never creates
one, and refuses any other PR with `DELIVERY_PR_UNCERTAIN`.
The commit that records the PR URL becomes the PR head. That record is the
only source of the PR URL: its published Review names the PR, its trailers
bind it by number and hash, and `open-pr` and `merge-pr` read the URL there. A
local Review only mirrors the URL and must match it when one exists, so a
Delivery cancelled before it had a local Review still reaches the target
through its PR. The record also moves a reviewed Delivery from `review` to
`awaiting_merge` and re-renders the Delivery map; a cancelled Delivery keeps
`cancelled`. A PR that already exists when `open-pr` records it, adopted or
found by a new PR intent, first gets the Review at that intent as its body, so
the provider shows the published Review, such as a cancellation, and not an
earlier Review or a body written by hand. The body update is idempotent and
precedes the record, so a rerun after a lost response sends it again.

Closure requires provider-confirmed merge evidence for the exact reviewed
head, passing provider checks with at least one success, and target ancestry.
A skipped or neutral check passes but does not count as that success. The
merge method is a merge commit; squash and rebase results fail closed. Release
Management is not part of Delivery closure. `verify-merge` checks the same
evidence without asking the provider to change the PR: it reports a PR that
is already merged and refuses any other with `DELIVERY_MERGE_PROOF_INVALID`.

The tracked status stays `awaiting_merge` after the merge, because the target
branch receives the PR head's bytes. The Delivery map shows that tracked
status, so the Integration branch and the target branch render the same map.
The Delivery compiler derives `merged` offline from Git for a Delivery in
`awaiting_merge`, or in `review` when its PR was recorded before the record
set `awaiting_merge`. The proof is a two-parent merge that the current branch
reaches on any path, whose second parent is the Delivery's recorded PR head,
identified by its record, Delivery and intent trailers, and whose own message
carries no `Agentrof-Record` trailer. The coordinator writes that trailer on
every commit it creates, so its own two-parent commits never count, such as
the `reopen-item` commit whose second parent is the recorded PR head; a
provider merge commit or a manual `git merge` does. A later Delivery's
Integration branch therefore still sees the target's merge after a target
refresh brought it in through a second parent. The one caveat is that a manual
merge of the Integration branch into any other branch also counts as proof;
the controlled Integration refs make that unlikely. Without that proof, for
example on the Integration branch itself or after a fast-forward or squash,
the Delivery keeps its tracked status and is still checked against its current
approved sources. A merged Delivery keeps its pinned historical sources, as a
cancelled one does. Only a Delivery whose Review records its PR is derived;
any other keeps its tracked status without a Git query. When Git cannot
evaluate the proof, in a shallow clone, outside a Git checkout or after a
failed Git query, `check` and `status` fail with that finding instead of
reporting the tracked status as the answer, so a CI job that checks a
Delivery needs the full history.

A merged Delivery is closed. Every coordinator verb that would change its refs
first decides as the compiler does: when the published Review records the PR,
it asks the same proof of the freshly fetched target tip and refuses with
`DELIVERY_POST_MERGE_TRANSITION` once the target holds it; a history that
cannot answer refuses too. Once the merge dropped the Integration ref, the
proof alone decides. `open-pr` still reports the recorded PR,
`merge-pr` still verifies the merge, both reading the PR record from the
target history when the ref is gone, and the release of a plan revision or
upgrade barrier still runs, because the project Fence must not stay barred.

## Target changes, recovery and cancellation

A disjoint target advance may be merged into Integration by the controlled
target-refresh operation. A selected-source change invalidates Scope and any
dependent plan. A path or contract overlap after claims enters the plan
revision and Item reconciliation protocol. No stale target grants a Slot,
worktree, Review approval or merge action.

Every mutating coordinator operation supports exact refetch classification:
accepted, rejected, response uncertain or repository incident. A rejected
atomic push is named from the refetched refs, never from Git's wording: a moved
Fence lease, any other moved lease, or a remote that takes the same push only
without atomic support. A leased ref that holds the pushed candidate, or moved
on from a history that holds it, is the response uncertain class: the push may
have landed before its response was lost, so it reports
`DELIVERY_TRANSACTION_UNCERTAIN`, never a lease that changed no ref, and the
refs are read again before any retry. Recovery never reconstructs semantic
state from a local receipt alone. Remote records and tracked package hashes
remain authoritative.
A direct target update the remote rejected while the refetched target does not
contain its carrier head changed nothing: that is the zero-effect proof, so its
elected call returns to `prepared` in the target update receipt, and the host
that holds the receipt can reauthorize a fresh attempt or abort the handoff.
Any other rejected or unanswered update call is never repeated blindly. An
authorization whose Fence push the remote rejected keeps its prepared receipt
unless the refetched Fence never took the authorizing candidate, so a Fence
that may carry the intent still has the receipt its handoff needs.

Cancellation is an explicit action inside `/deliver DLV-###`. Its approved
intent freezes exact Story dispositions, quiesces active Items, reverts every
Item integration merge on the Integration's own line in reverse order and
publishes one cancellation Review through the same Integration branch and
final PR. That includes the integration a reopened Item left and each earlier
integration of an Item integrated again, so such a Story is
`integrated_reverted` whatever its Item ref now records. A scope-only or
claims-free Delivery uses `not_started` dispositions and never fabricates Item refs,
review evidence or integration bases. A cancellation is final: a Delivery
whose published status is already `cancelled` refuses another cancellation,
any invalidation of its cancellation Review, a publication of its execution
plan, a revision of its scope, a target refresh and a claim of its Items with
`DELIVERY_CANCELLATION_INVALID`, so that Review still reaches the target
through the PR. Each of these verbs reads the status the Integration records,
because a cancellation writes it there alone and a checkout's `delivery.md`
keeps the status it had.

## Setup and package upgrade

Setup uses one convergent `inspect`, `apply`, `check` planner. It preserves
authored Markdown, retained closed-schema configuration values and user-owned Obsidian
settings while converging package-owned files and policy keys. Open Deliveries
are quiesced behind the Fence before a package upgrade changes Delivery
contracts. The detailed sequence is defined in
[upgrade-protocol.md](upgrade-protocol.md).

## Mechanical contracts

The machine-readable Delivery contract set is under
`plugins/software-engineering-team/skill-content/deliver/data/`:

- `delivery-document-contract.json`
- `delivery-control-record-contract.json`
- `delivery-protocol-1.json`
- `delivery-provider-contract.json`
- `delivery-receipt-contract.json`
- `delivery-result-contract.json`

`tools/validate.py` enforces the closed file set and contract identities.
`tools/build_distributions.py` builds all registered hosts from the same canonical
sources and embeds the same protocol capability. `make check` is the release
gate for source validation, generated-distribution parity and all compiler,
coordinator, provider, setup and host tests.
