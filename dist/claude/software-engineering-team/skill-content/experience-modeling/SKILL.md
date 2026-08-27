---
name: experience-modeling
description: Internal knowledge for living, process-owned Experience packages, their one aggregate application, stable record identities, revisions, coverage and deterministic gates.
exposure: internal
---

# Experience Modeling

Treat Experience Design as a living graph owned by a primary Business Analysis process, not as a deployment release or numbered baseline.

## When to Use

Load for Experience authoring, review and exact Experience references in backlog planning.

## Core Rules

- Follow the `obsidian-vault` skill for every docs-tree path, metadata, link and artifact-policy decision in this contract.
- Use `experiences/<process-slug>/experience.md`. The slug is the semantic primary
  BA process, never `application`, `exp-*`, `EXP-*`, a Requirement, release or
  technical component; `application` is reserved from slugs, targets and aliases.
- One active Experience owns one primary process; related packages use exact refs.
- BA process refs are vault-relative extensionless topology paths accepted as
  approved by the selected strict-current BA compiler.
- Use `JRN`, `FLW`, `SCR`, `STA` and `TRN` stable IDs. Cite records as
  `checkout:SCR-001@r2` and packages as `checkout@r3`.
- Revise in place with `begin-revision`: changed children increment and
  supersede; unchanged IDs/revisions persist; retired records remain in ledgers.
- The root package owns lifecycle approval. Child records use only
  `record_state: active|retired`; child approval fields are forbidden.
- Requirement/manual modes share proposal, authoring, challenge and approval;
  only Requirement mode adds traceability and binds the final receipt set.
- A transient scope proposal hashes the full action set, selected receipt
  hashes, canonical primary processes, approved process set, current application
  receipt and Requirement state. Pass its exact file/hash to every mutation and
  approval; it covers all actions and whether the application changes.
- Generate with `experience_compile.py`; `_generated/` is disposable and
  `_ledger/` is durable compiler truth.
- Experience has exactly one HTML implementation at
  `workspace/docs/experience-design/artifacts/application.html`. Its shipped
  runtime is fixed, declarative and network-free. Authors change its
  declarative application content, not the runtime, and its metadata binds the
  exact approved contract-v3 Design System receipt and source. Every active
  process package binds that same receipt; a scope plan must include any
  package revisions needed to restore that equality.
- Each process package has only `artifacts/application-map.json`; schema v2 maps
  every active qualified ref to root route/state pairs. The inline schema-v2
  contract declares all states, simulations, context and returns. The fixed
  style scaffold is immutable;
  author CSS stays in its marked block, cannot redefine tokens and uses approved
  variables. Tokens have ordered root/dark scopes, contrasted opaque palettes
  and positive dimensions. State text
  colors contrast with both base surfaces and cannot act as surfaces. The token
  block permits only property-valid font/easing/shadow values and the canonical
  responsive override. Markup fixes title/brand/body/scaffold/direct-route topology,
  closed tags, native controls/roles, valid `lang`/`dir` and no HTML comments.
  Preserve form/search/filter/context ownership and tab order; radios share one
  form owner. Choice values and accessible names are unique; validate hidden
  items as eventual-visible; actions stay outside. CSS rejects custom/vendor/nested CSS,
  resets, grid, horizontal margins and content-box; flex requires same-rule wrap.
  Sizing/scaffold classes are runtime-owned; type/spacing use bounded tokens.
  Reject direction/bidi, transforms, non-normal white-space, inline `style`,
  invalid `tabindex`, presentational sizing, browser invocation (`accesskey`,
  `title`, `placeholder`, `label[for]`, non-boolean `hidden`), SVG/MathML and
  reserved names. Images are static PNG through terminal `IEND`; alt
  is not visible content. Reject generated text, forced-colors opt-out and
  ARIA IDREFs use ASCII separators and static passive text leaves. Dialog ARIA is
  passive naming/description plus optional `aria-modal="true"`; reject
  `closedby`. Labels own one control; private content is a passive text leaf; optgroups need labels; tables are invalid.
  Listboxes use direct text-only options. Return controls exist once per
  declared return-target route. Routed-form submits are
  visibly named, reachable, sequential and passive-ARIA-only; image submits are
  invalid. Form/context controls need valid domains and matching visibility. Only the
  fixed announcer owns live-region semantics; unmanaged native widgets are
  invalid. ARIA is globally allowlisted and state/relation attributes are
  exact-owner-only. Passive descriptions bind visible scalar text or unique
  targets; external JSON is scalar-only. Unknown/package-local files, extra
  artifacts/manifests and registries are invalid.
- Application and process revisions are independent. An application-only
  revision leaves every process receipt unchanged. Approval still creates a
  new globally current `application@rN` receipt and returns it with the exact
  current process receipt set. Any approved package-set delta also advances
  that receipt.
- Application revision state is compiler-owned at
  `_generated/open-application-revision.json`. Its exact schema binds the
  proposal, application action, package-action hash, approved application
  preimage, one exact successor revision and `draft|in_review` phase. HTML
  metadata cannot advance lifecycle state: only `enter-application-review`
  moves `draft` to `in_review`, and successful approval removes the state.
- Every Experience CLI command serializes on a project-scoped cross-platform
  lock and recovers a durable prepared journal before reading. A mutation
  fsyncs its exact Experience-root/navigation-map snapshot before writing and
  keeps the lock through closing validation. Crash recovery restores only that
  transaction's exact root, so rollback cannot erase another committed writer.
- Application `reuse` is an explicit read-only path with no package mutation:
  it creates no open revision, requires no reviewer attestation and returns
  the verified current application plus its exact current zero-or-more process
  receipts; zero is valid only for a compiler-verified empty application.
- If the final active process retires, keep the canonical application as one
  explicit `application`-owned empty route. Approval advances the application
  receipt and returns no process receipt; do not delete or reset its history.
- Create, update, rename and retire are one approved transaction across the
  complete action set, maps, root application, generated projections and
  compiler-owned ledgers/receipts. Rename/retire scope includes all live
  reverse-ref packages; their revisions open first and repair affected exact
  refs before review. A partial result is never a handoff.
- The root `_ledger/application-revisions.json` is durable application receipt
  history. `_generated/application-registry.json` is its disposable current
  projection, distinct from the removed per-package artifact registry.
- A newer application receipt makes older Requirement and backlog Experience
  bindings non-current. Each consumer rebinds the new application receipt
  through its normal revision before further handoff.
- Mechanical checks cannot be waived. Fresh read-only reviewers challenge
  semantic coverage and rendered fidelity; the UX Designer fixes canonical
  records/application content or records a real assumption/open question before
  rerunning affected lenses. Map coverage and declared route binding are
  mechanical. Visual fidelity, coherence and usability remain reviewer
  judgments. Atomic approval consumes an exact-schema-v2 transient attestation
  bound to the proposal plus the current application source, process-package
  set, coverage and application hashes and requires zero blockers; it is
  evidence of review freshness, not durable review content.
