# Document Type System

Type names below follow common document-engine conventions; map them to the declared engine's equivalents.

| Type | Usage | Notes |
|------|-------|-------|
| `ObjectId` | Default `_id`, auto-generated, compact | Contains timestamp, machine id, counter. Roughly time-ordered. |
| `String` (UTF-8) | Text fields | No per-field max length, but the document size limit applies globally. |
| `Int32` / `Int64` | Integer fields | Use `Int64` for ids, counters, and anything that can grow large. |
| `Double` | Floating point | Never use for currency. Use `Decimal128` instead. |
| `Decimal128` | Exact decimal | For financial and monetary values. Exact arithmetic. |
| `Boolean` | true/false | Standard boolean semantics. |
| `Date` | Timestamps | Stored as milliseconds since epoch. Always UTC. |
| `Timestamp` (internal) | Replication internals | Do not use in application documents. Reserved for the engine's log. |
| `Binary` | Binary data | Subtypes exist for generic bytes, UUID, encrypted payloads. Large files belong in blob storage, not in documents. |
| `Array` | Ordered list of values | Can contain mixed types. Indexed with multikey indexes. Document size limit applies. |
| `Embedded Document` | Nested object | Full sub-document. Indexable and queryable with dot notation. |
| `Null` | Null value | Explicitly stored null. Different from "field does not exist". |
| `UUID` | UUID stored as a binary subtype | More compact than string UUIDs; prefer the binary form. |
| `MinKey` / `MaxKey` | Boundary values | For sharding and internal comparisons. Do not use in application documents. |

## Type Selection Rules

- Identifiers: `ObjectId` (default) or binary `UUID` (for ids generated across distributed systems).
- Timestamps: the engine's `Date` type, always UTC; convert to local time in the application layer.
- Currency: `Decimal128`, never `Double`. Floating point drifts; money must not.
- Counters: `Int64` for safety margin.
- Status/enum: `String` constrained by a schema-validation `enum`; more readable than numeric codes.
- Email/URL: `String` with a regex constraint in schema validation.
- Flags: `Boolean`; simple, queryable, indexable.
- Metadata/tags: `Array` of `String` with a multikey index for tag queries.

## Null Semantics

- Distinguish "field is null" from "field is absent"; queries and sparse indexes treat them differently. Pick one convention per field and document it.
- Every optional field needs a rationale; default to required with a sensible default value.
