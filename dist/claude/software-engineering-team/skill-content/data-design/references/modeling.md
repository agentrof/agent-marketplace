# Data modeling

## Semantics and ownership

Describe each entity, lifecycle and authoritative writer. Name business keys
separately from technical IDs. State relationship cardinality, optionality,
ownership, ordering and deletion/archive effects. Trace cascades to their
leaves. Prevent cross-owner writes and undeclared copies of mutable values.

For every changed field, record its concrete engine type, meaning, units,
required/null/absent semantics, default, validation, uniqueness scope,
sensitivity and applicable retention. Avoid redundant audit or status fields
without a consumer or invariant. Keep terminology consistent with the domain.
Give one representative example when the semantics would otherwise be unclear.

Use exact representations for currency and quantities whose rounding matters.
Distinguish an instant from a local calendar date or recurring local time, and
preserve any timezone needed by the business rule. Distinguish unknown, absent,
empty and zero. Choose identifier generation from identity/distribution needs
rather than assuming one integer or UUID scheme fits every store.

## Relational shape

Normalize authoritative mutable facts to prevent update anomalies. Document a
denormalized copy's purpose, source, refresh and staleness, or its explicitly
frozen event-time meaning. Consider a derived view before adding another writer.

Use primary/business keys, foreign keys, uniqueness and value constraints where
the engine can enforce the invariant. Check null behavior of constraints and
uniqueness in the actual engine. Express tenant/parent-scoped uniqueness and
cross-tenant relationship restrictions. A polymorphic reference needs an
integrity strategy if the store cannot enforce it directly.

## Document and key-value shape

Design from access patterns, atomic update boundaries and bounded growth.
Embedding suits cohesive data with a bounded lifecycle. Independently updated,
many-to-many or unbounded data can require references. A reference must declare
orphan prevention and deletion semantics. State schema versions and validation
for evolving records.

Name maximum document/item, embedded collection and key sizes for the selected
engine. Bound time buckets and overflow paths. For computed/subset/extended
records, specify source ownership and repair after missed updates. Choose read
and write consistency from the required durability and conflict behavior.

## Queries and physical design

List important filters, joins, ordering, pagination and write patterns. Justify
indexes against those patterns and verify with representative plans. Include
uniqueness, predicates, collation and ordering before declaring indexes
redundant. Foreign-key indexing and compound-prefix behavior depend on the
engine and workload. A small intentional scan can be correct.

Measure read/write amplification and storage cost. Add partitioning or sharding
only for a demonstrated access, capacity or lifecycle need. Evaluate key
distribution, hot partitions, routing, rebalance and cross-partition operations.
Keep naming and type conventions consistent with the chosen engine and project.
Check version-sensitive DDL and index behavior in official documentation.
