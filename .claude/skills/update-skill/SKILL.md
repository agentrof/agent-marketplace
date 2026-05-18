---
name: update-skill
description: Dependency-aware mutator for a skill under .claude/skills/<id>/. Use when the user asks to update, modify, rename, restructure, or remove parts of an existing skill. Always runs deterministic mechanical scans plus advisory semantic review and the epistemic loop before any write.
---

# update-skill

## Purpose

Mutate an existing skill safely. Three mechanical scans plus an advisory semantic review produce an impact report listing every dependent at every depth in the dependency graph. Nothing is written to `.claude/` without explicit user approval.

## Inputs (collect via dialog, never assume)

- `target_skill_id`: id of the skill to mutate.
- `change_spec`: structured description of the change. Examples:
  - frontmatter field rename or value update
  - manifest field update (`description`, `tags`, `depends_on`, `version`)
  - body edit
  - file rename or move inside the skill folder
  - file removal

If any required field is missing, ask the user. Do not guess.

## Flow

1. **Open run workspace**. Generate UUID4. Create `.run/<uuid>/META.md` (`status: pending`, `component: update-skill`, `kind: skill`) and `.run/<uuid>/artifacts/`. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.

2. **Verify target**. Confirm `.claude/skills/<target_skill_id>/manifest.yaml` exists; if not, ask the user.

3. **Run epistemic loop** per `references/loop-spec.md`, using `references/personas.md`. The loop interrogates the proposed change itself: is it the right change, is the wording correct, are there hidden side effects. Iterations land in `.run/<uuid>/artifacts/iter-<N>/`. Loop exits on consensus.

4. **Mechanical scan (deterministic gate)**. Run three scripts in sequence and persist their raw JSON outputs:
   - `scripts/dep_scan.py --target <target_skill_id> --output .run/<uuid>/artifacts/dep_scan.json`
   - `scripts/content_scan.py --target <target_skill_id> --rename-from <old_string> --rename-to <new_string> --output .run/<uuid>/artifacts/content_scan.json` (rename args optional)
   - `scripts/path_scan.py --old-path <old> --new-path <new> --output .run/<uuid>/artifacts/path_scan.json` (only when paths change)

5. **Semantic review (advisory)**. Read the target's old and new description plus body, read each direct dependent's body, judge whether behavior change would break the dependent. Findings appended in memory.

6. **Compose impact report**. Write `.run/<uuid>/artifacts/impact-report.md` with two clearly labeled sections:
   - `## Mechanical findings (deterministic)` - direct dependents, transitive dependents by depth, cycles, content matches, path references.
   - `## Advisory findings (Claude review)` - semantic concerns. May be empty.
   Render the report in chat.

7. **Ask the user**: `Apply target only`, `Cascade to all affected`, or `Cancel`. Cycles are flagged as warnings; cascading inside a cycle requires per-component confirmation.

8. **On Apply or Cascade**: perform the writes. Save `.run/<uuid>/artifacts/applied-changes.diff` capturing the resulting file changes. Update consumer manifests when ids are renamed.

9. **Close run workspace**. Update `META.md`: `status` = `done` | `cancelled` | `failed`, `ended` set, `## Outputs` lists every `.claude/` file touched, `## Artifacts` lists `impact-report.md`, `dep_scan.json`, `content_scan.json`, `path_scan.json` (if produced), `applied-changes.diff` (if applied), iteration folders. Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Rules

- Never write to `.claude/` before user approval.
- Cycles: do not auto-resolve; surface to the user.
- Apply per `references/dep-check-rules.md` for every dependent encountered.
- Caveman style, no em dash, no hardcoded paths.
