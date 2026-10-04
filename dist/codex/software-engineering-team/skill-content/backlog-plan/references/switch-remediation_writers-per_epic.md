# Per-Epic Remediation Writers

These are the instructions of process switch `remediation_writers` at
`per_epic`. A task binds this file only when the project's Process Policy
selects that value; at the default, `single_writer`, one Product Owner writer
applies every accepted finding of every epic review as the flow describes.
Where this file and the flow differ on who applies the epic reviews' findings,
this file governs.

The Product Owner stays the only backlog writer role, and every writer pass
keeps the flow's rules: triage, the preserved severities, the epic review
note it writes and the compiler check after its writes. Only the number of
writer instances and what each reads change.

## When

Wait for every epic review to return, and for every calibration of its
claims where switch `review_loop` runs one, before any writer starts, as the
flow requires. The root review still waits for every epic package to be
green.

## Group the findings

The coordinator groups the accepted findings, verbatim, by the notes each
one names:

- An epic's finding: every note it names is that epic's `epic.md`, one of its
  stories or test plans, or its current review note.
- A cross-epic finding: it names notes of two or more epics, the backlog root
  or a root review note, or the backlog as a whole, such as a dependency
  cycle or a duplicate id.

Write one JSON findings record per epic with findings and one for the
cross-epic findings under the project-local runtime directory
`.agentrof/agent-marketplace/.runtime/`, each with a `findings` list whose
items keep the reader's id, severity, anchor and text.

## Per-epic writers

1. Derive each epic writer's task with
   `task_inputs.py --entry backlog-plan --role product-owner --mode revise --epic <EP-ID> --findings <epic record>`.
   Its manifest is that epic's review scope at the `review_manifest_scope`
   value in force, so at `bounded` it reads the epic reader's read set, and it
   names `remediation_writers: per_epic`. Its write scope is the epic's
   `epic.md`, stories, test plans and current review note, never another
   epic's notes or the backlog root.
2. Start every epic writer together within the host's agent limit; queue the
   rest. No two writers ever write the same note: the write scopes of epic
   writers are disjoint, and the cross-epic writer starts only after every
   epic writer has returned.
3. A writer may read its manifest as it works; it never reads the whole
   backlog before its first edit.
4. A writer that needs to change a note outside its write scope does not
   change it: it returns that finding with the note and the reason, and the
   coordinator adds it to the cross-epic record.
5. Each writer runs `backlog_compile.py check --docs <workspace>/docs --json`
   after its writes and resolves the findings in its own notes. A finding in
   another epic's notes is that writer's work in progress, never its own.
6. Under switch `mechanical_pass_tier` at `mechanical`, an epic writer whose
   findings each name their exact fix runs as the Product Owner's mechanical
   variant on that switch's terms, its task derived with `--pass-kind apply_findings`
   and one `--input` per document the findings change.

## Cross-epic writer

After every epic writer has returned, one Product Owner writer applies the
cross-epic record, the findings the epic writers returned included. Derive
its task without `--epic`:
`task_inputs.py --entry backlog-plan --role product-owner --mode revise --findings <cross-epic record> --input workspace/docs/<note>`,
with one `--input` for each note the findings name and each review note that
records them. Its write scope is exactly those inputs. A writer that needs
another note names it and why; add it with `--input` and rerun the task. Skip
this writer when the record is empty.

## After the writers

Run `backlog_compile.py check --docs <workspace>/docs --json` once every
writer has returned. Each fix's re-review runs exactly as the step's review
loop says, against the regenerated manifests, and the root review still reads
its own manifest after every epic package is green.

## Measurement

The project owner measures outside every task; no role acts on it. Per
backlog revision, record the wall clock from the last epic review's return to
the last writer's return, each writer's input tokens and context compactions,
and the findings each writer returned for another scope, and compare them
with a `single_writer` revision. The root review's findings after the
remediation are the guard: every accepted finding resolved and no new
cross-epic defect.
