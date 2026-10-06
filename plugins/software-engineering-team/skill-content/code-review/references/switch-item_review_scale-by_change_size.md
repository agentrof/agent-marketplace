# Code review scaled to the Item's change

These are the instructions of process switch `item_review_scale` at
`by_change_size`. A task binds this file only when the project's Process
Policy selects that value; at the default, `fixed`, every Item's code review
runs the reader set switch `code_review_panel` selects. Inside a Delivery read
the value and its limits with `process_policy.py value --switch
item_review_scale --delivery DLV-###`; the Delivery pins the policy it runs
under.

## Measuring the change

After the freeze, the coordinator measures the frozen change against its base
with each measure `code-review/data/change-size-measures.json` declares, and
records the counts in the code review brief.

## Choosing the readers

- When every measure the owner set a limit for is at or under its limit, the
  official code reviewer reads the Item alone, as at `code_review_panel`
  `single_reader`.
- When any measure exceeds its limit, the reader set `code_review_panel`
  selects reads, as today.
- A measure the owner set no limit for never makes a change small. With no
  limit set, every Item keeps today's reader set.

The official code reviewer always reads, with its full review passes and the
same severity rules; this switch only decides whether the panel joins it. A
repair round reads with the reader set the Item's first round chose.

## Measurement

The project owner measures outside every task; no role acts on it. Record per
Item its counts, the reader set, the code review wall clock and every valid
critical or major finding later found in an Item the official reviewer read
alone.
