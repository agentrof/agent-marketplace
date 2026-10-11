# QA's final test run accepts the frozen record

These are the instructions of process switch `own_target_reuse` at
`accept_record`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, QA's final test run runs every own
Test Plan target of the Item itself, as the flow describes. Inside a Delivery
read the value with `process_policy.py value --switch own_target_reuse
--delivery DLV-###`; the Delivery pins the policy it runs under.

The value acts on the pre-handoff regression run that switch
`pre_handoff_regression` at `touched_suites` makes before the freeze: the run
the freeze accepted, which ran the Item's own Test Plan targets with the
earlier stories' touched targets on the frozen tree. Before the freeze the
coordinator repeats that run after each repair until it passes, so a frozen
candidate has passed it. At `off` of that switch no such run exists and
nothing is reused.

## What the run reuses

QA runs `run --kind test` as at `off`, with no spot-run file, which the
runner refuses at this value. The runner reuses the accepted run under the
bindings the earlier stories' targets need: the run passed intact on the
frozen tree, with the selection and the approved command derived for the
frozen candidate, in the declared environment of QA's run, and no longer ago
than final evidence stays fresh. It then takes every own Test Plan target of
the Item from it as well and keeps none for the command. Every reused id goes
to `AGENTROF_REUSED_TESTS`, and the run's identity records the reused own
targets under `reused_pre_handoff.own_targets`, with an empty
`spot_test_ids`, so QA's full-suite evidence binds them. The approved test
command still runs every target the accepted run did not cover.

- QA starts no second run of a reused target and needs no diagnostic run to
  confirm one. Its only check of the reuse is that the record binds the exact
  frozen candidate: the run's `reused_pre_handoff.evidence_hash` is the
  frozen session's accepted run and the result carries no
  `pre_handoff_reuse` refusal.
- When no accepted run binds the frozen candidate, or a binding differs,
  nothing is reused, the approved test command runs the same selection
  itself, every suite included, and the result names the binding as
  `pre_handoff_reuse` and says so as `own_target_reuse`.
- `run --fresh` reuses nothing and runs every suite. It is never needed to get
  past the coverage audit; see below.
- Evidence approval checks the reuse as recorded: the Delivery runs
  `own_target_reuse` at `accept_record`, the reused own targets are every
  automation target of the Item's own Test Plan and the spot-run targets are
  empty.
- `approve-item-evidence` records the reuse in the Item's verification
  record, below the earlier stories' block: the reused own targets, `none` as
  the spot-run targets, the reused run's number in the pre-handoff list and
  its evidence hash, or `none.` when QA's final test run reused no own target.

## QA's audit of the reused targets

The coverage audit and the right-reason rule still cover every own target.
The final test run's results hold only the targets it ran, so pass the
accepted run's JUnit to `scenario_report.py` beside the final run's. The run
is the `regression-run` with the reused evidence hash in `pre-handoff.json`
beside the Item's verification session, and its raw output is that record's
`output_file`. Read the accepted run's output for each reused own target as
for a target QA ran: a skip, a retry or a warning it cannot explain is a
finding, and so is any target QA cannot tell passed for the right reason.

A reused case that the accepted run deferred to its live group, because that
run ran with live tests off as the Verification Contract declares for the
default run, keeps that deferral exactly as a fresh default run would: it is
not NO-TEST for the default run, and its coverage comes from the live group
run that executes it, whose JUnit QA passes to the audit beside the others.
Only a reused case that the accepted run recorded failed, skipped for any
other reason or not at all is NO-TEST. An approved command that cannot tell a
live deferral from another skip, or that writes each run's JUnit to the same
file, is a finding for the Verification Contract, never a reason to rerun the
suite with `--fresh` or to drop the audit.

QA's report names in Suite Results the reused own targets and the accepted
run they came from, beside the reused earlier stories.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Delivery, record the wall clock of QA's final test run per round, the own
targets it reused, which the Item's verification record lists, and every
defect found after a passing QA gate, in a later review round, in the
Delivery Review or after merge, in an own target that gate reused. The
registry's promotion rule judges them over at least 3 Deliveries.
