---
name: delivery-lanes
description: Retired compatibility stub; parallel lane orchestration is not a public entry.
exposure: internal
---

# Retired Delivery Lanes

## When to Use

- Only after the delivery execution contract is explicitly approved.

## Procedure

1. Do not expose this skill as a user entry.
2. Route any legacy invocation to `/delivery-plan`.
3. Write nothing and do not create a lane or worktree record.
