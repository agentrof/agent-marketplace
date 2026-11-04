---
name: solution-design
description: Chooses component boundaries, interfaces and implementation approach when a task changes architecture, technology or cross-component behavior.
exposure: internal
---

# Solution Design

## Outcome

An implementable design delta with clear ownership, compatible interfaces and
proportionate operational cost.

## When to use

Load for changed boundaries, interfaces, technologies, dependencies or runtime
behavior. For an established local pattern, confirm its fit and implement it.

## Inputs and authority

Use the task, accepted Solution decisions, affected code and operation documents.
The solution landscape under `workspace/docs/solution-design/` owns component
topology and technology. Detailed contracts and module design live under
`workspace/docs/system-architecture/`. Preserve existing paths and identities.
Changes to accepted product direction require its decision authority.

## Method

1. Trace the requested behavior through current components, interfaces and
   stores. Identify the exact gap and reuse existing capabilities.
2. Compare the simplest sufficient design with the strongest credible
   alternative when the choice is material. Evaluate build, buy or integrate
   for a new capability. Consider operability, failure
   recovery, team capability, cost, lock-in and exit. Record substantial
   decisions once, with constraints, tradeoffs and affected scopes.
3. Give each mutable resource and module one owner. Define boundary inputs,
   outputs, authorization, errors, retries, timeouts and compatibility. Load
   data-design before changing persisted structure or consistency semantics.
4. Map implementation into independently verifiable tasks. Give concurrent
   writers disjoint ownership and explicit integration seams. Sequence shared
   state changes and account for generated outputs and migrations.
5. Declare runtime impact, including services, configuration, secrets, data,
   readiness, observability and rollout or recovery. Measure the uncertain
   constraint that could invalidate the design. Escalate a conflicting accepted
   decision with concrete evidence and a proposed resolution.

## Evidence and completion

Return changed decisions/contracts, ownership and integration order, testable
failure behavior, and environment impact or a reasoned absence of impact.
Update only documents the change makes stale. A local correction within an
accepted design can remain in the task without a new architecture record.

## Conditional references

- For boundary, API or cross-cutting changes, read [architecture](references/architecture.md).
- For implementation planning or stack-specific pitfalls, read [implementation](references/implementation.md).
- For schemas and migrations, load [data-design](../data-design/SKILL.md).
- For user interaction changes, load [experience-design](../experience-design/SKILL.md).
