---
name: challenge-review
description: Read-only adversarial review method and parallel lens panels for project-local document reviews.
exposure: internal
---

# Challenge Review

Use fresh, read-only perspectives to expose missing evidence before a
document gate. Findings return to the owning workflow, not to a durable review
history. The approved canonical documents are the lasting evidence.

## When to Use

- A Business Analysis domain is approaching an approval gate.
- A backlog, Solution Design, Design System or Operation contract review runs
  in the mode that `review_mode` in `data/review-panels.json` selects: its
  flow's single reviewer by default, or its review panel. Solution follows the
  single review plan in `solution-architecture/references/challenge-lenses.md`
  in either mode, including its risk-triggered specialists.
- An earlier challenge left blocking findings that need targeted re-review.

## The Loop

1. Select proportionate lenses from the owning stage's review plan; Business
   Analysis uses the lens bank and topic experts, Solution in `single` mode
   uses its named primary reviewer and risk-triggered specialists, and a step
   in `panel` mode takes its default lens assignments from
   `data/review-panels.json`. Include each lens and its focus in that
   reviewer's explicit input.
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

- [lens-bank](references/lens-bank.md): standard lenses and panel sizing. Read when selecting a Business Analysis challenge panel.
- [review-panel](references/review-panel.md): the review mode switch, parallel lens readers, merged findings, panel verdict and targeted re-review. Read when a flow reaches a review step that `data/review-panels.json` declares or when you are one of a panel's lens readers.
- [expert-casting](references/expert-casting.md): grounded practitioner questions. Read when a named domain question needs an expert.
- [triage](references/triage.md): live finding disposition and durable-resolution rules. Read when challenge findings return.
