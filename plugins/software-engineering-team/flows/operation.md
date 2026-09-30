# Operation Contract Flow

Spawn template: paste `{{constitution}}`, the exact contract path, accepted
Solution decision bindings, the command-safety lens or the reader's lens
assignment and `SELF-CHECK` into every reviewer prompt. Load the
`obsidian-vault` skill before writing vault truth.

Read this complete flow before `/configure operation verification` or
`/configure operation environment` changes durable state. Operation is
project-global delivery truth under `workspace/docs/operation/`; it is not a
Requirement stage and it does not alter product-stage package hashes.

1. Resolve the selected kind. `qa-engineer` is the only Verification Contract
   writer; `devops-engineer` is the only Environment Contract writer. Both
   record exact accepted/current Solution decision references, repository
   relative workdirs and command semantics. Verification also records where
   Delivery PR checks come from, as
   `skill-content/setup/references/ci-bootstrap.md` defines.
2. Run `operation_compile.py check --kind <kind> --json`. An approved contract
   changes only after `begin-revision`; a changed or superseded cited Solution
   decision makes the contract unusable until it is revised and re-approved.
3. When the contract crosses test/runtime boundaries or changes where
   Delivery PR checks come from, review it in the mode that `review_mode` in
   `skill-content/challenge-review/data/review-panels.json` selects. In
   `single` mode, the default, spawn the non-writing counterpart as a
   read-only reviewer; the review prompt includes the exact contract path,
   accepted Solution references, command safety lens and `SELF-CHECK`. In
   `panel` mode run review panel `operation_verification` for a Verification
   Contract or review panel `operation_environment` for an Environment
   Contract, as `skill-content/challenge-review/references/review-panel.md`
   defines. Its lens readers are the non-writing counterpart role, read-only
   and on that role's own tier. Derive each reader's inputs with
   `task_inputs.py --entry configure --role <counterpart> --mode review
   --skill challenge-review`. Every prompt includes the exact contract path,
   accepted Solution references, the reader's lens assignment and
   `SELF-CHECK`. Resolve every critical or major finding before approval.
4. Approve with `operation_compile.py approve --kind <kind>`. Return the exact
   contract receipt. Do not run a downstream product stage automatically.

Verification may declare an optional `diagnostic_test_command` adapter for
failed or affected tests, with `diagnostic_test_workdir` defaulting to `.` only
at execution. Add it through the normal revision and approval lifecycle.
Neither field is inserted into an older contract. Without the adapter, no
focused diagnostic command is authorized; the approved full test command
retains its existing meaning.

The adapter reads the compiler-owned selection file named by
`AGENTROF_DIAGNOSTIC_TESTS`. Its JSON contains `schema_version: 1`, the exact
`candidate_hash`, `failed_test_ids` and `affected_test_ids`. Treat test IDs as
data and pass them to the test runner through an argument array, never shell
interpolation. The approved adapter provisions dependencies in its private
candidate checkout or uses the approved fixed environment; it cannot borrow
ignored dependencies from the writer's checkout. Diagnostic output cannot
satisfy final full-suite evidence.
