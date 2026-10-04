# Epic Review Cadence

These are the instructions of process switch `epic_review_cadence` at
`overlap_calibration`. A task binds this file only when the project's Process
Policy selects that value; at the default, `wait_per_panel`, the coordinator
starts the next epic review only after the previous review's calibration and
its own record of that review. Where this file and the flow differ on when an
epic review starts, this file governs.

It changes when work starts, never what anyone reads or decides. Every epic
review keeps its readers, its manifest and its `--expected-hash` recheck as
switch `review_panels` and switch `review_manifest_scope` set them, and
calibration keeps its reader, tier and rules as switch `review_loop` sets
them.

## When the epic reviews run one after another

The flow lets independent epic reviews run in parallel against unchanged
inputs. When the host's agent limit makes the coordinator queue them, run the
queue this way:

1. Start the next epic review as soon as the current review's last reader
   returns. Never wait for that review's calibration or for the
   coordinator's own record of it.
2. At `review_loop` `blocking_delta`, start one calibration reader for a
   reader's critical and major claims as soon as that reader returns, while
   the other readers of the review still run. Each claim is still ruled once
   by a fresh calibration reader, neither the writer nor a reader that
   returned a finding of the review, on the claiming reviewer's role and own
   tier, with exactly the inputs the review loop names. A claim a later
   reader of the same review repeats from a different lens joins the record
   of that review; it is not ruled twice.
3. Within the host's agent limit, a free slot goes first to a reader of the
   review in progress, then to the next epic review's readers, then to a
   calibration reader. Calibration only has to finish before the writer
   pass, which waits for every epic review anyway.
4. No writer action starts until every epic review and every calibration of
   its claims has returned. Nothing writes to the backlog while a reader or a
   calibration reader runs, so the inputs every running reader reads stay the
   frozen candidate it was given.

## Record at a review boundary

At a review boundary the coordinator writes only the claim record that
calibration and the writer pass read, under the project-local runtime
directory `.agentrof/agent-marketplace/.runtime/`, one JSON file per epic
review with this fixed shape and nothing else:

```json
{
  "epic": "EP-003",
  "manifest_source_hash": "sha256:<the manifest the readers read>",
  "findings": [
    {"id": "F-1", "lens": "criteria-coverage", "severity": "major",
     "anchor": "backlog/epics/<epic>/stories/<story>/test-plan.md",
     "evidence": "<the reader's evidence, verbatim>",
     "impact": "<the reader's impact, verbatim>"}
  ]
}
```

Copy each returned finding verbatim; triage, merging and every review-note
section wait for the writer pass, which writes them from these records.
A calibration result is added to the record of its review when it returns.

## Measurement

The project owner measures outside every task; no role acts on it. For each
transition between two epic reviews, record the time from the earlier
review's last reader return to the next review's first reader start, and per
claim the calibration minutes. On a frozen candidate, compare the confirmed
critical and major findings with a `wait_per_panel` run of the same inputs.
The registry's promotion rule judges these over at least 2 backlog revisions.
