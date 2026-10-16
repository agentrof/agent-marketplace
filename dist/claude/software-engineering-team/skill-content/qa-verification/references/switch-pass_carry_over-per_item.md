# Keep each Item's passed cases from pass to pass

These are the instructions of process switch `pass_carry_over` at
`per_item`, selected in the project's Process Policy. At `off`, the runner
names no record and keeps none.

## What the runner keeps

When the approved test or live command writes `carry-over-pass.json` into
`AGENTROF_VERIFICATION_SCRATCH`, the runner adds it to the Item's carry-over
record after the run, whatever the pass's verdict, so the cases that passed in
a failed pass are kept with the ones that failed. Each case carries its
outcome, its input paths and their digest at the pass's product commit. A
pass record of another Item is not kept, and a run whose candidate checkout
was not intact adds nothing.

## What the next pass receives

Every later `run --kind test` or `run --kind live_test` of the same Item gets
the record's path in `AGENTROF_CARRY_OVER_RECORD`, its `candidate_hash` set to
the frozen candidate, and its run identity binds the record's digest. The
project's command decides which case to carry. A missing, foreign or malformed
record never counts a case as passed: every case runs.

QA reads the carried cases the command reports as evidence of the pass that
ran them; carrying never replaces QA's independent assessment.
