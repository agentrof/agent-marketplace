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

After an approved source handoff, synchronize the local checkout in a separate
direct shell call using the resolved absolute Git executable from the checkout
root. The Experience guard attests only `merge --no-edit <committed-source>` or
`restore --source=<committed-source> --worktree -- workspace/docs/experience-design`.
Fetch required target and Fence objects separately before synchronization. The immutable source
must contain the current remote target and its exact Experience tree, and the
remote protocol-2 Fence must be open and target that same commit. A local commit
alone does not establish this authority. Wrapped scripts, mixed shell commands,
bare executable names, PowerShell and unknown shell identities receive no Git
writer exemption. Native cmd commands retain canonical quoting requirements.

Synchronization verifies exact committed protected bytes and the owning
application gate, preserves existing local history and the restore's index,
and publishes the accepted postimage through the same serialized authority as
compiler writers. A shell reader that began earlier then recognizes that
postimage instead of restoring its older snapshot. Authoring new approval
receipts continues through the owning compiler lifecycle.

Protected writers acquire their lock before reading the recovery preimage.
Every snapshot captures and rechecks the authority generation around its tree;
a concurrent transition rejects capture before any capsule is published.
Every accepted publication advances that transient generation even when its
content is unchanged. Durable approval receipts and revisions are unaffected.

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
It always runs static checks, then the change's own tests or valid local
evidence for exactly that candidate; pull request CI runs every test on Linux
and macOS, and `check --full` runs them locally on request. Verify again immediately before commit. `make check` remains the
exhaustive sequential oracle; local receipts never replace required remote
platform, installation or release checks.

## Execution profiles

Canonical agents declare only a host-neutral `reasoning` tier from
`tools/data/models.json`: one of the three role tiers `high`, `medium` and
`low`, or `inherit`. Tier names are single words because each is both a
kebab-case reasoning level and a snake_case key in the profile tables. A
process switch value may declare generated agent variants on another tier;
every variant the package ships, the review-panel readers and the writers'
fix passes alike, runs on `low` (see Process switches).

Each host keeps two tables under `platforms/<host>/`:

- `model-catalog.json` pins every model the package may run, keyed by its
  exact model ID: its `family`, the `efforts` the model supports on that host
  (empty when it takes none), the oldest host CLI release that runs it as a
  role's model (`min_cli_version`), the official `sources` that document
  them, and the date they were `verified`.
- `execution-profiles.json`, profile `auto`, maps every tier to a `model` of
  the catalog and, optionally, an `effort`; the `inherit` tier maps to
  nothing.

Model IDs are the only names: a model bump renames the catalog entry and
every tier that names it. The builder and `tools/validate.py` refuse a tier
whose model the catalog lacks, an effort outside its model's supported set
or the host's vocabulary, and a catalog ID outside the host adapter's
documented format or its entry's family, such as a Claude alias. They also
refuse a CI host CLI in `tools/data/host-cli-versions.json` older than any
catalog model's `min_cli_version`: the host gates install that version and
start no role, so a lower pin would pass on a CLI whose roles cannot run
their model. Model names stay out of `plugins/`. Claude agents receive the
tier's model ID as `model:` and, when the tier sets one, `effort:`; an
omitted `effort` follows the session. Codex dist agents carry the resolved
`model` and `model_reasoning_effort`, which setup renders into
`.codex/agents/`.

The package defaults are the owner's decision of 1 Oct 2026 on #349. On
Claude Code the high tier runs Opus 5.5 at `xhigh`, the medium tier Opus 5.5
at `medium` and the low tier Sonnet 5.5 at `high`. Anthropic meant `xhigh`
for long-running agentic and coding work
([effort](https://platform.claude.com/docs/en/build-with-claude/effort)),
and on its own charts Opus 5.5 scores 66.4% on Terminal-Bench 4.0 at `xhigh`
against 64.2% at `high` and 64.8% at `max`, and 1820 against 1692 on
GDPval-AA at `high` ([Opus 5.5](https://www.anthropic.com/claude-opus-5-5)).
Opus 5.5 at `medium` is Anthropic's starting point for most agent workloads
([cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence))
and scores 54.6% on FrontierCode, above its 51.4% at `xhigh`. Anthropic
starts Sonnet 5.5 at `medium` for well-specified tasks and moves it to
`high` for harder or longer ones, since at `low` and `medium` it more often
stops to check in before a long task is done
([Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5));
at `high` it scores 49.4% on FrontierCode against 36.5% at `medium`
([Sonnet 5.5 results](https://www.anthropic.com/claude-sonnet-5-5)). On
Codex every tier runs GPT-6.1 Sol at `xhigh`: on the Artificial Analysis
Coding Agent Index v1.5
([coding agents](https://artificialanalysis.ai/agents/coding-agents), read 1
Oct 2026) Codex with GPT-6.1 Sol at `xhigh` scores 63 at 15.5 minutes and
$1.04 per task, where Claude Code with Opus 5.5 at `max` scores 66 at 1.1
hours and $13.0 and Claude Code with Sonnet 5.5 at `max` scores 68 at 1.5
hours and $14.2. Every generated variant, the four `-lens` readers and the
four `-mechanical` writers, runs on the `low` tier and so keeps the values
the owner chose for it. No canonical role runs the low tier, which on Codex
stays at Sol `xhigh`, so a `-mechanical` fix pass, such as one that rewrote a
single numeric test in about 16 minutes in one measured project, still
generates at the writers' effort. On OpenAI's own charts Sol scores 75.2% on
DeepSWE v1.1 at `high`, above its 71.9% at `max`
([GPT-6.1 Sol](https://openai.com/index/introducing-gpt-6-1-sol/)), but
lowering the Codex low tier waits for the frozen-task A/B of #404 on
recorded fix passes. Every tier pins an effort, because a Claude Code
subagent without `effort` follows the session's level, `max` included
([subagents](https://code.claude.com/docs/en/sub-agents)). No tier defaults
to `max` or `ultra`: each host's effort policy asks the owner to confirm
`max` with its cost evidence, and a Codex role at `ultra` delegates to
further subagents. Each catalog keeps a model no tier runs, Haiku 4.5 on
Claude Code and Luna on Codex.

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
Codex version that offer it.

A role whose pinned model cannot run falls back to the session's model with
a visible warning, by one strategy on both hosts. Setup and refresh read the
host's own model list from the binary that runs the session, through the
package's `scripts/host_models.py`, which starts no model, and judge each
model a role is pinned to, the project's `tier_models` included: `available`
when the list holds it and reflects the signed-in account, `unavailable`
when the list does not hold it, since that binary cannot run it, and
`unverified` when no list could be read or the list reflects no account.
Every probe runs within a time limit, and a failure gives `unverified`,
never an error that stops setup. The roles of an `unavailable` model render
on the session's model at their own effort, and a warning names the model,
its tiers and its roles; their files record the verdict, so `check` and
`inspect` reproduce it, and every setup or refresh judges the model again.
An `unverified` model keeps its pin with a note, and the run-time rule
covers it. The tier effort of a project model outside the catalog is
checked against the levels the list names for it, with a warning when the
list does not name it. At run time a failed role gets a warning that never
blocks work and at most one start again on the session's model, only when
the failed run changed nothing.

On Claude Code the binary is `CLAUDE_CODE_EXECPATH` or the executable of
`CLAUDE_PID`, else `claude` on PATH when its version meets the highest
`min_cli_version` of the catalog models the tiers run, which the tier map
ships. The list is the `models` of its reply to one `initialize` control
request, sent with every hook off and no user message, and it reflects the
account only for a claude.ai login. The request loads no MCP server
(`--strict-mcp-config` without `--mcp-config`): `claude -p` would otherwise
start the project's `.mcp.json` servers without the approval an interactive
session asks for, and every user and plugin server. Claude Code refuses the
flag while an organization's managed MCP config exists, so there every pin
stays `unverified`. A rendered role on the session's model
carries `model: inherit` and keeps its `effort`, and its stamp keeps the
settings the project config resolves, so the session start check finds it
current. A model policy already substitutes the main conversation's model
with Claude Code's own warning. For a model that Claude Code's wording names
as not found, refused or too new for the installed Claude Code, the host
contract has the coordinator tell the user and, when the failed run changed
nothing, that is its task is read-only or the `task_inputs.py` invocation
that derived its manifest still passes with `--expected-hash`, which compares
the content of its inputs and of every modified or new source it covers,
spawn the same role once more with `model` set to the session's family alias,
which lands on the session's exact model, and stop if that spawn fails too; a
writer that already changed files is never spawned again on a partly changed
tree. `git status` cannot judge that: a further edit to an already modified
file, or a new file in an untracked folder, leaves its output as it was, and
a flow that commits only at its end runs its fix passes on modified files.
The manifest every entry derives before it delegates a role is the record of
the tree before the run, so no extra step precedes a spawn. The Claude plugin's
hook on `Agent`, `model_fallback.py` under `PostToolUseFailure` and
`PostToolUse`, adds the warning and that instruction to a foreground failure
and warns when a role started on another model than its pin. It matches only
Claude Code's model wording, since a subagent's error detail names the pin
for any API error, and stays silent for a spawn that passes `model` and under
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, so a model the user chose never triggers
it and the re-spawn never repeats.

On Codex the binary is the nearest `codex` process above setup, since Codex
exports no variable that names it, else the target of the `apply_patch`
alias on PATH, `CODEX_CLI_PATH` when the environment carries it, `codex` on
PATH or a CLI that an installed app bundles, of `CODEX_VERSION` when that is
set. The list is the account catalog Codex caches in `models_cache.json`
when the cache comes from that binary's version and is at most 24 hours old,
otherwise the output of `codex debug models`, which reflects no account
inside the command sandbox or when it equals `codex debug models --bundled`.
The role files of an `unavailable` model omit `model` and keep
`model_reasoning_effort`. Codex has no hook event for a failed role, so the
run-time warning comes from the host contract: when a spawned role still
ends in `Agent errored: ...` that names its pinned model as unknown,
unsupported, not found or not supported with the account, the coordinator
tells the user and, when the failed run changed nothing, runs
`generate_codex_project.py apply --scope local --inherit-model <model>` and
starts the same role again. That run-time record stays until
`--restore-model <model>` or `--execution-profile auto`. When that start
errors too, the coordinator runs it with `--restore-model <model>`, which
renders the pin again, before it stops, so no run-time record stays that no
successful start backs. A rate limit or another error that names the model
moves no role. `--execution-profile auto` restores the pins and checks them
again.

A project may override the package per tier and per role, never the
catalog. Every build ships `templates/tier-map.json`, which names each
role's tier, generated variants included, and per host each tier's pinned
model, package effort and the efforts its model takes, the host's model
catalog with the efforts each model takes, the host's effort vocabulary and the shape of a model ID of
its list (`MODEL_ID_SHAPE` in the host adapter, which the builder checks
against every catalog ID). Each host's `platforms/<host>/effort-policy.json`
names the efforts that need the owner's confirmation, with the vendors' cost
evidence the confirmation question states (`max` on both hosts), and the
efforts no tier may run at, with the reason (Codex `ultra`, which starts
subagents of its own); the `effort_policy` check keeps both within the
host's vocabulary and refuses a package pin at a refused effort.
`workspace/config.json` holds only overrides. Setup writes nothing into the
config: the owner sets `tier_models`, per host and tier a `model`, an ID of
the host's model list or `session`, and an `effort`, and `role_tiers`, which
moves any role between the high, medium and low tiers on every host.
`role_settings.py` resolves both for the config writer and the generators: a
role's tier from `role_tiers`, else the package, then that tier's model and
effort from `tier_models`, else the package profile, a missing key keeping
the package value. A catalog model takes its catalog efforts, the session's
model any effort the catalog knows, and any other ID, checked only for its
shape until setup reads the host's own model list, the host's vocabulary.
`/configure models` writes through `project_config.py set-tier`, which
writes a confirmed effort only with `--confirmed` and records nothing equal
to the package value, so a later package change reaches the project, and
`set-role-tier`; like `set`, neither creates a missing config or writes into
one whose fields, schema, team or languages `check` refuses, since setup
writes the config. `project_config.py tiers` prints the config-level
effective map, and with `--model-list` each tier's model choices, read from
the active host's own list through its `host_models` adapter when the
package ships one, without a model the host's own picker hides, such as a
Codex catalog entry whose `visibility` is `hide` or `none`, which setup
still judges like every model of the list. Setup keeps the overrides and
stops on one the installed package no longer takes, which `--default`
removes even for a tier, host or role the package no longer has. On Claude
Code setup renders every role into
`.claude/agents/software-engineering-team-<role>.md` with its resolved model
and effort, `session` as `model: inherit`, since a project agent's name
cannot contain the `:` of a plugin's scoped name, and the host contract
spawns it before the plugin's `software-engineering-team:<role>`; the
fallback hook pins it by its rendered file, or by the tier map when the
project holds none. Each rendered file's header stamps the package
version, the digest of its source agent and the digest of its resolved model
and effort. At session start `team_guard.py register` reports rendered files
whose stamp does not match the installed package or the project's config, as
after a plugin update or a config change not yet rendered, tells the user to
run setup or a refresh, and has the session spawn the plugin's roles until
then. `CLAUDE_CODE_EFFORT_LEVEL`
still overrides that `effort` and `maxEffortLevel` caps it. On Codex setup
renders the resolved model and effort into the role files, `session`
without a `model` key, and keeps the effort under every profile and model
fallback. The last header line of each role file carries the same stamp,
and `team_guard.py register` reports a role file whose stamp does not match
and tells the user to run setup: Codex has no other identity of a role to
start instead, and it reads a role file each time it starts the role, so
the render setup does applies at the next start. Generated agent files are
never edited by hand.

`tools/model_drift.py` finds newer models of the pinned families in each
host's own catalog, and the model catalog bump in
[the maintainer protocol](maintainer-operations-protocol.md) turns one into
a reviewed pin.

`inherit` is the user override that makes every role follow the parent
session's model at its own tier effort:

- Codex: `generate_codex_project.py apply --project-root <root> --scope local
  --execution-profile inherit` omits `model` from every role file and keeps
  `model_reasoning_effort`, because a role without it runs the parent's
  effort, `low` by default for Sol in Codex 0.159. The managed files record
  the choice, later refreshes keep it, and `--execution-profile auto`
  restores the default. It is a local setup flag, not a
  `workspace/config.json` field: the profile is a personal host choice that
  changes only the ignored projection, while a project's tier models and
  efforts live in `tier_models`.
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
owning flow names, or that a SKILL.md links. The `finding_code_references`
check rejects a Delivery finding code that authored Markdown names, the host
contracts and overlays under `platforms/` included, but the Delivery result
contract does not declare, so a switch reference, flow or host contract that
tells a role which refusal to expect names one the result envelope carries.

### Promotion rule

A default flips only when the switch's component metric meets its threshold
over its promotion unit; when every shared quality guard holds: valid critical
or major findings found after an approval, Item reopens, code review cycles
and QA rounds per Item, Delivery Review deviations and defects reported after
merge; and when the owner approves the flip. The unit is at least 3
Deliveries. Another unit needs the switch's idea issue or an owner decision to
declare it: `review_manifest_scope` counts 5 epic review passes across 2
backlog revisions because the owner confirmed that unit on 1 Oct 2026, the
switch acting only in backlog planning, outside any Delivery, and
`code_review_panel` counts 5 code-review passes across 2 Deliveries, the
measurement criterion its idea issue #347 states. One default
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
`design-system-reviewer-lens` on the `low` tier; the reviewers themselves
keep their own tier, and the validator requires a variant for every
read-only panel reader. The switch's flip rule is the owner's condition for
#312: at least 5 panel passes across at least 2 flows, panel valid-major
recall at least equal to the official review's, and panel wall time at most
50% of the official one.

## Code review panel

Process switch `code_review_panel` selects who reads a Delivery Item's frozen
candidate in code review: the official code reviewer alone at the default,
`single_reader`, or at `beside_official` a lens panel beside it.
`plugins/software-engineering-team/skill-content/code-review/data/code-review-panel.json`
declares the panel in the review-panel shape: its one review step
`code_review`, the reader role, the lenses and the lens assignments, one
reader each. Only `beside_official` binds that data, as its `value_data`,
together with `code-review/references/switch-code_review_panel-beside_official.md`,
and the switch's `agent_variants` make every build ship `code-reviewer-lens`
on the `low` tier; `code-reviewer` keeps its own tier, as every calibration
reader does. `delivery_verification.py panel-result` registers the official
result and each lens result, `calibrate` rules every panel claim, and
`merge-panel` registers the one code review result with each finding's source
and the pass's panel record. The `code_review_panel` validator check rejects
data without the review-panel shape or with a step other than `code_review`, a
reader without the value's variant, a variant without a reader and data the
value does not bind. A new lens or a regrouped panel is a data change.

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
`solution-architect-mechanical` on the `low` tier; each keeps its
writer's body, boundaries and identity, so writer ownership is unchanged. The
validator requires a switch value to declare every variant its switch
reference names, so a build never stops shipping a variant a pass spawns, and
rejects a mechanical variant for a read-only reviewer or challenger, so every
review, re-check and calibration keeps its tier under both values.

What a variant changes depends on the host's tables. Every variant runs
on the `low` tier. On Claude Code that is Sonnet at effort `high`, which
moves every writer from Opus to Sonnet, above the `medium` of
`product-owner`, `qa-engineer` and `devops-engineer` and below the `xhigh`
of `solution-architect`. On Codex every tier runs Sol at `xhigh`, so a
variant keeps its writer's own model and effort and changes only the fresh
context of the pass; the coordinator still starts the variant for every
fix pass, and the pass record says which ran with its model and effort.
These values are placeholders until the variants' frozen-task A/B sets
them; lowering the Codex low tier waits for that A/B (#404).

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

`contract_deltas` counts an expected Operation contract revision only through
a story's optional `operation_impact: required|not_applicable` classification
with its `operation_reason`. The vault policy declares it under
`backlog_contract.optional_story_classifications`, `backlog_compile.py check`
validates it whenever a story carries it, and the `vault_policy_shape`
validator check requires each declared classification and its reason to be
vault-wide text properties. A story without it is unknown for it and counts no
Operation delta. Switch `delivery_path` at `light_when_eligible` reads
`required` as the failed condition `no_operation_impact`.
