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
target. The target may be planned and absent until delivery. Every story
criterion appears in at least one scenario. A test plan records intended
verification only.

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
