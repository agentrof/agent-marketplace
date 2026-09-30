# Delivery Execution Flow

Spawn template: paste `{{constitution}}` into every role prompt.

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

## Parallel verification

Follow the approved Item's `verification_schedule`. An older Item with no
field retains the sequential protocol. For `parallel_snapshot_v1`, commit
the complete product/test candidate and use the packaged
`scripts/delivery_verification.py --worktree <item-root> freeze --delivery
DLV-### --story <story>` before invoking the two readers. The resulting
candidate and session identities bind all source and instruction inputs.
Generate each role's `manifest` with `--role code_reviewer|qa_engineer` and
`--mode review_initial|review_repair|qa_diagnostic|qa_final`.
While readers are active, use the same CLI's `inspect --path <file>` and
`diff [--path <file>]` to read the frozen Git candidate. `inspect --base`
selects its exact integration base. These interfaces permit source inspection
without opening a general shell writer through the barrier.
Use `inspect --instruction <package-relative-file>` for bound package instructions.

Invoke Code Review and QA independently through the host's native agent
mechanism. Keep the implementation writer idle until both readers finish or
their cancellation is confirmed. Readers return separate JSON results through
`result --file <result.json>`; the existing owner alone persists canonical
reports after the barrier. A cancelled or diagnostic result cannot approve an
Item. Code, test, contract or instruction drift invalidates the candidate.

QA uses `run --kind test|mutation|dependency_audit` for the approved commands.
For failed or affected tests first, an optional approved
`diagnostic_test_command` enables `run --kind diagnostic_test --selection-file
<scratch-selection.json>`. Copy the selector schema from the QA manifest,
fill literal `failed_test_ids` and `affected_test_ids`, and write it under the
manifest's scratch directory. The runner validates the candidate binding and
passes normalized selection data through `AGENTROF_DIAGNOSTIC_TESTS`; it never
appends those identifiers to shell text. The compiler-owned JSON adds
`selected_test_ids`, the sorted union of the failed and affected identifiers.
Only diagnostic commands receive this environment variable; final checks and
runtime commands remove it, including any inherited value.
A changed selection cannot reuse an
older diagnostic run. Without this approved adapter, use the full approved
test command or revise the Operation Contract through its normal approval.
Diagnostic command evidence never satisfies the final full-suite gate.
Commands run in an independent scratch checkout of the frozen product commit,
with no shared Git objects or mutable working files. The runner exposes an
output directory through `AGENTROF_VERIFICATION_SCRATCH`. Provision dependencies through
approved commands and environment, never by borrowing the writer's ignored
files. Tests and mutation cannot temporarily edit the reviewer's source.
The runner stores raw output under ignored runtime, binds it to the candidate,
command and environment, and reuses only identical successful evidence.
`--fresh` explicitly reruns a command. The mutation command reads the exact
JSON file scope through `AGENTROF_MUTATION_FILES`; no shell path interpolation
is permitted. The QA result names the raw evidence identities and records
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

`validate --delivery DLV-### --story <story>` must accept both final results
before evidence approval. The existing compiler derives actual product HEAD
and persists the two reports. Its subsequent evidence child commit does not
change the verified product candidate; integration continues to prove the
exact direct-parent binding. Runtime verification is mandatory exactly when
the approved Item declares `runtime_required: true`.
For that Item, run the approved Environment Contract through
`environment --verb down|up|seed|logs|url [--value <approved-identifier>]`.
Its persistent private checkout supports the existing fresh-runtime protocol;
return the recorded event hashes with the full per-surface runtime findings.
Successful down, up, seed, logs and final down are required, alongside the
independent runtime assessment. A non-runtime Item starts no environment.

## Activation and publication

Activation writes an ignored pending writer receipt before the atomic Item,
Slot, Integration and Fence transaction. The receipt is promoted only after
both Item and Slot refs equal the candidate OID, then the coordinator
materializes the detached Item worktree from that exact OID. A missing receipt
does not change remote semantic status, but it denies local writer readiness
until the exact remote activation is re-verified or explicitly taken over.
Takeover is an explicit host-loss decision; it reuses the existing Item and
Slot refs, elects a new writer epoch under exact leases and never allocates a
second Slot.

After implementation, only the current Item's initialized Code Review and
Verification reports may have uncommitted authoring changes. Evidence approval
derives both reviewed and verified commits from that worktree's real `HEAD`;
it never accepts caller-supplied commit text. The only permitted uncommitted
files after approval are that Item's `code-review.md` and `verification.md`.
Approval and publication reject tracked entries with `assume-unchanged` or
`skip-worktree` flags, without modifying the index.
`push-item` attaches those records to the committed product/test tip as one
`item-evidence-v1` child and advances Item plus Slot together. It rejects a
product commit that edits Delivery control files, except the current required
Architecture Item's compiler-stamped delta hash and refreshed source hash.
That exception preserves every other Item byte and control path, and requires
the exact committed, sealed delta to remain within approved architecture claims.
An Item whose writer converged it on a refreshed Integration may also carry
that Integration commit's Delivery controls byte for byte and record the commit
as its `integration_base_commit`. The commit must lie on the Integration's own
line after the Item's previous base and be contained in the product tip, and
the Item record keeps the commit's plan-owned fields with only its own status,
stamp and base.
`push-item` also rejects a product or test path outside the Item's path claims,
which cover their paths and everything below them. Vault paths keep the
control, Architecture and projection rules, and a path held exactly as the
Item's `integration_base_commit` holds it is not the Item's change.
`integrate-item` reads the
evidence from the remote Item ref and requires that its exact direct parent is
the reviewed and verified product/test tip.

The flow ends in one Delivery Review, one final PR and provider-neutral merged
evidence. The Review is authored as a draft `delivery-review.md` in the
Delivery package before `approve-review`. Approval keeps every authored
section, fills only the sections left empty and the navigation the compiler
owns, and binds that content in its approval hash; the PR body is that Review.
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
Before Item work starts, `/deliver DLV-###` may run the internal
`refresh-target` coordinator. A disjoint target advance becomes one
`target-refresh-v1` Integration child. Compiler-owned map and relation
projections are regenerated from the combined candidate and preserve existing
approvals. Claimed-path overlaps and changed pinned semantic sources are
rejected without ref mutation, pending explicit source or plan revision.
Authored merge conflicts remain unresolved. No stale target grants a Slot or
worktree. `reopen-item` reactivates a sealed Item on the Integration that
absorbed it, so a target refreshed after integration never strands the Item.
Failed checks, target drift, review changes and process loss become explicit
resumable states. A rejected remote transaction changes no ref and names the
lease it lost, or a remote without atomic push support, from the refetched
refs. One whose refetched refs hold its candidate, or moved on from it, may
have landed and reports `DELIVERY_TRANSACTION_UNCERTAIN` instead. Release
Management is intentionally not part of this flow.
