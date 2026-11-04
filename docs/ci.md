# CI and release validation

The required `check` aggregate proves the selected test inventory completed;
it depends directly on the shard results. Every PR also emits the CodeQL and two-host lifecycle contexts. Branch protection
requires `check`, `analyze-python` and `Claude Code and Codex lifecycle`.
The PR release-impact check shares the deterministic-check job's checkout and
Python setup. Its failure fails that job and the required aggregate. Superseded
PR validations cancel by PR number. Main validations use the commit SHA and
event type so a later push, manual run or schedule preserves the push validation
a release is waiting for.

## Test scope and execution

`tools/data/ci-test-policy.json` owns the one supported Python version,
operating systems, shard jobs and worker processes per job and initial
duration weights. CI tests that Python once per
operating system. Linux runs every test. macOS and Windows run only the tests
that prove behavior of their own system, each named by its exact ID: the
platform group both run (worker processes, the hook launcher, case-insensitive
file systems), the macOS group (file flags, bare `python3` commands, the git
that a macOS gate worker calls) and the Windows group (path separators,
junctions, long paths, CRLF checkouts, locks, text pipes and Git for Windows).
A test that repeats platform-neutral logic runs on Linux only. Running every
test on macOS was measured and left out: the macOS runners took pull request CI
from 2 min 32 s to 6 to 7.5 minutes. The plan job sets up the policy's
major.minor itself, records the exact release it resolved as the plan's
`python_release`, and every other validation job sets up that release from the
plan's output, so a toolcache update during a run cannot give two shards of one
lane different patch releases; each shard and the aggregate refuse a runtime
on any other release. A test pins the major.minor literal,
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
validation. Test-declared skips remain visible in the reports and count as
accepted outcomes. The aggregate verifies accounting. Reviewers assess the
justification for each skip.

The shared platform group covers detached retrospective collection, contended
and duplicate queue writes, native failure hooks, bounded Graphify execution,
serial proof execution and work-queue fencing. These run on both macOS and
Windows as well as in Linux's full inventory. Integration tests keep the
`test_<module>.py` name so source changes select their own suite before the
local gate applies its unit-only boundary.

A worker, in CI or local validation, holds every test not marked
`@integration` to its own process and its own temporary directory. Each such
test gets a fresh directory inside the worker's as `tempfile`'s default, and a
process-wide audit hook watches every thread while it runs:
starting a process (subprocess, multiprocessing, `os.system`, fork, spawn,
exec) or writing outside that directory (any open for writing, SQLite files,
directory, link, rename, remove, mode, time and FIFO calls, through a
directory descriptor too) raises, is recorded and fails the test even when
the test catches the error, and so does a thread the test leaves running.
The innermost runner decides, so a runner under test keeps its own watch.
Class and module fixtures run outside it. Direct unittest invocations do not
install the CI worker boundary.

CLI rule matrices use `tools/tests/python_entry.py` to execute the actual Python
entry point in the worker, with isolated argv, import roots, environment, binary
streams and temporary-directory selection. The helper restores that state even
on an exception and keeps the unit boundary active. It does not emulate process
launch, timeout, interpreter encoding, signals or operating-system behavior.
Explicit `@integration` acceptance and refusal smokes retain those boundaries
for the compilers, config writer and hooks. Unit fixtures can supply narrowly
declared Git transport responses; approval, hashes, graph resolution, grant
decisions and file writes still run through production code. Real Git history,
worktree routing, package refresh, races and rollback remain CI integration
tests. Setup preflight rules have minimal input fixtures alongside the real
setup lifecycle checks. No test is omitted from the remote full suite.

A worker, in CI or local validation, runs its tests with tripwire `claude`
and `codex` binaries in place of the host binaries a session names:
`CLAUDE_CODE_EXECPATH`, `CODEX_CLI_PATH`, `CODEX_VERSION` and `CODEX_HOME`
point at them and `CLAUDE_PID` is removed. A project generator or
`host_models.py` that a test runs with the runner's own environment therefore
reaches a tripwire, never the developer's Claude Code or Codex, its MCP
servers or its account's model cache. A tripwire records the call and fails;
the worker fails the test during which it ran, or the shard when a class or
module fixture ran it after the last test. A test pins fakes of its own with
`fixtures.isolated_hosts`. Direct unittest invocations need their own isolated host fixtures.
Every CI worker process also gets its own `TMPDIR`, `TMP` and `TEMP` under the
runner's work directory, outside any Git checkout; on Windows that directory is
on the work disk, which creates small files several times faster than the
system disk that holds the default `TEMP`.

Setup and project-vault tests may copy an immutable, process-local project
prepared by one setup apply. Each test receives independent files and Git
objects, and fixture construction is invalidated when relevant setup functions
or environment inputs change. Real worktree, ref, refresh and rollback tests
retain their operating-system and Git boundaries.

| Profile | Selection |
| --- | --- |
| `full` | Every test on Linux; on macOS the platform and macOS groups; on Windows the platform and Windows groups. Every PR, manual and scheduled run, and main without reusable proof |
| `reuse` | Main's prior successful validation of the identical input, its PR's full run, with fresh static and transition checks |

Every pull request runs `full`: the local gate runs only a change's own tests,
so the full suite runs before every merge.

Fresh reports contain per-test duration, outcome and actual Python, Git and
operating-system identities. A plan weighs each test by the policy's
`test_seconds` for its runner system: the tests that took at least five
seconds, refreshed from a durations file with
`python3 tools/ci_tests.py estimates --durations <file>`. Every other test
weighs `default_seconds`. Fixture startup estimates favor reusing a
process-local seed without creating empty partitions. Reports distinguish test
time, fixture build/validation/copy time, worker wall time and shard wall time.
Timing data changes partition balance only, never coverage. Weekly and
manually dispatched validation run full coverage. `make check` runs the static
checks and the change's own unit tests; `make static-check` provides the cheap always-fresh
contract, version, count and deterministic distribution checks.

## Exact local validation

After generating distributions, stage the complete candidate and run:

```text
make check-local
make verify-local
```

The direct interfaces are `python3 tools/ci_local.py check --staged --target
origin/main` and `python3 tools/ci_local.py verify --staged --target origin/main`.
`check --fresh` ignores saved test results. The gate runs unit tests only:
a test marked `@integration` (`tools/tests/levels.py`), alone or through its
class, starts a process or writes outside its own temporary directory, and
only pull request CI runs it. The gate runs the change's own unit tests,
most specific first, while pull request CI runs every test on Linux:
1. the changed test methods of each changed test module, or the whole module
   when code outside its test methods changed (blank lines aside);
2. the test methods whose source names a changed non-Python input (by file
   name, or by folder and name when the name repeats), or the whole module
   when it names the input outside its test methods;
3. in the test module named after each changed Python module
   (`test_<module>.py`), the tests that name a changed function or class;
4. the test modules that import a changed test helper;
5. the rest of each such own test module.

Generated `dist/` copies select nothing of their own; the distribution sync
check and their canonical sources cover them. `budget_estimated_seconds` bounds
the selection by the full-suite lane's per-test estimates, in that order: a
test that would pass the budget is left to pull request CI, and the selection
reason counts those tests and the integration tests left to CI. A change whose
own tests are all integration tests runs only the static checks locally. 120 estimated seconds run in about 35 seconds with
four workers on a recent Mac, static checks included, which run beside the
test workers.
`check --full` runs every unit test; `verify` then checks that full receipt. On macOS, `direct_tools`
names the tools whose `/usr/bin` entry is an `xcrun` trampoline, which resolves
the developer directory on every call; workers call the tool it resolves to,
which in measured runs cut a Git call from about 9 ms to 3 ms. When a worker
fails, the gate's last lines name each failing test with the last line of its
traceback, and it keeps that run's worker logs and reports in
`.agentrof/agent-marketplace/.runtime/ci-local/last-failure/` until the next
run. `--jobs` selects one to four separate
processes, bounded by CPU count; policy defaults to four. The processes are
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
`make check` includes the staged local gate. Full-suite execution remains in
pull request CI. Local validation installs no Git hooks.

The local driver compares every tracked worktree byte and executable mode with
the index, using NUL-safe Git records. Partial staging, unresolved merges,
assume-unchanged/skip-worktree flags, unsupported index entries (including
tracked symlinks whose external targets cannot be bound) and untracked
source files fail before tests. Ignored runtime caches are explicitly allowed
by `ci-local-policy.json`; other ignored untracked sources fail. On Windows,
executable bits remain the index's identity because the filesystem does not
provide POSIX executable modes. Checkouts transformed by text filters must
first match their staged bytes. The selection reads the entire merge-base to index
candidate, including earlier branch commits, deletion and both sides of a
rename. A missing base selects the full suite. The command never fetches refs.

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
partial or failed worker reports fail. Test-declared skips retain their recorded
outcome. Platform integration regressions run in the CI shards.
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
selects fresh full validation. Fork and scheduled runs still verify
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
installation coverage. Every manual and scheduled run, and every PR that
changes a package, host, install or release-hosts path, installs both real
hosts fresh on macOS with Python 3.14, Node 24 and the CLI versions
`tools/data/host-cli-versions.json` pins; no host result is reused. A PR that
changes none of them skips the install, and the required check still reports.

Both hosts' checkout and public-channel lifecycles verify installed file hashes
and executable modes against the candidate before and after refresh.
Codex's installation receipt supplies the cache path and must match the enabled
inventory's package identity and version. A marketplace source path cannot
satisfy that check.
Codex installation and upgrade checks require enabled entry skills from the
exact installed source. The same app-server process lists configured hooks
and compares their source, event, command, timeout and normalized hash with
the installed package. Untrusted hook metadata is accepted. This check proves
discovery, while the separate subprocess regression exercises hook-to-report
behavior with both hosts' payloads. Native dispatch and model compliance need
live host acceptance evidence.

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
   however late the pull request merges.
2. After merge, `main` runs fresh static gates and either verifies equivalent
   PR evidence, in about a minute, or executes full tests. A commit reuses
   PR evidence only when its tree equals the PR's tested merge. CodeQL
   continues to run on `main`.
3. A release dispatches the `Release` workflow for one `main` commit. The
   `Auto release` workflow does so on the push that merges a release commit,
   when `versions.json` names a version no release tag holds yet; `ship`
   does so by hand. Both use the dispatch response's `workflow_run_id` to
   identify the run. A missing or invalid ID stops with an unknown outcome
   that must be inspected before retrying. The `Release` workflow checks the commit's release state,
   including a version newer than every release tag or, with no tag at all,
   than the version of the commit `stable` points to, an ancestor of the
   release commit. It waits for that commit's own `main` validation and
   requires its success, and never runs the tests or the host lifecycles
   again. It then stages the version tag and `stable` with exact
   leases, installs both hosts from the real public `stable` channel and
   creates the immutable Release. Rollback, immutable Release reconciliation
   and clean-main completion keep their contracts.

Public-channel installation retries only the failing host. Every attempt
verifies the expected `stable` SHA before and after that host's lifecycle.
The dispatch response follows GitHub's versioned REST contract.
[GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

Workflow or policy changes invalidate prior receipts deliberately. Measure a
release from the merge to the immutable Release, and the pull request that
carries the release commit by its slowest shard, runner queue time included.
Timing targets are acceptance goals, not grounds to omit a failed or slow
gate.

Local unit workers also reject process starts during test-module loading and class/module fixtures, including attempts a fixture catches. Shared fixture seeds must be built in process and remain read-only; each unit test mutates only its own temporary copy.

Descriptor-relative `os.open` writes retain their directory argument in thread-local audit context; an unknown descriptor or an uncaptured relative native open is refused. Nested loaders and class/module fixtures inherit an active ancestor test's filesystem boundary until their own test starts. Caught outside-write refusals remain attached to that active test.
