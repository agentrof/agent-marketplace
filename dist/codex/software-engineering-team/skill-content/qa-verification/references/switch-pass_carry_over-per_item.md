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

The record uses `schema_version: 1`, nonempty `project_key`, `delivery_id`,
`item_id` and `run_id`, the exact `product_commit`, and `carried` and `fresh`
case lists. Each case has `case_id`, `outcome`, repository-relative normalized
`input_paths` and a full `sha256:` `input_digest`; a case appears only once.
The runner stamps a missing or null `candidate_hash` with the current candidate
and rejects a different hash. A repeated `run_id` replaces its previous entry.
Malformed history is discarded before the command receives the path, so it
falls back to running every case.

## What the next pass receives

Every later `run --kind test` or `run --kind live_test` of the same Item gets
the record's path in `AGENTROF_CARRY_OVER_RECORD`, its `candidate_hash` set to
the frozen candidate, and its run identity binds the record's digest. The
project's command decides which case to carry. A missing, foreign or malformed
record never counts a case as passed: every case runs.

QA reads the carried cases the command reports as evidence of the pass that
ran them; carrying never replaces QA's independent assessment.
