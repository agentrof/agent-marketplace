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
```

The public path for implementation work is:

```text
/setup -> /requirement -> applicable stages -> /backlog-plan
       -> /delivery-plan -> /execution-plan -> /deliver
```

Entry skills are the only user-facing commands. Coordinator verbs such as
`claim-items`, `start-item`, `integrate-item`, `open-pr` and `merge-pr` are
internal operations invoked by the owning entry.

`/issue-report` is the one external, stateless support entry. It previews an
Agent Marketplace GitHub issue in chat and files only the explicitly approved
payload. It does not require setup and never reads or writes Requirement,
Delivery, workspace or runtime state as workflow state.

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
revision before a new handoff. An already-created nonterminal Delivery remains
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

## Delivery Planning

`/delivery-plan "<goal>"` creates a disposable local proposal.
`/delivery-plan DLV-###` resumes one exact reserved Delivery. Scope approval
binds the goal, exact Story set, dependency facts, Definition of Done and
target branch. The Git coordinator then reserves the Delivery by atomically
creating its Integration ref with the project Fence lease.

A Delivery is one reviewable outcome. It has no duration, estimate, cadence,
capacity or release field. Before reservation, declining or stopping leaves no
tracked file, ID, ref or provider object. After reservation, its ID,
goal-derived slug and scope hash are immutable.

## Execution Planning

`/execution-plan DLV-###` writes the exact Item topology. Each Item is the
canonical owner of:

- `execution_after` and cross-Delivery dependency bindings;
- path and contract claims;
- implementation owner and supporting responsibilities;
- role sequence;
- review and verification strategy.

`execution-plan.md` is a compiler-rendered aggregate of those Item records.
Approval is local. `publish-execution-plan` is the only network writer for the
approved plan and creates no Item worktree or execution slot. Claims begin only
after the published plan and target baseline are verified remotely.

Execution approval pins the approved Verification Contract on every Item. An
Item marked `runtime_required: true` additionally pins the approved
Environment Contract. Contract hash drift blocks Item start, resume, reopen and
takeover; Operation remains outside Requirement and product-stage routing.

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

## Fence and execution slots

The project Fence serializes cross-machine changes that must not race:
Delivery reservation, governed Delivery Governance handoff, source handoff, plan barriers,
upgrade and provider target mutation. Every mutation uses exact observed OIDs
and an atomic remote transaction. A lost lease changes no semantic ref.

Approved Delivery Governance owns `max_parallel`, the hard project-wide maximum
number of simultaneously active Items. Slot refs `001..N` enforce that limit across Deliveries, hosts and
machines. Activation advances the Item and selected Slot to the same candidate
OID. Normal Item writes advance both refs together. Pause or integration
deletes the Slot under an exact lease. A Slot is coordination evidence, not a
schedule or backlog property.

A protocol-1 Fence is accepted only by the dedicated quiescent migration path.
It must be open with every Slot free; `upgrade-fence-v1` writes the
protocol-2 Fence with the current approved Governance hash. No new Item
mutation is legal until that conversion succeeds.

## Item execution

`/deliver DLV-###` derives state from tracked files and freshly verified remote
refs. Starting an Item requires the current plan, source hashes, target,
predecessors, claims, Fence and one free Slot to pass. Before the atomic remote
transaction, the coordinator writes an ignored pending receipt. It promotes
the receipt only after Item and Slot refs both equal the accepted candidate,
then creates the Item worktree from that exact OID.

An active writer may push only while its receipt epoch matches the remote Item
and Slot lineage. Pause requires a clean worktree whose local head equals the
verified remote Item. A missing receipt denies local writer readiness. Explicit
takeover elects a new epoch on the existing Item and Slot refs; it never
allocates a second Slot.

Product and test changes stay on the Item branch. Before approving evidence,
the active Item worktree must be clean and its real `HEAD` becomes both the
reviewed and verified commit; callers cannot supply an arbitrary commit ID.
The subsequent Item push accepts only a committed change after the active
remote Item, refuses Delivery control-file changes in that product commit and
allows uncommitted changes solely to the generated Code Review and Verification
records. It creates an `item-evidence-v1` child whose direct parent is that
exact product/test commit, then advances both Item and Slot together.

Integration reads the Item, Code Review and Verification records from the
remote Item tip, not from the primary worktree. It accepts an Item only when
those records are approved/current and bind the exact direct product/test
parent. Each successful integration produces one merge commit and releases the
Slot atomically.

## Delivery Review and PR

After every Item is integrated, the aggregate gate runs the approved Verification Contract tests,
portable vault gate and Delivery checks on the exact Integration head. The one
Delivery Review records outcome, deviations, evidence, unfinished scope and
follow-up decisions. Its approval binds the reviewed Integration commit.

The coordinator publishes that Review, elects one durable PR intent and uses
the provider adapter to create or adopt exactly one PR for the Delivery. The PR
head is the Integration branch and its base is the resolved target branch.
Provider calls are elected by crash-durable receipts and are never repeated
blindly after an ambiguous result.

Closure requires provider-confirmed merge evidence for the exact reviewed
head, successful required checks and target ancestry. The merge method is a
merge commit; squash and rebase results fail closed. Release Management is not
part of Delivery closure.

## Target changes, recovery and cancellation

A disjoint target advance may be merged into Integration by the controlled
target-refresh operation. A selected-source change invalidates Scope and any
dependent plan. A path or contract overlap after claims enters the plan
revision and Item reconciliation protocol. No stale target grants a Slot,
worktree, Review approval or merge action.

Every mutating coordinator operation supports exact refetch classification:
accepted, rejected, response uncertain or repository incident. Recovery never
reconstructs semantic state from a local receipt alone. Remote records and
tracked package hashes remain authoritative.

Cancellation is an explicit action inside `/deliver DLV-###`. Its approved
intent freezes exact Story dispositions, quiesces active Items, reverts
integrated Item merges in reverse order and publishes one cancellation Review
through the same Integration branch and final PR. A scope-only or claims-free
Delivery uses `not_started` dispositions and never fabricates Item refs,
review evidence or integration bases.

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
