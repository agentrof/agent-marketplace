# Graph and Payload

The committed `.obsidian/` payload makes every clone of the vault open
the same way: same link format, same labels, same graph clusters, same
property types, same brand. Setup materializes it from `templates/vault/`
per-file, only where missing; the vault check asserts the
contract-bearing keys.

The whole `workspace/docs/` directory is the vault. Every subtree participates
in the same global graph, backlinks index and local graph. A Solution decision
can cite Business Analysis, an Experience screen can cite that decision and
Design System, and an ADR can cite the screen. Folder boundaries do not limit
Obsidian link resolution.

## Committed files and their sentinels

- `app.json`: `useMarkdownLinks: false`, `newLinkFormat: "absolute"`,
  `alwaysUpdateLinks: true`, `attachmentFolderPath` = the policy
  attachments directory. These control what the vault APP writes; with
  them, an owner-created link or in-app rename lands in the same
  vault-absolute form the agents write.
- `core-plugins.json`: the core set with the bases plugin off in this
  generation; base-file views are off because hubs must be markdown
  notes: only real wikilinks draw edges.
- `community-plugins.json`: exactly the policy's vetted
  `community_plugins` set, today the front-matter title display plugin
  vendored under `plugins/`. Anything not on the policy list stays
  banned; the payload check holds the enable list, the vendored
  directory and the policy in parity.
- `graph.json`: the GLOBAL graph's committed contract: its search
  filter and ordered color groups mirror the policy's `graph_search`
  and named `graph_color_groups`. Each policy record binds a stable id,
  exact query and RGB value, so reordering or extending the legend cannot
  move a color to another document type.
  `showOrphans: true` and `hideUnresolved: false` stay on: a defect the
  graph hides is a defect the team ships.
- `types.json`: derived from the policy's closed `property_types` using
  Obsidian-native `text`, `multitext`, `number`, `checkbox`, `date`,
  `datetime`, `tags` and `aliases`; the check
  restores drift, so property types never fork per machine.
- `snippets/brand.css` (enabled via `appearance.json`): house accent,
  heading and callout colors and graph variables, in light AND dark
  theme selectors; no layout overrides.

## The vendored title plugin

Graph, explorer, search and tab labels come from each note's `title`
frontmatter, not its filename, through ONE vetted community plugin:
the front-matter title plugin, pinned at release 4.1.1 and vendored
verbatim (`manifest.json`, `main.js`, our `data.json`, its LICENSE)
under `templates/vault/.obsidian/plugins/`. The plugin is GPL-3.0
licensed (not MIT); it ships with its license text intact as an
independent aggregated component beside this plugin's own content, so
its copyleft binds the vendored bundle only.

- Trust prompt: the vault app asks the owner ONCE to trust community
  plugins when the vault first opens. That click is the owner's, never
  automated, which is why the fallback truth below is documented
  instead of assumed away.
- Settings contract: `data.json` sets the display template to the bare
  `title` key and enables the explorer, graph, search, suggest (the
  quick switcher), tab and canvas features. `noteLink` stays OFF
  because that feature rewrites note files on disk, bypassing the
  write-time hooks; `alias` stays off for determinism. The payload
  check asserts exactly these feature keys, so a drifted key can never
  silently no-op.
- Minimum app version: the vendored manifest's `minAppVersion`
  (1.12.0) is the consumer's minimum vault-app version.
- Fallback truth: with the plugin absent, declined or broken (an app
  update can break a pinned build), nothing corrupts; labels fall back
  to the filenames, which the naming law keeps meaningful plain
  type-suffixed names.
- Update procedure: re-vendor a newer TAGGED release's `manifest.json`
  and `main.js`, keep our committed `data.json`, and review the new
  bundle at vendor time. Honest risk: `main.js` is third-party
  JavaScript running in the owner's vault app; pinning, vendoring and
  vendor-time review bound that risk, they do not remove it.

## Type-based color groups

The global graph is colored by document TYPE, not by folder. The
policy's `graph_color_groups` records own the stable palette and order
the queries, first match wins. Every type in the taxonomy owns a color:

- One `tag:#doc/<type-kebab>` group per doc type, across all trees:
  space, domain, glossary, actor-roster, budget-set, entity, process,
  rule-set, acceptance-set, decision, challenge-record, integration,
  landscape, engagement, design-master, page-override.
- `tag:#doc/moc OR tag:#doc/home`: the navigation layer as one group.
- Completeness is machine-guarded: the marketplace validator errors on
  a taxonomy type without a color group AND on a group whose tag names
  no known type (a dead legend). Adding a type forces its color in the
  same commit.
- The palette is authored once in the policy. The committed `graph.json`
  mirrors every query and RGB pair byte-for-byte, and the validator rejects
  any seed drift.

Graph queries support no pipe-OR and no tag wildcards; the policy
writes OR-joined full tags in the legal grammar, validated before the
payload ever ships.

## The global filter

The committed search (`-path:_generated`) hides disposable registries and
status boards from the GLOBAL graph only. Machine-owned relation and large
navigation catalogs under `maps/_relations` and `maps/_navigation` remain
graph-visible because they carry bounded real edges. The filter never touches
the local graph. Review records stay visible because they are authored
knowledge.

## What stays user-local

`workspace.json`, `workspace-mobile.json` and `.trash/` are the
gitignored UI state. New vaults receive the standard palette exactly.
Later color edits in an existing `graph.json` are treated as user
overrides and the normalize verb preserves them. Run the packaged
`vault_check.py standardize-graph-colors --vault workspace/docs` to discard
those overrides and restore every standard color. Global-graph
forces beyond the committed groups and search, and every local-graph
setting, are per-user; teach owners the local graph (depth 1-2) as the
daily tool and the global graph as the onboarding and QA view.

## Reading the graph as QA

- A cluster with no internal edges means leaves are not cross-linked:
  nav peers are missing or formulaic.
- A node with no edges is an orphan the check should have caught; run
  the vault check before trusting the picture.
- Unresolved (ghost) nodes are broken citations; they render because
  hiding them would hide defects.
- Duplicate or filename-shaped labels mean the title layer is degraded:
  confirm the plugin is enabled and run the checker's title findings
  down to zero.

## Callouts

Callouts keep the alert grammar (`> [!NOTE]`, `[!IMPORTANT]`,
`[!WARNING]`, ...); it renders as a callout in the vault app and as an
alert on the code host, so no dual syntax exists.
