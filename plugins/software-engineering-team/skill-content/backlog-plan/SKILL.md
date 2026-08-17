---
name: backlog-plan
description: User-gated preparation entry that turns approved analysis, solution, design-system and experience documents into a project-local nested Markdown backlog with epic reviews, story test plans and reproducible coverage views.
exposure: entry
---

# Backlog Plan

Create the durable delivery-ready plan after upstream product decisions are
approved. This entry prepares delivery, but it does not start delivery.

## When to Use

- Requirement Flow has approved the request impact matrix and every stage
  marked `required` is approved and current; `reuse` rows cite valid approved
  evidence and `not_applicable` rows carry their concrete rationale.
- Every defect or technical intake has its approved source, issue or decision
  evidence in the Requirement record when no feature traceability applies.
- The user wants a tracked backlog before any delivery work begins.

## Procedure

1. Run `requirement_route.py --project-root <root> REQ-### --json`. Require the
   response to route to `backlog-plan`; an earlier entry is a hard route.
2. Read `flows/backlog-planning.md`, `product-planning` and the `obsidian-vault`
   skill completely; the vault policy is authoritative for paths and metadata.
3. Initialize `workspace/docs/backlog/` with `backlog_compile.py init`.
4. Create one folder per epic. Each epic contains `epic.md`, `reviews/` and
   `stories/<story-slug>/story.md` plus `test-plan.md` for every story. Authored
   titles and H1s use the configured document-type designations. The
   capitalized designation is the complete root backlog/root-review label;
   authored epic/story/test-plan bases append it. Stable paths, IDs and type
   keys do not change with display language.
5. Assign one `owner_role` per story and any concrete `supporting_roles`.
   Author the seven required story sections, resolvable upstream links and
   dependency reasons. Set `work_kind` to `feature`, `defect` or `technical`.
   `work_kind` is independent from the request route. Feature work carries the
   approved criterion, Experience, Design System and Solution Design refs when
   those stages constrain the story. Defect and technical work may use explicit
   approved source, issue or decision evidence when the impact matrix says the
   feature stages are not applicable. Do not add an assignee or runtime
   identity. Historical BA is out of scope unless the root backlog deliberately
   declares canonical `analysis_scopes`.
6. Give every scenario non-empty source refs. Feature scenarios use story
   criteria; defect/technical scenarios use declared criteria and/or approved
   `related_to` evidence. Map every declared planning source to at least one
   scenario and complete the exact seven-class
   coverage table: `empty`, `boundary`, `invalid-input`, `authorization`,
   `duplicate-concurrent`, `failure`, `adjacent-regression`. A covered class
   cites existing scenarios; an inapplicable class cites none and explains why.
   Covered rows classify the exact scenario set. Give every
   automation-required scenario an automation target.
7. Run the packaged `backlog_compile.py check --render` and the scoped
   Obsidian vault check;
   initialization reconciles the backlog property/graph fragment and renders
   the map and backlog navigation. Outgoing wikilinks provide graph and
   backlink relations without rewriting approved upstream notes. Challenge the
   whole package through fresh read-only backlog reviewers. Give each reviewer
   an exact named input set and expected relation sets; wait for all epic
   reviewers before the Product Owner writes any epic review or fix. The
   Product Owner is the only backlog writer. After epic packages are green,
   invoke and wait for the root reviewer, then let the Product Owner write the
   root review. The epic review covers the exact story and test-plan set; the
   root review covers the exact epic set, global scope, dependencies, release
   ordering and coverage. Its structured `Deferred Criteria` table carries
   an escaped-table vault wikilink `criterion_ref`, `owner_role`, `reason` and
   `revisit_trigger`. Every selected AC/BR is either story-covered or deferred,
   never both. Replace all review
   placeholders and generic approvals with concrete evidence and conclusions.
8. Only after the user approves the exact Markdown changes, use the packaged
   compiler's atomic `approve` verb. It stamps the package, backlog, epics,
   reviews and test plans, preserves stories as `planned`, renders
   `_generated/registry.json`, `board.md`, `dependency-map.md` and
   `test-coverage.md`, and verifies the resulting package hash. Commit the
   tracked docs.
9. Stop with `deliver` as the next explicit entry. Backlog approval is not
   delivery activation.
