# Advanced Patterns

## Materialized Views

- Precomputed query results stored as a table; the sanctioned alternative to denormalizing base tables.
- Refresh concurrently where the engine supports it so readers are never blocked; the view then needs a unique index.
- Declare the refresh cadence and trigger (schedule, post-batch hook) in the design. An undeclared cadence is the same drift bug as an undeclared snapshot.
- Index materialized views like real tables; they serve dashboards, reporting, and expensive aggregations.

## Generated Columns

- `GENERATED ALWAYS AS (expression) STORED` for derived, indexable values: extracted JSON scalars, normalized search keys (`lower(email)`), computed totals.
- Stored generated columns can carry B-tree indexes; virtual ones (where supported) save storage but cannot always be indexed.
- A generated column is the safe denormalization: the engine owns the refresh, so no snapshot ADR is needed.

## Polymorphic Entities

- Single-table: one table, a `type` discriminator column, nullable type-specific columns, CHECK constraints per type. Best when subtypes share most fields.
- Class-table: shared columns in a parent table, type-specific columns in child tables sharing the parent PK. Best when subtypes diverge heavily.
- AVOID table inheritance (the `INHERITS` mechanism). Constraints and indexes do not propagate reliably, FKs to the parent miss child rows, and every engine document steers away from it. Use one of the two patterns above, or list partitioning by type when subtypes are queried separately.
- AVOID a generic `owner_type`/`owner_id` pair without FKs; it discards referential integrity. If used anyway, record the ADR and the application-level integrity strategy.

## Temporal / History Tracking

- Standard-SQL system versioning where the engine offers it; otherwise the trigger-plus-history-table pattern: an AFTER trigger copies the old row, with valid-from and valid-to timestamps, into `{table}_history`.
- History tables are append-only: no updates, minimal indexes (entity id, valid range), consider block-range indexing and range partitioning for retention.
- For "what was true at time T" queries, index `(entity_id, valid_from)` and query with a range predicate; exclusion constraints can guarantee non-overlapping validity periods.

## Audit Patterns

- Every mutable entity carries `created_at`, `updated_at`, `created_by`, `updated_by`. Deliver this as a documented common-field group so the application layer implements it once (base model or middleware), with `updated_at` maintained by a trigger or the ORM, chosen explicitly.
- Row-level audit log: triggers on INSERT/UPDATE/DELETE writing operation type, old and new values (as a JSON document), actor, and timestamp to an append-only audit table. Statement-level audit extensions exist where regulation demands full coverage; that is an ops decision to flag, not design.
- Soft delete: `deleted_at` timestamp (null means live), partial indexes filtered to live rows, and a documented answer for what happens to children of a soft-deleted parent.

## Row-Level Security

- Where the engine supports policies, multi-tenant isolation can live in the database: enable row security on the table and write policies keyed on the tenant or user context.
- Design impact: every table under a policy needs the tenant key column, indexed, usually leading compound indexes.
