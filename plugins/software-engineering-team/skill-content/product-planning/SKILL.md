---
name: product-planning
description: Program, release and feature backlog planning knowledge for the team's product-owner role. Loaded by software-engineering-team flows; not user-facing.
exposure: internal
---

# Product Planning

Worked methods for grouping an approved brief into epics, slicing it into
stories and ordering them. The role constitution says what a story is;
this skill says how to cut one and where it goes in the queue.

## When to Use

Loaded by `flows/backlog-planning.md` in baseline, replan or feature mode.
The product-owner authors a transient JSON plan; only an approved, exact-hash
`backlog-plan apply` may create or change durable program, release, epic and
story structure in PMO. Approved analysis registries are the criteria source. An existing
solution-design tree (workspace/docs/solution-design/, vault law per
the obsidian-vault skill) is read first: build-buy-integrate
verdicts shape what is sliced versus bought; exact Experience Design release
registries constrain behavior and ordering. Not for analysis, architecture or
release activation.

Read `references/program-release-contract.md` before authoring or reviewing any
baseline, replan or feature plan.

## Hierarchy and Sizing

Four authored planning levels and one runtime level:
- Program: the approved outcome and full product planning boundary. A large
  greenfield effort has one program with multiple ordered releases, not one
  unbounded release.
- Release: an activatable slice with one exact effective Experience Design
  registry hash. Every story belongs to exactly one release. A later release
  may depend on an earlier one; the reverse is invalid.
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
  deliver entry, or a task the work order records itself.

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
- The quality ledger (PMO database rows) is an ordering input: read its
  tail before planning (ledger list --project-key <key> --tail <N>
  --json); a recurring finding category is risk evidence (sequence the
  next story touching that area earlier, or mint an analysis rule), and
  rising rounds-to-green on a module argues for smaller slices there.
- At the backlog gate, negotiate scope in plain must/should/deferred
  language; every "deferred" lands on the deferred list with a written
  reason, never a silent drop.
- Method transparency: the backlog summary names the ordering method
  applied (dependency order, then risk-adjusted value) and the top three
  ordering decisions with one-line rationale each: the gate reviews
  reasoning, not a bare list.

## References

- [program-release-contract](references/program-release-contract.md): mandatory JSON plan identities, qualified refs, ownership, release allocation, feature execution-set and gate contract. Read when starting any planning run.
- [slicing-patterns](references/slicing-patterns.md): each split pattern with a worked before/after, the size tests, merge rules, anti-pattern gallery. Read when a story fails a size test or resists vertical slicing.
- [structured-records](references/structured-records.md): dependency-edge rules with reasons, the SHARES definition with a worked example, DoD item authoring rules, both anti-pattern galleries. Read when authoring depends_on or dod_items.
- [prioritization](references/prioritization.md): risk-adjusted sequencing step by step, one worked value/risk/size weighing, deferral discipline. Read when ordering the backlog or defending the order at the gate.
- [flow-metrics](references/flow-metrics.md): cadence, throughput and cycle-time concepts, explicitly not ordering inputs. Read when the owner asks for schedule forecasting.
