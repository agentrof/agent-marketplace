---
name: setup
description: Inspect, check and apply one convergent project bootstrap or package refresh without replacing authored project truth.
exposure: entry
---

# Setup

Setup never writes executable test or environment placeholders into a consuming
project. The project copies the CI template, replacing `{{test_command}}` and
`{{tests_root}}` and dropping or replacing `{{dependency_audit_job}}` and `{{environment_smoke_job}}`,
as [ci-bootstrap](references/ci-bootstrap.md) describes, while setup alone
replaces `{{project_local_ignores}}` in its managed gitignore block. No
placeholder is allowed to reach a consuming repository.

## When to Use

- First run after installing the team in a repository.
- A clone or host change needs local instructions regenerated.
- A package refresh needs managed-file drift previewed and reconciled.

## Procedure

1. Resolve the Git root. The only workspace is `workspace/`; never ask for or
   create an alternate docs location. One team owns one project. Run:

   ```text
   <absolute-python> <absolute-package-scripts>/setup_project.py inspect --project-root <root> --json
   ```

   Present its pre-mutation operation list.
2. Resolve any reported blocker and the Windows-only `git.core_longpaths` choice that `references/windows-long-paths.md` defines, then run:

   ```text
   <absolute-python> <absolute-package-scripts>/setup_project.py apply --project-root <root> --json
   ```

   Apply creates the project-local
   `.agentrof/agent-marketplace/.runtime/` scratch directory, creates the
   folders `workspace_layout` in `workspace/config.json` allows, seeds
   `workspace/experience-design/DESIGN.md` and its `README.md` when absent,
   reconciles the managed workspace surfaces and runs its closing gate. A disposable OS guard
   serializes mutating setup apply processes. Apply rebuilds its authoritative
   plan under that guard, and each target is rechecked immediately before its
   atomic replacement. If the gate fails, setup restores only exact unchanged
   postimages; an observed concurrent edit is preserved and reported as a
   rollback conflict. Pause non-setup editors on all setup-managed targets for
   the short apply window because portable filesystems provide no conditional
   replace against a non-cooperating writer. Repeating apply with the same
   package and project must produce no operation.
   Disposable tool databases, such as scanner caches and mutation results, are
   permitted in that ignored scratch. Their file format does not make them
   workflow truth. Canonical project and backlog state remains in tracked
   workspace files; setup rejects their reserved runtime storage names.
   Use the active host runtime's exact absolute Python executable and the
   installed package script's absolute path. A bare Python command name,
   `/usr/bin/env` indirection or direct shebang invocation cannot receive
   machine-writer authorization because a shell alias or function can replace
   it before execution.
3. Run `<absolute-python> <absolute-package-scripts>/setup_project.py check
   --project-root <root> --json`. Check
   uses the same convergence planner as inspect and apply. A stale package
   projection fails even when the file exists.
4. Run the generated host project `inspect`, resolve every declared
   preserve/discard choice for user-owned instruction companions, then run its
   `apply` and `check`. Never overwrite companions silently. This host adapter
   projection is no project truth. Present the `roles` table: role, class,
   model, effort and source, any `kept` file and the `notice` it reports.
5. Refresh rebuilds the closed `workspace/config.json` shape, preserves valid
   language values and the `class_models` and `role_classes` overrides, and
   never edits authored notes. Retired command fields are dropped. Issue
   reporting is external and stateless, so setup never creates an issue type,
   tree or runtime record.
6. Ensure the managed root `.gitignore` block ignores only project-local
   runtime and host projections.
7. Ensure the managed root `.gitattributes` block holds
   `workspace/docs/** -text`. Governed files then check out byte-identical to
   the repository on every OS, which the byte-exact checks need under Git for
   Windows' default
   `core.autocrlf=true`. Project lines outside the block stay as written;
   check fails when a later line, a nested `.gitattributes` or
   `.git/info/attributes` turns conversion back on for a governed file. A
   checkout that Git converted before the rule keeps its CRLF copies: commit
   the block, commit or stash local `workspace/docs/` changes, then run
   `git rm -r --cached --quiet -- workspace/docs` and
   `git checkout HEAD -- workspace/docs` once. Never use
   `git add --renormalize` here; it would stage those CRLF copies.
8. Return tracked files that package writers before v0.4.0 left owner-only
   to the mode a checkout gives them. A tracked non-executable file at exactly
   0600 in `workspace/config.json`, `workspace/docs/`, `.gitignore` or
   `.gitattributes` gains the group and other read bits the umask allows,
   0644 under a 022 or 002 umask, and no write bit; `inspect` lists it as a
   `file_mode` operation and `check` reports it as drift.
9. Setup never emits a template with unresolved command placeholders.
10. Run `setup_project.py companions check`; optional third-party skills, and
    `companions apply` installs them only on the owner's go (README Companions).
11. Review and commit the exact tracked refresh diff before a workflow handoff.
    Report `deliver` as the next entry, run in a fresh session per task
    started with the workhorse-model command the host contract names.
    Start a fresh host session after setup.
