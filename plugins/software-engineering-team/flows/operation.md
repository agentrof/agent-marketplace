# Operation Contract Flow

Spawn template: paste `{{constitution}}`, the exact contract path, accepted
Solution decision bindings, command-safety lens and `SELF-CHECK` into every
reviewer prompt. Load the `obsidian-vault` skill before writing vault truth.

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
3. Spawn the non-writing counterpart as a read-only reviewer when the contract
   crosses test/runtime boundaries or changes where Delivery PR checks come
   from. The review prompt includes the exact contract path, accepted Solution
   references, command safety lens and `SELF-CHECK`. Switch `review_panels`: at
   `lens_panel`, review panel `operation_verification` or
   review panel `operation_environment` replaces this reviewer, as
   `skill-content/challenge-review/references/switch-review_panels-lens_panel.md`
   defines. Switch `mechanical_pass_tier`: at `mechanical`, the writer pass
   that applies the review's named fixes, and the `check` and `approve`
   commands of steps 2 and 4, run as
   `skill-content/challenge-review/references/switch-mechanical_pass_tier-mechanical.md`
   defines.
   Switch `review_loop`: at `blocking_delta`, this review loop follows
   `skill-content/challenge-review/references/switch-review_loop-blocking_delta.md`.
   Switch `execution_planning`: at `single_source_bundle`, a contract that a
   Delivery's execution plan revises is written against the fact ownership of
   `skill-content/execution-plan/data/fact-ownership.json`, and that plan's
   bundle review replaces this step, as
   `skill-content/configure/references/switch-execution_planning-single_source_bundle.md`
   defines.
4. Approve with `operation_compile.py approve --kind <kind>`. Return the exact
   contract receipt. Do not run a downstream product stage automatically.
   Switch `owner_gates`: at `two_fixed_gates`, a revision that a Delivery's
   execution plan needs is approved in that Delivery's gate A, as
   `skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
   defines.

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
