# Linking

The vault's value is its edges. Every citation is an edge or it is
invisible; every edge follows one grammar or the graph lies.

## Wikilink grammar

```markdown
[[solution-design/decisions/order-events-decision|SD-007]] link, aliased
[[business-analysis/shop/space|analysis space]]            link, aliased
![[_attachments/checkout-flow.png]]                        embed
[[business-analysis/shop/budgets#^event-volume|budget]]    block anchor
```

- Targets are vault-absolute: the path from the vault root, forward
  slashes, exact case, no leading slash, no `.md` extension on notes.
  Never rely on shortest-name resolution: the vault-absolute path is
  the one grammar the checker resolves deterministically, and it stays
  valid however the tree grows.
- Always alias prose links: the reader sees the id or a noun phrase,
  never a raw path. A bare id in prose is always the ALIAS of a link
  to the doc that mints it; an id-shaped alias on any other target is
  an `alias_ownership` error.
- Embeds (`![[...]]`) are for attachments and deliberate transclusion
  only; a normal citation is a link, not an embed.
- Coverage and gates count only typed outgoing relations. Free prose links
  remain graph edges but are not traceability proof. General knowledge links
  may cycle; lifecycle, inheritance, and dependency relation graphs are DAGs.

## Anchors

- Heading anchors (`#Some Heading`) are banned: heading text is
  output-language prose, rewrites kill the anchor silently, and the
  checker cannot hold prose stable. Link the note itself.
- Where a row or paragraph inside a note needs a stable handle, mint a
  block id at the end of that line (` ^event-volume`) and cite
  `[[path#^event-volume|display]]`. Block ids are kebab ASCII, minted
  once, never renamed.

## Tables

Inside a table row the alias pipe must be escaped or it splits the cell:

```markdown
| decision | [[solution-design/decisions/order-events-decision\|SD-007]] |
```

Schema-declared id-citation columns (cites, affects, blocks, targets,
verify) carry the SAME escaped-pipe wikilink form, targeting the doc
that mints the id: `[[business-analysis/shop/domains/inventory/rules/stock-item-lifecycle-rules\|BR-INV-001]]`.
The compiler normalizes citation cells back to bare ids for the registry.
A bare id left in a citation cell is an error the `normalize` verb rewrites.
The ONE place a bare id stays legal is its mint: the id
column of the owning row, where a wikilink is the error instead.

## What stays a markdown link

- External URLs: `[text](https://...)`.
- Targets OUTSIDE the docs tree (sketches, demos, environment files):
  standard relative markdown links. They do not resolve inside the vault
  app; say so in the sentence when confusion is possible.

## Renames

Headless renames are owned by the checker, not by the vault app or a
manual grep: `vault_check.py normalize --rename [--dry-run] [--json]` builds
the grammar-driven rename map (plain named files, type-suffixed content
notes; already-compliant files are skipped), then renames and
rewrites every referrer across the WHOLE vault (body links, frontmatter
values, map rows) in one operation, even when `--scope` narrows the
map; generated views are re-rendered by their owning verbs afterwards.
A rename whose referrers include an explicitly excluded repair-scope path is
vetoed and reported as `blocked_by_excluded_path` with the blocking paths;
`--dry-run`
prints each source -> target pair with its referrer count for the gate
conversation. Decision notes are never renamed after acceptance; the
alias and the generated index absorb discoverability.
