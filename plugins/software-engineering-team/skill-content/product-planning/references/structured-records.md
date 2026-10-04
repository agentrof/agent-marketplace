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

Document references other than `experience_refs` are quoted, vault-absolute
wikilinks. A criterion link targets its approved owning note and uses the BA
registry-qualified identity as its alias; an Experience ref is a qualified
exact revision:

```yaml
criterion_refs:
  - "[[business-analysis/accounts/acceptance/account-access-acceptance|accounts:AC-ACC-001]]"
experience_refs:
  - "checkout:JRN-001@r1"
uses_design:
  - "[[design-system/MASTER|Product design master]]"
```

The upstream note must exist, be approved and not be superseded. Bare tokens
such as `AC-ACC-001` are not links and do not satisfy coverage.

`experience_refs` are the exception to the wikilink form: each is a qualified
exact living record revision. It must resolve through a process receipt pinned
at backlog scope for the same pinned `application@rN`. The application and process receipts are backlog
input bindings, not values repeated in each story. A newer approved application
receipt makes that binding non-current even if this story's record revision and
owning process receipt did not change.

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
Delivery Sequencing
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

Authored titles and matching H1s are direct, natural graph labels in the
configured output language. Stable type keys, paths, IDs, registry JSON and
disposable generated-view labels remain English machine vocabulary.

## Review findings

Severity rates what a reviewer finding would cause if Delivery built and
tested the package exactly as written. The independent reviewer assigns it
from evidence and never inflates it to look thorough or deflates it to reach
a verdict. The Product Owner chooses each disposition but never changes a
returned severity. Compiler and vault-gate errors are not rated; every one
blocks until it is fixed.

| severity | blocks approval | the package as written would |
|---|---|---|
| `critical` | yes | contradict an approved source, or drop or weaken approved scope or protection |
| `major` | yes | be unverifiable, unexecutable or unowned, order work wrongly, or let careful readers build or test different behavior |
| `minor` | no | still yield the same behavior, verification, ownership and sequencing |

Critical findings include a criterion, scenario or scope statement that
contradicts its approved BA criterion or rule, accepted Solution decision,
Design System, Experience record, operation contract or Requirement impact
matrix; an approved criterion or rule dropped or narrowed without a recorded
deferral; and a missing or weakened authorization, privacy, data-integrity or
irreversible-action obligation.

Major findings include an unverifiable or unproven criterion or rule, where no
scenario can fail it, a Then observes nothing or a coverage-class disposition
is wrong; a Given/When/Then that cannot be arranged in its target, or an
automation target that cannot exercise it; a broken, missing, reversed or
unjustified relation or dependency; an obligation with no owner or a listed
role without a concrete responsibility; and a story that cannot be delivered
and demonstrated as one vertical slice.

Minor findings are wording, clarity, redundancy, formatting and title style,
and imprecision that cannot mislead. Imprecision that could lead a careful
reader to build or test different behavior is major, never minor. Findings
that share one root cause are one finding at the severity of that cause.

The review note's `verdict` field records the reader's returned verdict,
`approved` or `changes_requested`, in the same write as its `Verdict` section;
atomic approval reads only this field. `approved` never stands under status
`changes_requested`: when the re-review approves, status returns to `draft`,
and only approval sets `approved`. The compiler refuses a concluded `Verdict`
section with an empty `verdict`, any other value, and that contradiction.

Only an open critical or major finding keeps a review at `changes_requested`.
The Product Owner closes each one with a fix or with cited evidence that
disproves it, and the re-review confirms either. A minor finding never blocks
approval and never starts another review round. Fix it only in a writer pass
that already carries a blocking fix, whose re-review reads all changed text;
otherwise leave the reviewed text unchanged and record the finding in the
review note's optional `Accepted Minor Findings` section, placed before
`Verdict`:

```markdown
## Accepted Minor Findings

| finding | owner_role | reason | revisit_trigger |
|---|---|---|---|
| [[backlog/epics/identity/stories/sign-in/story\|ST-002]] Scope states the lockout rule twice in different words. | product_owner | Both sentences state one rule, so behavior and verification are unchanged. | Revisit at the next revision of ST-002. |
```

`finding` states the minor finding and cites the affected vault note with an
escaped-table wikilink. `owner_role` is `product_owner`, `qa_engineer` or
`business_analyst`, the backlog authoring role that follows it up. `reason`
says why the text is safe to accept as written, and `revisit_trigger` names
the event that reopens it. The compiler validates every row whenever the
section is present; a review without accepted minor findings omits it. The
section never holds a critical or major finding, which closes only through a
confirmed fix or disproof.

A re-review after a blocking fix or disproof reads the regenerated manifest
together with those findings, any cited evidence and the changed paths. It
confirms that each finding is closed and reviews the changed text with its
dependency context. It does not re-audit unchanged text that an earlier pass
for the same review note already reviewed. A new critical or major finding
continues the loop; a new minor finding follows the record rule above.
