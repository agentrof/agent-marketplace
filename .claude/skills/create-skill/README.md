# create-skill

Primitive marketplace skill. Scaffolds a new skill folder under `.claude/skills/<new-id>/` via dialog and an iterative reasoning loop.

## When to use

User asks to create, scaffold, generate, add, or initialize a new skill.

## What it does

Asks for missing inputs, runs a 10-iteration multi-persona reasoning loop, presents a synthesized design, and on approval writes the conformant skeleton plus body content to `.claude/skills/<id>/`.

## Key files

- `references/skill-rules.md` - full skill spec (local).
- `assets/skill-template/` - skeleton folder cloned into the target.
- `scripts/render_template.py` - copies and substitutes placeholders.
- Canonical loop spec (read at runtime): `.claude/skills/create-agent/references/loop-spec.md`.
- Canonical persona set (read at runtime): `.claude/skills/create-agent/references/personas.md`.
