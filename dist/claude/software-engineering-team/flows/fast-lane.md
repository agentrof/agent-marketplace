# Delivery flow

## Outcome

An authorized request becomes coherent tasks, independently reviewed changes and
verified readiness in the intended environment. Time targets include request
arrival, queueing, approval, CI, merge and readiness waits. The current task
clock starts at registration after its lock is acquired. Earlier request and
queue time remains unknown unless separately observed. Report this measurement
boundary when comparing recorded durations with the end-to-end target.

## 1. Frame and allocate

Read the [working contract](../constitution.md), applicable project instructions and current decisions.
For each deliverable record the outcome, acceptance criteria, dependencies, risks,
sources and proof. Keep related code and documentation together. Size estimates
cost. Data, security and irreversible effects determine mandatory checks.

Use `task_lane.py start --title <title> --request <request> --size s|m|l`
with explicit `--risk` flags when the effect is confirmed. Discovery suggestions
need judgment. The task receives its own branch and worktree. Do not switch a
shared checkout to run another task. Fill every meaningful criterion and its
verification method, then run `approve --task <id>` under the existing authority.
Keep newly discovered effects in Request or Acceptance Criteria and update
`required_methods` and `risks` when applicable. Design and proof notes alone do
not update the task contract used for method discovery.

## 2. Load only the methods needed

Use `task_lane.py method --task <id> --name <method>` to load and record a method.
After applying it, record the actual project evidence with the same command and
`--evidence <project-relative-file>`. Loading and a worker's completion report are
separate observations. A reviewer checks whether the evidence satisfies the goal.

Use analyze for consequential uncertainty, solution-design for system boundaries,
data-design for persisted meaning, experience-design for changed interaction,
verify for proof selection and review for the independent candidate review.
A method is not a mandatory new agent or approval meeting.

Use installed, enabled companions at the step they improve. This routing is
mandatory for applicable work and follows the task's actual effects.

| Companion | Required use |
| --- | --- |
| Ponytail | Scope, implementation and review of code or design changes |
| Graphify | Source discovery for code changes using an existing graph |
| Impeccable | User-facing content and interaction, UI bugfixes and visual verification |
| security-audit | Confirmed risks, high-rigor work and changed system boundaries |

Load an applicable companion once per working context with
`companion --task <id> --name <id> --host <host> --source <local-SKILL.md>`.
Apply the returned operation-specific procedure. Selected source sections keep
their exact text and provenance. Read deeper references when the operation
requires them. For Graphify, omit `--source` and pass `--graph <graph.json>
--query <question>`. The host's companion configuration controls implicit loading
of the long native entry. This command supplies the installed query procedure.
The command records the real process result and returns a bounded preview with
an explicit truncation marker and the complete output path. Source access,
command execution and a worker's application report remain separate evidence.
Repeat with `--evidence <project-file>` to record application evidence.
For an unavailable, disabled or failed capability, use `--unavailable <reason>`
with `--evidence <core-fallback-file>` instead of `--source`. These availability
and fallback declarations are worker reports. A host catalog can establish
enabled availability. A readable file alone establishes only source presence.
`review-inputs` reports the required companions and their evidence gaps.
PR and merge gates recheck the actual change and current evidence. Renew an
affected companion's evidence after the candidate changes, using the already
loaded source when its content is unchanged.

A missing graph uses focused source discovery. Creating a new graph follows
project authorization and stays outside unrelated proof. Use security-audit in
focused guidance mode for ordinary risk review. Its full audit workflow needs
explicit scope. Companion instructions inherit the task's authority, write
allocation and correction budget.

## 3. Implement and parallelize

The task owner may implement directly. Delegate independent work through the host
contract. Resolve the light, work, review or critical compute profile with
`model_class.py resolve --profile <profile> --access read|write --host <host>`.
Use its exact launch identity. Read its capability limitations before claiming
isolation. Record host-observed settings when exposed, otherwise mark unverified.

Every child brief contains the outcome, task/worktree, write ownership, critical
invariants, authorization limits, relevant procedures, proof and stopping
condition. The review launcher supplies a bounded procedure packet directly to
its child. Each required procedure needs a concrete candidate-specific assessment
with source citations. A parent's earlier receipt cannot satisfy this review.
Do not copy the whole parent conversation. Reuse a worker for its scoped repair.
Never allocate more active workers than independent ready work or host capacity.

## 4. Prove and review the candidate

Run the smallest sufficient proof through the project's declared commands. Use a
warm environment and explicit resource ownership. Share a batch proof only for
its recorded input tree and compatibility boundary. Full-batch green alone does
not prove every intermediate merge prefix. Required CI retains its authority.

Run `review-inputs --task <id>` to inspect gaps, then
`review --task <id> --host <host>` while proof executes. Its independent native CLI
needs model API and host-session access. Obtain missing access through the host's
normal per-command permission flow before launch. Reviewer tools remain read-only.
The launcher supplies scoped procedures and binds the result to this candidate.
For an atomic train candidate, use
`review --batch <number> --host <host>` against its integration worktree.
Use one consolidated correction pass, then a focused recheck of changed behavior.
A second failed repair requires a new diagnosis or smaller task. Keep blocking
security, correctness and integrity findings open until resolved. Cosmetic
preferences do not block delivery. File useful follow-ups only under publication
authority and deduplicate them.

## 5. Merge and observe readiness

Open the task PR with `open-pr --task <id>`. Merge only after proof and review of
its full current commit using `merge --task <id> --pr <number> --head <sha>
--critical 0`. High-risk work needs explicit merge delegation or owner action.
The command checks required CI or the configured local evidence, binds the merge
to the launcher's current review record and observes the result. Caller-supplied
finding counts alone cannot establish review. A queued merge remains pending.

Observe the actual integration revision, configuration and required live behavior.
Record the milestone with `task_lane.py ready --task <id> --pr <number>
--revision <deployed-commit> --environment <name> --evidence <project-file>`.
The receipt distinguishes worker-reported runtime evidence from verified merge
ancestry. Report ready only with that evidence. A shared readiness check may cover several
tasks when its revision includes them. Completion and failed commands trigger a
bounded local retrospective observer. Native session hooks refresh late usage.
The observer groups events and records timing, usage, procedure delivery and gaps
without waiting for analysis on the delivery path. Read its saved report through
`retrospective.py report --project <root> --json`. Autopilot owns authorized deeper
analysis, remediation and native-worker dispatch. No issue text authorizes new work.
