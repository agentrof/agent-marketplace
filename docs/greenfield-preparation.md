# Greenfield Preparation

Greenfield product preparation is a sequence of owner-gated decisions, not an
autonomous delivery run. Large analysis and solution work may take many
sessions. Each stage closes its own evidence before the next entry starts.

```text
setup
-> business-analysis
-> solution-design
-> design-system
-> experience-design
-> backlog-plan
-> STOP
-> deliver or delivery-lanes
```

`preparation_check.py` is the routing authority. An agent does not infer the
next stage from conversation memory. The check reads project origin and the
approved, current compiler evidence for every stage.

## Why delivery stops before execution

Business Analysis defines the product truth and criteria. Solution Design
chooses the landscape. Design System defines visual rules. Experience Design
projects approved analysis spaces and domains into release journeys, bounded
flow sets, screens, states, transitions and approved preview packages.

Only then does `backlog-plan` create the program and release baseline. It maps
every story to qualified criteria, solution and budget records, exact
Experience Design revisions, owners, dependencies and structured readiness and
completion evidence. Mechanical compilation, independent review, domain gates,
cross-release reconciliation and the program gate precede one atomic PMO
apply.

Backlog approval is not release activation. The entry stops after apply so the
owner can review the complete baseline and explicitly start `deliver` or
`delivery-lanes`.

## Existing-project feature delivery

An existing project uses `deliver`. The entry runs scoped Business Analysis
and solution-impact checks, re-enters Solution Design only when the landscape
changes, creates an Experience Design delta only when behavior changes, and
uses the same backlog planning flow in feature mode. Its execution set contains
only the feature stories and owner-approved unfinished transitive
prerequisites. Active and completed story contracts remain frozen.

## Durable and transient surfaces

- `workspace/docs/experience-design/` is tracked product knowledge organized
  by program, release and projected analysis scope.
- `workspace/experience-design-work/` holds transient candidates.
- `workspace/planning/` holds transient backlog plan drafts.
- `workspace/sketches/` remains exploratory and never becomes a release
  baseline without explicit promotion and compiler verification.

Setup creates only the empty top-level surfaces. The owning compilers create
program, release, space and domain folders when real content is born. Setup
asks for `project_origin`; it never guesses. A keyed or previously contracted
project uses Agent Marketplace Upgrade, which preserves user content, adds an
`unclassified` origin for explicit owner classification, migrates legacy
backlogs without inventing approvals and requires a fresh session.

## Enforcement model

Hooks give immediate path and machine-field feedback. Experience and backlog
compilers prove identity, closure, coverage, freshness, inheritance, ordering
and exact hashes at gates. PMO enforces claims, append-only decisions, active
release boundaries and transactional apply. Mechanical findings cannot be
waived; user gates remain the authority for product judgment.
