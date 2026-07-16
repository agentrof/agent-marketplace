# Agent Marketplace

A catalog of curated, end-to-end agent teams for Claude Code. Install a
complete team and run at the goal; this is not a parts store.

The first team, `software-engineering-team`, is an orchestrated software and product
development team: business analysis, planning, architecture, design
system, implementation, review, verification and a containerized
environment, driven through a small set of user-invocable entry skills.

Every team runs on the `project-management-office` plugin: a
shared operations backbone holding one central database for projects,
epics, stories, machine-generated tasks, work-order state, findings and audit
events, written only through its CLI and recorded deterministically via
hooks. Installing a team installs `project-management-office`
automatically as a dependency;
`/project-management-office:control-tower` starts Control Tower and replies with the running URL.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 2 | 12 | 10 | 14 |
<!-- counts:end -->

Counts above are injected by `tools/counts.py`; they are never written by
hand anywhere in this repository.

## Install

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

## Quickstart

Everything runs through the team's entry skills; agents and knowledge
skills stay behind them.

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
| `/project-management-office:control-tower` | Starts Control Tower, the read-only web dashboard over the central database, and replies with the clickable URL. |

A first session usually looks like: `setup`, then `business-analysis`
for the first topic, `solution-design` for the system foundations,
`design-system` before any screen work, then `request`.

## Repository map

- [docs/architecture.md](docs/architecture.md): the invariants.
- [docs/authoring.md](docs/authoring.md): component templates and rules.
- [docs/orchestration.md](docs/orchestration.md): the flow contract.
- `tools/`: validator, counts injector, scaffolder, tests.

## Quality gates

```
make check
```

runs the validator, the counts drift gate and the test suite. CI runs the
same command on every push and pull request; a single finding is red.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: the validator
is the rulebook, `make check` must be green, and the named anti-patterns
stay out.

## License

MIT
