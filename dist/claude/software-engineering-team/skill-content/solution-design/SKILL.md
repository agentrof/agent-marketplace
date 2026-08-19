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

1. Read `flows/solution-design.md` completely and resolve mode. Requirement
   mode accepts only the router-bound BA receipt. Manual mode requires one
   explicitly selected strict-current BA package; a legacy-readonly package
   can only be reused by Requirement, not used to author a new landscape.
   Multiple candidates stop
   for selection before any file is created.
2. Read `references/engagement-session.md`, the team constitution, the
   `solution-architecture` skill and the `obsidian-vault` skill. Confirm the
   local workspace config belongs to the Software Engineering Team.
3. Run the shared BA package resolver for every cited scope, then run:

   ```text
   landscape_check.py --tree workspace/docs/solution-design
   vault_check.py check --vault workspace/docs --scope solution-design --json
   ```

   Run the packaged scripts.

   A missing or failing predecessor routes back to Business Analysis. The
   approved documents are the complete stage state.
4. `solution-architect` is the sole writer. Work one kebab-case engagement
   topic at a time. First allocate every active BA process to a topology
   component, or list it in the landscape's `not_technical_allocations` as
   `<canonical-process-ref>|<rationale>`. Record the owner-approved topology, app/component names,
   responsibility and dependency boundaries in `components/<component-id>/`.
   A `build` component is the only kind that declares an `app_kind` and exact
   future `workspace/apps/<component-id>` path; external, managed and
   self-hosted dependencies remain components without project source trees.
5. Record framing, constraints, options, rejected alternatives, verdict,
   accepted technology/data-store/environment/integration decisions and exact
   traceability links in `engagements/` and `decisions/`. Component stacks may
   differ; no config-derived global stack is assumed.
6. Run the named read-only `solution-reviewer` before the project decision gate.
   Apply accepted resolutions to the canonical documents, then re-run only
   affected readers until no blocking evidence gap remains. Reviewer replies
   are transient input, not files. Render the decision index, relations and
   navigation after each accepted change.
7. Ask the owner to Approve, Request changes or Pause through the host choice
   gate. On approval, close the engagement, run
   `landscape_check.py confirm-topology --tree workspace/docs/solution-design`,
   then `landscape_check.py approve --tree workspace/docs/solution-design` to
   stamp the package. The confirmation records the complete topology, naming
   and allocation set. An approved package first uses `begin-revision` before edits.
8. When the landscape package is approved, bind it in Requirement mode or
   report its exact receipt and `design-system` in manual mode.
   This skill never creates implementation tasks or delivery state.
