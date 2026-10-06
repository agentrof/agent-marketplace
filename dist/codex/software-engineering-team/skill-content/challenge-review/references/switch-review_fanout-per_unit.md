# Per-Unit Readers

These are the instructions of process switch `review_fanout` at `per_unit`.
A task binds this file only when the project's Process Policy selects that
value; at the default, `single_reader`, the step's reader reads every
changed unit in turn. Every task of an owning flow binds it, and only the
orchestrating entry acts on it: a role never starts or closes another agent.

## Units

A unit is what the flow's review step names as its changed unit: a story with
its test plan, an Operation contract, an Experience process package, an
Item's change, or one analysis, Solution or Design System document. A review
of one changed unit keeps one reader.

## Fan-out

1. Run the step's compiler checks first; their cross-unit facts are the
   aggregator's input.
2. Spawn one fresh reader of the step's reader role per changed unit, all in
   one message, as the host contract names. Each reader is given its unit's
   inputs as switch `review_scope` scopes them. Under switch `review_panels`
   at `lens_panel`, each lens assignment runs once per unit.
3. Wait for every unit reader. Then spawn one fresh aggregator of the same
   reader role with the readers' findings and the compilers' cross-unit
   facts only. It decides cross-unit questions no compiler enforces:
   overlap, ordering judgment and the meaning of shared contracts. It never
   re-checks a fact a compiler already refuses: dependency cycles and
   direction, coverage tables, duplicate ids, relation sets.
4. The aggregator may ask for a unit's text with a stated reason; the
   coordinator adds it to its task and records the request.
5. Wait for the aggregator before triage or any writer action. Every
   returned severity stands; no reader gets another reader's reply.

## Measurement

Record per review its units, its wall clock against its slowest unit reader
and the aggregator's requests with their reasons.
