---
name: delivery-plan
description: Create or resume one scope-bound Delivery proposal and prepare its offline Delivery records.
exposure: entry
---

# Delivery Planning

## When to Use

- Use after the approved backlog and Definition of Done are available.
- Use when the user wants to define one reviewable outcome without starting
  implementation or creating Git coordination state.

Use `/delivery-plan "<goal>"` for a new local proposal. Use
`/delivery-plan DLV-###` only to resume or revise that exact Delivery. The
entry never fuzzy-resumes an older Delivery and never creates a branch,
worktree, slot or remote ref by itself.

Read `flows/delivery-planning.md` completely before creating the proposal.

## Required order

1. Confirm the approved backlog, the exact story and test-plan links, and the
   approved Definition of Done.
2. Run `delivery_compile.py init` in the project workspace to render the
   temporary semantic proposal. Show goal, observable outcome, exclusions,
   dependencies and conflicts.
3. After the user approves scope, run `approve-scope`; only the later Git
   coordinator may publish the package and reserve the Delivery.
4. If the Definition of Done is absent, route through `/configure DOD` and
   return to the original goal after the protected documentation handoff.

There is no duration, cadence, estimate, velocity or release field. A Delivery
is one reviewable outcome and may contain one or many backlog stories.
