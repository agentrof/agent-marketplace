# Develop Flow

State-machine procedure for delivering ONE work package (or one atomic
change) end to end. Loaded and executed by entry skills; the spec it must
match lives in the repository's orchestration document.

## Critical behavioral rules

You MUST follow these rules exactly. Violating any of them is a failure.

1. Execute steps in the declared order. Do NOT skip, reorder or merge.
2. State and artifacts are the source of truth. Read prior steps from
   FILES, never from conversation memory. After any compaction, re-read
   state.json and the relevant artifacts before acting.
3. Stop at every GATE and CHECKPOINT and wait for explicit user approval.
   Offer exactly: Approve / Request changes (revise, then re-gate) /
   Pause (save state and stop).
4. Halt on failure: present the error and ask how to proceed. Never
   continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode. This flow IS the plan.

## State contract

Run directory: workspace/runs/<yyyymmdd>-<kebab-slug>/ (gitignored).
Contents: state.json, constitution.md (copied at init), and transient
review.md / qa.md finding records. Nothing else is ever written here.

state.json has ONE writer: you, the main conversation. Rewrite it before
a step starts (status in_progress) and after it ends (done). Keys, all
snake_case: run_id, request, status, current_step, steps (per step:
status, artifact, attempts), gates (decision, decided_at), iterations
(review, qa), bindings (role to skill), ownership (role to paths; keys
are snake_case role names such as backend_developer, never agent file
names), created_at, updated_at.

Suite artifacts (junit output and the like) are written to gitignored
workspace/ paths (workspace/junit-<suite>.xml), never into the run
directory.

Step status: pending | in_progress | done | blocked | escalated.
Run status: running | waiting_gate | blocked | escalated | complete.
Transition guard: a step starts only when its predecessor is done.

Single-active-run lock: an existing state.json with status running or
waiting_gate IS the lock. On invocation, offer Resume (continue from the
first non-done step) or Archive and start fresh. Never run two develop
flows concurrently in one repository.

## Spawn prompt template

Every agent spawn assembles, in this order:

1. Identity: "You are <agent-name>, executing step <n> of run <run_id>.
   Run directory: <path>."
2. The constitution body, pasted verbatim:

   {{constitution}}

3. Inputs: an explicit file list, split into read-fully (this step's
   declared inputs) and summary-only (other prior artifacts).
4. Skill binding: the knowledge skill(s) bound to this role in
   state.json bindings, read from workspace/config.json.
5. The task, with its acceptance criteria, each carrying a verify line.
6. Output: the exact artifact path(s), the requirement to end with
   SELF-CHECK, write nothing else, and never touch state.json.

Parallel dispatch: independent spawns go out as multiple Task calls in a
single message; consume their artifacts from disk afterwards.

## Mechanical post-step check

After every spawned step, BEFORE advancing state: the expected artifact
exists at its exact path, is non-empty, and carries its mandated
sections. On violation, re-spawn the SAME step exactly once with the
violation named ("your prior attempt produced no artifact at PATH; write
it now"). On a second failure, set the step blocked, write state, and
halt with the reason and resume instructions.

## Steps

### Step 0: init

- Resolve the project git root; anchor everything there. No git
  repository: stop and offer to initialize one.
- Read workspace/config.json. Missing: stop and route the user to the
  setup entry. Unsupported stack values: refuse honestly and stop.
- Create the run directory; copy the plugin's constitution.md into it;
  write the initial state.json with bindings from config (backend role to
  the backend stack skill, frontend role to the frontend stack skill,
  architect to the architecture skill plus every database skill in the
  databases set, planner to the planning skill, analyst to the
  requirements-analysis skill, reviewer to the review skill, verifier to
  the verification skill).
- Ensure workspace/runs/ is gitignored; append the rule when missing.
- Create the work branch for this package from the main line, named
  wp-<nn>-<kebab-slug> (atomic route: atomic-<kebab-slug>).

### Step 1: architecture delta

- Spawn software-team-software-architect with the approved brief, the
  living documents under workspace/docs/system-architecture/, and this
  package's scope from the backlog.
- The architect applies its delta to the living documents and returns
  the ownership map; store the map in state.json.

### GATE: model and contract

- Present the architect's delta summary in conversation: changed and new
  sections, the breaking-change flag, and any denormalization decisions
  with their recorded rationale. The full record is the git diff.
- Gate criteria: every endpoint in scope has complete error cases; the
  ownership map has no overlaps. The gate cannot pass otherwise.
- Approve / Request changes / Pause.

### Step 2: implementation (parallel)

- If this package's Definition of Ready requires a screen and no approved
  preview exists: run the design flow now (flows/design.md) and return.
- Spawn software-team-backend-developer and
  software-team-frontend-developer in one message, each bounded to its
  ownership paths, each with read-fully inputs: the architecture delta,
  the contract, the approved preview (frontend), the design master
  (frontend). Packages without client or server work spawn only the
  relevant developer.
- Ownership overlap discovered mid-flight: serialize (backend first),
  note it in state.
- Post-step check: the configured test command passes per developer's
  SELF-CHECK; code changes exist only inside ownership paths.

### Step 3: review loop (max 3 rounds)

- Spawn software-team-code-reviewer with the diff scope, the living
  architecture documents, and the transient record path
  <run>/review.md.
- Verdict approve: continue. Verdict fix_required (issued only when a
  critical or major finding is open; minors never spin the loop): route
  each finding to the developer owning its file; the reviewer then
  re-checks only the fixes on the same evolving record. Increment
  iterations.review.
- A finding that implicates the approved architecture does NOT enter the
  fix loop: set escalated, present it, and stop for the owner's decision
  (record the decision retroactively, or revise the delta through a mini
  model gate, then resume here).
- iterations.review reaching 3 without approve: blocked, escalate, halt.

### Step 4: verification loop (max 3 rounds)

- Spawn software-team-qa-engineer with the brief's criteria, the record
  path <run>/qa.md, and the configured commands.
- Pass: continue. Fail: route findings to the owning developer exactly as
  in review; increment iterations.qa; re-verify only what changed.
- Requirement gaps escalate to the owner; they are never patched
  silently. iterations.qa reaching 3 without pass: blocked, escalate,
  halt.

### GATE: delivery

- Present the package summary: what was built, review verdict and rounds,
  verification results, minors carried as notes.
- Approve / Request changes / Pause.

### Step 5: finalize

- Commit on the work branch and open the pull request; its body carries
  the compact quality summary (review verdict and rounds, coverage
  matrix result, live verification result, minor notes).
- Backlog updates NEVER ride this branch. At the checkpoint after merge,
  update workspace/docs/backlog.md on the main line: mark the package
  done, reconcile ordering, then ask "continue with the next package?".
- Set the run complete only when every step's status is done and every
  gate carries decision and decided_at; a complete run with pending
  steps is a contract violation. The run directory keeps only
  state.json, the constitution copy and the transient records.

## Atomic route variant

For an atomic change (entry classified it): run step 0 as written; skip
step 1, both gates, and the review and verification loops; spawn only
the owning developer with the task and ownership bounded to the touched
files; finalize with a small pull request whose body states the route
(atomic), the diff summary and the test command result (the review and
verification fields of the quality summary do not apply).

UI-touching atomic work still draws every visual value from the design
master's tokens. No master in the project: a change that would introduce
a new visual value stops and routes to the design-system entry; changes
introducing no visual value (text, copy, an existing-token swap)
proceed.

Escape hatch: the moment the work touches the data model, the contract
or the schema, STOP, report "not atomic" with what was found, and hand
the request to the large route unchanged. Disposition: set the run
escalated and archive it, delete the abandoned atomic branch, and let
the large route start its own run.
