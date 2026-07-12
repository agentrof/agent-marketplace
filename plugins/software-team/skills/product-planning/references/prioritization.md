# Risk-Adjusted Sequencing

Step-by-step ordering for workspace/docs/backlog.md. Two passes: the
dependency pass decides first, the value-and-risk weighing orders whatever
the dependency pass leaves free. No estimate field exists in any artifact,
so all weighing happens in coarse words, never in numbers pretending
precision.

## Step 1: Dependency Pass

1. Read every story's dependency field; confirm the order never places a
   story before one it depends on (cycle and graph-shape rules live in
   the role constitution).
2. Name the critical path, the longest dependency chain, in the backlog
   summary: it is the floor no reordering can beat, and the first place to
   look when the owner asks what to cut.

## Step 2: Walking Skeleton

Order the walking skeleton first: the thinnest slice that crosses every
layer end to end and proves the pieces connect at all. It jumps the queue
even when a richer story carries more user value, because its failure
teaches more, earlier, than any other story's success. Record it in the
priority field: "critical: walking skeleton".

## Step 3: Risk Pass

Among the stories the dependency pass leaves free, order by what a
failure would invalidate:

- First the story whose failure would force the most other stories to
  be re-planned: count its outgoing dependency edges plus the stories
  whose Definition of Done assumes it works.
- De-risk or spike the high-uncertainty story first when its assumptions
  gate others; a surprise there discovered late re-plans the whole tail.
- A risky story that gates nothing waits its turn; risk alone does not
  jump the queue.

## Step 4: Value Pass

Order the remainder by user-visible value per review unit: what an actor
named in the brief can newly do, against roughly how much review the
story costs. The cosmetic tail (polish, copy, layout refinement) goes
last as named stories; it is still on the backlog, never silently
dropped.

## The Weighing, Worked Once

Scoring schemes in the weighted-shortest-job-first lineage inspire this
step, paraphrased into three coarse questions because no estimate field
exists: what does shipping it newly allow, what assumption does it retire,
how much review does it cost.

After the skeleton, suppose three stories are free: WP-02 Decide a
report, WP-06 Foreign-currency expenses, WP-08 Find reports.

| Story | Value if shipped | Risk retired | Size |
|---|---|---|---|
| WP-02 Decide a report | High: closes the brief's core loop (AC-2) | Medium: exercises the report state model | One review unit |
| WP-06 Foreign currency | Medium: one rule (BR-021) | High: the conversion assumption gates WP-03 export totals | One review unit |
| WP-08 Find reports | Medium: approver convenience (AC-4) | Low: assumes nothing unproven | One review unit |

Decision: WP-06 goes second despite lower value, because its conversion
assumption gates the export story; if the assumption fails, WP-03's
Definition of Done changes. WP-02 third, WP-08 fourth.

Write the outcome, not the table, into the backlog: the table is thinking,
the priority field is the record ("high: retires the conversion assumption
that gates WP-03").

## Deferral Discipline

- must/should/deferred is the whole scope vocabulary at the backlog gate
  (the must-should-could scheme is the lineage, paraphrased); both "could"
  and "won't" land as deferred.
- Every deferred item enters the deferred list with the criterion it
  defers and a written reason: "deferred: BR-013 delegation, no absent
  approvers expected before the second release". A deferral without a
  reason is a silent drop and fails the coverage map.
- Revisit the deferred list at every checkpoint: reinstate the item as a
  story, re-affirm its reason, or drop it by the owner's explicit
  decision. A deferred list untouched across two checkpoints is a smell,
  not a steady state.
- Deferral is scope negotiation, not ordering: a story ordered last is
  still promised; a deferred item is not.
