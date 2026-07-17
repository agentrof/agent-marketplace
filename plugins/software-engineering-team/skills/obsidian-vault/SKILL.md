---
name: obsidian-vault
description: Vault authoring law for the team's workspace docs tree. Loaded by software-engineering-team flows and personas whenever they write under the docs tree; not user-facing.
user-invocable: false
---

# Obsidian Vault

The consuming project's docs tree (workspace/docs/) is ONE vault: the
owner opens it in the vault app and reads the knowledge graph; agents
author every note headlessly. This skill is the single home of the vault
law. Every rule below is enforced by
`${CLAUDE_PLUGIN_ROOT}/scripts/vault_check.py` and the per-write hook;
variation points live in `data/vault-policy.json`, never in prose.

## When to Use

- Loaded whenever a persona or spawned agent creates or edits any file
  under the docs tree, curates a map note, or repairs a degraded vault.

## Linking Law

- DO cite vault content as a vault-absolute wikilink with an alias:
  `[[solution-design/decisions/order-events-decision|SD-007]]`. Paths
  run from the vault root, forward slashes, exact case, no leading slash.
- DON'T write relative markdown links between vault files (hook-denied);
  external URLs stay `[text](https://...)`; targets outside the docs
  tree stay standard relative links.
- Heading anchors are banned; the stable in-note anchor is a block id:
  `[[business-analysis/shop/budgets#^event-volume|volume budget]]`.
- In table cells escape the alias pipe: `[[path\|SD-007]]`. Schema
  id-citation columns carry these wikilinks too; a bare id is legal
  only where it is minted.
- Zero unresolved wikilinks and zero orphan notes are gate invariants.

## Naming and Title Law

- Naming Law: named files are plain per-folder contracts (`space.md`,
  `domain.md`, `glossary.md`, `actors.md`, `budgets.md`, `round-<n>.md`);
  typed content is `<slug>-<type>.md` with the schema's English suffix
  (`checkout-rules.md`, `order-events-decision.md`). No chain or id
  prefixes; duplicates across folders are legal, and only a
  policy-banned generic basename where a slug is due is an error.
- Title Law: `title` is the user-facing graph label, shown instead of
  the filename by the vetted display plugin: a natural output_language
  phrase naming the content and ENDING with its type designation,
  rendered in the output_language (canonical English table in
  references/metadata.md). Never id-led, never the raw stem; the H1
  equals the title byte-for-byte. The checker holds presence, the H1
  match, the id-lead ban and uniqueness.
- Alias Law: ids live in frontmatter, never in filenames or titles: a
  note that owns an id or node code carries it in `aliases`; an
  id-shaped link alias must decorate a link to the id's owning note,
  nothing else.
- The policy lists the vetted community-plugin set (the front-matter
  title display plugin, bare `title` template) in the committed
  payload; without it, labels fall back to the meaningful slug names.

## Metadata Law

- Every authored note carries frontmatter: `type` plus the `tags` mirror
  at minimum. `tags`/`aliases` are BLOCK lists (one `- item` line each);
  inline `[a, b]` lists are hook-denied.
- Tags are exactly the stamped mirror `doc/<type>` plus
  `status/<status>` when a status exists; nothing else, never hand-picked.
- Doc-referencing keys (supersedes, superseded_by, governs, verifies,
  engagement) hold quoted vault-absolute wikilinks: they draw the graph
  edges. `governs`/`verifies` are ALWAYS block lists, one item even for
  a single target. Dates are YYYY-MM-DD; one value type per key
  vault-wide (data/vault-policy.json property_types).
- Status transitions and their tag mirrors are written only by owning
  verbs (approve, stamp-decision); a hand-typed stamp is guard-denied.

## Structure Law

- Star topology: home is the single knowledge-base root and DYNAMIC:
  it links ONLY the content-bearing subtrees' maps (nothing at seed);
  the entry that births a tree materializes its map seed and adds the
  home line. Each map links its subtree's hubs, curated by the
  producing persona in the same session that creates or retires docs.
- Nav section: the line `<!-- sec: nav -->` opens it in every tree (the
  heading text above it is free output-language prose); the FIRST
  wikilink is the owning hub per the policy hubs ladder (the deepest
  existing hub covering the note; hubs and uncovered notes point at the
  subtree map), then 2-5 contextual peers.
- Decision records are atomic notes under their tree's decisions/
  directory (`<slug>-decision.md`; the id lives in the frontmatter
  alias, never the filename, title or H1); where policy renders an
  index it is GENERATED. Generated files (first-line marker) are
  re-rendered, never edited.

## Stewardship

Vault stewardship is a standing duty, cited by every docs-producing
entry: run `${CLAUDE_PLUGIN_ROOT}/scripts/vault_check.py check --vault
workspace/docs --scope <subtree>`; every finding it names is THIS
session's repair work before the gate; deterministic classes go through
the `migrate` verb; repairs to generated files are re-renders; pass the
active freeze set as repeated `--exclude` flags so frozen docs surface
as named warnings; a red vault check blocks the gate like a red compile.

## Verbs and Enforcement

- `check` validates; errors block gates, warnings are named, never block.
- `render-decisions` renders each decision tree's index; `stamp-decision`
  stamps status, decided date, tag mirror and the supersede chain in one
  operation; `migrate` applies the deterministic legacy rewrites, and
  `migrate --rename` heals naming: it renames per the grammar and
  rewrites every referrer in one operation, vetoing renames whose
  referrers are frozen.
- The per-write hook denies non-compliant content before it lands and
  re-checks every landed vault write; shell file moves bypass it and
  surface at the next check.

## References

- [linking.md](references/linking.md): wikilink grammar, aliases, embeds, block ids, out-of-vault escapes. Read when writing or fixing any citation.
- [metadata.md](references/metadata.md): the property contract per key, tag vocabulary, stamped mirrors. Read when authoring frontmatter or adding a property.
- [vault-structure.md](references/vault-structure.md): home, maps, nav sections, generated views, attachments, layout law. Read when creating a doc, curating a map, or repairing structure.
- [graph.md](references/graph.md): the committed payload, the vendored title plugin, graph groups, what stays user-local. Read when the payload check fails or when advising the owner on vault usage.
