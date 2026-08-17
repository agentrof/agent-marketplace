---
name: experience-design
description: Interactive release-scoped product experience modeling after approved analysis, solution landscape and design system. Use to define journeys, bounded flow sets, screens, states, transitions and approved HTML artifacts before baseline backlog planning.
exposure: entry
---

# Experience Design

Produce an approved, compiler-backed experience baseline without implementing product code.

## When to Use

- Approved analysis, solution landscape and design master are ready for release experience work.
- A later release needs an experience delta before backlog planning.

## Procedure

1. Read `workspace/config.json`; require the Requirement Flow impact matrix to
   mark Experience Design as `required`, an approved BA scope, an approved
   solution landscape and `workspace/docs/design-system/MASTER.md`.
   Run `requirement_route.py --project-root <root> REQ-### --json` and stop at
   the entry it names when an earlier stage is incomplete.
2. Load the canonical flow at `flows/experience-design.md`, the
   `experience-modeling` knowledge skill and the `obsidian-vault` law. Follow
   them exactly.
3. Work only in `workspace/docs/experience-design/`, plus the experience
   map/home reconciliation owned by the vault policy. Markdown and HTML start
   as drafts at their durable release paths and are approved there. Never
   promote a sketch as a baseline and never create a filesystem run folder.
4. Stop after the program gate. Name `backlog-plan` as the next entry. Do not activate a release and do not start delivery.
