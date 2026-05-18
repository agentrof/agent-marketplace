---
name: update-agent
description: Dependency-aware mutator for a subagent file at .claude/agents/<id>.md. Use when the user asks to update, modify, rename, or remove an existing subagent. Always runs deterministic mechanical scans plus advisory semantic review and the epistemic loop before any write.
---

# update-agent

## Purpose

Mutate an existing subagent file safely. Three mechanical scans plus an advisory semantic review produce an impact report listing every dependent at every depth. Nothing is written to `.claude/` without explicit user approval.

## Inputs (collect via dialog, never assume)

- `target_agent_id`: id of the subagent to mutate (file `.claude/agents/<id>.md`).
- `change_spec`: structured description of the change. Examples:
  - frontmatter field rename or update (`description`, `tools`, `model`)
  - body edit
  - id rename (which also renames the file)
  - **deep-mode toggle**: enable or disable the marketplace epistemic loop on this agent (inserts or removes the `0. **Deep mode** ...` preamble block under `## Flow` and the two canonical Read instructions per `.claude/skills/create-agent/references/agent-rules.md`)
  - removal

If any required field is missing, ask the user.

## Flow

1. **Open run workspace**. Generate UUID4. Create `.run/<uuid>/META.md` (`status: pending`, `component: update-agent`, `kind: skill`) and `.run/<uuid>/artifacts/`. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.

2. **Verify target**. Confirm `.claude/agents/<target_agent_id>.md` exists; if not, ask the user.

3. **Run epistemic loop** per the canonical specs at `.claude/skills/create-agent/references/loop-spec.md` and `.claude/skills/create-agent/references/personas.md`. The loop interrogates the proposed change. Iterations land in `.run/<uuid>/artifacts/iter-<N>/`. Loop exits on consensus.

4. **Mechanical scan (deterministic gate)**:
   - `scripts/dep_scan.py --target <target_agent_id> --output .run/<uuid>/artifacts/dep_scan.json` - finds skills whose `manifest.yaml.depends_on` lists this agent id.
   - `scripts/content_scan.py --target <target_agent_id> --rename-from <old> --rename-to <new> --output .run/<uuid>/artifacts/content_scan.json` (rename args optional).
   - `scripts/path_scan.py --old-path <old> --new-path <new> --output .run/<uuid>/artifacts/path_scan.json` (only when paths change, e.g. on id rename).

5. **Semantic review (advisory)**. Read the target's old and new frontmatter plus body, read each direct dependent (skill or other agent referencing this id), judge behavior shift.

6. **Compose impact report**. Write `.run/<uuid>/artifacts/impact-report.md` with two clearly labeled sections (mechanical, advisory). Render in chat.

7. **Ask the user**: `Apply target only`, `Cascade to all affected`, or `Cancel`. Cycles flagged; cycle cascading needs per-component approval.

8. **On Apply or Cascade**: perform writes. If id renamed, move `.claude/agents/<old>.md` to `.claude/agents/<new>.md` and update consumer manifests. For deep-mode toggle, insert or remove the canonical deep snippet block precisely as defined in `.claude/skills/create-agent/references/agent-rules.md` (two Read lines plus loop wrapper as step 0 of `## Flow`). Save `.run/<uuid>/artifacts/applied-changes.diff`.

9. **Close run workspace**. Update `META.md`: `status`, `ended`, `## Outputs`, `## Artifacts`. Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Rules

- Never write to `.claude/` before user approval.
- Cycles never auto-resolve.
- Apply per `references/dep-check-rules.md` for every dependent.
- Caveman style, no em dash, no hardcoded paths.
- Phase 1 fact: agents have no `manifest.yaml`. Only consumers (skills) can declare `depends_on: [<agent-id>]`. Agents themselves declare no dependencies in Phase 1.
