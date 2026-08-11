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
- `.agentrof/agent-marketplace/.runtime/plan/`: gitignored plan drafts.
- `.agentrof/agent-marketplace/.runtime/work-orders/`: gitignored work-order
  snapshots owned by the current worktree. Durable delivery state stays in
  PMO.

Greenfield preparation runs setup, business-analysis, solution-design,
design-system, experience-design, and backlog-plan, then stops before explicit
delivery. Existing projects start scoped feature work through deliver. Use
delivery-lanes only after preparation is approved.
