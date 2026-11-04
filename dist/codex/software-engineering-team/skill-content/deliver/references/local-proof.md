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

The task-record-only change allowed by the compiler does not authorize reuse
across code changes. Before reuse, verify dependencies, command selection and
environment inputs still match. Re-run affected proof when they change.
A recorded exit code is evidence of that run, with its stated coverage.
On POSIX, a command that leaves background processes in its process group
fails proof and the runner stops that group. Timeout and cancellation also
stop the group. Unconfirmed cleanup blocks subsequent proof until reconciled.
Detached sessions and external services require the project's own cleanup
contract. Windows uses process-tree termination on timeout or cancellation,
which cannot establish cleanup of children whose parent has already exited.
Use a project runner with verified lifecycle isolation for that workload.

## Selection by impact

`local_proof_map` is an ordered list of
`{"paths": ["glob"], "commands": ["command"], "reason": "coverage"}` rules.
Use `"none": true` in place of commands only for a justified no-test path.
Matching ignores case and `*` spans folders. The first matching rule per file
selects commands, and their union runs once. An uncovered path, missing map,
empty diff selects full `local_proof`. Risk determines required coverage through
the selected methods and project rules. An explicit impact map remains in force.
`prove --full` explicitly selects the full proof.

Declare the required live or environment check as an executable command when
it belongs in this proof. Keep its revision and environment identity with the
result. Post-merge readiness still checks the actual integrated revision in
the intended environment.

## Share a full proof across a batch

Use this only when one atomic integration PR is authorized. Member branches
remain independently reviewable, while the combined candidate receives the
full proof and one independent native review.

- `prove --task <id> --deferred` records pending batch proof and runs no test.
  An ordinary task merge rejects deferred and combined proof records.
- `train add --task <id> --pr <number> --critical 0` records the current local
  and remote member head. Deferred members await aggregate review. Members
  with ordinary proof require their own fresh review record. New members after
  sealing join the next batch.
- `train prove --batch <n> --base <branch>` integrates the recorded members
  into `train/<n>` and runs full proof. The candidate and base are fixed for
  that result. Changed members or base require renewed proof and review.
- `review --batch <n> --host <host> --base <branch>` reviews the combined
  candidate. Shared procedure text is supplied once, with assessment coverage
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
  candidate. `train bisect` samples failing prefixes and reports a suspect. Prefer
  the actual failure evidence and bounded diagnosis for interacting defects.

Observe the merge result, reconcile linked member PRs, then verify readiness.
A green final tree gives no independent proof for partially merged prefixes.
Keep batch membership, review and proof identity together throughout resume.
