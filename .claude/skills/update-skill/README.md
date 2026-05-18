# update-skill

Primitive marketplace skill. Dependency-aware mutator for existing skills under `.claude/skills/<id>/`.

## When to use

User asks to update, modify, rename, restructure, or remove parts of a skill.

## What it does

Runs three mechanical scans (dependency BFS with cycle detection, content grep, path-rename check) plus an advisory semantic review and the epistemic loop. Produces an impact report listing every dependent at every depth. Writes nothing to `.claude/` without explicit user approval.

## Key files

- `references/dep-check-rules.md` - procedural rules Claude applies for each dependent.
- `references/skill-rules.md` - skill spec (must still hold after the mutation).
- `references/personas.md` - 10 epistemic personas used by the loop.
- `references/loop-spec.md` - loop mechanics.
- `scripts/dep_scan.py` - manifest depends_on graph walker, BFS with cycle detection.
- `scripts/content_scan.py` - greps SKILL.md, AGENT.md, README.md, manifest.yaml bodies.
- `scripts/path_scan.py` - greps for old path references when files move.
