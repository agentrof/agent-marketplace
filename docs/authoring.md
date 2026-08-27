# Authoring guide

This repository ships one host-neutral Software Engineering Team. Its durable
source is the plugin content under `plugins/software-engineering-team/`; host
wrappers are generated under `dist/` and are never edited by hand.

## Repository boundaries

- Keep canonical skills, agents, flows, scripts and templates under
  `plugins/software-engineering-team/`.
- Keep host-specific loading, permissions, choice behavior, and lifecycle
  behavior under the relevant `platforms/<host>/` adapter directory.
- Keep project content in the consuming repository's tracked
  `workspace/docs/` vault. `.agentrof/`, `.claude/`, `.codex/`, and `.opencode/`
  are local,
  ignored runtime/projection surfaces only.
- Keep the Software Engineering Team standalone and scope every operation to
  the current project checkout.

## Requirement Flow authoring contract

The user moves through these durable document gates:

```text
requirement -> business-analysis -> solution-design -> design-system -> experience-design -> backlog-plan -> delivery-plan -> execution-plan -> deliver
```

An exact `REQ-###` selects the Requirement-driven chain. Its impact matrix
decides `required`, `reuse` or `not_applicable`; every completed applicable
stage binds a current, approved, committed package receipt. Without `REQ-###`,
the same entries are manual: the user explicitly selects approved/current
upstream packages and no Requirement, Stage Impact or Stage Results state is
created. A stage is complete only when its package is approved and committed.
`requirement_route.py` reads only Requirement-driven durable state; manual
entries never call it.

Experience Design is the aggregate exception to the one-receipt wording. Its
stage result contains the globally current `application@rN` receipt and the
exact current zero-or-more process receipt set. Zero process receipts are valid
only for the verified empty application. Any later approved application or
package-set delta makes that application receipt non-current, so a Requirement
or backlog that consumes Experience must rebind through its own normal revision
before a new handoff.

The complete current lifecycle, Delivery tree and Git coordination contract is
defined in [requirement-delivery-protocol.md](requirement-delivery-protocol.md).

## Backlog contract

The canonical tree is:

```text
workspace/docs/backlog/
├── backlog.md
├── reviews/
│   └── round-<n>-backlog-review.md
├── epics/<epic-slug>/
    ├── epic.md
    ├── reviews/round-<n>-epic-review.md
    └── stories/<story-slug>/
        ├── story.md
        └── test-plan.md
└── _generated/
    ├── registry.json
    ├── board.md
    ├── dependency-map.md
    └── test-coverage.md
```

The six fixed backlog type keys are `backlog`, `backlog-review`, `epic`,
`epic-review`, `story` and `test-plan`. Their project-selected display labels
are authored directly in each note's `title`. Colors, graph queries and
path/type rules are fixed in `vault-policy.json` and rendered into
`.obsidian/graph.json` and `.obsidian/types.json`. Issue reporting stays outside
the vault and cannot serve as backlog evidence.

Unless a subtree defines a closed artifact contract, files below an
`artifacts/` directory under a policy-valid vault folder are opaque local
artifacts, not Markdown notes. Their names and extensions are unconstrained,
symlinks are rejected, and authored Markdown may link to a real artifact with
a relative Markdown link or embed.

A contract-v3 Design System pairs `MASTER.md` with
`design-system/artifacts/standalone.html`. The catalog's page and specimen
slots are fixed, but its colors, type, spacing, dimensions, elevation and
motion come only from the marked MASTER token block. Run `init-catalog` only
to create a missing skeleton and `sync-catalog` after MASTER changes; approval
requires a complete, current, offline catalog.

Experience Design instead has a closed aggregate application contract. The
only Experience HTML is
`workspace/docs/experience-design/artifacts/application.html`; each process
package contains only `artifacts/application-map.json` in its artifact folder.
The version-2 map connects every active exact qualified ref such as
`checkout:SCR-001@r2` to one or more exact `route` + `state_class`
entries. The version-2 inline contract declares those routes, the complete
ordinary/loading/empty/validation/permission/stale/conflict/failure/retry/
recovery taxonomy, transitions and deterministic local outcome simulations.
The HTML retains the shipped declarative runtime and its exact CSP hash,
performs no network access, executes context-preserving and intentional-return
transitions, and carries the exact approved contract-v3 Design System binding
in its application metadata. Every active process package must bind that same
receipt. The bound token block must expose the complete canonical application
token set in one `:root`, then one complete
`[data-catalog-theme="dark"]` palette and the final canonical responsive scope.
Every required token has one direct, concrete
value: conditional, duplicate, empty, CSS-wide and unresolved values are
invalid. Required palette values are statically verifiable opaque hex colors;
text colors meet 4.5:1 against both base surfaces; focus and border tokens meet
3:1 against both. State colors cannot be authored as surfaces without a paired
on-state token contract. Layout/focus/type values cannot collapse to zero. The token
block accepts no ordinary selector/property: only the exact
direct root/dark custom-property scopes and canonical responsive root override
are valid. Font-family, motion-easing and shadow values must also be valid in
their consumer-property grammar; URL/raw-style escapes are invalid. The scaffold selects a deterministic light color scheme until the fixed runtime selects the
dark scope. The fixed style scaffold is immutable; application-specific CSS lives
only inside the marked author-style block, cannot redefine catalog tokens and
uses approved catalog variables for visual values. Author HTML uses a closed
semantic element set, normalized single-token IDs and labels without
default-ignorable/control code points or non-ASCII separators, class token lists
with only ASCII whitespace separators, native controls,
and only the fixed `status`, `listbox` and `option` explicit roles. Browser
capability/custom elements are invalid. HTML comments, bogus declarations and
parser-ambiguous markup are invalid. The direct document title exactly matches
the fixed visible brand. The body, toolbar, preference controls and main
route-root slots have one exact closed topology; content cannot live outside
a declared direct route root. Every `lang` uses the closed BCP47 grammar, every
`dir` is exact `ltr`, `rtl` or `auto`, and `xml:lang` is invalid. Native form, search, filter and preserved-context
controls keep their native owner, keyboard order and state semantics. Radio
context groups also share one native form owner; split native groups cannot
masquerade as one preserved context value. Search/filter collection items use
non-list, non-form browser-stable semantic containers and cannot contain one another.
Every item-bearing route owns exactly one matching controller, and disclosure
targets cannot also be collection items. An initially hidden collection item is
validated in the eventual-visible state because the runtime may reveal it.
Routing and action controls remain outside every collection item the runtime can
hide, so filtering cannot disconnect the declared route graph.
Each `data-filter-value` is an exact, single-ASCII-space-delimited list of
distinct lowercase tokens; validator and runtime tokenization are identical.
Listbox, filter, select, radio and checkbox choice domains use distinct,
non-empty normalized accessible names as well as distinct values. Every routed-form submit affordance
has visible text plus an accessible name, is reachable, sequentially keyboard
reachable and passive-ARIA-only; image submits are invalid;
the form owner itself cannot declare `tabindex`, and every field constraint set
must have a mechanically provable valid domain. Preserved-context native
controls obey the same type-appropriate satisfiable-domain rule. Form owners, fields and submit
controls share one exact disclosure/dialog topology; hidden submitters cannot
activate the runtime. An implicit `label` has a visible scalar caption and
exactly one labelable descendant; hidden, inert, privacy-masked or
collection-controlled caption text cannot name it. `aria-labelledby` uses one
exact ASCII ID token, while `aria-describedby` lists only ASCII-whitespace
separated ID tokens. Their targets are static, reachable, passive text leaves,
not form-widget state, privacy content or search/filter items, and share the
consumer's exact route/disclosure/dialog visibility topology. Every `optgroup` is enabled, non-empty and
has a visible scalar label. Tables are outside the closed application element
set. A listbox contains only direct,
text-only canonical option buttons. Return controls exist exactly once on each
route targeted by a declared non-empty return. `accesskey` and non-boolean
`hidden` values such as `until-found` are unmanaged browser state and invalid;
so are author `title`, `placeholder` and explicit `label[for]` channels.
Only the fixed application announcer owns live-region semantics; `output`,
`meter`, `progress` and other unmanaged native announcement/widget channels are
invalid. CSS `direction`/`unicode-bidi`, non-`none` text transforms and every
author `white-space` value except `normal` are invalid; use semantic HTML text.
CSS whitespace is only TAB/LF/FF/CR/SPACE; other Unicode separators and controls,
including escaped forms, make author CSS invalid.
Intrinsic sizing is fixed-scaffold-owned: author CSS cannot set width/height,
inline/block size or their min/max variants. The scaffold enforces the bounded
touch-target token on controls and constrains images to their container.
Fixed `application-*` scaffold classes have exact owners and cannot be reused
by route content. Embedded media
is limited to validated, non-interlaced, non-palette static PNG data images with
one terminal `IEND` chunk.
`data-private` marks only a reachable, text-only passive leaf outside headings,
labels and controls; application identity and control purpose remain readable
in masked mode.
Browser-sanitized date/time/number-like input values do not prove visible
record or privacy content. Passive `aria-description` text must be visible scalar
content; `aria-describedby` must be a non-empty unique IDREF list whose targets
are exact in-body description nodes. ARIA is globally allowlisted; every
state/relation attribute belongs only to its exact canonical owner. Contract,
map, registry and ledger JSON
strings must contain only Unicode scalar values.
External JSON is also bounded by the canonical byte, node and nesting limits;
author CSS has a bounded at-rule nesting depth. An inline `style` attribute is
invalid even when empty, and authored `tabindex` is absent or exact `0` (the
fixed main alone owns exact `-1`).
Author CSS is fail-closed: custom/vendor properties and nesting are invalid;
visual values use one exact variable from the matching semantic token class.
Length, motion, easing, layer and shadow tokens also have token-specific closed
bounds; merely positive CSS values are insufficient.
CSS-wide resets are allowed only by the closed token-free layout grammar, and
a surface token cannot also become a visible foreground or state
cue on selectors that may target the same element. Token-free layout values use
a closed property-specific grammar. Author grid layout, content-box sizing and
horizontal margins are runtime-owned; flex/inline-flex declarations require
same-rule wrapping. The fixed scaffold forces border-box geometry, long-token
wrapping and container-bounded controls/images. Reverse/dense flow and explicit grid
placement are invalid because visual and DOM/tab order must agree. HTML presentational sizing
attributes, browser-owned invocation attributes, inline SVG/MathML and
runtime-reserved DOM names are invalid. Image alternative text is an
accessible name, not mechanical proof of visible record or privacy content.
Generated-content CSS (`content`, quotes and counters) is invalid because it
could diverge visible or accessible labels from the contract. Author styles
also cannot disable forced-colors adaptation. An
`aria-labelledby` target used for a contractual name must be one rendered,
plain-text leaf with no competing ARIA naming override. Native dialogs use
only passive naming/description ARIA and, when authored, `aria-modal="true"`;
their runtime modal state cannot be contradicted and author `closedby` policy is
not allowed.
The Experience tree is a closed file surface. Package-local previews, artifact
manifest notes, arbitrary files and the former artifact registry are invalid
rather than compatibility formats.

The Experience compiler reserves `application` from process slugs and aliases.
Its approval transaction covers every create, update, rename or retire action,
the affected package maps, the aggregate application and compiler-owned
open-revision and receipt state. Every mutating compiler command runs under one
project-scoped lock; a durable runtime journal and exact preimage recover an
interrupted mutation before the next command. An application-only revision
changes no process revision, but it still produces a new globally current
`application@rN`. Every approved package-set delta does the same and returns
the exact current zero-or-more process receipts with it. The durable application history is
`experience-design/_ledger/application-revisions.json`; its current generated
projection is `experience-design/_generated/application-registry.json`.
Mechanical checks establish exact ref/route/state coverage, reachable outcome
paths, closed files, runtime/CSP integrity and receipt bindings. Approval also
requires a transient fresh `experience-reviewer` attestation bound to the
proposal and exact in-review application revision, status, source,
package-set, coverage and application hashes, with zero blockers.
Reviewers, not those checks, decide whether the rendered application faithfully
expresses the records and Design System.

`backlog_compile.py` is the only backlog helper. It creates deterministic
stubs, validates front matter and nested paths, checks one owner and any
supporting roles, resolves upstream and dependency links, checks unique IDs and
dependency cycles, requires every story to have a test plan, maps every
acceptance criterion to at least one Given/When/Then scenario, and renders
disposable JSON/board/coverage views under `backlog/_generated/`.

Every story contains User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. It names one
`owner_role` and may name unique `supporting_roles`; each listed role has a
concrete responsibility and the owner cannot repeat as supporting. The fields
hold team role identifiers only.

`criterion_refs`, `derives_from`, `depends_on`,
`uses_design` and `constrained_by` are vault-absolute wikilinks. Criterion and
rule links resolve to exact stable headings in approved upstream notes.
Dependencies target stories and have matching reasons in the story body.
`experience_refs` instead use exact living Experience aliases such as
`checkout:SCR-001@r2`, resolved through the owning approved package registry
or durable ledger for historical work. Each selected active ref must also be
covered by the pinned application's package map. Backlog input bindings retain
both the globally current `application@rN` and the current owning process
receipts.

An epic review derives from its epic and verifies the exact story/test-plan set
below it. The root review derives from the backlog and relates to the exact
epic set. Approval is blocked until the root backlog, both review layers and
every story test plan are approved. Test-plan scenarios may be marked
`automation: required` or `manual`; required scenarios name their planned
automation target. Delivery execution consumes the approved backlog and does
not rewrite its source.

Epic-review sections are Scope, Slicing, Criteria Coverage, Test Design,
Dependencies, Role Ownership, Findings and Verdict. Root-review sections are
Epic Coverage, Cross-Epic Overlap, Cross-Epic Dependencies, Delivery Sequencing,
Shared Contracts, Deferred Criteria, Global Test Coverage, Findings and
Verdict.

## Host and runtime contract

Claude Code and Codex install the same standalone team through their native
marketplaces. OpenCode projects instead use the generated project projector;
it creates no Agent Marketplace global install or dependency. Setup creates
only a project-local runtime and host
projection, reconciles the tracked vault contract plus the ignored local
community-plugin projection, and runs the same convergence check on each
native marketplace host. Generated distributions come from:

```text
python3 tools/build_distributions.py
make check
```

Package upgrades run `setup_project.py inspect`, `apply` and `check`, preserve
authored Markdown and user-owned configuration, and roll back setup-owned
writes when the closing check fails. Completed-stage routing requires only its
relevant docs and config to be committed and clean. Delivery upgrade policy
keeps this file-first, project-local boundary. The complete refresh
sequence and compatibility rules live in [upgrade-protocol.md](upgrade-protocol.md).

## Validation expectations

Run `make check` before committing. It validates repository contracts, package
receipts, generated distributions, focused compiler tests and runtime scripts.
Use `make counts` only to refresh derived README counts. Never edit `dist/`
directly.
