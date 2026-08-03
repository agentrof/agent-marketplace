# Triage: Findings Into the Space

Every returned finding gets exactly one disposition, recorded as a CH
row in the round record. The record is audit history: closed rounds are
locked and never edited; corrections happen in the next round.

## Dispositions

| disposition | meaning | targets cell carries |
|---|---|---|
| covered | the analysis already handles it | the BR/AC ids that prove it |
| fix | a real gap; the analyst edits the owning doc | the ids minted or changed by the fix |
| assumption | plausible expert knowledge awaiting the owner | the AS id it became |
| question | needs the owner's ruling | the OQ id it became |
| rejected | not a real finding | the one-sentence reason |

Rules:

- Severity is copied from the challenger verbatim; a triage that
  downgrades severity is a contract violation. Disposition is the
  analyst's judgment; severity is not.
- covered/fix/assumption/question MUST cite resolving target ids that
  exist; the compiler fails a record whose targets do not resolve.
- Duplicates across lenses merge into one row, the merged lenses named
  in the lens cell; merging is triage's job, never the panel's.
- A blocking finding keeps its round from converging even when its fix
  landed the same day: the NEXT round's fresh eyes confirm the fix.

## The triage audit

The analyst grades critique of its own work, so the two burial paths get
an independent check before the record closes:

- One fresh-context, read-only spawn (analysis-challenger role, audit
  task) receives ONLY the round record plus the documents its covered
  and rejected rows cite, and re-judges each such disposition: does the
  cited evidence actually support burying the finding?
- Disagreements are recorded in the record's triage audit section and
  presented at the gate; the owner rules. The audit never re-opens fix,
  assumption or question rows; those already surface on their own.

## The round record

Created via the compiler's stub (type challenge_record), one file per
round under the node's reviews/ folder (space rounds at the root). It
holds: the panel roster with the one-line why per member, the findings
table (CH rows), the triage audit outcome, and the verdict. Closing a
round is a status flip plus one script call, never a hand edit: flip the
round to in_review, then ba_compile.py approve --space <space> --doc
<round file> --verdict <converged|continue> sets verdict, status
approved with the UTC date, and locked true in the same write; the
guard hook denies any later edit (and any hand-written stamp date).
