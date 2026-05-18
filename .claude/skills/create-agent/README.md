# create-agent

Primitive marketplace skill. Scaffolds a new Claude Code subagent file at `.claude/agents/<new-id>.md` via dialog and an iterative reasoning loop.

## When to use

User asks to create, scaffold, generate, add, or initialize a new agent.

## What it does

Asks for missing inputs, runs a 10-iteration multi-persona reasoning loop, presents a synthesized design, and on approval writes the single-file subagent.

## Key files

- `references/agent-rules.md` - subagent file spec.
- `references/personas.md` - 10 epistemic personas used by the loop.
- `references/loop-spec.md` - loop mechanics.
- `assets/agent-template.md` - skeleton subagent file.
- `scripts/render_template.py` - writes the substituted file.
