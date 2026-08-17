# Architecture

This repository ships one standalone Software Engineering Team. Its canonical
behavior is host-neutral; Claude and Codex are packaging adapters.

## Invariants

1. Every enforceable rule is validated by `tools/validate.py` and `make check`.
2. Roles contain behavior and boundaries; domain knowledge lives in skills.
3. Entry skills are the only user surface. Internal skills are not user-facing.
4. Durable project truth is tracked files, never conversation memory.
5. Business Analysis, Solution Design, Design System and Experience Design are
   self-contained document workflows. Their approved Git-tracked artifacts are
   their complete state before backlog creation.
6. One standalone Software Engineering Team owns one project checkout.
7. The project-local runtime is
   `<git-root>/.agentrof/agent-marketplace/.runtime/` and contains only ignored,
   disposable scratch and cache files. Deleting it cannot change project truth.
8. The vault root is `workspace/docs/`. Its policy, designations, graph colors,
   maps and typed front matter are project-local and versioned with the project.
   No second workspace path is valid.
9. The backlog source is `workspace/docs/backlog/`. Its nested epic, story,
   review and test-plan Markdown files are canonical.
10. `backlog_compile.py` is a deterministic compiler. It produces disposable
    `_generated/registry.json`, `board.md`, `dependency-map.md` and
    `test-coverage.md` views and never imports a second source of truth.
11. An epic review derives from its epic and verifies the exact child story
    and test-plan set, including intra-epic dependencies. A root review derives
    from the backlog, relates to the exact epic set and covers cross-epic
    overlap, cycles, ordering and coverage.
12. Every story has a sibling `test-plan.md`. Criteria and rules map to stable
    scenarios; automation-required scenarios name an executable-test target.
13. Every story has exactly one accountable implementation owner and may name
    supporting implementation roles with concrete body responsibilities.
    Runtime identities are not backlog properties.
14. Backlog approval checks structural coverage, exact relation sets and
    review approval. Test execution, JUnit evidence and release readiness are
    delivery concerns.
15. File names are stable slugs; membership is path-derived. A story does not
    duplicate its epic relationship in front matter.
16. Designation display values are project config data with one reconcile
    writer and may follow project language settings. Canonical backlog type
    keys are `backlog`, `backlog-review`, `epic`, `epic-review`, `story` and
    `test-plan`; the team also reserves `issue-report` for tracked defects and
    improvements. Type keys, graph queries and graph colors are shipped policy.
17. Timestamps written by compilers come from UTC system time. User-authored
    approval timestamps are not accepted as evidence.
18. Distribution output under `dist/` is generated only by
    `tools/build_distributions.py`.
19. Requirement Flow ends at a committed, approved backlog. Delivery Flow owns
    scope reservation, execution coordination, review, PR handoff and merge;
    Release Management remains a later scope.
20. Delivery configuration fields remain optional before activation. Active
    preparation limits and every configured optional field are validated; no
    field is removed merely because its delivery consumer is deferred.

## Ownership

- `plugins/software-engineering-team/`: canonical workflows, agents, skills,
  compilers and templates.
- `platforms/`: Claude/Codex manifests and host overlays.
- `workspace/docs/`: consuming project's Obsidian vault and backlog.
- `tools/`: build, validation, release and scaffolding contracts.
