# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the governed project knowledge vault. Open this
  directory as the vault root. Its maps, generated views, document-type
  graph policy, citations, and navigation follow the
  `obsidian-vault` skill and the owning validation commands.
- `{{workspace}}/docs/experience-design/artifacts/`: the author-owned
  Experience prototype workspace. Its structure, files, technologies, assets
  and behavior are free; the compiler snapshots bytes and paths only.
- `{{workspace}}/docs/experience-design/experiences/`: living approved
  Experience packages, one active package per primary BA process. `application`
  is a reserved slug. Prototype files are review evidence, not delivery code.
- `{{workspace}}/apps/`: application code, one directory per application.
- `{{workspace}}/docs/operation/`: approved verification and environment
  contracts used when Delivery turns stories into executable Items.
- `{{workspace}}/demos/` and `{{workspace}}/sketches/`: outward demos and
  design exploration previews.
- `<git-root>/.agentrof/agent-marketplace/.runtime/`: project-local runtime
  scratch and generated caches. It is never a shared or global database.
- `{{workspace}}/docs/backlog/`: tracked Obsidian backlog source. The nested
  epic, story, review and test-plan files are the durable Requirement state.

Requirement Flow evaluates the request impact matrix, runs only the required
Requirement stages, validates reused or not-applicable stages, and ends at
backlog-plan unless the request resolves with no change. Delivery then starts
only through its explicit Delivery Flow entry. Release Management remains out
of scope for this package.

Experience handoff binds the globally current `application@rN` and the exact
current process receipts together. Every approved package-set or
application-only delta advances that application receipt; older Requirement and
backlog bindings must revise and rebind before further handoff.
