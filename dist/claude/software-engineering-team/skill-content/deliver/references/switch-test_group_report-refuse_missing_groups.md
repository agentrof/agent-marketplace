# A test run checks every test group

These are the instructions of process switch `test_group_report` at
`refuse_missing_groups`. A task binds this file only when the project's
Process Policy selects that value; at the default, `off`, the runner records a
test command's exit code, output and identity and knows no groups, as the flow
describes. Inside a Delivery read the value with `process_policy.py value
--switch test_group_report --delivery DLV-###`; the Delivery pins the policy
it runs under.

A command that stops at its first failing group shows one review round only
part of the suite, and a group that never collected reads like one that
passed. At this value the runner reads which groups ran from the command
itself.

## The contract declaration

The value acts only where the approved Verification Contract declares both
optional fields; without them every run is as at `off`.

- `test_groups`: the ids of the groups the approved test command runs, each
  once, as a list of literal ids.
- `test_group_report`: the file the command writes, as a normalized relative
  path under `AGENTROF_VERIFICATION_SCRATCH`.

`operation_compile.py check` refuses one field without the other, a group id
that is empty, padded, starts with `-` or holds a control character, a
repeated id and a path that is absolute, not normalized or leaves the scratch
directory. Both are declared through a Verification Contract revision in the
Operation flow, never in a task.

The command writes one status per declared group, with its case counts:

```json
{"schema_version": 1, "groups": {"<group id>": {"status": "passed", "passed": 12, "failed": 0, "skipped": 1}}}
```

`status` is `passed`, `failed` or `not_collected`. The command runs every
group to its end and exits non-zero when any failed; a group that fails to
collect is `not_collected`, never left out. A group of which a selection or a
reuse list leaves no case to run is `passed` with zero cases.

## What the runner records

`run --kind test`, `run --kind diagnostic_test` and `regression-run` remove
any group report before the command starts, so only that command can write
one, and read it once the command ends:

- The run's record lists every declared group under `test_groups` with its
  status and case counts, as the run result and `status` print it. A group
  the report lacks, or names without a declared status and non-negative
  counts, is `missing`, and `missing_test_groups` names each one;
  `test_group_report_problem` says why when the report is absent or of
  another shape.
- A run with a missing group is recorded not intact. The exit code stays the
  command's.
- The run's identity binds the declared groups and report path, so a record
  taken before the declaration is never reused.
- `freeze` refuses a pre-handoff run that is not intact and names the groups
  its report lacks.
- Evidence approval refuses a final test run whose identity does not bind
  the declared groups and report path, whose record lacks a declared group, or
  that records a group `missing`, `not_collected` or `failed`.

## Findings

QA, and the coordinator before the freeze, names each `failed` or
`not_collected` group in a finding with its failing cases or its collection
error, from the run's output. A `missing` group means the approved command did
not report every group it runs: that is a finding for the Verification
Contract, whose revision goes through the Operation flow. Never edit, wrap or
extend the command in a task, and never write or change the group report by
hand. The report is the command's own claim; the coverage audit and the
right-reason rule still read the run's results and output.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Delivery, record the declared groups and test cases each Item's first QA gate
run reached against the suite's, the blocking findings a later round raised in
tests the first round never ran, and the runs recorded not intact for a
missing group. The registry's promotion rule judges them over at least 3
Deliveries.
