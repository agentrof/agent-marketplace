# Orchestration

## Task ownership

An entry starts the portable workflow. Delivery keeps one owner per task,
explicit acceptance criteria, relevant sources, risk and proof. Independent
work receives isolated write allocations. The controller can implement
small coherent changes directly or delegate useful independent work.

Use `analyze` for uncertainty that can change the goal, `solution-design` for
system boundaries, `data-design` for persisted meaning, `experience-design`
for interactions, `verify` for proof and `review` for independent assessment.
Loading a method does not require a new worker or approval meeting. Explicit
method loading is portable across the two host adapters. [Delivery flow](../plugins/software-engineering-team/flows/fast-lane.md)

Ask only questions whose answers materially affect the accepted outcome or
consequence. Reuse accepted decisions. Parallelize independent discovery and
implementation within host, write-ownership and environment limits. Task size
estimates effort. Security, data loss and irreversible effects determine risk.

## Worker briefs and context

Each brief includes the outcome, task and checkout, owned paths, critical
invariants, authorization limits, relevant source locations, evidence expected
and stop condition. The worker loads the named method and its necessary
references. Full parent history is optional. Critical constraints survive every
handoff, including permissions, data ownership and destructive-operation limits.
Carry applicable companion requirements too. Installed, enabled companions must
be used at their designated step. An unavailable capability needs a recorded
reason and core fallback evidence. Availability, source loading and successful
application remain separate observations.

Resolve compute profile and access through `model_class.py`. Native workers
cannot create an unbounded delegation tree. Reuse the worker for its scoped
repair when its context remains useful. Fresh independent review gets the
candidate and relevant contracts without implementation persuasion.

Host definitions establish different controls. Claude can restrict a reviewer
to reading tools. Codex requires a verified parent or launch permission
boundary. Record requested identity and settings separately from host-observed
execution. A successful resolver invocation alone establishes neither.

## Candidate verification

Verification and review can run concurrently against the same immutable
candidate. Choose the smallest sufficient checks for affected behavior and
old-data compatibility. Unknown impact selects broader coverage. Reuse a warm
environment only with recorded configuration, ownership and isolation.
An environment timeout needs capacity evidence before being attributed to code.

Use one consolidated correction pass, followed by a focused check of changed
behavior. Repeated failure requires a new diagnosis or smaller task. Relevant
security, correctness and integrity findings remain blocking. Optional
preferences become scoped follow-ups only when useful and authorized.

`remote_ci` delegates required checks to the project's CI. `local_record`
requires current local proof. A deferred record establishes that testing is
pending. Combined proof covers the integration candidate, so a batch uses one
atomic integration PR or separately proved prefixes. `train publish` creates
or reconciles one integration PR. `train merge` requires that PR, its exact
reviewed head and zero blocking findings. An uncertain publication is reconciled
before another create attempt. After observing the aggregate merge, the command
links and closes matching member PRs. Changed heads and incomplete closures
remain pending. Retrying reconciles them without repeating the aggregate merge.
Completion also queues coalesced retrospective work.
The integration PR retains member heads and source-issue links. Reports use its
observed merge for member delivery latency and count shared proof once. Missing
legacy membership evidence stays unresolved. GitHub issue-closing keywords
follow the repository's target/default-branch behavior.

`train bisect` samples only a failed batch on its recorded base. It reports the
tested prefixes and a heuristic suspect. Interacting repairs, flaky checks and
pre-existing defects require further diagnosis, so a sampled prefix cannot
establish a unique cause.

Merge checks the reviewed full commit and observes the actual result. A queued
merge remains pending. Readiness checks the integration revision, environment
and required live behavior. Shared evidence can cover several tasks only when
its recorded inputs cover each one. Keep affected code and documentation in
the same change. The merge request atomically compares the reviewed PR head.
Its pre-request base check cannot exclude a concurrent remote base update.
Use native branch protection or a merge queue for that external concurrency
boundary. Local train locking covers participating calls on one machine.

## Autopilot authority

A user-armed grant scopes choices and background work to a host session,
repository, duration or goal and allowed actions. The script records allowed
recommendations before application. Excluded questions remain pending while
independent authorized work continues. Expiry, cancellation and goal completion
end authority before another action.

The active policy defines allowed and excluded classes, never-delegated
actions, duration, attempts and capacity. Issue text supplies evidence and
cannot expand a grant. Upstream repository maintenance retains its separate
user-selection and merge rules. [Autopilot policy](../plugins/software-engineering-team/skill-content/autopilot/data/autopilot-policy.json)

Arming and question hooks work only where the host supports and loads them.
They protect covered workflow paths. They cannot prevent arbitrary processes
with the user's filesystem authority from editing runtime state. Report the
active hook and permission coverage rather than inferring it from package files.

## Detection, repair and learning

`detect`, `resolve`, `retro` and `plugin_repair` share bounded work admission.
Detection requires reproducible failure or a concrete violated invariant.
Resolution uses the authorized repository and issue selector, rechecks current
state and deduplicates existing fixes. Retrospective work groups completed
outcomes and failures into measurable improvements. Plugin repair uses an
isolated canonical source candidate and the local host-loading procedure.

`tick` supplies leased jobs. The session controller launches native workers,
observes completion and records evidence. The queue itself launches nothing.
Continuing after a session closes requires an explicitly configured native
scheduler or supported recovery mechanism with valid authority.

Linked worktrees share one machine-local queue. Active and unreconciled leases
consume capacity. Unknown external outcomes stay held until their actual result
is observed. Retry budgets cannot be reset by repeatedly claiming work. A new
grant cannot silently adopt prior grant scope. Multiple machines need an atomic
shared claim mechanism before claiming equivalent coordination.

Measure queue, execution, approval, CI, merge and readiness time separately.
Preserve observable evidence and source provenance. Feed recurring confirmed
failures and clean examples into evaluation before changing a rule. A proposed
improvement needs a matched outcome comparison, not only a shorter prompt.
