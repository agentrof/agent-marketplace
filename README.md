# Agent Marketplace

Claude Code-native catalog of agents and skills. Self-bootstrapping: the marketplace ships six primitive skills that create, list, and update further entries.

## Layout

- `CLAUDE.md` - repo rules. Read first.
- `.claude/agents/<id>.md` - Claude Code subagents.
- `.claude/skills/<id>/` - Claude Code skills, each a fixed-shape project folder.
- `.run/<uuid>/` - per-execution workspace (gitignored).

## Six primitives

- `create-skill`, `create-agent` - scaffold new components via dialog and a deep epistemic reasoning loop.
- `list-skills`, `list-agents` - enumerate the catalog.
- `update-skill`, `update-agent` - dependency-aware mutators with mechanical scans, advisory semantic review, and impact report.

## Adding entries

Invoke `create-skill` or `create-agent` in Claude Code. The skill asks for missing inputs, runs its loop, presents a synthesized design, and writes to `.claude/` on approval.

## Status

Phase 1: Claude Code-only, no runner, no cross-IDE shims, no orchestrator. See `CLAUDE.md` section 17 for full out-of-scope list.
