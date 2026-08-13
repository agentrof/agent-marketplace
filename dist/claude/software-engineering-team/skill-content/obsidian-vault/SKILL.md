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
- Authored notes use policy-governed plain or type-suffixed filenames. User
  identity lives in `title`; stable ids live in frontmatter `aliases`, never
  filenames or titles. H1 must equal `title` byte-for-byte.
- `title` ends with the configured type designation under the metadata law.
  The designation map and history have one writer:
  `reconcile-designations`.
- Generated first-line-marker files and machine directories are rendered,
  never hand-edited. Statuses, dates, tag mirrors, inverse relations, and
  supersede chains are written only by their owning verbs.
- Cross-subtree semantic links are allowed. Structural navigation constrains
  the first nav link, not traceability or prose links.

## Required references

- [linking.md](references/linking.md): citation and rename grammar. Read when writing or repairing any link.
- [metadata.md](references/metadata.md): frontmatter and typed fields. Read when changing metadata, titles, statuses, relations, or designations.
- [vault-structure.md](references/vault-structure.md): paths, maps, nav, and generated views. Read when creating, moving, renaming, or rendering notes.
- [graph.md](references/graph.md): payload, plugins, labels, colors, and local UI state. Read when the payload fails or the owner needs vault advice.

## Stewardship and enforcement

Every docs-producing entry runs:

```text
vault_check.py check --vault workspace/docs --scope <subtree>
```

Repair every error before its gate, name warnings, re-render generated files,
and pass frozen paths as repeated `--exclude` flags.

- `check` validates; errors block, warnings do not.
- `render-decisions`, `render-navigation`, and `render-relations` own generated
  indexes, navigation, inverse projections, shards, and traceability reports.
- `adoption-plan` inventories legacy content. `activate-adoption` requires the
  exact green plan hash and current project contract.
- `stamp-decision` writes status, UTC date, tag mirror, and both supersede ends
  atomically.
- `migrate` owns deterministic normalization; `migrate --rename` applies the
  naming grammar and rewrites every referrer atomically, vetoing frozen
  referrers.
- The per-write hook denies invalid content before landing and rechecks after
  landing. Shell moves surface at the next check.
- The portable full gate is `.github/agentrof/vault-gate.pyz check
  --project-root . --json`.
