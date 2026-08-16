---
name: product-planning
description: Product-owner methods for grouping approved knowledge into a nested Markdown backlog and designing complete story test plans.
exposure: internal
---

# Product Planning

Use this skill after Business Analysis, Solution Design, Design System and
Experience Design are approved. The project vault is canonical:
`workspace/docs/backlog/` contains one root backlog, one folder per epic, one
review folder per epic, one story folder per capability and one test plan per
story. Read the `obsidian-vault` skill before reading or writing this tree; its
policy defines paths, metadata, designations and graph colors.

## When to Use

- Loaded by the Product Owner, Business Analyst and QA Engineer during
  `backlog-plan`.
- Loaded by the Backlog Reviewer when challenging a complete backlog package.

## Hierarchy

- Backlog: the single project planning boundary.
- Epic: a business goal with a measurable outcome, never a work unit.
- Story: one vertical, demonstrable capability and one review unit.
- Test plan: the planned scenario design for that story.

Approved Experience Design notes remain the source for journeys, screens and
release boundaries. Stories link to them directly.

## Story contract

Every story has exactly one accountable `owner_role`. Optional
`supporting_roles` name other team roles that make a concrete implementation
contribution. The owner cannot repeat in the supporting list, supporting roles
cannot repeat, and every listed role has a concrete responsibility in the
body. These fields contain stable team role identifiers, never people, host
tasks, agent sessions or execution IDs.

The required body sections are User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. A dependency
is a vault-absolute story link plus a reason, never list order. A deferred
criterion records an owner and revisit trigger.

## Traceability

Use vault-absolute wikilinks in `criterion_refs`, `experience_refs`,
`derives_from`, `depends_on`, `uses_design` and `constrained_by`. Criterion and
rule links target exact stable headings in approved notes. Every target must
exist and remain approved; bare aliases or invented shorthand do not satisfy
coverage.

## Scenario coverage

The Business Analyst and QA Engineer co-author the sibling `test-plan.md`.
Each scenario has a stable `<story-id>-TS-###` heading, category, target,
automation (`required` or `manual`), source links and Given/When/Then.
Automation-required scenarios name an `automation_target`; the target may be
planned until delivery. Every mapped criterion and rule appears in at least one
scenario. Empty, boundary, invalid input, authorization, duplicate/concurrent
action, failure and adjacent-regression paths are covered or explicitly marked
inapplicable with a reason.

Backlog preparation never records suite output, test results, story completion
or release readiness.

## Review

The epic review derives from its epic and verifies exactly every child story
and test plan. It covers scope, slicing, criteria, test design, dependencies,
role ownership, findings and verdict. The root review derives from the backlog
and relates to exactly every epic. It covers cross-epic overlap, dependency
direction, cycles, release ordering, shared contracts, deferred criteria,
global coverage, findings and verdict.

Run the packaged `backlog_compile.py check --render` as the mechanical gate.
The atomic approval verb preserves stories as `planned`, stamps the approved
package and records a content hash that becomes stale after any authored
change. Generated views under `_generated/` are disposable.

## References

- [slicing-patterns](references/slicing-patterns.md). Read when a story fails a size test or resists vertical slicing.
- [structured-records](references/structured-records.md). Read when authoring role ownership, dependency edges, references or scenarios.
- [prioritization](references/prioritization.md). Read when ordering stories.
- [program-release-contract](references/program-release-contract.md). Read when attaching approved Experience Design references.
- [flow-metrics](references/flow-metrics.md). Read when forecasting cadence;
  never use it as a scope gate.
