# Architecture

This repository ships one standalone Software Engineering Team. Its canonical
behavior is host-neutral; Claude Code, Codex, and OpenCode are packaging
adapters.

## Invariants

1. Every enforceable rule is validated by `tools/validate.py` and `make check`.
2. Roles contain behavior and boundaries; domain knowledge lives in skills.
3. Entry skills are the only user surface. Internal skills are not user-facing.
4. Durable project truth is tracked files, never conversation memory.
5. Business Analysis, Solution Design, Design System and Experience Design are
   self-contained document workflows. Their approved Git-tracked packages are
   their complete state before backlog creation. Experience Design additionally
   owns one canonical acceptance application and its exact package mappings.
6. One standalone Software Engineering Team owns one project checkout.
7. The project-local runtime is
   `<git-root>/.agentrof/agent-marketplace/.runtime/` and contains only ignored,
   disposable scratch and cache files. Deleting it cannot change project truth.
8. The vault root is `workspace/docs/`. Its policy, graph colors, maps and
   typed front matter are project-local and versioned with the project.
   No second workspace path is valid.
9. The backlog source is `workspace/docs/backlog/`. Its nested epic, story,
   review and test-plan Markdown files are canonical.
10. `backlog_compile.py` is a deterministic compiler. It produces disposable
    `_generated/registry.json`, `board.md`, `dependency-map.md` and
    `test-coverage.md` views and never imports a second source of truth.
11. An epic review derives from its epic and verifies the exact child story
    and test-plan set, including intra-epic dependencies. A root review derives
    from the backlog, relates to the exact epic set and covers cross-epic
    overlap, cycles, ordering and coverage.
12. Every story has a sibling `test-plan.md`. Criteria and rules map to stable
    scenarios; automation-required scenarios name an executable-test target.
13. Every story has exactly one accountable implementation owner and may name
    supporting implementation roles with concrete body responsibilities.
    Runtime identities are not backlog properties.
14. Backlog approval checks structural coverage, exact relation sets and
    review approval. Test execution, JUnit evidence and release readiness are
    delivery concerns.
15. File names are stable slugs; membership is path-derived. A story does not
    duplicate its epic relationship in front matter.
16. Authored titles are direct, natural phrases in the configured output
    language; IDs stay in aliases, while stable type keys, graph queries and
    graph colors remain shipped policy. Canonical backlog type keys are
    `backlog`, `backlog-review`, `epic`, `epic-review`, `story` and
    `test-plan`. Issue reporting is an external, stateless support workflow and
    is never a vault type or project evidence source.
17. Timestamps written by compilers come from UTC system time. User-authored
    approval timestamps are not accepted as evidence.
18. Distribution output under `dist/` is generated only by
    `tools/build_distributions.py`.
19. Every host is discovered through `platforms/<host>/adapter.json` and its
    adapter module. Host-specific path names, manifests, permissions, hooks,
    and runtime behavior remain in that platform directory; central tooling
    only orchestrates registry discovery, canonical copying, provenance, and
    owned generated-tree replacement.
20. OpenCode packages are project projections, not a fabricated native
    marketplace artifact. Their Agent Marketplace-owned private state is under
    `.opencode/agentrof/agent-marketplace/`; `opencode.json`, `opencode.jsonc`,
    and host-created bootstrap files remain user/host owned.
21. A project projection requires explicit stopped-client attestation for
    mutation. Its multi-file publication is rollback-capable but is not a
    single atomic swap while a host client is running.
22. OpenCode stable release evidence is exact-candidate, host-executable
    evidence, not merely a generated-tree check: the pinned terminal binary
    must pass the real fake-provider CLI/hook probe and interactive PTY/ConPTY
    probe on native macOS, Linux, and Windows. Real WSL2 evidence is a separate
    release blocker and is never inferred from a Linux runner.
23. Requirement Flow ends at a committed, approved backlog. Delivery Flow owns
    scope reservation, execution coordination, review, PR handoff and merge;
    Release Management remains a later scope.
24. `workspace/config.json` is a closed bootstrap contract. Technology and
    datastore choices belong to accepted Solution decisions, commands belong
    to Operation Contracts, and the hard Delivery concurrency guard belongs to
    approved Delivery Governance under `workspace/docs/delivery/governance/`.
25. Outside an explicitly closed artifact contract, `artifacts/` beneath a
    policy-valid vault folder holds opaque, local files. Generic artifact
    content is neither a vault note nor workflow executable behavior; symlinks
    are forbidden and Markdown may link to real local artifacts. Design System
    and Experience Design define closed artifact surfaces below.
26. A contract-v3 Design System publishes MASTER.md and its offline standalone
    catalog together at `design-system/artifacts/standalone.html`. The catalog
    has a fixed DOM flow while all visual values and project content bind to
    MASTER's machine-readable token block.
27. An open Business Analysis package revision is `package_status: draft` and
    may contain approved carryover documents plus multiple draft or in-review
    documents. Only `approve-package` creates a current package receipt; Git
    history is the audit baseline for the prior approved state.
28. Experience Design has exactly one visual implementation at
    `workspace/docs/experience-design/artifacts/application.html`. Each process
    package has exactly one version-2 `artifacts/application-map.json` that
    maps exact qualified record revisions to declared route/state entries in
    that application. The version-2 inline contract exposes the complete state
    taxonomy and deterministic outcome simulations. The application uses the
    fixed declarative, CSP-bound, network-free runtime, executes preserved
    context and intentional returns, and binds
    exact approved contract-v3 Design System metadata, including the complete
    root application token set and explicit dark-theme overrides; every active process
    package binds that same Design System receipt. Package-local previews,
    artifact manifests and the former per-package artifact registry are not
    compatibility inputs. The root `_ledger/application-revisions.json` is
    durable receipt history; `_generated/application-registry.json` is its
    disposable current projection.
29. `application` is reserved from Experience process slugs and aliases. An
    approved package-set delta or application-only delta creates a new globally
    current `application@rN` receipt alongside the exact current process
    receipts. Create, update, rename and retire apply under one project-scoped
    lock as a crash-recoverable transaction across packages, maps, application,
    compiler-owned open-revision state and receipt state. Downstream Requirement
    and backlog state must bind the new application receipt before a new
    handoff. An already-created Delivery continues to verify its exact pinned,
    approved backlog and Story/Test Plan hashes instead of being invalidated by
    an unrelated later application revision. Retiring the final process keeps
    the application receipt sequence alive with one reserved `application`
    empty route and no process receipts; a later process can join through the
    next application revision. Exact map coverage is mechanical; visual
    fidelity remains a fresh reviewer judgment recorded only as a transient
    proposal/revision/status/source/package-set/coverage/application-hash-bound
    zero-blocker attestation at approval.

The normative Requirement and Delivery lifecycle is documented in
[requirement-delivery-protocol.md](requirement-delivery-protocol.md).

## Ownership

- `plugins/software-engineering-team/`: canonical workflows, agents, skills,
  compilers and templates.
- `platforms/`: registered host adapters, native manifests, overlays, and
  OpenCode project-projection contracts.
- `workspace/docs/`: consuming project's Obsidian vault and backlog.
- `tools/`: build, validation, release and scaffolding contracts.
