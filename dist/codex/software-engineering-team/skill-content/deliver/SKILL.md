---
name: deliver
description: Delivery entry placeholder that routes to the later file-first execution contract after an approved backlog.
exposure: entry
---

# Deliver

## When to Use

- An approved project backlog exists and the user explicitly asks to start
  delivery.

## Procedure

1. Run `preparation_check.py status` and require an approved backlog.
2. Read the target story and sibling `test-plan.md`.
3. Stop with a named note that delivery execution and release gates are a
   separate follow-up scope. Do not write execution state.
