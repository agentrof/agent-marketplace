# Design Review Checklist (Document Store)

Design-time review of a document-store schema deliverable. Findings are graded Critical / Major / Minor. Anything Critical or Major blocks approval.

## Document Model Decisions

- [ ] Every embed-vs-reference choice is justified against the decision framework (access pattern, update frequency, cardinality, growth)
- [ ] No unbounded arrays inside documents; every embedded array states its growth bound
- [ ] No document can plausibly approach the engine's document size limit under projected growth
- [ ] References are manual id fields, not engine-specific formal reference types
- [ ] Two-way references (if any) declare a consistency management strategy

## Denormalized Copies (MAJOR on violation)

- [ ] Every extended reference or denormalized copy of a MUTABLE source field declares its refresh mechanism (application-side write, change-stream listener, or batch job)
- [ ] Every such copy declares its staleness tolerance
- [ ] A mutable copy with no declared refresh mechanism is a MAJOR finding: silent drift from the source is a correctness bug, not a style nit
- [ ] Immutable copies (order line price at time of purchase) are explicitly marked as intentional snapshots

## Field Specification

- [ ] Every field has all specification columns filled: type, required, default, indexed, unique, description
- [ ] No placeholder types ("TBD", "string", "any")
- [ ] Currency and financial fields use an exact decimal type, never float or double
- [ ] Timestamp fields use the engine's date type, UTC
- [ ] Identifier fields use a concrete id type, not a generic "id"
- [ ] Enum values are explicitly listed and mirrored in schema validation
- [ ] Required/optional designation is justified for every field; null vs absent is a documented convention

## Index Strategy

- [ ] Every declared query pattern has a supporting index
- [ ] Compound index field order follows the ESR rule (Equality, Sort, Range)
- [ ] Every index has a rationale tied to a specific query pattern or user story
- [ ] No redundant indexes (index A a leftmost prefix of index B)
- [ ] Unique indexes exist wherever business rules require uniqueness
- [ ] Partial indexes exist for filtered queries (active-only records under soft delete)
- [ ] TTL indexes exist for expiring data (sessions, tokens, temporary records)
- [ ] High-write collections do not carry excessive indexes
- [ ] At most one array field per compound (multikey) index

## Relationship Integrity

- [ ] All relationships state explicit cardinality (1:1, 1:N, N:M) and direction
- [ ] Cardinality matches the business domain
- [ ] Every reference declares an integrity strategy (application-level cascade, orphan cleanup job, or restrict-in-code); document stores do not enforce it for you
- [ ] Orphan prevention is documented for every referencing collection
- [ ] The implementation method (embedded, manual reference) fits the access pattern

## Common Field Patterns

- [ ] `created_at` and `updated_at` exist on all mutable entities with type and update mechanism
- [ ] `created_by` and `updated_by` exist on all mutable entities, referencing the users entity
- [ ] Soft-delete strategy is documented where applicable, with partial indexes for active-only queries
- [ ] Cascade behavior for soft-deleted parents is documented
- [ ] Common patterns are documented once as a base-document recommendation for implementers

## Sharding and Consistency (when in scope)

- [ ] The shard key passes all four selection criteria (cardinality, frequency, monotonicity, query isolation)
- [ ] Critical writes specify majority write concern
- [ ] Multi-document transactions are the exception; the schema achieves atomicity through embedding where possible
- [ ] Schema validation exists for every production collection with strict level and error action

## Decision Traceability

- [ ] Every significant decision has an ADR-format entry: status, context, decision, alternatives, consequences
- [ ] Embedding vs referencing decisions are individually documented
- [ ] Context explains WHY, not just WHAT; no hand-waving rationale ("for performance" without specifics)
- [ ] At least one alternative was considered per decision
- [ ] Consequences include trade-offs, not only benefits
