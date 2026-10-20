# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the project documents. A role reads the sources its
  task file lists and greps this tree for anything missing.
- `{{workspace}}/apps/`: application code, one directory per application.
- `{{workspace}}/tests/`: the project's tests, or the folder `tests_root` in
  the config names.
- `{{workspace}}/experience-design/`: `DESIGN.md` and one
  `<feature-slug>/design.md` with `exports/` per designed feature.
- `{{workspace}}/project/<name>/`: project-specific data. A hook refuses any
  top-level folder under `{{workspace}}/` or `{{workspace}}/docs/` that
  `workspace_layout` in the config does not allow.
- `{{workspace}}/memory/`: `current-plan.md` (done, next step, open
  decisions; kept current at every step), `product-decisions.md`, `me.md`
  and `profile.md`, all loaded below.
- `{{workspace}}/docs/operation/`: verification and environment knowledge and
  the Process Policy.
- `<git-root>/.agentrof/agent-marketplace/.runtime/`: project-local runtime
  scratch and generated caches. It is never a shared or global database.
- `{{workspace}}/docs/backlog/tasks/`: tracked fast-lane task records.

Work runs through the `deliver` entry on the fast lane: task, branch, pull
request, review and merge. Release Management remains out of scope for this
package.
