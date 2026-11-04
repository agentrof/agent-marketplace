# Software Engineering Team

- Start project work through `deliver`. One owner coordinates task records,
  isolated writers, conditional methods, proof, independent review and readiness.
- `{{workspace}}/config.json` holds the closed project contract. Change it through
  `configure` or its reviewed migration writer.
- `{{workspace}}/docs/backlog/tasks/` holds durable task outcomes and evidence.
  Read the active task and affected source files first. Use an existing graph
  to find dependencies, then verify findings against source.
- Read `{{workspace}}/memory/current-plan.md` when resuming or coordinating work.
  Read relevant `product-decisions.md` entries before changing product behavior.
  Update these files when the plan or a binding decision changes, and reconcile
  affected documentation with the delivered code.
- Owner rules in `{{workspace}}/memory/me.md`, `profile.md` and the user companion
  remain binding. Every worker brief carries applicable authority, data safety,
  environment constraints, write ownership and its stopping condition.
- Application files follow the project's accepted layout. `tests_root` locates
  shared tests. `workspace_layout` declares permitted workspace folders.
  Feature design records live under `{{workspace}}/experience-design/`.
- Finish only after the merged revision is ready in its intended environment
  and required live checks pass. Pending proof, review or readiness stays pending.
