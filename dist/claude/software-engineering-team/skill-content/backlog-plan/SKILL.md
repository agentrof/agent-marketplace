---
name: backlog-plan
description: User-gated preparation entry that turns approved analysis, solution, design-system and experience documents into a project-local nested Markdown backlog with epic reviews, story test plans and reproducible coverage views.
exposure: entry
---

# Backlog Plan

Create the durable delivery-ready plan after upstream product decisions are
approved. This entry prepares delivery, but it does not start delivery.

## When to Use

- Business Analysis, Solution Design, Design System and Experience Design are
  approved and their compilers are green.
- The user wants a tracked backlog before any delivery work begins.

## Procedure

1. Run `preparation_check.py status --project-root <root> --json`. Require the
   response to route to `backlog-plan`; an earlier entry is a hard route.
2. Read `flows/backlog-planning.md`, `product-planning` and the `obsidian-vault`
   skill completely; the vault policy is authoritative for paths and metadata.
3. Initialize `workspace/docs/backlog/` with `backlog_compile.py init`.
4. Create one folder per epic. Each epic contains `epic.md`, `reviews/` and
   `stories/<story-slug>/story.md` plus `test-plan.md` for every story.
5. Assign one `owner_role` per story and any concrete `supporting_roles`.
   Author the seven required story sections, resolvable upstream links,
   dependency reasons, complete criterion coverage and an automation target
   for every automation-required scenario. Do not add an assignee or any
   runtime identity.
6. Run the packaged `backlog_compile.py check --render` and the scoped
   Obsidian vault check;
   initialization reconciles the backlog property/graph fragment and renders
   the map and backlog navigation. Outgoing wikilinks provide graph and
   backlink relations without rewriting approved upstream notes. Challenge
   the whole
   package: the epic review covers the exact story and test-plan set; the root
   review covers the exact epic set, global scope, dependencies, release
   ordering and coverage.
7. Only after the user approves the exact Markdown changes, use the packaged
   compiler's atomic `approve` verb. It stamps the package, backlog, epics,
   reviews and test plans, preserves stories as `planned`, renders
   `_generated/registry.json`, `board.md`, `dependency-map.md` and
   `test-coverage.md`, and verifies the resulting package hash. Commit the
   tracked docs.
8. Stop with `deliver` as the next explicit entry. Backlog approval is not
   delivery activation.
