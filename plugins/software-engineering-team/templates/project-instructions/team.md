# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the governed project knowledge vault. Open this
  directory as the vault root. Its maps, generated views, document-type
  graph policy, citations, and navigation follow the
  `obsidian-vault` skill and the owning validation commands.
- `{{workspace}}/docs/experience-design/artifacts/application.html`: the one
  canonical, CSP-bound, network-free Experience acceptance application. Its
  version-2 declarative runtime executes deterministic state outcomes,
  preserved context and intentional returns; metadata binds the exact approved
  contract-v3 Design System.
- `{{workspace}}/docs/experience-design/experiences/`: living approved
  Experience packages, one active package per primary BA process. Each package
  maps exact record revisions to application route/state entries through its
  sole version-2 artifact, `artifacts/application-map.json`. `application` is
  a reserved slug;
  package-local previews and manifests are invalid. Sketches are exploration,
  not Experience state.
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
