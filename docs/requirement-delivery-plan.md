# Requirement and Delivery Flow plan

Status: proposed product and implementation contract. This document is not
authorization to implement the plan. Release Management is deliberately out
of scope.

The historical file name is retained while this proposal is reviewed. The
implementation may rename it only after every canonical reference is updated
in the same change.

## 1. Purpose and boundaries

Agentrof will expose three product lifecycles:

```text
Requirement Flow -> Delivery Flow -> Release Management
```

- Requirement Flow begins with a user request and normally ends with an
  approved, committed backlog revision. When approved evidence proves that no
  implementable change remains, it ends with an explicit user-approved
  `resolved_no_change` Requirement instead of manufacturing a backlog story.
- Delivery Flow begins with Delivery Planning and ends when one Delivery PR
  is merged into the target branch and that merge is verified.
- Release Management begins after merge. It is a later design and must not be
  inferred from Delivery completion.

The project remains one Software Engineering Team in one project checkout.
PMO, Control Tower, SQLite, project keys, cross-project coordination,
work-order ledgers, task-attempt histories, assignee identities and global
`.agentrof` state remain retired.

Canonical product knowledge remains tracked Markdown and JSON under
`workspace/docs/`. Git commits are the audit trail. Project-local
`.agentrof/agent-marketplace/.runtime/` may contain only disposable runtime
material such as linked worktrees and hook recovery state. It is never the
semantic source of truth.

## 2. Decision contract and impact

This table consolidates the user's confirmed direction and the implementation
choices proposed to make it executable. It becomes normative only when the
whole plan is approved; until then, its costs and guardrails are review items.

| Decision | Benefit | Cost or risk | Required guardrail |
|---|---|---|---|
| Call the pre-backlog lifecycle **Requirement Flow** | One name covers feature, defect, technical and initial-project work | Existing preparation/greenfield wording becomes stale | Replace routing terminology everywhere; do not add a workflow-mode field |
| Support **stage-by-stage** and **orchestrated** invocation | Large programs can advance one stage at a time; bounded changes can run end to end | Two entry styles can drift | Both consume the same Requirement record and the same stage compilers |
| Call the execution lifecycle **Delivery Flow** and one scoped unit a **Delivery** | No Scrum, timebox or cadence implication | Existing Sprint wording must disappear | Use Delivery Goal, Delivery Scope, Delivery Planning and Delivery Review consistently |
| Give Delivery no planned duration | Agent and human elapsed time are not comparable | Traditional velocity and timebox metrics are unavailable | Store no estimate, points, planned dates, duration, capacity or velocity |
| Allow multiple Deliveries concurrently | Independent outcomes can advance in parallel | Duplicate story work and shared-surface collisions become possible | Global story claims, path/contract checks and a required WIP limit |
| Require `max_parallel` with no hidden default | Parallelism is an explicit project decision | Existing projects may not have the field | Do not block package refresh; require configuration before first Delivery activation |
| Use one final PR per Delivery in the supported lifecycle | Goal, code and evidence arrive as one reviewable unit | PRs can become too large; an externally forced wrong merge is a repository incident | Delivery Planning splits independent goals; provider preflight fails closed before activation |
| Use merge commits only in v1 | Reviewed commit ancestry survives merge and can be verified with Git | Repositories enforcing squash/rebase require policy change | Reject squash/rebase closure; design content-hash compatibility separately in the future |
| Use the real item branch as the story claim | No separate claim branch or database is needed | Item branch names must be globally deterministic | Branch name derives only from the stable story ID, never Delivery ID or mutable title |
| Use one remote **Project Fence** ref | Delivery, semantic-source, configuration and upgrade operations on otherwise unrelated refs cannot race past one another | One metadata-only branch appears in the provider branch list | `agentrof/fence` has no unique file diff or worktree and advances in the same atomic push as the protected refs |
| Use remote slot refs for global WIP | `max_parallel` works across hosts and machines | Remote access, atomic push and ref permissions are required | Couple item-branch activation and slot creation in one compare-and-swap transaction |
| Keep one evolving Delivery Review | One-hour and ten-day Deliveries use the same light structure | Process learning is not a separate retrospective artifact | Put outcome, quality, deviation and learning sections in Delivery Review |
| Keep backlog appendable during Delivery | New requirements do not stop active work | A global backlog package hash changes while work is active | Pin selected story and test-plan hashes; never use the live package hash as active scope truth |
| Freeze claimed and delivered stories | Delivery evidence cannot be invalidated silently | Requirement corrections cannot edit an active story in place | Create a superseding story or cancel before changing unmerged scope |
| Wait for cross-Delivery dependencies to merge | No stacked Delivery branches or PR chains | A dependent Delivery may be planned but cannot execute yet | Activation requires prerequisite Delivery heads in the fetched target branch |
| Keep one project-configurable designation per document type | Output terminology remains localizable | Creators and validators can drift | Fixed machine keys, paths, queries and colors; all creators read config |

Two limits are deliberate:

- Git item branches provide atomic exclusivity for one story. Path and contract
  claims expose expected collision risk between different stories, but they
  are not distributed locks. Current-ref revalidation, serialized integration
  and repeated review/test gates are the hard safety mechanisms.
- Merge-commit-only support is an explicit v1 compatibility boundary. A
  repository that cannot guarantee this merge method cannot activate a
  Delivery under this plan.

## 3. Canonical vocabulary

The following vocabulary is normative:

| Concept | Canonical term | Meaning |
|---|---|---|
| Pre-backlog lifecycle | Requirement Flow | User intent through approved backlog revision |
| Manual invocation style | Stage-by-stage | User invokes each applicable stage entry explicitly |
| Autonomous invocation style | Orchestrated | `/requirement` dispatches applicable stages |
| Scope-bound implementation unit | Delivery | One goal, exact story set and one final PR |
| Scope decision | Delivery Planning | Selects goal, exact stories, exclusions and DoD |
| Execution topology decision | Execution Planning | Defines item order, parallel waves, roles and change claims |
| Executable scope member | Delivery Item | One selected story, its branch and its evidence |
| Parallel projection | Execution Wave | Compiler-derived set of simultaneously runnable items |
| Implementation phase | Delivery Execution | Branch work, integration, review, QA and PR handling |
| Closing assessment | Delivery Review | One combined outcome, quality, acceptance and learning record |
| Global story reservation | Item claim | Existence of the deterministic remote item branch |
| Project-wide operation fence | Project Fence | One metadata-only remote ref that makes otherwise unrelated Git transitions compete on one exact OID |
| Project-wide concurrency right | Execution slot | One remote Slot ref in the configured WIP range |

Do not use `waterfall` and `agile` for the two invocation styles. They describe
development methods, not who invokes the same Requirement Flow. Do not use
Sprint, iteration, work package, work order, Delivery Lane, lane record, run
record or task ledger in canonical product language.

## 4. User-facing entry surface

The final public project surface is:

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
/issue-report
/demo
/sketch
/organize-docs
```

- Existing `/setup`, `/configure` and `/issue-report` responsibilities remain;
  they are included here because Delivery may route through `/configure`.
- `/configure DOD` is the exact public form for creating or deliberately
  revising the one project Definition of Done. `/delivery-plan` routes to this
  same flow only when DoD is missing, and prints the original explicit
  `/delivery-plan "<goal>"` command to resume after the approved DoD reaches
  target. No hidden caller/session state or Delivery ID is retained.
- Existing `/demo`, `/sketch` and `/organize-docs` remain public with their
  current authority. Their outputs may become approved Requirement intake
  evidence, but none bypasses Requirement Flow, writes backlog execution state
  or mutates Delivery coordination.
- `/requirement "<intent>"` creates a new orchestrated Requirement and
  `/requirement REQ-###` selects exactly one existing Requirement for inspect
  and any state-valid action. Bare
  `/requirement` asks for a new intent and never fuzzy-resumes or silently
  selects an open Requirement. Once an exact Requirement is selected, its
  closed action menu is derived from this exact matrix: an uncommitted local
  `draft` may continue, revise, approve or **Discard draft**; a committed
  `draft` may continue, revise, approve or **Withdraw**; an `approved`,
  unincorporated Requirement may continue, revise, **Withdraw**, **Resolve with
  no change** or **Supersede**; an `approved`, incorporated Requirement may be
  inspected or **Supersede**; a terminal Requirement is inspect-only. The old
  side of Supersede must be `approved`; a draft or terminal old record is
  rejected. The exact reviewed draft replacement must already contain the
  `supersedes` relation in its approval candidate.
  Unavailable actions are hidden and a direct invalid attempt returns
  `mutation_state: none`. `discard`, `resolve-no-change`, `withdraw` and
  `supersede` remain internal compiler verbs, not separate public entries.
- The existing five stage entries remain available for stage-by-stage
  invocation.
- `/delivery-plan "<goal>"` creates one local scope proposal;
  `/delivery-plan DLV-###` resumes or revises one exact scope-approved Delivery
  before claims exist. An unpublished proposal is intentionally not portable
  or resumable after process loss. With no argument, `/delivery-plan` asks for
  a new goal/scope intake and never guesses or resumes an existing Delivery.
- `/execution-plan DLV-###` creates or revises that exact Delivery's execution
  topology.
- `/deliver DLV-###` activates, executes, resumes, integrates, reviews and
  closes that Delivery; it presents only state-valid actions such as continue,
  pause/resume, cancellation preview or PR handoff. `/deliver DLV-### status`
  is its read-only public status form. Coordinator verbs such as `start-item`
  and `cancel-delivery` remain internal and are not independently discoverable
  host entries.
- `/delivery-lanes` is retired. Delivery Items are the executable units;
  Execution Waves are only compiler-derived scheduling views.

No project-level `workflow_mode`, `greenfield_mode`, `agile_mode` or
`waterfall_mode` is stored. A user may choose stage-by-stage invocation for
one requirement and orchestrated invocation for the next.

Minimum user journey:

```text
/requirement "Add enterprise federation"
  -> show normalized intent and four-stage impact
  -> user approves the Requirement
  -> commit/review it through the project's ordinary Git policy
  -> resume `/requirement REQ-###` after it reaches target
  -> run required stages and Backlog Planning

/delivery-plan "Enterprise SAML access"
  -> show candidate goal and exact story scope
  -> when DoD is missing, approve/commit it and resume after it reaches target
  -> user approves Delivery Scope

/execution-plan DLV-001
  -> show item graph, waves, roles and collision risks
  -> user approves; item branches claim the stories atomically

/deliver DLV-001
  -> configure the maximum simultaneously active Delivery Item count
     (`max_parallel`) if still absent
  -> after `/configure` commits that value to target, resume `/deliver DLV-001`
  -> execute/resume until Delivery Review
  -> user approves PR opening and later merge
```

For stage-by-stage work, every stage entry uses the same closed grammar:
free-form text creates a new Requirement and obtains its impact approval;
`REQ-###` resumes exactly that Requirement; no argument may propose the sole
eligible Requirement but asks when there are zero or multiple candidates.
Free-form input never fuzzy-matches an old record. `/requirement REQ-001` is
the exact selection form for open, incorporated or terminal records; only a
state-valid action is offered. A Requirement is derived as open only
when its status is `draft` or `approved` and the compiler-owned
`requirement_incorporated` predicate is false. That predicate requires one
approved root backlog review to prove the nonempty exact current-story set,
reciprocal story links and complete outcome/stage-evidence coverage.
`resolved_no_change`, `superseded` and `withdrawn` are terminal and never
route. Selection is not routing: incorporated approval remains selectable for
Supersede and terminal records remain selectable read-only. A stage entry may
propose the one open Requirement whose row marks that
stage `required` and whose stage
compiler reports incomplete. With zero or multiple candidates it asks instead
of guessing. The Requirement ID remains visible in Obsidian links and every
stage backlink, so the user does not have to remember hidden runtime context.

`/backlog-plan` has its own eligibility rule because it is not an impact-matrix
row. `/backlog-plan REQ-###` selects explicitly. Without an ID it may propose
the one approved open Requirement for which every `required` stage package is
approved/current, every `reuse` target remains compiler-valid, every
`not_applicable` row remains valid and no approved root Requirement Coverage
projection exists. Otherwise it asks or reports the missing prerequisite.

Every normal user gate has the same three non-destructive outcomes:

- **Approve** advances only the exact reviewed decision.
- **Request changes** leaves or returns the artifact to draft, records concrete
  findings and performs no downstream write.
- **Stop for now** preserves the current safe resumable state and names the
  exact entry and ID needed to continue. Stopping after remote Delivery scope
  approval is not cancellation and creates no item claim or slot.

These outcomes apply to Requirement impact, changed stages, backlog revision,
DoD, Delivery Scope, Execution Plan, Delivery Review and merge confirmation.
Destructive withdrawal, supersession and cancellation remain the separate
explicit decisions in section 10.1.

Merge confirmation specializes the same outcomes because no authored merge
artifact exists: Approve attempts the exact verified merge; Stop leaves the PR
open and Delivery `awaiting_merge`, resumable through `/deliver DLV-###`;
Request changes requires a concrete finding and performs no merge. A
product/test finding uses `reopen-item`; a review/evidence-only finding returns
Delivery Review to `changes_requested` through the exact remote
`delivery-review-invalidated-v1` transition and requires its exact
reapproval. It never silently
changes scope or opens another PR.

Terminal and irreversible proposals use the same public entries but a stricter
closed outcome contract:

- `/requirement REQ-###` **Discard draft** is legal only when the compiler
  proves the record is local, uncommitted and has no approved/committed
  downstream output. Approve removes only that exact local Requirement and its
  compiler-generated map projection; the provisional ID was never reserved.
  Request changes and Stop are byte/ref/provider no-ops. A committed draft is
  never deleted through this path.
- `/requirement REQ-###` may present **Resolve with no change** only with a
  valid evidence preview. Approve performs the single terminal compiler
  transition. Request changes and Stop perform zero file/ref/provider mutation,
  leave the Requirement in its exact prior state and return the same exact
  resume entry; a later correction after approval is a new/superseding
  Requirement, never an undo.
- **Withdraw** is offered while a committed or approved, nonterminal
  Requirement is not incorporated. Approve writes the exact reason and
  terminal state; Request changes/Stop preserve prior bytes and return the
  same resume entry. An uncommitted draft still uses Discard; a committed
  draft can never use Discard.
  **Supersede** requires one exact reviewed `draft` replacement whose
  `supersedes` relation already names the old Requirement and which has no
  approved downstream output; the old Requirement must be exact `approved`
  and may be incorporated or unincorporated. The approval preview binds that relation into
  the replacement `source_hash`. Approve atomically stamps the replacement
  `approved` and writes the old record's reciprocal `superseded_by` plus
  terminal status in one reviewed authoring change; Request changes/Stop
  mutate neither record. An already-approved relation-less replacement cannot
  be patched while preserving its approval, and the old record is never
  terminalized without the relation-bound replacement approval.
- `/deliver DLV-###` cancellation always starts with a read-only exact preview.
  Before the barrier, Approve establishes the immutable intent/barrier;
  Request changes returns findings and a new preview with zero mutation; Stop
  leaves the Delivery in its prior runnable/paused state with zero mutation.
  After the barrier, cancellation cannot be declined, resumed as normal work or
  have its intent/dispositions replaced. At its Delivery Review, Request
  changes leaves the review `changes_requested` and the barrier intact, permits
  only documentation/evidence correction and, before first publication, a
  deterministic rebuild of the unpublished reverse candidate from the same
  seals/current target. It never permits an extra product/test revert. Stop
  preserves the same fenced state; Approve alone advances to the one PR.

## 5. End-to-end behavior

```text
User request
  -> Requirement intake and impact approval
  -> either evidence-backed resolved-no-change closure
  -> or Applicable stage work and stage approvals
       -> Backlog create or begin-revision
       -> Backlog review and approval
  -> Delivery Planning and scope approval
  -> Execution Planning and approval
  -> Atomic item claims
  -> Fenced item activation and execution slots
  -> Item implementation, code review and QA
  -> Serialized integration
  -> Delivery Review and user PR approval
  -> One merge-commit PR
  -> Verified Delivery closure
```

Requirement work and Delivery work may overlap. A new Requirement may append
new backlog stories while other Deliveries run. It may not mutate a claimed or
previously delivered story.

## 6. Requirement Flow

### 6.1 Requirement identity and path

Every new backlog delta is anchored by one approved Requirement record.
Existing approved backlogs are grandfathered; their first new revision creates
Requirement records only for the new change, not retroactively for history.

Identity and path rules:

```text
ID:       REQ-001
slug:     saml-authentication
path:     workspace/docs/requirements/req-001-saml-authentication.md
map:      workspace/docs/maps/requirements.md
```

- IDs are uppercase `REQ-` plus at least three zero-padded digits.
- Slugs are immutable ASCII lower-kebab values matching
  `[a-z0-9]+(?:-[a-z0-9]+)*` and contain at most 48 characters.
- The compiler proposes the next ID from the current local vault and freshly
  fetched target. Draft authoring remains offline-capable, but no downstream
  stage may begin until the approved Requirement commit reaches target.
- Ordinary Git integration and the portable gate reject a duplicate ID. If a
  concurrent documentation change wins after the fetch, the Requirement alone
  returns to draft, receives the next ID/path and is reapproved/recommitted.
  Because downstream work is forbidden before this handoff, no approved stage
  package or backlog relation ever needs unsafe identity retargeting.
- The slug is machine terminology, not localized output prose. It is never
  renamed after approval.

### 6.2 Requirement record contract

Example front matter:

```yaml
type: requirement
id: REQ-001
title: Enterprise SAML access requirement
status: approved
owner_role: product_owner
request_kind: feature
urgency: critical
derives_from:
  - "[[issues/saml-login-failure|ISSUE-014]]"
revision: 1
approved_at_utc: "<compiler-owned UTC>"
source_hash: "<compiler-owned sha256>"
tags:
  - doc/requirement
  - status/approved
aliases:
  - REQ-001
```

`urgency` values are exactly `low`, `normal`, `high` and `critical`. The author
explains the choice under `Evidence and Constraints`; urgency is never a
free-form value containing both level and rationale.

Requirement urgency is intake pressure, not a second Delivery queue. Backlog
Planning assigns the existing story `work_kind` and priority after analysis.
When a story derives from multiple Requirements, or its implementable
classification differs from an intake, its Scope/Priority Rationale explains
the consolidation. Delivery Planning consumes only approved story fields,
dependencies and current user direction; it does not sort directly by
Requirement urgency.

Required body shape:

```text
# Enterprise SAML access <configured Requirement designation>
## Intent
## Outcome and Acceptance
## Scope and Non-Goals
## Evidence and Constraints
## Stage Impact
## Navigation
```

The first five sections are authored. Navigation is compiler-rendered.
Approval metadata lives only in front matter; there is no authored Approval
section.

`request_kind` is exactly `feature`, `defect` or `technical`.

- A hotfix is `request_kind: defect` with `urgency: critical` and a concrete
  rationale. It is not a fourth request kind.
- A dependency upgrade or refactor is normally `technical`.
- A new user capability is normally `feature`.

The Requirement record is product input, not an execution log. It stores no
attempt, event history, user identity, agent identity, host task, session,
assignee, lock, estimate or duration.

### 6.3 Stage impact matrix

Every Requirement evaluates all four knowledge stages independently:

| Stage key | Allowed disposition |
|---|---|
| `business-analysis` | `required`, `reuse`, `not_applicable` |
| `solution-design` | `required`, `reuse`, `not_applicable` |
| `design-system` | `required`, `reuse`, `not_applicable` |
| `experience-design` | `required`, `reuse`, `not_applicable` |

The body contains exactly one row per stage:

```markdown
| stage | disposition | evidence_refs | rationale |
|---|---|---|---|
| business-analysis | required | [[business-analysis/identity/space\|Identity]] | Acceptance and rule analysis must change. |
| solution-design | required | [[solution-design/engagements/identity-access\|Identity access]] | The identity-provider contract must change. |
| design-system | not_applicable |  | No token, component or visual-pattern change. |
| experience-design | reuse | [[experience-design/programs/prg-1/program\|Account access]] | Existing sign-in journey remains valid. |
```

Rules:

- `required` runs the stage and requires its normal final approval. The stage
  compiler derives whether this creates a package or revises an existing one;
  that mechanical distinction is not a Requirement decision.
- A `required` row may cite approved evidence that must be revised. It does not
  cite a future file merely to make an unresolved Wikilink appear valid.
- Every package produced or revised by a `required` stage derives from the
  Requirement. Backlinks therefore identify the exact changed output set
  without mutating the already approved Requirement record.
- `reuse` links a compiler-valid approved package and explains why it remains
  sufficient. It does not create a redundant stage approval.
- `not_applicable` has no target link and contains a concrete, requirement-
  specific reason. The compiler validates the empty evidence set and
  non-placeholder rationale; the Product Owner judges whether the rationale is
  substantively true. A redundant reason-code field is deliberately absent:
  stage plus disposition already supplies the machine classification.
- Mechanical `not_applicable` validity means only that the approved Requirement
  source hash is current and the row retains its exact legal shape, empty
  evidence set and non-placeholder rationale. A compiler never infers from new
  project evidence that the substantive decision remains true. New evidence
  requires a Product Owner revision and reapproval of the Requirement.
- Backlog Planning is not a matrix row. It is required for every implementable
  Requirement; an evidence-backed `resolved_no_change` terminal creates no
  backlog delta.
- If discovery changes a disposition, the Requirement returns to draft, its
  revision increments and the user reapproves the changed impact decision.

### 6.4 Invocation styles

Stage-by-stage invocation:

1. The first invoked stage creates or resumes the Requirement record and
   obtains impact approval.
2. Before any stage starts, require the approved Requirement commit on target.
3. Each stage entry applies the same ordered prerequisite check used by the
   orchestrator: `business-analysis`, then `solution-design`, then
   `design-system`, then `experience-design`. For every earlier row,
   `required` needs its current approved output, `reuse` needs its exact
   compiler-valid target, and `not_applicable` needs its still-valid rationale.
   A later entry cannot bypass an incomplete earlier prerequisite.
4. The selected entry performs only its own approved disposition and returns
   control.
5. The user invokes the next applicable stage when ready.
6. `/backlog-plan` consumes the same Requirement and approved stage outputs
   only after the separate eligibility rule in section 4 passes.

Orchestrated invocation:

1. `/requirement "<intake>"` creates a Requirement; `/requirement REQ-###`
   explicitly resumes one.
2. It obtains impact approval before expensive work.
3. It dispatches only `required` stages in dependency order.
4. It verifies `reuse` targets and mechanically checks `not_applicable` rows.
5. It preserves each changed stage's existing final user-approval gate.
6. Unless the Requirement closes as `resolved_no_change`, it finishes by
   creating or revising the project backlog.

The orchestrator delegates to existing stage agents and skills. It must not
reimplement Business Analysis, Solution Design, Design System, Experience
Design or Backlog Planning inside the new entry skill.

### 6.5 Requirement approval and traceability

Requirement status is `draft`, `approved`, `resolved_no_change`, `superseded`
or `withdrawn`.

- Approval is compiler-owned and binds request kind, urgency, Intent, Outcome and
  Acceptance, Scope and Non-Goals, Evidence and Constraints, all impact rows
  and source relations to `source_hash`.
- Any semantic edit removes approval and returns the record to draft.
- Every new or changed story derives from exactly one or more approved
  Requirements and retains its approved planning sources.
- Whether a Requirement has reached backlog is derived only through the shared
  compiler-owned `requirement_incorporated` predicate: a nonempty exact story
  set in the approved root backlog review, reciprocal story links and complete
  outcome/stage-evidence coverage. Routing, maps, Obsidian boards and backlog
  approval call this same predicate; no surface reimplements a weaker
  approximation and no duplicate `incorporated` state is stored.
- A correction to a Requirement already represented by a currently claimed or
  successfully delivered Story creates a replacement Requirement and a new
  correction Story; the frozen old Story/Test Plan remain byte-identical.
- Requirement supersession uses the existing exact reciprocal relation pair.
  The reviewed draft replacement already owns `supersedes`; the terminal old
  record receives `superseded_by`. Both resolve to the other Requirement. One
  compiler-owned approval transaction hashes and stamps the replacement with
  that relation and terminalizes the old record. The replacement must have no
  approved downstream output before this first supersession handoff.
- Terminalizing a Requirement never rewrites or deletes an approved stage,
  backlog or Delivery consumer. The compiler-owned root Requirement Coverage
  projection records each incorporated Requirement as
  `REQ-###|sha256:<approved source hash>|<full target commit>|<exact current story IDs>`.
  Current backlog consumers validate that binding; an active/closed Delivery
  validates the old approved Requirement blob through its pinned
  `backlog_commit`. A retained Wikilink may therefore resolve to the current
  `superseded` record without pretending that record is newly eligible input.
  A Story whose only Requirement source is now terminal is immediately
  ineligible for a new Delivery. Requirement supersession and backlog
  replacement are deliberately two approved changes: first the reciprocal
  Requirement transaction lands; then `/backlog-plan <replacement-id>` begins
  a separate revision that either proves another current approved Requirement
  source or adds a replacement Story and supersedes the obsolete unclaimed
  Story. The intermediate state is historically valid but not selectable.
  A successfully delivered old Story, or an active old Story that the user
  explicitly chooses to finish, becomes an exact `depends_on` predecessor of
  the new correction Story. The new Story derives from the replacement
  Requirement and never writes `supersedes`, because the frozen old side
  cannot receive a reciprocal link. Its Delivery dependency binding blocks
  until the exact old Delivery reaches target. If the old Delivery is
  cancelled instead, no correction Story is approved against the active
  claim: after cancellation reaches target and claim cleanup is proven, the
  never-successfully-delivered package uses the ordinary revision/supersession
  rule below. Withdrawn/superseded Requirements never seed new routing or
  incorporation.
- Before superseding an incorporated Requirement whose current coverage still
  feeds any selectable Story, the approved reciprocal Requirement candidate
  uses Project Fence `source_handoff` with kind `requirement_supersession`.
  Claim/reservation and this target handoff compete on the same Fence. If an
  open scope containing an affected Story wins first, supersession stops before
  provider/target mutation and the user completes, revises or cancels that
  Delivery under its pinned source; if handoff wins first, new reservation/
  claim remains blocked until target contains the exact terminal/replacement
  relation and Fence returns to `open`. An
  unincorporated Requirement supersession has no selectable Story source and
  needs no Delivery fence.
- An uncommitted draft with no approved downstream output may be explicitly
  discarded. Once approved or committed but before
  `requirement_incorporated` becomes true, abandonment uses compiler-owned
  `withdraw`: status becomes terminal `withdrawn`, the authored Evidence and
  Constraints section contains the user-approved reason, and historical stage
  outputs remain resolvable. Withdrawal is rejected after any approved backlog
  incorporation, claim or successful Delivery; correction then uses an
  approved superseding Requirement and backlog revision so existing story
  sources never become invalid silently. Withdrawn Requirements are excluded
  from routing, backlog incorporation and Delivery selection; `superseded` is
  reserved for a record with an explicit replacement.
- When approved evidence proves the request is duplicate, already satisfied or
  otherwise needs no implementable change, `resolve-no-change` writes terminal
  `resolved_no_change` with an exact user-approved reason and evidence links.
  It is legal only before backlog incorporation and produces no backlog
  revision or synthetic story. It is distinct from withdrawal because the
  requested outcome was evaluated rather than abandoned.
- `derives_from` on a Requirement is optional. It contains only pre-existing,
  approved intake evidence such as an issue or decision. For direct user
  intake, the Requirement record itself is the durable source; the system does
  not fabricate a second intake note or retain a hidden prompt log.

### 6.6 Git handoff

Requirement Flow uses the project's ordinary authoring branch and Git review
policy. It does not create Delivery claims or execution slots.

Before `/delivery-plan` may select its output:

- Requirement, changed stage packages and backlog revision are approved;
- the complete Requirement planning-source handoff is committed and clean;
- its commit is present in the freshly fetched target branch;
- protected repositories may use their normal documentation PR to reach that
  target branch.

The first Requirement handoff happens immediately after Requirement approval
and before any `required` stage dispatch. A numeric-ID collision therefore has
one bounded recovery surface: re-ID and reapprove the Requirement itself.

This remote requirement is stronger than the offline Phase 0 authoring gate
because multi-machine Delivery cannot safely begin from local-only knowledge.

## 7. Living backlog and revision protocol

### 7.1 One backlog, many revisions

The project keeps one backlog tree. Requirement Flow never creates a second
backlog. It either initializes the tree or runs `begin-revision` against the
approved tree.

The approved backlog remains planning truth. Delivery never writes completion
into backlog, epic, story or test-plan status fields. Effective execution and
completion are rendered from Delivery records.

### 7.2 Revision behavior

`backlog_compile.py begin-revision` must:

1. Require a valid approved backlog and clean committed Requirement planning
   sources.
2. Fetch active item branches and the target branch.
3. Build the frozen story set from active item claims and successfully closed
   Deliveries. A target-resident cancelled package is not delivered scope.
4. Increment root backlog revision and return the root to draft.
5. Permit additions and permitted edits without touching frozen packages.

Allowed changes:

- add a Requirement, epic, story or test plan;
- revise an approved story/test plan that is not currently claimed and has
  never been successfully delivered; a historical cancelled claim is eligible
  only after its cancellation package reaches target and exact claim cleanup
  succeeds;
- add a review round for an affected epic;
- add one new root backlog review round;
- update root coverage and dependency projections;
- mark an obsolete, not-currently-claimed and never-successfully-delivered
  story superseded without deleting history.

Story supersession is a closed pair transition. The new approved Story owns
`supersedes` pointing to the obsolete Story; the obsolete Story receives
`superseded_by`, changes only `status: planned -> superseded` plus its matching
status tag/relation, and remains at the same path. Its sibling approved Test
Plan remains byte-identical historical evidence and becomes nonselectable
solely because its parent Story is superseded. The new Story has its own new
sibling Test Plan. The backlog compiler writes both Story relations and the
root Requirement Coverage/current-story projection in the same approved
backlog revision. A currently claimed or successfully delivered Story can
never take this pair transition. It remains byte-identical; when the user
finishes that old Delivery, the legal correction graph is a new Story derived
from the replacement Requirement with `depends_on` pointing to the old Story
and no `supersedes` relation. A cancelled old claim becomes eligible for the
pair transition only after target-resident cancellation and exact ref cleanup.

Rejected changes:

- edit, move, delete or relabel a currently claimed story or its test plan;
- edit a story or test plan already contained in a successfully closed
  Delivery;
- change selected acceptance, dependency, role or scenario semantics in place;
- write any backlog file from a Delivery integration or item branch;
- make a root deferral silently expand requirement scope.

If active scope is wrong, the user either continues the current Delivery
unchanged or explicitly cancels it before revising. A newly discovered outcome
normally becomes a new dependent story through Requirement Flow.

Before a revision that changes any existing selectable Story/Test Plan byte or
path may reach target, the handoff compiler renders the exact source-intent
mapping and the coordinator acquires Project Fence mode `source_handoff` with
kind `backlog_revision`. It then refetches target plus every Delivery/Item ref
and recomputes the frozen set. If an Item claim won first, acquisition or the
post-acquisition scan rejects the candidate; no target PR/direct update may
become mergeable. If source handoff won first, Delivery reservation may remain
read-only but `claim-items` is blocked. Immediately before the approved source
change can update target, Fence records the irreversible target-update intent.
After target contains the exact approved mapping, Fence returns to `open` on
that target and claims revalidate the new source hashes. Append-only changes
that create new IDs and do not alter an existing selectable Story/Test byte do
not need this source fence. A raw/out-of-band target merge that bypasses the
intent is a repository incident, not a supported race outcome.

### 7.3 Incremental approval

Backlog approval becomes revision-aware:

- unchanged approved documents remain byte-identical with their original
  approval timestamps and source hashes;
- only new or changed documents receive new hashes and timestamps;
- only affected epics receive a new epic-review round;
- every revision receives one new root backlog-review round;
- the root package hash covers the complete current approved package;
- active Deliveries ignore later root package hashes and retain selected
  story/test-plan hashes from their own baseline.

The current rule requiring one identical approval timestamp across the entire
package must be retired. Exact relations, global coverage and review findings
remain mandatory.

Concurrent Requirement branches never merge two independently approved root
backlog revisions as if both were current. `begin-revision` records the exact
target backlog baseline in its compiler projection. Approval and CI require
that baseline to remain the latest approved target backlog. After one revision
lands, another branch refetches target, reapplies only its Requirement delta,
rebuilds the root revision/review/map from the combined package and obtains a
fresh backlog approval. A stale root hash, duplicate revision number or review
that omits the already merged delta fails the portable gate. No Requirement
delta is silently dropped and Requirement Flow does not add a remote lock or
second backlog.

### 7.4 Requirement-aware planning-source rules

For a new story:

- `derives_from` includes its approved Requirement record;
- it cites every approved output from a `required` stage and every `reuse`
  target when that evidence constrains the story;
- it does not copy a stage link merely because another story from the same
  Requirement needs it;
- the root backlog review proves that every approved `required` output and
  every `reuse` target for the Requirement constrains at least one of its new
  or changed stories. A produced/reused output represented by no story is a
  planning inconsistency, not a reason to add false links to every story;
- every test scenario cites at least one approved planning source, including
  the Requirement when it is the direct acceptance boundary;
- defect and technical work still require approved issue, decision or
  architecture evidence where applicable.

The root backlog review adds one exact `Requirement Coverage` projection per
Requirement in the revision: the nonempty new/current story set, mapped
outcomes, and the story or stories representing every `required` output and
`reuse` target. The compiler derives it from canonical relations and rejects
authored omissions, extras, empty incorporation and one-way links.

This replaces project-origin-based feature rules. It does not weaken
traceability.

## 8. Delivery information architecture

### 8.1 Canonical tree

The complete tree after Execution Plan approval is:

```text
workspace/docs/
├── requirements/
│   └── req-001-saml-authentication.md
├── backlog/
│   └── ...
├── delivery/
│   ├── definition-of-done.md
│   ├── deliveries/
│   │   └── dlv-001-saml-authentication/
│   │       ├── delivery.md
│   │       ├── execution-plan.md
│   │       ├── items/
│   │       │   ├── auth-01/
│   │       │   │   ├── item.md
│   │       │   │   ├── code-review.md
│   │       │   │   └── verification.md
│   │       │   └── auth-02/
│   │       │       ├── item.md
│   │       │       ├── code-review.md
│   │       │       └── verification.md
│   │       ├── delivery-review.md
│   │       └── _generated/
│   │           ├── execution-graph.md
│   │           └── evidence.json
│   └── _generated/
│       ├── board.md
│       ├── registry.json
│       └── conflict-map.md
└── maps/
    ├── requirements.md
    └── delivery.md
```

Rules:

- `delivery/definition-of-done.md` and `maps/delivery.md` live on the target
  branch.
- An active Delivery's full folder lives on its integration branch/worktree.
- The final PR adds that folder and its updated delivery map to the target
  branch.
- Every Delivery `_generated` output is ignored, disposable and reproduced by
  the compiler. Neither `execution-graph.md` nor `evidence.json` is canonical
  or committed.
- Root delivery `_generated` files are also local and ignored because they
  project remote active refs. They must never contain unresolved Wikilinks to
  files that exist only on another branch.
- On the target branch, `maps/delivery.md` links every target-resident Delivery
  package, including explicitly cancelled packages, and labels the semantic
  outcome. In an integration branch it links that target set plus its one
  active Delivery so the branch vault remains reachable. The final PR carries
  this exact map addition after updating from the latest target.
- There is no `workstreams/`, `lanes/`, `tasks/`, `runs/`, `attempts/`,
  `locks/` or event-history directory.

### 8.2 Delivery identity and naming

```text
ID:              DLV-001
slug:            saml-authentication
directory:       dlv-001-saml-authentication
execution plan:  DLV-001-EXEC
review:          DLV-001-REVIEW
item key:        AUTH-01 (the existing story ID)
item directory:  auth-01
```

- Delivery IDs are uppercase `DLV-` plus at least three zero-padded digits.
- Delivery slugs are proposed from the Delivery Goal, use the same ASCII
  lower-kebab grammar as Requirement slugs and become immutable in the same
  successful remote transaction that persists scope approval. They never
  inherit a Requirement slug because one Delivery may combine stories from
  several Requirements or epics.
- Before that transaction, ID and slug are explicitly provisional local
  proposal values. No remote draft ref is created.
- The user's pre-reservation Scope decision is allocation-independent. It
  approves the normalized Goal, outcomes, selected story/test/DoD baselines,
  exclusions and dependency preconditions, not the provisional numeric ID,
  path, aliases or identity-derived hashes. The coordinator retains this
  decision only inside the current reservation attempt. The first successful
  remote reservation materializes the final ID/slug/path, recomputes
  `scope_hash` and compiler stamps and persists the sole Scope approval. A
  losing absent-ref race may rematerialize that identical semantic projection
  under the next ID without a second user interaction, but only before any
  reservation for this Delivery succeeds. Any semantic byte change, a changed
  goal-derived slug, process restart without the exact local decision proof or
  any post-reservation identity change requires a fresh Scope approval.
- A Delivery ID is reserved by atomically creating its integration remote
  branch `agentrof/deliveries/<lowercase-delivery-id>` with the complete
  scope-approved package and an absent-ref lease. The uniqueness ref is
  ID-only, so different slugs cannot reserve the same ID. Collision allocates
  the next ID, regenerates machine IDs/paths/hashes and preserves the user's
  already approved semantic goal/scope decision.
- The allocator scans target-resident Delivery aliases and remote integration
  refs, proposes one greater than the highest numeric ID, and relies on the
  absent-ref push for the final race decision. Gaps are never reused.
- A Delivery Item uses the stable story ID as its path/ref key but does not
  mint a second frontmatter `id` or alias. The story remains the sole owner of
  `AUTH-01`; the containing Delivery relation provides item context.
- Item directories use the lowercase canonical story ID, not a copied title.
- Existing story IDs may be `ST-001`, `AUTH-01` or another compiler-valid
  project-wide ID. The branch/path transform is ASCII lowercase only. The
  backlog compiler requires project-wide uniqueness after ASCII case-folding,
  so two IDs can never collapse to one ref or directory. Delivery preflight
  also requires `git check-ref-format --branch` to accept every complete
  generated branch name.
- Document filenames are fixed English machine names and are never localized.

Stable ID ownership:

| Document | ID/alias example |
|---|---|
| Requirement | `REQ-001` |
| Definition of Done | `DOD` |
| Delivery | `DLV-001` |
| Execution Plan | `DLV-001-EXEC` |
| Delivery Item | No new ID; relation/path key is story `AUTH-01` |
| Code Review | `DLV-001-AUTH-01-CR` |
| Verification | `DLV-001-AUTH-01-QA` |
| Delivery Review | `DLV-001-REVIEW` |

Path grammar:

| Artifact | Canonical project-relative path grammar |
|---|---|
| Requirement | `workspace/docs/requirements/req-<digits>-<slug>.md` |
| Definition of Done | `workspace/docs/delivery/definition-of-done.md` |
| Delivery directory | `workspace/docs/delivery/deliveries/dlv-<digits>-<slug>/` |
| Delivery root | `<delivery-directory>/delivery.md` |
| Execution Plan | `<delivery-directory>/execution-plan.md` |
| Delivery Item directory | `<delivery-directory>/items/<lowercase-story-id>/` |
| Delivery Item | `<item-directory>/item.md` |
| Code Review | `<item-directory>/code-review.md` |
| Verification | `<item-directory>/verification.md` |
| Delivery Review | `<delivery-directory>/delivery-review.md` |
| Generated Delivery views | `<delivery-directory>/_generated/{execution-graph.md,evidence.json}` |

`<digits>` is the zero-padded numeric portion with at least three digits;
`<slug>` follows the 48-character lower-kebab rule; and
`<lowercase-story-id>` is the injective ASCII-lowercase transform validated by
the backlog compiler. A typed Delivery document outside this grammar is a
portable-gate error.

### 8.3 Document types, designations and graph colors

Add these fixed type keys and configurable default display designations:

| Type key | Config key | Default designation | Fixed color | Decimal RGB |
|---|---|---|---|---:|
| `requirement` | `requirement` | `requirement` | `#8E44AD` | 9323693 |
| `definition-of-done` | `definition-of-done` | `definition of done` | `#16A085` | 1482885 |
| `delivery` | `delivery` | `delivery` | `#2471A3` | 2388387 |
| `execution-plan` | `execution-plan` | `execution plan` | `#D68910` | 14059792 |
| `delivery-item` | `delivery-item` | `delivery item` | `#229954` | 2267476 |
| `code-review` | `code-review` | `code review` | `#5B8FF9` | 6000633 |
| `verification` | `verification` | `verification` | `#17A589` | 1549705 |
| `delivery-review` | `delivery-review` | `delivery review` | `#B9770E` | 12154638 |

Graph queries are fixed `tag:#doc/<type-key>` values in this exact order.
The eight new groups append immediately after the existing `issue-report`
group; setup never reorders the older groups. Colors are package policy and
are not project-configurable. Display
designations remain project-configurable and follow the existing designation
history and controlled-reconciliation protocol.

Designation mutation has a deliberately strict v1 Delivery boundary. Before
`/configure` or `reconcile-designations` changes a designation, its exact
inspect scans all remote Integration, Item and Slot refs. Its approved
candidate acquires Project Fence mode `source_handoff` with kind
`designation_reconciliation`, then repeats that scan. If any Delivery already
exists in target-resident package paths or any live remote ref, including a
successfully closed or cancelled Delivery, or a first reservation wins the
Fence race, the operation returns `DELIVERY_DESIGNATION_CHANGE_BLOCKED` with
`mutation_state: none`; config, titles, hashes, historical approval digests and
provider state remain byte-for-byte unchanged. Historical Delivery packages
are never retitled or rehashed in v1. Thus controlled designation
reconciliation is legal only before the first Delivery reservation. If the
source Fence wins, Delivery reservation/claim is blocked until the exact
candidate reaches target and Fence returns to `open`. Before making the
ordinary reviewed configuration/documentation PR/direct update mergeable, the
coordinator writes the irreversible target-update intent. The final target
change updates the config value and every affected pre-Delivery tracked
title/H1/source hash, runs the full portable gate and completes only after
target matches the bound source intent. The first later Delivery pins the new
hashes. An out-of-band designation change
discovered while any Delivery package or ref exists is a repository incident: all Delivery
mutation stops until target is restored; neither target refresh nor upgrade
treats it as a title-only semantic exception.

No Sprint, workstream, lane or retrospective designation is added.

### 8.4 New property types

Add these exact machine properties to vault policy and every lazy fragment
that may create the corresponding document:

| Property | Type | Rule |
|---|---|---|
| `request_kind` | text | Requirement-only closed value: `feature`, `defect` or `technical` |
| `urgency` | text | Requirement-only closed value: `low`, `normal`, `high` or `critical` |
| `definition_of_done` | link | Exactly one approved DoD on a Delivery |
| `definition_of_done_source_hash` | text | Pinned approved DoD source hash |
| `definition_of_done_commit` | text | Full target OID containing the pinned approved DoD blob |
| `target_branch` | text | Resolved Git target branch, stored as evidence |
| `backlog_commit` | text | Full Git object ID containing selected backlog sources |
| `scope_hash` | text | Compiler hash of exact story/test-plan baselines |
| `plan_hash` | text | Compiler hash of execution topology and claims |
| `item_plan_hash` | text | Compiler hash of one item's approved execution projection |
| `target_impact_hash` | text | `sha256:<64 lowercase hex>` over one nonempty target reconciliation projection; absent from Markdown when no Item impact exists, while wire records use literal `none` |
| `story_source_hash` | text | Pinned approved story source hash |
| `test_plan_source_hash` | text | Pinned approved test-plan source hash |
| `integration_base_commit` | text | Exact integration head last merged into an item |
| `execution_after` | link-list | Delivery Item ordering edges |
| `dependency_bindings` | text-list | Compiler-owned immutable external Delivery/claim/source bindings for backlog `depends_on` edges |
| `waits_for` | link-list | Execution-only cross-Delivery ordering edges to exact backlog stories |
| `waits_for_bindings` | text-list | Compiler-owned immutable predecessor Delivery/claim/source bindings for `waits_for` |
| `path_claims` | text-list | Normalized repository-relative files/prefixes |
| `contract_claims` | text-list | Closed-kind semantic contract identifiers |
| `role_sequence` | text-list | Closed Software Engineering Team role IDs |
| `reviewed_commit` | text | Full product/test Git object ID reviewed |
| `reviewed_integration_commit` | text | Exact remote pre-stamp Integration parent approved by the Delivery Review |
| `verified_commit` | text | Full product/test Git object ID verified |
| `cancellation_intent_hash` | text | Compiler digest of the user-approved cancellation reason, tips and dispositions |
| `cancellation_disposition` | text | Cancelled Delivery Item-only closed value: `not_started`, `integrated_reverted` or `unintegrated_discarded` |
| `cancellation_previous_tip` | text | Cancelled Delivery Item-only exact pre-finalization Item tip, or literal `none` when no Item ref ever existed |
| `pull_request_url` | text | Validated PR URL, absent until PR creation |
| `approval_hash` | text | Delivery Review digest excluding mutable PR URL metadata |

Do not add `effective_status`, slot, worktree, assignee, task, host, session,
estimate or duration properties.

Hash boundaries are exact:

- `scope_hash` covers Delivery ID/slug, Goal and Observable Outcome, exact
  story/test-plan links and source hashes, DoD link/hash/commit, target/backlog commit,
  the canonical designation projection used by those sources, exclusions and
  dependency preconditions derived from the selected backlog
  stories. Execution-only cross-Delivery serialization discovered later does
  not retroactively alter this scope decision.
- `plan_hash` covers `scope_hash`, the current `target_impact_hash` when one
  exists, exact Delivery Item set, dependency edges,
  `dependency_bindings`, `waits_for` edges and bindings, path/contract claims,
  role sequences, integration order and verification strategy.
- `item_plan_hash` is computed in topological order. It covers `scope_hash`,
  one item's story/test hashes, `execution_after`, `dependency_bindings`,
  `waits_for` and `waits_for_bindings`, path/contract claims, role sequence, applicable
  verification strategy, that Item's target-impact projection when applicable,
  and the sorted mapping from every same-Delivery direct
  predecessor story ID to that predecessor's final `item_plan_hash`.
  The DAG makes this non-recursive while propagating predecessor changes to all
  transitive descendants. It excludes unrelated parallel item projections.
- `cancellation_intent_hash` covers Delivery ID, `scope_hash`, cancellation
  reason, exact target baseline and the sorted mapping of every Story ID to its
  pre-quiesce remote Item tip and closed provisional disposition. A selected
  Story that has no Item ref because cancellation occurs before claims uses
  the literal tip `none` and disposition `not_started`; no synthetic Item ref,
  plan hash or integration base is created. It excludes the later quiesce
  tips, barrier epoch and the hash field itself.

`target_impact_hash` is `sha256:` plus lowercase SHA-256 over compact
sorted-key JSON with
exact keys `barrier_epoch`, `delivery`, `items`, `previous_plan_hash`,
`previous_target` and `target`. `previous_plan_hash` is `none` only before any
approved plan. `items` is a Story-ID-sorted mapping whose value has exactly:
`action` (`replan|reopen`), `contracts` (sorted canonical claim
IDs), `descendants` (sorted Story IDs), `merge` (`clean|textual_conflict`),
`paths` (sorted canonical claims) and `phase` (`integrated|unintegrated`). The
projection is rendered in the compiler-owned Target Reconciliation section of
`execution-plan.md`; it is not a runtime JSON file. Every later target advance
under the same barrier recomputes the cumulative projection from the original
pre-barrier target and invalidates approval.

The classifier is total and uses this priority order:

| Observable target delta | Item mapping and action | Merge value / result |
|---|---|---|
| Selected Story/Test source mutation with zero claims | No Item mapping; require exact Scope and, when present, Execution Plan reapproval | Wire `Target-Impact-Hash: none`; new Scope/Plan hashes bind the decision |
| Selected Story/Test source mutation after any claim | No candidate | `claimed_source_violation`; fail closed before a ref mutation |
| DoD path changed while the pinned historical blob/hash/commit remains valid | No Item mapping for this Delivery | Disjoint; wire `none` |
| Target path/contract intersects an approved Plan but no Item ref exists | No Item mapping. Publish claims-free `target-refresh-v1/plan_invalidated`, return the Plan and Item topology to draft, then require a fresh Plan approval/publication | Wire `Target-Impact-Hash: none`; the invalidating refresh is never an effective claim carrier |
| Target path intersects an Item `path_claims`, or the scanner maps a changed path/contract to `contract_claims`, after at least one Item claim exists | Include the directly affected Item. `unintegrated -> replan`; `integrated -> reopen` | `textual_conflict` iff any affected owned path cannot be cleanly three-way merged; otherwise `clean` |
| An Item is a transitive execution descendant of an included Item | Include it with empty direct `paths`/`contracts`; use `replan` when unintegrated and `reopen` when integrated | `clean` unless that Item is also directly affected |
| Setup-owned managed path | No Delivery Item mapping | Incoming payload wins only inside upgrade; a Delivery-authored change is corruption |
| Compiler-owned Delivery projection | Transition writer regenerates it | It never becomes an Item-owned conflict |
| Every remaining normalized delta that does not intersect this Delivery's selected sources, DoD policy, approved path/contract claims, setup-owned paths or user-authored package | No Item mapping | Disjoint; take target bytes and regenerate shared compiler projections. This includes another Delivery's product paths, Delivery directory and ordinary repository prose/code outside the current claims |
| This Delivery's user-authored prose, malformed rename, or any path whose ownership/classification remains ambiguous after the prior rows | No candidate | Manual blocker; no impact hash or ref mutation |

Every normalized path/contract atom is assigned exactly once by the first
matching row. Zero matches or multiple matches after priority normalization is
a compiler error, not an implicit manual or disjoint result. In a mixed delta,
disjoint atoms travel with the higher-priority affected candidate, while any
source violation/manual blocker rejects the whole candidate with zero ref
mutation. An all-disjoint delta creates no impact digest and preserves current
approval/evidence. Source-intent paths can never fall through to the disjoint
row, and ordinary refresh remains forbidden while Fence is `source_handoff`.

`paths` is exactly the sorted normalized intersection of the cumulative target
diff with the Item's approved path claims. `contracts` is exactly the sorted
approved contract-claim subset selected by the closed scanner rules.
`descendants` is the sorted transitive Item set added only because of execution
dependencies. For a multi-path Item, `merge` is `textual_conflict` when at
least one directly affected path has a textual, binary, add/add,
modify/delete, rename or mode conflict; the name is a closed v1 wire value,
and every such conflict uses the exact fetched-target entry as the neutral
Integration tree. Otherwise it is `clean`. No host may infer action from prose
or collapse a mixed set differently.

An empty `items` mapping never produces a digest. Markdown omits
`target_impact_hash`; every record that requires the wire field emits literal
`none`. A nonempty mapping is rendered in `execution-plan.md`. A scope-only
Delivery has no Execution Plan and therefore can only carry literal `none`;
its reapproval is bound by `scope_hash`. Every later target advance under the
same barrier keeps `previous_target` equal to the original pre-barrier target,
sets `target` to the newest target and recomputes the cumulative projection.
Before the first Item ref exists, even a Plan-relevant target delta has an
empty mapping: there is no remote Item work or evidence to reconcile. Its
claims-free invalidating refresh carries no impact digest, cannot preserve the
old Plan approval and must be followed by a new
`execution-plan-published-v1` before `claim-items`.

The canonical cancellation-intent projection has exactly these keys:
`delivery`, `reason`, `scope_hash`, `stories` and `target`. `stories` is a
Story-ID-sorted mapping whose value has exactly `disposition` and `tip`.
This scope-only golden vector prevents either host from inventing nulls or
synthetic refs:

```text
{"delivery":"DLV-001","reason":"Request withdrawn before execution","scope_hash":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","stories":{"AUTH-01":{"disposition":"not_started","tip":"none"}},"target":"1111111111111111111111111111111111111111"}
sha256:5d0e2cbcb3769b36ce75a371461954aefb8da5346be67d04fb48fe71105cea39
```

`cancellation_projection_hash` is `sha256:` plus lowercase SHA-256 over
compact sorted-key JSON with exactly `barrier_epoch`,
`cancellation_intent_hash`, `delivery`, `stories` and `target`. `stories` is
the final Story-ID-sorted mapping of exact `disposition` and pre-finalization
`previous_tip`; scope-only cancellation uses `{}`. The hash never includes a
candidate commit OID. The compiler separately proves the candidate's
target-relative product/test diff is empty and its knowledge paths equal the
closed cancellation package, avoiding a self-referential tree hash.
- A change to any normalized input invalidates its applicable persisted
  approval. The only bounded exception is pre-reservation Delivery ID/path and
  identity-derived hash rematerialization after an absent-ref collision: the
  semantic Scope projection and goal-derived slug must remain byte-exact, no
  remote reservation may yet exist for that Delivery, and the first winning
  package receives the sole persisted approval stamp. Generated wave numbers,
  local worktree paths, slots and remote fetch timestamps are never hash
  inputs.

Hashing never hashes a field into itself. All new compilers share this exact
canonicalization contract:

1. Parse front matter according to its closed schema; sort mapping keys by
   Unicode code point; emit compact UTF-8 JSON with no insignificant
   whitespace. `role_sequence` is ordered. Relation lists, tags, aliases,
   `execution_after`, `dependency_bindings`, `waits_for`,
   `waits_for_bindings`, path claims and contract claims are normalized as
   sets, rejected on duplicate and sorted by
   their canonical scalar value before hashing. Story scope sorts by canonical
   story ID.
2. Normalize body line endings to LF and one final LF. Remove only sections
   enclosed by the existing compiler/vault generated-block markers. Authored
   prose, outgoing relations and verdicts remain.
3. `source_hash` hashes the canonical front matter with only `source_hash`
   removed plus the normalized semantic body.
4. Delivery Review `approval_hash` hashes the same projection with
   `approval_hash`, `source_hash` and compiler-owned `pull_request_url`
   removed. It therefore cannot be changed by URL recording but does bind all
   user-approved prose, relations, verdict, reviewed commit and approval time.
5. Delivery Review `source_hash` includes the computed `approval_hash` and
   `pull_request_url`, while still excluding `source_hash` itself.
6. `scope_hash`, `plan_hash` and `item_plan_hash` use explicit schema-specific
   canonical JSON projections listed above rather than serialized Markdown.

Golden-vector fixtures must pin exact input bytes, canonical projections and
digests in both host distributions.

## 9. Delivery document contracts

Every document below also follows the universal vault metadata contract:
`title` uses the configured designation, a document that owns a stable ID has
that ID as its sole ID-shaped alias, the status tag is the kebab mirror of
`status`, and the body ends with valid Navigation. Delivery Item is the one
explicit exception to ID ownership: its folder and relation reuse the story
ID, while the story note remains the sole alias owner. Samples emphasize
type-specific fields but do not relax those universal requirements.

Titles and H1s follow these designation-aware templates. `<subject>` is
localized output prose; the configured designation is preserved verbatim, and
the H1 must byte-equal `title`. IDs live only in aliases, never as a second
title suffix.

| Type | Exact title/H1 template | Example with default designation |
|---|---|---|
| Requirement | `<request subject> <requirement designation>` | `Enterprise SAML access requirement` |
| Definition of Done | `<definition-of-done designation>` | `definition of done` |
| Delivery | `<goal subject> <delivery designation>` | `Enterprise SAML access delivery` |
| Execution Plan | `<goal subject> <execution-plan designation>` | `Enterprise SAML access execution plan` |
| Delivery Item | `<story subject> <delivery-item designation>` | `Authenticate with SAML delivery item` |
| Code Review | `<story subject> <code-review designation>` | `Authenticate with SAML code review` |
| Verification | `<story subject> <verification designation>` | `Authenticate with SAML verification` |
| Delivery Review | `<goal subject> <delivery-review designation>` | `Enterprise SAML access delivery review` |

Definition of Done deliberately has no inferred project-name prefix: neither a
checkout folder nor a host-specific value is canonical. Its configured
designation is the entire title/H1 and is preserved with exact project-selected
casing.

### 9.1 `definition-of-done.md`

There is exactly one project Definition of Done.

Example front matter:

```yaml
type: definition-of-done
id: DOD
status: approved
owner_role: product_owner
revision: 1
approved_at_utc: "<compiler-owned UTC>"
source_hash: "<compiler-owned sha256>"
tags:
  - doc/definition-of-done
  - status/approved
```

Required sections:

```text
Code
Automated Tests
Code Review
Verification
Security and Privacy
Documentation and Operations
Integration
PR Handoff
Approval
Navigation
```

Each requirement has a stable `DOD-###` row so Delivery Review can cite exact
checks. A Delivery pins the approved DoD source hash and exact target commit
containing that blob. Active validation reads
`<definition_of_done_commit>:workspace/docs/delivery/definition-of-done.md`
and recomputes the pinned hash; it never substitutes the mutable current-path
blob. Later DoD revisions affect future Deliveries only. The current Wikilink
remains navigation, while the Git blob is execution evidence.

If the DoD is missing, `/delivery-plan` runs a one-time prerequisite flow on
the project's ordinary documentation branch before allocating a Delivery ID:

1. `init-dod` creates the draft.
2. `check-dod` validates stable rows and project-specific commands/evidence.
3. The user approves it through `approve-dod`.
4. The approved DoD is committed and reaches the target branch through the
   project's normal Git policy.
5. `/delivery-plan` resumes from the fresh target.

The Delivery compiler owns these verbs. The bootstrap never writes a draft DoD
directly onto a protected target branch.

Deliberate revision uses the same stable ID/path and `/configure DOD`; it never
creates a revision file or mutates an active Delivery baseline:

1. `begin-dod-revision` requires the current target path to be approved,
   captures its exact target commit and `source_hash`, increments `revision`
   and creates revision N+1 as draft on an ordinary documentation branch.
2. `check-dod` preserves stable `DOD-###` row identities and validates the new
   commands/evidence. `approve-dod` requires target still contains the exact
   captured base, stamps the new approved hash/time and permits the ordinary
   protected Git handoff.
3. Approve advances only that authoring change. Request changes keeps the
   revision draft and findings with no target/Delivery mutation. Stop preserves
   the draft and returns `/configure DOD`; no Delivery ID, ref or Slot exists.
4. A concurrent target revision makes the draft stale. The loser reapplies to
   the new approved target, increments from that revision and obtains a fresh
   approval; it never overwrites or auto-merges two DoD decisions.

The live DoD state is only `draft|approved`. An older approved revision is
superseded by Git ancestry when N+1 reaches target, not by a second Markdown
record or a live `superseded` state. Existing Deliveries continue to verify the
pinned historical blob; future Deliveries pin the new target revision.

### 9.2 `delivery.md`

Example front matter:

```yaml
type: delivery
id: DLV-001
status: scope_approved
owner_role: product_owner
goal: "Enterprise users authenticate through the approved SAML provider."
derives_from:
  - "[[backlog/epics/identity/stories/saml-access/story|AUTH-01]]"
definition_of_done: "[[delivery/definition-of-done|Definition of Done]]"
definition_of_done_source_hash: "<approved DoD source hash>"
definition_of_done_commit: "<target commit containing that exact DoD blob>"
target_branch: main
backlog_commit: "<target commit containing the approved selected stories>"
scope_hash: "<compiler-owned sha256>"
revision: 1
approved_at_utc: "<compiler-owned UTC>"
source_hash: "<compiler-owned sha256>"
tags:
  - doc/delivery
  - status/scope-approved
```

Required sections:

```text
Goal
Observable Outcome
Scope Rationale
Exclusions
Dependency Preconditions
Definition of Done Baseline
Risks and Conflict Summary
User Decisions
Navigation
```

`derives_from` is the exact story scope. Epic links may provide navigation but
never add executable scope. A Delivery may span epics only when the stories
form one coherent goal and one reviewable PR.

`Dependency Preconditions` is a compiler-rendered exact projection of the
selected stories' approved backlog `depends_on` edges and their current target
merge facts. Execution Planning may later add `waits_for` on individual items
for cross-Delivery serialization; that separate relation belongs only to
`item.md` and the Execution Plan aggregates.

No duration, estimate, point, capacity, target date or velocity field is
legal.

### 9.3 `execution-plan.md`

Example front matter:

```yaml
type: execution-plan
id: DLV-001-EXEC
status: approved
owner_role: software_architect
derives_from:
  - "[[delivery/deliveries/dlv-001-saml-authentication/delivery|DLV-001]]"
revision: 1
plan_hash: "<compiler-owned sha256>"
approved_at_utc: "<compiler-owned UTC>"
source_hash: "<compiler-owned sha256>"
tags:
  - doc/execution-plan
  - status/approved
```

Required sections:

```text
Preconditions
Item Graph
Execution Waves
Role Sequences
Path Claims
Contract Claims
Integration Order
Verification Strategy
Failure and Recovery
Approval
Navigation
```

The canonical per-item topology lives only in each `item.md` front matter:
`execution_after`, compiler-owned `dependency_bindings`, `waits_for`,
compiler-owned `waits_for_bindings`, `path_claims`, `contract_claims` and
`role_sequence`.
Software Architect decisions are supplied through compiler verbs that update
those fields. `Item Graph`, `Execution Waves`, `Role Sequences`, `Path Claims`,
`Contract Claims` and `Integration Order` in this file are exact
compiler-rendered aggregates, never a second authored truth. The authored
sections are Preconditions, Verification Strategy and Failure and Recovery;
the compiler owns Approval and Navigation. Users approve the combined plan and
its hash, but never hand-edit wave numbers or duplicate item rows here.

### 9.4 `item.md`

Example front matter for an execution-approved, claimed item:

```yaml
type: delivery-item
status: in_scope
derives_from:
  - "[[backlog/epics/identity/stories/saml-access/story|AUTH-01]]"
  - "[[backlog/epics/identity/stories/saml-access/test-plan|AUTH-01 test plan]]"
related_to:
  - "[[delivery/deliveries/dlv-001-saml-authentication/delivery|DLV-001]]"
story_source_hash: "<approved story hash>"
test_plan_source_hash: "<approved test-plan hash>"
integration_base_commit: "<exact integration head last merged into this item>"
item_plan_hash: "<current approved item projection hash>"
execution_after: []
dependency_bindings: []
waits_for: []
waits_for_bindings: []
path_claims:
  - tree:src/identity/
  - tree:tests/identity/
  - file:config/saml.yml
contract_claims:
  - api:identity/saml
  - config:identity/saml
role_sequence:
  - software_architect
  - backend_developer
  - code_reviewer
  - qa_engineer
source_hash: "<compiler-owned sha256>"
tags:
  - doc/delivery-item
  - status/in-scope
```

Field presence is phase-exact; null, empty-string and placeholder hashes are
forbidden:

| Projection | Required fields beyond universal metadata | Forbidden/not yet created |
|---|---|---|
| Local proposal and remote `scope_approved` package | `derives_from`, `related_to`, `story_source_hash`, `test_plan_source_hash` | `execution_after`, `dependency_bindings`, `waits_for`, `waits_for_bindings`, `path_claims`, `contract_claims`, `role_sequence`, `item_plan_hash`, `integration_base_commit`; code-review and verification files do not yet exist |
| Execution Plan draft | `execution_after`, compiler-owned `dependency_bindings`, `waits_for`, compiler-owned `waits_for_bindings`, `path_claims`, `contract_claims` and `role_sequence`, even when a legal list is empty | `item_plan_hash`, `integration_base_commit`; evidence files remain absent |
| Execution Plan approved, before claims | All topology fields plus `item_plan_hash`; draft code-review and verification files now exist and bind that item-plan hash | `integration_base_commit` |
| Claimed, active, blocked, paused or integrated | All approved-plan fields plus `integration_base_commit`, initially the exact claims-established integration head and thereafter the exact integration head last merged into/reopened for the item | No lifecycle field may be null or inferred from a placeholder |
| Cancelled | Preserve every field that was valid at the cancellation point and require `cancellation_intent_hash`, `cancellation_disposition` and `cancellation_previous_tip`; `not_started` uses literal tip `none` and never synthesizes a plan hash or integration base | Missing earlier-phase fields remain absent, not null |

The parent Delivery/Execution Plan status and fresh remote refs determine which
row applies. The portable compiler rejects both premature fields and missing
required fields, so scope stubs cannot masquerade as executable items.

Phase validation is also ref-contextual, because one Integration tree and its
parallel Item branches intentionally project different moments of the same
Item. On an Item ref, `claimed` and later projections require
`integration_base_commit` exactly as the table states. On the Integration ref,
a not-yet-integrated Item retains its approved-plan projection without that
field; its exact Item ref is the operational projection and must independently
pass the claimed row. When an Item is integrated or reopened, the merged
Integration projection contains and requires the base. The global portable
gate validates the Integration package plus the complete remote Item-ref set
together; it neither copies an Item-only field into the marker tree nor treats
the deliberate pre-integration absence as a placeholder. This avoids a
self-referential claims-marker OID while keeping every executable Item branch
fully pinned.

Required sections:

```text
Delivery Scope
Execution Steps
Role Responsibilities
Implementation Evidence
Definition of Done Evidence
Blocking or Pause Reason
Deviations and Follow-ups
Integration Handoff
Navigation
```

Item files do not copy acceptance criteria or test scenarios. They link and
hash the approved story package.

The cancelled projection is canonical target knowledge, not disposable ref
metadata. `cancellation_disposition` and `cancellation_previous_tip` are
compiler-owned front-matter fields included in the Item `source_hash`.
`cancellation-finalized-v1` writes the same values into every Delivery Item
projection on Integration; each retained `item-cancelled-v1` child must render
an identical Item projection. The compiler-owned Cancellation Dispositions
table in `delivery-review.md` contains the exact Story-ID-sorted triples
`story | cancellation_disposition | cancellation_previous_tip`, and its set
must equal the Delivery scope. That table, the canonical cancellation
projection hash and Integration Item files are the permanent target truth.
Before cleanup, every retained Item trailer must also agree byte-for-byte;
cleanup may delete those coordination refs, and an unintegrated discarded
child need not remain reachable afterward. A clean clone reconstructs the
closed cancellation solely from the target Item fields, Review table,
projection hash, package and merge evidence; it never requires deleted Item
trailers. A
claims-free Story remains represented by its Integration Item stub with
`not_started | none`; it never receives an Item ref.

Path claims use one closed form:

```text
file:<repository-relative-posix-path>
tree:<repository-relative-posix-directory>/
```

Absolute paths, empty components, `.`/`..`, backslashes and glob syntax are
forbidden. A `file` claim overlaps the same file or a containing `tree`; two
`tree` claims overlap only when one is a path-component ancestor of the other.
String-prefix lookalikes such as `src/auth/` and `src/authentication/` do not
overlap.

Contract claims match:

```text
<kind>:<stable-name>
```

Allowed initial kinds are `api`, `data`, `config`, `environment`, `security`,
`ui` and `migration`. Adding a kind is a schema change, not free text.

`execution_after` links only Delivery Items inside the same Delivery and owns
their execution-only item DAG. The compiler also reads every pinned story's
backlog `depends_on` links. A dependency selected in the same Delivery becomes
a derived predecessor edge. An external dependency receives exactly one
machine-owned `dependency_bindings` value; it is never copied into
`execution_after` or `waits_for`.

`waits_for` links only approved backlog story notes selected by another open
Delivery. It is the sole authored truth for an execution-only cross-Delivery
ordering edge introduced by path/contract conflict analysis. The compiler
resolves every external backlog dependency and every waited-for story to
exactly one current or successfully merged Delivery claim and writes the
corresponding machine-owned binding in this canonical scalar form, sorted by
story ID:

```text
DLV-002|AUTH-02|<full initial item-claim Git OID>|sha256:<64 lowercase hex story source hash>
```

`dependency_bindings` has a one-to-one exact-set correspondence with external
backlog `depends_on` targets that are not selected in the same Delivery.
`waits_for_bindings` has the same correspondence with `waits_for`. Both are
approval snapshots, not authored decisions. The compiler rejects self-links,
duplicates, unknown dependencies, mismatched claim trailers and source hashes.
While the predecessor is open, activation requires the exact claim OID in the
named Delivery's current item ancestry. After successful predecessor closure
and ref cleanup, it requires that claim OID in verified target merge ancestry
and the matching target Delivery package. Cancellation, claim release, a
different Delivery claiming the same story or a source-hash change makes the
dependent Execution Plan stale and requires user reapproval of a new binding
or removal/replanning of the edge. A binding never follows a new claimant
dynamically.

`role_sequence` uses closed Software Engineering Team role IDs. The story's
one owner role remains accountable; supporting roles contribute on the same
item branch/worktree according to this sequence. No person, assignee, agent,
host task, session or worktree path is stored.

The sequence is mechanically closed:

- the story `owner_role` appears exactly once;
- every story `supporting_roles` member appears exactly once, and no undeclared
  implementation/design role may be added merely to pad the sequence;
- `code_reviewer` and `qa_engineer` each appear exactly once for every item and
  after all roles allowed to change product/test code;
- a conditional built-experience evaluator such as `ux_designer` must already
  be a declared supporting role, appears exactly once after an executable build
  exists and before QA/ready closure when its Requirement impact applies;
- duplicates, missing declared roles, unknown roles, evaluator-before-builder
  order and a supporting role with no explicit responsibility are errors;
- Product Owner scope/integration decisions are Delivery gates outside the
  item `role_sequence`, not a hidden writer step.

The existing backlog role contract remains the source for which role is owner
and which roles support the story; Execution Planning orders that exact set and
adds only the two mandatory independent evaluation roles. Delivery Item does
not copy `owner_role` or `supporting_roles` into front matter; its pinned story
and `role_sequence` prevent a second ownership truth.

### 9.5 `code-review.md`

One evolving record exists per item:

```yaml
type: code-review
id: DLV-001-AUTH-01-CR
status: approved
derives_from:
  - "[[delivery/deliveries/dlv-001-saml-authentication/items/auth-01/item|AUTH-01]]"
item_plan_hash: "<current approved item projection hash>"
reviewed_commit: "<last product-or-test change commit>"
source_hash: "<compiler-owned sha256>"
```

The body contains the existing correctness, conformance, architecture,
security and maintainability passes plus stable finding IDs. Status is
`draft`, `changes_requested` or `approved`.

### 9.6 `verification.md`

One evolving record exists per item:

```yaml
type: verification
id: DLV-001-AUTH-01-QA
status: passed
derives_from:
  - "[[delivery/deliveries/dlv-001-saml-authentication/items/auth-01/item|AUTH-01]]"
verifies:
  - "[[backlog/epics/identity/stories/saml-access/test-plan|AUTH-01 test plan]]"
item_plan_hash: "<current approved item projection hash>"
verified_commit: "<same product-or-test change commit as review>"
test_plan_source_hash: "<approved test-plan hash>"
source_hash: "<compiler-owned sha256>"
```

The body contains scenario coverage, suite results, mutation evidence when
configured, runtime evidence when applicable, UX/accessibility evidence when
applicable and stable findings. Status is `draft`, `failed` or `passed`.

Review and verification bind the last commit that changed product or test
paths and the current approved `item_plan_hash`, not a later evidence-only
commit. A later product/test change or a revision of that item's projection
invalidates both. An unrelated item's plan revision does not. Evidence-only
commits are permitted only inside the current item evidence subtree.

### 9.7 `delivery-review.md`

One evolving record replaces separate closing-review and retrospective files:

```yaml
type: delivery-review
id: DLV-001-REVIEW
status: approved
derives_from:
  - "[[delivery/deliveries/dlv-001-saml-authentication/delivery|DLV-001]]"
verifies:
  - "[[delivery/deliveries/dlv-001-saml-authentication/items/auth-01/item|AUTH-01]]"
  - "[[delivery/deliveries/dlv-001-saml-authentication/items/auth-01/code-review|AUTH-01 code review]]"
  - "[[delivery/deliveries/dlv-001-saml-authentication/items/auth-01/verification|AUTH-01 verification]]"
plan_hash: "<current approved execution plan hash>"
reviewed_commit: "<final integration product commit>"
reviewed_integration_commit: "<exact pre-stamp integration parent approved by this review>"
approved_at_utc: "<compiler-owned UTC>"
approval_hash: "<compiler-owned user-approved digest>"
source_hash: "<compiler-owned sha256>"
```

Required sections:

```text
Goal Outcome
Scope Disposition
Definition of Done Evidence
Integrated Quality Evidence
Demonstration and Acceptance
Deviations
Lessons and Follow-up
PR Decision
Findings
Verdict
Navigation
```

For a normally executed Delivery, the exact item, code-review and verification
sets must match Delivery scope, and both `plan_hash` and `reviewed_commit` are
required. Every approved Delivery Review requires
`reviewed_integration_commit`, the exact remote pre-stamp Integration parent
whose complete tree the user reviewed. The approval publication child is
proved separately by `delivery-review-published-v1`.

Cancellation uses a phase-exact projection instead of fabricating artifacts:

| Cancellation point | Required review fields and relations | Forbidden |
|---|---|---|
| Scope-approved, before Execution Plan | `scope_hash`, `cancellation_intent_hash`, `reviewed_integration_commit`; `verifies` is the exact scope Item-stub set | `plan_hash`, `reviewed_commit`, Code Review and Verification files |
| Execution approved, no product/test work | Current `plan_hash`, `cancellation_intent_hash`, `reviewed_integration_commit`; exact Item set plus only evidence files that legally exist | Synthetic `reviewed_commit` or synthetic passed evidence |
| Product/test work exists | Current `plan_hash`, `cancellation_intent_hash`, `reviewed_integration_commit`, last real `reviewed_commit`; exact Item set plus every existing evidence record and revert/aggregate evidence | Missing item disposition, omitted existing evidence or fabricated approval |

The compiler derives the row from the Integration and Item histories. Missing
earlier-phase fields stay absent rather than null. The same Delivery Review
sections remain, but non-executed quality/DoD rows state the exact cancellation
disposition and why execution evidence is not applicable.

An actionable lesson becomes a linked `technical` Requirement/backlog story,
not an untracked action row. A product/test change after approval invalidates
the review. PR URL recording may create one compiler-owned evidence-only
commit without repeating user approval. `approval_hash` follows the
non-self-referential canonical projection in section 8.4 and binds all
user-approved prose, relations, verdicts and the reviewed commits. `source_hash`
includes the URL and computed approval hash and is recomputed by `record-pr`.
The portable gate verifies both digests and rejects any other edit.

When a new actionable lesson is discovered during final review, the Delivery
branch does not write Requirement/backlog files. The review remains draft;
Requirement Flow creates the technical Requirement and backlog delta on an
ordinary authoring branch, merges it to target, then Delivery merges that new
target and repeats the affected aggregate checks and Delivery Review. Only the
current target-resident Requirement/story link may close the lesson. This
cross-flow handoff is exceptional but prevents either an illegal branch write
or an untracked follow-up.

## 10. Approval and state model

### 10.1 User approval gates

The normal successful path requires only these user decisions:

1. **Requirement approval**: intent, scope, non-goals and impact matrix.
2. **Changed-stage approval**: the existing final approval for each stage
   marked `required`.
3. **Backlog revision approval**: exact new/changed backlog diff and coverage.
4. **Definition of Done approval, only when missing or deliberately revised**:
   project-wide quality rows before any Delivery pins them.
5. **Delivery Scope approval**: goal, exact stories, exclusions and DoD.
6. **Execution Plan approval**: graph, waves, roles, claims and integration
   order.
7. **PR opening approval**: final Delivery Review and reviewed integration
   commit.
8. **Merge action**: explicit user merge request or an externally observed
   authorized merge after required remote checks pass.

Internal agent review iterations do not create user approval records. Git
records the actual author/committer; Markdown does not duplicate identity.

Exception/destructive paths require their own explicit decisions and never
reuse a normal-path approval implicitly:

| Exception | Required user decision |
|---|---|
| Requirement withdrawal | Approve the exact terminal reason; compiler writes `withdrawn` |
| Requirement no-change resolution | Approve the exact evidence and reason proving no implementable backlog delta; compiler writes `resolved_no_change` |
| Requirement supersession | Approve one relation-bound draft replacement and the reciprocal terminalization as a single compiler transaction; backlog Story replacement remains a later, separately approved `/backlog-plan` revision |
| Delivery cancellation | First approve the irreversible cancellation decision and provisional disposition of every item; after quiesce/reverts, separately approve the exact cancellation Delivery Review and PR diff |
| Fenced takeover | Confirm takeover of the exact stale remote item/slot tip after seeing host-loss evidence; this is an operational confirmation, not product approval |
| Orphan/ref cleanup that could abandon unique commits | Confirm the exact retained/cancelled disposition after the coordinator proves which commits would lose their last named ref |

Normal pause/resume, finding remediation and safe exact-lease cleanup need no
new product approval when they preserve an already approved scope/plan.
For Requirement no-change and Delivery cancellation, section 4's three-outcome
specialization is normative: pre-transition Request changes/Stop prove
`mutation_state: none`; post-cancellation-barrier Request changes/Stop retain
the exact epoch, tips, intent hash and dispositions. No exception path treats
silence, Stop or requested revisions as approval.

### 10.2 Persisted semantic states

Persist only states that are not more reliably proven by Git:

| Type | Persisted states |
|---|---|
| Requirement | `draft`, `approved`, `resolved_no_change`, `superseded`, `withdrawn` |
| Definition of Done | `draft`, `approved` |
| Delivery | `draft`, `scope_approved`, `cancelled` |
| Execution Plan | `draft`, `approved` |
| Delivery Item | `in_scope`, `blocked`, `paused`, `cancelled` |
| Code Review | `draft`, `changes_requested`, `approved` |
| Verification | `draft`, `failed`, `passed` |
| Delivery Review | `draft`, `changes_requested`, `approved` |

Delivery Item transitions have one executable owner and one closed contract:

| From | To | Orchestrated verb | Remote effect |
|---|---|---|---|
| `in_scope` without slot | `in_scope` active | `start-item` | Atomically advance item ref to an activation commit and create one slot at the same OID |
| `paused` | `in_scope` active | `resume-item` | Atomically advance item ref to a resume/activation commit and create one slot at the same OID |
| `in_scope` integrated with exact failure | `in_scope` active | `reopen-item` | Atomically invalidate evidence, advance project/integration/item refs and create one slot; normal start remains forbidden |
| `in_scope` active | `blocked` active | `block-item` | Compiler changes the item record; item and retained slot advance atomically to the same new OID |
| `blocked` active | `in_scope` active | `unblock-item` | Compiler changes the item record; item and retained slot advance atomically to the same new OID |
| `in_scope` or `blocked` active | `paused` | `pause-item` | Compiler changes the item record; item advances and the exact slot is deleted in one transaction |
| any noncancelled state | `cancelled` | `cancel-delivery` only | First quiesce every selected item, then persist the user-approved exact disposition |

The offline compiler prepares and validates the new tree; `delivery_git.py`
owns the atomic remote transition. Neither layer independently claims success.
`blocked` always retains a slot; `paused` and `cancelled` never do.

### 10.3 Semantic and coordination status

The board never collapses authored decisions and Git facts into one ambiguous
status. It displays two columns:

- `semantic_status` is the persisted document value above;
- `coordination_status` is derived from fresh remote refs, exact tips and Git
  ancestry.

Delivery coordination status follows this complete precedence order:

The table uses three closed predicates. `normal_terminal_items` requires every
exact Item tip integrated and no Slot. `cancellation_review_phase` requires the
exact unmatched cancellation intent/barrier and zero Slots, but does not
pretend that a local finalization/Review candidate is remote; it remains true
until the latest cancellation Review is successfully published. A fresh clone
can derive this phase from Integration, retained Item refs and Slot absence,
while local candidate readiness is shown only as a separate next-action detail.
`cancellation_terminal_published` requires no Slot, every retained Item tip at
the matching validated `item-cancelled-v1` disposition, every `not_started`
Story to have no Item ref, and Integration to contain the exact irreversible
cancellation barrier, `cancellation-finalized-v1`, cancelled Item projections
and current approved cancellation Review. Unintegrated discarded commits are
deliberately not required to be Integration ancestors.

| Coordination status | Proof |
|---|---|
| `target_merged` | Every merge-derived closure predicate in section 15.4 passes against fresh provider and target evidence |
| `awaiting_merge` | Normal Delivery has no unmatched barrier, while cancellation has its exact expected barrier; the current phase-exact `delivery-review-published-v1` has a validated provider PR in `OPEN`, `draft=false` state whose exact head equals current Integration branch and contains the canonical URL-record commit; `normal_terminal_items` or `cancellation_terminal_published` applies and target lacks that final head. Fence may be temporarily non-open for an unrelated source/configuration handoff; that blocks merge authority, not PR-readiness truth |
| `active` | At least one valid slot ref equals a current item tip for the Delivery |
| `pr_handoff` | `normal_terminal_items` or `cancellation_terminal_published` passes, the current Delivery Review is remotely published and target lacks the reviewed head, but PR opening is not yet complete: no intent exists, an intent is unmatched, the lifecycle PR is draft/closed/uncertain, or its URL record is not current |
| `review` | Normal Delivery satisfies `normal_terminal_items` while its final Review is incomplete, or cancellation satisfies `cancellation_review_phase` while `cancellation_terminal_published` is false |
| `claimed` | Every deterministic item branch exists and no item is active |
| `awaiting_claims` | Exact `execution-plan-published-v1` is remote and approved but the atomic claim transaction is incomplete |
| `planned` | Scope is approved and Execution Plan is not approved |
| `draft` | Scope is not approved |

`coordination_status` and merge permission are separate. An exact ready PR
remains `awaiting_merge` while Fence is `source_handoff` or `configuring`, but
the merge action is hidden/denied with `mutation_state: none`; the default
board shows the owning handoff as blocker and its public resume entry, while
Diagnostics shows exact Fence evidence. If finish/abort leaves target exact,
the same status becomes merge-actionable without a Delivery mutation. If the
handoff advances target, normal target refresh, Review invalidation and
reapproval apply before merge. This rule also covers a ready cancellation PR
and never broadens provider-side merge closure or bypasses source eligibility.

Delivery Item coordination status follows this order:

| Coordination status | Proof |
|---|---|
| `target_merged` | Parent Delivery satisfies the complete normal or cancellation closure predicate in section 15.4 |
| `integrated` | The exact observed remote item tip is an ancestor of integration head |
| `active` | One slot ref equals the item's current remote tip |
| `claimed` | Its deterministic item branch exists |
| `unclaimed` | No item branch exists |

Semantic exceptions remain visible beside those facts. For example, a blocked
item is `blocked / active`, a paused item is `paused / claimed`, and a
cancelled item may move from `cancelled / claimed` to
`cancelled / target_merged` before cleanup. A Delivery is successfully closed
only when it is not semantically cancelled, its approved outcome review is
valid and coordination is `target_merged`. `blocked` requires a retained slot;
`paused` requires no slot. Any impossible combination is a reconciliation
error, not a guessed state.

Do not mirror coordination values into authored front matter. Doing so would
create two operational truths and a repair engine.

## 11. Delivery Planning

`/delivery-plan` performs these steps:

1. Require a valid Git repository, unique target-branch upstream, clean
   primary checkout and fresh remote fetch.
2. Require approved backlog, approved selected stories/test plans, current
   Requirement-source eligibility for every selected Story and an approved
   committed Definition of Done.
3. Select one coherent Delivery Goal and exact story set. Epics are not
   executable.
4. Block a story already claimed or successfully delivered. Also reject a
   story visible in another fetched noncancelled scope-approved Delivery. That
   planning scan is not a second reservation authority: simultaneous scope
   approvals can still race, and only atomic item-branch creation wins global
   exclusivity.
5. Verify every dependency is merged, in this Delivery, or explicitly a
   cross-Delivery precondition that blocks later activation.
6. Challenge scope that is too broad for one reviewable PR. There is no time
   estimate or numeric size gate.
7. Build a provisional local proposal from the exact target with a proposed
   goal-derived slug, `DLV-###`, `delivery.md`, item stubs and scope hash. It is
   rendered through a detached temporary index under the ignored project-local
   runtime proposal directory. It never writes the primary checkout, creates a
   local branch/ref, dirties the vault or consumes a Delivery ID. Decline,
   process loss and target advance may discard it safely.
8. Present goal, inclusions, exclusions, dependencies, risks and impact.
9. After user approval, refetch target and Delivery refs, allocate the current
   next ID and freeze the goal-derived slug.
10. Compiler-stamp the scope-approved package and atomically create the
    ID-only integration ref with an absent-ref lease while advancing/creating
    the `open` Project Fence ref. The first Delivery commit contains
    the complete approved package, not an empty draft.
11. Classify any rejected atomic push after a fresh all-ref fetch. If the
    proposed Integration ref remains absent, Fence mode is still `open` and
    only unrelated Fence churn caused the lease loss, rebuild the Fence child
    and retry the same ID. Allocate the next ID only when
    the proposed ID integration ref or target alias actually exists. Before
    preserving the allocation-independent decision, refetch target, Fence,
    every Delivery/Item ref and rerun every non-identity Scope precondition:
    exact selected Story/Test/DoD hashes and approvals, dependency facts,
    goal-derived slug, and absence of a claim, successful delivery or another
    noncancelled scope-approved Delivery containing any selected Story. Only
    when that semantic projection remains byte-identical and the collision is
    ID-only may the coordinator regenerate machine identity/path/hash fields
    and retry without another user gate. Same-Story competition stops with no
    loser reservation; a changed Story/Test/DoD/dependency/goal/slug discards
    the local approval proof and requires a fresh Scope preview/approval.
    Unrelated target/backlog movement may preserve approval only when every
    selected input remains exact. Never reuse rejected package bytes. The first
    winning remote package receives the sole persisted Scope approval stamp;
    verify that ref, then open its linked integration worktree.

The integration branch at this point is a durable scope-approved planning
branch, not an execution claim. No unapproved remote draft, item branch,
execution slot or product-code worktree exists. A crash after the atomic push
is resumed from the valid scope package; a crash before it leaves no remote
Delivery or consumed ID.

## 12. Execution Planning

`/execution-plan` operates only in the Delivery integration worktree:

1. Fetch current target. If it is newer than the target already merged into
   Integration, run the closed claims-free classifier first. A disjoint delta
   uses deterministic `target-refresh-v1/disjoint`; a path/contract-relevant
   delta invalidates any prior Plan through
   `target-refresh-v1/plan_invalidated`; and a selected Story/Test byte change
   requires `delivery-scope-revised-v1`. No interactive product/prose
   resolution is authored on Integration. Run the portable gate, complete any
   required fresh Scope/Plan approval, then recheck every noncancelled/
   nonmerged remote Delivery with an
   approved Execution Plan and all story claims. A Delivery without a slot is
   still part of conflict analysis.
2. Ask the Software Architect to produce item dependencies, normalized path
   claims, contract claims, role sequences and integration order.
3. Combine backlog `depends_on` with same-Delivery execution-only
   `execution_after` edges and cross-Delivery execution-only `waits_for` story
   edges. Each edge kind retains its one canonical owner. Compile an exact
   `dependency_bindings` value for every external backlog dependency and a
   `waits_for_bindings` value for every execution-only cross-Delivery edge.
4. Reject cycles and unknown references.
5. Reject an unordered path or contract overlap inside the Delivery. An
   overlap is allowed only when an explicit dependency serializes the items.
6. Reject a known overlap with another open Delivery unless an exact
   `waits_for` story edge makes this item, and therefore its Delivery, wait for
   the claimed predecessor story's Delivery to merge.
7. Derive execution waves and integration order topologically, using canonical
   story ID as the deterministic tie-breaker. Users express a different order
   only by adding a justified `execution_after` edge; they never edit wave or
   sequence numbers.
8. Make every story appear exactly once as one Delivery Item.
9. Check that claimed paths cover expected implementation and test surfaces.
10. Present waves, role sequences, conflicts, WIP effect and integration order
    for user approval.
11. `delivery_compile approve-execution` stamps `execution-plan.md`, every Item
    projection/hash and first draft Code Review/Verification file into one
    exact local candidate tree; it performs no network write.
12. `delivery_git.py publish-execution-plan` is the sole Git publisher. It
    requires open Fence/no barrier/no PR intent, portable-valid bytes and exact
    target/source baselines, then atomically advances Fence + Integration to
    `execution-plan-published-v1`. It creates no Item/Slot/worktree. Remote
    acceptance followed by response loss is reconstructed from that exact
    record and returns complete without a second commit or user approval.
13. Only after publication verifies remotely, `claim-items` creates all
    deterministic item branches from that exact effective approved plan head
    in one atomic absent-ref push. These are the sole global story claims. No
    slot, product change or item worktree is created yet.

A one-story Delivery still receives a compact Execution Plan with one wave and
one item. Multiple roles on one story use the same item branch/worktree in the
approved sequence. Parallelism is across independent items, not multiple
writers sharing one worktree.

Known path and contract overlaps are blocking planning findings, but their
cross-Delivery scan is an optimistic snapshot rather than a distributed lock.
Two different stories can race after both scans. Activation therefore repeats
the scan, integration is serialized, and every upstream change invalidates the
affected review and verification. The plan does not claim impossible atomic
semantic isolation between unrelated story IDs.

Any topology, claim or role change returns the Execution Plan to draft,
increments revision and requires reapproval.

After item claims exist, the selected story set cannot be added to, removed
from or replaced inside that Delivery. A scope correction cancels/replans the
Delivery or creates a new Requirement/Delivery as appropriate. A non-scope
Execution Plan revision follows this exact protocol:

A target delta first computes the complete affected Item set with the table in
section 8.4, then selects exactly one lifecycle:

| Affected set | Sole legal lifecycle |
|---|---|
| Empty | Ordinary disjoint `target-refresh-v1`; no Item/evidence mutation |
| Nonempty | The zero-Slot plan-revision barrier below, fresh impact-bound Plan approval and one atomic release; integrated Items receive `integrated_reopen`, unintegrated Items receive `unintegrated_rebase` |

The first matching row wins. Ordinary target refresh never carries a nonempty
impact mapping and ordinary `reopen-item` is not a target-reconciliation path.
An implementation must reject either alternative record sequence with
`mutation_state: none`; the same Plan hash can never authorize two lifecycles.

The sole upgrade-mode exception before claims may refresh source hashes for
the **same** exact Story/Test set after the upgrade classifier reports
`scope_reapproval_required`. It cannot alter Goal, story set, DoD decision or
dependencies; it requires fresh Scope and any existing Execution Plan approval
and is published only inside `upgrade-target-merge-v1`. After one Item claim,
even that exception is forbidden.

1. Begin only in Fence mode `open` with no unmatched barrier/PR intent.
   Atomically install the `plan-revision` Delivery barrier, advance every
   active Item to a quiesce child and delete every occupied Slot. Slotless and
   integrated refs remain exact behind Integration.
2. If a relevant target delta caused the revision, prepare
   `plan-revision-target-refresh-v1`. The initial barrier and refresh may be one
   atomic Fence + Integration + active-Item/Slot transaction, with the barrier
   as the refresh first-parent ancestor. If the barrier already exists, require
   zero Slots and advance open Fence + Integration only. A later target advance
   under the same epoch appends another refresh, recomputes cumulative impact
   from the pre-barrier baseline and invalidates approval. Refetch after every
   accepted push; drift leaves the barrier active and repeats classification.
3. Reject selected Story/Test mutation as a Scope/source violation; it cannot
   be absorbed by plan revision. A newer DoD path is irrelevant when the pinned
   historical blob remains valid. For code/config/contract deltas, render the
   exact Target Reconciliation projection and `target_impact_hash`, applying
   the total phase/action/aggregation table in section 8.4.
   A true claimed-path conflict takes the fetched target entry in Integration;
   no product resolution is authored there.
4. Compute old/proposed `item_plan_hash` values and the transitive impact set.
   An already integrated Item's approved topology projection cannot change;
   such a change requires cancellation or a compensating Requirement/Story.
   Its target conflict may still be marked `reopen` without rewriting that
   historical plan projection. Revise and reapprove the plan, binding the
   latest `target_impact_hash` even when claim strings/order are unchanged.
5. Prepare one `delivery-barrier-release-v1` Integration head plus the complete
   Item reconciliation set. An affected integrated Item receives
   `item-target-reconcile-v1/integrated_reopen` as a child of the release head.
   An unintegrated/quiesced Item receives
   `item-target-reconcile-v1/unintegrated_rebase` with its old tip first and the
   release head second, preserving nonconflicting owned work and neutralizing
   textual conflicts for later Item-owned resolution. Other nonintegrated
   plan-affected Items receive the same deterministic sync form. For a
   plan-only revision with no target delta, every reconcile record carries
   `Target-Impact-Hash: none` and `Target` equal to the exact current validated
   target baseline; no synthetic target-impact digest is created.
6. In one atomic push advance Fence to its open release child, Integration to
   the approved release and every affected Item to its exact reconcile tip; no
   Slot is created. Any lease rejection changes nothing. Success requires the
   complete expected Item set, portable tree and exact impact/plan hashes.
   The release record, returned open Fence and every reconcile record bind the
   same exact target.
7. Invalidate review/verification for every changed Item and transitive
   descendant; unchanged integrated evidence remains valid. Reopened
   predecessors are no longer Integration ancestors, so dependents stay
   blocked. Rerun path/contract/dependency checks and allow explicit activation
   only after the release verifies remotely.

A target-bound release is not workflow-complete from the multi-ref CAS alone.
After success or an ambiguous response, the coordinator refetches target,
Fence, every released Integration/Item ref and Slots before creating a
worktree, returning writer readiness or consuming another approval. Exact
released refs plus the bound target is `complete`. Exact released refs plus a
newer target is `partial` with `DELIVERY_TARGET_CONVERGENCE_REQUIRED`: the old
barrier remains released and is never resurrected. No released Item has a
Slot, and every later activation performs the mandatory fresh-target gate.

When the exact release open-Fence child is still current, the coordinator runs
the ordinary classifier from the release target to the new target. A disjoint
delta uses ordinary refresh; a nonempty claimed impact begins a new plan-
revision epoch and fresh approval; selected-source, governed-config or setup
drift follows its normal source/config/incident rule. If a valid successor
Fence child already won, that successor owns convergence and is never
overwritten. Continued unexplained movement remains `partial` and fail-closed.
The same post-CAS rule applies to a legal plan-revision abort and every upgrade
release. After upgrade, open Deliveries converge in stable Delivery-ID order
without reviving the completed upgrade epoch; each upgrade release record uses
`Target == Handoff-Target` and the returned open Fence binds that same target.

`abort-plan-revision` is freely legal before a barrier-bound target refresh
when it restores the exact last approved projection. After such a refresh it
is legal only if a newer exact classifier proves the cumulative delta disjoint
and the previous plan completely valid; otherwise Stop preserves the safe
barrier and `/deliver DLV-###` resumes this same revision.

The original claim commit remains the story reservation audit; the current
item file and integration plan prove the active plan revision.

## 13. Git coordination and branch contract

### 13.1 Remote and target resolution

Delivery uses the upstream remote of the repository's default branch. It does
not add duplicate project config for a remote alias or target branch.

Planning preflight requires:

- one resolvable remote default branch;
- fetch and authenticated push access;
- a clean primary checkout;
- server support for atomic multi-ref push;
- branch rules that permit creation/deletion of Delivery refs;
- a supported provider check proving that Delivery PRs can use and are
  protected against non-merge-commit completion.

If upstream resolution is ambiguous, Delivery stops with standard Git
configuration instructions. It never guesses between remotes. `max_parallel`
is not required for Requirement Flow, Delivery Planning, Execution Planning or
story claiming. It becomes mandatory immediately before the first item
activation.

The first provider adapter is GitHub. It verifies repository merge policy,
creates or reads the exact head/base PR, verifies required checks and confirms
the observed merge method. V1 requires `allow_merge_commit=true`,
`allow_squash_merge=false`, `allow_rebase_merge=false` and no mandatory merge
queue that rewrites the reviewed head. Automatic head deletion must also be
off so Agentrof can verify closure before exact-lease cleanup. A provider
without a capability-equivalent adapter fails preflight before item activation.
It may export a non-actionable planning summary, but there is no unverified
manual `/deliver` path that can later claim PR or closure guarantees.

The adapter follows the existing issue-filing dependency posture: use an
authenticated `gh` CLI when available, otherwise the GitHub API through
`GH_TOKEN`/`GITHUB_TOKEN`. It adds no marketplace plugin dependency and stores
no credential in project files.

### 13.2 Git ref and branch names

Examples assume the resolved upstream remote is `origin`. The remote alias is
derived from the target branch and is never embedded in a canonical branch
name.

| Purpose | Canonical full remote ref | User-visible short name | Worktree |
|---|---|---|---|
| Existing target branch, example only | `refs/heads/main` | `main` | Existing primary worktree; Agentrof does not create or rename it |
| Project Fence | `refs/heads/agentrof/fence` | `agentrof/fence` | Never |
| Delivery Integration branch | `refs/heads/agentrof/deliveries/dlv-001` | `agentrof/deliveries/dlv-001` | One Integration worktree |
| Delivery Item branch and story claim | `refs/heads/agentrof/items/auth-01` | `agentrof/items/auth-01` | One Item worktree while locally active |
| Project-wide execution slot 1 | `refs/heads/agentrof/slots/001` | `agentrof/slots/001` | Never |
| Project-wide execution slot 2 | `refs/heads/agentrof/slots/002` | `agentrof/slots/002` | Never |

Exact grammars:

```text
Fence:       refs/heads/agentrof/fence
Integration: refs/heads/agentrof/deliveries/dlv-<at-least-3-digits>
Item:        refs/heads/agentrof/items/<ascii-lowercase-story-id>
Slot:        refs/heads/agentrof/slots/<at-least-3-digits>
```

Examples:

```text
DLV-001               -> agentrof/deliveries/dlv-001
DLV-1042              -> agentrof/deliveries/dlv-1042
ST-001                -> agentrof/items/st-001
AUTH-01               -> agentrof/items/auth-01
PAYMENT-204           -> agentrof/items/payment-204
slot 1                -> agentrof/slots/001
slot 27               -> agentrof/slots/027
slot 1000             -> agentrof/slots/1000
```

Integration names contain only the immutable lowercase Delivery ID. The slug
remains in the tracked folder and never enters the ref. Item names contain only
the ASCII-lowercase globally unique story ID; they contain neither Delivery ID
nor title slug. Backlog story IDs already match
`[A-Z][A-Z0-9]*-[0-9]{2,}` and their lowercase transform is injective. Slot
`000` is forbidden; valid slots are `001..max_parallel`, rendered with at least
three digits.

The complete `agentrof/**` branch namespace is reserved to `delivery_git.py`.
It is fixed English, lowercase, never localized and never accepts a user title
or arbitrary suffix. Manual creation of a matching branch is a collision or
corruption finding, not a user extension point. Every generated short name
must pass `git check-ref-format --branch` before any remote operation.

On a machine that owns a worktree, the local Integration or Item branch uses
the same `refs/heads/agentrof/...` name and tracks the corresponding remote
branch. A fetch may expose read-only observations such as:

```text
refs/remotes/origin/main
refs/remotes/origin/agentrof/fence
refs/remotes/origin/agentrof/deliveries/dlv-001
refs/remotes/origin/agentrof/items/auth-01
refs/remotes/origin/agentrof/slots/001
```

Remote-tracking refs are caches, never writer authority. Fence and Slot refs
have no local branch and no worktree. The coordinator uses freshly fetched
remote OIDs as exact lease expectations.

No additional Agentrof branch/ref is created for Requirement, Execution Plan,
review, QA, role, agent, cancellation, plan revision, upgrade, PR or Release.
Cancellation and barriers are commits on the existing Fence, Integration and
Item refs. The final PR head is the Integration branch itself. There is no
separate claim, sprint, release, review, QA or per-agent branch, and Agentrof
creates no tag. A merge candidate is an unreferenced local commit OID produced
from a detached temporary index and is either pushed atomically or discarded;
it never receives a durable local or remote ref.

Remote mutation rules:

- Every mutation names the full expected current object ID.
- Missing-ref creation uses an explicit empty expected value.
- Update requires the exact observed remote object ID and a fast-forward.
- Deletion requires the exact observed remote object ID.
- Bare `--force`, bare `--force-with-lease`, `reset --hard`, force deletion and
  force worktree removal are forbidden.
- Published Item and Integration branches are never rebased or history-
  rewritten.

`agentrof/fence` is the one project-wide remote fencing ref. Its current closed
record is `project-fence-v1` with mode
`open|source_handoff|configuring|upgrade`, a unique epoch and fetched target
OID. `source_handoff` protects an incorporated Requirement supersession,
backlog Story/Test mutation or designation reconciliation while it travels to
target, so source mutation and Delivery reservation/Item claim cannot both win
after observing the same Fence. Only the current tip is operationally meaningful;
its ancestry is never parsed as a task/event ledger or copied into Markdown.
The ref is lease-deleted only when its exact current mode is `open`, every
Source/target-update/carrier/Upgrade field is `none`, target/config validation
is current and no open Delivery or setup transition exists. A
`source_handoff`, `configuring` or `upgrade` Fence is never deletable, even
when no Delivery ref exists. Unreachable historical fence commits may be
garbage-collected only after that verified open-state deletion.

If the Fence ref is absent, an ordinary Delivery reservation, semantic source
handoff or parallelism configuration may recreate it only after a fresh all-ref scan proves that no
Integration, Item or Slot ref and no in-progress setup transition exists.
Upgrade may also create it for a ref-free project or through an explicitly
validated incoming compatibility adapter. A missing Fence beside any open
Agentrof ref is corruption and fails closed; it is never interpreted as mode
`open`. Simultaneous legitimate creators use an absent-ref lease and cannot
both win.

Deletion or corruption of an established Fence outside the coordinator is a
repository incident, not a normal reconciliation state. Automatic
reconstruction is forbidden because `source_handoff`, `configuring` or
`upgrade` intent may have
existed only in the lost tip. Recovery may restore only the exact previously
observed Fence OID under an absent-ref lease after validating its closed record
and every open Agentrof ref. If that object cannot be recovered from a provider
ref log or another verified clone, v1 remains fail-closed and requires an
explicit user-authorized repository repair outside the supported lifecycle.
It never fabricates a new epoch, guesses `open`, or deletes Item/Slot refs to
make the evidence fit.

The first Fence commit is a same-tree child of the fetched target; every later
Fence commit is a same-tree child of the exact observed Fence tip. Its tree is
never project knowledge. This makes every update fast-forward while its
trailers bind the current target/config fact. Fence has no checkout and no
worktree; the coordinator creates these metadata-only commits directly.

Every compiler-owned fence/control commit carrying an `Agentrof-Record`
trailer also carries exactly one `Agentrof-Protocol: <positive integer>`.
Initial examples use `1`; upgrade transition/write selection follows section
21.4. The integer is a closed wire-protocol version, not a package version or
migration counter. Unknown, missing or mixed unsupported protocol values fail
closed before a ref mutation.

Fence participation is a closed operation contract rather than a broad
implicit rule:

| Operation family | Atomic remote refs |
|---|---|
| Semantic source-handoff acquire/target-update-intent/finish/abort | Fence only; the exact approved reachable carrier/candidate is bound before any provider/direct target mutation, and claim/reservation/configuration/upgrade compete on that Fence tip. Reauthorization uses the dedicated row below |
| Delivery reservation or pre-claim scope revision | Fence + Integration |
| Initial Execution Plan publication | Fence + Integration; no Item/Slot/worktree creation |
| Ordinary target refresh | Fence + Integration merge candidate; no Item/Slot mutation |
| Plan-revision target refresh | Open Fence + Integration; when beginning the barrier, the same atomic transaction also quiesces active Items and deletes their exact Slots |
| Cancellation target refresh | Open Fence + Integration merge candidate carrying the exact unmatched cancellation epoch/intent; no Item/Slot mutation or reopen |
| Item claim set | Fence + Integration + every new Item claim |
| Start, resume or reopen | Fence + Integration + one Item + one Slot |
| Plan-revision/cancellation/upgrade barrier begin or permitted release | Fence + affected Integration/Item/Slot refs |
| Per-Delivery upgrade target merge | Exact same-epoch `upgrade/acquired` or `upgrade/target_handoff` Fence with non-`none` Target-Update-Intent + one Integration, processed in stable Delivery-ID order; Fence remains the same epoch/contract/phase |
| Config/upgrade target-update intent | Fence only: exact acquired/configuring tip advances by setting immutable `Target-Update-Intent` plus complete carrier before a provider/direct target mutation can begin; abort is forbidden afterward |
| Target-carrier reauthorization | Fence + the exact existing carrier authoring ref in one same-repository atomic receive-pack transaction for both carrier kinds; explicit leases require the old Fence and carrier-head OIDs, and both refs advance or neither does. `github_pr` additionally retains the same draft/unmerged PR number/ref. Fork-head PRs are unsupported in v1 |
| Upgrade target handoff | Fence only: after every open Integration contains the same current target, exact upgrade tip advances to or refreshes `upgrade/target_handoff`; only `Upgrade-Phase` and `Handoff-Target` change, while epoch, acquisition `Target`, Target-Update-Intent, config hash, contract hash and protocol remain exact |
| PR creation/adoption intent or PR URL record | Fence + Integration; provider create is called only by the local holder of a winning creation-intent receipt, never by adoption |
| `max_parallel` configure begin/intent/finish/abort | Fence; the irreversible target-update intent precedes provider/direct target mutation, finish verifies the exact target, and abort is acquired-only |
| Active product/evidence push, block or unblock | Equal Item + Slot only |
| Pause or takeover | Item + exact Slot only |
| Item integration seal | Integration + Item + exact Slot only |
| Initial cancellation revert/finalization + Review publication | Open Fence + exact Integration containing the unmatched cancellation barrier + the complete retained Item set; one local reverse-order `cancellation-revert-v1` chain, finalization and Review are published as one ancestry, and the Item set is empty for any claims-free Delivery |
| Cancellation Review re-publication | Open Fence + exact Integration only; the complete retained Item set must already equal its matching terminal `item-cancelled-v1` tips and is verified but not advanced. A fresh finalization/Review ancestry carries only same-intent documentation/evidence/current-target projections and never a new `cancellation-revert-v1` |
| Normal Delivery Review publication | Open Fence + Integration; an existing lifecycle PR must be the same draft/unmerged PR and follows the common post-PR linearization |
| Delivery Review evidence-only invalidation | Open Fence + Integration; the same lifecycle PR is made/verified draft and unmerged before the CAS and follows it afterward |
| Verified closure cleanup | Exact Item/Slot/Integration deletions; optional final Fence deletion only after a fresh all-ref scan plus exact open/cleared/current-target/config proof |

Reservation, initial plan publication, ordinary target refresh, claim,
activation, PR creation/adoption intent/record and
barrier-begin families require Fence mode `open`; handoff/release/configuration
verbs require their exact matching current mode and epoch. `source_handoff`
rejects reservation and claim; designation handoff additionally rejects any
open Delivery, Requirement supersession rejects any noncancelled/nonclosed
Delivery scope containing a Story in the old Requirement's current coverage,
and backlog source handoff rejects
mutation of a currently claimed or successfully delivered Story/Test.
Per-Delivery upgrade target
merge requires the exact upgrade epoch, bound contract and immutable target
update intent. `upgrade` rejects ordinary reservation, refresh, claim,
activation and configuration. The Item/Slot-local families
remain safe because every barrier that could invalidate them competes on those
same exact Item/Slot refs, while integration also competes on Integration.
`max_parallel` configuration and every source handoff keep their Fence mode
until the exact target commit is observed. Before any target write, the Fence
receives a durable `Target-Update-Intent`; from that point recovery is
requery/finish, never abort. Thus a delayed provider/direct target update
cannot land under an already released epoch. A crash leaves a conservative
recoverable mode, never an implied success.

`authorize-target-update` uses the same call-election discipline as PR
creation. Before CAS, the approved candidate must already be reachable through
one exact durable authoring ref. A GitHub handoff additionally requires one
exact existing draft PR; a direct handoff requires the candidate commit on
that ref and an exact target-base lease. The Fence intent child durably binds
repository, carrier kind/ref/object, head and base, so a fresh clone can fetch
the same candidate and recompute the semantic/config/upgrade projection rather
than trying to invert a hash. Duplicate PRs, a deleted/moved carrier ref, wrong
head/base, repository mismatch or projection mismatch fail closed.

The coordinator writes a private pending receipt containing the exact
candidate Fence OID, Attempt and those carrier fields before CAS. Only the
machine whose exact Fence candidate wins may begin the provider/direct target
mutation; it marks the receipt `call_started` immediately beforehand. A fresh
clone seeing the durable intent never starts another target write, but may
fetch/requery the bound ref/PR/target and finish an already-proven handoff.
Rejected intent CAS removes a conclusive pending receipt; accepted-response
loss reconstructs intent from Fence and provider/target state. The Attempt
grammar is the same 22-character unpadded base64url token used by PR intents.

The authorized target action is closed by carrier kind.

For `direct_target`, after exact Fence, receipt and carrier verification, the
elected process durably enters `call_started` and issues exactly one fast-
forward push of Head to the resolved target ref with an explicit expected old
OID equal to Base. Head must descend from Base. Rejection is classified from a
fresh target query; only a conclusive zero-effect rejection may reauthorize,
while an ambiguous result is requery-only.

For `github_pr`, the matching receipt holder is the only process that may
prepare or merge the provider PR. After intent publication it requeries target,
carrier ref and the one PR. The PR must be `OPEN`, draft, have the exact head
repository/ref/OID and base the resolved target branch. Making it ready is an
idempotent, requeryable provider preparation, not the target call. The PR is
never merge-called until ready state, required checks, exact head and current
base are verified. Immediately before merge the coordinator requeries again;
pre-call target movement returns the PR to verified draft and enters the zero-
effect reauthorization path. The receipt winner then durably enters
`call_started` and issues exactly one merge-commit-only call bound to the exact
Head. Auto-merge scheduling, admin bypass, squash and rebase are forbidden.

Provider success requires one provider-confirmed merge commit whose second
parent is the recorded Head, whose first parent is the actual target tip at
provider linearization and which is an ancestor of freshly fetched target. If
that first parent differs from carrier Base, the Base-to-first-parent delta
must be disjoint from the bound projection and the final target must still
recompute the exact intent; otherwise the already-mutated target is a manual
incident. A closed-unmerged PR, wrong head/base, squash/rebase result or
multiple merge objects never finishes Fence. `verified` is written only after
these provider and target facts hold.

After `call_started`, an ambiguous provider/transport outcome remains requery-
only. A fresh Attempt is legal only after all-state evidence proves zero target
effect: the matching receipt is still `prepared` and no direct/merge call
began; an exact direct-target lease was conclusively rejected; or the same
provider PR is conclusively unmerged and has been returned to verified draft
after its merge call was rejected. An ambiguous provider state, partial target
effect or merged PR remains requery/manual-only. The newly observed target
delta must be disjoint from the bound source/config/upgrade projection.

Both carrier kinds require their authoring ref, target and Fence to live on the
same upstream remote. Let `F0/R@H0` be the bound Fence and carrier pair. Build
`H1` as the deterministic fast-forward descendant of `H0` that incorporates
the fresh target `T1`, with Base `T1` and the same semantic projection. One
atomic push with explicit leases advances `F0 -> F1` and `R@H0 -> R@H1`; `F1`
retains mode, epoch, intent, carrier kind/ref/object and binds a fresh Attempt,
Head `H1` and Base `T1`. Rejection changes neither ref; accepted-response loss
is complete only when both exact tips verify, and a mixed pair is corruption.
For `github_pr`, bounded provider requery must additionally show the same PR
draft/unmerged at `R@H1`. No rebase, force update, new carrier ref, second PR or
fork carrier is legal. Abort remains forbidden after the first intent.
If ready-to-draft normalization was required before this Git transaction, it
is a separately observed provider mutation: response loss is `uncertain`, and
successful normalization followed by Fence/carrier lease loss is `partial`,
never `none`. Resume requeries that same PR and exact pair; it never creates or
selects another provider object.

Exact Fence record trailers are:

```text
Agentrof-Record: project-fence-v1
Agentrof-Protocol: <selected protocol integer>
Agentrof-Mode: open | source_handoff | configuring | upgrade
Agentrof-Epoch: <22-character epoch token>
Agentrof-Target: <full fetched target OID>
Agentrof-Config-Hash: <canonical governed config hash or none>
Agentrof-Source-Kind: none | requirement_supersession | backlog_revision | designation_reconciliation
Agentrof-Source-Intent: none | sha256:<64 lowercase hex>
Agentrof-Target-Update-Intent: none | sha256:<64 lowercase hex>
Agentrof-Target-Update-Attempt: none | <22-character attempt token>
Agentrof-Target-Repository: none | upstream | github:<canonical-owner>/<canonical-repo>
Agentrof-Target-Carrier-Kind: none | github_pr | direct_target
Agentrof-Target-Carrier-Ref: none | refs/heads/<validated authoring ref>
Agentrof-Target-Carrier-Object: none | pr:<positive decimal> | direct
Agentrof-Target-Carrier-Head: none | <full candidate OID>
Agentrof-Target-Carrier-Base: none | <full expected target OID>
Agentrof-Upgrade-Phase: none | acquired | target_handoff
Agentrof-Upgrade-Contract: none | sha256:<64 lowercase hex>
Agentrof-Handoff-Target: none | <full fetched target OID>
```

Mode combinations are a closed table; every unshown combination is malformed:

| Mode/phase | `Target` | `Config-Hash` | Source fields | Target-update/carrier fields | Upgrade fields |
|---|---|---|---|---|---|
| `open` | Exact current target | Exact current governed hash or `none` | All `none` | All `none` | All `none` |
| `source_handoff` acquired | Immutable source baseline | Exact baseline governed hash or `none` | Real kind and intent | All `none` | All `none` |
| `source_handoff` authorized | Same baseline | Same hash | Same real values | Intent equals Source-Intent; real Attempt and complete carrier | All `none` |
| `configuring` acquired | Immutable config baseline | Desired governed hash | All `none` | All `none` | All `none` |
| `configuring` authorized | Same baseline | Same desired hash | All `none` | Intent equals Config-Hash; real Attempt and complete carrier | All `none` |
| `upgrade/acquired` before intent | Immutable upgrade baseline | Exact baseline governed hash or `none` | All `none` | All `none` | Real contract; Handoff-Target `none` |
| `upgrade/acquired` authorized | Same baseline | Same hash | All `none` | Intent equals Upgrade-Contract; real Attempt and complete carrier | Same contract; Handoff-Target `none` |
| `upgrade/target_handoff` | Same baseline | Same hash | All `none` | Same real intent/Attempt/carrier | Same contract; real current Handoff-Target |

Returning to `open` always clears Source, target-update/carrier and Upgrade fields and
rebinds `Target` plus `Config-Hash` to the exact freshly fetched target. It
never copies an acquisition baseline forward as if it were current.

The target-update group is all-or-none: real Intent and Attempt require all
six carrier fields; absent Intent requires all of them to be `none`.
`Target-Repository` is literal `upstream` for a direct mutation on the exact
resolved default-branch upstream remote that also holds Fence, or
`github:<canonical-owner>/<canonical-repo>` for `github_pr`; owner/repo follows
the provider object's credential-free canonical spelling and direct/provider
forms cannot be interchanged. For `github_pr`, the canonical PR head
repository, target repository and repository hosting `agentrof/fence` must be
identical. A fork-head PR fails before intent publication with
`DELIVERY_PROVIDER_UNSUPPORTED` and `mutation_state: none`; fork support would
require a separate cross-repository state machine and is outside v1.
`Target-Carrier-Ref` is a full validated `refs/heads/*` authoring ref outside
`refs/heads/agentrof/*`. Before merge it must exist at exact Head. The sole
missing-ref exception is provider auto-delete after the exact recorded PR is
`MERGED`: provider history must retain the PR Head, target must contain its
provider-confirmed merge commit with that Head as second parent, and every
projection check above must pass. The exception never covers a moved ref,
closed-unmerged PR, pre-merge deletion, squash/rebase merge or different PR,
and reauthorization is impossible after it applies. For `github_pr`, Object
is `pr:<positive decimal>` and the one provider PR must initially be
draft/unmerged with that ref/head/base. For
`direct_target`, Object is literal `direct`, Head is the reachable candidate
commit and Base is the expected target lease. A carrier head contains the full
approved candidate tree and its Base is an ancestor. Source intent is
recomputed from the immutable Fence acquisition `Target` plus carrier Head;
when carrier Base differs after reauthorization, the acquisition-Target-to-
Base delta must be disjoint from every bound source path. Config and upgrade
intents are recomputed from the candidate tree's exact governed value or
managed/contract projection. The validator rejects a carrier that merely
produces the same final scalar while changing an unbound path.

Fence epoch lifecycle is exact. Freshness is scoped to one logical transition,
defined by operation, approved mutation-plan hash and predecessor leases. Apply
samples only after plan equality and resamples a collision with reachable
Fence/control lineage, another fresh marker in the same plan or a live pending
receipt. Only the CAS winner publishes the token; a losing unpublished
candidate has no remote epoch identity. Every child inside one non-open
operation, including authorization, reauthorization, Delivery barriers,
per-Delivery merge and target handoff, retains its exact operation token.
Finish or legal abort returns to `open` with a new token distinct from every
still-reachable prior operation and clears mode fields. Accepted-response
recovery retains the published token; ambiguity retains its receipt and never
remints. Restoration of one externally lost exact Fence OID is the sole
operation that neither mints nor advances. Cleanup/GC creates no forever-used
token ledger, so historical uniqueness after all evidence is deleted is not a
v1 claim. Epoch remains safe as the receipt filename; separators, dots,
padding, malformed length and reuse against live/reachable evidence fail before
filesystem access.

`Agentrof-Config-Hash` governs exactly one WIP field, not the whole project
config. When `max_parallel` is absent its value is the literal `none`.
Otherwise serialize this exact projection as compact UTF-8 JSON with sorted
keys, no whitespace and no final newline, then prefix the lowercase SHA-256
digest with `sha256:`:

```text
{"max_parallel":3}
sha256:8888f9feb2b8198a46d2f516ce529f7923efeb776c95362535d0f09931ddf1ec
```

No stack, command, designation or other config field participates in this
WIP `Config-Hash`; designation handoff uses the separate Source fields. In mode
`open`, `Agentrof-Target` is the fetched target commit
whose `workspace/config.json` has that exact projection. In mode
`configuring`, `Agentrof-Target` is the exact baseline target and
`Agentrof-Config-Hash` is the desired projection hash. Its
`Target-Update-Intent` is initially `none`; immediately before the config
target PR/direct mutation becomes mergeable it atomically becomes that exact
config hash with a unique Attempt. Recovery may return to `open` only after a fresh target commit
contains the desired hash. Abort is legal only while the target-update intent
is still `none` and all-state provider/target inspection proves no canonical
handoff began; after intent, recovery only requeries or finishes.
Both hosts share golden vectors for `none`, boundary positive integers and
malformed/extra-field rejection.

`source_handoff` binds one approved semantic-source candidate. The source
intent is `sha256:` over compact sorted-key UTF-8 JSON with no whitespace or
final newline and exactly `base_target`, `kind` and `paths`. `base_target` is
the full fetched target OID and `kind` is the matching Source-Kind. `paths` is
a nonempty object keyed by normalized POSIX project-relative path; every value
contains exactly `before` and `after`. Each side is either the literal string
`absent` or an object with exactly `kind`, `mode` and `sha256`: `kind` is
`file|symlink`, `mode` is `100644|100755|120000`, and `sha256` hashes raw file
bytes or raw symlink-target bytes with the `sha256:` prefix. `before` must equal
the entry at `base_target`, `after` must equal the approved candidate, and the
two sides must differ. A deletion uses `after: "absent"`; a creation uses
`before: "absent"`; and a rename is two independently sorted path entries.
Directories are implicit. Empty maps, unchanged entries, absolute/backslash/
dot-segment paths, implicit renames, unlisted keys and noncanonical Git modes
fail before acquisition. This exact projection and digest have cross-host
golden vectors for modify, create, delete, rename, executable-bit and symlink
changes.

Requirement supersession uses the source intent when an incorporated current
Requirement still feeds one or more selectable Stories; the path set covers
both reciprocal Requirement records and every changed map/navigation
projection. Backlog revision uses it whenever an existing selectable Story/
Test Plan byte or path changes; designation reconciliation uses it for all
affected config/title/H1/source-hash paths. Acquire atomically changes Fence
from `open`, then reruns fresh open-Delivery/claim/source validation.
Before the approved branch/PR can update target, Fence sets
`Target-Update-Intent` equal to `Source-Intent` and a unique Attempt;
afterward abort is forbidden.
Finish requires a freshly fetched target whose exact path mapping matches the
intent and a refetched carrier whose head, when compared with the immutable
Fence acquisition `Target`, recomputes that mapping. When reauthorization made
the carrier Base newer than the acquisition target, the acquisition-target-to-
Base delta must be disjoint from every mapped source path and Base-to-Head must
contain the approved candidate change. It then returns Fence to `open` bound
to that target. If Delivery
reservation/claim wins first, source acquisition loses and reclassifies; if
source acquisition wins first, reservation/claim cannot pass until finish.
Out-of-band target mutation without this protocol is a repository incident.

Source fields are `none` outside `source_handoff`. Upgrade fields are all
`none` outside `upgrade`. `Target-Update-Intent` is `none` in `open`, at
source/config acquisition and at upgrade acquisition. For
the complete upgrade epoch, `Agentrof-Target` and `Agentrof-Config-Hash` remain
the immutable acquisition baseline. An `upgrade/acquired` record binds a
host-neutral `upgrade_contract_hash`; handoff target is `none`. Once local
apply has succeeded and before its target update becomes publishable, Fence
sets `Target-Update-Intent` to that contract hash plus a unique Attempt. This is the irreversible
point: target/provider response loss is recovered only by requery/finish and
`abort-upgrade` is no longer legal. The value is
`sha256:` plus lowercase SHA-256 over compact sorted-key UTF-8 JSON with no
final newline and exactly these top-level keys:
`contract_files`, `delivery_protocol`, `managed_payload`,
`migration_adapters` and `transition_writer`.

- `managed_payload` maps every normalized POSIX project-relative package-owned
  setup path, sorted by path, to exactly `kind`, `mode` and `sha256`. `kind` is
  `file|symlink`; directories are implicit. `mode` is the six-digit Git mode
  `100644|100755|120000`; `sha256` hashes raw file bytes or raw symlink-target
  bytes and includes the `sha256:` prefix.
- `contract_files` maps the exact stable host-neutral resource IDs in the
  table below to their raw-byte `sha256:` digests. Host wrapper paths are never
  IDs, and no unlisted file may enter this mapping.
- `delivery_protocol` has exactly `read_min`, `read_max`, `write` and sorted
  unique `transition_writes`, all positive integers with the range rules from
  the package manifest.
- `migration_adapters` maps each supported protocol integer rendered as a
  canonical decimal string to exactly `adapter_id`, `contract_sha256` and
  sorted unique `transition_writes`. Adapter IDs are closed ASCII lower-kebab
  values; the digest binds the adapter's host-neutral semantic contract.
- `transition_writer` is the exact positive protocol integer selected for
  upgrade transition commits and must occur in both the manifest and matching
  adapter entry.

The v1 resource registry is literal:

| Resource ID | Canonical host-neutral source path |
|---|---|
| `vault-policy` | `plugins/software-engineering-team/skill-content/obsidian-vault/data/vault-policy.json` |
| `delivery-document-contract` | `plugins/software-engineering-team/skill-content/deliver/data/delivery-document-contract.json` |
| `delivery-result-contract` | `plugins/software-engineering-team/skill-content/deliver/data/delivery-result-contract.json` |
| `delivery-control-record-contract` | `plugins/software-engineering-team/skill-content/deliver/data/delivery-control-record-contract.json` |
| `delivery-migration-registry` | `plugins/software-engineering-team/skill-content/deliver/data/delivery-migration-registry.json` |
| `delivery-protocol-1` | `plugins/software-engineering-team/skill-content/deliver/data/delivery-protocol-1.json` |

For protocol `1`, `migration_adapters["1"]` has exactly
`adapter_id: "delivery-protocol-1"`, `contract_sha256` equal to the raw-byte
digest of resource `delivery-protocol-1`, and `transition_writes: [1]`.
`delivery-migration-registry.json` is the closed protocol-to-adapter/resource
mapping; future advertised protocol integers require one exact canonical
adapter resource and registry row before a manifest may name them. Build and
manifest validation require exact-set equality among the literal resource
registry, package files, protocol read range and adapter keys. Missing, extra,
host-remapped or duplicate IDs fail before hash calculation. Golden tests use
one complete compact JSON projection/digest and prove that every individual
resource byte, path boundary, adapter ID and protocol mapping mutation changes
the hash identically on Claude and Codex.

This projection covers the canonical project-managed setup payload bytes,
vault/schema policy, compiler-owned migration contract and incoming Delivery
protocol writer. It excludes Claude/Codex wrappers, credentials, timestamps,
JSON source ordering, machine paths and host-specific package manifests.
`upgrade/target_handoff` retains the same contract hash and
epoch and adds the exact fetched target OID whose managed project projection
recomputes to that hash. A different package may resume the epoch only when it
recomputes the identical host-neutral contract. Abort is legal only from
`acquired` while `Target-Update-Intent` is `none` and before any provider/
direct target publication can begin;
`target_handoff` is irreversible and must finish with the incoming package.
Two coordinators proposing the same handoff compete on the exact acquired
Fence OID. After a lost lease, the loser refetches: an identical handoff is
classified as already complete, while a different handoff target or contract
is a collision and fails closed. It never advances the epoch, substitutes a
new acquisition baseline or converts a completed handoff into abort. If target
advances after `target_handoff` but before release, release is blocked. The
incoming transition writer reruns the same-epoch classifier and per-Delivery
upgrade target-merge round against the new target while Fence remains
`upgrade/target_handoff`, then atomically refreshes `Handoff-Target` only after
every open Integration contains that target. Disjoint and relevant non-source
changes follow the normal upgrade classifier.
Any selected-source change first appearing after upgrade acquisition is an
incident regardless of claim count. No Slot or ordinary writer is released
during this convergence.

Before any Fence mutation, governed-config drift is classified independently
from source and upgrade projections:

- `open` requires current target `Config-Hash` to equal the Fence value; an
  unrelated target advance with the same hash may be rebound by the new child;
- `source_handoff` requires both its acquisition baseline and every observed
  current target to retain the same governed hash. It cannot authorize or
  absorb an out-of-band `max_parallel` change;
- `configuring` is the sole mode allowed to move the acquisition baseline hash
  to its exact desired hash, and only through its bound carrier/intent;
- `upgrade` preserves the acquisition governed hash through handoff/release;
  package migration never changes `max_parallel`.

Separately, `upgrade` recomputes and compares the bound Upgrade-Contract;
other modes never interpret that projection. Any unauthorized governed-config
or upgrade-contract drift blocks reservation, claim, activation and finish;
the coordinator never silently adopts it because another target commit was
otherwise unrelated.

#### Closed control-record grammar

Every control record contains exactly one `Agentrof-Record` and exactly one
`Agentrof-Protocol`, followed by exactly the record-specific trailers below.
Every trailer name shown without a prefix in the table is emitted with the
literal `Agentrof-` prefix, for example `Delivery` means
`Agentrof-Delivery`.
Duplicate or unlisted `Agentrof-*` trailers, an unknown record name, malformed
OID/hash/ID/slot/epoch values, wrong parent count/order, illegal tree diff or a
record on the wrong ref fail before mutation. IDs and hashes use the canonical
grammars in this document; an OID is the repository's full lowercase object ID;
an epoch is 16 cryptographically random bytes encoded as exactly 22 unpadded
base64url characters (`[A-Za-z0-9_-]{22}`). It is unique per logical operation:
all Fence, Delivery barrier, Item quiesce/reconcile records and local receipts
belonging to that one operation repeat its exact token, while no distinct
operation acquisition may reuse it while the earlier token remains reachable
or live; a Slot is the
zero-padded numeric key in the current configured range.
`Revert-Order` is a canonical positive base-10 integer beginning at `1`, with
no leading zero, gap or duplicate inside one cancellation candidate; its
Story sequence must be the exact reverse of Integration seal order.
`Cancellation-Phase` is exactly `prepublication|published`. The former is
legal only before any matching `cancellation-finalized-v1` has reached the
Integration ref; the latter requires the complete current terminal cancellation
package and retained Item set already remote. A phase mismatch is corruption,
not a recovery hint.

| Record | Exact record-specific trailers | Parent and tree rule | Atomic mutation and terminal success proof |
|---|---|---|---|
| `project-fence-v1` | `Mode`, `Epoch`, `Target`, `Config-Hash`, `Source-Kind`, `Source-Intent`, `Target-Update-Intent`, `Target-Update-Attempt`, `Target-Repository`, `Target-Carrier-Kind`, `Target-Carrier-Ref`, `Target-Carrier-Object`, `Target-Carrier-Head`, `Target-Carrier-Base`, `Upgrade-Phase`, `Upgrade-Contract`, `Handoff-Target` | One parent: exact prior Fence, or exact target for absent-ref creation; same parent tree. Mode-specific `none`/real-field combinations, carrier bindings and acquired/authorize/reauthorize/handoff transitions are exact | Fence plus the refs named by the operation matrix; exact fetched Fence tip equals candidate, target-update/carrier fields are all absent or complete, the carrier is exact or satisfies the one post-merge auto-delete proof, and no abort crosses an intent |
| `delivery-reservation-v1` | `Delivery`, `Slug`, `Target` | One parent: exact target; tree adds only the complete scope-approved Delivery package/map projection | Fence + absent Integration; both verified at candidate OIDs |
| `delivery-scope-revised-v1` | `Delivery`, `Previous-Scope-Hash`, `Scope-Hash`, `Previous-Target`, `Target` | Same-target revision has one parent, the exact current claims-free Integration. Source/target revision has that Integration first and freshly fetched target second. Tree changes only the approved Scope, exact Item-stub set, deterministic Delivery projections and Plan/evidence invalidation; no Item/Slot ref exists | Open Fence + Integration advance atomically; exact zero-claim proof, new approval/hash, target parent shape and portable tree verify. It is never a claim carrier by itself |
| `execution-plan-published-v1` | `Delivery`, `Scope-Hash`, `Plan-Hash`, `Target` | One parent: exact current claims-free Integration head whose validated history contains the current scope reservation/revision and any prior plan publication/target refresh; tree changes only `execution-plan.md`, selected Item topology/`item_plan_hash`, first draft Code Review/Verification files and deterministic Delivery projections | Open Fence + Integration advance atomically; no Item/Slot/worktree exists, portable tree and exact remote record verify |
| `claims-established-v1` | `Delivery`, `Scope-Hash`, `Plan-Hash` | One parent: the exact effective approved plan carrier defined in section 13.5, using either its validated disjoint-refresh chain or claims-free upgrade-release alternative; same tree | Fence + Integration + all absent Item claims; marker and complete claim set verified |
| `target-refresh-v1` | `Delivery`, `Previous-Target`, `Target`, `Refresh-Mode`, `Plan-Hash` | Two parents: exact current Integration first and freshly fetched target second. `disjoint` changes only the deterministic merge/generated projections and preserves the exact current Plan hash, or uses `none` before approval. Claims-free `plan_invalidated` has an approved Plan, returns that Plan/Item/evidence projection to draft and emits `Plan-Hash: none`; it carries no Item impact mapping | Fence + Integration advance atomically; target ancestry, total classification and portable tree verify. Only `disjoint` with an exact Plan hash may be an effective claim carrier; `plan_invalidated` requires a fresh Plan publication |
| `cancellation-target-refresh-v1` | `Delivery`, `Barrier-Epoch`, `Cancellation-Intent-Hash`, `Previous-Target`, `Target`, `Cancellation-Phase` | Two parents: exact current Integration containing the unmatched cancellation barrier first and freshly fetched target second. `prepublication` contains no finalization/Review and never publishes a local revert candidate; it deterministically merges target while preserving current Delivery-owned product/test entries on conflict and invalidates every local cancellation candidate. `published` requires the prior terminal package/Item set, takes the exact fetched-target entry for every Delivery-owned product/test path, updates cancellation projections and invalidates Review | Open Fence + Integration advance atomically; exact barrier/intent remain current and no Item/Slot ref changes. `prepublication` requires later local revert recomputation; `published` proves an empty target-relative product/test diff and keeps the exact terminal Item refs unchanged |
| `plan-revision-target-refresh-v1` | `Delivery`, `Barrier-Epoch`, `Previous-Target`, `Target`, `Previous-Plan-Hash`, `Target-Impact-Hash` | Two parents: exact Integration containing the unmatched plan-revision barrier first and freshly fetched target second; tree is the deterministic target-neutral merge plus compiler-owned Target Reconciliation projection | Open Fence + Integration advance; zero Slots after success, barrier/impact remain unmatched/current and portable tree passes |
| `upgrade-target-merge-v1` | `Delivery`, `Upgrade-Epoch`, `Upgrade-Contract`, `Previous-Target`, `Target`, `Scope-Hash`, `Plan-Hash`, `Target-Impact-Hash` | Two parents: exact current Integration containing the unmatched same-epoch upgrade barrier and only validated same-epoch/same-contract `upgrade-target-merge-v1` descendants first, bound refreshed target second; `Previous-Target` is the latest validated target in that chain and the impact remains cumulative from the original pre-barrier baseline. Tree follows the closed upgrade classifier, target-neutral conflict rule and transition-readable generated/Target Reconciliation projections | Exact same-epoch `upgrade/acquired` or `upgrade/target_handoff` Fence with the immutable contract-valued Target-Update-Intent advances to a same-mode child with one Integration; all trailers/ancestry, source classification and outgoing readability verify before the next Delivery |
| `item-claim-v1` | `Delivery`, `Story`, `Scope-Hash`, `Plan-Hash` | One parent: exact claims-established marker; tree changes only that Item's compiler-owned `integration_base_commit` to the marker OID plus its resulting source hash | Created only in the complete claim transaction; exact branch tip, Item base and trailers verified |
| `item-activation-v1` | `Delivery`, `Story`, `Claim`, `Item-Plan-Hash`, `Slot`, `Writer-Epoch` | One parent: exact current Item; initial start is same-tree, while resume changes only compiler-owned `paused -> in_scope` status/reason | Fence + Integration authorization + Item + absent Slot; remote Item and Slot equal candidate |
| `item-start-authorized-v1` | `Delivery`, `Story`, `Plan-Hash`, `Item-Tip`, `Target`, `Slot`, `Writer-Epoch` | One parent: exact Integration; same tree | Same start/resume transaction; Integration, Item and Slot exact and `Target` equals the freshly scanned target-refresh baseline |
| `item-takeover-v1` | `Delivery`, `Story`, `Item-Plan-Hash`, `Previous-Tip`, `Slot`, `Writer-Epoch` | One parent: exact previous Item/Slot tip; same tree | Remote Item + existing Slot advance to candidate |
| `delivery-barrier-v1` | `Delivery`, `Barrier-Kind`, `Barrier-Epoch`; cancellation also requires `Cancellation-Intent-Hash`; upgrade also requires `Upgrade-Contract` | One parent: exact Integration, or exact cancellation-intent commit for cancellation; same tree | Operation-matrix refs all move or none; unmatched exact kind/epoch is visible on Integration |
| `delivery-barrier-release-v1` | `Delivery`, `Barrier-Kind`, `Barrier-Epoch`, `Barrier-OID`, `Target`, plus resulting `Plan-Hash` and `Target-Impact-Hash` for revision or `Upgrade-Contract` and `Target-Impact-Hash` for upgrade | One parent: exact current Integration descendant of named barrier; tree may differ only by the approved plan/migration/reconciliation projection. `Target` is the latest validated plan-revision target or exact upgrade `Handoff-Target` | Fence + Integration and affected Items move atomically; release `Target`, returned open-Fence `Target` and every Item reconcile `Target` are equal, the exact matching barrier/impact is released and no other kind is consumed |
| `item-quiesce-v1` | `Delivery`, `Story`, `Barrier-Kind`, `Barrier-Epoch`, `Previous-Tip` | One parent: exact active Item/Slot tip; tree changes only compiler-owned Item status/reason to `paused` | Barrier transaction advances Item and deletes equal Slot; lineage and previous tip verify |
| `item-target-reconcile-v1` | `Delivery`, `Story`, `Barrier-Epoch`, `Target`, `Target-Impact-Hash`, `Previous-Tip`, `Reconcile-Kind`, `Item-Plan-Hash` | `integrated_reopen`: one parent, exact final release Integration head. `unintegrated_rebase`: first parent exact current Item, second parent release head. Tree updates compiler-owned base/plan/evidence; textual claimed-path conflicts take the release/target entry so later Item work owns resolution | Created in the atomic barrier release; no Slot. Exact prior tip remains ancestor, affected evidence is invalid and later activation owns all product resolution |
| `item-integration-v1` | `Delivery`, `Story`, `Item-Plan-Hash`, `Reviewed-Tip`, `Integration-Parent` | One parent: exact ready Item/Slot tip; same tree | Integration merge candidate uses observed Integration as first parent and seal as second; Item advances to seal and equal Slot is deleted |
| `item-reopen-authorized-v1` | `Delivery`, `Story`, `Plan-Hash`, `Failed-Finding`, `Previous-Seal`, `Slot`, `Writer-Epoch` | One parent: exact Integration containing previous seal; tree changes only compiler-owned review/evidence invalidation | Fence + Integration + Item + absent Slot all move; exact remote invalidated state and refs verify |
| `item-reopen-v1` | `Delivery`, `Story`, `Item-Plan-Hash`, `Previous-Seal`, `Slot`, `Writer-Epoch` | One parent: exact reopen-authorization commit; tree changes only that Item's compiler-owned `integration_base_commit` to the authorization OID plus its evidence invalidation/source hash | Same reopen transaction; remote Item and Slot equal candidate |
| `cancellation-revert-v1` | `Delivery`, `Story`, `Barrier-Epoch`, `Cancellation-Intent-Hash`, `Integration-Seal`, `Revert-Order` | One parent: exact current remote cancellation Integration for the first candidate, then the prior local same-epoch revert. It applies the section 17.2 raw-entry algorithm for the exact product-changing Integration whose second parent is the named seal. Target-changed entries take exact current target; untouched entries restore the integration first parent; already-neutral entries permit a same-tree record. No interactive resolution is legal | Exists only in the unpublished cancellation Review candidate chain and reaches remote through the all-or-none final Review publication. Exactly one record per product-changing seal, exact reverse first-parent order and final product/test tree equal to target; wrong seal/order/entry/parent is rejected |
| `cancellation-finalized-v1` | `Delivery`, `Barrier-Epoch`, `Cancellation-Intent-Hash`, `Cancellation-Projection-Hash`, `Target` | One parent: the last local `cancellation-revert-v1` in the exact reverse-order chain, or exact current Integration when no revert is required. Its tree changes only cancelled Item/Delivery dispositions, compiler projections and draft Review; product/test tree equals its parent. On reapproval, dispositions and product/test tree remain exact and only documentation/evidence/current-target projections may change | Prepared locally immediately before the Review child. Initial publication atomically includes the whole revert/finalization/Review ancestry and complete new retained Item children. Re-publication atomically advances Fence + Integration only, verifies the existing terminal Item set byte/OID-exact and rejects any new revert record. Zero Slots; an initial empty Item set is legal whenever no claim exists |
| `delivery-review-published-v1` | `Delivery`, `Reviewed-Integration`, `Approval-Hash`, `Target`, `Cancellation-Intent-Hash` | Normal: one parent is the exact remote pre-stamp Integration baseline. Cancellation: one parent is the local `cancellation-finalized-v1` candidate in the same atomic transaction. Tree changes only `delivery-review.md` approval fields and deterministic review/map projections; `reviewed_integration_commit` equals that parent OID; cancellation intent is literal `none` for normal Delivery | Normal advances open Fence + Integration. Initial cancellation publication also advances the complete retained Item set; cancellation re-publication advances only Fence + Integration after verifying every retained Item already equals its exact terminal tip. If a lifecycle PR exists it is exact draft/unmerged and follows the final candidate; exact remote record/provider head verify |
| `delivery-review-invalidated-v1` | `Delivery`, `Previous-Review`, `Finding-Code`, `Finding-Hash`, `Target`, `Cancellation-Intent-Hash` | One parent: exact current Integration whose ancestry contains the named latest published Review; tree changes only Delivery Review `approved -> changes_requested`, stable finding evidence and deterministic review/map projections. Cancellation intent is `none` for normal Delivery | Open Fence + Integration advance atomically; the exact existing PR is draft/unmerged before mutation and follows the new head after it, or no PR exists; accepted-response loss is reconstructed from record/provider state |
| `pr-creation-intent-v1` | `Delivery`, `Review-Head`, `Target`, `Provider`, `Attempt` | One parent: exact `delivery-review-published-v1` head; same tree | Fence + Integration advance atomically; exact intent tip is durable before the sole provider-create call |
| `pr-adoption-intent-v1` | `Delivery`, `Review-Head`, `Target`, `Provider`, `Pull-Request`, `URL-Hash` | One parent: exact `delivery-review-published-v1` head; same tree | Fence + Integration advance atomically only for one exact provider PR; provider create is forbidden and exact draft head/base must follow the intent tip |
| `pr-url-recorded-v1` | `Delivery`, `Intent`, `Provider`, `Pull-Request`, `URL-Hash` | One parent: exact current Integration descendant of the unmatched creation or adoption intent; tree changes only compiler-owned `pull_request_url`, source hash and status-tag-safe metadata | Fence + Integration advance atomically; provider object, URL digest and portable tree all verify before the same draft PR becomes ready |
| `cancellation-intent-v1` | `Delivery`, `Scope-Hash`, `Target`, `Cancellation-Intent`, `Cancellation-Intent-Hash` | One parent: exact Integration; same tree | Published immediately before the cancellation barrier in the same atomic transaction; fresh clone decodes the canonical intent and recomputes its exact hash |
| `item-cancelled-v1` | `Delivery`, `Story`, `Barrier-Epoch`, `Cancellation-Intent-Hash`, `Disposition`, `Previous-Tip` | One parent: exact retained Item tip under the cancellation barrier; tree changes only compiler-owned Item cancellation projection | Every retained Item ref reaches its exact cancelled tip before cancellation PR; target package and tips agree |

`Reviewed-Integration` is the exact pre-stamp aggregate baseline parent;
`Review-Head` is the exact `delivery-review-published-v1` tip, not that older
baseline. `Approval-Hash` is the current Delivery Review approval digest.
`Cancellation-Projection-Hash` uses the canonical projection in section 8.4.
`Finding-Hash` is `sha256:` over compact sorted-key JSON containing exactly
the closed `code`, normalized evidence `paths`, `refs` and authored finding
text; `Previous-Review` is the exact invalidated publication OID.
These distinctions are validated as wire grammar and prevent either a
self-referential review field or a PR intent that skips publication.

For `upgrade-target-merge-v1`, `Plan-Hash` is the literal `none` when the
Delivery is only scope-approved and has never received an Execution Plan
approval; otherwise it is the exact current approved `plan_hash`. The adapter
derives the phase from the validated Integration package and rejects a missing,
placeholder or stale value. `Scope-Hash` is the exact current approved value or
the exact newly user-approved value for a zero-claim selected-source refresh;
`Target-Impact-Hash` is literal `none` whenever the canonical Item mapping is
empty, including disjoint scope-only and zero-claim source reapproval; the
Scope/Plan hashes bind those decisions. A nonempty mapping carries the exact
cumulative digest. Upgrade therefore covers scope-only, planned,
active, paused and partially/fully integrated open Deliveries without
inventing an Execution Plan.

`item-target-reconcile-v1.Reconcile-Kind` is exactly
`integrated_reopen|unintegrated_rebase`. The first makes the final release
Integration head its sole parent; the prior seal is already an ancestor, and
the Item becomes nonintegrated `in_scope` with its base/evidence invalidated and
later uses ordinary `start-item`. The
second keeps the exact current/quiesced Item as first parent and the release
head as second parent, preserving nonconflicting owned work and history while
setting any textual claimed-path conflict to the release's target-neutral
entry. It preserves `paused` for a paused/quiesced prior tip and otherwise
preserves `in_scope`, so its next verb is exactly resume or start. Both update
`integration_base_commit` to the release head, create no Slot and require the
next active Item commit, Code Review and Verification to own/bind the final
resolution.

For a plan revision with no relevant target reconciliation,
`delivery-barrier-release-v1.Target-Impact-Hash` and every plan-sync
`item-target-reconcile-v1.Target-Impact-Hash` are the literal `none`; those
Item records bind `Target` to the exact release baseline. No reconcile record
is created solely for target impact. Once a barrier-bound target refresh
exists, the exact latest cumulative hash is mandatory on plan approval,
release and every reconcile record.

PR control scalars are closed in v1. `Provider` is the literal `github`.
`Attempt` is 16 cryptographically random bytes encoded as exactly 22 unpadded
base64url characters (`[A-Za-z0-9_-]{22}`), unique among this Delivery's
observed intents. `Pull-Request` is the provider PR number as positive base-10
digits with no sign or leading zero. The canonical URL is exactly
`https://github.com/<owner>/<repo>/pull/<Pull-Request>` with lowercase scheme
and host, no credentials, port, query, fragment or trailing slash; owner/repo
spelling must equal the provider object's canonical names. `URL-Hash` is
lowercase SHA-256 over those UTF-8 URL bytes with no newline. Golden vector:

```text
https://github.com/agentrof/example/pull/17
sha256:eaab7583b5519459bc21ad5ac4f265429ff0d2b17fa9d25e70b57c01893338cd
```

`Disposition` is exactly `not_started`, `integrated_reverted` or
`unintegrated_discarded`. `not_started` is legal only when the Story has no
Item ref and its intent tip is the literal `none`; it creates no
`item-cancelled-v1` record. Pause/retain is chosen before cancellation and is not
a cancellation disposition. `Cancellation-Intent` is unpadded base64url of the
compact UTF-8 canonical JSON projection used for
`cancellation_intent_hash`; decoding, canonical reserialization and digest
equality are mandatory. Resume uses `item-activation-v1` with a new writer
epoch and the exact paused Item parent. Ordinary product, test and evidence
commits contain no `Agentrof-*` trailer and inherit protocol interpretation
only from their validated first-parent control ancestor.

A `pr-creation-intent-v1` or `pr-adoption-intent-v1` is unmatched until its
exact descendant `pr-url-recorded-v1` verifies the same intent/provider object.
While unmatched,
every Integration-mutating verb except that intent's `open-pr` requery and
`record-pr` fails closed, including plan revision, cancellation, reopen,
upgrade, new activation and cleanup. A creation intent alone may authorize the
single provider POST and requires its matching local call receipt. Immediately
before that POST the adapter refetches Integration **and repeats all-state PR
lookup**. If one exact draft head/base PR appeared after intent election, it
performs no POST, verifies that existing PR follows the intent tip and records
its URL through the same intent. Otherwise it requires Integration to equal the
intent OID, calls once, then requires the returned provider head to equal that
OID. An adoption intent never creates a provider call receipt and can never
authorize POST: it only advances the existing exact draft PR's branch head to
the same-tree intent, requeries that PR and records its URL. A raw incompatible
external head change remains a draft provider incident, never an adopted PR.

Control-record terminal proof is remote truth. A transition is remotely
complete when its exact refs, OIDs, ancestry, records and tree rules validate;
any fresh clone can observe that fact without a local receipt. Local writer
readiness is a separate checkout fact requiring the matching verified receipt
and usable Item worktree. Missing receipt is not remote corruption: status still
renders `active`, mutation is denied locally and explicit takeover is offered.

Git's `--atomic` contract is used when claiming multiple item refs: either all
are created or none are. If the server does not support atomic push, v1
activation stops before work starts. There is no unsafe sequential fallback.

The coordinator emits exact-lease commands; users do not assemble them. A
missing item ref uses this logical shape:

```text
git push --atomic \
  --force-with-lease=refs/heads/agentrof/items/auth-01: \
  <remote> <claim-oid>:refs/heads/agentrof/items/auth-01
```

Bare `--force-with-lease` is never used. The empty expected value means the
remote ref must not exist.

### 13.3 Commit and PR naming

Machine control commits use fixed English subjects and the closed
trailers defined below. Human-authored summaries follow the project's
`terminology_language`, while IDs, keys and trailer names stay English.

Every closed `Agentrof-Record` has exactly one subject function:

| `Agentrof-Record` | Required subject pattern | Example |
|---|---|---|
| `project-fence-v1` | `Fence project in <mode> mode` | `Fence project in upgrade mode` |
| `delivery-reservation-v1` | `Reserve <DLV-ID>` | `Reserve DLV-001` |
| `delivery-scope-revised-v1` | `Revise scope for <DLV-ID>` | `Revise scope for DLV-001` |
| `execution-plan-published-v1` | `Publish execution plan for <DLV-ID>` | `Publish execution plan for DLV-001` |
| `claims-established-v1` | `Establish claims for <DLV-ID>` | `Establish claims for DLV-001` |
| `target-refresh-v1` | `Refresh target for <DLV-ID>` | `Refresh target for DLV-001` |
| `cancellation-target-refresh-v1` | `Refresh cancellation target for <DLV-ID>` | `Refresh cancellation target for DLV-001` |
| `plan-revision-target-refresh-v1` | `Refresh plan target for <DLV-ID>` | `Refresh plan target for DLV-001` |
| `upgrade-target-merge-v1` | `Merge upgrade target for <DLV-ID>` | `Merge upgrade target for DLV-001` |
| `item-claim-v1` | `Claim <STORY-ID> for <DLV-ID>` | `Claim AUTH-01 for DLV-001` |
| `item-activation-v1` | `Activate <STORY-ID> for <DLV-ID>` | `Activate AUTH-01 for DLV-001` |
| `item-start-authorized-v1` | `Authorize <STORY-ID> for <DLV-ID>` | `Authorize AUTH-01 for DLV-001` |
| `item-takeover-v1` | `Take over <STORY-ID> for <DLV-ID>` | `Take over AUTH-01 for DLV-001` |
| `delivery-barrier-v1` | `Quiesce <DLV-ID> for <kind>` | `Quiesce DLV-001 for plan-revision` |
| `delivery-barrier-release-v1` | `Release <DLV-ID> <kind> barrier` | `Release DLV-001 plan-revision barrier` |
| `item-quiesce-v1` | `Pause <STORY-ID> for <DLV-ID> <kind>` | `Pause AUTH-01 for DLV-001 upgrade` |
| `item-target-reconcile-v1` | `Reconcile <STORY-ID> for <DLV-ID>` | `Reconcile AUTH-01 for DLV-001` |
| `item-integration-v1` | `Seal <STORY-ID> for <DLV-ID>` | `Seal AUTH-01 for DLV-001` |
| `item-reopen-authorized-v1` | `Authorize reopen of <STORY-ID> for <DLV-ID>` | `Authorize reopen of AUTH-01 for DLV-001` |
| `item-reopen-v1` | `Reopen <STORY-ID> for <DLV-ID>` | `Reopen AUTH-01 for DLV-001` |
| `cancellation-intent-v1` | `Record cancellation intent for <DLV-ID>` | `Record cancellation intent for DLV-001` |
| `cancellation-revert-v1` | `Revert <STORY-ID> from <DLV-ID>` | `Revert AUTH-01 from DLV-001` |
| `cancellation-finalized-v1` | `Finalize cancellation for <DLV-ID>` | `Finalize cancellation for DLV-001` |
| `item-cancelled-v1` | `Cancel <STORY-ID> for <DLV-ID>` | `Cancel AUTH-01 for DLV-001` |
| `delivery-review-published-v1` | `Publish delivery review for <DLV-ID>` | `Publish delivery review for DLV-001` |
| `delivery-review-invalidated-v1` | `Invalidate delivery review for <DLV-ID>` | `Invalidate delivery review for DLV-001` |
| `pr-creation-intent-v1` | `Prepare PR creation for <DLV-ID>` | `Prepare PR creation for DLV-001` |
| `pr-adoption-intent-v1` | `Adopt existing PR for <DLV-ID>` | `Adopt existing PR for DLV-001` |
| `pr-url-recorded-v1` | `Record PR for <DLV-ID>` | `Record PR for DLV-001` |

The validator derives the expected subject from the parsed record and its
closed trailers; mismatch is corruption. Its record-name set must equal this
table's set exactly. Ordinary non-control commits use these separate patterns:

| Commit | Required subject pattern | Example |
|---|---|---|
| Product/test change | `<STORY-ID>: <imperative summary>` | `AUTH-01: validate the SAML audience` |
| Item evidence-only | `<STORY-ID>: update delivery evidence` | `AUTH-01: update delivery evidence` |
| Integration sync into item | `Merge <integration-ref> into <item-ref>` | `Merge agentrof/deliveries/dlv-001 into agentrof/items/auth-01` |
| Item into integration | `Merge <STORY-ID> into <DLV-ID>` | `Merge AUTH-01 into DLV-001` |

Normal commits may add explanatory bodies. They must not invent Agentrof
trailers. Reservation, claim and activation trailers are written and parsed
only by `delivery_git.py`.

PR naming is fixed:

```text
Title: DLV-001: <Delivery Goal>
Head:  agentrof/deliveries/dlv-001
Base:  <resolved target branch>
```

The PR body is generated from the exact scope, DoD evidence, quality results,
deviations, risks and approved Delivery Review.

### 13.4 Project-local worktree paths

```text
<main-worktree>/.agentrof/agent-marketplace/.runtime/worktrees/
└── dlv-001/
    ├── integration/
    └── items/
        ├── auth-01/
        └── auth-02/
```

The same ignored runtime owns cooperative writer receipts outside the
worktrees:

```text
<main-worktree>/.agentrof/agent-marketplace/.runtime/writer-receipts/
└── dlv-001/
    └── auth-01.json
```

The receipt contains only Delivery ID, Story ID, exact Item ref, Slot ref,
candidate/current activation, resume, reopen or takeover OID, its opaque writer epoch and
`pending|verified` local phase. It contains no
person, agent, host, session or credential. It is created only after this
machine prepares the unique candidate, first as `pending`; remote verification
atomically promotes it to `verified`. If the remote accepted the candidate but
the response was lost, replay may promote only that exact pending
OID/epoch after remote equality validation. A conclusive rejected push whose
observed refs remain at the recorded preconditions removes the pending receipt;
an ambiguous result preserves it until remote classification. A fresh observer or a machine that
lost the pending receipt may derive remote `active` status but is not locally
writer-ready and must use explicit takeover. Every canonical active-item push
requires a verified receipt whose epoch matches the nearest validated
activation/resume/reopen/takeover record in the current remote Item lineage. Fetching a newer
tip never adopts or rewrites the receipt; loss or mismatch requires explicit takeover. This is
cooperative coordinator fencing, not a security claim against a user who
bypasses hooks with the same Git credentials.

PR creation uses a separate ignored local call receipt:

```text
<main-worktree>/.agentrof/agent-marketplace/.runtime/provider-receipts/
└── dlv-001-pr.json
```

It contains only Delivery ID, exact `pr-creation-intent-v1` OID, provider,
head/base, opaque attempt and phase `prepared|call_started|verified`. The
winning coordinator writes `prepared` before the Fence + Integration intent
push. After remote intent verification and immediately before the one provider
POST, it atomically writes `call_started`; after exact provider requery it
writes `verified`. A matching `prepared` receipt may resume before the call.
Once `call_started` exists, response loss or process death permits requery
only, never a second create. A fresh clone or lost receipt likewise performs
all-state lookup only. If no lifecycle PR can be found after the provider's
bounded consistency window, v1 reports a manual provider incident rather than
risk a second PR. This receipt contains no token, authenticated URL, user,
agent, host or session identity.

Every source/config/upgrade target handoff uses a separate ignored local call
receipt:

```text
<main-worktree>/.agentrof/agent-marketplace/.runtime/target-update-receipts/
└── <fence-epoch>.json
```

It contains exactly the Fence epoch, exact pre-intent Fence OID, candidate
Fence OID, mode, source/config/upgrade intent hash, opaque Attempt, all six
durable repository/carrier fields copied from that Fence candidate and phase
`prepared|call_started|verified`; none is optional. The winning
coordinator writes `prepared` before the Fence intent CAS, promotes to
`call_started` immediately before the one authorized provider/direct target
mutation and writes `verified` only after exact provider/target requery.
Accepted-response loss preserves the receipt and permits requery only. A fresh
clone without it can prove the durable Fence intent and finish/reconcile a
matching already-existing handoff, but can never initiate a second target
write. A conclusively rejected Fence intent CAS removes only its matching
`prepared` receipt; an ambiguous result preserves it until ref classification.
If no matching target or provider object is discoverable after the
bounded consistency window, the Fence remains conservative and reports a
manual incident. Receipts contain no token, authenticated URL, user, agent,
host or session identity.

Both external-call receipt families use one exact process-shared election.
Each receipt has a sibling `<receipt-name>.lock`; before creating, reading,
replacing or deleting the receipt, a process obtains a crash-releasing
exclusive OS advisory lock on that file, rejects symlinks and reloads the
complete canonical receipt plus remote intent/Fence preimage while holding the
lock. A different live `prepared` candidate is never overwritten. The
`prepared -> call_started` transition is exact-preimage compare-and-replace,
not an unconditional rename: the winner writes a same-directory temporary
file, fsyncs it, atomically replaces the receipt, fsyncs the parent directory
and only then may issue the one external call. `call_started` or `verified`
never grants another caller authority. A crash before durable replacement
releases the OS lock and permits the same exact `prepared` candidate to resume;
a crash afterward, including before the external call, permits requery only.
Verification and cleanup use the same lock/preimage rule. If process-shared
locking, atomic replacement or the required fsync semantics are unavailable,
provider/direct mutation fails preflight. Lock files are disposable local
coordination, not semantic truth.

A proved zero-effect target reauthorization uses that same lock to replace
only the exact prior Attempt receipt with the new `prepared` Fence+carrier
candidate before the atomic remote push; a byte/preimage mismatch is requery-
only. Remote rejection restores/removes only that matching new receipt, while
accepted-response loss classifies the exact two-ref result before any call.

- Paths are deterministic local conveniences and are never written into
  Markdown.
- The runtime anchor is always the registered main worktree, even when a
  command starts inside an integration or item worktree. The coordinator
  canonicalizes `git rev-parse --git-common-dir`, matches it to
  `git worktree list --porcelain -z`, verifies the candidate contains the
  project `workspace/config.json`, and refuses ambiguous or bare layouts.
- Live worktrees are discovered through `git worktree list --porcelain -z`,
  not by reading worktree internals or keeping a registry.
- Each main-worktree anchor creates at most one local item worktree, but another
  machine may retain a stale physical worktree after takeover. Remote item/slot
  equality, not directory existence, is the sole writer authority.
- Setup and upgrade must preserve this subtree and refuse to remove a live
  worktree.
- Cleanup removes only a clean worktree whose reviewed commits are safely
  present on the expected remote branch or merged target.
- Deleting project-local runtime manually is outside the correctness boundary;
  pushed branches permit recovery, but uncommitted work cannot be recovered
  and a deleted writer receipt requires explicit takeover before another push.

### 13.5 Delivery reservation and story claim protocol

The item branch is both the implementation branch and the global story claim.
The integration branch reserves only the Delivery ID; it never reserves a
story.

Delivery ID reservation creates the complete scope-approved package commit
from the exact fetched target with these closed trailers:

```text
Agentrof-Record: delivery-reservation-v1
Agentrof-Protocol: 1
Agentrof-Delivery: DLV-001
Agentrof-Slug: saml-authentication
Agentrof-Target: <full target oid>
```

Execution Plan approval creates one no-product-change claim commit per item
from a common claims-established marker:

```text
Agentrof-Record: item-claim-v1
Agentrof-Protocol: 1
Agentrof-Delivery: DLV-001
Agentrof-Story: AUTH-01
Agentrof-Scope-Hash: <scope hash>
Agentrof-Plan-Hash: <plan hash>
```

The coordinator validates trailers, exact parent, item path, story/test hashes
and plan hash before treating a branch as a claim. Natural-language commit
messages are never parsed as protocol.

The **effective approved plan head** is the exact
`execution-plan-published-v1` commit
followed only by validated carrier descendants that preserve or explicitly
reapprove the exact current `scope_hash` and `plan_hash`: disjoint
`target-refresh-v1/Refresh-Mode: disjoint`, or a claims-free matching upgrade chain ending in its
exact `delivery-barrier-release-v1`. An upgrade release is a legal carrier only
when no Item ref existed throughout that epoch, its ancestry contains the
matching plan publication or the zero-claim source reapproval embedded in the
upgrade merge, its latest Target/Scope/Plan/impact values match the current
package and no unrelated semantic/control descendant intervenes. Thus both an
unchanged plan and a source-reapproved plan can claim after upgrade without a
second user gate. The marker is a same-tree child of that exact effective
head:

```text
Agentrof-Record: claims-established-v1
Agentrof-Protocol: 1
Agentrof-Delivery: DLV-001
Agentrof-Scope-Hash: <scope hash>
Agentrof-Plan-Hash: <plan hash>
```

The claim transaction:

1. Fetch target, Fence, Integration, Item and Slot refs without pruning
   unrelated refs; require Fence mode `open`.
2. Require the effective approved plan head to contain the latest validated
   target. If target advanced, run the phase-exact delta classifier: a disjoint
   `target-refresh-v1` preserves the same plan hash and becomes the new
   effective head. A path/contract-relevant delta with no Item refs publishes
   `target-refresh-v1/plan_invalidated`, carries no Item impact hash, creates no
   barrier or Item ref and cannot become an effective head; a fresh
   `execution-plan-published-v1` is required before retrying. A selected
   Story/Test source change instead requires the approved two-parent
   `delivery-scope-revised-v1` followed by a fresh Plan publication.
3. Verify every selected story/test remains byte-equal to its pinned source
   hashes and still has a current eligible Requirement source under the latest
   validated root coverage. Historical eligibility pinned by an already
   claimed Delivery never authorizes this first claim.
4. Create the claims-established marker on the exact effective approved plan head
   and make every closed claim commit a direct child of that marker. Each claim
   tree changes only its own Item's compiler-owned
   `integration_base_commit` to the marker OID plus the resulting source hash;
   it does not touch another Item or product path.
5. In one atomic push, advance the Fence to a unique `open` child,
   compare-and-swap Integration from the effective approved plan head to the marker and
   create every missing item branch with absent-ref leases. Every ref is a real
   mutation; neither Fence nor plan-head lease can be optimized away.
6. If the transaction is rejected, refetch and classify every involved ref.
   Unrelated Fence churn with the same effective approved Integration tip
   and every proposed item ref still absent retries the exact same claim set
   using a new Fence child. An Integration advance or any Item branch
   appearance is a real plan/claim collision and never follows that retry
   path. Atomic push guarantees the failed attempt itself created no partial
   claim.
7. Inspect the current refs: the same Delivery marker plus the complete exact
   claim set means explicit resume; another open Delivery means collision;
   merged history means the story is already delivered.
8. Do not report claim completion, create an item worktree or modify product
   code until every claim is verified remotely.

This closes the race where two machines both observe an absent branch. The
remote, not the local precheck, decides the winner.

Worked claim example:

```text
Remote has no agentrof/items/auth-01
DLV-001 atomically creates that real implementation branch -> AUTH-01 claimed
DLV-002 tries the same absent-ref creation -> remote rejects it
DLV-002 changes scope or waits; it never steals/overwrites the branch
```

There is no separate claim file, claim branch, lock row or database record.

The losing Delivery has no partial claims because the transaction is atomic.
It has exactly three legal outcomes:

1. **Wait for cancellation only**: it remains non-executable
   `awaiting_claims` while the competing Delivery is nonterminal. It may retry
   the unchanged claim set only after that Delivery's cancellation is on
   target, its claim is lease-released and the selected story/test hashes are
   still exact. A successful predecessor merge makes this option permanently
   invalid because the story is delivered.
2. **Revise before claims**: only when this Delivery has zero item/slot refs and
   no product/evidence work, `revise-unclaimed-scope` returns scope and any
   Execution Plan to draft. It may change the exact story set or accept new
   approved source hashes for the same still-unclaimed stories. In either case
   both Scope and Execution Plan approvals become stale, hashes are recomputed
   and fresh approvals are required. An unrelated backlog append changes only
   the target baseline and does not rewrite pinned selected-story hashes. The
   verb atomically advances the Fence plus the same Integration ref to
   `delivery-scope-revised-v1`. A voluntary same-target revision uses one
   Integration parent; a selected-source/target revision uses the exact old
   Integration first and freshly fetched target second. Both variants publish
   the newly approved Scope and invalidate/remove the old Plan approval and
   evidence projection. The revision record is never an effective claim
   carrier: a fresh, separate `execution-plan-published-v1` approval is
   mandatory before claims. History is preserved; no Delivery ID is replaced
   and no branch is rewritten. Response loss is reconstructed from the exact
   record, while any Item appearance or stale source/target makes the candidate
   lose with no partial scope change.
3. **Cancel**: use the scope-only cancellation path and merge its exact
   disposition record before cleanup.

“Wait” never means the loser can execute after the winner successfully
delivers the same story. Any source-hash change while waiting also forces the
second path or cancellation before another claim attempt.

An integration reservation left by a crash is never silently deleted and its
ID is never reused. Reconciliation either resumes a valid partial Delivery or,
after explicit user confirmation, completes a cancellation record on that
same branch and merges the record to target before lease-protected cleanup.
Unique authored descendants are never discarded by recovery.

### 13.6 Global execution slots

`max_parallel` is the number of slot branches from `001` through the configured
value. Its user-facing meaning is the maximum simultaneously active Delivery
Item count across the project, hosts and machines. It caps valid remote writer
rights, not stale physical directories that may remain on another machine.

To start an item:

1. Fetch target plus the Fence, require Fence mode `open`, and read the
   target's current positive `max_parallel`.
2. Require every same-Delivery backlog/execution predecessor item in the
   Integration ref and every external predecessor's exact
   `dependency_bindings` or `waits_for_bindings` claim/source proof in its
   named open Delivery or verified target merge. A planned, cancelled or
   dynamically re-claimed dependency is not execution readiness.
3. Read the exact last scanned target from the latest validated
   `target-refresh-v1`, or the reservation target when no refresh exists. If
   target advanced, diff that baseline to the new target before mutation and
   rerun the global normalized path/contract overlap scan against every
   noncancelled, nonclosed claimed Delivery. If the delta is disjoint,
   atomically merge it through `target-refresh-v1`, rerun the portable/conflict
   checks and use that target OID in start authorization. Before the affected
   Item integrates, **any** normalized `path_claims` overlap or declared/
   scanner-detected `contract_claims` collision is relevant and blocks for
   plan revision and reapproval, even when the proposed topology, claims and
   ordering text would remain identical. There is no unreviewed “same plan”
   merge exception for a claimed implementation surface. Before Item claims
   exist, `target-refresh-v1/plan_invalidated` followed by a new
   `execution-plan-published-v1` approval converges a path/contract case;
   selected-source change uses `delivery-scope-revised-v1` first. Neither path
   creates a barrier, Item ref or target-impact digest. After claims exist,
   compute the complete affected set and apply section 12's sole
   lifecycle matrix. Every nonempty mapping selects the barrier-bound
   `plan-revision-target-refresh-v1`, fresh Plan approval and atomic Item
   reconciliation, including an all-integrated or mixed set. A change that
   cannot fit the approved scope/plan without
   altering an integrated projection requires cancellation or a compensating
   Requirement/Story.
   Activation never silently starts from an old or unscanned target.
4. Read the latest barrier control record on the integration branch's
   first-parent history. Reject start/resume when a plan-revision,
   cancellation or upgrade barrier lacks its exact matching release.
5. Reject an item whose exact tip carries `item-integration-v1` or is already
   an ancestor of the current integration head. A sealed/integrated item can
   never be restarted.
6. Read exact remote item and slot refs. If a slot already equals this item's
   current remote tip, a matching verified local receipt means continue with no
   ref mutation; a missing/stale receipt requires explicit takeover. It is not
   `resume-item`. That verb is reserved for a compiler-owned `paused` Item with
   no Slot and creates a new writer epoch plus one absent Slot.
7. Choose the lowest missing slot in the current range.
8. Create a unique no-product-change activation commit on the exact observed
   item tip. It carries `Agentrof-Record: item-activation-v1`, Delivery ID,
   Story ID, claim OID and a random fencing nonce.
9. Create a same-tree integration child with
   `Agentrof-Record: item-start-authorized-v1`, Delivery ID, Story ID, slot and
   fencing nonce, plus a unique `open` Fence child.
10. Write the exact candidate OID/epoch receipt locally as `pending`; no agent
   or worktree may write product bytes yet.
11. In one atomic push, compare-and-swap Fence, Integration to
   that authorization commit, item to the activation commit and the absent
   slot to that same item commit. All four refs really change, so project or
   Delivery barrier creation and activation cannot both win from the same
   observation.
12. Verify all remote refs and refetch target before promoting the receipt. If
    target still equals the `Target` trailer, promote only the matching pending
    receipt to `verified`, then create/open the item linked worktree and start
    agent work. If target advanced in the scan-to-CAS window, do not grant
    writer readiness: use the exact pending candidate to atomically pause the
    just-activated Item and delete its Slot, then run the delta classifier. A
    disjoint delta refreshes target and may resume with a new epoch; a relevant
    delta follows plan-revision/reopen/cancellation rules. Any pause race is
    classified from current control refs, and no product byte is written.
    Resume, reopen and takeover use the same prepare-pending -> remote CAS ->
    verify-target -> promote sequence.
13. If remote activation and receipt verification succeed but linked-worktree
    materialization fails before any agent/product write, run the closed
    pre-writer pause path. It requires the remote Item and Slot to equal the
    verified activation tip, no registered worktree, or only a partially
    registered worktree whose `HEAD` and index are provably clean/equal, and no
    descendant beyond the activation control record. Build a paused child from
    that remote tip in the private operation repository and atomically advance
    Item/delete Slot without touching or removing the colliding filesystem
    path. Dirty, ambiguous or unequal partial materialization keeps the Slot
    and receipt and becomes `DELIVERY_WORKTREE_UNSAFE`; no broad cleanup or
    second Slot is attempted. Response loss is classified from the paused tip
    and missing Slot before receipt cleanup.

Logical command shape:

```text
git push --atomic \
  --force-with-lease=refs/heads/agentrof/fence:<observed-fence-oid> \
  --force-with-lease=refs/heads/agentrof/deliveries/dlv-001:<observed-integration-oid> \
  --force-with-lease=refs/heads/agentrof/items/auth-01:<observed-item-oid> \
  --force-with-lease=refs/heads/agentrof/slots/001: \
  <remote> \
  <new-open-fence-oid>:refs/heads/agentrof/fence \
  <start-authorization-oid>:refs/heads/agentrof/deliveries/dlv-001 \
  <activation-oid>:refs/heads/agentrof/items/auth-01 \
  <activation-oid>:refs/heads/agentrof/slots/001
```

If another machine wins a different item slot, refresh and try another free
slot. If another machine advances this same item, its item-ref lease rejects
the whole transaction; it must not retry a second slot. If no slot is free,
the item remains `in_scope / claimed`.

- While an item is active, its item ref and slot ref must equal the same current
  OID. Every product, test, evidence, block or unblock push is one atomic
  transaction that advances both refs from the same observed OID to the same
  new OID under explicit leases. A slot is therefore a rolling writer fence,
  not a fixed initial token.
- `blocked` retains its slot because execution is still live. The block commit
  advances both refs atomically.
- Pausing creates the compiler-owned `paused` item commit and, in one atomic
  transaction, advances the item ref to it while lease-deleting the exact slot.
  Normal post-work pause requires the linked worktree clean,
  local `HEAD` equal to the freshly verified remote Item/Slot tip and no index,
  untracked or unpushed change. Otherwise it returns
  `DELIVERY_WORKTREE_UNSAFE` with `mutation_state: none`, preserves the Slot and
  writer receipt, and names commit/push-item or explicit cleanup as the next
  action. The story claim remains through the item ref. Only after remote
  success verification is the local receipt deleted; accepted-response loss
  requeries the exact candidate before cleanup. If later local cleanup fails,
  its old epoch still cannot pass the remote lineage/Slot check. The only
  no-worktree exception is step 13's pre-writer path; it proves that no product
  work began and uses the exact activation control tip, never a general bypass
  of cleanliness.
- A takeover is an explicit recovery action. It atomically advances the item
  branch to a new fenced activation commit and moves the existing slot from
  its exact old item tip to the same new tip. The old writer's next item/slot
  transaction fails closed. Only the winning machine writes the new local
  receipt. A stale clone that merely fetches the takeover tip still lacks the
  matching local epoch and cannot push without another explicit takeover.
- `max_parallel` has no default and may be absent until activation. Once a
  value has been committed, it may only stay equal or increase in v1.
  `configure` checks target history and rejects a decrease. This monotonic rule
  prevents a stale clone from opening a slot that a newer lower cap removed.

Slot refs are protected operational coordination truth. They are not semantic
project knowledge, but they are authoritative while work is active and cannot
be reconstructed safely from item branches alone. A missing or malformed slot
is a corruption finding requiring explicit reconciliation; the system never
silently creates a replacement.

Delivery-level quiescence uses a durable integration barrier, not a transient
empty-slot observation:

```text
Agentrof-Record: delivery-barrier-v1
Agentrof-Protocol: 1
Agentrof-Delivery: DLV-001
Agentrof-Barrier-Kind: plan-revision | cancellation | upgrade
Agentrof-Barrier-Epoch: <22-character epoch token>
```

For `plan-revision` and `cancellation`, the barrier transaction advances the
Fence to a unique child whose mode remains `open`. For `upgrade`, the
same atomic transaction advances it to the unique
`project-fence-v1` child whose mode is `upgrade` and whose epoch binds
the project upgrade. In all three cases it advances Integration to the
same-tree Delivery control commit, advances only currently active Item refs to
their own quiesce children and deletes their occupied slots under exact leases
in one atomic push. Slotless and already sealed/integrated item refs remain
byte-exact; the Integration barrier prevents their activation without
destroying their ancestry proof. Any concurrent start/resume updates the Fence
plus Integration and an Item, so one transaction loses and must
refetch. The latest barrier stays active until a
same-tree `delivery-barrier-release-v1` commit names the exact kind and epoch.
Plan revision and compatible upgrade release it only after every item has the
approved projection/base; cancellation never releases it. Claim, start,
resume, push and integration verbs fail closed while a matching release is
absent, except the narrowly allowed compiler/coordinator migration operations.

Barriers never stack, nest or supersede one another. The closed matrix is:

| Current unmatched barrier | Permitted next control action | Rejected until resolved |
|---|---|---|
| none | begin `plan-revision`, begin `cancellation`, or acquire project `upgrade` when its other preconditions pass | none |
| `plan-revision` | barrier-bound target refresh plus `finish-plan-revision` or legally bounded `abort-plan-revision` for the exact epoch | cancellation, upgrade, ordinary refresh, another revision and all writer activation |
| `cancellation` | cancellation target refresh, disposition/revert/review, its one cancellation PR and closure only | plan revision, upgrade, ordinary PR, release, reopen and writer activation |
| `upgrade` | incoming compatibility/migration plus exact `finish-upgrade`, or bounded pre-target-handoff `abort-upgrade` | plan revision, cancellation, ordinary PR and writer activation |

Every begin/acquire operation scans all relevant integration first-parent
control records and refuses an unmatched barrier. Upgrade acquisition is
all-or-none across the project and refuses when any open Delivery is already in
plan revision or cancellation. Cancellation never silently supersedes a plan
revision: the revision must first finish, or `abort-plan-revision` must restore
the last approved semantic plan with an additive compiler-owned commit and
release the exact revision epoch. No history is reset or rewritten. Release is
valid only for the currently unmatched kind/epoch and is never applied out of
order.

Each active-item child has the closed record below, uses the current item tip
as its sole parent and changes the item semantic status to `paused` before its
slot is deleted. This includes an item previously `blocked`, so the
`blocked => slot` invariant is never broken.

```text
Agentrof-Record: item-quiesce-v1
Agentrof-Protocol: 1
Agentrof-Delivery: DLV-001
Agentrof-Story: AUTH-01
Agentrof-Barrier-Kind: plan-revision | cancellation | upgrade
Agentrof-Barrier-Epoch: <matching Delivery barrier epoch>
Agentrof-Previous-Tip: <full item OID>
```

Resume/migration verifies this exact lineage. Cancellation later replaces the
temporary paused projection with the explicit `item-cancelled-v1` record; plan
revision or upgrade may return it to `in_scope` only through the matching
barrier release protocol.

With `max_parallel: 2`, only `agentrof/slots/001` and `002` are legal. Two
active items may hold them even when they belong to different Deliveries. A
third item remains claimed but receives no valid writer right until one exact
slot is released. A stale worktree may still exist on a fenced-out machine,
but it cannot push. Slots identify neither a person nor an agent; they are only
remote WIP permits.

### 13.7 CI and branch-rule impact

Because portable hosted remotes reliably support branches, slots use
`refs/heads/`. They may otherwise trigger redundant CI.

Project CI must:

- ignore `agentrof/fence` push events;
- ignore `agentrof/slots/**` push events;
- run focused checks on `agentrof/items/**` when configured;
- run full integration checks on `agentrof/deliveries/**` and PRs;
- require the portable vault/delivery gate on the final PR;
- forbid squash and rebase merge for Delivery PRs;
- require the PR branch to be current with target before merge.

Branch/ruleset policy must permit the Delivery integrator to create/update and
lease-delete Fence, Item, Slot and Integration refs and, only during proved
target reauthorization, fast-forward the exact already-bound ordinary carrier
ref in the same atomic push as Fence. It has no general authority over other
authoring refs. The implementation may inspect and report provider policy. It
must not mutate external repository settings without explicit user
authorization.

## 14. Delivery Execution

### 14.1 Item work boundary

An item branch may change only:

- product/test paths covered by approved `path_claims`;
- its own `items/<story-id>/item.md`;
- its own `code-review.md` and `verification.md` through the owning
  orchestrator;
- explicitly approved generated artifacts for those paths.

It may not change:

- backlog or Requirement documents;
- `delivery.md`, `execution-plan.md` or `delivery-review.md`;
- another item's folder;
- root maps or global generated views;
- product paths outside approved claims.

An unexpected path or contract expansion pauses the item. The Software
Architect revises the Execution Plan, global conflicts are rechecked and the
user reapproves before work resumes. Requirement scope expansion returns to
Requirement Flow instead of being absorbed into Delivery.

An active item never uses a plain branch-only push. `delivery_git.py` pushes
the new commit by atomically advancing both the item ref and its equal slot ref
under their exact old-OID leases. Local commits may accumulate while a tool is
working, but they become remotely authoritative only through that fenced
transaction. A stale worktree can retain local bytes; it cannot pass the remote
fence after takeover, pause, integration or cancellation.

The mechanical ownership diff is always calculated from the exact
`integration_base_commit` last merged into the item to the current item tip.
Changes merely inherited from integration are therefore excluded. Conflict
resolution that changes the result relative to that base remains in the item
diff, must fit `path_claims`, and invalidates review/verification.

Path claims are mechanically complete because changed paths are enumerable.
Before an authored write, the hook lexically normalizes the requested path,
resolves every existing parent symlink and rejects any result outside the
registered project root or approved Item worktree. A symlink itself may be a
claimed Git change, but it never authorizes writing through that link to an
external path.
Contract claims are mechanically checked for syntax, duplicates, declared
overlap and any configured deterministic project scanner. Completeness of a
semantic contract claim that has no deterministic scanner is an explicit
Software Architect and Code Review evidence lens; the plan does not pretend a
generic compiler can infer every API, data or security contract from a diff.

### 14.2 Role sequence

The story retains exactly one implementation `owner_role` and optional
supporting roles. The Execution Plan adds a role sequence, not an assignee.

Typical sequence when Software Architect and UX Designer are declared
supporting roles for the story:

```text
Software Architect
  -> owner developer and supporting implementation roles
  -> Code Reviewer
  -> UX Designer verification when applicable
  -> QA Engineer
```

- The Product Owner owns Delivery scope and the final Delivery Review.
- The Software Architect owns Execution Plan topology, claims and integration
  decisions.
- Developers own product code and tests within claims.
- Code Reviewer and QA remain read-only evaluators; the orchestrator persists
  their canonical records.
- UX Designer performs built-experience verification only when that Story's
  exact approved planning-source set contains a constraining Design System or
  Experience Design output and the Story declares the role/responsibility and
  applicable built-experience scenario. Another Requirement's disposition does
  not create a second Delivery truth for a multi-Requirement Story.
- Product Owner integration and final-review decisions occur at Delivery gates
  and are deliberately not encoded as an item role-sequence member.
- No Scrum Master, Delivery Manager, PMO role or named person is introduced.

### 14.3 Item quality loop

For each item:

1. Merge the latest remote integration branch into the item branch, record its
   exact OID as `integration_base_commit`, and validate the owned diff. Never
   rebase a published item branch.
2. Implement product code and automated tests.
3. Run configured focused tests and developer self-verification.
4. Run code review and persist one evolving record.
5. Fix findings on the same branch and retain stable finding IDs.
6. Run built UX/accessibility verification when applicable.
7. Run QA coverage audit, suite, mutation and runtime protocols as applicable.
8. Require code review and verification to bind the same last product/test
   change commit.
9. Run `check-item-ready`. Readiness is derived from the current exact remote
   item tip, approved review, passed verification, DoD evidence, allowed
   evidence-only paths and the writer's valid slot. It is never persisted as a
   second status.

There is no fixed review-round limit. A changed product/test commit
invalidates prior approval/pass automatically.

### 14.4 Serialized item integration

Integration is a compare-and-swap operation on the remote integration branch:

1. Fetch its exact remote object ID.
2. Fetch and pin the exact remote item and slot tips. Require both refs to equal
   the same OID and that item tip to contain the observed integration head and
   complete ready evidence, not merely the older `reviewed_commit`.
3. Require current code-review approval and QA pass bound to the same last
   product/test commit.
4. Create a same-tree child of the item tip with closed
   `Agentrof-Record: item-integration-v1`, Delivery, Story, item-plan-hash and
   reviewed-tip plus observed Integration-parent trailers. This unique
   integration seal is the new item tip.
5. Create an explicit unreferenced merge-commit candidate OID from a detached
   temporary index. Its first parent is the observed Integration head and its
   second parent is the integration seal. Do not create a temporary branch/ref
   and do not move the local Integration ref.
6. In one atomic push perform three real mutations with exact leases:
   integration advances from the observed head to the candidate, item advances
   from the ready tip to the seal, and the equal slot ref is deleted. Git is
   never asked to treat an up-to-date/no-op refspec as a CAS predicate. Any item
   write, takeover or competing integration rejects the entire transaction.
7. On success, verify all remote refs and then fast-forward the local
   integration checkout and delete the consumed local writer receipt. On lease
   rejection, discard only the unpublished temporary candidate and refetch all
   three refs. Retry by merging a new Integration head only when Item and Slot
   remain equal at the exact previously observed tip and the verified receipt
   is still current. If Item or Slot changed, classify its closed control
   record: an equal newer pair means another writer/takeover won; an absent
   Slot plus paused/quiesced, sealed/integrated or cancelled Item follows that
   state and never retries activation/integration implicitly; divergence is
   corruption. A competing integration is reconciled from its exact seal.
   Every legal retry reruns affected review/verification.
8. Keep the sealed item branch. Its existence continues to claim the story until the
   parent Delivery merges or is explicitly cancelled.

Path and contract analysis reduces collisions but cannot prove semantic
independence. Every item therefore integrates current upstream state and the
final Delivery runs aggregate gates.

### 14.5 Explicit reopen after integration

Normal `start-item`/`resume-item` never restarts a sealed item. When an
aggregate check, open-PR check or user review produces a concrete finding that
requires product/test changes, `reopen-item` is the only legal path:

1. Require Fence mode `open`, no active Delivery barrier, an unmerged parent
   Delivery, exact failed finding evidence and an item tip that is already an
   ancestor of current integration.
2. Invalidate the current Delivery Review and that item's code-review and
   verification evidence through the compiler. Scope expansion still returns
   to Requirement Flow; reopen cannot change story scope or claims.
3. Create an `item-reopen-authorized-v1` integration child containing the root
   evidence invalidation.
4. Create an `item-reopen-v1` item commit whose parent is that authorization
   commit and whose tree contains the item/evidence invalidation plus that
   Item's `integration_base_commit` set to the authorization OID. It therefore
   contains the complete current integration head while advancing the sealed
   item branch fast-forward.
5. Atomically advance Fence, Integration and Item refs and
   create one free slot pointing to the reopen item commit under exact leases.
6. Return through the normal implementation, review, verification, integration
   seal and aggregate gate. The existing open PR updates from the same
   integration branch; a second PR is never opened.

A reopen without the exact failure and invalidation preconditions is rejected.

## 15. Final Delivery Review, PR and merge

### 15.1 Aggregate gate

After every item is integrated and no slot remains for a normal Delivery, or
after every Item has its exact terminal cancellation disposition for a
cancelled Delivery:

1. Run the phase-exact target refresh against the latest fetched target. A
   normal Delivery uses `target-refresh-v1`: a disjoint delta merges directly,
   while any nonempty path/contract impact enters the plan-revision barrier,
   receives fresh impact-bound approval and atomically creates
   `integrated_reopen`/`unintegrated_rebase` tips; restart, re-review, reverify
   and reintegrate those Items before the aggregate gate continues. A
   cancelled Delivery instead uses
   `cancellation-target-refresh-v1` under its exact irreversible barrier. It
   never reopens or reactivates a cancelled/discarded Item. Before the first
   cancellation publication, `prepublication` refresh invalidates the local
   revert/finalization candidate and requires it to be rebuilt against the new
   target; no revert or terminal Item tip reaches remote. After terminal
   publication, `published` refresh keeps every terminal Item ref exact, takes
   target entries on Delivery-owned product/test paths, invalidates Review and
   requires the target-relative product/test diff to remain empty. If the
   existing intent/dispositions cannot be preserved, the
   Delivery remains fenced as a manual incident rather than changing them.
   If the PR already exists, either path keeps that same PR draft under the
   post-PR linearization contract.
2. Recheck cross-Delivery dependencies and change claims.
3. Run the complete configured test command.
4. Run mutation analysis when configured.
5. Run from-scratch environment and live verification when applicable.
6. Run combined-diff code review, security and architecture checks.
7. Run combined UX/accessibility verification when applicable.
8. Verify every pinned DoD row from the exact historical DoD commit/blob and
   every item evidence record; a newer target DoD neither invalidates nor
   silently changes this Delivery.
9. Render the final tracked `maps/delivery.md` addition and require the
   portable gate to prove the exact final package. No tracked map change is
   deferred until after merge.
10. Render `delivery-review.md` and ask the user to approve PR opening.
11. After approval, `publish-delivery-review` atomically publishes one
    `delivery-review-published-v1` child. For cancellation it publishes the
    locally reviewed zero-or-more `cancellation-revert-v1` chain,
    `cancellation-finalized-v1`, every first terminal
    `item-cancelled-v1` child and the Review child in the same all-or-none
    transaction; no cancellation revert/finalization reaches remote before
    this user gate. On later cancellation Review publication, every terminal
    Item tip is verified exact but remains unchanged; only Fence + Integration
    publish the fresh finalization/Review ancestry. If the same lifecycle PR already
    exists, it must first be draft/unmerged at the exact parent head and must
    follow the new child after publication. A target already stale at the
    mandatory pre-push refetch, or a stale Integration, review hash or provider
    state, rejects with no publication. Target cannot be a no-op CAS member:
    if it advances only after that refetch while the Fence/Integration push
    wins, publication is a proven `partial` result, never false success. Keep
    the PR draft, publish the exact Review invalidation, run the phase-exact
    target refresh and require fresh aggregate Review approval. For
    cancellation, already-published cancelled Item tips remain historical and
    dispositions stay immutable while target refresh/refinalization converges.
    Accepted-response loss is reconstructed from Fence, Integration and
    provider head without a second approval commit.

`reviewed_commit` is the last commit that changed product or test paths;
`reviewed_integration_commit` is the exact remote pre-stamp Integration parent
whose complete tree the user approved. It is not the publication commit's
self-referential OID. `delivery-review-published-v1` is its sole approval
child and may change only review/map projection bytes. Another target merge or
product change invalidates both bindings and this gate. PR creation/adoption
intent must be a child of that verified publication record.

### 15.2 PR creation

After user approval:

- require Fence mode `open` and no unmatched plan-revision or
  upgrade barrier. A normal PR also rejects an unmatched cancellation barrier;
  the separately validated cancellation-PR path requires that exact barrier;
- query the provider first for every lifecycle PR whose head repository/ref is
  the exact Integration branch and whose base is the resolved target,
  including `OPEN`, closed-unmerged and merged records. More than one is
  corruption. Apply this total classifier before any provider create:

  | Integration lifecycle evidence | Provider result | Sole legal outcome |
  |---|---|---|
  | Canonical matched intent plus URL record | Exact `OPEN` draft or ready PR | Reuse it. Make/verify draft while updating head/title/body, then perform the provider-only ready transition after gates; no new intent, URL record, adoption or POST |
  | Canonical matched intent plus URL record | Exact closed-unmerged PR | Reopen that same PR when supported, make/verify draft and reuse it; unsupported reopen is `DELIVERY_PR_STATE_INVALID`, never a second PR |
  | Canonical matched intent plus URL record | Temporarily absent | Bounded all-state requery, then manual incident if still absent; never create |
  | Unmatched creation intent | Exact `OPEN` draft/ready or closed-unmerged PR | Normalize that same PR to reopened draft as needed, then URL-record it through the existing creation intent; no POST or adoption intent |
  | Unmatched creation intent | None after bounded consistent lookup | Only the local holder of that intent's prepared receipt may issue the one POST below |
  | No lifecycle intent/URL record | Exact `OPEN` draft/ready or closed-unmerged PR | Normalize that same PR to reopened draft as needed, then atomically publish one adoption intent and one URL record; provider create count remains zero |
  | No lifecycle intent/URL record | None | Elect one creation intent below |
  | Any | Exact merged PR without the required canonical URL record, or more than one matching PR | Repository incident; no adoption, POST or second PR |

  Normalization is idempotent: `OPEN` ready becomes draft; closed-unmerged is
  reopened and made draft only when the adapter proves that exact provider
  object supports it. Provider response loss is resolved by requery. If
  normalization changed provider state but a later Git intent CAS loses,
  `mutation_state` is `partial`; the winner is rediscovered and no caller may
  POST. After every normalization and intent CAS, reverify exact PR object,
  head/base and draft/unmerged state. An administrator merge is the existing
  fail-closed incident path;
- when no lifecycle PR and no existing intent exist, prepare the local
  provider receipt and atomically advance Fence + Integration to one
  `pr-creation-intent-v1` child of the exact verified
  `delivery-review-published-v1` head. A losing clone or
  any clone without that intent's prepared receipt performs lookup only;
- after verifying a creation intent, repeat the total all-state classifier.
  Any exact unmerged PR is normalized and recorded through that same intent;
  perform no POST. Only when lookup still proves none, mark the local receipt `call_started` and issue
  exactly one provider create call for a **draft** PR from the Integration
  branch to the resolved target. GitHub draft-PR support and prohibition on
  merging a draft are v1 adapter preflight capabilities. An existing intent is
  never interpreted as permission for a new caller or a second create;
- immediately before that POST, refetch Integration and require its exact tip
  to equal the intent OID; after response/requery, require the provider head to
  equal the same OID. Any mismatch keeps the PR draft and fails closed;
- use title `DLV-001: <Delivery Goal>`;
- generate the PR body from Delivery Goal, exact stories, DoD, tests, findings,
  risk, demonstration and Delivery Review;
- record the returned PR number/URL through the compiler-owned
  `pr-url-recorded-v1` transition only for first creation/adoption; a canonical
  existing lifecycle record is preserved unchanged during reapproval;
- permit one evidence-only commit for PR metadata;
- prove every change after `reviewed_commit` is inside the current Delivery
  evidence subtree, and prove `record-pr` changed only the compiler-owned
  `pull_request_url`, `source_hash` and status-tag-safe metadata;
- query the provider adapter to prove the PR head is the integration ref, the
  base is the resolved target, state is draft/open and no second lifecycle PR
  exists for that ref;
- while the PR is still draft, atomically publish the URL metadata commit,
  verify the provider head contains it and rerun the portable package gate;
  only then mark that same PR ready for review/merge. On reapproval, perform no
  URL commit: verify the historical canonical record, current head/base/title/
  body and portable package, then use an idempotent provider-only ready update.
  Response loss converges by requerying those exact fields. A normal merge gate
  never accepts a draft, stale PR body or a head lacking the one URL record.

The v1 verified path uses the GitHub adapter. It may use authenticated `gh` or
the GitHub API, but both hosts must expose the same capability and error model.
An unsupported provider was already rejected before activation; this section
has no alternate manual execution path. Agentrof does not claim it can detect
arbitrary PRs without provider access.

Provider creation and local URL persistence are intentionally idempotent
across a crash boundary. If PR creation succeeds but its response or the
`record-pr` push is lost, the next `open-pr` discovers that same head/base PR,
validates it and records its URL without opening another PR or changing the
existing `approval_hash`.

The provider adapter must also expose an atomic uniqueness guarantee for one
open PR per exact head repository/ref and base; GitHub's duplicate-PR rejection
is defense in depth and part of the v1 capability check. Query-then-create is
not treated as a lock: the remote Integration intent CAS elects the only local
receipt allowed to call. Two coordinators may both observe none, but exactly
one intent wins and provider create call count remains one. A
duplicate/conflict response or any ambiguous/lost response enters requery-only
classification. Exactly one matching lifecycle PR resumes; more than one fails
closed as corruption; none after the bounded consistency requery is a
provider-uncertain/manual-incident result. Neither the current nor a later
invocation issues another create from that spent intent. A PR closed before
URL persistence remains visible to all-state lookup; if lookup itself cannot
prove it, safety wins over automatic liveness.

If an administrator bypasses provider policy and merges the pre-metadata draft
head, `record-pr` never advances Integration after that merge. The target
package lacks the required final URL projection, so normal closure fails with
a repository-incident finding; the URL remains derivable from provider
evidence, but repair is an explicit out-of-lifecycle documentation procedure,
not a hidden post-merge commit or second Delivery PR.

### 15.3 Merge contract

Before merge:

- Project Fence must be `open`, and normal Delivery must have no
  unmatched Delivery barrier. A cancellation merge instead requires the exact
  irreversible cancellation barrier and cancelled package predicate;
- the provider adapter must reconfirm merge-commit-only policy and required
  remote CI/checks must be green;
- the exact lifecycle PR must be `OPEN`, `draft=false`, and its head must
  contain the compiler-owned PR URL metadata commit;
- target must not have advanced beyond the reviewed integration base;
- if target advanced, use the phase-exact ordinary or cancellation target-
  refresh record, keep the same lifecycle PR draft and repeat the aggregate
  gate;
- the user must explicitly request merge or perform it through the provider;
- merge method must create a merge commit preserving the integration head as
  an ancestor of target.

When Agentrof performs the GitHub merge, it uses the equivalent of
`gh pr merge --merge --match-head-commit <exact-pr-head>` and never uses
`--admin`, squash, rebase or an automatic bypass of required checks.

Squash and rebase merges do not close a v1 Delivery. They produce an explicit
repository-incident finding because reviewed commit ancestry is lost. If one
occurs despite preflight, claims and refs remain held and Agentrof performs no
automatic second PR, revert or fabricated content-equivalence closure. A
merged provider PR cannot be updated or reopened, so any repository repair is
an explicitly user-authorized manual incident procedure outside the supported
one-PR lifecycle. This v1 boundary is reported before activation and is tested
to fail closed rather than promise an impossible same-PR recovery.

### 15.4 Merge-derived closure

No post-merge Markdown state commit is required.

The board derives `closed` only when:

1. the Delivery folder first appears on the fetched target branch in the
   provider-confirmed PR whose state is `MERGED`, method is merge-commit and
   exact required-check conclusions for its final head are successful;
2. that merge commit has the pre-merge target as first parent and the exact PR
   integration head as second parent, and that first parent is itself an
   ancestor of the integration head (proving the reviewed head contained the
   current target);
3. provider head/base identity still matches the recorded Delivery PR, and the
   exact PR head contains no unmatched plan-revision/upgrade barrier; a
   cancellation closure instead proves its expected unmatched cancellation
   barrier and cancelled package;
4. the approved `reviewed_integration_commit` is an ancestor of that PR head;
   a normal executed Delivery also proves its real `reviewed_commit` is an
   ancestor of `reviewed_integration_commit`, while a phase-exact cancellation
   applies the optional-field rules in section 9.7;
5. the PR head contains exactly one compiler-owned URL-record commit whose
   canonical URL equals the provider object; a pre-record draft merge is a
   repository incident, not normal closure;
6. commits after `reviewed_integration_commit` change only compiler-owned PR
   metadata/evidence paths and same-tree control records;
7. the exact Delivery package and relations pass the portable gate.

After closure verification:

- delete item, slot and integration remote refs with exact observed-OID
  leases;
- remove only clean linked worktrees;
- for each deleted Item ref, reconcile the same-named local branch only after
  proving no worktree has it checked out. Absence is complete. When its exact
  observed local OID equals, or is an ancestor of, either the pre-deletion
  validated terminal Item OID or the target-resident
  `cancellation_previous_tip` for that Story, and the local objects validate
  that lineage when present, delete it with
  `git update-ref -d <ref> <observed-local-oid>`. The target cancellation/merge
  closure and absence of the old remote claim are mandatory; the discarded
  Item object itself need not remain target-reachable after approved cleanup.
  A checked-out,
  divergent, descendant, uniquely committed or otherwise unverifiable local
  ref is retained and reported as `DELIVERY_LOCAL_REF_DIVERGED`; it is never
  reset, repointed or force-deleted. This local finding does not recreate the
  remote claim, but later same-clone reselection must reconcile it before
  creating the deterministic branch/worktree;
- rerender only the ignored primary board and verify that the tracked delivery
  map already equals the version merged by the PR;
- refetch all Agentrof refs and, only when no open Delivery, configuration
  transition or upgrade remains, lease-delete the exact observed
  `agentrof/fence` tip. A concurrent new operation wins its Fence CAS and makes
  this optional cleanup fail safely;
- report cleanup failures without reversing an already proven merge.

A later Delivery may now depend on this Delivery because its reviewed head is
proven inside target. PR merge is not a production release.

Fence mode is a current preflight condition, not historical closure evidence.
The Fence is intentionally deletable and its ancestry is not a ledger, so a
clean clone must never need to prove its mode at merge time. Configuration of
`max_parallel` changes coordination capacity, not Delivery semantics. If an
external merge races `configuring`, exact Integration-head, provider,
required-check, merge-parent and barrier proofs above still determine closure.
Plan revision, cancellation and upgrade remain durable because they advance
the Integration head with their own barrier records.

Every Integration mutation after a lifecycle PR exists is linearized against
provider merge using durable target and Integration facts, not deleted Fence
history. This includes PR URL recording, target refresh, plan-revision begin,
cancellation begin, reopen and upgrade acquisition. Except for initial URL recording while
the PR is already draft, the adapter first converts the exact PR back to draft
and verifies it unmerged. After the Git CAS it immediately refetches provider
and target **before** reporting transition success, opening a worktree,
running cancellation reverts or continuing migration.

If the transition wins first, the provider PR head advances from the reviewed
head, so expected-head merge is stale and all applicable gates must rerun
before that same PR becomes ready again. If the exact prior reviewed head
reaches target first, normal merge closure wins: the later Git transition is
superfluous, no new writer or semantic continuation begins, and the command
reports that it did not establish cancellation/reopen/revision/upgrade. Pure
same-tree/compiler-control descendants are exact-lease cleaned after closure.
Any unique semantic descendant is retained under its current ref and reported
for explicit discard/repair; it is never silently deleted or treated as part
of the merged Delivery. The pre-metadata draft bypass is the stricter incident
defined above and `record-pr` never runs post-merge.

For upgrade specifically, a merge-first Delivery is excluded from migration;
its same-tree upgrade barrier and claims are closure-cleaned while upgrade
continues for remaining open Deliveries. Thus transition-first fences and
merge-first closes for every post-PR operation, without a historical Fence-mode
predicate.

## 16. Obsidian experience

### 16.1 Active Delivery vaults

Each active integration worktree contains a complete project vault and its
Delivery files. It may be opened as a separate Obsidian vault. Item worktrees
also contain the project vault but are execution surfaces, not global boards.

`/deliver DLV-001 status` calls the read-only coordinator `locate` verb and
returns the canonical absolute integration-worktree vault path plus an
`Open active Delivery vault` action when the host supports it. The primary
board shows the same path as plain text. It never fabricates a Wikilink to a
file absent from the primary branch.

### 16.2 Primary board

The primary checkout renders an ignored local board by:

1. listing/fetching Fence, Integration, Item and Slot refs;
2. reading active documents with `git show <ref>:<path>` without checkout;
3. deriving separate semantic and coordination states from documents, refs
   and ancestry;
4. combining target-resident closed and cancelled Delivery records;
5. showing human WIP as `used / max_parallel`;
6. reporting orphan item refs, orphan slots, duplicate IDs, stale evidence,
   unmet dependencies and path/contract conflicts.

The default board shows Goal, exact Scope, semantic state, derived progress,
current blocker, next user action and human WIP. It does not expose ref names,
OIDs, Fence mode/epoch or CAS terminology. A separate explicit Diagnostics
view and machine JSON contain exact refs, OIDs, epochs and recovery evidence.
Active remote-only documents use a plain `Open active Delivery vault` action,
not false Wikilinks. Target-resident closed or cancelled Delivery links are
normal resolvable Wikilinks through `maps/delivery.md`.

If fetch fails, the renderer returns nonzero and leaves the previous board
marked with its old fetch commit and timestamp. It must never present cached
remote state as fresh.

### 16.3 Backlog views

Backlog story and epic authored statuses remain planning statuses. Obsidian
boards join story links to Delivery Items and display derived values such as
`unclaimed`, `claimed`, `active`, `integrated` and `delivered`. This avoids two
writers updating the same story file.

Exact projection rules:

| Backlog object | Delivery projection |
|---|---|
| Story | `delivered` only when one successfully closed Delivery proves its item in the target merge; a cancelled Delivery does not count |
| Epic | `partially_delivered` when at least one current story is delivered; `delivered` when every nonsuperseded current story is delivered |
| Requirement | `incorporated` only when the shared `requirement_incorporated` predicate passes; `delivered` only when that exact nonempty current-story set is entirely delivered; `resolved_no_change`, `withdrawn` and `superseded` remain distinct terminal outcomes |
| Backlog | Remains approved planning truth; it has no execution-complete status |

These values are generated views only. Delivery does not edit the Requirement,
epic, story or root backlog file to mark completion.

## 17. Failure, resume and cancellation

### 17.1 Standard recovery

| Failure | Required behavior |
|---|---|
| Remote unavailable | Draft planning may continue; activation and shared transitions stop |
| Item branch collision | Same Delivery offers explicit resume; another Delivery blocks |
| No free slot | Item remains `in_scope / claimed`; no local work starts |
| Lost host/session | Inspect the existing slot and perform explicit fenced takeover; never create a second slot |
| Slot exists, item branch missing | Corruption finding; explicitly restore the branch from the exact validated current slot-tip OID under an absent-ref lease, preserving all pushed product/evidence commits; never silently release |
| Open Delivery expects an Item ref but both Item and Slot are absent | Fail closed with the exact missing-ref finding. Restore only an OID proven by an Integration merge parent, target ancestry, provider reflog or another verified clone and only after full record/tree validation; sealed Items are normally recoverable from Integration ancestry. If no exact object is recoverable, retain the Delivery as a manual repository incident; never synthesize a claim/paused tip or free the story |
| Item appears active but slot is missing | Corruption finding; stop all writes and reconcile explicitly, never reconstruct a slot |
| Item branch exists, no slot | Classify the exact control tip first: claimed/in-scope may start, paused requires resume/takeover, sealed/integrated rejects restart, cancelled remains terminal until target cancellation and cleanup |
| Slot remains after integration | Verify ancestry, then lease-delete as stale coordination state |
| Integration lease rejected | Merge new integration head into item, rerun gates and retry |
| Review or QA fails | Same record and stable findings evolve; independent items may continue |
| PR checks fail | Reopen the affected item worktree, fix, reintegrate and repeat aggregate review |
| PR is closed without merge | Fail closed and retain all refs. The user may explicitly reopen that exact provider PR; if the provider cannot reopen it, report a manual repository incident rather than opening a second Delivery PR |
| Target advances | Merge target into integration and repeat aggregate gate |
| Cleanup fails after merge | Delivery remains closed; `reconcile` retries exact safe cleanup |
| Orphan integration reservation | Resume its validated partial package or formally cancel it; never reuse the ID or drop unique commits |
| Fence mode remains `source_handoff` | Requery the bound approved path mapping and target. Abort only before Target-Update-Intent and after proving no provider/direct handoff began; after intent, finish or report a repository incident |
| Fence mode remains `configuring` | Compare the bound baseline target and desired config hash with fresh target; finish when target contains it. Abort only before Target-Update-Intent and before any provider/direct handoff; after intent, requery/finish |
| Fence mode remains `upgrade` | Resume setup/migration or run the exact acquired-and-no-Target-Update-Intent abort protocol; never clear the Fence by deleting the ref |
| Bound target carrier is missing, duplicated or mismatched | `DELIVERY_TARGET_CARRIER_INVALID`; retain the Fence and perform no provider/direct mutation. Restore/repair only the exact named carrier or use same-intent atomic Fence+carrier reauthorization after conclusive zero target effect. The sole missing-ref success is an exact provider-merged, auto-deleted branch whose historical head and merge-commit ancestry pass the closed proof |
| Target-update response remains ambiguous | `DELIVERY_TARGET_UPDATE_UNCERTAIN`; retain Fence and receipt, perform requery only and never call, abort or reauthorize until the outcome becomes conclusive |
| Fence ref is absent while any Integration, Item or Slot ref exists | Repository incident; fail closed. Restore only the exact validated lost OID when still recoverable; otherwise require explicit out-of-protocol repository repair. No ordinary verb recreates or guesses `open` |

Normal carryover does not exist because Delivery has no timebox. A persistent
blocker pauses the same Delivery. Work does not move merely because a date
arrived.

### 17.2 Cancellation

- An unpublished local scope proposal has no ID/ref and may be discarded. A
  remotely reserved Delivery is already scope-approved and requires the full
  cancellation protocol; there is no ambiguous abandoned remote draft.
- First render a read-only exact scope/tip/disposition preview. If the user
  wants work retained or continued, use pause/resume and do not begin
  cancellation. An approved or active Delivery enters cancellation only after
  the user explicitly confirms cancellation plus provisional disposition of
  every item. Cancellation also requires Fence mode `open` and no unmatched
  Delivery barrier; a plan revision must finish/abort first, and project
  upgrade must finish/abort before cancellation can begin.
- A pre-barrier Request changes or Stop result leaves Fence, Integration,
  Item, Slot, files and provider objects byte-exact and returns the same
  `/deliver DLV-###` entry. Once the barrier wins, Stop is only a safe pause of
  the cancellation workflow, not a return to Delivery execution; Request
  changes may revise cancellation documentation/evidence and, before initial
  publication, rebuild the exact deterministic local reverse chain from the
  same seals/current target. It may not add a revert or rewrite the bound
  intent, reason, Story dispositions or pre-quiesce tips.
- After that decision, atomically install a
  compiler-owned `cancellation-intent-v1` Integration commit followed by the
  `cancellation` Delivery barrier and quiesce every selected Story that
  currently has an active Item/Slot pair. A scope-only cancellation has no
  Item refs to quiesce; its exact `not_started`/`none` intent entries and the
  Fence + Integration barrier transaction are the complete durable proof.
  The intent commit binds the user-approved cancellation reason, `scope_hash`,
  exact pre-quiesce Item-tip set, provisional disposition per Item, target
  baseline and `cancellation_intent_hash`. The barrier repeats that hash.
  Advance the Fence plus Integration through both commits, create one
  unique quiesce commit for each active item tip, advance those item refs and
  delete their occupied slots in the same multi-ref transaction. Slotless and
  integrated refs remain exact; start/resume cannot cross the integration
  barrier. Any lost lease aborts the whole transaction before cancellation is
  established and requires a fresh preview. Once established, the cancellation
  barrier is irreversible and never released. A lost response or host can
  reconstruct the exact approved intent from Integration plus each quiesce
  record's previous-tip binding; it never asks for a different disposition
  after continuation has become impossible.
- Only after quiescence may the coordinator refetch and bind the exact final
  item tips used by the cancellation decision.
- Every cancellation continuation also refetches target before producing or
  merging its review. If target advanced, the coordinator first drafts the
  existing lifecycle PR when one exists, then atomically publishes
  `cancellation-target-refresh-v1` with the exact cancellation barrier epoch,
  intent hash, target baseline and phase. `prepublication` preserves current
  Integration-owned product/test entries on a conflict, publishes no local
  revert/finalization/Item candidate and invalidates the complete local
  candidate so the reverse-order chain is rebuilt from this refreshed head.
  `published` requires the existing terminal package, takes the exact fetched-
  target entry on Delivery-owned product/test paths, keeps every terminal Item
  ref exact and invalidates Review for a new Integration-only publication.
  Neither phase calls `reopen-item`. Target-owned changes are not reverted
  merely because they touch a formerly claimed path. Before approval the later
  local candidate, and after publication the refresh itself, prove that all
  Delivery-owned integrated product/test effects are neutralized relative to
  the refreshed target and that the final PR product/test diff is empty. The provisional Item dispositions remain those
  bound by the irreversible intent. If an additive merge/revert cannot
  preserve them and the documentation-only target diff, the Delivery stays
  fenced with a manual incident; it never escapes by reopening an Item,
  replacing the intent or starting a second cancellation.
- An active branch is never auto-deleted.
- Before the barrier, work that should continue resumes the same Delivery; it
  never manufactures a new Delivery to bypass claims.
- If work is discarded, first push every recoverable item/integration commit
  for inspection and record each exact item OID as
  `integrated_reverted` or `unintegrated_discarded` in the Delivery Review.
  A scope-only Story instead remains `not_started` with tip `none`; it never
  receives a fabricated inspection OID or an `item-cancelled-v1` branch.
  Nonintegrated product commits are not cherry-picked into integration. If the
  user wants to retain or continue them, cancellation is the wrong action and
  the same Delivery remains paused instead.
- An `unintegrated_discarded` OID is explicitly a pre-cleanup inspection
  identifier, not durable archived Git history: after exact-lease branch
  deletion the remote may garbage-collect it. The durable target record is the
  user-approved disposition, affected-path summary and reason. Work that must
  remain recoverable is paused, not discarded.
- If no product/test change has been integrated, the existing integration
  branch is stamped cancelled and its final target diff must contain only the
  cancelled Delivery knowledge package.
- If product/test changes have already been integrated, build the reverse chain
  mechanically from raw Git tree entries; no text patch, heuristic three-way
  inverse or interactive conflict choice is legal. Let `B` be the target bound
  by cancellation intent, `T` the exact target after every required
  prepublication cancellation refresh, and `G1..Gn` the product/test-changing
  Integration merges in first-parent order. For each `Gi`, `Pi` is its first
  parent, its `Integration-Seal` trailer names the exact Item seal second
  parent, and `delta_i` is the raw product/test tree-entry delta `Pi..Gi`.
  Evidence-only/same-tree integrations create no revert record; one Story may
  have multiple seals after reopen and each product-changing seal is distinct.
  A rename is its delete plus create entries, and bytes, symlink target, mode
  and absence are all part of entry equality.

  Process `Gn..G1`. For every path in `delta_i`, set the current candidate as
  follows: when `B..T` changed that path, copy exact `T[path]` or delete it when
  absent; otherwise, when the candidate entry equals `Gi[path]`, restore exact
  `Pi[path]`; when it already equals `Pi[path]` or `T[path]`, leave it unchanged
  because a later reverse step already neutralized the path; every other entry
  is `DELIVERY_CANCELLATION_FINALIZATION_STALE` and publishes nothing. Paths
  outside `delta_i` remain exact. A target-dominated step may therefore be a
  same-tree control commit, but it is still required for that changing seal.
  Missing, extra, duplicate or reordered seal records fail validation. The
  final product/test tree must equal `T` exactly.

  Prepare one local `cancellation-revert-v1` per required seal in that exact
  order. The first parents the current remote cancellation Integration and
  each next parents the prior candidate. Do not push these commits separately,
  reset or rewrite history. Their complete chain is part of the later all-or-
  none Review publication. Rerun aggregate tests and prove the final target-
  relative product/test diff is empty and the PR contains only the cancelled
  Delivery knowledge package plus this explicit revert history. After initial
  publication no additive revert record is legal; a later published target
  refresh preserves an empty product/test diff and changes only the allowed
  target-owned/projection/evidence surface.
- Before Delivery Review approval or PR opening, the compiler prepares the
  exact local zero-or-more revert chain, its `cancellation-finalized-v1`
  Integration candidate, approved Review child and every retained
  `item-cancelled-v1` tip. Nothing in this
  finalization set reaches remote while the user may still Request changes or
  Stop. On Approve, `publish-delivery-review` atomically advances the exact
  open Fence, current Integration containing the matching irreversible
  barrier, the complete revert/finalization/Review ancestry and the complete
  retained Item set; no Slot may exist. A claims-free scope-approved or
  execution-approved Delivery has an empty ref set and never fabricates one;
  its Stories remain `not_started/none` and phase-valid Plan fields are kept.
  Every nonempty Item record names Delivery, Story, barrier epoch, intent hash,
  disposition and previous tip. On a later documentation/evidence Request
  changes, dispositions and product/test tree remain immutable: publish Review
  invalidation, prepare a finalization revision plus a new Review child, verify
  the existing complete terminal Item OIDs and repeat a Fence+Integration-only
  approval transaction. A new revert candidate is rejected with
  `DELIVERY_CANCELLATION_FINALIZATION_STALE` and `mutation_state: none`; no
  second `item-cancelled-v1` child is created.
  Unintegrated product commits are not
  merged, and accepted-response loss is recognized only when the entire set is
  present. A competing target refresh, publisher or provider merge wins the
  shared Fence/Integration lease atomically; no partial cancellation state is
  legal.
- Open the one cancellation PR from the existing integration branch. If a
  Delivery PR already exists, update that same PR after reapproval; never open
  a second PR. The user approves its exact dispositions and diff.
- Only after the cancellation PR is merged may exact-lease cleanup delete item,
  slot and integration refs and their clean worktrees. The same fresh all-ref
  proof may lease-delete `agentrof/fence` only when its exact mode is `open`,
  all handoff/carrier fields are cleared, target/config validation is current
  and no other open Delivery or setup transition remains.
- A cancelled story may be selected later only after cancellation is present
  on target and the old claim is safely released.
- A cancelled Delivery is never part of the delivered/frozen story set. Only a
  successfully merged Delivery with verified outcome closure permanently
  freezes its stories.
- Scope expansion is not cancellation. It enters Requirement Flow as a new
  story and Delivery decision.

## 18. Compiler and command architecture

### 18.1 Requirement compiler

Add `requirement_compile.py` as the single machine-field writer with verbs:

```text
init
check
approve
discard
resolve-no-change
supersede
withdraw
status
render-map
```

It owns IDs, path validation, impact rows, relations, hashes, status tags and
approval invalidation.

Replace `preparation_check.py` with `requirement_route.py`. It reads the
approved Requirement, asks each existing stage compiler for current validity,
routes the next `required` stage or Backlog Planning, and returns the same JSON
contract to both hosts. It does not own stage documents or duplicate their
checks. `resolved_no_change`, `withdrawn` and `superseded` are terminal and are
never routed to a stage or Backlog Planning.

### 18.2 Backlog compiler

Extend `backlog_compile.py` with:

```text
begin-revision
revision-status
```

Its checks consume Requirement impact decisions, active item claims and
successfully closed Delivery baselines. Approval becomes incremental while
retaining exact global coverage and review gates.

### 18.3 Delivery compiler

Add `delivery_compile.py` for offline semantic truth:

```text
init-dod
begin-dod-revision
check-dod
approve-dod
init
check
approve-scope
approve-execution
prepare-item-transition
check-item-ready
approve-review
record-pr
prepare-cancellation
prepare-cancellation-finalization
status
render
```

It owns schemas, exact sets, state transitions, source hashes, staleness,
relations, DoD evidence and portable checks. It performs no hidden network
write. `prepare-item-transition --to in_scope|blocked|paused|cancelled` is an
internal coordinator primitive; it does not report the remote transition
complete by itself.

### 18.4 Git coordinator

Add `delivery_git.py` for explicit Git operations:

```text
preflight
configure-parallelism
begin-source-handoff
authorize-target-update
reauthorize-target-update
finish-source-handoff
abort-source-handoff
reserve-delivery
revise-unclaimed-scope
publish-execution-plan
claim-items
begin-plan-revision
finish-plan-revision
abort-plan-revision
refresh-target
start-item
resume-item
reopen-item
takeover-item
push-item
block-item
unblock-item
pause-item
quiesce-delivery
quiesce-upgrade
finish-upgrade
abort-upgrade
integrate-item
cancel-delivery
publish-delivery-review
invalidate-delivery-review
open-pr
verify-merge
reconcile
board
locate
```

It owns fetch, explicit-OID leases, atomic ref creation, fenced worktrees,
protected slots, temporary integration candidates, provider handoff and
ancestry-derived closure. It does not become a task scheduler or state
database.

All Git/provider subprocesses use explicit argument arrays with shell
interpretation disabled. Canonical IDs, paths, refs and remotes are validated
before they become arguments; no user string can add an option or command
segment.

`reserve-delivery` is the sole owner of the scope-approved Integration-ref plus
Fence transaction. `begin-plan-revision` installs the exact
plan-revision barrier and quiesces active items; when target impact triggered
the revision it may atomically include the first
`plan-revision-target-refresh-v1`, while later refreshes use the same exact
epoch. After compiler approval, `finish-plan-revision` validates/moves the
complete `item-target-reconcile-v1` set and writes the exact matching release.
`quiesce-delivery` is an internal shared primitive called by
those closed verbs and cancellation, not a user-facing half-transition.
`abort-plan-revision` proves no approved scope/product/evidence change, writes
an additive restoration of the last approved plan projection and releases only
the exact current revision epoch.
`finish-upgrade` owns upgrade-barrier release; cancellation has no release
verb because its barrier is irreversible.
`begin-source-handoff` is the sole Fence owner for an incorporated Requirement
supersession, approved existing Story/Test mutation or designation
reconciliation. `authorize-target-update` advances an
acquired source/config/upgrade Fence to its immutable target-update intent and
complete durable carrier and
must run before the provider/direct target write can begin. From that point the
matching abort verb is illegal. `finish-source-handoff` verifies the exact
target path mapping and returns Fence to `open`; `abort-source-handoff` is
legal only before target-update intent and after all-state provider/target
proof that no handoff began. The same authorization primitive closes the
configuration and upgrade abort race without adding another ref.
`reauthorize-target-update` owns only the closed zero-target-effect case: it
retains mode/epoch/semantic intent and atomically advances the same-repository
Fence + existing carrier ref to a freshly based fast-forward carrier and unique
Attempt. It preserves the same provider PR when applicable and rejects fork
heads, relevant target drift, changed/merged provider state, a mixed remote pair
or ambiguous prior outcome.
`cancel-delivery` accepts every reserved phase. Before an Execution Plan it
uses the scope-only review projection in section 9.7 and never asks the
compiler to synthesize absent hashes or evidence files.
After the barrier, `cancel-delivery` prepares and validates the exact
reverse-order `cancellation-revert-v1` chain and dispositions locally but does
not publish any revert/finalization before Review approval. A claims-free scope-approved or execution-approved Delivery uses an
empty retained-Item set and keeps every selected Story at `not_started/none`;
existing phase-valid Plan/evidence fields remain exact.
`revise-unclaimed-scope` is the only scope-editing exception after reservation;
it first proves the Delivery has no item/slot refs or product/evidence work,
then publishes the newly reapproved package with one Fence/Integration CAS.
`publish-execution-plan` is the sole network owner of the initial approved
Execution Plan candidate produced offline by `delivery_compile`. It publishes
only the closed tree surface in section 13.2 with one Fence/Integration CAS;
it never creates Item/Slot refs. Exact replay is complete, benign Fence-only
lease loss may retry the same candidate, and any Integration/source/plan-hash
change is `DELIVERY_PLAN_STALE` and requires rerender/reapproval. `claim-items`
never publishes or repairs plan files; it consumes only the verified published
record.
`publish-delivery-review` is the sole network owner of an approved normal or
cancellation Review candidate. It requires the exact reviewed parent, review
and approval hashes, current target/Fence and phase-exact aggregate proof.
Normal publication advances Fence + Integration to the Review record.
Cancellation publication advances Fence + Integration through the local
zero-or-more revert records, `cancellation-finalized-v1` and Review record, and
on first publication advances every retained Item to its complete
`item-cancelled-v1` child in the same atomic push; the set may be empty only
when no claim exists. Request changes before this push is local and mutation-
free. A later target/documentation/evidence correction first uses the phase-
exact cancellation refresh or Review invalidation, then prepares a same-intent/
same-disposition finalization revision without a new revert record. Re-publication advances Fence +
Integration only, after proving the complete existing Item-terminal set exact;
it never appends a second `item-cancelled-v1`. With an existing
PR the publisher verifies draft/unmerged head/base before and after; provider
merge racing the push follows the common merge-first/transition-first rule.
Review/target staleness observed before the push returns no mutation. If target
alone advances after the last observation but publication wins, the publisher
returns `partial`, keeps the PR draft and immediately attempts the exact
`delivery-review-invalidated-v1` transition; failure to publish that
invalidation remains a conservative `DELIVERY_REVIEW_STALE` blocker. The next
resume performs target refresh and reapproval. Accepted-response loss replays
only when the whole normal or cancellation set is complete and can still be
reclassified `partial` by the mandatory post-publication target refetch.
`invalidate-delivery-review` is the sole evidence/review-only Request-changes
publisher after approval. It binds the latest published Review and concrete
finding, drafts/verifies the same PR when one exists, and advances open Fence +
Integration to `delivery-review-invalidated-v1`; it never mutates an Item,
opens a PR or changes cancellation dispositions. Product/test findings still
use `reopen-item`. A fresh clone therefore cannot treat an invalidated Review
as merge-ready, and the next approval uses the normal publication verb.
`refresh-target` compares the latest validated reservation/refresh target to
the fetched target and applies the section 8.4 classifier plus section 12
affected-set matrix. A disjoint delta publishes the exact two-parent
`target-refresh-v1/disjoint` merge with one Fence/Integration CAS. Before
claims, a Plan-relevant path/contract delta instead publishes
`target-refresh-v1/plan_invalidated`, returns the Plan projection to draft and
requires fresh publication; a selected-source change uses the two-parent
scope-revision record. Neither claims-free path creates Item reconciliation.
After any claim, every nonempty set selects zero-Slot plan revision, fresh
impact-bound approval and the complete atomic reconcile set; integrated Items later restart only from
`integrated_reopen`. A projection that cannot remain valid stops for
cancellation/compensating Requirement. A clean
same-path merge inside that plan-revision candidate may use Git's deterministic merged result, but it still
invalidates and rechecks the accountable Item. A true
textual conflict never accepts an interactive product/test resolution in the
Integration candidate: that path is set byte/mode-for-byte to the fetched
target entry, then the reopened accountable Item reapplies or resolves the
Delivery change so its owned diff, Code Review and Verification bind the
result. Before PR creation, a disjoint refresh does not change scope,
topology, claims or approval. After a lifecycle PR exists, it first
makes that same PR draft and follows the common post-PR merge linearization;
the new Integration head invalidates Delivery Review, aggregate checks and
merge approval, which must rerun before the same PR becomes ready again.
When the exact unmatched barrier is `plan-revision`, `refresh-target` selects
`plan-revision-target-refresh-v1`, requires zero Slots (or participates in the
initial all-ref barrier transaction), preserves the barrier epoch and renders
the cumulative Target Reconciliation projection. It never routes back to
ordinary refresh until the exact release wins.
When the exact unmatched barrier is `cancellation`, the same public verb
selects the separate `cancellation-target-refresh-v1` contract instead of the
ordinary classifier. It requires the matching barrier epoch and intent hash,
derives exact phase from remote finalization/Item facts, does not mutate an
Item/Slot ref and does not permit `reopen-item`. `prepublication` invalidates
all local candidates and requires their recomputation before the empty-diff
gate; `published` itself preserves the empty target-relative product/test diff
and invalidates Review. An existing lifecycle PR is drafted first and
reused; merge-first closes the already reviewed cancellation head and prevents
the refresh, while refresh-first advances the head and makes a stale merge
ineligible under the common post-PR linearization rule.

Every mutating verb supports a read-only inspect/preflight result before
writing and returns machine-readable JSON for both hosts.

The shared result envelope is closed and host-neutral:

```json
{
  "schema_version": 1,
  "ok": false,
  "operation": "start-item",
  "mutation_state": "none",
  "mutation_plan_hash": "sha256:12e85799171f2dce0c33eea106a347f7a08a7584b3322e5b39483f31b8906bed",
  "observations": [],
  "planned_mutations": [],
  "findings": [
    {
      "code": "DELIVERY_SLOT_UNAVAILABLE",
      "severity": "blocker",
      "refs": [],
      "paths": [],
      "message": "<localized human explanation>",
      "next_entry": "/deliver DLV-001"
    }
  ]
}
```

The canonical source for this wire schema and its closed finding-code registry
is
`plugins/software-engineering-team/skill-content/deliver/data/delivery-result-contract.json`.
Gate 3 creates that source before coordinator code; both hosts generate from it
and validation rejects an emitted code or shape absent from it. The registry
maps every failure-table row to exactly one primary code and may add structured
secondary context without inventing synonyms. It is closed for each
`schema_version`: a new code requires an explicit canonical-source review and
golden-vector change before runtime uses it. Gate 3 must map every failure row
before its first coordinator implementation. Its initial required codes are:

```text
REQUIREMENT_ID_COLLISION
REQUIREMENT_NOT_CURRENT
REQUIREMENT_STAGE_ORDER
REQUIREMENT_STAGE_IMPACT_INVALID
REQUIREMENT_NOT_INCORPORATED
BACKLOG_REVISION_STALE
BACKLOG_SOURCE_CLAIMED
BACKLOG_COVERAGE_MISMATCH
DELIVERY_INPUT_INVALID
DELIVERY_SCOPE_STALE
DELIVERY_PLAN_STALE
DELIVERY_REVIEW_STALE
DELIVERY_CLAIM_CONFLICT
DELIVERY_DEPENDENCY_UNMET
DELIVERY_BARRIER_ACTIVE
DELIVERY_ITEM_NOT_READY
DELIVERY_ITEM_ALREADY_INTEGRATED
DELIVERY_ITEM_REF_MISSING
DELIVERY_PATH_CLAIM_EXCEEDED
DELIVERY_CONTRACT_CLAIM_EXCEEDED
DELIVERY_CANCELLATION_INVALID
DELIVERY_CANCELLATION_FINALIZATION_STALE
DELIVERY_TARGET_IMPACT_INVALID
DELIVERY_TARGET_SOURCE_VIOLATION
DELIVERY_TARGET_CONVERGENCE_REQUIRED
DELIVERY_SOURCE_HANDOFF_STALE
DELIVERY_TARGET_CARRIER_INVALID
DELIVERY_TARGET_UPDATE_UNCERTAIN
DELIVERY_FENCE_MISSING
DELIVERY_FENCE_CORRUPT
DELIVERY_FENCE_MODE
DELIVERY_FENCE_LEASE_LOST
DELIVERY_SLOT_UNAVAILABLE
DELIVERY_SLOT_INVALID
DELIVERY_SLOT_DUPLICATE
DELIVERY_ITEM_SLOT_MISSING
DELIVERY_ITEM_SLOT_DIVERGED
DELIVERY_REMOTE_ATOMIC_UNSUPPORTED
DELIVERY_REF_COLLISION
DELIVERY_LEASE_LOST
DELIVERY_LOCAL_REF_DIVERGED
DELIVERY_WRITER_RECEIPT_MISSING
DELIVERY_WRITER_RECEIPT_STALE
DELIVERY_WORKTREE_UNSAFE
DELIVERY_PROVIDER_UNSUPPORTED
DELIVERY_PR_UNCERTAIN
DELIVERY_PR_INTENT_STRANDED
DELIVERY_PR_DUPLICATE
DELIVERY_PR_STATE_INVALID
DELIVERY_PR_HEAD_BASE_MISMATCH
DELIVERY_REQUIRED_CHECK_FAILED
DELIVERY_MERGE_POLICY_INVALID
DELIVERY_MERGE_PROOF_INVALID
DELIVERY_POST_MERGE_TRANSITION
DELIVERY_UPGRADE_INCOMPATIBLE
DELIVERY_UPGRADE_CONTRACT_MISMATCH
DELIVERY_UPGRADE_HANDOFF_COLLISION
DELIVERY_DESIGNATION_CHANGE_BLOCKED
DELIVERY_PROTOCOL_UNSUPPORTED
DELIVERY_PATH_ESCAPE
```

`observations` is a list of objects with exactly `kind`, `target` and `value`.
`kind` is one of `config|file|provider|ref|worktree`; `target` is a canonical
credential-free logical identifier; `value` is `absent`, a full OID, a
lowercase `sha256:` digest or a scalar explicitly enumerated for that
observation key in the canonical contract. Observations sort by
`(kind, target, value)` and duplicate `(kind, target)` pairs are rejected.
`planned_mutations`
is a list whose objects contain exactly `atomic_group`, `order`, `kind`,
`target`, `before`, `after` and `lease`. Mutation `kind` is one of
`file_create|file_update|file_delete|provider_create|provider_update|ref_create|ref_update|ref_delete|worktree_create|worktree_remove`;
`order` is a nonnegative integer; the other fields are canonical strings and
use literal `none` when inapplicable. Lists are sorted by
`(atomic_group, order, kind, target)` and duplicate logical targets in one
group or duplicate `(atomic_group, order)` coordinates are rejected. An
`$result` reference names an earlier coordinate whose registered mutation kind
produces exactly one OID; forward, missing or non-OID references fail.

`planned_mutations` describes a semantic mutation, not a promise that inspect
has persisted a random candidate OID. `before` and `lease` bind exact observed
values. A deterministic `after` may be a full OID/value. When the future
candidate contains a fresh epoch, Attempt, writer epoch, provider object or an
OID derived from one of them, `after` instead uses exact form
`semantic-v1:sha256:<64 lowercase hex>`. That digest hashes compact sorted-key
UTF-8 JSON with exactly `format`, `kind`, `target` and `postimage`; `format` is
literal `agentrof-mutation-postimage-v1`. The closed mutation-kind postimage
binds every deterministic parent, tree, path, subject, trailer and provider-
state field. A fresh scalar is represented only by
`{"$fresh":"<role>","binding":"<logical-id>"}`, where role is one of
`fence_epoch|target_update_attempt|pr_attempt|barrier_epoch|writer_epoch`; an
OID produced by another planned entry is represented only by
`{"$result":"<atomic_group>/<order>"}`. Equal fresh role/binding pairs resolve
once across the plan and different pairs resolve independently. The postimage
excludes only Git author/committer identity and timestamps, the resulting OID
and these explicitly fresh values. Unknown/extra markers or kind postimages
fail validation.

Inspect never invokes the random generator or resolves a marker. Apply freshly
recomputes observations, semantic postimages and the supplied
`mutation_plan_hash`; mismatch returns before randomness, receipt creation,
candidate materialization or mutation. Only after equality may Apply resolve
each marker once, construct concrete commits in the private object store, write
the required pending receipt and execute exact leases. It then proves the real
candidate normalizes back to the inspected descriptor. The successful Apply
envelope retains the approved semantic descriptor/hash rather than replacing
it with generated OIDs. If observations changed, Apply returns a new
plan/finding with `mutation_state: none`; it never substitutes a candidate
under the old hash. Inspect may therefore exit and delete all private objects
without persisting a nonce or prepared-plan token.

The envelope has exactly the keys shown. `schema_version` is a positive
integer; `ok` is boolean; `operation` is a registered closed verb; and
`mutation_state` is exactly `none|complete|partial|uncertain`:

- `none`: the coordinator proves that no durable project filesystem, project
  Git ref/worktree or provider mutation occurred; every inspect result uses
  this state;
- `complete`: all semantic postconditions are verified, including an
  idempotent replay that itself needed no new write;
- `partial`: at least one durable mutation is proven but the operation's full
  postcondition is not yet complete, such as a created PR whose URL record is
  still absent;
- `uncertain`: a provider/transport boundary prevents proving whether the
  attempted external mutation occurred. No mutating retry is legal until
  requery resolves it.

For an apply verb, `ok: true` requires `complete`; inspect may be `ok: true`
with `none`. A stale/precondition denial is `none`; `DELIVERY_PR_UNCERTAIN` is
always `uncertain`, as is `DELIVERY_TARGET_UPDATE_UNCERTAIN`. Each finding has
exactly `code`, `severity`, `refs`, `paths`, `message` and `next_entry`.
Severity is `blocker|error|warning|info`; refs and project-relative paths are
deduplicated canonical string lists; `message` is human text; and
`next_entry` is a public-entry string or JSON `null`. Findings sort by
`(severity rank, code, refs, paths)` using that displayed severity order, and
extra keys are rejected.

`mutation_plan_hash` is lowercase SHA-256 over compact sorted-key UTF-8 JSON
containing exactly `schema_version`, `operation`, `observations` and
`planned_mutations`; it excludes `ok`, `mutation_state`, findings, localized text,
credentials and machine-local temporary paths. The empty `start-item` plan in
the example is the cross-host golden vector. Observation ordering, mutation
ordering, every registry code and redaction behavior have golden tests; schema
changes require a new result `schema_version`, never an in-place reinterpretation.

`schema_version`, `ok`, `operation`, `mutation_state`, `mutation_plan_hash`, observations,
planned mutations and finding codes are machine contracts. Human messages may
follow output language and are not test or routing keys. Inspect performs zero
durable project, ref, worktree or provider mutation. Fresh remote observation
and candidate construction run only in one private operation-local bare Git
repository/object store under the ignored project runtime, or an OS-private
temporary directory, never in the project repository's object database,
`FETCH_HEAD` or remote-tracking refs. That cache may be created during an
operation, is not semantic truth, receives no worktree, is excluded from the
mutation plan and is removed before a normal return; crash leftovers are
ignored and TTL-cleaned by exact path. A prepared-but-rejected commit therefore
cannot leave a durable project object. Apply recomputes under the relevant
lease/fence and either executes that semantically identical plan or reports a
new finding with the exact mutation state above. A denial is not considered tested merely
because the process returned nonzero; tests also prove exact finding code and
zero partial mutation. Tokens, credential-bearing remote URLs and environment
secrets are always redacted.

## 19. Agent, skill and flow changes

### Agent and role impact

Reuse the existing Software Engineering Team. Do not add another team or PMO
role.

- Product Owner: Requirement accountability, Delivery scope and Delivery
  Review.
- Business Analyst: Requirement interpretation, BA applicability and backlog
  acceptance traceability.
- Backlog Reviewer: read-only exact Requirement Coverage, stage-evidence,
  story/test-plan and deferral-set reconstruction; it rejects missing, extra or
  one-way Requirement incorporation before backlog approval.
- Solution Architect: Solution applicability and approved constraints.
- UX/Design roles: Design System and Experience applicability plus built
  verification when required.
- Software Architect: Execution Plan, path/contract claims, dependency graph
  and serialized integration.
- Owner developer and supporting implementation roles: code and tests.
- Code Reviewer: read-only findings and verdict.
- QA Engineer: test-plan co-authorship in Requirement Flow and independent
  Delivery verification.

Agent prompts must distinguish role identity from assignment. `owner_role`
means the one accountable implementation role; `supporting_roles` and
`role_sequence` mean additional role participation. None names a person or
host task.

### Skill and flow impact

Add or activate:

- entry skill `/requirement`;
- entry skill `/delivery-plan`;
- entry skill `/execution-plan`;
- full `/deliver` entry;
- internal Requirement orchestration methods;
- internal Delivery Planning, Execution Planning and Git coordination
  references;
- flows for Requirement, Delivery Planning, Execution Planning and Delivery
  Execution.

Update existing stage skills to read the Requirement impact matrix without
duplicating it. Update Product Planning, Code Review, QA Verification,
Software Architecture, Docker Compose and UI/UX guidance for the canonical
Delivery paths and commit-binding rules.

Keep `/setup`, `/configure`, `/issue-report`, `/demo`, `/sketch` and
`/organize-docs` as public entries. Their routing/help text changes where
needed to point at Requirement evidence or exact Delivery resume commands.
`/configure DOD` delegates every Markdown mutation to the Delivery compiler;
it does not turn the config writer into a DoD writer. No other retained entry's
existing authority is silently retired or broadened.

Update the Backlog Reviewer prompt and Product Planning review contract so the
reviewer reconstructs each root `Requirement Coverage` projection from exact
Requirement, stage-output, story and test-plan relations; generic prose or a
compiler-rendered row is not accepted as reviewer evidence by itself.

Retire:

- `/delivery-lanes` and its placeholder flow;
- placeholder-only `develop` wording;
- `greenfield` and `existing` routing language;
- the stale statement that the product has no Delivery units;
- the obsolete `item list --json` reference;
- any suggestion that backlog status records execution completion.

### Concrete repository change map

The implementation touches these canonical surfaces together; a partial slice
must not be released:

| Concern | Canonical source impact |
|---|---|
| Lifecycle terminology/routing | Replace `scripts/preparation_check.py` with `scripts/requirement_route.py`; update `docs/architecture.md`, `docs/authoring.md`, README and project instructions |
| Requirement knowledge | Add `scripts/requirement_compile.py`, `skill-content/requirement/`, `flows/requirement.md`, Requirement map/template/policy entries |
| Backlog revisions | Extend `scripts/backlog_compile.py`, `skill-content/backlog-plan/`, `skill-content/product-planning/` and `flows/backlog-planning.md` |
| Delivery knowledge | Add `scripts/delivery_compile.py`, `skill-content/delivery-plan/`, `skill-content/execution-plan/`; replace placeholder `skill-content/deliver/` and add canonical `data/delivery-document-contract.json`, `delivery-result-contract.json`, `delivery-control-record-contract.json`, `delivery-migration-registry.json` and `delivery-protocol-1.json` before coordinator code |
| Delivery flow docs | Add `flows/delivery-planning.md`, `flows/execution-planning.md`, `flows/delivery-execution.md`; remove `flows/delivery-lanes.md` and replace placeholder `flows/develop.md` |
| Git coordination | Add `scripts/delivery_git.py`; update CI/gitignore templates and setup/runtime path policy |
| Configuration/setup | Update `scripts/project_config.py`, `setup_project.py`, `setup_check.py`, `skill-content/configure/`, `skill-content/setup/` and the upgrade protocol |
| Vault enforcement | Update `skill-content/obsidian-vault/data/vault-policy.json`, vault references/templates, `scripts/vault_check.py`, `vault_gate.py` and the shared `vault_hook.py` overlay |
| Role behavior | Update Product Owner, Backlog Reviewer, Software Architect, owner developer, Code Reviewer, QA Engineer and applicable design/architecture agent prompts; do not add a team or role |
| Host parity | Update both `platforms/*/software-engineering-team/{manifest.json,host-contract.md}`, overlays and host generators from canonical sources |
| Generated packages | Extend `.agent-marketplace-package.json` with the closed Delivery protocol capability, then regenerate both `dist/` trees with `tools/build_distributions.py`; never patch them directly |
| Test contracts | Rename preparation/greenfield tests to Requirement terminology; add compiler, delivery schema, multi-clone Git, provider, hook, setup-upgrade and both-host E2E suites |

Scaffold and validation rules must recognize the new entries, flows and scripts
before canonical files are added. Counts are regenerated only after the final
canonical inventory stabilizes.

## 20. Vault, hook and writer boundaries

Vault policy must add the document types, paths, statuses, property types,
relations, designation defaults and graph colors specified above.

Relation rules include:

- Requirement `derives_from` zero or more pre-existing approved intake
  evidence notes; direct user intake requires no fabricated predecessor.
- A current selectable Story `derives_from` one or more current approved
  Requirements plus only the planning sources that actually constrain that
  story. A superseded/frozen historical Story may retain a terminal Requirement
  link only when the exact root-coverage or pinned-`backlog_commit` proof in
  section 6.5 validates its formerly approved source; that exception never
  makes the Story selectable for a new Delivery.
- Delivery `derives_from` the exact selected story set.
- Execution Plan derives from exactly one Delivery.
- Delivery Item derives from exactly one story and its sibling test plan.
- Delivery Item `execution_after` targets only sibling Delivery Items;
  `waits_for` targets only approved backlog stories with a current claim in a
  different open Delivery.
- Code Review derives from exactly one Delivery Item.
- Verification derives from one Delivery Item and verifies its exact test plan.
- Delivery Review derives from one Delivery. Normal execution verifies the
  exact Item, Code Review and Verification sets; cancellation follows the
  phase-exact relation/field projection in section 9.7 and never fabricates an
  artifact absent at the cancellation point.

Branch-aware writer rules:

- Target/primary: Requirement, backlog, DoD and target-resident maps; no active
  product Delivery state.
- Integration: its Delivery root, Execution Plan, Delivery Review, integrated
  item records and delivery map; no backlog mutation.
- Item: approved product/test claims plus only its own item/evidence subtree.
- Reviewers and QA return read-only records; the owning orchestrator writes the
  canonical file.
- Only canonical compiler/coordinator paths may write machine fields, config
  or remote Fence/control refs.

Write-time enforcement is explicit:

| Current branch/worktree | Allowed authored writes | Immediate denial |
|---|---|---|
| Target documentation branch | Requirement, upstream stage, backlog, DoD and tracked maps through their owner/compiler | Any active Delivery product/evidence path |
| `agentrof/deliveries/dlv-###` | That Delivery root, Execution Plan, Delivery Review, map and coordinator-driven item integrations | Backlog/Requirement changes and direct item product editing |
| `agentrof/items/<story-id>` with valid local fence | Approved product/test path claims plus that one item/evidence subtree | Another item, Delivery root, backlog, Requirement or unclaimed product path |
| Temporary integration candidate | Coordinator-only merge result | Interactive authored write |

The hook reads the approved local plan and verifies the current branch/path
before Write/Edit. It denies raw shell `git push`, ref deletion, rebase, reset
and worktree removal against Delivery namespaces unless the exact canonical
`delivery_git.py` path owns the operation. Machine fields remain compiler-only.
Remote lease verification and CI repeat these checks because hooks are a
correctness convenience, not a security boundary.

Hooks are correctness guards, not a security sandbox. Portable compiler,
remote CAS and final CI remain authoritative.

## 21. Configuration, setup and upgrade

### 21.1 Config changes

Remove active `project_origin` completely:

- remove setup `--origin` and `set-origin`;
- remove greenfield/existing route selection;
- remove origin-based backlog restrictions;
- retain only a migration tombstone that deletes the old field from projects.

Keep Requirement `request_kind`/`urgency` and story `work_kind`/priority in
their separate scopes; Delivery consumes the story classification only.

`max_parallel` becomes required before Delivery activation:

- it is a positive integer with no implicit or shipped default;
- `/configure` presents it as “maximum simultaneously active Delivery Item
  count across this project, hosts and machines,” never as sprint capacity,
  agent count or duration;
- package refresh of a Requirement-only project does not fail solely
  because it is absent;
- `/delivery-plan` and `/execution-plan` remain available while it is absent;
- the first Item activation inside `/deliver DLV-###` routes to `/configure`
  and requires an explicit value before any slot or worktree is created;
  `/configure` shows the exact human meaning and target diff, performs no
  Delivery activation, and returns `next_entry: /deliver DLV-###`. If project
  policy requires a protected config PR, the Delivery remains claimed with no
  Slot until that config commit reaches target; the exact `/deliver DLV-###`
  resume then rechecks Fence/config hash before activation;
- slot acquisition always reads the value from the freshly fetched target,
  never an integration branch copy;
- changing the field is a remote-aware coordinator operation: Fence mode
  moves `open -> configuring -> open`; `configuring` binds the exact baseline
  target plus desired canonical config hash. Before the protected config
  PR/direct target write can begin, Fence records the same hash as its
  irreversible target-update intent; abort is legal only before that intent.
  The final `open` child binds the exact fetched target commit that contains
  the desired hash;
  Delivery reservation/claim/start and upgrade reject `configuring`;
- once committed, the value is monotonic nondecreasing in v1; a future
  decrease requires a separately designed remote configuration epoch;
- no second item, Delivery or agent parallelism field is added.

Keep existing stack, database, command and source-directory fields. Delivery
preflight names exactly which commands are required by the selected impacts.
Designation fields remain configurable, but their writer must run section
8.3's fresh remote-ref preflight. Any open Delivery blocks their mutation and
controlled reconciliation; this rule is enforced by `project_config.py`, the
vault hook, portable gate and both host entries rather than only documented in
the UI. `max_parallel` remains the only value in `Config-Hash`; designation
bytes appear only in the bounded `source_handoff` intent during their target
handoff and return to `none` afterward.

### 21.2 Setup payload

Setup adds:

- Requirement and Delivery map scaffolds;
- Definition of Done template support;
- new designation defaults while preserving project selections;
- graph queries/colors and property types;
- allowed project-local runtime worktree directories;
- disposable project-local writer-receipt directories and hook validation;
- CI templates that exclude Fence and Slot refs;
- portable Requirement/Delivery compilers and coordinator.

Setup must never create a sample Delivery, ask a misleading capacity question
before Delivery is relevant, or select a hidden `max_parallel`.
It must preserve active worktrees, authored documents, configured
designations and user-owned Obsidian settings during N to N+1 refresh.

### 21.3 Compatibility

- Existing approved upstream-stage and backlog documents remain valid.
- Old projects receive no synthetic Requirement history.
- The next backlog revision must use a Requirement record.
- Delivery placeholder entries are replaced only when the complete new gate is
  available on both hosts.
- No partially active Delivery entry is shipped.

### 21.4 Upgrade with active Deliveries

Package refresh remains project-local and file-first. Active Git branches do
not create a second setup database or migration ledger.

The installed package root's existing
`.agent-marketplace-package.json` is the only package provenance input. Keep
its existing `schema_version`, `component`, `host`, semantic `version`,
`build_id`, source provenance, `files` hashes and `runtime_contracts`, and add
this closed package-level capability:

```json
"delivery_protocol": {
  "read_min": 1,
  "read_max": 1,
  "write": 1,
  "transition_writes": [1]
}
```

`delivery_git.py` contains a closed adapter registry keyed by each integer in
that advertised read range. `transition_writes` is the exact sorted set of
older/current record versions the incoming package can deliberately emit while
an outgoing package may still need to resume. Setup verifies the manifest schema, expected
component/host, every file hash, equality of the registry's supported range
and the manifest read/transition ranges, and the incoming writer version before reading remote
records. It validates the current Fence/Integration control chain and, for
each item or slot tip, resolves the nearest applicable compiler-owned control
record on the item's validated first-parent lineage. Ordinary product, test and
evidence commits deliberately carry no `Agentrof-*` trailers; they inherit the
wire interpretation of that control ancestor and cannot change protocol.
Every observed control-record version must have an incoming read adapter; all
post-release control records use the incoming `write` version. Mixed records
are accepted only when every version is explicitly in range and the selected
adapters prove their cross-version invariant; otherwise inspect reports an
unsafe blocker. Missing lineage, an unrecognized `Agentrof-Record`, or an
`Agentrof-Protocol` trailer on an ordinary authored commit is corruption and
fails closed.

`build_id` proves exact package bytes and semantic `version` informs product
release, but neither chooses a Delivery migration. Protocol trailers plus the
manifest capability choose compatibility. This manifest is never copied into
`workspace/config.json`, authored Markdown or `.agentrof`; it remains package
metadata and therefore does not reintroduce a project upgrade ledger.

`setup_project inspect` must:

1. resolve the registered main worktree and enumerate the Fence,
   active integration/item refs, occupied slot refs and linked worktrees
   without changing them; report
   dirty local linked worktrees explicitly (remote slots still reveal active
   writer rights whose other-machine filesystem cannot be inspected);
2. run the incoming compilers read-only against every active Delivery ref;
3. classify each finding as package-owned convergence, schema-compatible
   authored content, compiler-manageable Delivery migration or semantic/manual
   blocker;
4. show exact main-worktree changes and exact per-Delivery follow-up paths;
5. refuse apply only for a genuine unsafe blocker, not because a Delivery is
   merely active or `max_parallel` is absent.

Before `setup_project apply` mutates any managed surface, the incoming package
must use its bundled compatibility adapter, selected from validated closed
`Agentrof-Record` trailers. It never executes code from an active worktree or
relies on the outgoing package still being installed.

Upgrade acquisition first proves Fence mode `open` (or a ref-free legacy
project accepted by the incoming adapter) and no open Delivery has an
unmatched plan-revision or cancellation barrier. It never stacks an upgrade
barrier over either state.

Setup then selects the highest transition writer that is both listed by the
incoming package and readable by the installed outgoing package. If none
exists, active Delivery upgrade fails before quiescence. Acquisition, quiesce
and any pre-handoff abort use that transition protocol; only after the
irreversible target handoff may new records use the incoming `write` version.

Setup atomically advances/creates `agentrof/fence` from mode `open` to a unique
`project-fence-v1` record whose mode is `upgrade`, phase is `acquired`, and whose
epoch plus host-neutral `upgrade_contract_hash` identify this exact upgrade. In
the same transaction it advances
every open Delivery integration ref to a
`delivery-barrier-v1` of kind `upgrade`, advances only active item refs to
individual quiesce children and lease-deletes their equal slots. Slotless and
sealed/integrated item refs remain exact behind their integration barrier.

Reservation, claim, `max_parallel` configuration and start/resume all require
mode `open` and atomically advance the same project ref. Ordinary active-item
pushes touch the item/slot refs setup quiesces. Therefore no capacity increase,
new Delivery, claim or writer can race past upgrade: one transaction loses a
real ref lease and must refetch. The design is independent of the current slot
range and needs no non-fast-forward slot rewrite.

A lost lease, unsupported old protocol or inability to verify Fence mode,
all open integration barriers and an empty active-slot set is a genuine unsafe
blocker. Uncommitted bytes in another machine's stale worktree are not deleted;
that writer is fenced and must later reconcile explicitly. The exact
Fence/Integration/Item/Slot operations are listed in inspect and covered by
the user's apply confirmation. A project with no Fence ref creates the
upgrade record using an absent-ref lease; a simultaneous first Delivery or
configuration attempt cannot also create it.

Only after quiescence, `setup_project apply` updates setup-owned
main-worktree/config/template surfaces under its existing transaction and
rollback contract. It preserves all registered worktrees, remote refs,
authored Markdown, designation choices and user-owned Obsidian settings. It
never traverses an item worktree and rewrites it in place. If setup then fails,
items remain safely paused; rollback never silently reactivates old writers.

After local apply and its closing check succeed, but before the incoming
setup/config/docs branch or PR can update target, `authorize-target-update`
atomically sets Fence `Target-Update-Intent` to the exact upgrade contract.
This is the no-return boundary. A failure or lost response after it keeps the
upgrade fenced and resumes by provider/target requery; it never calls
`abort-upgrade` or exposes the outgoing writer against possibly incoming target
bytes.

Before any active Delivery resumes after the refreshed setup changes are
committed to and fetched from target:

1. require Fence mode `upgrade` and rescan every Delivery/Item ref created
   before the barrier (new reservation/claim is mechanically blocked);
2. Run this closed source/tree classifier before each Delivery merge:
   - an unrelated Requirement/backlog append is disjoint;
   - a newer DoD path is legal only while the Delivery's pinned historical
     blob/hash/commit remains valid;
   - a selected Story/Test change already present in the immutable upgrade
     acquisition target, with **zero Item claims**, is
     `scope_reapproval_required`: preserve the old decision until the user
     approves the exact new source hashes under the upgrade barrier, and also
     reapprove an existing Execution Plan when present. This is compatibility
     with a source handoff completed before upgrade acquisition, not permission
     to mutate source while upgrade owns the Fence;
   - any selected Story/Test change first appearing after upgrade acquisition,
     or any acquisition-baseline change after a claim, is
     `claimed_source_violation`: publish no merge/handoff for that Delivery and
     require target correction or explicit repository repair; upgrade never
     absorbs a source mutation that bypassed `source_handoff`;
   - setup-owned managed paths take the exact incoming target payload; any
     purported Delivery-authored change there is corruption;
   - a clean product/test merge is deterministic; a textual conflict on an
     integrated/claimed Item path takes the fetched target entry exactly and
     records `reopen`/`unintegrated_rebase` impact. Compiler-owned Delivery
     schema projections use the transition writer; a conflict in user prose is
     a manual semantic blocker, never an automatic side choice.
3. In stable Delivery-ID order, publish one transition-readable
   `upgrade-target-merge-v1` per open Integration. It carries exact Scope,
   Plan (`none` before approval) and Target Impact hashes and advances the
   exact same-epoch `upgrade/acquired` Fence with the immutable target-update
   intent to a same-mode child plus that one
   Integration. When acquisition-baseline zero-claim source reapproval is required, the candidate
   includes the newly approved scope and plan projection; without that user
   approval it is inspect-only. A completed matching record is skipped; a lost
   response is classified from refs. Crash after `1/N` leaves `acquired` and
   resumes only missing Delivery IDs.
4. Refetch target before handoff. If it advanced, remain `acquired`, rerun the
   stable-order classifier/merge round for the new target and recompute every
   affected approval/hash cumulatively from the original pre-barrier baseline.
   Round two and later parent each new record from the exact current
   same-epoch/same-contract Integration descendant, set `Previous-Target` to
   the immediately prior validated target and never jump back to the barrier
   commit. Only when every still-open Delivery contains the same current
   target/epoch/contract may Fence alone advance to
   `upgrade/target_handoff`; no incoming-protocol migration record precedes it.
5. Run the incoming Delivery compiler. Byte-compatible semantic hashes continue
   directly. Machine-only schema projections migrate automatically. Any Scope,
   Execution Plan or verification semantic change receives the existing exact
   user approval and binds the upgrade `target_impact_hash`; no user prose,
   scope or verdict is silently rewritten.
6. Refetch target immediately before release. If it differs from
   `Handoff-Target`, keep every barrier and Slot absence exact, run another
   same-epoch incoming-writer classifier/merge round while Fence remains
   `upgrade/target_handoff`, and refresh `Handoff-Target` only after every open
   Integration contains the new target. Disjoint and non-source relevant
   deltas use the same closed classifier. Any selected Story/Test mutation that
   first appears after acquisition is an unauthorized source-handoff incident
   regardless of claim count; no release occurs on drift.
7. Prepare the complete release set. Integrated affected Items receive
   `item-target-reconcile-v1/integrated_reopen`; quiesced/paused/claimed Items
   receive `unintegrated_rebase`; unaffected integrated tips remain exact.
   In one atomic push release every matching upgrade barrier, advance all
   affected Item tips, and return Fence to a unique `open` child with zero
   Slots. A rejection changes nothing. Items later reacquire normal Slots and
   own every product conflict resolution through fresh review/verification.
   The release, returned Fence and Item records all bind exact
   `Handoff-Target`, and the common post-CAS target-refetch/`partial`
   convergence contract in section 12 applies before any writer readiness is
   returned.

`abort-upgrade` may release the exact known upgrade barriers only while phase
is `acquired`, `Target-Update-Intent` is `none`, local apply has not crossed its
successful handoff boundary and provider/target all-state proof shows that no
incoming target publication began. Otherwise recovery must requery/finish; it
never guesses or silently reactivates writers. After target handoff, an
incompatible Delivery keeps the maintenance barrier and blocks execution until
the explicit migration decision is complete.

An automatic migration may change only machine-owned fields, fixed paths,
tags, relations and generated projections. It may not rewrite user prose,
scope, acceptance, role decisions or evidence verdicts. Unsupported semantic
change remains an explicit blocker with the active remote work preserved.

No authored Requirement or Delivery file stores package version, build ID,
migration level or host identity. Package provenance remains package metadata;
Git history and current compiler checks prove project state.

## 22. Implementation sequence

Implementation must proceed in these gates. A later gate does not begin until
the preceding gate is green.

### Gate 1: terminology and Requirement foundation

- replace Sprint/preparation/origin routing terminology;
- keep this canonical plan at `docs/requirement-delivery-plan.md` and update
  every canonical reference in the same change;
- add Requirement schema, compiler, type, designation, graph and map;
- add `/requirement` while reusing existing stage entries;
- remove `project_origin` from active config and routing;
- add Requirement-focused positive/negative and localization tests.

### Gate 2: living backlog revision

- add `begin-revision` and incremental approval;
- make planning-source rules consume Requirement impacts;
- define the backlog-freeze input contract against compiler-supplied Delivery
  snapshots and prove it with offline interface fixtures only; live ref-backed
  claim and closure evidence is not implemented in this gate;
- update backlog reviews, maps and flow metrics;
- prove concurrent Requirement/backlog append and stale-root-review rejection
  without depending on a Delivery ref implementation.

### Gate 3: offline Delivery knowledge model

- add DoD, Delivery, Execution Plan, Item, Code Review, Verification and
  Delivery Review schemas/templates;
- add designations, colors, relations and exact-set validators;
- add the closed host-neutral result schema/finding registry and its golden
  canonicalization vectors before any coordinator emits JSON;
- implement persisted and derived state projections without Git mutation;
- replace placeholder entries only internally, not in released host manifests.

### Gate 4: Git coordination

- implement remote/default preflight, deterministic refs and explicit leases;
- implement the versioned closed record/trailer protocol and package adapter
  registry before any Delivery ref can be written;
- implement atomic item claims, atomic item/slot activation and writer fencing;
- implement the semantic source-handoff and target-update-intent families
  before allowing an existing selectable Story/Test byte, designation set,
  governed config or upgrade payload to reach target; prove each competes with
  reservation/claim on the same Fence;
- connect `begin-revision` to fresh active-claim and successfully closed
  Delivery evidence, then prove currently claimed and successfully delivered
  Story packages freeze, verified cancelled-and-cleaned packages become
  revision-eligible, and unrelated backlog append preserves every pinned
  Delivery baseline;
- implement project-local linked worktrees and resume/reconcile;
- implement path enforcement, contract evidence and detached-candidate
  serialized integration;
- implement orphan reservation and partial-cancellation recovery;
- test with multiple independent clones of one bare remote.

### Gate 5: Delivery execution and quality

- wire owner/supporting roles, code review, QA, mutation, runtime and UX gates;
- implement commit-binding and evidence-only commit rules;
- implement aggregate Delivery Review and PR handoff;
- ship the GitHub provider adapter, verify merge-commit-only policy and derive
  closure through provider evidence plus ancestry.

### Gate 6: hooks, CI and both hosts

- enforce branch-aware writer boundaries;
- update CI templates and branch namespace rules;
- update Claude and Codex manifests, prompts, generators and hook overlays;
- regenerate both distributions; never edit `dist/` manually.

### Gate 7: migration and release proof

- prove old-package to new-package setup convergence;
- prove existing approved backlog compatibility and first Requirement revision;
- prove actual Claude and Codex install/update and entry discovery;
- close residue, documentation, count and generated-distribution gates.

## 23. Required test plan and acceptance scenarios

### 23.1 Test objective and release rule

The implementation is accepted by observable outcomes and invariant coverage,
not line coverage or the number of tests. Every normative state transition,
command verb, failure row and invariant in section 26 must have at least one
positive case and one illegal/stale-precondition case. Every remote mutation
also needs an accepted-but-response-lost recovery case.

Each case records these six elements in its test name/docstring and assertions:

1. exact initial tracked documents, refs, provider state and local worktrees;
2. one action or one deliberately controlled interleaving;
3. exact semantic result and machine finding code;
4. complete expected filesystem/ref/provider mutation set;
5. complete forbidden mutation set;
6. repeated-command or recovery result.

Exit status, stdout text or code coverage alone is never a sufficient oracle.
A negative case passes only when the expected closed finding code is returned,
the returned `mutation_state` matches reality and a legal next action is
reported. For `none`, every tracked/project-runtime semantic byte, project Git
object/ref/`FETCH_HEAD`/remote-tracking ref, worktree and provider object
remains exact; the private operation-local observation directory is absent
again on normal return.
For `partial` or `uncertain`, the test proves the complete allowed mutation
set, forbids every other side effect, then proves fresh requery converges to
`complete` or a closed manual-incident result without a blind retry. A success case passes only when the expected semantic files,
remote graph and provider evidence all agree and a second check is clean.

No required test may be skipped, marked expected-failure, depend on test order
or be retried until green. Default repository tests use no external network.
The live provider acceptance is a separate release-candidate gate against an
explicit disposable/sandbox repository, never a developer or production repo.

### 23.2 Test layers and suite ownership

| Layer | Required suite | What it proves | Required gate |
|---|---|---|---|
| Static contract | `tools/validate.py`, `test_validator_contract.py`, `test_retired_residue_contract.py` | Canonical names, no PMO/SQLite/global state residue, registered scripts/entries/types, no stale generated source | `make check` |
| Pure compiler | `test_requirement_compile.py`, `test_requirement_route.py`, `test_backlog_revision.py`, `test_delivery_compile.py` | Schemas, relations, state transitions, canonical hashes, localization and no network writes | `make check` |
| Single-checkout integration | `test_requirement_delivery_flow.py`, `test_delivery_hook.py`, `test_project_vault_contract.py` | Vault paths, branch-aware writers, portable gate, maps and target handoffs | `make check` |
| Real Git protocol | `test_delivery_git_protocol.py`, `test_delivery_git_races.py` | Actual object graph, atomic pushes, explicit leases, worktrees, slots, barriers and recovery across clones | `make check` |
| Provider contract | `test_delivery_provider_github.py` with a hermetic adapter double | Head/base/check/PR/merge semantics and lost-response idempotence without network | `make check` |
| Upgrade compatibility | `test_delivery_upgrade.py`, `test_package_refresh.py` | Package N to N+1, protocol adapters, active-work fencing, rollback and resume | `make check` |
| Host package E2E | `test_delivery_host_parity.py`, `test_smoke_plugin_installs.py` | Claude/Codex entry discovery, identical JSON semantics, install/update and generated package parity | `make check` and release gate |
| Live provider smoke | `tools/smoke_requirement_delivery.py --provider github --repo <sandbox>` | Real GitHub settings, one merge-commit PR and closure evidence | Release candidate only |

`tools/tests/test_requirement_delivery_contract.py` is the mechanical coverage
registry. It enumerates every public/internal verb, persisted transition,
failure-table row and section-26 invariant, and requires its stable case IDs to
be registered by the suites above. It also rejects duplicate IDs and a test
family that exists in the plan but is absent from independent unittest
discovery. The registry is test metadata, not project or runtime state.

### 23.3 Deterministic fixtures, interleavings and oracles

The shared fixture contains:

- one tracked project vault with fixed UTC clock and Git author/committer dates;
- approved Requirements exercising `required`, `reuse` and `not_applicable`;
- an approved backlog with one independent Story, one same-Delivery dependency,
  one cross-Delivery dependency, one path/contract collision and one
  built-experience Story;
- one local bare upstream, two fresh independent clones and one deliberately
  stale clone;
- a provider state double with exact repository settings, PR, checks and merge
  states;
- package N and N+1 payloads for both host distributions, including one
  compatible protocol transition and one unsupported transition;
- English and Turkish/custom designation configurations.

Compiler tests freeze time explicitly. Git fixtures freeze identities and
timestamps where exact OIDs are asserted. Race tests use real Git refs and
pushes, but interleave at explicit `observe -> prepare -> push -> verify`
boundaries through an injected command-runner seam. They never use sleep,
timing probability or a production environment variable that can pause the
coordinator. Failed seeds/interleavings are stable named cases, not random CI
luck.

Every mutating-case oracle captures and compares:

- tracked and ignored filesystem manifests, content hashes and dirty status;
- full target/Fence/Integration/Item/Slot OIDs plus parent graph;
- registered worktrees and local writer receipts;
- provider PR/check/merge objects and adapter call count;
- machine JSON envelope and finding codes;
- the result of an immediate portable gate and command replay.

Sensitive environment values, authenticated remote URLs and tokens are seeded
with sentinels and asserted absent from stdout, stderr, JSON, commits and files.

Inspect/apply parity tests exit the inspect process, remove its private object
cache and apply in a fresh process for `start-item`, PR-creation intent and
target-update intent. Inspect makes zero RNG calls; different seeds and commit
clocks still produce byte-identical observations, semantic `after` descriptors
and `mutation_plan_hash`. Apply may mint different epochs/Attempts and real
OIDs but normalizes to that descriptor. Changed observations return before RNG,
cache, receipt or durable mutation with a new plan; unknown placeholders,
result bindings or an actual OID masquerading as an inspected random candidate
fail on both hosts. Crash tests cover marker resolution, private candidate,
pending receipt, atomic acceptance and response loss: a normal `none` result
leaves no receipt/cache, retained ambiguity is `uncertain`, and proven partial
external/ref effect is `partial`.

### 23.4 Complete happy-path journeys

These are outcome tests, not demonstrations assembled from separately mocked
steps:

| Case | Journey | Required final proof |
|---|---|---|
| `HP-REQ-01` | New orchestrated Requirement with required/reuse/N/A stages | Requirement reaches target, only required stages run, reused evidence stays unchanged, approved backlog is exact and portable |
| `HP-REQ-02` | The same intent through stage-by-stage entries and protected documentation handoffs | Exact resume grammar works after each target handoff and final semantic backlog contract equals `HP-REQ-01` |
| `HP-REQ-03` | Approved evidence proves the request already satisfied | Requirement becomes `resolved_no_change`, no Story/backlog revision is fabricated and routing stays terminal |
| `HP-BKL-01` | Two concurrent Requirement branches append different deltas | First lands; second rebases/recompiles/reapproves from current target; both exact deltas and coverage survive |
| `HP-BKL-02` | New Requirement appends backlog while an unrelated Delivery is active | Active Story/test hashes and approvals remain exact; new approved Story is selectable independently |
| `HP-DEL-01` | One-Story Delivery from scope proposal through merge | One Integration branch, one Item claim, one Slot while active, valid CR/QA/review, one merge-commit PR and clean reconstructed closure |
| `HP-DEL-02` | Multi-Story Delivery with parallel and sequential predecessors | Deterministic waves, global WIP, serialized exact-tip integration and one aggregate PR preserve all evidence |
| `HP-DEL-03` | Two independent Deliveries execute on two clones | Distinct claims progress concurrently, occupied Slots never exceed `max_parallel`, either Delivery may merge first |
| `HP-DEL-04` | Delivery waits on external backlog dependency and execution-only overlap edge | Both pinned predecessor bindings block activation, then unlock only after the exact predecessor target closure |
| `HP-DEL-05` | Active Item pauses, resumes on the same machine and is explicitly taken over on another | Receipt/epoch and Item/Slot CAS permit one current cooperative writer; stale clone cannot push after fetch |
| `HP-DEL-06` | Non-scope Execution Plan revision before any affected Item integrates | Exact barrier/quiesce/reapproval, merge of the approved Integration projection into affected Items and release occur without rebase; only affected descendant evidence becomes stale |
| `HP-CAN-01` | Scope-approved Delivery cancels before Execution Plan | The exact `not_started`/`none` golden intent hash, cancellation review/PR/cleanup succeed with no Item ref, synthetic plan hash, integration base, CR or QA file |
| `HP-CAN-01B` | Execution Plan is published but claims do not exist, then Delivery cancels | Existing Plan/evidence fields remain phase-valid, every Story is `not_started/none`, the retained Item mapping is empty and cancellation publication/replay creates no Item ref or synthetic execution evidence |
| `HP-CAN-02` | Active and partially integrated Delivery cancels | Approved intent survives crash, unintegrated work disposition is explicit, integrated code is reverted and target diff is knowledge-only |
| `HP-UPG-01` | Package N to N+1 with two active Items on Claude and Codex | Bound upgrade contract quiesces writers, preserves authored/local work, hands off exact target, migrates/resumes and converges identically |

### 23.5 Requirement and backlog

- feature, defect and technical request classification; hotfix maps to defect
  plus critical urgency and a concrete rationale; story work kind/priority
  remains independently accountable and multi-Requirement consolidation is
  explained;
- all three stage dispositions, malformed/missing rows, generic N/A rationale
  and stale reuse target;
- unchanged approved N/A passes only its mechanical row/source-hash checks;
  semantic Requirement edit invalidates it, while unrelated new evidence never
  silently changes the Product Owner disposition;
- `required` derives create-versus-revise mechanically and never requires an
  unresolved future link;
- stage-by-stage and orchestrated flows produce the same backlog contract;
- free-text `/requirement` never fuzzy-resumes an existing record, while exact
  `/requirement REQ-###` selects any existing record and exposes only its
  state-valid action matrix; routing/continue alone requires the open
  predicate. Incorporated-approved Supersede succeeds, draft-old Supersede is
  rejected and terminal selection is read-only with zero mutation;
- bare `/requirement` asks for new intake and never resumes; an exact selected
  Requirement exposes only its closed state-valid action set, and internal
  discard/resolve/withdraw/supersede verbs are not host-discoverable;
- state-derived Requirement action menus expose uncommitted-draft discard,
  committed-or-approved pre-incorporation withdraw and replacement-bound
  supersede only in their legal
  phases; invalid direct attempts return `mutation_state: none`. Approve,
  Request changes and Stop prove the exact file/ref/provider effects for each
  action on both hosts;
- every stage entry accepts free text as new intake, exact `REQ-###` as resume
  and no argument only for a single eligible proposal; zero/multiple/terminal
  candidates and wrong stage order ask or fail without side effects;
- two authoring branches that propose the same Requirement ID force the loser
  to re-ID/reapprove and reach target before any stage output is created;
- every stage-by-stage entry rejects an unmet earlier required/reuse/N/A
  prerequisite and the Requirement-not-on-target condition exactly as the
  orchestrator does;
- applicability revision invalidates approval;
- draft discard, committed/approved withdrawal and reciprocal Requirement supersession
  follow their distinct terminal contracts and never reappear in routing;
- discard rejects a committed/downstream draft; a committed draft exposes
  Withdraw and proves Approve/Request changes/Stop effects. Supersession
  refuses a missing, already-approved relation-less or downstream-used
  replacement; the reviewed draft already contains `supersedes`, its approval
  hash binds that relation, and one transaction stamps it while writing the
  old reciprocal link. Stop/Request changes mutate neither Requirement;
- pre-incorporation withdrawal is accepted; incorporated-unclaimed, claimed
  and delivered Requirement withdrawal are rejected without invalidating the
  approved backlog; correction uses supersession;
- superseding an incorporated Requirement preserves historical stage/root
  coverage and active/closed Delivery validation through the exact prior
  approved source/`backlog_commit`. The intermediate current Story with only a
  terminal Requirement source is rejected from new Delivery selection; a
  later separately approved `/backlog-plan` revision installs reciprocal Story
  supersession, leaves the old sibling Test Plan byte-identical, adds the new
  Story/Test Plan pair and refreshes exact root coverage. Crash/Stop/replay at
  either handoff never combines the two approval decisions or exposes the old
  Story as selectable;
- Story supersession golden cases permit only old `planned -> superseded` plus
  reciprocal `supersedes/superseded_by` and status-tag changes, leave the old
  Test Plan/path/approval bytes exact, require a new sibling Test Plan, and
  reject the transition during an active claim or after successful Delivery.
  A continued/delivered frozen old Story instead stays byte-identical while
  the new correction Story derives from the replacement Requirement and
  `depends_on` the old Story with no `supersedes`; activation waits for its
  exact target closure;
- `resolved_no_change` requires approved evidence/reason, creates no Story and
  remains distinct from discard, withdrawal and supersession;
- no-change Approve performs the one terminal transition; Request changes and
  Stop on both hosts return the exact resume entry with `mutation_state: none`,
  unchanged Requirement bytes/refs and no downstream stage/backlog mutation;
- routing, Backlog Reviewer coverage, Requirements map and Obsidian board use
  one `requirement_incorporated` predicate and agree on empty, one-way,
  missing-stage, extra-story and exact-valid sets;
- Turkish/custom designation creation with fixed machine paths;
- first backlog creation and later append revision;
- two concurrent backlog revisions cannot merge stale root hashes/reviews; the
  loser reapplies its delta to fresh target and preserves both Requirements;
- source-intent golden vectors cover modify/create/delete/rename, executable
  mode and symlink changes with exact before/after target projections; Claude
  and Codex produce the same compact JSON/digest and reject an empty map,
  unchanged entry, path traversal, implicit rename or wrong base-target byte;
- a Story/Test-changing backlog handoff and `claim-items` race through the
  same Fence in both deterministic orders. Source-first blocks reservation/
  claim until the exact approved target mapping lands; claim-first rejects the
  backlog handoff with zero target/provider mutation. Target-update response
  loss retains the source Fence and converges by exact target/PR requery;
- incorporated Requirement supersession races scope reservation/claim for one
  of its current Stories in both orders. Supersession-first makes the Story
  nonselectable before Fence release; Delivery-first preserves the pinned old
  source and rejects terminalization before target mutation even before Item
  claim. An unrelated Delivery may coexist after the affected-coverage scan
  and a retry;
- unchanged approved bytes/timestamps remain unchanged;
- currently claimed and successfully delivered story mutation is rejected;
- unclaimed never-successfully-delivered story revision is accepted; a prior
  cancelled claim remains rejected before cancellation target merge or claim
  cleanup, then becomes eligible and preserves the historical cancelled
  Delivery's pinned old `backlog_commit` while a future Delivery pins the new
  source hash;
- a scope-approved but unclaimed selected Story/test change invalidates Scope
  and Execution Plan approvals and requires `revise-unclaimed-scope`; an
  unrelated append preserves pinned hashes;
- an actionable final-review lesson routes through a separate technical
  Requirement authoring branch, target handoff and aggregate-review rerun;
  Delivery branches never write Requirement/backlog files;
- every normal gate's approve/request-changes/stop outcome preserves the exact
  expected resumable state and performs no inferred destructive transition;
- global coverage and exact review sets remain closed.

### 23.6 Delivery schema and planning

- ID, slug, path and branch normalization;
- `/delivery-plan` with no argument asks for new goal/scope and never resumes;
  free text creates a proposal and exact `DLV-###` resumes/revises before
  claims; `/execution-plan` and `/deliver` require exact `DLV-###` and reject
  zero/multiple-candidate guessing without exposing internal coordinator verbs;
- `/deliver DLV-###` exposes state-valid continue/pause/resume/cancellation/PR
  actions only; missing `max_parallel` routes through public `/configure`,
  creates no Slot, and returns the exact same Delivery resume entry after the
  protected target handoff;
- exact Fence, Integration, Item and Slot ref grammars accept the documented
  examples, reject uppercase/slugs/manual suffixes/slot `000`, and reserve the
  complete `agentrof/**` namespace;
- Delivery slug is goal-derived and independent from Requirement slugs;
- declined/crashed/stale local scope proposal lives only in detached runtime
  scratch and leaves no dirty checkout, ref, worktree or consumed ID;
- case-folded story IDs cannot collide in item paths or branch refs;
- one Delivery Goal and exact story set;
- epic rejected as executable scope;
- claimed or successfully delivered story rejected; an unclaimed duplicate
  plan is reported but item-ref creation remains the sole reservation race;
- the losing claim UX offers only wait-for-cancellation, revise scope or cancel;
  successful winner merge removes wait permanently, while winner cancellation
  plus exact cleanup permits the unchanged claim retry;
- dependency cycles and unmet cross-Delivery dependency rejected;
- external backlog `depends_on` and execution-only `waits_for` each receive
  their distinct exact binding; open, cancelled, reclaimed and successfully
  merged predecessor proofs cannot be confused;
- path prefix and contract collision detection;
- overlap allowed only with explicit ordering;
- wave derivation deterministic and user wave edits rejected;
- post-claim story-set edit is rejected; a plan-only revision pauses all active
  items, updates only affected item-plan hashes, preserves unchanged
  integrated evidence and rejects a change to an integrated item projection;
- role sequence uses only closed team roles;
- role sequence contains the story owner and every supporting role exactly
  once, mandatory Code Reviewer/QA exactly once in legal order, and the
  conditional UX evaluator only when declared/applicable; the applicable case
  executes Code Review -> UX evaluation -> QA, while the non-applicable case
  omits UX without changing the remaining order;
- phase-exact item fields reject premature hashes/bases, missing approved-plan
  fields, null placeholders and evidence files created before plan approval;
- each initial claim materializes only its own `integration_base_commit` at the
  claims-established marker; reopen updates only that Item's base to the exact
  authorization head, with all sibling/product trees unchanged;
- immediately after a multi-item claim, the Integration tree passes with
  approved-plan/no-base projections while every Item ref passes claimed/base
  validation; after partial integration only the merged Item requires its base
  in Integration and all remaining Item refs remain independently exact;
- phase-exact scope-only cancellation accepts an exact Item-stub review while
  forbidding synthetic plan/integration/review/QA fields;
- scope-only and retained-Item cancellation finalization publish one exact
  `cancellation-finalized-v1` transaction: empty versus complete Item ref set,
  projection hash, parent/tree and zero-Slot rules have golden cases. Initial
  publication creates the exact complete terminal Item set; later same-intent
  re-publication verifies those OIDs and mutates Fence + Integration only.
  Two clones with different candidates, prepublication/published target refresh
  versus finalize and accepted-response loss produce one legal complete phase
  or none, never a remote subset or second Item-cancelled child;
- before cleanup, target Item fields, Review table, projection hash and every
  retained `item-cancelled-v1` trailer must match. After exact ref cleanup, a
  clean clone reconstructs the same cancellation from target/package/merge
  evidence alone; a field/table/hash mismatch fails even when deleted Item
  control objects are unavailable and no validator requires their recovery;
- execution-plan publication accepted with response loss and an
  `awaiting_claims` claim collision can both proceed to claims-free
  cancellation: the current Plan projection remains exact, every Story stays
  `not_started/none`, the finalization mapping is `{}` and replay never creates
  a synthetic Item branch;
- cancellation preview Request changes/Stop before the barrier are byte/ref/
  provider no-ops; after the barrier, Request changes preserves intent and
  dispositions while evolving only cancellation-compatible review/revert
  evidence, and Stop resumes the same fenced cancellation rather than work;
- `execution_after` accepts only sibling items, while `waits_for` accepts only
  a story claimed by a different open Delivery; its exact Delivery ID, initial
  claim OID and story hash binding blocks until that parent Delivery's full
  target closure and becomes stale on cancellation/reclaim;
- no duration, estimate, points, capacity or identity fields accepted;
- multi-Requirement built-experience applicability derives only from the
  Story's exact constraining sources, declared roles and scenarios;
- path claims reject absolute/traversal/backslash/glob/empty components and
  symlink escape; generated ref arguments cannot be interpreted as Git options;
- inspect JSON is mutation-free, apply binds the recomputed
  `mutation_plan_hash`, finding codes are stable across hosts and secret
  sentinels never appear;
- all configurable designations render correctly with exact fixed colors. A
  live, successfully closed or cancelled Delivery package makes both hosts
  reject designation config/reconcile with
  `DELIVERY_DESIGNATION_CHANGE_BLOCKED`, `mutation_state: none`, exact zero
  file/ref/provider mutation and byte-identical historical approval hashes.
  Only the no-Delivery-ever case updates all affected config/title/H1/hash
  projections, and the first later Delivery pins them;
- DoD bootstrap completes before Delivery ID allocation and pins its source
  hash in scope approval.
- `/configure DOD` performs single-path N to N+1 revision with Approve,
  Request changes and Stop outcomes; two concurrent revisions make the loser
  reapply/reapprove, no second DoD file/live `superseded` state appears, and
  configure returns the explicit original Delivery command without consuming
  ID/ref/Slot state on either host.
- an active Delivery pinned to DoD revision 1 continues to validate the exact
  historical blob after target approves revision 2; a new Delivery pins
  revision 2, while a missing/tampered historical object fails closed rather
  than reading the current path.

### 23.7 Multi-clone Git races

Use at least two independent clones and one local bare remote:

- Fence and Slot remain remote-only with no worktree; Integration and Item use
  exact matching local branches/worktrees; remote-tracking refs never grant
  writer authority, and merge candidates create no temporary named ref;
- two Deliveries claim the same story simultaneously; exactly one wins;
- unrelated Fence churn rejects a Delivery reservation without
  consuming its still-absent ID; refetch retries the same ID, while a real
  integration-ref/target-alias collision allocates the next ID;
- multi-story atomic claim creates all refs or none;
- initial `publish-execution-plan` changes only Fence + Integration to one
  `execution-plan-published-v1`; it creates no Item/Slot/worktree. Accepted-
  response loss replays complete, identical two-clone publication converges,
  differing plan hashes yield one winner plus `DELIVERY_PLAN_STALE`, and raw
  Integration push remains denied;
- plan approval `P` followed by a disjoint same-plan target refresh `R` claims
  atomically from `R` as the effective approved plan head; another clone using
  stale `P` loses, and a relevant refresh cannot use this preservation path;
- an execution-approved/no-claim Delivery survives upgrade and claims from the
  exact validated upgrade release carrier, both when Plan/Scope bytes remain
  unchanged and when zero-claim source reapproval was embedded in the upgrade;
  stale pre-upgrade publication, wrong Scope/Plan/impact or an upgrade chain
  that ever contained an Item ref cannot claim;
- pre-claim scope revision requires zero item/slot refs, fresh Scope and
  Execution Plan approvals and one project/integration CAS; a losing Delivery
  may wait only for competing cancellation, never for successful duplicate
  delivery;
- `delivery-scope-revised-v1` golden graphs cover same-target one-parent and
  selected-source two-parent revisions, exact old/new Scope and Target hashes,
  Plan/evidence invalidation, subject/trailers/tree boundaries and mandatory
  fresh `execution-plan-published-v1`. Stale/response-lost/two-clone cases
  produce one exact revision or none; the revision itself is never a claim
  carrier and creates no Item/Slot/worktree;
- two clones reserve the same Delivery ID with different slugs; the ID-only
  integration ref permits exactly one winner; the loser discards the rejected
  package bytes, rematerializes the byte-identical semantic Scope under the
  next ID with a new `scope_hash`, and persists its sole compiler-stamped
  approval without a second semantic user gate. Any semantic/slug change or
  replay after one reservation exists requires reapproval;
- repeat ID-retry classification where the winner scopes/claims/merges the
  loser's selected Story: the loser creates no next-ID reservation. Unrelated
  target/backlog append preserves exact selected inputs, while selected
  Story/Test/DoD/dependency or Goal/slug change discards the approval proof and
  requires a fresh preview; every rejection leaves no loser package/ref;
- different stories claim successfully;
- a second Delivery introduces a path/contract overlap after the first plan was
  approved but before its Item activation; the mandatory fresh activation scan
  blocks the unordered pair with the exact path/contract finding and creates
  no Slot or writer receipt;
- target advances after plan approval: a disjoint delta produces one validated
  two-parent `target-refresh-v1` and start authorization pins that target;
  before any Item ref exists, a same-path or contract-relevant delta emits
  `target-refresh-v1/plan_invalidated`, carries `Plan-Hash: none`, creates no
  barrier/Item/Slot/receipt and requires a newly approved/published Plan. Two
  clones racing stale-plan claim against that refresh give exactly one legal
  result; the stale Plan never becomes a claim carrier. A selected Story/Test
  byte change instead uses the approved two-parent Scope revision followed by
  fresh Plan publication;
- the total target classifier assigns every normalized atom exactly once. A
  second Delivery's product path, Delivery directory and regenerated shared
  map plus an unrelated Requirement/backlog append are disjoint to the first
  Delivery before and after claims. Mixed disjoint+claimed impact includes
  only the accountable Item; mixed disjoint+source/manual blocker publishes
  nothing. Synthetic zero-owner/double-owner atoms hard-fail rather than
  falling through;
- target advances from scanned `T1` to `T2` after activation candidates are
  prepared/accepted but before receipt promotion: both disjoint and relevant
  interleavings pause/delete the new Slot before product work; only the
  disjoint path refreshes and resumes with a new epoch, while the relevant path
  remains fenced for its exact decision;
- after the first Item claim, the affected-set matrix has exactly two cases:
  empty uses ordinary disjoint refresh; any nonempty set uses zero-Slot plan
  revision, changes
  `target_impact_hash`/`plan_hash`, requires fresh approval and publishes one
  atomic reconcile release. All-integrated, all-unintegrated and mixed cases
  prove their exact Item record set; the old ordinary-refresh/reopen path and
  old Plan hash are rejected with zero mutation;
- the all-unintegrated case converges end to end through
  `plan-revision-target-refresh-v1`, fresh impact-bound approval, atomic
  `unintegrated_rebase`, later activation and integration; it never loops on
  the same baseline. With an open PR, every selected path first drafts and
  later reuses that same PR;
- a mixed integrated/unintegrated impact produces one cumulative hash,
  `integrated_reopen` plus `unintegrated_rebase` tips in the same release, and
  blocks transitive descendants until both accountable Items pass fresh gates;
  repeat with multiple target advances under one epoch and abort both before
  and after refresh to prove the bounded abort rule;
- exercise both a clean same-path auto-merge and a true textual conflict after
  Item integration. The clean result is deterministic and still invalidates
  affected evidence; the conflicting refresh candidate takes the exact target
  entry, contains no interactive Integration-authored product bytes, and the
  reopened Item owns the reapplication/resolution diff bound by fresh Code
  Review and Verification;
- after the irreversible cancellation barrier, target advances on both a
  never-started claim path and an integrated-then-reverted path: the exact
  `cancellation-target-refresh-v1` epoch/intent wins or loses atomically and
  creates no Slot or Item reopen. Before first publication, the
  `prepublication` record exposes no revert/finalization/terminal Item ref and
  forces the whole local candidate to be rebuilt before empty-diff approval.
  After first publication, the `published` record preserves the exact existing
  `item-cancelled-v1` OIDs, proves the empty target-relative product/test diff,
  invalidates Review and republishes only Fence + Integration. Repeat with the
  cancellation PR already open and prove the same draft PR is reused;
- two machines race for the last slot; exactly one wins;
- two machines race to activate the same item into different free slots;
  exactly one item-ref CAS and one slot win;
- activation/resume/reopen/takeover writes an exact pending local receipt before push; a
  conclusive lease rejection removes it, accepted-response loss on the same
  machine promotes only that matching candidate, and a fresh observer without
  it reports remote `active` but cannot write until explicit takeover;
- claims-established marker races an integration-plan advance; either the
  exact plan and all claims win or no claim exists;
- unrelated Fence churn with unchanged Integration and absent
  item refs retries the exact claim set; item or integration collision never
  takes that benign retry path;
- start-item losing only to unrelated Fence churn while Integration, Item and
  chosen Slot remain unchanged retries the same activation decision; it neither
  creates a second activation nor reports false capacity loss;
- rolling item+slot push races takeover; exactly one pair advances and the
  stale writer cannot push its local commit;
- after takeover, a stale clone that fetches the new tip but lacks the new
  local writer receipt/epoch still cannot push without explicit takeover;
- writer push races integration seal; either the complete new tip is sealed or
  integration loses and retries, never an omitted evidence tip;
- integration races takeover and pause separately: a changed Item/Slot pair or
  absent Slot is classified from its exact control record and never enters the
  Integration-only merge/retry path with a stale receipt;
- an already-active equal Item/Slot with a matching verified receipt continues
  without a control commit; without that receipt it requires takeover, while
  `resume-item` accepts only paused/no-Slot state;
- pause rejects dirty bytes, staged changes, untracked files, an unpushed local
  commit or local `HEAD` unequal to the verified remote Item/Slot with
  `DELIVERY_WORKTREE_UNSAFE`, zero mutation and the receipt retained. A clean
  equal-tip pause advances Item/deletes Slot atomically; accepted-response loss
  requeries before receipt cleanup and another machine can resume only through
  the normal explicit receipt/takeover rules;
- a sealed/integration-ancestor item rejects start and resume in a fresh clone;
- plan-revision and cancellation barriers each race a slotless-item activation;
  either activation is subsequently included in quiescence or the durable
  barrier wins and activation fails;
- the barrier matrix rejects plan-revision/cancellation/upgrade stacking in
  every ordered pair; exact finish/abort release permits only the subsequent
  kind, and an irreversible cancellation barrier is never released;
- plan revision crashes after barrier/plan approval while only a subset of the
  complete Item candidates has been prepared locally: every remote ref remains
  at the exact pre-release state. The final atomic push exposes either the
  complete Integration+Item release set or none; accepted-response loss is
  reconstructed as complete, and any observed remote subset is corruption.
  Concurrent finish versus abort yields one complete legal result;
- target moves after the final plan-revision release/abort scan, before or
  after the release CAS. A fresh pre-push observation suppresses the stale
  candidate; otherwise an accepted exact release in either ordering is
  remotely complete but returns `partial` on mandatory post-CAS refetch,
  grants no writer/worktree and converges through disjoint refresh or a new
  impact-bound revision. Accepted-response loss performs the same postcheck,
  and a competing activation joins new quiescence or fails before product work;
- takeover, pause and integration each race plan-revision, cancellation and
  upgrade acquisition; the closed operation matrix gives exactly one winner
  with no mixed Item/Slot/Integration state;
- Project Fence `upgrade` acquisition races activation, first/increase
  `max_parallel` configuration, Delivery reservation and item claims; exactly
  one actual Fence CAS wins and no writer starts in upgrade mode;
- `source_handoff` acquisition for backlog bytes and designation reconciliation
  races Delivery reservation and claim in both orders. Handoff-first prevents
  every Delivery mutation until its exact target mapping is verified; Delivery-
  first leaves target/config/title/source hashes exact. Designation handoff
  additionally rejects any target-resident or remotely reserved Delivery,
  including closed/cancelled history, and a bypassed target merge is classified
  as a repository incident rather than silently refreshed;
- source, `max_parallel` and upgrade handoffs each acquire a unique
  target-update Attempt before provider/direct target mutation. Abort-first
  proves no handoff began and leaves target exact; intent-first forbids abort,
  permits only the elected local receipt to make the one target call and lets a
  fresh clone requery/finish without repeating it. Target accepted/response-
  lost, provider object temporarily absent and delayed target merge cases keep
  the Fence conservative and never reopen the old writer mode;
- target intents durably bind exact repository, carrier kind/ref/object/head/
  base. Same-repository GitHub draft PR and direct-target golden cases let a
  fresh clone recompute the source/config/upgrade projection after unrelated
  target movement. An unmerged missing carrier ref is
  `DELIVERY_TARGET_CARRIER_INVALID`; an auto-deleted ref succeeds only after
  the exact recorded PR merge commit retains historical Head as second parent
  and target ancestry proves it. Same-tree squash/cherry-pick, duplicate/wrong
  PR, fork, moved/deleted pre-merge ref, head/base/repository mismatch and same-
  scalar/wrong-tree candidates fail closed;
- two processes sharing one runtime race both a target-update receipt and a PR-
  creation receipt. The sibling OS lock plus exact-preimage, fsynced
  `prepared -> call_started` transition elects one caller. Inject crash before
  lock, after lock/before persistence, after durable persistence/before call,
  during call and after provider acceptance; every later process requeries and
  no duplicate external call occurs;
- a target action with conclusively proven zero target effect may use
  `reauthorize-target-update`: disjoint movement yields a fast-forward carrier
  descendant and fresh Attempt in the same epoch. One same-repository atomic
  push advances exact old Fence + carrier ref or neither; two clones proposing
  different descendants, carrier/Fence lease loss, crash before push and
  accepted-response loss never expose a mixed pair. Direct keeps kind
  `direct_target`; provider retains the same draft/unmerged PR and creates no
  PR. Relevant drift, changed/merged provider object, ambiguity or partial
  target effect remains requery/manual only. Provider draft normalization
  followed by Git-CAS loss is `partial`; normalization response loss is
  `uncertain`, while a pre-provider two-ref rejection is `none`;
- provider target handoff exercises draft-to-ready response loss, required-
  check failure, target/head/base drift, external exact merge, merge-call
  response loss and post-merge branch auto-delete. Only one exact-head merge-
  commit result finishes; closed-unmerged, duplicate, admin, auto-merge,
  squash, rebase and fork-head paths fail without a second PR/call;
- table-driven Fence records accept exactly the documented open, source
  acquired/authorized, configuring acquired/authorized and upgrade acquired/
  authorized/target-handoff field combinations. Wrong intent equality, real
  Attempt without intent, stale baseline carried into `open`, mixed Source/
  Upgrade fields or omitted clearing fails on both host adapters;
- Fence epochs use exact 22-character base64url grammar. One logical upgrade
  token is accepted identically across Project Fence, N Delivery barriers,
  Item quiesce/reconcile records and its receipt; a different operation may
  not reuse it while that evidence is live/reachable. Every open operation/
  acquisition and finish/abort rotates,
  while every child of one non-open operation retains its token. Traversal,
  dot, padding, length, cross-operation reuse and stale-receipt cases fail
  before path access. Separate RNG seeds/clocks still produce byte-identical
  inspect descriptors without invoking RNG; Apply resamples forced collisions,
  and tests make no historical-nonreuse assertion after Fence cleanup/GC;
- project config-hash golden vectors match across hosts; `configuring`
  recovery distinguishes desired hash on the bound baseline from final
  `open` target/hash and rejects full-config or extra-field digests;
- out-of-band `max_parallel` drift is rejected in `open`, every Source-Kind and
  both upgrade phases; only `configuring` may move baseline to exact desired
  hash. An unrelated target append with unchanged governed hash may rebind.
  Upgrade-Contract drift is checked only in upgrade and never confused with
  Config-Hash;
- merge of an exact reviewed PR head racing `configuring` remains
  reconstructible from Integration/provider/merge evidence without historical
  Fence ancestry; configuration neither fabricates nor suppresses closure;
- deleting/corrupting the Fence while any Delivery/Item/Slot ref
  remains fails closed in `open`, `source_handoff`, `configuring` and both
  upgrade phases; only exact recoverable-OID restoration is accepted and no
  ordinary verb recreates a false `open` fence;
- upgrade acquisition races an ordinary active writer push; either its latest
  item+slot tip is included and quiesced or setup loses and replans;
- setup apply requires Fence mode `upgrade`, every open Delivery's matching
  barrier and zero active slots; release requires a fresh all-ref compatibility
  scan and exact epoch CAS;
- two incoming packages with different host-neutral upgrade contract hashes
  cannot resume one epoch; identical Claude/Codex contracts may. Golden
  vectors prove ordering invariance and exact equality, while one managed
  byte/path/mode/kind, contract digest, adapter/range or transition-writer
  change alters the hash and causes a closed resume collision;
- crash after upgrade acquisition, local apply, target handoff and partial
  Delivery migration has one exact resume/abort result; abort is rejected at
  and after `target_handoff`;
- two coordinators race the same acquired upgrade toward target handoff;
  identical epoch/contract/target becomes one success plus one
  already-complete result, while a different handoff target or contract is a
  closed collision with no epoch/baseline rewrite;
- upgrade crashes after `1/N` stable-order `upgrade-target-merge-v1`
  transactions; each completed Fence+Integration pair is recognized exactly,
  only remaining Deliveries advance on resume, and target handoff loses or wins
  atomically against the final per-Delivery merge with no partial phase claim;
- protocol 1 to protocol 2 upgrade emits transition-readable acquire/quiesce/
  abort records; a pre-handoff rollback remains readable by package N, while
  incoming writer protocol begins only after target handoff;
- a blocked item quiesces to a validated `item-quiesce-v1` paused child, and
  plan/upgrade resume plus irreversible cancellation consume the exact epoch;
- Fence, Integration, Item and Slot control updates are all
  fast-forward/create/delete operations under branch rules; no hidden
  non-fast-forward maintenance rewrite exists;
- explicit takeover changes the fencing token and rejects the old writer's
  next push;
- project-wide occupied Slots never exceed `max_parallel`;
- a stale clone after a `max_parallel` increase remains conservative, and a
  decrease is rejected from target history;
- same Delivery normal start refuses duplication and explicit resume works;
- stale slot, missing item ref and orphan integration ref produce exact
  recovery findings;
- duplicate Slots for one Item, Item/Slot divergence, malformed/out-of-range
  Slot and active-looking Item without Slot each produce a distinct fail-closed
  reconciliation code and no guessed repair;
- missing item ref restores from the exact current slot tip, including its
  latest product/evidence commits, not the original activation ancestor;
- claimed, paused and sealed Item refs deleted while Slot is absent each fail
  closed: only an exact recoverable object may be lease-restored, sealed
  recovery proves the Integration merge parent, and unrecoverable history is a
  manual incident with no synthesized branch or released story;
- orphan reservation with unique commits cannot be deleted or reused;
- integration CAS rejects a stale writer without losing commits;
- a rejected integration candidate never diverges the local integration ref;
- integration contains the exact ready remote item tip, including review and
  verification evidence, before slot release;
- no force, rebase or dirty-worktree cleanup path exists;
- every closed control record has table-driven valid/invalid golden cases for
  missing/extra/duplicate trailers, value grammar, parent order, tree diff,
  legal prior state and exact mutation set on both host adapters;
- `delivery-barrier-release-v1` specifically rejects a missing Target or any
  mismatch among release Target, returned open-Fence Target,
  `Handoff-Target` and Item reconcile Target;
- the closed `Agentrof-Record` set and required-subject table are identical:
  every record kind accepts exactly its derived subject pattern and rejects an
  unknown record, missing table row, extra table row, wrong ID/kind/mode or
  otherwise mismatched subject on both host adapters;
- reserve, claim, start, active push, integrate, barrier begin/release and
  cleanup each inject remote-accepted/client-response-lost; replay recognizes
  the exact success and creates no second commit/ref/slot/seal;
- cancellation loses its response immediately after intent+barrier push; a
  fresh clone reconstructs the approved intent/tips/dispositions and continues
  without re-asking or changing them;
- cancellation publication golden graphs cover zero, one and multiple
  product-changing seals: two Stories editing one path and one Story
  integrate/reopen/reintegrate each produce the exact reverse first-parent
  record sequence. Target text/binary/add/delete/mode/symlink changes use raw
  target-dominated entries; unchanged overlap restores the named merge first
  parent and same-tree reverse records are accepted when already neutral.
  Every intermediate tree/OID, local parent chain, finalization parent and one
  atomic Review+complete-Item publication is golden. Missing, duplicate,
  alternative-entry, wrong order/inverse/parent, stale target, competing clone
  and provider-merge interleavings publish the whole candidate or none;
  accepted-response loss reconstructs without a second revert/approval, and
  post-publication additive revert records are rejected with
  `DELIVERY_CANCELLATION_FINALIZATION_STALE`/`none`, while a documentation- or
  evidence-only correction re-publishes through Fence+Integration with the
  exact existing terminal Item set;
- expected worktree path collision or materialization failure after successful
  activation preserves remote writer truth and offers the exact pre-writer
  pause/reconcile path, never a second Slot/worktree. No-worktree and clean/
  equal partial registration may publish the paused child directly from the
  verified remote tip; dirty/ambiguous partial state retains Slot+receipt and
  reports `DELIVERY_WORKTREE_UNSAFE`. Accepted-response loss converges without
  touching the colliding path;
- cleanup races a new reservation/configuration and loses only its exact Fence
  deletion lease; the closed Delivery and new operation both remain valid;
- cleanup races source-handoff acquisition, authorization, finish and abort in
  both orders. Fence deletion succeeds only from exact `open` with every mode/
  carrier field cleared and current target/config proof; no non-open intent is
  ever garbage-collected by cleanup;
- same-clone cancellation cleanup CAS-deletes an exact safe old local Item ref,
  then a later legal reselection creates the same standardized branch/worktree
  name from the new remote claim without reset. Absent/ancestor-clean refs
  converge after response loss; checked-out, descendant, divergent or unique-
  commit refs remain untouched with `DELIVERY_LOCAL_REF_DIVERGED`, and another
  clone's new claim is never changed by stale local cleanup;
- remote without atomic support fails before work starts.

### 23.8 Execution and evidence

- changed file outside path claims pauses execution;
- configured contract scanners block undeclared machine-detectable changes;
  semantic-only contract completeness appears as explicit architecture/review
  evidence rather than a fabricated generic detector;
- integration-derived paths are excluded from item ownership while conflict
  resolutions remain owned and gated;
- code review and QA bind the same product/test commit;
- evidence-only commit preserves gates;
- PR URL metadata changes the full source hash but preserves the immutable
  approval hash and cannot alter review prose or verdict;
- source/approval/scope/plan/item-plan golden vectors prove exact
  non-self-referential canonicalization on both hosts; target-impact vectors
  cover literal `none`, one clean Item, one conflict, mixed paths/Items,
  transitive descendants and a cumulative second target round with byte-equal
  JSON and `sha256:` digests on Claude and Codex;
- changing a predecessor item projection changes every transitive descendant
  item-plan hash while an unrelated parallel item hash remains stable;
- plan-only `HP-DEL-06` reconciliation emits exact
  `Target-Impact-Hash: none` plus the validated release target on every Item
  sync record; missing, fabricated-digest and wrong-target variants fail the
  table-driven wire/tree tests;
- product/test commit invalidates review, verification and Delivery Review;
- integration advance requires item update and affected gate rerun;
- full aggregate test/mutation/runtime/UX matrix follows configured impacts;
- one-story Delivery uses the same compact contract;
- multiple roles operate sequentially on one item branch without assignee
  state.

### 23.9 PR and closure

- one integration PR contains code, Delivery docs and updated map;
- initial normal review, reapproval while the same PR is draft, and final
  cancellation review each publish through `delivery-review-published-v1`.
  Golden tests prove `reviewed_integration_commit` equals the pre-stamp parent,
  the publication tree is review/map-only, Fence+Integration CAS is atomic,
  stale parent/review/provider state changes nothing and accepted-response
  loss replays without a second approval commit;
- normal and cancellation Review publication race provider merge in both
  deterministic orders. Publication-first advances the exact draft PR head and
  makes the old reviewed head unmergeable; merge-first proves closure of the
  prior head and publishes no Review/finalization/Item descendant. Cancellation
  publication additionally proves its `cancellation-finalized-v1`, Review and
  complete retained-Item set are all remote or all absent;
- normal and cancellation Review publication race target advance around the
  final observation. Target-first returns `mutation_state: none` with no Review
  commit. Publication-first, including accepted-publication-response-loss,
  requeries to `partial` and never marks the PR ready. If the invalidation CAS
  wins, the same invocation may proceed to the phase-exact target refresh and
  later fresh Review approval on the same PR. If invalidation loses, it returns
  `DELIVERY_REVIEW_STALE`; the next resume first publishes/reconstructs that
  invalidation and only then refreshes. A stale published Review can never pass
  a clean-clone handoff;
- an evidence/review-only Request changes publishes exactly one
  `delivery-review-invalidated-v1` from the latest Review, leaves every Item
  ref and product/test byte unchanged, makes the existing PR draft at the new
  head and blocks a fresh clone from treating the old approval as ready.
  Two-clone CAS, accepted-response loss and provider-merge races each produce
  one complete invalidation, one already-complete result or merge-first
  closure, never a second PR or a mixed approved/draft state;
- the GitHub adapter verifies exact head/base and no second open PR for the
  integration ref;
- provider PR creation succeeds but response or URL-record push is lost;
  replay discovers the same exact PR, preserves approval hash and leaves one
  PR;
- two clones both observe no lifecycle PR and race `pr-creation-intent-v1`;
  exactly one Fence/Integration CAS and local call receipt win, provider create
  call count is one, and the loser switches permanently to all-state requery;
- one exact pre-existing PR with no intent is made/verified draft, adopted by a
  single `pr-adoption-intent-v1` Fence/Integration CAS and URL-recorded with
  provider create count zero; ready, closed-unmerged, merged and duplicate
  states follow their exact draft/reopen/incident paths. Adoption accepted-
  response loss resumes from a fresh clone without POST;
- provider normalization (ready-to-draft or closed-to-reopened-draft) succeeds
  but the following adoption/intent CAS loses to another Integration mutation:
  the result is `partial`, the winner/current provider object is requeried and
  no caller posts or records a stale URL. Repeat with normalization response
  loss, two local processes sharing one runtime and a fresh clone;
- a canonical matched URL record whose provider object is temporarily absent
  performs bounded all-state requery and then returns the exact manual incident;
  it never falls through to adoption, intent election or provider create;
- external draft PR creation races local creation-intent CAS in both orders;
  the intent holder's mandatory pre-POST requery adopts the exact PR when it
  appeared and total provider create count remains zero/one as appropriate,
  never two;
- after a creation intent wins but before POST, an exact external PR appearing
  as OPEN-ready or CLOSED-unmerged is normalized to the same draft object (or
  yields the closed reopen-unsupported finding), then URL-recorded through the
  existing creation intent with provider create count zero. Temporary absence,
  normalization response loss, merged state and duplicates follow the total
  classifier and never fall through to a blind POST;
- an unmatched PR intent races plan revision, cancellation, reopen, upgrade
  and activation in both operation orders: the earlier Integration CAS wins,
  and once intent wins only its exact requery/URL-record transition is legal;
- coordination precedence renders approved review with no PR, unmatched
  intent, draft PR, provider uncertainty and closed-unmerged PR as
  `pr_handoff`, never the older `claimed` state; active reopen still precedes
  it and ready exact PR becomes `awaiting_merge`;
- ready normal and cancellation PRs remain `awaiting_merge`, never `claimed`,
  while an unrelated Fence `source_handoff` or `configuring` operation is
  active. The board exposes the handoff blocker/next entry and the merge action
  returns zero mutation. Abort/finish without target movement restores merge
  permission; target movement routes to refresh/Review/reapproval. Provider-
  side merge during the block still follows the existing closure/incident
  proof and is not authorized by the status label;
- scope-only, unintegrated-discarded and integrated-reverted cancellation each
  derive `review` after barrier/dispositions while review is incomplete,
  `pr_handoff` for approved/no-PR, unmatched intent and URL-recorded draft,
  `awaiting_merge` only for the ready exact PR, then `target_merged`, without
  pretending discarded Item tips are Integration ancestors;
- provider accepts the create but its response is lost, then another actor
  closes the draft before URL recording; open/closed/temporarily-empty lookup
  orderings never issue a second create, and a stranded intent becomes the
  exact manual incident;
- definitive no-mutation denial returns `mutation_state: none`; provider
  accepted but URL not recorded is `partial`; ambiguous acceptance is
  `uncertain`; fresh requery/recording converges to `complete` without changing
  the approval hash;
- normal flow creates the PR as draft, records its URL into the same branch,
  verifies the portable head and only then marks it ready; an externally
  bypassed pre-record draft merge is a repository incident and never causes a
  post-merge `record-pr` commit;
- target advance invalidates final review until merged and rerun;
- target advance between the last check and an externally forced merge fails
  closure unless the merge first parent is already an ancestor of the exact PR
  head;
- merge commit preserves integration ancestry and closes Delivery;
- provider merge succeeds but response/verification fetch is lost; reconcile
  proves that existing merge and never submits a second merge;
- squash and rebase merges do not close Delivery;
- merge-commit-only repository policy is verified before item activation;
- accidental squash/rebase retains claims, fails closure and reports a manual
  repository incident; v1 does not invent an impossible same-PR recovery;
- red required checks on the exact merged head fail closure even after an
  external/admin merge; a closed-unmerged PR fails `awaiting_merge` and only
  the same explicitly reopened PR may resume;
- PR-open then item-reopen/fix returns to active/review until the new exact head
  is reapproved; target-refresh, reopen and cancellation reapproval preserve
  exactly one lifetime intent/URL-record commit and the same PR, create no POST
  or adoption intent, idempotently update current title/body/head/base, reject
  the stale old body and converge a lost ready-update response by provider
  requery. A stale URL alone never yields `awaiting_merge`;
- merge Stop leaves the same PR open and exact Delivery `awaiting_merge` with
  `/deliver DLV-###` resume and no ref/provider mutation;
- merge Request changes with product/test scope requires a concrete finding and
  exact `reopen-item`, while evidence-only changes return Delivery Review to
  draft/reapproval; neither path merges or opens a second PR;
- target refresh, plan revision, cancellation, reopen and upgrade each race provider merge in
  both deterministic orders: transition-first changes/drafts the PR head and
  blocks stale merge; merge-first closes the prior reviewed head, prevents all
  post-CAS work and exact-cleans only control projections while retaining any
  unique semantic descendant for explicit disposition. Cancellation PR proves
  its separate allowed barrier predicate;
- cancellation target refresh races its cancellation PR merge in both orders:
  refresh-first advances the exact barrier-bound head and forces cancellation
  review/reapproval on the same PR; merge-first proves closure of the prior
  reviewed cancellation head and performs no refresh, Item reopen or second PR;
- a merge racing `max_parallel` configuration closes solely from durable
  Integration/provider/parent/check evidence; a clean clone needs no deleted
  Fence history;
- cancellation after partial integration uses explicit revert commits and
  produces a documentation-only target diff without history rewrite;
- local proposal discard, scope-only cancellation, active-unintegrated discard,
  retain-as-pause, existing-PR cancellation, barrier lease loss, post-cancel
  cleanup failure and later legal reselection each follow their exact distinct
  path;
- cleanup deletes only exact expected refs and clean worktrees;
- cleanup failure leaves a closed Delivery plus retryable finding;
- dependent Delivery activates only after verified target merge;
- PR merge never sets Release Management state.

### 23.10 Obsidian, upgrade and hosts

- active remote board renders effective statuses without unresolved Wikilinks;
- board renders semantic and coordination status separately for blocked,
  paused, cancelled and active combinations;
- default board/host output exposes Goal, Scope, blocker, next action and human
  WIP without ref/OID/Fence/CAS jargon; explicit Diagnostics exposes the exact
  technical evidence;
- `locate` resolves the main-worktree-anchored active vault even when invoked
  inside a linked worktree;
- stale/offline board is marked stale and returns nonzero;
- target-resident Delivery map is resolvable in a clean clone;
- portable vault gate passes with ignored local board absent;
- all per-Delivery and root `_generated` views are absent from the canonical
  clean-clone package and regenerate deterministically;
- project-local runtime worktrees survive setup refresh;
- package N to N+1 preserves authored data and explicit `max_parallel`;
- package manifests validate exact file hashes and the closed
  `delivery_protocol` read/write/transition range; supported old record versions select a bundled
  adapter, while missing/unknown/out-of-range protocol trailers fail before
  any ref or project mutation;
- `upgrade_contract_hash` has one full cross-host golden projection containing
  the literal six-resource registry, exact protocol-1 adapter row, managed
  payload and transition writer. Missing/extra/remapped resources fail exact-
  set validation; one raw byte, mode, symlink target, resource path/digest,
  adapter ID, protocol range or ordering mutation changes/rejects the digest
  identically on Claude and Codex;
- item/slot tips containing ordinary product/evidence commits resolve protocol
  from the nearest validated control ancestor; forged trailers or a broken
  first-parent control lineage fail closed;
- N to N+1 with two active item worktrees previews every impact, preserves
  refs/uncommitted authored work, upgrades only setup-owned main surfaces and
  resumes through the controlled target-merge/plan-revision path;
- the same N to N+1 protocol runs against scope-approved/no-plan,
  execution-approved/no-slot, active, paused and integrated open Deliveries;
  `upgrade-target-merge-v1` emits literal `Plan-Hash: none` only for the first
  phase and the exact current plan hash for every later phase; disjoint,
  scope-only and acquisition-baseline zero-claim source-reapproval mappings emit
  `Target-Impact-Hash: none`, while a nonempty Item mapping emits the exact
  digest. The execution-approved/no-claim cases then claim from the validated
  upgrade release carrier without another user gate;
- upgrade classifier covers setup-owned paths, unrelated append, pinned DoD,
  clean product merge, target-neutral textual conflict and user-prose conflict
  across scope-only, active, partially and fully integrated Deliveries. A
  zero-claim selected-source change requires barrier-bound Scope/Plan
  reapproval; any post-claim selected-source mutation yields zero merge/handoff
  and a repair incident;
- crash/replay after `1/N` Delivery merges, a second target advance before
  handoff and final mixed `integrated_reopen|unintegrated_rebase` release all
  preserve exact old/new protocol readability and lose no unique Item tip;
  two- and three-round golden ancestry cases parent each new
  `upgrade-target-merge-v1` from the exact prior same-epoch descendant, bind
  latest `Previous-Target` plus cumulative original-baseline impact and reject
  a jump back to the barrier;
- target advances after `upgrade/target_handoff` but before release: disjoint,
  and relevant non-source cases keep all barriers and zero Slots, run another
  same-epoch incoming-writer merge round and refresh `Handoff-Target` only when
  every open Integration is current. A selected Story/Test mutation first
  appearing after acquisition is a repair incident even with zero claims;
  acquisition-baseline zero-claim reapproval remains separately legal. Two-
  clone, crash and accepted-response-loss cases never release an older handoff
  target;
- target advances after the final upgrade release scan, before or after the
  all-ref release CAS. A fresh pre-push observation suppresses the stale
  candidate; otherwise an accepted release in either ordering returns
  `partial` on its mandatory postcheck, grants no Slot/worktree and converges
  through open-mode disjoint refresh or a new Delivery plan revision.
  Unauthorized selected-source, setup-contract or governed-config drift fails
  closed; a valid successor source/config/upgrade Fence owns convergence, and
  no case revives the old upgrade epoch;
- active N-protocol writers lose to the project upgrade maintenance barrier;
  no N+1 apply occurs without Fence mode `upgrade`, matching Delivery
  barriers and zero active slots, and release occurs only after a fresh all-ref
  compatibility rescan;
- Claude and Codex run equivalent Requirement and Delivery flows;
- both generated host manifests expose the exact retained public-entry set,
  including `/demo`, `/sketch` and `/organize-docs`; only the explicitly
  retired `/delivery-lanes` disappears and no internal coordinator verb leaks;
- actual checkout install/update smoke passes on both hosts;
- generated distribution, counts, validation and diff gates remain clean.

### 23.11 State-model, crash-consistency and hardening tests

Table-driven state tests enumerate every persisted transition in section 10.2,
every derived-state precedence row in section 10.3 and every ordered barrier
pair. For each state/action pair they prove exactly one of: legal transition,
idempotent already-complete result, stale/retry result or closed rejection.
After every action, the complete section-26 invariant set runs against the
filesystem, remote graph and provider state.

In addition to named races above, every mutating coordinator verb injects a
failure at these boundaries where applicable:

1. before local candidate/tree preparation;
2. after candidate preparation but before remote/provider mutation;
3. after remote/provider acceptance but before the client receives success;
4. after remote verification but before local worktree/receipt update;
5. during closing cleanup.

Recovery reruns from a fresh process and, for shared operations, a fresh clone.
It must classify exact remote success versus no-op versus collision without a
second semantic commit, PR, merge, claim, Slot or user approval. Local-only
uncommitted bytes are never claimed recoverable. The sole writer-local
exception is an accepted activation/resume/reopen/takeover whose matching pending receipt is
unavailable in that fresh clone: remote success remains valid, but local
mutation stays denied until the already-specified explicit takeover creates a
new fenced writer epoch. Recovery never fabricates or adopts the public epoch.

Hardening cases also require:

- inspect, denied apply and lease-lost candidate preparation mutate neither the
  project Git object database nor `FETCH_HEAD`, remote-tracking refs, worktrees
  or provider state. Fresh observation/candidate objects exist only in the
  operation-local bare cache, disappear on normal return, and an exact-path
  crash-remnant cleanup never touches semantic refs or another operation;
- target-update receipts cover source/config/upgrade `prepared`, `call_started`
  and `verified` phases. Same-machine accepted-response loss resumes only the
  matching Fence OID/Attempt; a fresh clone can requery/finish but cannot make
  the call, and a stale/different receipt authorizes no target mutation;
- no command builds a shell string from Requirement, Delivery, Story, path,
  ref, remote or provider input; subprocesses receive explicit argument arrays;
- path and worktree canonicalization rejects traversal, absolute paths,
  symlink escape, nested repository confusion and an ambiguous main worktree;
- malformed/hostile ref names and trailers cannot become command options;
- raw shell Git mutation is denied by hooks but final correctness still comes
  from portable validation and remote CAS;
- diagnostics redact tokens and credential-bearing URLs;
- generated/ignored/runtime deletion cannot change semantic truth;
- test cleanup deletes only its own explicit temporary clones, bare remote and
  sandbox-provider objects, and a failed test leaves evidence for diagnosis
  rather than using broad destructive cleanup.

### 23.12 Test execution, traceability and evidence

The future implementation adds all hermetic suites to ordinary independent
unittest discovery and therefore to `make check`. The minimum local proof is:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/tests -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 python3 tools/validate.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/build_distributions.py --check
git diff --check
make check
```

Focused development commands may run one suite, but no implementation gate
closes without the full discovery command. The release candidate additionally
runs regenerated-package install/update smoke for Claude and Codex and the
live GitHub sandbox journey. Provider credentials are injected only for that
command and are never written to fixtures or artifacts.

The contract registry emits one ephemeral CI summary mapping invariant,
transition, failure row and command verb to passed stable case IDs. It does not
write into `workspace/docs/`, `.agentrof` or package manifests. A missing
mapping, duplicate ID, undiscovered test module, skipped required case or host
parity mismatch fails the build.

## 24. Final release gates for this implementation

The change is complete only when all are true:

1. Canonical terminology contains Requirement Flow, Delivery Flow and Delivery
   with no active Sprint, greenfield/existing routing or Delivery Lane entry.
2. Requirement and backlog revision end-to-end tests are green.
3. Multi-clone item-claim, slot and integration race tests are green.
4. Every section-23 happy path, state transition, failure row, control-record
   grammar case and crash boundary is registered and green with no skip.
5. One complete Delivery reaches a verified merge commit through both host
   projections.
6. A second concurrent Delivery proves independent parallel progress and
   global WIP enforcement.
7. A conflicting Delivery proves atomic story-claim blocking, known
   path/contract planning findings and safe revalidation when two distinct
   stories race.
8. An incorporated-Requirement, Story/Test or designation target handoff proves
   the shared Fence race in both orders and never terminalizes/mutates a claimed
   source package or releases an old writer after target-update intent.
9. A clean clone reconstructs target-resident knowledge and a fresh remote
   board.
10. Upgrade N to N+1 preserves project content, active Git work and configured
    terminology.
11. `tools/validate.py`, release validation, counts, generated-distribution
    checks, independent test discovery, `git diff --check` and full
    `make check` pass.
12. Actual Claude and Codex install/update smoke passes after regenerated
    distributions.
13. The live GitHub sandbox journey creates exactly one merge-commit PR,
    reconstructs closure from a clean clone and cleans only its named test refs.

## 25. Explicit exclusions

This plan does not implement:

- production deployment or Release Management;
- release trains, environments, promotion or rollback policy;
- Scrum roles, ceremonies, fixed Sprint length or velocity;
- time estimates, points, target dates or capacity planning;
- PMO, Control Tower, databases, project keys or cross-project state;
- person assignment, agent/session ownership or durable task scheduling;
- stacked Delivery PRs;
- squash/rebase merge compatibility;
- an external issue tracker as canonical Delivery truth;
- automatic recovery after a provider violates the verified merge-method
  policy;
- a second active-state store in Markdown, JSON or `.agentrof`.

## 26. Implementation invariants

The implementer must preserve these invariants throughout:

1. Every backlog delta begins from an approved Requirement.
2. Every Delivery derives from exact approved story/test-plan hashes.
3. One story has at most one open deterministic item branch.
4. One item has at most one occupied global slot.
5. Occupied slots never exceed configured `max_parallel`.
6. One active Delivery Item has one remote fenced writer right. Stale physical
   worktrees may exist on other machines but cannot push.
7. One Delivery has one ID-only integration branch and at most one final PR in
   the supported lifecycle; a prohibited external merge is a fail-closed
   repository incident, not a second automatic PR path.
8. Item branches and integration branches are never rebased or force-pushed.
9. Only merge commits can close a v1 Delivery.
10. Backlog planning state is never rewritten by Delivery.
11. Git refs prove coordination; Markdown proves semantic decisions.
12. Generated boards are projections and never canonical state.
13. A change outside approved scope stops and replans; it is not silently
    absorbed.
14. Review, QA and user approval bind exact product/test commits.
15. PR merge closes Delivery but does not imply Release.
16. No implementation step may reintroduce PMO, SQLite, global `.agentrof`,
    work orders, assignees or session ledgers.
17. Item-ref CAS is the sole atomic story-reservation authority; plan selection
    and path/contract scans never pretend to be distributed locks.
18. Slot activation and item-writer fencing change atomically, and a slot is
    never reconstructed silently.
19. A cancelled Delivery never freezes a story as successfully delivered.
20. `max_parallel` has no default and never decreases in v1 after its first
    committed value.
21. Start/resume changes integration, item and slot in one transaction; it
    rejects active barriers and sealed/integrated item tips.
22. Plan revision, cancellation and upgrade use durable barriers; an empty-slot
    observation alone is never quiescence. During upgrade, the project
    Fence tip has mode `upgrade`, every open Delivery has its matching
    upgrade barrier, active items have been quiesced and no slot remains;
    there is no maintenance-OID exception to `slot == item tip`.
23. Closure revalidates exact provider state, merge method, head/base, required
    checks and current-target ancestry; a URL or merge commit alone is not
    sufficient.
24. Every external backlog dependency and cross-Delivery `waits_for` edge is
    pinned to one Delivery ID, initial claim OID and story source hash;
    cancellation or a different claimant never changes its meaning dynamically.
25. A Delivery has at most one unmatched barrier. Barrier kinds never stack or
    supersede, and only the exact current kind/epoch may finish or abort.
26. The Project Fence config hash covers only the canonical
    `max_parallel` projection and is byte-identical across hosts.
27. Every compiler-owned control record follows the one closed trailer,
    parent, tree-diff and mutation grammar; unknown or malformed records fail
    before mutation on both hosts.
28. Remote Item/Slot CAS is writer authority; the local receipt prevents a
    stale cooperative coordinator from adopting a fetched takeover epoch and
    never claims to be a credential-level security boundary.
29. An irreversible cancellation barrier atomically binds the exact approved
    intent hash, pre-quiesce tips and dispositions, including scope-only
    cancellation without synthetic execution evidence.
30. An active upgrade is bound to one host-neutral upgrade contract and phase;
    another payload cannot resume it, and pre-handoff abort remains readable by
    the outgoing package.
31. Merge closure is reconstructible from target, Integration, package and
    provider evidence; it never depends on deleted historical Fence ancestry.
32. Routing, backlog review, maps and boards share one exact
    `requirement_incorporated` predicate.
33. `resolved_no_change` closes a proven no-work Requirement without a fake
    Story; incorporated Requirements cannot be withdrawn.
34. Every supported target mutation that can terminalize a selectable Story's
    Requirement source, alter a selectable/frozen Story/Test byte or reconcile
    configured designations is fenced by one exact `source_handoff`;
    reservation/claim and that handoff cannot both win from the same Fence
    observation.
35. Source, governed-config and upgrade target writers cross one durable
    target-update intent before provider/direct mutation. Abort is impossible
    after that point; response loss is recovered by requery/finish and never a
    blind repeat. A fresh same-intent Attempt requires conclusive zero target
    effect and atomically advances the same-repository Fence + exact carrier-
    ref pair; provider form retains one draft/unmerged PR and fork carriers are
    forbidden in v1.
36. Before cleanup, a cancelled Item's target-resident disposition, prior tip,
    retained remote record and Delivery Review projection are one canonical
    fact. After cleanup, target Item fields, Review table and projection hash
    remain sufficient; deleted coordination trailers are never required.
37. Read-only observation and rejected candidate preparation never mutate the
    project repository's object database, refs, worktrees or provider state;
    disposable operation-local Git objects are not semantic truth.
38. Every external create/merge/direct-target call is elected by one crash-
    durable, process-exclusive exact-preimage `prepared -> call_started`
    transition; only that transition's process may call.
39. A target carrier is same-repository and exact-head. Reauthorization
    atomically advances Fence plus its existing carrier ref; a mixed ref pair,
    rewritten carrier, fork or non-merge-commit provider result is unsupported.
40. Every plan-revision/upgrade release binds one Target across release, open
    Fence and Item reconcile records. Remote release may be complete while
    workflow convergence is `partial`, but no Slot/worktree is authorized
    until post-CAS target validation or a closed successor operation owns the
    newer target.
41. Every target-delta atom has exactly one classifier owner; only the true
    remainder is disjoint, and any blocker rejects the whole candidate.
42. Remote Item ref owns a Story claim. A same-named local Item branch is
    reusable only after exact safe CAS deletion and is never force-repointed.
43. Cancellation publishes one mechanically derived reverse chain with one
    record per product-changing Integration seal, and its final product/test
    tree equals the exact current target.
44. Configured designations may reconcile only before the first Delivery
    package/ref exists; historical approved Delivery bytes and hashes are never
    silently retitled.
45. An exact ready PR remains `awaiting_merge`; a non-open source/config Fence
    blocks merge authority without regressing coordination truth.
46. Inspect hashes nonce-bearing work through closed semantic postimages and
    performs no RNG/materialization. Apply resolves fresh markers only after
    observations, leases and the supplied plan hash still match.

## 27. Normative external foundations

- Git remote compare-and-swap and atomic multi-ref behavior:
  <https://git-scm.com/docs/git-push>
- Stable machine-readable worktree discovery:
  <https://git-scm.com/docs/git-worktree>
- GitHub repository merge-method policy:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/configuring-commit-merging-for-pull-requests>
- GitHub repository settings exposed to the provider adapter:
  <https://docs.github.com/en/rest/repos/repos>
- GitHub PR head/base/check/merge evidence exposed by the CLI:
  <https://cli.github.com/manual/gh_pr_view> and
  <https://cli.github.com/manual/gh_pr_merge>
