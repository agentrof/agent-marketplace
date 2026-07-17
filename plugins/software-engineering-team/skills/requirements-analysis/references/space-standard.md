# The Analysis Space Standard

One topic = one analysis space: a folder under
workspace/docs/business-analysis/<slug>/ holding typed, cross-linked
markdown documents. Authored files are the single source of truth;
everything derived (index, registries, backlinks, status, question board)
is generated into <space>/_generated/ by the compiler and protected
against hand edits. The compiler
(${CLAUDE_PLUGIN_ROOT}/scripts/ba_compile.py) reads its taxonomy from the
schema data file shipped with the business-analysis skill; this reference
explains the standard to a human and to the analyst persona. When the two
ever disagree, the schema and the compiler win: a rule that is not
machine-checked is not a rule.

## The node model

- A NODE is a folder with one overview document plus content folders.
  Every named file carries the CHAIN-QUALIFIED slug: the space
  directory's name, extended with every domain folder on the path,
  joined by the schema's slug_join character. The space root's overview
  is <slug>-space.md; a child node is a domain: domains/<d>/ with
  <space>-<d>-domain.md. Domains nest by the same rule
  (domains/finance/domains/accounts-payable/ holds
  <space>-finance-accounts-payable-domain.md); the compiler warns at
  depth 2 and fails at depth 3.
- The chain exists because basenames are VAULT-UNIQUE (they are the
  graph's fallback labels and link targets); a duplicate or bare
  contract name (space.md, domain.md) is an error. When two folder
  paths spell the same chain, rename a domain folder so the chain stays
  unique, then let the vault checker's migrate --rename regenerate
  filenames and rewrite referrers.
- Content folders inside any node, created only when non-empty:
  processes/, entities/, rules/, acceptance/, decisions/, reviews/.
- Cross-domain concerns live ONLY at the root, one home each:
  <slug>-glossary.md, <slug>-actors.md, <slug>-budgets.md,
  integrations/. A domain may carry its own <chain>-budgets.md for
  domain-specific deltas. The root may also own processes/, rules/ and
  acceptance/ for genuinely cross-domain flows (order-to-cash spanning
  finance and inventory lives at the root and mints ids in the space's
  own namespace).
- Generated views live only in _generated/ (index.md, registry.md,
  registry.json, backlinks.md, status.md, open-questions.md). Their
  first line is the generated-by marker; a guard hook denies hand edits.
  Authored docs never link into _generated/; the vault policy lists it
  as a machine directory, so its non-note files are tolerated
  mechanically.

A small feature is the degenerate case: the root node is the only
domain, four skeleton files plus a handful of content docs. An
enterprise-scale topic grows domains on evidence, never from the org
chart (see the decomposition reference).

## Document types

Common frontmatter on every authored doc (keys snake_case): type, title,
status (draft | in_review | approved | superseded), owner_role, tags.
Exactly when approved: approved_at, stamped by the ba_compile approve
verb with the UTC calendar date (never hand-written; the guard hook
denies a typed date and the compiler rejects a future one). Exactly when
superseded: superseded_by. Doc identity is its path; there is no
separate id key.

- title is the user-facing graph label: human output_language prose,
  never the filename stem. A decision doc's title is id-led and quoted
  (`title: "DEC-ERP-001: Single warehouse first"`); its H1 matches.
- tags is a BLOCK list (one `- item` line each) holding exactly the
  stamped mirror: `doc/<type-kebab>` plus `status/<status-kebab>`
  (rule_set becomes `doc/rule-set`, in_review becomes
  `status/in-review`). The mirror is written by the owning verbs, never
  hand-picked. Inline flow lists (`tags: [a, b]`) are hook-denied.
- aliases (block list) is mandatory where a doc owns an id or code: a
  space or domain overview carries its code (`- ERP`), a decision doc
  its DEC id; the compiler enforces both.
- Doc-referencing keys (governs, verifies, superseded_by) hold QUOTED
  vault-absolute wikilinks; governs and verifies are ALWAYS block
  lists, one `- "[[...]]"` item per target, even for a single target
  (one value type per key vault-wide). An empty relation is an ABSENT
  key, never an empty string.

| type | lives at | mints | notes |
|---|---|---|---|
| space | <slug>-space.md | AS, OQ | carries code:, the space head summary (30 lines max), purpose, domain map, out of scope |
| domain | domains/<d>/<space>-<d>-domain.md | AS, OQ | carries code:; mission, boundaries, process map, data notes; the chain extends per nesting level |
| process | processes/*.md | AS, OQ | actors, trigger, main flow, exception flows |
| entity | entities/*.md | AS, OQ | fields (a table whose first column is the fixed `field` identifier column), lifecycle, propagation semantics |
| rule_set | rules/*.md | BR, AS, OQ | carries governs: (entity or process targets) |
| acceptance_set | acceptance/*.md | AC, AS, OQ | carries verifies:; every AC cites BR ids and a verify cell |
| decision | decisions/dec-<code>-<nnn>-<slug>.md | DEC, AS, OQ | id-prefixed filename (code lowercase, number zero-padded); context, options, ruling, consequences |
| glossary | <slug>-glossary.md (root) | AS, OQ | one vocabulary per space; terms table columns: term, technical_name, definition (technical_name empty when the term names no technical artifact) |
| actor_roster | <slug>-actors.md (root) | AS, OQ | actors, roles, permission vocabulary |
| budget_set | <chain>-budgets.md | AS, OQ | all six non-functional categories, empty ones written as "none stated, confirmed" |
| integration | integrations/*.md (root) | AS, OQ | carries system_name and direction; exchange, failure semantics, ownership |
| challenge_record | reviews/<chain>-round-N.md | CH | chain-slugged round files (space rounds: <slug>-space-round-N.md); the challenge loop's audit record; see the challenge-review skill |

Required sections carry language-neutral anchors so the checker never
parses translated heading text: the H2 text is free (it follows the
project's output language), the marker is fixed English:

    ## Is Kurallari <!-- sec: rules -->

Head summary: the lines between the H1 and the first H2. Cap: 30 lines
for the space overview (the brief contract's summary survives here), 10
lines for every other doc.

## The distributed brief contract

The old six-section brief did not disappear; it distributed:

| brief section | new home |
|---|---|
| head summary | <slug>-space.md, between H1 and first H2 |
| purpose and scope | <slug>-space.md purpose_scope and out_of_scope |
| non-functional budgets | <slug>-budgets.md, all six categories |
| process analysis | process docs, mapped from each overview |
| conceptual data dictionary | entity docs; trivial entities stay rows in the domain overview's data notes until promoted |
| business rules | rule_set docs |
| acceptance criteria | acceptance_set docs |
| open questions | Open Questions tables anywhere, rolled up into _generated/open-questions.md |

The compiler proves the contract at the gate: a subtree cannot approve
without at least one process, one rule_set and one acceptance_set.

## Ids

Format: KIND-CODE-NNN (BR-INV-004, AC-FIN-012, OQ-ERP-003). KIND is one
of BR, AC, AS, OQ, DEC, CH. CODE is the nearest enclosing node's code:
2 to 4 uppercase letters, declared once in the node overview's
frontmatter, unique across the whole space. LEG is reserved.

- Rows, not prose: an id is minted only as a table row with the fixed
  English snake_case headers of its kind, in the section its kind owns.
  A rule stated in a paragraph does not exist for the registry.
- Minting discipline: BR only in rule_set docs, AC only in
  acceptance_set, DEC only in decision, CH only in challenge_record;
  AS and OQ in any authored doc's Assumptions / Open Questions tables.
- Ids are permanent. A number is never reused and never renumbered;
  retirement is a row status. Moving a doc between domains re-mints its
  ids (the compiler fails a code mismatch), which is intended: ids never
  silently change owners.
- A bare id is legal in exactly ONE place: its MINT, the id column of
  the owning row (a mint written as a wikilink is an error). Everywhere
  else an id citation is a vault-absolute wikilink to the OWNING doc,
  and the compiler verifies the target really mints it, so every
  citation is a checked claim. In prose the id is the alias:

  ```markdown
  [[business-analysis/<slug>/domains/inventory/rules/stock-item-lifecycle|BR-INV-001]]
  ```

  TABLE CELLS in schema-declared id-citation columns (cites, affects,
  blocks, targets, verify) carry the SAME link in escaped-pipe form
  (`[[path\|BR-INV-001]]`); the compiler normalizes cells back to bare
  ids for the registry and statement hashes, so cell rewrites never
  change machine identity, and a bare id left in a citation cell is an
  error the vault checker's migrate verb rewrites.
- AS and OQ rows carry opened_on dates, pasted from the PMO CLI's
  `now --date` output (never typed from memory; the compiler rejects a
  future date); the compiler flags open rows older than the schema
  threshold. The assumption-aging principle is machine-checked, not
  remembered.

## Lifecycle and gates

Per-doc status walks draft -> in_review -> approved; approved -> draft
reopens for rework; any -> superseded retires a doc (successor named).
A doc cannot approve while it holds an open assumption or open question
row, a dead link, or a missing section.

Computed roll-ups (never authored): node status is the minimum over its
subtree; FOUNDATION APPROVED means <slug>-space.md, <slug>-glossary.md,
<slug>-actors.md and <slug>-budgets.md are approved; a domain is
BUILDABLE when the foundation is approved, its subtree is approved, its
challenge gate is satisfied and every id it cites across domain lines
has an approved owner.

The minimal approvable unit is the domain node. Downstream flows read
buildability per domain: a demo touching only inventory does not wait
for the payroll analysis. The whole-space gate additionally requires the
space-level challenge round when child domains exist.

## Formatting

- One H1 per doc, matching the title (a decision doc's H1 is the id-led
  form: `DEC-ERP-001: <title>`). No emoji in headings, no em dash
  anywhere: the compiler enforces the same bans the marketplace
  validator enforces on shipped content.
- Structure is language-neutral: frontmatter keys and machine-parsed
  values (type, status, dates, roles), sec anchors, table headers, ids,
  file and directory names are fixed English; the title value, body
  prose and free-text cell contents follow workspace/config.json
  output_language.
- Names and technical terms follow terminology_language (default
  English), never output_language: entity, field and lifecycle-state
  names in table cells, mermaid identifiers (erDiagram entities and
  attributes, stateDiagram states) and established technical terms in
  prose. A name derived from a business concept is rendered in the
  terminology_language (Fatura becomes the Invoice entity, fatura_no
  the invoice_number field); record the pair in the glossary doc.
- Diagrams (fenced mermaid blocks) render on the hosting platforms the
  team already uses: flowchart TD for process flows, stateDiagram-v2 for
  entity lifecycles, erDiagram for a domain's conceptual entities (business
  cardinalities only), flowchart LR for context maps. A diagram is never
  the only carrier of a rule: every illegal transition in a picture is
  also a BR row.
- Callouts use the blockquote alert syntax ([!NOTE], [!IMPORTANT],
  [!WARNING]); they carry context, never BR or AC content.
- Vault-internal citations are vault-absolute wikilinks with an alias;
  the ba_compile check and the per-write hook both enforce it:

  ```markdown
  [[business-analysis/shop/domains/inventory/entities/stock-item|Stock Item]]
  ```

  Standard relative markdown links remain only for targets OUTSIDE
  workspace/docs/ (sketches, demos, environment files); external URLs
  stay `[text](https://...)`. Link text (the alias) is a noun phrase or
  a bare id, never "here".
- Budgets and other row-level anchors are block ids: mint ` ^kebab-id`
  at the end of the row once, never rename it, and cite
  `[[business-analysis/shop/shop-budgets#^event-volume|volume budget]]`.
  Heading anchors are banned vault-wide: heading text is output-language
  prose and cannot hold an anchor stable.

## Navigation and stewardship

- Every authored doc ends with the nav section, marked
  `<!-- sec: nav -->` (the heading text above the marker is
  output-language prose). The FIRST wikilink is the owning HUB per the
  vault policy's hubs ladder: domain content points at its domain
  overview, root content at the space overview, and the overviews
  themselves point at `[[maps/business-analysis]]`. Then 2-5 peers a
  reader would jump to next. Stubs are born with the right hub.
- The map links HUBS: a new space or domain overview joins
  maps/business-analysis.md in the same session that births it; leaves
  get their edges from their hubs and nav sections, not from the map.
  A retired doc leaves the map the same way. The space's FIRST content
  also materializes the map seed and adds the home map line (the
  business-analysis entry owns that duty).
- The vault checker runs at every gate alongside the compiler; frozen
  docs are passed as repeated --exclude flags and surface as named
  warnings, never silently skipped.
- Deterministic legacy rewrites go through the vault checker's migrate
  verb. Migrate is format-only and sanctioned on approved docs: it never
  touches status and never stamps.

## Rename and restructure runbook

1. Renames are grammar work, not grep work: run the vault checker's
   `migrate --rename --dry-run --json` for the plan (each old -> new
   pair with its referrer count; renames whose referrers include a
   frozen path are listed as blocked_by_frozen_referrer and stay
   deferred until release).
2. Execute `migrate --rename`: it renames and rewrites every referrer
   across the whole vault (body links, frontmatter values, map rows)
   in one operation; already-compliant files are skipped.
3. Moving a doc between nodes is still a manual move first: relocate
   the file, then run --rename so the filename follows its new chain
   and the referrers follow the file. Re-mint its ids under the new
   code (retire the old rows in place with a superseded_by note;
   numbers are never reused) and update citations.
4. Run check + render plus the vault check; a dead wikilink or code
   mismatch fails loudly. Re-render regenerates the registry so
   alias-ownership checks read post-rename paths.

## Worked example: one rule_set

```markdown
---
type: rule_set
title: Stock item lifecycle rules
status: approved
approved_at: 2026-07-10
owner_role: business_analyst
tags:
  - doc/rule-set
  - status/approved
governs:
  - "[[business-analysis/shop/domains/inventory/entities/stock-item]]"
---

# Stock Item Lifecycle Rules

Lifecycle constraints for
[[business-analysis/shop/domains/inventory/entities/stock-item|Stock Item]],
bounded by
[[business-analysis/shop/decisions/dec-erp-001-single-warehouse-first|DEC-ERP-001]].

## Rules <!-- sec: rules -->

| id | statement | kind | status | cites |
|---|---|---|---|---|
| BR-INV-001 | The sku cannot change after the item's first stock movement; a change attempt is refused with the movement date named. | constraint | active | |
| BR-INV-005 | A blocked or discontinued item accepts no stock movements; any attempt is refused and the state is unchanged. | lifecycle | active | [[business-analysis/shop/decisions/dec-erp-001-single-warehouse-first\|DEC-ERP-001]] |

## Assumptions <!-- sec: assumptions -->

| id | statement | source | affects | status | opened_on |
|---|---|---|---|---|---|
| AS-INV-003 | Negative on-hand quantity is never permitted. | inferred from the goods-receipt walkthrough; owner confirmed | [[business-analysis/shop/domains/inventory/rules/stock-item-lifecycle\|BR-INV-001]] | confirmed | 2026-07-08 |
```

The mint cells stay bare (BR-INV-001 mints here); the cites and affects
cells are escaped-pipe wikilinks to the owning docs, collapsed back to
bare ids in the registry. The owning domain overview at
domains/inventory/shop-inventory-domain.md carries `code: INV` and the
matching alias:

```markdown
---
type: domain
title: Inventory
code: INV
status: approved
approved_at: 2026-07-09
owner_role: business_analyst
tags:
  - doc/domain
  - status/approved
aliases:
  - INV
---
```

Every active BR is cited by at least one AC somewhere in the space or
the compiler flags it; the registry's cited_by column is the backlink
proof.
