# update-agent

Primitive marketplace skill. Dependency-aware mutator for existing subagents at `.claude/agents/<id>.md`.

## When to use

User asks to update, modify, rename, or remove an existing subagent.

## What it does

Runs three mechanical scans (depends_on graph BFS with cycle detection, content grep, path-rename check) plus an advisory semantic review and the epistemic loop. Produces an impact report listing every dependent at every depth. Writes nothing to `.claude/` without explicit user approval.

## Key files

- `references/dep-check-rules.md` - procedural rules for each dependent (local).
- `references/agent-rules.md` - subagent spec (local, must still hold after the mutation).
- Canonical loop spec (read at runtime): `.claude/skills/create-agent/references/loop-spec.md`.
- Canonical persona set (read at runtime): `.claude/skills/create-agent/references/personas.md`.
- `scripts/dep_scan.py`, `content_scan.py`, `path_scan.py` - same as `update-skill`, duplicated here per Phase 1 convention.
