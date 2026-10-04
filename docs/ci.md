# CI and release validation

The required `check` aggregate proves the selected test inventory completed;
it depends directly on the shard results. Every PR and every merge queue group
also emits the CodeQL and two-host lifecycle contexts. Branch protection
requires `check`, `analyze-python` and `Claude Code and Codex lifecycle`.

## Test scope and execution

`tools/data/ci-test-policy.json` owns the one supported Python version,
operating systems, shard jobs and worker processes per job, impact groups,
dependency closures and initial duration weights. CI tests that Python once per
operating system. Linux runs every test. macOS and Windows run only the tests
that prove behavior of their own system, each named by its exact ID: the
platform group both run (worker processes, the hook launcher, case-insensitive
file systems), the macOS group (file flags, the bare system interpreter) and
the Windows group (path separators, junctions, long paths, CRLF checkouts,
locks, text pipes and Git for Windows). A test that repeats platform-neutral
logic runs on Linux only. The plan job sets up the version itself and every
other validation job takes it from the plan's output; a test pins that literal,
the release workflows' versions, the host lifecycle policy and the plugin's
runtime floor to the policy. `tools/ci_tests.py` inventories individual
unittest cases without running them, selects their scope and balances them by
duration over every worker process of a lane; each shard job then takes whole
processes. A job starts its planned processes, folded onto fewer when the
runner reports fewer CPUs, and each process runs its IDs in ID order, so a
module's fixtures start once per process. Each worker verifies the plan and
runtime discovery before executing its assigned IDs. The shard report merges
the workers' results, one row per planned test, and records each worker's test
count and wall time; a worker that fails, exits early or reports a test outside
its set fails the shard. The aggregate checks the exact report set and test
IDs: missing, duplicate, failed, cancelled or mismatched results fail
validation. Explicit platform skips remain visible; mandatory native Windows
regressions cannot skip.

A worker, in CI or local validation, runs its tests with tripwire `claude`
and `codex` binaries in place of the host binaries a session names:
`CLAUDE_CODE_EXECPATH`, `CODEX_CLI_PATH`, `CODEX_VERSION` and `CODEX_HOME`
point at them and `CLAUDE_PID` is removed. A project generator or
`host_models.py` that a test runs with the runner's own environment therefore
reaches a tripwire, never the developer's Claude Code or Codex, its MCP
servers or its account's model cache. A tripwire records the call and fails;
the worker fails the test during which it ran, or the shard when a class or
module fixture ran it after the last test. A test pins fakes of its own with
`fixtures.isolated_hosts`. `make check` runs unittest without the tripwires.
Every CI worker process also gets its own `TMPDIR`, `TMP` and `TEMP` under the
runner's work directory, outside any Git checkout; on Windows that directory is
on the work disk, which creates small files several times faster than the
system disk that holds the default `TEMP`.

Delivery compiler and execution fixtures may copy an immutable, process-local
starting repository prepared before the first Item starts. PR-intent fixtures
may copy the state `prepare_pr_creation` leaves for the default Item:
integrated and reviewed, its worktree removed and its writer receipt released;
its verification sessions stay, named by the removed worktree's path, so no
copy reads them. Setup and project vault tests may copy a project one
`setup_project.py apply` left, which holds no absolute path. Every test gets
independent files, Git objects and a bare remote; the origin is rebound to that
copy and transient fetch metadata is removed. No seed contains a linked Item
worktree or an active writer receipt. Construction and isolation have dedicated
coverage; changed setup functions or environment use fresh preparation. The
named Windows text-pipe emulator may build a separate seed under its exact
wrapper and reuse it only within that wrapper's lifetime. Its underlying runner
must be the native runner and every other setup binding and environment value
must remain unchanged; seeds are discarded on context exit. Arbitrary mocks
never qualify. Git operations under test, including concurrent ref and lease
observations, remain real.

| Profile | Selection |
| --- | --- |
| `full` | Every test on Linux; on macOS the platform and macOS groups; on Windows the platform and Windows groups, with the mandatory native regressions |
| `impact` | Always-required contracts and the transitive affected groups for the complete base-to-candidate diff of a PR or merge queue group |
| `reuse` | Prior successful validation of identical input, with fresh static and transition checks |

Unknown paths, new test modules, shared fixtures, workflow/selection changes
and shared runtime inputs select full coverage. Existing test changes include
their consumers. Plugin Markdown is shipped behavior, not repository-only
documentation. Generated changes without corresponding canonical changes
select full coverage. Both sides of renames and deleted files participate in
impact analysis.

Fresh reports contain per-test duration, outcome and actual Python, Git and
operating-system identities. Successful runs publish bounded timing history
for later plans. Plans report each history source run, attempt, age and
artifact digest, restored weight counts and fallback reasons. History is bound
to the exact policy, so the first plan after a policy change weighs each test
by the policy's `test_seconds` for its runner system: the tests that took at
least five seconds, refreshed from a run's `ci-durations` artifact with
`python3 tools/ci_tests.py estimates --durations <file>`. Every other test
weighs `default_seconds`. Fixture startup estimates favor reusing a
process-local seed without creating empty partitions. Reports distinguish test
time, fixture build/validation/copy time, worker wall time and shard wall time.
Timing data changes partition balance only; missing or invalid history uses
policy weights without changing coverage. Weekly and
manually dispatched validation run full coverage. `make check` remains an
exhaustive local gate; `make static-check` provides the cheap always-fresh
contract, version, count and deterministic distribution checks.

## Exact local validation

After generating distributions, stage the complete candidate and run:

```text
make check-local
make verify-local
```

The direct interfaces are `python3 tools/ci_local.py check --staged --target
origin/main` and `python3 tools/ci_local.py verify --staged --target origin/main`.
`check --fresh` ignores saved test results. `--jobs` selects one to four separate
processes, bounded by CPU count; policy defaults to two. The processes are
balanced with the per-test estimates of the full-suite lane's system, refined
by the local system's own. Every worker receives
its own `TMPDIR`, `TMP` and `TEMP`, exact test IDs and an independent result file.
Worker scratch directories live outside the candidate checkout and any Git
worktree ancestry, so a non-Git fixture cannot discover an enclosing repository.
A configured temporary root inside a checkout or Git repository is rejected;
choose an external `TMPDIR`/`TMP`/`TEMP`. Receipts remain in the ignored project
runtime cache.
Before starting workers, isolated Python with site initialization disabled
warms a fresh external bytecode cache from an explicit standard-library import
list. Workers share that cache with bytecode writes disabled; project and
fixture bytecode are never populated there. Cache contents are checked after
workers finish, and a prewarm failure or changed cache invalidates the run.
`make check` still runs all tests without requiring staging or producing a local
receipt. No Git hooks are installed.

The local driver compares every tracked worktree byte and executable mode with
the index, using NUL-safe Git records. Partial staging, unresolved merges,
assume-unchanged/skip-worktree flags, unsupported index entries (including
tracked symlinks whose external targets cannot be bound) and untracked
source files fail before tests. Ignored runtime caches are explicitly allowed
by `ci-local-policy.json`; other ignored untracked sources fail. On Windows,
executable bits remain the index's identity because the filesystem does not
provide POSIX executable modes. Checkouts transformed by text filters must
first match their staged bytes. Impact includes the entire merge-base to index
candidate, including earlier branch commits, deletion and both sides of a
rename. Missing base, unknown/shared inputs or incomplete inventory mappings
select the full suite. The command never fetches refs.

Every `check` executes static validation afresh. A successful local receipt
binds HEAD/base/index bytes and modes, inventory, selected IDs, command and
policy hashes, Python/Git/OS, Git configuration and environment digests. The
environment digests cover only the variables `ci-local-policy.json` binds, the
names and prefixes that can change what the tests do, such as `PATH`, `LANG`,
`LC_*`, `TZ`, `PYTHON*`, `GIT_*` and the host variables the tools read. Host
session ids, sandbox tokens and scratch roots are not bound, so a `verify` from
another agent session of the same host accepts the receipt. Each bound value is
written only as a digest keyed with a random key of the local cache, never as
the value. A test fails when the tools read a variable that is neither bound
nor listed as unbound with its reason. When the receipt differs from the plan only in its
environment, `verify` names the changed variables, never their values. Make's
orchestration variables are removed before checks so direct and Make entry
points use the same effective environment. `verify` without `--jobs` uses the
worker count of the receipt's `check`. The Git configuration digest leaves out
the entries `git_configuration_ignored` names, `branch.*` and `remote.*.fetch`,
which other worktrees of the same repository rewrite when they create, track,
fetch or delete branches. For the same reason the run does not watch the shared
`config` and `packed-refs` files for writes; the candidate's HEAD, base and
bytes and the filtered configuration are compared instead.
Results expire after at most 24 hours; reuse does not extend that deadline.
The latest failed, interrupted, changed or corrupt attempt invalidates prior
success. The source is rechecked after statics and workers. Missing, duplicate,
partial or failed worker reports and skipped mandatory native regressions fail.
`verify` checks this identity immediately before commit. A process-scoped OS lock
prevents simultaneous validators in one checkout and releases on process exit.

Receipts live only in ignored `.agentrof/agent-marketplace/.runtime/ci-local/`.
They are disposable local feedback, never remote CI or release authority.

## Performance evidence

`ci-performance` artifacts and the job summary separate runner scheduling after
known dependency completion from test steps, whole-job time and fixture costs.
Unfinished jobs or unknown dependency times stay unknown. The reporter reads
one exact run attempt and has no gate or receipt authority. Its absence cannot
turn a failed validation into success; the normal coverage gates remain required.

`tools/data/ci-performance-policy.json` owns comparison windows, sample minima,
change classifications supplied by observations, observational metrics and target
reductions. `python3 tools/ci_performance.py compare --samples <json> --output
<json>` compares each change class separately. The input is an array of records:

```json
[{"cohort": "baseline", "change_class": "runtime", "metric": "local_validation_seconds", "value": 120, "observed_at": "2026-09-28T10:00:00Z"}]
```

Use actual measured values and baseline/candidate cohorts. The default window is
30 days with at least five observations per cohort. Too few samples and missing
quality/platform measurements remain explicit; they never establish improvement.
Local validation targets 40% lower median, Delivery test and review-to-acceptance
30%, with zero omitted required gates or platform checks. Work duration, model,
agent preparation, commands, queue time, tokens, repair rounds and escaped or
reopened defects are observations without invented targets. Model/role telemetry
must be supplied by its actual host; no token or quality measurements are inferred
from test timings.

## Reusing validation evidence

The immutable Actions artifact is scoped to its run and attempt. It binds the
actual checked-out SHA and tree, complete selected plan, runtime identities
and the hash of the workflow, selection, evidence and host-version contracts.
Only a successful run of this repository's validation workflow can supply
reusable evidence. The reader checks the latest relevant run and attempt,
artifact provenance and digest, safe archive shape, age (at most 24 hours),
repository identity, exact tree and plan legitimacy. A failed, running or
rerun successor invalidates an older result. Missing or unusable evidence
selects fresh full validation. Fork, scheduled and merge queue runs still verify
all their reports but do not emit reusable receipts.

The merge path additionally verifies that the receipt tested the selected
same-repository PR's merge tree and that GitHub identifies the resulting
commit as that PR's merge. Before recording inherited evidence, the aggregate
rechecks its source so a retry or expiry during execution cannot pass. Inherited
run IDs and artifact digests remain in the receipt for audit. Only `main`'s
push validation inherits evidence.

Within a single run, plan and per-partition report artifacts use stable logical
names so GitHub's failed-job retry can retain successful producers. Rerunning a
producer replaces only its own intermediate artifact. The aggregate still
requires every report to match the exact plan hash and source tree; mixed
plans fail. Final evidence remains immutable and names the current run attempt,
so a prior attempt's receipt never authorizes a retry.

Unit-test evidence covers repository test execution and never grants host
installation coverage. Every PR, merge queue group, manual and scheduled run
installs both real hosts fresh, on the runner, Python and Node versions
`tools/data/ci-host-policy.json` pins and the CLI versions
`tools/data/host-cli-versions.json` pins; no host result is reused.

Evidence never replaces current Git topology, release policy, ref-transaction
checks, CodeQL or newly published public-channel installs. The read-only
validation workflows never publish refs or releases.

## Release transitions

1. A pull request runs its selected tests and both real host lifecycles. A
   release commit, the last commit of a pull request, changes only version
   surfaces, the changelog, release metadata, consumed changesets and
   `dist/`, so the same run tests the final release tree, and `check-pr`
   proves the commit is the deterministic bump of its parent at the commit's
   own committer date, which names the release `YYYY.M.N` for that month
   however late the pull request merges. When `main` requires the merge
   queue, each queued group repeats the required checks on the exact commit
   `main` moves to, selecting impact coverage over the group's complete diff.
2. After merge, `main` runs fresh static gates and either verifies equivalent
   PR evidence, in about a minute, or executes full tests. A queued commit
   reuses PR evidence only when its tree equals the PR's tested merge. CodeQL
   continues to run on `main`.
3. A release dispatches the `Release` workflow for one `main` commit. The
   `Auto release` workflow does so on the push that merges a release commit,
   when `versions.json` names a version no release tag holds yet; `ship`
   does so by hand. The `Release` workflow checks the commit's release state,
   including a version newer than every release tag or, with no tag at all,
   than the version of the commit `stable` points to, an ancestor of the
   release commit. It waits for that commit's own `main` validation and
   requires its success, and never runs the tests or the host lifecycles
   again. It then stages the version tag and `stable` with exact
   leases, installs both hosts from the real public `stable` channel and
   creates the immutable Release. Rollback, immutable Release reconciliation
   and clean-main completion keep their contracts.

Workflow or policy changes invalidate prior receipts deliberately. Measure a
release from the merge to the immutable Release, and the pull request that
carries the release commit by its slowest shard, runner queue time included.
Timing targets are acceptance goals, not grounds to omit a failed or slow
gate.
