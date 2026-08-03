# Architecture Styles

The style vocabulary for structure verdicts. Every style verdict lands in the decision log with its forcing symptom named; a style adopted without a symptom is decoration and gets sent back at the gate.

## Modular Monolith (team default)

- Shape: one deployable backend, one frontend, modules with declared interfaces inside the codebase.
- Dependency rules: a module's internals are private; every cross-module call goes through the module's declared interface; no module reads or writes another module's tables or collections; an import cycle between modules is a finding.
- Earns its keep by default: one transaction scope, one deploy, boundaries that are cheap to redraw while they are still wrong. The boundary discipline is the point; it is what makes later extraction cheap.
- Exit test: extract a module only when a forcing symptom from the style table in SKILL.md is observed and recorded; never extract on prediction.

## Layered (inside every module)

- Shape: interface layer (transport parsing, response mapping), service layer (business rules), data access layer (storage calls).
- Dependency rule: arrows point downward only. The interface layer never reaches past the service layer into storage; the data access layer never calls upward.
- Decision test: IF a handler contains a business rule or a storage call, THEN it moves down a layer. A handler does exactly three things: parse the request, call one service operation, map the response.
- Failure symptom: a business-rule change forces edits in handler files, or a storage change forces edits in service files.

## Ports and Adapters

- Shape: the domain core defines interfaces (ports) for everything it needs; adapters implement them at the edge; concrete wiring happens only at the outermost composition point.
- Dependency rules: domain and use-case code import only domain types and ports, never adapters and never framework types. Both directions cross through ports: driving (transport in) and driven (storage and external calls out).
- Earns its keep when domain rules are rich enough that tests must run without infrastructure, or when a cited budget requires swapping an edge (storage engine, external provider) without touching the core.
- DON'T apply it to thin CRUD modules: a port plus adapter wrapping one trivial query is ceremony. Record the plain layered shape instead.

## Event-Driven

- Shape: modules communicate through events, immutable facts named in past tense; the producer does not know its consumers.
- Dependency rules: an event payload is a contract surface and is versioned like one. Delivery is at-least-once, so every consumer is idempotent by design. Ordering holds only per aggregate key, never globally; a design that assumes global order is wrong before it ships.
- Earns its keep: a workflow outlives a single request, one fact fans out to several reactions, or a seam needs independent deploy cadence.
- Costs to write into the decision record: eventual consistency with a declared staleness tolerance, and correlation ids as the only way to trace a flow across hops.

## CQRS (command-query separation)

- Shape: a write model shaped by invariants, a separate read model shaped by queries, projections keeping the read side current.
- Dependency rules: command handling validates against write-side state only, never against a projection; read models are disposable and rebuildable from write-side facts.
- When NOT to, which is the default answer: the write model serves reads fine in most systems. Separation adds a second model, a projection path, and a staleness contract. DON'T adopt it for symmetry, purity, or anticipated load.
- Earns its keep only against a recorded symptom: a quantified read budget or query shape the write model cannot serve within the cited budget.
- Every projection is a denormalized copy of mutable data: snapshot semantics apply, with a declared refresh path and staleness tolerance, per the constitution rule.

## Symptom-to-Cause Table

| Symptom observed in the delta or codebase | Likely cause | Structural fix |
|---|---|---|
| Use-case tests require a running database | Storage calls inside domain or service code | Put storage behind a port; inject an in-memory implementation in tests |
| Circular imports between modules | Depending on concrete classes instead of declared interfaces | Import the interface; wire the concrete class at the composition point |
| Persistence annotations on domain entities | Storage model fused to the domain model | Separate persistence model; map at the data access boundary |
| Every feature lands entirely in handler files | Missing service layer | Handler parses, calls one service operation, maps the response |
| A change in one module forces edits in another owner's files | Hidden coupling across the boundary | Declare the interface at the seam; route the dependency through it |
| Two modules subscribe to each other's events in a loop | A workflow split across a seam with no owning process | Give the workflow one owning module, or an explicit saga with an owner |
| Read endpoints join across several modules' data | Query shape fighting the ownership map | A declared projection with snapshot semantics, never cross-module table reads |

Each fix that changes a boundary or adds a projection is a decision log entry; the symptom column is the forcing symptom the entry cites.
