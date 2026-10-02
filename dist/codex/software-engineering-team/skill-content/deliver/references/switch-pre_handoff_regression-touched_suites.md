# Pre-handoff regression run

These are the instructions of process switch `pre_handoff_regression` at
`touched_suites`. A task binds this file only when the project's Process
Policy selects that value; at the default, `off`, the implementation hands the
candidate to Code Review and QA after its own Item's tests, as the flow
describes. Inside a Delivery read the value with `process_policy.py value
--switch pre_handoff_regression --delivery DLV-###`; the Delivery pins the
policy it runs under. The run applies to an Item whose approved plan verifies
with `parallel_snapshot_v1`, the schedule whose candidate `freeze` binds.

The implementation roles still prove their own Item's tests. This run adds the
suites of the earlier stories that the change touches, which no
implementation role is asked to run.

## Selection

After the implementation commit and before the freeze, the coordinator derives
the selection with `scripts/delivery_verification.py --worktree <item-root>
regression-selection --delivery DLV-### --story <story>`. It reads the exact
committed candidate, so commit every product and test change first; any
uncommitted change other than the two evidence reports refuses it. It takes
the Item's environment lock while it derives and writes the selection, so
while another command holds that lock, a pre-handoff run included, it refuses
with `DELIVERY_ENVIRONMENT_BUSY` and names the holder.

- An earlier story is one with an Item that is `integrated` in a Delivery the
  candidate holds as merged, whose `path_claims` hold or lie under a path the
  candidate changes against its `integration_base_commit`. A cancelled Item,
  a Delivery that has not merged and the current Delivery are not read.
- The selection holds the `automation_target` of every `automation: required`
  scenario in each earlier story's Test Plan and in the Item's own Test Plan,
  as `affected_test_ids` in the schema of the approved diagnostic adapter. It
  is written to `scratch/pre-handoff-selection.json` in the Item's
  verification runtime, and while the candidate is unchanged QA can pass it to
  `run --kind diagnostic_test --selection-file`.
- An earlier story's Test Plan is the revision its Item integrated, the one
  whose digest the Item records as `test_plan_source_hash`: the candidate's
  own Test Plan while it is that revision, else that revision read from the
  candidate's Git history. A backlog revision made since for a later Delivery
  changes nothing until an Item integrates it. When the candidate holds a
  later integration of the same story, of a merged Delivery or of the current
  one, the newest integrated revision counts.
- The output names each earlier story with the Delivery that merged it, the
  Test Plan revision and the commit it was read from, the claims and changed
  paths that touch it and the targets it adds, the Item's own targets and the
  command the run takes.
- It refuses, instead of dropping a suite, when Git cannot decide whether a
  Delivery merged, when a Delivery, Item or Delivery Review record cannot be
  read, when neither the candidate nor its history holds the Test Plan
  revision an earlier Item integrated and when a target is no literal test
  id.

A story whose tests exercise code outside its own path claims is not selected;
when the Item changes such code, name that suite to QA for its first gate.

## Run

Run the selection with `scripts/delivery_verification.py --worktree
<item-root> regression-run --delivery DLV-### --story <story>`. It takes the
Item's environment lock for the whole run, as `lane-run`,
`regression-selection` and QA's `run` and `environment` take it for theirs:
while another command holds it, `regression-run` refuses with
`DELIVERY_ENVIRONMENT_BUSY` and names the holder, and while the run holds it,
each of them and `freeze` refuse the same way and name the run. It also
refuses while Code Review or QA still reads a frozen candidate. The run reads
the Item worktree only while it derives the selection again and clones the
candidate, and only then do writes to the worktree wait: each one the reader
barrier guards refuses with `DELIVERY_ENVIRONMENT_BUSY` and names the run,
and only the verification scratch stays writable. Once its command runs in the
private clone, the worktree takes writes again. A commit made then is a new
candidate: the run is recorded against the tree it cloned and `freeze` checks
the current one, so the new candidate needs a run of its own.

- With an approved `diagnostic_test_command`, it runs that adapter with the
  selection in `AGENTROF_DIAGNOSTIC_TESTS`, the normalized data QA's
  diagnostic receives, `selected_test_ids` included. As in QA's run, a
  command that changes that file or `scratch/pre-handoff-selection.json`
  leaves the run with `selection_intact` false, so it does not pass.
- Without one, or when the selection is empty, it runs the full approved
  `test_command`, as the flow's fallback says.

Like QA's first gate below, the run reports every failing group of what it
runs where the approved command allows it, so one repair queue holds every
failure, and a group that fails to collect is a failed group, never one left
out. A command that stops at its first failing group or drops a group it could
not collect is a finding for the Verification Contract, whose revision goes
through the Operation flow; never edit, wrap or extend it in a task.

The command runs in a private checkout of the exact candidate commit, under
the Item's environment lock, so no ignored file of the Item worktree reaches
it, and without any `PYTHONPATH`, `PYTHONHOME` or `NODE_PATH` entry that
resolves outside that checkout, which it reports as `dropped_search_paths`. A
runtime Item's tests may need the services of the approved Environment
Contract; bring them up first, as the implementation does for its own tests.
Each run is recorded against the candidate's tree: its command, selection,
earlier stories, exit code, raw output, wall clock and the declared
environment it ran in, the variables run evidence binds, kept in
`pre-handoff.json` beside the Item's verification session. The result names
the raw output as `output_path`.

## Repair before the freeze

A failing run is repaired before the freeze, never handed to the readers. Read
the raw output and give each failure, as one repair queue, to the
implementation role whose change caused it, or at `implementation_schedule`
`parallel_lanes_v1` to the lane whose scope holds that change. Commit the
repair and run `regression-run` again on the new candidate. A failure the Item
cannot repair inside its claims is a blocking question for the owner, never a
reason to skip the run. A failure whose cause lies outside the candidate, such
as services that were down, is fixed there and the run repeated on the same
candidate.

## Freeze

`freeze` refuses with `DELIVERY_PRE_HANDOFF_MISSING` until the latest
`regression-run` on the exact candidate tree, with the selection and command
derived for it, passed. A new commit, a changed selection or a changed approved
command therefore needs a new run. While a command holds the Item's
environment lock, a pre-handoff run included, `freeze` refuses with
`DELIVERY_ENVIRONMENT_BUSY` and names the holder, so a run still going is
never passed over. The session the freeze writes keeps the
accepted run under `pre_handoff`, with the earlier stories it covered, and its
wall clock as `metrics.pre_handoff_seconds`; `status` shows both to the
readers. It also carries every run of the Item so far, failed runs included,
as `pre_handoff_history`, each run once however often a tree is frozen, so a
run record moved aside loses no run a freeze already carried.
`approve-item-evidence` records that history with any later run in the Item's
verification record, each run with its candidate tree, kind, result, exit
code, wall clock and the earlier stories it ran. Until it does, the runs exist
only in the Item's verification runtime, in its session and in
`pre-handoff.json`, so clearing that runtime before then loses them.

## QA's final test run reuses the run

QA's `run --kind test` on the frozen candidate takes the earlier stories'
targets from the run the freeze accepted, instead of running them a second
time on the same tree. The runner derives the frozen candidate's selection
again and reuses the run only when it passed intact on the frozen tree, with
that selection and the approved command derived for it, in the declared
environment of QA's run, and no longer ago than final evidence stays fresh,
the verification policy's `raw_evidence_max_age_seconds`. `run --fresh`
reuses no run and runs every suite. It writes those targets, apart from the
Item's own Test Plan targets and every earlier target that prefixes one or
lies under one as text, which QA always runs itself, to `reused-tests.json` in
the Item's verification runtime and names that file in
`AGENTROF_REUSED_TESTS`: `schema_version: 1`, the `candidate_hash`, the run's
`pre_handoff_evidence_hash` and `reused_test_ids`. No other command receives
the variable, an inherited one included. As with a diagnostic selection, a
command that changes that file leaves the run with `selection_intact` false,
so it does not pass.

- An approved test command that skips the listed ids runs only the Item's
  own story and every suite the run did not cover, also when it skips them by
  node id prefix, as pytest's `--deselect` does. One that ignores the file
  runs everything, as before; making it skip them is a Verification Contract
  revision through the Operation flow, never an edit in a task.
- The run's identity records `reused_pre_handoff`: the reused run's evidence
  hash and the reused test ids by earlier story, so QA's full-suite evidence
  binds them. When a binding differs, nothing is reused, the approved command
  runs every suite, and the result names the binding as `pre_handoff_reuse`.
- `approve-item-evidence` records the reuse in the Item's verification record,
  below the pre-handoff runs: each reused earlier story with its test ids, the
  reused run's number in that list and its evidence hash, or `none.` when QA's
  final test run reused nothing.
- Evidence approval checks the reuse as recorded: the frozen session's
  accepted run, passed intact on the frozen tree, still fresh at approval, as
  QA's own record must be, of the approved command and in the same declared
  environment, and only test ids that run selected, none that is, prefixes or
  lies under one of the Item's own.
- QA may spot-run one reused group per Delivery through `run --kind
  diagnostic_test` with that story's targets, to find a flake the reuse would
  hide. QA's report names in Suite Results each reused earlier story, the
  reused run's evidence hash and the result of any spot run.

## QA's first gate

QA runs its first gate on a candidate so that it reports every failing group,
not only the first, where the approved command allows it, so one round
surfaces every regression. When the approved test command stops at its first
failing group, run the groups it did not reach through the approved diagnostic
adapter, when there is one, before returning the result: the pre-handoff
selection and the Test Plans of the earlier stories the change touches name
them, and a group the final test run reused needs no such run. A group that
fails to collect is a failed group: name it in a finding with its collection
error, never leave it out of the result. The command
itself is the project's: never edit, wrap or extend it in a
task. A command that cannot report every failing group is a finding for the
Verification Contract, whose revision goes through the Operation flow. Name
the earlier story in every finding that is a regression in its suite.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Delivery, record per review round the blocking findings Code Review and QA
raise that are regressions in an earlier story's suite, and the wall clock of
each `regression-run`, which the Item's verification record lists run by run
with its result. Record as well the wall clock of QA's final test run, the
earlier stories it reused, which the Item's verification record lists, and
whether a reused group failed when QA ran it again. The registry's promotion
rule judges them over at least 3 Deliveries.
