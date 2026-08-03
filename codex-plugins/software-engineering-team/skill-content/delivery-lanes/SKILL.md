---
name: delivery-lanes
description: The integrator surface for parallel delivery; it never delivers a story itself. Proposes which ready stories can start together from dependencies and claims, opens git worktree lanes the user drives through their own request sessions, tracks where gate approvals are pending, and owns every merge checkpoint on the main line.
disable-model-invocation: true
---

# Delivery Lanes

One session per project coordinates many work orders; this is that
session's entry.

## When to Use

- Several ready stories should be delivered in parallel lanes.
- The user asks where lanes, pending approvals or merges stand.
- A lane reports its opened pull request and needs the merge checkpoint.
- A lane's session died and the work needs triage.
- Not for delivering a single story (the request entry) and never from
  inside a lane worktree.

## Procedure

1. Pre-flight: read workspace/config.json (missing, or without a
   project_key: route to the setup entry and stop). Resolve the PMO CLI
   per the develop flow's state contract and run the idempotent init-db.
   Verify this session sits on the PRIMARY checkout: the current branch
   is the default branch AND `git rev-parse --git-dir` equals
   `git rev-parse --git-common-dir`. A linked worktree fails that test:
   refuse, name the primary checkout path, and route lane work to the
   request entry.
2. Load the flow printed by "$RUN" path "$TEAM" flows/delivery-lanes.md
   (dispatcher variables per that state contract) and execute it under
   its full state contract.
3. Gate approvals belong to the owning lane session, never to this one;
   the CLI's worktree binding enforces it, and every approval request is
   answered with the owning lane's worktree path.
