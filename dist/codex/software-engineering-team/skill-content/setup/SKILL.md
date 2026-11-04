---
name: setup
description: Bootstrap or migrate project-owned integration when required inputs are missing or incompatible.
exposure: entry
---

# Setup

## Outcome

A usable project integration with preserved authored files and explicit host capabilities.

## When to use

Use for first setup, a missing host projection or an explicit migration. A package
version change alone does not require setup.

## Inputs and authority

Read the repository instructions and existing `workspace/config.json`. Use the
active host's exact absolute Python executable and installed package script path.
Authored files and unrelated host settings remain owned by the user.

## Method

1. Run `setup_project.py inspect --project-root <root> --json`. Resolve only the
   reported consequential choices. Existing overrides that cannot be translated
   unambiguously need a decision before migration.
2. Run `setup_project.py apply --project-root <root> --json`, then `check` with the
   same arguments. The planner owns locking, atomic writes, rollback and convergence.
3. Inspect, apply and check the host projection as the host contract describes.
   Read the actual model/capability table and retained-file warnings. An available
   model is not evidence of a successful invocation.
4. Use existing project test and environment commands. Complete the declared CI
   template only when the project needs it. Resolve `{{test_command}}`, `{{tests_root}}`,
   `{{dependency_audit_job}}` and `{{environment_smoke_job}}` from that contract.
   Setup owns `{{project_local_ignores}}`. No executable placeholder may remain.
5. Check the four supported companions once with
   `setup_project.py companions check --all --project-root <root> --json`.
   Ponytail, Graphify, Impeccable and security-audit are mandatory for relevant
   work when installed and enabled. Report missing or unverified availability.
   Use `--verify-load` for an explicit host-loader check. Install only under
   installation authority with `companions apply --companion <id>`.

User-wide companion invocation changes require explicit authorization and the
host contract's dedicated `companions configure` command. Ordinary setup and
package refresh preserve that user-wide policy. Verify the changed catalog in
a fresh host session.

## Evidence and completion

Return the exact diff, empty remaining setup plan, projection status and any host
reload requirement. Keep ordinary package updates separate from project migration.

## Conditional references

- Read [CI bootstrap](references/ci-bootstrap.md) when adding project CI.
- Read [Windows paths](references/windows-long-paths.md) only for its reported choice.
