# QA's final test run reuses the Item's own targets

These are the instructions of process switch `own_target_reuse` at
`spot_run`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, QA's final test run runs every own
Test Plan target of the Item itself, as the flow describes. Inside a Delivery
read the value with `process_policy.py value --switch own_target_reuse
--delivery DLV-###`; the Delivery pins the policy it runs under.

The value acts on the pre-handoff regression run that switch
`pre_handoff_regression` at `touched_suites` makes before the freeze: the run
the freeze accepted, which ran the Item's own Test Plan targets with the
earlier stories' on the frozen tree. At `off` of that switch no such run
exists and nothing is reused. That reference's rules for QA's final test run
hold, except that QA runs itself only the own targets it spot-runs and those
the runner keeps with them; the run supplies the rest.

## The spot run

Before its final test run, QA chooses the own Test Plan targets it runs
itself, at least one: those whose failure would cost most by the risk order of
its plan, and every target whose result in the accepted run it cannot explain.
It writes them to a file in the manifest's scratch directory and passes it with
`run --kind test --spot-run-file <file>`:

```json
{"schema_version": 1, "candidate_hash": "<frozen candidate hash>", "spot_test_ids": ["<own target>"]}
```

The runner refuses a file outside the verification scratch, one that binds
another candidate, declares other keys, names no target, names one twice or
names a target that is no `automation_target` of an `automation: required`
scenario in the Item's own Test Plan. It refuses the option with `--fresh`,
which reuses nothing, with any kind but `test` and at `own_target_reuse` `off`.
No command receives the file; the run's identity binds the ids it names.

## What the run reuses

With the file, the runner reuses the accepted run under the bindings the
earlier stories' targets need: the run passed intact on the frozen tree, with
the selection and the approved command derived for the frozen candidate, in
the declared environment of QA's run, and no longer ago than final evidence
stays fresh. It then takes every own target from it too, apart from the
spot-run targets and every own target that is, prefixes or lies under one of
them, or under such a target in turn, which the approved test command runs;
an earlier story's target that overlaps one of those runs as well. Every
reused id goes to `AGENTROF_REUSED_TESTS`, and the run's identity records the
reused own targets and the spot-run targets under
`reused_pre_handoff.own_targets`, so QA's full-suite evidence binds them.

- Without the file the run reuses only the earlier stories' targets, and its
  result says so as `own_target_reuse`; so it does when the spot-run targets
  keep every own target.
- When a binding differs nothing is reused, every suite runs, and the result
  names the binding as `pre_handoff_reuse`.
- Evidence approval checks an own-target reuse as recorded: the Delivery runs
  `own_target_reuse` at `spot_run`, the reused own targets and the spot-run
  targets are disjoint, non-empty sets of automation targets of the Item's own
  Test Plan, and no reused id is, prefixes or lies under a target the command
  had to run.
- `approve-item-evidence` records the reuse in the Item's verification record,
  below the earlier stories' block: the reused own targets, the spot-run
  targets, the reused run's number in the pre-handoff list and its evidence
  hash, or `none.` when QA's final test run reused no own target.

## QA's audit of the reused targets

The coverage audit and the right-reason rule still cover every own target.
The final test run's results hold only the targets it ran, so pass the
accepted run's JUnit to `scenario_report.py` beside the final run's. The run
is the `regression-run` with the reused evidence hash in `pre-handoff.json`
beside the Item's verification session, and its raw output is that record's
`output_file`. An approved command that writes each run's JUnit to the same
file leaves the reused targets without results: the audit then reports them as
NO-TEST and fails, which is a finding for the Verification Contract, never a
reason to drop the audit. Read the accepted run's output for each reused own
target as for a target QA ran: a skip, a retry or a warning it cannot explain
is a finding, and so is any target QA cannot tell passed for the right reason.

QA's report names in Suite Results the reused own targets, the spot-run
targets and their results, beside the reused earlier stories.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Delivery, record the wall clock of QA's final test run per round, the own
targets it reused and spot-ran, which the Item's verification record lists,
and every defect found after a passing QA gate, in a later review round, in
the Delivery Review or after merge, in an own target that gate reused. The
registry's promotion rule judges them over at least 3 Deliveries.
