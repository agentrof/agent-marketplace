# Structured Records: Dependencies and DoD Items

The two structured record types the backlog import carries besides the
story fields: dependency edges and DoD items. Both land in the PMO
database as first-class rows; both are consumed by machines, not just
readers. This file holds the worked rules; the skill body holds the
decision surface.

## Dependency edges

Each dependency is `{"item": "WP-01", "reason": "..."}` in the story's
`depends_on` list; the edges are the record, and the only one: the
scheduler, the renderer and the dashboard all read them.

- An edge is real only when the story CONSUMES the target's output: an
  artifact, a state, a capability its Definition of Done needs. The
  reason names that need ("the decision screen needs the report state
  model from WP-01"). "Comes after WP-01" restates the ordering and is
  not a reason.
- Edges are the parallelization contract, not reading order: concurrent
  work orders are scheduled from them. Every unnecessary edge serializes
  work that could run in parallel; every missing edge lets a story start
  before its ground exists. Author them as if a machine will obey them,
  because one will.
- Two stories that always change together are one story wrongly split;
  merge them (constitution rule) instead of wiring edges between them.
- The import rejects a dependency cycle mechanically and names the cycle
  path. A rejected cycle is a mis-cut backlog, not a formatting problem:
  re-slice at the state transition that broke the direction.
- The derived execution order (the CLI's item order subcommand, and the
  dashboard's order panel) is topological over the edges with the
  priority tier as tiebreak; it is computed, never stored, so it is
  always current with the edges you author.

## SHARES

A shared contract is NOT a dependency. Two stories that both touch a
named contract (an interface file, a schema, an endpoint) are marked
SHARES with that contract's name: SHARES records co-ownership for
scheduling and review; an edge records consumption order.

Worked example: WP-05 "Find reports" and WP-06 "Export approved reports"
both extend the reports endpoint. Neither consumes the other's output,
so no edge exists between them; both carry "SHARES: reports endpoint
contract" in their scope lines. A scheduler may run them in parallel but
knows their reviews watch the same contract; an edge here would have
serialized them for no reason.

## DoD items

The dod text field summarizes; `dod_items` is its checkable
decomposition, individual records a checker flips to verified or failed
one at a time (the QA step does this through the CLI).

- One verifiable property per item, phrased so a checker can pass or
  fail it without interpretation: name the observable behavior, the
  exact artifact, or the mechanical check. "A member with a valid token
  sets a new password and signs in with it" clears the bar; "password
  reset works" does not.
- DoD items are exempt from brevity: one item per property, as many
  items as the story has properties, no cap. Never compress the spec to
  keep the backlog short.
- No self-referential count claims ("references it exactly once"): the
  moment a duplicate appears the statement contradicts itself and the
  checker lands on it. State the property, not the count, unless the
  count IS the property.
- Phrase scope-confinement items to stay correct under concurrent
  sibling work orders: "this story altered only files whose diff touches
  avatars", never "no other files changed" (sibling orders change other
  files legitimately).
- Every item traces to a brief criterion or business rule; an item that
  cites nothing is invented scope (raise it in open questions) or too
  small to stand alone (fold it into a sibling item).
- The story's scope names its expected environment impact (a new
  service, store or exposed surface, or explicitly none): the
  delivery-lanes flow's SHARES advisory reads it to keep two
  environment-impacting stories out of the same parallel wave.

## Anti-pattern gallery

| Anti-pattern | Smell in the backlog | Fix |
|---|---|---|
| Ordering-as-reason | "depends on WP-01: comes first" | Name the consumed artifact or state, or delete the edge |
| Contract edge | An edge between two stories that only share an endpoint | Replace with SHARES on both |
| Chain insurance | Edges added "to be safe" along the reading order | Delete every edge whose target output is not consumed |
| Vague DoD item | "works correctly", "is user friendly" | Name the observable behavior and its trigger |
| Count claim | "mentions the token exactly once" | State the property; drop the count |
| Global scope claim | "no other files changed" | Scope to this story's diff signature |
