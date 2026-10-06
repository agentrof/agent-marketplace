# Lane table for parallel lanes

These are the instructions of process switch `lane_table` at `recorded`. A
task binds this file only when the project's Process Policy selects that
value; at the default, `off`, the coordinator tracks its lanes in its own
context. Inside a Delivery read the value with `process_policy.py value
--switch lane_table --delivery DLV-###`.

A session restarted after a crash or a context reset otherwise has no record
of which lanes already finished, and relaunches them.

## Rules

1. Before it launches anything, a coordinator that runs parallel lanes reads
   `lane_table.py pending --run <run>`; `<run>` is the Delivery id or another
   stable name of the run. A lane listed `finished` is never launched again;
   its `note` names where its result is.
2. Before it launches a lane, it records `lane_table.py start --run <run>
   --lane <lane> --role <role> --workdir <dir> [--branch <branch>]`. Start
   refuses a lane already finished.
3. When the lane returns, it records `lane_table.py finish --run <run> --lane
   <lane> --state finished|failed --note <where the result is>`.
4. A lane listed `running` after a restart has no live agent: resume it from
   its branch or working directory, never from scratch, unless they hold
   nothing.

The table lives at `.agentrof/agent-marketplace/.runtime/lanes/<run>.json`. It
is scratch, never a durable record; the Delivery's own records stay the truth.
