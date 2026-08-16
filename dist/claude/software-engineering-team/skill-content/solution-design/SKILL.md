---
name: solution-design
description: Interactive, file-first solution landscape and decision workflow after approved Business Analysis and before Design System, Experience Design and backlog planning.
exposure: entry
---

# Solution Design

Maintain one project-level solution landscape under
`workspace/docs/solution-design/`. Every accepted technology, boundary,
integration, method and sustainability verdict becomes a linked Markdown
decision. Nothing decided remains only in conversation.

## When to Use

- Business Analysis has an approved scope and the project needs landscape or
  architecture decisions.
- A later product change revisits an existing solution decision.

## Procedure

1. Read `references/engagement-session.md`, the team constitution, the
   `solution-architecture` skill and the `obsidian-vault` skill. Confirm the
   local workspace config belongs to the Software Engineering Team.
2. Run the Business Analysis approval gate for every cited scope, then run:

   ```text
   landscape_check.py --tree workspace/docs/solution-design
   vault_check.py check --vault workspace/docs --scope solution-design --json
   ```

   Run the packaged scripts.

   A missing or failing predecessor routes back to Business Analysis. The
   approved documents are the complete stage state.
3. Work one kebab-case engagement topic at a time. Record framing, constraints,
   options, rejected alternatives, verdict, affected components and exact
   traceability links in `engagements/`, `decisions/` and `reviews/`.
4. Run one fresh read-only challenge round before the project decision gate. Fix every
   mechanical finding; disposition judgment findings with a reason or named
   revisit note. Render the decision index, relations and navigation after
   each accepted change.
5. Ask the owner to Approve, Request changes or Pause through the host choice
   gate. On approval, stamp the engagement with its compiler, update the
   landscape and maps, run the complete checks, and commit the tree.
6. When the landscape is approved, report `design-system` as the next entry.
   This skill never creates implementation tasks or delivery state.
