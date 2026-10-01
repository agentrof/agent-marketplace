# Coverage Audit

The audit answers one question deterministically: does every planned acceptance
criterion, business rule and story scenario have at least one passing test? It
is a set intersection, not an opinion.

## Inputs

- The approved story Test Plan, passed with `--plan`. It plans each scenario
  it defines under a `<story-id>-TS-###` heading, such as `AUTH-01-TS-003`,
  followed by the qualified `AC` and `BR` identities that scenario's
  `source_refs` cite, such as `inventory:BR-STOCK-001`. An identity the plan
  only mentions, such as another story's scenario named in a Given clause or
  a supersession note, is not planned.
- When an Item's regressions run the scenarios of the stories it depends on,
  pass the Item's own approved Test Plan first and each dependency's approved
  Test Plan as a further `--plan` value, and name with `--superseded` each
  dependency scenario that the Item's own approved Test Plan supersedes. A
  superseded scenario leaves the audit with every `AC` or `BR` identity that
  no remaining scenario cites. The script refuses a superseded id that the
  first plan defines: the Item's own scenarios never leave the audit.
- An explicit identity list goes through `--brief` instead, which plans every
  qualified or unqualified `BR` and `AC` identity and every story scenario in
  its text. A Test Plan never goes through `--brief`: each identity it cites
  would become a planned row.
  Identities are extracted verbatim; the audit never infers an unlabeled
  requirement or scenario.
- The suite results in JUnit XML, produced by running the configured suite command with its XML reporter enabled.

## Matrix Schema

One row per requirement id, in plan or brief order:

| Id | Requirement summary | Mapped tests | Result |
|---|---|---|---|
| INVENTORY:BR-STOCK-001 | No duplicate stock item | test_duplicate_stock_item | PASS |
| ST-004-TS-002 | Login issues a token | (none) | NO-TEST |

Result values:

- `PASS`: at least one mapped test, all mapped tests passed.
- `FAIL`: at least one mapped test failed or errored.
- `NO-TEST`: no mapped test, or every mapped test was skipped. Skipped tests are not coverage.

Any NO-TEST or FAIL row fails the audit. There is no PARTIAL and no justified-gap state at this layer; a legitimate exemption must be resolved upstream by removing or rewording the requirement id in the brief, with human approval.

## Tagging Conventions

A test maps to a requirement when the requirement id appears in the test's identity, in one of two stack-appropriate forms:

- **Marker in the server suite.** The test framework's marker or metadata
  mechanism attaches the exact qualified requirement or scenario identity,
  and the JUnit reporter renders it either into the test name or into a
  `<property>` element. Example: `test_transfer_rejected[accounts:BR-TRF-012]`.
- **Name prefix in the client suite.** The test title carries the bracketed
  identity as a prefix: `[ST-003-TS-004] shows field errors on invalid submit`.

Both forms reduce to the same rule the script applies: the literal id string, matched case-insensitively, present in the test case name, class name, or property values. One test may cover several ids; list it in every matching row.

## Running the Audit

```
scenario_report.py \
  --plan workspace/docs/backlog/epics/accounts/stories/login/test-plan.md \
  --junit results-server.xml results-client.xml
```

Run the packaged `skill-content/qa-verification/scripts/scenario_report.py`.

Multiple plans or briefs and multiple JUnit files are merged. The script prints the matrix, then a machine-readable summary line, and exits nonzero when any NO-TEST or FAIL row exists. Paste the matrix into the verification record unedited.

## Audit Discipline

- Run the audit BEFORE reading the suite's own summary; the suite can be green while whole rules are untested.
- The matrix is id-granular: a PASS row proves at least one tagged test passed, never that every partition of the rule is covered. The test-design reference derives the partition-level expectation; a planned partition with no mapped test is a NO-TEST finding even when its id row passes.
- Never hand-edit the matrix to close a gap. The only fixes are: a new test lands (someone else writes it), or the brief changes with approval.
- Re-run the audit after every loop-back iteration; the matrix in the record must always reflect the latest suite results.
