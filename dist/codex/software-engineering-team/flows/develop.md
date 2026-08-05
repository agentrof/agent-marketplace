# Develop Flow

State-machine procedure delivering ONE story (or one atomic change) end
to end. Loaded by entry skills; its spec lives in the orchestration document.

## Critical behavioral rules

You MUST follow these rules exactly. Violating any of them is a failure.

1. Execute steps in the declared order. Do NOT skip, reorder or merge.
2. State and artifacts are the source of truth. Read prior steps from
   the PMO database and from FILES, never from conversation memory;
   after any compaction, run resume-info and re-read before acting.
3. Stop at every GATE and CHECKPOINT for explicit user choice via a
   choice gate (tradeoffs in descriptions). Offer exactly:
   Approve / Request changes (revise, re-gate) / Pause (save, stop).
4. Halt on failure: present the error and ask. Never continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode. This flow IS the plan.

## State contract

All process state lives in the central PMO database. ONE writer: you,
the main conversation, through the PMO CLI only. Agents never touch it;
hooks record spawn/stop mechanics; a guard blocks direct file writes.

CLI resolution, once per work order (re-read this paragraph after any
compaction): PMO="${AGENT_MARKETPLACE_HOME:-${AGENTROF_HOME:-$HOME/.agentrof}/agent-marketplace}/bin/pmo_cli.py";
missing means run the project-management-office control-tower entry once
to bootstrap it (project-management-office absent entirely: STOP,
reinstall this plugin). Run the idempotent ensure before first use. The
dispatcher lives beside it, RUN="${AGENT_MARKETPLACE_HOME:-${AGENTROF_HOME:-$HOME/.agentrof}/agent-marketplace}/bin/marketplace_run.py"
with TEAM=software-engineering-team: `"$RUN" run "$TEAM" scripts/<x>.py`
executes a plugin script, `"$RUN" path "$TEAM" <relpath>` prints a
plugin file to read; plugin files are reached only through it.

Work order identity: project_key comes from workspace/config.json
(stamped by setup); the key is <yyyymmdd>-<kebab-slug> (suffix -2, -3
when an abandoned order already holds it), its date prefix pasted from
`"$PMO" now --compact` (init refuses a stale prefix). Subcommands in
play: now; work-order init / set-step / record-gate / bump /
set-ownership / set-status / release / validate; task open / close;
finding open / update / list; coverage import / list; budget set; checkpoint;
item import / update / list / add-dep / add-dod / set-dod / order;
resume-info. The CLI enforces the enums, the step-transition
guard, the complete guard (steps done, findings closed; story work
orders also need imported coverage and a ledger checkpoint), snake_case
ownership roles, and ownership-overlap refusal across ALL active work
orders of the project; advance step status with set-step as you move.

Claims, enforced atomically at work-order init: one active work order
per worktree; one per story; ownership path prefixes disjoint across the
project's active work orders. A refused init means resume the holder
(resume-info names it), never archive it blind.

The order directory workspace/work-orders/<key>/ (gitignored) holds ONLY
the snapshots init copied there (constitution.md, brief-snapshot/ with
the whole analysis space, config.snapshot.json) plus the freeze manifest
step 0 writes (freeze.json; a guard denies edits to its listed paths
while the order is active). Snapshots are read for the whole order; a
mid-order space or config edit never changes a running story; nothing else is written there.

Findings, coverage rows, budget verdicts, round counters and tasks are
DATABASE rows, not files. Reviewer and verifier spawns RETURN findings
in the reply; record them with finding open/update and pass the open set
into re-review spawns from finding list --json. Severity enum: critical,
high, medium, low (a reviewer's "major" is high, "minor" is low).

Task trail: before each spawned step, open its task (task open
--work-order-key <key> --role <snake_case role> --step <n> --title
"<title>"); after the post-step check passes, close it (task close ...
--outcome done). Role names are the FULL agent role names
(software_architect, backend_developer, frontend_developer,
devops_engineer, code_reviewer, qa_engineer, ux_designer,
product_owner); a shortened name forks the hooks' task trail. Hooks
stamp timing and the attempt history; the semantic fields are yours.

DoD verification trail: the QA step's verdicts flip the story's
dod_items one by one (item set-dod --dod-id <id> --status verified, or
failed with --failure-reason); pending dod_items at the delivery gate are named as open work.

Suite artifacts (junit output) go to gitignored workspace/junit-<suite>.xml, never the order dir.

Step status: pending | in_progress | done | blocked | escalated.
Work order status: running | waiting_gate | blocked | escalated | complete.

## Spawn prompt template

Every agent spawn assembles, in this order:

1. Identity: "You are <agent-name>, executing step <n> of work order
   <key>. Order directory: <path>."
2. The constitution body, pasted verbatim:

   {{constitution}}

3. Inputs: an explicit file list, split into read-fully (this step's
   declared inputs) and summary-only (other prior artifacts).
4. Skill binding: the knowledge skill(s) bound to this role in the work
   order's bindings, read from workspace/config.json.
5. The task, with its acceptance criteria, each carrying a verify line.
6. Output: the exact artifact path(s), the requirement to end with
   SELF-CHECK, write nothing else, and never touch the PMO database.

Parallel dispatch: independent roles go out as multiple host-native agent
spawns in one dispatch; consume their artifacts from disk afterwards.
Resolve every role through the active host contract.

## Mechanical post-step check

After every spawned step, BEFORE advancing state, run
"$RUN" run "$TEAM" scripts/artifact_check.py --path <artifact>
--require-sections "<the artifact's mandated sections>" (data model:
"Summary, Entities"; contract: "Summary, Error cases"; environment
contract: "Commands, Scenarios, Tolerated Warnings"). Analysis-space
checking has a single home: "$RUN" run "$TEAM" scripts/ba_compile.py
check, never artifact_check. Exit 1: re-spawn the SAME step exactly once
with the violation named; a second failure sets the step blocked via
set-step and halts with resume instructions. The script proves structure;
still read the artifact for semantic sanity before presenting any gate.

## Steps

### Step 0: init

- Resolve the project git root; anchor everything there. No git
  repository: stop and offer through a choice gate to initialize one.
- Read workspace/config.json. Missing or without a project_key: stop and
  route to the setup entry. Unsupported stack values: refuse and stop.
- Resolve the PMO CLI per the state contract and run init-db.
- Brief precondition, mechanical: run
  "$RUN" run "$TEAM" scripts/ba_compile.py check --space
  workspace/docs/business-analysis/<slug> --gate approval (--node
  <domain> when the story touches one domain), then render. Nonzero
  blocks init: route to the business-analysis entry.
- Initialize the work order: work-order init --project-key <key>
  --work-order-key <id> --request "<request>" --worktree <git root>
  --story <WP-##> --bindings '<json>'
  --order-dir workspace/work-orders/<key>
  --constitution "$("$RUN" path "$TEAM" constitution.md)"
  --brief workspace/docs/business-analysis/<slug> --config
  workspace/config.json. It claims the worktree and the story, marks the
  story in_development, writes the step skeleton and copies the
  snapshots (the space directory lands as brief-snapshot/).
- Freeze manifest: read the story's criterion ids from the database
  (coverage list --project-key <key> --story <WP-##> --json), resolve
  them with ba_compile.py resolve against the LIVE space and
  write the owning doc paths as freeze.json ({"frozen_paths": [...]}) in
  the order directory; the guard freezes exactly those docs while the
  order is active. Bindings from config: backend role to the backend
  stack skill, frontend role to the frontend stack skill, devops role to
  the environment stack skill, architect to the architecture skill, the
  obsidian-vault skill (vault law for the living documents) and every
  database skill in the databases set, planner to the planning
  skill, analyst to the requirements-analysis skill, reviewer to the
  review skill, verifier to the verification skill.
- Ensure workspace/work-orders/ is gitignored; append the rule if absent.
- Create the work branch for this story from the main line, named
  wp-<nn>-<kebab-slug> (atomic route: atomic-<kebab-slug>).

### Step 0.5: readiness gate

- Lives in step 0's state row: no own step id; passing advances nothing.
- Read this story's row (item list --kind story --json) and check every
  item of its Definition of Ready against reality (dependencies actually
  merged per the story's recorded edges, criteria unambiguous, preview
  present when required). Any item failing: the story is NOT ready;
  return it to the product owner with the failing items named, set the
  story back (item update --external-id <WP-##> --status planned), set
  the work order blocked via work-order set-status, and stop. Never
  start implementation on an unready story.

### Step 1: architecture delta

- Spawn software-architect with the snapshotted analysis
  space: read-fully the snapshot's root overview, budgets and the docs
  owning this story's claimed ids (ba_compile.py resolve against the
  snapshot); summary-only its generated registry and index, plus the
  solution-design landscape and its generated decision index when
  present (read fully only the decision notes the delta must conform
  to). Add the living documents under
  workspace/docs/system-architecture/ and this story's scope from its
  backlog row.
- The architect applies its delta to the living documents (new or
  changed decisions land as their own notes under
  workspace/docs/system-architecture/decisions/ per the decision-records
  contract; afterwards the flow re-renders the generated index via
  "$RUN" run "$TEAM" scripts/vault_check.py render-decisions --vault
  workspace/docs) and returns
  the ownership map; the delta declares its environment impact (services,
  stores, runtime variables, seed needs, or an explicit none) and the map
  grants workspace/environment/ to the devops engineer when impact exists
  or no environment definition exists yet. Store the map via work-order
  set-ownership. The CLI refuses overlapping prefixes, inside the work
  order and against every other active work order; a refusal routes back
  to the architect as the named violation.

### GATE: model and contract

- Present the architect's delta summary: changed and new sections, the
  breaking-change flag, denormalization decisions with their recorded
  rationale. The full record is the git diff.
- Mechanical half, BEFORE presenting the gate:
  "$RUN" run "$TEAM" scripts/contract_check.py --contract
  workspace/docs/system-architecture/api-contract.md (every endpoint
  declares error cases); "$RUN" run "$TEAM" scripts/vault_check.py
  check --vault workspace/docs --scope system-architecture (freeze.json
  paths passed as --exclude); and work-order validate; any nonzero
  blocks the gate, routed back to the architect as the named violation.
- Judgment half at the gate: error-case completeness in substance,
  boundary sanity, budget citations. The gate cannot pass otherwise.
- Approve / Request changes / Pause. Record the outcome with
  record-gate --gate model_contract.

### Step 2: implementation (parallel)

- A screen-requiring Definition of Ready without an approved preview:
  run the design flow now (flows/design.md) and return.
- Environment first: on a delta declaring environment impact (or a
  project with no environment definition yet), spawn
  devops-engineer serially before the developers, bounded
  to the environment prefix (workspace/environment/); its SELF-CHECK is
  the from-scratch cycle its bound skill defines.
- Spawn backend-developer and frontend-developer in one message, each bounded to its
  ownership paths, read-fully inputs: the architecture delta, the
  contract, the approved preview and design master (frontend); include
  the configured environment command in the prompt. Stories without
  client or server work spawn only the relevant developer.
- Ownership overlap discovered mid-flight: serialize (backend first)
  and note it via event append.
- Re-slice branch: a developer reporting scope larger than the story
  (new entities, endpoints or screens the backlog never sliced) halts
  the step; present the discovery, route to the product owner for a
  re-slice through a mini backlog gate and import, then resume this
  story with corrected scope or abort it in favor of the new slices.
  Never absorb discovered scope silently.
- Post-step check: the configured test command passes per developer's
  SELF-CHECK; code changes stay inside ownership paths; server-touching
  stories export the interface schema and the suite's client-shape check
  ran against it (contract drift is a red suite, not an opinion).

### Step 3: review loop (max 3 rounds)

- Spawn code-reviewer with the diff scope, the living
  architecture documents, and the currently open findings from finding
  list --json (empty on round one).
- The reviewer RETURNS its verdict and findings; the FIRST action on
  that reply is recording every new finding with finding open --source
  review. Fixing an unrecorded finding is a contract violation.
- Verdict approve: continue. Verdict fix_required (issued only when a
  critical or high finding is open; lower severities never spin the
  loop): route each finding to the developer owning its file; the
  reviewer re-checks only the fixes against the same set; flip resolved
  findings with finding update --status fixed --round <n>; increment
  with work-order bump --counter review.
- Churn guard: at re-review, a NEW critical or high finding on
  untouched, already-passed lines needs the reviewer to cite what
  changed; otherwise reject it and keep the round scoped to the fixes.
- A finding implicating the approved architecture does NOT enter the
  fix loop: set the step escalated, present it, stop for the owner.
  Exits: the owner's ruling lands retroactively as its own decision
  note under workspace/docs/system-architecture/decisions/ (the
  stamp-decision verb owns status and supersede mechanics; the flow
  re-renders the index) and the loop resumes; or the architect revises
  the delta through a mini model gate, then resume here.
- The review counter reaching 3 without approve: blocked, escalate, halt.

### Step 4: verification loop (max 3 rounds)

- Spawn qa-engineer with the story's criteria read from
  the snapshot (the acceptance docs named by ba_compile.py resolve over
  the claimed ids), the currently open findings from finding list --json,
  and the configured commands (test_command, mutation_command,
  env_command).
- The mutation gate is mandatory on code stories: QA runs the configured
  mutation command scoped to this story's changed code-owned files; every
  surviving mutant in changed lines is a finding (high on a BR/AC-tagged
  path, low otherwise); a missing mutation_command on a code story is
  itself a blocking finding.
- The verifier RETURNS findings, the coverage matrix and the budget
  table. The FIRST action on that reply is recording them: finding open
  --source qa per finding; the coverage script with --json-out, imported
  (coverage import); each budget verdict with budget set (verified, or
  unverified with the reason; load-only budgets are never faked green).
  Routing a fix before recording is a contract violation.
- Flip the story's dod_items from the verdicts (item set-dod --status
  verified, or failed with --failure-reason); a failed item is a finding
  by another name and routes with them.
- Pass: continue. Fail: route findings to the owning developer exactly
  as in review; work-order bump --counter qa; re-verify only what
  changed and flip fixed findings.
- Requirement gaps escalate to the owner, never patched silently. The
  qa counter reaching 3 without pass: blocked, escalate, halt.

### Step 4.5: design verification (screenful stories only)

- After QA passes, spawn ux-designer READ-ONLY with the
  approved preview, the design master and the built screens (reuse the
  environment the QA live protocol stood up): it re-judges the
  realization with its pre-delivery checklist (contrast, spacing rhythm,
  motion character, hierarchy, token fidelity) against the spec it
  authored. Findings are recorded with finding open --source design_qa
  and route like review findings (token drift to the frontend developer;
  spec ambiguity escalates); one fix round, then re-judge once; then
  tear the environment down.

### GATE: delivery

- Present the story summary: what was built, review verdict and rounds,
  verification results (coverage, mutation, budgets, dod_items from the
  database), low-severity findings carried as notes.
- Approve / Request changes / Pause. Record the outcome with
  record-gate --gate delivery.

### Step 5: finalize

- Commit on the work branch and open the pull request (message and PR
  body in the terminology_language, default English); the body carries
  the compact quality summary (review verdict and rounds, coverage
  matrix result, mutation result, live verification result, low-severity
  notes) and, when the story authored migrations, the migration notes:
  which migrations, their order, the safe-to-run-twice statement and the
  rollback note. The handoff is complete or the story is not done.
- Merge checkpoint (main line only). Backlog updates NEVER ride this
  branch; the list below runs only on the primary checkout. Solo: this
  session, after the human merges. Parallel (worktree opened by the
  delivery-lanes flow): step 5 ends at the opened pull request; mark it done
  with the PR URL as artifact, set the work order waiting_gate (claims
  stay held), report the PR to the integrator and stop; the integrator
  executes this list at its merge checkpoint. After merge, on the main line:
  - mark the story done (item update --external-id <WP-##> --status
    done), then run the checkpoint subcommand (checkpoint
    --work-order-key <key>, --escaped-defect when a fix-atomic traced
    back): it appends the story's quality-ledger row in the database
    (read back with ledger list --project-key <key> --tail <N> --json);
  - append the story's line to workspace/CHANGELOG.md from the PR
    quality summary (append-only; any date pasted from "$PMO" now --date);
  - publish the exported interface schema under workspace/docs/api/;
  - vault stewardship: clear this order's freeze.json (the flow authors
    the manifest), re-render the decision indexes (vault_check.py
    render-decisions), then run "$RUN" run "$TEAM" scripts/vault_check.py
    check --vault workspace/docs and repair every finding now, map notes
    included (index conflicts heal by re-render, never hand-merge);
  - update the analysis-space BR rows changed by fix-atomic work since
    the last checkpoint (edit the owning rule_set row in place: same id,
    new statement, or retire plus mint), then ba_compile.py check plus
    render and commit the views with the change;
  - spec-fork tripwire: compare statement_sha256 of the claimed ids
    between the snapshot's registry.json and ba_compile.py resolve on
    the live space; a mismatch no recorded fix-atomic update explains is
    an escaped spec fork: halt and present it before completing;
  - record deployed_verified (item update --deployed-verified true) when
    the owner confirms the merged story runs in its target environment;
  - every tenth checkpoint, or on demand, run the architecture
    reconciliation: an architect audit of living documents against the
    code and of page overrides against the design master (stable
    overrides fold back in; contradictions become findings);
  - set the work order complete (work-order set-status --status
    complete) only when every step is done and every finding is closed;
    on story work orders the coverage rows and the ledger line must be
    in the database; the CLI's complete guard refuses otherwise;
  then ask "continue with the next story?" via a choice gate (solo) or
  hand back to the delivery-lanes flow's PROPOSE
  (parallel).

## Atomic route variant

Atomic work has two tiers; the entry names the tier at classification.
Both tiers run step 0 as written, except: pass --story only when the
change maps to an existing backlog story (most atomic work has none).
Skipped steps are marked done explicitly before finalize (work-order
set-step --status done --artifact "skipped: <tier>"), keeping the
complete guard honest about what ran.

COSMETIC-ATOMIC (no behavior change: copy, a label, an existing-token
swap): skip step 1, both gates, and the review and verification loops;
spawn only the owning developer with the task and ownership bounded to
the touched files; finalize with a small pull request (message and body
in the terminology_language) whose body states the route
(cosmetic-atomic), the diff summary and the test command result.

FIX-ATOMIC (any behavior change: a bug fix, a rule correction): after
step 0, in order (every item below is SPAWNED agent work with its task
trail; writing the test, the fix or the review in the main conversation
is a contract violation):
1. Reproduction first: the spawned owning developer writes a FAILING
   test that reproduces the defect, tagged to the violated BR id (or a
   newly minted id under the owning domain's code when the behavior was
   never specified), before touching the fix. The test is permanent
   regression-suite growth.
2. Fix until the reproduction test and the whole suite are green.
3. ONE reviewer pass (single round): findings recorded and routed once
   to the developer, re-checked once; an architecture-implicating
   finding escalates as in the large route.
4. Finalize with a small pull request (message and body in the
   terminology_language) whose body states the route (fix-atomic), the
   reproduction test name, the diff summary and the test command result.
5. At the merge checkpoint ON MAIN, update or add the BR row in the
   owning rule_set when the fix changed or defined specified behavior
   (the backlog-main rule extended to the analysis space), re-run
   ba_compile.py check and render, and run the checkpoint subcommand
   with --escaped-defect when the fix traces to a prior story.

Both tiers: UI-touching atomic work still draws every visual value from
the design master's tokens. No master in the project: a change that
would introduce a new visual value stops and routes to the design-system
entry; changes introducing no visual value proceed. Environment paths:
the devops engineer is the owning developer; changing the service or
store set is never atomic; fix-atomic work substitutes the skill-defined
from-scratch reproduction for the failing test.

Tripwire, BEFORE finalize on both tiers: run
"$RUN" run "$TEAM" scripts/atomic_tripwire.py --repo . --range
main...HEAD. Exit 1 proves the work was never atomic: STOP, report "not
atomic" with the flagged files, and hand the request to the large route
unchanged. Disposition: set the work order escalated (work-order
set-status), delete the abandoned atomic branch, and let the large route
start its own work order. The judgment-level escape hatch stands
independently: the moment the work touches the data model, the contract,
the schema or the environment's service or store set, stop without
waiting for the tripwire.
