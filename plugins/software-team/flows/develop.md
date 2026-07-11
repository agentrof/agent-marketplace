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

state.json has ONE writer: you, the main conversation, and you write it
ONLY through ${CLAUDE_PLUGIN_ROOT}/scripts/state_tool.py (init, set-step,
record-gate, bump, set-ownership, set-run-status, validate,
release-lock). The tool enforces the enums, the transition guard, the
run-complete guard and the timestamps; hand-editing state.json is a
contract violation. Keys, all snake_case: run_id, request, status,
current_step, steps (per step: status, artifact, attempts), gates
(decision, decided_at), iterations (review, qa), bindings (role to
skill), ownership (role to paths; keys are snake_case role names such as
backend_developer, never agent file names), created_at, updated_at.

The run reads the SNAPSHOTS init copied into the run directory
(brief.snapshot.md, config.snapshot.json) for its whole duration; a
brief or config edited mid-run does not change a running package.

Suite artifacts (junit output and the like) are written to gitignored
workspace/ paths (workspace/junit-<suite>.xml), never into the run
directory.

Step status: pending | in_progress | done | blocked | escalated.
Run status: running | waiting_gate | blocked | escalated | complete.
Transition guard: a step starts only when its predecessor is done.

Single-active-run lock: state_tool init acquires workspace/runs/.lock
exclusively and REFUSES when another run holds it; a refused init means
resume the holder, never archive it blind. The lock is released by
state_tool release-lock at finalize or pause. Never run two develop flows
concurrently in one repository.

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

After every spawned step, BEFORE advancing state, run
${CLAUDE_PLUGIN_ROOT}/scripts/artifact_check.py --path <artifact>
--require-sections "<the artifact's mandated sections>" (data model:
"Summary, Entities"; contract: "Summary, Error cases"; brief: "Purpose
and Scope, Business Rules, Acceptance Criteria"; records: their format's
sections). Exit 1 is the violation: re-spawn the SAME step exactly once
with the violation named ("your prior attempt produced no artifact at
PATH / is missing SECTIONS; write it now"). On a second failure, set the
step blocked, write state, and halt with the reason and resume
instructions. The script proves structure; you still read the artifact
for semantic sanity before presenting any gate.

## Steps

### Step 0: init

- Resolve the project git root; anchor everything there. No git
  repository: stop and offer to initialize one.
- Read workspace/config.json. Missing: stop and route the user to the
  setup entry. Unsupported stack values: refuse honestly and stop.
- Initialize the run with ${CLAUDE_PLUGIN_ROOT}/scripts/state_tool.py
  init --workspace workspace --run-id <id> --request "<request>"
  --constitution ${CLAUDE_PLUGIN_ROOT}/constitution.md --brief <brief
  path> --bindings '<json>'. It creates the run directory, copies the
  constitution, snapshots the brief and config, acquires the lock and
  writes the state skeleton. Bindings from config: backend role to the
  backend stack skill, frontend role to the frontend stack skill,
  architect to the architecture skill plus every database skill in the
  databases set, planner to the planning skill, analyst to the
  requirements-analysis skill, reviewer to the review skill, verifier to
  the verification skill.
- Ensure workspace/runs/ is gitignored; append the rule when missing.
- Create the work branch for this package from the main line, named
  wp-<nn>-<kebab-slug> (atomic route: atomic-<kebab-slug>).

### Step 0.5: readiness gate

- Check every item of this package's Definition of Ready from the
  backlog against reality (dependencies actually merged, criteria
  unambiguous, preview present when required). Any item failing: the
  package is NOT ready; return it to the product owner with the failing
  items named, note it in the backlog on the main line, set the run
  status blocked via state_tool and release the lock. Never start
  implementation on an unready package.

### Step 1: architecture delta

- Spawn software-team-software-architect with the snapshotted brief, the
  living documents under workspace/docs/system-architecture/, and this
  package's scope from the backlog.
- The architect applies its delta to the living documents and returns
  the ownership map; store the map via state_tool set-ownership.

### GATE: model and contract

- Present the architect's delta summary in conversation: changed and new
  sections, the breaking-change flag, and any denormalization decisions
  with their recorded rationale. The full record is the git diff.
- Mechanical half, run BEFORE presenting the gate:
  ${CLAUDE_PLUGIN_ROOT}/scripts/contract_check.py --contract
  workspace/docs/system-architecture/api-contract.md (every endpoint
  declares error cases) and ${CLAUDE_PLUGIN_ROOT}/scripts/
  ownership_check.py --state <run>/state.json (no overlapping paths).
  Either exiting nonzero blocks the gate; route the output back to the
  architect as the named violation.
- Judgment half at the gate: error-case completeness in substance,
  boundary sanity, budget citations. The gate cannot pass otherwise.
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
- Re-slice branch: when a developer reports the scope is larger than the
  package (new entities, endpoints or screens the backlog never sliced),
  halt the step, present the discovery, and route to the product owner
  for a re-slice; the backlog delta passes a mini backlog gate, then
  resume this package with its corrected scope or abort it in favor of
  the new slices. Never absorb discovered scope silently.
- Post-step check: the configured test command passes per developer's
  SELF-CHECK; code changes exist only inside ownership paths; on
  server-touching packages the exported interface schema exists and the
  client-shape check in the suite ran against it (contract drift is a
  red suite, not an opinion).

### Step 3: review loop (max 3 rounds)

- Spawn software-team-code-reviewer with the diff scope, the living
  architecture documents, and the transient record path
  <run>/review.md.
- Verdict approve: continue. Verdict fix_required (issued only when a
  critical or major finding is open; minors never spin the loop): route
  each finding to the developer owning its file; the reviewer then
  re-checks only the fixes on the same evolving record. Increment
  iterations.review.
- Churn guard: at re-review, a NEW critical or major finding on
  untouched, already-passed lines is accepted only when the reviewer
  cites what changed to justify it; otherwise reject the finding and
  keep the round scoped to the fixes.
- A finding that implicates the approved architecture does NOT enter the
  fix loop: set escalated, present it, and stop for the owner's decision.
  Exits: the owner records the decision retroactively (an owner-decision
  entry in the decision log, supersede mechanics) and the loop resumes;
  or the architect revises the delta through a mini model gate, then
  resume here.
- iterations.review reaching 3 without approve: blocked, escalate, halt.

### Step 4: verification loop (max 3 rounds)

- Spawn software-team-qa-engineer with the brief's criteria, the record
  path <run>/qa.md, and the configured commands (test_command and
  mutation_command).
- The mutation gate is mandatory on code packages: QA runs the
  configured mutation command scoped to this package's changed files;
  every surviving mutant in changed lines is a finding (major on a
  BR/AC-tagged path, minor otherwise); a missing mutation_command on a
  code package is itself a blocking finding.
- Pass: continue. Fail: route findings to the owning developer exactly as
  in review; increment iterations.qa; re-verify only what changed.
- Requirement gaps escalate to the owner; they are never patched
  silently. iterations.qa reaching 3 without pass: blocked, escalate,
  halt.

### Step 4.5: design verification (screenful packages only)

- After QA passes, spawn software-team-ux-designer READ-ONLY with the
  approved preview, the design master and the built screens (run the app
  via the configured command): it re-judges the realization with its
  pre-delivery checklist and self-critique (contrast, spacing rhythm,
  motion character, hierarchy, token fidelity) against the spec it
  authored. Findings route like review findings (token drift to the
  frontend developer; spec ambiguity escalates); one fix round, then
  re-judge once.

### GATE: delivery

- Present the package summary: what was built, review verdict and rounds,
  verification results, minors carried as notes.
- Approve / Request changes / Pause.

### Step 5: finalize

- Commit on the work branch and open the pull request; its body carries
  the compact quality summary (review verdict and rounds, coverage
  matrix result, mutation result, live verification result, minor notes)
  and, when the package authored migrations, the migration notes:
  which migrations, their order, the safe-to-run-twice statement and the
  rollback note. The handoff is complete or the package is not done.
- Backlog updates NEVER ride this branch. At the checkpoint after merge,
  on the main line:
  - update workspace/docs/backlog.md: mark the package done, reconcile
    ordering;
  - append the package's line to workspace/CHANGELOG.md from the PR
    quality summary (append-only);
  - publish the exported interface schema under workspace/docs/api/;
  - append the package's line to workspace/docs/quality-ledger.md:
    finding categories, counts by severity, review and qa
    rounds-to-green, and the escaped-defect marker when a fix-atomic
    traced back to this package;
  - update the brief's BR-### entries changed by fix-atomic work since
    the last checkpoint;
  - record deployed_verified in the backlog's checkpoint fields when the
    owner confirms the merged package runs in its target environment
    (merged is not working-in-the-world);
  - every tenth checkpoint, or on demand, run the architecture
    reconciliation: an architect audit of the living documents against
    the code as implemented, and of page overrides against the design
    master (stable overrides fold back in; contradictions become
    findings);
  then ask "continue with the next package?".
- Set the run complete only when every step's status is done and every
  gate carries decision and decided_at; a complete run with pending
  steps is a contract violation. The run directory keeps only
  state.json, the constitution copy and the transient records.

## Atomic route variant

Atomic work has two tiers; the entry names the tier at classification:

COSMETIC-ATOMIC (no behavior change: copy, a label, an existing-token
swap): run step 0 as written; skip step 1, both gates, and the review
and verification loops; spawn only the owning developer with the task
and ownership bounded to the touched files; finalize with a small pull
request whose body states the route (cosmetic-atomic), the diff summary
and the test command result.

FIX-ATOMIC (any behavior change: a bug fix, a rule correction): run step
0 as written, then, in order:
1. Reproduction first: the owning developer writes a FAILING test that
   reproduces the defect, tagged to the violated BR-### (or a newly
   minted id when the behavior was never specified), before touching the
   fix. The test is permanent regression-suite growth.
2. Fix until the reproduction test and the whole suite are green.
3. ONE reviewer pass (single round): findings routed once to the
   developer, re-checked once; an architecture-implicating finding
   escalates as in the large route.
4. Finalize with a small pull request whose body states the route
   (fix-atomic), the reproduction test name, the diff summary and the
   test command result.
5. At the merge checkpoint ON MAIN, update or add the BR-### in the
   brief when the fix changed or defined specified behavior (the
   backlog-main rule extended to the brief), and mark the ledger entry
   as an escaped defect when the fix traces to a prior package.

Both tiers: UI-touching atomic work still draws every visual value from
the design master's tokens. No master in the project: a change that
would introduce a new visual value stops and routes to the design-system
entry; changes introducing no visual value proceed.

Tripwire, BEFORE finalize on both tiers: run
${CLAUDE_PLUGIN_ROOT}/scripts/atomic_tripwire.py --repo . --range
main...HEAD. Exit 1 proves the work was never atomic: STOP, report "not
atomic" with the flagged files, and hand the request to the large route
unchanged. Disposition: set the run escalated and archive it, delete the
abandoned atomic branch (state_tool release-lock), and let the large
route start its own run. The judgment-level escape hatch stands
independently: the moment the work touches the data model, the contract
or the schema, stop without waiting for the tripwire.
