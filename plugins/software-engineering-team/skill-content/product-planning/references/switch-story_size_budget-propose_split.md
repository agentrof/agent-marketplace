# Story Size Budget

These are the instructions of process switch `story_size_budget` at
`propose_split`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, nothing is measured or shown and
every step runs as its flow and role files describe. Where this file and a
flow differ on a step that names switch `story_size_budget`, this file
governs.

## What the budget is

The budget makes the story rule "one vertical, demonstrable capability and
one review unit" checkable without adding a field to any story or capping
anything:

- The backlog compiler derives each measure from what the story and its Test
  Plan already hold. `data/story-size-measures.json` declares the measures
  and how each is derived: `acceptance_criteria`, `test_scenarios`,
  `implementation_roles` and `contract_deltas`.
- The owner sets the limits in the Process Policy's Parameters table through
  `/configure process`. The package sets none, and a measure without a limit
  is reported but never over budget.
- A story is over budget when a measure exceeds its limit. Over budget is
  advisory: it never fails `backlog_compile.py check`, never blocks a review
  or an approval and never rewrites a criterion.
- The one-review-unit rule and the no-estimate rule of
  `references/flow-metrics.md` stand as written. A limit bounds a review
  unit, never time or effort. A count is a proxy: a story within budget can
  still be too big and one over budget can still be one capability, so the
  owner decides each case.

## Reading the measures

Under this value `backlog_compile.py check --json` adds `story_size`: the
limits and, per story, each measure's `value` with its `limit` when set,
`over_budget` with the measures over their limit and `size_exceptions` with
the measures a Size Exceptions row keeps. `over_budget_stories` lists every
story with a measure over budget. These counts are compiler facts; never
recount them by hand.

## Proposing a split

After the stories and Test Plans are authored and `check --json` reports no
source finding, and before any epic review manifest is derived:

1. For each story in `over_budget_stories` with an over-budget measure that
   no Size Exceptions row keeps, the Product Owner proposes a split with
   `references/slicing-patterns.md`: every part is one vertical, demonstrable
   capability that passes the Too-Big and Too-Small tests, and the proposal
   names each part's criteria, scenarios, roles and dependencies.
2. Never merge, reword, drop or compress a criterion or a scenario to fit a
   limit. One criterion per verifiable property still holds, and a compound
   criterion written to fit is a finding.
3. Ask the owner one choice-gate question per over-budget story: split as
   proposed, or keep the story. Recommend the split first unless the story
   still reads as one review unit; then recommend keeping it and give the
   reason. The answer sets direction only: the exact Markdown diff still goes
   to the backlog approval gate.

## Applying an accepted split

1. Create each new story with `backlog_compile.py stub-story`, passing the
   criterion, Experience, design, constraint and evidence references that
   move to it.
2. Move each moving criterion line byte for byte from the source story's
   Acceptance section into the new story's. Move each moving scenario block
   byte for byte into the new story's Test Plan; only its heading changes to
   the new story's `<story-id>-TS-###`, as the compiler requires, and its
   Coverage Classes classification moves with it.
3. Remove the moved `criterion_refs` and scenarios from the source story and
   its Test Plan, so every moved criterion is covered by exactly one of the
   two stories, and replace every stub the new story still holds.
4. Record a dependency between the parts only with its reason. No part may be
   verifiable only after a sibling lands.
5. Run `backlog_compile.py check --json`. Coverage must be exact; a part
   still over budget gets its own proposal.

## Keeping a story

A kept story gets one row per over-budget measure in the `Size Exceptions`
table of its epic's current review note, written by the Product Owner with
that note from the owner's decision:

```markdown
## Size Exceptions

| story | measure | reason |
|---|---|---|
| [[backlog/epics/<epic>/stories/<story>/story\|ST-###]] | acceptance_criteria | Why the story is still one review unit. |
```

`story` links a story of that epic with its id as the alias and the table
pipe escaped, `measure` is a declared measure id and `reason` is concrete.
The compiler validates every row and rejects a repeated story and measure.

## Reviews

The review manifest's `check.story_size` block carries the measures of the
stories in scope as given facts. The reviewer never recounts them and never
raises a finding for a count alone. In its slicing lens it checks that each
split moved its criteria and scenarios verbatim and that each kept story is
still one review unit with a concrete Size Exceptions reason; a kept story
over budget without its row is a minor finding. The epic review note's
Slicing evidence names the limits its review ran under; the note records the
Process Policy's path, revision and source hash of the round, as every review
round does.

## Delivery proposal

At `/delivery-plan`, `delivery_compile.py init` prints `story_size` for the
selected stories. Show each measure, its limit and the measures over budget
in the proposal, read-only: the budget never changes the selection or the
scope decision, which stay the owner's.
