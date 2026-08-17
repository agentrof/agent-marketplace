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

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. The Git coordinator first publishes the approved Review, then
publishes one durable PR-creation intent and records the provider URL as its
exact descendant. Provider create/merge calls are adapter-owned and must
requery the intent and reviewed Integration head before any external mutation.
Failed checks, target drift, review changes and process loss become explicit
resumable states. Release Management is intentionally not part of this flow.
