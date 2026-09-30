# Authoring guide

This repository ships one host-neutral Software Engineering Team. Canonical
content is under `plugins/software-engineering-team/`; generated host wrappers
are under `dist/` and are never edited by hand.

## Repository boundaries

- Keep canonical skills, agents, flows, scripts and templates under
  `plugins/software-engineering-team/`.
- Keep host-specific loading, permissions and behavior under the appropriate
  `platforms/<host>/` adapter.
- Keep consuming-project content in tracked `workspace/docs/`. `.agentrof/`,
  `.claude/`, and `.codex/` are ignored runtime surfaces.
- Keep the Software Engineering Team standalone and scoped to the current
  project checkout.
- Declare package executable paths in root `package-modes.json`; source
  filesystem modes are not a portable package contract.
- Treat `.gitattributes` and the builder text allowlist as one checkout
  boundary: known UTF-8 text is canonicalized, while unknown and binary paths
  are byte-exact.

## Requirement Flow contract

```text
requirement -> business-analysis -> solution-design -> design-system -> experience-design -> backlog-plan -> delivery-plan -> execution-plan -> deliver
```

An exact `REQ-###` selects the Requirement-driven chain. Its impact matrix
decides `required`, `reuse` or `not_applicable`; each applicable stage binds a
current approved committed receipt. Without `REQ-###`, the user explicitly
selects approved/current upstream packages and no Requirement state is created.

An identical Requirement receipt retry verifies the current committed package
and retained receipts before preserving all bytes and downstream links. A
changed predecessor still invalidates downstream receipts. Backlog generated
views likewise avoid rewriting identical bytes without caching validation.

Backlog defaults to four pinned input families. The explicit `headless-v1`
contract may omit genuinely absent Design System/Experience families only for
an approved technical/defect Requirement with those stages `not_applicable`,
current BA/Solution bindings, a CLI/worker/scheduler Solution topology, no
authored links into the omitted family and complete Git history proving no
previous visual package. Each new revision repeats and revalidates
`--absent-input`; current or historical packages cannot be discarded to qualify.
Historical Delivery consumption verifies the original approved committed
backlog boundary, so later visual work does not invalidate that snapshot.

Experience Design produces the globally current `application@rN` receipt and
the exact current zero-or-more process receipt set. A later approved prototype
or package-set delta makes that application receipt non-current, so consuming
Requirement and backlog revisions must rebind before a new handoff.

The complete lifecycle is defined in
[requirement-delivery-protocol.md](requirement-delivery-protocol.md).

## Artifact policy

Unless a subtree explicitly declares a closed artifact contract, files below
an `artifacts/` directory under a policy-valid vault folder are opaque local
artifacts, not Markdown notes. Names and extensions are unconstrained,
symlinks are rejected, and authored Markdown may link or embed a real local
artifact.

Design System is a closed exception: contract-v3 pairs `MASTER.md` with its
offline `design-system/artifacts/standalone.html` catalog.

Experience Design is intentionally not a closed exception. The complete
`workspace/docs/experience-design/artifacts/` tree is a UX author's prototype
workspace. It may include any directories, file names, page topology, HTML,
CSS, JS, framework build output, dependencies and assets. `index.html`,
multiple linked pages and `css/`, `js/` or media folders are useful optional
conventions. They are never compiler requirements.

The Experience compiler does not parse, lint, execute, sandbox, normalize or
rewrite prototype contents. Its only artifact checks are safety and lifecycle
boundaries: snapshot files are regular non-symlink files within the artifact
tree, and an approved snapshot still matches its recorded bytes and paths. It
writes lifecycle data only below `experience-design/_generated/` and
`experience-design/_ledger/`.

Prototype implementation is an exploratory acceptance artifact. Delivery later
implements the product according to its own architecture, engineering and
quality standards. A reviewer can recommend practices for the prototype but
those recommendations cannot become hidden compiler rules.

The compiler reserves `application` from Experience process slugs and aliases.
Its approval transaction covers create, update, rename and retire actions,
prototype receipt state and compiler-owned open-revision state. An
application-only revision changes no process revision but creates a new current
`application@rN`. The durable history is
`experience-design/_ledger/application-revisions.json`; the current projection
is `experience-design/_generated/application-registry.json`.

Reviewers, not the snapshot compiler, judge prototype fidelity, usability,
accessibility, visual quality, behavior and design coherence. Approval requires
a fresh transient schema-v4 attestation bound to proposal, artifact-tree,
package-set and application hashes. Its advisory notes are never an approval
condition or compiler rule.

## Backlog contract

The canonical tree is:

```text
workspace/docs/backlog/
├── backlog.md
├── reviews/
│   └── round-<n>-backlog-review.md
├── epics/<epic-slug>/
│   ├── epic.md
│   ├── reviews/round-<n>-epic-review.md
│   └── stories/<story-slug>/
│       ├── story.md
│       └── test-plan.md
└── _generated/
    ├── registry.json
    ├── board.md
    ├── dependency-map.md
    └── test-coverage.md
```

`backlog_compile.py` creates deterministic stubs, validates front matter and
paths, resolves upstream and dependency links, requires each story's test plan
and renders disposable views. `experience_refs` use exact values such as
`checkout:SCR-001@r2`; they resolve through an approved process registry or
ledger and are bound to the pinned application and process receipt set.

Every story contains User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. Criterion and
rule links are vault-absolute links to stable headings. Delivery execution
consumes an approved backlog without rewriting source.

## Host and runtime contract

Claude Code and Codex install the same standalone team through their native
marketplaces. Setup creates only project-local runtime and host projection,
preserves authored Markdown and user-owned configuration, and rolls back
setup-owned writes when closing checks fail.

Generate distributions and verify before committing:

```text
python3 tools/build_distributions.py
git add <complete-change-paths>
make check-local
python3 tools/ci_local.py verify --staged --target origin/main
```

Use `make counts` only to refresh derived README counts. Never edit `dist/`
directly.

`check-local` requires complete staging and exact worktree/index equality.
It always runs static checks, then the complete affected selection or valid
local evidence for exactly that candidate. Unknown/shared inputs select full
coverage. Verify again immediately before commit. `make check` remains the
exhaustive sequential oracle; local receipts never replace required remote
platform, installation or release checks.

## Execution profiles

Canonical agents declare only a host-neutral `reasoning` tier from
`tools/data/models.json`. Tier names are single words because each is both a
kebab-case reasoning level and a snake_case key in the profile tables. A
process switch value may declare generated agent variants on another tier,
such as the `lens` tier of the review-panel readers or the `mechanical` tier
of the writers' fix passes (see Process switches).
Each host maps every tier to its own model and
effort in `platforms/<host>/execution-profiles.json`, profile `auto`. The
builder and `tools/validate.py` accept only the host's documented values, so
model names stay out of `plugins/`. Claude agents receive `model:` and, when
the table sets one, `effort:`; an omitted `effort` follows the session. Codex
dist agents carry the resolved `model` and `model_reasoning_effort`, which
setup renders into `.codex/agents/`.

`inherit` is the user override that makes every role follow the parent
session's model and effort:

- Codex: `generate_codex_project.py apply --project-root <root> --scope local
  --execution-profile inherit` omits both keys from every role file. The managed files record the choice, later
  refreshes keep it, and `--execution-profile auto` restores the default. It
  is a local setup flag, not a `workspace/config.json` field: the closed
  config refuses performance knobs, and the profile is a personal host choice
  that changes only the ignored projection.
- Claude Code: a plugin cannot switch frontmatter per user, and
  `${user_config.*}` is substituted only in the agent body. The documented
  session setting `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (Claude Code 2.1.257 or
  later) runs every role on the main conversation's model. A frontmatter `effort` overrides the session
  level but not `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the
  session. Both reach every subagent in the session, not only this team.
  Claude Code's permission modes do not select a model or effort.

## Process switches

Every new process behaviour ships behind a switch whose default is today's
behaviour. `plugins/software-engineering-team/skill-content/configure/data/process-switches.json`
declares each switch; a project's values live in
`workspace/docs/delivery/process-policy.md`, changed only through
`/configure process` and never in `workspace/config.json`. A switch without a
policy row follows its package default.

To add a switch:

1. Declare it in the registry: its `summary`, owning `flows`, at least two
   `values` with their `tradeoffs`, which the choice gate shows, the `default`
   that keeps today's behaviour, its component `metric` and `promotion` with
   its `unit` and `threshold`; `issue` names its idea issue. A value that runs
   a role on another tier declares `agent_variants` (`suffix`, `tier`,
   `description`, `agents`), and every build then ships `<agent>-<suffix>`
   with the base agent's body.
2. Anchor it as switch `<id>` at each step it changes in its owning flows.
   The anchor names the switch reference that the other value follows and
   adds nothing else to the flow.
3. Write each non-default value's instructions in
   `skill-content/<skill>/references/switch-<switch>-<value>.md`, in the skill
   that the tasks needing them select. `task_inputs.py` binds the file only
   when the project's policy selects that value. Never link it from SKILL.md
   and never write one for the default value.
4. Keep the default path unchanged: with the switch at its default, flows,
   skills, agents, manifests and compiler outputs stay byte-identical. Extend
   `tools/tests/test_default_equivalence.py` when the switch touches a
   compiler, and test each value with every other switch at its default.
5. Inside a Delivery, read the value with
   `process_policy.py value --switch <id> --delivery DLV-###`, which refuses a
   Delivery whose pinned policy has drifted.

The `process_switches` validator check rejects a default outside the values,
a switch that an owning flow does not name, a flow that names an undeclared
switch or one it does not own, a switch without a metric or promotion rule,
malformed agent variants, and a switch reference that names an undeclared
switch or value or the default, that no owning flow names, or that a SKILL.md
links.

### Promotion rule

A default flips only when the switch's component metric meets its threshold
over its promotion unit, at least 3 Deliveries unless the switch declares
another unit; when every shared quality guard holds: valid critical or major
findings found after an approval, Item reopens, code review cycles and QA
rounds per Item, Delivery Review deviations and defects reported after merge;
and when the owner approves the flip. One default flips per release, so the
following Deliveries attribute a change to one flip. A flipped default that
later breaks a quality guard flips back in the next release. A flip changes
`default`, keeps every value, moves the promoted value's instructions into
the default path and moves the previous default's path into its own switch
reference, so a project that chose either value explicitly keeps it.

## Review panels

`plugins/software-engineering-team/skill-content/challenge-review/data/review-panels.json`
declares each review step's reader role, its lenses (`id`, `focus` and, for a
step whose writer records a review note, the note sections each lens
`covers`) and `default_panel`, the list of lens assignments whose length is
the default panel size. A flow anchors its step as review panel `<step>`, and
prose names a lens as lens `<id>`. The `review_panels` validator check rejects
unknown readers, steps no flow anchors, anchors to undeclared steps, unknown,
duplicate or unassigned lens ids, empty panels or assignments, and review-note
sections that no lens or more than one owner covers. Adding a lens or a
review step needs only a data change plus its anchor.

Process switch `review_panels` selects between each step's single reviewer,
the default `single_reader`, and its review panel, `lens_panel`. Every flow
that anchors a review panel names the switch, and the validator rejects one
that does not. The panel instructions live in
`challenge-review/references/switch-review_panels-lens_panel.md`, which a
task binds only at `lens_panel`, so the single-reviewer path keeps its
released instructions. The switch's `agent_variants` make every build ship
`backlog-reviewer-lens`, `solution-reviewer-lens` and
`design-system-reviewer-lens` on the `lens` tier; the reviewers themselves
keep their own tier, and the validator requires a variant for every
read-only panel reader. The switch's flip rule is the owner's condition for
#312: at least 5 panel passes across at least 2 flows, panel valid-major
recall at least equal to the official review's, and panel wall time at most
50% of the official one.

## Mechanical passes

Process switch `mechanical_pass_tier` selects who runs the writer side of
backlog, Operation contract and Solution Design reviews. At `role_tier`, the
default, every writer pass runs on its role's own tier. At `mechanical`, a
pass whose returned findings each name their exact fix (`apply_findings`)
runs on the owning writer's `-mechanical` variant, and `render`, `stamp` and
`check` steps run as direct entry commands with no role pass. The pass kinds,
their eligibility and the frozen-task A/B that sets the tier's host values
live in
`challenge-review/references/switch-mechanical_pass_tier-mechanical.md`.
The switch's `agent_variants` make every build ship `product-owner-mechanical`,
`qa-engineer-mechanical`, `devops-engineer-mechanical` and
`solution-architect-mechanical` on the `mechanical` tier; each keeps its
writer's body, boundaries and identity, so writer ownership is unchanged. The
validator requires the switch to declare these variants and rejects one for a
read-only reviewer or challenger, so every review, re-check and calibration
keeps its tier under both values.
