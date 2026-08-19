---
name: obsidian-vault
description: Machine-enforced authoring law for the team's workspace docs vault. Load whenever creating, editing, linking, classifying, navigating, rendering, or repairing content under the docs tree.
exposure: internal
---

# Obsidian Vault

Treat `workspace/docs/` as one vault and one graph. Business Analysis,
Solution Design, System Architecture, Design System, and Experience Design are
subtrees, not separate vaults. Variation lives only in
`data/vault-policy.json`.

## When to Use

- Load whenever creating, editing, linking, classifying, navigating,
  rendering, or repairing content under `workspace/docs/`.

## Core law

- The taxonomy and property surface are closed. Unknown types, paths, names,
  properties, statuses, tags, relations, unresolved links, and orphan notes
  fail their owning gate.
- Authored notes use policy-governed stable filenames. User
  identity lives in `title`; stable ids live in frontmatter `aliases`, never
  filenames or titles. H1 must equal `title` byte-for-byte.
- `title` is a direct, natural phrase in the configured output language. It
  must not be id-led, the raw filename stem, generic, or duplicated in the
  graph.
- Generated first-line-marker files and machine directories are rendered,
  never hand-edited. Statuses, dates, tag mirrors, inverse relations, and
  supersede chains are written only by their owning verbs.
- Cross-subtree semantic links are allowed. Structural navigation constrains
  the first nav link, not traceability or prose links.

## Required references

- [linking.md](references/linking.md): citation and rename grammar. Read when writing or repairing any link.
- [metadata.md](references/metadata.md): frontmatter and typed fields. Read when changing metadata, titles, statuses, or relations.
- [vault-structure.md](references/vault-structure.md): paths, maps, nav, and generated views. Read when creating, moving, renaming, or rendering notes.
- [graph.md](references/graph.md): payload, plugins, labels, colors, and local UI state. Read when the payload fails or the owner needs vault advice.

## Stewardship and enforcement

Every docs-producing entry runs:

```text
vault_check.py check --vault workspace/docs --scope <subtree>
```

Repair every error before its gate, name warnings, re-render generated files,
and use repeated `--exclude` flags only for an explicitly reviewed repair
scope. Exclusion is not a lifecycle, lock, or approval state.

- `check` validates; errors block, warnings do not.
- `render-decisions`, `render-navigation`, and `render-relations` own generated
  indexes, navigation, inverse projections, shards, and traceability reports.
- `check` and `normalize` are the only vault-wide adoption/repair operations;
  they inspect the tracked vault directly and never require a project state
  service.
- `stamp-decision` writes status, UTC date, tag mirror, and both supersede ends
  atomically.
- `normalize` owns deterministic repair; `normalize --rename` applies the
  naming grammar and rewrites every referrer atomically. An explicitly
  excluded referrer vetoes that rename and is reported by path.
- The per-write hook denies invalid content before landing and rechecks after
  landing. Shell moves surface at the next check.
- The portable full gate is `.github/agentrof/vault-gate.pyz check
  --project-root . --json`.
