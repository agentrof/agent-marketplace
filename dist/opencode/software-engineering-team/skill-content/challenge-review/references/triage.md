# Triage: Findings Into Canonical Documents

Challenge findings are live review input, not durable project state. The
owning persona triages every returned finding after all readers finish, and is
the only writer. The approved canonical documents and compiler result are the
lasting evidence.

## Dispositions

| disposition | meaning | required action |
|---|---|---|
| covered | canonical evidence already handles it | cite the exact document and stable ids in the live response |
| fix | a real gap | edit the owning document, then re-run its compiler |
| assumption | plausible knowledge awaits confirmation | mint or update the stage's canonical assumption structure |
| question | the project decision authority must rule | mint or update the stage's canonical open-question or decision structure |
| rejected | evidence disproves or excludes the finding | state the evidence-backed reason in the live response |

Rules:

- Severity stays exactly as returned. Disposition is the owner's judgment;
  severity is not.
- Covered and rejected findings need exact evidence in the live triage. They
  create no file merely to prove the review occurred.
- Fix, assumption and question dispositions must land in the canonical
  document structure with existing stable identifiers and relations.
- Merge duplicate findings only for triage clarity. Do not mint challenge ids,
  maintain counters, or preserve reviewer transcripts.
- Re-run a fresh, targeted reviewer after a blocking fix. A compiler-green
  document alone proves structure; the fresh reader confirms the evidence gap
  is actually closed.

## Completion

Challenge is complete when every selected reader has returned, no blocking
finding remains, the canonical documents contain every accepted resolution,
and the owning compiler and vault checks are green. The normal approval verb
then stamps the final documents. No reviewer-state field, transcript or audit
note participates in the gate.
