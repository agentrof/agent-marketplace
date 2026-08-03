# Agent Marketplace

A catalog of curated, end-to-end agent teams for Claude Code and native
Codex App/CLI. Install a complete team and run at the goal; this is not a
parts store.

The first team, `software-engineering-team`, is an orchestrated software and product
development team: business analysis, planning, architecture, design
system, implementation, review, verification and a containerized
environment, driven through a small set of user-invocable entry skills.

Every team runs on the `project-management-office` plugin: a
shared operations backbone holding one central database for projects,
epics, stories, machine-generated tasks, work-order state, findings and audit
events, written only through its CLI and recorded deterministically via
hooks. Claude installs this backbone through the team dependency; the Codex
marketplace installs it by default. The `control-tower` entry starts Control
Tower and replies with the running URL.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 2 | 12 | 12 | 14 |
<!-- counts:end -->

Counts above are injected by `tools/counts.py`; they are never written by
hand anywhere in this repository.

## Install on Claude Code

```
/plugin marketplace add agentrof/agent-marketplace
/plugin install software-engineering-team
```

The `project-management-office` backbone installs automatically with the team (a plugin
dependency); no separate step.

Then, inside your project:

```
/software-engineering-team:setup
```

Setup bootstraps the project workspace, asks for anything it cannot
detect, and points you at the next step.

## Install on Codex App or CLI

```text
codex plugin marketplace add agentrof/agent-marketplace
codex plugin add software-engineering-team@agent-marketplace
```

Start a new task/session, open `/hooks`, inspect and trust both Agentrof
plugins, and start another task if Codex asks for a reload. Then select
`software-engineering-team:setup` in the App skill picker or CLI `/skills`
picker (or invoke it with `$`). Setup generates the managed Agentrof block in
the project's `AGENTS.md` and native `.codex/agents/*.toml` role definitions.
Those agents become discoverable at the next task/session boundary.

The marketplace marks PMO for default installation. Codex App applies that
policy; current CLI builds may show it as available without materializing it
when a local marketplace is added. If it is absent, setup stops safely and
prints the exact recovery command:

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
| `/software-engineering-team:request` | Real work. Atomic asks ship as a small PR; everything else runs the backlog path with gates. |
| `/software-engineering-team:delivery-lanes` | The integrator surface for parallel delivery: proposes lanes, opens worktrees the user drives in their own sessions, owns every merge checkpoint. |
| `/software-engineering-team:configure` | The single change gate for the project config. |
| `/software-engineering-team:build-docs-vault` | On-demand reorganization of the whole docs vault: full audit, owner-gated renames, deterministic migration, curated maps and titles. |
| `/project-management-office:control-tower` | Starts Control Tower, the read-only web dashboard over the central database, and replies with the clickable URL. |

A first session usually looks like: `setup`, then `business-analysis`
for the first topic, `solution-design` for the system foundations,
`design-system` before any screen work, then `request`.

## Repository map

- [docs/architecture.md](docs/architecture.md): the invariants.
- [docs/authoring.md](docs/authoring.md): component templates and rules.
- [docs/orchestration.md](docs/orchestration.md): the flow contract.
- `plugins/*/skill-content/`: canonical skill packages shared by both hosts.
- `.agents/plugins/marketplace.json`: native Codex marketplace policy.
- `codex-plugins/`: generated self-contained Codex distributions.
- `tools/`: validator, surface generators, counts injector, scaffolder, tests.

## Quality gates

```
make check
```

runs the validator, Claude/Codex package drift gates, the counts drift gate
and the test suite. CI runs the same command on every push and pull request;
a single finding is red.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: the validator
is the rulebook, `make check` must be green, and the named anti-patterns
stay out.

## License

MIT
