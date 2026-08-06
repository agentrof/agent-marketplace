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

1. Read `workspace/config.json`; require `project_origin`, `project_key`, an approved BA scope, an approved solution landscape and `workspace/docs/design-system/MASTER.md`. Run `preparation_check.py status --project-root <root> --json` and stop at the entry it names when an earlier stage is incomplete.
2. Load the canonical flow at `flows/experience-design.md`, the
   `experience-modeling` knowledge skill and the `obsidian-vault` law. Follow
   them exactly.
3. Work only in `workspace/docs/experience-design/` and `workspace/experience-design-work/<run-key>/`, plus the experience map/home reconciliation owned by the vault policy. Never promote a sketch as a baseline.
4. Stop after the program gate. Name `backlog-plan` as the next entry. Do not activate a release and do not start delivery.
