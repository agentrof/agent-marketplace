# Design Review Checklist

Design-time review of a relational schema deliverable. Severity: Critical blocks approval outright; Major must be fixed before the QA gate; Minor is recorded.

## Field Specification Completeness

- [ ] Every field has all columns filled: type, required/optional, default, indexed, unique, description. No empty cells.
- [ ] No placeholder types ("TBD", "string", "any"); types are specific to the declared engine.
- [ ] Currency and financial fields use an exact decimal type, never float or double. (Major)
- [ ] Timestamp fields use timezone-aware types, stored in UTC. (Major)
- [ ] No banned types: naive timestamp, `char(n)`/`varchar(n)`, `money`, `serial`, integer-as-boolean.
- [ ] Enum-like fields either list allowed values in a CHECK constraint or reference a lookup table; evolving sets do not use native enums.
- [ ] NULL is allowed only with explicit rationale; NOT NULL is the default stance.

## Denormalization and Snapshots

- [ ] A denormalized copy of a MUTABLE source field with no declared refresh mechanism is a MAJOR finding. A copy that silently drifts from its source is a correctness bug, not a style nit.
- [ ] Every denormalized copy also declares its staleness tolerance and is recorded as an ADR (context, decision, alternatives, consequences). A mutable-field copy with no ADR reference is a MAJOR finding.
- [ ] Deliberate point-in-time snapshots (price at purchase) are named so the frozen semantic is explicit and are documented as never-refreshed.
- [ ] Materialized views or summary tables were considered before denormalizing a base table.

## Index Strategy

- [ ] Every query pattern listed in the design has a supporting index; no pattern relies on a full-table scan.
- [ ] Compound index column order follows ESR (equality, sort, range).
- [ ] No redundant indexes: no index whose column list is a prefix of another's.
- [ ] Every foreign-key column has an explicit index; the engine does not create one. A missing FK index is a MAJOR finding.
- [ ] Every index has a rationale tied to a specific query pattern or story; no speculative indexes.
- [ ] Partial indexes exist for filtered hot subsets (soft-delete live rows, active status).
- [ ] High-write tables carry a justified, minimal index set.
- [ ] Upsert conflict targets have exact-matching unique indexes.

## Keys, Constraints, Relationship Integrity

- [ ] Every table has a primary key (or a documented ADR for keyless append-only event tables).
- [ ] Every relationship has explicit cardinality, direction, and an FK action: RESTRICT by default, CASCADE only for true composition, SET NULL only for optional associations.
- [ ] No unintended cascade chains (trace every CASCADE to its leaves).
- [ ] Business invariants are enforced by constraints (CHECK, UNIQUE, EXCLUDE), not only in application code.
- [ ] CHECK constraints on nullable columns are paired with NOT NULL where the rule must always hold.
- [ ] Unique constraints exist wherever business rules require uniqueness, scoped (tenant, parent) where applicable.
- [ ] Polymorphic links without FKs are flagged and carry a documented application-level integrity strategy.

## Common Field Patterns

- [ ] `created_at`, `updated_at`, `created_by`, `updated_by` on every mutable entity, with the update mechanism named.
- [ ] Soft-delete strategy documented where applicable, with partial indexes for live rows and defined child behavior.
- [ ] Common fields documented as a group with a code-level implementation recommendation (base model, mixin, middleware).

## Schema Evolution Safety

- [ ] Migrations touching large live tables use safe DDL: concurrent index builds, NOT VALID then VALIDATE for new constraints, batched backfills.
- [ ] Column type changes and renames use expand-contract, not in-place ALTER.
- [ ] New NOT NULL columns with volatile defaults are staged (add nullable, backfill, then constrain).

## Decision Traceability

- [ ] Every significant decision has an ADR-format entry: status, context, decision, alternatives considered, consequences.
- [ ] Context explains WHY, at least one alternative was weighed, consequences include trade-offs.
- [ ] No hand-waving rationale ("for performance" without a named query or measurement).
- [ ] Normalization deviations, index type selections, and non-obvious type choices are each covered.
