# Load

@{{workspace}}/memory/me.md

## Rules

{{workspace}}/memory/me.md. Read and follow.

## House rules

- Two language axes live in {{workspace}}/config.json, both defaulting to
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
produces lives under `{{workspace}}/`:

- `{{workspace}}/config.json`: project declaration; machine-managed, never
  hand-edited. Change it through the configure entry.
- `{{workspace}}/docs/`: the team's knowledge base (business-analysis,
  solution-design, system-architecture, design-system, experience-design), maintained as
  one vault: open `{{workspace}}/docs/` as the vault root (never the repo
  root); on first open the vault app asks ONCE to trust the vendored
  community plugins (that click enables the title-based labels, each
  ending in its type designation from config.json's machine-managed
  doc_type_designations map; the map and its history ledger are
  written only by vault_check.py reconcile-designations through the
  configure entry, which also retitles the vault on a change);
  `home.md` is the knowledge-base root and `maps/` the navigation
  layer; the global graph colors notes by document type, one color per
  type; citations are vault-absolute wikilinks; the obsidian-vault
  skill and vault_check.py own the law. Files opening with a
  generated-by marker (each tree's `decision-log.md`) are GENERATED
  views: re-rendered by their owning verbs, never hand-edited.
- `{{workspace}}/apps/`: application code, one folder per application.
- `{{workspace}}/environment/`: the containerized environment (definition,
  build recipes, seed scenarios, contract document); devops-owned, one
  command brings the whole system up.
- `{{workspace}}/demos/` and `{{workspace}}/sketches/`: outward demo packages and
  design exploration previews.
- `.agentrof/agent-marketplace/.runtime/plan/`: gitignored plan drafts.
  Experience drafts and approved baselines both live under docs.
- `.agentrof/agent-marketplace/.runtime/work-orders/`: work-order snapshots
  (brief, config, constitution) owned by this worktree; gitignored. Delivery
  state (work orders, the backlog
  of stories and its ordering, tasks, findings, the quality ledger)
  lives in the central PMO database, read through the PMO CLI.

Greenfield preparation is setup, business-analysis, solution-design,
design-system, experience-design, backlog-plan, then STOP. Start deliver or
delivery-lanes explicitly afterward. Existing projects start feature work with
deliver. Other entries include sketch, demo, configure and organize-docs.
