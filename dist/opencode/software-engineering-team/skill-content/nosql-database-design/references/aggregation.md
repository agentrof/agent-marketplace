# Aggregation Pipelines

## Commonly Used Stages

| Stage | Purpose | Example |
|-------|---------|---------|
| `$match` | Filter documents (always first, so indexes apply) | `{ $match: { status: "active" } }` |
| `$group` | Group and aggregate | `{ $group: { _id: "$category", total: { $sum: "$price" } } }` |
| `$project` | Reshape documents, include/exclude fields | `{ $project: { full_name: { $concat: ["$first", " ", "$last"] } } }` |
| `$lookup` | Left outer join with another collection | `{ $lookup: { from: "orders", localField: "_id", foreignField: "user_id", as: "orders" } }` |
| `$unwind` | Flatten arrays into separate documents | `{ $unwind: "$tags" }` |
| `$sort` | Sort results | `{ $sort: { created_at: -1 } }` |
| `$limit` / `$skip` | Pagination | `{ $skip: 20 }, { $limit: 10 }` |
| `$facet` | Run multiple sub-pipelines in parallel | Multi-dimension analytics in one pass |
| `$merge` / `$out` | Write results to a collection (materialized view) | `{ $merge: { into: "monthly_stats" } }` |
| `$addFields` | Add computed fields | `{ $addFields: { total: { $multiply: ["$price", "$qty"] } } }` |
| `$bucket` | Group into ranges | `{ $bucket: { groupBy: "$age", boundaries: [0, 18, 30, 60, 100] } }` |

## Performance Rules

- `$match` and `$sort` go at the beginning of the pipeline so they can use indexes.
- `$project` early to shrink documents flowing through later stages.
- Avoid `$lookup` on large collections; a recurring `$lookup` on a hot path is a signal the schema should embed or denormalize instead.
- Enable disk spill (`allowDiskUse`) for aggregations that exceed the in-memory stage limit; better, redesign so they do not.
- Use `$merge` for incremental materialized views; `$out` replaces the whole target collection.
- A pipeline that exists to reassemble what one document could have held is a schema-design failure, not an aggregation problem. Fix the model.
