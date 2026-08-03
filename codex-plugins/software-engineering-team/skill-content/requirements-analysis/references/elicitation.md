# Elicitation Techniques

Question sequences for the persona's rounds. The persona owns WHICH
rounds run (purpose, actors, flows, data, lifecycle); this file owns the
questions inside them and the close that ends them.

## Goal-to-Actor-to-Impact Chain

Impact mapping, reduced to a single-interlocutor questioning device: the
chain runs goal, actors, impacts, deliverables, and every scope decision
must survive being walked backwards along it.

Sequence (purpose round):

1. "When this exists and works, what number or state changes for the
   business? How would you notice?"
2. "Who can move that number? Who can block it?"
3. "What must each of those people start doing, stop doing, or do
   differently?"
4. "What is the smallest thing that causes that change in behavior?"

Rules:

- A proposed feature that cannot be walked back to a goal is recorded in
  open questions with the failed chain attached, never accepted into
  scope on enthusiasm.
- A goal without a measure becomes an assumption row ("success means X,
  unmeasured") and is challenged at close.
- Actors surfaced here seed the actor round; an actor with no impact
  attached is a distribution-list entry, not a requirement source.

## Scenario Walkthrough

Ask for one concrete past instance, never a general description.
"Usually" answers describe the owner's self-image; instances describe
the actual process.

Sequence (main-flow round):

1. "Walk me through the last time this actually happened, step by step."
2. Per step: "Who did that? What did they look at? What did they decide?
   Where did it go next?"
3. "Where did it go wrong that time, or the time it did?"
4. After two instances: "Which differences between those two runs were
   real rules, and which were accidents?"

Rules:

- Park every generalization uttered mid-walkthrough and re-probe it:
  "tell me about a time it did not go that way."
- Mine each step for actor, data touched, decision made, exception hit;
  these become flow steps, dictionary fields and BR candidates.
- Abstract to a flow only after the second instance; a flow drawn from a
  single instance enters the ledger as an assumption row.

## Laddering

When the owner states a preference, ask why until the rule behind it
surfaces, usually within three to five whys.

Worked sequence:

1. "Approvals must be manual." Why?
2. "Auto-approval burned us once." What did it cost?
3. "A refund over the threshold went out and was unrecoverable."
4. Record the rule: refunds above the threshold require a named
   approver. Then challenge the leftover width: "below the threshold,
   may approval be automatic?"

Rules:

- Record the surfaced rule as a BR; the preference itself is not a BR.
- Stop laddering at a business rule, an external constraint, or a value
  judgment the owner explicitly owns; never ladder past a compliance
  constraint hunting for a "real" reason.
- If the ladder dead-ends at "we have always done it this way", write an
  assumption row proposing the narrower rule and challenge it at close.

## Boundary Probing

Every number the owner utters hides several rules.

For any stated limit, quantity or threshold ask:

- What happens exactly at the limit?
- What happens one past it: refusal, queue, silent truncation, override?
- What happens at zero or none?
- Who, if anyone, may exceed it, and is the exception recorded?

Each answer is minted as a testable BR; an unanswered probe becomes an
assumption row with a proposed default.

## Edge-Case Question Bank

Run once per feature. Every hit lands as a BR, an AC, or an assumption
row; a hit that lands as narrative prose is lost.

| Category | Question stems |
|---|---|
| Empty | First ever run; zero items in the list; all data deleted; the required upstream record missing |
| Boundary | Zero, one, exactly at the limit, one past it; longest allowed text; oldest allowed date |
| Concurrent | Two people edit the same record; approve and cancel arrive together; same person, two sessions |
| Stale | Acting on data changed since it was read; a link or notification pointing at a deleted record |
| Wrong role | Unauthorized link sharing; role revoked mid-session; acting across the tenant or team boundary |
| Scale | Ten times expected volume; search, pagination and export at that volume; the one giant record |

## Challenge-Then-Confirm Close

The persona mandates the close; these are its mechanics.

1. Run the completeness and consistency sweeps first (the persona's own
   checklist), silently; their findings feed the challenge list.
2. Challenge round: present each finding as a claim with a proposed
   resolution, not as an open-ended question. "The brief says nothing
   about X; I intend to write BR-021 as Y; confirm or correct." Ask only
   where the answer changes the brief either way; batch the round to the
   few highest-stakes items, never a checklist recital.
3. Steelman before challenging an owner decision: state the strongest
   case for it in one sentence, then the risk that argues against it.
4. Confirm by reading back the changed BR/AC ids with their one-line
   contents, not a prose summary; the owner confirms ids, prose invites
   drift.
5. Sweep the assumption ledger last: confirm-or-strike every open row
   (format below); a row is deferred only by the owner's explicit word,
   and it moves to open questions when deferred.

## Assumption Ledger

Working state inside the conversation; the brief carries only the
ledger's confirmed outcomes and its deferrals (as open questions), never
the raw ledger.

| Field | Content |
|---|---|
| id | AS-###, stable, never reused |
| statement | One falsifiable sentence, worded so the owner can say no |
| source | The answer, silence or inference that spawned it |
| affects | BR/AC ids that stand or fall with it |
| status | open, confirmed, struck, deferred |

Rules:

- A row is created at the moment of inference, mid-round, never
  reconstructed at close from memory.
- Confirm-or-strike: at close every open row is either confirmed (its
  content moves into a BR, AC or scope line and the row records the
  target id) or struck (its dependent BR/AC ids re-examined and fixed).
- Word each statement so disagreement is possible; "the system handles
  errors sensibly" is not a ledger entry.
- Deferred rows exist only with the owner's explicit deferral and land
  in open questions naming what stays blocked until answered.
