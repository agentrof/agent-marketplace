# Metadata

Frontmatter is the vault's typed surface: the property panel, the graph
edges from values, the graph labels and every generated index render
from it. One value type per key vault-wide; the table lives in
`data/vault-policy.json` under `property_types` and the committed
`types.json` is derived from it.

## The floor

Every authored note opens with frontmatter carrying at least:

```markdown
---
type: decision
title: Order event distribution decision
status: accepted
tags:
  - doc/decision
  - status/accepted
aliases:
  - SD-007
---
```

- `type` names the note's kind (decision, rule-set, landscape, moc,
  home, page-override, ...). The kind drives the tag mirror.
- `title` is the user-facing graph label (see the Title Law in
  SKILL.md): a natural output_language phrase that names the content
  and is never the raw filename stem or id-led. The first H1 is
  byte-identical to the title; the checker enforces presence, the H1
  match, id-lead, raw-stem, generic, and duplicate-label rules.
- `tags` is ALWAYS a block list and always exactly the mirror:
  `doc/<type>` plus `status/<status>` when the note has a status,
  underscores kebab-ized (`in_review` becomes `status/in-review`).
  Nothing else: tags are stamped state, not folksonomy.
- `aliases` (block list) is the id's ONLY home besides its minted row:
  mandatory wherever the note owns a bare id OR a node code. A decision
  note carries exactly one id-shaped alias (`- SD-007`), an analysis
  space or domain overview carries its code (`- SHP`), so search, the
  quick switcher and unlinked mentions find them; ids never appear in
  filenames, titles or H1s. An id-shaped LINK alias must target the
  id's owning note (`alias_ownership`).

## Title law

Titles are authored directly; their classification is expressed independently
by the fixed `type` key and `doc/<type>` tag. When similar records would
otherwise collide in the graph, qualify the subject naturally (for example,
with its owning space, scope, or audience).

Worked shapes:

- `rules/checkout-rules.md` holds `title: Checkout rules`.
- `processes/order-fulfillment-process.md` holds `title: Order fulfillment process`.
- `entities/customer-entity.md` holds `title: Customer entity`.
- `decisions/order-events-decision.md` holds
  `title: Order event distribution decision`, alias `SD-007`.
In every case the H1 repeats the title byte-for-byte.

## Shapes

- Singular `tag:` / `alias:` keys are dead grammar; only the plural
  list keys exist.
- Inline flow lists (`tags: [a, b]`) are denied at write time: the
  stdlib parsers read them as scalars, and the contract is one dash
  item per line.
- Dates are `YYYY-MM-DD`. Stamped dates (`approved_at`, `decided_at`)
  are written only by their owning verb off the UTC clock; the guard
  denies a hand-typed value.
- The policy types every key: text (`code`, `scope`, `review_scope`,
  `verdict`, `system_name`, `direction`, ...), number (`round`),
  date (`approved_at`, `decided_at`), list
  (`tags`, `aliases`, `governs`, `verifies`). A value of the wrong
  shape is a `frontmatter_props` error.

## Relation keys

Doc-referencing keys hold QUOTED vault-absolute wikilinks and draw
edges in the graph and backlinks panel:

```markdown
supersedes: "[[solution-design/decisions/order-events-v1-decision]]"
governs:
  - "[[business-analysis/shop/domains/orders/entities/order-entity]]"
```

- `governs` and `verifies` are ALWAYS block lists, one quoted wikilink
  per `- item` line, even for a single target: the property panel
  holds one value type per key. The `normalize` verb lifts a scalar into
  a one-item list.
- The supersede chain is bidirectional (`supersedes` on the younger,
  `superseded_by` on the older) and is written by `stamp-decision` in
  one operation; hand-editing one end breaks symmetry and the check.
- An empty relation is an ABSENT key, never an empty string: the stdlib
  parser reads `key: ""` as a list opener and the property panel shows
  noise.

The cross-subtree traceability vocabulary is `derives_from`, `satisfies`,
`constrained_by`, `implements`, `uses_design` and `related_to`. These are
always block lists of quoted vault-absolute wikilinks. Canonical scope,
criterion, decision and experience references live in the link alias. Free
body links remain useful graph edges but do not satisfy coverage. The relation
renderer owns inverse visibility, so downstream work never mutates approved
upstream semantic content merely to create a backlink.

## Status lifecycle

`status` values and their transitions belong to each tree's own
contract (analysis space standard, decision records). This skill owns
only the mechanical mirror: whenever a verb flips `status`, the same
write flips the `status/` tag. A mismatch is a gate error.
