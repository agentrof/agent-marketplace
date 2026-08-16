---
name: challenge-review
description: Read-only adversarial review method for project-local analysis and solution documents.
exposure: internal
---

# Challenge Review

Use fresh, read-only perspectives to expose missing evidence before a
document gate. Findings return to the owning Markdown review note; there is no
central findings store or run record.

## When to Use

- A Business Analysis domain or solution-design engagement is approaching an
  approval gate.
- A previous challenge round left blocking findings that need re-review.

## The Loop

1. Select lenses from the lens bank and topic experts; record the panel and
   reason in the round note.
2. Give each challenger only the named files, the constitution and its lens.
   Never pass conversation history or an interpretation of the author intent.
3. Triage every finding as `fix`, `covered`, `assumption`, `question` or
   `rejected` with evidence and a target document. A proposal becomes a fact
   only after the owner accepts it.
4. Apply fixes, run the owning compiler and vault check, and close the round
   note. Round 1 is mandatory; repeat only while blocking findings remain, up
   to the configured maximum of three rounds.
5. A closed gate must name unresolved questions and their owner. Silence is
   not a disposition.

## Core Rules

- Challengers are read-only. The owning persona is the single writer.
- Severity is preserved from the challenge response.
- Mechanical findings cannot be rejected as stylistic.
- Small scopes still receive a proportionate panel; no filler rounds.

## References

- [lens-bank](references/lens-bank.md): standard lenses and panel sizing. Read when selecting a challenge panel.
- [expert-casting](references/expert-casting.md): grounded practitioner questions. Read when a named domain question needs an expert.
- [triage](references/triage.md): disposition and round-record shape. Read when findings return from a challenge round.
