# Check lifecycle paths before a full verification pass

These instructions apply only at `qa_preflight: lifecycle_first`. At `off`, the
existing QA order and freeze rules remain unchanged.

## Before freezing

The approved Verification Contract lists each short lifecycle or live path
that must be tried before a full pass in a `Verification Rehearsals` table:

| Step | Phase | Group | Target | Waiver |
| --- | --- | --- | --- | --- |
| restart-ready | lifecycle | smoke | tests/live.py::test_restart_ready | - |
| live-write | live | smoke | tests/live.py::test_write_round_trip | - |

Use unique step ids and literal test targets from the declared live groups.
The approved `live_diagnostic_command` executes them through the existing
`AGENTROF_LIVE_TESTS` selection file and reports each target in the existing
`live-case-report.json`. The project owns what restart, readiness and log
checks mean; package code never invents a service or restart command.
Include stopped-node restart and post-readiness log checks when the project's
lifecycle includes them, and every live path that has not yet passed.

Run `rehearsal-status --delivery <id> --story <id>` for the list, then `rehearse`
with the same ids. It runs only pending targets, lifecycle first, and stops at
the first failure. Fix and commit before repeating it. Changed candidate,
command, environment, expired evidence or changed output makes a target pending
again. This evidence never replaces QA's full live run or its final assessment.

Only the owner may approve a waiver: record the concrete reason in the target's
`Waiver` cell and obtain approval of that Verification Contract revision.
Unapproved or stale contracts cannot waive a target. Each frozen pass records
the reason, contract approval hash and exact candidate to which it applied.
Never insert a waiver merely to advance the pass.

`start-pass` checks these records before regression selection, the unit command
and freeze. Direct `freeze` applies the same gate. Required repair review still
comes before any rehearsal command. A non-runtime Item with no live groups
needs no rehearsal table and starts no environment.

## QA order

After the normal suite checks, run the approved environment `down`, `up`,
`seed` and `logs` commands before the long live groups. The up/readiness and
post-readiness log checks must pass first; the runner refuses live groups
without their current, intact records. The short declared rehearsal target
already exercised any project-specific stopped-node restart before freeze.

Run the selected live groups, then repeat the log check for state they left
behind and finish the existing runtime assessment and teardown. Final runtime
evidence requires a log record after the last live run. Starting the suite early
at `qa_gate_order: gate_first` does not move live groups ahead of lifecycle checks.
