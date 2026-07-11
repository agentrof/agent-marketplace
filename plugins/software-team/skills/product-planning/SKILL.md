---
name: product-planning
description: Product planning knowledge for the team's product-owner role. Loaded by software-team flows; not user-facing.
user-invocable: false
---

# Product Planning

Worked methods for slicing an approved brief into work packages and
ordering them. The role constitution says what a package is; this skill
says how to cut one and where it goes in the queue.

## When to Use

Loaded when producing workspace/docs/backlog.md from an approved brief, or
reconciling it at a merge checkpoint. Not for writing the brief (analyst
work) and not for technical choices (architect work).

## Slicing Rules

- Slice vertically only: every package crosses all the layers it needs to
  deliver ONE capability observable from outside. If the demo sentence for
  a package starts with "the code now has", re-slice.
- Split with the named patterns, tried in this order: by workflow step, by
  business-rule variation, by data variation, by interface subset, spike
  as last resort.
- Gate every slice on the two checks that bite hardest here:
  - Independent: the Definition of Done is verifiable with the package's
    listed dependencies merged and nothing else. A slice only verifiable
    after a LATER slice is mis-cut; re-slice or fix the dependency field.
  - Testable: the Definition of Done names at least one brief criterion
    and the observable behavior that proves it.
- DON'T create horizontal layer packages ("the schema", "all endpoints"):
  nothing observable ships and the dependency graph serializes.
- DON'T create setup-or-plumbing packages without an observable criterion;
  fold scaffolding into the first package that needs it.
- DON'T accept a package whose Definition of Done cites no brief criterion
  (acceptance criterion or BR-###): it is either too small (merge it) or
  invented scope (raise it in open questions).

## Ordering Method

Two passes; the second never overrides the first.

1. Dependency order: dependency fields decide before anything else; name
   the critical path in the backlog summary.
2. Risk-adjusted value, over the packages dependency order leaves free:
   - Walking skeleton first: the thinnest end-to-end slice through every
     layer, even when a richer package carries more user value.
   - Then the package whose failure would invalidate the most others;
     de-risk or spike a high-uncertainty package first when its
     assumptions gate other packages.
   - Then highest user-visible value per review unit.
   - Cosmetic tail (polish, copy, layout refinement) last, as named
     packages, never as padding inside earlier ones.

- The priority field carries the reason, not just a rank: "P1: unblocks
  WP-04 and WP-05", never a bare "P1".
- At the backlog gate, negotiate scope in plain must/should/deferred
  language; every "deferred" lands on the deferred list with a written
  reason, never as a silent drop.

## Method Transparency

- The backlog summary names the ordering method applied (dependency order,
  then risk-adjusted value) and the top three ordering decisions with a
  one-line rationale each, so the gate reviews reasoning, not a bare list.

## References

- [slicing-patterns](references/slicing-patterns.md): each split pattern with a worked before/after, the size tests, merge rules, anti-pattern gallery. Read when a package fails a size test or resists vertical slicing.
- [prioritization](references/prioritization.md): risk-adjusted sequencing step by step, one worked value/risk/size weighing, deferral discipline. Read when ordering the backlog or defending the order at the gate.
- [flow-metrics](references/flow-metrics.md): cadence, throughput and cycle-time concepts, explicitly not ordering inputs. Read when the owner asks for schedule forecasting.
