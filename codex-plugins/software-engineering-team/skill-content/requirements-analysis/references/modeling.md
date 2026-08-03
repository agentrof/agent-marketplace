# Modeling Methods

When one-sentence BRs stop working: interacting conditions get a
decision table, lifecycles get a state machine. Both exist to mint
complete, testable BR/AC sets, never diagrams for their own sake.

## Decision Table Method

Trigger: two or more conditions interact to pick one outcome, or a rule
sentence nests and/or/unless more than once.

Worked example: order discount rules at Acme Corp. Across three answers
the owner has said: "premium customers get the discount", "big orders
get it too, but a person signs off", "first orders always get it".

Step 1, extract conditions and their value domains:

- customer_tier: standard, premium
- order_size: below threshold, at or above threshold
- first_order: yes, no

Step 2, list actions: apply discount (yes or no), require sign-off (yes
or no).

Step 3, build the full table, one column per combination (here eight),
and fill every cell by asking the owner. DON'T infer a cell from the
pattern; an unknown cell becomes an assumption row.

| # | tier | size | first | discount | sign_off |
|---|------|------|-------|----------|----------|
| 1 | premium | at/above | yes | yes | yes |
| 2 | premium | at/above | no | yes | yes |
| 3 | premium | below | yes | yes | no |
| 4 | premium | below | no | yes | no |
| 5 | standard | at/above | yes | yes | yes |
| 6 | standard | at/above | no | yes | yes |
| 7 | standard | below | yes | yes | no |
| 8 | standard | below | no | no | no |

Filling the table exposed what the prose hid: row 7 (standard, small,
first order) was never actually decided; the "always" in "first orders
always get it" stayed an assumption row until the owner confirmed it.

Step 4, collapse rows whose actions are identical when one condition is
irrelevant, and only after the owner confirms the irrelevance: rows 1-2
and 5-6 merge (first_order is irrelevant when size is at/above), and
rows 3-4 merge the same way.

| # | tier | size | first | discount | sign_off |
|---|------|------|-------|----------|----------|
| A | any | at/above | any | yes | yes |
| B | premium | below | any | yes | no |
| C | standard | below | yes | yes | no |
| D | standard | below | no | no | no |

Step 5, mint one BR per surviving row:

- BR-031: orders at or above the threshold receive the discount and
  require sign-off by a named approver, for every customer tier.
- BR-032: premium customers receive the discount on orders below the
  threshold, without sign-off.
- BR-033: a standard customer's first order below the threshold receives
  the discount without sign-off.
- BR-034: a standard customer's repeat order below the threshold
  receives no discount.

Smells:

- Two rows with identical conditions and different actions: a
  contradiction between owner answers; surface it, never pick one side.
- A condition that changes no action in any row: drop it, then ask what
  the owner meant by mentioning it.
- A cell filled because it was "obvious": that is an inference; ledger
  it.

## State-Machine Method

Trigger: an entity has lifecycle rules (the persona's data-lifecycle
mandate); statuses gate actions or freeze fields.

Worked example: a "request" entity at Acme Corp.

Step 1, states from the owner's nouns: draft, submitted, approved,
rejected, cancelled, closed. Test each: a state must gate at least one
action or freeze at least one field, otherwise it is a display label,
not a state.

Step 2, events with guards:

- submit (by the requester; guard: required fields complete)
- approve, reject (by an approver; guard: approver is not the requester)
- cancel (by the requester; guard: not yet approved)
- close (by an approver, after fulfilment)

Step 3, transition table, state by event, every cell filled with the
next state or ILLEGAL:

| state | submit | approve | reject | cancel | close |
|---|---|---|---|---|---|
| draft | submitted | ILLEGAL | ILLEGAL | cancelled | ILLEGAL |
| submitted | ILLEGAL | approved | rejected | cancelled | ILLEGAL |
| approved | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL | closed |
| rejected | see note | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL |
| cancelled | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL |
| closed | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL | ILLEGAL |

Note: the rejected row forces a real question: may a rejected request be
resubmitted, or must the requester start a new one? That cell cannot
stay unfilled; it is an assumption row until answered.

Step 4, mint BRs:

- One BR per legal transition carrying a guard. BR-041: only an approver
  who is not the requester may approve a submitted request.
- One BR per illegal transition, and every ILLEGAL cell is testable.
  BR-042: a cancelled request cannot be submitted, approved, rejected or
  closed; any such attempt is refused and the state is unchanged.
- One BR per state freeze. BR-043: after approval, the request's amount
  and beneficiary are frozen; edits are refused.

Step 5, hand the table to the persona's lifecycle coverage check: every
transition named in the brief's process analysis must appear in this
table, and every legal transition here must be reachable from a flow.

## Given/When/Then Smells

- Compound When ("When the user saves and submits"): two behaviors, two
  criteria. Split.
- Untestable Then ("Then it works correctly", "Then the experience is
  smooth"): name the observable: a state, a message, a stored value, a
  refusal.
- Rule smuggled into Given ("Given an order eligible for discount"):
  eligibility is a BR; cite its id in the criterion, never encode it
  silently in setup.
- Then without an observer: if neither the actor nor the QA chain can
  see the effect, the criterion verifies nothing.
- Missing verify line: every AC ends with one, stating how QA observes
  pass or fail on the delivered increment. An AC without a verify line
  is a wish, not a criterion.

## Traceability Conventions

- Ids are permanent: BR-### and AC-### are never renumbered; a struck id
  retires and its number is not reused.
- Every AC cites the BR ids it exercises; every BR is cited by at least
  one AC, or is explicitly marked structural (definitional, no behavior
  to exercise).
- QA names its checks by AC id; a stable id is what lets a check survive
  a brief update, and renumbering silently orphans the whole chain.
- Cross-entity scenarios carry their own ACs; a chain of single-entity
  ACs does not prove the chain end to end.
- When a decision table or transition table changes, diff by BR id:
  changed cells produce changed BRs, and the affected AC ids are listed
  in the same brief update.
