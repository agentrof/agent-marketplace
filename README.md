# Agent Marketplace

A catalog of curated, end-to-end agent teams for Claude Code and native
Codex App/CLI. Install a complete team and run at the goal; this is not a
parts store.

The first team, Software Engineering Team (`software-engineering-team`), is
an orchestrated software and product development team: business analysis,
planning, architecture, design system, implementation, review, verification
and a containerized environment, driven through a small set of
user-invocable entry skills.

Every team runs on Project Management Office (`project-management-office`):
a shared operations backbone holding one central database for projects,
epics, stories, machine-generated tasks, work-order state, findings and audit
events, written only through its CLI and recorded deterministically via
hooks. Claude installs this backbone through the team dependency. Codex users
install PMO explicitly before a team because Codex does not currently resolve
plugin-to-plugin dependencies. The `control-tower` entry starts Control Tower
and replies with the running URL.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 2 | 12 | 13 | 14 |
<!-- counts:end -->

Counts above are injected by `tools/counts.py`; they are never written by
hand anywhere in this repository.

## Naming contract

| Product | Technical id | Visible name | Owner |
|---|---|---|---|
| Marketplace | `agent-marketplace` | Agent Marketplace | Agentrof |
| Operations plugin | `project-management-office` | Project Management Office | Agentrof |
| Team plugin | `software-engineering-team` | Software Engineering Team | Agentrof |

Agent role ids stay short and semantic. Claude adds the plugin namespace;
Codex uses the same bare id from the generated project agent. PMO stores the
role in snake_case:

| Visible role | Canonical and Codex id | Claude identity | PMO role |
|---|---|---|---|
| Analysis Challenger | `analysis-challenger` | `software-engineering-team:analysis-challenger` | `analysis_challenger` |
| Backend Developer | `backend-developer` | `software-engineering-team:backend-developer` | `backend_developer` |
| Business Analyst | `business-analyst` | `software-engineering-team:business-analyst` | `business_analyst` |
| Code Reviewer | `code-reviewer` | `software-engineering-team:code-reviewer` | `code_reviewer` |
| DevOps Engineer | `devops-engineer` | `software-engineering-team:devops-engineer` | `devops_engineer` |
| Domain Expert | `domain-expert` | `software-engineering-team:domain-expert` | `domain_expert` |
| Frontend Developer | `frontend-developer` | `software-engineering-team:frontend-developer` | `frontend_developer` |
| Product Owner | `product-owner` | `software-engineering-team:product-owner` | `product_owner` |
| QA Engineer | `qa-engineer` | `software-engineering-team:qa-engineer` | `qa_engineer` |
| Software Architect | `software-architect` | `software-engineering-team:software-architect` | `software_architect` |
| Solution Architect | `solution-architect` | `software-engineering-team:solution-architect` | `solution_architect` |
| UX Designer | `ux-designer` | `software-engineering-team:ux-designer` | `ux_designer` |

## Install on Claude Code

```
/plugin marketplace add agentrof/agent-marketplace
/plugin install software-engineering-team
```

The `project-management-office` backbone installs automatically with the team (a plugin
dependency); no separate step.
Every Claude team entry still requires PMO's ready session signal. A disabled
dependency or failed PMO hook bootstrap stops the entry without changing files
or project state and reports the recovery step.

Then, inside your project:

```
/software-engineering-team:setup
```

Setup bootstraps the project workspace, asks for anything it cannot
detect, and points you at the next step.

## Install on Codex App or CLI

```text
codex plugin marketplace add agentrof/agent-marketplace
codex plugin add project-management-office@agent-marketplace
codex plugin add software-engineering-team@agent-marketplace
```

Start a new task/session, open `/hooks`, inspect and trust Project Management
Office and Software Engineering Team, and start another task if Codex asks for
a reload. Then select
`software-engineering-team:setup` in the App skill picker or CLI `/skills`
picker (or invoke `$software-engineering-team:setup`). Setup generates the managed Agentrof block in
the project's `AGENTS.md` and native `.codex/agents/*.toml` role definitions.
Those agents become discoverable at the next task/session boundary.

One delivery team owns a project. PMO is shared infrastructure, but setup
refuses a second team's workspace or project-agent ownership before changing
files. This keeps the same bare agent ids on Claude and Codex without collisions
inside the project's `.codex/agents/` directory.

The PMO command is required even though the marketplace marks it
`INSTALLED_BY_DEFAULT`; that policy is retained for marketplace hosts that
apply it, but the install contract never relies on it. Every team entry checks
for a ready PMO before mutation. If PMO is missing, disabled, untrusted, or
failed to bootstrap, the entry stops without changing files or project state
and prints the matching recovery step.

```text
codex plugin add project-management-office@agent-marketplace
```

Codex mutating entries run in Code/Default mode. They stop without writes in
Plan mode and ask you to switch modes.

## Quickstart

Everything runs through the team's entry skills; agents and knowledge skills
stay behind them. The table uses Claude's slash form; Codex exposes the same
entry names through its skill picker and `$` invocation.

| Entry | What it does |
|---|---|
| `/software-engineering-team:setup` | Bootstraps the workspace and the project config. Idempotent. |
| `/software-engineering-team:business-analysis` | Interactive analysis; produces the approved brief every flow stands on. |
| `/software-engineering-team:solution-design` | Interactive solution architecture: landscape, technology and topology decisions as living documents the team plans and designs against. |
| `/software-engineering-team:design-system` | Creates or updates the design master from picked candidates. |
| `/software-engineering-team:sketch` | Design exploration: divergent single-file previews, no code. |
| `/software-engineering-team:demo` | Pre-sales package: a navigable single-file demo, no code. |
| `/software-engineering-team:deliver` | Real work. Atomic asks ship as a small PR; everything else runs the backlog path with gates. |
| `/software-engineering-team:delivery-lanes` | The integrator surface for parallel delivery: proposes lanes, opens worktrees the user drives in their own sessions, owns every merge checkpoint. |
| `/software-engineering-team:configure` | The single change gate for the project config. |
| `/software-engineering-team:organize-docs` | On-demand reorganization of the whole docs vault: full audit, owner-gated renames, deterministic migration, curated maps and titles. |
| `/project-management-office:control-tower` | Starts Control Tower, the read-only web dashboard over the central database, and replies with the clickable URL. |
| `/project-management-office:issue-desk` | Reviews and files issues captured by marketplace safety hooks. |
| `/project-management-office:upgrade` | Inspects, plans, applies, or recovers a safe marketplace upgrade. Codex: `$project-management-office:upgrade`. |

A first session usually looks like: `setup`, then `business-analysis`
for the first topic, `solution-design` for the system foundations,
`design-system` before any screen work, then `deliver`.

## Upgrading an existing project

> [!IMPORTANT]
> Update the installed plugins first, close existing Agentrof sessions, then
> start a new session and invoke Agent Marketplace Upgrade. Normal marketplace
> mutations remain locked while an upgrade or recovery is required.

Claude users invoke `/project-management-office:upgrade`; Codex users select
`project-management-office:upgrade` or invoke `$project-management-office:upgrade`.
The entry first performs a read-only status check, shows one readiness or
blocker gate, writes a fingerprint-bound plan only after approval, and asks
again before apply. A successful run requires another fresh session so both
hosts load the new hooks, skills, and project agents.

The upgrader owns only PMO data, `.agentrof/project.json`, Agentrof-marked
project instruction blocks, Agentrof-owned project agent files, and declared
machine-owned config fields. It does not overwrite user code, authored docs,
memory, demos, sketches, secrets, environment files, custom CI, or unmanaged
instruction content. See [Upgrade protocol](docs/upgrade-protocol.md) for the
complete safety and recovery contract.

## Repository map

- [docs/architecture.md](docs/architecture.md): the invariants.
- [docs/authoring.md](docs/authoring.md): component templates and rules.
- [docs/orchestration.md](docs/orchestration.md): the flow contract.
- [docs/upgrade-protocol.md](docs/upgrade-protocol.md): user and runtime upgrade contract.
- [docs/migration-authoring.md](docs/migration-authoring.md): ordered migration discipline.
- `plugins/`: host-neutral canonical roles, workflows, skills, scripts and templates.
- `platforms/{claude,codex,shared}/`: host manifests, contracts and runtime overlays.
- `.agents/plugins/marketplace.json`: native Codex marketplace policy.
- `.claude-plugin/marketplace.json`: Claude marketplace catalog.
- `versions.json`: the single stable version registry for the marketplace and plugins.
- `.changes/`: one release-impact declaration for every normal pull request.
- `dist/{claude,codex}/`: generated, self-contained distributions; never edit them.
- `tools/`: validator, distribution builder, counts injector, scaffolder, tests.

## Quality gates

```
make check
```

runs the validator, Claude/Codex package drift gates, the counts drift gate
and the test suite. CI runs the same command on every push and pull request;
a single finding is red.

## Release channels

The marketplace catalog is read from `main`, while every Claude and Codex
plugin source resolves from the `stable` branch. Normal merges do not change a
SemVer value. CI identifies each main build as
`main.<first-parent-count>.g<sha7>` and stores both generated host packages as
an artifact.

Stable SemVer is authored only through `versions.json`. A plugin has one
platform-independent version, so its Claude and Codex packages always publish
together at the same number. Every normal pull request adds a JSON changeset
under `.changes/`; `patch`, `minor`, and `major` impacts are combined per
component and the highest pending impact advances the marketplace version.
Release-free documentation, test, and CI work uses an empty components object.

Maintainers start the `Prepare stable release` workflow manually. It either
bootstraps the first stable channel or opens an unmerged `release/stable` pull
request. The workflow uses the repository `GITHUB_TOKEN`, dispatches validation
events, and never auto-merges it. GitHub holds the automated PR's CI for
maintainer approval. Merging that exact PR runs all deterministic
and real-host gates again, verifies the prior
stable commit, creates the annotated `vX.Y.Z` tag and GitHub Release, and moves
`stable` forward. `make release-check` tests the current checkout packages;
`make public-release-check` tests the published stable channel.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: the validator
is the rulebook, `make check` must be green, and the named anti-patterns
stay out.

## License

MIT
