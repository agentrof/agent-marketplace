---
name: deliver
description: Resume one exact Delivery, execute its approved Items and close one Delivery Review.
exposure: entry
---

# Deliver

Before delegating a role, follow the packaged `templates/task-input-contract.md`
and derive its inputs with `scripts/task_inputs.py --entry deliver`.
Use the owning compiler's exact scope and source bindings.

## When to Use

- Use with an exact `DLV-###` after its Execution Plan and Item claims are
  published.
- Use to resume implementation, integrate Items and complete the one Delivery
  Review and PR handoff.

Read `flows/delivery-execution.md` completely before changing Delivery state.

## Invocation

Use `/deliver DLV-###` for the exact Delivery and `/deliver DLV-### status`
for its derived semantic state. An ID is required; `start-item` and other Git
verbs are internal coordinator operations and are not public entry syntax.
`/deliver DLV-### status` also runs `closure-audit --delivery DLV-###` and
shows its outcome and recovery beside the semantic state.

## Closure

Merge only through `merge-pr` on the recorded PR head; never merge the
Delivery PR, its Integration or an Item branch through the provider or Git
directly. Report a Delivery complete only when `closure-audit` returns
`closed`. Any other outcome is reported as it is, with the recovery the audit
names: `merged_cleanup_pending` needs `verify-merge`, and
`external_product_merge` is never a merge and goes to the project owner.
Broad delegation, time pressure and an autopilot grant authorize running the
flow as prescribed and never waive evidence, integration, the PR record or
closure. A quality exception the flow permits is accepted only at its
explicit gate, with the named rule, its bounded scope, the approval's
provenance and the remaining obligations recorded.

## Boundary

Read the approved `delivery.md`, `execution-plan.md`, Item records, code
reviews and verification records. The execution flow owns resumable Item
work, serialized integration, one aggregate Delivery Review and one final PR.
Item evidence is authored only in the active Item worktree: approval derives
the reviewed and verified OID from its real committed `HEAD`, with only the
initialized Code Review and Verification report drafts allowed pending. The coordinator
publishes that committed product/test change with its review and verification
records. Do not copy evidence from the primary worktree or supply an arbitrary
commit identifier. Integration validates the remote Item tip and its exact
product/test parent before it can merge.
For an Item whose approved plan requires architecture impact, invoke the
Software Architect first. The exact Item may create or revise only its claimed
System Architecture records with `architecture_compile.py`, stamp its
`architecture_delta_hash`, then pass that hash through verification alongside
the code change.
Cancellation is a deliberate exception inside the same entry. `/deliver
DLV-###` first renders an exact read-only cancellation preview when the user
chooses cancellation. The internal `cancel-delivery` coordinator records the
reason and every Item disposition, releases active Slots atomically, reverts
every integration merge of its Items in reverse order, including one a
reopened Item left, and publishes the cancellation Review.
It never fabricates an Item, plan hash or integration base for a scope-only
cancellation; response loss is recovered by refetching the exact Fence,
Integration, Item and Slot tips.
The PR handoff uses `prepare-pr-creation`, `open-pr` and `merge-pr` internally:
the provider adapter must make the exact reviewed head ready, use a merge
commit with an exact head lease and prove that the resulting merge commit is
in the target ancestry before reporting `merged`. A provider-reported check
passes when its result is success, skipped or neutral; all must pass, and
at least one must have succeeded, immediately before the merge
call and in merged evidence. Squash, rebase and a different PR are never
accepted as closure evidence. Recording the PR moves the Delivery to
`awaiting_merge`; `status` reports `merged` only when the current branch
reaches, on any path, a two-parent merge of that recorded PR head that carries
no `Agentrof-Record` trailer, also for a PR recorded while the Delivery stayed
in `review`. Coordinator commits, such as the reopen commit, never count; a
manual merge of the Integration branch into another branch would. A shallow
clone or a failed Git query makes `status` and `check` fail with an explicit
finding.
Once the merge is proven, `merge-pr` or `verify-merge` deletes the Delivery's
Integration ref and its integrated Item refs; the target's package is the
record from then on. Only the project Fence and a cancelled Story's Item ref
stay, and running `verify-merge` again on a Delivery merged earlier removes
what an older release left.
Release management is deliberately out of scope. No command may infer a
status from a branch name alone; the compiler and verified remote evidence are
the source of semantic truth.
