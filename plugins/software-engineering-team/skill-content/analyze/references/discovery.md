# Discovery and challenge

Use the existing task as the working record. A broader product change can
update its owning analysis documents without creating a parallel specification.

## Questions that change the work

- Who performs the action, on whose data, and with what authority?
- What successful outcome will be observed? What must remain compatible?
- Which state transitions are allowed, refused, reversible or irreversible?
- Which facts are current, derived or frozen when a business event occurs?
- What happens after duplicate submission, concurrent edits, stale reads,
  partial completion, cancellation, dependency failure or loss of authority?
- What known load, retention, latency or availability constraint changes the
  implementation choice? If unknown, what measurement would decide it?

Select the questions relevant to the change. Ask related material questions
together with a recommended option and meaningful tradeoffs. An unimportant
unknown does not justify blocking independent work. A consequential unknown
does not become a fact because a plausible default exists.

## Acceptance criteria

Each criterion names a precondition/action and an observable result, including
state, error, permission or timing where relevant. Use as many criteria as the
outcome requires. Split when outcomes can be delivered independently, or a
dependency must settle first. Line count and criterion count do not determine
risk. Link the criterion to its cheapest convincing check.

Distinguish business validation from implementation choice. A required currency
precision belongs to the rule. A library selected to enforce it belongs to the
design. An existing behavior observed in code may be a bug, so resolve conflict
with accepted product decisions explicitly.

## Durable knowledge

Keep terms, rules, decisions, assumptions and quantified budgets in their owning
documents. Preserve stable identities, retire obsolete entries and supersede
decisions visibly. Link requirements to their design and evidence without
copying whole sections. Record the user decision that resolves a contradiction.

## Challenge completion

Stop once the material ambiguity has a decision, explicit dependency or bounded
experiment. Further critique must bring new evidence or a different concrete
failure. Carry optional opportunities to the task backlog under the active
authorization policy. A challenge is complete when the material decision is testable and its
remaining uncertainty has an explicit owner.
