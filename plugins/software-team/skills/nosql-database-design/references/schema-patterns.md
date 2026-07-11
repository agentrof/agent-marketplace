# Schema Design Patterns

## Embedding Patterns

- Full embedding: the entire subdocument lives in the parent (address in user).
- Subset embedding: only frequently-accessed fields embedded; the full document is referenced.
- Array embedding: a list of subdocuments in the parent (order items in an order).
- Bounded arrays: arrays with a known maximum size (tags, categories).
- Anti-pattern: unbounded arrays that grow indefinitely (messages in a channel). These eventually hit the document size limit and degrade every read of the parent.

## Referencing Patterns

- Manual reference: store the referenced document's `_id` as a plain field. This is the preferred form.
- Engine-specific formal reference types: avoid. They add structure without enforcing integrity; prefer manual references.
- Denormalized reference: store `_id` plus frequently-accessed fields (`user_id` + `user_name`). See the extended reference rule below.
- Two-way referencing: both documents store each other's `_id`. Use sparingly; it doubles the consistency burden and needs an explicit reconciliation strategy.

## Polymorphic Pattern

- Store different entity types in one collection with a `type` discriminator field.
- Each type can carry different fields, validated by conditional JSON Schema.
- Use for: products with divergent attributes (electronics, clothing, food), notifications (email, SMS, push).
- Index `type` plus type-specific fields with partial indexes.

## Bucket Pattern

- Group multiple related data points into a single bucket document.
- Use for: time-series (hourly/daily buckets), sensor readings, event logs.
- Reduces document count, enables pre-aggregation, bounds document size.
- Structure: `{ date: "2024-01-15", hour: 14, readings: [{...}, {...}], count: 120, sum: 4560 }`

## Outlier Pattern

- Handle documents that are statistical outliers (a celebrity user with millions of followers).
- Flag outliers with `has_overflow: true` and store the excess in overflow documents.
- Prevents unbounded array growth in the base document while keeping the common case simple.

## Computed Pattern

- Pre-compute derived values and store them in the document: `total_count`, `average_rating`, `last_activity`.
- Trade write complexity for read performance.
- Maintain with increment/set operators on the write path instead of recomputing on read.

## Subset Pattern

- Store a subset of a related collection's data inside the parent (latest comments in a post, top products in a category).
- Full data lives in the related collection; only the hot slice is embedded.
- Eliminates join-stage lookups for the common read.

## Extended Reference Pattern

- Store frequently-accessed fields from a referenced document alongside the reference id.
- Example: `{ author_id: "...", author_name: "John Doe", author_avatar: "/img/john.png" }`
- Eliminates join-stage lookups for common display scenarios.
- HARD RULE: every copied field whose source is mutable is a snapshot. The design MUST declare its refresh mechanism (application-side write to all copies, a change-stream listener, or a batch job) and its staleness tolerance. A mutable copy with no declared refresh path is a design defect: it drifts from its source silently.

## Tree Patterns

### Parent Reference

- Each node stores its parent's id: `{ _id: 3, parent_id: 1 }`
- Simple. Good for: finding the parent, moving subtrees.
- Bad for: finding all descendants (requires recursive queries).

### Child Reference

- Each node stores an array of children ids: `{ _id: 1, children: [2, 3] }`
- Good for: finding immediate children.
- Bad for: finding ancestors.

### Materialized Path

- Store the full ancestor path as a string: `{ path: "/1/2/3", name: "leaf" }`
- Good for: finding all descendants (prefix regex on `path`), finding ancestors (split the path).
- Index `path`; anchored prefix regex queries use the index.

### Nested Sets

- Store `left` and `right` values from a pre-order traversal.
- Good for: finding all descendants in one query (`left > parent.left AND right < parent.right`).
- Bad for: inserts and moves (every position recalculates). Use only for read-mostly trees.

## Approximation Pattern

- Use approximations for counters that do not need exact real-time accuracy.
- Increment with probability: update one in N times, multiply by N on read.
- Reduces write load for high-frequency counters (page views, likes).

## Schema Versioning Pattern

- Store a `schema_version` field in every document.
- Application code migrates between versions lazily on read.
- Batch migration jobs handle proactive upgrades.
- This is the document-store path to zero-downtime schema evolution; adopt it from the first collection, not retroactively.
