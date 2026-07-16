# Metadata

Frontmatter is the vault's typed surface: the property panel, the graph
edges from values, and every generated index render from it. One value
type per key vault-wide; the table lives in `data/vault-policy.json`
under `property_types` and the committed `types.json` is derived from it.

## The floor

Every authored note opens with frontmatter carrying at least:

```markdown
---
type: decision
title: Order event distribution
tags:
  - doc/decision
  - status/accepted
---
```

- `type` names the note's kind (decision, rule-set, landscape, moc,
  home, guide, page-override, ...). The kind drives the tag mirror.
- `tags` is ALWAYS a block list and always exactly the mirror:
  `doc/<type>` plus `status/<status>` when the note has a status,
  underscores kebab-ized (`in_review` becomes `status/in-review`).
  Nothing else: tags are stamped state, not folksonomy.
- `aliases` (block list) is mandatory wherever the note owns a bare id:
  a decision note carries its id (`- SD-007`) so search and unlinked
  mentions find it.

## Shapes

- Singular `tag:` / `alias:` keys are dead grammar; only the plural
  list keys exist.
- Inline flow lists (`tags: [a, b]`) are denied at write time: the
  stdlib parsers read them as scalars, and the contract is one dash
  item per line.
- Dates are `YYYY-MM-DD`. Stamped dates (`approved_at`, `decided_at`)
  are written only by their owning verb off the UTC clock; the guard
  denies a hand-typed value.

## Relation keys

Doc-referencing keys hold QUOTED vault-absolute wikilinks and draw
edges in the graph and backlinks panel:

```markdown
supersedes: "[[solution-design/decisions/sd-003-order-events-v1]]"
governs: "[[business-analysis/shop/domains/orders/entities/order]]"
```

- The supersede chain is bidirectional (`supersedes` on the younger,
  `superseded_by` on the older) and is written by `stamp-decision` in
  one operation; hand-editing one end breaks symmetry and the check.
- An empty relation is an ABSENT key, never an empty string: the stdlib
  parser reads `key: ""` as a list opener and the property panel shows
  noise.

## Status lifecycle

`status` values and their transitions belong to each tree's own
contract (analysis space standard, decision records). This skill owns
only the mechanical mirror: whenever a verb flips `status`, the same
write flips the `status/` tag. A mismatch is a gate error.
