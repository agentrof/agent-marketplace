# Authoring guide

This repository ships one host-neutral Software Engineering Team. Its durable
source is the plugin content under `plugins/software-engineering-team/`; host
wrappers are generated under `dist/` and are never edited by hand.

## Repository boundaries

- Keep canonical skills, agents, flows, scripts and templates under
  `plugins/software-engineering-team/`.
- Keep host-specific loading and choice-gate behavior under `platforms/claude/`
  and `platforms/codex/`.
- Keep project content in the consuming repository's tracked
  `workspace/docs/` vault. `.agentrof/`, `.claude/` and `.codex/` are local,
  ignored runtime/projection surfaces only.
- Keep the Software Engineering Team standalone and scope every operation to
  the current project checkout.

## Greenfield authoring contract

The user moves through these durable document gates:

```text
business-analysis -> solution-design -> design-system -> experience-design -> backlog-plan
```

Each stage writes its own Markdown artifacts and runs its own compiler/checker.
Before the backlog exists, those approved documents are the complete stage
state. A stage is complete when its approved documents are tracked in Git.
`preparation_check.py` only reads those documents and routes to the next stage.

## Backlog contract

The canonical tree is:

```text
workspace/docs/backlog/
├── backlog.md
├── reviews/
│   └── round-<n>-backlog-review.md
├── epics/<epic-slug>/
    ├── epic.md
    ├── reviews/round-<n>-epic-review.md
    └── stories/<story-slug>/
        ├── story.md
        └── test-plan.md
└── _generated/
    ├── registry.json
    ├── board.md
    ├── dependency-map.md
    └── test-coverage.md
```

The six fixed backlog type keys are `backlog`, `backlog-review`, `epic`,
`epic-review`, `story` and `test-plan`; `issue-report` is the seventh stable
team type for tracked defects and improvements. Their project-selected display
designations live in `workspace/config.json`. Colors, graph queries and
path/type rules are fixed in `vault-policy.json` and rendered into
`.obsidian/graph.json` and `.obsidian/types.json`.

`backlog_compile.py` is the only backlog helper. It creates deterministic
stubs, validates front matter and nested paths, checks one owner and any
supporting roles, resolves upstream and dependency links, checks unique IDs and
dependency cycles, requires every story to have a test plan, maps every
acceptance criterion to at least one Given/When/Then scenario, and renders
disposable JSON/board/coverage views under `backlog/_generated/`.

Every story contains User Value, Scope, Non-Goals, Implementation
Responsibilities, Acceptance, Dependencies and Delivery Notes. It names one
`owner_role` and may name unique `supporting_roles`; each listed role has a
concrete responsibility and the owner cannot repeat as supporting. The fields
hold team role identifiers only.

`criterion_refs`, `experience_refs`, `derives_from`, `depends_on`,
`uses_design` and `constrained_by` are vault-absolute wikilinks. Criterion and
rule links resolve to exact stable headings in approved upstream notes.
Dependencies target stories and have matching reasons in the story body.

An epic review derives from its epic and verifies the exact story/test-plan set
below it. The root review derives from the backlog and relates to the exact
epic set. Approval is blocked until the root backlog, both review layers and
every story test plan are approved. Test-plan scenarios may be marked
`automation: required` or `manual`; required scenarios name their planned
automation target. Delivery execution is a later scope.

Epic-review sections are Scope, Slicing, Criteria Coverage, Test Design,
Dependencies, Role Ownership, Findings and Verdict. Root-review sections are
Epic Coverage, Cross-Epic Overlap, Cross-Epic Dependencies, Release Ordering,
Shared Contracts, Deferred Criteria, Global Test Coverage, Findings and
Verdict.

## Host and runtime contract

Claude and Codex install the same standalone team. Their manifests contain no
plugin dependency. Setup creates only a project-local runtime and host
projection, materializes the tracked vault payload, reconciles the designation
map, and runs checks. Generated distributions come from:

```text
python3 tools/build_distributions.py
make check
```

Package upgrades rerun the idempotent setup contract, preview managed-file
drift, preserve authored Markdown, regenerate only selected managed surfaces
and run the local checks. Any future delivery upgrade policy must keep this
file-first, project-local boundary.

## Validation expectations

Run `make check` before committing. It validates repository contracts, release
metadata, generated distributions, focused compiler tests and runtime scripts.
Use `make counts` only to refresh derived README counts. Never edit `dist/`
directly.
