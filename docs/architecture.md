# Architecture

This repository ships one standalone Software Engineering Team. Its canonical
behavior is host-neutral; Claude Code and Codex are packaging adapters.

## Invariants

1. Every enforceable rule is validated by `tools/validate.py` and `make check`.
2. Roles contain behavior and boundaries; domain knowledge lives in skills.
3. Entry skills are the only user surface. Internal skills are not user-facing.
4. Durable project truth is tracked files, never conversation memory.
5. Business Analysis, Solution Design, Design System and Experience Design are
   self-contained document workflows. Their approved Git-tracked packages are
   their complete state before backlog creation. Experience Design additionally
   owns one approved, author-owned prototype snapshot and its exact package set.
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
    overlap, cycles, ordering and coverage. The read-only
    `backlog_review_inputs.py` manifest narrows epic reading to that scope plus
    dependency and source closure; root review retains the complete package.
    Process switch `review_manifest_scope` sets how far an epic reader's source
    closure reaches: at the default, `transitive`, every included note expands
    its own links; at `bounded`, only the epics, stories and test plans of the
    scope and dependency closure do, each note they link to or cite adds its
    front-matter relations one hop further, and the manifest's hash binds only
    the story identities and dependency edges that reach that closure.
    At `review_panels` `lens_panel` it names that value and its `check` block
    carries the compiler facts for the current review note, which the panel's
    lens readers take as given; at the default a reader's manifest has no
    compiler facts. Its hash must be rechecked before
    persisting a review. An epic manifest's hash binds the notes it reads and
    the story identities and dependency edges that reach them, except that at
    `bounded` an edge that reaches a story read only through a link leaves the
    manifest fresh; the root manifest's binds every backlog note.
    Process switch `review_scope_record` at `both_scopes` adds to an epic
    reader's manifest the sizes of both read sets, which its hash binds, and
    never changes what the reader reads. At process switch
    `remediation_writers` `per_epic`, an epic writer's manifest reads that
    epic's review scope at the `review_manifest_scope` value in force. At
    process switch `root_review_scope` `revision_delta`, the root reader of a
    revision reads in full only the changed stories and their neighbours, with
    every other story as a hash-bound summary; its manifest's hash still binds
    every backlog note.
12. Every story has a sibling `test-plan.md`. Criteria and rules map to stable
    scenarios; automation-required scenarios name an executable-test target.
    A scenario may state `rows`, a positive integer, and `row_split`,
    `serial`, `sharded` or `grouped`; process switch `test_cost_budget` at
    `flag_serial_rows` flags one that runs more rows serially than the
    owner's limit, and never fails a check. A scenario may also state the
    `level` its target runs at, `unit`, `fixture` or `live`, which `check`
    validates at every value, and a `level_reason`; process switch
    `test_levels` at `declared` lists each automation-required scenario that
    states no level and each `fixture` or `live` one that states no reason,
    and the list never fails a check.
13. Every story has exactly one accountable implementation owner and may name
    supporting implementation roles with concrete body responsibilities.
    Runtime identities are not backlog properties. Process switch
    `story_size_budget` at `propose_split` compares measures the compiler
    derives from a story and its Test Plan with owner-set limits from the
    Process Policy; it adds no story field, never fails a check and never
    rewrites a criterion. A story may classify its Operation impact as
    `operation_impact: required|not_applicable` with an `operation_reason`;
    like a Requirement impact matrix row it classifies impact and carries no
    estimate, and a story without it is unknown for it.
14. Backlog approval checks structural coverage, exact relation sets and
    review approval. A review requests changes only for an open critical or
    major finding; an accepted minor finding is recorded in the review note's
    optional, compiler-validated `Accepted Minor Findings` table with an owner
    role and revisit trigger. Test execution, JUnit evidence and release
    readiness are delivery concerns. Process switch `review_loop` sets how far
    this rule reaches: at the default, `current`, every other review step keeps
    its own loop; at `blocking_delta`, Operation contracts record accepted minor
    findings in their own compiler-validated `Accepted Minor Findings` section,
    Delivery code review minors carry an owner role and revisit trigger into the
    Item's code review record and the Delivery Review, a re-review reads only
    the open blocking findings, the changed text and its dependency context,
    and a critical or major claim gates only once one fresh, read-only
    calibration reader, never its writer or claimant, confirms it with a
    citation of the text. Each review keeps its rulings where its compiler
    reads them, and a review note or an Operation contract also records the
    findings its review returned, so no finding that stays critical or major
    enters `Accepted Minor Findings`.
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
    the pinned model catalog and the per-tier model and effort profiles,
    and runtime behavior remain in that platform directory;
    central tooling
    only orchestrates registry discovery, canonical copying, provenance, and
    owned generated-tree replacement.
20. Requirement Flow ends at a committed, approved backlog. Delivery Flow owns
    scope reservation, execution coordination, review, PR handoff and merge;
    Release Management remains a later scope.
21. `workspace/config.json` is a closed bootstrap contract. Technology and
    datastore choices belong to accepted Solution decisions, commands belong
    to Operation Contracts, and the hard Delivery concurrency guard belongs to
    approved Delivery Governance under `workspace/docs/delivery/governance/`.
    Beside the languages it holds only the overrides `tier_models` and
    `role_tiers`: a tier's model and effort per host, within what the
    package's tier map lets that tier take, and a role's tier on every host.
    Setup writes neither, and the package catalog and profiles stay the
    defaults.
22. Outside an explicitly closed artifact contract, `artifacts/` beneath a
    policy-valid vault folder holds opaque, local files. Generic artifact
   content is neither a vault note nor workflow executable behavior; symlinks
   are forbidden and Markdown may link to real local artifacts. Design System
   alone defines a closed artifact surface below.
23. A contract-v3 Design System publishes MASTER.md and its offline standalone
    catalog together at `design-system/artifacts/standalone.html`. The catalog
    has a fixed DOM flow while all visual values and project content bind to
    MASTER's machine-readable token block.
24. An open Business Analysis package revision is `package_status: draft` and
    may contain approved carryover documents plus multiple draft or in-review
    documents. Only `approve-package` creates a current package receipt; Git
    history is the audit baseline for the prior approved state.
25. Experience Design prototype files beneath
    `workspace/docs/experience-design/artifacts/` are wholly author-owned.
    Their folders, file names, formats, dependencies, behavior and presentation
    are not compiler inputs. The compiler records only a safe, byte-level,
    recursive artifact inventory and its hash in the approved snapshot. The
    root `_ledger/application-revisions.json` is durable receipt history;
    `_generated/application-registry.json` is its disposable current projection.
26. `application` is reserved from Experience process slugs and aliases. An
    approved package-set delta or application-only delta creates a new globally
    current `application@rN` receipt alongside the exact current process
    receipts. Create, update, rename and retire apply under one project-scoped
    lock as a crash-recoverable transaction across packages, prototype artifacts,
    compiler-owned open-revision state and receipt state. Downstream Requirement
    and backlog state must bind the new application receipt before a new
    handoff. An already-created Delivery continues to verify its exact pinned,
    approved backlog and Story/Test Plan hashes instead of being invalidated by
    an unrelated later application revision. Retiring the final process keeps
    the application receipt sequence alive with an empty artifact inventory and
    no process receipts; a later process can join through the next application
    revision. Reviewers provide fidelity and usability advice; approval uses a
    transient attestation bound to proposal, artifact-tree, package-set and
    application hashes without treating that advice as a compiler gate. A
    scope composed only of stale `draft|in_review` non-retire mutations is
    recoverable only by an atomic exact-set rebind from its hash-verified old
    plan to a fresh plan that binds the predecessor hash, current inputs and
    application receipt; the rebind preserves authored child-record and
    artifact bytes plus approved ledgers, and resets review to `draft`.
27. Every official compiler mutation that writes authored Markdown produces
    an immediately legal per-write Vault result. After deterministic generated
    views are rendered, the same tree passes both its scoped Vault gate and its
    owning compiler gate; producer and consumer contracts are tested together.
    A tool event may share an immutable Vault model across its changed paths;
    separate writes retain their immediate pre/post validation boundaries.
    Virtual patch checks read the complete candidate file view, including
    generated registries and artifacts, without modifying source files.
28. Maintainer issue work starts only from an explicit user instruction in an
    active maintainer session; GitHub issue events never start an agent. The
    protocol may prepare a pull request but never merge one without explicit
    approval. Stable release authority comes only from an explicit user request
    bound to an unambiguous PR set. Every pull request emits the required
    aggregate and two-host lifecycle contexts. Test partitions must account
    for every selected case; shared or unknown impact selects full coverage.
    Reused validation binds successful trusted workflow evidence to the exact
    tested tree and current coverage contract; missing evidence runs fresh
    tests, and both host lifecycles always run fresh. Release-owned files
    change only in a release commit, the last commit of a pull request, whose
    tree is the deterministic bump of its parent at its own commit date; the
    replay ignores ambient Git attributes, excludes, replacement refs and
    graph overlays. A release tags an approved `main` commit whose own push
    validation succeeded and never runs the tests again. Snapshot records are
    prefix-free;
    generated text uses LF, unknown/binary payloads remain byte-exact, and the
    tracked `package-modes.json` contract supplies platform-neutral executable
    modes. Schema-v4 package provenance binds the closed file inventory, hashes
    and executable set, one hash line per file, and carries no per-commit
    source identity; the release metadata records the source snapshot
    `build_id` and verification recomputes it. Public stable installs,
    exact-lease ref transactions, immutable Release reconciliation and
    clean-ref completion remain mandatory.
29. Backlog, Solution Design, Design System and Operation contract reviews
    run as process switch `review_panels` selects in the project's Process
    Policy: one reviewer per step at the default, `single_reader`, or at
    `lens_panel` a review panel of parallel, read-only lens readers. Lens sets
    are validated data in `challenge-review/data/review-panels.json`; a panel
    replaces a step's single reviewer and never stacks on top of it, and it
    keeps the step's review loop, which process switch `review_loop` sets. The
    read-only document reviewers keep their own tier, and every build ships
    their generated `-lens` variants on the `low` tier for panel readers.
    Review steps whose machine interface accepts one result per role keep one
    reader, except Delivery code review under process switch
    `code_review_panel` at `beside_official`: a panel of the code reviewer's
    `-lens` variant reads beside the official code reviewer, a fresh
    calibration reader on the code reviewer's own tier rules each panel claim,
    and a merge step registers the one result.
30. Every new process behaviour ships behind a process switch declared in
    `configure/data/process-switches.json` whose default is the behaviour it
    changes. With every switch at its default, every compiler and coordinator
    output is byte-identical to the previous release on the same inputs and
    every task binds the same instructions, apart from the defect fixes that
    `EXPECTED_DIFFERENCES` in `tools/tests/test_default_equivalence.py` lists,
    each with the issue whose fix made it, and the instruction files its
    `SHIPPED_ADDITIONS` lists, each with the issue that makes default tasks
    bind it; otherwise only script and contract hashes change, and golden
    all-default runs prove it. Two exceptions stand outside this rule: the
    model and effort lines of rendered agents follow each host's model
    catalog and execution profile, not default
    equivalence, so a catalog change reaches every role under any policy,
    and a role whose pinned model cannot run falls back to the session's
    model with a warning; and a user-armed session entry such as autopilot ships without a switch,
    because without an active grant its hooks exit silently, no task binds
    it and every output stays byte-identical. A non-default value's
    instructions live in switch references that `task_inputs.py` binds only
    when the project's Process Policy selects that value, together with the
    package data only that value reads. Project values live in
    `workspace/docs/delivery/process-policy.md`, never in
    `workspace/config.json`, and each Delivery pins the policy revision it ran
    under. Outside a Delivery, a backlog approval records its pin in the root
    backlog, and each review it approves keeps the pin of the policy it ran
    under, which must set the approval's values for every switch the
    backlog-planning flow owns.

The normative Requirement and Delivery lifecycle is documented in
[requirement-delivery-protocol.md](requirement-delivery-protocol.md).
Repository issue and release operations are documented separately in
[maintainer-operations-protocol.md](maintainer-operations-protocol.md).

## Ownership

- `plugins/software-engineering-team/`: canonical workflows, agents, skills,
  compilers and templates.
- `platforms/`: registered host adapters, native manifests, and overlays.
- `workspace/docs/`: consuming project's Obsidian vault and backlog.
- `tools/`: build, validation, release and scaffolding contracts.
