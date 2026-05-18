# Agent Rules

Canonical specification for any Claude Code subagent shipped under `.claude/agents/<id>.md`. `create-agent` and `update-agent` read this file as the source of truth.

## File location

Single file: `.claude/agents/<id>.md`. No project folder. No sibling files.

## Frontmatter

```yaml
---
name: <id>                       # kebab-case, must match file name (without .md), [a-z0-9-], max 64 chars
description: <one-line>          # when to invoke this subagent, max 1024 chars
tools: <comma-list>              # optional, e.g. "Read, Grep, Bash". Omit to inherit parent tools.
model: <opus|sonnet|haiku>       # optional, omit to inherit parent's model
---
```

Required: `name`, `description`. Optional: `tools`, `model`.

## Naming rules

- File name (without `.md`) and frontmatter `name` must be identical.
- Format: `^[a-z][a-z0-9-]*[a-z0-9]$`, no consecutive dashes, no trailing dash, max 64 chars.

## Body

System prompt the subagent executes when invoked. Caveman style: terse, bullets, no preambles, no trailing summary.

Required body sections, in order:

1. `## Purpose` - one sentence.
2. `## Inputs` - what the parent passes when invoking. Ask user if missing.
3. `## Flow` - step-by-step procedure.
4. `## Rules` - caveman style enforcement, no em dash, no hardcoded paths, run workspace integration.

## Run workspace integration (mandatory)

The body must instruct the subagent to open a run workspace per CLAUDE.md section 13 at start of every invocation:

- Generate UUID4 on entry.
- Create `.run/<uuid>/META.md` with `status: pending`, `component: <id>`, `kind: agent`.
- Create `.run/<uuid>/artifacts/`.
- Print to chat: `Task <uuid-short> started. Output at .run/<uuid-full>/`.

And close on exit:

- Set `status: done | failed | cancelled`, set `ended`, populate `## Outputs` and `## Artifacts` sections.
- Print: `Task <uuid-short> done. See .run/<uuid-full>/META.md.`

## Dependency declaration (Phase 1)

Agents do not carry a `manifest.yaml`. Other components may declare `depends_on: [<agent-id>]` in their own manifests; agents themselves do not declare dependencies in Phase 1.

## Deep mode (optional, opt-in at create or update time)

A generated agent may be marked **deep**. A deep agent inherits the marketplace epistemic reasoning loop and runs it on every invocation before producing any output. Non-deep agents skip the loop entirely.

When deep is enabled, the body **must** contain a step `0` block at the very top of `## Flow`, immediately above the existing step `1`. The block has this exact shape (verbatim, except for whitespace):

```
0. **Deep mode**. This agent inherits the marketplace epistemic reasoning loop. Before any other step:
   - `Read .claude/skills/create-agent/references/personas.md`
   - `Read .claude/skills/create-agent/references/loop-spec.md`
   - Apply the loop per `loop-spec.md` (10 iterations, 10 questions, 10 personas, 10 critics, early-exit on consensus) to the inbound request. Persist iteration artifacts under `.run/<uuid>/artifacts/iter-<N>/`. Synthesize a final approach. Only then proceed to step 1.

```

The two repo-root-relative paths inside the Read instructions are the **canonical** location for the persona and loop specs. They are the single source of truth for the entire marketplace; the other deep primitives (`create-skill`, `update-skill`, `update-agent`) also read these same files.

A non-deep agent's body has no step `0`; `## Flow` begins directly at step `1`.

`create-agent`'s `scripts/render_template.py --deep` produces the deep block; without `--deep` it is omitted. `update-agent` inserts or removes the same block when toggling deep mode on an existing agent.

## Banned content

- The em dash character (U+2014). Use comma, colon, hyphen, or two sentences.
- Absolute paths beginning with user-home prefixes (macOS `/Users`, Linux `/home`, or any `~`-style reference). Use repo-root-relative paths instead.

## Subagent constraints (Claude Code platform fact)

- A subagent cannot invoke another subagent. It can invoke skills via natural language.
- Subagent runs in its own isolated context. Returns a single final response to the caller.
- `tools` frontmatter restricts what the subagent may use; omit to inherit caller's tools.
