# Source Rebind Pins

These are the instructions of process switch `source_rebind` at
`receipt_when_unchanged`. A task binds this file only when the project's
Process Policy selects that value; at the default, `reviewed_revision`, a
Delivery whose backlog pin moved takes a new execution approval as the flow
describes.

## What the Delivery keeps

A backlog approved through `apply-source-rebind` leaves a sealed receipt
below `backlog/artifacts/source-rebinds/`; only a receipt HEAD holds counts. `delivery_compile.py check` and
every Delivery source check accept the Delivery's old backlog pin as an
alias of the current one only when a chain of committed receipts, source
rebinds or schema migrations, joins the pinned package hash to the current
one. Each hop is replayed from Git: its predecessor is a committed,
hash-verified approval and an ancestor of HEAD, its postimage is the
approved backlog that holds its sealed hash, the approved root review names
it, and its bindings, changed source documents and impact are rebuilt.

The Delivery then keeps its execution approval, `delivery.md`, every Item
record, its plan hashes, the Definition of Done, Operation and Process
Policy pins and its Integration; nothing is written, approved or published.

## When it re-approves

Approve the execution plan again, as the flow describes, when any of these
holds:

- an Item's Story or Test Plan bytes changed on the chain;
- a receipt names an Item's Story or Test Plan as impacted by its changed
  sources, even with unchanged bytes, including every story of an epic
  impacted as a whole;
- a receipt is uncommitted, tampered, ambiguous or does not replay; the
  Delivery check names each receipt file it skipped;
- the Definition of Done, an Operation contract or the Process Policy moved.

## Measurement

The project owner measures outside every task; no role acts on it. Per
Delivery whose pin a receipt carried, record the execution re-approvals,
publications and Item convergences it avoided and every Item a receipt
named as impacted.
