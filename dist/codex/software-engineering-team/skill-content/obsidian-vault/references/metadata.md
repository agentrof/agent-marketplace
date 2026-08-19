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
  and ENDS with the type's designation (table below), never the
  filename stem, never id-led. The first H1 is byte-identical to the
  title; the checker enforces presence, the H1 match and the id-lead
  ban, and warns on stem-identical, generic or duplicate labels.
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

## Type designations

The title's closing designation is mechanical. The English default table below
is minted per project into config.json's `doc_type_designations` map
(type-kebab -> designation). Display wording may follow the project's output
language, terminology language, or an explicitly approved mixture. The checker
holds each typed note's title against that data at a word boundary under an
NFKC+casefold fold. Map absence or unreadable config warns per note naming the
mint duty, never a silent pass; only the English default table ships.

The map is mutable with memory, never hand-edited. Its single writer,
for mint and change alike, is `vault_check.py reconcile-designations`
(the write-time hook denies any other Write/Edit of the map or the
ledger). A change records the outgoing value in the machine-managed
`doc_type_designation_history` ledger sibling in the SAME config write,
then transitions every affected title and byte-matching H1 source -> target
(tail-stripping every known prior value of the note's own type, so no
double suffix survives), sweeps wikilink aliases byte-equal to the source
title, and re-renders the generated views. The `designation_drift`
check holds titles against the ledger: a retired value closing a title
is an error (mechanically fixable), while a mid-title stranding or a
non-closing designation warns.
An adopted vault with stale titles and no ledger states its prior value
explicitly via `--from` (never recorded: it was not a configured
value).

| doc type | designation |
|---|---|
| space | space overview |
| domain | domain overview |
| glossary | glossary |
| actor-roster | actors |
| budget-set | budgets |
| process | process |
| entity | entity |
| rule-set | rules |
| acceptance-set | acceptance criteria |
| decision | decision |
| integration | integration |
| landscape | landscape |
| engagement | engagement |
| design-master | design master |
| page-override | page override |
| backlog | product backlog |
| backlog-review | backlog review |
| epic | epic |
| epic-review | epic review |
| story | story |
| test-plan | test plan |
| issue-report | issue report |
| experience | experience |
| journey | journey |
| flow-set | flow set |
| screen | screen |
| system-architecture | system architecture |
| architecture-component | architecture component |
| architecture-module | architecture module |
| interface-contract | interface contract |
| data-model | data model |
| threat-model | threat model |
| runtime-view | runtime view |
| reliability-view | reliability view |
| observability-view | observability view |
| architecture-connection | architecture connection |
| architecture-standard | architecture standard |
| artifact-manifest | artifact manifest |
| engagement | engagement |
| design-master | design master |
| page-override | page override |

Nav-layer notes (home, maps) carry no designation. Worked shapes,
generic English (render the designation into the output_language):

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
