---
name: create-skill
description: Scaffold a new Claude Code skill under .claude/skills/<id>/ with the fixed marketplace shape. Use when the user asks to create, scaffold, generate, add, or initialize a new skill. Runs an iterative multi-persona reasoning loop before writing.
---

# create-skill

## Purpose

Create a conformant new skill folder under `.claude/skills/<new-id>/` following the marketplace specification in `references/skill-rules.md`. Output is structurally correct and reasoned through the epistemic loop in `references/loop-spec.md`.

## Inputs (collect via dialog, never assume)

- `skill_id`: kebab-case unique id, matches new folder name.
- `description`: one line, what the skill does and when to invoke it.
- `tags`: list of free-form tags.
- `depends_on`: list of ids this new skill will call or assume.
- `purpose`: a longer paragraph for the loop to reason about. Required for the loop to be meaningful.

If any required field is missing, ask the user. Do not guess.

## Flow

1. **Open run workspace**. Generate a UUID4. Create `.run/<uuid>/META.md` with `status: pending`, frontmatter populated. Create `.run/<uuid>/artifacts/`. Print to chat: `Task <uuid-short> started. Output at .run/<uuid-full>/`.

2. **Verify id uniqueness**. Read `.claude/skills/` listing; if `skill_id` already exists, ask the user for a new id.

3. **Run epistemic loop** per `references/loop-spec.md`. Use `references/personas.md` as persona spec. Each iteration writes to `.run/<uuid>/artifacts/iter-<N>/`. Loop exits early on consensus.

4. **Synthesize**. Write `.run/<uuid>/artifacts/final-design.md` summarizing the agreed-on skill design (frontmatter, body outline, scripts/assets/references/examples plan).

5. **Present** the final design in chat. Ask the user: "Apply, Revise, or Cancel?"

6. **On Apply**: invoke `scripts/render_template.py <skill_id> <description> [...]`. This copies `assets/skill-template/` to `.claude/skills/<skill_id>/` with placeholders substituted. Then materialize the rest of the design (`tags`, `depends_on`, body text) by editing the freshly written files. Write `.run/<uuid>/artifacts/summary.md` listing every file created.

7. **Close run workspace**. Update `META.md`: `status: done`, `ended` set, `## Outputs` lists created files, `## Artifacts` lists `summary.md`, `final-design.md`, and iter folders. Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## On Revise

Re-enter the loop from the synthesis step with the user's feedback as additional input. Do not rerun all 10 iterations unless the user asks.

## On Cancel

`META.md` status set to `cancelled`, `## Outputs` empty, `## Artifacts` lists what exists. No write to `.claude/`.

## Rules

- Caveman style in the generated `SKILL.md` body.
- No em dash anywhere.
- No hardcoded paths. Scripts derive root from `__file__`.
- Generated skill body must instruct future Claude invocations to open a run workspace per CLAUDE.md section 13.
