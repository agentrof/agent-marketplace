# Per-Item fixed cost in the Delivery Review

These are the instructions of process switch `item_cost_report` at
`per_step`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, the Delivery Review carries no
per-Item cost table. Inside a Delivery read the value with
`process_policy.py value --switch item_cost_report --delivery DLV-###`.

## The table

The coordinator adds a `Fixed cost` section to the Delivery Review with one
row per Item and one column per step: architecture delta, implementation,
code review rounds, QA rounds, evidence records and integration. Each cell is
the wall clock in minutes between the step's recorded start and end, read
from the timestamps the Item's records and verification session already hold,
plus a last column with the Item's total.

- A step the Item did not run is `-`.
- A step whose start or end is not recorded is `missing`, never an estimate.
- The table reports; it changes no gate, no verdict and no evidence.

## Measurement

The project owner measures outside every task; no role acts on it. The table
is the data the defaults of `item_qa_tier` and `item_review_scale` are decided
on across several Deliveries.
