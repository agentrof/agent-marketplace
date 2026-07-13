# Non-Functional Budgets

A budget is a quantified constraint the design must satisfy. Unquantified adjectives ("fast", "scalable", "highly available") are not budgets and cannot justify structure. Budgets enter the decision log as accepted facts; decisions cite them; reviews check the chain.

## Budget Categories

| Category | Quantified when it names | Example of an accepted budget |
|---|---|---|
| Latency | Percentile, operation, load context | Search responds under 300 ms at p95 during expected peak traffic |
| Volume | Current size, growth rate, horizon | Orders grow by roughly 50k rows a month over the next two years |
| Availability | Target, scope, measurement clock | Checkout reachable 99.9 percent measured monthly; back office excluded |
| Concurrency | Peak simultaneous actors on the contended entity | Up to 40 concurrent editors on the same board at peak |
| Security posture | Data classification and audience | Health fields visible only to the treating role; every access logged |
| Operability | Recovery time and data-loss tolerance | Restore service within one hour; lose at most five minutes of writes |

## From Budget to Structure

A budget earns its place by changing a decision. The recurring mappings:

| Accepted budget | Structural decision it drives |
|---|---|
| Read latency beyond what the write-shaped model serves | Declared read model or cache with an invalidation path (cross-cutting reference) |
| Volume growth beyond single-node comfort | Store selection and partitioning stay with the database design skills; the architect records the budget, the store design satisfies it, both cite it |
| Availability above best-effort | Degradation paths, timeout and retry obligations at each seam, redundancy stance |
| High concurrency on one entity | Aggregate sizing and locking stance; possibly event-driven serialization at that seam |
| Strict security posture | Attribute-based authorization and audit logging as contract obligations |
| Tight recovery targets | Backup cadence, rebuildable projections, restore rehearsal as an operability requirement |

- Direction matters: the budget drives the decision, never the reverse. Writing a budget after choosing the structure is the retroactive rubber stamp from the decision-records anti-pattern table.

## Refuse to Guess: the Escalation Script

IF a structural decision depends on a budget the brief does not quantify, THEN:

1. Halt that decision only; the rest of the delta proceeds.
2. Name the blocked decision and the exact quantity needed: "cursor vs offset pagination for the activity feed needs expected collection growth".
3. Offer bracketed options with consequences: "bounded in the tens of thousands of rows, offset is fine; unbounded growth forces opaque cursors, a contract-visible choice".
4. Record the answer as an accepted budget in the decision log before designing against it.

- DON'T write an assumed number into the design. At review time an assumed budget is indistinguishable from a validated one, and the review will then enforce a guess.
- DON'T pad the escalation with every conceivable metric; ask for the one number the blocked decision needs, with the options it selects between.
- Self-check before escalating: would any plausible answer change the design? IF every answer yields the same structure, THEN no budget is needed and the decision proceeds without one.

## Traceability

- Every accepted budget is cited by name in each decision that satisfies it: "cursor pagination chosen against the accepted feed-growth budget (BR-014)".
- The chain is checked twice: at the design gate (does every performance, scale, or availability rationale cite an accepted budget?) and at review conformance (does the implementation match the decision that cites it?).
- A decision carrying a scale rationale with no cited budget is returned as incomplete, the same class of defect as an endpoint without error cases.
- A budget cited by no decision is a flag in the other direction: either a decision is missing or the budget was never real; raise it in the delta summary rather than letting it rot.
- The chain closes at verification: each budget appears in the QA record's budget table as VERIFIED (a perf-smoke or seeded-volume assertion measured it) or UNVERIFIED with the reason; a budget nobody measures and nobody marks unverified is decoration.
