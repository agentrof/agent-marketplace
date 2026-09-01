# Delivery Planning Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/delivery-plan` is the user-facing scope flow. It selects one exact backlog
story set, checks current Requirement and Definition of Done evidence, renders a
temporary proposal, obtains the Delivery Scope decision and then hands the
approved files to the explicit Git coordinator. No timebox, slot, branch,
worktree or release field belongs in this flow.

The proposal is disposable until reservation. A declined or interrupted
proposal leaves the target checkout, refs and authored vault unchanged. After
reservation, the Delivery ID, goal-derived slug and scope hash are immutable.
