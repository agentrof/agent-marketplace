# Experience Design Flow

Spawn template: provide every role with `{{constitution}}`, exact upstream
receipts, the current `application@rN` when one exists, the exact process set,
affected package/map paths, the canonical application path and a required
`SELF-CHECK`.

This flow maintains living, process-owned Experience packages. It does not
create releases, numbered baselines, programs or delivery state.

## Entry and scope proposal

1. Read this flow fully, `experience-modeling`, `obsidian-vault` and the
   current approved BA, Solution and Design System packages.
2. Resolve the mode exactly. An explicit `REQ-###` is Requirement mode;
   otherwise it is manual mode. Manual mode must not inspect or create a
   Requirement, Stage Impact or Stage Results.
3. In Requirement mode run `requirement_route.py` first. Accept `author` only
   for a real change; accept `bind_reuse` only after every supplied living
   Experience process receipt and the supplied `application@rN` verify current.
   Do not mutate another stage.
4. Always run `experience_compile.py propose` first and retain its JSON only
   in a transient local scope-plan file. The proposal identifies
   primary BA process ownership and each `create`, `update`, `reuse`, `rename`
   or `retire` action, any independent application-only revision, the current
   approved process set and application receipt, exact upstream receipt hashes
   and, in Requirement mode, the Requirement semantic state. Ask the user to
   approve that complete action set before any Experience mutation. Ambiguous
   process ownership or an unclassified application delta is a blocker.

## Preflight and authoring

1. Requirement mode obtains BA, Solution and Design references from the
   current Stage Results. Manual mode requires one exact approved/current BA,
   Solution and Design receipt; never choose among multiple candidates. The
   Design System input must be contract v3 and its exact receipt/source binding
   is application metadata. Every active process package must bind that same
   receipt; include any needed package revisions in the approved scope plan.
2. Process records live only under
   `workspace/docs/experience-design/experiences/<process-slug>/`. The slug
   names the primary BA process, never `application`, a Requirement, component,
   release or `exp-` prefix. `application` is also reserved from aliases. One
   primary process has one active Experience.
3. Use the same transient `--scope-plan <file>` and approved
   `--proposal-hash <hash>` for every create, revision, rename, retirement and
   set approval. A canonical primary process is
   `business-analysis/<space>/(domains/<domain>/)*/processes/<process-slug>-process`;
   it must resolve through the selected BA compiler topology and receipt. The
   same resolver validates primary and related process references. A no-op
   revision is prohibited. Rename and retirement proposals automatically add
   every package with a live reverse exact-ref as an `update` action. Open all
   of those revisions before the lifecycle mutation; then revise/supersede the
   affected records so no live graph keeps an alias or retired target.
4. UX Designer is the only writer. Child records use stable `JRN`, `FLW`,
   `SCR`, `STA` and `TRN` identities, exact refs and `record_state` only.
   Place all canonical records in the process-owned package; `_generated/`
   and `_ledger/` are compiler-owned.
5. Keep exactly one Experience HTML implementation at
   `workspace/docs/experience-design/artifacts/application.html`. Start from
   the compiler-provided skeleton and preserve its fixed declarative,
   CSP-bound, network-free runtime. Author only declarative application content
   and the exact Design System binding. Declare route/state entries and
   deterministic local simulations for the complete state taxonomy, including
   context-preserving transitions and intentional returns. Keep the shipped
   style scaffold byte-exact; add CSS only in the marked author-style block and
   consume approved catalog variables for visual values. Require the complete
   single-value token contracts in exact root, dark, responsive order; opaque static palette values
   with enforced contrast and non-collapsing critical dimensions. Token blocks
   contain no ordinary CSS beyond the canonical responsive root override. State
   text colors meet 4.5:1 against both base surfaces; focus/border meet 3:1.
   State colors cannot serve as surfaces without a paired on-state contract. Font/easing/shadow values
   must be valid in their consumer-property grammar.
   Use the closed semantic element set, normalized single-token IDs and labels
   without invisible/control/default-ignorable code points or non-ASCII
   separators, native controls and the fixed role/state
   model; preserve native ownership and sequential keyboard access for form, search, filter
   and context controls; every radio context group shares one native form
   owner. Use one exact variable from the matching semantic
   token class for authored visual values; CSS-wide visual resets are invalid. Token-free layout values use the
   closed flow-preserving grammar; reject reverse/dense flow and explicit grid
   placement. Do not use CSS nesting,
   custom/vendor properties, HTML presentational sizing, browser-owned
   invocation attributes (including `accesskey` and non-boolean `hidden`), inline SVG/MathML, runtime-reserved DOM names,
   CSS-generated text (`content`, quotes or counters), CSS direction/bidi or
   non-`none` text transforms, non-collapsing white-space, forced-colors opt-out,
   arbitrary scripts, network/capability elements or a package-local preview.
   Embedded media is validated static PNG/JPEG data only. Contractual
   `aria-labelledby` targets are rendered plain-text leaves without another
   ARIA name. Dialogs use passive naming/description ARIA and optional exact
   `aria-modal="true"`; do not author `closedby`. Search/filter collection items
   are non-list, mutually non-nested, non-form, route-controller-owned and never disclosure
   targets. Listboxes contain only direct text-only canonical option controls.
   Return controls exist once exactly on declared return-target routes. Every
   routed-form submit has visible text plus a name, is reachable, sequential and
   passive-ARIA-only; image submits are invalid. Its form owner has no
   `tabindex`, and field constraints have a provable valid
   domain. Only the fixed announcer owns live-region semantics; unmanaged native
   widgets are invalid. Passive ARIA descriptions bind visible scalar content or
   unique exact in-body targets. ARIA is globally allowlisted and state/relation
   attributes are exact-owner-only. All contract/map/receipt JSON strings are
   Unicode-scalar-only. Image alt text alone does not establish visible record or privacy content.
6. Every process package contains exactly
   `artifacts/application-map.json` as its artifact surface. Map every active
   qualified exact ref to one or more declared route/state entries in the root
   application. Artifact-manifest notes, extra Experience implementation files
   and `_generated/artifact-registry.json` are invalid; unknown Experience files
   are also rejected by the closed tree. Render after each
   coherent change, then run compiler, application and scoped vault checks.
7. The compiler, not HTML metadata, owns the application lifecycle in
   `_generated/open-application-revision.json`. That exact state binds the
   proposal, application action, package-action hash, approved preimage, one
   exact successor revision and `draft|in_review` phase. Only
   `enter-application-review` may transition `draft` to `in_review`; approval
   requires that phase and removes the state. Never author, copy or renumber it.
8. Every Experience CLI command takes one project-scoped cross-platform lock
   and recovers a durable prepared transaction journal before reading. Each
   mutation snapshots the exact Experience root and navigation map, fsyncs the
   journal before its first write and holds the lock through closing validation.
   After process death the next command restores that exact snapshot; a failed
   writer cannot roll back a later successful writer.

## Challenge loop

1. After an authoring milestone, invoke a fresh, read-only
   `experience-reviewer` for each applicable lens: process/criterion coverage;
   journey-state closure; failure/recovery; solution constraints; Design
   System/accessibility; cross-Experience ownership; and application fidelity.
2. Reviewers return structured transient findings only. They never write a
   `reviews/` tree, history note, round counter or lock.
3. UX Designer fixes every blocking finding in canonical records. A genuine
   unresolved fact belongs in `experience.md` as an assumption or open
   question. Mechanical checks establish exact map coverage and declared route
   bindings, not visual fidelity. The reviewer judges whether rendered routes,
   states and interactions faithfully express the records and Design System.
   Re-run only affected review lenses; repeat until no blockers.
4. The final fresh reviewer emits one exact-schema-v2 transient JSON
   attestation bound to the approved proposal and current application revision,
   status, source hash, process-package-set hash, coverage hash and application
   hash. It identifies `experience-reviewer`, carries a timezone-aware review
   timestamp and contains zero blockers.

## Approval and handoff

1. Move every affected package to `in_review`, render, then use one atomic
   `experience_compile.py approve-set --scope-plan <file> --proposal-hash <hash> --review-attestation <file>`
   invocation. It must cover the proposal's complete create, update, rename,
   retire and application action set, affected maps, root application and
   compiler-owned open-revision, ledger and receipt state. An application-only
   revision has an empty changed-process set and does not increment process
   revisions. Any
   failure rolls back the complete transaction; no partial result is a handoff.
   An exact application `reuse` with no package mutation is a reachable
   read-only approval path: it creates no open revision, consumes no reviewer
   attestation and returns only the verified current application/process
   receipts.
2. Every successful approval creates a new globally current
   `application@rN`, whether the delta is a package set or only the application,
   and returns it with the exact current zero-or-more process receipts; zero is
   valid only for a compiler-verified empty application. The preceding
   application receipt becomes non-current. When the last process retires,
   author one `application`-owned empty route and return only the application
   receipt; its revision sequence remains available for later processes.
3. Requirement mode binds the application and process receipts in one
   `requirement_compile.py bind-stage` operation. Manual mode returns the same
   complete set but writes no Requirement state. An existing Requirement or
   backlog bound to the preceding application must rebind through its normal
   revision before further handoff.
4. Hand off only that exact approved receipt set to `backlog-plan`. Do not
   create stories, product application code, system architecture or Delivery
   Items here.
