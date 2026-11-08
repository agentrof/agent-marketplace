# Local proof and atomic integration

Read this when a task uses local proof or shares proof with a batch.
`pr_proof: remote_ci` keeps required remote checks authoritative.
`pr_proof: local_record` requires recorded local proof before ordinary merge.

## One candidate

1. Commit the intended candidate and leave its worktree clean. Run
   `task_lane.py prove --task <id> --base <branch>` with the installed script.
2. Commands come from `local_proof`, with `make test` as the fallback and
   `{base}` replaced by the target branch. `proof_runner`, when configured,
   wraps each command. The proof runner admits one shared-environment proof
   at a time and bounds command duration. Busy capacity leaves work pending.
3. The runner stops at the first failure. Diagnose and fix its cause, commit
   the changed candidate and prove it again. Keep the first failure visible.
4. The runtime record holds the candidate head, cleanliness, command results,
   timings and selection reason. `open-pr` checks the record in local mode.
   Run `task_lane.py review --task <id> --host <host>` for independent
   review of the same candidate. Resolve blocking findings and merge with its
   full `--head` and `--critical 0`. Merge requires the fresh native review record.

A recorded exit code is evidence of that run, with its stated coverage.
On POSIX, a command that leaves background processes in its process group
fails proof and the runner stops that group. Timeout and cancellation also
stop the group. Unconfirmed cleanup blocks subsequent proof until reconciled.
Detached sessions and external services require the project's own cleanup
contract. Windows uses process-tree termination on timeout or cancellation,
which cannot establish cleanup of children whose parent has already exited.
Use a project runner with verified lifecycle isolation for that workload.

## Optional reuse of successful proof

Without `proof_reuse` in `workspace/config.json`, each proof invocation runs its
selected commands fresh. To opt in, provide `identity_command` as a non-empty
argv list, for example `["project-proof-identity"]`. It executes
directly without an implicit shell. Optional `max_age_seconds` accepts integers
from 1 to 86400, with 86400 as the default.

The project-owned probe must be cheap and metadata-only. Return stable opaque
stdout covering actual installed dependencies, untracked fixtures, toolchain
state and relevant external service generations. A lockfile alone cannot
establish what is installed. The probe has a five-second timeout and a 64 KiB
output limit. Raw probe output and raw environment values are never persisted
in proof records.

The runtime automatically binds the exact candidate and proof root, tracked
code and tests, selected commands and runner, scope, proof runtime and
environment identity. A successful, complete record can be reused only while
these inputs and the probe identity still match and its age remains valid.
Reused results retain their original execution timestamps. Changed inputs,
missing or unreadable evidence, expiry, probe failure, or a failed or
interrupted proof cause a fresh run. Legacy records receive one fresh run
before becoming eligible. Task-record edits cannot establish a cache hit for
a different candidate.

Unconfirmed probe cleanup blocks shared proof until reconciled, just as
unconfirmed test-process cleanup does.

The project owns identification and isolation of mutable external state.
Reuse validates the recorded input contract. Test coverage and the probe's
completeness still require engineering judgment. Keep volatile live checks
fresh when their environment cannot provide a reliable generation identity.

## Selection by impact

`local_proof_map` is an ordered list of
`{"paths": ["glob"], "commands": ["command"], "reason": "coverage"}` rules.
Use `"none": true` in place of commands only for a justified no-test path.
Matching ignores case and `*` spans folders. The first matching rule per file
selects commands, and their union runs once. If one selected rule has exactly the
configured `local_proof` command set, only `local_proof` runs, in its configured order;
narrower commands are omitted unless another selected rule adds commands to the
full set. In that case the complete impact union is retained.
This compares exact commands after `{base}`
substitution, without interpreting shell syntax. An uncovered path, missing map,
empty diff selects full `local_proof`. Risk determines required coverage through
the selected methods and project rules. An explicit impact map remains in force.
`prove --full` explicitly selects the full proof.

Declare the required live or environment check as an executable command when
it belongs in this proof. Keep its revision and environment identity with the
result. Post-merge readiness still checks the actual integrated revision in
the intended environment.

## Share proof across a batch

Use this only when one atomic integration PR is authorized. Member branches
remain independently reviewable, while the combined candidate receives the
selected proof and one independent native review.

- `prove --task <id> --deferred` records pending batch proof and runs no test.
  An ordinary task merge rejects deferred and combined proof records.
- `train add --task <id> --pr <number> --critical 0` records the current local
  and remote member head. Deferred members await aggregate review. Members
  with ordinary proof require their own fresh review record. New members after
  sealing join the next batch.
- `train prove --batch <n> --base <branch>` integrates the recorded members
  into `train/<n>` and applies `local_proof_map` to the complete fixed-base
  candidate diff, using the same fallback and optional reuse contract as task
  proof. Existing valid full-proof receipts remain accepted for that candidate.
  The candidate, base and member heads are fixed for that result. Changed members
  or base require renewed proof and review. `--dry-run` creates no candidate:
  its command list is labelled `conservative_full`, with selection `unknown`,
  because the actual impact plan requires the integrated candidate.
- `review --batch <n> --host <host> --base <branch>` reviews the combined
  candidate with the same impact-selected command contract. Shared procedure text
  is supplied once, with assessment coverage
  required for each applicable member.
- `train publish --batch <n> --base <branch>` publishes or reconciles one
  integration PR after fresh aggregate review. A publication
  whose outcome is unknown stays pending until remote state is reconciled.
- `train merge --batch <n> --base <branch> --pr <integration-pr> --head <sha>
  --critical 0` merges that integration candidate. Add `--delegated` only
  under explicit high-risk merge authority. Required CI still applies in
  remote mode. Native branch protection must handle concurrent base updates.
- `train status` reports state. `train remove --task <id> --batch <n>` can
  remove a member from an open, red or green batch, invalidating the old
  candidate. `train bisect` samples failing prefixes with the original batch's
  selected commands and reports a suspect. It does not narrow proof to each
  prefix's diff. Prefer actual failure evidence and bounded diagnosis for
  interacting defects.

Observe the merge result, reconcile linked member PRs, then verify readiness.
A green final tree gives no independent proof for partially merged prefixes.
Keep batch membership, review and proof identity together throughout resume.
