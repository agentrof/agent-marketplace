# Design Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Use this state-machine for a bounded design preview or demo. It is a
project-local document flow, independent of delivery orchestration.

## Rules

1. Execute directions, pick, refinement and approval in that order.
2. Read prior outputs from files. Stop at every explicit user choice gate.
3. Load the `obsidian-vault` skill before any docs-tree write.
4. On a failed mechanical check, stop, show the finding and repair it before
   asking for the next choice.

## Preconditions

- The relevant Business Analysis space passes its compiler approval gate.
- `workspace/docs/design-system/MASTER.md` exists when the preview uses a
  design system. Missing upstream content routes to its owning entry.
- The project-local workspace config exists and belongs to the team.

## Steps

### 1. Directions

The UX Designer produces three genuinely divergent directions in one preview
file with realistic placeholder data, an explicit difference axis and token
references. Use `workspace/sketches/<slug>/preview.html` or the requested demo
path. Check that the file exists, opens standalone and contains all directions.

### 2. Direction gate and refinement

Present one choice per direction with its tradeoff. Refine the chosen direction
in the same file. Record page-specific token deviations in the design-system
page override tree rather than silently changing the master. Re-run the
contrast, focus, dark-mode and reduced-motion checklist after each refinement.

### 3. Handshake and persistence

The project decision authority approves the refined preview through a choice gate. Keep the approved
preview at its durable project-local path, commit it, and report the owning
entry's next step. Preview approval creates no delivery state.
