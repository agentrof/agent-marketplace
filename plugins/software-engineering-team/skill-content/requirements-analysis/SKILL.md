---
name: requirements-analysis
description: Requirements analysis knowledge for the team's business analyst role. Loaded by software-engineering-team flows; not user-facing.
exposure: internal
---

# Requirements Analysis

Worked methods for the analyst persona. The constitution owns the round
structure and the space contract; this skill owns the technique inside
each round and the standard the documents obey.

## When to Use

Load when the analyst persona starts or updates an analysis space.
Everything here feeds the space's typed documents: BR/AC rows, the
assumption ledger, the non-functional budgets, and the tree they live in.

## Space Discipline

- One topic, one analysis space; every fact has exactly one owning
  document and every other mention links to it.
- Rows, not prose: a minted id (BR, AC, AS, OQ, DEC) exists only as a
  table row in its kind's section; BR rows only in rule_sets, AC rows
  only in acceptance_sets. A rule narrated in a paragraph is invisible
  to the registry and to QA.
- Cite ids as links to their owning doc; the compiler proves the target
  mints them. Numbers are permanent: retire, never renumber or reuse.
  Citations are wikilinks per the vault contract (the obsidian-vault
  skill owns the grammar).
- Run the compiler's check and render after every authoring milestone;
  a gate is never presented on a red compile.

## Elicitation Discipline

- Open each topic with a goal-to-actor-to-impact chain: measurable goal,
  actors who can move or block it, behavior change wanted from each. A
  feature that cannot be chained to a goal goes to open questions, never
  into scope.
- Walk one concrete past instance end to end before abstracting a flow;
  park every "usually we..." and re-probe it against a real case.
- Ladder each stated preference: ask why until a rule, constraint or
  cost surfaces; record the rule as a BR, then challenge the leftover
  width of the preference. DON'T record the preference as the rule.
- Probe every stated limit at the boundary: at it, past it, at zero, at
  scale. Each answer becomes a testable BR.
- Run the persona's edge-case bank (empty, boundary, concurrent, stale,
  wrong role, scale) once per feature; every hit lands as a BR, an AC or
  an assumption row, never as narrative prose.
- Ledger every inference the moment it is made: id, statement, source,
  affected BR/AC ids, status. Confirm-or-strike at close: each open row
  is confirmed into the brief or struck with dependents flagged; a
  silently open row is a defect.
- DON'T use multi-stakeholder formats (workshops, surveys, group
  prioritization); this flow has one interlocutor. Cover the same ground
  as sequenced single-person questions.

## Modeling Notations

- Two or more interacting conditions on one outcome: decision table.
  Fill every cell by asking (unknown cells become assumption rows),
  collapse only owner-confirmed irrelevance, mint one BR per surviving
  column. A rule sentence nesting and/or/unless twice is the trigger.
- Lifecycle rules: state-machine method. States gate actions, events
  carry guards, and every illegal transition is enumerated as its own
  testable BR.
- Acceptance criteria in Given/When/Then, one behavior per criterion,
  each ending with the verify line the QA chain consumes. DON'T compound
  the When; DON'T write a Then nobody can observe.

## Non-Functional Elicitation

- Walk all six categories once per brief: performance, volume,
  availability, security and roles, compliance, operability. Write an
  empty category as "none stated, confirmed", never leave it silent; the
  architect must tell "no requirement" from "not asked".
- Vague quality words (fast, scalable, secure, reliable) are banned from
  budgets. Every budget carries a number, a bound or percentile, and a
  load context.
- Route by testability: pass/fail-verifiable on the increment becomes a
  BR or AC; design-bounding but not increment-testable becomes a named
  budget in the brief's non-functional budgets subsection. The architect
  refuses to guess a missing budget, so a gap there blocks design.

## References

- [space-standard](references/space-standard.md): the analysis-space standard: node model, document types, id scheme, lifecycle, formatting, rename runbook, worked example. Read when creating a space, adding a document type, or unsure how a doc must be shaped.
- [decomposition](references/decomposition.md): explicitly approved domain split proposals, semantic promotion signals, the fact-routing test and session resume protocol. Read when a topic spans more than one domain or deciding where a new fact lives.
- [elicitation](references/elicitation.md): question sequences per technique, challenge-then-confirm mechanics, assumption ledger format. Read when planning a questioning round or closing a brief.
- [modeling](references/modeling.md): decision-table and state-machine methods worked end to end, Given/When/Then smells, traceability conventions. Read when a rule cluster or lifecycle resists one-sentence BRs.
- [nfr-elicitation](references/nfr-elicitation.md): per-category question sets, quantification table, budgets handoff contract. Read when eliciting or writing the non-functional budgets subsection.
- [research-methods](references/research-methods.md): user-research repertoire with output artifacts. Read when the owner can reach real end users for this topic; skip otherwise.
