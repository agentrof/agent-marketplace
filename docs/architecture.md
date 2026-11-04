# Architecture

One standalone Software Engineering Team defines host-neutral delivery methods.
Claude Code and Codex supply packaging, invocation and permission adapters.

## Method and execution boundaries

The public entries form the user surface and load internal methods when needed.

| Entry | Responsibility |
| --- | --- |
| setup | Bootstrap and explicit project migration |
| configure | Project-owned configuration changes |
| deliver | Task-based delivery and readiness |
| autopilot | User-armed delegation and bounded background work |
| retrospective | Evidence-based improvement and timing analysis |
| issue-report | Anonymous upstream issue and local candidate repair |
| companions-uninstall | Removal of selected supported integrations |

Six internal methods supply conditional depth. `analyze`, `solution-design`,
`data-design`, `experience-design`, `verify` and `review` contain procedures and
linked references. Delivery loads the needed method explicitly on both hosts.
Native skill registration differs by host and does not establish application.

Five technical launch definitions are generated from the shared platform
registry. `light-worker`, `worker`, `reviewer`, `critical-worker` and
`critical-reviewer` bind compute and access. They carry bounded execution
instructions. Domain responsibilities remain in the methods. Native workers
are optional where they improve isolation, independence or useful parallelism.

## Invariants

1. Canonical behavior lives under `plugins/`. Host manifests, hooks, permission
   capabilities, catalogs and projections live under `platforms/`. Registered
   adapters own host-specific semantics.
2. Durable project truth lives in tracked project files and accepted external
   records. Project documents use `workspace/docs/`, with stable slugs,
   path-derived membership and titles in the configured output language.
   Machine keys and canonical package content use English.
3. `workspace/config.json` is a closed contract validated by `project_config.py`.
   Technical and datastore choices belong in accepted project decisions.
   Four compute profiles, `light`, `work`, `review` and `critical`, accept
   project `class_models` overrides. Access is a separate launch decision.
4. One owner controls each task and write allocation. Parallel writers use
   disjoint paths or isolated worktrees. Shared environment capacity and
   irreversible effects require explicit admission and authority.
5. Persisted meaning, data ownership, relationships, constraints, tenancy and
   migration compatibility receive consequence-based review. Task size never
   substitutes for data or security risk.
6. Context loading starts with the goal, critical boundaries and relevant
   sources. An existing graph can guide discovery. Source reads establish
   current facts. Applicable installed, enabled companions are mandatory.
   Missing capabilities and their core fallback evidence remain visible.
7. Method loading, worker-reported application and verified results remain
   separate evidence. Model requests and host-observed model settings remain
   separate too. A definition or prompt cannot prove host enforcement.
8. Proof and review bind to their actual candidate. A combined batch proof
   covers its integration tree. Publishing a prefix requires its own valid
   proof or a single atomic integration PR. A pending plan or queued merge
   cannot establish delivery readiness.
9. Compilers generate UTC timestamps. Typed timestamps and editable receipts
   cannot independently establish user authorization.
10. Runtime state under `.agentrof/agent-marketplace/.runtime/` and the queue
    under Git's common directory is ignored, local and reconstructible. Removing
    it can lose coordination and evidence, so reconcile external effects before
    restarting work. It never supplies new authority.
11. The queue coordinates linked worktrees on one machine. Leases, bounded
    attempts and reconciliation control duplicate work locally. A session
    controller dispatches workers. A bounded local retrospective observer consumes
    completion, failure and session events independently of delivery. It performs
    no model calls, tests or remote operations. Opaque Codex shell outcomes request
    one refresh per session and configured time window, collected when it closes.
    Explicit failure and lifecycle events remain immediate. Structured native
    completion records establish failures independently of command output text.
    Sustained delegated work across
    closed sessions requires an explicitly configured native scheduler or recovery
    mechanism. Later startup recovers interrupted local observations.
12. Hooks enforce only covered operations when enabled and loaded. Prompts
    express intent. The native worker launch guard validates bounded briefs and
   injects current rule sources before covered spawn calls. Its receipt records
   prepared source delivery, while semantic compliance remains unproven. Hook
   failures outside the launcher and external delegation paths require host
   controls. Native permissions and verified sandbox boundaries provide
    their documented controls. Missing enforcement stays visible.
13. Package builds are generated from canonical sources for every registered
    host. `dist/` is never hand-edited. New components use `tools/scaffold.py`.

## Validation and release integrity

Repository CI accounts for every selected test. Shared or unknown impact
selects full coverage. Reused validation binds trusted successful evidence to
an exact tree and the current coverage contract. Both host lifecycle checks
remain fresh. Missing reusable evidence runs the required tests.

Release-owned files change only in the final release commit. Its tree is the
deterministic bump of its parent at its commit date. Replay ignores ambient
Git attributes, excludes, replacement refs and graph overlays. Snapshot records
are prefix-free. Generated text uses LF, binary and unknown payloads remain
byte-exact, and `package-modes.json` defines portable executable modes.

Schema-v4 provenance binds the closed package inventory, file hashes and
executable set, with one hash line per file. The release records the canonical
snapshot `build_id`. Verification recomputes it. Public stable installs,
exact-lease ref transactions, immutable release reconciliation and clean-ref
completion remain required. A release tags an approved `main` commit whose
push validation succeeded and reuses that validation.

Maintainer issue operations start only from explicit user selection. Issue
activity cannot start them. Merge and release authority follow the separate
maintainer protocol. Consumer identities, private data and their source links
never enter public package artifacts or history. [Maintainer operations](maintainer-operations-protocol.md)

## Ownership

- `plugins/software-engineering-team/` owns skills, conditional references,
  delivery flows, scripts and templates.
- `platforms/shared/software-engineering-team/launch-profiles.json` owns the
  shared technical launch registry. Each host renders its supported controls.
- `tools/` owns scaffolding, building, validation and release contracts.
- Consuming repositories own project decisions, tasks, environment contracts
  and evidence. Local projections remain generated integration files.
