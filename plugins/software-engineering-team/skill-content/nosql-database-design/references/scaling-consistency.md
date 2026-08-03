# Scaling and Consistency

## Sharding Strategies

### Shard Key Selection Criteria

| Criteria | Ideal | Avoid |
|----------|-------|-------|
| **Cardinality** | High (many unique values) | Low (few values like status, boolean) |
| **Frequency** | Even distribution | Hot spots (most documents have same value) |
| **Monotonicity** | Non-monotonic (random, hashed) | Monotonically increasing (timestamp, ObjectId) |
| **Query isolation** | Queries include shard key | Queries require broadcast to all shards |

### Range-Based Sharding

- Shard key ranges are distributed across shards.
- Good for: range queries on the shard key (date ranges for time-series).
- Risk: hot spots when the data distribution is uneven or the key is monotonic.

### Hash-Based Sharding

- A hash of the shard key drives placement.
- Good for: even write distribution, point queries.
- Bad for: range queries (they broadcast to all shards).
- Declare with `{ field: "hashed" }` as the shard key.

### Zone Sharding

- Assign ranges of shard key values to specific shards or zones.
- Use for: geographic data locality (regional data pinned to regional shards), tiered storage.
- Combine with range or hash sharding.

### Compound Shard Key

- Multiple fields: `{ tenant_id: 1, created_at: 1 }`
- Improves query isolation (tenant-scoped queries route to one shard).
- Fixes the cardinality problem of a single low-cardinality field.

## Write Concerns

| Level | Guarantee | Performance |
|-------|-----------|-------------|
| Zero acknowledgment | Fire-and-forget | Fastest, least safe |
| Primary acknowledged | The primary applied the write | Common default, lost on failover before replication |
| Majority | A majority of the replica set acknowledged | Strong durability; REQUIRED for critical writes |
| Specific node count | A fixed number of nodes acknowledged | Custom durability |
| Journaled | Written to the on-disk journal | Crash-safe, adds latency |

## Read Concerns

| Level | Guarantee |
|-------|-----------|
| Local | Data from the primary; may be rolled back after failover |
| Available | Data from any node; fastest, least consistent |
| Majority | Data acknowledged by a majority; durable |
| Linearizable | Reflects all writes prior to the read; strongest, slowest |
| Snapshot | Point-in-time view for multi-document transactions |

## Read Preferences

| Preference | Target | Use Case |
|------------|--------|----------|
| Primary | Primary only | Default, strong consistency |
| Primary preferred | Primary, fall back to secondary | Availability with a consistency preference |
| Secondary | Secondaries only | Offloaded reads, analytics |
| Secondary preferred | Secondary, fall back to primary | Read scaling |
| Nearest | Lowest-latency node | Geo-distributed reads |

Secondary reads serve stale data by definition; pair them with a stated staleness tolerance.

## Multi-Document Transactions

- Modern document engines support multi-document ACID transactions within a replica set and, at higher cost, across shards.
- Use the driver's transaction wrapper for automatic retry on transient errors.
- Keep transactions short; engines enforce a timeout measured in seconds.
- Design rule: reach for embedding first. A schema that needs frequent multi-document transactions is usually a schema that embedded too little or referenced too much; single-document updates are atomic for free.

## Change Streams

- Watch collection changes: `collection.watch([pipeline], options)`
- Event types: insert, update, replace, delete, invalidate.
- Persist resume tokens and resume from the last position after a crash.
- Use for: real-time notifications, cache invalidation, event sourcing, data sync, and refreshing denormalized copies (a legitimate refresh mechanism for the extended reference pattern).
- Filter server-side: `[{ $match: { operationType: "insert" } }]`; project only needed fields to cut transfer.
