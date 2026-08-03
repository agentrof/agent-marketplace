# Coverage Audit

The audit answers one question deterministically: does every acceptance criterion and business rule have at least one passing test? It is a set intersection, not an opinion.

## Inputs

- The brief documents containing requirement ids. Recognized id forms: `BR-###` (business rule) and `AC-###` (acceptance criterion). Ids are extracted verbatim; the audit never infers unlabeled requirements, so an untagged requirement in the brief is itself a finding (escalate to have it labeled).
- The suite results in JUnit XML, produced by running the configured suite command with its XML reporter enabled.

## Matrix Schema

One row per requirement id, in brief order:

| Id | Requirement summary | Mapped tests | Result |
|---|---|---|---|
| BR-001 | No duplicate emails | test_create_user_duplicate_email_returns_conflict | PASS |
| AC-004 | Login issues a token | (none) | NO-TEST |

Result values:

- `PASS`: at least one mapped test, all mapped tests passed.
- `FAIL`: at least one mapped test failed or errored.
- `NO-TEST`: no mapped test, or every mapped test was skipped. Skipped tests are not coverage.

Any NO-TEST or FAIL row fails the audit. There is no PARTIAL and no justified-gap state at this layer; a legitimate exemption must be resolved upstream by removing or rewording the requirement id in the brief, with human approval.

## Tagging Conventions

A test maps to a requirement when the requirement id appears in the test's identity, in one of two stack-appropriate forms:

- **Marker in the server suite.** The test framework's marker or metadata mechanism attaches the id, and the JUnit reporter renders it either into the test name or into a `<property>` element on the test case. Example rendered name: `test_transfer_rejected_when_account_frozen[BR-012]`.
- **Name prefix in the client suite.** The test title carries the bracketed id as a prefix: `[AC-003] shows field errors on invalid submit`.

Both forms reduce to the same rule the script applies: the literal id string, matched case-insensitively, present in the test case name, class name, or property values. One test may cover several ids; list it in every matching row.

## Running the Audit

```
python scripts/scenario_report.py \
  --brief workspace/docs/user-stories.md workspace/docs/business-rules.md \
  --junit results-server.xml results-client.xml
```

Multiple briefs and multiple JUnit files are merged. The script prints the matrix, then a machine-readable summary line, and exits nonzero when any NO-TEST or FAIL row exists. Paste the matrix into the verification record unedited.

## Audit Discipline

- Run the audit BEFORE reading the suite's own summary; the suite can be green while whole rules are untested.
- The matrix is id-granular: a PASS row proves at least one tagged test passed, never that every partition of the rule is covered. The test-design reference derives the partition-level expectation; a planned partition with no mapped test is a NO-TEST finding even when its id row passes.
- Never hand-edit the matrix to close a gap. The only fixes are: a new test lands (someone else writes it), or the brief changes with approval.
- Re-run the audit after every loop-back iteration; the matrix in the record must always reflect the latest suite results.
