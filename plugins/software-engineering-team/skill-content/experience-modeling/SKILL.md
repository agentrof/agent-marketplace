---
name: experience-modeling
description: Internal knowledge for living, process-owned Experience packages, author-owned prototype snapshots, stable record identities and deterministic lifecycle gates.
exposure: internal
---

# Experience Modeling

Treat Experience Design as a living graph owned by primary Business Analysis
processes, not as a delivery implementation or numbered baseline.

## When to Use

Load for Experience authoring, review and exact Experience references in
backlog planning.

## Core rules

- Follow `obsidian-vault` for every docs-tree path, metadata, link and artifact
  policy decision.
- Use `experiences/<process-slug>/experience.md`. The slug is the semantic
  primary BA process, never `application`, `exp-*`, a Requirement, release or
  technical component.
- One active Experience owns one primary process. Cite records as
  `checkout:SCR-001@r2` and packages as `checkout@r3`.
- Revise in place with `begin-revision`. Changed children increment and
  supersede; unchanged IDs and revisions persist; retired records stay in
  ledgers. Package approval belongs only to the root package.
- Requirement and manual modes share proposal, authoring, challenge and
  approval. Requirement mode alone adds traceability and binds final receipts.
- A transient scope proposal binds the full action set, selected input receipt
  hashes, primary processes, process set and current application receipt. Pass
  its exact file and hash to every mutation and approval.
- Generate with `experience_compile.py`, invoked through the active host
  runtime's exact absolute Python executable and the installed script's
  absolute path. Bare Python names, `/usr/bin/env` indirection and direct
  shebang execution are guard-only because the shell can replace them before
  execution. `_generated/` is disposable and `_ledger/` is durable compiler
  truth.

## Prototype boundary

`workspace/docs/experience-design/artifacts/` is an opaque, author-owned
prototype tree. Authors may choose its files, directories, languages, tools,
dependencies, pages, links, media, behavior and presentation. The compiler
does not parse HTML, CSS, JavaScript, assets, framework output or any prototype
convention. It never supplies a UI skeleton or rewrites prototype files.

Use sensible conventions when they help the team, for example `index.html`,
separate pages, `css/`, `js/` and media folders, but treat them as advice rather
than acceptance requirements. Delivery later implements the approved product
under its own engineering standards.

The only mechanical prototype constraints are lifecycle boundaries: files must
remain inside the artifact tree, be regular non-symlink files when snapshotted,
and match the approved byte inventory. Active Experience packages need at
least one artifact before review. The receipt records the sorted file paths,
bytes hashes, artifact-tree hash and package-set hash. It does not claim UI
quality, accessibility, security or implementation suitability.

## Lifecycle and review

- Application and process revisions are independent. An application-only
  revision leaves process receipts unchanged but creates `application@rN`.
- `_generated/open-application-revision.json` binds proposal, action,
  predecessor and successor revision with `draft|in_review` phase. It is
  compiler-owned; prototype files hold no lifecycle metadata.
- When a package set composed only of `draft` or `in_review` non-retire
  mutations is stranded on an obsolete proposal, generate a fresh recovery
  proposal from the exact old plan plus current input and application
  bindings, then use `recover-open-scope` with both exact plans and hashes.
  Recovery must bind the old proposal hash into the fresh plan, prove both
  plans and every open package name the same complete mutation set, rebind it
  and the open application atomically, retain package revisions, reset review
  to `draft`, preserve authored child records and prototype/package artifact
  bytes, and leave approved ledgers and receipts untouched. A scope containing
  `retirement_pending` or a retire action fails closed. A legacy scope may
  already carry only the fresh plan's exact current input bindings; all other
  package identity, revision and Requirement bindings remain exact.
- If an immutable `application@rN` already publishes the exact open package
  revisions, `recover-open-scope` must fail closed. Use
  `rehydrate-published-scope` with the exact old scope plan, proposal hash and
  application ref. It restores package roots only after their historic source
  bytes reproduce every published package hash, leaves application receipts and
  artifacts untouched, and returns to the ordinary successor-revision flow.
  For r2+ packages, rehydration also needs an intact prior `_ledger/` history.
  The application receipt stores hashes, not historic registry or record
  snapshot bytes, so restore the complete package ledger from a trusted backup
  before retrying if that history is absent.
- `enter-application-review` snapshots the current artifact tree. Approval
  requires a fresh exact-schema-v4 reviewer attestation bound to proposal,
  artifact-tree, package-set and application hashes. Its `advisories` remain
  informational and cannot become compiler rejection rules.
- Reviewers judge fidelity, usability, accessibility, interaction quality,
  visual quality, architecture and risks from the actual prototype. A passing
  compiler check is only evidence of snapshot integrity.
- An `application` reuse action is read-only. If the final process retires,
  approval records an empty artifact inventory and no process receipts.
- Create, update, rename and retire are one crash-recoverable transaction over
  packages, artifact receipt state and generated lifecycle state. A newer
  application receipt makes old Requirement and backlog bindings non-current.
