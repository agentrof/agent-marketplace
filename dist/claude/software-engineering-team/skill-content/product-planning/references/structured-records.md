# Structured Records: Roles, Dependencies and Test Coverage

The Markdown front matter and headings in each story package are the only
structured record.

## Role ownership

```yaml
owner_role: backend_developer
supporting_roles:
  - frontend_developer
  - devops_engineer
```

Exactly one implementation role owns the integrated story result.
`supporting_roles` is optional and contains unique team role identifiers. It
cannot contain the owner. Every listed role appears once in `Implementation
Responsibilities` with a concrete contribution. Do not store a person, host
task, agent session or transient execution identity.

Allowed values are closed:

- `owner_role`: `backend_developer`, `frontend_developer`, `devops_engineer`
- `supporting_roles`: the owner-role set plus `software_architect` and
  `ux_designer`

Product Owner, Business Analyst, QA Engineer and reviewers participate through
the flow and are not repeated as implementation roles. QA ownership of the
sibling test plan is expressed by `owner_role: qa_engineer` on
`test-plan.md`.

## Traceability links

All document references are quoted, vault-absolute wikilinks. A criterion link
targets its approved owning note and uses the BA registry-qualified identity as
its alias:

```yaml
criterion_refs:
  - "[[business-analysis/accounts/acceptance/account-access-acceptance|accounts:AC-ACC-001]]"
experience_refs:
  - "[[experience-design/programs/prg-1/releases/rel-1/journeys/account-access-journey|Account access journey]]"
uses_design:
  - "[[design-system/MASTER|Product design master]]"
```

The upstream note must exist, be approved and not be superseded. Bare tokens
such as `AC-ACC-001` are not links and do not satisfy coverage.

Every story also declares its intake kind:

```yaml
work_kind: feature
```

`feature`, `defect` and `technical` are the complete vocabulary. A story
retains `criterion_refs`, `experience_refs`, at least one `uses_design`
reference and a Solution Design `constrained_by` reference whenever its
Requirement impact matrix marks those outputs as required or reused. A defect
or technical story may omit those upstreams only when `related_to` names at
least one approved or accepted source, issue or decision note and the matrix
marks the upstream stage not applicable. This is scoped intake evidence, not a
way to weaken feature traceability.

Historical BA registries do not silently expand a scoped intake. By default,
only explicitly selected story criteria/evidence are in scope; a root deferral
cannot select its own scope. Put canonical
`analysis_scopes` on `backlog.md` only to
select a complete approved BA space or nested domain deliberately:

```yaml
analysis_scopes:
  - erp#domains/inventory
```

Every active approved AC and BR under a declared scope must then be covered or
deferred exactly once, using the same compiler rule as any selected Requirement
scope.

## Dependency edges

Use `depends_on` only when the story consumes a concrete output, state or
capability from another story:

```yaml
depends_on:
  - "[[backlog/epics/identity/stories/register-account/story|ST-001]]"
```

Name the same target and its reason in `Dependencies`:

```markdown
- [[backlog/epics/identity/stories/register-account/story|ST-001]] - consumes
  the registered account identifier.
```

The compiler validates exact target agreement, story target type and an
acyclic graph. "Comes after" is ordering prose, not a dependency reason.

## Test-plan records

Every story has exactly one sibling `test-plan.md`. Use one stable heading per
scenario:

```markdown
## ST-002-TS-001

- category: happy-path
- target: api
- automation: required
- automation_target: tests/api/test_accounts.py::test_sign_in
- source_refs:
  - [[business-analysis/accounts/acceptance/account-access-acceptance|accounts:AC-ACC-002]]
- Given: an active registered account exists
- When: valid credentials are submitted
- Then: access is granted for that account
```

`automation` is `required` or `manual`; required scenarios name an automation
target. The target may be planned and absent until delivery. `source_refs` is
required on every scenario. For a feature, it contains only the story's
declared criteria. For defect or technical work, it contains the story's
declared criteria and/or approved `related_to` evidence. Every declared
planning source appears in at least one scenario. A test plan records intended
verification only.

Every test plan also has one exact coverage-class table:

```markdown
| class | disposition | scenario_refs | reason |
|---|---|---|---|
| empty | covered | ST-002-TS-002 | |
| boundary | not_applicable | - | No numeric or cardinality boundary exists in this slice. |
| invalid-input | covered | ST-002-TS-003 | |
| authorization | covered | ST-002-TS-004 | |
| duplicate-concurrent | covered | ST-002-TS-005 | |
| failure | covered | ST-002-TS-006 | |
| adjacent-regression | covered | ST-002-TS-007 | |
```

The seven class keys are closed. `covered` cites one or more scenario IDs that
exist in the same test plan. `not_applicable` cites none and gives a concrete
reason. The union of all covered rows is exactly the declared scenario set;
one scenario may serve multiple classes, but none may remain unclassified.
Missing classes, unknown/orphan scenarios and unexplained exclusions fail.

## Review coverage

An epic review uses `derives_from` for its epic and `verifies` for the exact
child story and test-plan set. The root review uses `derives_from` for the
backlog and `related_to` for the exact epic set. Review prose contains the
required lenses and findings, but prose mentions never substitute for relation
coverage.

The epic-review body has these headings:

```text
Scope
Slicing
Criteria Coverage
Test Design
Dependencies
Role Ownership
Findings
Verdict
```

The root-review body has these headings:

```text
Epic Coverage
Cross-Epic Overlap
Cross-Epic Dependencies
Release Ordering
Shared Contracts
Deferred Criteria
Global Test Coverage
Findings
Verdict
```

`Deferred Criteria` is not prose. It is a table with exactly these columns:

```markdown
| criterion_ref | owner_role | reason | revisit_trigger |
|---|---|---|---|
| [[business-analysis/accounts/rules/access-rules\|accounts:BR-ACC-004]] | product_owner | Delegation is outside the first release. | Revisit when delegated access enters approved scope. |
```

`owner_role` is always the closed team role `product_owner`; deferral scope and
revisit accountability cannot be assigned to an invented token.

The link target is the approved owning acceptance/rule note, the alias is the
registry-qualified identity, and the table pipe is escaped. Every selected
active AC and BR in an approved BA registry is either covered by one or more
stories or occurs once in this table. A shared criterion may support multiple
delivery slices, but it cannot be both covered and deferred. A root
`analysis_scopes` declaration expands that equality to a complete named scope.
Overlap, unknown/wrong-owner links and uncovered
identities fail. Every other review section contains section-labelled
`Evidence [<section>]:` and `Conclusion [<section>]:` lines. Evidence cites at
least one resolvable vault-absolute
wikilink and explains why it supports that lens; the conclusion states the
lens-specific result. Long generic prose, `approved`, `pass`, `looks good`,
`no findings`, `none` and untouched placeholders fail.

Configured document-type designations govern authored titles and matching H1s.
The configured designation, with output-language-aware initial casing, is the
complete root backlog/root-review label;
user-authored base titles append the applicable designation. Stable type keys,
paths, IDs, registry JSON and disposable generated-view labels remain English
machine vocabulary.
