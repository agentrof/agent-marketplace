---
name: nosql-database-design
description: Document database design expertise. Document modeling, embedding vs referencing, schema patterns, indexing, sharding, and consistency for document stores. Loaded by the architecture role when the project stack declares a document database.
user-invocable: false
---

# Document Database Design

Sibling: the sql-database-design skill covers relational design. Choosing between them is an upstream stack decision: join-heavy, integrity-critical access patterns favor relational; document-local, read-shaped access patterns favor a document store.

## When to Use

Load this skill when designing or reviewing a document-store schema: collection layout, embed-vs-reference decisions, index plans, shard keys, or consistency settings. Do not load it for relational schema work (use the sibling) or for runtime tuning and cluster operations, which are out of scope for design.

## Cardinal Rules

- Data that is accessed together is stored together. This is the cardinal rule of document modeling.
- Schema design is driven by the application's query patterns, not by entity relationships. Enumerate the queries first, then shape the documents.
- Favor reads over writes: denormalize for read performance, accept write complexity, and account for it explicitly.
- Documents are not rows. Embrace nested structures, arrays, and polymorphism.
- Design for bounded growth. Document engines impose a hard document size limit (16MB is typical); never model an unbounded array inside a document.

## Embedding vs Referencing

| Factor | Embed | Reference |
|--------|-------|-----------|
| Access pattern | Always accessed together | Accessed independently |
| Data relationship | True composition (part-of) | Association (related-to) |
| Update frequency | Rarely updated | Frequently updated |
| Data size | Small, bounded | Large or unbounded |
| Data duplication | Acceptable, managed | Unacceptable |
| Atomicity | Need atomic operations | Eventual consistency OK |
| Cardinality | 1:few or 1:bounded-many | 1:unbounded-many or N:M |
| Growth | Bounded (won't exceed the document size limit) | Unbounded |

DO use manual references (store the referenced document's id as a plain field). DON'T use engine-specific formal reference types; they add ceremony without integrity.

## Schema Patterns

Named patterns, one line each; full detail in the schema-patterns reference:

- Polymorphic: mixed entity types in one collection with a `type` discriminator.
- Bucket: group time-series or event points into bounded bucket documents.
- Outlier: flag statistical outliers and overflow their excess into side documents.
- Computed: pre-compute derived values on write, read them cheaply.
- Subset: embed the hot slice of a related collection, keep the full data referenced.
- Extended reference: store the reference id plus a few frequently-read source fields.
- Tree: parent-reference, child-reference, materialized path, or nested sets.
- Schema versioning: a `schema_version` field per document enables lazy, zero-downtime migration.

Hard rule for extended references and every other denormalized copy: a copied field whose source is MUTABLE is a snapshot. It MUST declare its refresh mechanism (application-side write, change-stream listener, or batch job) and its staleness tolerance. An undeclared mutable copy is a design violation, not a style nit; it silently drifts into a correctness bug.

## Indexing

- Compound index field order follows the ESR rule: Equality fields first, Sort fields second, Range fields last.
- Compound indexes serve leftmost prefixes: an index on `{a, b, c}` serves queries on `{a}` and `{a, b}`.
- Every index must cite the query pattern it serves. No speculative indexes; no index that is a prefix of another.
- Key index kinds: single-field, compound, multikey (arrays; at most one array field per compound), TTL (auto-expiry for sessions and tokens), partial (filtered subsets, essential for soft-delete), unique.
- Full catalog, including text, geospatial, hashed, wildcard, and collation indexes, in the indexing reference below.

## Sharding

### Shard Key Selection Criteria

| Criteria | Ideal | Avoid |
|----------|-------|-------|
| **Cardinality** | High (many unique values) | Low (few values like status, boolean) |
| **Frequency** | Even distribution | Hot spots (most documents have same value) |
| **Monotonicity** | Non-monotonic (random, hashed) | Monotonically increasing (timestamp, ObjectId) |
| **Query isolation** | Queries include shard key | Queries require broadcast to all shards |

Strategy detail (range, hash, zone, compound keys) in the scaling-consistency reference below.

## Consistency

- Critical writes use majority write concern: acknowledged by a majority of the replica set, or the write is not durable against failover. Default single-node acknowledgment is for non-critical paths only.
- Prefer single-document atomicity (embedding) over multi-document transactions; reach for transactions last. Read/write concerns, read preferences, transactions, and change streams in the scaling-consistency reference below.

## Naming

- Collections: plural, lowercase, snake_case (`users`, `order_items`).
- Fields: pick ONE convention (camelCase or snake_case), match the application layer, apply it everywhere. The primary key is always `_id`.
- Reference fields: `{referenced_singular}_id` or `{referencedSingular}Id`. Booleans: `is_`/`has_` prefix. Timestamps: `_at` suffix.
- Indexes: `idx_{collection}_{fields}`; unique indexes `uidx_{collection}_{fields}`.

## References

- [data-types](references/data-types.md): document type system and type selection rules. Read when choosing field types or an id strategy.
- [schema-patterns](references/schema-patterns.md): every pattern above in full, including tree patterns. Read when applying or reviewing one of the named patterns.
- [indexing](references/indexing.md): full index catalog and index properties. Read when the plan needs more than single-field and compound indexes.
- [aggregation](references/aggregation.md): pipeline stages and performance rules. Read when a read path needs server-side aggregation.
- [schema-validation](references/schema-validation.md): collection-level JSON Schema validation. Read when writing validators for production collections.
- [scaling-consistency](references/scaling-consistency.md): sharding, concerns, transactions, change streams. Read when choosing a shard key or consistency settings.
- [performance](references/performance.md): covered queries, plan analysis, pooling. Read when validating the design against its query patterns.
- [review-checklist](references/review-checklist.md): design-time review checklist. Read when reviewing a document-store schema deliverable.
- [qa-checklist](references/qa-checklist.md): QA gate with PASS/FAIL criteria. Read when running the QA gate on the schema deliverable.
