# Skill Rules

Canonical specification for any skill shipped under `.claude/skills/<id>/`. `create-skill` and `update-skill` both read this file as the source of truth.

## Required folder layout

```
.claude/skills/<id>/
  SKILL.md          # required; frontmatter + body
  README.md         # required; human docs
  manifest.yaml     # required; marketplace metadata
  scripts/          # required folder; .gitkeep if empty
  assets/           # required folder; .gitkeep if empty
  references/       # required folder; .gitkeep if empty
  examples/         # required folder; .gitkeep if empty
```

All four subfolders must exist. Empty ones are kept via `.gitkeep`.

## SKILL.md frontmatter

```yaml
---
name: <id>                    # kebab-case, must match folder name, [a-z0-9-], max 64 chars
description: <one-line>       # what + when to invoke, max 1024 chars
---
```

Required: `name`, `description`. No other frontmatter fields in Phase 1.

## manifest.yaml schema

```yaml
id: <kebab-case-unique-id>    # must match folder name and SKILL.md frontmatter name
kind: skill
version: <semver>             # default 0.1.0
description: <one-line>       # may match SKILL.md description verbatim
tags: [<tag>, ...]            # optional, default []
depends_on: [<id>, ...]       # optional, default []. ids of components called or assumed.
```

Required: `id`, `kind` (always `skill`), `version`, `description`. Optional: `tags`, `depends_on`.

No `provides`, no `inputs`, no `outputs`, no `output_target` in Phase 1.

## Naming rules

- Folder name, `id`, and SKILL.md `name` must be identical.
- Format: `^[a-z0-9-]+$`, must start with letter, no consecutive dashes, no trailing dash.
- Max 64 characters.

## Banned content in any file

- The em dash character (U+2014). Use comma, colon, hyphen, or two sentences instead.
- Absolute paths beginning with user-home prefixes (macOS `/Users`, Linux `/home`, or any `~`-style reference). Use repo-root-relative paths instead.

## Folder semantics

- `scripts/`: executable code the skill invokes via Bash. Claude does not read these as instructions. Python 3, derive root from `__file__`, exit non-zero on failure.
- `assets/`: static files copied verbatim into target locations. Skeletons, templates, fixtures.
- `references/`: documents Claude loads into context on demand. Specs, persona definitions, rule lists.
- `examples/`: completed inputs and outputs for human study. Real skills or representative outputs.

## Body style

SKILL.md body is caveman: terse, bullets, no preambles, no trailing summary. Required sections, in order: `## Purpose`, `## Inputs`, `## Flow`, `## Rules`. Additional sections allowed.

## Run workspace integration

Every skill body must instruct Claude to open a run workspace per CLAUDE.md section 13 at start of invocation and close it at end, writing `META.md` and any artifacts under `.run/<uuid>/`.
