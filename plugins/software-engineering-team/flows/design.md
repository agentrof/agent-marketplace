# Design Flow

Spawn template: paste `{{constitution}}` into every role prompt.

Vault first, per constitution section 5: every role starts from the default `project_reading` plan.
Batch-read its units, using the frozen context reader for frozen candidates. When context is insufficient, wrong or unavailable, use manual search, reads and relationship discovery
on the role's initiative or parent direction; record sources and reasons, rebind evidence, preserve gates and return `context_findings` to the parent for user-approved reporting.
For gaps, use `vault_query.py`, machine indexes and generated views, typed frontmatter, relation blocks and wikilinks, then maps and targeted search.
Fallback navigation with a shell can use `home.md`, `maps/_generated/relation-status.md`,
`maps/_generated/cross-subtree-matrix.md` and
`maps/_generated/stale-relations.md`, the Design System MASTER the
preview uses. Switch `context_pack`: at `role_digest`, a spawned role
receives its pack from `context_pack.py build --entry <entry> --role
<role> --mode <mode> --project-root <root>` instead of the full required
reads; it reads a named source in full only when the pack does not cover
a case, and records that read and why, as
`skill-content/challenge-review/references/switch-context_pack-role_digest.md`
defines. Switch `step_timing`: at `recorded`, the coordinator runs
`step_timing.py start --run <run> --step <step> [--budget <id>]` before
each step and `step_timing.py end --run <run> --span <span>` after it,
and records each role spawn with `--kind spawn --parent <step span>
--role <role> --phase reading|writing|review|re_review|waiting`. An end
that returns overrun is reported to the owner at once with its
breakdown, largest contributor and lever. The run ends with
`step_timing.py report --run <run> --write`, as
`skill-content/challenge-review/references/switch-step_timing-recorded.md`
defines. Switch `step_budgets`: at `enforced`, each budgeted step is
compared with its maximum, the owner's parameter or the package target
in `skill-content/configure/data/step-budgets.json`; a step may finish
well under it, and only an exceeded maximum is reported, never blocking,
as
`skill-content/challenge-review/references/switch-step_budgets-enforced.md`
defines.

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
Switch `review_scope`: at `impact_closure`, a refinement reads only the picked
direction and the MASTER sections it uses; `task_inputs.py` derives each
reader's inputs with `--changed <note>` and `--base <approved commit>`, and a
re-check given `--findings` derives only the fix delta; every role reads the
change's impact closure, each approved, unchanged note outside it only as its
hash-bound summary, reads beyond it when unsure and records why, a writer
fixes a reported graph gap through its owning compiler and recomputes the
closure, and a confirmation re-review reads only the fix's delta, as
`skill-content/challenge-review/references/switch-review_scope-impact_closure.md`
defines.
