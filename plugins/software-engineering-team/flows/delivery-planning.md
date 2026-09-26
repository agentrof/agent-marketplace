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
backlog; rebind that Requirement through `/requirement REQ-###` first. A
superseded, withdrawn or `resolved_no_change` Requirement cannot be rebound, so
a backlog revision re-traces the Story to a current Requirement, such as the
named successor, or drops it. When a selected Story cites `experience_refs`,
the backlog must bind the globally current `application@rN` in its
compiler-owned `input_bindings`; otherwise approval names that receipt and
refuses until a backlog revision binds it. A manual-mode revision pins it with
`--input-ref`. A requirement-mode revision takes it from the root
Requirement's Experience Stage Results, rebound first through
`/requirement REQ-###`, or, when that Requirement marks Experience
`not_applicable`, from `--input-ref` at `begin-revision`. Until its next
revision, a requirement-mode backlog approved before it carried
`input_bindings` binds through its root Requirement's Experience Stage Results
instead. A reserved Delivery keeps verifying its pinned inputs historically.
