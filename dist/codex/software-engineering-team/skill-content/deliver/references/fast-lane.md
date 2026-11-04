# Tasks and parallel lanes

Read [the delivery flow](../../../flows/fast-lane.md) for the executable sequence.

One owner holds a task's allocated worktree. Split by independently verifiable
outcomes and dependencies. Keep code, tests and affected documentation together.
Several tasks may share immutable inputs and one identified integration proof.
Conflicting writes and stateful environment changes must be sequenced.

The main session owns task intake, worker admission, merge observation and status.
An idle worker or a free model slot is not permission to create more environments.
Use the project's existing proof runner and resource contract. Never acquire an
image, service or paid capacity outside the current authority.

Track request-to-ready wall time for each task and batch makespan for the whole
request. Sum worker effort separately. A faster task that lengthens the batch or
increases escaped defects is not an improvement.
