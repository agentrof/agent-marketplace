---
name: product-planning
description: Product-owner methods for grouping approved knowledge into a nested Markdown backlog and designing complete story test plans.
exposure: internal
---

# Product Planning

Use this skill after Requirement Flow approves the required intake evidence and
impact matrix. The project vault is canonical:
`workspace/docs/backlog/` contains one root backlog, one folder per epic, one
review folder per epic, one story folder per capability and one test plan per
story. Read the `obsidian-vault` skill before reading or writing this tree; its
policy defines paths, metadata, title rules and graph colors.

## When to Use

- Loaded by the Product Owner, Business Analyst and QA Engineer during
  `backlog-plan`.
- Loaded by the Backlog Reviewer when challenging a complete backlog package.

## Hierarchy

- Backlog: the single project planning boundary.
- Epic: a business goal with a measurable outcome, never a work unit.
- Story: one vertical, demonstrable capability and one review unit.
- Test plan: the planned scenario design for that story.

Approved living Experience packages remain the source for journeys, screens
and process boundaries. The globally current `application@rN` identifies their
author-owned prototype snapshot. Stories link to exact child references that
resolve through the pinned process receipt.

## Story contract

Every story has exactly one accountable `owner_role`. Optional `supporting_roles`
name other team roles that make a concrete implementation contribution. The owner cannot repeat in the supporting list, supporting roles
cannot repeat, and every listed role has a concrete responsibility in the
body. These fields contain stable team role identifiers, never people, host
tasks, agent sessions or execution IDs.

Every story has `work_kind: feature|defect|technical`. Stories carry approved
BA, Solution Design, Design System and Experience Design references whenever
the Requirement impact matrix says those outputs constrain the story.
`defect` and `technical` stories may use `related_to` approved or accepted
source, issue or decision evidence when those feature-stage outputs are not
applicable.

Coverage is scoped to explicitly selected story and review sources.
`analysis_scopes` on the root backlog deliberately expands it to every
active approved AC and BR in a named BA space or domain. It is never inferred
from unrelated historical registries.

The required body sections are User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. A dependency
is a vault-absolute story link plus a reason, never list order. A deferred
criterion records an owner and revisit trigger.

## Traceability

Use vault-absolute wikilinks in `criterion_refs`, `derives_from`, `depends_on`,
`uses_design` and `constrained_by`. Criterion and rule links target exact stable
headings in approved notes. `experience_refs` use qualified exact revisions
such as `checkout:SCR-001@r2`; each must exist in a pinned current process
receipt for the pinned application. Bare
unqualified IDs or invented shorthand do not satisfy coverage.

## Scenario coverage

The Business Analyst and QA Engineer co-author the sibling `test-plan.md`.
Each scenario has a stable `<story-id>-TS-###` heading, category, target,
automation (`required` or `manual`), source links and Given/When/Then.
Automation-required scenarios name an `automation_target`; the target may be planned until delivery. Every scenario cites at least one declared planning source. Feature scenarios cite story criteria; defect and technical scenarios
cite story criteria and/or approved `related_to` evidence. Every declared
planning source appears in at least one scenario. A structured `Coverage
Classes` table contains exactly empty,
boundary, invalid-input, authorization, duplicate-concurrent, failure and
adjacent-regression. A class is `covered` by existing scenario IDs or
`not_applicable` with a concrete reason and no scenario IDs. Covered rows
classify the exact scenario set; scenarios may appear in multiple rows but none
may be orphaned.

Backlog Planning never records suite output, test results, story completion
or release readiness.

## Review

The epic review derives from its epic and verifies exactly every child story
and test plan. It covers scope, slicing, criteria, test design, dependencies,
role ownership, findings and verdict. The root review derives from the backlog
and relates to exactly every epic. It covers cross-epic overlap, dependency
direction, cycles, delivery sequencing, shared contracts, deferred criteria,
global coverage, findings and verdict.

The latest root review's `Deferred Criteria` table records exactly four fields:
`criterion_ref`, `owner_role`, `reason`, `revisit_trigger`. The criterion is a
vault-absolute aliased wikilink with its table pipe escaped. Every active AC
and BR selected by the Requirement impact matrix occurs in story
`criterion_refs` or that table, never both. An explicit root
`analysis_scopes` declaration expands the same equality to a complete named
scope. Unknown, overlapping and uncovered identities fail. Generic review
placeholders are not review evidence.

Run `backlog_compile.py check --render` as the mechanical gate; atomic approval
keeps stories `planned` and hash-stamps the package. Its input bindings include
the globally current application receipt and exact process receipt set. A newer
approved application makes the backlog input non-current even when process
receipts are unchanged. Generated views are disposable.

Authored titles are direct, natural graph labels in the configured output
language. Canonical keys, paths, IDs, registry JSON and generated machine-view
labels remain stable English machine vocabulary.

## References

- [slicing-patterns](references/slicing-patterns.md). Read when a story fails a size test or resists vertical slicing.
- [structured-records](references/structured-records.md). Read when authoring role ownership, dependency edges, references or scenarios.
- [prioritization](references/prioritization.md). Read when ordering stories.
- [living-experience-contract](references/living-experience-contract.md). Read when attaching approved Experience Design references.
- [flow-metrics](references/flow-metrics.md). Read when forecasting cadence; never use it as a scope gate.
