---
name: software-architecture
description: Software architecture knowledge for the team's architect role. Loaded by software-engineering-team flows; not user-facing.
exposure: internal
---

# Software Architecture

Decision rules for Delivery-owned System Architecture: component/module/facet
records, boundaries, contract conventions, budgets, and cross-cutting choices,
one active Delivery Item delta at a time.

## When to Use

Load only for an active Delivery Item whose `architecture_impact` is required.
Materialize `workspace/docs/system-architecture/` adaptively with
`architecture_compile.py`: root, exact Solution component hubs, recursive
modules and only the needed interface/data/security/runtime/reliability/
observability facets. Upstream accepted Solution component decisions bind every
delta; a contradiction escalates instead of deviating. Do NOT load for store
schema detail; the sql-database-design and nosql-database-design siblings own
that. Do NOT load for implementation; realization lives in component-selected
method skills.
Read `obsidian-vault`; its taxonomy and ledger rules govern docs mutations.

Each root, component hub and leaf record carries Delivery Item provenance,
stable identity and revision. Existing records change only through
`begin-revision --item-ref`; ledger snapshots are durable and generated
registries are disposable. Decisions and standards declare `affected_scopes`;
the compiler derives their lowest common ancestor path. Cross-component
connections live at the architecture root.

Vault traceability is typed and cross-subtree. Living docs and ADRs use
`derives_from`, `implements`, `satisfies` and `constrained_by` to cite the
owning Solution decision, qualified BA budget/criterion and exact Experience
journey, screen or transition. API contracts connect the acceptance criterion
and Experience transition they realize; threat models may connect actors,
journeys, screens and solution boundaries. Story identity remains a raw
`story` field in the project-local Markdown backlog; vault knowledge references
are aliased wikilinks. Run the
relation renderer after each architecture delta.

## Topology Application

- Solution Design is the sole owner of the project topology: app count,
  component boundaries, sourcing, names and selected technologies. This skill
  never selects a default topology or changes it.
- Apply the accepted component catalog exactly. If a Delivery Item exposes a
  missing boundary or a topology contradiction, stop and return it to Solution
  Design with the concrete forcing evidence.
- Architectural styles are guard rails inside an accepted built component,
  never a menu for changing component topology. Do not introduce a service,
  backend or frontend app without an approved Solution revision.

Use service decomposition only for an already-owned independent deploy seam;
use CQRS for a quantified read-model limit; use event handoff for durable
restartable workflows; and use ports/adapters when domain rules leak imports.

## Boundary Method

- Bounded contexts drive module boundaries: one model and one vocabulary per context. A term that changes meaning across a seam marks a boundary.
- Dependency arrows point inward: domain code carries no transport or storage imports, and every module crossing goes through a declared interface. An import of another module's internals is a boundary violation finding.
- Each boundary yields exactly one owner in the ownership map: module, owner, and the interface contract named at every seam. No overlaps, no orphan modules.

## Contract Conventions

Altitude boundary: a sentence belongs in this section only if it stays true when the backend stack changes. WHAT the contract declares lives here; HOW it is realized lives only in the bound stack skill.

- DO name resources as plural nouns, one convention across the contract.
- DON'T put verbs in paths; a non-CRUD action becomes a subordinate resource or a state transition.
- DO keep nesting shallow, one parent deep; promote anything deeper to a top-level resource with a filter.
- DO make PUT and DELETE idempotent by construction; repeating the call converges on the same state.
- DO declare an idempotency key for every unsafe operation a client may retry after a timeout.
- DO declare pagination on every collection endpoint at contract time; retrofitting it is a breaking change.
- DO declare exactly one error envelope and use it for every error response of every endpoint.
- DO assign status codes from the single taxonomy in the rulebook; 401 means unauthenticated, 403 unauthorized, 409 state conflict, 422 validation failure.
- DO record an explicit versioning stance in the contract, even when it is "no version segment, additive changes only".
- DON'T mirror the storage model in the contract; the two evolve for different consumers.

## Non-Functional Budgets

- Refuse to guess: IF a structural decision depends on a load, volume, availability, or authorization budget the brief does not quantify, THEN escalate with the exact number needed and bracketed options. It is an escalation, never an assumption; an assumed budget written into the design is a violation, not initiative.
- Every accepted budget is cited by name in the decision that satisfies it; a decision claiming a performance or scale rationale without a cited budget is incomplete.

## Cross-Cutting Defaults

- Name the authorization model in every delta: role-based unless an attribute condition (ownership, tenancy, time window) is itself a business rule; record the model and where the check lives in each endpoint's authorization field.
- Caching only with a declared invalidation path. A cache whose invalidation trigger is unnamed is a finding, not an optimization.
- A transaction inside one store stays a transaction. IF a write must span stores or modules, THEN read the cross-cutting reference before designing it; never hand-build coordination where the store already guarantees atomicity.
- Every new seam declares its observability obligations in the contract: correlation-id propagation and the golden signals it emits. A seam without them is invisible in production by design.

## Decision Records

| Write a record | Change note only |
|---|---|
| Style, boundary, or ownership change | Rename with no contract change |
| Contract versioning stance or any breaking change | Additive endpoint following recorded conventions |
| Cross-cutting choice: authorization model, cache, outbox or saga | Implementation detail inside one owner's files |
| Any denormalized copy of a mutable field | Bug fix restoring declared behavior |
| Accepted non-functional budget | Configuration value change |

## References

- [architecture-styles](references/architecture-styles.md): dependency rules per style, what earns each style, symptom-to-cause table. Read when a delta proposes or questions a structural style.
- [service-boundaries](references/service-boundaries.md): bounded contexts, context mapping, decomposition order, ownership rows. Read when drawing or disputing a module boundary or filling the ownership map.
- [api-design](references/api-design.md): the full contract rulebook behind the conventions above. Read when declaring or reviewing any endpoint.
- [nfr-budgets](references/nfr-budgets.md): budget categories, budget-to-decision map, escalation script, traceability. Read when a structural decision depends on load, volume, availability, or security numbers.
- [cross-cutting](references/cross-cutting.md): authorization placement, outbox and saga, caching, resilience and observability obligations. Read when a delta spans modules or touches any cross-cutting default.
- [design-qualities](references/design-qualities.md): coupling vocabulary and structural decision tests shared with the review role. Read when judging a proposed structure or writing a structural finding.
- [decision-records](references/decision-records.md): write-or-skip detail, record formats, supersede mechanics, anti-patterns. Read when writing, superseding, or auditing a decision note.
