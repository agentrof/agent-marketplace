---
name: execution-plan
description: Build and approve the offline topology, claims and role sequence for one exact Delivery.
exposure: entry
---

# Execution Planning

## When to Use

- Use after one exact Delivery has an approved scope.
- Use when implementation ordering, ownership, path claims and verification
  strategy must be approved before any Item branch is claimed.

Use `/execution-plan DLV-###` with an exact Delivery ID. The command is not a
candidate selector and cannot resume by free text.

Read `flows/execution-planning.md` completely before compiling topology.

The Software Architect compiles the canonical topology into each Item's
`item.md`: `execution_after`, dependency and cross-Delivery bindings,
`path_claims`, `contract_claims` and `role_sequence`. The
`execution-plan.md` file is a compiler-rendered aggregate, not a second
authored truth.

Before approval, reject cycles, duplicate stories, unordered path/contract
overlaps, unknown role IDs and stale story/test/DoD hashes. `delivery_compile.py
approve-execution` creates the draft review/verification records and stamps
the plan hash offline. It does not create branches, worktrees, slots or
remote refs. Publication is a later explicit `delivery_git.py
publish-execution-plan` operation.
