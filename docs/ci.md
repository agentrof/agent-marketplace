# CI and release validation

The required `check` aggregate proves the selected test inventory completed.
Every PR also emits the existing compatibility, CodeQL and two-host lifecycle
contexts. Branch protection names remain stable. The compatibility context
label `Python 3.x` represents the current interpreter pinned in the CI policy.

## Test scope and execution

`tools/data/ci-test-policy.json` owns operating systems, Python minor versions,
shard counts, impact groups, dependency closures and initial duration weights.
`tools/ci_tests.py` inventories individual unittest cases without running them,
selects their scope and balances independent partitions by duration. Each
worker verifies the plan and runtime discovery before executing its assigned
IDs. The aggregate checks the exact report set and test IDs: missing,
duplicate, failed, cancelled or mismatched results fail validation. Explicit
platform skips remain visible; mandatory native Windows regressions cannot
skip.

The small macOS minimum-version suite uses one worker. That worker first
executes the seven Apple system-Python launcher cases with the original system
environment, before installing the policy-selected Python. The validated plan
structure assigns exactly one Apple owner whenever vault-hook tests are
selected; missing, duplicate or disabled ownership fails plan validation.
This avoids a separate macOS job competing with the full-suite workers.

Delivery PR-intent fixtures may copy an immutable, process-local starting
repository prepared before the first Item starts. Every test gets independent
files, Git objects and a bare remote; the origin is rebound to that copy and
transient fetch metadata is removed. The seed contains no linked Item worktree
or active writer receipt. Construction and isolation have dedicated coverage;
changed setup functions or environment use fresh preparation. Git operations
under test, including concurrent ref and lease observations, remain real.

| Profile | Selection |
| --- | --- |
| `full` | All tests on the primary Linux and macOS lanes, plus the complete native compatibility policy on macOS minimum Python and both Windows interpreters |
| `impact` | Always-required contracts and the transitive affected groups for the complete base-to-candidate diff |
| `release` | Release, packaging, setup, upgrade and CI contracts after trusted source evidence, deterministic replay and runtime-equivalence proof |
| `reuse` | Prior successful validation of identical input, with fresh static and transition checks |

Unknown paths, new test modules, shared fixtures, workflow/selection changes
and shared runtime inputs select full coverage. Existing test changes include
their consumers. Plugin Markdown is shipped behavior, not repository-only
documentation. Generated changes without corresponding canonical changes
select full coverage. Both sides of renames and deleted files participate in
impact analysis.

Fresh reports contain per-test duration, outcome and actual Python, Git and
operating-system identities. Successful runs publish bounded timing history
for later plans. Timing data changes partition balance only; missing or
invalid history uses policy weights without changing coverage. Weekly and
manually dispatched validation run full coverage. `make check` remains an
exhaustive local gate; `make static-check` provides the cheap always-fresh
contract, version, count and deterministic distribution checks.

## Reusing validation evidence

The immutable Actions artifact is scoped to its run and attempt. It binds the
actual checked-out SHA and tree, complete selected plan, runtime identities
and the hash of the workflow, selection, evidence and host-version contracts.
Only a successful run of this repository's validation workflow can supply
reusable evidence. The reader checks the latest relevant run and attempt,
artifact provenance and digest, safe archive shape, age (at most 24 hours),
repository identity, exact tree and plan legitimacy. A failed, running or
rerun successor invalidates an older result. Missing or unusable evidence
selects fresh full validation. Fork and scheduled runs still verify all their
reports but do not emit reusable receipts.

The merge path additionally verifies that the receipt tested the selected
same-repository PR's merge tree and that GitHub identifies the resulting
commit as that PR's merge. Before recording inherited evidence, the aggregate
rechecks its source so a retry or expiry during execution cannot pass. Inherited
run IDs and artifact digests remain in the receipt for audit. A caller cannot
replace publication evidence with a preparation result.

Within a single run, plan and per-partition report artifacts use stable logical
names so GitHub's failed-job retry can retain successful producers. Rerunning a
producer replaces only its own intermediate artifact. The aggregate still
requires every report to match the exact plan hash and source tree; mixed
plans fail. Final evidence remains immutable and names the current run attempt,
so a prior attempt's receipt never authorizes a retry.

Unit-test evidence covers repository test execution. It does not grant host
installation coverage. A separate checkout-lifecycle receipt binds successful
real Claude Code and Codex installs to the tested tree, smoke harness, pinned
CLI versions and host runtime policy in `tools/data/ci-host-policy.json`.
The host reader verifies the trusted source workflow, merged PR, latest run and
attempt, age, artifact provenance and digest. Only the explicitly supplied
prepare/publish candidate may reuse it. PR, standalone manual and scheduled
host checks remain fresh; fork and scheduled runs do not emit reusable proof.
The required host aggregate rechecks the exact source before accepting reuse,
and rejects changed or expired proof. Missing proof at planning runs fresh
host installs. Failed-job retries retain the logical planning artifact while
final receipts remain attempt-specific.

Neither evidence type replaces current Git topology, release policy,
ref-transaction checks, CodeQL or newly published public-channel installs.
The read-only validation workflows never publish refs or releases.

## Release transitions

1. A feature PR runs its selected tests and both real host lifecycles.
2. After merge, main runs fresh static gates and either verifies equivalent
   PR evidence or executes full tests. CodeQL continues to run on main.
3. Explicit release preparation consumes exact-main validation (or runs full
   validation), verifies matching checkout-host evidence or exercises both
   hosts, builds the release on Linux and checks all static contracts before
   publishing the candidate branch.
4. The release PR always proves its one-commit deterministic replay from
   trusted main. A separate classifier compares complete Git bytes and modes,
   allowing only closed version/provenance/catalog/changelog/changeset
   transformations. Unchanged runtime payload plus valid main evidence permits
   the release profile. Any unrecognized transformation or missing evidence
   selects full tests. Both real host lifecycles still exercise the candidate.
5. Publication verifies the exact two-parent merge and its tree, consumes the
   successful release PR validation or executes full tests, and verifies
   matching checkout-host evidence or exercises both hosts. Only then may the
   existing trusted transaction stage stable/tag refs.
6. Fresh public-channel installs verify the newly staged exact SHA on both
   hosts. The public smoke CI target does not repeat unit tests; its required
   predecessors already verified candidate coverage. Rollback, immutable
   Release reconciliation, exact-lease cleanup and clean-main completion keep
   their existing contracts.

The first optimization PR cannot inherit evidence from older workflows and
therefore runs full validation. Workflow or policy changes invalidate prior
receipts deliberately. Measure the first full PR by the slowest shard,
including runner queue time; measure a release separately from preparation
dispatch to publication and to final main validation. Parallel publication
and main validation must not be added together. Timing targets are acceptance
goals, not grounds to omit a failed or slow gate.
