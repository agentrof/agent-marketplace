---
name: deliver
description: Resume one exact Delivery, execute its approved Items and close one Delivery Review.
exposure: entry
---

# Deliver

## When to Use

- Use with an exact `DLV-###` after its Execution Plan and Item claims are
  published.
- Use to resume implementation, integrate Items and complete the one Delivery
  Review and PR handoff.

## Invocation

Use `/deliver DLV-###` for the exact Delivery and `/deliver DLV-### status`
for its derived semantic state. An ID is required; `start-item` and other Git
verbs are internal coordinator operations and are not public entry syntax.

## Boundary

Read the approved `delivery.md`, `execution-plan.md`, Item records, code
reviews and verification records. The execution flow owns resumable Item
work, serialized integration, one aggregate Delivery Review and one final PR.
Release management is deliberately out of scope. No command may infer a
status from a branch name alone; the compiler and verified remote evidence are
the source of semantic truth.
