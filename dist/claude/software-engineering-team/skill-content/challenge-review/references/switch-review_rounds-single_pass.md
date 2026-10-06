# Review Rounds

These are the instructions of process switch `review_rounds` at
`single_pass`. A task binds this file only when the project's Process Policy
selects that value; at the default, `current`, every review runs the readers
switch `review_panels` selects and the loop switch `review_loop` selects.
Where this file and a flow or role file,
`switch-review_panels-lens_panel.md` or
`switch-review_loop-blocking_delta.md` differ on a review step this file
covers, this file governs.

It covers the backlog epic and root reviews, the Solution Design review, the
Design System review and the Operation contract reviews. Every reader and the
writer of those steps bind it: a role that does not bind this skill, the
Operation counterparts and every step's writer, adds
`--skill challenge-review` to its task. Business Analysis challenges keep
their own loop, the Experience attestation stays advisory, and Delivery code
review keeps its `review_loop` value.

## One reader

Each review runs one fresh, read-only reader of the step's reader role on
that role's own tier, also when `review_panels` is `lens_panel`: the reader
covers every lens of the step in one pass, and no panel or second reader runs
beside it. For Solution Design it is the primary reviewer alone; the
risk-triggered specialists do not run. For an Operation contract it is the
non-writing counterpart, `devops-engineer` for the Verification Contract and
`qa-engineer` for the Environment Contract. A revision's first review reads
only the changed text, the diff between the last approved revision and the
candidate, with its dependency context, as the Re-review section below
defines; a document with no approved revision is read whole.

## Severity

A step rates findings by its own table where it has one: the backlog Review
findings section of `product-planning/references/structured-records.md` and
the Solution Severity table of
`solution-architecture/references/challenge-lenses.md`. Design System and
Operation contract reviews use this table:

| severity | blocks | the reviewed document as written would |
|---|---|---|
| `critical` | yes | contradict an approved source or accepted decision, or drop or weaken a security, privacy, accessibility, data or irreversible-action obligation |
| `major` | no | be unverifiable, unexecutable or unowned, or let careful readers build, test or operate different behavior |
| `minor` | no | still yield the same behavior, verification and ownership |

The reader's severity stands as returned: no calibration reader runs, and
the writer never lowers or raises a returned severity. Only an open critical
finding keeps the verdict at `changes_requested`.

## Review record

The record keeps the shape of `switch-review_loop-blocking_delta.md`: a
backlog review note and an Operation contract keep `Returned Findings` and
`Accepted Minor Findings`, with the same ids, columns and placement. They keep
no `Severity Calibration` section. At this value, whatever the `review_loop`
value, `backlog_compile.py` and `operation_compile.py` require no calibration
row for a critical or major finding and accept a major finding in
`Accepted Minor Findings`; a critical finding never enters it.

## Follow-ups

A minor or major finding never starts a round. Fix it only in the writer pass
that already carries a critical fix, whose re-review reads all changed text.
Otherwise leave the reviewed text unchanged and record it as a follow-up with
an owner role, the reason the text is safe to accept and a revisit trigger,
where `switch-review_loop-blocking_delta.md` records a minor finding: the
review note's or contract's `Accepted Minor Findings` table, or the approval
gate of Solution Design and Design System, which shows each major follow-up
first.

## Re-review

After a writer pass fixes or disproves a critical finding, one re-review runs,
and only one. It reads only the open critical findings with their evidence,
the changed text and its dependency context, derived with `task_inputs.py`,
`--findings <record>`, `--base <reviewed commit>` and one `--input` per
changed path and dependency-context note, as
`switch-review_loop-blocking_delta.md` defines. Its reader is one fresh
reader of the step's reader role. It confirms each given finding is closed and
reviews the changed text; it never re-audits unchanged text. A new minor or
major finding becomes a follow-up. A critical finding still open after it goes
to the step's approval gate with its evidence, and the owner decides: approve
with it as a follow-up, order one more writer pass and re-review, or reject.
No further round starts without that decision.
