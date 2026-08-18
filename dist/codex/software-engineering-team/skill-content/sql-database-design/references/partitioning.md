# Partitioning

## When to Partition

- Very large tables (roughly beyond the hundred-million-row mark) where queries consistently filter on a candidate partition key, usually time.
- Tables whose maintenance dictates it: data pruned or bulk-replaced periodically. Detaching a partition is instant; deleting millions of rows is not.
- Do not partition small tables for tidiness. Partitioning adds planning overhead and constraint limitations.

## Strategies

| Strategy | Key shape | Typical use |
|----------|-----------|-------------|
| RANGE | Continuous values, usually dates | Time-series, events, logs; monthly or quarterly partitions; enables pruning for date-filtered queries |
| LIST | Discrete values | Region, tenant tier, status class; multi-tenancy with geographic distribution |
| HASH | No natural range or list key | Even distribution by hash of a key such as `user_id` |

Sub-partitioning combines them: range by month, hash within the month, when a single time slice is still too hot.

## Design Rules

- The partition key must appear in the dominant query predicates or pruning never happens and every query fans out to all partitions.
- Include the partition key in the primary key and any UNIQUE constraint; global uniqueness across partitions is not enforceable declaratively.
- Foreign keys FROM a partitioned table are restricted in most engines; plan integrity via application checks or triggers and document that as an ADR.
- Use declarative partitioning. Do NOT build partitioning on table inheritance; it lacks the current pruning guarantees.

## Lifecycle Management

- Create partitions ahead of need (a scheduled job or the engine's automation extension); an insert with no matching partition fails.
- Archive by detaching: `ALTER TABLE ... DETACH PARTITION`, then dump or move the standalone table.
- Retention policy is part of the schema design: state which partitions are dropped, when, and who owns the job.
- Indexes: define them on the parent so every new partition inherits them; verify the engine propagates automatically or script it.
