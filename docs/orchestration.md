# Orchestration spec

The contract the software-team flows implement. Flow files are reviewed
against this document; when they disagree, one of the two changes in the
same pull request.

## Surfaces and routes

Entry skills (user surface): request, sketch, demo, business-analysis,
design-system, setup, configure. Entries stay thin: parse input, run the
pre-flight, then either delegate to a flow file via the plugin root
variable (request, sketch, demo) or execute their own short interactive
procedure in the main conversation (business-analysis, design-system,
setup, configure). Internal flows: design, develop.

Routes:

- **request** classifies BINARY in the main conversation (the PO hat is
  the entry's own instruction text, never an agent spawn) and confirms the
  route in one line. Atomic has two tiers: cosmetic-atomic (no behavior
  change: owning dev agent, small diff, pull request on an atomic-<slug>
  branch) and fix-atomic (behavior change: failing reproduction test
  first, tagged to the violated or newly minted BR, then the fix, then
  one reviewer pass; the BR updates in the brief on main at the
  checkpoint). UI-touching atomic work still draws every visual value
  from the design master's tokens (no master: a change that would
  introduce a new visual value routes to the design-system entry). The
  escape hatch is mechanical and judgmental: the tripwire script scans
  the diff for model/contract/schema touches before finalize, and the
  moment such a touch is recognized in judgment the run stops, reports
  "not atomic", sets the run escalated and archives it, deletes the
  atomic branch, and hands over to the large route, which starts its own
  run. Large: brief precondition, then product-owner produces the
  backlog, then packages run one by one through the develop flow (branch
  wp-<nn>-<slug>) with a readiness gate before each package and
  checkpoints between packages.
- **sketch / demo** require a brief (business-analysis runs first when
  missing) and a design system MASTER (the design-system entry owns its
  creation; other flows only redirect).
- **business-analysis** and **design-system** are interactive main
  conversation flows with a closing approval gate each.
- **setup** is idempotent bootstrap; **configure** is the single change
  gate for the project config file.

## Critical behavioral rules (head of every flow)

1. Execute steps in the declared order; never skip, reorder or merge.
2. State and artifacts are the source of truth: read prior steps from
   files, never from conversation memory; after any compaction re-read
   state before acting.
3. Stop at every gate and wait for explicit user choice. Develop-flow
   gates offer Approve, Request changes (revise and re-gate), Pause
   (save and stop); design-flow gates offer their own choices (pick a
   direction or re-run with different emphases; approve the refined
   preview).
4. Halt on failure: present the error and ask; never continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode; the flow is the plan.

## State contract

One run directory per run under the workspace runs folder, gitignored,
containing only state.json, the constitution copy, the brief and config
snapshots init takes (the run reads the snapshots for its whole
duration), and transient review/qa finding files. state.json has a
single writer: the main conversation, writing ONLY through the plugin's
state_tool script (init, set-step, record-gate, bump, set-ownership,
set-run-status, validate, release-lock); the tool enforces the enums,
the transition guard, the run-complete guard and the timestamps.

Step status enum: pending, in_progress, done, blocked, escalated.
Run status enum: running, waiting_gate, blocked, escalated, complete.
Transition guard: a step may start only when its predecessor is done.

Keys (snake_case): run_id, request, status, current_step, steps (per-step
status, artifact, attempts), gates (decision, decided_at), iterations
(review, qa), bindings (role to skill: stack skills come from config,
method skills such as the architect's architecture skill, the planner's
planning skill and the analyst's requirements-analysis skill are static
and declared in the develop flow's init step), ownership (role to files;
keys are snake_case role names, never agent file names), created_at,
updated_at. A run may be set complete only when every step is done and
every gate carries decision and decided_at. Suite artifacts (junit
output) go to gitignored workspace paths, never into the run directory.

Single-active-run lock: state_tool init acquires an exclusive lock file
under the runs folder and refuses when another run holds it; a refused
init means resume the holder, never archive it blind. The lock releases
at finalize or pause. While a run is running or waiting_gate, the
business-analysis and configure entries refuse edits that would fork the
running spec.

## Spawn prompt contract

Every Task spawn assembles, in order:

1. Identity: role name, step, run id, run directory. Standalone design
   spawns (sketch, demo, design-system entries) have no run directory or
   state.json; identity names the flow and topic instead, and the
   constitution body is read from the plugin's constitution file.
2. The constitution body, pasted verbatim (placeholder: `{{constitution}}`).
3. Inputs: explicit file list, split into read-fully and summary-only.
4. Skill binding: the knowledge skill(s) bound to the role from config.
5. The task plus its acceptance criteria, each with a verify line.
6. Output: exact artifact path(s), the SELF-CHECK closing requirement,
   write-nothing-else, never touch state.json.

## Step output contract

A step emits exactly one artifact at its declared path (plus code changes
for implementation steps). No sidecar reports, no memory writes. The
mechanical post-step check runs the plugin's artifact_check script (path,
non-empty, mandated sections) before state advances. On violation:
exactly one retry with the violation named in the prompt; a second failure
sets blocked and halts with resume instructions. The model gate's
mechanical half runs the contract_check and ownership_check scripts
before the gate is presented.

## Gates and loops

Gate inventory scales with the route: classification confirm; brief
approval; DS gate (the design-system candidate pick); backlog gate;
model and contract gate (architect delta shown in conversation with a
breaking-change flag; the record is the git diff); direction pick and
handshake (two distinct design-flow gates); delivery gate;
inter-package checkpoints; the pull request itself. Every gate is a
manual stop; there is no auto mode in v1.

Fixed output paths: design-system candidates preview at
workspace/docs/design-system/candidates.html (deleted after MASTER is
written); sketch and in-package previews at
workspace/sketches/<slug>/preview.html; demo packages at
workspace/demos/<slug>/demo.html.

Review and QA run sequentially after implementation, each a bounded loop:
findings route to the owning developer by file; only the fix is
re-checked (a new critical or major on untouched, already-passed lines
at re-review must cite what changed); maximum three rounds each, then
blocked plus escalation. Findings that implicate the approved
architecture never enter the fix loop; they escalate immediately, and
the owner may overrule by recording an owner-decision entry in the
decision log. Severity policy: fix_required only for critical and major
findings; minors become pull request notes. QA's gates include the
coverage matrix, the mandatory mutation gate scoped to the package's
changed files, the budget-verification table (verified or honestly
unverified per quantified budget) and the live protocol; screenful
packages add a read-only design verification by the designer against
the approved preview.

At the merge checkpoint on the main line: backlog reconciliation, the
changelog append from the PR quality summary, the published interface
schema, the quality-ledger line (finding categories, severity counts,
rounds-to-green, escaped-defect marker), brief BR updates from fix-atomic
work, the deployed_verified field once the owner confirms the package
runs in its target environment, and every tenth checkpoint the
architecture reconciliation (living docs vs code; page overrides vs the
design master).

## Concurrency and git

One active run per repository. Backlog updates never ride feature
branches; they happen on the main line at the checkpoint after merge.
Architecture deltas deliberately ride the package branch so model and code
merge atomically. Each package ends in its own pull request; merging is a
human act.
