---
name: experience-design
description: Designs and checks user-facing content, interaction and visual states for UI bugfixes, screens, journeys, navigation and workflows.
exposure: internal
---

# Experience Design

## Outcome

A coherent user journey whose content, interaction and visual states work in
the product's actual context.

## When to use

Load for changed user-facing content, interaction, presentation, accessibility
or navigation. Visible-label bugfixes require this check.
Apply an established component directly for a clear local correction.

## Inputs and authority

Use the task, accepted business behavior, affected journeys, interface contract,
design system and current rendered product. Preserve the project's experience
document paths and chosen direction. A visual proposal does not redefine
permissions, data meaning or product scope.

## Method

1. Identify the user's job, device/input context, information hierarchy and
   difficult state. Inspect the affected surface before proposing changes.
   Use installed, enabled Impeccable for this scope and record its actual load
   and visual evidence through the delivery companion procedure.
2. Reuse the existing design language. For a new direction, compare genuinely
   different structures and explain the tradeoffs before refinement. Avoid
   reopening settled design choices for an ordinary component fix.
3. Specify success, loading, empty, error, denied, stale and pending states
   where relevant. Explain what happened and how to recover. Prevent accidental
   duplicate actions and make save, cancellation and conflict behavior clear.
4. Connect the journey to actual data and interface behavior. Design semantic
   controls, keyboard/focus paths, readable content, responsive layout and
   reduced-motion behavior. Use the project's tokens and accessibility target.
5. Inspect the rendered result at relevant sizes and states. Compare it with
   the intended outcome and correct material differences within the delivery
   budget. Update the affected design knowledge alongside the implementation.

## Evidence and completion

Return the changed journey/states, major design rationale, implementation
constraints and observed visual/interaction evidence. Name anything that could
not be exercised. Screenshots establish appearance only, so validate important
interactions through the running surface.

## Conditional references

- For interface states, accessibility or design-system changes, read [interface](references/interface.md).
- For browser implementation details, read [implementation](../solution-design/references/implementation.md).
- For live interaction checks, load [verify](../verify/SKILL.md).
