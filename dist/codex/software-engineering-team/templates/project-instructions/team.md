# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the governed project knowledge vault. Open this
  directory as the vault root. Its maps, generated views, document-type
  designations, graph policy, citations, and navigation follow the
  `obsidian-vault` skill and the owning validation commands.
- `{{workspace}}/docs/experience-design/`: approved program and release
  experience graphs. Sketches are exploration, not baselines.
- `{{workspace}}/apps/`: application code, one directory per application.
- `{{workspace}}/environment/`: containerized environment definitions, build
  recipes, seed scenarios, and its command contract.
- `{{workspace}}/demos/` and `{{workspace}}/sketches/`: outward demos and
  design exploration previews.
- `<git-root>/.agentrof/agent-marketplace/.runtime/`: project-local runtime
  scratch and generated caches. It is never a shared or global database.
- `{{workspace}}/docs/backlog/`: tracked Obsidian backlog source. The nested
  epic, story, review and test-plan files are the durable preparation state.

Requirement Flow evaluates the request impact matrix, runs only the required
preparation stages, validates reused or not-applicable stages, and ends at
backlog-plan unless the request resolves with no change. Delivery then starts
only through its explicit Delivery Flow entry. Release Management remains out
of scope for this package.
