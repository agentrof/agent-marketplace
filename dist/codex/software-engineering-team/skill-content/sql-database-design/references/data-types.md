# Data Type Decisions

Rules are engine-neutral; where engines diverge, the divergence is stated as a capability, never a version. Confirm the exact spelling against the project's declared engine.

## Decision Table

| Need | Use | Never | Why |
|------|-----|-------|-----|
| Surrogate ID | `bigint GENERATED ALWAYS AS IDENTITY` | `serial`, plain `int` for growth tables | Identity is standard SQL; bigint avoids painful widening later |
| Distributed / opaque ID | `uuid`, time-ordered variant preferred | random UUID as clustered/primary index on hot tables | Random UUIDs shred B-tree locality; time-ordered ones insert append-mostly |
| Money, quantities needing exactness | `numeric(p,s)` | `float`, `double`, the `money` type | Binary floats cannot represent decimal cents; `money` couples locale to storage |
| Free-form text | `text` | `char(n)`, `varchar(n)` | Length limits belong in CHECK constraints where they are a business rule; `char(n)` pads |
| Event time | timezone-aware timestamp, stored UTC | naive `timestamp`, `timetz` | Naive timestamps silently mean "somebody's local time" |
| Date only / duration | `date` / `interval` | timestamps abused for dates | Intent-revealing, correct comparisons |
| True/false | `boolean NOT NULL` | integer flags | Tri-state only when unknown is a real business state |
| Small stable value set | native enum type | enum for evolving business states | Enums are painful to shrink; evolving sets (order status) use `text` + CHECK or a lookup table |
| Semi-structured attributes | binary JSON column type | JSON as a substitute for real columns | Core relations stay relational; JSON is for optional, variable attributes |
| Ordered small list queried by element | array type where the engine has one | arrays for relationships | Relationships need a junction table with FKs |
| Intervals (booking, validity) | range types where available | start/end column pairs without an overlap constraint | Ranges get overlap operators and exclusion constraints |
| IP / network data | native network types where available | `text` | Native types validate and support containment operators |
| Binary blobs | the engine's byte-array type | base64 in text | Half the size, no encode/decode tax |

## Numeric Sizing

- Prefer `bigint` for anything that counts or identifies; `integer` when the range is provably bounded; avoid the smallest size unless storage is measured as critical.
- Floating point (`double precision`) is for measurements and scientific values where relative error is acceptable. Exact decimal arithmetic always means `numeric`.

## Text Handling

- Case-insensitive lookup: expression index on `lower(col)` and query the same expression. A case-insensitive collation or text type is the alternative when the column participates in PK/FK/UNIQUE constraints.
- Validation belongs in CHECK constraints or reusable domain types (`CREATE DOMAIN email AS text CHECK (...)`), not in the length parameter of a string type.

## Time Handling

- Store every instant in UTC using the timezone-aware type; convert at the presentation layer.
- Never specify sub-second precision suffixes on timestamp types; take the default.
- Distinguish transaction-start time from wall-clock time when the engine offers both; audit fields usually want transaction time.

## Engine Divergence Notes

- Some engines treat `text` and bounded varchar identically in performance; others reward bounded types. The design rule stays: limits are business rules, expressed as constraints.
- UUID storage differs: native 128-bit type vs 16-byte binary. Never store UUIDs as 36-character text.
- Enum support differs: native enum types vs column-level enum vs none. The lookup-table fallback is portable and self-documenting.
- Auto-increment differs (identity columns vs auto-increment attribute). The rule is the same: one canonical generated key, gaps are normal.
