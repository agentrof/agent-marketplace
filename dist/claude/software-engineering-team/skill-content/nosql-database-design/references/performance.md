# Performance Patterns

## Covered Queries (Index-Only Reads)

- A query is covered when every returned field is in the index; the engine never fetches the full document.
- Requires an explicit projection: `find({ email: "x" }, { email: 1, name: 1, _id: 0 })` with an index on `{ email: 1, name: 1 }`.
- Verify with the query plan: total documents examined equal to zero confirms coverage.

## Query Plan Analysis

- Run the explain facility with execution statistics on every query pattern in the design.
- Key signals: the ratio of documents examined to documents returned (should approach one), execution time, and which index was chosen.
- Index scan is the goal; a full collection scan on a production query pattern is a FAIL.
- An in-memory sort stage means the sort field is missing from the index; fix the index order (ESR), not the query.

## Index Intersection

- The planner can combine two single-field indexes for one query.
- It is a fallback, not a plan: a compound index beats intersection for any known multi-field query. Reserve intersection for genuinely ad-hoc query surfaces.

## Connection Pooling

- Size the pool from concurrent operations per application instance, not from guesswork; monitor the server's connection counters.
- Tune via connection string options: max pool size, min pool size, max idle time.
- Serverless/function runtimes: one connection per instance, or a pooling proxy; a cold-started fleet can exhaust the server's connection budget.

## Storage Engine Cache

- The default cache is roughly half of available memory; the working set should fit inside it.
- Monitor cache statistics; a working set larger than cache turns every read into disk latency.
- Watch the dirty-pages ratio on write-heavy workloads; sustained high dirty ratios mean the disk cannot keep pace.

## Workload-Shaped Design

- Read-heavy: embed data, add compound indexes, consider secondary reads, aim for covered queries.
- Write-heavy: reference instead of embed (avoid rewriting large documents), use bulk writes, keep the index count minimal.
- Mixed: subset pattern (embed hot data, reference cold data), computed pattern for pre-aggregation.
- Update-heavy documents that grow past their allocated size force document moves; keep frequently-updated fields in small documents and avoid arrays that grow on every write.
