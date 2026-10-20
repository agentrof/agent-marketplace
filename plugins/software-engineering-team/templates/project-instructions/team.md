# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the project documents. A role reads the sources its
  task file lists and greps this tree for anything missing.
- `{{workspace}}/apps/`: application code, one directory per application.
- `{{workspace}}/docs/operation/`: verification and environment knowledge.
- `<git-root>/.agentrof/agent-marketplace/.runtime/`: project-local runtime
  scratch and generated caches. It is never a shared or global database.
- `{{workspace}}/docs/backlog/tasks/`: tracked fast-lane task records.

Work runs through the `deliver` entry on the fast lane: task, branch, pull
request, review and merge. Release Management remains out of scope for this
package.
