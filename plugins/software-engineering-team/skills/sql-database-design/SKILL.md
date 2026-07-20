---
name: sql-database-design
description: Relational database schema design expertise, loaded by software-engineering-team agents for database work. Practical normalization, key and null rules, index architecture, constraint engineering, and zero-downtime schema evolution. Loaded by the database role agent when the project stack is relational.
user-invocable: false
---

# SQL Database Design

Sibling: [nosql-database-design](../nosql-database-design/SKILL.md). Choosing the store is an upstream decision: join-heavy and integrity-critical access patterns mean relational; document-local, read-shaped patterns mean a document store.

## When to Use

Load when designing or reviewing a relational schema: entities, fields, keys, constraints, indexes, partitioning, or migrations. This skill covers schema DESIGN, not query tuning or database administration.

## Normalization in Practice

- Normalize to 3NF by default. Denormalize ONLY for a measured, high-ROI read path, and record it as an ADR (context / decision / alternatives / consequences). Every denormalized copy of a mutable source field is a SNAPSHOT: it MUST declare its refresh mechanism (DB trigger, application-side write, or batch job) and its staleness tolerance.
- Prefer materialized views or summary tables over denormalizing base tables.
- Theory and worked decompositions: the normalization reference below.

## Keys and Nulls

- Every table gets a PRIMARY KEY. Prefer a bigint identity column (`GENERATED ALWAYS AS IDENTITY`); use UUID only when global uniqueness or opacity is needed, and prefer time-ordered UUIDs for index locality.
- Natural keys only when stable, unique, and immutable (ISO country codes). Composite PKs for junction tables.
- NOT NULL is the default stance. NULL means "unknown", never "empty" or "zero". Allow NULL only with written justification.

## Never Use These Types

- DO NOT use `timestamp` without time zone; DO use the timezone-aware timestamp type, stored in UTC.
- DO NOT use `char(n)` or `varchar(n)`; DO use `text` plus a CHECK length constraint when a limit is a business rule.
- DO NOT use the `money` type or any float for currency; DO use exact `numeric(p,s)`.
- DO NOT use `serial`; DO use `GENERATED ALWAYS AS IDENTITY`.
- DO NOT use integers as booleans; DO use `boolean NOT NULL`.
- Full type decision tables: the data-types reference below.

## Gotchas

- Foreign-key columns are NOT auto-indexed. Add the index manually; this is the top real-world missing-index cause.
- UNIQUE allows multiple NULLs by default. Use a nulls-not-distinct unique constraint to allow only one.
- Sequences and identity columns have gaps (rollbacks, crashes, concurrency). Normal; never "fix" them.
- Adding a column with a volatile default (`now()`, random UUID) rewrites the whole table; non-volatile defaults are fast.
- Concurrent index builds cannot run inside a transaction; plain index builds block writes.
- No silent coercions: precision overflow errors out instead of truncating.

## Indexing Rules

- Index the access paths you actually query: FK columns, frequent filters, sort keys, join keys. Nothing speculative.
- Compound index column order follows ESR: Equality columns first, Sort columns second, Range columns last.
- Leftmost-prefix rule: an index on `(a, b, c)` serves `(a)` and `(a, b)` but never `(b)` alone. Drop any index that is a prefix of another.
- Partial indexes for hot subsets (`WHERE deleted_at IS NULL`); expression indexes for computed lookups (`lower(email)`).
- Index type catalog and semi-structured column indexing: the indexing reference below.

## Constraint Essentials

- Foreign keys: default `ON DELETE RESTRICT`. `CASCADE` only for true composition (order to order_items). `SET NULL` for optional associations.
- CHECK for value ranges and cross-column rules; NULL passes CHECK, so pair with NOT NULL.
- UNIQUE for business identity, composite UNIQUE for scoped uniqueness (`tenant_id, slug`).
- Exclusion constraints prevent overlapping ranges (no double-booking).
- Enforce invariants in the database, not only in application code.

## Zero-Downtime DDL

| Operation | Safe? | Strategy |
|-----------|-------|----------|
| ADD COLUMN nullable, no default | Yes | Instant metadata change |
| ADD COLUMN with non-volatile default | Yes | Stored in catalog, no rewrite |
| ADD COLUMN with volatile default | No | Add nullable, backfill in batches, then set default |
| ADD INDEX | Yes | Build concurrently (outside a transaction) |
| ADD FK / CHECK to a large live table | Yes | `ADD CONSTRAINT ... NOT VALID`, then `VALIDATE CONSTRAINT` (short lock, then background scan) |
| DROP COLUMN | Careful | Stop all reads/writes in code first, then drop |
| ALTER COLUMN TYPE | Dangerous | Expand-contract |
| RENAME COLUMN / DROP TABLE | Dangerous | Expand-contract; confirm zero references first |

Expand-contract: (1) Expand: add the new column alongside the old. (2) Migrate: backfill in batches. (3) Transition: switch application reads/writes. (4) Contract: drop the old column after a confirmation period. Details: the partitioning reference for partition DDL, the performance reference for batch sizing.

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Tables | plural, snake_case | `users`, `order_items` |
| Columns | singular, snake_case | `first_name`, `created_at` |
| Primary key | `id` or `{table_singular}_id` | `user_id` |
| FK column | `{referenced_table_singular}_id` | `team_id` |
| FK constraint | `fk_{table}_{ref_table}_{column}` | `fk_orders_users_user_id` |
| Unique constraint | `uq_{table}_{columns}` | `uq_users_email` |
| Check constraint | `ck_{table}_{description}` | `ck_orders_positive_total` |
| Index | `idx_{table}_{columns}` | `idx_orders_created_at_status` |

## References

- [normalization](references/normalization.md): normal forms theory and decomposition practice. Read when normalizing a design or justifying a denormalization ADR.
- [data-types](references/data-types.md): type decision tables. Read when a field's type is not settled by the never-use rules above.
- [indexing](references/indexing.md): index type catalog, ESR, semi-structured columns. Read when an access path needs more than a plain B-tree.
- [partitioning](references/partitioning.md): range, list, hash partitioning. Read when table size or a pruning pattern puts partitioning on the table.
- [advanced-patterns](references/advanced-patterns.md): materialized views, generated columns, temporal and audit patterns. Read when a read path wants precomputation or history tracking.
- [performance](references/performance.md): query plans, selectivity, write-heavy design, batch sizing. Read when validating the index strategy or sizing a backfill.
- [examples](references/examples.md): worked entity examples. Read when starting a schema and wanting a reference shape.
- [review-checklist](references/review-checklist.md): design-time review checks. Read when reviewing a relational schema deliverable.
- [qa-checklist](references/qa-checklist.md): QA gate checks with PASS/FAIL criteria. Read when running the QA gate on a schema deliverable.
