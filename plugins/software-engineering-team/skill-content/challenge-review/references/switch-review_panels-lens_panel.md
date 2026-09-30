# Review Panels

These are the instructions of process switch `review_panels` at `lens_panel`.
A task binds this file only when the project's Process Policy selects that
value; at the default, `single_reader`, every review runs as its flow and role
files describe. Where this file and a flow or role file differ on a review
step that `data/review-panels.json` declares, this file governs.

A review panel runs one fresh, read-only reader per lens assignment, in
parallel, over the same inputs. It replaces a step's single reviewer and never
runs beside one. A flow names its step as review panel `<step>`;
`data/review-panels.json` declares that step's reader role, lenses and default
panel.

## Panel data

- `lenses` gives every lens its `id` and `focus`. A step whose writer records
  a review note also lists, in `covers`, the note sections that each lens
  owns. The writer fills those sections from that lens's result and the
  note's `panel_sections` from the merged panel result. Prose names a lens as
  lens `<id>`.
- `default_panel` lists the lens assignments of a first review; each is one
  reader. Its length is the default panel size, and every lens belongs to
  exactly one assignment.

## Readers and inputs

- A read-only reviewer role runs as its lens-tier variant: spawn
  `backlog-reviewer-lens`, `solution-reviewer-lens` or
  `design-system-reviewer-lens` where the step's reader role is
  `backlog-reviewer`, `solution-reviewer` or `design-system-reviewer`. The
  Operation counterparts run as themselves, on their own tier.
- Derive a reader's manifest with its reader role, as the step's flow does.
  A reader role that does not bind this skill, and the step's writer, add
  `--skill challenge-review`, so every panel task binds this file and the
  lens data.

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
   wide. Its assignment narrows the coverage its role file describes. A
   defect it notices outside the assignment is one line naming the lens that
   owns it; that line never replaces the owning lens's review.

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

## Backlog epic and root review

- The review panel `backlog_epic` runs for each epic and review panel
  `backlog_root` for the root, in place of the single `backlog-reviewer`.
  Every lens reader receives the same returned manifest and every named path;
  the manifest carries no lens key, so one manifest serves the whole panel.
  Recompute it with `--expected-hash <source_hash>` before accepting any
  panel finding. The metadata-recovery root review is a `backlog_root`
  panel: each reader verifies the recovery conditions and the exact delta
  through its lens.
- The manifest's `check` block carries the compiler facts readers would
  otherwise re-derive: source errors, which are empty in any returned
  manifest, the current review note's pending final-gate findings, the audit
  of its declared against expected relations, counts and each story's
  source-to-scenario map. Lens readers take these facts as given and never
  recount them; a reader audits source membership through
  `check.relation_audit`.
- The Product Owner merges findings that share one root cause as above. Each
  lens section of the review note takes its evidence and conclusion from the
  lens that covers it; Findings and Verdict come from the merged panel
  result. Wait for every epic panel before any epic write and for the root
  panel before the root review.
- A re-review reruns only the lens assignments that returned the blocking
  findings. Each reads the regenerated manifest with its findings, any cited
  evidence and the changed paths, which are the manifest files whose
  `sha256` changed; the first rerun assignment also reviews the changed text
  with its dependency context. It does not re-audit unchanged text that an
  earlier pass for the same review note already reviewed.

## Solution Design review

The review panel `solution_design` replaces the single primary
`solution-reviewer` of the review plan in
`solution-architecture/references/challenge-lenses.md`: one fresh reader per
lens assignment, whose lenses together also cover BA allocation, topology,
naming, sourcing and decision status. The primary reviewer existed only so
that loading both the entry skill and the plan could not start two full
panels; that guard stays: run exactly one primary reviewer or one panel per
review, never both. Risk-triggered specialists are still selected by the
plan; each stays independent of the writer and of every panel reader, and a
specialist is never a second panel. A re-review reruns the affected lens
assignments with the changed-text check, and the affected specialists.

## Design System review

The review panel `design_system` runs one read-only reader per lens
assignment, in parallel, each with MASTER, the catalog, page overrides and the
exact BA/Solution bindings. Each reader renders the desktop, mobile and
reduced-motion states its lens needs. Resolve every critical or major finding
before approval.

## Operation contract review

The review panel `operation_verification` reviews a Verification Contract and
review panel `operation_environment` an Environment Contract. Their lens
readers are the non-writing counterpart role, read-only and on that role's
own tier. Derive each reader's inputs with `task_inputs.py --entry configure
--role <counterpart> --mode review --skill challenge-review`. Every prompt
includes the exact contract path, accepted Solution references, the reader's
lens assignment and `SELF-CHECK`. Resolve every critical or major finding
before approval.

## Lens reader output

A lens reader returns its role file's output contract for its assigned lens:

- `lens`: the assigned lens ids; every finding also carries its lens.
- Severity is `critical`, `major` or `minor` from the step's table; the
  verdict requests changes only while a critical or major finding is open.
- A backlog lens reader may report `relation_audit` as `confirmed` when its
  sources match `check.relation_audit`.
- `SELF-CHECK` covers every supplied path and the assigned lens.

## Steps outside the panel data

- Business Analysis already runs one lens or expert per reader from
  `lens-bank.md` and `expert-casting.md`, on its roles' own tiers.
- Solution risk specialists are extra readers that `challenge-lenses.md`
  selects; they are not panel assignments.
- Delivery code review and QA, and the Experience attestation, keep one
  reader per role because their machine interfaces accept one result per
  role. They join panels later through a merge step that produces that one
  result.
