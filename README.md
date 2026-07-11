# Agent Marketplace

A catalog of curated, end-to-end agent teams for Claude Code. Install a
complete team and run at the goal; this is not a parts store.

The first team, `software-team`, is an orchestrated software and product
development team: business analysis, planning, architecture, design
system, implementation, review and verification, driven through a small
set of user-invocable entry skills and file-state flows.

## Catalog

<!-- counts:start -->
| Plugins | Agents | Entry skills | Knowledge skills |
|---|---|---|---|
| 1 | 8 | 7 | 10 |
<!-- counts:end -->

Counts above are injected by `tools/counts.py`; they are never written by
hand anywhere in this repository.

## Install

```
/plugin marketplace add xbarslan/agent-marketplace
/plugin install software-team
```

Then, inside your project:

```
/software-team:setup
```

Setup bootstraps the project workspace, asks for anything it cannot
detect, and points you at the next step.

## Quickstart

Everything runs through the team's entry skills; agents and knowledge
skills stay behind them.

| Entry | What it does |
|---|---|
| `/software-team:setup` | Bootstraps the workspace and the project config. Idempotent. |
| `/software-team:business-analysis` | Interactive analysis; produces the approved brief every flow stands on. |
| `/software-team:design-system` | Creates or updates the design master from picked candidates. |
| `/software-team:sketch` | Design exploration: divergent single-file previews, no code. |
| `/software-team:demo` | Pre-sales package: a navigable single-file demo, no code. |
| `/software-team:request` | Real work. Atomic asks ship as a small PR; everything else runs the backlog path with gates. |
| `/software-team:configure` | The single change gate for the project config. |

A first session usually looks like: `setup`, then `business-analysis`
for the first topic, `design-system` before any screen work, then
`request`.

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
