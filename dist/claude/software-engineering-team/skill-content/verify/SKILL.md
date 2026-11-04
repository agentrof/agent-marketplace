---
name: verify
description: Selects and executes proportionate evidence for acceptance criteria, regressions and live behavior, including shared validation of compatible tasks.
exposure: internal
---

# Verify

## Outcome

Reproducible evidence that the candidate satisfies its acceptance criteria,
with failures and unverified claims visible.

## When to use

Load while planning proof, before claiming completion, and when a check fails.
Use existing valid evidence before spending on another equivalent run.

## Inputs and authority

Use the task, candidate identity, changed dependencies and the project's
Verification and Environment documents under `workspace/docs/operation/`.
Honor their commands and current host permissions. Validate only authorized
targets. A reviewer or test helper cannot grant a destructive reset, download
or external mutation.

## Method

1. Map each acceptance criterion and changed risk to the cheapest sufficient
   check. Reuse relevant tests. Add regression coverage where existing tests
   would miss the defect. Include refusal, concurrency and old-data cases when
   their invariants can change.
2. Establish the exact code, configuration, fixture and environment being
   tested. Check resource capacity before expensive work. Keep writers away
   from a tested snapshot and isolate mutable test resources.
3. Run focused checks first. Batch compatible tasks' shared validation on an
   integrated candidate. Preserve criterion-level attribution and bisect a
   failing batch. Follow the project's required CI and proof policy.
4. Exercise affected runtime wiring and user-visible behavior through the
   normal entry point. Reuse a healthy development environment. Reserve clean
   provisioning and destructive tests for disposable owned fixtures when those
   properties are relevant.
5. Classify failures as product, test, environment, resource or observation
   defects from evidence. Investigate the first failure before retrying. Bound
   retries and retain their results. An unchanged failure stops the same loop.

## Evidence and completion

Record candidate identity, commands, results, durations, criterion coverage and
artifact locations through the flow's evidence mechanism. Distinguish executed,
reused, skipped and unverified checks. A file's existence, a planned test or an
agent's success statement cannot establish a passing result.

## Conditional references

- For coverage, failure triage or batch proof, read [test selection](references/test-selection.md).
- For shared resources, containers or live checks, read [environments](references/environments.md).
- For data-integrity proof, read [evolution](../data-design/references/evolution.md).
