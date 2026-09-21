# Delivery Execution Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/deliver DLV-###` resumes from tracked Delivery files and verified remote
evidence. It starts or resumes one Item only when its exact plan, target,
predecessor, Fence and global slot checks pass. Product and test changes stay
on the Item worktree; Integration accepts only reviewed, verified Item
handoffs and compiler-owned projections.

Activation writes an ignored pending writer receipt before the atomic Item,
Slot, Integration and Fence transaction. The receipt is promoted only after
both Item and Slot refs equal the candidate OID, then the coordinator
materializes the detached Item worktree from that exact OID. A missing receipt
does not change remote semantic status, but it denies local writer readiness
until the exact remote activation is re-verified or explicitly taken over.
Takeover is an explicit host-loss decision; it reuses the existing Item and
Slot refs, elects a new writer epoch under exact leases and never allocates a
second Slot.

After implementation, only the current Item's initialized Code Review and
Verification reports may have uncommitted authoring changes. Evidence approval
derives both reviewed and verified commits from that worktree's real `HEAD`;
it never accepts caller-supplied commit text. The only permitted uncommitted
files after approval are that Item's `code-review.md` and `verification.md`.
Approval and publication reject tracked entries with `assume-unchanged` or
`skip-worktree` flags, without modifying the index.
`push-item` attaches those records to the committed product/test tip as one
`item-evidence-v1` child and advances Item plus Slot together. It rejects a
product commit that edits Delivery control files, except the current required
Architecture Item's compiler-stamped delta hash and refreshed source hash.
That exception preserves every other Item byte and control path, and requires
the exact committed, sealed delta to remain within approved architecture claims.
`integrate-item` reads the
evidence from the remote Item ref and requires that its exact direct parent is
the reviewed and verified product/test tip.

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. The Git coordinator first publishes the approved Review, then
publishes one durable PR-creation intent and records the provider URL as its
exact descendant. Provider create/merge calls are adapter-owned and must
requery the intent and reviewed Integration head before any external mutation.
Before Item work starts, `/deliver DLV-###` may run the internal
`refresh-target` coordinator. A disjoint target advance becomes one
`target-refresh-v1` Integration child. Compiler-owned map and relation
projections are regenerated from the combined candidate and preserve existing
approvals. Claimed-path overlaps and changed pinned semantic sources are
rejected without ref mutation, pending explicit source or plan revision.
Authored merge conflicts remain unresolved. No stale target grants a Slot or
worktree. `reopen-item` reactivates a sealed Item on the Integration that
absorbed it, so a target refreshed after integration never strands the Item.
Failed checks, target drift, review changes and process loss become explicit
resumable states. Release Management is intentionally not part of this flow.
