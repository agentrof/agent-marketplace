---
name: challenge-review
description: Read-only adversarial review method for project-local analysis and solution documents.
exposure: internal
---

# Challenge Review

Use fresh, read-only perspectives to expose missing evidence before a
document gate. Findings return to the owning workflow, not to a durable review
history. The approved canonical documents are the lasting evidence.

## When to Use

- A Business Analysis domain is approaching an approval gate.
- A solution-design engagement is approaching an approval gate; use its single
  review plan in `solution-architecture/references/challenge-lenses.md` for
  reader selection and lens coverage, and this skill for triage.
- An earlier challenge left blocking findings that need targeted re-review.

## The Loop

1. Select proportionate lenses from the owning stage's review plan; Business
   Analysis uses the lens bank and topic experts, while Solution uses its
   named primary reviewer and risk-triggered specialists. Include each lens
   and its reason in that reviewer's explicit input.
2. Give each challenger only the named files, the constitution and its assigned
   lens coverage. Never pass conversation history or an interpretation of the
   author intent.
3. Wait for every reviewer in the selected panel before writer action. The
   owning persona triages returned findings as `fix`, `covered`, `assumption`,
   `question` or `rejected`, always against concrete evidence.
4. Write accepted resolutions only into their canonical owning documents.
   Convert uncertain facts into the stage's existing assumption, open-question
   or decision structure; do not persist reviewer output as an audit log.
5. Run the owning compiler and vault check, then re-run only the affected
   read-only challenge while blocking evidence gaps remain. Include any new
   dependency or risk exposed by a fix. Once blocking gaps are closed, do not
   require an extra clean round or a previously silent reviewer. The final
   compiler approval is the durable gate evidence.

## Core Rules

- Challengers are read-only. The owning persona is the single writer.
- Severity is preserved from the challenge response.
- Mechanical findings cannot be rejected as stylistic.
- Small scopes still receive proportionate scrutiny; no filler reviews.
- Reviewers return findings only. The owning persona is responsible for every
  canonical edit and waits for all parallel readers before writing.

## References

- [lens-bank](references/lens-bank.md): standard lenses and panel sizing. Read when selecting a challenge panel.
- [expert-casting](references/expert-casting.md): grounded practitioner questions. Read when a named domain question needs an expert.
- [triage](references/triage.md): live finding disposition and durable-resolution rules. Read when challenge findings return.
