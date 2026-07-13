# Index Architecture

Every index must trace to a named query pattern. An index with no query is pure write tax.

## Index Type Catalog

| Type | Serves | Use for | Notes |
|------|--------|---------|-------|
| B-tree (default) | `=`, `<`, `>`, `BETWEEN`, prefix `LIKE 'abc%'`, `ORDER BY` | Almost everything; PK and UNIQUE use it implicitly | Best on high-selectivity columns |
| Hash | `=` only | Exact-match lookups on high-cardinality columns | Narrow win over B-tree; skip unless measured |
| Inverted (GIN-style) | Containment on composite values: arrays, semi-structured documents, full-text | JSON columns, tag arrays, search vectors | Slower writes, fast reads |
| Generalized search tree (GiST-style) | Overlap, containment, nearest-neighbor on ranges and geometry | Range columns, exclusion constraints, spatial data | Required backing for exclusion constraints |
| Block-range (BRIN-style) | Range scans on physically ordered data | Append-only time-series and log tables | Tiny footprint; useless if physical order does not correlate |
| Covering (INCLUDE) | Index-only scans | Hot queries reading a few extra columns | Payload columns ride along without being key columns |
| Partial | Filtered subsets | Soft-delete (`WHERE deleted_at IS NULL`), hot-status rows | Smaller and faster than indexing the whole table |
| Expression | Computed lookups | `lower(email)`, date truncation | Query must repeat the exact expression |

## Compound Index Design

1. Equality columns first (used with `=`).
2. Sort columns second (used in `ORDER BY`).
3. Range columns last (`<`, `>`, `BETWEEN`).

That is the ESR rule. Then apply the leftmost-prefix principle: an index on `(a, b, c)` serves predicates on `(a)`, `(a, b)`, `(a, b, c)`, but never `(b)` or `(c)` alone.

- Redundancy check: if index A's column list is a prefix of index B's, drop A.
- Put the most selective equality column first among equals.
- Combine a low-selectivity column (status) with a high-selectivity one (user_id) rather than indexing the low-selectivity column alone.

## Mandatory Indexes

- Every foreign-key column. The engine does not create these; missing FK indexes cause slow joins and lock escalation on parent deletes.
- Every column pair behind a scoped-uniqueness business rule (as a UNIQUE index).
- The conflict target of any upsert: `ON CONFLICT (cols)` needs an exact-matching unique index; partial unique indexes do not qualify.

## Semi-Structured Column Indexing

For a binary JSON column holding optional attributes:

- Default: an inverted index on the whole column. Serves containment (`col @> '{"k":"v"}'`), key existence, and path queries.
- Containment-only workloads: a containment-specialized operator class gives a smaller, faster index but drops key-existence support. Choose per measured workload.
- Equality or range on ONE scalar field inside the document: extract it to a stored generated column and put a B-tree on that. Query the generated column, not the JSON expression.
- Arrays inside the document (tags): inverted index plus containment operators.
- Constrain the column's shape (`CHECK (jsonb_typeof(col) = 'object')` or equivalent) so the index has a predictable structure.

Keep core relations in real columns. The moment a JSON field appears in a join or a business rule, promote it.

## Write Cost

- Every index slows every INSERT and indexed-column UPDATE. High-write tables get the minimum viable set.
- Updating an indexed column defeats in-place update optimizations; keep hot mutable columns out of indexes when possible.
- Bulk loads: drop secondary indexes, load, rebuild concurrently.
