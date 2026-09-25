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

Scope approval checks the selection's upstream bindings before the handoff.
It refuses a selected Story whose implemented Requirement does not route to
backlog; rebind that Requirement through `/requirement REQ-###` first. When a
selected Story cites `experience_refs`, the backlog must bind the globally
current `application@rN` through its compiler-owned `input_bindings` (manual
mode, or a requirement-mode backlog that carries them) or its root
Requirement's Experience Stage Results; otherwise approval names the current
receipt and refuses until a manual-mode backlog revision pins it or a
Requirement's Experience stage binds it. A reserved Delivery keeps verifying
its pinned inputs historically.
