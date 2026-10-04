# QA's final test run runs in partitions

These are the instructions of process switch `test_engines` at
`partitioned`. A task binds this file only when the project's Process Policy
selects that value; at the default, `single`, QA's final test run is one
approved command under the Item's environment lock, as the flow describes.
Inside a Delivery read the value with `process_policy.py value --switch
test_engines --delivery DLV-###`; the Delivery pins the policy it runs under.

The Item's environment lock keeps every command serial because every command
shares one environment. Each command already runs in its own private clone, so
only the environment is shared. At this value the Operation contracts declare
several isolated test engines, and QA's final test run spreads the suite over
them.

## The contract declaration

The value acts only where the approved Verification Contract declares
`test_partition_command`; without it every run is as at `single`. The
Verification Contract then declares, all or none:

- `test_partition_command`: the approved command that runs one partition. It
  reads `AGENTROF_TEST_PARTITION`, a JSON file the runner writes with the
  candidate hash, the partition id, its groups, its engine id and the reused
  test ids, and `AGENTROF_TEST_ENGINE`, the engine id, and runs in
  `test_workdir`.
- `test_engines`: the engine ids the gate may use.
- A `Test Partitions` table with the columns `partition`, `groups` and
  `profile`: each partition's id, the groups it runs, comma-separated, and the
  runtime profile it needs.
- Optional `shared_profiles`: the profiles whose partitions may share one
  engine at once, such as hermetic tests that use no engine. A partition of
  any other profile takes an engine alone.

It also declares `test_groups`, the groups the table partitions.
`operation_compile.py check` refuses one of the three without the others, a
group in no partition or in two, a group `test_groups` does not declare, a
partition named twice, ids other than letters, digits, `.`, `_` and `-`, and
a shared profile the table does not use. The Environment Contract declares the
same ids in its own `test_engines`, each engine provisioned in isolation: its
own cluster or project name, ports, volumes and registry namespace. The runner
refuses an engine the Environment Contract does not declare. Both contracts
change only through the Operation flow, never in a task.

## The partitioned run

`run --kind test` derives the plan from the approved contract and refuses a
plan the contract check would refuse. It then:

1. Orders the partitions longest first by each one's last recorded duration in
   the Item's runtime, one never recorded first.
2. Schedules them over the engines: never two partitions of an exclusive
   profile on one engine at once, and never more partitions at once than
   there are engines.
3. Runs each partition in its own private clone of the frozen candidate, with
   `AGENTROF_TEST_PARTITION`, `AGENTROF_TEST_ENGINE` and its own
   `AGENTROF_VERIFICATION_SCRATCH` under the scratch's `partitions/<id>`
   directory. No other command receives the two partition variables. A
   reused test id leaves every partition's selection, as the file names it.
4. Holds the Item's environment and verification command locks from the
   first partition's start until the last one ends, and releases them on
   every exit path.
5. Lets every partition run to its end: a partition that fails, that cannot
   start or, at `test_group_report` `refuse_missing_groups`, whose group
   report lacks one of its groups never stops another, and is recorded failed.
6. Writes one record: per partition its id, engine, exit code, intactness,
   duration and output file with its hash; the merged output; and an exit
   code of 0 only when every partition passed intact. Its identity binds the
   partition plan, the partition command, the engines, the shared profiles
   and the declared environment, never which engine ran which partition.

Evidence approval refuses a final test run that does not run the declared
plan, that lacks a declared partition, holds one twice, holds a failed or not
intact one, or ran another command than the approved partition command, such
as the single `test_command`. QA's result names the partition command as the
full suite's command.

`regression-run` and `run --kind diagnostic_test` stay one command each.

## QA's reading

Read each partition's output and the JUnit its own scratch directory holds,
as for the single run. A failure that appears only beside another partition,
or only on one engine, is a finding: an isolation leak between engines or an
order dependence between groups, never a reason to rerun until green. The
coverage audit and the right-reason rule still cover every partition.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Delivery, record the wall clock of QA's final test run beside the sum of its
partitions' durations, which the run's record holds, the host's CPU and
memory pressure during the run, and every failure traced to an isolation leak
between engines or an order dependence between groups. The registry's
promotion rule judges them over at least 3 Deliveries.
