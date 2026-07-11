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
  route in one line. Atomic: owning dev agent, small diff, pull request on
  an atomic-<slug> branch; UI-touching atomic work still draws every
  visual value from the design master's tokens (no master: a change that
  would introduce a new visual value routes to the design-system entry).
  Escape hatch: work that turns out to touch model, contract or schema
  stops, reports "not atomic", sets the run escalated and archives it,
  deletes the atomic branch, and hands over to the large route, which
  starts its own run. Large: brief precondition, then product-owner
  produces the backlog, then packages run one by one through the develop
  flow (branch wp-<nn>-<slug>) with checkpoints between packages.
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
containing only state.json, the constitution copy, and transient
review/qa finding files. state.json has a single writer: the main
conversation. It is rewritten before a step starts (in_progress) and after
it finishes (done).

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

Single-active-run lock: a state.json with status running or waiting_gate
IS the lock; a second develop invocation must offer resume or archive,
never run concurrently.

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
for implementation steps). No sidecar reports, no memory writes, no
changelogs. Mechanical post-step check before state advances: the artifact
exists at the exact path and carries its mandated sections. On violation:
exactly one retry with the violation named in the prompt; a second failure
sets blocked and halts with resume instructions.

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
re-checked; maximum three rounds each, then blocked plus escalation.
Findings that implicate the approved architecture never enter the fix
loop; they escalate immediately. Severity policy: fix_required only for
critical and major findings; minors become pull request notes.

## Concurrency and git

One active run per repository. Backlog updates never ride feature
branches; they happen on the main line at the checkpoint after merge.
Architecture deltas deliberately ride the package branch so model and code
merge atomically. Each package ends in its own pull request; merging is a
human act.
