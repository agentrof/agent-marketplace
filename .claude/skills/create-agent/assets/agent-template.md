---
name: {{AGENT_ID}}
description: {{DESCRIPTION}}
{{TOOLS_LINE}}
{{MODEL_LINE}}
---

# {{AGENT_ID}}

## Purpose

{{PURPOSE}}

## Inputs

- {{INPUT_1}}
- {{INPUT_2}}

If any required field is missing, ask the user. Do not guess.

## Flow

1. Open run workspace per CLAUDE.md section 13. Generate UUID4, create `.run/<uuid>/META.md` (`status: pending`, `component: {{AGENT_ID}}`, `kind: agent`) and `.run/<uuid>/artifacts/`. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.
2. {{STEP_1}}
3. {{STEP_2}}
4. Close run workspace. Update `META.md` (`status: done`, `ended` set, `## Outputs` populated, `## Artifacts` listed). Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Rules

- Caveman style.
- No em dash.
- No hardcoded paths.
- Cannot invoke other subagents (Claude Code platform constraint). May invoke skills via natural language.
