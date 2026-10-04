# Remediation Bookkeeping

These are the instructions of process switch `remediation_bookkeeping` at
`compiler`. A task binds this file only when the project's Process Policy
selects that value; at the default, `writer`, the Product Owner writer
records the rechecks, the expected hashes and the preservation evidence
itself. Where this file and the flow differ on who writes that bookkeeping,
this file governs.

The command records; it decides nothing. Every fix, triage, disposition,
review-note section and verdict stays the writer's, and every recheck stays
an independent reader's. A closure row only copies what a recheck reader
returned.

## Closure tables

Each recheck reader returns, beside its findings, one closure row per
finding it was given: the finding's id, its own lens or role id, `closed` or
`open`, the `source_hash` of the manifest it read and its evidence, a
sentence that cites the note it checked with a wikilink. The coordinator
copies the rows verbatim into one JSON file under the project-local runtime
directory `.agentrof/agent-marketplace/.runtime/`, naming the review note
each belongs to:

```json
{
  "closures": [
    {"review": "backlog/epics/<epic>/reviews/round-2-epic-review.md",
     "finding": "F-3", "reader": "criteria-coverage", "result": "closed",
     "manifest_hash": "sha256:<the manifest the reader read>",
     "evidence": "[[backlog/epics/<epic>/stories/<story>/test-plan|ST-002-TP]] ST-002-TS-003 now asserts the lockout after five attempts."}
  ]
}
```

One row per finding and reader: a later recheck of the same finding by the
same reader replaces its earlier row in the file, never adds a second one.

## The command

After its content fixes, the writer runs once:

```text
backlog_compile.py record-rechecks --docs <workspace>/docs \
  --closures <closures file> --candidate <reviewed commit> --report <report file>
```

`--candidate` is the commit the review read, the pinned candidate the flow
commits before its first review; the report file lives under the runtime
directory too. In one step the command:

- writes each named review note's `Recheck Closures` table, replacing the
  section when it exists and placing it before `Accepted Minor Findings`, or
  else before `Verdict`; it refuses an approved note, a note that is not the
  current review of its epic or the root, and a row whose finding
  `Returned Findings` does not list;
- derives the review manifest of every scope it touched, each epic's and the
  root's, under the current Process Policy and records each `source_hash` as
  that scope's expected hash;
- compares every backlog file with the candidate and records which changed,
  were added or removed and how many stayed byte for byte, and fails on a
  changed or removed review note the candidate holds approved.

It writes the report as JSON. The command is deterministic: rerun it after
any change instead of editing a row, a hash or the report by hand. A wording
change in a review note reruns the command, never the model.

## Check

The writer's own check is one call with `--verify` added: it writes nothing
and fails when a review note's `Recheck Closures` or the report differ from
what the command would write now. `backlog_compile.py check` validates the
`Recheck Closures` rows of every draft review note at this value. Each
`--expected-hash` recheck the flow requires before accepting a result is
unchanged; the report's expected hashes are what those rechecks compare.

## Measurement

The project owner measures outside every task; no role acts on it. Per
remediation writer pass, record the time from the coordinator's
fixes-ready message to the writer's final answer, and compare the review
notes' closure rows with the readers' closure tables, row for row.
