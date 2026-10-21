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

## Artifact policy

Unless a subtree explicitly declares a closed artifact contract, files below
an `artifacts/` directory under a policy-valid vault folder are opaque local
artifacts, not Markdown notes. Names and extensions are unconstrained,
symlinks are rejected, and authored Markdown may link or embed a real local
artifact.

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
evidence for exactly that candidate; pull request CI runs every test on Linux,
and `check --full` runs them locally on request. Verify again immediately before commit. `make check` remains the
exhaustive sequential oracle; local receipts never replace required remote
platform, installation or release checks.

## Execution profiles

Canonical agents declare only a host-neutral `model_class`, one of the
purpose classes `tools/data/models.json` declares: `mechanical`,
`routine_authoring`, `complex_authoring`, `routine_review`,
`critical_decision`, `security_review` and `orchestration`. A class that
escalates has a second slot, `<class>_escalated`, which a task of size `l`,
rigor `high` or a declared risk flag (`auth`, `secrets`, `data_placement`,
`migration`, `irreversible`) runs; `security_review` and `orchestration`
never move. Every step that spawns an agent resolves its spawn through
`scripts/model_class.py resolve`, by the role's class or the class the step
names.

Each host keeps two tables under `platforms/<host>/`:

- `model-catalog.json` pins every model the package may run, keyed by its
  exact model ID: its `family`, the `efforts` the model supports on that host
  (empty when it takes none), the oldest host CLI release that runs it as a
  role's model (`min_cli_version`), the official `sources` that document
  them, and the date they were `verified`.
- `execution-profiles.json`, profile `auto`, maps every class slot to a
  `model` of the catalog and, optionally, an `effort`.

Model IDs are the only names: a model bump renames the catalog entry and
every slot that names it. The builder and `tools/validate.py` refuse a slot
whose model the catalog lacks, an effort outside its model's supported set
or the host's vocabulary, and a catalog ID outside the host adapter's
documented format or its entry's family, such as a Claude alias. They also
refuse a CI host CLI in `tools/data/host-cli-versions.json` older than any
catalog model's `min_cli_version`: the host gates install that version and
start no role, so a lower pin would pass on a CLI whose roles cannot run
their model. Model names stay out of `plugins/`. Claude agents receive the
slot's model ID as `model:` and, when the slot sets one, `effort:`; an
omitted `effort` follows the session. Codex dist agents carry the resolved
`model` and `model_reasoning_effort`, which setup renders into
`.codex/agents/`.

The package defaults follow the owner's decision of 16 Oct 2026: small
models for mechanical work, large ones only for critical decisions, security
and data-loss review and large build work. On Claude Code `mechanical` runs
Haiku 5.5 at `high`, `routine_authoring` Sonnet 5.5 at `high`,
`routine_review` Sonnet 5.5 at `medium`, `complex_authoring` Sonnet 5.5 at
`xhigh`, `critical_decision` and `security_review` Opus 5.5 at `high` and
`orchestration` Sonnet 5.5 at `medium`; each escalated slot moves one effort
or model up. The 21 Oct 2026 cost decision lowered routine and security review
one effort each, because in one measured project the orchestrating session on
the top model was over half the delivery cost: `deliver` runs in a fresh
session started on Sonnet, one per task.
Haiku 5.5 takes `low` to `max` and needs Claude Code 2.1.293
([models](https://platform.claude.com/docs/en/about-claude/models/overview),
[model config](https://code.claude.com/docs/en/model-config)); its `haiku`
alias still resolves to Haiku 4.5 on Bedrock, Vertex and Foundry. On Codex
`mechanical` runs GPT-6 Luna at `high` and every other slot GPT-6.1 Sol,
from `low` for `orchestration` to `high` for `security_review` and
`xhigh` for the escalated authoring and decision slots. Every slot
pins an effort, because a Claude Code subagent without `effort` follows the
session's level, `max` included
([subagents](https://code.claude.com/docs/en/sub-agents)). No slot defaults
to `max` or `ultra`: each host's effort policy asks the owner to confirm
`max` with its cost evidence, and a Codex role at `ultra` delegates to
further subagents.

A Claude Code spawn passes the `model` alias and `effort` the resolver
returns, which override the role file's pins for that spawn (a per-spawn
`effort` needs Claude Code 2.1.292). A Codex role file's `model` wins over a
spawn request, so a Codex role always runs its own class pins and the
resolver returns a note instead of another agent.
Rendered Claude role agents also set `omitClaudeMd: true` and the
`disallowedTools` of `platforms/claude/agent-tools.json`; the subagents page
documents both keys and the `mcp__<server>` and `mcp__*` patterns.

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
model a role is pinned to, the project's `class_models` included: `available`
when the list holds it and reflects the signed-in account, `unavailable`
when the list does not hold it, since that binary cannot run it, and
`unverified` when no list could be read or the list reflects no account.
Every probe runs within a time limit, and a failure gives `unverified`,
never an error that stops setup. The roles of an `unavailable` model render
on the session's model at their own effort, and a warning names the model,
its class slots and its roles; their files record the verdict, so `check` and
`inspect` reproduce it, and every setup or refresh judges the model again.
An `unverified` model keeps its pin with a note, and the run-time rule
covers it. The slot effort of a project model outside the catalog is
checked against the levels the list names for it, with a warning when the
list does not name it. At run time a failed role gets a warning that never
blocks work and at most one start again on the session's model, only when
the failed run changed nothing.

On Claude Code the binary is `CLAUDE_CODE_EXECPATH` or the executable of
`CLAUDE_PID`, else `claude` on PATH when its version meets the highest
`min_cli_version` of the catalog models the class slots run, which the class
map ships. The list is the `models` of its reply to one `initialize` control
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
with Claude Code's own warning. For a spawn whose pinned model Claude Code
names as not found, refused or too new, the host contract has the coordinator
tell the user and, when the failed run changed nothing, spawn the same role
once more with `model` set to the session's family alias, and stop if that
spawn fails too; a writer that already changed files is never spawned again.

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
run-time rule is the host contract's: when a spawned role ends in `Agent
errored: ...` naming its pinned model and the failed run changed nothing, the
coordinator applies `--execution-profile inherit` and starts the role once
more; `--execution-profile auto` restores the pins and checks them
again.

A project may override the package per class slot and per role, never the
catalog. Every build ships `templates/model-classes.json`, which names each
role's class, and per host each slot's pinned
model, package effort and the efforts its model takes, the host's model
catalog with the efforts each model takes, the host's effort vocabulary and the shape of a model ID of
its list (`MODEL_ID_SHAPE` in the host adapter, which the builder checks
against every catalog ID). Each host's `platforms/<host>/effort-policy.json`
names the efforts that need the owner's confirmation, with the vendors' cost
evidence the confirmation question states (`max` on both hosts), and the
efforts no slot may run at, with the reason (Codex `ultra`, which starts
subagents of its own); the `effort_policy` check keeps both within the
host's vocabulary and refuses a package pin at a refused effort.
`workspace/config.json` holds only overrides. Setup writes nothing into the
config: the owner sets `class_models`, per host and slot a `model`, an ID of
the host's model list or `session`, and an `effort`, and `role_classes`,
which moves any role to another class on every host. The retired
`tier_models` and `role_tiers` fail the check, and setup drops them.
`role_settings.py` resolves both for the config writer and the generators: a
role's class from `role_classes`, else the package, then that slot's model
and effort from `class_models`, else the package profile, a missing key keeping
the package value. A catalog model takes its catalog efforts, the session's
model any effort the catalog knows, and any other ID, checked only for its
shape until setup reads the host's own model list, the host's vocabulary.
`/configure models` writes through `project_config.py set-class`, which
writes a confirmed effort only with `--confirmed` and records nothing equal
to the package value, so a later package change reaches the project, and
`set-role-class`; like `set`, neither creates a missing config or writes into
one whose fields, schema, team or languages `check` refuses, since setup
writes the config. `project_config.py classes` prints the config-level
effective map, and with `--model-list` each slot's model choices, read from
the active host's own list through its `host_models` adapter when the
package ships one, without a model the host's own picker hides, such as a
Codex catalog entry whose `visibility` is `hide` or `none`, which setup
still judges like every model of the list. Setup keeps the overrides and
stops on one the installed package no longer takes, which `--default`
removes even for a slot, host or role the package no longer has. On Claude
Code setup renders every role into
`.claude/agents/software-engineering-team-<role>.md` with its resolved model
and effort, `session` as `model: inherit`, since a project agent's name
cannot contain the `:` of a plugin's scoped name, and the host contract
spawns it before the plugin's `software-engineering-team:<role>`; the
fallback hook pins it by its rendered file, or by the class map when the
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
session's model at its own class effort:

- Codex: `generate_codex_project.py apply --project-root <root> --scope local
  --execution-profile inherit` omits `model` from every role file and keeps
  `model_reasoning_effort`, because a role without it runs the parent's
  effort, `low` by default for Sol in Codex 0.159. The managed files record
  the choice, later refreshes keep it, and `--execution-profile auto`
  restores the default. It is a local setup flag, not a
  `workspace/config.json` field: the profile is a personal host choice that
  changes only the ignored projection, while a project's class models and
  efforts live in `class_models`.
- Claude Code: a plugin cannot switch frontmatter per user, and
  `${user_config.*}` is substituted only in the agent body. The documented
  setting `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (Claude Code 2.1.257 or later),
  in the `env` block of a project's settings or of the user's settings, runs
  every role on the main conversation's model, including its exact version
  and context window. A frontmatter `effort` overrides the session level but
  not `CLAUDE_CODE_EFFORT_LEVEL`, which pins one level for the session. Both
  reach every subagent in the session, not only this team. Claude Code's
  permission modes do not select a model or effort.

## Process choices

A process behaviour change ships as the new default. A choice a project may
make differently is one optional key of `workspace/config.json`, added to
`project_config.py` (`CONFIG_FIELDS`, `ORDINARY_FIELDS` and its value check),
kept by setup when present and changed through `/configure`. Today the only
one is `pr_proof`.
