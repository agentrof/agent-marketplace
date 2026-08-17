# Delivery Execution Flow

Spawn template: paste `{{constitution}}` into every role prompt.

`/deliver DLV-###` resumes from tracked Delivery files and verified remote
evidence. It starts or resumes one Item only when its exact plan, target,
predecessor, Fence and global slot checks pass. Product and test changes stay
on the Item worktree; Integration accepts only reviewed, verified Item
handoffs and compiler-owned projections.

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. Failed checks, target drift, review changes and process loss become
explicit resumable states. Release Management is intentionally not part of
this flow.
