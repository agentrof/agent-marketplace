# The Lens Bank

Each lens is one spawned perspective. A lens gets the domain subtree
read fully and judges ONLY through its own questions; overlap between
lenses is expected and handled at triage, never pre-coordinated.

## Scenario lenses

| lens | hunts | question stems |
|---|---|---|
| negative-scenarios | missing exception and refusal paths | What happens when the input is empty, duplicated, over the limit, out of order? Where does a refused action leave the record? Which flows have a happy path only? |
| concurrency-and-scale | races and volume cliffs | Two actors act on the same record in the same minute: who wins, what does the loser see? Which rule breaks first at ten times the stated volume? What is never allowed to happen twice? |
| compliance-and-audit | untraceable actions, retention gaps | Who did what, when, on whose authority, provable how? What must be kept, for how long, and what must be deletable on request? Which rule changes on a regulator's schedule? |
| integration-failure | silent external dependencies | The external system is down, slow, or answers wrong: which flows stall, which corrupt? What is retried, what must never be retried? Who owns the reconciliation? |
| misuse-and-fraud | profitable abuse of stated rules | Which rule can an actor exploit as written? What does the malicious insider do with the permissions the roster grants? Which limit invites splitting one action into many small ones? |
| data-lifecycle | propagation and freeze gaps | For each entity: which updates propagate, which must not, what freezes at issue time? Which state transitions are missing from the table? What happens to dependent records on delete or supersede? |
| industry-variant | parochial assumptions | How do other organizations run this process, and which of their variants would break this analysis? What does the analysis assume about THIS organization that the next customer will not share? |
| testability | unverifiable criteria | Can each criterion be verified as written, with the verify cell it carries? Which criteria hide two behaviors in one row? Which rule has no criterion exercising it? |
| feasibility-signal | budget and rule gaps that stall design | Which missing or unquantified budget would make the architect halt? Which pair of rules cannot both be satisfied? Which rule silently requires data no entity carries? |

## Cross-domain lenses (space-level round only)

| lens | hunts | question stems |
|---|---|---|
| cross-domain-consistency | contradictions between domains | Which two rules in different domains disagree about the same record? Does the same glossary term mean the same thing in every domain that uses it? Which cross-domain flow cites rules from only one side? |
| integration-coherence | seams between domains and systems | Every integration doc: which domains consume it and do their rules agree on its failure semantics? Which cross-domain process has no owner for its middle step? |

## Panel sizing

- Floor, small domain (one node, a handful of docs): negative-scenarios,
  data-lifecycle, testability, plus one cast expert.
- Standard domain: the floor plus concurrency-and-scale and
  misuse-and-fraud, two cast experts.
- Regulated or externally connected domains add compliance-and-audit
  and integration-failure respectively; customer-facing or multi-tenant
  topics add industry-variant.
- Space-level round: the two cross-domain lenses, plus feasibility-signal
  over the whole registry.
- Round 2 and 3 re-run ONLY the lenses whose findings were blocking,
  plus one lens that was silent (fresh eyes on the fixes).
