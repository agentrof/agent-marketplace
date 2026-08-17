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
Cancellation is a deliberate exception inside the same entry. `/deliver
DLV-###` first renders an exact read-only cancellation preview when the user
chooses cancellation. The internal `cancel-delivery` coordinator records the
reason and every Item disposition, releases active Slots atomically, reverts
integrated Item merges in reverse order and publishes the cancellation Review.
It never fabricates an Item, plan hash or integration base for a scope-only
cancellation; response loss is recovered by refetching the exact Fence,
Integration, Item and Slot tips.
The PR handoff uses `prepare-pr-creation`, `open-pr` and `merge-pr` internally:
the provider adapter must make the exact reviewed head ready, use a merge
commit with an exact head lease and prove that the resulting merge commit is
in the target ancestry before reporting `merged`. Squash, rebase and a
different PR are never accepted as closure evidence.
Release management is deliberately out of scope. No command may infer a
status from a branch name alone; the compiler and verified remote evidence are
the source of semantic truth.
