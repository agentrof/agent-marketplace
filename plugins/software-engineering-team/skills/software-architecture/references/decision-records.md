# Decision Records

The decision log is append-only. It is the baseline the reviewer checks conformance against, and the only place where a style, boundary, budget, or cross-cutting verdict counts as made.

## Write or Skip

| Write a record | Skip: the delta change note is enough |
|---|---|
| Structural style adopted or changed (any row of the style table) | Rename or move with no contract change |
| Module boundary drawn, moved, or removed; ownership row changed | Additive endpoint following recorded conventions |
| Store choice, consistency model, or transaction-spanning design (outbox, saga) | Bug fix restoring declared behavior |
| Authorization model or check placement | Implementation detail inside one owner's files |
| Any denormalized copy of a mutable field (snapshot declaration) | Configuration value change |
| Accepted non-functional budget | Test-only change |
| Versioning stance; any breaking contract change | Formatting already covered by recorded conventions |

Tie-break test: IF a future reviewer could call the change a violation without the record, THEN write the record.

## Small Decisions: the Y-Statement

One line, six mandatory slots:

"In the context of <delta or flow>, facing <forcing symptom or cited budget>, we chose <option> over <named alternatives>, to achieve <benefit>, accepting <cost>."

- An empty "over" slot means no alternative was weighed; that is the missing-alternatives anti-pattern below, not a small decision.
- The "facing" slot cites a symptom or budget by name; "facing future growth" fails the quantification rule in [nfr-budgets](nfr-budgets.md).
- Use the Y-statement whenever the decision fits one line without losing a slot; the moment a slot needs a paragraph, it is a full record.

## Large Decisions: the Full Record

Use when the decision changes a contract, a boundary, stored data, or a cross-cutting default. Fields, all mandatory:

- id and title: a stable id and a title that states the decision, not the topic: "Cursor pagination for activity feeds", never "Pagination".
- status: proposed, accepted, superseded (with the successor id), or rejected.
- context: the forcing symptom or cited budget plus the constraints true at decision time, written so a reader years later needs no other source.
- options: at least two, each with honest costs. An option list where only the winner has pros is a rubber stamp in table form.
- decision: one imperative paragraph.
- consequences: positive AND negative. A record with an empty negative side did not examine the tradeoff; send it back.
- links: superseded record, cited budgets and briefs, affected ownership rows.

Keep a record under a page. A record nobody reads protects nobody.

## Supersede, Never Edit

- Accepted records are immutable. To change course: write a new record with status accepted, set the old record to superseded with the new id, and link both directions.
- Rejected records stay in the log. A rejected option with its reasons prevents the same debate from running twice.
- Renumbering, rewording, or deleting an accepted record is a violation the reviewer reports, even when the edit "just clarifies". The clarification goes in a new, linked record.

## Anti-Patterns

| Anti-pattern | Test that catches it | Consequence |
|---|---|---|
| Retroactive rubber stamp | Record written after the implementation merged, to bless it | The gate reviewed nothing; the alternatives were never live options |
| Missing alternatives | Single-option record | An announcement, not a decision; return it |
| Consequence-free record | Negative consequences section empty | Tradeoff unexamined; the cost surfaces in production instead |
| Edited history | An accepted record's text changed after acceptance | The review baseline moved silently; conformance becomes unverifiable |
| Vague decision | Cannot be caught violated at review | Not falsifiable, so not enforceable; rewrite as a checkable choice |
| Orphan record | Cited by no delta, citing no symptom or budget | Decoration; wire it into the traceability chain or reject it |
