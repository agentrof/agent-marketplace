# Design Flow

State-machine procedure for a design handshake: N genuinely divergent
directions, a pick, refinement rounds, an approved preview. Used by the
sketch entry, the demo entry, and screenful stories inside the develop
flow. It never creates the design master.

## Critical behavioral rules

You MUST follow these rules exactly. Violating any of them is a failure.

1. Execute steps in the declared order. Do NOT skip, reorder or merge.
2. State and artifacts are the source of truth: read prior outputs from
   FILES, never from conversation memory; re-read after any compaction.
3. Stop at every gate and wait for explicit user choice, asked through
   the AskUserQuestion popup (options with tradeoffs, recommended
   first).
4. Halt on failure; present the error and ask. Never continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode. This flow IS the plan.

## Preconditions

- Dispatcher, as in the develop flow's state contract:
  RUN="${AGENTROF_HOME:-$HOME/.agentrof}/bin/agentrof_run.py" with
  TEAM=software-engineering-team; plugin files are reached only through it.
- The topic's analysis space passes its approval gate for the touched
  scope: run "$RUN" run "$TEAM" scripts/ba_compile.py check --space
  workspace/docs/business-analysis/<slug> --gate approval (add --node
  domains/<name> when the work touches one domain; a buildable domain
  does not wait for the others). Nonzero, or no space at all: stop and
  run the business-analysis entry flow first, then resume here.
- The design master exists at workspace/docs/design-system/MASTER.md.
  Missing: STOP, tell the user "no design system yet", route them into
  the design-system entry, and resume here once MASTER exists. This flow
  never creates or edits the master. Any docs-tree write follows the
  obsidian-vault skill's vault law.

## Spawn prompt template

Spawns follow the develop flow's template shape. Inside a develop work
order, identity and constitution sourcing are the develop flow's.
Standalone (sketch, demo, design-system entries), identity is "You are
<agent-name>, executing the design flow for <topic>." and the
constitution body is read from the file printed by
"$RUN" path "$TEAM" constitution.md, then pasted verbatim:

{{constitution}}

then inputs (the space root overview read fully plus the touched
domain's process and acceptance docs read fully, the generated registry
summary-only; MASTER.md read fully; the relevant page override when one
exists), the task with the requested direction count, and the exact
output path. Standalone design work has no order
directory and no PMO work-order row; the state-touching clauses of the
develop template do not apply.

## Steps

### Step 1: directions

- Spawn ux-designer to produce the requested number of
  genuinely divergent directions (default three) in ONE self-contained
  preview file with realistic placeholder data, every value drawn from
  MASTER tokens, and the axis of difference plus rationale stated per
  direction. Reader-facing placeholder copy follows output_language;
  identifiers in it follow terminology_language.
- Output path by mode: sketch mode and in-story mode write
  workspace/sketches/<slug>/preview.html; demo mode writes the working
  file under workspace/demos/<slug>/.
- Mechanical check: the file exists, opens standalone, contains the
  declared number of directions. One named retry, then halt.

### GATE: direction pick

- Present the preview; the user picks a direction through the
  AskUserQuestion popup (one option per direction, its axis of
  difference in the description) or requests different emphases (re-run
  step 1 once with the new emphases).

### Step 2: refinement rounds

- Iterate on the chosen direction in the SAME file: the user requests
  adjustments, the designer applies them with rationale. Page-specific
  deviations from MASTER are recorded as
  workspace/docs/design-system/pages/<page>.md by the design-system
  entry, never silently.
- Demo mode additionally expands the chosen direction into a multi-screen
  navigable package: one self-contained file, simple in-file navigation,
  realistic placeholder data, zero external requests.

### GATE: handshake

- The user approves the refined preview through the AskUserQuestion
  popup. The approved file IS the specification; no separate spec
  document exists.
- Before approval is offered, the designer must have passed the
  pre-delivery checklist and its adversarial self-critique (contrast,
  focus, dark variant, token compliance, reduced motion).

### Step 3: persist and hand back

- Sketch mode: the approved preview stays under workspace/sketches/<slug>/
  and is committed; it seeds later demo or develop work.
- Demo mode: the final package is committed as
  workspace/demos/<slug>/demo.html.
- In-story mode: the approved preview stays under
  workspace/sketches/<wp-slug>/, is committed with the story, and its
  path is recorded as the work order's step artifact (work-order
  set-step --artifact) for the frontend developer.
