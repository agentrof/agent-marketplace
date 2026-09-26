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
An Item whose writer converged it on a refreshed Integration may also carry
that Integration commit's Delivery controls byte for byte and record the commit
as its `integration_base_commit`. The commit must lie on the Integration's own
line after the Item's previous base and be contained in the product tip, and
the Item record keeps the commit's plan-owned fields with only its own status,
stamp and base.
`integrate-item` reads the
evidence from the remote Item ref and requires that its exact direct parent is
the reviewed and verified product/test tip.

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. The Review is authored as a draft `delivery-review.md` in the
Delivery package before `approve-review`. Approval keeps every authored
section, fills only the sections left empty and the navigation the compiler
owns, and binds that content in its approval hash; the PR body is that Review.
The Git coordinator first publishes the approved Review, then
publishes one durable PR-creation intent and records the provider URL as its
exact descendant. That record is the PR head and moves the reviewed Delivery
to `awaiting_merge`. The compiler reports `merged` only when the current
branch reaches, on any path, a two-parent merge of that exact head that
carries no `Agentrof-Record` trailer, also for a PR recorded while the
Delivery stayed in `review`. Coordinator commits, such as the reopen commit,
never count; a manual merge of the Integration branch into another branch
would. A merged Delivery keeps its pinned sources, and the Delivery map keeps
the tracked status.
Provider create/merge calls are adapter-owned and must
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
