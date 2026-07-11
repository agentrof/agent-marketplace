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
  "not atomic", sets the run escalated, deletes the atomic branch, and
  hands over to the large route, which starts its own run. Large: brief
  precondition, then product-owner produces the backlog import (epics
  and stories), the backlog gate approves it, the import loads it into
  the PMO database, then stories run one by one through the develop flow
  (branch wp-<nn>-<slug>) with a readiness gate before each story and
  checkpoints between stories.
- **sketch / demo** require a brief (business-analysis runs first when
  missing) and a design system MASTER (the design-system entry owns its
  creation; other flows only redirect).
- **business-analysis** and **design-system** are interactive main
  conversation flows with a closing approval gate each.
- **setup** is idempotent bootstrap (including the PMO prerequisite
  check and project registration); **configure** is the single change
  gate for the project config file.

## Critical behavioral rules (head of every flow)

1. Execute steps in the declared order; never skip, reorder or merge.
2. State and artifacts are the source of truth: read prior steps from
   the PMO database and from files, never from conversation memory;
   after any compaction run resume-info and re-read before acting.
3. Stop at every gate and wait for explicit user choice. Develop-flow
   gates offer Approve, Request changes (revise and re-gate), Pause
   (save and stop); design-flow gates offer their own choices (pick a
   direction or re-run with different emphases; approve the refined
   preview).
4. Halt on failure: present the error and ask; never continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode; the flow is the plan.

## Operations backbone (the pmo plugin)

The software-team plugin declares the pmo plugin as a dependency;
installing the team installs the backbone. Pmo owns the user-level data
directory (default: .agentrof under the user's home; AGENTROF_HOME
overrides) holding one central SQLite database for every project and
every future team: projects, epics, stories, machine-generated tasks,
runs with step state and gates, findings, coverage rows, budget
verdicts, the quality ledger and an append-only audit event per
mutation.

The database has exactly ONE writer: the PMO CLI. Flows call the synced
launcher (bin/pmo_cli.py under the data directory; pmo's SessionStart
hook keeps it current). Spawned agents never touch the database; a
PreToolUse guard hook denies direct file writes to it.

Hooks carry the mechanics, flows carry the semantics: pmo's
SubagentStart/SubagentStop hooks stamp task start and finish times for
team agents automatically; the orchestrator's CLI calls carry which
step, which round and which outcome. SessionStart injects resume context
when the project has an active run; SessionEnd flags a session that dies
with a run still active.

## Work-item hierarchy

Epic (PO-authored grouping with a business goal) > story (PO-authored;
the only planning unit; WP-## ids; carries scope, exclusions, Definition
of Ready, Definition of Done) > task (machine-generated: the develop
flow opens one per spawned step and the hooks stamp its timing; nobody
authors tasks). The CLI's import rejects stories with empty scope,
exclusions, DoR or DoD. workspace/docs/backlog.md and
workspace/docs/quality-ledger.md are GENERATED views rendered from the
database at checkpoints; they are committed for review and durability
and never hand-edited.

## State contract

Run state lives in the database: run row (project, claimed story, run
key, status, current step, review and qa round counters, worktree),
step rows (status, artifact path, attempts), gate rows (decision,
decided_by, decided_at), ownership rows, finding rows (stable F-### ids,
source review/qa/design_qa, severity, open/fixed/waived), coverage rows,
budget rows, ledger lines. The CLI enforces the enums, the step
transition guard, the run-complete guard (steps done, gates recorded,
findings closed), snake_case ownership roles and ownership-overlap
refusal across all of the project's active runs.

Step status enum: pending, in_progress, done, blocked, escalated.
Run status enum: running, waiting_gate, blocked, escalated, complete.

Claims, atomic at run init: one active run per worktree; one active run
per story; disjoint ownership path prefixes across active runs. A
refused init means resume the holder (resume-info names it), never
archive it blind. Claims free when the run leaves the active statuses
(running, waiting_gate). While a run is active, the business-analysis
and configure entries refuse edits that would fork the running spec.

The run directory (workspace/runs/<run-key>/, gitignored) holds ONLY the
snapshots init copies there: the constitution, the brief snapshot and
the config snapshot; the run reads the snapshots for its whole duration.
Suite artifacts (junit output) go to gitignored workspace paths, never
into the run directory.

## Spawn prompt contract

Every Task spawn assembles, in order:

1. Identity: role name, step, run key, run directory. Standalone design
   spawns (sketch, demo, design-system entries) have no run directory or
   run row; identity names the flow and topic instead, and the
   constitution body is read from the plugin's constitution file.
2. The constitution body, pasted verbatim (placeholder: `{{constitution}}`).
3. Inputs: explicit file list, split into read-fully and summary-only.
4. Skill binding: the knowledge skill(s) bound to the role from config.
5. The task plus its acceptance criteria, each with a verify line.
6. Output: exact artifact path(s), the SELF-CHECK closing requirement,
   write-nothing-else, never touch the PMO database.

## Step output contract

A step emits exactly one artifact at its declared path (plus code changes
for implementation steps). No sidecar reports, no memory writes. The
mechanical post-step check runs the plugin's artifact_check script (path,
non-empty, mandated sections) before state advances. On violation:
exactly one retry with the violation named in the prompt; a second failure
sets the step blocked and halts with resume instructions. The model
gate's mechanical half runs the contract_check script and the CLI's run
validate before the gate is presented (ownership overlap is already
refused at set-ownership). Review and verification findings are not
files: the reviewer and verifier return them in the reply and the
orchestrator records them through the CLI, passing the open set back
into re-review spawns.

## Gates and loops

Gate inventory scales with the route: classification confirm; brief
approval; DS gate (the design-system candidate pick); backlog gate
(approve, then item import loads the database and the backlog view is
re-rendered); model and contract gate (architect delta shown in
conversation with a breaking-change flag; the record is the git diff);
direction pick and handshake (two distinct design-flow gates); delivery
gate; inter-story checkpoints; the pull request itself. Every gate is a
manual stop and is recorded with record-gate; there is no auto mode.

Fixed output paths: design-system candidates preview at
workspace/docs/design-system/candidates.html (deleted after MASTER is
written); sketch and in-package previews at
workspace/sketches/<slug>/preview.html; demo packages at
workspace/demos/<slug>/demo.html.

Review and QA run sequentially after implementation, each a bounded loop:
findings route to the owning developer by file; only the fix is
re-checked (a new critical or high finding on untouched, already-passed
lines at re-review must cite what changed); maximum three rounds each,
then blocked plus escalation. Findings that implicate the approved
architecture never enter the fix loop; they escalate immediately, and
the owner may overrule by recording an owner-decision entry in the
decision log. Severity policy: fix_required only for critical and high
findings; lower severities become pull request notes. QA's gates include
the coverage matrix (scenario_report, imported into the database with
its --json-out file), the mandatory mutation gate scoped to the story's
changed files, the budget-verification verdicts (verified or honestly
unverified per quantified budget, recorded with budget set) and the live
protocol; screenful stories add a read-only design verification by the
designer against the approved preview (findings recorded as design_qa).

At the merge checkpoint on the main line: the story marked done and the
ledger checkpoint appended (with the escaped-defect flag when a
fix-atomic traced back), the backlog and ledger views re-rendered from
the database, the changelog append from the PR quality summary, the
published interface schema, brief BR updates from fix-atomic work, the
deployed_verified field once the owner confirms the story runs in its
target environment, and every tenth checkpoint the architecture
reconciliation (living docs vs code; page overrides vs the design
master).

## Concurrency and git

One active run per worktree, one active run per story, disjoint
ownership across a project's active runs: the claim system is designed
for parallel worktrees, while the shipped flows still drive one run at a
time (a parallel-orchestration flow is future work and needs no schema
change). Backlog updates never ride feature branches; the database is
updated and the views re-render on the main line at the checkpoint after
merge. Architecture deltas deliberately ride the story branch so model
and code merge atomically. Each story ends in its own pull request;
merging is a human act.
