# Performance Patterns

Design-time performance thinking. Production tuning and ops belong to other roles; the schema designer's job is to not build the bottleneck in.

## Reading Query Plans

- `EXPLAIN ANALYZE` gives actual execution metrics, not estimates. Read plans for the schema's dominant queries before declaring the index strategy done.
- Sequential scan on a large table filtered by a selective predicate: missing index.
- Sort node feeding a `LIMIT` or `ORDER BY`: an index matching the sort order removes it.
- Nested-loop join on two large inputs: usually a missing join-key index; hash or merge joins want their inputs indexed or pre-sorted.
- Rows-estimated vs rows-actual off by orders of magnitude: stale statistics or a correlation the planner cannot see; consider extended statistics on the correlated columns.
- The engine's statement-statistics facility identifies the queries worth planning against; design for the top patterns, not hypotheticals.

## Selectivity

- High selectivity (many distinct values: email, user_id) makes a good standalone index.
- Low selectivity (status, boolean flags) makes a poor standalone index; the planner will often ignore it.
- Fix low selectivity by combining: lead a compound index with the high-selectivity column, or use a partial index on the hot low-selectivity value (`WHERE status = 'active'`).

## Update-Heavy Tables

- Separate hot and cold columns: frequently updated columns in their own narrow table minimizes row-version churn on the wide, stable data.
- Leave free space per page (fill-factor style settings) so in-place row updates avoid index maintenance.
- Avoid updating indexed columns; that defeats the in-place optimization entirely.
- Multi-version engines leave dead row versions on update and delete; vacuum-style maintenance handles them, but the design should avoid hot, wide, frequently rewritten rows.

## Insert-Heavy Tables

- Minimize indexes; every index is paid on every insert.
- Bulk ingestion uses the engine's bulk-copy path or multi-row inserts, never row-at-a-time.
- Consider a natural key such as `(timestamp, device_id)`; many append-only event tables need no surrogate key at all. If one is needed, a bigint identity beats a random UUID.
- Partition by time or hash to spread write load and make retention cheap.
- Rebuildable staging data can use unlogged tables (fast, not crash-safe); a deliberate, documented trade.

## Upsert-Friendly Design

- The conflict target needs an exact-matching UNIQUE index; partial indexes do not qualify.
- Update only columns that actually changed (reference the excluded row's values); `DO NOTHING` is cheaper than `DO UPDATE` when no change is needed.

## Backfill Batching

Schema migrations that rewrite data run in batches: update a bounded set (order by PK, take N thousand), commit, pause briefly, repeat until zero rows touched. One giant UPDATE holds locks and bloats the table.

## Connection and Read Scaling

- Connection pooling is a required capability between application and database; pool size derives from core count, not from user count. This is deployment's concern; the design's concern is that transactions stay short so pooling works.
- Read replicas offload read-heavy patterns but lag the primary; any query routed to a replica must tolerate the documented staleness. Same discipline as denormalization snapshots: declared tolerance or it is a bug.
