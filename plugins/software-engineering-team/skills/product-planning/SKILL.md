---
name: product-planning
description: Product planning knowledge for the team's product-owner role. Loaded by software-engineering-team flows; not user-facing.
user-invocable: false
---

# Product Planning

Worked methods for grouping an approved brief into epics, slicing it into
stories and ordering them. The role constitution says what a story is;
this skill says how to cut one and where it goes in the queue.

## When to Use

Loaded when producing the backlog import (epics and stories in the PMO
database; workspace/docs/backlog.md is its generated view) from an
approved analysis space (its registry is the criteria source), or
reconciling at a merge checkpoint. An existing solution-design tree
(workspace/docs/solution-design/) is read first: build-buy-integrate
verdicts shape what is sliced versus bought; Transition ordering
constrains story order. Not for analysis (analyst work), not for
technical choices (architect work).

## Hierarchy and Sizing

Three levels, only two authored:
- Epic: a business goal with a one-line goal statement ("customers
  manage their own accounts"); groups stories for reporting, nothing is
  built from an epic directly. Few and broad beats many and thin; a
  one-story epic is a label, not a group.
- Story: the only planning unit. One demonstrable capability, one review
  unit, one work order, worked by several roles together (analysis fed
  in, architecture delta, implementation, review, verification). Carries
  scope, what it excludes, Definition of Ready, Definition of Done.
- Task: NEVER authored here. The work order generates task rows from
  its own steps and spawns; writing "backend task, frontend task" into
  the backlog duplicates the flow and drifts from it.
- The broad-story tripwire: a story named for a module or a screen
  family ("quotes module", "order viewing") is an epic in disguise; it
  can go anywhere and never finishes. Recut until the demo sentence is
  one user-observable behavior with a named criterion.
- The micro-story tripwire: a story whose whole scope is one role's few
  edits (rename a field, restyle a button) is atomic work for the
  request entry, or a task the work order records itself.

## Slicing Rules

- Slice vertically only: every story crosses all the layers it needs
  to deliver ONE outside-observable capability. A demo sentence starting
  "the code now has" means re-slice.
- Split with the named patterns, in order: by workflow step, by
  business-rule variation, by data variation, by interface subset,
  spike as last resort.
- Gate every slice on the two checks that bite hardest here:
  - Independent: the Definition of Done is verifiable with the story's
    listed dependencies merged and nothing else. A slice only verifiable
    after a LATER slice is mis-cut; re-slice or fix the dependency field.
  - Testable: the Definition of Done names at least one analysis
    criterion and the observable behavior that proves it.
- DON'T create horizontal layer stories ("the schema", "all endpoints"):
  nothing observable ships and the dependency graph serializes.
- DON'T create setup-or-plumbing stories without an observable criterion;
  fold scaffolding into the first story that needs it.
- DON'T accept a story whose Definition of Done cites no analysis id
  (AC or BR): too small (merge it) or invented scope (raise it in open
  questions).

## Dependency Authoring

- Each dependency is an {item, reason} edge, real only when the story
  CONSUMES the target's output; the reason names that need, never the
  ordering. Cycles are rejected at import.
- Edges are the parallelization contract: an unnecessary edge serializes
  concurrent work orders, a missing one starts a story too early. A
  shared contract is not a dependency; mark SHARES with its name.

## DoD Items

- The dod field summarizes; dod_items is its checkable decomposition:
  ONE verifiable property per item, pass/fail without interpretation,
  each tracing to an analysis criterion; exempt from brevity, as many
  items as the story has properties.

## Ordering Method

Two passes; the second never overrides the first.
1. Dependency order: dependency fields decide before anything else; name
   the critical path in the backlog summary.
2. Risk-adjusted value, over the stories dependency order leaves free:
   - Walking skeleton first: the thinnest end-to-end slice through every
     layer, even when a richer story carries more user value.
   - Then the story whose failure would invalidate the most others;
     de-risk or spike a high-uncertainty story first when its
     assumptions gate other stories.
   - Then highest user-visible value per review unit.
   - Cosmetic tail (polish, copy, layout refinement) last, as named
     stories, never padding inside earlier ones.

- The priority field carries the reason ("high: unblocks WP-04"), never
  a bare rank; tiers are critical/high/medium/low, import-enforced.
- The quality ledger (workspace/docs/quality-ledger.md, a generated
  view) is an ordering input: read its tail before planning; a recurring
  finding category is risk evidence (sequence the next story touching
  that area earlier, or mint an analysis rule), and rising
  rounds-to-green on a module argues for smaller slices there.
- At the backlog gate, negotiate scope in plain must/should/deferred
  language; every "deferred" lands on the deferred list with a written
  reason, never a silent drop.
- Method transparency: the backlog summary names the ordering method
  applied (dependency order, then risk-adjusted value) and the top three
  ordering decisions with one-line rationale each: the gate reviews
  reasoning, not a bare list.

## References

- [slicing-patterns](references/slicing-patterns.md): each split pattern with a worked before/after, the size tests, merge rules, anti-pattern gallery. Read when a story fails a size test or resists vertical slicing.
- [structured-records](references/structured-records.md): dependency-edge rules with reasons, the SHARES definition with a worked example, DoD item authoring rules, both anti-pattern galleries. Read when authoring depends_on or dod_items.
- [prioritization](references/prioritization.md): risk-adjusted sequencing step by step, one worked value/risk/size weighing, deferral discipline. Read when ordering the backlog or defending the order at the gate.
- [flow-metrics](references/flow-metrics.md): cadence, throughput and cycle-time concepts, explicitly not ordering inputs. Read when the owner asks for schedule forecasting.
