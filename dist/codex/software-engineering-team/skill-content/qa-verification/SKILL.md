---
name: qa-verification
description: Verification framework for the QA role. Defines the test-plan categories, the coverage audit, live runtime verification, severity classification, and the PASS/FAIL verdict contract. Loaded for QA steps by software-engineering-team flows.
exposure: internal
---

# QA Verification

Verification, not authorship. QA never writes or modifies product code or tests; it checks that others' tests cover requirements, pass for the right reason, and prove the running application behaves.

Load `obsidian-vault` before Operation Contract work.

## When to Use

Load when verifying a delivered increment against its approved story test plan:
qualified acceptance criteria (`<space>:AC-<CODE>-###`), qualified business
rules (`<space>:BR-<CODE>-###`), stable story scenarios
(`<story-id>-TS-###`) and
edge cases. This skill defines HOW to verify. WHAT the stack-specific checks
look like comes from each stack skill bound to the change: also load their
`references/qa-checklist.md` files before planning. Not for authoring tests,
fixing bugs, or reviewing code style (the review role owns style).

## Test Plan Categories

Classify every planned check into exactly one category. A plan missing any category is incomplete.

1. **Field and endpoint validation.** Single request-response correctness. Happy path asserting the exact success status code; unauthenticated and wrong-role rejections with their distinct status codes; validation failure with field-level error detail; not-found; conflict on duplicates; boundary values (empty, max length, special characters, zero, negative).
2. **Business-rule violations.** For each rule, attempt the violation and assert BOTH the correct exception or error type AND a meaningful message. A test that only asserts "something failed" does not cover the rule. Include state-transition checks: legal transitions succeed, illegal ones are rejected.
3. **Process scenarios.** Multi-step, cross-entity flows: create, then update, then check the related records. Assert propagation where a rule requires it and deliberate non-propagation where a rule forbids it. One scenario per cross-entity rule, tagged with the rule id.

## Coverage Audit

- Cross every qualified AC/BR identity and every stable story-scenario identity
  from the approved test plan with the test tags in the suite results. The
  product is a matrix: one row per identity, mapped tests, result.
- A row with no mapped test is NO-TEST. That is a deterministic finding, never a judgment call, and it fails the audit.
- Run the audit mechanically with the packaged
  `skill-content/qa-verification/scripts/scenario_report.py --brief <test-plan.md>
  --junit <results.xml>`. Exit code 1 means gaps exist.
- [coverage-audit](references/coverage-audit.md): tagging conventions per suite type and the full matrix schema. Read when building the matrix or when a mapped-test lookup is ambiguous.
- [test-design](references/test-design.md): partition, boundary, decision-table, and pairwise methods for deriving the minimal expected test set per requirement. Read when auditing whether a covered id is covered enough (one mapped test, untested partitions).

## Command Indirection

- DO read `test_command`, `mutation_command` and dependency-audit disposition
  from the approved `workspace/docs/operation/verification-contract.md`.
  Read the runtime verbs (`up`, `down`, `seed <scenario>`, `logs`,
  `url <service>`) from the approved Environment Contract when live runtime
  verification is required.
- DON'T hardcode tool invocations or ports. These approved contracts are the
  only project entry points.
- DO record the exact commands executed in the report, so the run is reproducible.
- The suite is hermetic: the test and mutation commands never depend on a standing environment; a suite found depending on one is a blocking finding (waiver semantics in the environment stack skill's Hermetic Suite Rule).
- The mutation gate is mandatory on code stories: run the mutation command scoped to the story's changed code-owned files (environment-owned paths are verified by the live protocol, not by mutants); a surviving mutant in changed lines is a finding, a missing mutation_command on a code story is a blocking finding. Method: [mutation](references/mutation.md). Read when running the mutation gate or judging a survivor.

## Severity Classification

| Severity | Definition | Action |
|---|---|---|
| Critical | Data loss, security hole, crash, complete feature failure | Block, escalate immediately |
| High | An acceptance criterion fails, major user impact | Block, must fix before sign-off |
| Medium | Partial feature failure, edge case fails | Block, must fix before sign-off |
| Low | Cosmetic, minimal user impact | Log only, does not block |

Runtime findings (CRITICAL/FAIL/MINOR in the live protocol) map into this table: runtime CRITICAL is Critical, runtime FAIL is High, runtime MINOR is Low. One severity ladder, one verdict.

## The Right-Reason Rule

A check is green only when it would fail for the right reason. Before trusting any pass:

- DO confirm the test asserts the specific outcome the criterion demands, not merely "no error".
- DO spot-check that a test would fail if the behavior regressed (read its assertions against the criterion).
- DON'T approve a pass you cannot explain.
- DON'T accept a test that was modified to pass, or a test that passes without exercising the changed code.
- DON'T count a skipped test as coverage.

## Live Runtime Verification

Automated green is necessary, not sufficient. After the suite passes, stand the environment up fresh with the approved Environment Contract, seed a named scenario, and walk every navigable surface: console audit, network audit, render audit, interaction audit, service-log audit, each with explicit FAIL conditions and per-surface PASS/FAIL records. Tear the environment down when the protocol (and the design verification that reuses it) is done.

- [runtime-protocol](references/runtime-protocol.md): the step-by-step live protocol with FAIL conditions per audit. Read when starting the live runtime pass.

## Report

Maintain ONE evolving verification record per increment: coverage matrix, suite results, live results, findings by severity, verdict. Its canonical copy is the orchestrator's tracked project documents: the spawn prompt hands you the currently open findings, your reply returns the full record (the orchestrator persists findings, coverage rows and budget verdicts from it). Update the same finding ids across iterations; never fork parallel reports.

- [report-format](references/report-format.md): the record skeleton and per-section update rules. Read when creating or updating the verification record.

## Exploratory Pass

- [exploratory](references/exploratory.md): charter and tour repertoire; findings never touch the verdict and route to the analyst as candidate business rules. Read when a human explicitly requests an exploratory pass outside the deterministic gate.

## Verdict

PASS requires ALL of:

- Coverage matrix has zero NO-TEST rows and zero FAIL rows (scenario_report exit code 0).
- Full suite passes via the approved Verification Contract command.
- Runtime protocol completed with zero Critical and zero High findings.
- Every finding above Low is resolved or explicitly waived by a human.

FAIL if ANY of:

- Any AC or BR id lacks a mapped test.
- Any test fails, errors, or passes for the wrong reason.
- Any runtime CRITICAL or FAIL condition observed.
- Verification Contract missing, or Environment Contract missing when the live protocol must run (escalate; do not improvise commands).

Rules:

- ALWAYS produce the test plan and matrix before executing anything.
- ALWAYS trace every check to an AC, BR, or named edge case.
- ALWAYS report bugs with reproduction steps, expected vs actual, severity.
- NEVER edit product code or tests; document and route back.
- NEVER change requirements; escalate gaps to a human.
- NEVER verify beyond the current scope, except the regression pass: adjacent surfaces by default, widened to every consumer of a shared module when the change touched one.
