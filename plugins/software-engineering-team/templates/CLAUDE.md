# Load

@workspace/memory/me.md

## Rules

workspace/memory/me.md. Read and follow.

## Team workspace

This project is driven by the software-engineering-team plugin. Everything the team
produces lives under `workspace/`:

- `workspace/config.json`: project declaration; machine-managed, never
  hand-edited. Change it through the configure entry.
- `workspace/docs/`: the team's knowledge base (business-analysis,
  solution-design, system-architecture, design-system). `backlog.md` and
  `quality-ledger.md` here are GENERATED views of the central PMO
  database; never hand-edit them.
- `workspace/apps/`: application code, one folder per application.
- `workspace/environment/`: the containerized environment (definition,
  build recipes, seed scenarios, contract document); devops-owned, one
  command brings the whole system up.
- `workspace/demos/` and `workspace/sketches/`: outward demo packages and
  design exploration previews.
- `workspace/work-orders/`: work-order snapshots (brief, config,
  constitution); gitignored. Live process state (work orders, stories,
  tasks, findings) lives in the central PMO database, read through the
  PMO CLI.

Start work with the team's entry skills: setup, business-analysis,
solution-design, design-system, sketch, demo, request, program,
configure.
