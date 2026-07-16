# Load

@workspace/memory/me.md

## Rules

workspace/memory/me.md. Read and follow.

## House rules

- Two language axes live in workspace/config.json, both defaulting to
  English: output_language governs .md body prose; terminology_language
  governs names, technical terms, code and comments, commit messages
  and PR bodies. The machine layer (file names, branches, keys, ids,
  CLI output) is always English.
- Decision and preference questions go through the AskUserQuestion
  popup: recommended option first with a "(Recommended)"-style suffix
  in the conversation's language, tradeoffs in descriptions, at most
  four options and four questions per batch.
- Variation points (enums, thresholds, formats, taxonomies, policy
  values) are declared config or schema, never hard-coded.

## Team workspace

This project is driven by the software-engineering-team plugin. Everything the team
produces lives under `workspace/`:

- `workspace/config.json`: project declaration; machine-managed, never
  hand-edited. Change it through the configure entry.
- `workspace/docs/`: the team's knowledge base (business-analysis,
  solution-design, system-architecture, design-system), maintained as
  one vault: open `workspace/docs/` as the vault root (never the repo
  root); `home.md` and `maps/` are the navigation layer; citations are
  vault-absolute wikilinks; the obsidian-vault skill and vault_check.py
  own the law. `backlog.md`, `quality-ledger.md` and each tree's
  `decision-log.md` are GENERATED views; never hand-edit them.
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
solution-design, design-system, sketch, demo, request, delivery-lanes,
configure.
