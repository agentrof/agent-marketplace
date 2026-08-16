---
name: setup
description: Idempotent project bootstrap for the local workspace, disposable runtime, Obsidian payload and standalone team instructions.
exposure: entry
---

# Setup

## When to Use

- First run after installing the team in a repository.
- A clone or host change needs local instructions regenerated.
- A package refresh needs managed-file drift previewed and reconciled.

## Procedure

1. Resolve the Git root. The only workspace is `workspace/`; never ask for or
   create an alternate vault location. One team owns one project. Run the packaged
   `scripts/setup_project.py --project-root <root> --origin <greenfield|existing>`
   bootstrap. It creates the project-local
   `.agentrof/agent-marketplace/.runtime/` scratch directory and
   only missing tracked directories, project config, payload, managed ignore
   block and portable gate; it preserves authored files.
2. Run the generated host project check. Present any preserve/discard choice
   for user-owned instruction companions; never overwrite them silently.
3. Read the `obsidian-vault` skill completely. The bootstrap materializes its
   vault payload under `workspace/docs/` and runs
   `vault_check.py reconcile-designations --defaults`. The canonical map includes the
   fixed backlog keys `backlog`, `backlog-review`, `epic`, `epic-review`,
   `story` and `test-plan`, plus the team's `issue-report` designation, in
   addition to the analysis/design types. Type keys and graph colors stay
   stable; display designation wording may follow the project's output and
   terminology languages. Route wording changes through `configure` and its
   decision gate.
4. Ensure the root `.gitignore` ignores only the project-local runtime and
   host projections.
5. Run `setup_check.py check`, `vault_check.py check` and the relevant
   compilers. CI materialization is a delivery concern and remains deferred
   until its command derivation contract is activated; setup never emits a
   template with unresolved command placeholders. The dormant CI template's
   `{{test_command}}`, `{{audit_command}}` and `{{env_command}}` substitutions
   are defined in [ci-bootstrap.md](references/ci-bootstrap.md) for that later
   activation. The ignore template's `{{project_local_ignores}}` substitution
   is the project-local root set from the packaged product contract. Commit
   generated project instructions only with user approval. On a package
   refresh, preview managed-file drift and apply only the user's selected
   managed changes.
6. Report `business-analysis -> solution-design -> design-system ->
   experience-design -> backlog-plan` for greenfield, or the scoped delivery
   route for an existing project. Start a fresh host session after setup.
