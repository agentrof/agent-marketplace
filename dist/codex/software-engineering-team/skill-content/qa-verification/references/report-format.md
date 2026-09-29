# Verification Record Format

ONE markdown file per verified increment. It evolves in place across QA iterations; never fork a second report. Each iteration updates the affected sections and appends to the iteration log.

For a parallel Item, `qa_diagnostic` runs failed or affected checks first and
returns findings without a terminal PASS. `qa_final` requires every QA gate.
The packaged `scripts/delivery_verification.py` binds independent results and
raw command evidence to the frozen candidate and rejects diagnostic approval.
A finished diagnostic releases its reader barrier but grants no approval.
Return the full record to the owner, who alone persists the canonical copy
after every reader has finished or confirmed cancellation.

Use the role manifest's `diagnostic_interface` for the optional approved
focused runner. Its selection file names literal failed and affected test IDs
for this candidate. `run --kind diagnostic_test --selection-file <file>`
executes the approved adapter; the adapter reads `AGENTROF_DIAGNOSTIC_TESTS`
as JSON and passes identifiers through its test runner's argument-list API.
The final suite still requires separate `run --kind test` evidence.

The manifest's `result_interface` provides the exact JSON fields to return.
Every final check needs `passed: true` and an independent `evidence` assessment.
The full-suite check also needs the approved command, zero exit code,
`identity.environment_hash` and `evidence_hash` from its raw test run.
Mutation and dependency audit name their own raw `evidence_hash`; mutation
also returns the complete compiler-selected file list. Fresh runtime returns
every recorded event hash in order. Diagnostic results cannot substitute any
of these final proofs.

## Skeleton

```markdown
# Verification Record: <increment name>

## Scope
- Increment under test, briefs consulted, adjacent surfaces included in the regression pass.
- Commands executed (from approved Operation Contracts): suite command, mutation command, environment command verbs.

## Coverage Matrix
<paste the scenario_report output unedited: matrix plus summary line>

## Suite Results
- Totals: tests run, passed, failed, skipped.
- Failures listed by test name with one-line cause each.
- Right-reason spot-checks performed: which passes were inspected and why they are trusted.

## Mutation Results
- Command and scope (the story's changed files), mutant totals
  (generated, killed, survived, timed out), every survivor with file,
  line, mutation and its finding id or ACCEPTED reasoning.
- Exempt stories (no code changed) say so explicitly.

## Budget Verification
One row per quantified budget from the brief:
| Budget | Method | Measured | Result |
|---|---|---|---|
Result is VERIFIED (a perf-smoke or seeded-volume assertion measured it),
or UNVERIFIED with the reason stated (load-dependent, no harness). An
UNVERIFIED row is honest; a faked green is a reporting defect.

## Live Runtime Results
Per surface:
| Surface | Console | Network | Render | Notes |
|---|---|---|---|---|
| <id> | PASS/FAIL | PASS/FAIL | PASS/FAIL | <details> |

Per interaction:
| Surface | Action | Expected | Actual | Result |
|---|---|---|---|---|

Boot-time third-party error lines, each with its bounded cause and disposition; warning-line counts per service.

Service-log audit: window covered, PASS/FAIL, offending lines (or none);
environment teardown confirmed.

## Findings
Grouped by severity (Critical, High, Medium, Low). Each finding:
- Stable finding id, severity, one-line summary.
- Reproduction steps.
- Expected vs actual.
- Traced requirement id (AC/BR) or runtime protocol step.
- Status: OPEN / FIXED (verified in iteration N) / WAIVED (by whom).

## Iteration Log
| Iteration | Date | Outcome | Routed to |
|---|---|---|---|
| 1 | <date> | FAIL: <blocking findings> | implement |
| 2 | <date> | PASS | sign-off |

Each Date cell is the pasted output of the local compiler's `now --date`
(system clock, UTC), never a remembered date.

## Verdict
- PASS or FAIL, with the gate-by-gate breakdown:
  coverage matrix clean / suite green / runtime clean / no unresolved blocking findings.
- On FAIL: the blocking finding ids and the loop-back destination.
```

## Rules

- The verdict section is last and single: one PASS or FAIL for the whole increment.
- A FAIL verdict lists exactly which gates failed; a PASS verdict shows all gates explicitly satisfied.
- Findings keep their ids across iterations; a fixed finding flips status, it is never deleted.
- Low findings appear in the record even though they do not block; silent omission is a reporting defect.
- Everything in the record is evidence-backed: command output, observed behavior, or a matrix row. No unverified claims.
