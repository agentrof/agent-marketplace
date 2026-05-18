---
name: {{SKILL_ID}}
description: {{DESCRIPTION}}
---

# {{SKILL_ID}}

## Purpose

{{PURPOSE}}

## Inputs

- {{INPUT_1}}
- {{INPUT_2}}

If any required field is missing, ask the user. Do not guess.

## Flow

1. Open run workspace per CLAUDE.md section 13. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.
2. {{STEP_1}}
3. {{STEP_2}}
4. Close run workspace. Update `META.md` (`status: done`, `## Outputs` populated, `## Artifacts` listed). Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Rules

- Caveman style.
- No em dash.
- No hardcoded paths.
