# update-agent

Primitive marketplace skill. Dependency-aware mutator for existing subagents at `.claude/agents/<id>.md`.

## When to use

User asks to update, modify, rename, or remove an existing subagent.

## What it does

Runs three mechanical scans (depends_on graph BFS with cycle detection, content grep, path-rename check) plus an advisory semantic review and the epistemic loop. Produces an impact report listing every dependent at every depth. Writes nothing to `.claude/` without explicit user approval.

## Key files

- `references/dep-check-rules.md` - procedural rules for each dependent.
- `references/agent-rules.md` - subagent spec (must still hold after the mutation).
- `references/personas.md` - 10 epistemic personas used by the loop.
- `references/loop-spec.md` - loop mechanics.
- `scripts/dep_scan.py`, `content_scan.py`, `path_scan.py` - same as `update-skill`, duplicated here per Phase 1 convention.
