# Step Budgets

These are the instructions of process switch `step_budgets` at `enforced`.
A task binds this file and `skill-content/configure/data/step-budgets.json`
only when the project's Process Policy selects that value; at the default,
`off`, no step is compared with a maximum. Every task of an owning flow binds
it, and only the orchestrating entry acts on it.

A budget is a maximum, never an expectation: a step may finish well under
it, and exceeding it is the reported event. Enforcement is reporting only: a
budget never blocks, fails, shortens or skips a step, and a review is never
cut to stay under one.

1. `process_policy.py value --switch step_budgets` returns each maximum in
   minutes: the owner's parameter where set, else the package target that
   `step-budgets.json` declares and the registry ships as a package limit.
2. Time is measured only while switch `step_timing` is `recorded`; name the
   budget with `--budget <id>` on the step's `step_timing.py start`.
3. When a step exceeds its maximum, report it at once with its reading,
   writing and waiting breakdown, its largest contributor and the lever that
   applies: closure too wide, levels run in sequence, no context pack, or
   missing relations.
4. The run's closing `step_timing.py report` compares every budgeted step
   with its maximum.
