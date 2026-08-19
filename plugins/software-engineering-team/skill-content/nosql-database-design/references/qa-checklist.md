# QA Gate Checklist (Document Store)

Gate-stage validation of a document-store design deliverable against upstream requirements. The gate emits PASS or FAIL; FAIL returns the deliverable to the executor with the findings list.

## Entity Coverage

- [ ] Every data model in the product brief has a corresponding collection or embedded entity definition
- [ ] Every user story's data needs are served by at least one entity
- [ ] Every business rule that implies data storage has a supporting entity
- [ ] No orphan entities (entities with no user story or business rule reference)
- [ ] All CRUD operations implied by user stories have entities to operate on
- [ ] Lookup/reference entities exist for enumerated value sets that may change
- [ ] Entity grouping by domain is logical and documented

## Upstream Consistency

- [ ] Entity names match the brief's business concepts through
      glossary.md's business-term to technical-name mapping, rendered in
      the terminology_language
- [ ] Non-functional requirements (audit, retention, multi-tenancy) are reflected in the schema
- [ ] Every acceptance criterion that implies data is served by the schema
- [ ] Fields cover all data points mentioned in user story scenarios
- [ ] Access patterns from user stories have supporting indexes
- [ ] Business rules that imply data constraints are enforced via schema validation, not only application logic
- [ ] Invariants are protected by validators and unique indexes where the engine can enforce them

## Example Completeness

- [ ] Every entity has a complete example document with ALL fields populated
- [ ] Example values are realistic and match the declared field types
- [ ] Examples use placeholder names only (John Doe, Jane Doe, Acme Corp)
- [ ] Examples demonstrate references correctly (valid ids that resolve within the example set)
- [ ] Denormalized copies in examples are consistent with their source documents
- [ ] A schema change log exists with an initial baseline entry (version, date, entity, change, rationale)

## Validation Process

1. Entity coverage: map every product-brief data model and user story to an entity definition; verify required fields exist for each use case.
2. Field audit: verify every specification cell is filled and types match the declared engine.
3. Index coverage: for each listed query pattern, identify the supporting index, verify field order and type; for each index, verify rationale and non-redundancy.
4. Relationship check: verify both sides exist, cardinality matches domain logic, and an integrity strategy is declared.
5. Cross-reference upstream: brief data models to entities (coverage), acceptance criteria to fields (completeness), business rules to validators (enforcement).

## PASS Criteria

- 100% entity coverage for product brief data models
- 100% field specification completeness (no empty cells)
- Every query pattern has a supporting index with rationale
- All relationships have cardinality and a declared integrity strategy
- Every mutable denormalized copy declares refresh mechanism and staleness tolerance
- Audit trail fields present on all mutable entities
- Every design decision has an ADR entry with rationale and alternatives
- All examples complete, using placeholder conventions
- Schema change log has a baseline entry

## FAIL Criteria

- Entity coverage below 100% for product brief data models
- Any field missing type, presence/nullability semantics, or description
- Any query pattern without a supporting index
- Any reference without an integrity strategy
- Any mutable denormalized copy without a declared refresh mechanism (MAJOR)
- Missing audit trail fields on mutable entities
- Design decisions without documented rationale
- Missing or incomplete examples
- Any Critical or Major finding open

## Rules

- ALWAYS verify entity-to-requirement traceability before anything else
- ALWAYS check every field specification cell is filled
- ALWAYS validate that index rationale ties to a specific query pattern
- ALWAYS verify audit trail fields on all mutable entities
- NEVER approve with undocumented indexes
- NEVER approve with a mutable copy lacking a refresh path
- NEVER approve with references lacking an integrity strategy
