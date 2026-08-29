# Authoring guide

This repository ships one host-neutral Software Engineering Team. Canonical
content is under `plugins/software-engineering-team/`; generated host wrappers
are under `dist/` and are never edited by hand.

## Repository boundaries

- Keep canonical skills, agents, flows, scripts and templates under
  `plugins/software-engineering-team/`.
- Keep host-specific loading, permissions and behavior under the appropriate
  `platforms/<host>/` adapter.
- Keep consuming-project content in tracked `workspace/docs/`. `.agentrof/`,
  `.claude/`, `.codex/` and `.opencode/` are ignored runtime or projection
  surfaces.
- Keep the Software Engineering Team standalone and scoped to the current
  project checkout.

## Requirement Flow contract

```text
requirement -> business-analysis -> solution-design -> design-system -> experience-design -> backlog-plan -> delivery-plan -> execution-plan -> deliver
```

An exact `REQ-###` selects the Requirement-driven chain. Its impact matrix
decides `required`, `reuse` or `not_applicable`; each applicable stage binds a
current approved committed receipt. Without `REQ-###`, the user explicitly
selects approved/current upstream packages and no Requirement state is created.

Experience Design produces the globally current `application@rN` receipt and
the exact current zero-or-more process receipt set. A later approved prototype
or package-set delta makes that application receipt non-current, so consuming
Requirement and backlog revisions must rebind before a new handoff.

The complete lifecycle is defined in
[requirement-delivery-protocol.md](requirement-delivery-protocol.md).

## Artifact policy

Unless a subtree explicitly declares a closed artifact contract, files below
an `artifacts/` directory under a policy-valid vault folder are opaque local
artifacts, not Markdown notes. Names and extensions are unconstrained,
symlinks are rejected, and authored Markdown may link or embed a real local
artifact.

Design System is a closed exception: contract-v3 pairs `MASTER.md` with its
offline `design-system/artifacts/standalone.html` catalog.

Experience Design is intentionally not a closed exception. The complete
`workspace/docs/experience-design/artifacts/` tree is a UX author's prototype
workspace. It may include any directories, file names, page topology, HTML,
CSS, JS, framework build output, dependencies and assets. `index.html`,
multiple linked pages and `css/`, `js/` or media folders are useful optional
conventions. They are never compiler requirements.

The Experience compiler does not parse, lint, execute, sandbox, normalize or
rewrite prototype contents. Its only artifact checks are safety and lifecycle
boundaries: snapshot files are regular non-symlink files within the artifact
tree, and an approved snapshot still matches its recorded bytes and paths. It
writes lifecycle data only below `experience-design/_generated/` and
`experience-design/_ledger/`.

Prototype implementation is an exploratory acceptance artifact. Delivery later
implements the product according to its own architecture, engineering and
quality standards. A reviewer can recommend practices for the prototype but
those recommendations cannot become hidden compiler rules.

The compiler reserves `application` from Experience process slugs and aliases.
Its approval transaction covers create, update, rename and retire actions,
prototype receipt state and compiler-owned open-revision state. An
application-only revision changes no process revision but creates a new current
`application@rN`. The durable history is
`experience-design/_ledger/application-revisions.json`; the current projection
is `experience-design/_generated/application-registry.json`.

Reviewers, not the snapshot compiler, judge prototype fidelity, usability,
accessibility, visual quality, behavior and design coherence. Approval requires
a fresh transient schema-v4 attestation bound to proposal, artifact-tree,
package-set and application hashes. Its advisory notes are never an approval
condition or compiler rule.

## Backlog contract

The canonical tree is:

```text
workspace/docs/backlog/
├── backlog.md
├── reviews/
│   └── round-<n>-backlog-review.md
├── epics/<epic-slug>/
│   ├── epic.md
│   ├── reviews/round-<n>-epic-review.md
│   └── stories/<story-slug>/
│       ├── story.md
│       └── test-plan.md
└── _generated/
    ├── registry.json
    ├── board.md
    ├── dependency-map.md
    └── test-coverage.md
```

`backlog_compile.py` creates deterministic stubs, validates front matter and
paths, resolves upstream and dependency links, requires each story's test plan
and renders disposable views. `experience_refs` use exact values such as
`checkout:SCR-001@r2`; they resolve through an approved process registry or
ledger and are bound to the pinned application and process receipt set.

Every story contains User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. Criterion and
rule links are vault-absolute links to stable headings. Delivery execution
consumes an approved backlog without rewriting source.

## Host and runtime contract

Claude Code and Codex install the same standalone team through their native
marketplaces. OpenCode uses the generated project projector. Setup creates
only project-local runtime and host projection, preserves authored Markdown and
user-owned configuration, and rolls back setup-owned writes when closing checks
fail.

Generate distributions and verify before committing:

```text
python3 tools/build_distributions.py
make check
```

Use `make counts` only to refresh derived README counts. Never edit `dist/`
directly.
