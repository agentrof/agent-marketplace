# QA Gate Checklist

Gate validation of the database design deliverable against upstream requirements. Runs after the design review; verdict is PASS or FAIL.

## Entity Coverage

- [ ] Every data model in the product brief has a corresponding entity definition.
- [ ] Every user story's data needs are served by at least one entity.
- [ ] Every business rule implying data storage has a supporting entity.
- [ ] No orphan entities (entities no story or rule references).
- [ ] Junction entities exist for all many-to-many relationships.
- [ ] Lookup entities exist for enumerated value sets that may change.
- [ ] All CRUD operations implied by stories have entities to operate on.

## Upstream Consistency

- [ ] Entity names match the brief's business concepts through
      glossary.md's business-term to technical-name mapping, rendered in
      the terminology_language.
- [ ] Non-functional requirements (audit, retention, multi-tenancy) are reflected in the schema.
- [ ] Every acceptance criterion that implies data is served by the schema; fields cover all data points in story scenarios.
- [ ] Access patterns from user stories have supporting indexes.
- [ ] Business rules implying constraints are enforced at the database level, and the schema's validation rules match the rule specifications.

## Example Completeness

- [ ] Every entity has a complete example row with ALL fields populated.
- [ ] Example values are realistic and match the declared types.
- [ ] Examples use placeholder names only (John Doe, Jane Doe, Acme Corp).
- [ ] Relationship references in examples resolve to valid IDs elsewhere in the examples.
- [ ] A schema change log exists with a baseline entry (version, date, entity, change, rationale).

## Verdict Criteria

PASS requires all of:

- Full entity coverage of the product brief's data models
- Full field specification completeness (no empty cells, no placeholder types)
- Every query pattern backed by an index with a written rationale
- All relationships carrying cardinality, integrity rules, and implementation method
- Audit fields on all mutable entities and a base-model recommendation present
- Every design decision in ADR form with alternatives and consequences
- Every denormalized copy of a mutable field carrying a declared refresh mechanism and staleness tolerance
- Complete examples following placeholder conventions, and a schema change log baseline
- Zero Critical and zero Major findings open from the design review

FAIL on any of:

- Entity coverage gaps against the brief or stories
- Any field missing type, nullability, or description
- Query patterns without supporting indexes, or indexes without rationale
- Relationships lacking integrity rules
- Missing audit fields on mutable entities
- A denormalized mutable-field copy with no refresh mechanism
- Design decisions without documented rationale
- Missing or incomplete examples
- Any open Critical or Major finding

## Rules

- ALWAYS verify entity-to-requirement traceability against both the brief and the stories.
- ALWAYS check every field table cell is filled.
- ALWAYS validate that each index rationale names a specific query pattern.
- ALWAYS verify audit fields on every mutable entity.
- ALWAYS check that design decisions have ADR entries.
- ALWAYS check every denormalized copy for a refresh mechanism and staleness tolerance.
- NEVER approve with missing field specifications.
- NEVER approve with undocumented indexes.
- NEVER approve with relationships lacking integrity rules.
- NEVER approve with an unreviewed cascade chain.
- Report prose in the configured output_language, names and technical terms in the terminology_language (both default English), in the step's output directory, with status, findings by severity, and coverage tables.
