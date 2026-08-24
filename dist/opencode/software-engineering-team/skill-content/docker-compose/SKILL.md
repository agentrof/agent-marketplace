---
name: docker-compose
description: Containerized environment expertise loaded by software-engineering-team agents for environment work. Use when defining or changing the project's containerized environment, authoring image build recipes, wiring healthchecks and service dependencies, designing deterministic seed scenarios, isolating parallel environment instances, or verifying a one-command full-stack bring-up with clean logs.
exposure: internal
---

# Docker Compose Environment

**Given:** an architecture delta declaring applications, services and stores, and an ownership map granting the environment prefix.
**Produces:** a compose-defined environment that rises from scratch with one command, healthchecked, seeded and clean, plus the entry point the whole team consumes.

Load the `obsidian-vault` skill before reading or writing the approved
Environment Contract under `workspace/docs/operation/`.

## When to Use

- Defining or extending the project's containerized environment
- Authoring or changing image build recipes
- Designing or updating seed scenarios and the seed runner
- Verifying the from-scratch cycle (load the qa checklist below)
- Reviewing environment-owned changes (load the review checklist below)

## Core Rules

1. **One command, fully up:** the `up` verb builds what changed, starts every service, waits on healthchecks and seeds the default scenario; it exits nonzero unless everything is healthy. Partial bring-up is failure.
2. **Health probes real capability:** a healthcheck asserts the service can do its job (query answered, dependency reachable), not that a process exists. Dependents declare `depends_on` with the healthy condition.
3. **Config only via environment variables:** images are identical across environments; no baked hosts, modes or secrets. Local secrets live in an env file that stays untracked; committed defaults are secretless.
4. **Exact image tags, non-root, multi-stage:** never a floating tag; final stages run as a non-root user on minimal bases; dependency manifests are copied before source so the build cache survives source edits.
5. **Teardown means everything:** the `down` verb removes containers, networks and data volumes. From-scratch verification starts with it.
6. **Isolation by naming:** namespacing comes from the entry point (below), never from caller discipline.

## The Environment Command

The approved Environment Contract at
`workspace/docs/operation/environment-contract.md` names one project-owned
entry point (a script or make target) with fixed verbs. All roles and flows
consume the environment ONLY through it:

| verb | contract |
|---|---|
| `up` | build as needed, start all, gate on health, seed default scenario; nonzero on any failure |
| `down` | full teardown including data volumes |
| `seed <scenario>` | (re)load a named scenario into the running environment |
| `logs` | aggregated service logs for the audit |
| `url <service>` | resolved base URL of a published service |

The entry point derives the compose project name itself: sanitized, lowercased basename of the working tree root. Parallel worktrees therefore get disjoint containers, networks, volumes and ports with zero caller effort.

The Environment Contract carries its command, workdir, scenarios, tolerated
warnings and service catalog. It is revisioned independently of Solution and
product-stage packages.

## Hermetic Suite Rule

The approved Verification Contract commands NEVER depend on a standing
environment; test fixtures own their ephemeral stores. If a project ever
violates this, bring the environment up once for the whole mutation run, never
per mutant. Environment cycles happen only at: authoring self-verification,
developer smoke checks, the QA live protocol, the CI smoke job, and merge
checkpoints.

## Seed Scenarios

- Reserved names: `baseline` (live verification), `demo` (stakeholder walkthrough of the real app), `empty`. Projects may add more.
- Deterministic: fixed identifiers and timestamps; same scenario, same data, every run; safe to re-run.
- Referentially intact: a scenario loads as a consistent graph, never orphaned rows.
- Ownership split: this skill's holder owns the runner and the scenario registry under the environment prefix; domain content (factories, loaders) belongs to the owning application and its developer. The runner invokes the application's loader with the scenario name.

## Fix-Atomic Environment Defects

A defect in environment-owned files cannot be reproduced by the hermetic suite. Substitute a recorded from-scratch reproduction (the failing `up` or readiness evidence, then the passing rerun) for the failing-test requirement; record it in the PR body.

## References

- [compose-patterns](references/compose-patterns.md): service anatomy, healthcheck design, dependency conditions, project-name derivation, port strategy, volumes. Read when writing or changing the compose definition or the entry point.
- [image-build](references/image-build.md): multi-stage recipes, non-root users, cache ordering, ignore files, tag policy. Read when authoring or reviewing a build recipe.
- [seed-scenarios](references/seed-scenarios.md): registry shape, determinism rules, the runner-loader boundary. Read when designing or changing seed data.

## Checklists

- [review-checklist](references/review-checklist.md): checkbox assertions for environment-owned diffs. Read when reviewing changes that touch the environment prefix.
- [qa-checklist](references/qa-checklist.md): from-scratch cycle, scenario determinism and service-log audit assertions. Read when planning QA for a story with environment impact.

## Related Skills

Pairs with the python-fastapi and react-typescript skills whose applications it packages, and with the sql-database-design and nosql-database-design skills whose stores it runs as services.
