# Impact-Closure Reading

These are the instructions of process switch `review_scope` at
`impact_closure`. A task binds this file only when the project's Process
Policy selects that value; at the default, `full`, every role reads the
complete package its step names. Every task of an owning flow binds it.

The closure scopes the default read; it never caps it. Severity, triage,
the review loop and every approval gate stay exactly as the flow and
switches `review_loop` and `review_rounds` define them.

## 1. Fast fail before any reader

Run the owning compiler's structural check named at the flow's review step
before any reader is spawned, in the mode that leaves review-completion
findings pending where the flow has one. A structural finding returns to the
writer first; no reader is ever spent on a package its compiler refuses.

## 2. Derive the closure

1. The change is every vault file changed in Git since the package's last
   approved revision, deletions included, never only the notes that lost a
   stamp. Run `impact_closure.py closure --docs <workspace>/docs --changed
   <path>...` with those paths. It returns `changed`, `closure`,
   `proven_unchanged`, `widened_by` and `graph_gaps`; every front-matter
   reference that names a note is an edge, and one that names none is a gap.
2. The reader's task binds every `closure` path in full and every
   `proven_unchanged` entry as its summary and `approval_hash`. Derive it
   with `task_inputs.py --changed <note> --base <approved commit>`; a
   re-check given `--findings` and the reviewed commit as `--base` derives
   only the fix delta. Backlog epic and root readers take the closure
   `backlog_review_inputs.py` derives, and code review and QA the one the
   `delivery_verification.py` manifest derives.
3. A note in `graph_gaps` is read in full: the graph cannot prove it
   unaffected.
4. `widened_by` names why the closure grew, a shared contract or the process
   policy touched; report it in the progress message.
5. A first approval reads the whole package, as at `full`. Unavailable approval
   history also requires full reading.

## 3. Navigate, then read

Every role starts with the default `project_reading` plan in its manifest and
batch-reads the selected units with the matching context reader. Expand an
incomplete plan. When context is insufficient or looks wrong, use targeted
manual search, reads and relation discovery on the role's initiative or parent
direction, recording the extra sources and reasons. All closure reading
obligations remain in force.

For unresolved relationships, use `vault_query.py` and
`impact_closure.py views --docs <workspace>/docs`, following the
constitution's tier order. The closure records which tier produced each edge;
a disagreement between tiers is a graph gap, reported as section 5 says.
An unchanged note may appear as a summary; never infer its content from that summary.
A summary never substitutes for required source reading or approval gates.

## 4. Beyond the closure

A role that is unsure, or suspects a defect outside its closure, reads the
note or file it needs. It records each such read in its output under
`beyond_closure` with the path and the reason, and a writer passes those rows
to `impact_closure.py` so the record shows where the graph was insufficient.

## 5. Report a missing relation

`impact_closure.py` and `vault_query.py` are read-only: they index the vault
and tell a role where to look, and never write a vault file. Each
`graph_gaps` row carries its evidence and a `suggested_fix`. A read-only reader never writes the vault. A role returns
a gap, or any missing, wrong or stale relation it finds, as a finding with
both notes, the text that shows the relation, its type and the suggested
fix. The flow's writer applies the fix through the owning compiler during
its normal revision; a relation added to an approved note follows that
package's revision rules, never a silent edit. Then recompute the closure in
the same run: read each note newly inside it, and refresh every review whose
manifest hash changed before its verdict counts.

## 6. Confirmation re-review

A re-review that confirms a fix reads only the fixed lines, the notes the fix
touches with their closure and the open findings it confirms, never the whole
package again. A fix that touches a shared contract widens it as section 2
says.

## Measurement

Record per review the closure size against the package size, the
beyond-closure reads with their reasons, the relation gaps reported and fixed and the
widenings. The registry's promotion rule judges these against full replays.
