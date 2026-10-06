# Test Cost Budget

These are the instructions of process switch `test_cost_budget` at
`flag_serial_rows`. Every task of Backlog Planning and Delivery Planning binds
this file when the project's Process Policy selects that value; at the
default, `off`, nothing is flagged and every step runs as its flow and role
files describe.

One table-driven test can outlast the rest of a gate, and splitting it is a
test design decision that is cheapest while the scenario is planned. At this
value Backlog Planning asks what each scenario costs to run before its epic
review.

## The two scenario fields

A Test Plan scenario may state how its automation target runs its rows:

```markdown
- automation: required
- automation_target: tests/api/test_limits.py::test_limit_table
- rows: 260
- row_split: sharded
```

- `rows`: a positive integer, the number of table rows the automation target
  runs.
- `row_split`: `serial`, all rows in one run after another; `sharded`, rows
  split into independent groups that can run in parallel; or `grouped`, rows
  that share one setup instead of one each.

Both are optional. `backlog_compile.py check` refuses a `rows` value that is no
positive integer and a `row_split` outside the three values whenever a scenario
states them, at every value of this switch. Declare `rows` for every
automation-required scenario whose target runs a table of rows, and keep it
current when the table grows: a stale count misses the test.

## The limit and the flag

The owner sets `serial_rows`, the most rows a scenario may run serially, in
the Process Policy's Parameters table through `/configure process`; the
package recommends none, and `data/test-cost-limits.json` declares the
parameter. `backlog_compile.py check --json` then adds
`test_cost`, which lists under `serial_row_scenarios` each
automation-required scenario whose `rows` exceed the limit while its
`row_split` is `serial` or absent, with its story, automation target, rows and
split, and its `level` while `test_levels` is `declared`. A scenario at or
below the limit, or one whose rows are `sharded` or `grouped`, is not listed.
The epic and root review manifests carry the same list for the stories they
read, and `delivery_compile.py init` for the selected stories.

The flag is advisory: it never fails a check, never blocks a review or an
approval and never rewrites a scenario.

## Before the epic review

For every flagged scenario, QA proposes a split before the first epic review
manifest, and the Product Owner persists it:

- Rows sharded into independent groups that can run in parallel, each group
  free of any order dependence on another, recorded as `row_split: sharded`.
- Rows grouped under one shared setup, recorded as `row_split: grouped`.

A split changes how the target runs its rows, never what the scenario
verifies: its Given, When and Then and its source refs stay as they are. When
no split fits, ask the owner one choice-gate question per flagged scenario,
with the split as the recommended option. A scenario the owner keeps serial
stays flagged; record the owner's decision and its reason in the epic review
note, where the reviewer reads it.

The backlog reviewer reads the list as a given fact and raises a finding for
a flagged scenario that has neither a split nor the owner's recorded reason.

## Measurement

The project owner measures outside every task; no role acts on it. For each
backlog revision, record the scenarios flagged, split and kept serial with a
reason, and for each Delivery the wall clock of the longest test in QA's final
gate and of the gate itself. The registry's promotion rule judges them over at
least 3 backlog revisions and 3 Deliveries.
