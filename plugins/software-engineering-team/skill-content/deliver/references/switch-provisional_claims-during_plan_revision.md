# An Item starts its fix while its plan revision runs

These are the instructions of process switch `provisional_claims` at
`during_plan_revision`. A task binds this file only when the project's Process
Policy selects that value; at the default, `after_approval`, an Item writes a
path its plan revision adds only after the revised plan is approved, published
and taken by its writer, as the flows describe. Inside a Delivery read the
value with `process_policy.py value --switch provisional_claims --delivery
DLV-###`; the Delivery pins the policy it runs under, so the value binds only
once an execution approval pinned it. Every host that runs the Delivery must
run a package that knows the provisional claim records before that approval.

A plan revision that adds a path to an active Item's `path_claims` holds the
code back until the whole source and plan chain is approved. At this value the
coordinator records a provisional claim of the added path with the barrier,
and the Item's writer commits the change at once. The integration rule stays
as it was: nothing provisional is frozen, reviewed, pushed or integrated
before the approved plan publishes the path.

## Recording a provisional claim

Once `begin-plan-revision` holds the barrier for this Delivery and the
checkout's draft Item record adds the path to the Item's `path_claims`, the
coordinator runs, on the host that holds the Item's verified writer receipt:

```text
delivery_git.py provisional-claim --delivery DLV-### --story <story> --path <path> [--path <path> ...]
```

It records `provisional-claim-v1` on the Integration line, a record without a
tree change, in one atomic push leased on the Integration. It refuses with
`DELIVERY_PROVISIONAL_CLAIM_REFUSED` and names the cause when:

- the Delivery does not run this value;
- the Fence holds no plan-revision barrier that this Delivery began;
- the Item is not active, holds no Slot, or this host holds no verified writer
  receipt for it;
- the Item runs `parallel_lanes_v1`, whose lane scopes a provisional claim
  cannot extend;
- a path is not a normalized repository path, or lies, with its case folded,
  under `workspace/docs`, `.git` or `.agentrof`;
- a path is not in the checkout's draft `path_claims` of that Item, or the
  published plan already claims it;
- a path overlaps, as written or with its case folded, a claim the published
  plan gives another Item that is not integrated or cancelled, a claim the
  draft plan gives another Item, or another Item's live provisional claim;
- the Item already holds a live provisional claim of other paths. Withdraw it
  with `withdraw-provisional-claim --delivery DLV-### --story <story>`, then
  claim the complete set. The same claim again returns the recorded one.

Response loss follows the transaction rule of every coordinator verb: after
`DELIVERY_TRANSACTION_UNCERTAIN`, read the refs again, then run the same claim
or withdrawal, which returns the record that landed as `reused`.

Every reader of the records checks each one again as the verb wrote it: its
protocol, an unchanged Integration tree, and its paths by the rules above. A
record that fails, such as one pushed by hand, stops the read with
`DELIVERY_COORDINATION_CORRUPT` and grants nothing.

## What a claim lets the writer do

The implementer's task, derived with `task_inputs.py --entry deliver`, adds
each live provisional path of its Item to the write scope as a path and its
descendants, with the constraint that it is not freezable or pushable before
the approved plan publishes it. The writer commits the change in the Item
worktree. `freeze` and a reader's task manifest (`delivery_verification.py
manifest`) refuse it with `DELIVERY_PROVISIONAL_CLAIM_PENDING`, and
`push-item` refuses it the same way, so the readers never start on it and
`integrate-item` never sees it. They read the Item's claims from the Item
record of the Integration commit the Item converged on, its
`integration_base_commit`, so an edit of the worktree's own Item record grants
nothing. Other reads of the candidate, such as `regression-selection`,
`regression-run` and `assertion-map`, still work on a provisional commit. Each
verb that reads the claims takes `--remote` for a Delivery remote other than
`origin`.

`block-item` and `pause-item` need a clean Item worktree, so flush the
provisional commits there with the plan, or revert them, before either verb.

A claim is live only while all of these hold, derived at every read and never
stored: the Fence still holds the same plan-revision barrier of this Delivery,
the Item ref still descends from the tip the claim recorded, the Item still
holds its Slot, and no takeover elected another writer. Otherwise it is void.

## How the revision ends a claim

`finish-plan-revision` and `abort-plan-revision` release every live claim of
the barrier in the same atomic push as the barrier, one
`provisional-claim-release-v1` record each, which names the claim record it
ends in `Claim-Record`; a claim that was already void stays void:

- `promoted`, at finish, when the published Item record claims every path. The
  writer converges the Item on that Integration; the convergence is a merge,
  so the provisional commit keeps its identity, and then freeze and
  `push-item` accept it.
- `orphaned`, at finish, when the approval dropped or moved a path.
- `withdrawn`, at abort, or by `withdraw-provisional-claim`.

`freeze` and `push-item` refuse a change to an orphaned, withdrawn or void
path with `DELIVERY_PROVISIONAL_CLAIM_ORPHANED` and name each path. The writer
reverts or reworks that change in the Item worktree; nothing rewrites it
automatically. `cancel-delivery` already waits for the barrier to end.

While a claim is live, and once promoted until the Item ref claims its paths,
`refresh-target` counts its paths as claimed, as written or with their case
folded, and refuses a target that changes them with
`DELIVERY_TARGET_SOURCE_VIOLATION`.

## What the owner sees

`delivery_compile.py status --delivery DLV-### [--remote <remote>]` lists each provisional claim
with its Story, paths, barrier epoch and state: `live`, `promoted`,
`orphaned`, `withdrawn` or `void`. `approve-review` fills a line in the
Delivery Review's Deviations that lists the Items that started work under a
provisional claim and how each claim ended, `void` for one no release ended.
