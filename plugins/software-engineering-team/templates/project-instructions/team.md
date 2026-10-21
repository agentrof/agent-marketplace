# Software Engineering Team

## Team workspace

- `{{workspace}}/config.json`: project declaration, changed only through the
  configure entry.
- `{{workspace}}/docs/`: the project documents. A role reads the sources its
  task file lists and greps this tree for anything missing.
- `{{workspace}}/apps/`: one folder per application with its own Dockerfile,
  lock file, a README stating its contract, and its tests and migrations.
- `{{workspace}}/tests/`, or the folder `tests_root` names: shared environment
  and harness suites.
- `{{workspace}}/experience-design/`: `DESIGN.md` and one
  `<feature-slug>/design.md` with `exports/` per designed feature.
- `{{workspace}}/project/<name>/`: project-specific data. A hook refuses any
  top-level folder under `{{workspace}}/` or `{{workspace}}/docs/` that
  `workspace_layout` in the config does not allow.
- `{{workspace}}/memory/`, all loaded below: `current-plan.md` is read first
  and updated at every step (done, next step, open decisions; history is
  archived out of it); `product-decisions.md` holds the owner's binding
  decisions, read before product work, and overrides the docs; `me.md` and
  `profile.md` hold the owner's own working rules.
- `{{workspace}}/docs/backlog/tasks/`: tracked fast-lane task records.

Work runs through the `deliver` entry: task, branch, pull request, review and
merge. Release Management is out of scope for this package.
