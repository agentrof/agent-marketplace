# Step Timing

These are the instructions of process switch `step_timing` at `recorded`. A
task binds this file only when the project's Process Policy selects that
value; at the default, `off`, `step_timing.py start` and `end` write nothing.
Every task of an owning flow binds it, and only the orchestrating entry acts
on it: a role never times another agent.

1. Before each flow step run `step_timing.py start --run <run> --step <step>`,
   with `--budget <id>` when switch `step_budgets` names a maximum for it,
   and after the step `step_timing.py end --run <run> --span <span>`.
2. Record each role spawn with `step_timing.py start --run <run> --step <step>
   --kind spawn --parent <step span> --role <role> --phase
   reading|writing|review|re_review|waiting`, and end it when the role
   returns.
3. An `end` that returns `overrun` is reported to the owner at once with its
   breakdown, its largest contributor and the lever that applies.
4. End the run with `step_timing.py report --run <run> --write`.

Times come off the system clock through the script; never type one. A span
whose start or end is not recorded is `missing`, never an estimate. The
record is measurement data, never review evidence; never delete or rewrite a
row without the owner's approval.
