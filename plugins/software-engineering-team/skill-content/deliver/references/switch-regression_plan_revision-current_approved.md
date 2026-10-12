# Current approved regression plans

The Process Policy selects this value. At process switch `regression_plan_revision` `current_approved`, pre-handoff
regression selection uses each earlier story's Test Plan from the complete
committed backlog approval in the exact candidate. The integrated Item and its
acceptance evidence are never rewritten. Missing, draft, stale or ambiguous
current approval refuses selection before a command starts.

Inspect `plan_revision` for the closed and current source hashes, the scenario
delta and current execution metadata. The diagnostic adapter receives those
records in `regression_plans` through its existing selection file. The run and
its evidence hash retain the receipt; changing the approval changes the
candidate tree and requires a new run. No target is silently dropped because
its current plan cannot be read.

A current revision can intentionally change assertions only through the normal
backlog review and approval. Review the recorded delta for lost coverage. A
failing newly selected target must be repaired before freeze; this value does
not waive the pre-handoff gate or authorize changing an approved contract.

Before approving a plan with newly bound unit targets, run
`delivery_verification.py --worktree <project> rebind-unit-run --delivery DLV-###`.
The runner deduplicates new unit targets, uses the approved diagnostic adapter
or full suite, and records the exact source tree, selection, command and raw
output. Plan approval refuses missing, stale or red evidence. Metadata-only
revisions require no extra execution.
