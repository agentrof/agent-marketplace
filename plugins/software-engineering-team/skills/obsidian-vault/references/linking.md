# Linking

The vault's value is its edges. Every citation is an edge or it is
invisible; every edge follows one grammar or the graph lies.

## Wikilink grammar

```markdown
[[solution-design/decisions/sd-007-order-events|SD-007]]   link, aliased
[[business-analysis/shop/space|analysis space]]            link, aliased
![[_attachments/checkout-flow.png]]                        embed
[[business-analysis/shop/budgets#^event-volume|budget]]    block anchor
```

- Targets are vault-absolute: the path from the vault root, forward
  slashes, exact case, no leading slash, no `.md` extension on notes.
  Never rely on shortest-name resolution; contract filenames repeat
  across subtrees and the tie-break is undefined.
- Always alias prose links: the reader sees the id or a noun phrase,
  never a raw path. A bare id in prose is always the ALIAS of a link to
  the doc that mints it.
- Embeds (`![[...]]`) are for attachments and deliberate transclusion
  only; a normal citation is a link, not an embed.

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
| decision | [[solution-design/decisions/sd-007-order-events\|SD-007]] |
```

Schema-declared id columns in analysis registries keep BARE ids (they
are machine-parsed data; the prose citation carries the edge).

## What stays a markdown link

- External URLs: `[text](https://...)`.
- Targets OUTSIDE the docs tree (sketches, demos, environment files):
  standard relative markdown links. They do not resolve inside the vault
  app; say so in the sentence when confusion is possible.

## Renames

The vault app's rename refactoring never runs headlessly. A rename is a
migration: grep the old path across the vault, rewrite every referrer
(body links, frontmatter values, map rows) in the same commit, then run
the vault check. Decision notes are never renamed after acceptance; the
alias and the generated index absorb discoverability.
