# create-skill

Primitive marketplace skill. Scaffolds a new skill folder under `.claude/skills/<new-id>/` via dialog and an iterative reasoning loop.

## When to use

User asks to create, scaffold, generate, add, or initialize a new skill.

## What it does

Asks for missing inputs, runs a 10-iteration multi-persona reasoning loop, presents a synthesized design, and on approval writes the conformant skeleton plus body content to `.claude/skills/<id>/`.

## Key files

- `references/skill-rules.md` - full skill spec.
- `references/personas.md` - 10 epistemic personas used by the loop.
- `references/loop-spec.md` - loop mechanics.
- `assets/skill-template/` - skeleton folder cloned into the target.
- `scripts/render_template.py` - copies and substitutes placeholders.
