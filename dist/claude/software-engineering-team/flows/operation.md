# Operation Contract Flow

Spawn template: paste `{{constitution}}`, the exact contract path, accepted
Solution decision bindings, command-safety lens and `SELF-CHECK` into every
reviewer prompt. Load the `obsidian-vault` skill before writing vault truth.

Vault first, per constitution section 5: every role starts from the default `project_reading` plan.
Batch-read its units, using the frozen context reader for frozen candidates. When context is insufficient, wrong or unavailable, use manual search, reads and relationship discovery
on the role's initiative or parent direction; record sources and reasons, rebind evidence, preserve gates and return `context_findings` to the parent for user-approved reporting.
For gaps, use `vault_query.py`, machine indexes and generated views, typed frontmatter, relation blocks and wikilinks, then maps and targeted search.
Fallback navigation with a shell can use `home.md`, `maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the contracts' typed relations,
the accepted Solution decisions they cite and `maps/delivery.md`. Switch
`context_pack`: at `role_digest`, a spawned role receives its pack from
`context_pack.py build --entry <entry> --role <role> --mode <mode>
--project-root <root>` instead of the full required reads; it reads a
named source in full only when the pack does not cover a case, and
records that read and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines. Switch `step_timing`: at `recorded`, the coordinator runs
`step_timing.py start --run <run> --step <step> [--budget <id>]` before
each step and `step_timing.py end --run <run> --span <span>` after it,
and records each role spawn with `--kind spawn --parent <step span>
--role <role> --phase reading|writing|review|re_review|waiting`. An end
that returns overrun is reported to the owner at once with its
breakdown, largest contributor and lever. The run ends with
`step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is
compared with its maximum, the owner's parameter or the package target
in `skill-content/configure/data/step-budgets.json`; a step may finish
well under it, and only an exceeded maximum is reported, never blocking,
as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

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
   Switch `review_rounds`: at `single_pass`, this review runs as one reader and
   one pass, following
   `skill-content/challenge-review/references/switch-review_rounds-single_pass.md`.
   Switch `reader_waves`: at `all_at_once`, every reader of a review or recheck
   wave starts at once, after every finished worker is closed, as
   `skill-content/challenge-review/references/switch-reader_waves-all_at_once.md`
   and the host contract define.
   Switch `review_scope`: at `impact_closure`, step 2's `operation_compile.py
   check --kind <kind> --json` runs before any reader is spawned;
   `task_inputs.py` derives each reader's inputs with `--changed <note>` and
   `--base <approved commit>`, and a re-check given `--findings` derives only
   the fix delta; every role reads the change's impact closure, each approved,
   unchanged note outside it only as its hash-bound summary, reads beyond it
   when unsure and records why, a writer fixes a reported graph gap
   through its owning compiler and recomputes the closure, and a confirmation
   re-review reads only the fix's delta, as
   `skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
   defines.
   Switch `review_fanout`: at `per_unit`, a review of more than one changed
   contract spawns one reader per unit in one message and then one aggregator
   for the cross-unit checks no compiler enforces, as
   `skill-content/challenge-review/references/switch-review_fanout-per_unit.md`
   defines.
   Switch `review_levels`: at `concurrent_when_independent`, a contract review
   starts beside the execution-plan topology review when neither cites the
   other's open findings, and approval still waits for every level, as
   `skill-content/challenge-review/references/switch-review_levels-concurrent_when_independent.md`
   defines.
   Switch `execution_planning`: at `single_source_bundle`, a contract that a
   Delivery's execution plan revises is written against the fact ownership of
   `skill-content/execution-plan/data/fact-ownership.json`, and that plan's
   bundle review replaces this step, as
   `skill-content/configure/references/switch-execution_planning-single_source_bundle.md`
   defines.
4. Approve with `operation_compile.py approve --kind <kind>`. Return the exact
   contract receipt. Do not run a downstream product stage automatically.
   A Verification Contract approval stamps `paired_environment_revision` and
   `paired_environment_source_hash` from the approved current Environment
   Contract; writers never author that receipt, so the two writers can work
   side by side. Approve the Environment Contract first; `check --kind
   verification` reports a later Environment approval as advisory drift.
   Switch `owner_gates`: at `two_fixed_gates`, a revision that a Delivery's
   execution plan needs is approved in that Delivery's gate A, as
   `skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
   defines.

Verification may declare opt-in live test groups that QA runs one at a time:
`live_test_command`, which contains the literal placeholder `{group}` exactly
once, `live_test_workdir` (`.` for the repository root) and `live_groups`, the
ordered, unique group names of letters, digits, `.`, `_` and `-`. The three
are declared together or not at all, and their approval follows the normal
revision lifecycle. Delivery runs a group with `run --kind live_test --group
<group>`; groups never become required checks by themselves.

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

Verification may also name, in `command_variables`, the environment variables
its commands read beyond `PATH`, `HOME`, `LANG`, `LC_*` and `TZ`, such as a
service URL or a credential the tests use. Each is a variable name, never a
value. Delivery run evidence covers those variables by name and binds their
values by a hash alone, keyed with a key kept only in the Item's verification
runtime, so a recorded run is reused only while they hold, a tracked record
that carries the hash checks no guess of a value, and a variable left out
never binds the evidence. The contract check refuses a name of the runner's
own `AGENTROF_` namespace: the runner sets those variables, and run evidence
binds them without a declaration, a selection file the runner writes by its
content. Add the field through the normal revision and approval lifecycle.
