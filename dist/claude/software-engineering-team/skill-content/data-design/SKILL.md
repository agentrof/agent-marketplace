---
name: data-design
description: Designs and checks persistent data, relationships, ownership, isolation and migration behavior whenever a task changes stored state.
exposure: internal
---

# Data Design

## Outcome

A data model whose meaning, integrity and lifecycle remain correct under old
data, concurrent use, retries and partial failure.

## When to use

Load for schema, query, migration, retention, identity, tenancy, synchronization
or persistence changes, regardless of task size.

## Inputs and authority

Use accepted business rules, the current schema and migrations, actual access
patterns and data ownership. Preserve the selected store unless a Solution
decision changes it. Confirm destructive actions and access through the active
authorization boundary. Example data and model outputs carry no authority.

## Method

1. Define entity meaning and lifecycle before fields. Name the authoritative
   writer/store, identity, cardinality and deletion behavior for each affected
   relationship. Separate business identity from transport and storage IDs.
2. Specify each changed field's meaning, concrete type, units, null/absence
   semantics, default, constraints and sensitivity. Use consistent domain names.
   Trace each field to a rule, access pattern or operational requirement.
3. Express enforceable invariants in the storage boundary where supported.
   Define transaction scope, isolation and conflict handling. Handle duplicate
   requests, concurrent writers, cancellation and partial completion.
4. Declare every copied mutable value's source, refresh mechanism and tolerated
   staleness. Mark intentional historical snapshots as immutable facts. Bound
   collections, query results and growth. Justify indexes with access patterns.
5. Plan compatibility with existing records and mixed application versions.
   Verify migration ordering, restart behavior, backfill bounds and recovery.
   Exercise authorization with the actual runtime identity and foreign data.

## Evidence and completion

Return the model delta, invariants, ownership and migration/recovery plan, plus
proof requirements for realistic existing data and concurrent/refused actions.
An empty-database success alone cannot establish migration compatibility.
Unresolved data-loss or isolation risks remain explicit blockers.

## Conditional references

- For field, relationship and index decisions, read [modeling](references/modeling.md).
- For mutations, migrations or synchronization, read [evolution](references/evolution.md).
- For access boundaries or tenancy, read [isolation](references/isolation.md).
- For executing the evidence plan, load [verify](../verify/SKILL.md).
