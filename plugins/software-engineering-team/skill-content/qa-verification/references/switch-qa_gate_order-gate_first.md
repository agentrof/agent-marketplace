# QA starts its gate first

These are the instructions of process switch `qa_gate_order` at `gate_first`.
A task binds this file only when the project's Process Policy selects that
value; at the default, `plan_first`, QA plans and maps every check before it
executes anything, as the qa-verification skill says. Inside a Delivery read
the value with `process_policy.py value --switch qa_gate_order --delivery
DLV-###`; the Delivery pins the policy it runs under.

A QA round lasts as long as its test command: the approved suite can run for
hours, while QA's own reading and writing take minutes. At this value QA's work
overlaps the command instead of coming before and after it.

## Order of a round

1. Read the manifest and write every input the round's first test command
   needs before it starts, such as a diagnostic selection or a spot-run
   selection another switch value asks for.
2. Start that command, `run --kind test` in a final round or `run --kind
   diagnostic_test` in a diagnostic one, in the background as the host runs a
   long command, before you plan anything, so that it runs while you work.
   Once it ends, the record it printed holds the evidence hash and environment
   hash the result copies, and `status --run <kind>` prints it again.
3. While it runs, do the work that needs none of its results: derive the
   risk-ordered partitions from the Test Plan, the criteria and the rules; map
   every criterion, rule and planned partition to its tagged tests and write
   the coverage matrix rows with their result cells open; read the candidate
   through `inspect` and `diff` for the right-reason checks; and draft the
   verification record with every result left open. The command holds the
   Item's environment and verification command locks, so start no other `run`
   or `environment` command until it ends.
4. Read the command's output only once the plan and the matrix are written.
   That keeps the intent of the skill's rule that the plan and the matrix come
   before execution: no check is planned, or left out, because of what the run
   showed.
5. Once the draft is written, wait for the command only through the runner's
   `wait --role qa_engineer`, as the flow's wait rule says: a call returns once
   the command has exited, or at the verification policy's `wait_bound_seconds`
   with the command it still waits for, and then you call it again at once, as
   a tool call of its own. Never wait through a sleep, a polling loop or a long
   timeout of your own.
6. When it ends, run the coverage audit on its results, fill the open cells and
   findings, then run the mutation and dependency audit commands and the live
   runtime protocol where the Item needs them, and register the result.

A command that fails early ends the overlap early: read its output at step 4
as soon as the plan and the matrix are written, and run any further command the
round needs only after it.

## Measurement

The project owner measures outside every task; no role acts on it. For each
QA round, record the wall clock from the end of QA's last runner command to the
registration of QA's result, which the Item's verification session holds as the
latest `completed_at` of its command and runtime records and the QA worker's
`completed_at`, and compare it with rounds run at `plan_first`. The registry's
promotion rule judges them over at least 3 Deliveries.
