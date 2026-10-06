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

1. Run `impact_closure.py closure --docs <workspace>/docs --changed <path>...`
   with the notes or files the change touched. It returns `changed`,
   `closure`, `proven_unchanged`, `widened_by` and `graph_gaps`.
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
5. A first approval reads the whole package, as at `full`.

## 3. Navigate, then read

Every role queries the vault with the `impact_closure.py` verbs first,
starting from `impact_closure.py views --docs <workspace>/docs` and the views
it names; when they give too little or look wrong it uses its own methods
and records that it did. It follows the relations of the notes in its
closure in the constitution's tier order and reads those notes. The closure records which
tier produced each edge; a disagreement between tiers is a graph gap,
reported and healed as section 5 says. A role never scans folders or re-reads
a package to find something. An unchanged note's summary proves its approval
still holds; never infer its content from that summary.

## 4. Beyond the closure

A role that is unsure, or suspects a defect outside its closure, reads the
note or file it needs. It records each such read in its output under
`beyond_closure` with the path and the reason, and a writer passes those rows
to `impact_closure.py` so the record shows where the graph was insufficient.

## 5. Heal a missing relation

A read-only reader never writes the vault. It returns a missing, wrong or
stale relation as a finding with both notes, the text that shows the relation
and its type. The flow's writer applies it with
`impact_closure.py heal --docs <workspace>/docs --source <note> --target
<note> --kind <relation> --evidence <finding> --role <writer role>`, which
validates it against the relation contract and re-renders the generated
views; a tier disagreement with no missing relation takes
`impact_closure.py render --docs <workspace>/docs --role <writer role>`
instead. A relation added to an approved note follows that package's revision
rules, never a silent edit. Then recompute the closure in the same run: read
each note newly inside it, and refresh every review whose manifest hash
changed before its verdict counts.

## 6. Confirmation re-review

A re-review that confirms a fix reads only the fixed lines, the notes the fix
touches with their closure and the open findings it confirms, never the whole
package again. A fix that touches a shared contract widens it as section 2
says.

## Measurement

Record per review the closure size against the package size, the
beyond-closure reads with their reasons, the relations healed and the
widenings. The registry's promotion rule judges these against full replays.
