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

Each host keeps two tables under `platforms/<host>/`:

- `model-catalog.json` pins every model class, such as `frontier`, `strong`
  or `fast`, to one exact model ID: its `family`, its `id`, the `efforts` the
  model supports on that host (empty when it takes none), the oldest host CLI
  release that runs it as a role's model (`min_cli_version`), the official
  `sources` that document them, and the date they were `verified`.
- `execution-profiles.json`, profile `auto`, maps every tier to a `class`
  and, optionally, an `effort`; the `inherit` tier maps to nothing.

A model bump therefore edits one class, and every tier on it follows. The
builder and `tools/validate.py` refuse a tier without a known class, an
effort outside its class's supported set or the host's vocabulary, and an ID
outside the host adapter's documented format or the class's family, such as
a Claude alias. They also refuse a CI host CLI in
`tools/data/host-cli-versions.json` older than any class's `min_cli_version`:
the host gates install that version and start no role, so a lower pin would
pass on a CLI whose roles cannot run their model. Model names stay out of
`plugins/`. Claude agents receive the class's ID as `model:` and, when the
tier sets one, `effort:`; an omitted `effort` follows the session. Codex dist
agents carry the resolved `model` and `model_reasoning_effort`, which setup
renders into `.codex/agents/`.

The Codex defaults follow OpenAI's subagent guidance. Demanding roles run the
`strong` class (Sol): the high tier at `xhigh`, the depth a main session at
`ultra` reasons at per request, and the medium tier at `medium`. The low and
mechanical tiers run the `fast` class (Luna) at `high`, OpenAI's starting
point for Luna. Lens readers stay on the `strong` class at `high` because
panel recall already trails the official reviewer's; their speed comes from
running in parallel, not from a weaker model. No tier uses `ultra`, which
delegates to further subagents.

Pinning trades automatic upgrades for control. A Claude Code family alias
such as `opus` runs on the main conversation's exact model, including its
`[1m]` context window, when that model belongs to the family; it resolves to
a version each provider serves; and an organization allowlist that blocks it
substitutes the newest permitted version of the family. A pinned ID does none
of that: the role keeps its version and window, an allowlist that blocks the
ID runs the role on the main conversation's model, a provider that does not
serve the ID fails the request unless a fallback model chain covers it, and
each model needs a Claude Code version that knows it. On Codex, where roles
named no model before, a pinned model needs an account, a workspace and a
Codex version that offer it. `tools/model_drift.py` finds newer models of the
pinned families in each host's own catalog, and the model catalog bump in
[the maintainer protocol](maintainer-operations-protocol.md) turns one into
a reviewed pin.

`inherit` is the user override that makes every role follow the parent
session's model and effort:

- Codex: `generate_codex_project.py apply --project-root <root> --scope local
  --execution-profile inherit` omits both keys from every role file. The
  managed files record the choice, later refreshes keep it, and
  `--execution-profile auto` restores the default. It is also the fallback
  when the account, the workspace or the Codex version lacks a pinned model.
  It is a local setup flag, not a `workspace/config.json` field: the closed
  config refuses performance knobs, and the profile is a personal host choice
  that changes only the ignored projection.
- Claude Code: a plugin cannot switch frontmatter per user, and
  `${user_config.*}` is substituted only in the agent body. The documented
  setting `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (Claude Code 2.1.257 or later),
  in the `env` block of a project's settings or of the user's settings, runs
  every role on the main conversation's model, including its exact version
  and context window. A frontmatter `effort` overrides the session level but
  not `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the session. Both
  reach every subagent in the session, not only this team. Claude Code's
  permission modes do not select a model or effort.

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
   with the base agent's body. A value that needs owner-set numbers declares
   `parameters` (`summary`, the `values` that take them, `declared_by` naming
   the package data file and key that declare their ids, `type` and
   `min_count`). The package sets no parameter value: the owner sets each one
   in the policy's Parameters table through `/configure process`, a parameter
   exists only while its switch is at a value that takes it, and
   `process_policy.py value` reports the typed values beside the switch's. A
   value whose instructions read package data that no default path reads
   declares it in `value_data`, a list of
   `skill-content/<skill>/data/<file>.json` paths per value; a task binds that
   data only together with the value's switch references. Data that several
   values read, of one switch or of several, is listed under each of them and
   bound when any of them is chosen. A switch whose instructions every task of
   its owning flows follows, whichever skills the task selects, declares
   `reference_scope: owning_flows`, as `owner_gates` does.
2. Anchor it as switch `<id>` at each step it changes in its owning flows.
   The anchor names the switch reference that the other value follows and
   adds nothing else to the flow.
3. Write each non-default value's instructions in
   `skill-content/<skill>/references/switch-<switch>-<value>.md`, in the skill
   that the tasks needing them select. `task_inputs.py` binds the file only
   when the project's policy selects that value, and only for a task whose
   entry runs one of the switch's owning flows. Never link it from SKILL.md
   and never write one for the default value.
4. Keep the default path unchanged: with the switch at its default, its only
   trace in a flow is the anchor of step 2, and skills, agents, manifests and
   compiler outputs stay byte-identical.
   `tools/tests/test_default_equivalence.py` compares every compiler output
   and every shipped task's bound paths on frozen inputs with the release base
   e47dbe0 and fails on any difference its `EXPECTED_DIFFERENCES` list does not
   name with the issue whose fix made it. Extend it when the switch touches a
   compiler, and test each value with every other switch at its default.
5. Inside a Delivery, read the value with
   `process_policy.py value --switch <id> --delivery DLV-###` and derive its
   tasks with `task_inputs.py --delivery DLV-###`; both follow the values the
   Delivery pinned, and until its Review they refuse an approved policy that
   changed one of them.

The `process_switches` validator check rejects a default outside the values,
a switch that an owning flow does not name, a flow that names an undeclared
switch or one it does not own, a switch without a metric or promotion rule,
malformed agent variants or parameters, a `reference_scope` other than
`owning_flows`, value data that is missing, listed twice for one value,
declared for the default or bound by no reference of its value, and a switch
reference that names an undeclared switch or value or the default, that no
owning flow names, or that a SKILL.md links.

### Promotion rule

A default flips only when the switch's component metric meets its threshold
over its promotion unit; when every shared quality guard holds: valid critical
or major findings found after an approval, Item reopens, code review cycles
and QA rounds per Item, Delivery Review deviations and defects reported after
merge; and when the owner approves the flip. The unit is at least 3
Deliveries. Another unit needs the switch's idea issue or an owner decision to
declare it: `review_manifest_scope` counts 5 epic review passes across 2
backlog revisions because the owner confirmed that unit on 1 Oct 2026, the
switch acting only in backlog planning, outside any Delivery. One default
flips per release, so the following Deliveries attribute a change to one flip.
A flipped default that later breaks a quality guard flips back in the next
release. A flip changes `default`, keeps every value, moves the promoted
value's instructions into the default path and moves the previous default's
path into its own switch reference.

Only a non-default choice survives a flip. The Process Policy keeps a row only
for a value other than the default, so a project whose row names the promoted
value keeps it, now as the default, while every switch without a row follows
the promoted default, also one whose owner answered the previous default at
the choice gate. A project that wants the previous default back sets it
through `/configure process`, where it is then a non-default choice.

A value that takes owner-set parameters stays non-default, since only a value
the owner chooses takes parameters. Its promotion ships package limits
instead: the switch's `parameters` gain `package_limits`, one positive value
per declared parameter id it covers, which a project at that value applies to
each parameter its Parameters table leaves unset and which count towards
`min_count`. `process_policy.py` and the validator accept that form, and
`story_size_budget`'s promotion text says so.

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
`check` steps run as direct entry commands with no role pass.
`templates/task-input-policy.json` declares the pass kinds under `pass_kinds`,
with the documents each writer's `apply_findings` pass may change, and
`task_inputs.py --pass-kind` refuses a mechanical kind for a review, re-check,
calibration, triage or repair task, for a role without a writer variant and
outside that value, binds the verdict's findings with their exact repairs and
narrows the write scope to the owning writer's documents among the inputs.
Their eligibility and the frozen-task A/B that sets the tier's host values
live in
`challenge-review/references/switch-mechanical_pass_tier-mechanical.md`.
The switch's `agent_variants` make every build ship `product-owner-mechanical`,
`qa-engineer-mechanical`, `devops-engineer-mechanical` and
`solution-architect-mechanical` on the `mechanical` tier; each keeps its
writer's body, boundaries and identity, so writer ownership is unchanged. The
validator requires a switch value to declare every variant its switch
reference names, so a build never stops shipping a variant a pass spawns, and
rejects a mechanical variant for a read-only reviewer or challenger, so every
review, re-check and calibration keeps its tier under both values.

What a variant changes depends on the host's tables. On Claude the
`mechanical` tier, Sonnet at effort `high`, is a lower model only for
`solution-architect`, which runs Opus; `product-owner`, `qa-engineer` and
`devops-engineer` already run Sonnet at the session's effort, so their
variant is lower only when the session runs above effort `high`. On Codex
every variant moves its writer from Sol to Luna. These values are
placeholders until the tier's frozen-task A/B sets them.

## Story size budget

`plugins/software-engineering-team/skill-content/product-planning/data/story-size-measures.json`
declares each story size measure: its `summary` and the `derivation` that the
backlog compiler implements in `STORY_SIZE_DERIVATIONS`. The
`story_size_measures` validator check rejects a measure whose derivation the
compiler does not implement, a derivation two measures share and a malformed
measure. Process switch `story_size_budget` declares its parameters
`declared_by` that file, so a new measure is a data change plus its
derivation, and the owner can set a limit for it at once.

At `off`, the default, nothing is measured or shown. At `propose_split`,
`backlog_compile.py check --json`, the review manifests and
`delivery_compile.py init` report each story's measures against the owner's
limits, and the Product Owner proposes a split for a story over budget before
its epic review, as
`product-planning/references/switch-story_size_budget-propose_split.md`
defines. The budget is advisory and adds no story field. The switch's flip
rule: at least 3 backlog revisions and 3 Deliveries, the owner accepting at
least half of the split proposals, and stories within budget reaching
integration with a median cycle time at most half that of stories over budget.
