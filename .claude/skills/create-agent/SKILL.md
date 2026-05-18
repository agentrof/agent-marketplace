---
name: create-agent
description: Scaffold a new Claude Code subagent at .claude/agents/<id>.md. Use when the user asks to create, scaffold, generate, add, or initialize a new agent. Runs an iterative multi-persona reasoning loop before writing.
---

# create-agent

## Purpose

Create a conformant new subagent file at `.claude/agents/<new-id>.md` following the marketplace specification in `references/agent-rules.md`. Output is structurally correct and reasoned through the epistemic loop in `references/loop-spec.md`.

## Inputs (collect via dialog, never assume)

- `agent_id`: kebab-case unique id, matches new file name (without `.md`).
- `description`: one line, when to invoke this agent.
- `tools`: optional comma-separated tool list (e.g. `Read, Grep, Bash`).
- `model`: optional, one of `opus`, `sonnet`, `haiku`.
- `purpose`: a paragraph for the loop to reason about. Required.

If any required field is missing, ask the user. Do not guess.

## Flow

1. **Open run workspace**. Generate a UUID4. Create `.run/<uuid>/META.md` (`status: pending`). Create `.run/<uuid>/artifacts/`. Print `Task <uuid-short> started. Output at .run/<uuid-full>/`.

2. **Verify id uniqueness**. Check `.claude/agents/<agent_id>.md` does not exist; ask the user for a new id if it does.

3. **Run epistemic loop** per `references/loop-spec.md`, using `references/personas.md`. Each iteration writes to `.run/<uuid>/artifacts/iter-<N>/`. Loop exits early on consensus.

4. **Synthesize**. Write `.run/<uuid>/artifacts/final-design.md` summarizing the agreed-on agent design (frontmatter fields, system-prompt outline).

5. **Present** the synthesis in chat. Ask: "Apply, Revise, or Cancel?"

6. **On Apply**: invoke `scripts/render_template.py <agent_id> <description> [--tools ...] [--model ...]`. Then customize the body with the loop's synthesized system prompt. Write `.run/<uuid>/artifacts/summary.md`.

7. **Close run workspace**. Update `META.md`: `status: done`, `ended` set, `## Outputs` lists `.claude/agents/<agent_id>.md`, `## Artifacts` lists `summary.md`, `final-design.md`, iter folders. Print `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## On Revise

Re-enter at synthesis with user feedback as input. Do not rerun all 10 iterations unless asked.

## On Cancel

`META.md` status `cancelled`, no write to `.claude/agents/`.

## Rules

- Generated subagent body must instruct future Claude invocations to open a run workspace per CLAUDE.md section 13.
- Caveman style, no em dash, no hardcoded paths.
