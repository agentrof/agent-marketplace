# Review Panels

A review panel runs one fresh, read-only reader per lens assignment, in
parallel, over the same inputs. It replaces a single all-lens reader and
never runs beside one. A flow names its step as review panel `<step>`;
`data/review-panels.json` declares that step's reader role, lenses and
default panel.

## Panel data

- `lenses` gives every lens its `id` and `focus`. A step whose writer records
  a review note also lists, in `covers`, the note sections that each lens
  owns. The writer fills those sections from that lens's result and the
  note's `panel_sections` from the merged panel result. Prose names a lens as
  lens `<id>`.
- `default_panel` lists the lens assignments of a first review; each is one
  reader. Its length is the default panel size, and every lens belongs to
  exactly one assignment.

## Dispatch

1. Derive the step's inputs once, exactly as its flow says. Every reader
   receives the same complete input set or manifest, the constitution, the
   step's severity rules, `SELF-CHECK` and exactly one assignment: its lens
   ids and focus text. Never pass another reader's reply, conversation
   history or the writer's interpretation.
2. Start every reader of the panel together through the host's parallel
   agent invocation. Independent panels over unchanged inputs may run
   together. Wait for every reader before any writer action.
3. A reader judges only through its assignment and goes deep rather than
   wide. A defect it notices outside the assignment is one line naming the
   lens that owns it; that line never replaces the owning lens's review.

## Findings and verdict

Each finding carries its lens id and a severity. A step's own severity table
governs where it has one: backlog Review findings and the Solution Severity
table. Other steps use this table:

| severity | blocks | the reviewed document as written would |
|---|---|---|
| `critical` | yes | contradict an approved source or accepted decision, or drop or weaken a security, privacy, accessibility, data or irreversible-action obligation |
| `major` | yes | be unverifiable, unexecutable or unowned, or let careful readers build, test or operate different behavior |
| `minor` | no | still yield the same behavior, verification and ownership |

Imprecision that could mislead a careful reader is major, never minor.

The owning writer triages with `triage.md`. Findings from different lenses
that share one root cause merge into one finding only for triage clarity:
the merged finding keeps every reader's evidence and the highest returned
severity. Merging mints no id and keeps no counter or transcript, and the
writer never lowers a returned severity.

The panel verdict is approved only when every assignment has returned, no
lens has an open critical or major finding and the owning compiler checks
named by the flow are green. A minor finding never blocks and never starts a
round; the step's own rule records or defers it.

## Re-review

After a writer pass fixes or disproves a critical or major finding,
regenerate the step's inputs and rerun only the assignments that returned
such a finding. Each rerun reader confirms that its findings are closed. The
first rerun assignment in `default_panel` order also performs the
changed-text check: it reads every changed path with its dependency context
through all of the step's lenses and names the owning lens of any new
finding. A newly exposed risk in a lens that did not rerun adds that lens's
assignment. No other assignment reruns, and no clean extra round follows
once no critical or major finding is open.

## Steps outside the panel data

- Business Analysis already runs one lens or expert per reader from
  `lens-bank.md` and `expert-casting.md`.
- Solution risk specialists are extra readers that `challenge-lenses.md`
  selects; they are not panel assignments.
- Delivery code review and QA, and the Experience attestation, keep one
  reader per role because their machine interfaces accept one result per
  role. They join panels later through a merge step that produces that one
  result.
