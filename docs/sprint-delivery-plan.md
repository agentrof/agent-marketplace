# Sprint delivery plan

This document records the agreed product direction and the implementation
boundary between preparation hardening and sprint delivery. It is a plan, not
a second runtime state store. Canonical project truth continues to live in the
project's tracked Markdown and JSON files.

## Product direction

- Ship one standalone Software Engineering Team for one project checkout.
- Keep PMO, Control Tower, SQLite, project keys, central coordination state,
  work orders, task-attempt ledgers and cross-project dependencies retired.
- Keep `workspace/docs/` as the project's Obsidian vault and Git-tracked source
  of truth. Keep only disposable cache and scratch data below the project-local
  `.agentrof/agent-marketplace/.runtime/` directory.
- Keep stable machine type keys, paths, relation names and graph colors. Keep
  document designations configurable because human-facing terminology follows
  the project's output and terminology languages.
- Treat the approved backlog as an immutable planning baseline. Delivery will
  be a separate tracked execution layer that links back to it instead of
  mutating approved story files.
- Use sprint-based delivery, not a claim of complete Scrum. Preserve Sprint
  Goal, exact scope, Definition of Done, Review and Retrospective semantics
  without adding a Scrum Master, Delivery Manager or PMO team.
- Keep sequential delivery as the safe default. Parallel delivery lanes will
  be optional Git branch and linked-worktree execution, bounded by dependencies,
  path ownership and a configured work-in-progress limit.

## Decision and impact record

| Decision | Positive effect | Cost or risk | Guardrail |
|---|---|---|---|
| Final approved documents are the only durable pre-backlog state | Removes audit and lock friction before delivery | A past challenge conversation is not reconstructed | Final compilers must enforce completeness, traceability and approval |
| Configurable display designations remain | Supports project language and terminology | Creation tools can drift from validation if they hard-code labels | Every creator reads project config; keys and paths remain fixed |
| Global BA coverage is exact | No approved rule or criterion silently disappears | Explicit deferrals require structured decisions | Covered and deferred sets must be disjoint and exactly equal the approved BA universe |
| Approved backlog is immutable during delivery | Stable scope baseline, simple review and clean Git history | Execution status cannot live in `story.md` | Sprint item records will carry mutable delivery state in Phase 1 |
| Git commit is the preparation handoff | No database or duplicate release ledger | Local-only work can pause at a handoff | Authoring remains unblocked; only transition to the next stage requires a clean committed approved subtree |
| Refresh is one convergent project operation | Predictable upgrades and idempotent setup | Managed projections need ownership boundaries | Inspect before apply, preserve authored/user-owned values, rollback on failed closing gates |
| Existing projects still require a scoped backlog | Delivery never starts from an undocumented change | Brownfield intake needs lighter source rules than greenfield | Explicit feature, defect and technical story sources; the same review and test-plan gates remain |
| Issue filing consumes an approved report | External writes cannot bypass project review | One extra explicit approval before posting | Fixed repository target, schema-valid report and explicit user request |
| Sequential delivery is the default | Lowest merge and recovery complexity | Less concurrency | Enable lanes only for independent scope with non-overlapping claims |
| Delivery lanes use Git live state | Avoids a Markdown or SQLite lock service | Abandoned branches/worktrees require cleanup | Deterministic branch contract, live Git inspection and serialized integration |

## Phase 0: preparation foundation hardening

Phase 0 is the only implementation scope before another product discussion.
It does not create sprint files, delivery statuses, delivery designations,
delivery compilers, branches, worktrees or lane execution.

### 0.1 Pre-backlog simplification

- Preserve live, read-only domain challenge and final compiler quality checks.
- Remove permanent challenge-round, audit-history and user-facing locked-state
  requirements from Business Analysis, Solution Design, Design System and
  Experience Design.
- Make approved final documents and their deterministic compiler outputs the
  complete durable result of each stage.
- Keep user approval at the end of each stage. Do not add per-step registry,
  lease, session or event records.

### 0.2 Backlog correctness

- Make backlog initialization, epic/story stubs and reviews use the configured
  display designations. Stable paths, IDs, type keys and JSON fields remain
  English and machine-readable.
- For greenfield work, derive the complete approved BA acceptance-criterion
  and business-rule universe. For existing projects, derive the exact universe
  only from explicitly selected `analysis_scopes`; a defect or technical
  intake with no analysis scope remains bounded by its approved source
  evidence. Require every in-scope identity to be assigned to at least one
  story or to one structured deferral containing an accountable role, reason
  and revisit trigger. A criterion may support multiple distinct story slices;
  reject duplicate entries within one record, covered/deferred overlap,
  unknown identities and uncovered identities.
- A feature story keeps explicit approved links to all four preparation
  packages: BA criteria, a Solution Design constraint, Design System usage and
  Experience Design. Existing feature intake must pass the same package
  compilers; only defect/technical intake may use the lighter evidence path.
- Require every test plan to make an explicit decision for the empty,
  boundary, invalid-input, authorization, duplicate/concurrent, failure and
  adjacent-regression coverage classes. A not-applicable decision requires a
  reason; a covered class requires a matching scenario.
- Reject untouched stub text and generic review verdicts. Exact-set review
  relations remain mandatory.
- Support scoped existing-project intake. Feature stories retain the complete
  upstream preparation contract; defect and technical stories use explicit
  issue, decision or constrained source evidence without weakening ownership,
  dependency, review or test-plan gates.
- Keep story state as planning state. Delivery completion never changes the
  approved story baseline.

### 0.3 Project refresh and handoff

- Expose one simple project refresh surface with inspect, apply and check
  behavior. A repeat invocation preserves the recorded project origin unless
  an explicit reclassification is requested through configure before any
  durable preparation document exists. Once preparation starts, origin is
  immutable and Git history remains the recovery mechanism.
- Reconcile package-managed config and Obsidian assertions, including missing
  or stale required property types, graph groups and package payload. Preserve
  authored Markdown, designation selections and user-owned Obsidian settings.
- Produce a complete mutation plan before writing. Mutating setup apply
  processes rebuild the authoritative plan under a shared guard and serialize
  against one another. Apply only those managed targets and recheck each target
  immediately before atomic replacement. On a failed closing gate restore only
  paths that still match the setup postimage; preserve and report observed
  concurrent edits. Non-setup editors pause writes to all setup-managed targets
  during apply.
- Make repeated refresh idempotent and test package N to N+1 convergence for
  both host projections.
- At a preparation-stage handoff, require the approved stage files and config
  to be tracked, committed and clean. Do not block ordinary editing while the
  current stage is still in progress. Remote push remains advisory because it
  cannot be proven offline.
- Route an existing project without an approved scoped backlog to backlog
  planning, never directly to delivery.
- Treat the vendored Obsidian community enhancement as package-projected local
  payload. Do not describe ignored local plugin files as committed project
  truth.

### 0.4 Supporting contract repairs

- Make the backlog reviewer truly read-only on both hosts. It returns
  structured findings; the Product Owner/orchestrator serially writes review
  records after all required readers finish.
- Protect governed `workspace/config.json` values across Bash pre/post hook
  checks so shell writes cannot bypass the declared writer contract.
- Make issue reports schema-valid, designation-aware, linked from the issue
  map and approval-gated before optional external filing. External filing still
  requires an explicit user request and uses the fixed marketplace target.
- Align QA coverage extraction with canonical qualified BA identities and
  backlog scenario IDs. Exact matching must not confuse short and long IDs.
- Remove scaffold language and build allowlists that could recreate retired
  run-state, resume-state or dashboard surfaces.

### 0.5 Phase 0 acceptance gates

Phase 0 is complete only when all of the following are true:

1. Focused positive and negative suites prove every changed compiler and hook
   contract, including Turkish designation creation, uncovered BA identities,
   invalid deferrals, missing coverage classes, placeholder reviews, stale
   payload, rollback, existing-project intake, Git handoff, issue approval and
   qualified QA IDs.
2. A greenfield end-to-end flow reaches an approved, committed backlog through
   the portable gate. An existing feature flow reaches an approved scoped
   backlog through all four upstream preparation packages; focused defect and
   technical intake tests prove the lighter approved-evidence contract.
3. Package N to N+1 refresh preserves authored Markdown, configured
   designations and user-owned Obsidian values, repairs managed drift, is
   idempotent and behaves equivalently for Claude and Codex.
4. Actual host install/update smoke succeeds for Claude and Codex. Hook parity
   is covered by host projections and focused invalid-write tests.
5. PMO, Control Tower, SQLite, global `.agentrof`, project-key, work-order,
   dashboard and retired challenge/audit/lock product residues are absent from
   active canonical and generated content. Intentional validator rules and
   negative test fixtures may name forbidden concepts. Passive names inside
   `retired_managed_properties` are the sole cleanup tombstones: setup consumes
   them only to delete old project fields and never stores their state.
6. Canonical validation, release validation, README counts, both generated
   distributions, independent test discovery, `git diff --check` and the full
   `make check` gate are green.

## Phase 1: proposed sprint delivery foundation

Phase 1 is deliberately unimplemented until its contract is reviewed with the
product owner. The current recommendation is:

```text
workspace/docs/delivery/
├── definition-of-done.md
├── sprints/
│   └── sprint-001/
│       ├── sprint.md
│       ├── items/<story-id>/
│       │   ├── item.md
│       │   ├── code-review.md
│       │   └── verification.md
│       ├── sprint-review.md
│       ├── retrospective.md
│       └── _generated/
└── _generated/
```

The proposed Phase 1 discussion must decide, with a separate impact analysis:

- exact sprint, sprint-item, review and verification document schemas;
- configurable delivery designations and fixed graph colors;
- Sprint Goal, selection, start approval, scope-change and close transitions;
- Definition of Done and release-readiness evidence;
- carry-over without rewriting a closed sprint;
- user acceptance, code-review and QA commit binding;
- sequential branch delivery and optional linked-worktree lanes;
- WIP limits, dependency waves, allowed-path overlap and serialized merges;
- sprint review, retrospective and non-gaming flow metrics;
- actual release as a separate explicit decision from Sprint Review.

Phase 1 must not reintroduce a database, central PMO, project keys, durable
runtime identities, work-order ledgers or Markdown lock tables.
