# Delivery Execution Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Vault first, per constitution section 5: every role starts from the default `project_reading` plan.
Batch-read its units, using the frozen context reader for frozen candidates. When context is insufficient, wrong or unavailable, use manual search, reads and relationship discovery
on the role's initiative or parent direction; record sources and reasons, rebind evidence, preserve gates and return `context_findings` to the parent for user-approved reporting.
For gaps, use `vault_query.py`, machine indexes and generated views, typed frontmatter, relation blocks and wikilinks, then maps and targeted search.
Fallback navigation with a shell can use `home.md`, `maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and `maps/_generated/stale-relations.md`,
`maps/delivery.md` and the Item's story, Test Plan and pinned contract relations. Switch
`context_pack`: at `role_digest`, a spawned role receives its pack from `context_pack.py build
--entry <entry> --role <role> --mode <mode> --project-root <root>` instead of the full required
reads; it reads a named source in full only when the pack does not cover a case, and records that
read and why, as `skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines. Switch `step_timing`: at `recorded`, the coordinator runs `step_timing.py start --run <run>
--step <step> [--budget <id>]` before each step and `step_timing.py end --run <run> --span <span>`
after it, and records each role spawn with `--kind spawn --parent <step span> --role <role> --phase
reading|writing|review|re_review|waiting`. An end that returns overrun is reported to the owner at
once with its breakdown, largest contributor and lever. The run ends with `step_timing.py report
--run <run> --write`, as `skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is compared with its maximum, the
owner's parameter or the package target in `skill-content/configure/data/step-budgets.json`; a step
may finish well under it, and only an exceeded maximum is reported, never blocking, as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md` defines. Switch
`step_baseline`: at `median`, a timed step is also compared with the median of its own last runs,
and `delivery_verification.py` times each live run, live step and rehearsal command under the QA
step keys of `skill-content/configure/data/step-budgets.json`; while a long QA command runs, the
coordinator polls `step_timing.py overruns` and reports each overrun with its step, elapsed time,
baseline and what it is doing, as `skill-content/deliver/references/switch-step_baseline-median.md`
defines.

`/deliver DLV-###` resumes from tracked Delivery files and verified remote
evidence. It starts or resumes one Item only when its exact plan, target,
predecessor, Fence and global slot checks pass. Each Item its
`execution_after` names must be integrated first: its remote Item tip records
`integrated` and the Integration contains that tip. Each Story its `waits_for`
names must reach this Integration the same way, which for a Story of another
Delivery means that Delivery merged into the target and this one refreshed
onto it; its Item tip's `Agentrof-Delivery` trailer names the package that
records its status. Once that Delivery merged and dropped the Item ref, its
package in this Integration answers instead, trusted only where this
Integration also holds the merge of its recorded PR. A Story no Delivery has
claimed, or one its Delivery cancelled, refuses the start with
`DELIVERY_DEPENDENCY_UNMET` and names a backlog revision as the way out.
Product and test changes stay on the Item worktree; Integration accepts only
reviewed, verified Item handoffs and compiler-owned projections.
Switch `implementation_schedule`: at `parallel_lanes_v1`, an Item whose approved
plan declares it runs its implementation roles in parallel lanes, as
`skill-content/deliver/references/switch-implementation_schedule-parallel_lanes_v1.md`
defines.
Switch `execution_planning`: at `single_source_bundle`, the Item's Software
Architect records decisions, alternatives and rationale and links the owning
contract section for rule text, as
`skill-content/software-architecture/references/switch-execution_planning-single_source_bundle.md`
defines.
Switch `owner_gates`: at `two_fixed_gates`, a question between gate A and gate
B is queued in the Delivery's `User Decisions` unless it is of an at-once class,
and the Delivery Review and the merge are decided together in gate B, as
`skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md` defines.
Switch `lane_table`: at `recorded`, a coordinator that runs parallel lanes
records each lane's start and finish and reads the lanes still pending before
it launches anything, as
`skill-content/deliver/references/switch-lane_table-recorded.md` defines.
Switch `lane_isolation`: at `scratch_clone`, each parallel fix lane works in
its own scratch clone and no delegated lane switches the main checkout's
branch, as
`skill-content/deliver/references/switch-lane_isolation-scratch_clone.md`
defines.
Switch `provisional_claims`: at `during_plan_revision`, an active Item's
writer commits a path its plan revision adds under a provisional claim, which
freeze, `push-item` and integration refuse until the approved plan publishes
the path, as
`skill-content/deliver/references/switch-provisional_claims-during_plan_revision.md`
defines.

## Parallel verification
Follow the Item's `verification_schedule`, or sequential when absent. At
`parallel_snapshot_v1`, commit the product/test candidate and use the packaged
`scripts/delivery_verification.py --worktree <item-root> freeze --delivery
DLV-### --story <story>` before invoking the two readers. The resulting
candidate and session identities bind all source and instruction inputs.
Generate each role's `manifest` with `--role code_reviewer|qa_engineer` and
`--mode review_initial|review_repair|qa_diagnostic|qa_final`.
`start-pass --delivery DLV-### --story <story>` checks required repair review
before regression work, then runs configured regressions, `freeze`, manifests
and `templates/reader-launch-prompts.json` prompts, including lens assignments.
Modes default to `review_repair` after an earlier candidate and `qa_final`;
`--review-mode` and `--qa-mode` override them. The first failing step stops the chain.
Switch `qa_preflight`: at `lifecycle_first`, follow `skill-content/qa-verification/references/switch-qa_preflight-lifecycle_first.md`.
Switch `runtime_lifecycle`: at `long_lived`, follow `skill-content/qa-verification/references/switch-runtime_lifecycle-long_lived.md`.
Switch `live_case_reuse`: at `delta_since_green`, follow `skill-content/qa-verification/references/switch-live_case_reuse-delta_since_green.md`.
Put the manifest's `command_form` verbatim into every reader prompt: while
readers are active, Bash admits only that one direct invocation followed by a
verb, and readers read files with the host's Read tool.
Every verification manifest includes the default `project_reading` plan. Start
with `inspect-context --plan <saved verification manifest>` to batch-read its
units from the frozen Git candidate. Preserve all `full_read` obligations and
verification gates. Use `expand-context --plan <manifest> --reason <reason>`
for continuation; `--ref` adds a reference. If context is insufficient or wrong, use frozen `inspect`
and `diff` on the reader's initiative or parent direction, and return
`context_findings` with recovery and proposed fix when known. The parent offers
an anonymous issue through `issue-report` only with exact-payload user approval.
Read the frozen candidate with `inspect --path <file>` and
`diff [--path <file>]`; `inspect --base` selects its exact integration base.
Use `inspect --instruction <package-relative-file>` for bound package instructions.
Switch `level_change_map`: at `assertion_map`, the writer maps the assertions
of each scenario whose Test Plan level the Item converts, `freeze` refuses an
incomplete map and the code reviewer reads the pairs it flags, as
`skill-content/deliver/references/switch-level_change_map-assertion_map.md`
defines.
Switch `repair_review` at `before_freeze` blocks a candidate on unresolved repair
review; minor findings enter full review. Follow
`skill-content/code-review/references/switch-repair_review-before_freeze.md`.
Switch `pre_handoff_regression`: at `touched_suites`, the coordinator runs the
suites of the earlier stories the candidate touches, with the Item's own Test
Plan targets, before `freeze`, which refuses until that run passed on the exact
candidate, as
`skill-content/deliver/references/switch-pre_handoff_regression-touched_suites.md`
defines.
Switch `regression_plan_revision`: at `current_approved`, earlier-story regression targets and metadata follow the complete current committed backlog approval, with a closed-to-current receipt, as `skill-content/deliver/references/switch-regression_plan_revision-current_approved.md` defines.
Switch `own_target_reuse`: at `spot_run`, QA's final test run also takes the
Item's own Test Plan targets from that run, but for the ones QA spot-runs, as
`skill-content/deliver/references/switch-own_target_reuse-spot_run.md`
defines; at `accept_record` it takes every own target from that run and runs
none of them itself, as
`skill-content/deliver/references/switch-own_target_reuse-accept_record.md`
defines.

Invoke Code Review and QA independently through the host's native agents. Keep the implementation
writer idle until both readers finish or cancellation is confirmed, except isolated `lane_isolation`
`scratch_clone` repairs of registered findings. Each reader registers `result --file <result.json>`
when its own command ends, even while the other runs; the owner alone persists canonical reports
after the barrier. The code reviewer runs no command, not even a diagnostic test. Its
`verification_evidence` names the freeze record and QA runs. It registers its finished result at
once and returns. A reader waiting for another role's step returns its result unregistered.
Cancelled or diagnostic results cannot approve an Item. Code, test, contract or instruction drift
invalidates the candidate. Switch `code_review_panel`: at `beside_official`, a lens panel reads the
frozen candidate beside the official code reviewer and `merge-panel` registers the one code review
result, as `skill-content/code-review/references/switch-code_review_panel-beside_official.md`
defines. At `risk_scoped`, a lens reader joins the official code reviewer only when the frozen
change triggers its lens by the owner's parameters, as
`skill-content/code-review/references/switch-code_review_panel-risk_scoped.md` defines. Switch
`item_review_scale`: at `by_change_size`, the official code reviewer reads alone an Item whose
frozen change is within the owner's size limits, as
`skill-content/code-review/references/switch-item_review_scale-by_change_size.md` defines. Switch
`review_scope`: at `impact_closure`, run the structural check before any reader. Both readers use
the frozen manifest's impact closure; approved unchanged notes outside it remain hash-bound
summaries. Expand uncertain context and record why. The owning compiler repairs graph gaps and
recomputes the closure; a confirmation re-review reads only the fix's delta, as
`skill-content/challenge-review/references/switch-review_scope-impact_closure.md` defines.

A role that waits inside its turn waits at most the Delivery verification
policy's `wait_bound_seconds`, 240 seconds, in any one tool call, so its next
model call reads its context from the host's prompt cache instead of writing
it there again. On the Item it waits only through the runner's `wait`, never
through a sleep, a polling loop or a long timeout of its own:
`wait --role <role>` returns once that reader's own verification command has
exited, and `wait` alone once no command holds the Item's environment or
verification command lock. Each call returns as soon as that happens, or at
the bound with the holder it still waits for; then call `wait` again at once,
each time as a tool call of its own. Start a command that can outlast one
`wait`, such as QA's full suite, in the background as the host runs a long
command, wait for it with `wait --role qa_engineer`, then read the record the
command printed. Read a recorded run again with `status --run <kind>`, or
every run with the session identity through `status --summary`; plain
`status` prints the whole session, bindings included.

QA uses `run --kind test|mutation|dependency_audit` for the approved commands.
QA runs each declared `live_groups` entry with `run --kind live_test --group <group>`,
substituting `{group}` in `live_test_command` in `live_test_workdir`.
The frozen clone, locks, environment binding and raw evidence match `run --kind test`.
The Test Plan selects live groups; they never join required checks automatically.
Read each record with `status --run live_test:<group>` and cite its `evidence_hash`.
At `live_group_run: combined`, pass all selected groups in one call, as
`--group a,b` or `--group a --group b`. Names are substituted in `live_groups`
order; the one record is read with `status --run live_test:a,b`.
Without `combined`, multiple groups in one call are refused.
Switch `qa_gate_order`: at `gate_first`, QA starts its first test command of a
round before it plans and drafts its result, as
`skill-content/qa-verification/references/switch-qa_gate_order-gate_first.md`
defines.
Switch `test_group_report`: at `refuse_missing_groups`, a test run also reads
the group report the approved command writes and records every declared group,
as
`skill-content/deliver/references/switch-test_group_report-refuse_missing_groups.md`
defines.
Switch `test_engines`: at `partitioned`, `run --kind test` runs the
partitions the Verification Contract declares in parallel on isolated test
engines and merges them into one record, as
`skill-content/deliver/references/switch-test_engines-partitioned.md`
defines.
Switch `item_qa_tier`: at `change_tier_per_item`, an Item's QA gate runs the
change-level tier the Verification Contract declares, and the Delivery runs
its integration tier once before its pull request, as
`skill-content/qa-verification/references/switch-item_qa_tier-change_tier_per_item.md`
defines.
Before an Item's first QA round, run `permission-preflight --delivery <id>
--story <id>` and show its exact `commands` and `allow_rules`, including live
groups and runtime verbs. The owner allows them once; the package never edits
host permissions. For declared `Owner Steps`, follow `owner_step_interface`
and `flows/operation.md`: keep the reader and freeze open, relay the command,
and wait for owner output without executing or confirming it for them.
Other host refusals go in `blocked_commands` as `blocked_by_permission` and
settle as diagnostic or failed, never passed. `status --summary` shows them;
ask the owner at once, then `resume-qa` reruns blocked commands on the same candidate.
For failed or affected tests, an approved `diagnostic_test_command` enables
`run --kind diagnostic_test --selection-file <scratch-selection.json>`.
Copy the QA manifest's selector to scratch with literal `failed_test_ids` and
`affected_test_ids`. The runner adds their sorted `selected_test_ids` union and
passes candidate-bound data through `AGENTROF_DIAGNOSTIC_TESTS`, never shell text.
An open inherited finding may list `negative_control_test_ids`. Its
`finding_controls: [{finding_id, test_ids}]` selects only prescribed ids in
changed test files, without a Test Plan scenario binding. The runner adds
these to `selected_test_ids`, passes `finding_control_test_ids` and
`evidence_scope: finding_diagnostic`, and records finding-scoped evidence QA
cites when resolving the finding. It never counts as acceptance evidence.
Test Plan selection and approved row values stay unchanged; the backlog
absorbs these controls at its next ordinary revision.
Only diagnostic commands receive this environment variable; final checks,
runtime commands and lane commands remove it, including any inherited value.
A changed selection cannot reuse an older diagnostic run. Without this
adapter, use the full approved test command or revise the Operation Contract
through its normal approval.
Diagnostic command evidence never satisfies the final full-suite gate. Commands run in an independent scratch checkout of the frozen product commit,
with no shared Git objects or mutable working files. The runner exposes an
output directory through `AGENTROF_VERIFICATION_SCRATCH` and the candidate's
Delivery and Item ids through `AGENTROF_DELIVERY_ID` and `AGENTROF_ITEM_ID`,
whatever the caller's shell exports. Provision dependencies through
approved commands and environment, never by borrowing the writer's ignored
files. Tests and mutation cannot temporarily edit the reviewer's source.
Raw output stays under ignored runtime, bound to the candidate, command and
result-affecting variables: `PATH`, `HOME`, `LANG`, `LC_*`, `TZ`, `AGENTROF_*`
and the Verification Contract's `command_variables`. Runner-written selection
files bind by content. Each named value is hashed with a random key kept only
in the Item's verification runtime; `PWD` and other session variables do not
bind it. No secret value is written, and the tracked hash checks no guess of
one. The runner reuses only identical successful evidence, these values included.
`--fresh` explicitly reruns a command. Evidence approval and `push-item` check
each identity as recorded: complete, running the approved command, and shared
by every final run and runtime event of the session. They never compare it
with their own shell, so the coordinator approves evidence QA recorded in
another. The mutation command reads the exact
JSON file scope through `AGENTROF_MUTATION_FILES`; no shell path interpolation
is permitted. A scope that holds no file launches no command: `run --kind
mutation` records `scope empty, not run` as a passed run with zero duration. The QA result names the raw evidence identities and records
coverage, right-reason and all applicable final checks. A diagnostic returns
quick findings without terminal eligibility; `resume-qa` allows final QA on
the same unchanged candidate while preserving its independent review.
After a failed final QA, finish any required runtime teardown, then use
`resume-qa` on that unchanged candidate. The runner archives the failed result
and runtime attempt, starts a fresh runtime attempt, and retains the completed
review. Failed runtime events cannot supply the new attempt's final evidence.

Combine blocking findings into one owner repair queue while retaining each
role's verdict, severity and stable finding IDs. Repair the product only after
both readers settle, commit it, then freeze the new candidate. Initial review
covers the full Item diff; re-review covers the new delta, open findings and
affected consumers. Correctness, conformance and security remain mandatory.
An unchanged candidate with unchanged source bindings and instructions reuses
its existing independent role results without another dispatch.
Switch `review_loop`: at `blocking_delta`, the code review loop follows
`skill-content/code-review/references/switch-review_loop-blocking_delta.md`.
Switch `item_verification`: at `single_verifier`, one verifier registers both results in one pass, as
`skill-content/deliver/references/switch-item_verification-single_verifier.md` defines.
Switch `repair_pass`: at `scoped_repair`, a reviewed repair of findings below critical settles with its
touched-suites run, as `skill-content/deliver/references/switch-repair_pass-scoped_repair.md` defines.
Switch `finding_disposition`: at `follow_up_issue`, only a critical finding blocks; each one below it is
filed as a follow-up issue, as `skill-content/deliver/references/switch-finding_disposition-follow_up_issue.md` defines.

`validate --delivery DLV-### --story <story>` must accept both final results
before evidence approval. The existing compiler derives actual product HEAD
and persists the two reports. Its subsequent evidence child commit does not
change the verified product candidate; integration continues to prove the
exact direct-parent binding. Runtime verification is mandatory exactly when
the approved Item declares `runtime_required: true`.
For that Item, run the approved Environment Contract through
`environment --verb down|up|seed|logs|url [--value <approved-identifier>]`.
Every verification session of the Item uses that one checkout path, preserving
the environment instance's path binding. Its first command re-pins the checkout
to the session's candidate and removes untracked and ignored files. Return
recorded event hashes with the full per-surface runtime findings.
Successful down, up, seed, logs and final down are required, alongside the
independent runtime assessment. A non-runtime Item starts no environment.

## Activation and publication
Activation writes an ignored pending writer receipt before the atomic Item, Slot, Integration and
Fence transaction. The receipt is promoted only after both Item and Slot refs equal the candidate
OID, then the coordinator materializes the detached Item worktree from that exact OID. A missing
receipt does not change remote semantic status, but it denies local writer readiness until the exact
remote activation is re-verified or explicitly taken over. Takeover is an explicit host-loss
decision; it reuses the existing Item and Slot refs, elects a new writer epoch under exact leases
and never allocates a second Slot.

After implementation, only the current Item's initialized Code Review and Verification reports may
have uncommitted authoring changes. Evidence approval derives both reviewed and verified commits
from that worktree's real `HEAD`; it never accepts caller-supplied commit text. The only permitted
uncommitted files after approval are that Item's `code-review.md` and `verification.md`. Approval
and publication reject tracked entries with `assume-unchanged` or `skip-worktree` flags, without
modifying the index. `push-item` attaches those records to the committed product/test tip as one
`item-evidence-v1` child and advances Item plus Slot together. It rejects a product commit that
edits Delivery control files, except the current required Architecture Item's compiler-stamped delta
hash and refreshed source hash. That exception preserves every other Item byte and control path, and
requires the exact committed, sealed delta to remain within approved architecture claims. An Item
whose writer converged it on a refreshed Integration may also carry that Integration commit's
Delivery controls byte for byte and record the commit as its `integration_base_commit`. The commit
must lie on the Integration's own line after the Item's previous base and be contained in the
product tip, and the Item record keeps the commit's plan-owned fields with only its own status,
stamp and base. `delivery_git.py converge-item --delivery DLV-### --story <story>` converges the
clean Item worktree in one step: it merges the current Integration, gives every Delivery control
path the Integration's bytes, writes the Item record in that converged form, renders the relation
views again with `vault_check.py render-relations` and commits. A conflict in any other path aborts
the merge, leaves the worktree at its tip and names each path for a manual merge. `push-item` also
rejects a product or test path outside the Item's path claims, which cover their paths and
everything below them. Vault paths keep the control, Architecture and projection rules, and a path
held exactly as the Item's `integration_base_commit` holds it is not the Item's change.
`integrate-item` reads the evidence from the remote Item ref and requires that its exact direct
parent is the reviewed and verified product/test tip.

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. The Review is authored as a draft `delivery-review.md` in the
Delivery package before `approve-review`. Approval keeps every authored
section, fills only the sections left empty and the navigation the compiler
owns, and binds that content in its approval hash; the PR body is that Review.
Switch `item_cost_report`: at `per_step`, the Review also carries each Item's
fixed cost step by step, as
`skill-content/deliver/references/switch-item_cost_report-per_step.md` defines.
The Git coordinator first publishes the approved Review, then
publishes one durable PR-creation intent and records the provider URL as its
exact descendant. That record is the PR head and moves the reviewed Delivery
to `awaiting_merge`. The compiler reports `merged` only when the current
branch reaches, on any path, a two-parent merge of that exact head that
carries no `Agentrof-Record` trailer, also for a PR recorded while the
Delivery stayed in `review`. Coordinator commits, such as the reopen commit,
never count; a manual merge of the Integration branch into another branch
would. A merged Delivery keeps its pinned sources, and the Delivery map keeps
the tracked status. Proving the merge deletes the Delivery's Integration ref
and its integrated Item refs; a cancelled Story keeps its Item ref, and the
project Fence stays. A shallow clone or a failed Git query makes `status` and
`check` fail with an explicit finding.
Provider create/merge calls are adapter-owned and must
requery the intent and reviewed Integration head before any external mutation.
Closure: merge only through `merge-pr` on the recorded PR head; never merge
the Delivery PR, its Integration or an Item branch through the provider or Git
directly. Report a Delivery complete only when `closure-audit` returns
`closed`: the target holds the recorded-head merge and no Integration ref,
integrated Item ref, Slot or writer receipt of the Delivery stays.
`closure-audit --delivery DLV-###` or `--all` only reads, and names each
outcome with its recovery: `closed`; `merged_cleanup_pending`, recovered by
`verify-merge`; `awaiting_merge`, with whatever keeps its recorded head from
closing it; `open`; `external_product_merge`, where the Delivery's product
reached the target without the recorded-head merge; or `unproven_record_merge`,
where the target merged a PR record that is not the Delivery's current
recorded head as the coordinator wrote it. Neither is ever reported as merged,
and the project owner decides their recovery; identical product bytes alone
are only a warning. Broad delegation, time
pressure and an autopilot grant authorize running this flow as prescribed and
never waive evidence, integration, the PR record or closure. A quality
exception the flow permits is accepted only at its explicit gate, with the
named rule, its bounded scope, the approval's provenance and the remaining
obligations recorded. The opt-in `delivery-closure` workflow runs
`closure-check` on every pull request and `protection-status` reports,
read-only, whether a ruleset requires it, as
`skill-content/setup/references/ci-bootstrap.md` describes.
Before Item work starts, `/deliver DLV-###` may run the internal
`refresh-target` coordinator. A disjoint target advance becomes one
`target-refresh-v1` Integration child. Compiler-owned map and relation
projections are regenerated from the combined candidate and preserve existing
approvals. Claimed-path overlaps and changed pinned semantic sources are
rejected without ref mutation, pending explicit source or plan revision.
Authored merge conflicts remain unresolved. No stale target grants a Slot or
worktree. `reopen-item` reactivates a sealed Item on the Integration that
absorbed it, so a target refreshed after integration never strands the Item.
A rejected remote transaction changes no ref and names the
lease it lost, or a remote without atomic push support, from the refetched
refs. One whose refetched refs hold its candidate, or moved on from it, may
have landed and reports `DELIVERY_TRANSACTION_UNCERTAIN` instead. Release
Management is intentionally not part of this flow.

`status --summary --run live_test:<group>` reports heartbeat, elapsed time and output.
A stale heartbeat means an interrupted runner, not a failed test. Adapters may write
`scratch/live-progress.json` under `AGENTROF_VERIFICATION_SCRATCH`, naming `test_id`,
`cases_done`, `cases_total` and `failures`; these observations never satisfy a gate.

With `live_environment_plan: effect_ordered_v1`, regression selection exports the
plan to the adapter: deduplicated builds, one shared environment, fixtures,
parallel readers, serial changes, resets and removals, with readiness/reset/residue
checks. Missing metadata runs as serial `changes`. The adapter owns service commands
and reports per-phase setup counts; final outcomes require completed command evidence.

A permission probe validates without execution; host authorization stays unknown.
Show the owner exact allow rules, including optional `live_permission_commands`.
Permission-only resume keeps successful evidence and lists refused `resume_commands`.

Finding-control bindings persist in the Item verification record and Delivery
Review. After merge, the next ordinary backlog revision lists unabsorbed
targets and requires them in the owning Test Plan before approval.

For one preflight-only retry, or a run whose record names `interrupted_by` (host sleep or network
loss), use `run --kind live_test --group <group> --resume` with the contract's `live_resume_command`,
on the same candidate and environment. The runner preserves the first attempt and verifies
`phase_evidence` files and hashes before passing only pending phases. It refuses stale or changed
evidence, a second retry or any failed product case. Preflight runs again before tests.

After failed QA, an approved `live_diagnostic_command` enables `repair-live
--delivery DLV-### --story <story>` on a committed repair after readers settle.
QA's `failed_live_cases` names only failed ids in the adapter's schema-1
`live-case-report.json` groups. Each selected case must be recorded as passed
on this candidate; zero exit status alone is insufficient. A missing, stale
or failed repair record blocks freeze; the full QA round still runs.
