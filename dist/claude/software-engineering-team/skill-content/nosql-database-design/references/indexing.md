# Index Catalog

## Single Field Index

- One field: `createIndex({ email: 1 })`
- `1` ascending, `-1` descending (direction matters for sort optimization).
- Supports equality, range, and sort on that field.
- `_id` is indexed automatically.

## Compound Index

- Multiple fields: `createIndex({ tenant_id: 1, created_at: -1, status: 1 })`
- ESR rule (Equality, Sort, Range):
  1. Equality fields first (fields matched with equals or in-list).
  2. Sort fields second (fields in the sort specification).
  3. Range fields last (greater-than / less-than bounds).
- Leftmost prefix: an index on `{a, b, c}` serves queries on `{a}`, `{a, b}`, `{a, b, c}`.
- Field order is critical; design for the most common query pattern and check the rest against prefixes.

## Multikey Index

- Created automatically when an array field is indexed; each element gets an index entry.
- Compound multikey: at most ONE array field per compound index.
- Use for: tags, categories, arrays of embedded documents.

## Text Index

- Full-text search over declared string fields.
- Supports stemming, stop words, language analyzers, weighted fields.
- One text index per collection (it can cover multiple fields).
- For production full-text search, prefer a dedicated search index (inverted-index based) over the built-in text index.

## Geospatial Index

- Sphere-aware index over GeoJSON data; supports near, within, and intersects queries.
- Use for location-based services and proximity search.
- Store coordinates as GeoJSON: `{ type: "Point", coordinates: [longitude, latitude] }` (longitude first).

## Hashed Index

- Indexes a hash of the field value for even distribution: `createIndex({ user_id: "hashed" })`
- Primary use: shard key for hash-based sharding.
- Supports equality queries only, never ranges.

## Wildcard Index

- Indexes all fields, or all fields matching a pattern: `createIndex({ "$**": 1 })`
- Use only for genuinely unpredictable field names. High storage cost; targeted indexes beat it whenever the fields are known.

## TTL Index

- Auto-expires documents after a duration: `createIndex({ expire_at: 1 }, { expireAfterSeconds: 0 })`
- Use for: session tokens, temporary data, cache entries, event logs.
- The expiry sweep runs periodically (about once a minute); do not rely on it for sub-minute precision.
- Single-field only; the field must be a `Date`.

## Partial Index

- Indexes only documents matching a filter: `createIndex({ email: 1 }, { partialFilterExpression: { is_active: true } })`
- Shrinks index size and maintenance cost.
- Essential for soft delete: index only active documents.
- The query must include the filter expression for the planner to choose the partial index.

## Collation Index

- Case-insensitive or locale-aware indexing: `createIndex({ name: 1 }, { collation: { locale: "en", strength: 2 } })`
- Strength two means case-insensitive comparison.
- The query must specify the same collation to use the index.

## Index Properties

| Property | Effect | Use Case |
|----------|--------|----------|
| `unique: true` | Enforce uniqueness | Email, username, slug |
| `sparse: true` | Index only documents where the field exists | Optional fields, rare values |
| `hidden: true` | Index exists but the planner ignores it | Test removal impact without dropping |

Index builds run online by default in current engines and do not block writes; still schedule large builds off-peak.

## Index Hygiene Rules

- Every index cites the query pattern or user story it serves. No rationale, no index.
- No redundant indexes: if index A's fields are a leftmost prefix of index B's, drop A.
- High-write collections carry the minimum viable index set; every extra index taxes every write.
- Unique indexes exist wherever a business rule demands uniqueness; application-level checks alone do not count.
