# Step Baseline

These are the instructions of process switch `step_baseline` at `median`.
A task binds this file and `skill-content/configure/data/step-baseline.json`
only when the project's Process Policy selects that value; at the default,
`off`, a step is compared only with its fixed `step_budgets` maximum. Every
task of the owning flow binds it, and only the orchestrating entry acts on it.

A baseline only reports: it never blocks, fails, shortens or skips a step,
and a verification command is never stopped to stay under one.

1. Time is measured only while switch `step_timing` is `recorded`. A step's
   baseline is the median of the last `runs` finished spans of the same step
   name, in any run, that ended before it started; it applies once
   `min_samples` exist. The step is over when it passes the larger of its
   `step_budgets` maximum, if one is in force, and `ratio_percent` of its
   baseline.
2. `delivery_verification.py` times its own QA steps in the Delivery's timing
   run, named by the Delivery id: each live run as `live_run:<group>`, each
   live step the live progress adapter reports as `live_step:<test id>` and
   each rehearsal command as `qa_lifecycle_gate:<step>`, each watching the
   live progress record. Time freeze, a pre-handoff run and an owner-run or
   manual scenario with `step_timing.py start --step <key>:<name> --budget
   <key>`, the keys `freeze`, `pre_handoff` and `manual_scenario`.
3. While a long QA command runs, run `step_timing.py overruns --run
   <delivery> --delivery <delivery>` each time a `wait` returns. Report each
   overrun to the owner at once: the step, its elapsed minutes, its baseline,
   its threshold and its `doing`, the live progress state and the last output
   line. A finished run's record carries its own `step_overrun`.
4. An overrun is a prompt to look, never a verdict on the product: read the
   live progress and output before naming a cause.
