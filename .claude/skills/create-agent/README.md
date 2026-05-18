# create-agent

Primitive marketplace skill. Scaffolds a new Claude Code subagent file at `.claude/agents/<new-id>.md` via dialog and an iterative reasoning loop.

## When to use

User asks to create, scaffold, generate, add, or initialize a new agent.

## What it does

Asks for missing inputs, runs a 10-iteration multi-persona reasoning loop, presents a synthesized design, and on approval writes the single-file subagent.

## Key files

- `references/agent-rules.md` - subagent file spec.
- `references/personas.md` - 10 epistemic personas (canonical for the whole marketplace; read by every deep primitive and every deep generated agent).
- `references/loop-spec.md` - loop mechanics (canonical, same).
- `assets/agent-template.md` - skeleton subagent file with `{{DEEP_SECTION}}` placeholder.
- `scripts/render_template.py` - writes the substituted file. `--deep` flag enables the canonical loop preamble in the generated agent body.
