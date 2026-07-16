# Graph and Payload

The committed `.obsidian/` payload makes every clone of the vault open
the same way: same link format, same graph clusters, same property
types, same brand. Setup materializes it from `templates/vault/`
per-file, only where missing; the vault check asserts the
contract-bearing keys.

## Committed files and their sentinels

- `app.json`: `useMarkdownLinks: false`, `newLinkFormat: "absolute"`,
  `alwaysUpdateLinks: true`, `attachmentFolderPath` = the policy
  attachments directory. These control what the vault APP writes; with
  them, an owner-created link or in-app rename lands in the same
  vault-absolute form the agents write.
- `core-plugins.json`: core plugins only, the bases plugin off in this
  generation; community plugins are not part of the product (no trust
  prompts, no runtime dependencies). Base-file views are likewise off;
  hubs must be markdown notes because only real wikilinks draw edges.
- `graph.json`: one slash-anchored color group per subtree
  (`path:"solution-design/"`), an explicit delivery group, then the
  maps/home group, first match wins. `showOrphans: true` and
  `hideUnresolved: false` stay on: a defect the graph hides is a defect
  the team ships.
- `types.json`: derived from the policy's `property_types`; the check
  restores drift, so property types never fork per machine.
- `snippets/brand.css` (enabled via `appearance.json`): house accent,
  heading and callout colors and graph variables, in light AND dark
  theme selectors; no layout overrides.

## What stays user-local

`workspace.json`, `workspace-mobile.json` and `.trash/` are the
gitignored UI state. Global-graph forces and filters beyond the
committed groups, and every local-graph setting, are per-user; teach
owners the local graph (depth 1-2) as the daily tool and the global
graph as the onboarding and QA view.

## Reading the graph as QA

- A cluster with no internal edges means leaves are not cross-linked:
  nav peers are missing or formulaic.
- A node with no edges is an orphan the check should have caught; run
  the vault check before trusting the picture.
- Unresolved (ghost) nodes are broken citations; they render because
  hiding them would hide defects.

## Callouts

Callouts keep the alert grammar (`> [!NOTE]`, `[!IMPORTANT]`,
`[!WARNING]`, ...); it renders as a callout in the vault app and as an
alert on the code host, so no dual syntax exists.
