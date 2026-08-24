# Non-Functional Elicitation

The six categories, the questions that quantify them, and the contract
for handing budgets to the architect. Functional probing gets rules out
of the owner; this gets numbers.

## Per-Category Question Sets

Walk all six once per brief. A category's output is one of: BR/AC
entries, named budgets, or the explicit line "none stated, confirmed".

### Performance

- "Which single interaction hurts most when slow? At what delay does it
  start hurting: one second, five, thirty?"
- "Is that at a quiet moment or the busiest one? What does the busiest
  moment look like: how many people, doing what?"
- "Is anything time-critical in the business sense: a deadline, a
  handover, a customer waiting on the phone?"

### Volume

- "How many of the core records exist on day one? After a year?"
- "What is the largest single item anyone will store or upload?"
- "How long must data stay available, and when must it be gone?"

### Availability

- "What does one hour of downtime cost, in money or trust? Which hour
  would be worst?"
- "After a failure, how much recent work is acceptable to lose: none,
  minutes, a day?"
- "Is there a natural maintenance window nobody would notice?"

### Security and Roles

- "Who must never see what? Name the pair."
- "Does anything cross the team or tenant boundary: sharing, export,
  public links?"
- "Who may delete, and must deletion be provable or reversible?"
- "If someone misused their access, would you need to prove what they
  did? How far back?"

### Compliance

- "Has any regulation, standard or contract clause already been named by
  your lawyers or customers?" Record the owner's words; DON'T supply a
  regulation the owner did not name: that is legal advice, not analysis.
- "Must data stay in a region? Must a person be erasable on request?"

### Operability

- "Who runs this day to day? What do they check each morning?"
- "When something breaks at night, who notices first: a user, or the
  system?"
- "What must be restorable, and how fast, after a bad deploy or a bad
  bulk edit?"

## Quantification Table

Vague quality words are banned from budgets. Replace them the moment
they are uttered:

| Vague word | Replacement question | Example budget |
|---|---|---|
| fast | "Which interaction, and at what delay does it hurt?" | search returns within two seconds at the 95th percentile with fifty concurrent users |
| scalable | "What volume this year, and what growth would be a good problem?" | list views stay within their latency budget up to one hundred thousand records per tenant |
| always up, reliable | "What does an hour down cost, and which hour is worst?" | available through business hours with at most one hour of unplanned downtime per month |
| secure | "Who must never see what, and what must be provable?" | every read of a sensitive record is attributable to a person for twelve months |
| real-time | "Stale by how much before someone acts on wrong data?" | dashboard figures at most one minute behind entry |
| user-friendly | Not a budget. Route to an AC on a named flow, or to the design chain. | none |

Every budget carries all three parts: a number, a bound or percentile,
and a load context. A budget missing one part goes back to its
replacement question.

## Routing Rule

Test each candidate: can the QA chain verify it pass/fail on a delivered
increment?

- Yes: it becomes a BR or an AC with a verify line, through the normal
  machinery. "An unauthorized role receives a refusal" is testable; it
  is a BR, not a budget.
- No, but it bounds design choices: it becomes a named budget. An
  availability budget shapes the architecture even though no single
  increment proves it.
- Neither testable nor design-bounding: it is not a requirement; ladder
  it for the rule behind it or drop it.

## Handoff Contract

Budgets land in the brief's non-functional budgets subsection, one entry
per budget:

- name: short and stable ("search latency", "tenant volume ceiling")
- statement: the quantified sentence (number, bound, load context)
- source: the owner's answer, or the assumption row it came from
- consequence: what breaks or gets redesigned if the budget is exceeded

The architect designs from these budgets and, by constitution, halts
rather than guesses when one is missing. Therefore:

- An unasked category is a design blocker, not a blank; the subsection
  lists every category, each holding budgets or "none stated,
  confirmed".
- A budget still resting on an unconfirmed assumption carries its AS-###
  id, so the architect can tell owner facts from analyst proposals.
- Changing a budget after design starts follows the brief's normal
  update path; budgets are part of the living brief, never side notes.
