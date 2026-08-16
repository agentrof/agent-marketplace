---
name: setup
description: Inspect, check and apply one convergent project bootstrap or package refresh without replacing authored project truth.
exposure: entry
---

# Setup

Deferred template activation substitutes `{{test_command}}`,
`{{audit_command}}`, `{{env_command}}` and `{{project_local_ignores}}`; these
tokens never land literally in a consuming project.

## When to Use

- First run after installing the team in a repository.
- A clone or host change needs local instructions regenerated.
- A package refresh needs managed-file drift previewed and reconciled.

## Procedure

1. Resolve the Git root. The only workspace is `workspace/`; never ask for or
   create an alternate vault location. One team owns one project. Run:

   ```text
   scripts/setup_project.py inspect --project-root <root> --json
   ```

   Present its pre-mutation operation list. On first setup only, pass
   `--origin <greenfield|existing>` when the default `greenfield`
   classification is not correct. Omit `--origin` on every refresh because a
   classified origin is preserved. Origin changes go through `configure`, not
   package refresh.
2. Resolve any reported blocker, then run:

   ```text
   scripts/setup_project.py apply --project-root <root> --json
   ```

   Apply creates the project-local
   `.agentrof/agent-marketplace/.runtime/` scratch directory, reconciles the
   managed workspace surfaces and runs its closing gate. A disposable OS guard
   serializes mutating setup apply processes. Apply rebuilds its authoritative
   plan under that guard, and each target is rechecked immediately before its
   atomic replacement. If the gate fails, setup restores only exact unchanged
   postimages; an observed concurrent edit is preserved and reported as a
   rollback conflict. Pause non-setup editors on all setup-managed targets for
   the short apply window because portable filesystems provide no conditional
   replace against a non-cooperating writer. Repeating apply with the same
   package and project must produce no operation.
3. Run `scripts/setup_project.py check --project-root <root> --json`. Check
   uses the same convergence planner as inspect and apply. A stale portable
   gate, payload key or package projection fails even when the file exists.
4. Run the generated host project `inspect`, resolve every declared
   preserve/discard choice for user-owned instruction companions, then run its
   `apply` and `check`. Never overwrite companions silently. This is a host
   adapter projection; project truth remains the canonical workspace.
5. Read the `obsidian-vault` skill completely. Refresh materializes its vault
   payload under `workspace/docs/`, adds only missing designation defaults to
   `workspace/config.json`, and converges the compiler-owned relation reports.
   It never retitles authored notes implicitly. The map includes the fixed
   backlog keys `backlog`, `backlog-review`, `epic`, `epic-review`, `story`
   and `test-plan`, plus the team's `issue-report` designation and all
   analysis/design types. Type keys and graph colors stay stable. Existing
   designation wording and history remain project configuration.
6. Policy-owned keys in `app.json`, `core-plugins.json`, `graph.json` and
   `types.json` converge while unrelated user knobs remain untouched. The
   vetted community-plugin enable list and the policy-owned plugin directories
   are ignored, package-projected local files. Refresh replaces changed files
   and removes assets retired by the package while preserving unrelated plugin
   directories. Never commit this local projection to the consuming project.
7. Ensure the managed root `.gitignore` block ignores only project-local
   runtime, host projections, local Obsidian UI state and the community-plugin
   projection. The contract JSON files and CSS snippet remain tracked,
   reviewable project changes.
8. Run the portable vault gate and relevant compilers. CI materialization is a
   delivery concern and remains deferred until its command derivation contract
   is activated. Setup never emits a template with unresolved command
   placeholders. The dormant CI template substitutions are defined in
   [ci-bootstrap.md](references/ci-bootstrap.md).
9. Review and commit the exact tracked refresh diff before a workflow handoff.
   Report `business-analysis -> solution-design -> design-system ->
   experience-design -> backlog-plan` for greenfield. An existing project goes
   to `backlog-plan` until its scoped backlog is approved and committed; it does
   not bypass planning into delivery. `preparation_check.py` checks only config
   and completed-stage paths, so unrelated active authoring does not block.
   Start a fresh host session after setup.
