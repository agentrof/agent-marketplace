# Backlog Planning Flow

The flow turns approved product knowledge into a versioned, project-local
backlog. Its canonical state is Markdown under `workspace/docs/backlog/`.

Spawn template: paste `{{constitution}}` into every role prompt. Load the
`obsidian-vault` skill before touching the docs tree; its policy is
authoritative.

## 0. Preconditions

- Business Analysis, Solution Design, Design System and Experience Design are
  approved and their own compilers are green.
- The user explicitly starts the backlog entry and reviews each authored
  package.
- Preparation state comes only from the tracked documents and their checks.

## 1. Materialize the backlog tree

Run the packaged backlog compiler:

```text
backlog_compile.py init --docs <workspace>/docs
```

The root contains `backlog.md` and `reviews/`. Each epic is a folder with an
`epic.md`, `reviews/`, and `stories/`. Each story folder contains exactly
`story.md` and `test-plan.md`. Membership is derived from the path.

## 2. Author stories

The Product Owner authors each `story.md` with exactly these sections:

```text
User Value
Scope
Non-Goals
Implementation Responsibilities
Acceptance
Dependencies
Delivery Notes
```

Every story has one `owner_role` and may have `supporting_roles`. The owner is
accountable for the integrated story result. Every listed supporting role must
have a concrete contribution in `Implementation Responsibilities`; the owner
cannot be repeated there as a supporting role. Store team role identifiers,
never people or runtime identities.

Use vault-absolute wikilinks for `criterion_refs`, `experience_refs`,
`derives_from`, `depends_on`, `uses_design` and `constrained_by`. Criterion
links target the approved owning note and carry its BA registry-qualified
criterion or rule identity as the alias.
Dependency links target stories, and every dependency has a reason in the
`Dependencies` section. List position is not dependency evidence.

## 3. Author test plans

The QA Engineer and Business Analyst co-author the sibling `test-plan.md`.
Every scenario has a stable `<story-id>-TS-###` heading and this shape:

```markdown
## ST-001-TS-001

- category: happy-path
- target: api
- automation: required
- automation_target: tests/api/test_accounts.py::test_register_account
- source_refs:
  - [[business-analysis/accounts/acceptance/account-access-acceptance|accounts:AC-ACC-001]]
- Given: the preconditions are satisfied
- When: the story action occurs
- Then: the observable outcome is correct
```

`automation` is `required` or `manual`; `required` needs an
`automation_target`. The target records intended delivery work and need not
exist yet. Every mapped criterion and rule appears in at least one scenario.
The scenario set explicitly considers empty, boundary, invalid input,
authorization, duplicate or concurrent action, failure and adjacent-regression
paths, recording why any inapplicable class is excluded.

The preparation trace ends at planned verification:

```text
criterion or rule -> scenario -> automation target
```

Executable tests, execution results, story completion and release readiness
belong to delivery.

## 4. Challenge and render

Run the packaged compiler:

```text
backlog_compile.py check --docs <workspace>/docs --render --json
```

An epic review uses `derives_from` for its owning epic and `verifies` for the
exact child story and test-plan set. Its body covers scope, slicing, criteria,
test design, intra-epic dependencies, role ownership, findings and verdict.

The root review uses `derives_from` for the root backlog and `related_to` for
the exact epic set. Its body covers cross-epic overlap, dependency direction,
cycles, release ordering, shared contracts, deferred criteria, global test
coverage, findings and verdict. Fix every finding and repeat until both review
layers are approved.

## 5. User approval and handoff

After the user approves the exact diff, run the packaged compiler atomically:

```text
backlog_compile.py approve --docs <workspace>/docs
backlog_compile.py check --docs <workspace>/docs --approved --render --json
```

Approval stamps the package, root backlog, epics, reviews and test plans while
stories remain `planned`. Commit `workspace/docs/backlog/` and the updated
`workspace/config.json` in the same project change. Report the package hash
and exact generated views. Stop and route to `deliver`; do not create delivery
state in this flow.
