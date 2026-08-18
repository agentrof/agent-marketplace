# Execution Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/execution-plan DLV-###` consumes one scope-approved Delivery. The compiler
stores topology on Item records and renders the Execution Plan as an exact
aggregate. It validates dependencies, cycles, path and contract claims, role
sequence, verification strategy and current source hashes before the user
approves the plan.

Approval is offline. `publish-execution-plan` is the only later network writer;
it creates no Item worktree, slot or product-code branch. Item claims begin only
after the published plan is verified remotely.
